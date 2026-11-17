"""Where the app's calculations run: in the thread that asks for one (`InProcessRunner`), or spread over worker
processes, so they use more than one core (`ProcessRunner`). See docs/architecture.md#worker-processes.

A job (worker.Job) gives a runner its tasks (tasks.py) and waits for their results; either runner stops a task
at its limits, and when its job is cancelled.
"""

import heapq
import itertools
import multiprocessing
import os
import queue
import sys
import threading
import time
import traceback
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import replace
from multiprocessing.connection import Connection
from typing import Any

from . import process, tasks
from .engine import Cancelled, Session, clear_caches, evidence_checked, gc_paused, note_evidence
from .state import GameState
from .tasks import ESTIMATE, Task, row_key
from .worker import MEMORY, REPLACED, Control, TooSlow, Watchdog


class TaskFailed(Exception):
    """A task that failed in a worker process, where its error stays: this has its traceback as `text`, and
    as `key` what tells the failure from others (`failure_key`), for the log (`__main__.Failures`)."""

    def __init__(self, key: tuple, text: str):
        super().__init__(text.strip().rpartition("\n")[2])
        self.key, self.text = key, text


class WorkerDied(Exception):
    """A task's worker process ended before the task did, or couldn't be started."""


def failure_key(error: BaseException) -> tuple:
    """What tells a failure from others: the error's type, and the places it was raised through. Not its
    message, whose values may change."""
    where = tuple((frame.f_code.co_filename, line) for frame, line in traceback.walk_tb(error.__traceback__))
    return f"{type(error).__module__}.{type(error).__qualname__}", where


class Future[T]:
    """A task given to a runner: `result` waits for it. `seconds` is how long it ran once it has, not counting
    any wait for a worker process, and `started` when a worker process was given it (time.monotonic's; None
    until then, and for a task run in the app). `cancel` stops this task alone, as cancelling its job does all
    of them: `result` then raises engine.Cancelled."""

    def __init__(self, cancel: Callable[[], None] | None = None) -> None:
        self.seconds = 0.0
        self.started: float | None = None
        self._cancel = cancel
        self._done = threading.Event()
        self._result: T | None = None
        self._error: BaseException | None = None
        self._then: list[Callable[[Future[T]], None]] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel()

    def then(self, call: Callable[[Future[T]], None]) -> None:
        """Have `call(self)` made once the task is done: now, if it is. By the thread that finishes it, so
        `call` must be quick, and take no lock (a queue's `put` is the use)."""
        with self._lock:
            if not self._done.is_set():
                self._then.append(call)
                return
        call(self)

    def done(self) -> bool:
        return self._done.is_set()

    def result(self) -> T:
        """The task's result, once it has one. Raises engine.Cancelled if its job was cancelled, or the task's
        error if it failed (TaskFailed or WorkerDied for one in a worker process)."""
        self._done.wait()
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]

    def finish(self, result: T | None = None, error: BaseException | None = None, seconds: float = 0.0) -> bool:
        """The runner's: the task's outcome. Whether it's the first, which stands."""
        with self._lock:
            if self._done.is_set():
                return False
            self._result, self._error, self.seconds = result, error, seconds
            self._done.set()
            calls, self._then = self._then, []
        for call in calls:
            call(self)
        return True


class Runner:
    """Runs tasks. `session` is the app's (engine.Session): with worker processes, what they've found about
    the hand's sorts (`engine.evidence_unchecked` asks it)."""
    session: Session
    width = 1  # how many tasks run side by side now: more than 1 with that many worker processes ready

    def submit(self, task: Task, control: Control | threading.Event | None = None, priority: int = 0) -> Future:
        """Start `task` for the job whose control (or plain event, or nothing) `control` is. `priority` is its
        place among the tasks waiting for a worker process (tasks.QUICK and the rest)."""
        raise NotImplementedError

    def clear_caches(self) -> None:
        """Empty the worker processes' caches (`engine.clear_caches`, which is the app's own)."""

    def release(self, search: int) -> None:
        """The search with this number is over (spread.py): drop what was kept for its tasks (`tasks.release`)."""
        tasks.release(search)

    def close(self) -> None:
        """End the worker processes."""


class InProcessRunner(Runner):
    """Runs each task in the thread that submits it, before `submit` returns: the app on one core."""

    def __init__(self, session: Session | None = None):
        self.session = session or Session()

    def submit(self, task: Task, control: Control | threading.Event | None = None, priority: int = 0) -> Future:
        future: Future = Future()
        start = time.monotonic()
        try:
            result = task.run(control, self.session)
        except Exception as error:  # raised again by `result`
            future.finish(error=error, seconds=time.monotonic() - start)
        else:
            future.finish(result, seconds=time.monotonic() - start)
        return future


STATES_KEPT = 4  # the states a worker keeps, by number, so each is sent once
ROWS_KEPT = 16  # the rows a worker is known to have handled last
LOGGED_KEPT = 1000  # what the workers have printed, so the same from another isn't logged again
CANCEL_GRACE = 2.0  # seconds a worker has to answer a cancel before it's ended
START_GRACE = 30.0  # seconds a worker has to start
DEATHS_MOST = 3  # workers that may end unexpectedly in a session before the app does without
PATIENCE = 0.05  # seconds a quick try or an estimate waits before it takes a worker from a search
MOVED = "moved"  # why a search's task is stopped for one: it goes back among the waiting


class _Entry:
    """A task on its way through a ProcessRunner. Also what the job's control and the watchdog stop
    (`Control.attach`): `stop`, `grown` and `shed` are the watchdog's (worker.Watchdog)."""
    deadline = float("inf")  # the worker process stops it for time itself

    def __init__(self, runner: ProcessRunner, number: int, task: Task, control: Control, priority: int):
        self.runner, self.number, self.task, self.control, self.priority = runner, number, task, control, priority
        self.future: Future = Future(lambda: runner._stop(self, REPLACED))
        self.worker: _Worker | None = None  # the one it was sent to
        self.asked = time.monotonic()
        self.moved = False  # stopped to make room (`ProcessRunner._make_room`): to run again

    def __lt__(self, other: _Entry) -> bool:
        return (self.priority, self.number) < (other.priority, other.number)

    def stop(self, reason: str) -> None:
        self.runner._stop(self, reason)

    def grown(self) -> int | None:
        return self.runner._grown(self)

    def shed(self) -> bool:
        return self.runner._shed(self)


class _Worker:
    """A worker process, as the app sees it."""

    def __init__(self, started: multiprocessing.Process, conn: Connection):
        self.process, self.conn = started, conn
        self.handle: int = started.sentinel  # full access, and unlike a pid never another process's
        self.started = time.monotonic()
        self.ready = False  # it has said so
        self.entry: _Entry | None = None  # its task, until it answers
        self.stopping: float | None = None  # when its task was cancelled, until it answers
        self.ended = False  # by the app
        self.retiring = False  # to end after its task: the pool has shrunk
        self.states: deque[int] = deque(maxlen=STATES_KEPT)  # the numbers of those it holds
        self.rows: deque[object] = deque(maxlen=ROWS_KEPT)
        self.lane: str | None = None
        self.used = 0.0  # when it last finished a task
        self.baseline = 0  # the memory it had when ready


class ProcessRunner(Runner):
    """Runs tasks in worker processes, `resize`d to the "Cores used" setting; with fewer than 2, or until one
    is ready, or for a task without a job's Control, in the thread that submits it, like InProcessRunner.

    Each worker runs one task at a time (`_serve`), and a free one takes the waiting task with the lowest
    priority number: of those, the one that last ran a task for the same row, else for the same lane
    (`Task.lane`), since it has cached what the task needs.

    A task's time limits are its worker's own. For memory, the app's watchdog adds up the app's and the
    workers' (`memory_used`), and over the limit stops the task of the worker that has grown most; a worker
    that doesn't give the memory back is ended and replaced, as is one left with more than a quarter of
    `memory_limit` after a task (the caches a heavy session leaves).

    A quick try or an estimate that has waited PATIENCE with every worker on the tasks of a search spread over
    them (spread.py) takes one: the search's task that started last is stopped, and waits its turn again. Such
    tasks are pure, so running one again only costs its time.

    Cancelling a job cancels its tasks: one waiting never starts, and a running one's worker has CANCEL_GRACE
    to stop it before it's ended and replaced. Either way the task is over for the job at once.

    A task whose worker ends unexpectedly fails (WorkerDied), and the worker is replaced. After DEATHS_MOST of
    those, or if a worker can't be started, the app runs every task itself for the rest of the session.
    Workers end with the app, however it ends: each exits once its pipe to the app is closed, and Windows
    ends those in the app's job object (`process.kill_on_close_job`).

    `log` is given what's worth logging, a line or more of text, and what the workers print to stderr.
    `job` and `orphan_exit` turn off each of the two ways workers end with the app, for the tests of the other.
    """

    def __init__(self, session: Session | None = None, *, log: Callable[[str], None] | None = None,
                 job: bool = True, orphan_exit: bool = True):
        self.local = InProcessRunner(session)
        self.session = self.local.session
        self.memory_limit = 0
        self._log = log or _log
        self._use_job, self._orphan_exit = job, orphan_exit
        self._job: int | None = None
        self._lock = threading.RLock()
        self._workers: list[_Worker] = []
        self._size = 0  # the workers wanted
        self._waiting: list[_Entry] = []  # a heap
        self._numbers = itertools.count(1)
        self._states: OrderedDict[int, tuple[int, GameState]] = OrderedDict()  # by id(): each one's number
        self._logged: OrderedDict[str, None] = OrderedDict()
        self._deaths = 0
        self._fallen = False
        self._closed = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._impatient: threading.Timer | None = None

    # --- For the app -----------------------------------------------------------------------------------

    def resize(self, cores: int, memory_limit: int | None = None) -> None:
        """Have `cores` worker processes, or none for 1: started now, and ended once free of their tasks."""
        with self._lock:
            if memory_limit is not None:
                self.memory_limit = memory_limit
            self._size = cores if cores >= 2 else 0
            if self._fallen or self._closed.is_set():
                return
            for index, worker in enumerate(sorted(self._workers, key=lambda w: w.entry is None)):
                worker.retiring = index >= self._size  # the busy ones are kept first
                if worker.retiring and worker.entry is None:
                    self._end(worker)
            while not self._fallen and len(self._workers) < self._size:
                self._start()

    def wait_ready(self, timeout: float = START_GRACE) -> bool:
        """Wait until every worker wanted is ready for tasks. Whether they are (not, once the app does without)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._fallen or (len(self._workers) >= self._size and all(w.ready for w in self._workers)):
                    return not self._fallen
            time.sleep(0.005)
        return False

    def submit(self, task: Task, control: Control | threading.Event | None = None, priority: int = 0) -> Future:
        if not isinstance(control, Control):
            return self.local.submit(task, control, priority)
        with self._lock:
            if not self._size or self._fallen or not any(w.ready for w in self._workers):
                entry = None
            else:
                entry = _Entry(self, next(self._numbers), task, control, priority)
        if entry is None:
            return self.local.submit(task, control, priority)
        control.attach(entry)  # stops it at once if the job is cancelled already
        with self._lock:
            if not entry.future.done():
                heapq.heappush(self._waiting, entry)
                self._dispatch()
                if priority <= ESTIMATE and entry.worker is None and self._impatient is None:
                    self._impatient = threading.Timer(PATIENCE, self._make_room)
                    self._impatient.daemon = True
                    self._impatient.start()
        return entry.future

    @property
    def width(self) -> int:
        with self._lock:
            if self._fallen or not self._size:
                return 1
            return max(1, sum(w.ready and not w.retiring for w in self._workers))

    def memory_used(self) -> int:
        """The bytes the worker processes have committed between them."""
        with self._lock:
            workers = list(self._workers)
        return sum(process.memory_used(worker.handle) for worker in workers)

    def clear_caches(self) -> None:
        with self._lock:
            for worker in self._workers:
                self._send(worker, ("clear",))

    def release(self, search: int) -> None:
        tasks.release(search)
        with self._lock:
            for worker in self._workers:
                self._send(worker, ("release", search))

    def close(self) -> None:
        with self._lock:
            self._closed.set()
            self._size = 0
            for worker in list(self._workers):
                self._end(worker)
            self._fail_waiting("The app is stopping.")

    # --- Workers ---------------------------------------------------------------------------------------

    def _start(self) -> None:
        """Start a worker. One that can't be started is the end of them (`_fall_back`)."""
        context = multiprocessing.get_context("spawn")
        try:
            ours, theirs = context.Pipe()
            started = context.Process(target=_serve, args=(theirs, self._orphan_exit), name="calculatro-worker",
                                      daemon=True)
            started.start()
            theirs.close()
        except Exception as error:
            self._fall_back(f"A worker process couldn't be started ({type(error).__name__}: {error}).")
            return
        worker = _Worker(started, ours)
        if self._use_job:
            if self._job is None:
                self._job = process.kill_on_close_job()
            if self._job is None or not process.add_to_job(self._job, worker.handle):
                self._say("A worker process couldn't be put in a job object: it still ends with the app "
                          "by its pipe.")
        self._workers.append(worker)
        threading.Thread(target=self._read, args=(worker,), name="worker-reader", daemon=True).start()
        if self._supervisor is None:
            self._supervisor = threading.Thread(target=self._supervise, name="worker-supervisor", daemon=True)
            self._supervisor.start()

    def _end(self, worker: _Worker, restart: bool = False) -> None:
        """End a worker on purpose, which doesn't count against the workers (`_gone`)."""
        worker.ended = True
        if worker in self._workers:
            self._workers.remove(worker)
        try:
            worker.process.terminate()
        except Exception:
            pass  # gone already
        if restart and not self._fallen and not self._closed.is_set() and len(self._workers) < self._size:
            self._start()

    def _read(self, worker: _Worker) -> None:
        """A worker's reader thread: what it sends, until its pipe closes."""
        while True:
            try:
                message = worker.conn.recv()
            except Exception:  # closed (EOFError, OSError), or a message that can't be read: it's no use now
                break
            with self._lock:
                self._received(worker, message)
        with self._lock:
            self._gone(worker)

    def _received(self, worker: _Worker, message: tuple) -> None:
        if message[0] == "ready":
            worker.ready = True
            worker.baseline = process.memory_used(worker.handle)
            self._dispatch()
        elif message[0] == "log":
            self._say(message[1])
        elif message[0] == "released":
            if worker.entry is None and not worker.ended:
                self._trim(worker)
        elif message[0] == "done":
            self._done(worker, *message[1:])

    def _done(self, worker: _Worker, number: int, kind: str, found: Any, seconds: float,
              usable: bool | None) -> None:
        entry = worker.entry
        if entry is None or entry.number != number:
            return
        worker.entry, worker.stopping, worker.used = None, None, time.monotonic()
        if usable is not None:
            note_evidence(entry.task.state, self.session, usable)
        if entry.moved and kind == "cancelled" and not entry.future.done():
            self._wait_again(entry)
        elif kind == "ok":
            self._finish(entry, found, seconds=seconds)
        elif kind == "failed":
            self._finish(entry, error=TaskFailed(*found), seconds=seconds)
        else:
            self._finish(entry, error=Cancelled(), seconds=seconds)  # for its job, it was over when cancelled
        if worker.ended:
            return
        if worker.retiring:
            self._end(worker)
        elif getattr(entry.task, "search", None) is None:  # not while it keeps what a search's tasks share
            self._trim(worker)
        self._dispatch()

    def _trim(self, worker: _Worker) -> None:
        """Replace an idle worker left with more than a quarter of the memory limit."""
        if self.memory_limit and process.memory_used(worker.handle) > self.memory_limit // 4:
            self._end(worker, restart=True)

    def _wait_again(self, entry: _Entry) -> None:
        entry.moved, entry.worker = False, None
        heapq.heappush(self._waiting, entry)

    def _make_room(self) -> None:
        """The timer's: if a quick try or an estimate has waited PATIENCE and every worker is on a task of a
        search spread over them, stop the one that started last, to run again later."""
        with self._lock:
            self._impatient = None
            now = time.monotonic()
            waiting = [e for e in self._waiting if e.priority <= ESTIMATE and not e.future.done()]
            if not waiting or self._closed.is_set():
                return
            workers = [w for w in self._workers if not w.retiring]
            taken = all(w.entry is not None and getattr(w.entry.task, "search", None) is not None
                        and not w.entry.future.done() for w in workers)
            if min(e.asked for e in waiting) + PATIENCE > now:
                pass  # not yet
            elif workers and taken and not any(w.entry.moved for w in workers):
                worker = max(workers, key=lambda w: w.entry.future.started or 0.0)
                worker.entry.moved = True
                self._send(worker, ("stop", worker.entry.number, MOVED))
                worker.stopping = now
                return  # the next to wait starts the timer again
            elif not taken:
                return
            self._impatient = threading.Timer(PATIENCE / 2, self._make_room)
            self._impatient.daemon = True
            self._impatient.start()

    def _gone(self, worker: _Worker) -> None:
        """A worker's pipe has closed: it has ended, by the app or not."""
        try:
            worker.conn.close()
        except OSError:
            pass
        entry = worker.entry
        if not worker.ended:
            worker.ended = True
            if worker in self._workers:
                self._workers.remove(worker)
            worker.process.join(1)
            if not self._closed.is_set():
                self._log(f"A worker process ended unexpectedly (exit code {worker.process.exitcode}).")
                self._deaths += 1
                if self._deaths >= DEATHS_MOST:
                    self._fall_back(f"{self._deaths} worker processes have ended unexpectedly.")
                elif len(self._workers) < self._size:
                    self._start()
        if entry is not None and entry.moved and not entry.future.done():
            self._wait_again(entry)  # ended by the app, for not stopping it in time
        elif entry is not None:
            self._finish(entry, error=WorkerDied("The worker process ended before the task did."))
        self._dispatch()

    def _fall_back(self, why: str) -> None:
        """Do without workers for the rest of the session."""
        if self._fallen:
            return
        self._fallen = True
        self._log(f"{why} The calculations run in the app itself (on one core) until the game is restarted.")
        for worker in list(self._workers):
            self._end(worker)
        self._fail_waiting(why)

    def _fail_waiting(self, why: str) -> None:
        waiting, self._waiting = self._waiting, []
        for entry in waiting:
            self._finish(entry, error=WorkerDied(why))

    def _supervise(self) -> None:
        """Ends the workers that have taken too long: to answer a cancel (a hang), or to start."""
        while not self._closed.wait(0.25):
            now = time.monotonic()
            with self._lock:
                for worker in list(self._workers):
                    if worker.stopping is not None and now >= worker.stopping + CANCEL_GRACE:
                        self._end(worker, restart=True)
                    elif not worker.ready and now >= worker.started + START_GRACE:
                        try:
                            worker.process.terminate()  # its reader counts it (`_gone`)
                        except Exception:
                            pass
                        worker.started = now  # not again while it ends

    def _say(self, text: str) -> None:
        """Log `text`, unless it was logged already this session: every worker would say the same."""
        if text in self._logged:
            return
        self._logged[text] = None
        while len(self._logged) > LOGGED_KEPT:
            self._logged.popitem(last=False)
        self._log(text)

    # --- Tasks -----------------------------------------------------------------------------------------

    def _dispatch(self) -> None:
        """Give the free workers the waiting tasks, the first (by priority, then age) first."""
        while self._waiting:
            entry = self._waiting[0]
            if entry.future.done():  # cancelled while it waited
                heapq.heappop(self._waiting)
                continue
            free = [w for w in self._workers if w.ready and w.entry is None and not w.retiring]
            if not free:
                return
            heapq.heappop(self._waiting)
            key, lane = row_key(entry.task.row), entry.task.lane
            # Its row's worker, else its lane's, else one no lane has used, else the one free longest.
            self._give(max(free, key=lambda w: (key in w.rows, w.lane == lane, w.lane is None, -w.used)), entry)

    def _give(self, worker: _Worker, entry: _Entry) -> None:
        task = entry.task
        number = self._number_of(task.state)
        entry.worker, worker.entry = worker, entry
        entry.future.started = time.monotonic()
        key = row_key(task.row)
        if key not in worker.rows:
            worker.rows.append(key)
        worker.lane = task.lane
        try:
            if number not in worker.states:
                worker.conn.send(("state", number, task.state))
                worker.states.append(number)  # the worker forgets its oldest as this does (`_serve`)
            worker.conn.send(("task", entry.number, replace(task, state=None), number,
                              evidence_checked(task.state, self.session)))
        except OSError:
            pass  # the worker has ended: its reader fails the task (`_gone`)
        except Exception as error:  # something in the task doesn't pickle
            worker.entry = None
            self._finish(entry, error=error)

    def _number_of(self, state: GameState) -> int:
        """The number `state` (this very object) goes to the workers under."""
        found = self._states.get(id(state))
        if found is None or found[1] is not state:
            found = self._states[id(state)] = (next(self._numbers), state)  # kept, so its id stays its own
            while len(self._states) > 2 * STATES_KEPT:
                self._states.popitem(last=False)
        self._states.move_to_end(id(state))
        return found[0]

    def _send(self, worker: _Worker, message: tuple) -> None:
        try:
            worker.conn.send(message)
        except OSError:
            pass  # the worker has ended, which its reader sees

    def _finish(self, entry: _Entry, result: Any = None, error: BaseException | None = None,
                seconds: float = 0.0) -> None:
        if entry.future.finish(result, error, seconds):
            entry.control.detach(entry)

    def _stop(self, entry: _Entry, reason: str) -> None:
        """Stop a task: for the memory limit, in its worker, which answers what it can (TooSlow, or a search's
        estimates); otherwise its job was cancelled, and it's over at once."""
        with self._lock:
            if entry.future.done():
                return
            running = entry.worker is not None and entry.worker.entry is entry
            if reason == MEMORY:
                if running:
                    self._send(entry.worker, ("stop", entry.number, MEMORY))
                return
            if running:
                self._send(entry.worker, ("stop", entry.number, reason))
                entry.worker.stopping = time.monotonic()
            self._finish(entry, error=Cancelled())

    def _grown(self, entry: _Entry) -> int | None:
        """How much the worker running `entry` has grown since it was ready; None if none is running it."""
        worker = entry.worker
        if worker is None or worker.entry is not entry or worker.ended:
            return None
        return max(0, process.memory_used(worker.handle) - worker.baseline)

    def _shed(self, entry: _Entry) -> bool:
        """The watchdog's, when stopping `entry` for memory didn't bring the app back under its limit: end
        its worker, if that's still what holds the most, and whether it did. The task, if it's still running,
        is given up on for memory."""
        with self._lock:
            worker = entry.worker
            if worker is None or worker.ended or worker.entry not in (None, entry):
                return False
            if worker is not max(self._workers, key=lambda w: process.memory_used(w.handle)):
                return False
            self._finish(entry, TooSlow(MEMORY))
            self._end(worker, restart=True)
            self._dispatch()
            return True


def _log(text: str) -> None:
    try:
        print(text, file=sys.stderr, end="" if text.endswith("\n") else "\n", flush=True)
    except Exception:
        pass  # a full disk, say


# --- In a worker process ---------------------------------------------------------------------------------

class _Forward:
    """A worker's `sys.stderr`: what's printed goes to the app at each flush, to log. A worker has no log of
    its own (and under pythonw, no stderr at all)."""

    def __init__(self, send: Callable[[tuple], None]):
        self.send, self.text = send, ""

    def write(self, text: str) -> int:
        self.text += text
        return len(text)

    def flush(self) -> None:
        text, self.text = self.text, ""
        if text:
            try:
                self.send(("log", text))
            except Exception:
                pass  # the app has gone


def _serve(conn: Connection, orphan_exit: bool = True) -> None:
    """A worker process: runs the app's tasks one at a time on this, its main thread, each stopped at its time
    limits by the worker's own watchdog, with its own engine session and caches.

    A listener thread reads what the app sends, so that a cancel (or a stop for the app's memory limit) reaches
    a running task, and so that the worker exits at once when the app has gone and its pipe is closed
    (`orphan_exit`), whatever the main thread is doing.

    It tells the app when it's ready, each task's outcome (how long it ran, and what it found of the hand's
    sorts: `engine.evidence_checked`), and what's printed to stderr.
    """
    sending = threading.Lock()

    def send(message: tuple) -> None:
        with sending:
            conn.send(message)

    sys.stderr = _Forward(send)
    try:
        _work(conn, send, orphan_exit)
    except BaseException:
        traceback.print_exc()
        sys.stderr.flush()
        os._exit(1)


def _work(conn: Connection, send: Callable[[tuple], None], orphan_exit: bool) -> None:
    inbox: queue.SimpleQueue[tuple] = queue.SimpleQueue()
    controls: dict[int, Control] = {}  # each task's, from when it's read
    guard = threading.Lock()
    watchdog = Watchdog()  # time limits only: the app watches the memory

    def listen() -> None:
        while True:
            try:
                message = conn.recv()
            except (EOFError, OSError):
                if orphan_exit:
                    os._exit(0)
                return
            if message[0] == "stop":
                with guard:
                    control = controls.get(message[1])
                if control is None:
                    pass  # finished already
                elif message[2] == MEMORY:
                    control.stop_attempts(MEMORY)
                else:
                    control.cancel(message[2])
            else:
                if message[0] == "task":
                    with guard:
                        controls[message[1]] = Control(watchdog)
                inbox.put(message)

    threading.Thread(target=listen, name="listener", daemon=True).start()
    session = Session()
    states: OrderedDict[int, GameState] = OrderedDict()
    send(("ready",))
    while True:
        message = inbox.get()
        if message[0] == "state":
            states[message[1]] = message[2]
            while len(states) > STATES_KEPT:
                states.popitem(last=False)
        elif message[0] == "clear":
            clear_caches()
        elif message[0] == "release":
            tasks.release(message[1])
            send(("released",))
        elif message[0] == "task":
            _, number, task, state_number, usable = message
            control, usable_now = controls[number], None
            start = time.monotonic()
            try:
                task = replace(task, state=states[state_number])
                if usable is not None:
                    note_evidence(task.state, session, usable)
                with gc_paused():
                    outcome = ("ok", task.run(control, session))
            except Cancelled:
                outcome = ("cancelled", None)
            except Exception as error:
                outcome = ("failed", (failure_key(error), "".join(traceback.format_exception(error))))
            seconds = time.monotonic() - start
            if task.state is not None:  # a cancelled or failed task may have checked the sorts first, too
                usable_now = evidence_checked(task.state, session)
            with guard:
                del controls[number]
            try:
                send(("done", number, *outcome, seconds, usable_now))
            except (EOFError, OSError):
                raise
            except Exception as error:  # a result that doesn't pickle
                send(("done", number, "failed",
                      (failure_key(error), "".join(traceback.format_exception(error))), seconds, None))

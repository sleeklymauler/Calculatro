"""Stand-ins for the app's parts: a source of states, and an engine whose rows take set times."""

import heapq
import itertools
import threading
import time
from fractions import Fraction

from calculatro.engine import Cancelled, DiscardOutcome, Session
from calculatro.pool import Future, Runner
from calculatro.state import GameState
from calculatro.worker import MEMORY, REPLACED, Control, Watchdog


class FakeSource:
    """Hands out queued states, one per poll(); the test adds more as it goes."""

    def __init__(self, *states: GameState):
        self.queue = list(states)

    def poll(self) -> GameState | None:
        return self.queue.pop(0) if self.queue else None


class FakeClock:
    """A stand-in for `time.monotonic` that moves on `step` seconds each time it's read, so what's done by a
    deadline (an estimate's samples) depends on the work done, not on how fast the machine is."""

    def __init__(self, step: float):
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def taking(seconds: dict[str, float], calls: list[tuple] | None = None, odds: dict[str, Fraction] | None = None):
    """A stand-in for `evaluate_discard` whose rows take set times, by the target's
    name (none for others), and stop when cancelled, as the engine does. The odds
    are `odds` by the target's name, else 1/(number of cards discarded + 2)."""
    def evaluate(state, target, discard, cancel=None, **_):
        if calls is not None:
            calls.append((target.name, len(discard)))
        end = time.monotonic() + seconds.get(target.name, 0)
        while time.monotonic() < end:
            if cancel is not None and cancel.is_set():
                raise Cancelled
            time.sleep(0.002)
        p = (odds or {}).get(target.name, Fraction(1, len(discard) + 2))
        return DiscardOutcome([state.hand[i] for i in discard], 1, p)
    return evaluate


class _Link:
    """What a ThreadRunner task's job stops it by (`Control.attach`)."""
    deadline = float("inf")

    def __init__(self, own: Control, future: Future):
        self.own, self.future = own, future

    def stop(self, reason: str) -> None:
        if reason == MEMORY:
            self.own.stop_attempts(MEMORY)
        else:
            self.own.cancel(reason)
            self.future.finish(error=Cancelled())  # over for its job at once, as with a worker process


class ThreadRunner(Runner):
    """A stand-in for pool.ProcessRunner whose `width` workers are threads here, so the engine's stand-ins
    (`taking`) work in them: tasks wait for a free one in order of priority, stop at their own limits, and are
    cancelled with their job or one by one. `began` is each task and its priority, in the order they started."""

    def __init__(self, width: int = 2):
        self.session, self.width = Session(), width
        self.began: list[tuple[int, object]] = []
        self._lock = threading.Lock()
        self._waiting: list[tuple] = []
        self._free = width
        self._numbers = itertools.count()
        self._watchdog = Watchdog(period=0.005)

    def submit(self, task, control=None, priority=0) -> Future:
        own = Control(self._watchdog)
        future: Future = Future(lambda: link.stop(REPLACED))
        link = _Link(own, future)
        control.attach(link)
        with self._lock:
            heapq.heappush(self._waiting, (priority, next(self._numbers), task, control, own, link))
        self._dispatch()
        return future

    def _dispatch(self) -> None:
        with self._lock:
            while self._free and self._waiting:
                priority, _, task, control, own, link = heapq.heappop(self._waiting)
                if link.future.done():
                    control.detach(link)
                    continue
                self._free -= 1
                self.began.append((priority, task))
                threading.Thread(target=self._run, args=(task, control, own, link), daemon=True).start()

    def _run(self, task, control, own, link) -> None:
        start = time.monotonic()
        try:
            found = task.run(own, self.session)
        except Exception as error:
            link.future.finish(error=error, seconds=time.monotonic() - start)
        else:
            link.future.finish(found, seconds=time.monotonic() - start)
        control.detach(link)
        with self._lock:
            self._free += 1
        self._dispatch()

"""Background calculations, so the live view never waits on a slow one, and the
limits that stop a part of one taking too long or using too much memory."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .engine import Cancelled

T = TypeVar("T")

# What stopped a part of a job besides the job being cancelled (`Attempt.reason`).
TIME = "time"
MEMORY = "memory"
REPLACED = "replaced"  # Job.cancel's reason by default: a new state


@dataclass(frozen=True)
class Limits:
    """How long the parts of the app's calculations may take, in seconds."""
    quick: float = 0.25  # each odds value's first try, just under the mod's patience (0.3 s)
    approx: float = 1.0  # an estimate of each value its first try found slow
    exact: float = 30.0  # each odds value's last try, after every other value's first
    search: float = 30.0  # a best-discard search


LIMITS = Limits()


@dataclass(frozen=True)
class TooSlow:
    """A calculation given up on: `limit` (TIME or MEMORY) stopped it."""
    limit: str


class Attempt:
    """A part of a job run with limits (`Control.limited`). Like an Event for the
    engine (`is_set`), set once its job is cancelled or a limit is reached;
    `reason` says which: TIME, MEMORY, or the job's reason (`Job.cancel`)."""

    def __init__(self, seconds: float):
        self.deadline = time.monotonic() + seconds
        self.reason: str | None = None
        self._event = threading.Event()
        self._lock = threading.Lock()

    def is_set(self) -> bool:
        return self._event.is_set()

    def stop(self, reason: str) -> None:
        """Stops it, unless it's stopped already (the first reason stands)."""
        with self._lock:
            if not self._event.is_set():
                self.reason = reason
                self._event.set()


class Watchdog:
    """The thread that stops attempts (`Attempt.stop`): each one at its
    deadline (TIME), and every one running while the app uses more than
    `memory_limit` bytes (MEMORY), `memory` being a reading of what it uses.

    Python gives the memory back as a stopped calculation unwinds, which takes
    a moment (0.4 s for 2 GB). Attempts started meanwhile, as when another job
    starts its next one, would be stopped for the memory of the one before,
    so after stopping them it waits until the memory has fallen below the
    limit, or `grace` seconds have passed, before it stops any for memory
    again. (The second is in case it never sees the memory fall: the next
    attempt may use as much again before it looks.)
    """

    def __init__(self, memory: Callable[[], int] | None = None, memory_limit: int = 0, period: float = 0.02,
                 grace: float = 2.0):
        self.memory, self.memory_limit, self.period, self.grace = memory, memory_limit, period, grace
        self._attempts: set[Attempt] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._freeing: float | None = None  # when it last stopped attempts for memory, until the memory falls

    def watch(self, attempt: Attempt) -> None:
        with self._lock:
            self._attempts.add(attempt)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="watchdog", daemon=True)
                self._thread.start()
        self._wake.set()

    def unwatch(self, attempt: Attempt) -> None:
        with self._lock:
            self._attempts.discard(attempt)

    def check(self) -> None:
        """Stops the attempts over a limit now (the thread does this every `period`)."""
        with self._lock:
            attempts = list(self._attempts)
        now = time.monotonic()
        for attempt in attempts:
            if now >= attempt.deadline:
                attempt.stop(TIME)
        if self.memory is None or not self.memory_limit:
            return
        if self.memory() <= self.memory_limit:
            self._freeing = None
        elif attempts and (self._freeing is None or now >= self._freeing + self.grace):
            for attempt in attempts:
                attempt.stop(MEMORY)
            self._freeing = now

    def _run(self) -> None:
        while True:
            with self._lock:
                idle = not self._attempts
                if idle:
                    self._wake.clear()
            if idle:
                self._wake.wait()
                continue
            self.check()
            time.sleep(self.period)


class Control:
    """What a job's function is given: like an Event for the engine (`is_set`, once
    the job is cancelled), parts with limits (`limited`), and a way to say how
    far it has got (`progress`)."""

    def __init__(self, watchdog: Watchdog | None = None):
        self.reason: str | None = None
        self.latest: Any = None  # the last progress reported
        self.updates = 0  # how many times it has been
        self._event = threading.Event()
        self._attempts: set[Attempt] = set()
        self._lock = threading.Lock()
        self._watchdog = watchdog

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def cancel(self, reason: str = REPLACED) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self.reason = reason
            self._event.set()
            attempts = list(self._attempts)
        for attempt in attempts:
            attempt.stop(reason)

    @contextmanager
    def limited(self, seconds: float) -> Iterator[Attempt]:
        """An attempt at a part of the job, stopped after `seconds`, or when the
        app uses too much memory (with a watchdog), or the job is cancelled."""
        attempt = Attempt(seconds)
        with self._lock:
            if self._event.is_set():
                attempt.stop(self.reason or REPLACED)
            self._attempts.add(attempt)
        if self._watchdog is not None:
            self._watchdog.watch(attempt)
        try:
            yield attempt
        finally:
            if self._watchdog is not None:
                self._watchdog.unwatch(attempt)
            with self._lock:
                self._attempts.discard(attempt)

    def progress(self, value: Any) -> None:
        self.latest, self.updates = value, self.updates + 1


def limited(cancel: Control | threading.Event | None, seconds: float, fn: Callable[[Any], T]) -> T | TooSlow:
    """`fn(cancel)` stopped after `seconds` or at the memory limit, when `cancel` is
    a job's Control: its result, or TooSlow if a limit stopped it. Raises
    Cancelled if the job was cancelled. Anything else as `cancel` sets no limits."""
    if not isinstance(cancel, Control):
        return fn(cancel)
    with cancel.limited(seconds) as attempt:
        try:
            return fn(attempt)
        except Cancelled:
            if attempt.reason in (TIME, MEMORY) and not cancel.is_set():
                return TooSlow(attempt.reason)
            raise


class Job(Generic[T]):
    """Runs `fn(control)` on a daemon thread (see Control).

    `fn` should raise engine.Cancelled once `control` is set (the engine does).
    A cancelled job finishes without a result; its thread exits on its own soon after.
    """

    def __init__(self, fn: Callable[[Control], T], watchdog: Watchdog | None = None):
        self.control = Control(watchdog)
        self._done = threading.Event()
        self._result: T | None = None
        self._error: BaseException | None = None
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()

    def _run(self, fn: Callable[[Control], T]) -> None:
        try:
            self._result = fn(self.control)
        except Cancelled:
            pass
        except BaseException as e:  # re-raised to the caller by result()
            self._error = e
        finally:
            self._done.set()

    def cancel(self, reason: str = REPLACED) -> None:
        self.control.cancel(reason)

    @property
    def cancelled(self) -> bool:
        return self.control.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Whether the job finished (or was cancelled) within `timeout` seconds."""
        return self._done.wait(timeout)

    def result(self) -> T:
        """The finished job's result; raises its error if it failed."""
        if not self._done.is_set():
            raise RuntimeError("job still running")
        if self._error is not None:
            raise self._error
        if self.cancelled:
            raise Cancelled
        return self._result  # type: ignore[return-value]

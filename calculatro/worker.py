"""Background calculations, so the live view never waits on a slow one."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Generic, TypeVar

from .engine import Cancelled

T = TypeVar("T")


class Job(Generic[T]):
    """Runs `fn(cancel)` on a daemon thread.

    `fn` should raise engine.Cancelled once `cancel` is set (the engine does).
    A cancelled job finishes without a result; its thread exits on its own soon after.
    """

    def __init__(self, fn: Callable[[threading.Event], T]):
        self.cancel_event = threading.Event()
        self._done = threading.Event()
        self._result: T | None = None
        self._error: BaseException | None = None
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()

    def _run(self, fn: Callable[[threading.Event], T]) -> None:
        try:
            self._result = fn(self.cancel_event)
        except Cancelled:
            pass
        except BaseException as e:  # re-raised to the caller by result()
            self._error = e
        finally:
            self._done.set()

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

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

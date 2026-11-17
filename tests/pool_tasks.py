"""Tasks for the tests of the worker processes (`test_pool`): what a worker is made to do there. A module of
its own, since a worker unpickles a task by importing where its class is."""

import os
import sys
import time
from dataclasses import dataclass, field

from calculatro import engine, hands, tasks
from calculatro.state import GameState
from calculatro.worker import limited

STATE = GameState(in_run=True)


@dataclass(frozen=True)
class Do:
    """Does `what` in the worker (a method's name here) with `arg`, for the row `row` in the lane `lane`."""
    what: str
    arg: object = None
    row: str = "row"
    lane: str = "odds"
    search: int | None = None  # as a task of a search spread over the workers has
    arg2: object = None  # only tells two tasks apart
    state: GameState | None = field(default=STATE, compare=False)

    def run(self, control, session):
        return getattr(self, "_" + self.what)(control, session)

    def _pid(self, control, session) -> int:
        return os.getpid()

    def _fail(self, control, session):
        raise ValueError(f"no good: {self.arg}")

    def _say(self, control, session) -> None:
        print(self.arg, file=sys.stderr, flush=True)

    def _wait(self, control, session):
        """Until stopped, with a time limit of `arg` seconds: TooSlow at a limit, Cancelled with the job."""
        def until_stopped(cancel):
            while not cancel.is_set():
                time.sleep(0.001)
            raise engine.Cancelled
        return limited(control, self.arg, until_stopped)

    def _hog(self, control, session):
        """Holds `arg` bytes until stopped, as a calculation does: within its limits (an attempt)."""
        def holding(cancel):
            held = bytearray(self.arg)
            while not cancel.is_set():
                time.sleep(0.001)
            del held
            raise engine.Cancelled
        return limited(control, 60, holding)

    def _deaf_hog(self, control, session) -> None:
        """Holds `arg` bytes whatever it's told."""
        held = bytearray(self.arg)
        time.sleep(60)
        del held

    def _hang(self, control, session) -> None:
        time.sleep(60)

    def _die(self, control, session) -> None:
        os._exit(3)

    def _unpicklable(self, control, session):
        return lambda: None

    def _check_sorts(self, control, session) -> bool:
        return engine.usable(self.state, None, session)

    def _sorts_checked(self, control, session) -> bool | None:
        return engine.evidence_checked(self.state, session)

    def _build_patterns(self, control, session) -> int:
        hands.get_target("Straight Flush").patterns()
        return hands._patterns.cache_info().currsize

    def _search_kept(self, control, session) -> int | None:
        kept = getattr(tasks._kept, "shared", None)
        return kept and kept[0]

    def _patterns_cached(self, control, session) -> int:
        return hands._patterns.cache_info().currsize

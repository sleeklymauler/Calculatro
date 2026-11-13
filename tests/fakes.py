"""Stand-ins for the app's parts: a source of states, and an engine whose rows take set times."""

import time
from fractions import Fraction

from calculatro.engine import Cancelled, DiscardOutcome
from calculatro.state import GameState


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
    def evaluate(state, target, discard, cancel=None):
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

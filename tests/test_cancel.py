"""A cancelled calculation ends soon after: the engine and the search check `cancel` often enough, all the way
through the slow scenarios, that a new state never waits long on the job it replaces.

Times are the job thread's CPU time (`time.thread_time`), not the clock's, and each run stops after a number of
checks, not of seconds, so a busy machine does the same work and doesn't fail them. Each case runs once first to
fill the caches built once per set of rules and target (patterns, `engine.counting._split_by_suit`, Cerulean
Bell's forced patterns...): those builds can't be cancelled (TODO.md, P8). Both run with the garbage collector
paused, as a job runs them (`worker.Job`): its passes don't check `cancel` either.
"""

import threading
import time
import traceback
from collections.abc import Callable

import pytest

from calculatro.cards import parse_query
from calculatro.engine import Cancelled, estimate_cards, estimate_discard, evaluate_cards, gc_paused
from calculatro.search import Progress, best_card_discards, best_discards, estimated_best
from calculatro.worker import Job
from scenarios import SCENARIOS

# The longest the job's thread may go between two checks of `cancel`. The mod waits 0.3 s for the odds of a new
# state (core.lua), and a cancelled job still running shares the interpreter with the new one.
LONGEST = 0.1
MEASURED = 60_000  # checks the measured run makes before it sets itself (about a second of the slowest cases)
# Checks the first run makes, filling the caches: as many, so the measured run never gets further and builds one.
WARM = MEASURED


class Checks:
    """A `cancel` that times the stretches between its checks, in the CPU time of the thread checking it, and sets
    itself at its `after`th check."""

    def __init__(self, after: int):
        self.left = after
        self.last = time.thread_time()
        self.longest = 0.0
        self.where = ""  # the check ending the longest stretch
        self.set_at: float | None = None

    def is_set(self) -> bool:
        now = time.thread_time()
        if now - self.last > self.longest:
            self.longest = now - self.last
            self.where = "".join(traceback.format_stack(limit=8)[:-1])
        self.last = now
        self.left -= 1
        if self.set_at is None and self.left < 0:
            self.set_at = now
        return self.set_at is not None


def scenario(sid: str):
    return next(s for s in SCENARIOS if s.id == sid)


def _search(sid: str) -> Callable[[object], object]:
    s = scenario(sid)
    state, target = s.state(), s.target()
    return lambda cancel: best_discards(state, target, cancel)


def _odds(sid: str) -> Callable[[object], object]:
    return scenario(sid).run


def _estimate(sid: str) -> Callable[[object], object]:
    s = scenario(sid)
    (state, discard), target = s.state(), s.target()
    return lambda cancel: estimate_discard(state, target, discard, cancel, seconds=60)


def _given_up(sid: str) -> Callable[[object], object]:
    """A search given up on as soon as it has its choices, answering from estimates (search.estimated_best)."""
    s = scenario(sid)
    state, target = s.state(), s.target()

    def run(cancel):
        progress = Progress()

        class GiveUp:
            def is_set(self) -> bool:
                return progress.estimate is not None or cancel.is_set()
        try:
            best_discards(state, target, GiveUp(), progress)
        except Cancelled:
            if cancel.is_set():
                raise
        return estimated_best(progress, cancel, 60)
    return run


HOUSE_BOTH = "standard | 8 unknown: The House (all face-down), sorted both ways | Pair | No jokers"


def _card_odds(text: str) -> Callable[[object], object]:
    state, discard = scenario(HOUSE_BOTH).state()
    return lambda cancel: evaluate_cards(state, parse_query(text), discard, cancel)


def _card_search(text: str) -> Callable[[object], object]:
    state, _ = scenario(HOUSE_BOTH).state()
    return lambda cancel: best_card_discards(state, parse_query(text), cancel)


def _card_estimate(text: str) -> Callable[[object], object]:
    state, discard = scenario(HOUSE_BOTH).state()
    return lambda cancel: estimate_cards(state, parse_query(text), discard, cancel, seconds=60)


CASES = {
    # Odds rows: The House's union by suit, Cerulean Bell's forced card, The Hook taking from face-down cards,
    # The Mark's face cards in a sort, and a posterior of every deal fitting two sorts.
    "odds, The House, any": _odds(
        "wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | Four Fingers + Shortcut"),
    "odds, Cerulean Bell": _odds(
        "standard | Keep 6, draw 2, Cerulean Bell | Full House + Straight Flush (any) | Four Fingers + Shortcut"),
    "odds, The Hook, sorted": _odds(
        "standard | 5 unknown: 5 face-down kept, sorted by rank, The Hook, 1 played | Pair | No jokers"),
    "odds, The Mark, sorted": _odds(
        "standard | 6 unknown: The Mark, 3 face-down, draw 3, sorted by rank | Full House + Straight Flush (any) "
        "| Four Fingers + Shortcut"),
    "odds, sorted both ways": _odds(
        "standard | 8 unknown: The House (all face-down), sorted both ways | Straight Flush | No jokers"),
    "estimate, sorted both ways": _estimate(
        "standard | 8 unknown: The House (all face-down), sorted both ways | Straight Flush | No jokers"),
    # Best-discard searches.
    "search, 10 cards": _search("standard | Best discard: 10 cards | Straight Flush | Four Fingers + Shortcut"),
    "search, 11 cards, any": _search(
        "standard | Best discard: 11 cards, holding no target | Flush + Straight + Full House (any) | No jokers"),
    "search, Crimson Heart": _search(
        "standard | Best discard: 8 cards, Crimson Heart, played, Joker debuffed | Straight Flush "
        "| Four Fingers + Shortcut"),
    "search, sorted": _search(
        "standard | Best discard: 8 cards, 2 face-down, sorted by rank | Flush + Straight + Full House (any) "
        "| Four Fingers + Shortcut"),
    "search given up, sorted": _given_up(
        "standard | Best discard: 8 cards, 2 face-down, sorted by rank | Straight Flush | Four Fingers + Shortcut"),
    # Card queries, over The House's first hand sorted both ways.
    "card odds, sorted both ways": _card_odds("2+ face cards"),
    "card search, sorted both ways": _card_search("2+ face cards"),
    "card estimate, sorted both ways": _card_estimate("2+ face cards"),
}


@pytest.mark.parametrize("name", CASES)
def test_cancel_is_checked_often(name):
    run = CASES[name]
    try:
        with gc_paused():
            run(Checks(WARM))
    except Cancelled:
        pass
    checks = Checks(MEASURED)
    try:
        with gc_paused():
            run(checks)
    except Cancelled:
        assert checks.set_at is not None, "cancelled without being set"
        ended = time.thread_time() - checks.set_at
        assert ended <= LONGEST, f"took {ended:.3f} s to end once cancelled"
    assert checks.longest <= LONGEST, f"{checks.longest:.3f} s without checking, until:\n{checks.where}"


def test_a_cancelled_job_ends_soon():
    """Through `worker.Job`, as the app runs it: cancelled partway through a slow search, it ends."""
    run = CASES["search, sorted"]
    started = threading.Event()

    def job_fn(control):
        started.set()
        return run(control)
    job = Job(job_fn)
    assert started.wait(5)
    time.sleep(0.3)  # well into the search
    assert not job.wait(0), "the search ended before it was cancelled"
    job.cancel()
    # Its thread is alone in this process, so it gets about as much CPU as the other cases'. The clock's time
    # is generous for a busy machine.
    assert job.wait(2), "still running 2 s after it was cancelled"
    with pytest.raises(Cancelled):
        job.result()

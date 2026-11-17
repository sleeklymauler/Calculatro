"""The calculations a runner runs (pool.Runner): in the job's own thread, or in a worker process.

Each task is the arguments of one of the functions here, with the limits it's given up at, and `run` is the
whole of it. A task goes to a worker process pickled, and its result comes back the same way, so both hold
only what pickles: states, targets and card queries (a query by its text: see `CardQuery.__reduce__`), and the
engine's and the search's results.
"""

import threading
from dataclasses import dataclass, replace
from fractions import Fraction
from threading import Event

from . import search
from .cards import CardQuery
from .engine import (
    DiscardOutcome,
    Estimate,
    Reuse,
    Session,
    estimate_cards,
    estimate_discard,
    evaluate_cards,
    evaluate_discard,
    with_usable_evidence,
)
from .hands import Target
from .search import Progress, Search, best_card_discards, best_discards, estimated_best
from .state import GameState
from .worker import LIMITS, Control, Limits, TooSlow, limited

# The order free worker processes take waiting tasks in (`Runner.submit`'s priority), first to last. An odds
# value's last try comes after a search: the player clicked for that, and is waiting.
OVERDUE = -1  # an estimate, once the quick tries that ran out have taken long enough (`Limits.quick_pass`)
QUICK = 0  # a value's first try (`Limits.quick`)
ESTIMATE = 1  # an estimate of a value found slow
SEARCH = 2  # a best-discard search
EXACT = 3  # a value's last try (`Limits.exact`)


def row_key(row: Target | CardQuery | None) -> object:
    """What tells a row's target apart from another's. A query goes by its text, since
    two parses of the same text may not be equal (their filters can be closures)."""
    return (row.text or row) if isinstance(row, CardQuery) else row


def odds(state: GameState, row: Target | CardQuery, discard: list[int], cancel: Event | None = None,
         session: Session | None = None) -> DiscardOutcome:
    """A row's exact odds after discarding `discard` (keeping the hand, for none)."""
    if isinstance(row, CardQuery):
        return evaluate_cards(state, row, discard, cancel, session)
    return evaluate_discard(state, row, discard, cancel, session=session)


def estimate(state: GameState, row: Target | CardQuery, discard: list[int], cancel: Event | None, seconds: float,
             session: Session | None = None) -> Estimate | None:
    """An estimate of the same in about `seconds`; None if the row has none."""
    if isinstance(row, CardQuery):
        return estimate_cards(state, row, discard, cancel, seconds, session)
    return estimate_discard(state, row, discard, cancel, seconds, session=session)


def find_best(state: GameState, target: Target | CardQuery, cancel: Event | None = None,
              progress: Progress | None = None, session: Session | None = None) -> Search:
    """The best discards for a row's target hand or card query."""
    if isinstance(target, CardQuery):
        return best_card_discards(state, target, cancel, progress, session=session)
    return best_discards(state, target, cancel, progress, session=session)


def search_within(state: GameState, target: Target | CardQuery, control: Control | Event | None,
                  limits: Limits = LIMITS, session: Session | None = None) -> Search | TooSlow:
    """find_best, given up after `limits.search` (or at the memory limit). One
    given up on answers from estimates of what it had found
    (`search.estimated_best`, in about `limits.search_estimate`), marked with
    what stopped it; TooSlow if it can't. (Unmarked if they turn out exact.)"""
    progress = Progress()
    found = limited(control, limits.search, lambda cancel: find_best(state, target, cancel, progress, session))
    if not isinstance(found, TooSlow):
        return found
    # The estimates stop themselves; the limit is for ones that can't.
    estimated = limited(control, 2 * limits.search_estimate,
                        lambda cancel: estimated_best(progress, cancel, limits.search_estimate))
    return given_up(found, estimated)


def given_up(found: TooSlow, estimated: Search | TooSlow | None) -> Search | TooSlow:
    """What a search stopped at a limit (`found`) answers, given what its estimates came to."""
    if not isinstance(estimated, Search):
        return found
    if estimated.estimated is None:
        return estimated  # every choice that could be best was quick after all: exact
    return replace(estimated, estimated=replace(estimated.estimated, limit=found.limit))


@dataclass(frozen=True)
class Odds:
    """A value's exact odds (`odds`), TooSlow if a limit stopped it: `seconds`, or the memory limit."""
    state: GameState
    row: Target | CardQuery
    discard: list[int]
    seconds: float
    lane = "odds"  # a worker process that ran the lane's last task has what the next one wants cached

    def run(self, control: Control | Event | None, session: Session) -> DiscardOutcome | TooSlow:
        return limited(control, self.seconds, lambda cancel: odds(self.state, self.row, self.discard, cancel, session))


@dataclass(frozen=True)
class Approximate:
    """A value's estimate (`estimate`) in about `seconds`. The estimate stops itself; the limit, twice that,
    is for one that can't start (TooSlow)."""
    state: GameState
    row: Target | CardQuery
    discard: list[int]
    seconds: float
    lane = "odds"

    def run(self, control: Control | Event | None, session: Session) -> Estimate | TooSlow | None:
        return limited(control, 2 * self.seconds,
                       lambda cancel: estimate(self.state, self.row, self.discard, cancel, self.seconds, session))


@dataclass(frozen=True)
class BestDiscard:
    """A row's whole best-discard search, with its limits (`search_within`)."""
    state: GameState
    row: Target | CardQuery
    limits: Limits = LIMITS
    lane = "search"

    def run(self, control: Control | Event | None, session: Session) -> Search | TooSlow:
        return search_within(self.state, self.row, control, self.limits, session)


# --- A search's steps, when it's spread over worker processes (spread.py) ---------------------------------
#
# Each has the number of its search (`search`). A worker keeps what the search's choices share (the state with
# usable evidence, and an engine.Reuse for its hand) from one of its tasks to the next, until the search is
# over (`release`) or another's task comes.

_kept = threading.local()  # `shared`: (the search's number, its state, its Reuse), for the thread's tasks


def _for_search(number: int, state: GameState, cancel: Event | None, session: Session) -> tuple[GameState, Reuse]:
    shared = getattr(_kept, "shared", None)
    if shared is None or shared[0] != number:
        usable = with_usable_evidence(state, cancel, session)
        shared = _kept.shared = number, usable, Reuse(usable.hand)
    return shared[1], shared[2]


def release(number: int) -> None:
    """Search `number` is over: drop what this thread kept for it (a worker process runs its tasks on one)."""
    shared = getattr(_kept, "shared", None)
    if shared is not None and shared[0] == number:
        _kept.shared = None


@dataclass(frozen=True)
class SearchPlan:
    """A search's layout (`search.plan`)."""
    state: GameState
    row: Target | CardQuery
    seconds: float
    search: int
    lane = "search"

    def run(self, control: Control | Event | None, session: Session) -> search.Plan | TooSlow:
        return limited(control, self.seconds, lambda cancel: search.plan(self.state, self.row, cancel, session))


@dataclass(frozen=True)
class RelabelKeys:
    """Each of `discards`' relabel key for the target `row` (`search.relabel_keys`)."""
    state: GameState
    row: Target
    discards: list[list[int]]
    seconds: float
    search: int
    lane = "search"

    def run(self, control: Control | Event | None, session: Session) -> list[object] | TooSlow:
        return limited(control, self.seconds, lambda cancel: search.relabel_keys(
            self.state, self.row, self.discards, cancel, session))


@dataclass(frozen=True)
class Choice:
    """One of a search's choices: the odds of `row` after discarding `discard`, or with `miss`, None if
    they're below 1 (`search.choice_odds`)."""
    state: GameState
    row: Target | CardQuery
    discard: list[int]
    seconds: float
    search: int
    miss: bool = False
    lane = "search"

    def run(self, control: Control | Event | None, session: Session) -> Fraction | TooSlow | None:
        def odds_of(cancel: Event | None) -> Fraction | None:
            state, reuse = _for_search(self.search, self.state, cancel, session)
            return search.choice_odds(state, self.row, self.discard, cancel, reuse, session, self.miss)
        return limited(control, self.seconds, odds_of)


@dataclass(frozen=True)
class EstimatedBest:
    """A search given up on, answering from estimates in about `seconds` (`search.estimated_from`, with what
    it had found of its choices). The estimates stop themselves; the limit, twice that, is for ones that
    can't."""
    state: GameState
    row: Target | CardQuery
    laid: search.Plan
    lows: list[Fraction]
    highs: list[Fraction]
    exact: dict[int, Fraction]
    out: set[int]
    seconds: float
    search: int
    lane = "search"

    def run(self, control: Control | Event | None, session: Session) -> Search | TooSlow | None:
        return limited(control, 2 * self.seconds, lambda cancel: search.estimated_from(
            self.state, self.row, self.laid, self.lows, self.highs, self.exact, self.out, cancel, self.seconds,
            session))


type Task = Odds | Approximate | BestDiscard | SearchPlan | RelabelKeys | Choice | EstimatedBest

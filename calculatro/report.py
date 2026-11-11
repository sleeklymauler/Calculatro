"""Odds for one game state, shared by the console output and the mod's panel."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from threading import Event

from .cards import CardQuery
from .engine import DiscardOutcome, discard_draws, evaluate_cards, evaluate_discard
from .hands import Target, combine
from .search import can_redraw
from .state import Card, GameState
from .worker import LIMITS, Control, Limits, TooSlow, limited


class Pending:
    """A value not worked out yet, in a partial report (`PENDING`)."""

    def __repr__(self) -> str:
        return "PENDING"


PENDING = Pending()

# A value in a report: the odds, given up on, or (in a partial report) still to come.
Value = Fraction | TooSlow | Pending


@dataclass(frozen=True)
class TargetOdds:
    name: str
    keep: Value  # keeping the hand, drawing nothing
    discard: Value | None  # discarding the selection; None if nothing is selected


@dataclass(frozen=True)
class CardOdds:
    query: CardQuery
    keep: Value  # the final hand after keeping the hand
    discard: Value | None  # after discarding the selection; None if nothing is selected


@dataclass(frozen=True)
class Report:
    targets: list[TargetOdds]  # "Any target" first when there are several
    cards: list[CardOdds]  # card queries over the final hand
    discarded: list[Card]  # the selection; empty if nothing is selected
    discard_draws: int  # cards drawn after discarding it
    discards_left: int
    redraw: bool = True  # False on the last hand with no discards: nothing is drawn after it
    partial: bool = False  # some values are still to come (PENDING)


def row_key(row: Target | CardQuery | None) -> object:
    """What tells a row's target apart from another's. A query goes by its text, since
    two parses of the same text may not be equal (their filters can be closures)."""
    return (row.text or row) if isinstance(row, CardQuery) else row


def build_report(state: GameState, targets: Sequence[Target], queries: Sequence[CardQuery] = (),
                 cancel: Control | Event | None = None, known: dict | None = None, limits: Limits = LIMITS,
                 progress: Callable[[Report], None] | None = None) -> Report | None:
    """Odds of the target hands and card queries for keeping the hand vs.
    discarding the in-game selection. None outside a run, with an empty hand,
    or with nothing to calculate. With no way left to throw cards away and draw
    (`can_redraw`), the selection is the hand to be played, so there are no
    discard odds.

    With a job's Control as `cancel`, each value (a row's Keep or Discard) is
    worked out in two passes, so a quick one is never held up by a slow one:
    first each with `limits.quick` seconds, then those that didn't finish,
    from the start, with `limits.exact` seconds each. One that doesn't finish
    then, or uses too much memory (`worker.Watchdog`), is TooSlow. The order
    in each pass is the panel's: Keep, then Discard. `progress` is given a
    partial report as each value is found.

    `known` holds each value as soon as it's worked out, or given up on, so it
    outlives a cancelled report and comes straight back when the same cards
    are selected again. Share it only between states with the same
    `__main__.odds_basis`.
    """
    if not state.in_run or not state.hand or not (targets or queries):
        return None
    if known is None:
        known = {}

    def key(row: Target | CardQuery, discard: list[int]) -> tuple:
        # By the cards, not their positions: sorting the hand doesn't change the odds.
        # Face-down cards the hand's sorts tell about differ by `Card.latent`.
        picked = frozenset(Counter(replace(state.hand[i], highlighted=False) for i in discard).items())
        return row_key(row), picked

    def evaluate(row: Target | CardQuery, discard: list[int], cancel) -> DiscardOutcome:
        return (evaluate_cards(state, row, discard, cancel) if isinstance(row, CardQuery)
                else evaluate_discard(state, row, discard, cancel))

    named = [(t.name, t) for t in targets]
    if len(targets) > 1:
        named.insert(0, ("Any target", combine(targets)))
    redraw = can_redraw(state)
    selected = state.selected if redraw else []
    rows: list[Target | CardQuery] = [t for _, t in named] + list(queries)
    values = [(row, []) for row in rows] + [(row, selected) for row in rows if selected]

    def value(row: Target | CardQuery, discard: list[int]) -> Value:
        found = known.get(key(row, discard), PENDING)
        return found.probability if isinstance(found, DiscardOutcome) else found

    def report() -> Report:
        def after(row: Target | CardQuery) -> Value | None:
            return value(row, selected) if selected else None
        found = [TargetOdds(name, value(t, []), after(t)) for name, t in named]
        cards = [CardOdds(q, value(q, []), after(q)) for q in queries]
        partial = any(isinstance(v, Pending) for o in found + cards for v in (o.keep, o.discard))
        # This state's cards, in its order: a known outcome may be from before the hand was sorted.
        return Report(found, cards, [state.hand[i] for i in selected],
                      discard_draws(state, selected) if selected else 0, state.discards_left, redraw, partial)

    todo = list({key(row, discard): (row, discard) for row, discard in values
                 if key(row, discard) not in known}.items())
    for seconds, last in ((limits.quick, False), (limits.exact, True)):
        later = []
        for k, (row, discard) in todo:
            found = limited(cancel, seconds, lambda c, row=row, discard=discard: evaluate(row, discard, c))
            if isinstance(found, TooSlow) and not last:
                later.append((k, (row, discard)))
                continue
            known[k] = found
            if progress is not None:
                progress(report())
        todo = later
    return report()

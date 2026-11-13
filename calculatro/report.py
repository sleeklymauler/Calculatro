"""Odds for one game state, shared by the console output and the mod's panel."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from threading import Event

from .cards import CardQuery
from .engine import (
    DiscardOutcome,
    Estimate,
    discard_draws,
    estimate_cards,
    estimate_discard,
    evaluate_cards,
    evaluate_discard,
    evidence_unchecked,
)
from .hands import Target, combine
from .search import can_redraw
from .state import Card, GameState
from .worker import LIMITS, Control, Limits, TooSlow, limited


class Pending:
    """A value not worked out yet, in a partial report (`PENDING`)."""

    def __repr__(self) -> str:
        return "PENDING"


PENDING = Pending()

# Where an approximation comes from (`Approx.how`):
BOUNDS = "bounds"  # the odds of other values
SAMPLE = "sample"  # deals of the face-down cards drawn at random (engine.estimate_discard)
DRAWS = "draws"  # final hands drawn at random (engine.estimate_discard, without the hand's sorts)


@dataclass(frozen=True)
class Approx:
    """An approximation of odds not (yet) worked out exactly. `how` says where
    it comes from: from BOUNDS, the odds are between `low` and `high`, which
    is certain; from a SAMPLE or DRAWS, they're about `estimate`, and likely
    (not certainly) between `low` and `high`. `limit` is None while the exact odds
    are still being worked out, else what made the app give up on them
    (worker.TIME or MEMORY)."""
    low: Fraction
    high: Fraction
    how: str = BOUNDS
    limit: str | None = None
    estimate: Fraction | None = None  # a SAMPLE's or DRAWS'

    @property
    def certain(self) -> bool:
        """Whether the odds are certain to be between `low` and `high`."""
        return self.how == BOUNDS


# A value in a report: the odds, an approximation of them, given up on, or (in
# a partial report) still to come.
Value = Fraction | Approx | TooSlow | Pending


class Slow:
    """In `known`, a value whose quick try didn't finish (`SLOW`), with its exact
    odds still to come: a repeat skips the quick try, and shows its
    approximation at once."""

    def __repr__(self) -> str:
        return "SLOW"


SLOW = Slow()

# In `known`, (ESTIMATE, a value's key) holds its estimate (engine.Estimate), or
# None if it has none: it's made once, after the value's quick try.
ESTIMATE = "estimate"


def interval(value: Value | None) -> tuple[Fraction, Fraction] | None:
    """The range a value's odds are certain to be in: its odds both ways if
    exact, an approximation's bounds, or None if nothing certain is known (an
    estimate's range is only likely)."""
    if isinstance(value, Fraction):
        return value, value
    if isinstance(value, Approx) and value.certain:
        return value.low, value.high
    return None


def _fraction(x: float) -> Fraction:
    return Fraction(x).limit_denominator(10 ** 6)


def _sampled(found: Estimate, near: tuple[Fraction, Fraction] | None, limit: str | None) -> Approx:
    """An estimate as an Approx, within the bounds `near` (certain) if there are any."""
    low, high = near or (Fraction(0), Fraction(1))

    def within(x: float) -> Fraction:
        return min(max(_fraction(x), low), high)
    return Approx(within(found.low), within(found.high), DRAWS if found.draws else SAMPLE, limit,
                  within(found.mean))


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
    partial: bool = False  # some values are still to come (PENDING, or an Approx without a limit)


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
    worked out in three passes, so a quick one is never held up by a slow one:
    first each with `limits.quick` seconds; then an estimate of each that
    didn't finish, in about `limits.approx` seconds; then those, from the
    start, with `limits.exact` seconds each. One that doesn't finish then, or
    uses too much memory (`worker.Watchdog`), is TooSlow. The order in each
    pass is the panel's: Keep, then Discard. `progress` is given a partial
    report as each value is found.

    A value that doesn't finish its quick try shows an approximation (Approx)
    meanwhile, if it has one, and keeps it if the exact odds are given up on:

      * "Any target"'s odds are at least each hand's on its own and at most
        their sum (or 1), so its BOUNDS come from their values, and narrow as
        they're found. When they meet, they're its exact odds, and it isn't
        worked out at all. So "Any target" comes after the hands in each pass.
      * A target's value is estimated (engine.estimate_discard), within those
        bounds for "Any target": from a SAMPLE of the deals of the face-down
        cards the hand's sorts tell about, or from DRAWS of final hands. A
        card query's only from a SAMPLE (engine.estimate_cards): without the
        sorts, it's always quick.

    `known` holds each value as soon as it's worked out, or given up on, or
    found slow (SLOW), and its estimate (under ESTIMATE), so they outlive a
    cancelled report and come straight back when the same cards are selected
    again. Its keys hold each value's target and selection, so share it only
    between states alike in everything else the odds depend on: the same cards,
    jokers, blind and sorts, in whatever order the hand is in.
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

    def estimate(row: Target | CardQuery, discard: list[int], cancel, seconds: float) -> Estimate | None:
        return (estimate_cards(state, row, discard, cancel, seconds) if isinstance(row, CardQuery)
                else estimate_discard(state, row, discard, cancel, seconds))

    named = [(t.name, t) for t in targets]
    combined: Target | None = None  # "Any target", bounded by `targets`
    if len(targets) > 1:
        combined = combine(targets)
        named.insert(0, ("Any target", combined))
    redraw = can_redraw(state)
    selected = state.selected if redraw else []
    rows: list[Target | CardQuery] = [t for _, t in named] + list(queries)
    values = [(row, []) for row in rows] + [(row, selected) for row in rows if selected]

    def bounds(row: Target | CardQuery, discard: list[int]) -> tuple[Fraction, Fraction] | None:
        """Certain bounds on "Any target"'s odds from its hands' (None for other
        rows, or with nothing known): a hand whose odds aren't known could be
        anything from 0 to 1."""
        if row is not combined:
            return None
        each = [interval(value(t, discard)) or (Fraction(0), Fraction(1)) for t in targets]
        low, high = max(lo for lo, _ in each), min(Fraction(1), sum(hi for _, hi in each))
        return (low, high) if (low, high) != (0, 1) else None

    def value(row: Target | CardQuery, discard: list[int]) -> Value:
        k = key(row, discard)
        found = known.get(k)
        if isinstance(found, DiscardOutcome):
            return found.probability
        near = bounds(row, discard)
        if near is not None and near[0] == near[1]:
            return near[0]
        if found is SLOW or isinstance(found, TooSlow):
            limit = found.limit if isinstance(found, TooSlow) else None
            if isinstance(estimated := known.get((ESTIMATE, k)), Estimate):
                return _sampled(estimated, near, limit)
            if near is not None:
                return Approx(*near, limit=limit)
        return PENDING if found is None or found is SLOW else found

    def report() -> Report:
        def after(row: Target | CardQuery) -> Value | None:
            return value(row, selected) if selected else None
        found = [TargetOdds(name, value(t, []), after(t)) for name, t in named]
        cards = [CardOdds(q, value(q, []), after(q)) for q in queries]
        partial = any(isinstance(v, Pending) or (isinstance(v, Approx) and v.limit is None)
                      for o in found + cards for v in (o.keep, o.discard))
        # This state's cards, in its order: a known outcome may be from before the hand was sorted.
        return Report(found, cards, [state.hand[i] for i in selected],
                      discard_draws(state, selected) if selected else 0, state.discards_left, redraw, partial)

    sent = None

    def found_more() -> None:
        """Gives `progress` the report, if it shows anything new."""
        nonlocal sent
        if progress is None:
            return
        now = report()
        shows = any(v is not None and not isinstance(v, Pending)
                    for o in now.targets + now.cards for v in (o.keep, o.discard))
        if shows and now != sent:
            progress(now)
            sent = now

    # "Any target" after the hands, whose values bound it (sorted is stable).
    todo = sorted({key(row, discard): (row, discard) for row, discard in values
                   if known.get(key(row, discard), SLOW) is SLOW}.items(), key=lambda item: item[1][0] is combined)
    if todo:
        found_more()  # what's known already, as when the same cards are selected again

    def attempt(seconds: float, before: Slow | None) -> None:
        # In the quick pass: a try ran out of time before the hand's sorts were checked, which every value
        # that uses them does first, so those would too (The House's first hand sorted both ways: 0.9 s).
        unchecked = False
        for k, (row, discard) in todo:
            if known.get(k) is not before or isinstance(value(row, discard), Fraction):
                continue  # done, or its bounds met
            if unchecked and evidence_unchecked(state, discard):
                known[k] = SLOW
                continue
            found = limited(cancel, seconds, lambda c, row=row, discard=discard: evaluate(row, discard, c))
            known[k] = SLOW if isinstance(found, TooSlow) and before is None else found
            if before is None and isinstance(found, TooSlow) and evidence_unchecked(state, discard):
                unchecked = True
            found_more()

    # The quick pass tries what hasn't been tried (a value found slow before
    # goes straight to its estimate, or the exact pass), the exact pass what
    # it found slow. Between them, each value found slow is estimated once.
    attempt(limits.quick, None)
    for k, (row, discard) in todo:
        if known.get(k) is not SLOW or (ESTIMATE, k) in known or isinstance(value(row, discard), Fraction):
            continue
        # The estimate stops itself after `limits.approx`; the limit is for one that can't start.
        found = limited(cancel, 2 * limits.approx,
                        lambda c, row=row, discard=discard: estimate(row, discard, c, limits.approx))
        known[(ESTIMATE, k)] = found if isinstance(found, Estimate) else None
        found_more()
    attempt(limits.exact, SLOW)
    return report()

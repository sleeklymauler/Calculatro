"""Odds for one game state, shared by the console output and the mod's panel."""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from threading import Event
from time import monotonic

from .cards import CardQuery
from .engine import (
    DiscardOutcome,
    Estimate,
    Session,
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
type Value = Fraction | Approx | TooSlow | Pending


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
                 progress: Callable[[Report], None] | None = None, session: Session | None = None) -> Report | None:
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

    So that a panel of many slow values isn't left without any for long, once
    the quick tries that ran out have taken `limits.quick_pass` seconds in all,
    the quick pass estimates the values it has found slow, and from then on
    each one straight after its quick try.

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

    `session`: see engine.Session. Without it, the report keeps what the engine works out only for itself.
    """
    if not state.in_run or not state.hand or not (targets or queries):
        return None
    return _Builder(state, targets, queries, cancel, {} if known is None else known, limits, progress,
                    session or Session()).run()


class _Builder:
    """One call of `build_report`: its arguments, the values to work out, and
    the passes that work them out. `value` reads each from `known` (with
    `bounds` for "Any target", which reads its hands' values)."""

    def __init__(self, state: GameState, targets: Sequence[Target], queries: Sequence[CardQuery],
                 cancel: Control | Event | None, known: dict, limits: Limits,
                 progress: Callable[[Report], None] | None, session: Session) -> None:
        self.state, self.targets, self.queries = state, targets, queries
        self.cancel, self.known, self.limits = cancel, known, limits
        self.progress, self.session = progress, session
        self.named = [(t.name, t) for t in targets]
        self.combined: Target | None = None  # "Any target", bounded by `targets`
        if len(targets) > 1:
            self.combined = combine(targets)
            self.named.insert(0, ("Any target", self.combined))
        self.redraw = can_redraw(state)
        self.selected = state.selected if self.redraw else []
        rows: list[Target | CardQuery] = [t for _, t in self.named] + list(queries)
        values = [(row, []) for row in rows] + [(row, self.selected) for row in rows if self.selected]
        # Those still to work out, "Any target" after the hands, whose values bound it (sorted is stable).
        self.todo = sorted({self.key(row, discard): (row, discard) for row, discard in values
                            if known.get(self.key(row, discard), SLOW) is SLOW}.items(),
                           key=lambda item: item[1][0] is self.combined)
        self.sent: Report | None = None  # the last report given to `progress`

    def run(self) -> Report:
        # The quick pass tries what hasn't been tried (a value found slow before
        # goes straight to its estimate, or the exact pass), the exact pass what
        # it found slow. Between them (or during the quick pass, once it has
        # taken long enough), each value found slow is estimated once.
        if self.todo:
            self.found_more()  # what's known already, as when the same cards are selected again
        self.attempt(self.limits.quick, None)
        self.estimate_slow()
        self.attempt(self.limits.exact, SLOW)
        return self.report()

    def key(self, row: Target | CardQuery, discard: list[int]) -> tuple:
        # By the cards, not their positions: sorting the hand doesn't change the odds.
        # Face-down cards the hand's sorts tell about differ by `Card.latent`.
        picked = frozenset(Counter(replace(self.state.hand[i], highlighted=False) for i in discard).items())
        return row_key(row), picked

    def evaluate(self, row: Target | CardQuery, discard: list[int], cancel) -> DiscardOutcome:
        if isinstance(row, CardQuery):
            return evaluate_cards(self.state, row, discard, cancel, self.session)
        return evaluate_discard(self.state, row, discard, cancel, session=self.session)

    def estimate(self, row: Target | CardQuery, discard: list[int], cancel, seconds: float) -> Estimate | None:
        if isinstance(row, CardQuery):
            return estimate_cards(self.state, row, discard, cancel, seconds, self.session)
        return estimate_discard(self.state, row, discard, cancel, seconds, session=self.session)

    def bounds(self, row: Target | CardQuery, discard: list[int]) -> tuple[Fraction, Fraction] | None:
        """Certain bounds on "Any target"'s odds from its hands' (None for other
        rows, or with nothing known): a hand whose odds aren't known could be
        anything from 0 to 1."""
        if row is not self.combined:
            return None
        each = [interval(self.value(t, discard)) or (Fraction(0), Fraction(1)) for t in self.targets]
        low, high = max(lo for lo, _ in each), min(Fraction(1), sum(hi for _, hi in each))
        return (low, high) if (low, high) != (0, 1) else None

    def value(self, row: Target | CardQuery, discard: list[int]) -> Value:
        """What's known of a value so far: its odds, an approximation, TooSlow, or PENDING."""
        k = self.key(row, discard)
        found = self.known.get(k)
        if isinstance(found, DiscardOutcome):
            return found.probability
        near = self.bounds(row, discard)
        if near is not None and near[0] == near[1]:
            return near[0]
        if found is SLOW or isinstance(found, TooSlow):
            limit = found.limit if isinstance(found, TooSlow) else None
            if isinstance(estimated := self.known.get((ESTIMATE, k)), Estimate):
                return _sampled(estimated, near, limit)
            if near is not None:
                return Approx(*near, limit=limit)
        return PENDING if found is None or found is SLOW else found

    def report(self) -> Report:
        """The report of what's known so far."""
        def after(row: Target | CardQuery) -> Value | None:
            return self.value(row, self.selected) if self.selected else None
        found = [TargetOdds(name, self.value(t, []), after(t)) for name, t in self.named]
        cards = [CardOdds(q, self.value(q, []), after(q)) for q in self.queries]
        partial = any(isinstance(v, Pending) or (isinstance(v, Approx) and v.limit is None)
                      for o in found + cards for v in (o.keep, o.discard))
        # This state's cards, in its order: a known outcome may be from before the hand was sorted.
        state, selected = self.state, self.selected
        return Report(found, cards, [state.hand[i] for i in selected],
                      discard_draws(state, selected) if selected else 0, state.discards_left, self.redraw, partial)

    def found_more(self) -> None:
        """Gives `progress` the report, if it shows anything new."""
        if self.progress is None:
            return
        now = self.report()
        shows = any(v is not None and not isinstance(v, Pending)
                    for o in now.targets + now.cards for v in (o.keep, o.discard))
        if shows and now != self.sent:
            self.progress(now)
            self.sent = now

    def estimate_slow(self) -> None:
        """Estimates each value found slow that has no estimate yet."""
        known, approx = self.known, self.limits.approx
        for k, (row, discard) in self.todo:
            if known.get(k) is not SLOW or (ESTIMATE, k) in known or isinstance(self.value(row, discard), Fraction):
                continue
            # The estimate stops itself after `limits.approx`; the limit is for one that can't start.
            found = limited(self.cancel, 2 * approx,
                            lambda c, row=row, discard=discard: self.estimate(row, discard, c, approx))
            known[(ESTIMATE, k)] = found if isinstance(found, Estimate) else None
            self.found_more()

    def attempt(self, seconds: float, before: Slow | None) -> None:
        """A pass of exact tries of `seconds` each: the quick pass (`before` None) of the
        values not tried yet, or the exact pass (`before` SLOW) of those it found slow."""
        known = self.known
        # In the quick pass: a try ran out of time before the hand's sorts were checked, which every value
        # that uses them does first, so those would too (The House's first hand sorted both ways: 0.9 s).
        unchecked = False
        wasted = 0.0  # in the quick pass, on tries that ran out
        for k, (row, discard) in self.todo:
            if known.get(k) is not before or isinstance(self.value(row, discard), Fraction):
                continue  # done, or its bounds met
            if unchecked and evidence_unchecked(self.state, discard, self.session):
                known[k] = SLOW
                continue
            start = monotonic()
            found = limited(self.cancel, seconds, lambda c, row=row, discard=discard: self.evaluate(row, discard, c))
            known[k] = SLOW if isinstance(found, TooSlow) and before is None else found
            if before is None and isinstance(found, TooSlow):
                unchecked = unchecked or evidence_unchecked(self.state, discard, self.session)
                wasted += monotonic() - start
            self.found_more()
            if before is None and wasted >= self.limits.quick_pass:
                self.estimate_slow()

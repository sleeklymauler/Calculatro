"""Odds for one game state, shared by the console output and the mod's panel."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from threading import Event

from .cards import CardQuery
from .engine import DiscardOutcome, evaluate_cards, evaluate_discard
from .hands import Target, combine
from .search import can_redraw
from .state import Card, GameState


@dataclass(frozen=True)
class TargetOdds:
    name: str
    keep: Fraction  # keeping the hand, drawing nothing
    discard: Fraction | None  # discarding the selection; None if nothing is selected


@dataclass(frozen=True)
class CardOdds:
    query: CardQuery
    keep: Fraction  # the final hand after keeping the hand
    discard: Fraction | None  # after discarding the selection; None if nothing is selected


@dataclass(frozen=True)
class Report:
    targets: list[TargetOdds]  # "Any target" first when there are several
    cards: list[CardOdds]  # card queries over the final hand
    discarded: list[Card]  # the selection; empty if nothing is selected
    discard_draws: int  # cards drawn after discarding it
    discards_left: int
    redraw: bool = True  # False on the last hand with no discards: nothing is drawn after it


def build_report(state: GameState, targets: Sequence[Target], queries: Sequence[CardQuery] = (),
                 cancel: Event | None = None, known: dict | None = None) -> Report | None:
    """Odds of the target hands and card queries for keeping the hand vs.
    discarding the in-game selection. None outside a run, with an empty hand,
    or with nothing to calculate. With no way left to throw cards away and draw
    (`can_redraw`), the selection is the hand to be played, so there are no
    discard odds.

    `known` holds each row's odds as soon as they're worked out, so they outlive
    a cancelled report and come straight back when the same cards are selected
    again. Share it only between states with the same `__main__.odds_basis`.
    """
    if not state.in_run or not state.hand or not (targets or queries):
        return None
    if known is None:
        known = {}

    def odds(row: Target | CardQuery, discard: list[int]) -> DiscardOutcome:
        # By the cards, not their positions: sorting the hand doesn't change the odds.
        # By a query's text, since two parses of it aren't equal (their filters are closures).
        picked = frozenset(Counter(replace(state.hand[i], highlighted=False) for i in discard).items())
        key = (row.text or row) if isinstance(row, CardQuery) else row, picked
        found = known.get(key)
        if found is None:
            found = known[key] = (evaluate_cards(state, row, discard) if isinstance(row, CardQuery)
                                  else evaluate_discard(state, row, discard, cancel))
        return found

    named = [(t.name, t) for t in targets]
    if len(targets) > 1:
        named.insert(0, ("Any target", combine(targets)))
    keep = [odds(t, []) for _, t in named]
    card_keep = [odds(q, []) for q in queries]

    redraw = can_redraw(state)
    selected = state.selected if redraw else []
    if not selected:
        rows = [TargetOdds(name, k.probability, None) for (name, _), k in zip(named, keep)]
        cards = [CardOdds(q, k.probability, None) for q, k in zip(queries, card_keep)]
        return Report(rows, cards, [], 0, state.discards_left, redraw)

    after = [odds(t, selected) for _, t in named]
    card_after = [odds(q, selected) for q in queries]
    rows = [TargetOdds(name, k.probability, a.probability)
            for (name, _), k, a in zip(named, keep, after)]
    cards = [CardOdds(q, k.probability, a.probability) for q, k, a in zip(queries, card_keep, card_after)]
    # This state's cards, in its order: a known outcome may be from before the hand was sorted.
    return Report(rows, cards, [state.hand[i] for i in selected], (after or card_after)[0].draws, state.discards_left)

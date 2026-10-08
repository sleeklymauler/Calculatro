"""Odds for one game state, shared by the console output and the mod's panel."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from threading import Event

from .cards import CardQuery
from .engine import evaluate_cards, evaluate_discard
from .hands import Target, combine
from .state import Card, GameState


@dataclass(frozen=True)
class TargetOdds:
    name: str
    keep: Fraction  # keeping the hand (drawing only to refill it)
    discard: Fraction | None  # discarding the selection; None if nothing is selected


@dataclass(frozen=True)
class Report:
    targets: list[TargetOdds]  # "Any target" first when there are several
    cards: list[tuple[CardQuery, Fraction]]  # drawn-card odds; only with a selection
    keep_draws: int  # cards drawn when keeping the hand
    discarded: list[Card]  # the selection; empty if nothing is selected
    discard_draws: int  # cards drawn after discarding it
    discards_left: int


def build_report(state: GameState, targets: Sequence[Target], queries: Sequence[CardQuery] = (),
                 cancel: Event | None = None) -> Report | None:
    """Target odds for keeping the hand vs. discarding the in-game selection,
    plus card-query odds for the cards drawn after that discard.

    Keeping the hand draws nothing (unless it isn't full), so card queries are
    only calculated for a selection. None outside a run, with an empty hand, or
    with nothing to calculate.
    """
    if not state.in_run or not state.hand or not (targets or queries):
        return None
    named = [(t.name, t) for t in targets]
    if len(targets) > 1:
        named.insert(0, ("Any target", combine(targets)))
    keep = [evaluate_discard(state, t, [], cancel) for _, t in named]
    keep_draws = keep[0].draws if keep else 0

    selected = state.selected
    if not selected:
        rows = [TargetOdds(name, k.probability, None) for (name, _), k in zip(named, keep)]
        return Report(rows, [], keep_draws, [], 0, state.discards_left)

    after = [evaluate_discard(state, t, selected, cancel) for _, t in named]
    card_odds = [(q, evaluate_cards(state, q, selected)) for q in queries]
    first = (after or [o for _, o in card_odds])[0]
    rows = [TargetOdds(name, k.probability, a.probability)
            for (name, _), k, a in zip(named, keep, after)]
    cards = [(q, o.probability) for q, o in card_odds]
    return Report(rows, cards, keep_draws, first.discarded, first.draws, state.discards_left)

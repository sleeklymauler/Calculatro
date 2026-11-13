"""Trying every discard, to check the best-discard search against."""

from collections import Counter
from fractions import Fraction
from itertools import combinations

from calculatro.cards import CardQuery
from calculatro.engine import evaluate_cards, evaluate_discard
from calculatro.search import _identity, best_card_discards, best_discards
from calculatro.state import GameState


def brute_force(state: GameState, target, evaluate=evaluate_discard) -> tuple[Fraction, set]:
    """The best odds over every discard, and the tied discards as multisets of the cards thrown away."""
    forced = state.forced if state.bell else None
    others = [i for i in range(len(state.hand)) if i != forced]
    limit = min(state.select_limit, len(state.hand)) if state.discards_left > 0 or state.hands_left > 1 else 0
    discards = [[]]
    for n in range(limit + 1):
        if forced is not None and n == limit:
            break
        for subset in combinations(others, n):
            if forced is not None:
                discards.append([*subset, forced])
            elif n:
                discards.append(list(subset))
    odds = [(evaluate(state, target, d).probability, d) for d in discards]
    best = max(p for p, _ in odds)
    return best, {cards_of(state, d) for p, d in odds if p == best}


def cards_of(state: GameState, discard) -> frozenset:
    return frozenset(Counter(_identity(state.hand[i]) for i in discard).items())


def check(state: GameState, target) -> list[list[int]]:
    """Checks the search for a target hand, or a card query, against brute_force."""
    if isinstance(target, CardQuery):
        found = best_card_discards(state, target)
        best, tied = brute_force(state, target, evaluate_cards)
    else:
        found = best_discards(state, target)
        best, tied = brute_force(state, target)
    assert found.probability == best
    assert [cards_of(state, o) for o in found.options] == list({cards_of(state, o): 0 for o in found.options})
    assert {cards_of(state, o) for o in found.options} == tied
    sizes = [len(o) for o in found.options]
    assert sizes == sorted(sizes)
    return found.options

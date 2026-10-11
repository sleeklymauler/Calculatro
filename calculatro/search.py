"""The discards that give the best odds of reaching a target after one draw.

Every discard the game allows is compared with keeping the hand, for this
discard and draw only (no looking ahead to later discards or hands). Hand
cards that the target can't tell apart give the same odds wherever they are,
so the search only enumerates how many to discard of each group of them
(_groups), evaluating one discard per choice. The best choices are then
expanded into concrete discards, one for each distinct set of cards the
player could throw away (identical cards are one), ordered by Search.options.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import product
from threading import Event

from .engine import Cancelled, evaluate_discard
from .hands import Target, bell_matters, card_key
from .state import Card, GameState


@dataclass(frozen=True)
class Search:
    # Hand indices to discard, one list per option, best first: fewest cards,
    # then keeping the higher ranks, then the leftmost cards. [] keeps the hand.
    options: list[list[int]]
    probability: Fraction


def best_discards(state: GameState, target: Target, cancel: Event | None = None) -> Search:
    """The discards (or keeping the hand) with the best odds of reaching `target`.

    Discards hold at most `state.select_limit` cards, and with Cerulean Bell
    always the forced card. Without discards left, a hand played to throw cards
    away does the same, as long as another hand is left to play after it
    (`can_redraw`); otherwise only keeping is an option.
    Raises engine.Cancelled soon after `cancel` is set.
    """
    hand = state.hand
    forced = state.forced if state.bell else None
    limit = min(state.select_limit, len(hand)) if can_redraw(state) else 0
    groups = _groups(state, target, forced)
    budget = limit - (forced is not None)

    best: Fraction | None = None
    winners: list[tuple[int, ...] | None] = []
    choices: list[tuple[int, ...] | None] = [None]  # keeping the hand
    if budget >= 0:
        choices += [c for c in _counts([len(g) for g in groups], budget) if forced is not None or any(c)]
    for counts in choices:
        if cancel is not None and cancel.is_set():
            raise Cancelled
        discard = _representative(groups, counts, forced)
        p = evaluate_discard(state, target, discard, cancel).probability
        if best is None or p > best:
            best, winners = p, [counts]
        elif p == best:
            winners.append(counts)

    options = {tuple(sorted(o)) for counts in winners for o in _expand(hand, groups, counts, forced)}
    return Search(sorted((list(o) for o in options), key=lambda o: _order(hand, o)), best)


def can_redraw(state: GameState) -> bool:
    """Whether the player can still throw cards away and draw before their last hand:
    with a discard, or by playing a hand when there's another to play after it."""
    return state.discards_left > 0 or state.hands_left > 1


def _groups(state: GameState, target: Target, forced: int | None) -> list[list[int]]:
    """Hand indices (bar the forced card) in groups whose cards the odds can't tell apart.

    Face-down cards are one group. A known card is grouped by which of the
    target's requirements it counts towards: the search only looks at kept
    cards through those. With Cerulean Bell the card that ends up forced
    matters by its class too (`_bell_after_draw`), so there it's by class.
    """
    rules = state.rules
    exact = state.bell and bell_matters(target, rules)
    patterns = target.patterns(rules)
    groups: dict[object, list[int]] = {}
    for i, c in enumerate(state.hand):
        if i == forced:
            continue
        key = card_key(c, rules)
        if c.hidden:
            group: object = "hidden"
        elif exact:
            group = ("key", key)
        else:
            group = frozenset((p, q) for p, pattern in enumerate(patterns)
                              for q, req in enumerate(pattern) if key is not None and key in req.keys)
        groups.setdefault(group, []).append(i)
    return list(groups.values())


def _counts(sizes: list[int], budget: int) -> Iterator[tuple[int, ...]]:
    """Every way to take up to `sizes[g]` from each group, `budget` in all."""
    if not sizes:
        yield ()
        return
    for k in range(min(sizes[0], budget) + 1):
        for rest in _counts(sizes[1:], budget - k):
            yield (k, *rest)


def _representative(groups: list[list[int]], counts: tuple[int, ...] | None, forced: int | None) -> list[int]:
    if counts is None:
        return []
    discard = [i for g, k in zip(groups, counts) for i in g[:k]]
    return discard + ([forced] if forced is not None else [])


def _identity(c: Card) -> Card:
    """What the player sees of a card: cards alike in every way are interchangeable."""
    return replace(c, highlighted=False, forced=False)


def _expand(hand: list[Card], groups: list[list[int]], counts: tuple[int, ...] | None,
            forced: int | None) -> Iterator[list[int]]:
    """The distinct discards taking counts[g] cards from each group (the leftmost of
    identical ones); None keeps the hand."""
    if counts is None:
        yield []
        return
    per_group: list[list[list[int]]] = []
    for g, k in zip(groups, counts):
        alike: dict[Card, list[int]] = {}
        for i in g:
            alike.setdefault(_identity(hand[i]), []).append(i)
        per_group.append([[i for same, n in zip(alike.values(), split) for i in same[:n]]
                          for split in _counts([len(s) for s in alike.values()], k) if sum(split) == k])
    for parts in product(*per_group):
        yield [i for part in parts for i in part] + ([forced] if forced is not None else [])


def _order(hand: list[Card], option: tuple[int, ...] | list[int]) -> tuple:
    # A face-down card's rank is unknown, so it's thrown away before any known one.
    ranks = sorted((0 if hand[i].hidden else hand[i].rank_id or 0 for i in option), reverse=True)
    return len(option), ranks, list(option)

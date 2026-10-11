"""The discards that give the best odds of reaching a target after one draw.

Every discard the game allows is compared with keeping the hand, for this
discard and draw only (no looking ahead to later discards or hands). Hand
cards that the target can't tell apart give the same odds wherever they are,
so the search only enumerates how many to discard of each group of them
(_groups), evaluating one discard per choice. Choices that are the same up to
relabelling ranks or suits share their odds (_relabelled), and for several
hands the odds of each bound those of any of them, which rules out most
choices (best_discards). The engine's work is shared between the discards
(engine.Reuse), since they all draw from the same unseen cards. The best
choices are then expanded into concrete discards, one for each distinct set
of cards the player could throw away (identical cards are one), ordered by
Search.options.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import product
from threading import Event

from .engine import Cancelled, Reuse, evaluate_discard
from .hands import RANKS, WILD, ClassKey, Pattern, Target, bell_matters, card_key, flush_suits, get_target
from .state import SUITS, Card, GameState


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

    reuse = Reuse(hand)  # every discard draws from the same pool
    relabelled: dict[Target, Callable[[list[int]], object]] = {}
    known: dict[tuple[Target, object], Fraction] = {}

    def odds(goal: Target, discard: list[int]) -> Fraction:
        if goal not in relabelled:
            relabelled[goal] = _relabelled(state, goal)
        key = goal, relabelled[goal](discard)
        p = known.get(key)
        if p is None:
            if cancel is not None and cancel.is_set():
                raise Cancelled
            p = known[key] = evaluate_discard(state, goal, discard, cancel, reuse).probability
        return p

    choices: list[tuple[int, ...] | None] = [None]  # keeping the hand
    if budget >= 0:
        choices += [c for c in _counts([len(g) for g in groups], budget) if forced is not None or any(c)]
    discards = [_representative(groups, counts, forced) for counts in choices]
    # The odds of any of several hands are at least each one's and at most
    # their sum (or 1), and each hand alone is much quicker to work out than
    # all together. Taking the discards by that upper bound, highest first,
    # only the ones that could still be best need the exact odds, and not
    # even those whose bounds meet.
    lows: list[Fraction] = [Fraction(0)] * len(choices)
    highs: list[Fraction] = [Fraction(1)] * len(choices)
    if len(target.hands) > 1:
        for j, discard in enumerate(discards):
            each = [odds(get_target(h), discard) for h in target.hands]
            lows[j], highs[j] = max(each), min(Fraction(1), sum(each))
    floor = max(lows)

    best: Fraction | None = None
    winners: list[tuple[int, ...] | None] = []
    for j in sorted(range(len(choices)), key=lambda j: -highs[j]):
        if highs[j] < max(floor, best or 0):
            break
        p = lows[j] if lows[j] == highs[j] else odds(target, discards[j])
        if best is None or p > best:
            best, winners = p, [choices[j]]
        elif p == best:
            winners.append(choices[j])

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


def _relabelled(state: GameState, target: Target) -> Callable[[list[int]], object]:
    """A key on discards, the same for two of them only if their odds of `target` are.

    The odds depend on the classes of the kept known cards and of the unseen
    ones, and on how many cards are discarded and kept face down. They don't
    change when a rank or suit the target can't tell apart is dropped from
    the classes, or when ranks or suits are relabelled in a way that maps the
    target's patterns onto themselves. Full House doesn't look at suits, and
    swapping any two ranks keeps its patterns, so throwing away a 7 is as good
    as throwing away an 8 if as many of each are unseen. Ranks are relabelled
    if the patterns allow it, else suits, and the key is then what's kept and
    unseen of each rank (or suit), in sorted order.

    The Mark draws face cards apart, so there a face card's rank is only
    swapped for another face card's. With Cerulean Bell the forced card's
    class matters too (see _groups), so discards aren't relabelled.
    """
    hand = state.hand
    rules = state.rules

    def counts(discard: list[int]) -> tuple[int, int]:
        return len(discard), sum(hand[i].hidden for i in discard)

    if state.bell:
        return lambda discard: (counts(discard), tuple(sorted(discard)))
    patterns = target.patterns(rules)
    reqs = {req for pattern in patterns for req in pattern}
    blind_to_suits = all((r, t) in req.keys for req in reqs for r, _ in req.keys for t in (*SUITS, WILD))
    blind_to_ranks = not state.mark and all((t, s) in req.keys for req in reqs for _, s in req.keys for t in RANKS)

    def label(key: ClassKey) -> ClassKey:
        return 0 if blind_to_ranks else key[0], "" if blind_to_suits else key[1]

    def shape(pattern: Pattern, swap: Callable[[ClassKey], ClassKey] = lambda k: k) -> frozenset:
        return frozenset(Counter((frozenset(swap(label(k)) for k in req.keys), req.need) for req in pattern).items())

    shapes = {shape(pattern) for pattern in patterns}

    def symmetric(blocks: list[list], axis: int) -> bool:
        """Whether swapping any two ranks (axis 0) or suits (1) in a block maps the patterns onto themselves."""
        def swapper(a: object, b: object) -> Callable[[ClassKey], ClassKey]:
            return lambda k: tuple((b if x == a else a if x == b else x) if i == axis else x
                                   for i, x in enumerate(k))
        return all({shape(pattern, swapper(a, b)) for pattern in patterns} == shapes
                   for block in blocks for a, b in zip(block, block[1:]))

    faces = (11, 12, 13)
    rank_blocks = ([[r for r in RANKS if r not in faces], list(faces)] if state.mark and not rules.pareidolia
                   else [list(RANKS)])
    suit_blocks = [list(flush_suits(rules))]  # with Smeared Joker, classes only have these
    if not blind_to_ranks and symmetric(rank_blocks, 0):
        axis, blocks = 0, rank_blocks
    elif not blind_to_suits and symmetric(suit_blocks, 1):
        axis, blocks = 1, suit_blocks
    else:
        axis, blocks = None, []
    block_of = {x: b for b, block in enumerate(blocks) for x in block}

    def labels(cards: list[Card]) -> list[ClassKey]:
        return [label(k) for c in cards if (k := card_key(c, rules)) is not None]

    def by_label(keys: list[ClassKey]) -> tuple[dict[object, list], list[ClassKey]]:
        """The keys' other parts by their rank (or suit) in a block, and the keys of none."""
        out: dict[object, list] = {x: [] for x in block_of}
        rest = []
        for k in keys:
            if axis is not None and k[axis] in out:
                out[k[axis]].append(k[1 - axis])
            else:
                rest.append(k)
        return out, rest

    unseen, _ = by_label(labels(state.deck + state.hidden_pool))  # the same for every discard

    def key(discard: list[int]) -> object:
        thrown = set(discard)
        kept, rest = by_label(labels([c for i, c in enumerate(hand) if i not in thrown]))
        return counts(discard), tuple(sorted(rest)), tuple(sorted(
            (block_of[x], tuple(sorted(kept[x])), tuple(sorted(unseen[x]))) for x in block_of))

    return key


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

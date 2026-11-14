"""The discards that give the best odds of reaching a target after one draw.

Every discard the game allows is compared with keeping the hand, for this
discard and draw only (no looking ahead to later discards or hands). Hand
cards that the target can't tell apart give the same odds wherever they are,
so the search only enumerates how many to discard of each group of them
(_groups), evaluating one discard per choice. Choices that are the same up to
relabelling ranks or suits share their odds (_relabelled), and for several
hands the odds of each bound those of any of them, which rules out most
choices (best_discards). Once odds of 1 are found, one draw that misses rules
a choice out (engine.can_miss). The engine's work is shared between the discards
(engine.Reuse), since they all draw from the same unseen cards. The best
choices are then expanded into concrete discards, one for each distinct set
of cards the player could throw away (identical cards are one), ordered by
Search.options.

Card queries (best_card_discards) only look at whether each kept card
matches, so their groups are matching, non-matching and face-down cards,
and every choice of counts is simply evaluated.

A face-down card the hand's sorts tell about (`Card.latent`) has a place of
its own in them, so it's a group of its own, and discards aren't relabelled:
which face-down card is thrown away matters (docs/probability-model.md).

A search records what it has found as it goes (Progress), so that one given
up on can still answer from estimates of the choices that could be best
(estimated_best).
"""

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import pairwise, product
from random import Random
from threading import Event
from time import monotonic

from .cards import CardQuery
from .engine import (
    Cancelled,
    Estimate,
    Reuse,
    can_miss,
    estimate_cards,
    estimate_discard,
    evaluate_cards,
    evaluate_discard,
    rules_after,
    with_usable_evidence,
)
from .hands import RANKS, WILD, ClassKey, Pattern, Target, bell_matters, card_key, flush_suits, get_target
from .rules import Rules
from .state import SUITS, Card, GameState


@dataclass(frozen=True)
class Estimated:
    """How a search given up on answered from estimates (estimated_best):
    the best option's odds are likely `low` to `high` (certainly, if they're
    equal), from estimates of final hands drawn at random if `draws`, else of
    deals of the face-down cards. `limit` is what stopped the exact search
    (worker.TIME or MEMORY), once the caller says."""
    low: Fraction
    high: Fraction
    draws: bool
    limit: str | None = None


@dataclass(frozen=True)
class Search:
    # Hand indices to discard, one list per option, best first: fewest cards,
    # then keeping the higher ranks, then the leftmost cards. [] keeps the hand.
    options: list[list[int]]
    probability: Fraction  # an estimate's, if `estimated`
    estimated: Estimated | None = None  # the options are the best by estimates, not certainly the best


type Estimator = Callable[[list[int], Event | None, float], Estimate | Fraction]


class Progress:
    """What a search has found so far, filled in as it goes, for
    estimated_best: each choice of how many cards to discard from each group
    (`choices`, None keeping the hand) and a discard for it, bounds on its
    odds that are certain (`lows`, `highs`), its exact odds where they're
    known (`exact`), the choices certainly not the best (`out`), and how to
    estimate a choice's odds in about so many seconds (`estimate`; exact
    where they're quick)."""

    def __init__(self) -> None:
        self.state: GameState | None = None
        self.groups: list[list[int]] = []
        self.forced: int | None = None
        self.choices: list[tuple[int, ...] | None] = []
        self.discards: list[list[int]] = []
        self.lows: list[Fraction] = []
        self.highs: list[Fraction] = []
        self.exact: dict[int, Fraction] = {}
        self.out: set[int] = set()
        self.estimate: Estimator | None = None

    def start(self, state: GameState, groups: list[list[int]], forced: int | None,
              choices: list[tuple[int, ...] | None], estimate: Estimator) -> None:
        self.state, self.groups, self.forced, self.choices = state, groups, forced, choices
        self.discards = [_representative(groups, counts, forced) for counts in choices]
        self.lows, self.highs = [Fraction(0)] * len(choices), [Fraction(1)] * len(choices)
        self.estimate = estimate


def best_discards(state: GameState, target: Target, cancel: Event | None = None,
                  progress: Progress | None = None) -> Search:
    """The discards (or keeping the hand) with the best odds of reaching `target`.

    Discards hold at most `state.select_limit` cards, and with Cerulean Bell
    always the forced card. Without discards left, a hand played to throw cards
    away does the same, as long as another hand is left to play after it
    (`can_redraw`); otherwise only keeping is an option.
    Raises engine.Cancelled soon after `cancel` is set, with what it has
    found by then in `progress`.
    """
    state = with_usable_evidence(state, cancel)
    hand = state.hand
    forced = state.forced if state.bell else None
    limit = min(state.select_limit, len(hand)) if can_redraw(state) else 0
    groups = _groups(state, target, forced)
    budget = limit - (forced is not None)

    reuse = Reuse(hand)  # every discard draws from the same pool
    rule_sets = _rule_sets(state)
    relabelled: dict[Target, list[Callable[[list[int]], object]]] = {}
    known: dict[tuple[Target, object], Fraction] = {}

    def odds(goal: Target, discard: list[int]) -> Fraction:
        if goal not in relabelled:
            relabelled[goal] = [_relabelled(state, goal, rules, cancel) for rules in rule_sets]
        key = goal, tuple(k(discard) for k in relabelled[goal])
        p = known.get(key)
        if p is None:
            if cancel is not None and cancel.is_set():
                raise Cancelled
            p = known[key] = evaluate_discard(state, goal, discard, cancel, reuse).probability
        return p

    choices: list[tuple[int, ...] | None] = [None]  # keeping the hand
    if budget >= 0:
        choices += [c for c in _counts([len(g) for g in groups], budget) if forced is not None or any(c)]
    progress = progress or Progress()
    progress.start(state, groups, forced, choices,
                   lambda discard, cancel, seconds: estimate_discard(state, target, discard, cancel, seconds))
    discards = progress.discards
    # The odds of any of several hands are at least each one's and at most
    # their sum (or 1), and each hand alone is much quicker to work out than
    # all together. Taking the discards by that upper bound, highest first,
    # only the ones that could still be best need the exact odds, and not
    # even those whose bounds meet.
    lows, highs = progress.lows, progress.highs
    if len(target.hands) > 1:
        for j, discard in enumerate(discards):
            each = [odds(get_target(h), discard) for h in target.hands]
            lows[j], highs[j] = max(each), min(Fraction(1), sum(each))
    floor = max(lows)

    rng = Random(0)  # only decides how quickly a miss is found, never the result
    best: Fraction | None = None
    winners: list[tuple[int, ...] | None] = []
    for j in sorted(range(len(choices)), key=lambda j: -highs[j]):
        if cancel is not None and cancel.is_set():
            raise Cancelled
        if highs[j] < max(floor, best or 0):
            break
        if lows[j] == highs[j]:
            p = lows[j]
        elif max(floor, best or 0) == 1 and can_miss(state, target, discards[j], rng, cancel=cancel):
            progress.out.add(j)
            continue  # below 1, so it can't tie, and how far below doesn't matter
        else:
            p = odds(target, discards[j])
        progress.exact[j] = p
        if best is None or p > best:
            best, winners = p, [choices[j]]
        elif p == best:
            winners.append(choices[j])

    options = {tuple(sorted(o)) for counts in winners for o in _expand(hand, groups, counts, forced)}
    return Search(sorted((list(o) for o in options), key=lambda o: _order(hand, o)), best)


def best_card_discards(state: GameState, query: CardQuery, cancel: Event | None = None,
                       progress: Progress | None = None) -> Search:
    """The discards (or keeping the hand) with the best odds of a final hand
    satisfying `query`, under the same rules as best_discards.

    The odds only look at whether a kept card matches the query (under each
    set of rules it may be judged under, `_rule_sets`), or that it's face
    down, so those are the groups whose counts are enumerated.
    Raises engine.Cancelled soon after `cancel` is set, with what it has found
    by then in `progress`.
    """
    state = with_usable_evidence(state, cancel)
    hand = state.hand
    rule_sets = _rule_sets(state)
    forced = state.forced if state.bell else None
    limit = min(state.select_limit, len(hand)) if can_redraw(state) else 0
    budget = limit - (forced is not None)
    groups: dict[object, list[int]] = {}
    for i, c in enumerate(hand):
        if i != forced:
            group = _hidden_group(c) if c.hidden else tuple(query.filter.test(c, rules) for rules in rule_sets)
            groups.setdefault(group, []).append(i)
    group_list = list(groups.values())

    choices: list[tuple[int, ...] | None] = [None]  # keeping the hand
    if budget >= 0:
        choices += [c for c in _counts([len(g) for g in group_list], budget) if forced is not None or any(c)]

    def estimate(discard: list[int], cancel: Event | None, seconds: float) -> Estimate | Fraction:
        # Without the hand's sorts telling about face-down cards, a query's odds are quick.
        found = estimate_cards(state, query, discard, cancel, seconds)
        return found if found is not None else evaluate_cards(state, query, discard, cancel).probability
    progress = progress or Progress()
    progress.start(state, group_list, forced, choices, estimate)
    best: Fraction | None = None
    winners: list[tuple[int, ...] | None] = []
    for j, counts in enumerate(choices):
        if cancel is not None and cancel.is_set():
            raise Cancelled
        p = progress.exact[j] = evaluate_cards(state, query, progress.discards[j], cancel).probability
        if best is None or p > best:
            best, winners = p, [counts]
        elif p == best:
            winners.append(counts)

    options = {tuple(sorted(o)) for counts in winners for o in _expand(hand, group_list, counts, forced)}
    return Search(sorted((list(o) for o in options), key=lambda o: _order(hand, o)), best)


FIRST_SHARE = 0.02  # the most seconds each choice's first estimate gets (estimated_best)


def estimated_best(progress: Progress, cancel: Event | None = None, seconds: float = 5.0) -> Search | None:
    """The best discards by estimates, for a search given up on, from what it
    had found (`progress`); None if it hadn't got as far as the choices.

    The choices that could still be best are those whose upper bound reaches
    the highest lower bound or exact odds found. Each is estimated
    (`Progress.estimate`, within its bounds) in rounds, each giving every
    choice twice the time the round before did. After each, those whose
    likely range falls below another's are dropped, and then all but the
    half of those estimated with the highest upper bounds (those known
    exactly cost nothing to keep), so each round takes about as long,
    until one is left or the next round wouldn't fit in about `seconds`.
    The best is the one with the highest likely lower bound (or exact odds),
    so that one estimated from a few lucky samples can't win; with every
    option alike in the cards it throws away (and those of choices tied with
    it exactly), and its range, which is only likely, so the answer is marked
    (`Search.estimated`). Unless every choice it looked at turned out quick to
    work out exactly (as card queries are): the answer is then exact.
    Raises engine.Cancelled soon after `cancel` is set.
    """
    state, estimate = progress.state, progress.estimate
    if state is None or estimate is None or not progress.choices:
        return None
    deadline = monotonic() + seconds
    lows, highs, exact = progress.lows, progress.highs, dict(progress.exact)
    floor = max([*lows, *exact.values()])
    ranges = {j: (p, p) for j, p in exact.items()}  # likely ranges
    means: dict[int, Fraction] = dict(exact)
    open_ = [j for j in range(len(progress.choices)) if j not in progress.out and highs[j] >= floor]
    draws, guessed = False, False
    per = min(FIRST_SHARE, seconds / 8 / max(1, len(open_)))
    while True:
        for j in [j for j in open_ if j not in exact]:
            found = estimate(progress.discards[j], cancel, per)
            if isinstance(found, Fraction):
                exact[j] = means[j] = found
                ranges[j] = found, found
                continue
            draws, guessed = draws or found.draws, True

            def within(x: float, j: int = j) -> Fraction:
                return min(max(Fraction(x).limit_denominator(10 ** 6), lows[j]), highs[j])
            ranges[j], means[j] = (within(found.low), within(found.high)), within(found.mean)
        best_low = max(ranges[j][0] for j in open_)
        open_ = [j for j in open_ if ranges[j][1] >= best_low]
        guesses = [j for j in open_ if j not in exact]  # those known exactly cost nothing to keep
        if len(guesses) > 1:
            kept = sorted(guesses, key=lambda j: (ranges[j][1], means[j]), reverse=True)[:(len(guesses) + 1) // 2]
            open_ = [j for j in open_ if j in exact or j in kept]
        left = sum(j not in exact for j in open_)
        per *= 2
        if len(open_) <= 1 or not left or monotonic() + per * left > deadline:
            break
    first = max(open_, key=lambda j: (ranges[j][0], means[j]))
    top = means[first]
    winners = [j for j in open_ if j in exact and exact[j] == top] if first in exact else [first]
    hand = state.hand
    options = {tuple(sorted(o)) for j in winners
               for o in _expand(hand, progress.groups, progress.choices[j], progress.forced)}
    return Search(sorted((list(o) for o in options), key=lambda o: _order(hand, o)), top,
                  Estimated(*ranges[first], draws) if guessed else None)


def can_redraw(state: GameState) -> bool:
    """Whether the player can still throw cards away and draw before their last hand:
    with a discard, or by playing a hand when there's another to play after it."""
    return state.discards_left > 0 or state.hands_left > 1


def _rule_sets(state: GameState) -> list[Rules]:
    """Every set of rules a final hand may be judged under: the current ones,
    and those Crimson Heart may leave after a play (`engine.rules_after`, the
    same for any play). Hand cards are only interchangeable if they are under
    each of them."""
    rule_sets = [state.rules]
    if state.hand:
        rule_sets += [rules for rules, _ in rules_after(state, [0]) if rules not in rule_sets]
    return rule_sets


def _groups(state: GameState, target: Target, forced: int | None) -> list[list[int]]:
    """Hand indices (bar the forced card) in groups whose cards the odds can't tell apart.

    Face-down cards are one group, bar those the hand's sorts tell about, which
    are one each (_hidden_group). A known card is grouped by which of the
    target's requirements it counts towards, under each set of rules the
    final hand may be judged under (`_rule_sets`): the search only looks at
    kept cards through those. With Cerulean Bell the card that ends up forced
    matters by its class too (`_bell_after_draw`), so there it's by class.
    """
    rules = state.rules
    exact = state.bell and bell_matters(target, rules)
    tables = [(r, target.patterns(r)) for r in _rule_sets(state)]
    groups: dict[object, list[int]] = {}
    for i, c in enumerate(state.hand):
        if i == forced:
            continue
        if c.hidden:
            group: object = _hidden_group(c)
        elif exact:
            group = ("key", card_key(c, rules))
        else:
            group = tuple(frozenset((p, q) for p, pattern in enumerate(patterns)
                                    for q, req in enumerate(pattern) if key is not None and key in req.keys)
                          for r, patterns in tables for key in (card_key(c, r),))
        groups.setdefault(group, []).append(i)
    return list(groups.values())


def _hidden_group(card: Card) -> object:
    """The group of a face-down card: all together, the marked ones apart (they're
    face cards), but one the hand's sorts tell about is on its own, since its
    place in them says what it can be. Two such could be merged only if the
    posterior were the same with them swapped."""
    return ("hidden", card.marked) if card.latent is None else ("latent", card.latent)


def _relabelled(state: GameState, target: Target, rules: Rules,
                cancel: Event | None = None) -> Callable[[list[int]], object]:
    """A key on discards, the same for two of them only if their odds of `target`
    with the final hand judged under `rules` are.

    The odds depend on the classes of the kept known cards and of the unseen
    ones, and on how many cards are discarded and kept face down. They don't
    change when a rank or suit the target can't tell apart is dropped from
    the classes, or when ranks or suits are relabelled in a way that maps the
    target's patterns onto themselves. Full House doesn't look at suits, and
    swapping any two ranks keeps its patterns, so throwing away a 7 is as good
    as throwing away an 8 if as many of each are unseen. Ranks are relabelled
    if the patterns allow it, else suits, and the key is then what's kept and
    unseen of each rank (or suit), in sorted order.

    The Mark's marked cards are drawn from the face cards apart, so with any
    of them a face card's rank is only swapped for another face card's. With Cerulean Bell the forced card's
    class matters too if it can change whether the target is played (`bell_matters`, see _groups); if it
    can't, the engine leaves it out, as it does the force. With face-down cards the hand's sorts
    tell about which of them is discarded does, so discards aren't relabelled.
    Nor are they when the sorts tell about cards discarded face down before:
    what's left to draw then isn't the same for a rank and the one it'd be
    swapped with. (Relabelling would only be right if it mapped the
    observations onto themselves.)
    """
    hand = state.hand

    def counts(discard: list[int]) -> tuple[int, int]:
        return len(discard), sum(hand[i].hidden for i in discard), sum(hand[i].marked for i in discard)

    if (state.bell and bell_matters(target, rules)) or any(o.latent for o in state.observed):
        return lambda discard: (counts(discard), tuple(sorted(discard)))
    patterns = target.patterns(rules)
    reqs = {req for pattern in patterns for req in pattern}
    blind_to_suits = all((r, t) in req.keys for req in reqs for r, _ in req.keys for t in (*SUITS, WILD))
    blind_to_ranks = not state.marked and all((t, s) in req.keys for req in reqs for _, s in req.keys for t in RANKS)

    def label(key: ClassKey) -> ClassKey:
        return 0 if blind_to_ranks else key[0], "" if blind_to_suits else key[1]

    def shape(pattern: Pattern, swap: Callable[[ClassKey], ClassKey] = lambda k: k) -> frozenset:
        return frozenset(Counter((frozenset(swap(label(k)) for k in req.keys), req.need) for req in pattern).items())

    shapes = {shape(pattern) for pattern in patterns}

    def symmetric(blocks: list[list], axis: int) -> bool:
        """Whether swapping any two ranks (axis 0) or suits (1) in a block maps the patterns onto themselves."""
        def swapper(a: object, b: object) -> Callable[[ClassKey], ClassKey]:
            if cancel is not None and cancel.is_set():  # each swap reshapes every pattern: thousands of them with
                raise Cancelled                         # Four Fingers and Shortcut, about 10 ms
            return lambda k: tuple((b if x == a else a if x == b else x) if i == axis else x
                                   for i, x in enumerate(k))
        return all({shape(pattern, swapper(a, b)) for pattern in patterns} == shapes
                   for block in blocks for a, b in pairwise(block))

    faces = (11, 12, 13)
    rank_blocks = [[r for r in RANKS if r not in faces], list(faces)] if state.marked else [list(RANKS)]
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

    unseen, _ = by_label(labels(state.unseen))  # the same for every discard

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

"""Exact probability that a hand reaches a target after one discard and draw.

The final hand is the kept known cards plus `m` cards drawn uniformly without
replacement from the unseen pool: the undrawn deck plus any face-down cards
in hand, which are equally unknown to the player. Kept face-down cards are
modelled as part of that draw, since by symmetry it's the same distribution.

Cards are grouped into classes that every pattern treats identically, so for
example Pair only distinguishes 13 ranks. Draws are then enumerated class by
class, multivariate-hypergeometric style, with two shortcuts:
  * once any pattern is satisfied, every completion of the draw counts, so
    the subtree's weight is a single binomial coefficient;
  * a pattern is dropped once it needs more cards than remain to be drawn, or
    more of some class than the remaining classes can supply. Both conditions
    only get worse deeper in the tree, so dropped patterns stay dropped.
A pattern's requirements may overlap (one card counting towards several), so
"cards needed" is bounded by the largest deficit sum over groups of mutually
disjoint requirements; a drawn card lowers each group's sum by at most one.
Classes are ordered by the first pattern they count towards through a
requirement on a single rank, so each pattern's classes are processed close
together and patterns resolve early. A pattern only joins the search at its
first class; before that its state follows from the kept cards alone.
Subtrees are memoized on what's left to do: for each live pattern, its unmet
requirements, each identified by which of the remaining classes it covers
plus its deficit. Many draw orders reach the same position, and different
patterns often leave the same remainder, so this key is canonical.

The search has to remember something about every suit whose patterns are
still live, so when patterns of several suits are linked through other
suits' cards (Four Fingers' Straight Flush lets any card of the run's 4th
rank complete it) its size multiplies across suits. Targets with patterns of
several suits are therefore split by suit with inclusion-exclusion; see
_union_by_suit.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from fractions import Fraction
from itertools import combinations, repeat
from math import comb
from operator import gt
from threading import Event

from .cards import CardQuery
from .hands import WILD, ClassKey, Pattern, Requirement, Target, card_key, suit_keys
from .rules import Rules
from .state import SUITS, Card, GameState


class Cancelled(Exception):
    """Raised when a calculation's `cancel` event is set before it finishes."""


def probability(target: Target, kept: list[Card], pool: list[Card], draws: int,
                rules: Rules = Rules(), cancel: Event | None = None) -> Fraction:
    """P(kept + `draws` random cards from `pool` contains `target` under `rules`).

    Raises Cancelled soon after `cancel` is set.
    """
    draws = max(0, min(draws, len(pool)))
    patterns = target.patterns(rules)
    rest, by_suit = _split_by_suit(patterns)
    # An unseen Wild Card counts as every suit, which ties the suits back together.
    if len(by_suit) > 1 and not any(key[1] == WILD for key in map(card_key, pool, repeat(rules)) if key):
        favourable = _union_by_suit(rest, by_suit, kept, pool, draws, rules, cancel)
    else:
        favourable = _favourable(patterns, [1] * len(patterns), kept, pool, draws, rules, cancel)
    return Fraction(favourable, comb(len(pool), draws))


@cache
def _split_by_suit(patterns: tuple[Pattern, ...]) -> tuple[tuple[Pattern, ...], dict[str, tuple[Pattern, ...]]]:
    """Patterns with requirements on one suit's cards, by that suit; the rest apart."""
    rest: list[Pattern] = []
    by_suit: dict[str, list[Pattern]] = {}
    for pattern in patterns:
        suits = {s for s in SUITS if any(req.keys <= suit_keys(s) for req in pattern)}
        if len(suits) == 1:
            by_suit.setdefault(suits.pop(), []).append(pattern)
        else:
            rest.append(pattern)
    return tuple(rest), {s: tuple(ps) for s, ps in by_suit.items()}


def _union_by_suit(rest: Sequence[Pattern], by_suit: dict[str, Sequence[Pattern]], kept: list[Card],
                   pool: list[Card], draws: int, rules: Rules, cancel: Event | None) -> int:
    """Favourable draws for a pattern of `rest` or of any suit, by inclusion-exclusion over suits.

    With A_s = "rest or one of suit s's patterns", the target is the union of
    the A_s, and the intersection of A_s over a set of suits S is "rest, or a
    pattern of every suit in S". Each term only tells apart the cards of the
    suits in S (and those `rest` cares about). That keeps it small when the
    patterns of different suits are linked through other suits' cards, as Four
    Fingers' Straight Flush is: one search over all suits would have to track
    every suit at once.

    A term with several suits needs enough cards of each of them, which is
    rarely possible. If it isn't, the term is just the `rest` term; otherwise
    the search prunes on the total.
    """
    suits = list(by_suit)
    present = {k for card in (*kept, *pool) if (k := card_key(card, rules)) is not None}
    # Every pattern of suit s needs at least this many cards of the suit.
    necessary = {s: Requirement(suit_keys(s), min(_cards_within(p, suit_keys(s), present) for p in pats))
                 for s, pats in by_suit.items()}
    rest_ways: int | None = None
    impossible: set[frozenset[str]] = set()
    total = 0
    for size in range(1, len(suits) + 1):
        sign = 1 if size % 2 else -1
        for subset in combinations(suits, size):
            key = frozenset(subset)
            together = tuple(necessary[s] for s in subset)
            if (any(key - {s} in impossible for s in subset)
                    or not _favourable([together], [1], kept, pool, draws, rules, cancel)):
                impossible.add(key)
                if rest_ways is None:
                    rest_ways = _favourable(rest, [1] * len(rest), kept, pool, draws, rules, cancel)
                total += sign * rest_ways
                continue
            patterns = list(rest)
            masks = [(1 << size) - 1] * len(rest)
            for bit, s in enumerate(subset):
                patterns += by_suit[s]
                masks += [1 << bit] * len(by_suit[s])
            total += sign * _favourable(patterns, masks, kept, pool, draws, rules, cancel,
                                        together if size > 1 else None)
    return total


def _cards_within(pattern: Pattern, keys: frozenset[ClassKey], present: set[ClassKey]) -> int:
    """Fewest cards with a class in `keys` in a hand of `present` classes that satisfies `pattern`."""
    inner = tuple(req for req in pattern if req.keys <= keys)
    return max((sum(inner[q].need for q in g) for g in _disjoint_groups(inner, present)), default=0)


def _favourable(patterns: Sequence[Pattern], masks: list[int], kept: list[Card], pool: list[Card],
                draws: int, rules: Rules, cancel: Event | None, constraint: Pattern | None = None) -> int:
    """Draws of `draws` cards from `pool` after which the satisfied patterns cover every group.

    Pattern p belongs to the groups in the bitmask `masks[p]`; with a single
    group this is "some pattern is satisfied". `constraint` holds whenever the
    groups are covered without a pattern that belongs to all of them, so once
    it's out of reach only such patterns are left.
    """
    if not patterns:
        return 0
    every = 0
    for mask in masks:
        every |= mask
    patterns = list(patterns)
    masks = list(masks)
    c = None  # the constraint's pattern index, while it's unmet
    if constraint is not None:
        c = len(patterns)
        patterns.append(constraint)
        masks.append(0)
    needs = [tuple(req.need for req in pattern) for pattern in patterns]
    # Requirements of a pattern split into groups that no card still to be
    # drawn counts twice towards; None when the whole pattern is one such group.
    drawable = {k for card in pool if (k := card_key(card, rules)) is not None}
    groups = [g if len(g) > 1 else None for g in (_disjoint_groups(p, drawable) for p in patterns)]

    # Which (pattern, requirement) slots each class key counts towards.
    members: dict[object, list[tuple[int, int]]] = defaultdict(list)
    for p, pattern in enumerate(patterns):
        for q, req in enumerate(pattern):
            for key in req.keys:
                members[key].append((p, q))

    have = [[0] * len(pattern) for pattern in patterns]
    for card in kept:
        for p, q in members.get(card_key(card, rules), ()):
            have[p][q] += 1

    # Merge pool cards with identical membership into one class; cards that
    # count towards nothing go into the dead class.
    class_counts: Counter[tuple[tuple[int, int], ...]] = Counter()
    for card in pool:
        sig = tuple(members.get(card_key(card, rules), ()))
        class_counts[sig] += 1
    dead = class_counts.pop((), 0)
    # Requirements spanning several ranks (e.g. a suit's count) say little about
    # which classes belong together, so classes are ordered by the others.
    local = [[len({rank for rank, _ in req.keys}) == 1 for req in pattern] for pattern in patterns]

    def order(item: tuple[tuple[tuple[int, int], ...], int]) -> tuple:
        sig = item[0]
        ps = [p for p, q in sig if local[p][q]] or [p for p, _ in sig]
        return min(ps), max(ps), sig

    classes = sorted(class_counts.items(), key=order)
    n = len(classes)

    # remaining[i] = pool cards in classes i.. plus dead cards
    remaining = [0] * (n + 1)
    remaining[n] = dead
    for i in range(n - 1, -1, -1):
        remaining[i] = remaining[i + 1] + classes[i][1]

    # slots[i] = for each pattern class i counts towards, the requirements it counts towards.
    slots: list[dict[int, list[int]]] = []
    for sig, _ in classes:
        slot: dict[int, list[int]] = defaultdict(list)
        for p, q in sig:
            slot[p].append(q)
        slots.append(slot)
    # Patterns each class counts towards: only these change when moving past it.
    touched = [sorted(slot) for slot in slots]
    # steps[p] = the classes that count towards pattern p. Nothing about p
    # changes between them, so its tables are indexed by how many are behind:
    # supply_at[p][j][q] = cards in its classes from the j-th on that count
    # towards requirement q; future_at[p][j][q] = an id for the set of those
    # classes, the same for every requirement with that set.
    steps: list[list[int]] = [[] for _ in patterns]
    for i, ps in enumerate(touched):
        for p in ps:
            steps[p].append(i)
    supply_at: list[list[tuple[int, ...]]] = []
    future_at: list[list[tuple[int, ...]]] = []
    suffix_ids: dict[tuple[int, int], int] = {}
    for p, pattern in enumerate(patterns):
        sup, fut = [0] * len(pattern), [0] * len(pattern)
        sups, futs = [tuple(sup)], [tuple(fut)]
        for i in reversed(steps[p]):
            for q in slots[i][p]:
                sup[q] += classes[i][1]
                fut[q] = suffix_ids.setdefault((i, fut[q]), len(suffix_ids) + 1)
            sups.append(tuple(sup))
            futs.append(tuple(fut))
        supply_at.append(sups[::-1])
        future_at.append(futs[::-1])

    assessed: dict[tuple[int, ...], tuple[int, frozenset] | None] = {}

    def assess(p: int, i: int) -> tuple[int, frozenset] | None:
        """Cards pattern `p` still needs at class `i` and its memo-key part; None if unreachable."""
        j = bisect_left(steps[p], i)
        key = (p, j, *have[p])
        if key in assessed:
            return assessed[key]
        deficits = [need - h if need > h else 0 for need, h in zip(needs[p], have[p])]
        if any(map(gt, deficits, supply_at[p][j])):
            v = None
        else:
            if groups[p] is None:
                missing = sum(deficits)
            else:
                missing = max(sum(deficits[q] for q in group) for group in groups[p])
            v = missing, frozenset((f, d) for f, d in zip(future_at[p][j], deficits) if d)
        assessed[key] = v
        return v

    # A pattern joins the search at the first class that counts towards it.
    # Until then it only depends on the kept cards, so whether it's still
    # possible follows from `rem` and it stays out of the memo key.
    start = [ps[0] if ps else n for ps in steps]
    initial = {p: v for p in range(len(patterns)) if (v := assess(p, 0)) is not None and v[0] <= draws}
    if c is not None:
        if c not in initial:  # only patterns of every group can be satisfied
            masks = [m if m == every else 0 for m in masks]
        if c not in initial or initial[c][0] == 0:  # nothing left to check
            initial.pop(c, None)
            c = None
    todo = every
    for p, (missing, _) in initial.items():
        if missing == 0:
            todo &= ~masks[p]
    if not todo:
        return comb(len(pool), draws)
    activate: list[list[int]] = [[] for _ in range(n)]
    # pending[a][i][r] = groups covered by possible patterns that join at class
    # i or later and need at most r cards; a = 0 counts only patterns of every group.
    pending = [[[0] * (draws + 1) for _ in range(n + 1)] for _ in range(2)]
    for p, (missing, _) in initial.items():
        if missing and (masks[p] or p == c):  # unsatisfied but possible, so some class counts towards it
            activate[start[p]].append(p)
            pending[1][start[p]][missing] |= masks[p]
            if masks[p] == every:
                pending[0][start[p]][missing] |= masks[p]
    for table in pending:
        for i in range(n, -1, -1):
            for r in range(draws + 1):
                if r:
                    table[i][r] |= table[i][r - 1]
                if i < n:
                    table[i][r] |= table[i + 1][r]
    c_start = start[c] if c is not None else n
    c_initial = initial[c][0] if c is not None else 0

    memo: dict[tuple, int] = {}

    def ways(i: int, rem: int, todo: int, state: dict[int, tuple[int, frozenset]]) -> int:
        """Ways to draw `rem` more cards from classes i.. (plus dead cards) after
        which the groups in `todo` are covered; `state` holds assess() of each
        pattern that has joined and may still be possible, and `todo` already
        leaves out the groups of its satisfied patterns."""
        if not todo:
            return comb(remaining[i], rem)
        if i == n:
            return 0
        # Whether the constraint can still be met. The patterns imply it, so it
        # only prunes: it doesn't change the result and stays out of the memo key.
        if c is None:
            free, c_state = True, None
        elif i > c_start:
            c_state = state.get(c)
            free = c_state is not None and c_state[0] <= rem
        else:
            free, c_state = c_initial <= rem, None
        # Drop patterns that are out of reach, only cover groups that are
        # already covered, or are left with the same requirements as another.
        live: dict[int, tuple[int, frozenset]] = {c: c_state} if free and c_state is not None else {}
        parts: set[tuple[int, frozenset]] = set()
        covered = pending[free][i][rem]
        for p, (missing, part) in state.items():
            if (missing <= rem and (mask := masks[p] & todo) and (free or masks[p] == every)
                    and (mask, part) not in parts):
                parts.add((mask, part))
                live[p] = missing, part
                covered |= mask
        if covered & todo != todo:
            return 0
        memo_key = (i, rem, todo, frozenset(parts))
        if memo_key in memo:
            return memo[memo_key]
        if cancel is not None and cancel.is_set():
            raise Cancelled
        sig, count = classes[i]
        total = 0
        for k in range(min(count, rem) + 1):
            if k:
                for p, q in sig:
                    have[p][q] += 1
            child = dict(live)
            left = todo
            for p in touched[i]:
                if p in child:
                    v = assess(p, i + 1)
                    if v is None:
                        del child[p]
                    else:
                        child[p] = v
                        if not v[0]:
                            left &= ~masks[p]
            for p in activate[i]:
                if ((masks[p] & todo and (free or masks[p] == every) or p == c and free)
                        and (v := assess(p, i + 1)) is not None):
                    child[p] = v
                    if not v[0]:
                        left &= ~masks[p]
            total += comb(count, k) * ways(i + 1, rem - k, left, child)
        for p, q in sig:
            have[p][q] -= min(count, rem)
        memo[memo_key] = total
        return total

    return ways(0, draws, todo, {})


def _disjoint_groups(pattern: Pattern, present: set[ClassKey]) -> list[tuple[int, ...]]:
    """Greedily split requirement indices into groups that no card of a `present` class counts twice towards."""
    keys = [req.keys & present for req in pattern]
    groups: list[list[int]] = []
    for q in range(len(pattern)):
        for group in groups:
            if all(keys[q].isdisjoint(keys[g]) for g in group):
                group.append(q)
                break
        else:
            groups.append([q])
    return [tuple(g) for g in groups]


@dataclass(frozen=True)
class DiscardOutcome:
    discarded: list[Card]
    draws: int  # cards drawn from the deck after discarding
    probability: Fraction


@dataclass(frozen=True)
class _Discard:
    discarded: list[Card]
    kept_known: list[Card]
    hidden_kept: int
    draws: int
    pool: list[Card]  # unseen cards: undrawn deck + face-down hand cards


def _discard(state: GameState, discard: list[int]) -> _Discard:
    discard_set = set(discard)
    kept = [c for i, c in enumerate(state.hand) if i not in discard_set]
    kept_known = [c for c in kept if not c.hidden]
    draws = max(0, min(state.hand_size - len(kept), len(state.deck)))
    return _Discard([state.hand[i] for i in sorted(discard_set)], kept_known,
                    len(kept) - len(kept_known), draws, state.deck + state.hidden_pool)


def evaluate_discard(state: GameState, target: Target, discard: list[int],
                     cancel: Event | None = None) -> DiscardOutcome:
    """Probability of reaching `target` after discarding hand cards at indices `discard`."""
    d = _discard(state, discard)
    p = probability(target, d.kept_known, d.pool, d.hidden_kept + d.draws, state.rules, cancel)
    return DiscardOutcome(d.discarded, d.draws, p)


def count_probability(query: CardQuery, pool: list[Card], draws: int,
                      rules: Rules = Rules()) -> Fraction:
    """P(the number of matching cards among `draws` drawn from `pool` satisfies `query`)."""
    draws = max(0, min(draws, len(pool)))
    matches = sum(1 for c in pool if query.filter.test(c, rules))
    others = len(pool) - matches
    hits = sum(comb(matches, k) * comb(others, draws - k)
               for k in range(min(matches, draws) + 1) if query.accepts(k))
    return Fraction(hits, comb(len(pool), draws))


def evaluate_cards(state: GameState, query: CardQuery, discard: list[int]) -> DiscardOutcome:
    """Probability that the cards drawn after discarding satisfy `query`.

    Only the newly drawn cards count. From the player's view they are a uniform
    sample of the unseen pool, even when face-down cards are kept in hand.
    """
    d = _discard(state, discard)
    return DiscardOutcome(d.discarded, d.draws, count_probability(query, d.pool, d.draws, state.rules))

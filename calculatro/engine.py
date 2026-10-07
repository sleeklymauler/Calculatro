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
Classes are ordered by the first pattern they count towards, so each
pattern's classes are processed close together and patterns resolve early.
Subtrees are memoized on what's left to do: for each live pattern, its unmet
requirements, each identified by which of the remaining classes it covers
plus its deficit. Many draw orders reach the same position, and different
patterns often leave the same remainder (e.g. Four Fingers' "4 suited cards
in all" shared by every window of a suit), so this key is canonical.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from math import comb

from .cards import CardQuery
from .hands import Pattern, Target, card_key
from .rules import Rules
from .state import Card, GameState


def probability(target: Target, kept: list[Card], pool: list[Card], draws: int,
                rules: Rules = Rules()) -> Fraction:
    """P(kept + `draws` random cards from `pool` contains `target` under `rules`)."""
    draws = max(0, min(draws, len(pool)))
    patterns = target.patterns(rules)

    needs = [tuple(req.need for req in pattern) for pattern in patterns]
    # Requirements of a pattern split into groups that are disjoint within
    # themselves; None when the whole pattern is one such group.
    groups = [g if len(g) > 1 else None for g in map(_disjoint_groups, patterns)]

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
    classes = sorted(class_counts.items(),
                     key=lambda c: (min(p for p, _ in c[0]), max(p for p, _ in c[0]), c[0]))
    n = len(classes)

    # remaining[i] = pool cards in classes i.. plus dead cards
    remaining = [0] * (n + 1)
    remaining[n] = dead
    for i in range(n - 1, -1, -1):
        remaining[i] = remaining[i + 1] + classes[i][1]

    # supply[p][q][i] = cards in classes i.. that count towards requirement (p, q);
    # future[p][q][i] = an id for the set of classes i.. it counts towards.
    supply = [[[0] * (n + 1) for _ in pattern] for pattern in patterns]
    future = [[[0] * (n + 1) for _ in pattern] for pattern in patterns]
    suffix_ids: dict[tuple[int, int], int] = {}
    for i in range(n - 1, -1, -1):
        for p, pattern in enumerate(patterns):
            for q in range(len(pattern)):
                supply[p][q][i] = supply[p][q][i + 1]
                future[p][q][i] = future[p][q][i + 1]
        for p, q in classes[i][0]:
            supply[p][q][i] += classes[i][1]
            future[p][q][i] = suffix_ids.setdefault((i, future[p][q][i + 1]), len(suffix_ids) + 1)

    # Patterns each class counts towards: only these change when moving past it.
    touched = [sorted({p for p, _ in sig}) for sig, _ in classes]

    def assess(p: int, i: int) -> tuple[int, frozenset] | None:
        """Cards pattern `p` still needs at class `i` and its memo-key part; None if unreachable."""
        deficits = [max(0, need - h) for need, h in zip(needs[p], have[p])]
        if any(d > s[i] for d, s in zip(deficits, supply[p])):
            return None
        if groups[p] is None:
            missing = sum(deficits)
        else:
            missing = max(sum(deficits[q] for q in group) for group in groups[p])
        return missing, frozenset((future[p][q][i], d) for q, d in enumerate(deficits) if d)

    memo: dict[tuple, int] = {}

    def ways(i: int, rem: int, state: dict[int, tuple[int, frozenset]]) -> int:
        """Ways to draw `rem` more cards from classes i.. (plus dead cards) that
        satisfy a pattern; `state` holds assess() of each pattern still possible."""
        live = {p: v for p, v in state.items() if v[0] <= rem}
        if any(missing == 0 for missing, _ in live.values()):
            return comb(remaining[i], rem)
        if not live or i == n:
            return 0
        memo_key = (i, rem, frozenset(part for _, part in live.values()))
        if memo_key in memo:
            return memo[memo_key]
        sig, count = classes[i]
        total = 0
        for k in range(min(count, rem) + 1):
            if k:
                for p, q in sig:
                    have[p][q] += 1
            child = dict(live)
            for p in touched[i]:
                if p in child:
                    v = assess(p, i + 1)
                    if v is None:
                        del child[p]
                    else:
                        child[p] = v
            total += comb(count, k) * ways(i + 1, rem - k, child)
        for p, q in sig:
            have[p][q] -= min(count, rem)
        memo[memo_key] = total
        return total

    initial = {p: v for p in range(len(patterns)) if (v := assess(p, 0)) is not None}
    favourable = ways(0, draws, initial)
    return Fraction(favourable, comb(len(pool), draws))


def _disjoint_groups(pattern: Pattern) -> list[tuple[int, ...]]:
    """Greedily split requirement indices into groups with pairwise-disjoint keys."""
    groups: list[list[int]] = []
    for q, req in enumerate(pattern):
        for group in groups:
            if all(req.keys.isdisjoint(pattern[g].keys) for g in group):
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


def evaluate_discard(state: GameState, target: Target, discard: list[int]) -> DiscardOutcome:
    """Probability of reaching `target` after discarding hand cards at indices `discard`."""
    d = _discard(state, discard)
    p = probability(target, d.kept_known, d.pool, d.hidden_kept + d.draws, state.rules)
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

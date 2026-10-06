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
Classes are ordered by the first pattern they count towards, so each
pattern's classes are processed close together and patterns resolve early, and subtrees are memoized on the remaining
deficits of the live patterns (many draw orders reach the same position).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from math import comb

from .hands import Target, card_key
from .state import Card, GameState


def probability(target: Target, kept: list[Card], pool: list[Card], draws: int) -> Fraction:
    """P(kept + `draws` random cards from `pool` contains `target`)."""
    draws = max(0, min(draws, len(pool)))
    patterns = target.patterns

    # Which (pattern, requirement) slots each class key counts towards.
    members: dict[object, list[tuple[int, int]]] = defaultdict(list)
    for p, pattern in enumerate(patterns):
        for q, req in enumerate(pattern):
            for key in req.keys:
                members[key].append((p, q))

    have = [[0] * len(pattern) for pattern in patterns]
    for card in kept:
        for p, q in members.get(card_key(card), ()):
            have[p][q] += 1

    # Merge pool cards with identical membership into one class; cards that
    # count towards nothing go into the dead class.
    class_counts: Counter[tuple[tuple[int, int], ...]] = Counter()
    for card in pool:
        sig = tuple(members.get(card_key(card), ()))
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

    # supply[p][q][i] = cards in classes i.. that count towards requirement (p, q)
    supply = [[[0] * (n + 1) for _ in pattern] for pattern in patterns]
    for i in range(n - 1, -1, -1):
        for p, pattern in enumerate(patterns):
            for q in range(len(pattern)):
                supply[p][q][i] = supply[p][q][i + 1]
        for p, q in classes[i][0]:
            supply[p][q][i] += classes[i][1]

    def live_patterns(i: int, rem: int, candidates: list[int]) -> tuple[list[int], tuple] | None:
        """Patterns still reachable plus a memo key of their deficits; None if one is satisfied."""
        live = []
        key = []
        for p in candidates:
            missing = 0
            ok = True
            deficits = []
            for q, req in enumerate(patterns[p]):
                deficit = max(0, req.need - have[p][q])
                if deficit > supply[p][q][i]:
                    ok = False
                    break
                missing += deficit
                deficits.append(deficit)
            if not ok or missing > rem:
                continue
            if missing == 0:
                return None
            live.append(p)
            key.append((p, tuple(deficits)))
        return live, tuple(key)

    memo: dict[tuple, int] = {}

    def ways(i: int, rem: int, candidates: list[int]) -> int:
        result = live_patterns(i, rem, candidates)
        if result is None:
            return comb(remaining[i], rem)
        live, key = result
        if not live or i == n:
            return 0
        memo_key = (i, rem, key)
        if memo_key in memo:
            return memo[memo_key]
        sig, count = classes[i]
        total = 0
        for k in range(min(count, rem) + 1):
            if k:
                for p, q in sig:
                    have[p][q] += 1
            total += comb(count, k) * ways(i + 1, rem - k, live)
        for p, q in sig:
            have[p][q] -= min(count, rem)
        memo[memo_key] = total
        return total

    favourable = ways(0, draws, list(range(len(patterns))))
    return Fraction(favourable, comb(len(pool), draws))


@dataclass(frozen=True)
class DiscardOutcome:
    discarded: list[Card]
    draws: int  # cards drawn from the deck after discarding
    probability: Fraction


def evaluate_discard(state: GameState, target: Target, discard: list[int]) -> DiscardOutcome:
    """Probability of reaching `target` after discarding hand cards at indices `discard`."""
    discard_set = set(discard)
    kept = [c for i, c in enumerate(state.hand) if i not in discard_set]
    kept_known = [c for c in kept if not c.hidden]
    hidden_kept = len(kept) - len(kept_known)
    draws = max(0, min(state.hand_size - len(kept), len(state.deck)))
    pool = state.deck + state.hidden_pool
    p = probability(target, kept_known, pool, hidden_kept + draws)
    return DiscardOutcome([state.hand[i] for i in sorted(discard_set)], draws, p)

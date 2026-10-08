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
"cards needed" is bounded by the largest deficit sum over groups of
requirements that no remaining class counts twice towards; a drawn card
lowers each group's sum by at most one.
Classes are swept rank by rank, or suit by suit when no requirement spans
several suits (the suits are then independent), so each pattern's classes
are processed close together and patterns resolve early. A pattern only
joins the search at its first class with a single-rank requirement on it.
Before that its state follows from the kept cards plus how many cards were
drawn from the classes its other requirements cover; those counts are shared
and go in the memo key.
Subtrees are memoized on what's left to do: for each live pattern, its unmet
requirements, each as the set of remaining classes that count towards it plus
its deficit (a "part"). Many draw orders reach the same position, and
different patterns often leave the same remainder, so this key is canonical.
A part that some other part implies (it needs a card from a subset of the
classes that one needs a card from) is dropped, so the key only holds parts
that matter.

The search has to remember something about every suit whose patterns are
still live, so when patterns of several suits are linked through other
suits' cards (Four Fingers' Straight Flush lets any card of the run's 4th
rank complete it) its size multiplies across suits. Targets with patterns of
several suits are therefore split by suit with inclusion-exclusion; see
_union_by_suit.
"""

from __future__ import annotations

import gc
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from fractions import Fraction
from itertools import combinations, repeat
from math import comb
from operator import gt
from threading import Event, Lock

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
    _, by_suit = _split_by_suit(patterns)
    with _gc_paused():
        # An unseen Wild Card counts as every suit, which ties the suits back together.
        # With two suits (Smeared Joker's colours), one search only has to carry
        # what the term with both of them does, and it can stop at either colour's
        # pattern, so splitting doesn't pay.
        if len(by_suit) > 2 and not any(key[1] == WILD for key in map(card_key, pool, repeat(rules)) if key):
            favourable = _union_by_suit(patterns, kept, pool, draws, rules, cancel)
        else:
            favourable = _favourable(patterns, [1] * len(patterns), kept, pool, draws, rules, cancel)
    return Fraction(favourable, comb(len(pool), draws))


_gc_lock = Lock()
_gc_searches = 0  # searches running with the garbage collector paused
_gc_was_enabled = False


@contextmanager
def _gc_paused() -> Iterator[None]:
    """Pause Python's cyclic garbage collector while any search runs.

    A big search creates millions of tuples and frozensets, and the collector
    keeps traversing them, which costs 5-15% of the time (docs/performance).
    The searches make no garbage cycles that outlive them (_favourable clears
    its memo), so nothing piles up. The collector is process-wide and a
    cancelled search can still be unwinding when the next one starts, so the
    searches are counted, and the last one to finish restores it.
    """
    global _gc_searches, _gc_was_enabled
    with _gc_lock:
        if not _gc_searches:
            _gc_was_enabled = gc.isenabled()
            gc.disable()
        _gc_searches += 1
    try:
        yield
    finally:
        with _gc_lock:
            _gc_searches -= 1
            if not _gc_searches and _gc_was_enabled:
                gc.enable()


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


@cache
def _interchangeable(patterns: tuple[Pattern, ...]) -> bool:
    """Whether swapping any two suits maps `patterns` onto themselves."""
    known = set(patterns)
    for a, b in combinations(SUITS, 2):
        swap = {a: b, b: a}
        relabelled: dict[Requirement, Requirement] = {}
        for pattern in patterns:
            image = []
            for req in pattern:
                if req not in relabelled:
                    relabelled[req] = Requirement(frozenset((r, swap.get(s, s)) for r, s in req.keys), req.need)
                image.append(relabelled[req])
            if tuple(image) not in known:
                return False
    return True


def _union_by_suit(patterns: tuple[Pattern, ...], kept: list[Card], pool: list[Card], draws: int,
                   rules: Rules, cancel: Event | None) -> int:
    """Favourable draws for `patterns`, by inclusion-exclusion over suits.

    The patterns with requirements on one suit's cards are grouped by that
    suit, and the others are `rest`. With A_s = "rest or one of suit s's
    patterns", the target is the union of the A_s, and the intersection of
    A_s over a set of suits S is "rest, or a pattern of every suit in S".
    Each term only tells apart the cards of the suits in S (and those `rest`
    cares about). That keeps it small when the patterns of different suits
    are linked through other suits' cards, as Four Fingers' Straight Flush
    is: one search over all suits would have to track every suit at once.

    A term with several suits needs enough cards of each of them, which is
    rarely possible. If it isn't, the term is just the `rest` term; otherwise
    the search prunes on the total.

    When the suits are interchangeable (the patterns treat them alike, and
    some of them have the same ranks kept and unseen, as with a fresh deck
    and nothing known), terms that differ only by relabelling suits are equal
    and computed once.
    """
    rest, by_suit = _split_by_suit(patterns)
    suits = list(by_suit)
    if _interchangeable(patterns):
        ranks = {s: (tuple(sorted(k[0] for c in kept if (k := card_key(c, rules)) and k[1] == s)),
                     tuple(sorted(k[0] for c in pool if (k := card_key(c, rules)) and k[1] == s))) for s in SUITS}

        def term_key(subset: tuple[str, ...]) -> object:
            return (tuple(sorted(ranks[s] for s in subset)),
                    tuple(sorted(ranks[s] for s in SUITS if s not in subset)))
    else:
        def term_key(subset: tuple[str, ...]) -> object:
            return subset
    terms: dict[object, int] = {}
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
            tk = term_key(subset)
            if tk not in terms:
                chosen = list(rest)
                masks = [(1 << size) - 1] * len(rest)
                for bit, s in enumerate(subset):
                    chosen += by_suit[s]
                    masks += [1 << bit] * len(by_suit[s])
                terms[tk] = _favourable(chosen, masks, kept, pool, draws, rules, cancel,
                                        together if size > 1 else None)
            total += sign * terms[tk]
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
    kept_have = [tuple(h) for h in have]

    # Merge pool cards with identical membership into one class; cards that
    # count towards nothing go into the dead class.
    # The search sweeps the classes rank by rank, so each straight window or
    # set of a kind is settled within a few classes, with Wild Cards first in
    # their rank. If no requirement spans several suits, the suits don't
    # interact (but through Wild Cards), and sweeping suit by suit only has to
    # remember one suit's progress at a time.
    class_counts: Counter[tuple[tuple[int, int], ...]] = Counter()
    lowest: dict[tuple[tuple[int, int], ...], tuple] = {}
    suit_by_suit = not any(len({suit for _, suit in req.keys} - {WILD}) > 1
                           for pattern in patterns for req in pattern)
    for card in pool:
        key = card_key(card, rules)
        sig = tuple(members.get(key, ()))
        class_counts[sig] += 1
        if sig:
            rank, suit = key
            place = (suit != WILD, suit, rank) if suit_by_suit else (rank, suit != WILD, suit)
            lowest[sig] = min(lowest.get(sig, place), place)
    dead = class_counts.pop((), 0)
    classes = sorted(class_counts.items(), key=lambda item: (lowest[item[0]], item[0]))
    # Requirements on a single rank, which decide when a pattern joins the search.
    local = [[len({rank for rank, _ in req.keys}) == 1 for req in pattern] for pattern in patterns]
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
        slots.append(dict(slot))
    # Patterns each class counts towards: only these change when moving past it.
    touched = [sorted(slot) for slot in slots]
    # steps[p] = the classes that count towards pattern p. Nothing about p
    # changes between them, so its tables are indexed by how many are behind:
    # supply_at[p][j][q] = cards in its classes from the j-th on that count
    # towards requirement q; future_at[p][j][q] = a bitmask of those classes.
    steps: list[list[int]] = [[] for _ in patterns]
    for i, ps in enumerate(touched):
        for p in ps:
            steps[p].append(i)
    supply_at: list[list[tuple[int, ...]]] = []
    future_at: list[list[tuple[int, ...]]] = []
    for p, pattern in enumerate(patterns):
        sup, fut = [0] * len(pattern), [0] * len(pattern)
        sups, futs = [tuple(sup)], [tuple(fut)]
        for i in reversed(steps[p]):
            for q in slots[i][p]:
                sup[q] += classes[i][1]
                fut[q] |= 1 << i
            sups.append(tuple(sup))
            futs.append(tuple(fut))
        supply_at.append(sups[::-1])
        future_at.append(futs[::-1])

    # The search follows each pattern as a "part": its groups and its unmet
    # requirements, each as (bitmask of the classes still to come that count
    # towards it, deficit). Parts are interned as ints, so patterns left
    # needing the same thing share one, states hash cheaply, and moving a
    # part past a class is a cached table lookup.
    part_ids: dict[tuple[int, frozenset[tuple[int, int]]], int] = {}
    part_mask: list[int] = []  # groups it belongs to
    part_reqs: list[tuple[tuple[int, int], ...]] = []
    part_need: list[int] = []  # lower bound on the cards it needs; 0 once satisfied
    part_bits: list[int] = []  # classes that count towards an unmet requirement
    part_single: list[tuple[int, int] | None] = []  # its requirement, if it's one card of some classes
    single_parts: set[int] = set()  # parts with a part_single
    supply_of: dict[int, int] = {}

    def intern(mask: int, unmet: list[tuple[int, int]]) -> int:
        key = mask, frozenset(unmet)
        pid = part_ids.get(key)
        if pid is None:
            pid = part_ids[key] = len(part_mask)
            part_mask.append(mask)
            part_reqs.append(tuple(unmet))
            part_need.append(_bound(unmet))
            bits = 0
            for f, _ in unmet:
                bits |= f
            part_bits.append(bits)
            part_single.append(unmet[0] if len(unmet) == 1 and unmet[0][1] == 1 else None)
            if part_single[pid] is not None:
                single_parts.add(pid)
        return pid

    def supply(bits: int) -> int:
        """Cards in the classes of bitmask `bits`."""
        s = supply_of.get(bits)
        if s is None:
            s = supply_of[bits] = sum(classes[i][1] for i in range(bits.bit_length()) if bits >> i & 1)
        return s

    def status(p: int, have: Sequence[int], j: int) -> int:
        """Part of pattern `p` with `have` cards towards each requirement and
        its j-th class next; DEAD if unreachable."""
        deficits = [need - h if need > h else 0 for need, h in zip(needs[p], have)]
        if any(map(gt, deficits, supply_at[p][j])):
            return DEAD
        return intern(masks[p], [(f, d) for f, d in zip(future_at[p][j], deficits) if d])

    def step(pid: int, i: int, k: int) -> int:
        """Part `pid` after drawing `k` cards of class `i`; DEAD if unreachable."""
        bit = 1 << i
        unmet = []
        for f, d in part_reqs[pid]:
            if f & bit:
                f ^= bit
                if d <= k:
                    continue
                d -= k
                if d > supply(f):
                    return DEAD
            unmet.append((f, d))
        return intern(part_mask[pid], unmet)

    # transitions[i][k][pid] = step(pid, i, k)
    transitions: list[list[dict[int, int]]] = [[{} for _ in range(count + 1)] for _, count in classes]

    def cards_needed(p: int, deficits: list[int]) -> int:
        """Lower bound on the cards pattern `p` needs, given its requirements' deficits."""
        if groups[p] is None:
            return sum(deficits)
        return max(sum(deficits[q] for q in group) for group in groups[p])

    # A pattern joins the search at the first class that counts towards one of
    # its requirements on a single rank (or towards it at all, if it has none).
    # Until then it doesn't need a place in the search state: it depends only
    # on the kept cards and on how many cards were drawn from the classes before
    # it that count towards its other requirements (`deferred`), and those
    # counts, shared by every waiting pattern with the same classes, go in the
    # memo key instead. Four Fingers' "a suited card of another rank" then
    # costs one bit per suit, instead of every straight window joining at the
    # suit's first card with its own copy of that requirement.
    start = []
    for p, ps in enumerate(steps):
        start.append(next((i for i in ps if any(local[p][q] for q in slots[i][p])), ps[0] if ps else n))
    # deferred[p] = (q, d) for each requirement q of pattern p that classes
    # before it joins count towards; d identifies the set of those classes,
    # deferred_sets[d], and the search keeps a running count of cards drawn
    # from it in drawn_from[d].
    deferred: list[list[tuple[int, int]]] = [[] for _ in patterns]
    deferred_ids: dict[tuple[int, ...], int] = {}
    for p, ps in enumerate(steps):
        before: dict[int, list[int]] = defaultdict(list)
        for i in ps[:bisect_left(ps, start[p])]:
            for q in slots[i][p]:
                before[q].append(i)
        deferred[p] = [(q, deferred_ids.setdefault(tuple(cls), len(deferred_ids))) for q, cls in before.items()]
    deferred_sets = list(deferred_ids)
    in_deferred: list[list[int]] = [[] for _ in range(n)]
    for d, cls in enumerate(deferred_sets):
        for i in cls:
            in_deferred[i].append(d)
    drawn_from = [0] * len(deferred_sets)
    initial = {p: part_need[v] for p in range(len(patterns))
               if (v := status(p, kept_have[p], 0)) != DEAD and part_need[v] <= draws}
    if c is not None:
        if c not in initial:  # only patterns of every group can be satisfied
            masks = [m if m == every else 0 for m in masks]
        if c not in initial or initial[c] == 0:  # nothing left to check
            initial.pop(c, None)
            c = None
    todo = every
    for p, missing in initial.items():
        if missing == 0:
            todo &= ~masks[p]
    if not todo:
        return comb(len(pool), draws)
    activate: list[list[int]] = [[] for _ in range(n)]
    # pending[a][i][r] = groups covered by possible patterns that join at class
    # i or later and may need at most r cards then; a = 0 counts only patterns
    # of every group.
    pending = [[[0] * (draws + 1) for _ in range(n + 1)] for _ in range(2)]
    # The most cards worth counting from each set of classes a waiting pattern
    # defers, by the pattern's join class.
    defer_caps: dict[tuple[int, int], int] = {}
    for p, need in initial.items():
        if need and (masks[p] or p == c):  # unsatisfied but possible, so some class counts towards it
            if p != c:
                activate[start[p]].append(p)
            if deferred[p]:  # cards drawn before it joins may count towards it
                deficits = [k - h if k > h else 0 for k, h in zip(needs[p], kept_have[p])]
                for q, d in deferred[p]:
                    if deficits[q]:
                        key = (d, start[p])
                        defer_caps[key] = max(defer_caps.get(key, 0), deficits[q])
                        deficits[q] = 0
                need = cards_needed(p, deficits)
            pending[1][start[p]][need] |= masks[p]
            if masks[p] == every:
                pending[0][start[p]][need] |= masks[p]
    # tracked[i] = (d, cap) for the counts of cards drawn from deferred sets
    # that the memo key holds at class i; of sets with the same classes before
    # i, only one.
    tracked_at: list[dict[tuple[int, ...], tuple[int, int]]] = [{} for _ in range(n + 1)]
    for (d, join), cap in defer_caps.items():
        cls = deferred_sets[d]
        for t in range(cls[0] + 1, join + 1):
            behind = cls[:bisect_left(cls, t)]
            d0, cap0 = tracked_at[t].get(behind, (d, 0))
            tracked_at[t][behind] = d0, max(cap0, cap)
    tracked = [tuple(t.values()) for t in tracked_at]
    for table in pending:
        for i in range(n, -1, -1):
            for r in range(draws + 1):
                if r:
                    table[i][r] |= table[i][r - 1]
                if i < n:
                    table[i][r] |= table[i + 1][r]
    c_start = start[c] if c is not None else n
    c_initial = initial[c] if c is not None else 0
    # The part a pattern joins the search with, by its deferred counts.
    join_at = [bisect_left(ps, s) for ps, s in zip(steps, start)]
    joined: dict[tuple[int, ...], int] = {}

    def join(p: int) -> int:
        key = (p, *(drawn_from[d] for _, d in deferred[p]))
        pid = joined.get(key)
        if pid is None:
            have = list(kept_have[p])
            for q, d in deferred[p]:
                have[q] += drawn_from[d]
            pid = joined[key] = status(p, have, join_at[p])
        return pid

    memo: dict[tuple, int] = {}
    # Parts of the patterns joining at a class, by (class, todo, free, deferred counts).
    joining: dict[tuple, list[int]] = {}

    def ways(i: int, rem: int, todo: int, state: list[int], cp: int | None) -> int:
        """Ways to draw `rem` more cards from classes i.. (plus dead cards) after
        which the groups in `todo` are covered. `state` holds the parts of the
        patterns that have joined and may still be possible, and `todo`
        already leaves out the groups of satisfied ones. `cp` is the
        constraint's part once it has joined (DEAD once out of reach)."""
        if not todo:
            return comb(remaining[i], rem)
        if i == n:
            return 0
        # Whether the constraint can still be met. The patterns imply it, so it
        # only prunes: it doesn't change the result and stays out of the memo key.
        if c is None:
            free = True
        elif i > c_start:
            free = cp != DEAD and part_need[cp] <= rem
            if not free:
                cp = DEAD
        else:
            free = c_initial <= rem
        # Drop parts that are out of reach or only cover groups that are already covered.
        if every == 1:  # one group, so no constraint either
            live = {pid for pid in state if part_need[pid] <= rem}
            if not live and not pending[1][i][rem]:
                return 0
        else:
            live = set()
            covered = pending[free][i][rem]
            for pid in state:
                if part_need[pid] <= rem and (mask := part_mask[pid] & todo) and (free or part_mask[pid] == every):
                    live.add(pid)
                    covered |= mask
            if covered & todo != todo:
                return 0
        deferred_key = tuple([min(drawn_from[d], cap) for d, cap in tracked[i]])
        memo_key = (i, rem, todo, frozenset(live), deferred_key)
        total = memo.get(memo_key)
        if total is not None:
            return total
        keys = [memo_key]
        if len(live) > 1 and (singles := live & single_parts):
            # A part that needs a card of some classes makes redundant any part
            # that needs one of a subset of them (and covers no other groups).
            size = len(live)
            for s in singles:
                if s not in live:
                    continue
                fs, ds = part_single[s]
                ms = part_mask[s] & todo
                for pid in [pid for pid in live if part_bits[pid] & fs]:
                    if pid != s and part_mask[pid] & todo & ~ms == 0:
                        for f, d in part_reqs[pid]:
                            if f & ~fs == 0 and d >= ds:
                                live.discard(pid)
                                break
            if len(live) < size:
                memo_key = (i, rem, todo, frozenset(live), deferred_key)
                total = memo.get(memo_key)
                if total is not None:
                    memo[keys[0]] = total
                    return total
                keys.append(memo_key)
        if cancel is not None and cancel.is_set():
            raise Cancelled
        count = classes[i][1]
        bit = 1 << i
        # Only parts that class i counts towards change here.
        moving = [pid for pid in live if part_bits[pid] & bit]
        still = [pid for pid in live if not part_bits[pid] & bit]
        if activate[i]:
            # The parts joining here follow from the deferred counts, capped as in the key.
            join_key = (i, todo, free, deferred_key)
            parts = joining.get(join_key)
            if parts is None:
                parts = joining[join_key] = [
                    pid for p in activate[i]
                    if masks[p] & todo and (free or masks[p] == every) and (pid := join(p)) != DEAD]
            moving += parts
        if i == c_start:
            cp = join(c) if free else DEAD
        c_moves = cp is not None and cp != DEAD and part_bits[cp] & bit
        sets = in_deferred[i]
        drawn = min(count, rem)
        total = 0
        moves = transitions[i]
        for k in range(drawn + 1):
            if k:
                for d in sets:
                    drawn_from[d] += 1
            table = moves[k]
            child = still[:]
            left = todo
            for pid in moving:
                t = table.get(pid)
                if t is None:
                    t = table[pid] = step(pid, i, k)
                if t != DEAD:
                    if part_need[t]:
                        child.append(t)
                    else:
                        left &= ~part_mask[t]
            child_cp = cp
            if c_moves:
                child_cp = table.get(cp)
                if child_cp is None:
                    child_cp = table[cp] = step(cp, i, k)
            total += comb(count, k) * ways(i + 1, rem - k, left, child, child_cp)
        for d in sets:
            drawn_from[d] -= drawn
        for key in keys:
            memo[key] = total
        return total

    try:
        return ways(0, draws, todo, [], None)
    finally:
        # `ways` refers to itself, so this frame's tables are only freed by the
        # garbage collector, which is paused (see _gc_paused); free the big ones.
        memo.clear()
        transitions.clear()
        part_ids.clear()
        joined.clear()
        joining.clear()


DEAD = -1  # a part that can no longer be satisfied


def _bound(unmet: list[tuple[int, int]]) -> int:
    """Lower bound on the cards needed for requirements (classes bitmask, deficit) that may overlap.

    The largest deficit sum over a greedy group of requirements with disjoint
    classes. The classes are those still to come, so requirements that only
    overlapped on classes already behind count separately.
    """
    groups: list[tuple[int, int]] = []  # (classes, deficit sum)
    for f, d in sorted(unmet, key=lambda u: -u[1]):
        for g, (classes, total) in enumerate(groups):
            if not classes & f:
                groups[g] = classes | f, total + d
                break
        else:
            groups.append((f, d))
    return max((total for _, total in groups), default=0)


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

"""Exact probability that a hand reaches a target after one discard and draw.

The final hand is the kept known cards plus `m` cards drawn uniformly without
replacement from the unseen pool: the undrawn deck plus any face-down cards
in hand or discarded, which are equally unknown to the player. Kept face-down cards are
modelled as part of that draw, since by symmetry it's the same distribution.
The Mark's face-down cards are known to be face cards, so there the draw is
weighted by how many face cards it has; see _mark_split. Face-down cards whose
place in the hand's sorts says what they can be aren't like that: the odds
average over what they are, by the posterior (`posterior`, _averaged).

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
import sys
from bisect import bisect_left
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import cache
from itertools import combinations, product, repeat
from math import comb, lcm, prod
from operator import gt
from random import Random
from threading import Event, Lock

from .cards import CardQuery
from .hands import (
    WILD,
    ClassKey,
    Pattern,
    Requirement,
    Target,
    bell_matters,
    card_key,
    disjoint_groups,
    forced_patterns,
    suit_keys,
)
from .posterior import Branch, Label, Posterior, Window, printed, sort_value, summed, windows
from .rules import Rules
from .state import SUITS, Card, GameState


class Cancelled(Exception):
    """Raised when a calculation's `cancel` event is set before it finishes."""


def probability(target: Target, kept: list[Card], pool: list[Card], draws: int,
                rules: Rules = Rules(), cancel: Event | None = None,
                marked: int = 0, marked_kept: int = 0, forced: Card | None = None,
                reuse: Reuse | None = None, windows: Sequence[Marked] = ()) -> Fraction:
    """P(kept + `draws` random cards from `pool` contains `target` under `rules`).

    With The Mark, `marked` of the pool's cards are face down (in hand or
    discarded), so they
    are known to be face cards (`flipped_by_mark`), and `marked_kept` of them
    are among the `draws`. The other draws come from the rest of the pool.

    With Cerulean Bell, `forced` is a card in hand besides `kept` that must be
    part of the hand played (`hands.forced_patterns`).

    `reuse` shares work between calculations with the same pool (see Reuse).

    With `windows`, some face-down cards are a random set of each one's cards,
    kept (among the `draws`) or gone (not among them); see _window_split. Not
    with The Mark.

    Raises Cancelled soon after `cancel` is set.
    """
    draws = max(0, min(draws, len(pool)))
    patterns = target.patterns(rules) if forced is None else forced_patterns(target, rules, card_key(forced, rules))
    if not draws:
        # Nothing to draw (The Mark's split then weighs the one draw by 1): the
        # kept cards make the target or don't. Quick, for the many deals of
        # face-down cards that are kept (_averaged).
        return Fraction(int(_holds(patterns, kept, rules)))
    _, by_suit = _split_by_suit(patterns)
    # An unseen Wild Card counts as every suit, which ties the suits back together.
    # With two suits (Smeared Joker's colours), one search only has to carry
    # what the term with both of them does, and it can stop at either colour's
    # pattern, so splitting doesn't pay.
    split = len(by_suit) > 2 and not any(key[1] == WILD for key in map(card_key, pool, repeat(rules)) if key)

    def favourable(faces: list[int] | None = None, face_weights: Sequence[tuple[tuple[int, ...], int]] = ()) -> int:
        if split:
            return _union_by_suit(patterns, kept, pool, draws, rules, cancel, faces, face_weights, reuse,
                                  relabel=not windows)
        return _favourable(patterns, [1] * len(patterns), kept, pool, draws, rules, cancel,
                           faces=faces, face_weights=face_weights, reuse=reuse)

    with _gc_paused():
        split_faces = _window_split(pool, draws, windows) if windows else _marks(
            _mark_split(pool, draws, rules, marked, marked_kept))
        if split_faces is None:
            return Fraction(favourable(), comb(len(pool), draws))
        # One search for every number of face cards, with the weights scaled to integers.
        faces, weights = split_faces
        scale = lcm(*(w.denominator for _, w in weights))
        return Fraction(favourable(faces, [(a, int(w * scale)) for a, w in weights]), scale)


def _holds(patterns: Sequence[Pattern], cards: Sequence[Card], rules: Rules) -> bool:
    """Whether `cards` satisfy one of `patterns`."""
    have = Counter(k for c in cards if (k := card_key(c, rules)) is not None)
    return any(all(sum(n for key, n in have.items() if key in req.keys) >= req.need for req in pattern)
               for pattern in patterns)


def flipped_by_mark(card: Card, rules: Rules) -> bool:
    """Whether The Mark draws `card` face down: `Card:is_face(true)`, which ignores debuffs."""
    return rules.pareidolia or (not card.is_stone and card.rank_id in (11, 12, 13))


def _mark_split(pool: list[Card], draws: int, rules: Rules, marked: int,
                marked_kept: int) -> tuple[list[bool], list[tuple[int, Fraction]]] | None:
    """How The Mark's face-down cards skew the unseen cards in the final hand.

    The `marked` face-down cards are a uniform sample of the pool's f face
    cards, and the other d = draws - marked_kept cards come uniformly from the
    pool less all of them, so k of those are face cards with probability
    C(f - marked, k) C(nf, d - k) / C(N - marked, d). Given k, the final hand's
    unseen face cards are a uniform sample of a = marked_kept + k of the f,
    and its other unseen cards one of d - k of the nf others. So P(target) is
    the sum over a of (favourable draws with exactly a face cards) times
    C(f - marked, k) / (C(N - marked, d) C(f, a)), the C(nf, d - k) cancelling.

    Returns which pool cards are face cards and those (a, weight) pairs, or
    None when the draw is uniform: no face-down cards, or every card a face
    card (Pareidolia). Also None if fewer pool cards are face cards than are
    face down, which only a rank changed while face down can cause.
    """
    if not marked or rules.pareidolia:
        return None
    faces = [flipped_by_mark(c, rules) for c in pool]
    f, n = sum(faces), len(pool)
    if f < marked:
        return None
    d = draws - marked_kept
    weights = [(marked_kept + k, Fraction(comb(f - marked, k), comb(n - marked, d) * comb(f, marked_kept + k)))
               for k in range(max(0, d - (n - f)), min(d, f - marked) + 1)]
    return faces, weights


Split = tuple[list[int], list[tuple[tuple[int, ...], Fraction]]]


def _marks(split: tuple[list[bool], list[tuple[int, Fraction]]] | None) -> Split | None:
    """The Mark's split (_mark_split) as the search takes it: the face cards are
    mark 0 (-1 for none), and each count a one-mark tuple."""
    if split is None:
        return None
    faces, weights = split
    return [0 if f else -1 for f in faces], [((a,), w) for a, w in weights]


@dataclass(frozen=True)
class Marked:
    """Face-down cards that are a uniformly random set of `size` of `cards`
    (counting copies), all kept or all gone (posterior.Window)."""
    cards: frozenset[Card]
    size: int
    kept: bool


def _window_split(pool: list[Card], draws: int, windows: Sequence[Marked]) -> Split:
    """How windows of face-down cards skew the unseen cards in the final hand,
    as _mark_split does The Mark's: which pool cards are each window's (its
    mark, or -1), and a weight for each count of each window's cards in the
    draw.

    A window's r face-down cards are a uniform r-set of its w cards, each
    window's independently, and the m other unknown cards of the final hand a
    uniform m-set of the N - R pool cards left, R being all the windows' r. A
    kept window's cards are among the `draws`, which are m and theirs: a draw
    with a of its cards has them in C(a, r) ways. A gone one's aren't: a draw
    misses them in C(w - a, r) ways. A draw is the product of its windows'
    ways out of the product of their C(w, r), times C(N - R, m).
    """
    faces = [-1] * len(pool)
    sizes = [0] * len(windows)
    for j, card in enumerate(pool):
        for k, window in enumerate(windows):
            if card in window.cards:
                faces[j] = k
                sizes[k] += 1
                break
    n = len(pool)
    m = draws - sum(w.size for w in windows if w.kept)
    whole = comb(n - sum(w.size for w in windows), m)
    ranges = []
    for window, w in zip(windows, sizes):
        r = window.size
        whole *= comb(w, r)
        if window.kept:
            ranges.append([(a, comb(a, r)) for a in range(r, min(w, draws) + 1)])
        else:
            ranges.append([(a, comb(w - a, r)) for a in range(min(w - r, m) + 1)])
    others = n - sum(sizes)
    weights = []
    for picked in product(*ranges):
        drawn = sum(a for a, _ in picked)
        if drawn <= draws and draws - drawn <= others:
            weights.append((tuple(a for a, _ in picked), Fraction(prod(ways for _, ways in picked), whole)))
    return faces, weights


_gc_lock = Lock()
_gc_searches = 0  # searches running with the garbage collector paused
_gc_was_enabled = False


@contextmanager
def _gc_paused() -> Iterator[None]:
    """Pause Python's cyclic garbage collector while any search runs.

    A big search creates millions of tuples and frozensets, and the collector
    keeps traversing them, which costs 5-8% of the time (docs/performance).
    The searches make no garbage cycles that outlive them (_favourable breaks
    its recursive closure's), so nothing piles up. The collector is process-wide and a
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
                   rules: Rules, cancel: Event | None, faces: list[int] | None = None,
                   face_weights: Sequence[tuple[tuple[int, ...], int]] = (), reuse: Reuse | None = None,
                   relabel: bool = True) -> int:
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

    `faces` and `face_weights` are passed on to _favourable: the identity
    holds for any weighting of the draws that depends only on their number of
    marked cards, and relabelling suits keeps every card's rank, so its face.
    Not `relabel` if the marks depend on suits (windows, _window_split).
    """
    rest, by_suit = _split_by_suit(patterns)
    suits = list(by_suit)
    if relabel and _interchangeable(patterns):
        ranks = {s: (tuple(sorted(k[0] for c in kept if (k := card_key(c, rules)) and k[1] == s)),
                     tuple(sorted(k[0] for c in pool if (k := card_key(c, rules)) and k[1] == s))) for s in SUITS}

        def term_key(subset: tuple[str, ...]) -> object:
            return (tuple(sorted(ranks[s] for s in subset)),
                    tuple(sorted(ranks[s] for s in SUITS if s not in subset)))
    else:
        def term_key(subset: tuple[str, ...]) -> object:
            return subset
    terms: dict[object, int] = {}
    # Every pattern of suit s needs at least this many cards of the suit, in a
    # hand of `present` classes. Classes that can't be present only weaken it.
    known = kept if reuse is None else (*kept, *reuse.cards)
    present = frozenset(k for card in (*known, *pool) if (k := card_key(card, rules)) is not None)
    necessary = None if reuse is None else reuse.necessary.get((patterns, present))
    if necessary is None:
        necessary = {s: Requirement(suit_keys(s), min(_cards_within(p, suit_keys(s), present) for p in pats))
                     for s, pats in by_suit.items()}
        if reuse is not None:
            reuse.necessary[patterns, present] = necessary
    rest_ways: int | None = None
    impossible: set[frozenset[str]] = set()
    total = 0
    for size in range(1, len(suits) + 1):
        sign = 1 if size % 2 else -1
        for subset in combinations(suits, size):
            key = frozenset(subset)
            together = tuple(necessary[s] for s in subset)
            if (any(key - {s} in impossible for s in subset)
                    or not _favourable([together], [1], kept, pool, draws, rules, cancel,
                                       faces=faces, face_weights=face_weights, reuse=reuse)):
                impossible.add(key)
                if rest_ways is None:
                    rest_ways = _favourable(rest, [1] * len(rest), kept, pool, draws, rules, cancel,
                                           faces=faces, face_weights=face_weights, reuse=reuse)
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
                                        together if size > 1 else None, faces, face_weights, reuse)
            total += sign * terms[tk]
    return total


def _cards_within(pattern: Pattern, keys: frozenset[ClassKey], present: set[ClassKey]) -> int:
    """Fewest cards with a class in `keys` in a hand of `present` classes that satisfies `pattern`."""
    inner = tuple(req for req in pattern if req.keys <= keys)
    return max((sum(inner[q].need for q in g) for g in _disjoint_groups(inner, present)), default=0)


def _favourable(patterns: Sequence[Pattern], masks: list[int], kept: list[Card], pool: list[Card],
                draws: int, rules: Rules, cancel: Event | None, constraint: Pattern | None = None,
                faces: list[int] | None = None, face_weights: Sequence[tuple[tuple[int, ...], int]] = (),
                reuse: Reuse | None = None) -> int:
    """Draws of `draws` cards from `pool` after which the satisfied patterns cover every group.

    Pattern p belongs to the groups in the bitmask `masks[p]`; with a single
    group this is "some pattern is satisfied". `constraint` holds whenever the
    groups are covered without a pattern that belongs to all of them, so once
    it's out of reach only such patterns are left.

    With `faces` (each pool card's mark, or -1: The Mark's face cards, or
    windows of face-down cards), a draw with a_k cards of each mark k counts w
    times for each (a, w) in `face_weights` (none if a isn't listed). Cards of
    each mark and the others are then kept in separate classes, and the search
    tracks how many of each mark are still to be drawn; the searches for each
    a share their memo.

    With `reuse`, the search's layout (_layout) and its result are kept for
    later calls with the same patterns and pool.
    """
    if not patterns:
        return 0
    lay = _layout(tuple(patterns), tuple(masks), pool, rules, constraint,
                  None if faces is None else tuple(faces), reuse)
    have = [[0] * len(pattern) for pattern in lay.patterns]
    for card in kept:
        for p, q in lay.members.get(card_key(card, rules), ()):
            have[p][q] += 1
    kept_have = [tuple(h) for h in have]
    if reuse is None:
        return _search(lay, kept_have, draws, cancel, face_weights, shared=False)
    key = lay, tuple(kept_have), draws, tuple(face_weights)
    found = reuse.favourable.get(key)
    if found is None:
        found = reuse.favourable[key] = _search(lay, kept_have, draws, cancel, face_weights, shared=True)
    return found


class Reuse:
    """What the engine's calculations can share when they draw from the same pool,
    as the discards a best-discard search compares do: each search's layout
    (_layout) and result, by what they depend on. Only for one thread.

    `cards` are every card that may be kept (the hand). The bounds that
    _union_by_suit prunes with then hold for any of them kept, so they're
    worked out once per pool rather than for each choice of kept cards.
    """

    def __init__(self, cards: Sequence[Card] = ()) -> None:
        self.cards = list(cards)
        self.layouts: dict[tuple, _Layout] = {}
        self.favourable: dict[tuple, int] = {}
        self.necessary: dict[tuple, dict[str, Requirement]] = {}


@dataclass(slots=True, eq=False)  # compared by identity: it's part of Reuse.favourable's keys
class _Layout:
    """What a search depends on besides the kept cards and the number of draws:
    the classes the pool's cards fall into, each pattern's tables, and the
    parts with their transitions (see _search). _layout's comments explain each."""
    every: int
    patterns: list[Pattern]
    masks: list[int]
    c: int | None
    faces: tuple[int, ...] | None
    needs: list[tuple[int, ...]]
    members: dict[object, list[tuple[int, int]]]
    classes: list[tuple[tuple[tuple[int, int], ...], int]]
    face_class: list[int]  # the class's mark, or -1
    remaining: list[int]
    faces_left: list[int]  # marked cards from class i on, of any mark
    marks_left: list[tuple[int, ...]]  # of each mark
    bases: tuple[int, ...]  # a count of each mark is one int, sum(count[k] * bases[k])
    part_mask: list[int]
    part_reqs: list[tuple[tuple[int, int], ...]]
    part_need: list[int]
    part_bits: list[int]
    part_single: list[tuple[int, int] | None]
    single_parts: set[int]
    intern: Callable[[int, list[tuple[int, int]]], int]
    status: Callable[[int, Sequence[int], int], int]
    step: Callable[[int, int, int], int]
    transitions: list[list[dict[int, int]]]
    cards_needed: Callable[[int, list[int]], int]
    start: list[int]
    deferred: list[list[tuple[int, int]]]
    deferred_sets: list[tuple[int, ...]]
    in_deferred: list[list[int]]
    join_at: list[int]
    joining_at: list[list[int]]
    signatures: dict[tuple, int]
    memo: dict[tuple, int]
    first: list[dict[tuple[int, ...], tuple[int, tuple[int, ...]]]]


def _layout(patterns: tuple[Pattern, ...], masks: tuple[int, ...], pool: list[Card], rules: Rules,
            constraint: Pattern | None, faces: tuple[int, ...] | None, reuse: Reuse | None) -> _Layout:
    """The layout of a search for _favourable; from `reuse` if it has one for these."""
    layout_key = None
    if reuse is not None:
        layout_key = patterns, masks, constraint, tuple(card_key(card, rules) for card in pool), faces
        lay = reuse.layouts.get(layout_key)
        if lay is not None:
            return lay

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

    # Merge pool cards with identical membership into one class; cards that
    # count towards nothing go into the dead class.
    # The search sweeps the classes rank by rank, so each straight window or
    # set of a kind is settled within a few classes, with Wild Cards first in
    # their rank. If no requirement spans several suits, the suits don't
    # interact (but through Wild Cards), and sweeping suit by suit only has to
    # remember one suit's progress at a time.
    # A class is its cards' membership and their mark (-1 for none; only told apart with `faces`).
    class_counts: Counter[tuple[tuple[tuple[int, int], ...], int]] = Counter()
    lowest: dict[tuple[tuple[tuple[int, int], ...], int], tuple] = {}
    suit_by_suit = not any(len({suit for _, suit in req.keys} - {WILD}) > 1
                           for pattern in patterns for req in pattern)
    for j, card in enumerate(pool):
        key = card_key(card, rules)
        cls = tuple(members.get(key, ())), -1 if faces is None else faces[j]
        class_counts[cls] += 1
        if cls[0]:
            rank, suit = key
            place = (suit != WILD, suit, rank) if suit_by_suit else (rank, suit != WILD, suit)
            lowest[cls] = min(lowest.get(cls, place), place)
    marks = max(1, 1 + max(faces, default=-1)) if faces is not None else 0
    dead = class_counts.pop(((), -1), 0)
    dead_marks = [class_counts.pop(((), k), 0) for k in range(marks)]
    by_place = sorted(class_counts.items(), key=lambda item: (lowest[item[0]], item[0]))
    classes = [(sig, count) for (sig, _), count in by_place]
    face_class = [mark for (_, mark), _ in by_place]
    # Requirements on a single rank, which decide when a pattern joins the search.
    local = [[len({rank for rank, _ in req.keys}) == 1 for req in pattern] for pattern in patterns]
    n = len(classes)

    # remaining[i] = pool cards in classes i.. plus dead cards; marks_left[i] = those of each mark,
    # and faces_left[i] those of any.
    remaining = [0] * (n + 1)
    remaining[n] = dead + sum(dead_marks)
    left = list(dead_marks)
    marks_left = [()] * (n + 1)
    marks_left[n] = tuple(left)
    for i in range(n - 1, -1, -1):
        remaining[i] = remaining[i + 1] + classes[i][1]
        if face_class[i] >= 0:
            left[face_class[i]] += classes[i][1]
        marks_left[i] = tuple(left)
    faces_left = [sum(m) for m in marks_left]
    bases = []
    base = 1
    for total in marks_left[0]:
        bases.append(base)
        base *= total + 1

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
            # Interning deduplicates requirements: several identical counts
            # still describe one condition, including a single-card out.
            part_single.append(unmet[0] if len(key[1]) == 1 and unmet[0][1] == 1 else None)
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
    # The part a pattern joins the search with, by its deferred counts.
    join_at = [bisect_left(ps, s) for ps, s in zip(steps, start)]
    # Searches with this layout share their memo (see _search). For that they
    # need the patterns (bar the constraint) that join at each class, or never
    # (n), and they keep each pattern's first part and deficits by its kept cards.
    memo: dict[tuple, int] = {}
    signatures: dict[tuple, int] = {}
    joining_at: list[list[int]] = []
    first: list[dict[tuple[int, ...], tuple[int, tuple[int, ...]]]] = []
    if reuse is not None:
        joining_at = [[] for _ in range(n + 1)]
        for p in range(len(patterns)):
            if p != c:
                joining_at[start[p]].append(p)
        first = [{} for _ in patterns]
    lay = _Layout(
        every=every, patterns=patterns, masks=masks, c=c, faces=faces, needs=needs, members=members,
        classes=classes, face_class=face_class, remaining=remaining, faces_left=faces_left,
        marks_left=marks_left, bases=tuple(bases),
        part_mask=part_mask, part_reqs=part_reqs, part_need=part_need, part_bits=part_bits,
        part_single=part_single, single_parts=single_parts, intern=intern, status=status, step=step,
        transitions=transitions, cards_needed=cards_needed, start=start, deferred=deferred,
        deferred_sets=deferred_sets, in_deferred=in_deferred, join_at=join_at, joining_at=joining_at,
        signatures=signatures, memo=memo, first=first)
    if layout_key is not None:
        reuse.layouts[layout_key] = lay
    return lay


def _initial_parts(lay: _Layout, kept_have: list[tuple[int, ...]], draws: int,
                   shared: bool) -> tuple[dict[int, int], dict[int, tuple[int, ...]]]:
    """The patterns that can still be satisfied, each with the cards it needs at
    least, and, if `shared`, the deficits of those not yet satisfied."""
    status, part_need = lay.status, lay.part_need
    if not shared:
        return {p: part_need[v] for p in range(len(lay.patterns))
                if (v := status(p, kept_have[p], 0)) != DEAD and part_need[v] <= draws}, {}
    initial: dict[int, int] = {}
    deficits_of: dict[int, tuple[int, ...]] = {}
    for p, (have, parts) in enumerate(zip(kept_have, lay.first)):
        found = parts.get(have)
        if found is None:
            found = parts[have] = (status(p, have, 0),
                                   tuple(need - h if need > h else 0 for need, h in zip(lay.needs[p], have)))
        v = found[0]
        if v != DEAD and part_need[v] <= draws:
            initial[p] = part_need[v]
            if initial[p]:
                deficits_of[p] = found[1]
    return initial, deficits_of


def _join_tables(lay: _Layout, kept_have: list[tuple[int, ...]], draws: int, masks: list[int], c: int | None,
                 initial: dict[int, int]) -> tuple[list[list[int]], list[int], list, list]:
    """What ways needs to know of the patterns still to join the search:
    activate[i], those that join at class i; join_need[p], the fewest draws
    pattern p can need by then; pending; and tracked."""
    n = len(lay.classes)
    start, deferred, deferred_sets = lay.start, lay.deferred, lay.deferred_sets
    activate: list[list[int]] = [[] for _ in range(n)]
    # pending[a][i][r] = groups covered by possible patterns that join at class
    # i or later and may need at most r cards then; a = 0 counts only patterns
    # of every group.
    pending = [[[0] * (draws + 1) for _ in range(n + 1)] for _ in range(2)]
    # Deferred caps by (set, join class, minimum draws needed at that class).
    # Even if every deferred requirement is met, the others still need cards.
    defer_caps: dict[tuple[int, int, int], int] = {}
    join_need = [0] * len(lay.patterns)
    for p, need in initial.items():
        if need and (masks[p] or p == c):  # unsatisfied but possible, so some class counts towards it
            if p != c:
                activate[start[p]].append(p)
            if deferred[p]:  # cards drawn before it joins may count towards it
                deficits = [k - h if k > h else 0 for k, h in zip(lay.needs[p], kept_have[p])]
                undeferred = deficits[:]
                for q, _ in deferred[p]:
                    undeferred[q] = 0
                need = lay.cards_needed(p, undeferred)
                for q, d in deferred[p]:
                    if deficits[q]:
                        key = (d, start[p], need)
                        defer_caps[key] = max(defer_caps.get(key, 0), deficits[q])
            join_need[p] = need
            pending[1][start[p]][need] |= masks[p]
            if masks[p] == lay.every:
                pending[0][start[p]][need] |= masks[p]
    # tracked[i][rem] holds only counts that a reachable waiting pattern needs.
    # Once too few draws remain, its history cannot affect any continuation.
    # Of sets with the same classes before i, keep just one representative.
    tracked_at: list[list[dict[tuple[int, ...], tuple[int, int]]]] = [
        [{} for _ in range(draws + 1)] for _ in range(n + 1)]
    for (d, join, need), cap in defer_caps.items():
        cls = deferred_sets[d]
        for t in range(cls[0] + 1, join + 1):
            behind = cls[:bisect_left(cls, t)]
            for rem in range(need, draws + 1):
                d0, cap0 = tracked_at[t][rem].get(behind, (d, 0))
                tracked_at[t][rem][behind] = d0, max(cap0, cap)
    tracked = [[tuple(t.values()) for t in by_rem] for by_rem in tracked_at]
    for table in pending:
        for i in range(n, -1, -1):
            for r in range(draws + 1):
                if r:
                    table[i][r] |= table[i][r - 1]
                if i < n:
                    table[i][r] |= table[i + 1][r]
    return activate, join_need, pending, tracked


def _class_signatures(lay: _Layout, kept_have: list[tuple[int, ...]], draws: int,
                      deficits_of: dict[int, tuple[int, ...]], shared: bool) -> list[int]:
    """What stands for the class in the memo's keys.

    The count from a class on depends on the kept cards only through the
    patterns that haven't joined by then (and the constraint), since the
    others' are in their parts: through what each of those still needs, if
    it can be satisfied. signature[i] stands for that and the number of
    draws, so memo entries hold for every search with this layout: with
    Reuse, the discards a best-discard search tries share them. It also
    tells the classes apart.
    Unshared, the memo only holds this search's entries, so the class will do.
    """
    signature = list(range(len(lay.classes) + 1))
    if shared:
        signatures = lay.signatures
        sig = signatures.setdefault(("base", draws, kept_have[lay.c] if lay.c is not None else None),
                                    len(signatures))
        for i in range(len(lay.classes), -1, -1):
            sig = signature[i] = signatures.setdefault(
                (sig, tuple([deficits_of.get(p) for p in lay.joining_at[i]])), len(signatures))
    return signature


def _drop_implied(lay: _Layout, live: set[int], singles: set[int], todo: int) -> None:
    """Remove from `live` the parts that `singles` make redundant: a part that
    needs a card of some classes does that to any part that needs one of a
    subset of them (and covers no other groups)."""
    part_mask, part_reqs, part_bits, part_single = lay.part_mask, lay.part_reqs, lay.part_bits, lay.part_single
    if lay.every == 1 and len(singles) > 1:
        # In a plain OR, "one of A or one of B" is "one of A | B".
        # This also drops parts implied by the union but not by either
        # original part alone. Different coverage groups must stay apart.
        bits = 0
        for pid in singles:
            bits |= part_bits[pid]
        merged = lay.intern(1, [(bits, 1)])
        live.difference_update(singles)
        live.add(merged)
        singles = {merged}
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


def _search(lay: _Layout, kept_have: list[tuple[int, ...]], draws: int, cancel: Event | None,
            face_weights: Sequence[tuple[tuple[int, ...], int]], shared: bool) -> int:
    """_favourable's count, for the kept cards' contributions `kept_have` to each requirement.
    `shared` if other searches will use the layout too (see _class_signatures).

    The setup is in the helpers above. The recursion stays one closure, since it
    runs millions of times and reads all of this on every call.
    """
    every, c = lay.every, lay.c
    masks = list(lay.masks)
    classes, face_class, remaining, faces_left = lay.classes, lay.face_class, lay.remaining, lay.faces_left
    part_mask, part_need, part_bits = lay.part_mask, lay.part_need, lay.part_bits
    single_parts, status, step, transitions = lay.single_parts, lay.status, lay.step, lay.transitions
    start, deferred, deferred_sets, in_deferred, join_at = (lay.start, lay.deferred, lay.deferred_sets,
                                                             lay.in_deferred, lay.join_at)
    n = len(classes)
    split = lay.faces is not None
    marks_left, bases = lay.marks_left, lay.bases
    marks = len(bases)
    single = marks == 1  # The Mark, or one window: the count is the number itself
    if split:
        # Each count of each mark as one int (see _Layout.bases), with its weight.
        coded = []
        for a, w in face_weights:
            if (all(x <= total for x, total in zip(a, marks_left[0])) and not any(a[marks:])
                    and draws - sum(a) <= remaining[0] - faces_left[0]):
                coded.append((sum(x * b for x, b in zip(a, bases)), a, w))
    drawn_from = [0] * len(deferred_sets)
    initial, deficits_of = _initial_parts(lay, kept_have, draws, shared)
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
        if split:
            return sum(w * prod(comb(total, x) for total, x in zip(marks_left[0], a))
                       * comb(remaining[0] - faces_left[0], draws - sum(a)) for _, a, w in coded)
        return comb(remaining[0], draws)
    activate, join_need, pending, tracked = _join_tables(lay, kept_have, draws, masks, c, initial)
    c_start = start[c] if c is not None else n
    c_initial = initial[c] if c is not None else 0
    joined: dict[tuple[int, ...], int] = {}
    signature = _class_signatures(lay, kept_have, draws, deficits_of, shared)

    def join(p: int) -> int:
        key = (p, *(drawn_from[d] for _, d in deferred[p]))
        pid = joined.get(key)
        if pid is None:
            have = list(kept_have[p])
            for q, d in deferred[p]:
                have[q] += drawn_from[d]
            pid = joined[key] = status(p, have, join_at[p])
        return pid

    memo = lay.memo
    # Joining parts, by (class, draws left, todo, free, deferred counts).
    joining: dict[tuple, list[int]] = {}

    def ways(i: int, rem: int, todo: int, state: list[int], cp: int | None, fr: int) -> int:
        """Ways to draw `rem` more cards from classes i.. (plus dead cards) after
        which the groups in `todo` are covered. `state` holds the parts of the
        patterns that have joined and may still be possible, and `todo`
        already leaves out the groups of satisfied ones. `cp` is the
        constraint's part once it has joined (DEAD once out of reach). With
        `faces`, exactly `fr` of the `rem` cards are face cards (0 otherwise);
        with several marks, `fr` codes how many of each (_Layout.bases)."""
        if single:
            others = remaining[i] - faces_left[i]
            if fr > faces_left[i] or rem - fr > others:
                return 0
            if not todo:
                return comb(faces_left[i], fr) * comb(others, rem - fr)
            marked = fr
        elif split:
            counts = [fr // b % (total + 1) for b, total in zip(bases, marks_left[0])]
            if any(map(gt, counts, marks_left[i])):
                return 0
            marked = sum(counts)
            others = remaining[i] - faces_left[i]
            if rem - marked > others:
                return 0
            if not todo:
                return prod(map(comb, marks_left[i], counts)) * comb(others, rem - marked)
        elif not todo:
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
        deferred_key = tuple([min(drawn_from[d], cap) for d, cap in tracked[i][rem]])
        memo_key = (signature[i], rem, todo, frozenset(live), deferred_key, fr)
        total = memo.get(memo_key)
        if total is not None:
            return total
        keys = [memo_key]
        if len(live) > 1 and (singles := live & single_parts):
            size = len(live)
            _drop_implied(lay, live, singles, todo)
            if len(live) < size:
                memo_key = (signature[i], rem, todo, frozenset(live), deferred_key, fr)
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
            # The counts omit unreachable patterns, so exclude those before
            # calling join(). Draws left also belongs in this cache's key.
            join_key = (i, rem, todo, free, deferred_key)
            parts = joining.get(join_key)
            if parts is None:
                parts = joining[join_key] = [
                    pid for p in activate[i]
                    if join_need[p] <= rem and masks[p] & todo and (free or masks[p] == every)
                    and (pid := join(p)) != DEAD and part_need[pid] <= rem]
            moving += parts
        if i == c_start:
            cp = join(c) if free else DEAD
        c_moves = cp is not None and cp != DEAD and part_bits[cp] & bit
        sets = in_deferred[i]
        # The most cards of class i that can be drawn, and whether they use up `fr`.
        mark = face_class[i]
        if not split:
            drawn, face_step = min(count, rem), 0
        elif mark < 0:
            drawn, face_step = min(count, rem - marked), 0
        elif single:
            drawn, face_step = min(count, fr), 1
        else:
            drawn, face_step = min(count, counts[mark]), bases[mark]
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
            # `del ways` below runs only once the search is over.
            total += comb(count, k) * ways(i + 1, rem - k, left, child, child_cp, fr - face_step * k)  # noqa: F821
        for d in sets:
            drawn_from[d] -= drawn
        for key in keys:
            memo[key] = total
        return total

    try:
        if split:
            return sum(w * ways(0, draws, todo, [], None, code) for code, _, w in coded)
        return ways(0, draws, todo, [], None, 0)
    finally:
        # `ways` refers to itself through its closure, which would keep this whole
        # frame alive until the garbage collector runs (it's paused, see
        # _gc_paused, so cycles pile up and are freed in one long pause later).
        # Breaking the cycle frees everything as soon as the search returns.
        del ways


DEAD = -1  # a part that can no longer be satisfied


def _bound(unmet: list[tuple[int, int]]) -> int:
    """Lower bound on the cards needed for requirements (classes bitmask, deficit) that may overlap.

    The largest deficit sum over a greedy group of requirements with disjoint
    classes. The classes are those still to come, so requirements that only
    overlapped on classes already behind count separately.
    """
    ordered = sorted(unmet, key=lambda u: -u[1])
    groups = disjoint_groups([f for f, _ in ordered])
    return max((sum(ordered[q][1] for q in group) for group in groups), default=0)


def _disjoint_groups(pattern: Pattern, present: set[ClassKey]) -> list[tuple[int, ...]]:
    """Greedily split requirement indices into groups that no card of a `present` class counts twice towards."""
    return [tuple(g) for g in disjoint_groups([req.keys & present for req in pattern])]


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
    pool: list[Card]  # unseen cards: undrawn deck + face-down cards in hand or discarded
    marked: int  # face-down cards known to be face cards (The Mark): in hand, discarded now or before
    hooked: int = 0  # kept cards The Hook throws away at random before the draw
    added: tuple[Card, ...] = ()  # DNA's copies in the final hand, which The Hook can't take
    added_hidden: int = 0  # DNA's copies of a face-down card


def _hooked(state: GameState, discard: list[int]) -> int:
    """How many kept cards The Hook throws away before the draw: 2 (or all there
    are) when the selection is played, which it has to be without discards left."""
    if not (state.hook and discard and state.discards_left <= 0):
        return 0
    return min(2, len(state.hand) - len(set(discard)))


def _dna_copies(state: GameState, discard: list[int]) -> int:
    """How many copies of the selection DNA puts in the hand: when it's a lone
    card played as the round's first hand, which it has to be without discards
    left. Not if the hand is debuffed, which only The Psychic does to a first
    hand (`evaluate_play` skips the jokers' `before` step)."""
    if state.discards_left > 0 or state.hands_played or len(set(discard)) != 1:
        return 0
    if state.blind == "The Psychic" and not state.blind_disabled:
        return 0
    return state.dna_copies


def _discard(state: GameState, discard: list[int]) -> _Discard:
    discard_set = set(discard)
    hooked = _hooked(state, discard)
    copies = _dna_copies(state, discard)
    played = state.hand[discard[0]] if copies else None
    kept = [c for i, c in enumerate(state.hand) if i not in discard_set]
    kept_known = [c for c in kept if not c.hidden]
    if not discard:
        # The game only draws after a play or discard (or at the round's start,
        # but odds are only asked for once the hand is dealt), so a hand left
        # short by a consumable or a bigger hand size stays short.
        draws = 0
    elif state.serpent:
        draws = 3  # however many were discarded, and even past the hand size
    else:
        # DNA's copies go straight into the hand, taking up room the draw would fill.
        draws = state.hand_size - len(kept) + hooked - copies
    draws = max(0, min(draws, state.deck_size))
    # One copy of a face-down card is exactly that card kept face down. Several
    # are the same unknown card, so evaluate_discard branches on what it is
    # (_posterior_for) and they arrive here as known cards.
    added_hidden = copies if played is not None and played.hidden else 0
    marked = state.hidden + state.discarded_hidden if state.mark else 0
    return _Discard([state.hand[i] for i in sorted(discard_set)], kept_known,
                    len(kept) - len(kept_known), draws, state.unseen, marked, hooked,
                    () if added_hidden or played is None else (played,) * copies, added_hidden)


def rules_after(state: GameState, discard: list[int]) -> list[tuple[Rules, int]]:
    """The rules the final hand is judged under after discarding `discard`,
    each with how many of the equally likely ways lead to it.

    Only Crimson Heart changes them, and only after a hand is played, which
    the selection has to be without discards left: after the draw
    (`Blind:drawn_to_hand`), it lifts every joker's debuff, bar a Perishable
    one's that has run out (`Card:set_debuff`), then debuffs one of the jokers
    that weren't debuffed, uniformly at random (any of them with only one).
    The draw itself is unchanged, and so is its size: a hand-size joker's
    bonus comes and goes after it.
    """
    jokers = state.jokers
    if not (discard and state.discards_left <= 0 and state.crimson and jokers):
        return [(state.rules, 1)]
    picks = [i for i, j in enumerate(jokers) if not j.debuff] if len(jokers) > 1 else [0]
    if not picks:
        picks = [None]  # every joker debuffed: none is picked, and only the perished stay so
    outcomes = Counter(Rules.from_jokers(replace(j, debuff=j.perished or i == pick) for i, j in enumerate(jokers))
                       for pick in picks)
    return list(outcomes.items())


def _merged(outcomes: list[tuple[Rules, int]], same: Callable[[Rules], object]) -> list[tuple[Rules, int]]:
    """`outcomes` with the rules that give the same `same` (and so the same odds) merged."""
    merged: dict[object, tuple[Rules, int]] = {}
    for rules, n in outcomes:
        key = same(rules)
        first, m = merged.get(key, (rules, 0))
        merged[key] = first, m + n
    return list(merged.values())


# --- What the hand's sorts say about face-down cards (docs/plans/face-down-windows.md) ---

_USABLE_KEPT = 16  # states whose evidence was checked: build_report and the search ask many times
_usable: OrderedDict[tuple, bool] = OrderedDict()
_usable_lock = Lock()
_ignored: set[tuple] = set()  # observations no deal fits, already logged


def _allowed(state: GameState) -> Callable[[Card], bool]:
    """Which unseen cards a face-down card can be: under The Mark only face cards
    (`flipped_by_mark`), as in _mark_split."""
    rules = state.rules
    return lambda card: not state.mark or flipped_by_mark(card, rules)


def _checker(cancel: Event | None) -> Callable[[], None]:
    def check() -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled
    return check


def usable(state: GameState, cancel: Event | None = None) -> bool:
    """Whether some deal of the unseen cards fits `state.observations`.

    Observations that none does (a card changed while face down, say) are set
    aside, logged once on stderr: the odds are then today's, with no part of
    that evidence used. Raises Cancelled soon after `cancel` is set.
    """
    if not any(o.latent for o in state.observed):
        return True
    key = (state.observations, state.observed, tuple(state.unseen), state.mark, state.mark and state.rules.pareidolia)
    with _usable_lock:
        if key in _usable:
            _usable.move_to_end(key)
            return _usable[key]
    # Telling no deals apart, the sum is one branch: quick even for The House's first hand.
    found = summed(state.observations, state.observed, state.unseen, _allowed(state), (), _nothing,
                   _checker(cancel)).total > 0
    if not found:
        failure = state.observations, state.observed
        with _usable_lock:
            first = failure not in _ignored
            _ignored.add(failure)
        if first:
            print("The hand's sorts fit no deal of the unseen cards, so the odds don't use them: "
                  f"{state.observations} of {state.observed}", file=sys.stderr, flush=True)
    with _usable_lock:
        _usable[key] = found
        while len(_usable) > _USABLE_KEPT:
            _usable.popitem(last=False)
    return found


def _nothing(hand: int | None, card: Card) -> None:
    return None


def posterior(state: GameState, cancel: Event | None = None, extra: tuple[int, ...] = (),
              label: Label | None = None, skip: frozenset[int] = frozenset()) -> Posterior | None:
    """What the face-down cards in `state.observations` are, as a posterior
    (posterior.summed), with the face-down cards at hand indices `extra` too
    (no evidence: see _posterior_for). None if there's nothing to branch on.

    Branches are summed by `label`, what the caller sees of each card (see
    posterior.summed); without it each deal is its own branch. The latent
    cards with ids in `skip` are left out.
    Under The Mark the face-down cards are face cards (`_allowed`). Observations
    no deal fits are set aside (`usable`).
    Raises Cancelled soon after `cancel` is set.
    """
    observations, observed = state.observations, state.observed
    if not usable(state, cancel):
        observations, observed = (), ()
    in_hand = {o.hand for o in observed if o.hand is not None}
    extra = tuple(i for i in extra if i not in in_hand)
    if not any(o.latent for i, o in enumerate(observed) if i not in skip) and not extra:
        return None
    found = summed(observations, observed, state.unseen, _allowed(state), extra, label, _checker(cancel), skip)
    # No card it can be: only a rank changed under The Mark does that, with DNA's copies alone.
    return found if found.total else None


def with_usable_evidence(state: GameState, cancel: Event | None = None) -> GameState:
    """`state`, or, if its observations are set aside (see `usable`), `state`
    without them, so that its face-down cards are interchangeable again."""
    if usable(state, cancel):
        return state
    return replace(state, observations=(), observed=(), hand=[replace(c, latent=None) for c in state.hand])


def _posterior_for(state: GameState, discard: list[int], cancel: Event | None,
                   classes: Callable[[Card], object] | None, skip: frozenset[int] = frozenset()) -> Posterior | None:
    """The posterior the odds of discarding `discard` average over, its deals
    summed by what those odds see of them (`classes`, see _labels); every deal
    its own branch without `classes`.

    Besides the evidence, a face-down card played alone as the round's first
    hand is enumerated when DNA makes 2 or more copies of it: they're the same
    card, which isn't like as many separate unseen ones. (One copy is exactly
    the card kept face down, so it needs no branching.)

    The face-down cards with ids in `skip` are left out (a window, _window_for).
    """
    dna = _dna_branch(state, discard)
    label = None if classes is None else _labels(state, discard, dna, classes)
    if _says_nothing_here(state, discard):
        state = replace(state, observations=(), observed=())
    return posterior(state, cancel, () if dna is None else (dna,), label, skip)


def _dna_branch(state: GameState, discard: list[int]) -> int | None:
    """The face-down card DNA copies 2 or more times, if any: it's branched on."""
    return discard[0] if _dna_copies(state, discard) >= 2 and state.hand[discard[0]].hidden else None


def _labels(state: GameState, discard: list[int], dna: int | None,
            classes: Callable[[Card], object]) -> Label:
    """What the odds of discarding `discard` see of a face-down card being
    `card`, given where it ends up (posterior.summed's `label`).

    A card kept (or copied by DNA) is part of the final hand: its class
    (`classes`), and whether Cerulean Bell forces it. One gone, thrown away now
    or discarded face down before, is only missing from the cards left to
    draw, so where it went doesn't matter, and nothing about it does if no card
    is drawn. Deals alike in all of that give the same odds: the engine sees
    cards only through their classes, and the unseen cards left are the same
    classes too.
    """
    thrown = set(discard)
    copied = discard[0] if _dna_copies(state, discard) else None  # in the final hand, once or more
    resolved = {o.hand for o in state.observed if o.hand is not None} | ({dna} if dna is not None else set())
    d = _discard(state, discard)
    # Face-down cards the branches don't resolve are drawn like the deck's cards.
    unresolved = sum(1 for i, c in enumerate(state.hand) if c.hidden and i not in thrown and i not in resolved)
    drawn = d.draws + unresolved + (d.added_hidden if dna is None else 0) > 0

    def label(hand: int | None, card: Card) -> object:
        if hand is None or (hand in thrown and hand != copied):
            return ("gone", classes(card)) if drawn else None
        return ("copied" if hand == copied else "kept", state.hand[hand].forced, classes(card))
    return label


def _says_nothing_here(state: GameState, discard: list[int]) -> bool:
    """Whether the evidence can't change the odds of discarding `discard`, so
    that today's odds are exact.

    One sort with no face-up card in it (The House's first hand) says only
    which of its face-down cards is higher than which, not what cards they
    are: every set of cards can be sorted. So it only matters once they meet
    different fates (kept, copied by DNA, or gone: thrown away now or discarded
    face down before), as those are then the higher or the lower ones, or if
    Cerulean Bell forces one of them.
    """
    if len(state.observations) != 1 or not all(o.latent for o in state.observed):
        return False
    thrown = set(discard)
    copied = thrown if _dna_copies(state, discard) else set()
    fates = set()
    for o in state.observed:
        if o.hand is not None and state.hand[o.hand].forced:
            return False
        fates.add("gone" if o.discarded or (o.hand in thrown and o.hand not in copied)
                  else "copied" if o.hand in copied else "kept")
    return len(fates) == 1


def _resolved(state: GameState, branch: Branch) -> GameState:
    """`state` with the branch's face-down cards turned up: each takes its
    placeholder's place in hand (selection and Cerulean Bell's force kept), and
    one of it leaves the unseen cards, whether it's kept or discarded. Those
    discarded face down before take the place of one of the discard pile's
    hidden cards each. Deck size, hand size and indices stay as they were."""
    return _resolver(state)(branch)


def _resolver(state: GameState) -> Callable[[Branch], GameState]:
    """_resolved for many branches of `state`: where each unseen card is is found once."""
    places: dict[Card, list[int]] = {}
    for j, card in enumerate(state.unseen):
        places.setdefault(card, []).append(j)
    hidden = [i for i, c in enumerate(state.discard) if c.hidden]

    def resolve(branch: Branch) -> GameState:
        taken: dict[Card, int] = {}
        gone = set()
        for card in [c for _, c in branch.cards] + list(branch.discarded):
            n = taken.get(card, 0)
            gone.add(places[card][n])
            taken[card] = n + 1
        hand = list(state.hand)
        for i, card in branch.cards:
            hand[i] = replace(card, highlighted=hand[i].highlighted, forced=hand[i].forced)
        discard = list(state.discard)
        for i, card in zip(hidden, branch.discarded):
            discard[i] = card
        unseen = [c for j, c in enumerate(state.unseen) if j not in gone]
        return replace(state, hand=hand, unseen=unseen, discard=discard, observations=(), observed=())
    return resolve


def _rule_classes(state: GameState, discard: list[int],
                  test: Callable[[Card, Rules], object]) -> Callable[[Card], object]:
    """What the odds see of a resolved card: `test` (its class, or whether it
    matches a query) under each set of rules the final hand may be judged under,
    and under The Mark whether it's a face card."""
    rule_sets = list(dict.fromkeys([state.rules, *(rules for rules, _ in rules_after(state, discard))]))
    mark = state.rules if state.mark else None
    seen: dict[Card, object] = {}

    def classes(card: Card) -> object:
        found = seen.get(card)
        if found is None:
            found = seen[card] = (tuple(test(card, rules) for rules in rule_sets),
                                  mark is not None and flipped_by_mark(card, mark))
        return found
    return classes


def _counts_towards(target: Target, state: GameState) -> Callable[[Card, Rules], object]:
    """A card's class as far as `target` can tell: which requirements of its
    patterns it counts towards. Two cards alike in that are interchangeable
    anywhere, kept or among the cards left to draw, since swapping them meets
    and misses the same patterns (as in `search._groups`). Under Cerulean Bell
    the forced card's own class can matter (`_bell_after_draw`), so there it's
    the card's class itself."""
    if state.bell and bell_matters(target, state.rules):
        return card_key

    def test(card: Card, rules: Rules) -> object:
        return _memberships(target.hands, rules).get(card_key(card, rules), frozenset())
    return test


@cache
def _memberships(hands: tuple[str, ...], rules: Rules) -> dict[ClassKey, frozenset[tuple[int, int]]]:
    """For each class, the requirements of the hands' patterns it counts towards, as (pattern, requirement)."""
    found: dict[ClassKey, set[tuple[int, int]]] = defaultdict(set)
    for p, pattern in enumerate(Target("", hands).patterns(rules)):
        for q, req in enumerate(pattern):
            for key in req.keys:
                found[key].add((p, q))
    return {key: frozenset(pq) for key, pq in found.items()}


def _over_branches(state: GameState, post: Posterior, odds: Callable[[GameState], Fraction],
                   cancel: Event | None) -> Fraction:
    """The odds averaged over the posterior's branches, by weight: `odds` of
    each branch's resolved state, every face-down card turned up. With one
    branch for each deal (posterior.reference), it's the tests' reference for
    _averaged."""
    total = Fraction(0)
    resolve = _resolver(state)
    for branch in post.branches:
        if cancel is not None and cancel.is_set():
            raise Cancelled
        total += branch.weight * odds(resolve(branch))
    return total / post.total


def _averaged(state: GameState, discard: list[int], classes: Callable[[Card], object],
              odds: Callable[[GameState, tuple[Marked, ...]], Fraction], cancel: Event | None) -> Fraction | None:
    """The odds of discarding `discard`, averaged over what the face-down cards the
    hand's sorts tell about are; None if they tell about none, or say nothing
    here (_says_nothing_here). `odds` evaluates a branch's resolved state, with
    the windows' cards left face down (_windows_for).

    The branches are summed by what the odds see of them (`classes`, _labels),
    so there's one evaluation for each thing they can tell apart, and none at
    all for each of a window's cards.
    """
    windows, splits = _windows_for(state, discard, classes, cancel)
    skip = frozenset(i for ids, _ in windows + splits for i in ids)
    marked = tuple(m for _, m in windows)
    post = _posterior_for(state, discard, cancel, classes, skip)
    if post is None:
        if not skip:
            return None
        post = Posterior((Branch((), Fraction(1)),), Fraction(1))  # the windows' cards were all there was
    whole = post.total
    for _, options in splits:
        whole *= sum(w for w, _, _ in options)
    total = Fraction(0)
    resolve = _resolver(state)
    for branch in post.branches:
        for chosen in product(*(options for _, options in splits)):
            if cancel is not None and cancel.is_set():
                raise Cancelled
            weight = branch.weight * prod(w for w, _, _ in chosen)
            cards = [c for _, turned, _ in chosen for c in turned]
            resolved = Branch(branch.cards + tuple((i, c) for i, c in cards if i is not None), branch.weight,
                              branch.discarded + tuple(c for i, c in cards if i is None))
            total += weight * odds(resolve(resolved), marked + tuple(m for _, _, ms in chosen for m in ms))
    return total / whole


_WINDOWS_KEPT = 16
_windows: OrderedDict[tuple, list[Window]] = OrderedDict()
WINDOW_COUNTS = 64  # most combinations of how many of each window's cards a draw has
SPLITS = 5000  # most ways to split a window by its cards' fates (_split)

# A way to split a window: its weight, the cards it turns up (by hand index, None if
# discarded face down before) and the windows left.
WindowSplit = tuple[int, tuple[tuple[int | None, Card], ...], tuple[Marked, ...]]


def _windows_for(state: GameState, discard: list[int], classes: Callable[[Card], object],
                 cancel: Event | None) -> tuple[list[tuple[frozenset[int], Marked]],
                                                list[tuple[frozenset[int], list[WindowSplit]]]]:
    """Windows of `state`'s face-down cards (posterior.windows) for the odds of
    discarding `discard` to weigh the draw by, rather than go through their
    cards: each one's card ids and how the engine sees it.

    A window's cards must all meet one fate, all kept or all gone, as only then
    does what the final hand holds depend on how many of its cards the draw
    has. Under The Mark the draw is already weighed (_mark_split), and The Hook
    and Cerulean Bell pick from the final hand at random; DNA's copies of a
    face-down card are branched on. None of those take windows. Of the rest,
    those whose cards the odds tell apart (`classes`) are taken, the most told
    apart first: one whose cards they see alike costs no more to go through.
    The search goes through every combination of how many of each window's
    cards the draw has, so they're taken while those are at most WINDOW_COUNTS.

    A window whose cards meet different fates, side by side in a sort, is split
    instead (_split), if it can be in at most SPLITS ways, and they're likely
    fewer than the deals `summed` would go through: that's the second list,
    with each window's ids and its ways.
    """
    if (state.mark or state.bell or _hooked(state, discard) or _dna_copies(state, discard)
            or _says_nothing_here(state, discard) or not usable(state, cancel)):
        return [], []
    key = (state.observations, state.observed, tuple(state.unseen))
    with _usable_lock:
        found = _windows.get(key)
        if found is not None:
            _windows.move_to_end(key)
    if found is None:
        found = windows(state.observations, state.observed, state.unseen, _allowed(state), _checker(cancel))
        with _usable_lock:
            _windows[key] = found
            while len(_windows) > _WINDOWS_KEPT:
                _windows.popitem(last=False)
    thrown = set(discard)
    d = _discard(state, discard)
    unknown = d.draws + d.hidden_kept + d.added_hidden  # cards drawn from the unseen ones, with the kept face-down
    useful = []
    splits = []

    def fate(i: int) -> bool:  # kept
        return state.observed[i].hand is not None and state.observed[i].hand not in thrown

    for w in found:
        fates = {fate(i) for i in w.ids}
        if len(fates) != 1:
            if w.order and (ways := _split(w, [fate(i) for i in w.order], state)) is not None:
                # Each way is a search with a window for each block, which costs about twice what a
                # deal summed by its labels does (posterior.summed): there may be as many as
                # this of those, for each fate's multiset of the labels its cards can have.
                labels = len({classes(c) for c in w.cards})
                kept = sum(map(fate, w.ids))
                deals = comb(labels + kept - 1, kept) * (comb(labels + w.size - kept - 1, w.size - kept)
                                                         if unknown else 1)
                if 2 * len(ways) <= deals:
                    splits.append((w.ids, ways))
            continue
        kept = fates.pop()
        if not kept and not unknown:
            continue  # gone, with nothing drawn: nothing to see of them
        seen = len({classes(c) for c in w.cards})
        if seen > 1:
            # How many of its cards the draw can have.
            counts = min(len(w.cards), unknown) - w.size + 1 if kept else min(len(w.cards) - w.size, unknown) + 1
            useful.append((seen, counts, w, kept))
    taken = []
    options = 1
    for _, counts, w, kept in sorted(useful, key=lambda u: -u[0]):
        if options * counts <= WINDOW_COUNTS:
            options *= counts
            taken.append((w.ids, Marked(w.cards, w.size, kept)))
    return taken, splits


def _split(window: Window, kept: list[bool], state: GameState) -> list[WindowSplit] | None:
    """The ways a window's cards can be, by the cards at the edges of its blocks
    of cards meeting one fate (`kept`, for each card in `window.order`); None if
    there are more than SPLITS.

    The window's cards are a uniform set, sorted into its order, so its blocks
    hold the highest cards, then the next, and so on. Given the lowest card of
    each block but the last, which the odds see turned up, each block's other
    cards are a uniform set of the window's cards between that one and the
    block above's: windows that meet one fate each. A way is as likely as the
    sets it leaves: the product of C(cards between, cards left in the block).
    """
    cards = sorted(window.cards, key=lambda c: sort_value(printed(c), window.mode), reverse=True)
    blocks: list[list[int]] = []  # each block's ids, top first
    fates: list[bool] = []
    for i, k in zip(window.order, kept):
        if fates and fates[-1] == k:
            blocks[-1].append(i)
        else:
            blocks.append([i])
            fates.append(k)
    below = [sum(map(len, blocks[b + 1:])) for b in range(len(blocks))]  # cards the blocks under b need
    found: list[WindowSplit] = []

    def ways(b: int, above: int, weight: int, turned: tuple, left: tuple) -> None:
        """Block b and those under it, the cards up to index `above` being taken."""
        if len(found) > SPLITS:
            return
        block = blocks[b]
        if b == len(blocks) - 1:
            rest = cards[above + 1:]
            found.append((weight * comb(len(rest), len(block)), turned,
                          (*left, Marked(frozenset(rest), len(block), fates[b]))))
            return
        hand = state.observed[block[-1]].hand
        for j in range(above + len(block), len(cards) - below[b]):
            between = cards[above + 1:j]
            more = (Marked(frozenset(between), len(block) - 1, fates[b]),) if len(block) > 1 else ()
            ways(b + 1, j, weight * comb(len(between), len(block) - 1), (*turned, (hand, cards[j])), (*left, *more))

    ways(0, -1, 1, (), ())
    found = [w for w in found if w[0]]
    return found if len(found) <= SPLITS else None


def evaluate_discard(state: GameState, target: Target, discard: list[int],
                     cancel: Event | None = None, reuse: Reuse | None = None) -> DiscardOutcome:
    """Probability of reaching `target` after discarding hand cards at indices `discard`.

    Without discards left they'd have to be played instead, which under The
    Hook also throws away 2 random kept cards (`_after_hook`), and under
    Crimson Heart changes which joker is debuffed (`rules_after`): the odds
    are then the average over the rules the final hand may be judged under.
    A lone card played as the round's first hand comes back with DNA
    (`_dna_copies`). Calls for the same state can share a `reuse` (see Reuse).

    What the hand's sorts show of its face-down cards (`GameState.observations`)
    is used: the odds are the average over what those cards are, weighed by
    the posterior (`_averaged`).
    """
    def odds(resolved: GameState, windows: tuple[Marked, ...]) -> Fraction:
        return _evaluate_discard(resolved, target, discard, cancel, reuse, windows).probability

    p = _averaged(state, discard, _rule_classes(state, discard, _counts_towards(target, state)), odds, cancel)
    if p is None:
        return _evaluate_discard(state, target, discard, cancel, reuse)
    d = _discard(state, discard)
    return DiscardOutcome(d.discarded, d.draws, p)


def _evaluate_discard(state: GameState, target: Target, discard: list[int],
                      cancel: Event | None, reuse: Reuse | None, windows: Sequence[Marked] = ()) -> DiscardOutcome:
    """evaluate_discard with every face-down card unknown, or resolved by a
    branch, but for those in the `windows`."""
    d = _discard(state, discard)
    outcomes = rules_after(state, discard)
    total = sum(n for _, n in outcomes)
    if len(outcomes) > 1:
        # Rules the target's patterns don't see give the same odds: Shortcut for a
        # Flush, or any of them for a Pair. Smeared also changes the cards' classes.
        # (Crimson Heart is the boss, so The Mark's split and Cerulean Bell don't apply.)
        outcomes = _merged(outcomes, lambda rules: (target.patterns(rules), rules.smeared))
    p = sum((n * _discard_odds(state, target, discard, d, rules, cancel, reuse, windows) for rules, n in outcomes),
            Fraction(0)) / total
    return DiscardOutcome(d.discarded, d.draws, p)


def _discard_odds(state: GameState, target: Target, discard: list[int], d: _Discard, rules: Rules,
                  cancel: Event | None, reuse: Reuse | None, windows: Sequence[Marked] = ()) -> Fraction:
    """evaluate_discard's odds with the final hand judged under `rules`.
    `windows` only come without The Hook, Cerulean Bell or The Mark (_windows_for)."""
    if d.hooked:
        return _after_hook(target, d, rules, cancel, reuse)
    hidden = d.hidden_kept + d.added_hidden
    known = d.kept_known + list(d.added)
    unseen = hidden + d.draws
    if state.bell and bell_matters(target, rules):
        forced = state.forced
        if not discard and forced is not None and not state.hand[forced].hidden:
            # Keeping the hand keeps its forced card.
            kept = [c for i, c in enumerate(state.hand) if i != forced and not c.hidden]
            return probability(target, kept, d.pool, unseen, rules, cancel, forced=state.hand[forced], reuse=reuse)
        return _bell_after_draw(target, known, d.pool, unseen, rules, cancel, reuse)
    return probability(target, known, d.pool, unseen, rules, cancel, d.marked, hidden, reuse=reuse, windows=windows)


def can_miss(state: GameState, target: Target, discard: list[int], rng: Random, tries: int = 200,
             cancel: Event | None = None) -> bool:
    """Whether a draw that makes none of `target`'s hands was found, so that the
    odds after discarding `discard` are certainly below 1. False proves nothing.

    Much quicker than the odds, so a search that has already found odds of 1
    can rule a discard out. It only tries uniform draws, so it gives up (False)
    where the draw isn't uniform or the kept cards are random: The Mark's split,
    Cerulean Bell's forced card and The Hook. Otherwise it mirrors _discard_odds.
    With several rules the final hand may be judged under (`rules_after`), a
    miss under one is enough, since each has some chance.

    Where the hand's sorts tell about face-down cards, a uniform draw could be
    one they rule out, so each try first picks what those cards are: any branch
    of the posterior has some chance, and so has any draw from what's left.
    The branches are summed by what the target sees, so they're all it can
    tell apart.
    """
    post = _posterior_for(state, discard, cancel, _rule_classes(state, discard, _counts_towards(target, state)))
    if post is None:
        return _can_miss(state, target, discard, rng, tries)
    resolved: dict[int, GameState] = {}
    for _ in range(tries):
        b = rng.randrange(len(post.branches))
        if b not in resolved:
            resolved[b] = _resolved(state, post.branches[b])
        if _can_miss(resolved[b], target, discard, rng, 1):
            return True
    return False


def _can_miss(state: GameState, target: Target, discard: list[int], rng: Random, tries: int) -> bool:
    d = _discard(state, discard)
    if d.marked or d.hooked or state.bell:
        return False
    rules = rules_after(state, discard)[0][0]
    patterns = target.patterns(rules)

    def holds(have: Counter) -> bool:
        return any(all(sum(n for key, n in have.items() if key in req.keys) >= req.need for req in pattern)
                   for pattern in patterns)

    known = Counter(k for c in (*d.kept_known, *d.added) if (k := card_key(c, rules)) is not None)
    if holds(known):
        return False  # whatever is drawn
    draws = min(d.hidden_kept + d.added_hidden + d.draws, len(d.pool))
    keys = [card_key(c, rules) for c in d.pool]
    for _ in range(tries if draws else 1):
        if not holds(known + Counter(k for k in rng.sample(keys, draws) if k is not None)):
            return True
    return False


def _after_hook(target: Target, d: _Discard, rules: Rules, cancel: Event | None,
                reuse: Reuse | None = None) -> Fraction:
    """P(`target`) when The Hook throws away `d.hooked` of the kept cards, uniformly
    at random, before the draw (`Blind:press_play`): the average over which ones.
    Kept cards of the same class give the same odds, so it goes by how many of
    each class (face-down cards being one) it takes."""
    groups: dict[object, list[Card]] = {}
    for c in d.kept_known:
        groups.setdefault(card_key(c, rules), []).append(c)
    sizes = [len(g) for g in groups.values()] + [d.hidden_kept]
    total = Fraction(0)

    def taken(i: int, left: int) -> Iterator[tuple[int, ...]]:
        if i == len(sizes):
            if not left:
                yield ()
            return
        for k in range(min(sizes[i], left) + 1):
            for rest in taken(i + 1, left - k):
                yield (k, *rest)

    for counts in taken(0, d.hooked):
        weight = 1
        for size, k in zip(sizes, counts):
            weight *= comb(size, k)
        kept = [c for g, k in zip(groups.values(), counts) for c in g[k:]] + list(d.added)
        hidden = d.hidden_kept - counts[-1] + d.added_hidden
        total += weight * probability(target, kept, d.pool, hidden + d.draws, rules, cancel, d.marked, hidden,
                                      reuse=reuse)
    return total / comb(sum(sizes), d.hooked)


def _bell_after_draw(target: Target, kept: list[Card], pool: list[Card], draws: int,
                     rules: Rules, cancel: Event | None, reuse: Reuse | None = None) -> Fraction:
    """P(`target` can be played) when Cerulean Bell forces a random card of the final hand.

    Playing or discarding clears the forced card, and after the draw the game
    forces a uniformly random card of the new hand (`Blind:drawn_to_hand`).
    So the probability is the average over the final hand's cards of the
    probability with that card forced: each kept card, and `draws` times a
    random unseen one, which is any pool card with the others drawn from the rest.

    Cards that the forced patterns can't tell apart give the same result, so
    one search covers each group (_forced_group).
    """
    draws = max(0, min(draws, len(pool)))
    size = len(kept) + draws
    if not size:
        return Fraction(0)
    total = Fraction(0)
    done: dict[object, Fraction] = {}
    for i, c in enumerate(kept):
        key = card_key(c, rules)
        if key not in done:
            done[key] = probability(target, kept[:i] + kept[i + 1:], pool, draws, rules, cancel, forced=c,
                                   reuse=reuse)
        total += done[key]
    if draws:
        groups: dict[object, list[int]] = {}
        for j, c in enumerate(pool):
            groups.setdefault(_forced_group(target.hands, rules, card_key(c, rules)), []).append(j)
        unseen = Fraction(0)
        for members in groups.values():
            j = members[0]
            unseen += len(members) * probability(target, kept, pool[:j] + pool[j + 1:], draws - 1, rules,
                                                 cancel, forced=pool[j], reuse=reuse)
        total += draws * unseen / len(pool)
    return total / size


@cache
def _forced_group(hands: tuple[str, ...], rules: Rules, key: ClassKey | None) -> tuple:
    """What decides the odds with a forced card of class `key` drawn from the pool:
    its forced patterns, and which of their requirements it would count towards
    if it stayed in the pool (one card fewer there for those)."""
    patterns = forced_patterns(Target("", hands), rules, key)
    return patterns, frozenset((p, q) for p, pattern in enumerate(patterns)
                               for q, req in enumerate(pattern) if key in req.keys)


def count_probability(query: CardQuery, pool: list[Card], draws: int, rules: Rules = Rules(),
                      marked: int = 0, marked_kept: int = 0, have: int = 0,
                      windows: Sequence[Marked] = ()) -> Fraction:
    """P(`have` plus the number of matching cards among `draws` random cards from
    `pool` satisfies `query`).

    With The Mark, `marked` of the pool's cards are face down (in hand or
    discarded), so they
    are known to be face cards, and `marked_kept` of them are among the
    `draws`. The other draws come from the rest of the pool; see _mark_split.
    Windows of face-down cards skew the draw instead (_window_split).
    """
    draws = max(0, min(draws, len(pool) - marked + marked_kept))
    split = _window_split(pool, draws, windows) if windows else _marks(
        _mark_split(pool, draws, rules, marked, marked_kept))
    if split is not None:
        faces, weights = split
        marks = max(len(a) for a, _ in weights) if weights else 0
        sizes = [0] * (marks + 1)  # each mark's cards, and the others' last
        matching = [0] * (marks + 1)
        for c, mark in zip(pool, faces):
            sizes[mark] += 1
            matching[mark] += query.filter.test(c, rules)
        total = Fraction(0)
        for a, w in weights:
            # Given a_k cards of each mark among the unseen cards, they're a uniform
            # sample of its cards (as its face-down ones are), and the others one of the rest.
            ways = [1]  # ways[x]: draws with x matching cards
            for size, match, k in zip(sizes, matching, (*a, draws - sum(a))):
                here = [comb(match, x) * comb(size - match, k - x) for x in range(min(match, k) + 1)]
                ways = [sum(ways[i] * here[x - i] for i in range(len(ways)) if 0 <= x - i < len(here))
                        for x in range(len(ways) + len(here) - 1)]
            total += w * sum(n for x, n in enumerate(ways) if query.accepts(have + x))
        return total
    draws = min(draws, len(pool))
    matches = sum(1 for c in pool if query.filter.test(c, rules))
    others = len(pool) - matches
    hits = sum(comb(matches, k) * comb(others, draws - k)
               for k in range(min(matches, draws) + 1) if query.accepts(have + k))
    return Fraction(hits, comb(len(pool), draws))


def evaluate_cards(state: GameState, query: CardQuery, discard: list[int],
                   cancel: Event | None = None) -> DiscardOutcome:
    """Probability that the final hand after discarding satisfies `query`.

    Like evaluate_discard, it averages over what the face-down cards the hand's
    sorts tell about are (`_averaged`). Raises Cancelled soon after
    `cancel` is set.
    """
    def odds(resolved: GameState, windows: tuple[Marked, ...]) -> Fraction:
        return _evaluate_cards(resolved, query, discard, windows).probability

    p = _averaged(state, discard, _rule_classes(state, discard, query.filter.test), odds, cancel)
    if p is None:
        return _evaluate_cards(state, query, discard)
    d = _discard(state, discard)
    return DiscardOutcome(d.discarded, d.draws, p)


def _evaluate_cards(state: GameState, query: CardQuery, discard: list[int],
                    windows: Sequence[Marked] = ()) -> DiscardOutcome:
    """evaluate_cards with every face-down card unknown, or resolved by a branch.

    The final hand is the kept cards and the draw. From the player's view the
    face-down kept cards and the draw are a uniform sample of the unseen pool,
    except with The Mark: its face-down cards are face cards, so fewer of
    those are left to draw. Cerulean Bell's forced card is in the final hand
    like any other, so it changes nothing. Without discards left the selection
    would be played instead, which under The Hook also throws away 2 random
    kept cards (`_hooked`): the odds are the average over which ones, and a lone
    card played as the round's first hand comes back with DNA (`_dna_copies`).
    Under Crimson Heart, Pareidolia may be debuffed or not after the play
    (`rules_after`), which changes the face cards: the odds are the average
    over the rules.
    """
    d = _discard(state, discard)
    outcomes = rules_after(state, discard)
    total = sum(n for _, n in outcomes)
    if len(outcomes) > 1:
        # The odds only see which cards match: Four Fingers and Shortcut never
        # change that, and Smeared and Pareidolia only for some queries.
        cards = (*d.kept_known, *d.added, *d.pool)
        outcomes = _merged(outcomes, lambda rules: tuple(query.filter.test(c, rules) for c in cards))
    p = sum((n * _card_odds(query, d, rules, windows) for rules, n in outcomes), Fraction(0)) / total
    return DiscardOutcome(d.discarded, d.draws, p)


def _card_odds(query: CardQuery, d: _Discard, rules: Rules, windows: Sequence[Marked] = ()) -> Fraction:
    """evaluate_cards's odds with the final hand judged under `rules`. `windows`
    only come without The Hook or The Mark (_windows_for)."""
    have = sum(query.filter.test(c, rules) for c in d.kept_known)
    added = sum(query.filter.test(c, rules) for c in d.added)
    if not d.hooked:
        hidden = d.hidden_kept + d.added_hidden
        return count_probability(query, d.pool, hidden + d.draws, rules, d.marked, hidden, have + added, windows)
    # Of the kept cards, only whether each matches matters, or that it's face down.
    sizes = (have, len(d.kept_known) - have, d.hidden_kept)
    total = Fraction(0)
    for taken in product(*(range(min(n, d.hooked) + 1) for n in sizes)):
        if sum(taken) == d.hooked:
            hidden = d.hidden_kept - taken[2] + d.added_hidden
            weight = prod(comb(n, k) for n, k in zip(sizes, taken))
            total += weight * count_probability(query, d.pool, hidden + d.draws, rules, d.marked, hidden,
                                                have - taken[0] + added)
    return total / comb(sum(sizes), d.hooked)

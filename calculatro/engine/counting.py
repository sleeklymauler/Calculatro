"""The counting search: the exact probability that kept cards plus a random draw reach a target.

The final hand is the kept known cards plus `m` cards drawn uniformly without
replacement from the unseen pool: the undrawn deck plus any face-down cards
in hand or discarded, which are equally unknown to the player. Kept face-down cards are
modelled as part of that draw, since by symmetry it's the same distribution.
The face-down cards The Mark drew as face cards (`Card.marked`) are known to be
face cards, so there the draw is weighted by how many face cards it has; see
_mark_split. Face-down cards whose
place in the hand's sorts says what they can be aren't like that: the evidence
module averages the odds over what they are.
What the final hand holds after a discard, and under which rules, is the
discarding module's (_finals); this one counts the draws for each.

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

import gc
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from functools import cache, lru_cache
from itertools import combinations, product, repeat
from math import comb, lcm, prod
from operator import gt
from threading import Event, Lock

from ..cards import CardQuery
from ..hands import WILD, ClassKey, Pattern, Requirement, Target, card_key, disjoint_groups, forced_patterns, suit_keys
from ..rules import Rules
from ..state import SUITS, Card


class Cancelled(Exception):
    """Raised when a calculation's `cancel` event is set before it finishes."""



def probability(target: Target, kept: list[Card], pool: list[Card], draws: int,
                rules: Rules = Rules(), cancel: Event | None = None,
                marked: int = 0, marked_kept: int = 0, forced: Card | None = None,
                reuse: Reuse | None = None, windows: Sequence[Marked] = ()) -> Fraction:
    """P(kept + `draws` random cards from `pool` contains `target` under `rules`).

    With The Mark, `marked` of the pool's cards are face down (in hand or
    discarded) and known to be face cards (`Card.marked`, `flipped_by_mark`),
    and `marked_kept` of them are among the `draws`. The other draws, face-down
    cards drawn while Pareidolia was held among them, come from the rest of the
    pool.

    With Cerulean Bell, `forced` is a card in hand besides `kept` that must be
    part of the hand played (`hands.forced_patterns`).

    `reuse` shares work between calculations with the same pool (see Reuse).

    With `windows`, some face-down cards are a random set of each one's cards,
    kept (among the `draws`) or gone (not among them); see _window_split. Not
    with The Mark.

    Raises Cancelled soon after `cancel` is set.
    """
    draws = _drawable(pool, draws, marked, marked_kept)
    forced_key = None if forced is None else card_key(forced, rules)
    patterns = target.patterns(rules) if forced is None else forced_patterns(target, rules, forced_key)
    if not draws:
        # Nothing to draw (The Mark's split then weighs the one draw by 1): the
        # kept cards make the target or don't. Quick, for the many deals of
        # face-down cards that are kept (_averaged).
        holds = _target_test(target.hands, rules, forced is not None, forced_key)
        return Fraction(int(holds(card_key(c, rules) for c in kept)))
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

    with gc_paused():
        split_faces = _window_split(pool, draws, windows) if windows else _marks(
            _mark_split(pool, draws, marked, marked_kept))
        if split_faces is None:
            return Fraction(favourable(), comb(len(pool), draws))
        # One search for every number of face cards, with the weights scaled to integers.
        faces, weights = split_faces
        scale = lcm(*(w.denominator for _, w in weights))
        return Fraction(favourable(faces, [(a, int(w * scale)) for a, w in weights]), scale)



def flipped_by_mark(card: Card) -> bool:
    """Whether The Mark draws `card` face down without Pareidolia: `Card:is_face(true)`,
    which ignores debuffs. With Pareidolia it draws every card face down, and those
    aren't marked (`Card.marked`), so this is what a marked card can be."""
    return not card.is_stone and card.rank_id in (11, 12, 13)



def _drawable(pool: list[Card], draws: int, marked: int, marked_kept: int) -> int:
    """`draws` within what the pool can give: the marked cards not kept are in the
    discard pile or thrown away, so they can't be drawn. _mark_split counts the
    other draws from the pool less every marked card, and finds no way to draw
    past this (odds of 0). Real states stay within it (`_discard`); a hand-written one might not.
    `marked_kept` can exceed `marked` only for DNA's unresolved copies of a card
    (_posterior_for found none it can be), so the pool's size bounds it too."""
    return max(0, min(draws, len(pool), len(pool) - marked + marked_kept))



def _mark_split(pool: list[Card], draws: int, marked: int,
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

    The face-down cards that aren't marked (drawn while Pareidolia was held,
    every card face down) are among the d: with the marked ones a uniform
    sample of the face cards, the rest of the deal is a uniform sample of
    what's left, whichever of it is face down.

    Returns which pool cards are face cards and those (a, weight) pairs, or
    None when the draw is uniform: no marked cards. Also None if fewer pool
    cards are face cards than are marked, which only a rank changed while face
    down can cause.
    """
    if not marked:
        return None
    faces = [flipped_by_mark(c) for c in pool]
    f, n = sum(faces), len(pool)
    if f < marked:
        return None
    d = draws - marked_kept
    weights = [(marked_kept + k, Fraction(comb(f - marked, k), comb(n - marked, d) * comb(f, marked_kept + k)))
               for k in range(max(0, d - (n - f)), min(d, f - marked) + 1)]
    return faces, weights



type Split = tuple[list[int], list[tuple[tuple[int, ...], Fraction]]]



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
def gc_paused() -> Iterator[None]:
    """Pause Python's cyclic garbage collector while any search or job (worker.Job) runs.

    A big search creates millions of tuples and frozensets, and the collector
    keeps traversing them, which costs up to 30% of the time (docs/performance).
    Each of its passes also runs on the thread that triggers it, without
    checking `cancel`: a full pass over a big search's tables took 0.77 s,
    in the middle of a posterior or a best-discard search.
    The calculations make next to no garbage cycles (_favourable breaks its
    recursive closure's), so nothing piles up. The collector is process-wide and a
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
        necessary = {}
        for s, pats in by_suit.items():  # thousands of patterns a suit with Four Fingers and Shortcut
            if cancel is not None and cancel.is_set():
                raise Cancelled
            necessary[s] = Requirement(suit_keys(s), min(_cards_within(p, suit_keys(s), present) for p in pats))
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
                  None if faces is None else tuple(faces), reuse, cancel)
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
            constraint: Pattern | None, faces: tuple[int, ...] | None, reuse: Reuse | None,
            cancel: Event | None = None) -> _Layout:
    """The layout of a search for _favourable; from `reuse` if it has one for these.

    It loops over the patterns several times, thousands of them for an "any" target with Four Fingers and
    Shortcut, so each loop checks `cancel` once a pattern."""
    layout_key = None
    if reuse is not None:
        layout_key = patterns, masks, constraint, tuple(card_key(card, rules) for card in pool), faces
        lay = reuse.layouts.get(layout_key)
        if lay is not None:
            return lay

    check = _checker(cancel)
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
    groups: list[list[tuple[int, ...]] | None] = []
    for pattern in patterns:
        check()
        g = _disjoint_groups(pattern, drawable)
        groups.append(g if len(g) > 1 else None)

    # Which (pattern, requirement) slots each class key counts towards.
    members: dict[object, list[tuple[int, int]]] = defaultdict(list)
    for p, pattern in enumerate(patterns):
        check()
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
        check()
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
        check()
        start.append(next((i for i in ps if any(local[p][q] for q in slots[i][p])), ps[0] if ps else n))
    # deferred[p] = (q, d) for each requirement q of pattern p that classes
    # before it joins count towards; d identifies the set of those classes,
    # deferred_sets[d], and the search keeps a running count of cards drawn
    # from it in drawn_from[d].
    deferred: list[list[tuple[int, int]]] = [[] for _ in patterns]
    deferred_ids: dict[tuple[int, ...], int] = {}
    for p, ps in enumerate(steps):
        check()
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



def _initial_parts(lay: _Layout, kept_have: list[tuple[int, ...]], draws: int, shared: bool,
                   check: Callable[[], None]) -> tuple[dict[int, int], dict[int, tuple[int, ...]]]:
    """The patterns that can still be satisfied, each with the cards it needs at
    least, and, if `shared`, the deficits of those not yet satisfied."""
    status, part_need = lay.status, lay.part_need
    initial: dict[int, int] = {}
    deficits_of: dict[int, tuple[int, ...]] = {}
    if not shared:
        for p in range(len(lay.patterns)):
            check()
            if (v := status(p, kept_have[p], 0)) != DEAD and part_need[v] <= draws:
                initial[p] = part_need[v]
        return initial, deficits_of
    for p, (have, parts) in enumerate(zip(kept_have, lay.first)):
        check()
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
                 initial: dict[int, int], check: Callable[[], None]) -> tuple[list[list[int]], list[int], list, list]:
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
        check()
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
    check = _checker(cancel)
    initial, deficits_of = _initial_parts(lay, kept_have, draws, shared, check)
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
    activate, join_need, pending, tracked = _join_tables(lay, kept_have, draws, masks, c, initial, check)
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
        # gc_paused, so cycles pile up and are freed in one long pause later).
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



def _checker(cancel: Event | None) -> Callable[[], None]:
    def check() -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled
    return check



@lru_cache(maxsize=256)
def _target_test(hands: tuple[str, ...], rules: Rules, bell: bool,
                 forced_key: ClassKey | None) -> Callable[[Iterable[ClassKey | None]], bool]:
    """_holds_test of the patterns of `hands`, or with `bell`, those with a
    forced card of class `forced_key` (Cerulean Bell): built once, for every
    hand an estimate draws, every kept deal of the face-down cards, and every
    draw can_miss tries."""
    target = Target("", hands)
    return _holds_test(forced_patterns(target, rules, forced_key) if bell else target.patterns(rules))



def _holds_test(patterns: tuple[Pattern, ...]) -> Callable[[Iterable[ClassKey | None]], bool]:
    """Whether cards with these class keys (None for a card of none, as
    `card_key` gives) satisfy one of `patterns`. Building it takes a while,
    so build it once for many hands (_target_test). The requirements met
    are a bit mask: those needing one card are met by any key of theirs, so
    each key has a mask of them; the others are counted. A pattern is only
    looked at once the first requirement it needs a card for is met."""
    index: dict[Requirement, int] = {}
    for pattern in patterns:
        for req in pattern:
            index.setdefault(req, len(index))
    need = [req.need for req in index]
    one_of: dict[ClassKey, int] = defaultdict(int)  # the requirements needing one card that a key meets
    many_of: dict[ClassKey, list[int]] = defaultdict(list)  # those needing more that it counts towards
    for req, i in index.items():
        for key in req.keys:
            if req.need == 1:
                one_of[key] |= 1 << i
            elif req.need > 1:
                many_of[key].append(i)
    met_anyway = sum(1 << i for i, n in enumerate(need) if n <= 0)
    by_first: dict[int, list[int]] = defaultdict(list)  # patterns' masks by the first requirement's bit
    for pattern in patterns:
        ids = [index[req] for req in pattern]
        first = next((i for i in ids if need[i] > 0), None)
        if first is None:
            return lambda keys: True  # a pattern that needs no cards
        by_first[1 << first].append(sum(1 << i for i in set(ids)))
    firsts = sum(by_first)
    one_of, many_of = dict(one_of), dict(many_of)

    def holds(keys: Iterable[ClassKey | None]) -> bool:
        met = met_anyway
        counts: dict[int, int] = {}
        for key in keys:
            met |= one_of.get(key, 0)
            for i in many_of.get(key, ()):
                counts[i] = counts.get(i, 0) + 1
        for i, n in counts.items():
            if n >= need[i]:
                met |= 1 << i
        todo = met & firsts
        while todo:
            bit = todo & -todo
            if any(m & met == m for m in by_first[bit]):
                return True
            todo ^= bit
        return False
    return holds



def count_probability(query: CardQuery, pool: list[Card], draws: int, rules: Rules = Rules(),
                      marked: int = 0, marked_kept: int = 0, have: int = 0,
                      windows: Sequence[Marked] = ()) -> Fraction:
    """P(`have` plus the number of matching cards among `draws` random cards from
    `pool` satisfies `query`).

    With The Mark, `marked` of the pool's cards are face down (in hand or
    discarded) and known to be face cards, and `marked_kept` of them are among
    the `draws`. The other draws come from the rest of the pool; see _mark_split.
    Windows of face-down cards skew the draw instead (_window_split).
    """
    draws = _drawable(pool, draws, marked, marked_kept)
    split = _window_split(pool, draws, windows) if windows else _marks(
        _mark_split(pool, draws, marked, marked_kept))
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


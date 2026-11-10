"""What the hand's sorts say about its face-down cards: which unseen cards they are.

See docs/plans/face-down-windows.md. The game sorts the hand by each card's true
rank and suit, face-down cards included, and the mod exports the sorts it saw
this round (`GameState.observations`), any number of each mode: a draw re-sorts
the hand, but an earlier sort is still true of the cards in it. A face-down card
in one of them, still in hand or since discarded face down, is a *latent* card
with evidence. Its identity is one of the unseen cards, and the order says
which: between a 9 and a 7 in a rank sort it's a 7, 8 or 9, and of two side by
side the left one is the higher.

The prior is a uniformly random injective assignment of the unseen cards to the
latent cards (with The Mark, of the unseen face cards: `allowed`). The
likelihood of the observations given an assignment is:

  * 0 unless every observation is in the game's order (printed keys
    non-increasing, Stones lowest);
  * for each tie group (observed cards with the same printed rank and suit, or
    all Stones, which the player can't order), the share of its orders that
    every observation agrees with. Unknown tie-breakers (original suit,
    creation order, a Stone's underlying card) are taken as uniformly random,
    and as the same in every sort, as the game's are.

A `Posterior` lists the assignments with a positive weight (prior times
likelihood) as `Branch`es. `summed` sums the assignments that its caller can't
tell apart into one branch, as it goes, so that a run of eight face-down cards
(10^8 deals) costs a fraction of a second; without a label, each assignment is
its own branch. Nothing is skipped or sampled: the engine averages its odds
over all of them (engine._averaged). `windows` finds latent cards that are a
uniformly random set of some cards, which the engine weighs the draw by
instead of going through them.

`reference` merges nothing, enumerating the unseen cards one by one, and is
what the tests check `summed` against.

This module only does the counting; it doesn't import the engine, so the
engine can import it. Cancelling is the caller's `check`, which raises.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Hashable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from itertools import combinations_with_replacement, pairwise, permutations
from math import comb, factorial, prod

from .state import Card, Observation, ObservedCard

# Card:get_nominal's suit_nominal, lowest first.
SUIT_ORDER = {"Diamonds": 0, "Clubs": 1, "Hearts": 2, "Spades": 3}

# What the sort sees of a card: (rank_id, suit), or None for a Stone, whose
# key is below every other card's in both sorts.
Printed = tuple[int, str] | None


def printed(card: Card | ObservedCard) -> Printed:
    """The printed rank and suit the sort orders a card by; None for a Stone."""
    if card.stone if isinstance(card, ObservedCard) else card.is_stone:
        return None
    return card.rank_id, card.suit


def sortable(card: Card) -> bool:
    """Whether the sort's place for `card` is known: a Stone, or a printed rank and suit."""
    return card.is_stone or (card.rank_id is not None and 2 <= card.rank_id <= 14 and card.suit in SUIT_ORDER)


def sort_value(key: Printed, mode: str) -> tuple[int, int]:
    """Where `key` sorts in `mode` ("desc" or "suit desc"): higher is further left."""
    if key is None:
        return (-1, -1)
    rank, suit = key
    return (rank, SUIT_ORDER[suit]) if mode == "desc" else (SUIT_ORDER[suit], rank)


@dataclass(frozen=True)
class Branch:
    """One assignment of the latent cards: each in hand by its hand index, with
    the unseen card it is; the unseen cards those discarded face down are, by id;
    and its weight (prior times likelihood, unnormalized)."""
    cards: tuple[tuple[int, Card], ...]
    weight: Fraction
    discarded: tuple[Card, ...] = ()


@dataclass(frozen=True)
class Posterior:
    branches: tuple[Branch, ...]
    total: Fraction  # the weights' sum; 0 if no assignment fits the observations


@cache
def orders(chains: tuple[tuple[int, ...], ...]) -> int:
    """How many orders of the items 0..n-1 keep the order of each chain.

    The items are those in the chains, numbered by first appearance. Counted
    over subsets: an item can come next once its predecessor in each chain has.
    """
    n = 1 + max((x for chain in chains for x in chain), default=-1)
    before = [0] * n
    for chain in chains:
        for a, b in pairwise(chain):
            before[b] |= 1 << a
    ways = [0] * (1 << n)
    ways[0] = 1
    for mask in range(1 << n):
        if not ways[mask]:
            continue
        for x in range(n):
            if not mask >> x & 1 and before[x] & mask == before[x]:
                ways[mask | 1 << x] += ways[mask]
    return ways[-1]


def tie_factor(members: Sequence[int], observations: Sequence[Observation]) -> Fraction:
    """The share of a tie group's orders that every observation agrees with:
    each of its t! orders is equally likely, and one order holds in every sort."""
    if len(members) < 2:
        return Fraction(1)
    inside = set(members)
    number: dict[int, int] = {}
    chains = []
    for observation in observations:
        chain = []
        for card_id in observation.cards:
            if card_id in inside:
                chain.append(number.setdefault(card_id, len(number)))
        chains.append(tuple(chain))
    return Fraction(orders(tuple(chains)), factorial(len(members)))


def _face_up_bound(entries: Sequence[int], face_up: dict[int, Printed], mode: str) -> tuple[int, int] | None:
    """Where the first face-up card of `entries` sorts in `mode`, if there's one."""
    return next((sort_value(face_up[i], mode) for i in entries if i in face_up), None)


def latent_ids(observed: Sequence[ObservedCard]) -> list[int]:
    """The ids of the latent cards (face down in hand, or discarded face down), in
    order of first appearance."""
    return [card_id for card_id, card in enumerate(observed) if card.latent]


def _branch(hand_of: Sequence[int | None], picked: Sequence[Card], weight: Fraction) -> Branch:
    """A Branch of the latent cards' `picked` identities, at `hand_of` (None if discarded)."""
    return Branch(tuple((i, c) for i, c in zip(hand_of, picked) if i is not None), weight,
                  tuple(c for i, c in zip(hand_of, picked) if i is None))


@dataclass
class _Setup:
    """What `summed` and `windows` work from: the latent cards to assign (slots:
    those in the observations, by id, then `extra`), the cards they can be, and
    what the observations say about each."""
    observations: list[Observation]  # without the latent cards left out
    ids: list[int]  # the slots' card ids, for those in the observations
    hand_of: list[int | None]  # each slot's hand index; None if discarded face down
    cards: list[Card]  # the unseen cards they can be, each once
    counts: list[int]  # how many of each are unseen
    keys: list[Printed]
    value_at: list[list[tuple[int, int]]]  # value_at[o][v]: where card v sorts in observation o
    face_up_groups: dict[Printed, list[int]]  # face-up cards' ids, by printed key
    face_up_factor: dict[Printed, Fraction]  # tie_factor of each of those groups
    position: list[list[int | None]]  # position[o][k]: slot k's place in observation o
    candidates: list[list[int]]  # each slot's cards, within the face-up cards around it


def _setup(observations: Sequence[Observation], observed: Sequence[ObservedCard], unseen: Sequence[Card],
           allowed: Callable[[Card], bool], extra: Sequence[int], skip: Collection[int]) -> _Setup | None:
    """The _Setup for these, leaving out the latent cards with ids in `skip`; None
    if the face-up cards alone already contradict the observations."""
    if skip:
        observations = [Observation(o.mode, tuple(i for i in o.cards if i not in skip)) for o in observations]
    observations = list(observations)
    ids = [i for i in latent_ids(observed) if i not in skip]
    hand_of: list[int | None] = [observed[card_id].hand for card_id in ids] + list(extra)
    n = len(hand_of)
    slot_of = {card_id: k for k, card_id in enumerate(ids)}

    values: dict[Card, int] = {}
    for card in unseen:
        if allowed(card) and sortable(card):
            values[card] = values.get(card, 0) + 1
    cards = list(values)
    counts = list(values.values())
    keys = [printed(c) for c in cards]
    modes = [observation.mode for observation in observations]
    value_at = [[sort_value(key, mode) for key in keys] for mode in modes]

    # Face-up cards in the same order in every observation they're in, ties apart.
    face_up = {card_id: printed(card) for card_id, card in enumerate(observed) if not card.latent}
    for o, observation in enumerate(observations):
        shown = [sort_value(face_up[i], modes[o]) for i in observation.cards if i in face_up]
        if any(a < b for a, b in pairwise(shown)):
            return None
    face_up_groups: dict[Printed, list[int]] = {}
    for card_id, key in face_up.items():
        face_up_groups.setdefault(key, []).append(card_id)
    face_up_factor = {key: tie_factor(members, observations) for key, members in face_up_groups.items()}
    if not all(face_up_factor.values()):
        return None

    position: list[list[int | None]] = [[None] * n for _ in observations]
    for o, observation in enumerate(observations):
        for p, card_id in enumerate(observation.cards):
            if card_id in slot_of:
                position[o][slot_of[card_id]] = p
    candidates: list[list[int]] = []
    for k in range(n):
        here = list(range(len(cards)))
        for o, observation in enumerate(observations):
            p = position[o][k]
            if p is None:
                continue
            hi = _face_up_bound(observation.cards[:p][::-1], face_up, modes[o])
            lo = _face_up_bound(observation.cards[p + 1:], face_up, modes[o])
            here = [v for v in here if (hi is None or value_at[o][v] <= hi) and (lo is None or value_at[o][v] >= lo)]
        candidates.append(here)
    return _Setup(observations, ids, hand_of, cards, counts, keys, value_at, face_up_groups, face_up_factor,
                  position, candidates)


Label = Callable[[int | None, Card], Hashable]


def summed(observations: Sequence[Observation], observed: Sequence[ObservedCard], unseen: Sequence[Card],
           allowed: Callable[[Card], bool], extra: Sequence[int] = (), label: Label | None = None,
           check: Callable[[], None] = lambda: None, skip: Collection[int] = ()) -> Posterior:
    """Every assignment of the latent cards in `observations` to cards of `unseen`
    that `allowed` lets them be, with its weight, the assignments that `label`
    can't tell apart summed into one branch.

    `label(hand, card)` is what the caller sees of a latent card at hand index
    `hand` (None if discarded face down) being `card`; None for nothing. A
    Branch stands for every assignment with the same multiset of labels: its
    weight is theirs summed, and its cards are one of them. Without `label`,
    every assignment is its own branch (copies are one card, with their number
    as a factor of the weight).

    `extra` are hand indices of more face-down cards to enumerate, with no
    evidence: DNA's copies of a face-down card (engine._posterior_for). Latent
    cards discarded face down are enumerated like those in hand: they're out of
    play, but the order says what they are, and so what's left to draw. The
    latent cards with ids in `skip` are left out, as if they weren't in the
    observations: right for those that don't depend on the others (`windows`).
    `check` is called now and then, and raises to cancel.

    The latent cards are assigned one by one, in order of first appearance,
    and the partial assignments are merged as they go, by all that the rest
    depends on: the cards that bound the latent cards still to come (the
    nearest assigned one on each side in each observation), how many of each
    card they've used that a card still to come could be, which latent cards
    share a printed key that a card still to come could have (their tie group),
    and the labels so far. A long run of face-down cards in one sort then
    costs about its length times the number of cards and labels, not the
    number of its deals. That's exact: the merged assignments have the same
    completions, with the same weights.
    """
    s = _setup(observations, observed, unseen, allowed, extra, skip)
    if s is None:
        return Posterior((), Fraction(0))
    observations, ids, hand_of, cards, counts, keys = s.observations, s.ids, s.hand_of, s.cards, s.counts, s.keys
    value_at, candidates, position = s.value_at, s.candidates, s.position
    face_up_groups, face_up_factor = s.face_up_groups, s.face_up_factor
    n = len(hand_of)
    candidate_sets = [set(c) for c in candidates]

    # bounds[k][k2], for slots k2 >= k: the nearest slots before k on each side
    # of k2 in each observation, as (o, j, whether j is on the left). Once
    # slots 0..k-1 are assigned, slot k2 sorts at or below the one on its left
    # and at or above the one on its right, and the others say no more.
    bounds: list[list[list[tuple[int, int, bool]]]] = []
    for k in range(n + 1):
        row = []
        for k2 in range(n):
            found = []
            if k2 >= k:
                for o in range(len(observations)):
                    p = position[o][k2]
                    if p is None:
                        continue
                    left = [(q, j) for j in range(k) if (q := position[o][j]) is not None and q < p]
                    right = [(q, j) for j in range(k) if (q := position[o][j]) is not None and q > p]
                    if left:
                        found.append((o, max(left)[1], True))
                    if right:
                        found.append((o, min(right)[1], False))
            row.append(found)
        bounds.append(row)
    # frontier[k]: the assigned slots whose cards the slots from k on depend on.
    frontier = [sorted({j for k2 in range(k, n) for _, j, _ in bounds[k][k2]}) for k in range(n + 1)]
    slots_of_value = [[k for k in range(n) if v in candidate_sets[k]] for v in range(len(cards))]

    # Printed keys whose tie group can have two or more cards, one of them latent.
    pool_of_key: dict[Printed, int] = {}
    for v, key in enumerate(keys):
        pool_of_key[key] = pool_of_key.get(key, 0) + counts[v]
    tied = {key for key, c in pool_of_key.items() if len(face_up_groups.get(key, ())) + c >= 2}
    values_of_key: dict[Printed, list[int]] = {}
    for v, key in enumerate(keys):
        values_of_key.setdefault(key, []).append(v)

    def factor(key: Printed, latent: tuple[int, ...]) -> Fraction:
        """The tie factor of key's group with the latent cards in slots `latent`,
        relative to the face-up cards' alone."""
        members = face_up_groups.get(key, [])
        if len(members) + len(latent) < 2:
            return Fraction(1)
        return tie_factor(members + [ids[k] for k in latent], observations) / face_up_factor.get(key, 1)

    labels: list[dict[int, int]] = [{} for _ in range(n)]
    label_ids: dict[Hashable, int] = {}

    def label_of(k: int, v: int) -> int:
        """The label of slot k being card v, as a small int (-1 for none)."""
        found = labels[k].get(v)
        if found is None:
            seen = (k, v) if label is None else label(hand_of[k], cards[v])
            found = labels[k][v] = -1 if seen is None else label_ids.setdefault(seen, len(label_ids))
        return found

    def fits(v: int, k2: int, k: int, assigned: Sequence[int]) -> bool:
        """Whether slot k2 can be card v as far as slots before k say."""
        for o, j, on_left in bounds[k][k2]:
            here, there = value_at[o][v], value_at[o][assigned[j]]
            if here > there if on_left else here < there:
                return False
        return True

    def live(v: int, k: int, assigned: Sequence[int]) -> bool:
        """Whether some slot from k on can be card v, as far as slots before k say."""
        return any(fits(v, k2, k, assigned) for k2 in slots_of_value[v] if k2 >= k)

    # A layer's states: (frontier cards, live used counts, live tie groups, labels) -> [weight, assignment]
    layer: dict[tuple, list] = {((), (), (), ()): [1, ()]}
    steps = 0
    for k in range(n):
        check()
        following: dict[tuple, list] = {}
        observed_slot = k < len(ids)
        for (_, used_items, ties_items, signature), (weight, assigned) in layer.items():
            used = dict(used_items)
            ties = dict(ties_items)
            for v in candidates[k]:
                steps += 1
                if steps % 64 == 0:
                    check()
                left = counts[v] - used.get(v, 0)
                if left <= 0 or not fits(v, k, k, assigned):
                    continue
                now = (*assigned, v)
                w = weight * left
                new_used = dict(used)
                new_used[v] = new_used.get(v, 0) + 1
                new_ties = dict(ties)
                key = keys[v]
                if observed_slot and key in tied:
                    new_ties[key] = (*new_ties.get(key, ()), k)
                kept_used = tuple(sorted((u, c) for u, c in new_used.items() if live(u, k + 1, now)))
                kept_ties = []
                for tie_key, latent in sorted(new_ties.items(), key=lambda item: _sortable_key(item[0])):
                    if any(live(u, k + 1, now) for u in values_of_key[tie_key]):
                        kept_ties.append((tie_key, latent))
                    else:
                        w *= factor(tie_key, latent)
                if not w:
                    continue
                lab = label_of(k, v)
                sig = signature if lab < 0 else tuple(sorted((*signature, lab)))
                state = (tuple(now[j] for j in frontier[k + 1]), kept_used, tuple(kept_ties), sig)
                entry = following.get(state)
                if entry is None:
                    following[state] = [w, now]
                else:
                    entry[0] += w
        layer = following

    found: dict[tuple, list] = {}
    for (_, _, ties_items, signature), (weight, assigned) in layer.items():
        for tie_key, latent in ties_items:
            weight *= factor(tie_key, latent)
        if not weight:
            continue
        entry = found.get(signature)
        if entry is None:
            found[signature] = [weight, assigned]
        else:
            entry[0] += weight
    out = tuple(_branch(hand_of, [cards[v] for v in assigned], Fraction(weight)) for weight, assigned in found.values())
    return Posterior(out, sum((b.weight for b in out), Fraction(0)))


def _sortable_key(key: Printed) -> tuple:
    return (0, 0, "") if key is None else (1, *key)


@dataclass(frozen=True)
class Window:
    """Latent cards that are a uniformly random set of `size` of the unseen
    cards `cards` (each copy counting), whatever the other latent cards are:
    the card ids `ids`.

    If they're side by side in an observation, and no two of `cards` are alike
    in print, `order` has their ids as they sit there, left to right, and
    `mode` is its mode: a set then sorts into them one way, the highest card on
    the left. Otherwise `order` is empty."""
    ids: frozenset[int]
    cards: frozenset[Card]
    size: int
    order: tuple[int, ...] = ()
    mode: str = ""


WINDOW_SETS = 5000  # most sets of cards a window is checked over


def windows(observations: Sequence[Observation], observed: Sequence[ObservedCard], unseen: Sequence[Card],
            allowed: Callable[[Card], bool], check: Callable[[], None] = lambda: None) -> list[Window]:
    """The groups of latent cards that are a Window.

    Latent cards depend on each other when they're side by side in an
    observation, or can be cards of the same printed rank and suit (they can't
    both be one card, and they'd tie). The rest of the deal doesn't change what
    a group of them that depends on no other is, since the weight of a deal is
    a product over such groups: so the group's own sum (`summed` with the
    others left out) is its posterior. It's a Window if that gives every set of
    `size` of its candidates the same weight. The engine then weighs the draw
    by how many of those cards it has (engine._window_split) instead of going
    through them.

    A run of face-down cards side by side in one sort, and in no other, between
    face-up cards none of them can tie with, is a Window: each set of the cards
    between sorts into it one way (cards alike in print in each of their
    orders, whose tie factors add up to 1). Other groups are checked by their
    sum, if they have at most WINDOW_SETS sets: the rest are left to `summed`,
    which is as exact, if slower.
    """
    s = _setup(observations, observed, unseen, allowed, (), ())
    if s is None:
        return []
    n = len(s.ids)
    parent = list(range(n))

    def find(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for o in range(len(s.observations)):
        on = sorted((p, k) for k in range(n) if (p := s.position[o][k]) is not None)
        for (p, a), (q, b) in pairwise(on):
            if q == p + 1:
                parent[find(a)] = find(b)
    by_key: dict[Printed, int] = {}
    for k in range(n):
        for v in s.candidates[k]:
            first = by_key.setdefault(s.keys[v], k)
            parent[find(k)] = find(first)
    groups: dict[int, list[int]] = {}
    for k in range(n):
        groups.setdefault(find(k), []).append(k)

    found = []
    for slots in groups.values():
        check()
        values = sorted({v for k in slots for v in s.candidates[k]})
        size, total = len(slots), sum(s.counts[v] for v in values)
        if total < size or any(s.keys[v] in s.face_up_groups for v in values):  # tying with a face-up card
            continue
        ids = frozenset(s.ids[k] for k in slots)
        seen_in = [o for o in range(len(s.observations)) if any(s.position[o][k] is not None for k in slots)]
        # An observation the group is side by side in, all of it.
        run = next((o for o in seen_in if _side_by_side([s.position[o][k] for k in slots])), None)
        if not (len(seen_in) == 1 and run is not None
                and all(len(s.candidates[k]) == len(values) for k in slots)) and not _uniform(
                    s, slots, values, ids, observations, observed, unseen, allowed, check):
            continue
        order, mode = (), ""
        if run is not None and len({s.keys[v] for v in values}) == len(values) == total:
            order = tuple(s.ids[k] for k in sorted(slots, key=lambda k: s.position[run][k]))
            mode = s.observations[run].mode
        found.append(Window(ids, frozenset(s.cards[v] for v in values), size, order, mode))
    return found


def _side_by_side(places: Sequence[int | None]) -> bool:
    return None not in places and max(places) - min(places) == len(places) - 1


def _uniform(s: _Setup, slots: Sequence[int], values: Sequence[int], ids: frozenset[int],
             observations: Sequence[Observation], observed: Sequence[ObservedCard], unseen: Sequence[Card],
             allowed: Callable[[Card], bool], check: Callable[[], None]) -> bool:
    """Whether the group of latent cards in `slots` gives every set of its
    candidates `values` the same weight, by its sum (if it has at most
    WINDOW_SETS sets)."""
    size, total = len(slots), sum(s.counts[v] for v in values)
    if comb(total, size) > WINDOW_SETS:
        return False
    post = summed(observations, observed, unseen, allowed, (), _card_of, check,
                  skip=[i for i in s.ids if i not in ids])
    # Each multiset of the candidates, with the number of sets of copies it is.
    sets: dict[tuple[int, ...], int] = {}
    for picked in combinations_with_replacement(values, size):
        ways = prod(comb(s.counts[v], picked.count(v)) for v in set(picked))
        if ways:
            sets[picked] = ways
    if len(post.branches) != len(sets):
        return False
    index = {card: v for v, card in enumerate(s.cards)}
    each = post.total / comb(total, size)
    return all(b.weight == each * sets.get(tuple(sorted(index[c] for c in [*(c for _, c in b.cards), *b.discarded])), 0)
               for b in post.branches)


def _card_of(hand: int | None, card: Card) -> Card:
    return card


def reference(observations: Sequence[Observation], observed: Sequence[ObservedCard], unseen: Sequence[Card],
              allowed: Callable[[Card], bool], extra: Sequence[int] = ()) -> Posterior:
    """`summed` without merging anything or pruning early, for the tests: every
    injective assignment of the unseen cards, one by one, weighed straight from
    the definition. Only for a few cards."""
    ids = latent_ids(observed)
    hand_of: list[int | None] = [observed[card_id].hand for card_id in ids] + list(extra)
    places = [j for j, card in enumerate(unseen) if allowed(card) and sortable(card)]
    found = []
    for picked in permutations(places, len(hand_of)):
        key_of = {card_id: printed(card) for card_id, card in enumerate(observed) if not card.latent}
        key_of.update((card_id, printed(unseen[j])) for card_id, j in zip(ids, picked))
        if any(sort_value(key_of[a], o.mode) < sort_value(key_of[b], o.mode)
               for o in observations for a, b in pairwise(o.cards)):
            continue
        groups: dict[Printed, list[int]] = {}
        for card_id, key in key_of.items():
            groups.setdefault(key, []).append(card_id)
        weight = Fraction(1)
        for members in groups.values():
            weight *= tie_factor(members, observations)
        if weight:
            found.append(_branch(hand_of, [unseen[j] for j in picked], weight))
    return Posterior(tuple(found), sum((b.weight for b in found), Fraction(0)))

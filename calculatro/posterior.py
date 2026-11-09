"""What the hand's sorts say about its face-down cards: which unseen cards they are.

See docs/plans/face-down-windows.md. The game sorts the hand by each card's true
rank and suit, face-down cards included, and the mod exports the latest sort of
each mode it saw (`GameState.observations`). A face-down card in one of them,
still in hand or since discarded face down, is a *latent* card with evidence. Its
identity is one of the unseen cards, and the order says which: between a 9 and a
7 in a rank sort it's a 7, 8 or 9, and of two side by side the left one is the
higher.

The prior is a uniformly random injective assignment of the unseen cards to the
latent cards (with The Mark, of the unseen face cards: `allowed`). The
likelihood of the observations given an assignment is:

  * 0 unless every observation is in the game's order (printed keys
    non-increasing, Stones lowest);
  * for each tie group (observed cards with the same printed rank and suit, or
    all Stones, which the player can't order), the share of its orders that
    every observation agrees with. Unknown tie-breakers (original suit,
    creation order, a Stone's underlying card) are taken as uniformly random,
    and as the same in both sorts, as the game's are.

A `Posterior` lists every assignment with a positive weight (prior times
likelihood) as a `Branch`. Nothing is skipped or sampled: the engine averages
its odds over all of them (engine.evaluate_discard).

`branches` merges assignments to equal cards (copies), which is exact: their
weights add up. `reference` doesn't, enumerating the unseen cards one by one,
and is what the tests check `branches` against.

This module only does the counting; it doesn't import the engine, so the
engine can import it. Cancelling is the caller's `check`, which raises.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from itertools import pairwise, permutations
from math import factorial

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
    each of its t! orders is equally likely, and one order holds in both sorts."""
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


def _earlier_slot(entries: Sequence[int], face_up: dict[int, Printed], slot_of: dict[int, int], k: int) -> int | None:
    """The first slot of `entries` enumerated before slot `k`, if no face-up card comes first."""
    for i in entries:
        if i in face_up:
            return None
        if slot_of[i] < k:
            return slot_of[i]
    return None


def latent_ids(observed: Sequence[ObservedCard]) -> list[int]:
    """The ids of the latent cards (face down in hand, or discarded face down), in
    order of first appearance."""
    return [card_id for card_id, card in enumerate(observed) if card.latent]


def _branch(hand_of: Sequence[int | None], picked: Sequence[Card], weight: Fraction) -> Branch:
    """A Branch of the latent cards' `picked` identities, at `hand_of` (None if discarded)."""
    return Branch(tuple((i, c) for i, c in zip(hand_of, picked) if i is not None), weight,
                  tuple(c for i, c in zip(hand_of, picked) if i is None))


def branches(observations: Sequence[Observation], observed: Sequence[ObservedCard], unseen: Sequence[Card],
             allowed: Callable[[Card], bool], extra: Sequence[int] = (),
             check: Callable[[], None] = lambda: None) -> Posterior:
    """Every assignment of the latent cards in `observations` to cards of `unseen`
    that `allowed` lets them be, with its weight. Copies are one card with their
    number as a factor of the weight.

    `extra` are hand indices of more face-down cards to enumerate, with no
    evidence: DNA's copies of a face-down card (engine._posterior_for). Latent
    cards discarded face down are enumerated like those in hand: they're out of
    play, but the order says what they are, and so what's left to draw.
    `check` is called now and then, and raises to cancel.
    """
    ids = latent_ids(observed)
    hand_of: list[int | None] = [observed[card_id].hand for card_id in ids] + list(extra)
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
            return Posterior((), Fraction(0))
    face_up_groups: dict[Printed, list[int]] = {}
    for card_id, key in face_up.items():
        face_up_groups.setdefault(key, []).append(card_id)
    face_up_factor = {key: tie_factor(members, observations) for key, members in face_up_groups.items()}
    if not all(face_up_factor.values()):
        return Posterior((), Fraction(0))

    # Each slot's candidates, within the face-up cards around it in each
    # observation, and the slots enumerated before it that are its nearest
    # neighbours there with no face-up card between: it must sort at or below
    # those on its left (`below`) and at or above those on its right (`above`).
    # So every adjacent pair is checked once both are known.
    below: list[list[tuple[int, int]]] = [[] for _ in hand_of]
    above: list[list[tuple[int, int]]] = [[] for _ in hand_of]
    candidates: list[list[int]] = []
    for k in range(len(hand_of)):
        here = list(range(len(cards)))
        for o, observation in enumerate(observations):
            if k >= len(ids) or ids[k] not in observation.cards:
                continue
            p = observation.cards.index(ids[k])
            left, right = observation.cards[:p][::-1], observation.cards[p + 1:]
            hi, lo = _face_up_bound(left, face_up, modes[o]), _face_up_bound(right, face_up, modes[o])
            here = [v for v in here if (hi is None or value_at[o][v] <= hi) and (lo is None or value_at[o][v] >= lo)]
            if (j := _earlier_slot(left, face_up, slot_of, k)) is not None:
                below[k].append((o, j))
            if (j := _earlier_slot(right, face_up, slot_of, k)) is not None:
                above[k].append((o, j))
        candidates.append(here)

    found: list[Branch] = []
    assigned = [0] * len(hand_of)
    used = [0] * len(cards)
    steps = 0

    def leaf(prior: int) -> None:
        touched: dict[Printed, list[int]] = {}
        for k in range(len(ids)):
            touched.setdefault(keys[assigned[k]], []).append(ids[k])
        weight = Fraction(prior)
        for key, latent in touched.items():
            members = face_up_groups.get(key, []) + latent
            if len(members) > 1:
                weight *= tie_factor(members, observations) / face_up_factor.get(key, 1)
        if weight:
            found.append(_branch(hand_of, [cards[v] for v in assigned], weight))

    def place(k: int, prior: int) -> None:
        nonlocal steps
        if k == len(hand_of):
            leaf(prior)
            return
        steps += 1
        if steps % 512 == 0:
            check()
        for v in candidates[k]:
            left = counts[v] - used[v]
            if (not left or any(value_at[o][v] > value_at[o][assigned[j]] for o, j in below[k])
                    or any(value_at[o][v] < value_at[o][assigned[j]] for o, j in above[k])):
                continue
            assigned[k] = v
            used[v] += 1
            place(k + 1, prior * left)
            used[v] -= 1

    check()
    place(0, 1)
    return Posterior(tuple(found), sum((b.weight for b in found), Fraction(0)))


def reference(observations: Sequence[Observation], observed: Sequence[ObservedCard], unseen: Sequence[Card],
              allowed: Callable[[Card], bool], extra: Sequence[int] = ()) -> Posterior:
    """`branches` without merging anything or pruning early, for the tests: every
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

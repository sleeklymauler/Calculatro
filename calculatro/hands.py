"""Poker-hand targets expressed as card patterns, following Balatro's rules.

A target is satisfied if the final hand *contains* it, i.e. some selection of
up to 5 cards would be evaluated by Balatro as containing that hand type.
This mirrors Balatro's own containment: a Full House contains Two Pair, a
Straight Flush contains both Straight and Flush, but Four of a Kind does not
contain Two Pair.

A Target names the hand types; its patterns depend on the joker Rules in
play (Four Fingers, Shortcut, Smeared) and are built per Rules on demand.
Each target is a list of patterns (OR). A pattern is a tuple of disjoint
requirements (AND): "at least `need` cards whose class is in `keys`".
Card classes are (rank_id, suit) where suit is one of SUITS or "Wild".
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from itertools import combinations, permutations

from .rules import Rules
from .state import SUITS, Card

WILD = "Wild"
RANKS = range(2, 15)  # 2..14, Ace = 14

ClassKey = tuple[int, str]


@dataclass(frozen=True)
class Requirement:
    keys: frozenset[ClassKey]
    need: int


Pattern = tuple[Requirement, ...]


@dataclass(frozen=True)
class Target:
    """One or more hand types; satisfied if the final hand contains any of them."""
    name: str
    hands: tuple[str, ...]

    def patterns(self, rules: Rules = Rules()) -> tuple[Pattern, ...]:
        return _patterns(self.hands, rules)


def card_key(card: Card, rules: Rules = Rules()) -> ClassKey | None:
    """Class of a known card for hand evaluation; None if it can't contribute."""
    if card.hidden or card.is_stone or card.rank_id is None or card.suit is None:
        return None
    # A debuffed Wild Card loses its wildness for flushes (Card:is_suit, flush_calc).
    suit = WILD if card.is_wild and not card.debuff else card.suit
    return (card.rank_id, suit)


def _rank(r: int) -> frozenset[ClassKey]:
    return frozenset((r, s) for s in (*SUITS, WILD))


def _suited_rank(r: int, s: str) -> frozenset[ClassKey]:
    return frozenset({(r, s), (r, WILD)})


def _suit(s: str) -> frozenset[ClassKey]:
    return frozenset(k for r in RANKS for k in _suited_rank(r, s))


# Ace-low A-2-3-4-5 through 10-J-Q-K-A; Balatro straights don't wrap around.
STRAIGHT_WINDOWS = [(14, 2, 3, 4, 5)] + [tuple(range(lo, lo + 5)) for lo in range(2, 11)]


def _n_of_a_kind(n: int) -> tuple[Pattern, ...]:
    return tuple((Requirement(_rank(r), n),) for r in RANKS)


@cache
def _hand_patterns(rules: Rules) -> dict[str, tuple[Pattern, ...]]:
    return {
        "Pair": _n_of_a_kind(2),
        "Two Pair": tuple(
            (Requirement(_rank(a), 2), Requirement(_rank(b), 2)) for a, b in combinations(RANKS, 2)
        ),
        "Three of a Kind": _n_of_a_kind(3),
        "Straight": tuple(
            tuple(Requirement(_rank(r), 1) for r in w) for w in STRAIGHT_WINDOWS
        ),
        "Flush": tuple((Requirement(_suit(s), 5),) for s in SUITS),
        "Full House": tuple(
            (Requirement(_rank(a), 3), Requirement(_rank(b), 2)) for a, b in permutations(RANKS, 2)
        ),
        "Four of a Kind": _n_of_a_kind(4),
        "Straight Flush": tuple(
            tuple(Requirement(_suited_rank(r, s), 1) for r in w)
            for s in SUITS for w in STRAIGHT_WINDOWS
        ),
        # Balatro scores a Royal Flush as a Straight Flush; it's just the 10-A window.
        "Royal Flush": tuple(
            tuple(Requirement(_suited_rank(r, s), 1) for r in range(10, 15)) for s in SUITS
        ),
        "Five of a Kind": _n_of_a_kind(5),
        "Flush House": tuple(
            (Requirement(_suited_rank(a, s), 3), Requirement(_suited_rank(b, s), 2))
            for s in SUITS for a, b in permutations(RANKS, 2)
        ),
        "Flush Five": tuple(
            (Requirement(_suited_rank(r, s), 5),) for s in SUITS for r in RANKS
        ),
    }


TARGETS: dict[str, Target] = {name: Target(name, (name,)) for name in _hand_patterns(Rules())}

_ALIASES = {"trips": "Three of a Kind", "quads": "Four of a Kind", "royal": "Royal Flush"}


def get_target(name: str) -> Target:
    """Look up a target by name, slug ("full-house") or alias ("trips")."""
    norm = name.strip().lower().replace("-", " ").replace("_", " ")
    if norm in _ALIASES:
        return TARGETS[_ALIASES[norm]]
    for full, target in TARGETS.items():
        if full.lower() == norm:
            return target
    raise KeyError(f"unknown target {name!r}; choose from: {', '.join(TARGETS)} (or trips, quads, royal)")


def _implies(a: Pattern, b: Pattern) -> bool:
    """Whether satisfying pattern `a` always satisfies pattern `b`.

    True if each requirement of `b` can be matched to a distinct requirement of
    `a` that draws from a subset of its classes and needs at least as many.
    Greedy matching can miss some implications, which only costs speed.
    """
    used: set[int] = set()
    for rb in b:
        for i, ra in enumerate(a):
            if i not in used and ra.need >= rb.need and ra.keys <= rb.keys:
                used.add(i)
                break
        else:
            return False
    return True


def _minimal(patterns: tuple[Pattern, ...]) -> tuple[Pattern, ...]:
    """Drop patterns that imply another one; they never change an OR's outcome."""
    unique = list(dict.fromkeys(patterns))
    need = [sum(r.need for r in p) for p in unique]
    return tuple(
        a for i, a in enumerate(unique)
        if not any(j != i and need[j] <= need[i] and _implies(a, b) for j, b in enumerate(unique))
    )


def combine(targets: list[Target]) -> Target:
    """A target satisfied when any of `targets` is (e.g. Three of a Kind OR Full House)."""
    if len(targets) == 1:
        return targets[0]
    targets = list(dict.fromkeys(targets))
    hands = tuple(dict.fromkeys(h for t in targets for h in t.hands))
    return Target(" / ".join(t.name for t in targets), hands)


@cache
def _patterns(hands: tuple[str, ...], rules: Rules) -> tuple[Pattern, ...]:
    by_hand = _hand_patterns(rules)
    if len(hands) == 1:
        return by_hand[hands[0]]
    return _minimal(tuple(p for h in hands for p in by_hand[h]))

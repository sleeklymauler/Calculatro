"""Poker-hand targets expressed as card patterns, following Balatro's rules.

A target is satisfied if the final hand *contains* it, i.e. some selection of
up to 5 cards would be evaluated by Balatro as containing that hand type.
This mirrors Balatro's own containment: a Full House contains Two Pair, a
Straight Flush contains both Straight and Flush, but Four of a Kind does not
contain Two Pair.

A Target names the hand types; its patterns depend on the joker Rules in
play (Four Fingers, Shortcut, Smeared) and are built per Rules on demand.
Each target is a list of patterns (OR). A pattern is a tuple of requirements
(AND): "at least `need` cards whose class is in `keys`". Requirements are
usually disjoint, so each card fills one slot; when they overlap, a card counts
towards every requirement it matches (e.g. "at least 4 suited cards" alongside
"a suited card of each of these ranks").
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


@cache
def suit_keys(s: str) -> frozenset[ClassKey]:
    """Classes of every card that counts as suit `s`, Wild Cards included."""
    return frozenset(k for r in RANKS for k in _suited_rank(r, s))


@cache
def _straight_windows(rules: Rules) -> list[tuple[int, ...]]:
    """Sets of ranks that make a Straight: 5 of them, or 4 with Four Fingers.

    Each rank follows the previous one, or with Shortcut may skip one rank
    (`10 8 6 4 2`). Ace plays low (A-2-3-4-5) or high (10-J-Q-K-A); Balatro
    straights don't wrap around.
    """
    n = 4 if rules.four_fingers else 5
    steps = (1, 2) if rules.shortcut else (1,)
    runs = [(lo,) for lo in range(1, 15)]  # 1 is the low Ace
    for _ in range(n - 1):
        runs = [(*run, run[-1] + d) for run in runs for d in steps if run[-1] + d <= 14]
    return [tuple(14 if r == 1 else r for r in run) for run in runs]


def _n_of_a_kind(n: int) -> tuple[Pattern, ...]:
    return tuple((Requirement(_rank(r), n),) for r in RANKS)


def _straight_flush(rules: Rules, royal: bool = False) -> tuple[Pattern, ...]:
    """Straight Flush patterns; `royal` keeps every scoring card at 10 or above.

    The game scores the flush cards plus the straight cards, and only names the
    hand a Royal Flush if none of them is below 10.
    """
    windows = [w for w in _straight_windows(rules) if not royal or min(w) >= 10]
    if not rules.four_fingers:
        return tuple(tuple(Requirement(_suited_rank(r, s), 1) for r in w) for s in SUITS for w in windows)
    # With Four Fingers the flush and the straight may be different cards, as long
    # as all of them fit in 5 played cards. That means: 3 run ranks have a suited
    # card, the 4th (`m`) has any card, and there are 4 suited cards in all. The
    # 4th suited card is a second one of a run rank, or one of any other rank: if
    # that's `m`, the whole run is suited. Only cards of rank 10+ may score in a
    # Royal Flush, so only they count there.
    # Runs that share their 3 suited ranks differ only in `m`, so they're one
    # pattern with "a card of any rank that completes the run" (with Shortcut,
    # 5 7 9 is completed by a 3, 4, 6, 8, 10 or Jack). Saying "a suited card of
    # another rank" rather than "4 suited cards in all" matters for speed too:
    # the engine then only remembers whether a suit has shown up, not how often.
    completions: dict[tuple[int, ...], set[int]] = {}
    for w in windows:
        for m in w:
            completions.setdefault(tuple(r for r in w if r != m), set()).add(m)
    # Listed by ranks first: the engine orders classes by the patterns they count
    # towards, and this keeps its sweep going rank by rank across the suits.
    patterns: list[Pattern] = []
    for three, ms in completions.items():
        any_m = Requirement(frozenset(k for m in ms for k in _rank(m)), 1)
        for s in SUITS:
            other = frozenset(k for r in RANKS if r not in three and (r >= 10 or not royal)
                              for k in _suited_rank(r, s))
            patterns.append((*(Requirement(_suited_rank(r, s), 1) for r in three), any_m, Requirement(other, 1)))
            for twice in three:
                patterns.append((*(Requirement(_suited_rank(r, s), 2 if r == twice else 1)
                                   for r in three), any_m))
    return tuple(patterns)


def _flush_house(rules: Rules) -> tuple[Pattern, ...]:
    patterns: list[Pattern] = []
    for s in SUITS:
        for a, b in permutations(RANKS, 2):
            sa, sb = _suited_rank(a, s), _suited_rank(b, s)
            patterns.append((Requirement(sa, 3), Requirement(sb, 2)))
            if rules.four_fingers:  # one of the five cards may be off-suit
                patterns.append((Requirement(sa, 3), Requirement(sb, 1), Requirement(_rank(b) - sb, 1)))
                patterns.append((Requirement(sa, 2), Requirement(_rank(a) - sa, 1), Requirement(sb, 2)))
    return tuple(patterns)


def _flush_five(rules: Rules) -> tuple[Pattern, ...]:
    patterns: list[Pattern] = []
    for s in SUITS:
        for r in RANKS:
            sr = _suited_rank(r, s)
            patterns.append((Requirement(sr, 5),))
            if rules.four_fingers:  # one of the five cards may be off-suit
                patterns.append((Requirement(sr, 4), Requirement(_rank(r) - sr, 1)))
    return tuple(patterns)


@cache
def _hand_patterns(rules: Rules) -> dict[str, tuple[Pattern, ...]]:
    flush_size = 4 if rules.four_fingers else 5
    return {
        "Pair": _n_of_a_kind(2),
        "Two Pair": tuple(
            (Requirement(_rank(a), 2), Requirement(_rank(b), 2)) for a, b in combinations(RANKS, 2)
        ),
        "Three of a Kind": _n_of_a_kind(3),
        "Straight": tuple(
            tuple(Requirement(_rank(r), 1) for r in w) for w in _straight_windows(rules)
        ),
        "Flush": tuple((Requirement(suit_keys(s), flush_size),) for s in SUITS),
        "Full House": tuple(
            (Requirement(_rank(a), 3), Requirement(_rank(b), 2)) for a, b in permutations(RANKS, 2)
        ),
        "Four of a Kind": _n_of_a_kind(4),
        "Straight Flush": _straight_flush(rules),
        # Balatro scores a Royal Flush as a Straight Flush; it's only the display name.
        "Royal Flush": _straight_flush(rules, royal=True),
        "Five of a Kind": _n_of_a_kind(5),
        "Flush House": _flush_house(rules),
        "Flush Five": _flush_five(rules),
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


def _at_least(pattern: Pattern, keys: frozenset[ClassKey]) -> int:
    """Fewest cards with a class in `keys` in a hand that satisfies `pattern`.

    Requirements with disjoint classes are met by different cards, so a greedy
    set of them gives a lower bound; it can miss some, which only costs speed.
    """
    inner = [r for r in pattern if r.keys <= keys]
    if len(inner) < 2:
        return inner[0].need if inner else 0
    total, used = 0, frozenset()
    for req in sorted(inner, key=lambda r: -r.need):
        if req.keys.isdisjoint(used):
            total += req.need
            used |= req.keys
    return total


def _implies(a: Pattern, b: Pattern) -> bool:
    """Whether satisfying pattern `a` always satisfies pattern `b`.

    Each requirement is a count of its own, so it's enough that `a` guarantees
    the cards each requirement of `b` needs.
    """
    return all(_at_least(a, rb.keys) >= rb.need for rb in b)


def _minimal(patterns: tuple[Pattern, ...]) -> tuple[Pattern, ...]:
    """Drop patterns that imply another one; they never change an OR's outcome.

    Of several equivalent patterns, the first is kept.
    """
    unique = list(dict.fromkeys(patterns))
    need = [sum(r.need for r in p) for p in unique]
    return tuple(
        a for i, a in enumerate(unique)
        if not any(j != i and need[j] <= need[i] and _implies(a, b) and (j < i or not _implies(b, a))
                   for j, b in enumerate(unique))
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

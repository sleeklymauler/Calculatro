"""Cards for tests, and a brute-force port of Balatro's evaluate_poker_hand to check the engine against."""

from __future__ import annotations

from fractions import Fraction
from itertools import combinations
from math import comb

from calculatro.rules import Rules
from calculatro.state import SUITS, Card

RANK_NAMES = {11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}


def card(rank_id: int, suit: str, enhancement: str = "c_base", debuff: bool = False, **kw) -> Card:
    return Card(rank=RANK_NAMES.get(rank_id, str(rank_id)), rank_id=rank_id, suit=suit,
                enhancement=enhancement, debuff=debuff, **kw)


def full_deck() -> list[Card]:
    return [card(r, s) for s in SUITS for r in range(2, 15)]


# --- Brute-force port of functions/misc_functions.lua -------------------------

def _id(c: Card) -> int | None:
    return None if c.is_stone else c.rank_id


RED = {"Hearts", "Diamonds"}


def _is_suit_flush(c: Card, suit: str, rules: Rules) -> bool:
    if c.is_stone:
        return False
    if c.is_wild and not c.debuff:
        return True
    if rules.smeared and (c.suit in RED) == (suit in RED):
        return True
    return c.suit == suit


def _x_same(num: int, hand: list[Card]) -> list[int]:
    counts: dict[int, int] = {}
    for c in hand:
        if _id(c) is not None:
            counts[_id(c)] = counts.get(_id(c), 0) + 1
    return [r for r, n in counts.items() if n == num]


def _flush(hand: list[Card], rules: Rules) -> list[Card]:
    """get_flush: the suited cards of the first suit with enough of them."""
    need = 4 if rules.four_fingers else 5
    if not need <= len(hand) <= 5:
        return []
    for s in SUITS:
        suited = [c for c in hand if _is_suit_flush(c, s, rules)]
        if len(suited) >= need:
            return suited
    return []


def _straight(hand: list[Card], rules: Rules) -> list[Card]:
    """get_straight: every card of the first long enough run of ranks."""
    need = 4 if rules.four_fingers else 5
    if not need <= len(hand) <= 5:
        return []
    ids: dict[int, list[Card]] = {}
    for c in hand:
        if _id(c) is not None and 1 < _id(c) < 15:
            ids.setdefault(_id(c), []).append(c)
    t: list[Card] = []
    length, straight, skipped = 0, False, False
    for j in range(1, 15):
        if (14 if j == 1 else j) in ids:
            length += 1
            skipped = False
            t += ids[14 if j == 1 else j]
        elif rules.shortcut and not skipped and j != 14:
            skipped = True
        else:
            length, skipped = 0, False
            if straight:
                break
            t = []
        if length >= need:
            straight = True
    return t if straight else []


def evaluate(hand: list[Card], rules: Rules) -> set[str]:
    _5, _4, _3, _2 = (_x_same(n, hand) for n in (5, 4, 3, 2))
    fl, st = _flush(hand, rules), _straight(hand, rules)
    out = set()
    if _5 and fl: out.add("Flush Five")
    if _3 and _2 and fl: out.add("Flush House")
    if _5: out.add("Five of a Kind")
    if fl and st:
        out.add("Straight Flush")
    if _4: out.add("Four of a Kind")
    if _3 and _2: out.add("Full House")
    if fl: out.add("Flush")
    if st: out.add("Straight")
    if _3: out.add("Three of a Kind")
    if len(_2) == 2 or (len(_3) == 1 and len(_2) == 1): out.add("Two Pair")
    if _2: out.add("Pair")
    if "Five of a Kind" in out: out.add("Four of a Kind")
    if "Four of a Kind" in out: out.add("Three of a Kind")
    if "Three of a Kind" in out: out.add("Pair")
    return out


def contains(final: list[Card], names: set[str], rules: Rules = Rules()) -> bool:
    """Can some playable selection (1-5 cards) be evaluated as one of `names`?"""
    return any(evaluate(list(sel), rules) & names
               for k in range(1, min(5, len(final)) + 1)
               for sel in combinations(final, k))


def brute(names: set[str], kept: list[Card], pool: list[Card], draws: int,
          rules: Rules = Rules()) -> Fraction:
    hits = sum(contains(kept + list(d), names, rules) for d in combinations(pool, draws))
    return Fraction(hits, comb(len(pool), draws))


def contains_forced(final: list[Card], forced: Card, names: set[str], rules: Rules = Rules()) -> bool:
    """Can a playable selection that includes `forced` be evaluated as one of `names`?"""
    return any(evaluate([forced, *sel], rules) & names
               for k in range(min(4, len(final)) + 1) for sel in combinations(final, k))

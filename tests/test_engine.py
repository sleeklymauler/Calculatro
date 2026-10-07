"""Engine checks against a brute-force port of Balatro's evaluate_poker_hand."""

from __future__ import annotations

import random
from collections import Counter
from fractions import Fraction
from itertools import combinations
from math import comb

import pytest

from calculatro.engine import evaluate_discard, probability
from calculatro.hands import TARGETS, combine, get_target
from calculatro.state import SUITS, Card, GameState

RANK_NAMES = {11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}


def card(rank_id: int, suit: str, enhancement: str = "c_base", debuff: bool = False, **kw) -> Card:
    return Card(rank=RANK_NAMES.get(rank_id, str(rank_id)), rank_id=rank_id, suit=suit,
                enhancement=enhancement, debuff=debuff, **kw)


def full_deck() -> list[Card]:
    return [card(r, s) for s in SUITS for r in range(2, 15)]


# --- Brute-force port of functions/misc_functions.lua (no jokers) ------------

def _id(c: Card) -> int | None:
    return None if c.is_stone else c.rank_id


def _is_suit_flush(c: Card, suit: str) -> bool:
    if c.is_stone:
        return False
    if c.is_wild and not c.debuff:
        return True
    return c.suit == suit


def _x_same(num: int, hand: list[Card]) -> list[int]:
    counts: dict[int, int] = {}
    for c in hand:
        if _id(c) is not None:
            counts[_id(c)] = counts.get(_id(c), 0) + 1
    return [r for r, n in counts.items() if n == num]


def _flush(hand: list[Card]) -> bool:
    return len(hand) == 5 and any(sum(_is_suit_flush(c, s) for c in hand) >= 5 for s in SUITS)


def _straight(hand: list[Card]) -> bool:
    if len(hand) != 5:
        return False
    ids = {_id(c) for c in hand if _id(c) is not None and 1 < _id(c) < 15}
    length = 0
    for j in range(1, 15):
        if (14 if j == 1 else j) in ids:
            length += 1
            if length >= 5:
                return True
        else:
            length = 0
    return False


def evaluate(hand: list[Card]) -> set[str]:
    _5, _4, _3, _2 = (_x_same(n, hand) for n in (5, 4, 3, 2))
    fl, st = _flush(hand), _straight(hand)
    out = set()
    if _5 and fl: out.add("Flush Five")
    if _3 and _2 and fl: out.add("Flush House")
    if _5: out.add("Five of a Kind")
    if fl and st:
        out.add("Straight Flush")
        if {_id(c) for c in hand} == {10, 11, 12, 13, 14}:
            out.add("Royal Flush")
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


def contains(final: list[Card], names: set[str]) -> bool:
    """Can some playable selection (1-5 cards) be evaluated as one of `names`?"""
    return any(evaluate(list(sel)) & names
               for k in range(1, min(5, len(final)) + 1)
               for sel in combinations(final, k))


def brute(names: set[str], kept: list[Card], pool: list[Card], draws: int) -> Fraction:
    hits = sum(contains(kept + list(d), names) for d in combinations(pool, draws))
    return Fraction(hits, comb(len(pool), draws))


# --- Tests --------------------------------------------------------------------

def random_card(rng: random.Random) -> Card:
    r, s = rng.randint(9, 14), rng.choice(SUITS)
    roll = rng.random()
    if roll < 0.1:
        return card(r, s, "m_wild", debuff=rng.random() < 0.3)
    if roll < 0.15:
        return card(r, s, "m_stone")
    return card(r, s)


@pytest.mark.parametrize("name", list(TARGETS))
def test_matches_brute_force(name):
    rng = random.Random(name)
    for _ in range(6):
        # Narrow rank range (9..A) and duplicates so every target is reachable.
        kept = [random_card(rng) for _ in range(rng.randint(2, 4))]
        pool = [random_card(rng) for _ in range(rng.randint(8, 12))]
        draws = rng.randint(1, min(4, 8 - len(kept)))
        assert probability(get_target(name), kept, pool, draws) == brute({name}, kept, pool, draws)


def test_combined_targets_match_brute_force():
    rng = random.Random(7)
    target = combine([get_target("trips"), get_target("flush")])
    for _ in range(10):
        kept = [random_card(rng) for _ in range(3)]
        pool = [random_card(rng) for _ in range(11)]
        expected = brute({"Three of a Kind", "Flush"}, kept, pool, 3)
        assert probability(target, kept, pool, 3) == expected


@pytest.mark.parametrize("names", [
    ("Straight", "Straight Flush"),
    ("Two Pair", "Full House", "Four of a Kind"),
    ("Flush", "Flush House", "Flush Five", "Royal Flush"),
])
def test_overlapping_targets_match_brute_force(names):
    rng = random.Random(" ".join(names))
    target = combine([get_target(n) for n in names])
    assert len(target.patterns()) < sum(len(get_target(n).patterns()) for n in names)  # pruned
    for _ in range(6):
        kept = [random_card(rng) for _ in range(3)]
        pool = [random_card(rng) for _ in range(11)]
        assert probability(target, kept, pool, 3) == brute(set(names), kept, pool, 3)


def test_qq99_example():
    """The QQ99XY question: keep QQ99 and draw 2, or keep QQ and draw 4?"""
    hand = [card(12, "Hearts"), card(12, "Spades"), card(9, "Clubs"), card(9, "Diamonds"),
            card(2, "Hearts"), card(7, "Clubs")]
    deck = [c for c in full_deck() if c not in hand]
    state = GameState(in_run=True, hand_size=6, hand=hand, deck=deck)
    trips = get_target("trips")

    keep_both = evaluate_discard(state, trips, [4, 5])
    assert keep_both.draws == 2
    # 4 outs (2 queens, 2 nines) in 46 cards
    assert keep_both.probability == 1 - Fraction(comb(42, 2), comb(46, 2))

    keep_queens = evaluate_discard(state, trips, [2, 3, 4, 5])
    assert keep_queens.draws == 4
    # Trips needs only rank counts, so check directly instead of the slow brute force.
    hits = sum(max(Counter(c.rank_id for c in hand[:2] + list(d)).values()) >= 3
               for d in combinations(deck, 4))
    assert keep_queens.probability == Fraction(hits, comb(46, 4))


def test_hidden_cards_drawn_from_unseen_pool():
    hand = [card(12, "Hearts"), card(12, "Spades"), Card(hidden=True)]
    hidden_identity = [card(12, "Clubs")]
    deck = [card(r, "Diamonds") for r in range(2, 8)]
    state = GameState(in_run=True, hand_size=3, hand=hand, deck=deck, hidden_pool=hidden_identity)
    # Keeping everything: the face-down card is one unknown card out of 7.
    assert evaluate_discard(state, get_target("trips"), []).probability == Fraction(1, 7)


def test_short_deck_limits_draws():
    hand = [card(5, "Hearts")]
    state = GameState(in_run=True, hand_size=8, hand=hand, deck=[card(5, "Spades")])
    out = evaluate_discard(state, get_target("pair"), [])
    assert out.draws == 1 and out.probability == 1


def test_full_deck_flush_five_speed():
    # Worst-ish case: big pool, many patterns, 5 draws.
    deck = full_deck()
    kept, pool = deck[:3], deck[3:]
    for name in ("Flush House", "Straight Flush", "Two Pair", "Full House"):
        probability(get_target(name), kept, pool, 5)

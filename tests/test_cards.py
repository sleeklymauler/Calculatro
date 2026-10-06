"""Card-count queries: parsing and exact odds against brute force."""

from __future__ import annotations

import random
from fractions import Fraction
from itertools import combinations
from math import comb

import pytest

from calculatro.cards import parse_query
from calculatro.engine import count_probability, evaluate_cards
from calculatro.state import SUITS, Card, GameState

RANK_NAMES = {11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}


def card(rank_id: int, suit: str, enhancement: str = "c_base", debuff: bool = False, **kw) -> Card:
    return Card(rank=RANK_NAMES.get(rank_id, str(rank_id)), rank_id=rank_id, suit=suit,
                enhancement=enhancement, debuff=debuff, **kw)


@pytest.mark.parametrize("text, label", [
    ("queen", "Queen: at least 1"),
    ("Q", "Queen: at least 1"),
    ("a queen", "Queen: at least 1"),
    ("2+ queens", "Queen: at least 2"),
    ("at least 2 queens", "Queen: at least 2"),
    ("2 queens", "Queen: at least 2"),
    (">1 queen", "Queen: at least 2"),
    ("no face cards", "Face card: none"),
    ("0 face", "Face card: none"),
    ("at most 1 spade", "Spade: at most 1"),
    ("<2 spades", "Spade: at most 1"),
    ("exactly 1 heart", "Heart: exactly 1"),
    ("=3 tens", "10: exactly 3"),
    ("10s", "T♠: at least 1"),
    ("2 of clubs", "2♣: at least 1"),
    ("2c", "2♣: at least 1"),
    ("QS", "Q♠: at least 1"),
    ("ten of diamonds", "T♦: at least 1"),
    ("ace of spades", "A♠: at least 1"),
    ("2 sevens", "7: at least 2"),
    ("purple", None),
    ("aces", "Ace: at least 1"),
    ("♥", "Heart: at least 1"),
])
def test_parse(text, label):
    if label is None:
        with pytest.raises(ValueError):
            parse_query(text)
    else:
        assert parse_query(text).label == label


def test_match_rules():
    wild = card(5, "Hearts", "m_wild")
    stone = card(12, "Spades", "m_stone")
    debuffed_wild = card(5, "Hearts", "m_wild", debuff=True)
    spade = parse_query("spade").filter.test
    assert spade(wild) and not spade(stone) and not spade(debuffed_wild)
    assert not parse_query("queen").filter.test(stone)
    assert not parse_query("face").filter.test(stone)
    assert parse_query("face").filter.test(card(11, "Clubs"))
    assert not parse_query("face").filter.test(card(14, "Clubs"))
    assert parse_query("5 of spades").filter.test(wild)


def brute(query, pool, draws) -> Fraction:
    hits = sum(query.accepts(sum(map(query.filter.test, d))) for d in combinations(pool, draws))
    return Fraction(hits, comb(len(pool), draws))


@pytest.mark.parametrize("text", ["queen", "2+ hearts", "no face cards", "exactly 1 spade",
                                  "at most 1 club", "2 of clubs", "3+ face"])
def test_matches_brute_force(text):
    rng = random.Random(text)
    query = parse_query(text)
    for _ in range(5):
        pool = [card(rng.randint(2, 14), rng.choice(SUITS),
                     rng.choice(["c_base"] * 6 + ["m_wild", "m_stone"])) for _ in range(rng.randint(8, 14))]
        draws = rng.randint(1, 5)
        assert count_probability(query, pool, draws) == brute(query, pool, draws)


def test_only_drawn_cards_count():
    # Keeping two queens doesn't make "a queen" certain: only the draw counts.
    hand = [card(12, "Hearts"), card(12, "Spades"), card(2, "Clubs", highlighted=True)]
    deck = [card(12, "Clubs")] + [card(r, "Diamonds") for r in range(3, 8)]
    state = GameState(in_run=True, hand_size=3, hand=hand, deck=deck)
    out = evaluate_cards(state, parse_query("queen"), state.selected)
    assert out.draws == 1 and out.probability == Fraction(1, 6)

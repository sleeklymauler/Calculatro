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
    ("lucky", "Lucky card: at least 1"),
    ("2+ mult cards", "Mult card: at least 2"),
    ("no glass cards", "Glass card: none"),
    ("a gold card", "Gold card: at least 1"),
    ("gold seal", "Gold Seal card: at least 1"),
    ("2 red seals", "Red Seal card: at least 2"),
    ("any seal", "Sealed card: at least 1"),
    ("sealed cards", "Sealed card: at least 1"),
    ("polychrome", "Polychrome card: at least 1"),
    ("holo", "Holographic card: at least 1"),
    ("exactly 1 foil", "Foil card: exactly 1"),
    ("edition", "Edition card: at least 1"),
    ("enhanced", "Enhanced card: at least 1"),
    ("foil hearts", "Foil Heart: at least 1"),
    ("lucky red seal queen", "Lucky Red Seal Queen: at least 1"),
    ("2+ steel kings", "Steel King: at least 2"),
    ("bonus 2 of clubs", "Bonus 2♣: at least 1"),
    ("wild", "Wild card: at least 1"),
    ("red", None),
    ("lucky purple", None),
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


def test_modifier_rules():
    lucky_q = card(12, "Hearts", "m_lucky", seal="Red", edition="foil")
    plain_q = card(12, "Hearts")
    gold = card(3, "Clubs", "m_gold")
    gold_seal = card(3, "Clubs", seal="Gold")

    def test(text, c):
        return parse_query(text).filter.test(c)

    assert test("lucky", lucky_q) and not test("lucky", plain_q)
    assert test("lucky red seal queen", lucky_q) and not test("lucky red seal king", lucky_q)
    assert test("foil heart", lucky_q) and not test("polychrome", lucky_q)
    assert test("gold", gold) and not test("gold", gold_seal)
    assert test("gold seal", gold_seal) and not test("gold seal", gold)
    assert test("sealed", gold_seal) and not test("sealed", gold)
    assert test("enhanced", gold) and not test("enhanced", gold_seal) and not test("enhanced", plain_q)
    assert test("edition", lucky_q) and not test("edition", plain_q)
    # Stone Cards have no rank, so a "stone queen" can't exist.
    assert test("stone", card(12, "Hearts", "m_stone")) and not test("stone queen", card(12, "Hearts", "m_stone"))
    # Debuffed cards are still drawn as what they are.
    assert test("mult", card(5, "Spades", "m_mult", debuff=True))


def brute(query, pool, draws) -> Fraction:
    hits = sum(query.accepts(sum(map(query.filter.test, d))) for d in combinations(pool, draws))
    return Fraction(hits, comb(len(pool), draws))


@pytest.mark.parametrize("text", ["queen", "2+ hearts", "no face cards", "exactly 1 spade",
                                  "at most 1 club", "2 of clubs", "3+ face", "2+ lucky",
                                  "no red seals", "foil hearts", "exactly 1 enhanced", "polychrome"])
def test_matches_brute_force(text):
    rng = random.Random(text)
    query = parse_query(text)
    for _ in range(5):
        pool = [card(rng.randint(2, 14), rng.choice(SUITS),
                     rng.choice(["c_base"] * 6 + ["m_wild", "m_stone", "m_lucky", "m_mult"]),
                     seal=rng.choice([None] * 3 + ["Red", "Gold"]),
                     edition=rng.choice([None] * 3 + ["foil", "polychrome"]))
                for _ in range(rng.randint(8, 14))]
        draws = rng.randint(1, 5)
        assert count_probability(query, pool, draws) == brute(query, pool, draws)


def test_only_drawn_cards_count():
    # Keeping two queens doesn't make "a queen" certain: only the draw counts.
    hand = [card(12, "Hearts"), card(12, "Spades"), card(2, "Clubs", highlighted=True)]
    deck = [card(12, "Clubs")] + [card(r, "Diamonds") for r in range(3, 8)]
    state = GameState(in_run=True, hand_size=3, hand=hand, deck=deck)
    out = evaluate_cards(state, parse_query("queen"), state.selected)
    assert out.draws == 1 and out.probability == Fraction(1, 6)

"""Card-count queries: parsing and exact odds against brute force."""

from __future__ import annotations

import random
from dataclasses import replace
from fractions import Fraction
from itertools import combinations, product
from math import comb

import pytest

from calculatro.cards import parse_query
from calculatro.engine import count_probability, evaluate_cards
from calculatro.rules import Rules
from calculatro.state import SUITS, Card, GameState, Joker

RANK_NAMES = {11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}
# Every combination of the rule jokers.
RULESETS = {
    " + ".join(name for name, on in zip(("four fingers", "shortcut", "smeared", "pareidolia"), flags) if on)
    or "no jokers": Rules(*flags)
    for flags in product((False, True), repeat=4)
}


def card(rank_id: int, suit: str, enhancement: str = "c_base", debuff: bool = False, **kw) -> Card:
    return Card(rank=RANK_NAMES.get(rank_id, str(rank_id)), rank_id=rank_id, suit=suit,
                enhancement=enhancement, debuff=debuff, **kw)


def matches(text: str, c: Card, rules: Rules = Rules()) -> bool:
    return parse_query(text).filter.test(c, rules)


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
    ("face of spades", "Face ♠: at least 1"),
    ("2+ face hearts", "Face ♥: at least 2"),
    ("lucky face of clubs", "Lucky Face ♣: at least 1"),
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
    assert matches("spade", wild) and not matches("spade", stone) and not matches("spade", debuffed_wild)
    assert not matches("queen", stone)
    assert not matches("face", stone)
    assert matches("face", card(11, "Clubs"))
    assert not matches("face", card(14, "Clubs"))
    assert not matches("face", card(11, "Clubs", debuff=True))
    assert matches("5 of spades", wild)
    assert matches("face of spades", card(12, "Spades")) and not matches("face of spades", card(12, "Hearts"))
    assert not matches("face of spades", card(9, "Spades")) and matches("face of hearts", card(13, "Clubs", "m_wild"))


def test_smeared_suits():
    smeared = Rules(smeared=True)
    assert matches("diamond", card(5, "Hearts"), smeared) and matches("heart", card(5, "Diamonds"), smeared)
    assert matches("club", card(5, "Spades"), smeared) and not matches("club", card(5, "Hearts"), smeared)
    assert matches("5 of clubs", card(5, "Spades"), smeared) and not matches("5 of clubs", card(5, "Spades"))
    # A debuffed Wild Card is only its own colour; a Stone Card has no suit.
    debuffed_wild = card(5, "Hearts", "m_wild", debuff=True)
    assert matches("diamond", debuffed_wild, smeared) and not matches("spade", debuffed_wild, smeared)
    assert not matches("spade", card(5, "Clubs", "m_stone"), smeared)


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
def test_pareidolia_faces(rules):
    # Pareidolia makes every card a face card, Stone Cards included, except
    # debuffed ones; the other rule jokers don't change what a face card is.
    cards = [card(11, "Clubs"), card(14, "Clubs"), card(2, "Hearts"), card(12, "Spades", "m_stone"),
             card(5, "Hearts", "m_wild"), card(13, "Diamonds", "m_stone")]
    expected = [True, rules.pareidolia, rules.pareidolia, rules.pareidolia, rules.pareidolia, rules.pareidolia]
    assert [matches("face", c, rules) for c in cards] == expected
    for c in cards:
        assert not matches("face", replace(c, debuff=True), rules)
    # Pareidolia changes no rank or suit, and modifiers still apply on top.
    assert matches("queen", card(12, "Spades"), rules) and not matches("queen", card(2, "Spades"), rules)
    assert matches("foil face", card(2, "Spades", edition="foil"), rules) == rules.pareidolia
    assert not matches("foil face", card(12, "Spades"), rules)
    assert not matches("face card", card(12, "Spades", debuff=True), rules)
    # A suited face card needs the suit too, which a Stone Card lacks.
    assert matches("face of spades", card(2, "Spades"), rules) == rules.pareidolia
    assert not matches("face of spades", card(12, "Spades", "m_stone"), rules)


def test_modifier_rules():
    lucky_q = card(12, "Hearts", "m_lucky", seal="Red", edition="foil")
    plain_q = card(12, "Hearts")
    gold = card(3, "Clubs", "m_gold")
    gold_seal = card(3, "Clubs", seal="Gold")

    assert matches("lucky", lucky_q) and not matches("lucky", plain_q)
    assert matches("lucky red seal queen", lucky_q) and not matches("lucky red seal king", lucky_q)
    assert matches("foil heart", lucky_q) and not matches("polychrome", lucky_q)
    assert matches("gold", gold) and not matches("gold", gold_seal)
    assert matches("gold seal", gold_seal) and not matches("gold seal", gold)
    assert matches("sealed", gold_seal) and not matches("sealed", gold)
    assert matches("enhanced", gold) and not matches("enhanced", gold_seal) and not matches("enhanced", plain_q)
    assert matches("edition", lucky_q) and not matches("edition", plain_q)
    # Stone Cards have no rank, so a "stone queen" can't exist.
    assert matches("stone", card(12, "Hearts", "m_stone")) and not matches("stone queen", card(12, "Hearts", "m_stone"))
    # Debuffed cards are still drawn as what they are.
    assert matches("mult", card(5, "Spades", "m_mult", debuff=True))


def brute(query, pool, draws, rules: Rules = Rules()) -> Fraction:
    hits = sum(query.accepts(sum(query.filter.test(c, rules) for c in d)) for d in combinations(pool, draws))
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


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("text", ["no face cards", "3+ face", "exactly 2 face", "at most 1 face",
                                  "lucky face", "2+ hearts", "queen", "5 of clubs"])
def test_matches_brute_force_with_rules(text, rules):
    rng = random.Random(text)
    query = parse_query(text)
    for _ in range(4):
        pool = [card(rng.randint(2, 14), rng.choice(SUITS),
                     rng.choice(["c_base"] * 5 + ["m_wild", "m_stone", "m_lucky"]),
                     debuff=rng.random() < 0.2)
                for _ in range(rng.randint(8, 12))]
        draws = rng.randint(1, 5)
        assert count_probability(query, pool, draws, rules) == brute(query, pool, draws, rules)


def test_pareidolia_from_game_state():
    # Only the drawn card counts: with Pareidolia it's a face card unless debuffed.
    hand = [card(12, "Hearts"), card(2, "Clubs", highlighted=True)]
    deck = [card(3, "Clubs"), card(4, "Clubs", debuff=True), card(5, "Spades", "m_stone"), card(13, "Hearts")]
    state = GameState(in_run=True, hand_size=2, hand=hand, deck=deck)
    face = parse_query("face")
    assert evaluate_cards(state, face, state.selected).probability == Fraction(1, 4)
    with_joker = replace(state, jokers=[Joker("j_pareidolia")])
    assert evaluate_cards(with_joker, face, state.selected).probability == Fraction(3, 4)
    debuffed = replace(state, jokers=[Joker("j_pareidolia", debuff=True)])  # a debuffed joker has no effect
    assert evaluate_cards(debuffed, face, state.selected).probability == Fraction(1, 4)


def test_only_drawn_cards_count():
    # Keeping two queens doesn't make "a queen" certain: only the draw counts.
    hand = [card(12, "Hearts"), card(12, "Spades"), card(2, "Clubs", highlighted=True)]
    deck = [card(12, "Clubs")] + [card(r, "Diamonds") for r in range(3, 8)]
    state = GameState(in_run=True, hand_size=3, hand=hand, deck=deck)
    out = evaluate_cards(state, parse_query("queen"), state.selected)
    assert out.draws == 1 and out.probability == Fraction(1, 6)

"""Card-count queries: parsing and exact odds against brute force."""

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
    ("polychrome", "Poly card: at least 1"),
    ("holo", "Holo card: at least 1"),
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
    ("2 kings of hearts", "K♥: at least 2"),
    ("queens of spades", "Q♠: at least 1"),
    ("aces of hearts", "A♥: at least 1"),
    ("no queens of spades", "Q♠: none"),
    ("jacks of clubs", "J♣: at least 1"),
    ("face cards of spades", "Face ♠: at least 1"),
    ("2 face cards of hearts", "Face ♥: at least 2"),
    ("face card of diamonds", "Face ♦: at least 1"),
    ("lucky face cards of clubs", "Lucky Face ♣: at least 1"),
    ("two aces", "Ace: at least 2"),
    ("three kings", "King: at least 3"),
    ("at least two queens", "Queen: at least 2"),
    ("exactly one heart", "Heart: exactly 1"),
    ("at most three spades", "Spade: at most 3"),
    ("one queen", "Queen: at least 1"),
    ("two twos", "2: at least 2"),
    ("two of hearts", "2♥: at least 1"),
    # Ranges.
    ("between 1 and 3 queens", "Queen: between 1 and 3"),
    ("1-3 queens", "Queen: between 1 and 3"),
    ("0 to 2 hearts", "Heart: between 0 and 2"),
    ("between two and four aces", "Ace: between 2 and 4"),
    ("2-2 queens", "Queen: exactly 2"),
    ("3-1 queens", None),
    # Several ranks or suits, and groups of ranks.
    ("queen or king", "Q/K: at least 1"),
    ("2+ queens or kings", "Q/K: at least 2"),
    ("2, 3 or 5", "2/3/5: at least 1"),
    ("jack, queen or king", "J-K: at least 1"),
    ("2 or 3 or 4 or 5", "2-5: at least 1"),
    ("10 or 4 of spades", "4/T ♠: at least 1"),
    # Named by the ranks left out when that's shorter.
    ("2, 3, 4, 6, 7, 8, 10, jack, queen or ace", "not 5/9/K: at least 1"),
    ("2, 3, 4, 5, 6, 7, 8, 9, 10, jack, queen or king of hearts", "2-K ♥: at least 1"),  # a run is shorter still
    ("2, 3, 4, 5, 7, 8, 9, 10, jack, queen, king or ace", "not 6: at least 1"),
    ("2, 4, 6 or 8", "2/4/6/8: at least 1"),
    ("even", "Even: at least 1"),
    ("odd hearts", "Odd ♥: at least 1"),
    ("fibonacci cards", "Fib: at least 1"),
    ("non-face cards", "Non-face: at least 1"),
    ("2+ non-face of spades", "Non-face ♠: at least 2"),
    ("hearts or diamonds", "♥♦: at least 1"),
    ("red cards", "♥♦: at least 1"),
    ("black suits", "♠♣: at least 1"),
    ("not hearts", "♠♣♦: at least 1"),
    ("queen of hearts or diamonds", "Q♥♦: at least 1"),
    ("1-3 even of red suits", "Even ♥♦: between 1 and 3"),
    ("queen or king of not hearts", "Q/K ♠♣♦: at least 1"),
    ("queen or purple", None),
    ("black", None),
    # Negated modifiers.
    ("not sealed", "No Seal card: at least 1"),
    ("unsealed", "No Seal card: at least 1"),
    ("unenhanced queens", "Plain Queen: at least 1"),
    ("not lucky", "No Lucky card: at least 1"),
    ("non-foil hearts", "No Foil Heart: at least 1"),
    ("at most 2 not red seal", "No Red Seal card: at most 2"),
    ("not edition", "No Edition card: at least 1"),
    ("no seal", "Sealed card: none"),
    ("not", None),
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


def test_rank_groups_and_sets():
    assert [r for r in range(2, 15) if matches("even", card(r, "Clubs"))] == [2, 4, 6, 8, 10]
    assert [r for r in range(2, 15) if matches("odd", card(r, "Clubs"))] == [3, 5, 7, 9, 14]
    assert [r for r in range(2, 15) if matches("fibonacci", card(r, "Clubs"))] == [2, 3, 5, 8, 14]
    assert [r for r in range(2, 15) if matches("queen or king", card(r, "Clubs"))] == [12, 13]
    # Stone Cards have no rank, so they're in no group but non-face (as the game sees it).
    stone = card(4, "Clubs", "m_stone")
    assert not matches("even", stone) and not matches("4 or 10", stone) and matches("non-face", stone)
    assert matches("non-face", card(5, "Hearts")) and not matches("non-face", card(11, "Hearts"))
    assert matches("non-face", card(11, "Hearts", debuff=True))
    pareidolia = Rules(pareidolia=True)
    assert not matches("non-face", card(5, "Hearts"), pareidolia)
    assert matches("non-face", card(5, "Hearts", debuff=True), pareidolia)


def test_suit_sets():
    wild = card(5, "Hearts", "m_wild")
    assert matches("red cards", card(5, "Diamonds")) and not matches("red cards", card(5, "Spades"))
    assert matches("not hearts", card(5, "Clubs")) and not matches("not hearts", card(5, "Hearts"))
    # A Wild Card is every suit, so it's one of the others too; a Stone Card is none.
    assert matches("not hearts", wild) and matches("black suits", wild)
    assert not matches("hearts or diamonds", card(5, "Hearts", "m_stone"))
    # With Smeared, a Heart is also a Diamond.
    assert matches("not hearts", card(5, "Hearts"), Rules(smeared=True))
    assert matches("queen or king of spades or clubs", card(13, "Clubs"))
    assert not matches("queen or king of spades or clubs", card(13, "Hearts"))


def test_negated_modifiers():
    plain = card(12, "Hearts")
    lucky = card(12, "Hearts", "m_lucky", seal="Red", edition="foil")
    assert matches("not sealed", plain) and not matches("unsealed", lucky)
    assert matches("unenhanced", plain) and not matches("not enhanced", lucky)
    assert matches("not lucky", plain) and not matches("not lucky", lucky)
    assert matches("not red seal", card(12, "Hearts", seal="Gold")) and not matches("not red seal", lucky)
    assert matches("not edition", plain) and not matches("non-foil", lucky)
    assert matches("not sealed queen or king of hearts", plain)
    assert not matches("not sealed queen or king of hearts", card(11, "Hearts"))


def test_ranges_count():
    q = parse_query("between 1 and 3 queens")
    assert [k for k in range(6) if q.accepts(k)] == [1, 2, 3]


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
                                  "no red seals", "foil hearts", "exactly 1 enhanced", "polychrome",
                                  "between 1 and 2 queen or king", "1-3 even of hearts or diamonds",
                                  "at most 2 not sealed", "0-1 non-face"])
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
                                  "lucky face", "2+ hearts", "queen", "5 of clubs", "1-2 non-face",
                                  "between 1 and 3 not hearts"])
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
    # The kept card is debuffed, so never a face card: only the drawn card can be,
    # and with Pareidolia it is unless debuffed.
    hand = [card(12, "Hearts", debuff=True), card(2, "Clubs", highlighted=True)]
    deck = [card(3, "Clubs"), card(4, "Clubs", debuff=True), card(5, "Spades", "m_stone"), card(13, "Hearts")]
    state = GameState(in_run=True, hand_size=2, hand=hand, unseen=deck)
    face = parse_query("face")
    assert evaluate_cards(state, face, state.selected).probability == Fraction(1, 4)
    with_joker = replace(state, jokers=[Joker("j_pareidolia")])
    assert evaluate_cards(with_joker, face, state.selected).probability == Fraction(3, 4)
    debuffed = replace(state, jokers=[Joker("j_pareidolia", debuff=True)])  # a debuffed joker has no effect
    assert evaluate_cards(debuffed, face, state.selected).probability == Fraction(1, 4)


def test_kept_cards_count():
    # The final hand is the kept cards and the draw: keeping two queens makes "a queen" certain.
    hand = [card(12, "Hearts"), card(12, "Spades"), card(2, "Clubs", highlighted=True)]
    deck = [card(12, "Clubs")] + [card(r, "Diamonds") for r in range(3, 8)]
    state = GameState(in_run=True, hand_size=3, hand=hand, unseen=deck)
    assert evaluate_cards(state, parse_query("queen"), state.selected).probability == 1
    out = evaluate_cards(state, parse_query("3+ queens"), state.selected)
    assert out.draws == 1 and out.probability == Fraction(1, 6)
    assert evaluate_cards(state, parse_query("at most 2 queens"), state.selected).probability == Fraction(5, 6)
    # Discarding a queen, the draw has to make up for it.
    assert evaluate_cards(state, parse_query("2+ queens"), [0, 2]).probability == Fraction(1, 3)


def final_hand_brute(state: GameState, query, discard: list[int]) -> Fraction:
    """P(`query`) over every final hand: which kept cards The Hook takes (when the
    selection is played), then which unseen cards the face-down kept ones and the draw are."""
    kept = [c for i, c in enumerate(state.hand) if i not in discard]
    hooked = min(2, len(kept)) if state.hook and discard and state.discards_left <= 0 else 0
    pool = state.unseen
    total, outcomes = Fraction(0), 0
    for taken in combinations(range(len(kept)), hooked):
        left = [c for i, c in enumerate(kept) if i not in taken]
        known = [c for c in left if not c.hidden]
        draws = min(state.hand_size - len(left), state.deck_size)
        for unseen in combinations(pool, len(left) - len(known) + draws):
            total += query.accepts(sum(query.filter.test(c, state.rules) for c in known + list(unseen)))
            outcomes += 1
    return total / outcomes


@pytest.mark.parametrize("blind", [None, "The House", "The Hook"])
@pytest.mark.parametrize("text", ["queen", "2+ hearts", "at most 1 face", "exactly 2 lucky",
                                  "between 1 and 3 not hearts", "no spades"])
def test_final_hand_matches_brute_force(text, blind):
    rng = random.Random(f"{text} {blind}")
    query = parse_query(text)
    for _ in range(4):
        cards = [card(rng.randint(2, 14), rng.choice(SUITS), rng.choice(["c_base"] * 4 + ["m_lucky", "m_wild"]))
                 for _ in range(13)]
        hand = cards[:5]
        hidden_pool = []
        if blind == "The House":
            hidden_pool, hand = hand[3:], hand[:3] + [Card(hidden=True)] * 2
        state = GameState(in_run=True, hand_size=5, hand=hand, unseen=cards[5:] + hidden_pool,
                          blind=blind, discards_left=0 if blind == "The Hook" else 1, hands_left=2)
        for discard in ([], [0], [1, 3], [0, 2, 4]):
            assert evaluate_cards(state, query, discard).probability == final_hand_brute(state, query, discard)

"""Engine checks against a brute-force port of Balatro's evaluate_poker_hand."""

from __future__ import annotations

import random
import threading
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction
from itertools import combinations
from math import comb

import pytest

from calculatro import hands
from calculatro.cards import parse_query
from calculatro.engine import (
    _bell_after_draw,
    _favourable,
    _holds,
    _holds_test,
    _split_by_suit,
    count_probability,
    evaluate_cards,
    evaluate_discard,
    probability,
    rules_after,
)
from calculatro.hands import TARGETS, Requirement, card_key, combine, disjoint_groups, forced_patterns, get_target
from calculatro.rules import Rules
from calculatro.state import SUITS, Card, GameState, Joker
from poker import brute, card, contains, contains_forced, full_deck

# --- Tests --------------------------------------------------------------------

@pytest.mark.parametrize("masks", [(1, 1, 1, 1, 1, 1), (1, 2, 3, 1, 2, 1)])
@pytest.mark.parametrize("kept_ranks", [(), (2,), (6,)])
@pytest.mark.parametrize("include_outs", [False, True])
def test_partial_patterns_match_exhaustive_draws(masks, kept_ranks, include_outs):
    """Overlapping outs and deferred counts with different caps and draw bounds.

    The single-card alternatives can merge only in the plain OR. Deferred
    patterns share earlier cards, but need different numbers of future
    cards, so their deferred counts stop mattering at different draw budgets.
    Duplicate pool cards remain distinct choices and the Stone card is dead.
    """
    def req(ranks, need=1):
        return Requirement(frozenset((r, "Hearts") for r in ranks), need)

    patterns = [
        (req([2, 6]),),
        (req([4, 8]),),
        (req([2, 4, 6, 8]), req([9])),
        (req([2, 4, 8], 2), req([6])),
        (req([2, 4, 8]), req([7]), req([9])),
        (req([2, 4, 6, 8]), req([2, 4, 6, 8])),  # identical requirements count once
    ]
    if not include_outs:
        patterns, masks = patterns[2:5], masks[2:5]
    kept = [card(r, "Hearts") for r in kept_ranks]
    pool = [card(r, "Hearts") for r in [2, 2, 4, 6, 7, 8, 9]] + [card(3, "Clubs", "m_stone")]
    every = 0
    for mask in masks:
        every |= mask
    for draws in range(len(pool) + 1):
        expected = 0
        for draw in combinations(pool, draws):
            covered = 0
            for pattern, mask in zip(patterns, masks):
                if all(sum((c.rank_id, c.suit) in r.keys for c in (*kept, *draw)) >= r.need
                       for r in pattern):
                    covered |= mask
            expected += covered == every
        assert _favourable(patterns, list(masks), kept, pool, draws, Rules(), None) == expected


def random_card(rng: random.Random) -> Card:
    r, s = rng.randint(9, 14), rng.choice(SUITS)
    roll = rng.random()
    if roll < 0.1:
        return card(r, s, "m_wild", debuff=rng.random() < 0.3)
    if roll < 0.15:
        return card(r, s, "m_stone")
    return card(r, s)


RULESETS = {
    " + ".join(name for name, on in (("four fingers", ff), ("shortcut", sc), ("smeared", sm)) if on)
    or "no jokers": Rules(four_fingers=ff, shortcut=sc, smeared=sm)
    for sm in (False, True) for ff in (False, True) for sc in (False, True)
}
SMEARED_RULESETS = {k: v for k, v in RULESETS.items() if v.smeared}
# Smeared leaves two suits, which aren't split (see probability()).
SPLIT_RULESETS = {k: v for k, v in RULESETS.items() if not v.smeared}


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("name", list(TARGETS))
def test_matches_brute_force(name, rules):
    rng = random.Random(name)
    for _ in range(6):
        # Narrow rank range (9..A) and duplicates so every target is reachable.
        kept = [random_card(rng) for _ in range(rng.randint(2, 4))]
        pool = [random_card(rng) for _ in range(rng.randint(8, 12))]
        draws = rng.randint(1, min(4, 8 - len(kept)))
        assert (probability(get_target(name), kept, pool, draws, rules)
                == brute({name}, kept, pool, draws, rules))


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
def test_quick_holds_test_matches_holds(rules):
    """_holds_test, for estimates from draws, says what _holds does: every target, and with a forced card."""
    rng = random.Random(str(rules))
    targets = [get_target(name) for name in TARGETS]
    targets.append(combine([get_target("Full House"), get_target("Straight Flush")]))
    for target in targets:
        test = _holds_test(target.patterns(rules))
        for _ in range(40):
            cards = [random_card(rng) for _ in range(rng.randint(0, 8))]
            assert test(card_key(c, rules) for c in cards) == _holds(target.patterns(rules), cards, rules)
            forced, rest = cards[:1], cards[1:]
            if forced:
                patterns = forced_patterns(target, rules, card_key(forced[0], rules))
                assert _holds_test(patterns)(card_key(c, rules) for c in rest) == _holds(patterns, rest, rules)


def test_combined_targets_match_brute_force():
    rng = random.Random(7)
    target = combine([get_target("trips"), get_target("flush")])
    for _ in range(10):
        kept = [random_card(rng) for _ in range(3)]
        pool = [random_card(rng) for _ in range(11)]
        expected = brute({"Three of a Kind", "Flush"}, kept, pool, 3)
        assert probability(target, kept, pool, 3) == expected


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("names", [
    ("Straight", "Straight Flush"),
    ("Two Pair", "Full House", "Four of a Kind"),
    ("Flush", "Flush House", "Flush Five", "Straight Flush"),
])
def test_overlapping_targets_match_brute_force(names, rules):
    rng = random.Random(" ".join(names))
    target = combine([get_target(n) for n in names])
    patterns = target.patterns(rules)
    assert len(patterns) < sum(len(get_target(n).patterns(rules)) for n in names)  # pruned
    for _ in range(6):
        kept = [random_card(rng) for _ in range(3)]
        pool = [random_card(rng) for _ in range(11)]
        assert probability(target, kept, pool, 3, rules) == brute(set(names), kept, pool, 3, rules)


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
def test_pareidolia_leaves_hands_unchanged(rules):
    # Pareidolia only makes every card a face card, which no hand type looks at,
    # alone or with any of the other rule jokers.
    with_pareidolia = replace(rules, pareidolia=True)
    for name in TARGETS:
        assert get_target(name).patterns(with_pareidolia) is get_target(name).patterns(rules), name
    rng = random.Random(f"pareidolia {rules}")
    for name in TARGETS:
        kept = [random_card(rng) for _ in range(rng.randint(2, 4))]
        pool = [random_card(rng) for _ in range(rng.randint(8, 11))]
        draws = rng.randint(1, min(4, 8 - len(kept)))
        assert (probability(get_target(name), kept, pool, draws, with_pareidolia)
                == brute({name}, kept, pool, draws, rules)), name


def dense_card(rng: random.Random, low_rank: int, suits: Sequence[str] = ("Hearts", "Spades")) -> Card:
    """Two suits, few ranks and many Wild Cards, so flush-based hands are common."""
    r, s = rng.randint(low_rank, 14), rng.choice(suits)
    roll = rng.random()
    if roll < 0.15:
        return card(r, s, "m_wild", debuff=rng.random() < 0.3)
    if roll < 0.2:
        return card(r, s, "m_stone")
    return card(r, s)


def _minimal_all_pairs(patterns):
    """hands._minimal as first written, comparing every pair of patterns."""
    unique = list(dict.fromkeys(patterns))
    need = [sum(r.need for r in p) for p in unique]
    return tuple(a for i, a in enumerate(unique)
                 if not any(j != i and need[j] <= need[i] and hands._implies(a, b)
                            and (j < i or not hands._implies(b, a))
                            for j, b in enumerate(unique)))


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
def test_minimal_skips_only_pairs_that_cant_imply(rules):
    combos = [tuple(TARGETS), ("Straight", "Flush", "Straight Flush"),
              ("Two Pair", "Full House", "Flush House", "Flush Five")]
    by_hand = hands._hand_patterns(rules)
    for names in combos:
        patterns = tuple(p for n in names for p in by_hand[n])
        assert hands._minimal(patterns) == _minimal_all_pairs(patterns), names


def test_patterns_built_once_by_concurrent_calculations(monkeypatch):
    """Calculations that need the same new patterns (one cancelled for the next) share one build."""
    builds = []
    minimal = hands._minimal
    monkeypatch.setattr(hands, "_minimal", lambda patterns: builds.append(1) or minimal(patterns))
    hands._patterns.cache_clear()
    target = combine([get_target("Straight Flush"), get_target("Flush House")])
    rules = Rules(four_fingers=True, shortcut=True)
    threads = [threading.Thread(target=target.patterns, args=(rules,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(builds) == 1


FOUR_FINGERS_RULESETS = {k: v for k, v in RULESETS.items() if v.four_fingers}


@pytest.mark.parametrize("rules", FOUR_FINGERS_RULESETS.values(), ids=FOUR_FINGERS_RULESETS.keys())
@pytest.mark.parametrize("name, low_rank", [
    ("Straight Flush", 8), ("Flush House", 11), ("Flush Five", 12),
])
def test_four_fingers_flush_hands_match_brute_force(name, low_rank, rules):
    # With Smeared, cards of all four suits, which make two colours.
    suits = SUITS if rules.smeared else ("Hearts", "Spades")
    rng = random.Random(name)
    for _ in range(8):
        kept = [dense_card(rng, low_rank, suits) for _ in range(rng.randint(2, 4))]
        pool = [dense_card(rng, low_rank, suits) for _ in range(rng.randint(8, 12))]
        draws = rng.randint(1, min(4, 8 - len(kept)))
        assert (probability(get_target(name), kept, pool, draws, rules)
                == brute({name}, kept, pool, draws, rules))


def two_suit_card(rng: random.Random, low_rank: int, wild: bool,
                  suits: Sequence[str] = ("Hearts", "Spades")) -> Card:
    """Like dense_card, with Wild Cards only if `wild`."""
    r, s = rng.randint(low_rank, 14), rng.choice(suits)
    roll = rng.random()
    if wild and roll < 0.15:
        return card(r, s, "m_wild", debuff=rng.random() < 0.3)
    if roll < 0.2:
        return card(r, s, "m_stone")
    return card(r, s)


@pytest.mark.parametrize("rules", SPLIT_RULESETS.values(), ids=SPLIT_RULESETS.keys())
@pytest.mark.parametrize("names, low_rank", [
    (("Straight Flush",), 8), (("Flush House",), 11), (("Flush Five",), 12),
    (("Flush",), 8), (("Full House", "Straight Flush"), 9), (("Two Pair", "Flush"), 10),
    (("Straight", "Flush"), 8),
])
def test_split_by_suit_matches_brute_force(names, low_rank, rules):
    """Targets with patterns of several suits are split by suit when no unseen card is Wild.

    Enough cards of two suits that terms with both of them come up; kept Wild
    Cards count towards both.
    """
    target = combine([get_target(n) for n in names])
    assert len(_split_by_suit(target.patterns(rules))[1]) > 2
    rng = random.Random(" ".join(names))
    for _ in range(8):
        kept = [two_suit_card(rng, low_rank, wild=True) for _ in range(rng.randint(2, 4))]
        pool = [two_suit_card(rng, low_rank, wild=False) for _ in range(rng.randint(8, 11))]
        draws = rng.randint(2, min(5, 8 - len(kept)))
        assert probability(target, kept, pool, draws, rules) == brute(set(names), kept, pool, draws, rules)


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("names, low_rank", [
    (("Straight Flush",), 8), (("Flush House",), 11), (("Full House", "Straight Flush"), 9),
    (("Straight", "Flush"), 8),
])
def test_interchangeable_suits_match_brute_force(names, low_rank, rules):
    """Suits with the same ranks kept and unseen give equal terms, which are computed once.

    A Club comes first in the search, so drawing it can leave a two-suit term
    out of reach before any of its suits' cards come up.
    """
    target = combine([get_target(n) for n in names])
    rng = random.Random(" ".join(names))
    for _ in range(6):
        ranks = [rng.randint(low_rank, 14) for _ in range(rng.randint(4, 6))]
        pool = [card(r, s) for r in ranks for s in ("Hearts", "Spades")] + [card(low_rank - 1, "Clubs")]
        kept = [card(r, s) for r in rng.sample(ranks, rng.randint(0, 1)) for s in ("Hearts", "Spades")]
        for c in kept:
            pool.remove(c)
        draws = rng.randint(3, min(6, 8 - len(kept)))
        assert probability(target, kept, pool, draws, rules) == brute(set(names), kept, pool, draws, rules)


@pytest.mark.parametrize("rules", SMEARED_RULESETS.values(), ids=SMEARED_RULESETS.keys())
@pytest.mark.parametrize("names, low_rank", [
    (("Flush",), 8), (("Straight Flush",), 8), (("Flush House",), 11),
    (("Flush Five",), 12), (("Full House", "Straight Flush"), 9), (("Straight", "Flush"), 8),
    (("Two Pair", "Flush House", "Flush Five"), 11),
])
@pytest.mark.parametrize("wild", [True, False], ids=["wild", "no wild"])
def test_smeared_flush_hands_match_brute_force(names, low_rank, wild, rules):
    """Four suits making two colours, so both suits of a colour make flushes together.

    Kept Wild Cards count towards both colours; unseen ones only with `wild`.
    """
    target = combine([get_target(n) for n in names])
    rng = random.Random(" ".join(names))
    for _ in range(8):
        kept = [two_suit_card(rng, low_rank, wild=True, suits=SUITS) for _ in range(rng.randint(2, 4))]
        pool = [two_suit_card(rng, low_rank, wild=wild, suits=SUITS) for _ in range(rng.randint(8, 11))]
        draws = rng.randint(2, min(5, 8 - len(kept)))
        assert probability(target, kept, pool, draws, rules) == brute(set(names), kept, pool, draws, rules)


SMEARED = Rules(smeared=True)


@pytest.mark.parametrize("hand, rules, hands", [
    # Hearts and Diamonds make a flush together, Spades and Clubs too.
    ("2H 7D 9H KD 4H", SMEARED, {"Flush"}),
    ("2S 7C 9S KC 4C", SMEARED, {"Flush"}),
    ("2H 7D 9H KS 4H", SMEARED, set()),
    ("9H TD JH QD KD", SMEARED, {"Straight Flush", "Straight", "Flush"}),
    ("TC JS QC KS AS", SMEARED, {"Straight Flush", "Straight", "Flush"}),
    # A Flush House or Flush Five needs every card in one colour.
    ("KH KD KH 7D 7H", SMEARED, {"Flush House", "Full House", "Two Pair", "Three of a Kind", "Pair", "Flush"}),
    ("KH KD KH KD KH", SMEARED, {"Flush Five", "Five of a Kind", "Four of a Kind", "Three of a Kind", "Pair",
                                 "Flush"}),
    ("KH KD KS 7D 7H", SMEARED, {"Full House", "Two Pair", "Three of a Kind", "Pair"}),
    # With Four Fingers one card may be off-colour.
    ("KH KD KS 7D 7H", Rules(four_fingers=True, smeared=True),
     {"Flush House", "Full House", "Two Pair", "Three of a Kind", "Pair", "Flush"}),
    # Every joker: 4 red cards and a Shortcut straight 2-4-6-8.
    ("2H 4D 6H 8S KD", Rules(four_fingers=True, shortcut=True, smeared=True),
     {"Straight Flush", "Straight", "Flush"}),
])
def test_smeared_examples(hand, rules, hands):
    cards = hand_of(hand)
    assert {n for n in TARGETS if contains(cards, {n}, rules)} == hands
    for name in TARGETS:
        assert probability(get_target(name), cards, [], 0, rules) == Fraction(name in hands), name


def test_smeared_debuffed_wild_keeps_its_colour():
    """A debuffed Wild Card isn't every suit, but it's still both suits of its colour."""
    debuffed_wild = card(5, "Clubs", "m_wild", debuff=True)
    cards = hand_of("2S 7C 9S KC") + [debuffed_wild]
    assert probability(get_target("Flush"), cards, [], 0, SMEARED) == 1
    cards = hand_of("2H 7D 9H KD") + [debuffed_wild]
    assert probability(get_target("Flush"), cards, [], 0, SMEARED) == 0
    assert probability(get_target("Flush"), cards, [], 0, Rules(four_fingers=True, smeared=True)) == 1


def test_four_fingers_straight_flush_with_many_unknown_cards():
    # Like The House: no known cards, 7 unknown. One search over every suit at
    # once took over 10 s here; the value is from that search.
    deck = full_deck()
    p = probability(get_target("Straight Flush"), deck[:1], deck[1:], 7, Rules(four_fingers=True))
    assert p == Fraction(904457, 16539300)


def test_flush_absorbs_four_fingers_flush_hands():
    """4 suited cards make a Four Fingers Flush, so it implies the other flush hands' patterns away."""
    rules = Rules(four_fingers=True)
    target = combine([get_target(n) for n in ("Flush", "Straight Flush", "Flush House")])
    assert target.patterns(rules) == get_target("Flush").patterns(rules)


def hand_of(text: str) -> list[Card]:
    """Cards from short notation: "2H 3H TS AD"."""
    ranks = {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}
    suits = {s[0]: s for s in SUITS}
    return [card(ranks.get(t[:-1]) or int(t[:-1]), suits[t[-1]]) for t in text.split()]


@pytest.mark.parametrize("hand, hands", [
    # 4-card Flush and Straight, including Ace-low.
    ("2H 7H 9H KH", {"Flush"}),
    ("AS 2H 3D 4C", {"Straight"}),
    ("JS QH KD AC", {"Straight"}),
    # The flush and the straight may be different cards (docs/jokers.md example).
    ("2H 3H 4H 9H 5S", {"Straight Flush", "Straight", "Flush"}),
    # ...and the 4th suited card may be a second copy of a straight rank.
    ("2H 3H 3H 4H 5S", {"Straight Flush", "Straight", "Flush", "Pair"}),
    ("9H TH JH QH KS", {"Straight Flush", "Straight", "Flush"}),
    # One card of a Flush House or Flush Five may be off-suit.
    ("KH KH KS 7H 7H", {"Flush House", "Full House", "Two Pair", "Three of a Kind", "Pair", "Flush"}),
    ("KH KH KH 7S 7H", {"Flush House", "Full House", "Two Pair", "Three of a Kind", "Pair", "Flush"}),
    ("KH KH KH KH KS", {"Flush Five", "Five of a Kind", "Four of a Kind", "Three of a Kind", "Pair",
                        "Flush"}),
])
def test_four_fingers_examples(hand, hands):
    cards = hand_of(hand)
    assert {n for n in TARGETS if contains(cards, {n}, Rules(four_fingers=True))} == hands
    for name in TARGETS:
        expected = Fraction(name in hands)
        assert probability(get_target(name), cards, [], 0, Rules(four_fingers=True)) == expected, name


SHORTCUT = Rules(shortcut=True)
BOTH = Rules(four_fingers=True, shortcut=True)


@pytest.mark.parametrize("hand, rules, straight", [
    # One missing rank between cards is skipped, every time (docs/jokers.md).
    ("TS 8H 6D 4C 2S", SHORTCUT, True),
    ("AS 3H 5D 7C 9S", SHORTCUT, True),  # Ace low
    ("9S JH KD AC 7S", SHORTCUT, True),  # Ace high
    ("2S 3H 4D 5C 8S", SHORTCUT, False),  # two missing ranks in a row break the run
    ("JS KH AD 2C 4S", SHORTCUT, False),  # no wrap-around
    ("2S 3H 4D 5C 8S", Rules(), False),
    # Both jokers: 4 ranks, each at most one rank apart.
    ("2S 4H 6D 8C", BOTH, True),
    ("QS AH 3D 5C", BOTH, False),
    ("TS QH AD", BOTH, False),  # too few cards
])
def test_shortcut_examples(hand, rules, straight):
    cards = hand_of(hand)
    assert contains(cards, {"Straight"}, rules) == straight
    assert probability(get_target("Straight"), cards, [], 0, rules) == straight


@pytest.mark.parametrize("hand, hands", [
    # The Four Fingers flush and the Shortcut straight may be different cards.
    ("2H 4H 6H KH 8S", {"Straight Flush", "Straight", "Flush"}),
    # Any 4 of 10-J-Q-K-A make a straight.
    ("TH JH KH AH 2S", {"Straight Flush", "Straight", "Flush"}),
    ("TH QH KH AH", {"Straight Flush", "Straight", "Flush"}),
])
def test_four_fingers_shortcut_examples(hand, hands):
    cards = hand_of(hand)
    assert {n for n in TARGETS if contains(cards, {n}, BOTH)} == hands
    for name in TARGETS:
        assert probability(get_target(name), cards, [], 0, BOTH) == Fraction(name in hands), name


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("name", ["Straight", "Straight Flush"])
def test_low_straights_match_brute_force(name, rules):
    """Ranks 2-8 and Aces, so Ace-low runs and Shortcut skips near the bottom come up."""
    rng = random.Random(name)
    ranks = [14, 2, 3, 4, 5, 6, 7, 8]
    for _ in range(6):
        cards = [card(rng.choice(ranks), rng.choice(["Hearts", "Spades"])) for _ in range(14)]
        kept, pool = cards[:3], cards[3:]
        assert probability(get_target(name), kept, pool, 3, rules) == brute({name}, kept, pool, 3, rules)


def test_qq99_example():
    """The QQ99XY question: keep QQ99 and draw 2, or keep QQ and draw 4?"""
    hand = [card(12, "Hearts"), card(12, "Spades"), card(9, "Clubs"), card(9, "Diamonds"),
            card(2, "Hearts"), card(7, "Clubs")]
    deck = [c for c in full_deck() if c not in hand]
    state = GameState(in_run=True, hand_size=6, hand=hand, unseen=deck)
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
    state = GameState(in_run=True, hand_size=3, hand=hand, unseen=deck + hidden_identity)
    # Keeping everything: the face-down card is one unknown card out of 7.
    assert evaluate_discard(state, get_target("trips"), []).probability == Fraction(1, 7)


def test_face_down_discards_stay_unseen():
    # A Queen of Clubs was discarded face down: the player doesn't know which
    # unseen card it was, so it's one of the 7 unseen cards that can't be drawn.
    hand = [card(12, "Hearts"), card(12, "Spades"), card(9, "Clubs")]
    unseen = [card(r, "Diamonds") for r in range(2, 8)] + [card(12, "Clubs")]
    state = GameState(in_run=True, hand_size=3, hand=hand, unseen=unseen, discard=[Card(hidden=True)])
    out = evaluate_discard(state, get_target("trips"), [2])
    assert state.deck_size == 6 and out.draws == 1 and out.probability == Fraction(1, 7)


def test_mark_face_down_discards_are_face_cards():
    # The Mark: a King was discarded face down, so it's one of the 2 unseen
    # Kings, and the other is among the 4 cards left to draw.
    state = replace(mark_state(), hand=[card(13, "Diamonds"), card(9, "Clubs")], hand_size=2,
                    discard=[Card(hidden=True)])
    out = evaluate_discard(state, get_target("pair"), [1])
    assert out.draws == 1 and out.probability == Fraction(1, 4)


def test_short_deck_limits_draws():
    hand = [card(5, "Hearts"), card(9, "Clubs")]
    state = GameState(in_run=True, hand_size=8, hand=hand, unseen=[card(5, "Spades")])
    out = evaluate_discard(state, get_target("pair"), [1])
    assert out.draws == 1 and out.probability == 1


@pytest.mark.parametrize("serpent", [False, True])
def test_keeping_a_short_hand_draws_nothing(serpent):
    # Hanged Man or a bigger hand size leaves the hand short: the game only
    # refills it after a play or discard, so keeping it draws nothing.
    hand = [card(2, "Spades"), card(5, "Hearts"), card(9, "Clubs")]
    unseen = [card(r, s) for r in range(2, 15) for s in ("Hearts", "Diamonds") if (r, s) != (5, "Hearts")]
    state = GameState(in_run=True, hand_size=8, hand=hand, unseen=unseen,
                      blind="The Serpent" if serpent else None, discards_used=int(serpent))
    keep = evaluate_discard(state, get_target("pair"), [])
    assert keep.draws == 0 and keep.probability == 0
    assert evaluate_cards(state, parse_query("heart"), []).probability == 1
    # Discarding refills it: up to the hand size, or The Serpent's 3.
    assert evaluate_discard(state, get_target("pair"), [0]).draws == (3 if serpent else 6)


def serpent_state(hand_cards: int, deck_cards: int = 10, **kw) -> GameState:
    """A Serpent round after a discard: 8-card hand size, a Queen of Spades among `deck_cards`."""
    hand = [card(r, "Hearts") for r in range(2, 2 + hand_cards)]
    deck = [card(12, "Spades")] + [card(r, "Clubs") for r in range(2, 1 + deck_cards)]
    return GameState(in_run=True, hand_size=8, hand=hand, unseen=deck, blind="The Serpent",
                     discards_used=1, **kw)


@pytest.mark.parametrize("discarded", [1, 3, 5])
def test_serpent_draws_three_after_any_discard(discarded):
    state = serpent_state(8)
    queen = parse_query("queen")
    out = evaluate_cards(state, queen, list(range(discarded)))
    assert out.draws == 3 and out.probability == Fraction(3, 10)
    assert evaluate_discard(state, get_target("pair"), list(range(discarded))).draws == 3


def test_serpent_draws_three_past_the_hand_size():
    # Already over the hand size: other blinds would draw nothing.
    state = serpent_state(9)
    assert evaluate_discard(state, get_target("pair"), [0]).draws == 3
    assert evaluate_discard(replace(state, blind="Small Blind"), get_target("pair"), [0]).draws == 0


def test_serpent_draws_at_most_the_deck():
    state = serpent_state(8, deck_cards=2)
    out = evaluate_cards(state, parse_query("queen"), [0, 1, 2, 3])
    assert out.draws == 2 and out.probability == 1


def test_serpent_keeping_a_short_hand_draws_nothing():
    # After a play or discard, its 3 cards were the round's draw until the next one.
    for counters in ({"hands_played": 1, "discards_used": 0}, {"hands_played": 0, "discards_used": 1},
                     {"hands_played": 0, "discards_used": 0}):
        state = replace(serpent_state(5), **counters)
        assert evaluate_discard(state, get_target("pair"), []).draws == 0


def test_disabled_serpent_fills_the_hand():
    state = serpent_state(8, blind_disabled=True)
    assert evaluate_discard(state, get_target("pair"), [0]).draws == 1
    assert evaluate_discard(state, get_target("pair"), [0, 1, 2, 3, 4]).draws == 5
    assert evaluate_discard(replace(serpent_state(5), blind_disabled=True), get_target("pair"), [0]).draws == 4


def is_face(c: Card) -> bool:
    """Card:is_face(true), as The Mark asks it: debuffs don't matter."""
    return not c.is_stone and c.rank_id in (11, 12, 13)


def mark_draws(pool: list[Card], marked: int, marked_kept: int, draws: int):
    """Every way The Mark's round can turn out, equally likely: which `marked` face
    cards are face down, which `marked_kept` of them are kept, and the `draws` cards
    drawn from the rest. Yields the unseen cards of the final hand."""
    faces = [j for j, c in enumerate(pool) if is_face(c)]
    for down in combinations(faces, marked):
        rest = [c for j, c in enumerate(pool) if j not in down]
        for kept_down in combinations(down, marked_kept):
            for drawn in combinations(rest, draws):
                yield [pool[j] for j in kept_down], list(drawn)


def mark_brute(names: set[str], kept: list[Card], pool: list[Card], marked: int, marked_kept: int,
               draws: int, rules: Rules = Rules()) -> Fraction:
    outcomes = [contains(kept + down + drawn, names, rules)
                for down, drawn in mark_draws(pool, marked, marked_kept, draws)]
    return Fraction(sum(outcomes), len(outcomes))


def mark_case(rng: random.Random, card_maker=random_card, **kw):
    """Kept cards, a pool with enough face cards for the face-down ones, and the
    counts to draw: at least one card, and usually a final hand of 5 or more."""
    while True:
        pool = [card_maker(rng, **kw) for _ in range(rng.randint(8, 10))]
        f = sum(map(is_face, pool))
        if 1 <= f < len(pool):
            break
    marked = rng.randint(1, min(3, f))
    marked_kept = rng.randint(0, min(2, marked))
    draws = rng.randint(1, min(4 - marked_kept, len(pool) - marked))
    kept = [card_maker(rng, **kw) for _ in range(max(1, rng.randint(5, 6) - marked_kept - draws))]
    return kept, pool, marked, marked_kept, draws


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("name", list(TARGETS))
def test_mark_matches_brute_force(name, rules):
    """The Mark's face-down cards are face cards, and the draws come from the rest of the pool."""
    rng = random.Random(f"mark {name}")
    for _ in range(4):
        kept, pool, marked, marked_kept, draws = mark_case(rng)
        assert (probability(get_target(name), kept, pool, marked_kept + draws, rules, None, marked, marked_kept)
                == mark_brute({name}, kept, pool, marked, marked_kept, draws, rules))


@pytest.mark.parametrize("rules", SPLIT_RULESETS.values(), ids=SPLIT_RULESETS.keys())
@pytest.mark.parametrize("names", [("Straight Flush",), ("Flush",), ("Full House", "Straight Flush")])
def test_mark_split_by_suit_matches_brute_force(names, rules):
    """No unseen Wild Cards, so these targets are split by suit, each term counting draws with so many face cards."""
    target = combine([get_target(n) for n in names])
    rng = random.Random(f"mark {names}")
    for _ in range(4):
        _, pool, marked, marked_kept, draws = mark_case(rng, two_suit_card, low_rank=9, wild=False)
        kept = [two_suit_card(rng, 9, wild=True) for _ in range(max(1, 5 - marked_kept - draws))]
        assert (probability(target, kept, pool, marked_kept + draws, rules, None, marked, marked_kept)
                == mark_brute(set(names), kept, pool, marked, marked_kept, draws, rules))


@pytest.mark.parametrize("text", ["king", "2+ face card", "at most 1 face card", "ace", "1+ heart", "0 queen"])
def test_mark_card_queries_match_brute_force(text):
    query = parse_query(text)
    rng = random.Random(f"mark {text}")
    for _ in range(6):
        kept, pool, marked, marked_kept, draws = mark_case(rng)
        have = sum(query.filter.test(c, Rules()) for c in kept)
        # The final hand's face-down cards count as well as the draw.
        outcomes = [query.accepts(have + sum(query.filter.test(c, Rules()) for c in down + drawn))
                    for down, drawn in mark_draws(pool, marked, marked_kept, draws)]
        assert (count_probability(query, pool, marked_kept + draws, Rules(), marked, marked_kept, have)
                == Fraction(sum(outcomes), len(outcomes)))


def mark_state(**kw) -> GameState:
    """The Mark: a King and a face-down card in hand; the unseen face cards are two Kings."""
    hand = [card(13, "Diamonds"), Card(hidden=True)]
    deck = [card(13, "Hearts"), card(2, "Clubs"), card(3, "Clubs"), card(4, "Clubs")]
    return GameState(in_run=True, hand_size=2, hand=hand, unseen=deck + [card(13, "Spades")],
                     blind="The Mark", **kw)


def test_mark_face_down_cards_are_face_cards():
    state = mark_state()
    # The face-down card is a face card, and both unseen face cards are Kings.
    assert evaluate_discard(state, get_target("pair"), []).probability == 1
    # Discarding the King: the face-down King pairs only with the other one, among 4 cards to draw.
    assert evaluate_discard(state, get_target("pair"), [0]).probability == Fraction(1, 4)
    # Discarding the face-down card leaves one King among 4 cards to draw.
    kings = parse_query("2+ kings")
    assert evaluate_cards(state, kings, [1]).probability == Fraction(1, 4)
    # Kept, the face-down card is a King.
    assert evaluate_cards(state, kings, []).probability == 1
    assert evaluate_discard(state, get_target("pair"), [1]).probability == Fraction(1, 4)


@pytest.mark.parametrize("change", [{"blind": "The Wheel"}, {"blind_disabled": True},
                                    {"jokers": [Joker("j_pareidolia")]}])
def test_face_down_cards_are_any_unseen_card_without_the_mark(change):
    # Any unseen card, as with The Wheel: once The Mark is disabled, its cards are
    # face up again, and with Pareidolia every card is a face card.
    state = replace(mark_state(), **change)
    assert evaluate_discard(state, get_target("pair"), []).probability == Fraction(2, 5)
    assert evaluate_cards(state, parse_query("2+ kings"), [1]).probability == Fraction(2, 5)
    assert evaluate_cards(state, parse_query("2+ kings"), []).probability == Fraction(2, 5)


def bell_brute(names: set[str], kept: list[Card], forced: Card, pool: list[Card], draws: int,
               rules: Rules = Rules()) -> Fraction:
    hits = sum(contains_forced(kept + list(d), forced, names, rules) for d in combinations(pool, draws))
    return Fraction(hits, comb(len(pool), draws))


def bell_draw_brute(names: set[str], kept: list[Card], pool: list[Card], draws: int,
                    rules: Rules = Rules()) -> Fraction:
    """Each card of the final hand is forced with equal probability."""
    total = Fraction(0)
    for d in combinations(pool, draws):
        final = kept + list(d)
        total += Fraction(sum(contains_forced(final[:i] + final[i + 1:], c, names, rules)
                              for i, c in enumerate(final)), len(final))
    return total / comb(len(pool), draws)


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("name", list(TARGETS))
def test_forced_card_matches_brute_force(name, rules):
    """Cerulean Bell, keeping the hand: its forced card must be part of the hand played."""
    rng = random.Random(f"bell {name}")
    for _ in range(5):
        cards = [random_card(rng) for _ in range(rng.randint(3, 5))]
        forced, kept = cards[0], cards[1:]
        pool = [random_card(rng) for _ in range(rng.randint(7, 10))]
        draws = rng.randint(1, min(3, 8 - len(cards)))
        assert (probability(get_target(name), kept, pool, draws, rules, forced=forced)
                == bell_brute({name}, kept, forced, pool, draws, rules))


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("name", ["Straight", "Flush", "Full House", "Straight Flush",
                                  "Flush House", "Flush Five"])
def test_forced_card_in_five_card_hands_matches_brute_force(name, rules):
    """Dense cards so 5-card hands come up, and a forced card that often can't be part of them."""
    rng = random.Random(f"bell dense {name}")
    for _ in range(4):
        forced = card(rng.randint(2, 14), rng.choice(SUITS))
        kept = [dense_card(rng, 9) for _ in range(rng.randint(2, 4))]
        pool = [dense_card(rng, 9) for _ in range(rng.randint(8, 10))]
        draws = rng.randint(2, min(4, 7 - len(kept)))
        assert (probability(get_target(name), kept, pool, draws, rules, forced=forced)
                == bell_brute({name}, kept, forced, pool, draws, rules))


@pytest.mark.parametrize("rules", RULESETS.values(), ids=RULESETS.keys())
@pytest.mark.parametrize("names", [("Straight",), ("Flush",), ("Full House",), ("Straight Flush",),
                                   ("Flush House",), ("Pair", "Straight")])
def test_random_forced_card_matches_brute_force(names, rules):
    """Cerulean Bell after a discard: it forces a random card of the final hand.

    Dense cards so 5-card hands come up, and low cards of any suit in the pool
    that often can't be part of them.
    """
    target = combine([get_target(n) for n in names])
    rng = random.Random(f"bell draw {names}")
    for _ in range(3):
        kept = [dense_card(rng, 9) for _ in range(rng.randint(3, 4))]
        pool = [dense_card(rng, 9) if rng.random() < 0.6 else card(rng.randint(2, 8), rng.choice(SUITS))
                for _ in range(rng.randint(7, 9))]
        draws = rng.randint(2, min(3, 8 - len(kept)))
        assert (_bell_after_draw(target, kept, pool, draws, rules, None)
                == bell_draw_brute(set(names), kept, pool, draws, rules))


@pytest.mark.parametrize("forced, rules, expected", [
    ("2S", Rules(four_fingers=True), True),  # four cards make it, and the forced card is played beside them
    ("AH", Rules(), True),  # it completes the Straight Flush
    ("2S", Rules(), False),  # 5 cards needed, so no room for it
])
def test_forced_card_and_straight_flush(forced, rules, expected):
    cards, card_forced = hand_of("TH JH QH KH"), hand_of(forced)[0]
    assert contains_forced(cards, card_forced, {"Straight Flush"}, rules) == expected
    assert probability(get_target("Straight Flush"), cards, [], 0, rules, forced=card_forced) == expected


def bell_state(**kw) -> GameState:
    """Cerulean Bell forcing the 2 of Spades; the deck has the Ace of Hearts that makes a Straight Flush."""
    hand = [card(10, "Hearts"), card(11, "Hearts"), card(12, "Hearts"), card(13, "Hearts"),
            card(2, "Spades", forced=True, highlighted=True)]
    deck = [card(14, "Hearts"), card(3, "Clubs"), card(4, "Clubs")]
    return GameState(in_run=True, hand_size=5, hand=hand, unseen=deck, blind="Cerulean Bell", **kw)


def test_bell_keeps_the_forced_card():
    state = bell_state()
    straight_flush = get_target("Straight Flush")
    # Keeping: the forced 2 has to be played, so no 5-card Straight Flush.
    assert evaluate_discard(state, straight_flush, []).probability == 0
    # Discarding it draws one of 3 cards, then forces a random one of the 5 in hand.
    assert evaluate_discard(state, straight_flush, [4]).probability == Fraction(1, 3)


@pytest.mark.parametrize("change", [{"blind": "The Wheel"}, {"blind_disabled": True}])
def test_no_forced_card_without_the_bell(change):
    state = replace(bell_state(), **change)
    assert evaluate_discard(state, get_target("Straight Flush"), []).probability == 0  # nothing to draw
    assert evaluate_discard(state, get_target("Straight Flush"), [4]).probability == Fraction(1, 3)
    # With 6 cards the 2 can be left out of the play, unless the Bell forces it
    # (after a draw it forces a random card: here one of the 5 that isn't the 2).
    def six(state):
        return replace(state, hand=state.hand + [card(5, "Diamonds")], hand_size=6)
    assert evaluate_discard(six(state), get_target("Straight Flush"), [5]).probability == Fraction(1, 3)
    assert evaluate_discard(six(bell_state()), get_target("Straight Flush"), [5]).probability == Fraction(5, 18)


def test_full_deck_flush_five_speed():
    # Worst-ish case: big pool, many patterns, 5 draws.
    deck = full_deck()
    kept, pool = deck[:3], deck[3:]
    for rules in RULESETS.values():
        for name in ("Flush House", "Straight Flush", "Two Pair", "Full House"):
            probability(get_target(name), kept, pool, 5, rules)


# --- The Hook -----------------------------------------------------------------

def hook_state(**kw) -> GameState:
    hand = [card(2, "Clubs"), card(5, "Diamonds"), card(9, "Hearts"), card(9, "Spades"), card(12, "Hearts"),
            Card(hidden=True)]
    deck = [card(r, s) for r, s in ((9, "Clubs"), (12, "Spades"), (12, "Clubs"), (4, "Hearts"),
                                     (6, "Hearts"), (13, "Hearts"), (3, "Spades"), (14, "Hearts"))]
    return GameState(**{"in_run": True, "hand_size": 6, "discards_left": 0, "hands_left": 2, "blind": "The Hook",
                        "hand": hand, "unseen": deck + [card(7, "Hearts")], **kw})


def test_the_hook_takes_two_kept_cards_when_playing():
    state = hook_state()
    pool = state.unseen
    kept = [2, 3, 4, 5]  # after playing the 2 and the 5
    for target in map(get_target, ("Pair", "Three of a Kind", "Two Pair", "Flush", "Full House")):
        expected = Fraction(0)
        for taken in combinations(kept, 2):
            left = [state.hand[i] for i in kept if i not in taken]
            known = [c for c in left if not c.hidden]
            expected += probability(target, known, pool, 4 + len(left) - len(known))
        out = evaluate_discard(state, target, [0, 1])
        assert out.draws == 4 and out.probability == expected / 6
    assert evaluate_cards(state, parse_query("at least 1 heart"), [0, 1]).draws == 4


def test_the_hook_only_after_a_play():
    state = hook_state()
    plain = replace(state, blind="Small Blind")
    trips = get_target("Three of a Kind")
    for s in (replace(state, discards_left=1), replace(state, blind_disabled=True)):  # discarded, or no Hook
        assert evaluate_discard(s, trips, [0, 1]) == evaluate_discard(replace(plain, discards_left=s.discards_left),
                                                                      trips, [0, 1])
    assert evaluate_discard(state, trips, []) == evaluate_discard(plain, trips, [])  # nothing played
    # It takes what's left when that's fewer than 2.
    out = evaluate_discard(state, trips, [0, 1, 2, 3, 4])
    assert out.draws == 6
    assert out.probability == probability(trips, [], state.unseen, 6)


# --- Crimson Heart ------------------------------------------------------------

def crimson_state(jokers: list[Joker], **kw) -> GameState:
    hand = [card(5, "Hearts"), card(6, "Hearts"), card(8, "Clubs"), card(9, "Hearts"), card(13, "Spades"),
            card(2, "Clubs")]
    deck = [card(r, s) for r, s in ((7, "Hearts"), (7, "Spades"), (4, "Hearts"), (10, "Diamonds"), (12, "Clubs"),
                                     (11, "Hearts"), (3, "Spades"), (14, "Hearts"), (9, "Clubs"))]
    return GameState(**{"in_run": True, "hand_size": 6, "discards_left": 0, "hands_left": 2,
                        "blind": "Crimson Heart", "hand": hand, "unseen": deck, "jokers": jokers, **kw})


def test_crimson_heart_rules_after_a_play():
    ff, sc, joker = Joker("j_four_fingers"), Joker("j_shortcut"), Joker("j_joker")

    def after(*jokers: Joker, discard=(0,), **kw) -> dict[Rules, int]:
        return dict(rules_after(crimson_state(list(jokers), **kw), list(discard)))

    # The debuffed joker comes back, and one of the others is debuffed.
    assert after(replace(ff, debuff=True), joker, sc) == {Rules(four_fingers=True, shortcut=True): 1,
                                                          Rules(four_fingers=True): 1}
    assert after(replace(ff, debuff=True), joker, joker, sc) == {Rules(four_fingers=True, shortcut=True): 2,
                                                                 Rules(four_fingers=True): 1}
    # A lone joker is debuffed again, debuffed or not.
    assert after(replace(sc, debuff=True)) == after(sc) == {Rules(): 1}
    # A run-out Perishable stays debuffed, and isn't picked.
    perished = replace(ff, debuff=True, perished=True)
    assert after(perished, replace(sc, debuff=True), joker) == {Rules(shortcut=True): 1}
    assert after(perished, replace(sc, debuff=True)) == {Rules(shortcut=True): 1}
    assert after(perished, replace(perished, key="j_smeared")) == {Rules(): 1}
    # Only after a play: not when keeping, discarding, or with the boss disabled or absent.
    for kw in ({"discard": ()}, {"discards_left": 1}, {"blind_disabled": True}, {"blind": "The Hook"}):
        assert after(replace(ff, debuff=True), joker, sc, **kw) == {Rules(shortcut=True): 1}


def test_crimson_heart_odds_average_the_rules_after_a_play():
    jokers = [Joker("j_four_fingers", debuff=True), Joker("j_joker"), Joker("j_shortcut")]
    state = crimson_state(jokers)
    plain = replace(state, blind="Small Blind")
    for name in ("Straight", "Flush", "Straight Flush", "Pair"):
        target = get_target(name)
        both = evaluate_discard(replace(plain, jokers=[Joker("j_four_fingers"), Joker("j_shortcut")]), target, [4, 5])
        ff = evaluate_discard(replace(plain, jokers=[Joker("j_four_fingers")]), target, [4, 5])
        out = evaluate_discard(state, target, [4, 5])
        assert out.draws == both.draws == 2
        assert out.probability == (both.probability + ff.probability) / 2
        # Keeping plays nothing, so it's judged under the current rules.
        assert evaluate_discard(state, target, []) == evaluate_discard(plain, target, [])
    straight = get_target("Straight")
    assert evaluate_discard(state, straight, [4, 5]) != evaluate_discard(plain, straight, [4, 5])


def test_crimson_heart_card_odds_after_a_play():
    # Pareidolia comes back after the play: every card is then a face card.
    state = crimson_state([Joker("j_pareidolia", debuff=True), Joker("j_joker")])
    plain = replace(state, blind="Small Blind", jokers=[Joker("j_pareidolia")])
    query = parse_query("at least 6 face cards")
    assert evaluate_cards(state, query, [4, 5]) == evaluate_cards(plain, query, [4, 5])
    assert evaluate_cards(state, query, [4, 5]).probability == 1
    assert evaluate_cards(state, query, []).probability == 0


def test_crimson_heart_works_out_rules_the_target_sees_once(monkeypatch):
    import calculatro.engine as engine
    calls = []
    real = engine.probability
    monkeypatch.setattr(engine, "probability", lambda *a, **kw: calls.append(a[4]) or real(*a, **kw))
    state = crimson_state([Joker("j_four_fingers", debuff=True), Joker("j_joker"), Joker("j_shortcut")])
    # Shortcut changes neither a Pair nor a Flush, so only one of the two rule sets is searched.
    for name, searched in (("Pair", 1), ("Flush", 1), ("Straight", 2)):
        calls.clear()
        evaluate_discard(state, get_target(name), [4, 5])
        assert len(calls) == searched


# --- DNA ----------------------------------------------------------------------

def dna_state(**kw) -> GameState:
    """No Pair in hand, and 7s and 2s among the cards left to draw."""
    hand = [card(3, "Hearts"), card(5, "Clubs"), card(7, "Spades"), card(9, "Hearts"), card(13, "Diamonds")]
    deck = [card(7, "Hearts"), card(2, "Clubs"), card(2, "Spades"), card(11, "Diamonds"), card(4, "Spades"),
            card(9, "Clubs")]
    return GameState(**{"in_run": True, "hand_size": 5, "discards_left": 0, "hands_left": 2, "hands_played": 0,
                        "dna_copies": 1, "hand": hand, "unseen": deck, **kw})


@pytest.mark.parametrize("name", ["Pair", "Two Pair", "Flush", "Straight"])
def test_dna_gives_the_hand_back(name):
    state, target = dna_state(), get_target(name)
    outcome = evaluate_discard(state, target, [2])
    assert outcome.draws == 0
    assert outcome.probability == evaluate_discard(state, target, []).probability


def test_dna_card_queries_give_the_hand_back():
    state = dna_state()
    for text in ("1+ spades", "at least 2 hearts", "at most 1 king"):
        query = parse_query(text)
        assert evaluate_cards(state, query, [2]).probability == evaluate_cards(state, query, []).probability


@pytest.mark.parametrize("change, discard", [
    ({"dna_copies": 0}, [2]),  # no active DNA
    ({"discards_left": 1}, [2]),  # it's thrown away, not played
    ({"hands_played": 1}, [2]),  # not the first hand
    ({"blind": "The Psychic"}, [2]),  # the hand is debuffed, so jokers don't act on it
    ({}, [1, 2]),  # not a lone card
])
def test_dna_only_acts_on_a_lone_first_hand(change, discard):
    without = replace(dna_state(), dna_copies=0, **{k: v for k, v in change.items() if k != "dna_copies"})
    state = replace(dna_state(), **change)
    target = get_target("Pair")
    assert evaluate_discard(state, target, discard) == evaluate_discard(without, target, discard)
    assert evaluate_discard(state, target, discard).draws == len(discard)


def test_dna_acts_under_a_disabled_psychic():
    state = dna_state(blind="The Psychic", blind_disabled=True)
    assert evaluate_discard(state, get_target("Pair"), [2]).draws == 0


def test_dna_copies_past_the_hand_size():
    # With Blueprint, the copies make a Pair of the played card, with nothing drawn.
    state = dna_state(dna_copies=2)
    outcome = evaluate_discard(state, get_target("Pair"), [2])
    assert (outcome.draws, outcome.probability) == (0, 1)
    assert evaluate_discard(state, get_target("Three of a Kind"), [2]).probability == 0
    assert evaluate_cards(state, parse_query("at least 2 spades"), [2]).probability == 1


def test_dna_short_hand_is_topped_up():
    # 5 cards in a hand of 6: the copy fills the played card's place, and 1 is drawn.
    state = dna_state(hand_size=6)
    outcome = evaluate_discard(state, get_target("Pair"), [2])
    assert outcome.draws == 1
    assert outcome.probability == probability(get_target("Pair"), state.hand, state.unseen, 1)


def test_dna_under_the_serpent_still_draws_three():
    state = dna_state(blind="The Serpent")
    outcome = evaluate_discard(state, get_target("Pair"), [2])
    assert outcome.draws == 3
    assert outcome.probability == probability(get_target("Pair"), state.hand, state.unseen, 3)


def test_dna_under_the_hook_never_loses_the_copy():
    # The Hook takes 2 of the 4 kept cards, then the copy arrives, then 2 are drawn.
    state = dna_state(blind="The Hook")
    hand, pool = state.hand, state.unseen
    played, others = hand[2], hand[:2] + hand[3:]
    for name in ("Pair", "Three of a Kind"):
        target = get_target(name)
        picks = [[c for c in others if c not in taken] for taken in combinations(others, 2)]
        expected = sum(probability(target, [*kept, played], pool, 2) for kept in picks) / len(picks)
        outcome = evaluate_discard(state, target, [2])
        assert (outcome.draws, outcome.probability) == (2, expected)
    query = parse_query("at least 1 spade")  # the only spade in hand is the copy, which always stays
    assert evaluate_cards(state, query, [2]).probability == 1


def test_dna_under_the_hook_copies_of_a_query():
    state = dna_state(blind="The Hook", dna_copies=2)
    assert evaluate_cards(state, parse_query("at least 2 spades"), [2]).probability == 1


def test_dna_under_cerulean_bell_may_force_the_copy():
    hand = [replace(c, forced=i == 2) for i, c in enumerate(dna_state().hand)]
    state = dna_state(blind="Cerulean Bell", hand=hand)
    target = get_target("Pair")
    outcome = evaluate_discard(state, target, [2])
    assert outcome.draws == 0
    assert outcome.probability == _bell_after_draw(target, [*hand[:2], *hand[3:], hand[2]], state.unseen, 0, Rules(),
                                                   None)


def test_dna_copy_of_a_face_down_card_is_keeping_it():
    hand = dna_state().hand[:4] + [Card(hidden=True)]
    state = dna_state(hand=hand, unseen=[*dna_state().unseen, dna_state().hand[4]])
    for name in ("Pair", "Flush"):
        outcome = evaluate_discard(state, get_target(name), [4])
        assert outcome.draws == 0
        assert outcome.probability == evaluate_discard(state, get_target(name), []).probability
    query = parse_query("at least 1 king")
    assert evaluate_cards(state, query, [4]).probability == evaluate_cards(state, query, []).probability


def test_dna_copy_of_a_face_down_card_under_the_mark():
    # All the same: The Mark knows it's a face card, kept or copied.
    king, queen = card(13, "Diamonds"), card(12, "Hearts")
    hand = dna_state().hand[:4] + [Card(hidden=True)]
    state = dna_state(blind="The Mark", hand=hand, unseen=[*dna_state().unseen, king, queen])
    target = get_target("Pair")
    assert evaluate_discard(state, target, [4]).probability == evaluate_discard(state, target, []).probability
    query = parse_query("at least 1 face card")
    assert evaluate_cards(state, query, [4]).probability == 1


@pytest.mark.parametrize("blind", [None, "The Mark"])
def test_dna_several_copies_of_a_face_down_card(blind):
    # Two copies are the same unknown card: a Pair for certain, and two of
    # whatever it is. The engine branches on what it is, with no evidence.
    hand = dna_state().hand[:4] + [Card(hidden=True)]
    king = card(13, "Clubs")
    unseen = [*dna_state().unseen, dna_state().hand[4], king]
    state = dna_state(hand=hand, unseen=unseen, dna_copies=2, blind=blind)
    outcome = evaluate_discard(state, get_target("Pair"), [4])
    assert (outcome.draws, outcome.probability) == (0, 1)
    # Two kings if it's one: any unseen card, or under The Mark a face card (J♦ K♦ K♣).
    kings = parse_query("at least 2 king")
    expected = Fraction(2, 3) if blind else Fraction(sum(c.rank_id == 13 for c in unseen), len(unseen))
    assert evaluate_cards(state, kings, [4]).probability == expected
    assert evaluate_cards(state, parse_query("exactly 1 king"), [4]).probability == 0


@pytest.mark.parametrize(("sets", "groups"), [
    ([], []),
    ([0b011, 0b100, 0b110, 0b001], [[0, 1], [2, 3]]),
    ([frozenset("ab"), frozenset("c"), frozenset("bc"), frozenset("a")], [[0, 1], [2, 3]]),
    ([frozenset(), frozenset("a"), frozenset()], [[0, 1, 2]]),
])
def test_disjoint_groups(sets, groups):
    assert disjoint_groups(sets) == groups

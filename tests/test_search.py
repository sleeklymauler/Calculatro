"""Best-discard search, checked against trying every discard."""

from __future__ import annotations

import random
import threading
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from itertools import combinations

import pytest

from calculatro.engine import Cancelled, Reuse, evaluate_discard
from calculatro.hands import combine, get_target
from calculatro.search import _identity, best_discards
from calculatro.state import SUITS, Card, GameState, Joker
from test_engine import card


def brute_force(state: GameState, target) -> tuple[Fraction, set]:
    """The best odds over every discard, and the tied discards as multisets of the cards thrown away."""
    forced = state.forced if state.bell else None
    others = [i for i in range(len(state.hand)) if i != forced]
    limit = min(state.select_limit, len(state.hand)) if state.discards_left > 0 or state.hands_left > 1 else 0
    discards = [[]]
    for n in range(limit + 1):
        if forced is not None and n == limit:
            break
        for subset in combinations(others, n):
            if forced is not None:
                discards.append([*subset, forced])
            elif n:
                discards.append(list(subset))
    odds = [(evaluate_discard(state, target, d).probability, d) for d in discards]
    best = max(p for p, _ in odds)
    return best, {cards_of(state, d) for p, d in odds if p == best}


def cards_of(state: GameState, discard) -> frozenset:
    return frozenset(Counter(_identity(state.hand[i]) for i in discard).items())


def check(state: GameState, target) -> list[list[int]]:
    found = best_discards(state, target)
    best, tied = brute_force(state, target)
    assert found.probability == best
    assert [cards_of(state, o) for o in found.options] == list({cards_of(state, o): 0 for o in found.options})
    assert {cards_of(state, o) for o in found.options} == tied
    sizes = [len(o) for o in found.options]
    assert sizes == sorted(sizes)
    return found.options


def random_state(rng: random.Random, hand=8, deck=14, **kw) -> GameState:
    cards = [card(r, s) for s in SUITS for r in range(2, 15)]
    rng.shuffle(cards)
    return GameState(**{"in_run": True, "hand_size": hand, "discards_left": 3, "hand": cards[:hand],
                        "deck": cards[hand:hand + deck], **kw})


TARGETS = ["Pair", "Two Pair", "Three of a Kind", "Straight", "Flush", "Full House"]


@pytest.mark.parametrize("seed", range(6))
def test_matches_trying_every_discard(seed):
    rng = random.Random(seed)
    state = random_state(rng)
    for name in TARGETS:
        check(state, get_target(name))
    check(state, combine([get_target("Flush"), get_target("Straight")]))


@pytest.mark.parametrize("jokers", [["j_smeared"], ["j_four_fingers", "j_shortcut"]])
def test_matches_trying_every_discard_with_jokers(jokers):
    rng = random.Random(7)
    state = random_state(rng, deck=10, jokers=[Joker(j) for j in jokers])
    for name in ("Straight", "Flush", "Straight Flush"):
        check(state, get_target(name))
    # "Any target": the hands' own odds bound it, and decide it when the bounds meet.
    check(state, combine([get_target(n) for n in ("Pair", "Flush", "Straight")]))
    check(state, combine([get_target(n) for n in ("Flush", "Straight Flush", "Full House")]))


def test_wild_stone_and_debuffed_cards():
    hand = [card(5, "Hearts", "m_wild"), card(9, "Clubs", "m_wild", debuff=True), card(7, "Spades", "m_stone"),
            card(7, "Hearts", debuff=True), card(12, "Hearts"), card(3, "Hearts"), card(3, "Diamonds"),
            card(13, "Spades")]
    deck = [card(r, s) for s in ("Hearts", "Clubs") for r in range(6, 13)]
    state = GameState(in_run=True, hand_size=8, discards_left=1, hand=hand, deck=deck)
    for name in ("Flush", "Pair", "Two Pair", "Straight"):
        check(state, get_target(name))


def test_the_serpent_always_draws_three():
    rng = random.Random(3)
    state = random_state(rng, blind="The Serpent")
    for name in ("Pair", "Flush", "Three of a Kind"):
        check(state, get_target(name))


def test_face_down_cards_are_interchangeable():
    # The House/The Mark: face-down cards can be thrown away, but the app can't tell them apart.
    rng = random.Random(4)
    state = random_state(rng, deck=10)
    hidden = [card(11, "Spades"), card(13, "Hearts")]
    hand = state.hand[:6] + [Card(hidden=True), Card(hidden=True)]
    for blind in ("The House", "The Mark"):
        s = replace(state, hand=hand, hidden_pool=hidden, blind=blind)
        for name in ("Pair", "Three of a Kind", "Flush"):
            for option in check(s, get_target(name)):
                # The leftmost face-down cards, never a choice between them.
                assert [i for i in option if i >= 6] in ([], [6], [6, 7])


def test_cerulean_bell_discards_its_forced_card():
    rng = random.Random(5)
    state = random_state(rng, deck=10, blind="Cerulean Bell")
    state = replace(state, hand=[replace(c, forced=i == 2) for i, c in enumerate(state.hand)])
    for name in ("Pair", "Flush", "Straight"):
        for option in check(state, get_target(name)):
            assert option == [] or 2 in option


def test_ties_go_to_fewest_cards_then_lowest_ranks():
    # Pair in hand: keeping wins outright.
    hand = [card(9, "Hearts"), card(9, "Spades"), card(2, "Clubs"), card(13, "Diamonds")]
    deck = [card(r, "Clubs") for r in range(3, 9)]
    state = GameState(in_run=True, hand_size=4, discards_left=1, hand=hand, deck=deck)
    assert best_discards(state, get_target("Pair")).options[0] == []
    # Only Clubs left to draw: throwing away the 3 other cards makes a Flush
    # certain, and so does throwing away more, but that's more cards.
    hand = [card(9, "Hearts"), card(2, "Clubs"), card(13, "Clubs"), card(4, "Spades"), card(10, "Diamonds")]
    deck = [card(r, "Clubs") for r in range(5, 9)] + [card(11, "Clubs")]
    state = GameState(in_run=True, hand_size=5, discards_left=1, hand=hand, deck=deck)
    found = best_discards(state, get_target("Flush"))
    assert found.probability == 1
    assert found.options == [[0, 3, 4], [0, 1, 3, 4], [0, 2, 3, 4], [0, 1, 2, 3, 4]]  # the 2 before the King


def test_ties_in_size_keep_higher_ranks():
    # Only Stone Cards to draw: no discard can make a Pair, so they all tie.
    hand = [card(14, "Hearts"), card(3, "Spades"), card(9, "Clubs")]
    deck = [card(5, "Hearts", "m_stone")] * 3
    state = GameState(in_run=True, hand_size=3, discards_left=1, hand=hand, deck=deck)
    found = best_discards(state, get_target("Pair"))
    assert found.probability == 0
    assert found.options[:4] == [[], [1], [2], [0]]
    assert found.options[4:7] == [[1, 2], [0, 1], [0, 2]]


def test_identical_cards_are_one_option():
    hand = [card(3, "Spades"), card(3, "Spades"), card(3, "Spades"), card(14, "Hearts")]
    deck = [card(14, r) for r in ("Spades", "Clubs")] + [card(2, "Hearts")] * 3
    state = GameState(in_run=True, hand_size=4, discards_left=1, hand=hand, deck=deck)
    options = check(state, get_target("Two Pair"))
    # Any of the 3s would do: only the leftmost are offered.
    threes = [[i for i in o if i < 3] for o in options]
    assert all(t == list(range(len(t))) for t in threes)


def test_selection_limit_and_no_discards():
    rng = random.Random(8)
    state = random_state(rng, select_limit=2)
    options = check(state, get_target("Flush"))
    assert max(map(len, options)) <= 2
    # Without discards, a hand can be played to throw cards away, if another is left after it.
    assert (best_discards(replace(state, discards_left=0, hands_left=2), get_target("Flush"))
            == best_discards(state, get_target("Flush")))
    last_hand = replace(state, discards_left=0, hands_left=1)
    found = best_discards(last_hand, get_target("Flush"))
    assert found.options == [[]]
    assert found.probability == evaluate_discard(last_hand, get_target("Flush"), []).probability


def test_cancelled():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        best_discards(random_state(random.Random(0)), get_target("Flush"), cancel)


def test_the_hook_when_playing_to_throw_cards_away():
    rng = random.Random(9)
    state = random_state(rng, deck=10, blind="The Hook", discards_left=0, hands_left=2)
    for name in ("Pair", "Flush", "Three of a Kind"):
        check(state, get_target(name))


@pytest.mark.parametrize("jokers, blind, names", [
    ([], None, ["Full House", "Straight Flush", "Flush / Straight / Full House"]),
    (["j_four_fingers", "j_shortcut"], None, ["Straight Flush", "Flush / Straight / Full House"]),
    (["j_smeared", "j_four_fingers"], None, ["Straight Flush", "Flush House"]),
    ([], "The Mark", ["Pair", "Straight Flush"]),
    ([], "Cerulean Bell", ["Flush", "Full House"]),
    ([], "The Hook", ["Flush", "Two Pair"]),
])
def test_shared_work_gives_the_same_odds(jokers, blind, names):
    # The search shares the engine's work between discards (engine.Reuse), which
    # must give exactly the odds of working each one out alone. A full deck
    # with face-down cards and Wild Cards gives it many classes and patterns.
    rng = random.Random(10)
    cards = [card(r, s, "m_wild" if (r + len(s)) % 9 == 0 else "c_base") for s in SUITS for r in range(2, 15)]
    rng.shuffle(cards)
    hand = cards[:6] + [Card(hidden=True)] * 2
    state = GameState(in_run=True, hand_size=8, hands_left=3, discards_left=0 if blind == "The Hook" else 2,
                      hand=hand, deck=cards[8:], hidden_pool=cards[6:8], blind=blind,
                      jokers=[Joker(j) for j in jokers])
    if blind == "Cerulean Bell":
        state = replace(state, hand=[replace(c, forced=i == 1) for i, c in enumerate(hand)])
    discards = [[]] + [list(d) for n in (1, 2) for d in combinations(range(8), n)]
    for name in names:
        target = combine([get_target(n) for n in name.split(" / ")])
        reuse = Reuse(state.hand)
        for d in discards:
            alone = evaluate_discard(state, target, d).probability
            assert evaluate_discard(state, target, d, reuse=reuse).probability == alone, (name, d)

"""Best-discard search, checked against trying every discard."""

from __future__ import annotations

import random
import threading
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from itertools import combinations

import pytest

from calculatro.cards import CardQuery, parse_query
from calculatro.engine import Cancelled, Reuse, can_miss, evaluate_cards, evaluate_discard
from calculatro.hands import combine, get_target
from calculatro.search import Progress, _identity, best_card_discards, best_discards, estimated_best
from calculatro.state import SUITS, Card, GameState, Joker
from test_engine import card


def brute_force(state: GameState, target, evaluate=evaluate_discard) -> tuple[Fraction, set]:
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
    odds = [(evaluate(state, target, d).probability, d) for d in discards]
    best = max(p for p, _ in odds)
    return best, {cards_of(state, d) for p, d in odds if p == best}


def cards_of(state: GameState, discard) -> frozenset:
    return frozenset(Counter(_identity(state.hand[i]) for i in discard).items())


def check(state: GameState, target) -> list[list[int]]:
    """Checks the search for a target hand, or a card query, against brute_force."""
    if isinstance(target, CardQuery):
        found = best_card_discards(state, target)
        best, tied = brute_force(state, target, evaluate_cards)
    else:
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
                        "unseen": cards[hand:hand + deck], **kw})


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
    state = GameState(in_run=True, hand_size=8, discards_left=1, hand=hand, unseen=deck)
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
        s = replace(state, hand=hand, unseen=state.unseen + hidden, blind=blind)
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
    state = GameState(in_run=True, hand_size=4, discards_left=1, hand=hand, unseen=deck)
    assert best_discards(state, get_target("Pair")).options[0] == []
    # Only Clubs left to draw: throwing away the 3 other cards makes a Flush
    # certain, and so does throwing away more, but that's more cards.
    hand = [card(9, "Hearts"), card(2, "Clubs"), card(13, "Clubs"), card(4, "Spades"), card(10, "Diamonds")]
    deck = [card(r, "Clubs") for r in range(5, 9)] + [card(11, "Clubs")]
    state = GameState(in_run=True, hand_size=5, discards_left=1, hand=hand, unseen=deck)
    found = best_discards(state, get_target("Flush"))
    assert found.probability == 1
    assert found.options == [[0, 3, 4], [0, 1, 3, 4], [0, 2, 3, 4], [0, 1, 2, 3, 4]]  # the 2 before the King


def test_ties_in_size_keep_higher_ranks():
    # Only Stone Cards to draw: no discard can make a Pair, so they all tie.
    hand = [card(14, "Hearts"), card(3, "Spades"), card(9, "Clubs")]
    deck = [card(5, "Hearts", "m_stone")] * 3
    state = GameState(in_run=True, hand_size=3, discards_left=1, hand=hand, unseen=deck)
    found = best_discards(state, get_target("Pair"))
    assert found.probability == 0
    assert found.options[:4] == [[], [1], [2], [0]]
    assert found.options[4:7] == [[1, 2], [0, 1], [0, 2]]


def test_identical_cards_are_one_option():
    hand = [card(3, "Spades"), card(3, "Spades"), card(3, "Spades"), card(14, "Hearts")]
    deck = [card(14, r) for r in ("Spades", "Clubs")] + [card(2, "Hearts")] * 3
    state = GameState(in_run=True, hand_size=4, discards_left=1, hand=hand, unseen=deck)
    options = check(state, get_target("Two Pair"))
    # Any of the 3s would do: only the leftmost are offered.
    threes = [[i for i in o if i < 3] for o in options]
    assert all(t == list(range(len(t))) for t in threes)


def test_stones_are_one_option():
    # Stones are exported without rank or suit, as the player sees them, so
    # two of them are the same card: throwing either is one option.
    stone = Card(enhancement="m_stone")
    hand = [stone, stone, card(14, "Spades"), card(13, "Diamonds")]
    state = GameState(in_run=True, hand_size=4, discards_left=1, hand=hand, unseen=[stone] * 4)
    options = check(state, get_target("Pair"))
    assert options[:4] == [[], [0], [3], [2]]


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


@pytest.mark.parametrize("jokers", [[], ["j_four_fingers", "j_shortcut"], ["j_smeared"]])
@pytest.mark.parametrize("seed", range(6))
def test_every_discard_tied_at_certain_odds_is_found(seed, jokers):
    # A pair in hand makes the best odds 1, so draws that miss rule discards out
    # (can_miss), which must never lose a tied one.
    state = random_state(random.Random(seed), hand=9, deck=12, jokers=[Joker(j) for j in jokers])
    check(state, combine([get_target(n) for n in ("Pair", "Flush", "Straight")]))
    check(state, get_target("Pair"))


def test_certain_only_with_several_hands_together():
    # The draw is one of two cards, one making a Flush and the other a Straight.
    hand = [card(10, "Spades"), card(11, "Spades"), card(12, "Spades"), card(13, "Spades"), card(2, "Hearts")]
    state = GameState(in_run=True, hand_size=5, discards_left=1, hand=hand,
                      unseen=[card(5, "Spades"), card(14, "Diamonds")])
    assert best_discards(state, get_target("Flush")).probability == Fraction(1, 2)
    assert best_discards(state, get_target("Straight")).probability == Fraction(1, 2)
    assert check(state, combine([get_target("Flush"), get_target("Straight")])) == [[4]]


@pytest.mark.parametrize("blind, discards, kw", [
    (None, 3, {}),
    (None, 0, {"dna_copies": 2}),
    ("The Serpent", 3, {}),
    ("The House", 3, {}),
    ("The Mark", 3, {}),
    ("Cerulean Bell", 3, {}),
    ("The Hook", 0, {}),
    ("Crimson Heart", 0, {"jokers": [Joker("j_four_fingers", debuff=True), Joker("j_shortcut")]}),
])
def test_a_miss_means_odds_below_1(blind, discards, kw):
    misses = 0
    for seed in range(8):
        rng = random.Random(seed)
        state = replace(random_state(rng, deck=10, blind=blind, **kw), discards_left=discards, hands_left=3)
        if blind in ("The House", "The Mark"):  # the last two cards go face down
            state = replace(state, unseen=[*state.unseen, *state.hand[-2:]],
                            hand=[*state.hand[:-2], Card(hidden=True), Card(hidden=True)])
        for target in (get_target("Pair"), get_target("Straight"),
                       combine([get_target("Flush"), get_target("Three of a Kind")])):
            for discard in ([], [0], [1, 2], [0, 3, 5], [0, 1, 2, 3, 4]):
                if can_miss(state, target, discard, rng):
                    misses += 1
                    assert evaluate_discard(state, target, discard).probability < 1, (seed, target, discard)
                    # The Hook only takes cards after a play, not when keeping the hand.
                    assert blind != "The Hook" or not discard
    # It gives up on the bosses that don't draw uniformly, and finds misses otherwise.
    assert (misses == 0) == (blind in ("The Mark", "Cerulean Bell"))


def test_no_miss_when_the_kept_cards_hold_the_target():
    hand = [card(5, "Spades"), card(5, "Hearts"), card(9, "Clubs")]
    state = GameState(in_run=True, hand_size=3, discards_left=1, hand=hand, unseen=[card(2, "Clubs"), card(3, "Clubs")])
    assert not can_miss(state, get_target("Pair"), [2], random.Random(0))
    assert can_miss(state, get_target("Pair"), [0], random.Random(0))


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


@pytest.mark.parametrize("jokers", [
    [Joker("j_four_fingers", debuff=True), Joker("j_joker"), Joker("j_shortcut")],
    # Smeared may be lost, telling apart cards that are alike now.
    [Joker("j_smeared"), Joker("j_four_fingers", debuff=True), Joker("j_joker")],
])
def test_crimson_heart_when_playing_to_throw_cards_away(jokers):
    # Cards alike under the current rules may not be under those after the play.
    state = random_state(random.Random(11), deck=10, blind="Crimson Heart", discards_left=0, hands_left=2,
                         jokers=jokers)
    for name in ("Straight", "Flush", "Straight Flush"):
        check(state, get_target(name))
    check(state, combine([get_target(n) for n in ("Pair", "Flush", "Straight")]))


def test_crimson_heart_card_queries():
    # Pareidolia may be lost: every card is a face card now, but not after the play.
    state = enhanced_state(random.Random(12), blind="Crimson Heart", discards_left=0, hands_left=2,
                           jokers=[Joker("j_pareidolia"), Joker("j_joker", debuff=True), Joker("j_smeared")])
    for text in ("2+ face cards", "at most 1 heart", "between 1 and 2 face cards of spades"):
        check(state, parse_query(text))


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
                      hand=hand, unseen=cards[6:], blind=blind,
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


QUERIES = ["queen", "2+ hearts", "3+ face cards", "at most 1 heart", "exactly 2 spades",
           "between 1 and 2 even", "no face cards", "lucky", "not sealed"]


def enhanced_state(rng: random.Random, **kw) -> GameState:
    """A random state whose cards are sometimes enhanced, sealed or debuffed."""
    def vary(c: Card) -> Card:
        return replace(c, enhancement=rng.choice(["c_base"] * 4 + ["m_lucky", "m_wild", "m_stone"]),
                       seal=rng.choice([None] * 3 + ["Red"]), debuff=rng.random() < 0.1)
    state = random_state(rng, deck=10, **kw)
    return replace(state, hand=[vary(c) for c in state.hand], unseen=[vary(c) for c in state.unseen])


@pytest.mark.parametrize("seed", range(4))
def test_card_queries_match_trying_every_discard(seed):
    state = enhanced_state(random.Random(seed))
    for text in QUERIES:
        check(state, parse_query(text))


def test_card_queries_keep_matching_cards():
    # Throwing a queen away for one draw can only lose one: keep them, throw the rest.
    hand = [card(12, "Hearts"), card(2, "Clubs"), card(12, "Spades"), card(3, "Clubs"), card(4, "Clubs")]
    deck = [card(12, "Clubs")] + [card(r, "Diamonds") for r in range(5, 12)]
    state = GameState(in_run=True, hand_size=5, discards_left=1, hand=hand, unseen=deck)
    assert check(state, parse_query("3+ queens")) == [[1, 3, 4]]
    # At most one: throwing both away is certain, as only one is left to draw.
    assert check(state, parse_query("at most 1 queen"))[0] == [0, 2]


@pytest.mark.parametrize("blind", ["The House", "The Mark", "Cerulean Bell", "The Serpent"])
def test_card_queries_with_bosses(blind):
    rng = random.Random(blind)
    state = enhanced_state(rng, blind=blind)
    if blind in ("The House", "The Mark"):
        state = replace(state, hand=state.hand[:6] + [Card(hidden=True)] * 2,
                        unseen=state.unseen + [card(11, "Spades"), card(13, "Hearts")])
    if blind == "Cerulean Bell":
        state = replace(state, hand=[replace(c, forced=i == 2) for i, c in enumerate(state.hand)])
    for text in ("king", "2+ face cards", "at most 1 heart", "between 1 and 2 hearts"):
        check(state, parse_query(text))


def test_card_queries_with_the_hook():
    state = enhanced_state(random.Random(9), blind="The Hook", discards_left=0, hands_left=2)
    for text in ("queen", "2+ hearts", "at most 1 face card"):
        check(state, parse_query(text))


def test_card_query_search_cancelled():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        best_card_discards(random_state(random.Random(0)), parse_query("queen"), cancel)


@pytest.mark.parametrize("copies", [1, 2])
@pytest.mark.parametrize("blind", [None, "The Hook", "The Serpent", "Cerulean Bell"])
def test_dna_when_playing_a_lone_card(copies, blind):
    # A card thrown away with DNA comes back, which isn't like a discard for the
    # search's groups and relabelling.
    state = random_state(random.Random(21), deck=10, blind=blind, discards_left=0, hands_left=2, dna_copies=copies)
    if blind == "Cerulean Bell":
        state = replace(state, hand=[replace(c, forced=i == 0) for i, c in enumerate(state.hand)])
    for name in ("Pair", "Two Pair", "Three of a Kind", "Flush", "Full House"):
        check(state, get_target(name))
    check(state, combine([get_target(n) for n in ("Three of a Kind", "Flush")]))
    for text in ("2+ spades", "at most 1 heart"):
        check(state, parse_query(text))


def test_dna_never_recommends_playing_a_lone_card_for_nothing():
    # With no Pair in hand, a lone card played comes back, so it can't make one.
    hand = [card(3, "Hearts"), card(5, "Clubs"), card(7, "Spades"), card(9, "Hearts"), card(13, "Diamonds")]
    deck = [card(3, "Spades"), card(5, "Hearts"), card(7, "Clubs"), card(9, "Clubs"), card(13, "Spades")]
    state = GameState(in_run=True, hand_size=5, discards_left=0, hands_left=2, dna_copies=1, hand=hand, unseen=deck)
    options = check(state, get_target("Pair"))
    assert all(len(o) != 1 for o in options)


def test_dna_face_down_cards():
    rng = random.Random(22)
    state = random_state(rng, deck=10, discards_left=0, hands_left=2, dna_copies=1)
    hidden = state.hand[-2:]
    state = replace(state, hand=state.hand[:-2] + [Card(hidden=True)] * 2, unseen=[*state.unseen, *hidden])
    for name in ("Pair", "Flush"):
        check(state, get_target(name))


# --- A search given up on (docs/probability-model.md#best-discard) -------------------


class StopAfter:
    """A `cancel` that's set after it has been asked `n` times: a search stopped part of the way."""

    def __init__(self, n: int):
        self.n = n

    def is_set(self) -> bool:
        self.n -= 1
        return self.n < 0


def given_up(search, state, target, asked: int) -> Progress:
    progress = Progress()
    with pytest.raises(Cancelled):
        search(state, target, StopAfter(asked), progress)
    return progress


@pytest.mark.parametrize("seed", range(4))
def test_a_search_given_up_answers_from_estimates(seed):
    """Stopped part of the way, the search's estimates find a discard as good as the best, or nearly (within a
    point: estimates can't tell much closer), whose odds are in the likely range it gives."""
    rng = random.Random(seed)
    state = random_state(rng, jokers=[Joker("j_four_fingers"), Joker("j_shortcut")] if seed % 2 else [])
    targets = [get_target(n) for n in ("Straight", "Flush", "Full House")]
    targets.append(combine(targets[:2]))
    for target in targets:
        exact = best_discards(state, target)
        progress = given_up(best_discards, state, target, rng.randint(1, 30))
        found = estimated_best(progress, None, 1.0)
        assert found.estimated is not None and found.estimated.draws
        p = evaluate_discard(state, target, found.options[0]).probability
        assert p >= exact.probability - Fraction(1, 100), target.name
        assert found.estimated.low <= p <= found.estimated.high, target.name
        assert {cards_of(state, o) for o in found.options} == {cards_of(state, found.options[0])} | {
            cards_of(state, o) for o in found.options}  # one choice's options, or ties found exactly


def test_a_search_records_what_it_found_before_it_was_given_up():
    """Its choices, their exact odds as they're found, and for several hands each one's bounds. (A full search of
    Straight here asks whether to stop 1401 times, and of the two hands 1444, mostly for the bounds.)"""
    state = random_state(random.Random(5))
    target = get_target("Straight")
    progress = given_up(best_discards, state, target, 700)
    assert progress.choices and len(progress.discards) == len(progress.choices)
    assert 0 < len(progress.exact) < len(progress.choices)
    assert all(evaluate_discard(state, target, progress.discards[j]).probability == p
               for j, p in progress.exact.items())
    target = combine([get_target("Flush"), get_target("Straight")])
    progress = given_up(best_discards, state, target, 700)
    known = [j for j, high in enumerate(progress.highs) if high < 1 or progress.lows[j] > 0]
    assert 0 < len(known) < len(progress.choices)
    assert all(progress.lows[j] <= evaluate_discard(state, target, progress.discards[j]).probability
               <= progress.highs[j] for j in known)


def test_a_search_given_up_before_its_choices_has_no_answer():
    assert estimated_best(Progress()) is None


def test_a_card_query_search_given_up_answers_exactly():
    """Without face-down cards in the hand's sorts, a card query's odds are quick: the answer is exact, unmarked."""
    state = random_state(random.Random(2))
    query = parse_query("at least 2 hearts")
    found = estimated_best(given_up(best_card_discards, state, query, 3), None, 1.0)
    assert found == best_card_discards(state, query)

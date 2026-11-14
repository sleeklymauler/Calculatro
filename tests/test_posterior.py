"""The odds with what the hand's sorts show of its face-down cards, checked
against dealing every way (`state_oracle`, docs/probability-model.md#face-down-cards-in-the-hands-sorts)."""

import random
import threading
from collections import Counter
from dataclasses import replace
from fractions import Fraction

import pytest

from calculatro import engine
from calculatro.cards import parse_query
from calculatro.engine import Cancelled, evaluate_cards, evaluate_discard, flipped_by_mark, posterior
from calculatro.hands import combine, get_target
from calculatro.posterior import deals, reference
from calculatro.search import best_card_discards, best_discards
from calculatro.state import SUITS, Card, GameState
from game_order import nominal
from poker import card
from rounds import Played, Round, played, random_round, round_discards
from search_oracle import check as check_search
from state_oracle import (
    STONE,
    Case,
    check,
    face_for_the_mark,
    oracle,
    ours_by_cards,
    posterior_by_cards,
    printed,
    query_holds,
    target_holds,
)

# Estimates stop at a deadline: by a clock that moves with the work done, so they are the same on any machine.
pytestmark = pytest.mark.usefixtures("fake_clock")

# --- The plan's examples -------------------------------------------------------------

def clubs(*ranks: int) -> list[Card]:
    return [card(r, "Clubs") for r in ranks]


def worked_example() -> Case:
    """7♣ 8♣ 9♣ unseen, two of them face down between a 10 and a 6 by rank."""
    seven, eight, nine = clubs(7, 8, 9)
    return Case(hand=["ten", "left", "right", "six"],
                cards={"ten": card(10, "Spades"), "six": card(6, "Hearts"), "left": nine, "right": seven},
                down={"left", "right"}, views={"desc": ["ten", "left", "right", "six"]}, deck=[eight])


def test_the_worked_example():
    case = worked_example()
    state = case.state()
    nine = parse_query("9")
    assert evaluate_cards(state, nine, [2]).probability == 1  # discard the right one
    assert evaluate_cards(state, nine, [1]).probability == Fraction(1, 3)  # the left one
    assert evaluate_cards(state, nine, []).probability == Fraction(2, 3)
    check(case, [[], [1], [2], [1, 2]], queries=[nine, parse_query("at least 1 8")], targets=[get_target("Pair")])


def test_the_hint_example():
    # The hint's lists as independent windows would say 5/7 and 2/7.
    case = Case(hand=["jack", "left", "right", "six"],
                cards={"jack": card(11, "Spades"), "six": card(6, "Hearts"), "left": card(10, "Clubs"),
                       "right": card(8, "Clubs")},
                down={"left", "right"}, views={"desc": ["jack", "left", "right", "six"]}, deck=clubs(7, 9))
    state = case.state()
    ten = parse_query("10")
    assert evaluate_cards(state, ten, [2]).probability == Fraction(3, 4)
    assert evaluate_cards(state, ten, [1]).probability == Fraction(1, 4)
    check(case, [[1], [2]], queries=[ten])


def test_a_card_between_two_spades_by_suit_makes_the_flush():
    spades = [card(r, "Spades") for r in (14, 12, 9, 5)]
    case = Case(hand=["A", "Q", "down", "9", "5"],
                cards={"A": spades[0], "Q": spades[1], "9": spades[2], "5": spades[3], "down": card(10, "Spades")},
                down={"down"}, views={"suit desc": ["A", "Q", "down", "9", "5"]},
                deck=[card(r, s) for r in (2, 3, 4) for s in ("Hearts", "Clubs")] + [card(7, "Spades")])
    state = case.state()
    assert evaluate_discard(state, get_target("Flush"), []).probability == 1
    # Today's odds, which don't know where it is: 2 of the 8 unseen cards are spades.
    plain = engine.with_usable_evidence(state)
    assert plain is state
    assert engine._evaluate_discard(state, get_target("Flush"), [], None, None).probability == Fraction(2, 8)
    # So no draw can miss: can_miss mustn't find one the evidence rules out.
    assert not engine.can_miss(state, get_target("Flush"), [], random.Random(0))
    check(case, [[], [0], [2]], targets=[get_target("Flush"), get_target("Pair")])


def test_windows_weigh_the_draw():
    # Two runs of face-down cards, each between face-up cards that none of them can
    # tie with: each is a uniformly random set of the cards between (posterior.windows),
    # and the engine weighs the draw by how many of each it has, kept or gone.
    case = Case(hand=["K", "high1", "high2", "9", "low", "3"],
                cards={"K": card(13, "Spades"), "9": card(9, "Hearts"), "3": card(3, "Diamonds"),
                       "high1": card(12, "Clubs"), "high2": card(10, "Hearts"), "low": card(6, "Clubs")},
                down={"high1", "high2", "low"}, views={"desc": ["K", "high1", "high2", "9", "low", "3"]},
                deck=[card(11, "Hearts"), card(10, "Clubs"), card(7, "Hearts"), card(5, "Clubs"), card(2, "Hearts")])
    state = case.state()
    found = engine.windows(state.observations, state.observed, state.unseen, lambda c: True)
    assert sorted(w.size for w in found) == [1, 2]
    classes = engine._rule_classes(state, [], engine._counts_towards(get_target("Straight"), state))
    assert len(engine._windows_for(state, [], classes, None)[0]) == 2  # both kept
    assert len(engine._windows_for(state, [0, 4], classes, None)[0]) == 2  # one kept, one gone
    # The run of two split, kept and gone: split again by its lower card, the higher one is a window
    # of the cards above it (engine._split), and the other run is a window as it was.
    windows, splits = engine._windows_for(state, [1], classes, None)
    assert [w.size for _, w in windows] == [1]
    assert [len(ways) for _, ways in splits] == [3]  # J, 10 or 7 is the lower one: Q and 10 left above
    discards = [[], [0], [4], [0, 4], [1], [0, 3, 5]]
    check(case, discards, [get_target("Straight"), get_target("Pair"), get_target("Flush")], QUERIES)


# --- Against dealing every way --------------------------------------------------------

def random_case(rng: random.Random, ranks=range(7, 12), copies=False, stones=False, wilds=False,
                hidden_extra=False, gone=False, discarded=False, both=None, latent=(1, 3), face_up=(1, 3),
                down_discarded=0, **kw) -> Case:
    suits = rng.sample(SUITS, 2)
    pool = [card(r, s, "m_wild" if wilds and rng.random() < 0.2 else "c_base") for r in ranks for s in suits]
    rng.shuffle(pool)
    pool = pool[:rng.randint(6, 8)]
    if copies:
        pool += [pool[0], rng.choice(pool)]
    if stones:
        pool += [STONE] * rng.randint(1, 2)
    rng.shuffle(pool)
    cards: dict[str, Card] = {}

    def take(prefix: str, n: int) -> list[str]:
        names = [f"{prefix}{i}" for i in range(n)]
        for name in names:
            cards[name] = pool.pop()
        return names

    up = take("U", rng.randint(*face_up))
    down = take("L", rng.randint(*latent))
    # Face-down cards discarded after some of the sorts: in the first, and each other with even odds.
    after = take("D", down_discarded)
    extra = take("H", 1) if hidden_extra else []
    played = take("G", 1) if gone else []
    thrown = [pool.pop()] if discarded else []
    modes = ["desc", "suit desc"] if both else [rng.choice(["desc", "suit desc"])] if both is False else \
        rng.choice([["desc"], ["suit desc"], ["desc", "suit desc"], ["suit desc", "desc"]])
    # One hidden order of the cards alike in print, the same for every sort.
    tiebreak = {n: rng.randrange(100) for n in cards}
    views = {}
    for k, mode in enumerate(modes):
        shown = up + down + (played if k == 0 else []) + [n for n in after if k == 0 or rng.random() < 0.5]
        views[mode] = sorted(shown, key=lambda n: nominal(printed(cards[n]), mode, tiebreak[n]), reverse=True)
    hand = [n for n in views[modes[-1]] if n not in played and n not in after] + extra
    return Case(hand=hand, cards=cards, down=set(down + extra + after), views=dict(sorted(views.items())),
                deck=pool, discarded=thrown, kw=kw)


TARGETS = [get_target("Pair"), get_target("Two Pair"), get_target("Three of a Kind")]
QUERIES = [parse_query(q) for q in ("9", "at least 2 hearts", "exactly 1 face card", "between 1 and 2 even")]


def some_discards(rng: random.Random, case: Case) -> list[list[int]]:
    n = len(case.hand)
    latent = [i for i, name in enumerate(case.hand) if name in case.down]
    picks = [[], [rng.choice(latent)], sorted(rng.sample(range(n), min(2, n)))]
    return [list(d) for d in dict.fromkeys(tuple(d) for d in picks)]


@pytest.mark.parametrize("seed", range(12))
def test_against_dealing_every_way(seed):
    rng = random.Random(seed)
    case = random_case(rng)
    check(case, some_discards(rng, case), TARGETS, QUERIES)


@pytest.mark.parametrize("seed", range(8))
def test_copies_and_stones_in_both_sorts(seed):
    # Cards alike in print tie: their hidden order is random, but one order for both sorts.
    rng = random.Random(100 + seed)
    case = random_case(rng, copies=True, stones=True, both=True, latent=(2, 3))
    check(case, some_discards(rng, case), TARGETS[:2], QUERIES[:2])


@pytest.mark.parametrize("seed", range(4))
def test_wild_cards_and_smeared(seed):
    rng = random.Random(200 + seed)
    jokers = [{"key": "j_smeared"}] if seed % 2 else []
    case = random_case(rng, wilds=True, ranks=range(7, 10), jokers=jokers)
    flush4 = [{"key": "j_four_fingers"}, *jokers]
    four_fingers = Case(**{**case.__dict__, "kw": {"jokers": flush4}})
    check(four_fingers, some_discards(rng, case), [get_target("Flush"), get_target("Pair")], [QUERIES[1]])


@pytest.mark.parametrize("seed", range(4))
def test_straights_with_four_fingers_and_shortcut(seed):
    # Branches merge by which requirements a card counts towards (_counts_towards), which for
    # these patterns tells most cards apart: checked against the reference and the oracle.
    rng = random.Random(250 + seed)
    case = random_case(rng, ranks=range(6, 12), jokers=[{"key": "j_four_fingers"}, {"key": "j_shortcut"}])
    check(case, some_discards(rng, case), [get_target("Straight"), get_target("Straight Flush"),
                                           get_target("Flush")])


@pytest.mark.parametrize("seed", range(4))
def test_cards_without_evidence_gone_and_discarded(seed):
    # A face-down card in no sort, a face-up card that was played after one, and
    # one discarded face down: all part of the deal, only the sorts' cards narrowed.
    rng = random.Random(300 + seed)
    case = random_case(rng, hidden_extra=True, gone=True, discarded=True, latent=(1, 2))
    check(case, some_discards(rng, case), TARGETS[:1], QUERIES[:2])


def test_the_house_all_face_down():
    # Keeping them all or throwing them all away doesn't need the sort (_says_nothing_here).
    rng = random.Random(400)
    case = random_case(rng, face_up=(0, 0), latent=(4, 4), both=False)
    assert not set(case.hand) - case.down
    check(case, [[], [0], [3], [0, 1], [0, 1, 2, 3]], TARGETS[:2], QUERIES[:1])
    case.views = {mode: view for mode in ("desc", "suit desc") for view in case.views.values()}
    check(case, [[], [0, 1, 2, 3]], TARGETS[:1], QUERIES[:1])  # two sorts do say something


def test_the_house_first_hand_keeping_it_is_todays_odds(monkeypatch):
    # Eight face-down cards sorted are too many deals to go through, and needn't be.
    deck = [card(r, s) for s in SUITS for r in range(2, 15)]
    names = [f"L{i}" for i in range(8)]
    cards = dict(zip(names, sorted(deck[:8], key=lambda c: nominal(printed(c), "desc"), reverse=True)))
    case = Case(hand=names, cards=cards, down=set(names), views={"desc": names}, deck=deck[8:])
    state = case.state()
    monkeypatch.setattr(engine, "summed", None)  # never called
    for discard in ([], list(range(8))):
        assert (evaluate_discard(state, TARGETS[0], discard).probability
                == engine._evaluate_discard(state, TARGETS[0], discard, None, None).probability)
        assert evaluate_cards(state, QUERIES[0], discard).probability == engine._evaluate_cards(
            state, QUERIES[0], discard).probability


# --- Face-down cards discarded after the sorts ---------------------------------------

@pytest.mark.parametrize("seed", range(10))
def test_cards_discarded_face_down_after_the_sorts(seed):
    # Still where they were seen: they bound the cards around them, and say
    # what's left to draw.
    rng = random.Random(1000 + seed)
    case = random_case(rng, down_discarded=rng.randint(1, 2), latent=(1, 2), both=True if seed % 2 else None)
    assert case.gone
    check(case, some_discards(rng, case), TARGETS[:2], QUERIES)


def only_one_discarded() -> Case:
    """A card discarded face down from between the 9♠ and the 7♥: the 9♦ or the 8♣."""
    return Case(hand=["nine", "seven"], cards={"nine": card(9, "Spades"), "seven": card(7, "Hearts"),
                                              "D": card(8, "Clubs")},
                down={"D"}, views={"desc": ["nine", "D", "seven"]},
                deck=[card(9, "Diamonds"), card(7, "Diamonds"), card(3, "Diamonds")])


def test_a_discarded_card_alone_says_what_is_left_to_draw():
    case = only_one_discarded()
    state = case.state()
    assert all(c.latent is None for c in state.hand) and state.discarded_hidden == 1
    pair = get_target("Pair")
    # The 7♦ is always left to draw, the 9♦ only if the 8♣ was discarded.
    assert evaluate_discard(state, pair, [0]).probability == Fraction(1, 3)
    assert evaluate_discard(state, pair, [1]).probability == Fraction(1, 6)
    # Without the sort they're alike: either is left to draw 3 times in 4, then drawn 1 time in 3.
    plain = engine._evaluate_discard(state, pair, [1], None, None).probability
    assert plain == engine._evaluate_discard(state, pair, [0], None, None).probability == Fraction(1, 4)
    check(case, [[], [0], [1], [0, 1]], [pair], [parse_query("9"), parse_query("at least 1 7")])


def test_the_search_doesnt_relabel_with_a_discarded_card_in_a_sort():
    # Pair can't tell a 9 from a 7, and as many of each are unseen, but they aren't
    # as likely to be left to draw: so only throwing away the 9♠ is best.
    state = only_one_discarded().state()
    pair = get_target("Pair")
    assert check_search(state, pair)[0] == [0]
    found = best_discards(state, pair)
    assert found.options == [[0]] and found.probability == Fraction(1, 3)


def test_the_house_after_discarding_some_face_down():
    # Its one sort, with some of its cards since discarded face down: those kept
    # are the higher or the lower ones, so even keeping the hand needs the deals.
    rng = random.Random(410)
    case = random_case(rng, face_up=(0, 0), latent=(3, 3), down_discarded=2, both=False)
    assert len(case.hand) == 3 and len(case.gone) == 2
    state = case.state()
    assert not engine._says_nothing_here(state, []) and not engine._says_nothing_here(state, [0])
    check(case, [[], [0], [0, 1, 2]], TARGETS[:2], QUERIES[:2])
    # Throwing away the rest too leaves them all gone: today's odds.
    assert engine._says_nothing_here(state, [0, 1, 2])


def test_dna_copies_of_the_higher_of_two_is_not_keeping_it():
    # Two face-down cards sorted, the left one played alone and copied twice: the
    # copies are of the higher card, so it isn't like keeping both.
    case = Case(hand=["L", "R"], cards={"L": card(9, "Clubs"), "R": card(7, "Clubs")}, down={"L", "R"},
                views={"desc": ["L", "R"]}, deck=[card(8, "Clubs"), card(2, "Hearts"), card(3, "Hearts")],
                kw={"discards_left": 0, "hands_left": 2, "dna_copies": 2})
    state = case.state()
    assert not engine._says_nothing_here(state, [0])
    nines = parse_query("at least 2 9")
    # The left one is the 9 in 4 of the 10 pairs, the right one in none.
    assert evaluate_cards(state, nines, [0]).probability == Fraction(4, 10)
    assert evaluate_cards(state, nines, [1]).probability == 0
    check(case, [[0], [1]], [get_target("Three of a Kind")], [nines])


def test_dna_one_copy_with_nothing_drawn():
    # One copy of the left card fills the hand, so nothing is drawn: what was played
    # is still in the final hand, so its branches mustn't merge like those of a card gone.
    case = Case(hand=["L", "R"], cards={"L": card(9, "Clubs"), "R": card(7, "Clubs")}, down={"L", "R"},
                views={"desc": ["L", "R"]}, deck=[card(8, "Clubs"), card(2, "Hearts"), card(3, "Hearts")],
                kw={"discards_left": 0, "hands_left": 2, "dna_copies": 1})
    state = case.state()
    assert engine._discard(state, [0]).draws == 0
    nines = parse_query("at least 1 9")
    assert evaluate_cards(state, nines, [0]).probability == Fraction(4, 10)
    check(case, [[0], [1]], [get_target("Pair")], [nines])


def marked_case(rng: random.Random, **kw) -> Case:
    """A random_case under The Mark, without Pareidolia: every face-down card a face card, and marked."""
    while True:  # The Mark only flips face cards
        case = random_case(rng, ranks=range(9, 14), blind="The Mark", **kw)
        if all(face_for_the_mark(case.cards[n]) for n in case.down):
            case.marked = set(case.down)
            return case


@pytest.mark.parametrize("seed", range(2))
def test_the_mark_with_a_card_discarded_face_down(seed):
    rng = random.Random(520 + seed)
    case = marked_case(rng, down_discarded=1, latent=(1, 2))
    check(case, some_discards(rng, case), TARGETS[:1], QUERIES[:3])


@pytest.mark.parametrize("seed", range(4))
def test_the_search_with_cards_discarded_face_down(seed):
    rng = random.Random(950 + seed)
    case = random_case(rng, down_discarded=1, latent=(1, 2), face_up=(2, 3))
    state = case.state()
    for target in (get_target("Pair"), get_target("Three of a Kind"), QUERIES[0], QUERIES[1]):
        check_search(state, target)


@pytest.mark.parametrize("seed", range(4))
def test_the_mark(seed):
    rng = random.Random(500 + seed)
    jokers = [{"key": "j_pareidolia"}] if seed == 3 else []  # gained after the draw: the cards are still marked
    case = marked_case(rng, jokers=jokers)
    assert case.state().marked == len(case.down)
    check(case, some_discards(rng, case), TARGETS[:1], QUERIES[:3])


def pareidolia_sold(rng: random.Random, need, **kw) -> Case:
    """A random_case under The Mark where Pareidolia was held for some draws: the face-down cards
    drawn without it face cards and marked, the others any card. Retried until `need(case)`."""
    while True:
        case = random_case(rng, ranks=range(9, 15), blind="The Mark", **kw)
        names = [*case.down, *(f"discarded {i}" for i in range(len(case.discarded)))]
        what = {**case.cards, **{f"discarded {i}": c for i, c in enumerate(case.discarded)}}
        case.marked = {n for n in names if face_for_the_mark(what[n]) and rng.random() < 0.6}
        if need(case):
            return case


def latent_unmarked(case: Case) -> bool:
    return any(n not in case.marked for n in case.first_seen() if n in case.down)


def marked_in_no_sort(case: Case) -> bool:
    return any(n not in case.first_seen() for n in case.marked)


@pytest.mark.parametrize("seed", range(6))
def test_pareidolia_sold_under_the_mark(seed):
    # Unmarked cards in the sorts take face cards that the marked ones in none can't be: the
    # posterior weighs each deal by how many ways those are still face cards.
    rng = random.Random(540 + seed)
    case = pareidolia_sold(rng, lambda c: latent_unmarked(c) and marked_in_no_sort(c), hidden_extra=True,
                           discarded=seed % 2 == 1, down_discarded=seed % 3 == 2, latent=(1, 2), face_up=(1, 2))
    check(case, some_discards(rng, case), TARGETS[:2], QUERIES[:3])


@pytest.mark.parametrize("seed", range(3))
def test_pareidolia_sold_under_the_mark_in_one_sort_of_face_down_cards(seed):
    # All face down, in one sort: which are the higher ones says which can be face cards, so it isn't
    # nothing, as it would be with every card alike (engine._says_nothing_here).
    rng = random.Random(560 + seed)
    case = pareidolia_sold(rng, lambda c: latent_unmarked(c) and c.marked, face_up=(0, 0), latent=(3, 3),
                           both=False)
    state = case.state()
    assert not engine._says_nothing_here(state, [])
    check(case, [[], list(range(len(case.hand)))], TARGETS[:1], QUERIES[:3])


@pytest.mark.parametrize("sorted_", [True, False])
@pytest.mark.parametrize("kw", [
    {},
    {"discards_left": 0, "hands_left": 2, "dna_copies": 1},
    {"discards_left": 0, "hands_left": 2, "dna_copies": 2},
], ids=["plain", "dna", "dna2"])
def test_pareidolia_sold_under_the_mark_with_dna(kw, sorted_):
    # A lone face-down card played as the first hand comes back: DNA's copies are of a card
    # marked or not. Without sorts, only the draw is weighed (engine._mark_split).
    rng = random.Random(f"{kw} {sorted_}")
    case = pareidolia_sold(rng, lambda c: c.marked and set(c.down) - c.marked, hidden_extra=True, discarded=True,
                           latent=(1, 2), **kw)
    if not sorted_:
        case.views = {}
    hand_down = [i for i, n in enumerate(case.hand) if n in case.down]
    discards = [[], [hand_down[0]], [hand_down[-1]], [0, hand_down[-1]]]
    check(case, [d for d in dict.fromkeys(map(tuple, discards))], TARGETS[:2], QUERIES[:2])


@pytest.mark.parametrize("blind, kw", [
    ("The Hook", {"discards_left": 0, "hands_left": 2}),
    ("The Serpent", {}),
    ("Cerulean Bell", {}),
    (None, {"discards_left": 0, "hands_left": 2, "dna_copies": 2}),
])
def test_bosses_and_dna(blind, kw):
    rng = random.Random(f"{blind} {kw}")
    case = random_case(rng, blind=blind, **kw)
    if blind == "Cerulean Bell":
        case.forced = next(n for n in case.hand if n not in case.down)
    discards = some_discards(rng, case)
    if blind == "Cerulean Bell":
        discards = [d if case.hand.index(case.forced) in d or not d else sorted({*d, case.hand.index(case.forced)})
                    for d in discards]
    check(case, discards, [get_target("Pair"), combine([get_target("Pair"), get_target("Straight")])], QUERIES[:2])


def test_an_exhausted_deck():
    rng = random.Random(600)
    case = random_case(rng)
    case.deck = case.deck[:1]
    check(case, [[0, 1], [len(case.hand) - 1]], TARGETS[:1], QUERIES[:1])


# --- Earlier sorts: playing out a round --------------------------------------------------
#
# The oracle above deals the unseen cards of the round's end. This one (`rounds`) starts from
# the shuffled deck: it draws the first hand every way, sorts it, throws cards away,
# draws every way and sorts again, keeping the deals whose every step looks as the
# player saw it. So it checks that conditioning on every sort seen this round, as
# `posterior.summed` does, is right however the cards got there.

def check_round(rnd: Round, truth: Played, discards, targets=(), queries=(), **kw) -> Case:
    """The posterior and the odds at the round's end with every sort kept, against
    playing out the round every way; and the merged branches against the reference."""
    case = rnd.case(truth, **kw)
    state = case.state()
    dealt = rnd.worlds(truth)
    post = posterior(state)
    if any(o.latent for o in state.observed):
        assert ours_by_cards(post) == posterior_by_cards(case, state, dealt)
        ref = reference(state.observations, state.observed, state.unseen, flipped_by_mark, (),
                        engine._slots(state, state.observed, ())[1])
        assert ours_by_cards(ref) == ours_by_cards(post)
    else:  # every face-down card played: today's odds, which must agree too
        assert post is None
    for discard in discards:
        for target in targets:
            found = evaluate_discard(state, target, discard).probability
            assert found == oracle(case, discard, target_holds(target, state), dealt), (target.name, discard)
        for query in queries:
            found = evaluate_cards(state, query, discard).probability
            assert found == oracle(case, discard, query_holds(query, state), dealt), (query.text, discard)
    return case


def the_plans_round() -> tuple[Round, list[str]]:
    """Stage 4's example: a face-down card between a 9♣ and a 7♣ by rank, kept while
    both are discarded, then between a K♦ and a 4♥ after the draw."""
    deck = {"9C": card(9, "Clubs"), "8C": card(8, "Clubs"), "7C": card(7, "Clubs"), "KD": card(13, "Diamonds"),
            "4H": card(4, "Hearts"), "8S": card(8, "Spades"), "6H": card(6, "Hearts"), "TD": card(10, "Diamonds")}
    return Round(deck, first=[False, True, False], steps=[("discard", [0, 2], [False, False])]), \
        ["9C", "8C", "7C", "KD", "4H"]


def test_the_plans_round():
    rnd, drawn = the_plans_round()
    eight = parse_query("at least 1 8")
    case = check_round(rnd, played(rnd, drawn), [[], [1]], [get_target("Pair")], [eight, parse_query("at least 1 10")])
    assert [mode for mode, _ in case.sorts] == ["desc", "desc"]
    state = case.state()
    # The earlier sort says it's an 8; the latest alone, anything between the K and the 4.
    assert evaluate_cards(state, eight, []).probability == 1
    latest = Case(**{**case.__dict__, "views": case.sorts[-1:]})
    assert evaluate_cards(latest.state(), eight, []).probability == Fraction(1, 2)


@pytest.mark.parametrize("seed", range(10))
def test_rounds_against_playing_them_out(seed):
    rng = random.Random(1100 + seed)
    rnd, drawn = random_round(rng, plays=seed % 2 == 1)
    truth = played(rnd, drawn, seed)
    check_round(rnd, truth, round_discards(rng, rnd.case(truth)), TARGETS[:2], QUERIES[:2])


@pytest.mark.parametrize("seed", range(4))
def test_rounds_with_copies_and_stones(seed):
    # Cards alike in print keep one hidden order through every sort of the round.
    rng = random.Random(1200 + seed)
    rnd, drawn = random_round(rng, copies=True, stones=seed % 2 == 0)
    truth = played(rnd, drawn, seed)
    check_round(rnd, truth, round_discards(rng, rnd.case(truth)), TARGETS[:1], QUERIES[:1])


def test_a_round_under_the_hook():
    rng = random.Random(1300)
    rnd, drawn = random_round(rng, hands=1)
    truth = played(rnd, drawn)
    check_round(rnd, truth, round_discards(rng, rnd.case(truth)), TARGETS[:1], QUERIES[:1],
                blind="The Hook", discards_left=0, hands_left=2)


# --- The best-discard search ---------------------------------------------------------

def test_the_search_throws_away_the_right_face_down_card():
    case = worked_example()
    state = case.state()
    nine = parse_query("9")
    assert check_search(state, nine)[0] == [2]  # the right one: the 9 is the left one or in the deck
    found = best_card_discards(state, nine)
    assert found.probability == 1


@pytest.mark.parametrize("seed", range(6))
def test_the_search_matches_trying_every_discard(seed):
    # Every subset, each with the odds the evidence gives: so options name hand indices.
    rng = random.Random(900 + seed)
    case = random_case(rng, copies=seed % 2 == 1, hidden_extra=seed % 3 == 0, latent=(1, 3), face_up=(2, 3))
    state = case.state()
    for target in (get_target("Pair"), get_target("Three of a Kind"), QUERIES[0], QUERIES[1]):
        check_search(state, target)


# --- Evidence set aside, and what doesn't change ----------------------------------------

def test_no_observations_is_todays_odds():
    case = worked_example()
    case.views = {}
    state = case.state()
    assert posterior(state) is None
    assert state.hand[1].latent is None


def test_evidence_no_deal_fits_is_set_aside(capsys):
    # A 9♣ between two 8s: nothing unseen fits, so the odds are today's, and it's logged once.
    case = worked_example()
    case.cards["ten"] = card(8, "Spades")
    case.views = {"desc": ["ten", "left", "right", "six"]}
    case.cards["six"] = card(8, "Hearts")
    case.cards["right"] = card(8, "Clubs")
    case.deck = clubs(7)
    state = case.state()
    nine = parse_query("9")
    plain = engine._evaluate_cards(state, nine, [2]).probability
    assert posterior(state) is None
    assert evaluate_cards(state, nine, [2]).probability == plain
    unused = engine.with_usable_evidence(state)
    assert unused.observations == () and all(c.latent is None for c in unused.hand)
    evaluate_cards(state, nine, [1])
    assert capsys.readouterr().err.count("fit no deal") == 1


def test_evidence_set_aside_is_remembered_only_for_the_latest(capsys, monkeypatch):
    # The observations already logged are bounded: one forgotten is logged again, not kept for good.
    # `_usable` keeps nothing here, so each call reaches the log.
    monkeypatch.setattr(engine, "_usable", type(engine._usable)())
    monkeypatch.setattr(engine, "_USABLE_KEPT", 0)
    monkeypatch.setattr(engine, "_ignored", type(engine._ignored)())
    monkeypatch.setattr(engine, "_IGNORED_KEPT", 1)

    def unfit(suit: str) -> GameState:
        case = worked_example()
        case.cards["ten"] = card(8, suit)
        case.views = {"desc": ["ten", "left", "right", "six"]}
        case.cards["six"] = card(8, "Hearts")
        case.cards["right"] = card(8, "Clubs")
        case.deck = clubs(7)
        return case.state()

    for suit in ("Spades", "Spades", "Diamonds", "Spades"):
        assert not engine.usable(unfit(suit))
    assert len(engine._ignored) == 1
    assert capsys.readouterr().err.count("fit no deal") == 3


def test_face_up_cards_out_of_order_are_set_aside():
    case = worked_example()
    case.views = {"desc": ["six", "left", "right", "ten"]}
    assert posterior(case.state()) is None


def test_resolving_changes_no_counts():
    case = random_case(random.Random(700), hidden_extra=True, discarded=True, latent=(1, 1), face_up=(1, 1),
                       down_discarded=1)
    state = case.state()
    assert state.discarded_hidden == 2
    for b in posterior(state).branches:
        resolved = engine._resolved(state, b)
        assert len(b.discarded) == 1
        assert (resolved.deck_size, len(resolved.hand)) == (state.deck_size, len(state.hand))
        assert (len(resolved.discard), resolved.discarded_hidden) == (len(state.discard), 1)
        assert sorted(map(repr, resolved.unseen + [c for i, c in b.cards] + list(b.discarded))) == sorted(
            map(repr, state.unseen))
        for discard in ([], [0], [1, 2]):
            assert engine._discard(resolved, discard).draws == engine._discard(state, discard).draws


def test_cancelled():
    cancel = threading.Event()
    cancel.set()
    case = random_case(random.Random(800), latent=(3, 3))
    state = case.state()
    engine._usable.clear()
    with pytest.raises(Cancelled):
        posterior(state, cancel)
    with pytest.raises(Cancelled):
        evaluate_cards(state, QUERIES[0], [0], cancel)
    with pytest.raises(Cancelled):
        evaluate_discard(state, TARGETS[0], [0], cancel)
    with pytest.raises(Cancelled):
        engine.estimate_discard(state, TARGETS[0], [0], cancel)


# --- Estimates (docs/probability-model.md#approximations) ---------------------------

@pytest.mark.parametrize("seed", range(8))
def test_deals_are_drawn_by_their_posterior_weight(seed):
    """Each deal is drawn as often as the oracle's posterior says, within 4 standard errors (seeded, so stable),
    and only deals it allows."""
    rng = random.Random(seed)
    case = random_case(rng, copies=seed % 2 == 0, stones=seed % 4 == 1, latent=(2, 3), down_discarded=seed % 3 == 2)
    state = case.state()
    want = posterior_by_cards(case, state)
    drawn = deals(state.observations, state.observed, state.unseen, lambda c: True)
    n = 4000
    got = Counter()
    draw = random.Random(seed)
    for _ in range(n):
        b = drawn.draw(draw)
        got[tuple(c for _, c in sorted(b.cards, key=lambda ic: ic[0])) + b.discarded] += 1
    assert set(got) <= set(want)
    for deal, p in want.items():
        assert abs(got[deal] / n - p) <= 4 * (p * (1 - p) / n) ** 0.5 + 1e-9, (deal, got[deal] / n, float(p))


@pytest.mark.parametrize("dna", [0, 1])
def test_final_hands_drawn_with_pareidolia_sold_agree_with_the_odds(dna):
    """The sampled model (engine._final_hands, which estimates draw from) deals marked cards from the
    face cards and the rest from what's left, as the exact odds do: their shares agree."""
    rng = random.Random(580 + dna)
    kw = {"discards_left": 0, "hands_left": 2, "dna_copies": 1} if dna else {}
    case = pareidolia_sold(rng, lambda c: c.marked and set(c.down) - c.marked, hidden_extra=True, discarded=True,
                           latent=(1, 2), **kw)
    case.views = {}
    state = case.state()
    hand_down = [i for i, n in enumerate(case.hand) if n in case.down]
    n = 4000
    for discard in ([], [hand_down[0]], [hand_down[-1]]):
        for target in TARGETS[:2]:
            exact = evaluate_discard(state, target, discard).probability
            final = engine._final_hands(state, target, discard)
            draw = random.Random(1)
            share = sum(final(draw) for _ in range(n)) / n
            margin = 4 * (float(exact) * (1 - float(exact)) / n) ** 0.5 + 1e-9
            assert abs(share - exact) <= margin, (target.name, discard, share, float(exact))


@pytest.mark.parametrize("seed, blind, kw", [(seed, None, {}) for seed in range(6)] + [
    (6, "The Mark", {}),
    (7, "The Hook", {"discards_left": 0, "hands_left": 2}),
    (8, "Cerulean Bell", {}),
    (9, None, {"discards_left": 0, "hands_left": 2, "dna_copies": 2}),
])
def test_estimates_hold_the_exact_odds(seed, blind, kw):
    """Each estimate's range holds the exact odds (seeded, so stable), under the bosses and DNA too."""
    rng = random.Random(100 + seed)
    while True:  # The Mark only flips face cards
        case = random_case(rng, ranks=range(9, 14) if blind == "The Mark" else range(7, 12), latent=(2, 3),
                           down_discarded=seed % 3 == 2, blind=blind, **kw)
        if blind != "The Mark" or all(engine.flipped_by_mark(case.cards[n]) for n in case.down):
            break
    if blind == "The Mark":
        case.marked = set(case.down)
    state = case.state()
    for discard in some_discards(rng, case):
        for target in TARGETS:
            found = engine.estimate_discard(state, target, discard)
            exact = evaluate_discard(state, target, discard).probability
            assert found is not None and found.samples >= 64
            assert found.low <= exact <= found.high, (target.name, discard)
        for query in QUERIES:
            found = engine.estimate_cards(state, query, discard)
            assert found is not None
            assert found.low <= evaluate_cards(state, query, discard).probability <= found.high, query.text


@pytest.mark.parametrize("seed, blind, kw", [(seed, None, {}) for seed in range(3)] + [
    (6, "The Mark", {}),
    (7, "The Hook", {"discards_left": 0, "hands_left": 2}),
    (8, "Cerulean Bell", {}),
    (9, None, {"discards_left": 0, "hands_left": 2, "dna_copies": 2}),
])
def test_estimates_from_deals_drawing_hands_hold_the_exact_odds(monkeypatch, seed, blind, kw):
    """With every deal's exact odds given up (as when they're slow), each deal drawn is one final hand drawn at
    random: the range still holds the exact odds."""
    exact = engine._evaluate_discard

    def slow(state, target, discard, cancel, *args):
        if isinstance(cancel, engine._Until):  # an estimate's deal: as if past its time
            raise Cancelled
        return exact(state, target, discard, cancel, *args)
    monkeypatch.setattr(engine, "_evaluate_discard", slow)
    drawn = []
    final_hands = engine._final_hands
    monkeypatch.setattr(engine, "_final_hands", lambda *args: drawn.append(1) or final_hands(*args))
    rng = random.Random(100 + seed)
    while True:  # The Mark only flips face cards
        case = random_case(rng, ranks=range(9, 14) if blind == "The Mark" else range(7, 12), latent=(2, 3),
                           down_discarded=seed % 3 == 2, blind=blind, **kw)
        if blind != "The Mark" or all(engine.flipped_by_mark(case.cards[n]) for n in case.down):
            break
    if blind == "The Mark":
        case.marked = set(case.down)
    state = case.state()
    for discard in some_discards(rng, case):
        for target in TARGETS:
            found = engine.estimate_discard(state, target, discard, seconds=60)
            assert found.low <= evaluate_discard(state, target, discard).probability <= found.high, target.name
    assert drawn


def test_estimates_share_one_sampler_of_deals(monkeypatch):
    """Every value estimated for a state draws from one sampler of deals, built once (it can take a second), and
    the estimates are the same as with a sampler built for each. A sampler built answers `usable` too."""
    rng = random.Random(42)
    case = random_case(rng, latent=(3, 3))
    state = case.state()
    values = [(target, discard) for discard in some_discards(rng, case) for target in TARGETS]

    def fresh(target, discard):
        engine._deals.clear()
        return engine.estimate_discard(state, target, discard, seconds=60)
    want = [fresh(t, d) for t, d in values]

    built = []
    monkeypatch.setattr(engine, "deals", lambda *args: built.append(1) or deals(*args))
    engine._deals.clear()
    engine._usable.clear()
    got = [engine.estimate_discard(state, t, d, seconds=60) for t, d in values]
    assert got == want
    assert len(built) == 1
    assert engine._usable[engine._evidence_key(state)] is True


def test_evidence_unchecked_until_usable_is_known():
    """The odds start by checking the sorts until `usable` has, for that state; not where the sorts say nothing
    (The House's first hand sorted once, kept), or there are none."""
    from scenarios import THE_HOUSE, THE_HOUSE_BOTH, THE_HOUSE_SORTED, Scenario

    def state(situation):
        return Scenario(situation, ("Pair",), "No jokers").state()[0]
    engine._usable.clear()
    engine._deals.clear()
    assert not engine.evidence_unchecked(state(THE_HOUSE), [])
    once = state(THE_HOUSE_SORTED)
    assert not engine.evidence_unchecked(once, [])
    assert engine.evidence_unchecked(once, [0])  # thrown away, it's the higher or the lower cards
    both = state(THE_HOUSE_BOTH)
    assert engine.evidence_unchecked(both, [])
    engine.usable(both)
    assert not engine.evidence_unchecked(both, [])


def test_estimate_from_draws_without_evidence():
    """With nothing the sorts say here, the estimate is from draws of final hands, of today's odds."""
    case = random_case(random.Random(3))
    state = replace(engine.with_usable_evidence(case.state()), observations=(), observed=())
    found = engine.estimate_discard(state, TARGETS[0], [], most=2000)
    assert found.draws and found.low <= evaluate_discard(state, TARGETS[0], []).probability <= found.high

"""build_report keeping each row's odds in `known`, and what lets it share them."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from calculatro import report
from calculatro.cards import parse_query
from calculatro.engine import Cancelled, evaluate_cards, evaluate_discard
from calculatro.hands import combine, get_target
from calculatro.report import build_report
from calculatro.state import SUITS, Card, GameState, Joker


def card(rank_id: int, suit: str = "Spades", **kw) -> Card:
    return Card(rank=str(rank_id), rank_id=rank_id, suit=suit, enhancement="c_base", **kw)


HAND = [card(9), card(10, "Hearts"), card(12, "Clubs"), card(12, "Diamonds"), card(14)]
UNSEEN = [card(r, s) for s in SUITS for r in (11, 13)]
STATE = GameState(in_run=True, hand_size=5, hands_left=2, discards_left=2, hand=HAND, unseen=UNSEEN)


def selecting(state: GameState, *indices: int) -> GameState:
    return replace(state, hand=[replace(c, highlighted=i in indices) for i, c in enumerate(state.hand)])


@pytest.fixture
def calls(monkeypatch) -> list[tuple]:
    """Every row the engine is asked for: (target or query text, the cards discarded)."""
    seen: list[tuple] = []

    def counted(evaluate, name):
        def wrapper(state, row, discard, *rest):
            seen.append((name(row), [state.hand[i].rank_id for i in discard]))
            return evaluate(state, row, discard, *rest)
        return wrapper

    monkeypatch.setattr(report, "evaluate_discard", counted(evaluate_discard, lambda t: t.name))
    monkeypatch.setattr(report, "evaluate_cards", counted(evaluate_cards, lambda q: q.text))
    return seen


def test_selecting_the_same_cards_again_calculates_nothing(calls):
    targets, known = [get_target("Pair"), get_target("Flush")], {}

    def run(*indices):
        # Parsed anew each time, as `tracked` does: two parses of a query aren't equal.
        found = build_report(selecting(STATE, *indices), targets, [parse_query("at least 2 queen")], known=known)
        return found.targets, [(c.keep, c.discard) for c in found.cards], found.discarded, found.discard_draws

    first = run(0, 1)
    assert len(calls) == 8  # keep and discard, for 2 targets, "Any target" and the query
    second = run(2)
    assert len(calls) == 12  # only the new selection's rows: keep is the same
    calls.clear()
    assert run(0, 1) == first
    assert run(2) == second
    assert calls == []
    alone = build_report(selecting(STATE, 0, 1), targets, [parse_query("at least 2 queen")])
    assert first == (alone.targets, [(c.keep, c.discard) for c in alone.cards], alone.discarded, alone.discard_draws)


def test_rows_of_different_targets_and_selections_stay_apart(calls):
    known = {}
    pair = build_report(selecting(STATE, 2, 3), [get_target("Pair")], known=known)
    trips = build_report(selecting(STATE, 2, 3), [get_target("Three of a Kind")], known=known)
    other = build_report(selecting(STATE, 0, 1), [get_target("Pair")], known=known)
    assert pair.targets[0].keep == 1
    assert pair.targets[0].discard != other.targets[0].discard
    assert trips.targets[0].keep == 0
    assert pair.targets[0].discard != trips.targets[0].discard


def test_sorting_the_hand_keeps_the_odds_and_reports_this_hand(calls):
    known = {}
    before = build_report(selecting(STATE, 0, 2), [get_target("Pair")], known=known)
    calls.clear()
    # The same cards selected, at other positions.
    sorted_state = selecting(replace(STATE, hand=HAND[::-1]), 4, 2)
    after = build_report(sorted_state, [get_target("Pair")], known=known)
    assert calls == []
    assert after.targets == before.targets and after.discard_draws == before.discard_draws
    assert after.discarded == [sorted_state.hand[2], sorted_state.hand[4]]


def test_identical_cards_share_their_odds(calls):
    state = replace(STATE, hand=[card(9), card(9), card(10), card(11), card(12)])
    known = {}
    first = build_report(selecting(state, 0), [get_target("Pair")], known=known)
    calls.clear()
    assert build_report(selecting(state, 1), [get_target("Pair")], known=known).targets == first.targets
    assert calls == []


def test_the_forced_card_is_not_the_same_card(calls):
    state = replace(STATE, blind="Cerulean Bell",
                    hand=[card(9, forced=True), card(9), card(10), card(11), card(12)])
    known = {}
    build_report(selecting(state, 0), [get_target("Pair")], known=known)
    calls.clear()
    build_report(selecting(state, 1), [get_target("Pair")], known=known)
    assert len(calls) == 1


def test_a_cancelled_report_keeps_the_rows_it_finished(monkeypatch):
    def cancelled_on_discard(state, target, discard, cancel=None):
        if discard:
            raise Cancelled
        return evaluate_discard(state, target, discard, cancel)

    monkeypatch.setattr(report, "evaluate_discard", cancelled_on_discard)
    known = {}
    with pytest.raises(Cancelled):
        build_report(selecting(STATE, 0), [get_target("Pair")], known=known)
    assert len(known) == 1  # Keep

    def only_the_discard(state, target, discard, cancel=None):
        assert discard
        return evaluate_discard(state, target, discard, cancel)

    monkeypatch.setattr(report, "evaluate_discard", only_the_discard)
    assert build_report(selecting(STATE, 0), [get_target("Pair")], known=known).targets[0].discard is not None


def _random_setup(rng: random.Random) -> tuple[GameState, list[int]]:
    """A random hand and selection, under a rule that treats particular cards specially."""
    def random_card():
        return Card(rank=str(r := rng.randint(9, 14)), rank_id=r, suit=rng.choice(SUITS),
                    enhancement=rng.choice(["c_base", "c_base", "c_base", "m_wild", "m_stone"]))

    cards = [random_card() for _ in range(18)]
    hidden = rng.choice([0, 0, 1, 2])
    known = rng.randint(max(1, 3 - hidden), 6 - hidden)
    hand = [*cards[:known], *[Card(hidden=True)] * hidden]
    unseen = cards[known:]
    setup = rng.choice(["plain", "The Mark", "Cerulean Bell", "The Hook", "Crimson Heart", "The Serpent", "DNA"])
    jokers = [Joker(k) for k in rng.sample(["j_four_fingers", "j_shortcut", "j_smeared", "j_pareidolia", "j_joker"],
                                           rng.randint(0, 3))]
    fields = {}
    discard = sorted(rng.sample(range(len(hand)), rng.randint(0, min(3, len(hand)))))
    if setup in ("The Hook", "Crimson Heart"):
        fields = {"blind": setup, "discards_left": 0}
        if setup == "Crimson Heart" and jokers:
            jokers[0] = replace(jokers[0], debuff=True)
    elif setup == "Cerulean Bell":
        fields = {"blind": setup}
        hand[0] = replace(hand[0], forced=True)
        discard = sorted({0, *discard})
    elif setup == "DNA":
        fields = {"dna_copies": rng.randint(1, 2), "discards_left": 0}
        discard = discard[:1]
    elif setup != "plain":
        fields = {"blind": setup}
    state = GameState(in_run=True, hand_size=len(hand), hands_left=3, **{"discards_left": 2, **fields},
                      hand=hand, unseen=unseen, jokers=jokers)
    return state, discard


def test_odds_do_not_depend_on_the_hands_order():
    """What lets `known` go by the cards rather than their positions, including where
    particular cards are treated specially: forced, or taken at random by The Hook."""
    rng = random.Random(20261105)
    targets = [get_target(n) for n in ("Pair", "Flush", "Straight", "Full House")]
    targets.append(combine(targets[:2]))
    queries = [parse_query(q) for q in ("at least 2 queen", "no face", "at most 1 hearts")]
    for _ in range(150):
        state, discard = _random_setup(rng)
        order = list(range(len(state.hand)))
        rng.shuffle(order)
        shuffled = replace(state, hand=[state.hand[i] for i in order])
        moved = sorted(order.index(i) for i in discard)
        target, query = rng.choice(targets), rng.choice(queries)
        a, b = evaluate_discard(state, target, discard), evaluate_discard(shuffled, target, moved)
        assert (a.probability, a.draws) == (b.probability, b.draws), (state, discard, target.name)
        a, b = evaluate_cards(state, query, discard), evaluate_cards(shuffled, query, moved)
        assert (a.probability, a.draws) == (b.probability, b.draws), (state, discard, query.text)

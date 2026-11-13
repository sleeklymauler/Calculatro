"""build_report keeping each row's odds in `known`, and what lets it share them."""

from __future__ import annotations

import json
import random
import threading
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from calculatro import report
from calculatro.cards import parse_query
from calculatro.engine import Cancelled, DiscardOutcome, Estimate, estimate_discard, evaluate_cards, evaluate_discard
from calculatro.hands import combine, get_target
from calculatro.report import BOUNDS, ESTIMATE, PENDING, SAMPLE, SLOW, Approx, build_report
from calculatro.state import SUITS, Card, GameState, Joker
from calculatro.worker import MEMORY, TIME, Control, Limits, TooSlow, Watchdog
from fakes import taking

pytestmark = pytest.mark.usefixtures("no_estimates")


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
    # Keep and discard, for 2 targets and the query. Not "Any target": its bounds meet (the Pair is certain, then
    # neither can be made).
    assert len(calls) == 6
    second = run(2)
    assert len(calls) == 9  # only the new selection's rows: keep is the same
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


def test_quick_values_first_then_the_slow_ones_then_given_up(monkeypatch):
    """Each value gets a quick try first, so a slow one never holds up the rest; the slow ones then get longer, and
    one that doesn't finish then is given up on, and isn't tried again."""
    calls: list[tuple] = []
    monkeypatch.setattr(report, "evaluate_discard", taking({"Flush": 0.3, "Straight Flush": 10}, calls))
    targets = [get_target(n) for n in ("Pair", "Flush", "Straight Flush")]
    control, known, partials = Control(Watchdog(period=0.005)), {}, []
    found = build_report(selecting(STATE, 0), targets, cancel=control, known=known,
                         limits=Limits(quick=0.1, exact=0.5), progress=partials.append)
    # Keep, then Discard, in the panel's order, in each pass, but "Any target" (the combined target) after the hands
    # whose odds bound it.
    rows = ("Pair", "Flush", "Straight Flush")
    assert calls == ([(t, n) for n in (0, 1) for t in rows] + [("Pair / Flush / Straight Flush", n) for n in (0, 1)]
                     + [(t, n) for n in (0, 1) for t in rows[1:]])
    assert [(t.name, t.keep, t.discard) for t in found.targets] == [
        ("Any target", Fraction(1, 2), Fraction(1, 3)), ("Pair", Fraction(1, 2), Fraction(1, 3)),
        ("Flush", Fraction(1, 2), Fraction(1, 3)), ("Straight Flush", TooSlow(TIME), TooSlow(TIME))]
    assert not found.partial and found.discard_draws == 1
    # Each value as it's found: the quick ones, then Flush's and Straight Flush's in turn. A value found slow changes
    # nothing shown (it has no approximation), so it sends nothing.
    shown = [[(t.keep, t.discard) for t in p.targets] for p in partials]
    assert len(partials) == 8 and all(p.partial for p in partials[:-1]) and partials[-1] == found
    assert shown[0][1] == (Fraction(1, 2), PENDING)
    assert shown[3][0] == (Fraction(1, 2), Fraction(1, 3)) and shown[3][2:] == [(PENDING, PENDING)] * 2
    assert shown[4][2] == (Fraction(1, 2), PENDING) and shown[5][3] == (TooSlow(TIME), PENDING)
    assert shown[6][2] == (Fraction(1, 2), Fraction(1, 3)) and shown[6][3] == (TooSlow(TIME), PENDING)
    calls.clear()
    again = build_report(selecting(STATE, 0), targets, cancel=control, known=known, limits=Limits(quick=0.1, exact=0.5))
    assert calls == [] and again == found


def test_a_value_using_too_much_memory_is_given_up(monkeypatch):
    """Over the memory limit a value is given up on, saying so, and the next one starts afresh once the memory is
    given back."""
    used = [0]
    slow = taking({"Flush": 10})

    def hungry(state, target, discard, cancel=None):
        if target.name != "Flush":
            return slow(state, target, discard, cancel)
        used[0] = 2000
        try:
            return slow(state, target, discard, cancel)
        finally:
            used[0] = 0  # given back as it unwinds

    monkeypatch.setattr(report, "evaluate_discard", hungry)
    control = Control(Watchdog(lambda: used[0], memory_limit=1000, period=0.005, grace=0.05))
    found = build_report(selecting(STATE, 0), [get_target("Flush"), get_target("Pair")], cancel=control,
                         limits=Limits(quick=5, exact=5))
    assert [(t.name, t.keep, t.discard) for t in found.targets][1:] == [
        ("Flush", TooSlow(MEMORY), TooSlow(MEMORY)), ("Pair", Fraction(1, 2), Fraction(1, 3))]


def test_a_new_state_cancels_a_value_on_its_last_try(monkeypatch):
    """Only the limits give a value up: cancelling the job still cancels it."""
    slow, calls = taking({"Pair": 10}), []
    control = Control(Watchdog(period=0.005))

    def cancelled_on_last_try(state, target, discard, cancel=None):
        # Cancelled once a value is tried again (the exact pass), not on a timer, which a busy runner's quick
        # pass could outlast.
        if (target.name, tuple(discard)) in calls:
            control.cancel()
        calls.append((target.name, tuple(discard)))
        return slow(state, target, discard, cancel)

    monkeypatch.setattr(report, "evaluate_discard", cancelled_on_last_try)
    known = {}
    with pytest.raises(Cancelled):
        build_report(selecting(STATE, 0), [get_target("Pair")], cancel=control, known=known,
                     limits=Limits(quick=0.05, exact=5))
    # Neither finished: both found slow, with no estimate (there are no face-down cards), nothing more.
    values = {k: v for k, v in known.items() if k[0] != ESTIMATE}
    assert set(values.values()) == {SLOW} and {k: v for k, v in known.items() if k[0] == ESTIMATE} == {
        (ESTIMATE, k): None for k in values}


def test_any_target_shows_bounds_until_its_exact_odds(monkeypatch):
    """A slow "Any target" shows its bounds from the hands' odds, narrowed as they're found, and keeps them, final, if
    its exact odds are given up on. Asked again, it shows them at once and skips the quick try."""
    calls: list[tuple] = []
    any_name = "Pair / Flush"
    odds = {"Pair": Fraction(1, 5), "Flush": Fraction(1, 10), any_name: Fraction(1, 4)}
    monkeypatch.setattr(report, "evaluate_discard", taking({"Flush": 0.3, any_name: 0.8}, calls, odds))
    targets = [get_target("Pair"), get_target("Flush")]
    control, known, partials = Control(Watchdog(period=0.005)), {}, []
    limits = Limits(quick=0.1, exact=0.5)
    found = build_report(STATE, targets, cancel=control, known=known, limits=limits, progress=partials.append)
    shown = [p.targets[0].keep for p in partials]
    assert shown == [PENDING,  # Pair
                     Approx(Fraction(1, 5), Fraction(1), BOUNDS),  # "Any target" found slow: at least Pair's odds
                     Approx(Fraction(1, 5), Fraction(3, 10), BOUNDS),  # Flush: at most their sum
                     Approx(Fraction(1, 5), Fraction(3, 10), BOUNDS, TIME)]  # given up on
    assert [p.partial for p in partials] == [True, True, True, False] and partials[-1] == found
    calls.clear()
    monkeypatch.setattr(report, "evaluate_discard", taking({any_name: 0.3}, calls, odds))
    partials.clear()
    # As if cancelled during its exact pass, before giving up.
    known = {k: SLOW if isinstance(v, TooSlow) else v for k, v in known.items()}
    again = build_report(STATE, targets, cancel=control, known=known, limits=limits, progress=partials.append)
    assert calls == [(any_name, 0)]  # straight to the exact pass
    assert partials[0].targets[0].keep == Approx(Fraction(1, 5), Fraction(3, 10), BOUNDS)  # at once
    assert again.targets[0].keep == Fraction(1, 4) and not again.partial


def test_bounds_that_meet_are_the_exact_odds(monkeypatch):
    """When a hand is certain, or none can be made, "Any target"'s bounds meet: it isn't worked out at all."""
    calls: list[tuple] = []
    odds = {"Pair": Fraction(1), "Flush": Fraction(0)}
    monkeypatch.setattr(report, "evaluate_discard", taking({}, calls, odds))
    found = build_report(STATE, [get_target("Pair"), get_target("Flush")])
    assert found.targets[0].keep == 1 and calls == [("Pair", 0), ("Flush", 0)]
    calls.clear()
    monkeypatch.setattr(report, "evaluate_discard", taking({}, calls, {"Pair": Fraction(0), "Flush": Fraction(0)}))
    assert build_report(STATE, [get_target("Pair"), get_target("Flush")]).targets[0].keep == 0
    assert len(calls) == 2


def estimating(estimates: dict[str, Estimate | None], calls: list[tuple]):
    """A stand-in for `estimate_discard`: `estimates` by the target's name."""
    def estimate(state, target, discard, cancel=None, seconds=1.0):
        calls.append(("estimate", target.name, len(discard)))
        return estimates.get(target.name)
    return estimate


def test_a_slow_value_shows_its_estimate_until_its_exact_odds(monkeypatch):
    """A value found slow is estimated before the exact pass, once, and the estimate shown meanwhile; kept, final, if
    the exact odds are given up on. Asked again, it shows at once, straight to the exact pass."""
    calls: list[tuple] = []
    monkeypatch.setattr(report, "evaluate_discard", taking({"Flush": 0.3, "Straight": 10}, calls))
    monkeypatch.setattr(report, "estimate_discard", estimating(
        {"Flush": Estimate(0.25, 0.2, 0.3, 1000), "Straight": None}, calls))
    targets = [get_target("Flush"), get_target("Straight")]
    control, known, partials = Control(Watchdog(period=0.005)), {}, []
    limits = Limits(quick=0.1, approx=0.1, exact=0.5)
    found = build_report(selecting(STATE, 0), targets, cancel=control, known=known, limits=limits,
                         progress=partials.append)
    # Each value found slow ("Any target" isn't), in the passes' order: Keep, then Discard; then the exact pass.
    assert calls[6:11] == [("estimate", t, n) for n in (0, 1) for t in ("Flush", "Straight")] + [("Flush", 0)]
    estimate = Approx(Fraction(1, 5), Fraction(3, 10), SAMPLE, None, Fraction(1, 4))
    shown = [p.targets[1].keep for p in partials]
    assert shown.index(estimate) < shown.index(Fraction(1, 2))  # Flush's estimate, then its exact odds
    assert found.targets[1].keep == Fraction(1, 2)
    assert found.targets[2].keep == TooSlow(TIME)  # nothing to estimate
    calls.clear()
    partials.clear()
    known = {k: SLOW if isinstance(v, DiscardOutcome) and k[0].name == "Flush" else v for k, v in known.items()}
    monkeypatch.setattr(report, "evaluate_discard", taking({"Flush": 10}, calls))
    again = build_report(selecting(STATE, 0), targets, cancel=control, known=known, limits=limits,
                         progress=partials.append)
    assert partials[0].targets[1].keep == estimate  # at once
    assert calls == [("Flush", 0), ("Flush", 1)]  # no quick try, no estimate again
    assert again.targets[1].keep == replace(estimate, limit=TIME)


def test_no_more_quick_tries_once_one_ran_out_checking_the_sorts(monkeypatch):
    """Once a quick try has run out of time before the hand's sorts were checked, the values that check them first
    skip theirs, as they'd run out too: they go straight to their estimates. The others still get theirs."""
    calls: list[tuple] = []
    monkeypatch.setattr(report, "evaluate_discard", taking({"Flush": 10, "Straight": 10}, calls))
    monkeypatch.setattr(report, "estimate_discard", estimating({}, calls))
    monkeypatch.setattr(report, "evidence_unchecked", lambda state, discard: not discard)  # Keep uses the sorts
    targets = [get_target("Flush"), get_target("Straight")]
    build_report(selecting(STATE, 0), targets, cancel=Control(Watchdog(period=0.005)),
                 limits=Limits(quick=0.05, approx=0.05, exact=0.05))
    quick = calls[:calls.index(("estimate", "Flush", 0))]
    # The Keeps after the first skipped ("Any target"'s too), the Discards tried.
    assert quick == [("Flush", 0), ("Flush", 1), ("Straight", 1), ("Flush / Straight", 1)]
    assert calls[len(quick):len(quick) + 4] == [("estimate", t, n) for n in (0, 1) for t in ("Flush", "Straight")]


def test_any_targets_estimate_stays_within_its_bounds(monkeypatch):
    """"Any target"'s estimate is shown rather than its bounds, but within them, which are certain."""
    calls: list[tuple] = []
    odds = {"Pair": Fraction(3, 10), "Flush": Fraction(1, 10)}
    monkeypatch.setattr(report, "evaluate_discard", taking({"Pair / Flush": 10}, calls, odds))
    monkeypatch.setattr(report, "estimate_discard", estimating({"Pair / Flush": Estimate(0.33, 0.2, 0.45, 50)}, calls))
    found = build_report(STATE, [get_target("Pair"), get_target("Flush")], cancel=Control(Watchdog(period=0.005)),
                         limits=Limits(quick=0.05, approx=0.1, exact=0.1))
    assert found.targets[0].keep == Approx(Fraction(3, 10), Fraction(2, 5), SAMPLE, TIME, Fraction(33, 100))


@pytest.mark.usefixtures("fake_clock")
def test_estimates_hold_the_exact_odds():
    """On the sorted scenarios that finish, each estimate's range holds the exact odds (it's seeded, so this is
    stable)."""
    from scenarios import SCENARIOS, Scenario
    expected = json.loads((Path(__file__).parent / "perf" / "expected.json").read_text())
    checked = 0
    for scenario in SCENARIOS:
        if not isinstance(scenario, Scenario) or not scenario.situation.sorts or scenario.id not in expected:
            continue
        if "Four Fingers" in scenario.jokers or "Shortcut" in scenario.jokers:
            continue  # the slowest to sample: a few are enough below
        state, discard = scenario.state()
        found = estimate_discard(state, scenario.target(), discard, seconds=0.2)
        if found is not None:
            checked += 1
            assert found.low <= Fraction(expected[scenario.id]) <= found.high, scenario.id
    assert checked > 30


def _draws_hold(state: GameState, target, discard: list[int]) -> None:
    """The estimate from draws of final hands is within 4.5 standard errors of the exact odds."""
    found = estimate_discard(state, target, discard, seconds=60, most=1500)
    p = evaluate_discard(state, target, discard).probability
    assert found.draws and found.samples >= 64  # fewer than `most` once its range is close enough
    assert abs(found.mean - p) <= 4.5 * float(p * (1 - p) / found.samples) ** 0.5, (state, discard, target.name)


@pytest.mark.usefixtures("fake_clock")
def test_estimates_from_draws_hold_the_exact_odds():
    """Final hands drawn at random make each target as often as the exact odds say, under every boss and joker
    rule the engine models, keeping the hand or discarding (seeded, so stable)."""
    rng = random.Random(20261112)
    targets = [get_target(n) for n in ("Pair", "Flush", "Straight", "Full House")]
    targets.append(combine([get_target("Straight"), get_target("Flush")]))
    setups = set()
    for _ in range(150):
        state, discard = _random_setup(rng)
        setups.add(state.blind or ("DNA" if state.dna_copies else "plain"))
        for target in rng.sample(targets, 2):
            for chosen in {(), tuple(discard)}:
                _draws_hold(state, target, list(chosen))
    assert len(setups) == 7


@pytest.mark.usefixtures("fake_clock")
def test_estimates_from_draws_with_dna_and_crimson_heart():
    """Cases the random ones rarely reach: DNA's copies of a face-down card are all one card, and Crimson Heart
    debuffs a random joker after the play."""
    hand = [card(9), card(10, "Hearts"), card(11, "Hearts"), Card(hidden=True), Card(hidden=True)]
    unseen = [card(r, s) for s in SUITS for r in (9, 10, 11, 12, 13)]
    dna = GameState(in_run=True, hand_size=5, hands_left=2, discards_left=0, hand=hand, unseen=unseen, dna_copies=2)
    for name in ("Pair", "Three of a Kind", "Full House"):
        _draws_hold(dna, get_target(name), [3])
    hearts = [card(r, "Hearts") for r in (9, 10, 11, 12)] + [card(2, "Clubs")]
    crimson = GameState(in_run=True, hand_size=5, hands_left=2, discards_left=0, hand=hearts, unseen=unseen,
                        blind="Crimson Heart", jokers=[Joker("j_four_fingers"), Joker("j_joker")])
    for name in ("Flush", "Straight Flush"):
        _draws_hold(crimson, get_target(name), [4])


def test_any_target_bounds_hold_the_exact_odds():
    """Each hand's odds bound those of any of them, on random hands, rules and discards."""
    rng = random.Random(20261111)
    names = ["Pair", "Two Pair", "Three of a Kind", "Straight", "Flush", "Full House"]
    for _ in range(60):
        state, discard = _random_setup(rng)
        targets = [get_target(n) for n in rng.sample(names, rng.randint(2, 3))]
        each = [evaluate_discard(state, t, discard).probability for t in targets]
        p = evaluate_discard(state, combine(targets), discard).probability
        assert max(each) <= p <= min(1, sum(each)), (state, discard, [t.name for t in targets])


def test_no_limits_without_a_job(monkeypatch):
    """Called with a plain Event (or none), as the console and the benchmarks do, every value is worked out."""
    monkeypatch.setattr(report, "evaluate_discard", taking({"Pair": 0.1}))
    found = build_report(selecting(STATE, 0), [get_target("Pair")], cancel=threading.Event(),
                         limits=Limits(quick=0.01, exact=0.01))
    assert found.targets[0].discard == Fraction(1, 3)


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

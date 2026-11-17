"""A best-discard search spread over worker processes (calculatro.spread): the same answer as from one, however
many of its choices are worked out side by side. The workers are threads here (fakes.ThreadRunner);
test_pool.py has real ones."""

import sys
from pathlib import Path

import pytest

from calculatro import search, spread, tasks
from calculatro.cards import parse_query
from calculatro.engine import Cancelled, Session
from calculatro.worker import MEMORY, TIME, Control, Limits, TooSlow
from fakes import ThreadRunner

sys.path.insert(0, str(Path(__file__).parent / "perf"))

from perfdata import load_expected
from scenarios import SCENARIOS, BestDiscard
from test_expected import SLOW

EXPECTED = load_expected()
SEARCHES = [s for s in SCENARIOS if isinstance(s, BestDiscard)]
QUICK = [s for s in SEARCHES if s.id not in SLOW]


def scenario(*parts: str) -> BestDiscard:
    return next(s for s in SEARCHES if all(part in s.id for part in parts))


def text(found: search.Search) -> str:
    return f"{found.probability} ({len(found.options)} options, first {found.options[0]})"


@pytest.mark.parametrize("case", QUICK, ids=lambda s: s.id)
def test_a_spread_search_finds_what_one_process_does(case: BestDiscard):
    runner = ThreadRunner(3)
    found = spread.best_discards(runner, case.state(), case.target(), Control())
    assert text(found) == EXPECTED[case.id] and found.estimated is None
    kinds = {type(task) for _, task in runner.began}
    assert tasks.SearchPlan in kinds and tasks.BestDiscard not in kinds


@pytest.mark.parametrize("parts", [
    ("8 cards |", "Flush + Straight", "No jokers"), ("2 face-down, sorted by rank", "| Flush |"),
    ("Cerulean Bell", "| Full House |"), ("11 cards, holding a Full House",)])
def test_every_option_and_its_order_is_the_same(parts):
    case = scenario(*parts)
    state, target = case.state(), case.target()
    assert spread.best_discards(ThreadRunner(4), state, target, Control()) == search.best_discards(state, target)


@pytest.mark.parametrize("written", ["at least 2 hearts", "no face", "at least 1 king"])
def test_a_card_querys_search_is_the_same(written):
    for case in (scenario("8 cards |", "| Flush |", "No jokers"), scenario("2 face-down, sorted by rank", "| Flush |")):
        state, query = case.state(), parse_query(written)
        assert (spread.best_discards(ThreadRunner(3), state, query, Control())
                == search.best_card_discards(state, query))


def test_without_workers_the_search_is_one_task():
    case = scenario("8 cards |", "| Flush |", "No jokers")
    runner = ThreadRunner(1)
    found = spread.best_discards(runner, case.state(), case.target(), Control())
    assert text(found) == EXPECTED[case.id]
    assert [type(task) for _, task in runner.began] == [tasks.BestDiscard]


def test_a_cancelled_job_cancels_the_search():
    case = scenario("8 cards |", "Straight Flush", "Four Fingers + Shortcut")
    control, runner = Control(), ThreadRunner(2)
    control.cancel()
    with pytest.raises(Cancelled):
        spread.best_discards(runner, case.state(), case.target(), control)
    assert runner.began == []


def test_at_its_time_limit_the_search_answers_from_estimates():
    """The limit is the whole search's, counted from its first task, not each task's."""
    case = scenario("11 cards, holding no target")
    runner = ThreadRunner(2)
    found = spread.best_discards(runner, case.state(), case.target(), Control(),
                                 Limits(search=0.3, search_estimate=0.2))
    assert isinstance(found, search.Search) and found.estimated is not None and found.estimated.limit == TIME
    assert found.options and found.estimated.low <= found.probability <= found.estimated.high
    assert type(runner.began[-1][1]) is tasks.EstimatedBest
    # Given up before it's laid out, there's nothing to estimate from.
    assert spread.best_discards(ThreadRunner(2), case.state(), case.target(), Control(),
                                Limits(search=0.0)) == TooSlow(TIME)


def test_a_choice_stopped_for_memory_is_tried_again_with_fewer_side_by_side(monkeypatch):
    """Each worker keeps its own engine.Reuse for the search. With one left, the search is given up for memory."""
    case = scenario("8 cards |", "| Full House |", "No jokers")
    state, target = case.state(), case.target()
    real, stops = search.choice_odds, [3]

    def choice_odds(state, row, discard, cancel=None, *rest, **kw):
        if discard and stops and (stops.append(stops.pop() - 1) or stops[-1] >= 0):
            cancel.stop(MEMORY)
            raise Cancelled
        return real(state, row, discard, cancel, *rest, **kw)

    monkeypatch.setattr(search, "choice_odds", choice_odds)
    runner = ThreadRunner(4)
    assert text(spread.best_discards(runner, state, target, Control())) == EXPECTED[case.id]  # 4, 3, 2, then 1
    stops[:] = [50]
    found = spread.best_discards(ThreadRunner(2), state, target, Control(), Limits(search_estimate=0.2))
    assert found.options and found.estimated is not None and found.estimated.limit == MEMORY


def test_what_a_search_kept_is_dropped_when_its_over():
    case = scenario("8 cards |", "| Flush |", "No jokers")
    state, target = case.state(), case.target()
    session = Session()
    task = tasks.Choice(state, target, [], 5.0, search=7)
    assert task.run(None, session) is not None
    kept = tasks._kept.shared
    assert kept[0] == 7 and tasks.Choice(state, target, [0], 5.0, search=7).run(None, session) is not None
    assert tasks._kept.shared is kept  # the same engine.Reuse for the search's next choice
    tasks.release(8)
    assert tasks._kept.shared is kept
    tasks.release(7)
    assert tasks._kept.shared is None

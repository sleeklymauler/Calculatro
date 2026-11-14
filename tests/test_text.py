"""`calculatro odds`: a state file's state, odds and best discard as text, for debugging."""

import json
import time
from fractions import Fraction
from pathlib import Path

import pytest

import calculatro.__main__ as cli
from calculatro import report
from calculatro.engine import Cancelled, Estimate
from calculatro.search import Search
from calculatro.state import SCHEMA
from calculatro.worker import LIMITS, Limits, Watchdog
from fakes import taking

pytestmark = pytest.mark.usefixtures("no_estimates")


def cards(*specs: tuple[int, str], selected=()) -> list[dict]:
    return [{"rank": str(r), "rank_id": r, "suit": s, "enhancement": "c_base", "highlighted": i in selected}
            for i, (r, s) in enumerate(specs)]


def state_file(tmp_path, **fields) -> Path:
    """A state like hand_state's in test_watch: 9♠ 9♥ 2♣, the 9♠ selected, one discard left, four hearts unseen."""
    snapshot = {"schema": SCHEMA, "id": "s:1", "in_run": True, "state": "SELECTING_HAND", "hand_size": 3,
                "hands_left": 1, "discards_left": 1,
                "hand": cards((9, "Spades"), (9, "Hearts"), (2, "Clubs"), selected=(0,)),
                "unseen": cards(*[(r, "Hearts") for r in range(3, 7)]), "discard": [],
                "tracked": {"hands": ["Pair"]}, **fields}
    path = tmp_path / "state.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


@pytest.fixture
def odds(capsys):
    """`calculatro odds` on a state file: its exit code, and what it printed to stdout and stderr."""
    def run(path, key=None, limits=LIMITS) -> tuple[int, str, str]:
        code = cli.print_odds(path, key, limits, Watchdog())
        out, err = capsys.readouterr()
        return code, out, err
    return run


def test_prints_the_state_and_its_odds(tmp_path, odds):
    code, out, err = odds(state_file(tmp_path))
    assert code == 0 and err == ""
    assert "Blind: -" in out and "Hand (3):" in out
    assert "Odds: keep hand  ->  discard 9♠ and draw 1" in out
    assert "Best discard" not in out  # none asked for


def test_values_given_up_on_and_approximated(tmp_path, odds, monkeypatch):
    """What isn't exact says so: "?" for a value given up on, and an approximation's range."""
    any_name = "Pair / Flush / Straight Flush"
    monkeypatch.setattr(report, "evaluate_discard", taking(
        {"Straight Flush": 10, any_name: 10}, odds={"Pair": Fraction(1, 5), "Flush": Fraction(1, 10)}))
    path = state_file(tmp_path, tracked={"hands": ["Pair", "Flush", "Straight Flush"]})
    _, out, _ = odds(path, limits=Limits(quick=0.05, exact=0.2))
    assert any("Straight Flush" in line and "?  ->        ?  (too slow)" in line for line in out.splitlines())
    # "Any target"'s bounds come from its hands': at least Pair's, at most their sum with Straight Flush's unknown.
    assert any(line.startswith("  Any target") and "*" in line and "too slow" in line for line in out.splitlines())


def test_the_best_discard_the_state_asks_for(tmp_path, odds, monkeypatch):
    monkeypatch.setattr(cli, "best_discards", lambda state, target, cancel, progress=None: Search([[2]], Fraction(1)))
    _, out, _ = odds(state_file(tmp_path, search={"key": "Pair", "nonce": 1}))
    assert out.splitlines()[-1] == "Best discard for Pair: discard 2♣, 100.00%"


def test_the_best_discard_for_another_row(tmp_path, odds, monkeypatch):
    monkeypatch.setattr(cli, "best_discards", lambda state, target, cancel, progress=None: Search([[]], Fraction(1)))
    path = state_file(tmp_path, search={"key": "Pair", "nonce": 1})
    _, out, _ = odds(path, "Pair")
    assert out.splitlines()[-1] == "Best discard for Pair: keep the hand, 100.00%"
    _, out, _ = odds(path, "Flush")
    assert out.splitlines()[-1] == "Best discard for Flush: no such row is tracked."


def until_cancelled(state, target, cancel, progress=None):
    while not cancel.is_set():
        time.sleep(0.001)
    raise Cancelled


def test_a_best_discard_given_up_on(tmp_path, odds, monkeypatch):
    monkeypatch.setattr(cli, "best_discards", until_cancelled)
    _, out, _ = odds(state_file(tmp_path), "Pair", Limits(search=0.1))
    assert out.splitlines()[-1] == "Best discard for Pair: too slow, given up on"


def test_a_best_discard_from_estimates(tmp_path, odds, monkeypatch):
    guesses = {(): Estimate(0.4, 0.35, 0.45, 1000, True), (0,): Estimate(0.6, 0.55, 0.65, 1000, True)}

    def search(state, target, cancel, progress=None):
        progress.start(state, [[0], [1], [2]], None, [None, (1, 0, 0)],
                       lambda discard, cancel, seconds: guesses[tuple(discard)])
        return until_cancelled(state, target, cancel)

    monkeypatch.setattr(cli, "best_discards", search)
    _, out, _ = odds(state_file(tmp_path), "Pair", Limits(search=0.1, search_estimate=0.5))
    assert out.splitlines()[-1] == ("Best discard for Pair: discard 9♠, 60.00%* (* by estimates, too slow to "
                                    "compare exactly; likely 55.00% to 65.00%)")


def test_nothing_tracked(tmp_path, odds):
    _, out, _ = odds(state_file(tmp_path, tracked={}))
    assert out.splitlines()[-1] == cli.NOTHING_TRACKED


def test_not_in_a_run(tmp_path, odds):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema": SCHEMA, "in_run": False}), encoding="utf-8")
    assert odds(path) == (0, "Not in a run.\n", "")


@pytest.mark.parametrize("contents, error", [
    (None, "it's missing"),
    ('{"schema": 1, "in_run": true}', "unsupported state schema 1"),
    ("{", "JSONDecodeError"),
])
def test_a_state_file_it_cant_read(tmp_path, odds, contents, error):
    path = tmp_path / "state.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    code, out, err = odds(path)
    assert code == 1 and out == "" and err.startswith(f"Can't read {path}") and error in err

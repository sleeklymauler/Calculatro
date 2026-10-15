"""The perf scenarios' exact results, checked by a plain `pytest` (and so in CI).

test_perf.py checks the same results along with timings, which need this
machine's baseline.json. These tests take no timings and need no baseline, and
leave out the scenarios in SLOW, which `pytest -m perf` still checks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from perfdata import load_expected
from scenarios import SCENARIOS, Scenario

EXPECTED = load_expected()

# The scenarios whose mean in baseline.json is 0.5 s or more, which would take
# most of the run's time. Keep it in step when adding slow scenarios.
SLOW = {
    "wild | 8 unknown: The House (all face-down) | Straight Flush | Four Fingers",
    "wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | Four Fingers",
    "standard | Keep 3, draw 5, Cerulean Bell | Full House + Straight Flush (any) | Four Fingers",
    "standard | 8 unknown: The Mark, 3 face-down, draw 5 | Full House + Straight Flush (any) | Four Fingers",
    "wild | 7 unknown: 2 face-down, draw 5 | Straight Flush | Four Fingers + Shortcut",
    "wild | 8 unknown: The House (all face-down) | Straight Flush | Four Fingers + Shortcut",
    "wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | Four Fingers + Shortcut",
    "standard | Keep 3, draw 5, Cerulean Bell | Straight Flush | Four Fingers + Shortcut",
    "standard | Keep 3, draw 5, Cerulean Bell | Full House + Straight Flush (any) | Four Fingers + Shortcut",
    "standard | 8 unknown: The Mark, 3 face-down, draw 5 | Straight Flush | Four Fingers + Shortcut",
    "standard | 8 unknown: The Mark, 3 face-down, draw 5 | Full House + Straight Flush (any) | Four Fingers + Shortcut",
    "standard | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) "
    "| Four Fingers + Shortcut + Smeared",
    "wild | 8 unknown: The House (all face-down) | Straight Flush | Four Fingers + Shortcut + Smeared",
    "wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) "
    "| Four Fingers + Shortcut + Smeared",
    "standard | 8 unknown: The Mark, 3 face-down, draw 5 | Full House + Straight Flush (any) "
    "| Four Fingers + Shortcut + Smeared",
    "standard | Best discard: 8 cards | Straight Flush | Four Fingers + Shortcut",
    "standard | Best discard: 8 cards | Flush + Straight + Full House (any) | Four Fingers + Shortcut",
    "standard | Best discard: 8 cards, two pairs | Straight Flush | Four Fingers + Shortcut",
    "standard | Best discard: 8 cards, two pairs | Flush + Straight + Full House (any) | Four Fingers + Shortcut",
    "standard | Best discard: 10 cards | Straight Flush | Four Fingers + Shortcut",
    "standard | Best discard: 10 cards | Flush + Straight + Full House (any) | Four Fingers + Shortcut",
}


def test_every_scenario_has_an_expected_result():
    ids = {s.id for s in SCENARIOS}
    assert sorted(ids - EXPECTED.keys()) == [], "check them, then run benchmark.py -k <id> --update-expected"
    assert sorted(EXPECTED.keys() - ids) == [], "expected.json has results for scenarios that are gone"
    assert sorted(SLOW - ids) == [], "SLOW names scenarios that are gone"


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s.id not in SLOW], ids=lambda s: s.id)
def test_scenario_result(scenario: Scenario):
    assert str(scenario.run()) == EXPECTED[scenario.id]

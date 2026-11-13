"""The perf scenarios' exact results, checked by a plain `pytest` (and so in CI).

test_perf.py checks the same results along with timings, which need this
machine's baseline.json. These tests take no timings and need no baseline, and
leave out the scenarios in SLOW, which `pytest -m perf` still checks.
"""

import pytest

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
    "standard | Best discard: 8 cards, two pairs | Straight Flush | Four Fingers + Shortcut",
    "standard | Best discard: 10 cards | Straight Flush | Four Fingers + Shortcut",
    "standard | Best discard: 10 cards | Flush + Straight + Full House (any) | Four Fingers + Shortcut",
    "standard | Best discard: 8 cards, Crimson Heart, played, Joker debuffed | Straight Flush "
    "| Four Fingers + Shortcut",
    "standard | Best discard: 8 cards, Crimson Heart, played, Four Fingers debuffed | Straight Flush | Shortcut",
    # The hand's sorts (Situation.sorts).
    "standard | 6 unknown: The Mark, 3 face-down, draw 3, sorted by rank | Full House + Straight Flush (any) "
    "| No jokers",
    "standard | 6 unknown: 1 face-down, draw 5, sorted by rank | Full House + Straight Flush (any) "
    "| Four Fingers + Shortcut",
    "standard | 7 unknown: 2 face-down, draw 5, sorted by rank | Straight Flush | Four Fingers + Shortcut",
    "standard | 7 unknown: 2 face-down, draw 5, sorted by rank | Full House + Straight Flush (any) "
    "| Four Fingers + Shortcut",
    "standard | 7 unknown: 2 face-down, draw 5, sorted by suit | Straight Flush | Four Fingers + Shortcut",
    "standard | 7 unknown: 2 face-down, draw 5, sorted by suit | Full House + Straight Flush (any) "
    "| Four Fingers + Shortcut",
    "standard | 7 unknown: 2 face-down, draw 5, sorted both ways | Full House + Straight Flush (any) "
    "| Four Fingers + Shortcut",
    "standard | 6 unknown: The Mark, 3 face-down, draw 3, sorted by rank | Straight Flush | Four Fingers + Shortcut",
    "standard | 6 unknown: The Mark, 3 face-down, draw 3, sorted by rank | Full House + Straight Flush (any) "
    "| Four Fingers + Shortcut",
    "standard | Best discard: 8 cards, 2 face-down, sorted by rank | Flush + Straight + Full House (any) | No jokers",
    "standard | Best discard: 8 cards, 2 face-down, sorted both ways | Flush + Straight + Full House (any) | No jokers",
    *(f"standard | 5 unknown: The House after its first discard, sorted by rank before and after | {t} | {j}"
      for t, j in (("Flush", "No jokers"), ("Full House", "No jokers"), ("Pair", "Four Fingers + Shortcut"),
                   ("Flush", "Four Fingers + Shortcut"), ("Full House", "Four Fingers + Shortcut"))),
    *(f"standard | 8 unknown: The House (all face-down), sorted by rank, the 3 in the middle discarded | {t} | {j}"
      for t in ("Pair", "Flush") for j in ("No jokers", "Four Fingers + Shortcut")),
    *(f"standard | Best discard: 8 cards, 2 face-down, sorted {s} | {t} | Four Fingers + Shortcut"
      for s in ("by rank", "both ways") for t in ("Straight Flush", "Flush + Straight + Full House (any)")),
    "standard | Best discard: 11 cards, holding a Full House | Flush + Straight + Full House (any) | No jokers",
    "standard | Best discard: 11 cards, holding no target | Flush + Straight + Full House (any) | No jokers",
    "standard | 7 unknown: 2 face-down, draw 5, sorted by rank, The Hook, played | Straight Flush | No jokers",
    *(f"standard | 7 unknown: 2 face-down, draw 5, sorted by rank, Cerulean Bell | {t} | No jokers"
      for t in ("Full House", "Straight Flush")),
    *(f"standard | 5 unknown: 5 face-down kept, sorted by rank, The Hook, 1 played | {t} | No jokers"
      for t in ("Pair", "Flush")),
    *(f"standard | 8 unknown: The House (all face-down), sorted both ways | {t} | No jokers"
      for t in ("Pair", "Straight Flush")),
}


def test_every_scenario_has_an_expected_result():
    ids = {s.id for s in SCENARIOS}
    assert sorted(ids - EXPECTED.keys()) == [], "check them, then run benchmark.py -k <id> --update-expected"
    assert sorted(EXPECTED.keys() - ids) == [], "expected.json has results for scenarios that are gone"
    assert sorted(SLOW - ids) == [], "SLOW names scenarios that are gone"


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s.id not in SLOW], ids=lambda s: s.id)
def test_scenario_result(scenario: Scenario):
    assert str(scenario.run()) == EXPECTED[scenario.id]

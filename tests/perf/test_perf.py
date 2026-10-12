"""Performance regression tests, run with `.venv\\Scripts\\python -m pytest -m perf`.

Each scenario in scenarios.py runs once and must give exactly the result in
expected.json (committed), in at most SLACK times its mean in baseline.json
(plus GRACE). baseline.json holds this machine's timings and isn't committed:
a scenario with no timing there is only checked for its result, and its
timing is saved there for the next run (see conftest.py). After changing the
engine, refresh it with
`.venv\\Scripts\\python tests\\perf\\benchmark.py --save tests\\perf\\baseline.json`
(see docs/performance/README.md). The default `pytest` run skips these.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from perfdata import BASELINE_FILE, load_expected, load_results  # noqa: E402
from scenarios import SCENARIOS, Scenario  # noqa: E402

pytestmark = pytest.mark.perf

EXPECTED = load_expected()
BASELINE = load_results(BASELINE_FILE)
SLACK = 3.0  # timing noise on a busy machine
GRACE = 0.1  # seconds, so millisecond scenarios don't fail on a hiccup


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario(scenario: Scenario, new_timings: dict[str, dict]):
    expected = EXPECTED.get(scenario.id)
    if expected is None:
        pytest.fail("no expected result: check it, then run benchmark.py -k <id> --update-expected")
    scenario.prepare()
    start = time.perf_counter()
    p = scenario.run()
    elapsed = time.perf_counter() - start
    assert str(p) == expected
    base = BASELINE.get(scenario.id)
    if base is None or "mean" not in base:
        new_timings[scenario.id] = {
            "runs": 1, "mean": elapsed, "stdev": 0.0, "median": elapsed, "min": elapsed, "max": elapsed,
            "times": [round(elapsed, 6)], "probability": str(p),
        }
        return
    assert elapsed <= SLACK * base["mean"] + GRACE, f"took {elapsed:.3f} s; baseline mean {base['mean']:.3f} s"

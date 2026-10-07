"""Performance regression tests, run with `.venv\\Scripts\\python -m pytest -m perf`.

Each scenario in scenarios.py runs once and must give exactly the result in
baseline.json, in at most SLACK times its baseline mean (plus GRACE). Timings
depend on the machine: after changing the engine or moving to another
machine, refresh the baseline with
`.venv\\Scripts\\python tests\\perf\\benchmark.py --save tests\\perf\\baseline.json`
(see docs/performance/README.md). The default `pytest` run skips these.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from scenarios import SCENARIOS, Scenario  # noqa: E402

pytestmark = pytest.mark.perf

BASELINE_FILE = Path(__file__).parent / "baseline.json"
BASELINE: dict[str, dict] = json.loads(BASELINE_FILE.read_text())["results"] if BASELINE_FILE.exists() else {}
SLACK = 3.0  # timing noise on a busy machine
GRACE = 0.1  # seconds, so millisecond scenarios don't fail on a hiccup


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario(scenario: Scenario):
    base = BASELINE.get(scenario.id)
    if base is None or "mean" not in base:
        pytest.skip("no baseline timing; run benchmark.py --save")
    scenario.prepare()
    start = time.perf_counter()
    p = scenario.run()
    elapsed = time.perf_counter() - start
    assert str(p) == base["probability"]
    assert elapsed <= SLACK * base["mean"] + GRACE, f"took {elapsed:.3f} s; baseline mean {base['mean']:.3f} s"

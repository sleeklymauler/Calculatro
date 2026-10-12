"""Saves the timings test_perf.py measured for scenarios with no baseline yet.

The first `pytest -m perf` on a machine checks only exact results, and its
timings become that machine's baseline.json; later runs are checked against
them. Scenarios added since are filled in the same way.
"""

from __future__ import annotations

import pytest

from perfdata import BASELINE_FILE, ROOT, add_results

NEW_TIMINGS = pytest.StashKey[dict[str, dict]]()


@pytest.fixture
def new_timings(request: pytest.FixtureRequest) -> dict[str, dict]:
    """Scenario id -> timing record (as benchmark.py saves them) to add to baseline.json."""
    return request.config.stash.setdefault(NEW_TIMINGS, {})


def pytest_sessionfinish(session: pytest.Session) -> None:
    new = session.config.stash.get(NEW_TIMINGS, {})
    if new:
        add_results(BASELINE_FILE, new, source="pytest -m perf", repeat=1)


def pytest_terminal_summary(terminalreporter, config: pytest.Config) -> None:
    new = config.stash.get(NEW_TIMINGS, {})
    if new:
        terminalreporter.write_line(
            f"perf: saved this run's timings for {len(new)} scenario(s) with none yet to "
            f"{BASELINE_FILE.relative_to(ROOT)}; later runs are checked against them. For steadier ones, run "
            f"benchmark.py --repeat 5 --budget 120 --save {BASELINE_FILE.relative_to(ROOT)}")

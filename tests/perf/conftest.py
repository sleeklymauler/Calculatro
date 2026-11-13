"""Stops `pytest -m perf` before it runs if this machine has no baseline yet.

test_perf.py checks timings against baseline.json, which benchmark.py
measures; a scenario selected to run with no timing there stops the whole run
at collection, saying what to measure.
"""

import pytest

from perfdata import BASELINE_FILE, ROOT, load_results

MEASURE = (r".venv\Scripts\python tests\perf\benchmark.py --repeat 5 --budget 120 "
           rf"--save {BASELINE_FILE.relative_to(ROOT)}")


@pytest.hookimpl(trylast=True)  # after -m and -k have deselected what won't run
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    ids = [item.callspec.params["scenario"].id for item in items
           if item.get_closest_marker("perf") and hasattr(item, "callspec")]
    if not ids:
        return
    baseline = load_results(BASELINE_FILE)
    missing = [id for id in ids if "mean" not in baseline.get(id, {})]
    if not baseline:
        pytest.exit(f"perf: no timings in {BASELINE_FILE.relative_to(ROOT)} yet; measure them first:\n  {MEASURE}",
                    returncode=pytest.ExitCode.USAGE_ERROR)
    if missing:
        lines = "\n".join(f'  {MEASURE} -k "{id}"' for id in missing)
        pytest.exit(f"perf: {len(missing)} scenario(s) have no timing in {BASELINE_FILE.relative_to(ROOT)}; "
                    f"measure them first (with -k, --save adds to the file):\n{lines}",
                    returncode=pytest.ExitCode.USAGE_ERROR)

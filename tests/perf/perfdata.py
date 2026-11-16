"""The perf scenarios' saved results, shared by benchmark.py and test_perf.py.

- expected.json (committed): each scenario's exact result. The same on every
  machine, so a change in it means the engine's answer changed.
- baseline.json (git-ignored): this machine's timings, which test_perf.py
  compares against. benchmark.py --save writes it, and `pytest -m perf`
  won't run without it.
"""

import json
import platform
from datetime import date
from pathlib import Path

import calculatro.engine

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
EXPECTED_FILE = HERE / "expected.json"
BASELINE_FILE = HERE / "baseline.json"


def load_expected() -> dict[str, str]:
    """Scenario id -> exact result, as a string."""
    return json.loads(EXPECTED_FILE.read_text(encoding="utf-8")) if EXPECTED_FILE.exists() else {}


def save_expected(expected: dict[str, str], order: list[str]) -> None:
    """Write `expected`, in the order of the scenario ids in `order` (others last)."""
    rank = {id: i for i, id in enumerate(order)}
    ordered = dict(sorted(expected.items(), key=lambda kv: rank.get(kv[0], len(rank))))
    EXPECTED_FILE.write_text(json.dumps(ordered, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def load_results(path: Path) -> dict[str, dict]:
    """The per-scenario timings in a file benchmark.py saved, or {} if there isn't one."""
    return json.loads(path.read_text(encoding="utf-8"))["results"] if path.exists() else {}


def save_results(path: Path, results: dict[str, dict], **settings) -> None:
    """Save timings with what they were measured on, and the benchmark `settings` used."""
    path.write_text(json.dumps({
        "date": date.today().isoformat(),
        "python": platform.python_version(),
        "machine": platform.processor() or platform.machine(),
        "engine": engine_path(),
        **settings,
        "results": results,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def add_results(path: Path, new: dict[str, dict], **settings) -> None:
    """Add timings to a saved file, keeping what it says they were measured with; or start one."""
    if not path.exists():
        save_results(path, new, **settings)
        return
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["results"].update(new)
    path.write_text(json.dumps(saved, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def engine_path() -> str:
    """Which engine was imported: relative to the repo when it's this one's. Its package's folder
    (calculatro/engine), or an older version's engine.py."""
    path = Path(calculatro.engine.__file__).resolve()
    if path.name == "__init__.py":
        path = path.parent
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:  # an older version, put first on PYTHONPATH
        return str(path)

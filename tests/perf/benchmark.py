"""Time the engine on the scenarios in scenarios.py.

    .venv\\Scripts\\python tests\\perf\\benchmark.py                 # all scenarios
    .venv\\Scripts\\python tests\\perf\\benchmark.py -k "Four Fingers" -k House
    .venv\\Scripts\\python tests\\perf\\benchmark.py --save tests\\perf\\baseline.json
    .venv\\Scripts\\python tests\\perf\\benchmark.py --compare tests\\perf\\baseline.json --markdown

Each scenario runs up to --repeat times, stopping early once its runs add up to
--budget seconds, so slow scenarios get fewer runs. A run longer than --timeout
seconds is cancelled and reported as "> timeout". One-off costs (building a
target's patterns, filling caches) are kept out of the timings; see measure().

To time an older version, check it out somewhere and put it first on
PYTHONPATH; the "Engine:" line shows which one was imported.

Each result is checked against tests/perf/expected.json, the exact results
(committed), and flagged if it differs; --update-expected writes them there
once a change is known to be intended. --save writes timings and results as
JSON (with -k, it adds them to the file); test_perf.py checks timings against
tests/perf/baseline.json, this machine's own and not committed. --compare
adds a column from such a file. See docs/performance/README.md.

Exits with status 1 if a result was not as expected or had no expected
result (unless --update-expected wrote it), else 0.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Timer

sys.path.insert(0, str(Path(__file__).parent))

from calculatro.engine import Cancelled
from perfdata import add_results, engine_path, load_expected, save_expected, save_results
from scenarios import JOKERS, SCENARIOS, Scenario

WARMUP = 1.0  # seconds; see measure()
NOISY = 0.25  # a standard deviation above this fraction of the mean, see --rerun-noisy


def measure(scenario: Scenario, repeat: int, budget: float, timeout: float) -> dict:
    """Timings of up to `repeat` runs; `timed_out` if a run hit `timeout`.

    The first run fills one-off caches. It's left out unless it took over
    WARMUP seconds, since then those costs are lost in the noise and
    repeating it would be expensive.
    """
    scenario.prepare()
    times: list[float] = []
    result = None
    warmup = True
    while len(times) < repeat and sum(times) < budget:
        cancel = Event()
        timer = Timer(timeout, cancel.set)
        timer.start()
        start = time.perf_counter()
        try:
            result = scenario.run(cancel)
        except Cancelled:
            return {"timed_out": timeout, "runs": len(times)}
        finally:
            timer.cancel()
        elapsed = time.perf_counter() - start
        if warmup:
            warmup = False
            if elapsed < WARMUP:
                continue
        times.append(elapsed)
    return {
        "runs": len(times),
        "mean": statistics.fmean(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0.0,
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "times": [round(t, 6) for t in times],
        "probability": str(result),
    }


def noisy(r: dict) -> bool:
    """Whether runs varied a lot, usually because something else was using the machine."""
    return "mean" in r and r["runs"] > 1 and r["stdev"] > NOISY * r["mean"]


def fmt(seconds: float) -> str:
    if seconds < 0.01:
        return f"{seconds * 1000:.1f} ms"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


def cell(r: dict | None, spread: bool = True) -> str:
    if r is None:
        return "-"
    if "timed_out" in r:
        return f"> {r['timed_out']:g} s"
    if spread and r["runs"] > 1:
        return f"{fmt(r['mean'])} ± {fmt(r['stdev'])}"
    return fmt(r["mean"])


def markdown(results: dict[str, dict], before: dict[str, dict] | None) -> str:
    """One row per deck, situation and target; a column per joker set."""
    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    for s in SCENARIOS:
        if s.id not in results:
            continue
        r = results[s.id]
        text = cell(r)
        if before is not None:
            text = f"{cell(before.get(s.id), spread=False)} → {text}"
        rows.setdefault((s.deck, s.situation.label, s.target_label), {})[s.jokers] = text
    head = ["Deck", "Situation", "Target", *JOKERS]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for (deck, situation, target), by_jokers in rows.items():
        lines.append("| " + " | ".join([deck, situation, target, *(by_jokers.get(j, "-") for j in JOKERS)]) + " |")
    return "\n".join(lines)


def positive(kind: type) -> Callable[[str], float]:
    """An argparse type: a number of `kind` above 0."""
    def parse(text: str):
        value = kind(text)
        if value <= 0:
            raise argparse.ArgumentTypeError(f"must be above 0, not {text}")
        return value
    parse.__name__ = kind.__name__  # argparse names the type in its "invalid ... value" errors
    return parse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", action="append", default=[],
                        help="only scenarios whose id contains this text (repeat to require several)")
    parser.add_argument("--repeat", type=positive(int), default=5, help="most runs per scenario (default 5)")
    parser.add_argument("--budget", type=positive(float), default=20.0,
                        help="stop repeating a scenario once its runs add up to this many seconds (default 20)")
    parser.add_argument("--timeout", type=positive(float), default=120.0,
                        help="cancel a run after this many seconds (default 120)")
    parser.add_argument("--save", type=Path, help="write the results to this JSON file")
    parser.add_argument("--rerun-noisy", action="store_true",
                        help=f"only re-measure the scenarios in the --save file whose standard deviation is over "
                             f"{NOISY * 100:.0f}%% of the mean, and update them there")
    parser.add_argument("--from", dest="source", type=Path,
                        help="don't measure: show the results in this JSON file (with --compare/--markdown)")
    parser.add_argument("--compare", type=Path, help="show results from this JSON file alongside")
    parser.add_argument("--markdown", action="store_true", help="print a Markdown table at the end")
    parser.add_argument("--update-expected", action="store_true",
                        help="write the results (measured, or from --from) to tests/perf/expected.json")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # "±" and "→" on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8")

    before = json.loads(args.compare.read_text())["results"] if args.compare else None
    results: dict[str, dict] = {}
    scenarios = [s for s in SCENARIOS if all(k in s.id for k in args.k)]
    if args.source:
        results = json.loads(args.source.read_text())["results"]
        scenarios = []
    elif args.rerun_noisy:
        if not (args.save and args.save.exists()):
            parser.error("--rerun-noisy needs an existing --save file")
        results = json.loads(args.save.read_text())["results"]
        scenarios = [s for s in scenarios if noisy(results.get(s.id, {}))]
    if scenarios:
        print(f"Engine: {engine_path()}", flush=True)
    expected = load_expected()
    width = max((len(s.id) for s in scenarios), default=0)
    wrong = 0
    for s in scenarios:
        r = results[s.id] = measure(s, args.repeat, args.budget, args.timeout)
        line = f"{s.id:<{width}}  {cell(r):>22}  runs={r['runs']}"
        if before is not None:
            line += f"  before={cell(before.get(s.id), spread=False)}"
            b = before.get(s.id)
            if b and "mean" in b and "mean" in r and r.get("probability") != b.get("probability"):
                line += "  RESULT DIFFERS"
        if "probability" in r:
            if s.id not in expected:
                line += "  NO EXPECTED RESULT"
                wrong += 1
            elif r["probability"] != expected[s.id]:
                line += "  NOT AS EXPECTED"
                wrong += 1
        print(line, flush=True)

    settings = {"repeat": args.repeat, "budget": args.budget, "timeout": args.timeout}
    if args.save and not args.source:
        if args.k and not args.rerun_noisy:
            add_results(args.save, results, **settings)
        else:
            save_results(args.save, results, **settings)
    if args.update_expected:
        ids = {s.id for s in SCENARIOS}
        found = {id: r["probability"] for id, r in results.items() if id in ids and "probability" in r}
        changed = sum(expected.get(id) != p for id, p in found.items())
        save_expected({**expected, **found}, [s.id for s in SCENARIOS])
        print(f"Updated {changed} of {len(found)} expected results in tests/perf/expected.json.")
    if args.markdown:
        print()
        print(markdown(results, before))
    if wrong and not args.update_expected:
        print(f"{wrong} result{'s' if wrong > 1 else ''} not as expected, or with no expected result.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

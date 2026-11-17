"""Time the engine on the scenarios in scenarios.py.

    .venv\\Scripts\\python tests\\perf\\benchmark.py                 # all scenarios
    .venv\\Scripts\\python tests\\perf\\benchmark.py -k "Four Fingers" -k House
    .venv\\Scripts\\python tests\\perf\\benchmark.py --save tests\\perf\\baseline.json
    .venv\\Scripts\\python tests\\perf\\benchmark.py --compare tests\\perf\\baseline.json --markdown
    .venv\\Scripts\\python tests\\perf\\benchmark.py --estimates -k sorted
    .venv\\Scripts\\python tests\\perf\\benchmark.py --cores 4 -k "Best discard"

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

--cores N runs each scenario as the app does with its "Cores used" setting at N:
as a task in one of N worker processes (pool.ProcessRunner), timed from here, so
with what crossing to the worker costs, and with the workers' memory counted.
The default, 1, calls the engine in this process, as the baseline was measured.

--estimates times, instead, the estimate the panel shows while a value's
exact odds are slow, for each odds scenario (from deals of the face-down
cards the hand's sorts tell about, or else from final hands drawn at random),
and how far it is from the expected result: an estimate's range is only
likely, so one outside it is flagged, not counted as wrong. A best-discard
scenario's search is given up as soon as it has its choices, and answers
from estimates (in Limits.search_estimate): how far below the best its
discard's exact odds are.
"""

import argparse
import gc
import json
import statistics
import sys
import time
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from threading import Event, Thread, Timer

from calculatro import spread, tasks
from calculatro.engine import Cancelled
from calculatro.pool import ProcessRunner
from calculatro.process import memory_used
from calculatro.worker import LIMITS, Control, Limits
from perfdata import add_results, engine_path, load_expected, save_expected, save_results
from scenarios import JOKERS, SCENARIOS, BestDiscard, Scenario

WARMUP = 1.0  # seconds; see measure()
NOISY = 0.25  # a standard deviation above this fraction of the mean, see --rerun-noisy
MEMORY_PERIOD = 0.01  # seconds between readings of the memory, see PeakMemory
MB = 2**20


class PeakMemory:
    """The most memory the process committed while it was running, read as the app's
    watchdog reads it (process.memory_used, with `runner`'s worker processes') every
    MEMORY_PERIOD seconds, so a peak shorter than that can be missed. `start` is the
    reading when it began."""

    def __init__(self, runner: ProcessRunner | None = None):
        self.used = memory_used if runner is None else lambda: memory_used() + runner.memory_used()
        self.start = self.peak = self.used()
        self._stop = Event()
        self._thread = Thread(target=self._run, name="peak-memory", daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(MEMORY_PERIOD):
            self.peak = max(self.peak, self.used())

    def __enter__(self) -> PeakMemory:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, self.used())


class Progress:
    """A "[3/40] " prefix for each scenario's line and, on a terminal, a line naming the scenario
    being run, written over by its result (or by the next one, if it prints none)."""

    def __init__(self, total: int, width: int):
        self.total = total
        self.digits = len(str(total))
        self.done = 0
        self.live = sys.stdout.isatty()
        self.blank = 2 * self.digits + 3 + width + 4  # the longest running line

    def prefix(self) -> str:
        return f"[{self.done:>{self.digits}}/{self.total}] "

    def start(self, id: str) -> None:
        self.done += 1
        if self.live:
            print(f"{self.prefix()}{id} ...".ljust(self.blank), end="\r", flush=True)

    def clear(self) -> None:
        if self.live:
            print(" " * self.blank, end="\r", flush=True)


def in_worker(scenario: Scenario | BestDiscard, runner: ProcessRunner, control: Control, timeout: float):
    """`scenario.run`, as a task in one of `runner`'s worker processes, with limits past `timeout`: the same
    result. A scenario with nothing cached has every worker's caches cleared first."""
    if getattr(scenario.situation, "cold", False):
        runner.clear_caches()
    if isinstance(scenario, BestDiscard):
        limits = Limits(search=2 * timeout, search_estimate=2 * timeout)
        found = spread.best_discards(runner, scenario.state(), scenario.target(), control, limits)
        return f"{found.probability} ({len(found.options)} options, first {found.options[0]})"
    state, discard = scenario.state()
    return runner.submit(tasks.Odds(state, scenario.target(), discard, 2 * timeout), control).result().probability


def measure(scenario: Scenario, repeat: int, budget: float, timeout: float,
            runner: ProcessRunner | None = None) -> dict:
    """Timings of up to `repeat` runs; `timed_out` if a run hit `timeout`. Also the
    peak memory over the runs (`peak_mb`, the whole process's, as the app's
    memory limit counts it) and its growth over the start (`growth_mb`).

    The first run fills one-off caches. It's left out unless it took over
    WARMUP seconds, since then those costs are lost in the noise and
    repeating it would be expensive.

    With `runner`, each run is a task in one of its worker processes (`in_worker`), whose
    memory is counted too. The first run fills that worker's caches, as the tasks of a
    row go to the worker that last had it.
    """
    if runner is None:
        scenario.prepare()
    gc.collect()
    times: list[float] = []
    result = None
    warmup = True
    timed_out = False
    with PeakMemory(runner) as memory:
        while len(times) < repeat and sum(times) < budget:
            cancel = Event() if runner is None else Control()
            timer = Timer(timeout, cancel.set if runner is None else cancel.cancel)
            timer.start()
            start = time.perf_counter()
            try:
                result = scenario.run(cancel) if runner is None else in_worker(scenario, runner, cancel, timeout)
            except Cancelled:
                timed_out = True
                break
            finally:
                timer.cancel()
            elapsed = time.perf_counter() - start
            if warmup:
                warmup = False
                if elapsed < WARMUP:
                    continue
            times.append(elapsed)
    used = {"peak_mb": round(memory.peak / MB, 1), "growth_mb": round((memory.peak - memory.start) / MB, 1)}
    if timed_out:
        return {"timed_out": timeout, "runs": len(times), **used}
    return {
        "runs": len(times),
        "mean": statistics.fmean(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0.0,
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "times": [round(t, 6) for t in times],
        "probability": str(result),
        **used,
    }


def estimates(scenarios: list[Scenario | BestDiscard], seconds: float) -> None:
    """Each scenario's estimate, its time, and how far it is from the expected result. A best-discard
    scenario's search is given up as soon as it has its choices, and answers from estimates in
    LIMITS.search_estimate: how much worse its discard is than the best."""
    expected = load_expected()
    width = max((len(s.id) for s in scenarios), default=0)
    errors = []
    losses = []
    scenarios = [s for s in scenarios if isinstance(s, (Scenario, BestDiscard))]
    progress = Progress(len(scenarios), width)
    for s in scenarios:
        progress.start(s.id)
        if isinstance(s, BestDiscard):
            s.prepare()
            found, elapsed, picked = s.estimate(LIMITS.search_estimate)
            line = (f"{progress.prefix()}{s.id:<{width}}  {fmt(elapsed):>8}  "
                    f"{float(picked) * 100:6.2f}% {found.options[0]}")
            if s.id in expected:
                best = Fraction(expected[s.id].split()[0])
                losses.append(float(best - picked))
                line += "  the best" if picked == best else f"  {float(best - picked) * 100:.2f} points below the best"
            print(line, flush=True)
            continue
        s.prepare()
        start = time.perf_counter()
        found = s.estimate(seconds)
        elapsed = time.perf_counter() - start
        if found is None:
            continue
        line = (f"{progress.prefix()}{s.id:<{width}}  {fmt(elapsed):>8}  "
                f"{found.mean * 100:6.2f}% ({found.low * 100:.2f} to {found.high * 100:.2f}, "
                f"{found.samples} {'hands' if found.draws else 'deals'})")
        if s.id in expected:
            exact = float(Fraction(expected[s.id]))
            errors.append(abs(found.mean - exact))
            line += f"  off by {(found.mean - exact) * 100:+.2f}"
            if not found.low <= exact <= found.high:
                line += "  OUTSIDE ITS RANGE"
        print(line, flush=True)
    progress.clear()
    if errors:
        print(f"{len(errors)} estimates: off by {statistics.fmean(errors) * 100:.2f} points on average, "
              f"{max(errors) * 100:.2f} at most.")
    if losses:
        print(f"{len(losses)} searches from estimates: {sum(x == 0 for x in losses)} found the best; "
              f"{statistics.fmean(losses) * 100:.2f} points below it on average, {max(losses) * 100:.2f} at most.")


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
    parser.add_argument("--estimates", type=positive(float), nargs="?", const=1.0, metavar="SECONDS",
                        help="time the estimates shown while the exact odds are slow instead, each with up to "
                             "SECONDS (default 1), and how far they are from the expected results")
    parser.add_argument("--cores", type=positive(int), default=1, metavar="N",
                        help="run each scenario as a task in one of N worker processes, as the app does with "
                             "\"Cores used\" at N (default 1: in this process, as the baseline is measured)")
    parser.add_argument("--update-expected", action="store_true",
                        help="write the results (measured, or from --from) to tests/perf/expected.json")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # "±" and "→" on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8")

    before = json.loads(args.compare.read_text())["results"] if args.compare else None
    results: dict[str, dict] = {}
    scenarios = [s for s in SCENARIOS if all(k in s.id for k in args.k)]
    if args.estimates:
        estimates(scenarios, args.estimates)
        return 0
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
    runner = None
    if args.cores > 1 and scenarios:
        runner = ProcessRunner()
        runner.resize(args.cores)
        if not runner.wait_ready():
            parser.error("the worker processes didn't start")
        print(f"Cores: {args.cores} (each scenario is a task in a worker process)", flush=True)
    expected = load_expected()
    width = max((len(s.id) for s in scenarios), default=0)
    wrong = 0
    progress = Progress(len(scenarios), width)
    for s in scenarios:
        progress.start(s.id)
        r = results[s.id] = measure(s, args.repeat, args.budget, args.timeout, runner)
        line = (f"{progress.prefix()}{s.id:<{width}}  {cell(r):>22}  "
                f"runs={r['runs']}  peak={r['peak_mb']:.0f} MB (+{r['growth_mb']:.0f})")
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

    if runner is not None:
        runner.close()
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

# Performance notes

How Calculatro's probability engine is kept fast, what it costs today, and
how to measure it.

- [engine.md](engine.md): the engine's design, from a performance point of
  view: what each technique saves, what was tried and dropped, and what to
  do when a new joker rule is slow.
- [benchmarks.md](benchmarks.md): measured timings for a fixed set of game
  situations. The current numbers come first (The Mark, Cerulean Bell and
  the partial-state reductions), then each earlier piece of work's before
  and after, newest first.
- [best-discard.md](best-discard.md): the panel's best-discard search,
  which compares every discard of the hand: how it avoids most of those
  calculations and shares the engine's work between the rest, with its
  timings.
- [jokers/](jokers/): one page per joker whose rules needed performance
  work.
  - [Four Fingers](jokers/four-fingers.md)
  - [Shortcut](jokers/shortcut.md)
  - [Smeared Joker](jokers/smeared.md)

  Pareidolia has no page and no benchmark scenarios: it only changes which
  cards are face cards, so hand patterns are the same with or without it.

Slow calculations never freeze the app: `__main__.run_watch` computes odds
in a background `worker.Job`, a new game state cancels it, and anything
slower than ~0.25 s shows "Calculating odds..." first. The goal here is to
keep that text rare, not to remove it. A row's odds are only calculated once
per selection while the hand, deck, jokers and blind stay the same (`known`
in [architecture.md](../architecture.md#the-app)), so the benchmarks' timings
are for a selection's first time.

## Measuring

The benchmark scenarios are in `tests/perf/scenarios.py`. Each one is a game
state built like the mod exports it, with face-down cards, a discard
selection and jokers, plus a target. It is timed through `evaluate_discard`,
the same call the app makes for one odds row. The "Best discard" scenarios
time `search.best_discards` instead, a click on a row's search button (see
[best-discard.md](best-discard.md)).

```
.venv\Scripts\python tests\perf\benchmark.py                       # everything, 5 runs each
.venv\Scripts\python tests\perf\benchmark.py -k "Four Fingers" -k House   # ids containing both
.venv\Scripts\python tests\perf\benchmark.py --compare tests\perf\baseline.json --markdown
```

- Each scenario runs up to `--repeat` times (default 5), stopping once its
  runs add up to `--budget` seconds (default 20). Use `--budget 120` for 5
  runs of every scenario. The output is mean ± standard deviation.
- One-off costs are kept out of the timings: building a target's patterns,
  and a first run that fills caches when it's quick. The "nothing cached"
  scenarios are the exception: they clear every cache before each run, to
  time the first odds for a newly tracked target or a new joker.
- A run is cancelled after `--timeout` seconds (default 120) and shown as
  "> 120 s". A timeout doesn't change the exit status. `--repeat`,
  `--budget` and `--timeout` must be above 0.
- `--save FILE` writes the results as JSON: each run's time, the mean,
  median and standard deviation, and the exact probability. `--compare FILE`
  shows a saved run alongside and flags any result that differs.
  `--markdown` prints a table like the ones in [benchmarks.md](benchmarks.md),
  and `--from FILE` prints it from a saved file without measuring.
- Background activity on the machine occasionally makes one run several
  times slower. `--save FILE --rerun-noisy` re-measures only the scenarios
  in FILE whose standard deviation is over 25% of the mean, and updates them
  in place.

### Regression tests

`tests/perf/test_perf.py` runs every scenario once. It checks the exact
result against `tests/perf/expected.json` and fails if a run takes over 3×
the mean in `tests/perf/baseline.json` (+0.1 s). These tests are marked
`perf` and skipped by a plain `pytest`:

```
.venv\Scripts\python -m pytest -m perf
```

A plain `pytest` (and so CI) checks the exact results without timings
(`tests/perf/test_expected.py`): every scenario but the slowest, the ones
in its `SLOW` set, whose mean is 0.5 s or more. Add a new scenario to
`SLOW` if it's that slow. It also checks that every scenario has an
expected result.

The two files are kept apart because only one of them is the same on every
machine:

- `expected.json` is committed: each scenario's exact probability. Every
  benchmark run is checked against it too, and flags a result that's "NOT
  AS EXPECTED" (or has "NO EXPECTED RESULT", for a new scenario), and then
  exits with status 1 (unless `--update-expected` wrote them). Once a
  change is checked to be intended, write the new results there:

  ```
  .venv\Scripts\python tests\perf\benchmark.py -k "Some scenario" --update-expected
  ```

  (`--from FILE --update-expected` takes them from a saved run instead.)
- `baseline.json` is this machine's timings, and git-ignored.
  `pytest -m perf` won't run until it has a timing for every scenario
  selected: it stops at collection and prints the commands to measure the
  missing ones. Measure it on a new machine, after adding scenarios, and
  after an engine change:

  ```
  .venv\Scripts\python tests\perf\benchmark.py --repeat 5 --budget 120 --save tests\perf\baseline.json
  ```

  With `-k`, `--save` replaces only those scenarios' timings in the file.
  The file records the date, Python version, CPU and which engine was
  imported. Delete it to start again, e.g. after a Python upgrade.

### Comparing with an older version

Check the older commit out next to the repo and put it first on
`PYTHONPATH`. The runner prints an `Engine:` line, so you can confirm which
code it imported. The scenarios only use APIs that existed before the Four
Fingers work (commit `7d2af71`).

```
git worktree add --detach ..\calculatro-before 7d2af71
$env:PYTHONPATH = "..\calculatro-before"
.venv\Scripts\python tests\perf\benchmark.py --save before.json
Remove-Item Env:PYTHONPATH
.venv\Scripts\python tests\perf\benchmark.py --compare before.json --markdown
git worktree remove ..\calculatro-before
```

## Adding a joker

1. Add a `JOKERS` entry in `tests/perf/scenarios.py`. Every scenario then
   also runs with it; add scenarios if the joker matters in situations not
   covered yet.
2. Run the benchmark. For slow scenarios, see "When adding a joker rule" in
   [engine.md](engine.md).
3. Write up anything that needed work in `jokers/<joker-name>.md`, using
   [four-fingers.md](jokers/four-fingers.md) as a template: what the rule
   does to the patterns, why it was slow, what fixed it, and what's still
   slow. Link it from the list above.
4. Add the new scenarios' results to `tests/perf/expected.json`
   (`--update-expected`), refresh your `tests/perf/baseline.json`, and
   update the tables in [benchmarks.md](benchmarks.md).

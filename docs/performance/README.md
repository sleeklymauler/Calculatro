# Performance notes

How Calculatro's probability engine is kept fast, what was tried, what's
still slow, and how to measure it.

- [engine.md](engine.md): the engine's design, from a performance point of
  view: what each technique saves, what was tried and dropped, and what to
  do when a new joker rule is slow.
- [best-discard.md](best-discard.md): the panel's best-discard search,
  which compares every discard of the hand: how it avoids most of those
  calculations and shares the engine's work between the rest, and what's
  still slow.
- [face-down.md](face-down.md): the odds with face-down cards in the hand's
  sorts: what made them fast, and the cases that are still slow.
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

These pages say what each change bought and what was tried and dropped, with
a rough figure where it helps, but they don't keep tables of timings: those
go stale with every engine change and depend on the machine. For current
timings, run the benchmark, or look in your `tests/perf/baseline.json` (see
[Regression tests](#regression-tests)). Older ones are in git history.

## Measuring

The benchmark scenarios are in `tests/perf/scenarios.py`. Each one is a game
state built like the mod exports it, with face-down cards, a discard
selection and jokers, plus a target. It is timed through `evaluate_discard`,
the same call the app makes for one odds row: one target, for one
keep-or-discard choice. The "Best discard" scenarios time
`search.best_discards` instead, a click on a row's search button (see
[best-discard.md](best-discard.md)).

Most scenarios start from one hand and vary what's unknown, the deck and the
jokers:

- **Hand:** 7♥ 8♥ 9♠ Q♣ K♦ 3♠ 5♦ J♥, hand size 8, from a 52-card deck.
- **Decks:** *standard*, a plain 52-card deck; *wild*, the same deck with
  4♣ 9♦ J♠ A♥ made Wild Cards. All four stay unseen, which is what makes it
  slow: the split by suit is off (see [engine.md](engine.md#splitting-by-suit)).
- **Unknown cards:** face-down cards kept, plus cards drawn after discarding.
  - *Keep 3, draw 5*: keep 7♥ 8♥ 9♠, discard the rest. The common case.
  - *6 unknown*: J♥ is face-down (The Wheel, The Fish); keep 7♥ 8♥ and
    discard 5.
  - *7 unknown*: 5♦ J♥ are face-down; keep 7♥ and discard 5.
  - *8 unknown, The House*: the whole hand is face-down, so nothing is
    known. On the standard deck this is a best case for the split by suit,
    since the suits are interchangeable.
  - *The Mark*: Q♣ K♦ J♥ are face-down, known to be face cards.
  - *Cerulean Bell*: nothing face-down; the first card discarded is the
    forced one.
  - *The Serpent*: 3 drawn whatever is discarded, after keeping 3 (a final
    hand of 6) or 7 (of 10).
  - *The Hook*: the selection played, then 2 of the 3 or 6 kept cards
    thrown away at random before the draw.
  - *Crimson Heart*: the selection played, then the debuffed joker swapped.
  - *sorted*: face-down cards narrowed down by the hand's sorts (see
    [face-down.md](face-down.md)).
- **"Any":** several targets combined, as in the "Any target" row.
- **Nothing cached:** every cache is cleared before each run, so building
  the target's patterns is timed too, as when a target is first tracked or a
  joker changes the rules. Their situation keeps the search itself small.
- **Jokers:** the combinations in `JOKERS` (`SEARCH_JOKERS`, fewer, for
  the best-discard scenarios).

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
- Each line also shows the scenario's peak memory, `peak=… MB (+…)`: the
  whole process's committed memory, read as the app's memory limit reads it
  (`process.memory_used`) every 10 ms over the scenario's runs, timed out or
  not, and in brackets how much it grew from the start. The benchmark has no
  memory limit, so a slow case shows what it would use if left alone. Caches
  that outlive a scenario count in the next one's start, so the growth is
  the fairer figure for one scenario; the peak is what the app's limit would
  have seen in this process.
- `--save FILE` writes the results as JSON: each run's time, the mean,
  median and standard deviation, the exact probability, and the peak memory
  (`peak_mb`, `growth_mb`). `--compare FILE`
  shows a saved run alongside and flags any result that differs.
  `--markdown` prints the results as a Markdown table (for a pull request,
  say), and `--from FILE` prints it from a saved file without measuring.
- Background activity on the machine occasionally makes one run several
  times slower. `--save FILE --rerun-noisy` re-measures only the scenarios
  in FILE whose standard deviation is over 25% of the mean, and updates them
  in place.
- `--estimates [SECONDS]` times the estimate the panel shows while a value's
  exact odds are slow (`engine.estimate_discard`, with up to SECONDS, 1 by
  default), for each odds scenario: from deals of the face-down cards in the
  hand's sorts, or else from final hands drawn at random. It shows how far
  each is from the expected result. Its range is only likely, so one outside
  it is flagged ("OUTSIDE ITS RANGE"), not counted as wrong. Measured on
  2026-11-11, with `-k sorted`: 112 estimates, off by 0.13 points on average
  and 0.99 at most, none outside its range, each within half a point either
  side in at most 0.95 s. On the 33 odds scenarios whose exact odds take
  0.25 s or more (all Four Fingers, Shortcut, The Mark or Cerulean Bell, and
  2 sorted): off by 0.16 points on average and 1.16 at most, none outside
  its range, each in at most 1 s.

### Regression tests

`tests/perf/test_perf.py` runs every scenario once. It checks the exact
result against `tests/perf/expected.json` and fails if a run takes over 3×
the mean in `tests/perf/baseline.json` (+0.1 s). These tests are marked
`perf` and skipped by a plain `pytest`:

```
.venv\Scripts\python -m pytest -m perf
```

They run in one process, not one per core as the other tests do: with `-m perf`,
`tests/conftest.py` gives `-n auto` no workers, so the timings aren't taken on a
busy machine. Don't pass a number of workers with `-n` for them.

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
   (`--update-expected`) and refresh your `tests/perf/baseline.json`.

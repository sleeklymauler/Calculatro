# Parallelism (P1)

How to run the app's calculations on more than one core without making it worse on a slow PC. This is the
plan for TODO item P1. It should also settle B4 and B5, and decide P2.

## Where the time goes

From `tests/perf/baseline.json` (2026-11-16, 743 scenarios, a Ryzen with 16 logical cores):

| Mean time | Scenarios |
|---|---|
| under 0.25 s | 651 |
| 0.25 to 1 s | 57 |
| 1 to 5 s | 25 |
| over 5 s | 10 |

- **Most values are already instant.** A value under 0.25 s gains nothing from another core, and sending it
  to one costs time. So the plan keeps the quick values quick and spreads out the slow ones.
- **The slowest are best-discard searches**, for example Four Fingers + Shortcut:
  - 8 cards with 2 face down, sorted by rank, "Any target": 20.9 s, peak 2.4 GB.
  - 10 cards, Straight Flush: 15.9 s.
  - 11 cards holding no target, "Any target": 4.6 s.

  A search evaluates hundreds of independent choices, so it splits well across processes.
- **A few single values are slow too.** The House with Four Fingers + Shortcut on the wild deck takes 14.4 s,
  and The House sorted both ways, Straight Flush, takes 10.7 s. One value is one engine call, so only splitting
  inside the engine would help these (phase 4, deferred).
- **A panel full of slow values waits on each value in turn.** B6 measured a panel of 30 values, 21 of them
  slow: its last approximation arrived after 18 s, because estimates run one at a time at 0.4 to 0.8 s each.
  These values are independent of each other.

## What "any PC" means here

Design for a slow PC: 2 cores and 4 threads, 4 to 8 GB of memory, a hard disk, and antivirus software that
scans each new process. Five rules follow from that.

1. **The game always wins the CPU.** The app sets itself to below-normal priority as it starts
   (`SetPriorityClass`, `BELOW_NORMAL_PRIORITY_CLASS`). Windows gives child processes the same class, so the
   workers inherit it. This helps today's single-process app as well, so it ships first (phase 0).
2. **1 core is today's app.** With "Cores used" at 1, there are no worker processes, no extra memory and
   nothing new to go wrong. Auto picks 1 on any PC with fewer than 8 logical cores.
3. **One memory budget for the whole app.** The "Memory limit" setting covers the app and all its workers
   together, not each process. Auto never picks more workers than the limit can hold (see Auto below).
4. **Nothing is left running.** Two things end the workers with the app, even if it crashes:
   - Each worker's listener thread is always reading its pipe. When the app is gone the read fails
     (`EOFError`), and the worker exits at once with `os._exit`, whatever its main thread is doing. This needs
     no polling and no handle to the app.
   - The workers are also in a Windows job object with kill-on-close, for a worker too stuck to do that.
     `multiprocessing` can't start a process suspended, so each worker is put in the job just after it
     starts. If the job can't be made or joined, the app logs it once and carries on: the pipe still covers it.
5. **A safe fallback.** Workers may fail to start (antivirus, a broken install) or die unexpectedly (3 times in
   a session). The app then logs this once and runs in a single process for the rest of the session. A worker
   the app ends itself (for memory, or a cancel it didn't answer) doesn't count towards the 3.

## Design

### Processes, not threads

The release ships the official embeddable Python, which has the global interpreter lock (GIL), so threads
can't use a second core. The app gets its own pool of worker processes:

- Each worker is a `multiprocessing.Process` (spawn) running `calculatro.pool._serve`.
- The app talks to each worker over its own `Pipe`.
- The app starts the workers at its first snapshot, at below-normal priority. It can't start them sooner:
  the number depends on the `cores` and `memory_gb` settings, which arrive in the snapshot. The mod writes one
  at the main menu (`in_run = false`), so they're ready long before the first blind. Starting them on demand
  would make the player wait for each one to start.
- A spawned worker imports the app's main module again, as `__mp_main__`. `calculatro/__main__.py` already
  does nothing on import (`main` is behind `if __name__ == "__main__"`), and it must stay that way: no worker
  may claim the mutex or open the log.
- The app keeps each worker's process handle (`Process.sentinel`, which has full access) for reading its
  memory and ending it. A pid could be reused; the handle can't.

The app process becomes the coordinator. It reads states, schedules tasks and writes the odds file. With 2 or
more cores it does no engine work itself.

Each worker holds its own `engine.Session` and its own functools caches (see "What each worker builds for
itself" below). Inside a worker:
- **The main thread** runs one task at a time, inside `gc_paused`.
- **A listener thread** reads messages from the app (cancel task *n*, clear caches, exit) and stops the task's
  `Attempt` in the worker.

The engine's `cancel` stays a cheap `threading.Event.is_set()`. A cancel reaches the engine within Python's
5 ms thread switch interval, plus the time the pipe takes.

**A worker has no log.** Under `pythonw` a worker's `sys.stderr` is `None`, and the app's log file is open in
the app only. So nothing a worker prints or raises would be seen. Instead:
- A task that fails sends back its formatted traceback and the key `Failures` goes by (the error's type and
  the places it was raised). A pickled exception loses its traceback, so it can't be re-raised in the app and
  logged as today. `Failures` takes the key and the text, and still logs each failure once.
- The worker's `sys.stderr` is replaced by one that sends each line to the app, which writes it to the log.
  The engine prints there when the hand's sorts fit no deal (`evidence.usable`), once a session. Every worker
  has its own session, so the app drops a line it has already logged.

**Cheap messages.** A state is sent to each worker once per job, under a number. Each task then names the
state by its number, the row (a hand's name, a combined target's hands, or a card query's text) and the
discard. Rows go by name because a `Target` holds a lock and a `CardQuery` holds closures, and neither pickles.
The worker looks them up again with `get_target`, `combine` and `parse_query`. Results are the dataclasses the
engine already returns (`DiscardOutcome`, `Estimate`, `Search`, `TooSlow`), which pickle as they are.

### One interface, two runners

`calculatro/pool.py` defines a `Runner` with `submit(task, priority) -> Future` and per-task cancellation. It
has two implementations:

- **`InProcessRunner`** keeps today's model: tasks run in the job's thread, under `worker.limited`. It's used
  with 1 core, for the fallback, and for most tests.
- **`ProcessRunner`** sends tasks to the worker processes.

The task functions are plain functions in a new `calculatro/tasks.py`, which both runners call: a value's exact
odds, a value's estimate, a whole search, a search plan, one search choice's odds, and `can_miss`. The engine
doesn't change.

`OddsJobs` and `SearchJobs` keep their `worker.Job` threads in the app process. Those threads now coordinate:
they submit tasks and wait for the results. `Job.cancel` cancels the job's queued and running tasks. The
structure of `__main__`, and the tests' patches (`cli.calculate`, `cli.best_discards`), stay as they are.

### Scheduling

One priority queue serves both jobs. When a worker is free, it takes the first task in this order:

1. The quick tries (`Limits.quick`), in the panel's order, with "Any target" after the hands.
2. The estimates of values found slow.
3. Search tasks: the player clicked and is waiting.
4. The exact tries (`Limits.exact`).

- **Exact tries may use every worker.** They never hold up a new state's quick tries or a click on search:
  either one is a new snapshot, and `OddsJobs.update` cancels the running job's tasks for every snapshot. The
  workers are free again within the cancel's 0.1 s. Keeping one worker back from them would only cost: with 2
  cores, the exact pass would be no faster than today's.
- **Quick work can take a worker from a search (phase 3).** A search isn't cancelled by a new selection, so
  once its choices are spread over every worker, a new state's quick tries could wait seconds for one
  choice's odds. If a quick try or an estimate has waited 50 ms with every worker on a search's tasks, the app
  cancels the search task that started last and puts it back in the queue. Search tasks are pure functions of
  their arguments, so running one again changes nothing but the time. In phases 1 and 2 a search is one task
  on one worker, and the others are never all taken by it.
- **Limits start when the task starts in a worker**, not when it's queued. The worker applies the time limit
  itself, with its own `worker.Watchdog`. A result says how long its task ran, which is what the quick pass's
  budget (`Limits.quick_pass`) adds up, not the time the app waited for it.
- **Tasks for a row go to the worker that last handled it**, when that worker is free, because its patterns are
  already cached there. Each worker builds a target's patterns once. That takes up to 90 ms with Four Fingers
  + Shortcut, and up to 0.35 s cold under Cerulean Bell (T5). That's enough to make a quick try that's fast
  today run out of time.
- **Workers warm up ahead.** When the tracked targets or the rule jokers change, each idle worker gets a
  low-priority task to build the patterns for the tracked targets.
- **With 1 core, the order is today's.** `_Builder`'s passes, and the quick-pass budget (`Limits.quick_pass`,
  B6), stay exactly as they are, so `test_report`'s expectations hold unchanged.

### What each worker builds for itself

Today one `Session` and one set of caches serve every value. With workers, each builds its own, so some work is
done once per worker where it was done once:

| What | Cost each | Shared how |
|---|---|---|
| A target's patterns (functools caches) | up to 90 ms, 0.35 s cold under Cerulean Bell | Warm-up, and rows kept to a worker |
| Whether the hand's sorts fit a deal (`Session.usable`) | up to 0.9 s (The House sorted both ways) | Sent by the app, below |
| A sampler of deals for estimates (`Session.deals`) | up to 1 s and 36 MB | Not shared |
| `_windows_for`'s windows, the pool's class keys | small | Not shared |

- **The check of the sorts is done once.** Every value that uses the sorts starts with it, so without sharing
  it, each worker's first quick try on a sorted hand would run out of time on the same 0.9 s. A result says
  whether its task checked the sorts and what it found. The app keeps that by the evidence, sends it to every
  worker (with the state, or as a message to those that have it), and the worker puts it in its
  `Session.usable`. This is also how the app answers `evidence_unchecked` for `_Builder`, which can no longer
  ask a session.
- **The samplers aren't.** A `Deals` is too large to send, so each worker that estimates on a sorted hand
  builds one: up to 1 s each, side by side, and 36 MB each (72 MB with `deals_kept = 2`). With 4 workers that's
  up to 290 MB of the memory limit and 4 s of CPU for a second of waiting. Measure it in phase 2 on B6's
  panel. If it costs more than it gains, keep a state's estimates to the workers that already hold its
  sampler, up to 2 of them.

### Memory

- **The app checks the total.** The app's `Watchdog` sums the committed memory of the app and every worker.
  `process.memory_used(handle)` reads each worker through its process handle and `K32GetProcessMemoryInfo`.
- **A worker's time limits are its own, its memory limit is the app's.** The worker's `Watchdog` has no memory
  reading. For memory, the app sends "stop task *n* for MEMORY", and the worker stops that task's `Attempt`
  with that reason, so `limited` and `search_within` answer `TooSlow(MEMORY)` as they do today.
- **Some tasks don't fit on their own.** The worst search peaks at 2.4 GB and the worst single value at
  2.1 GB, both over the default 2 GB limit, and 4 more scenarios peak over 1.2 GB. Two of those side by side
  go over the limit where each alone wouldn't. The next two rules are for that.
- **Over the limit, it stops the largest first.** It stops only the attempts of the worker that has grown most
  since it was idle, not every running attempt. That's the cause of B4. The existing grace (2 s, or until the
  memory falls) still applies.
- **A worker that doesn't shrink is replaced.** If a stopped worker hasn't come back under the limit by the end
  of the grace, the app ends it and starts a new one. Ending a process frees its memory at once, where Python
  would otherwise take a while to hand it back as it unwinds.
- **A value stopped for memory gets one more try.** If it was stopped while another task used the memory, it
  runs once more with nothing beside it before it's stored as `TooSlow(MEMORY)`. This fixes B4.
- **Caches.** When the rule jokers change, the app now sends "clear caches" to every worker, where it used to
  call `clear_caches()`. A worker whose idle memory is over a quarter of the limit is restarted between tasks:
  P7 measured 367 MB idle after a heavy session.

### Cancelling and hangs

- **Cancelling.** The `cancel` rule doesn't change: at most 0.1 s of CPU time between checks (`test_cancel`).
  Crossing a process adds only the time the message takes.
- **Ending a worker that doesn't stop.** If a worker hasn't acknowledged a cancel within 2 s, the app ends it
  and starts a new one. This also covers a hang inside a calculation (part of B13). The cancelled task's
  result isn't waited for: the job that cancelled it has already moved on.
- **Crashes.** If a worker dies mid-task, the app logs it through `Failures`. The value or search is answered
  as a failed job is today, and the worker is replaced.
- **A late result is dropped.** Every task has a number, and a result for a task the app has cancelled, or
  from a worker it has replaced, is ignored.

### Settings: "Cores used"

**In the mod**, it's a cycle on the settings page beside "Memory limit". Its values are `0` (Auto) and 1 up to
`min(8, love.system.getProcessorCount() - 1)`. It's stored as `cores` in `settings.json` (default 0) and
exported in each snapshot as `cores`, in and out of a run, as `memory_gb` is. The value exported is the
setting as it applies on this PC, clamped as `memory_gb` is (`memory.lua`). This bumps the state schema from
17 to 18 on both sides. The popup's text, which
must be ASCII and broken by hand:

```
How many of your CPU's cores the odds
and best-discard searches may use.
1 runs beside the game in one process,
using the least memory. More finish
slow odds and searches sooner.
Auto: chosen from your cores and memory.
```

**In the app**, `state.py` parses `cores` (`_number`, default 0). Changing it resizes the pool between tasks.
It isn't part of `odds_basis` or `search_basis`: more cores don't change any value, and phases 1 to 3 don't
make a single value faster. Both are made with `replace(state, ...)`, so a new field is part of them unless
`search_basis` resets it. It must (`cores=0`), or changing the setting would throw away every finished row.

**The cores the app may use** (`process.usable_cores`) are the bits set in its affinity mask
(`GetProcessAffinityMask`). `os.process_cpu_count()` isn't enough: on Windows it's `os.cpu_count()`, and
under `start /affinity 3` on the development machine it still answers 16 (checked 2026-11-16). The mod's
`love.system.getProcessorCount()` doesn't see the mask either. So the app clamps a `cores` above
`usable_cores - 1` down to it (to 1 at least), and the mod's row may offer more than the app will use on a PC
whose game is held to a few cores.

**Auto** (`process.auto_cores`) is the smaller of two numbers:

- **By CPU:** `usable_cores // 4`, at least 1 and at most 4. So up to 7 cores gives 1, 8 gives 2, 12 gives 3,
  and 16 or more gives 4.
- **By memory:** the memory limit ÷ 512 MB, so 1 GB allows 2 and 2 GB allows 4.

Phase 0's measurements may change these numbers.

## Phases

Each phase can ship on its own, in the weekly release cadence, and each one leaves 1 core behaving exactly as
the app does today.

### Phase 0: priority, and measurements

- `process.set_low_priority()`, called in `main` for `watch --game-pid` only.
- **Spawn cost:** a worker's start-up time and idle memory, on the development machine and on a simulated
  slow PC. To simulate one, start Balatro with `start /affinity 3` (two logical cores; Windows gives the app
  and its workers the same mask).
- **Round trip:** the time for one small task, against the median value (9 ms in the baseline).
- **A first look** (2026-11-16, development machine, the venv's Python 3.14.3, at below-normal priority): 4
  workers that import the whole app were all ready 0.15 s after the first was started, each holding 19 MB
  idle. A round trip of a small tuple over a `Pipe` took 0.02 ms. A worker started by a below-normal app was
  below normal itself. So only the slow PC and the release's Python are still open.
- **Check the release:** confirm that the release's Python can start a worker. Extend `build_release.ps1`'s
  check to start one worker and run one task.
- **Decide whether to go on.** If spawning takes more than about 2 s on the simulated slow PC, or a round trip
  more than about 1 ms (a tenth of the median value), rethink before phase 1.

### Phase 1: the pool, the setting, and a search in its own process

- `calculatro/pool.py` (`Runner`, `InProcessRunner`, `ProcessRunner`, `_serve`) and `calculatro/tasks.py`.
- `process.py`: the job object with kill-on-close, `memory_used(handle)`, `usable_cores` and `auto_cores`.
- `worker.Watchdog`: memory across processes, stopping the largest worker first.
- `SearchJobs` runs `search_within` as one task, with its `Progress` and both of its limits inside the worker.
- `_Builder` keeps its passes and their order. Each value's try or estimate becomes one task, which it submits
  and waits for before the next: where it calls `limited(...)` today, it calls the runner. So the odds use one
  worker at a time, and the search another. This alone settles **B5**: the search no longer shares a lock with
  the odds.
- The sorts' check goes through results and the app (see "What each worker builds for itself"), since
  `_Builder` has no session to ask from this phase on.
- Workers' errors and prints reach the log through the app.
- The mod's setting and the snapshot field, with the schema bumped to 18 on both sides.
- `calculatro odds --cores N`, and `benchmark.py --cores N`. The default stays 1, so `baseline.json` remains
  comparable.

### Phase 2: a report's values across workers

- `report._Builder` becomes a scheduler on the `Runner`, with the order above and rows sent back to
  the same worker.
- With tries side by side, a quick try that runs out before the sorts are checked no longer spares the others
  theirs: they've started already. The app cancels those still running for the same reason and marks them
  SLOW, as the quick pass does today for the ones after it.
- "Any target"'s task is cancelled once its bounds meet.
- Measure what the samplers of deals cost per worker, and decide whether to keep estimates to 2 workers.
- The retry with nothing beside it (**B4**).
- **Goal:** on B6's panel of 30 values (The House, sorted both ways) with 4 cores, the last approximation comes
  in about a quarter of today's 18 s.

### Phase 3: a search's choices across workers

`search.best_discards` is split into three steps:

- **Plan**, one task: the groups, the choices, a discard standing for each, and the relabel key of each
  (`_relabelled`, which costs up to 0.36 s).
- **Evaluate**, many tasks:
  - For "Any target", each hand's odds of each distinct choice, which give its bounds.
  - Then each choice's exact odds, in order of its upper bound. Tasks whose upper bound falls below the best
    found so far are cancelled.
  - `can_miss` tasks once the best is 1.
- **Reduce**, in the app: the same `best` and `winners` as today, then `_expand` and `_order`.

The search's limit (`Limits.search`) is for the whole search, not each task. The app holds it, from when the
search's first task starts in a worker, and at the limit cancels what's left and runs `estimated_best`. The
memory stop works the same way. The app keeps the `Progress` as results come in.

Each worker keeps an `engine.Reuse` for the search it's working on, dropped when the search ends.
`best_card_discards` evaluates every choice anyway, so its choices split across workers directly.
`estimated_best` stays one task, given the search's `Progress` by its data.

- **The result must be the same.** For every "Best discard" scenario, the options, their order and the odds
  must be identical with 1, 2 and 4 cores. The odds are exact, and `can_miss`'s seeded random draws only change
  how quickly it finds a miss. The order the choices finish in doesn't matter either: the winners are every
  choice whose odds equal the best, and a choice is only skipped once its upper bound is below the best found.
- **Quick work takes a worker from the search** (see Scheduling), which lands with this phase.
- **A risk to measure:** each worker builds its own `Reuse` memo, so peak memory may grow with the number of
  cores. The worst search already peaks at 2.4 GB on one core. If memory grows, a search uses only as many
  workers as the memory left allows.
- **Decide P2 here:** "Any target" on 11 cards (4.6 s) is about 400 independent unions, so it should come close
  to dividing by the number of cores. If it does, close P2.

### Phase 4 (deferred): inside one value

The slow single values (The House, The Mark sorted) would need the engine itself split up:
`_union_by_suit`'s terms, Crimson Heart's rule sets, and the posterior's deals. The parts depend on each other
and share caches, so this is left until phases 1 to 3 have been measured in players' hands.

## Results

Phases 0 to 3 were written on 2026-11-16. What they do now is in
[architecture.md](../architecture.md#worker-processes); this is what was measured, and where the code
differs from the plan above.

**Measured** (development machine, 16 logical cores):

- **Phase 0.** Four workers are ready in 0.28 s on two logical cores (0.41 s on one), holding 18 to 19 MB
  each. A round trip takes 0.02 ms. The release's Python 3.14.8 starts workers. All well inside the
  thresholds, so the plan went on.
- **Phase 2.** B6's panel (30 values, 21 slow) shows its last approximation after 17.5 s on 1 core, 9.0 s
  on 2 and 4.9 s on 4: the goal of about a quarter. The app and its workers peak at 213 MB with 4.
- **Phase 3.** Every best-discard scenario gives the same result on 1, 2 and 4 cores. On 4, searches over
  0.1 s take about half as long or less: 11 cards holding no target 1.9 s against 4.6 s, the slowest
  9.7 s against 20.9 s. The slowest search's memory grows from 2.4 GB to over 4 GB, as feared.

**Decided from that:**

- **Estimates aren't kept to 2 workers.** With a sampler built in each of 4 workers, B6's panel still
  scales almost with the cores, and uses little memory.
- **No warm-up.** A panel of 8 hands with Four Fingers + Shortcut, and one under Cerulean Bell, found no
  more values slow on cold workers than on warm ones, and took 0.1 to 0.3 s longer once. So the workers
  aren't sent the tracked targets ahead.
- **P2 stays open.** "Any target" on 11 cards divides by about 2.4 on 4 cores, not 4.
- **A search that runs out of memory uses fewer workers.** A choice stopped for memory is tried again
  with one fewer of the search's tasks side by side, down to one, and then the search is given up.

**Different from the plan:**

- `Target` pickles as it is. Only a card query goes by its text.
- The tests' patches moved to `tasks` (`tasks.evaluate_discard`, `tasks.best_discards`), where the
  functions now are.
- With workers, an estimate goes before the quick tries still waiting once the tries that ran out have
  taken `Limits.quick_pass` for each worker (`tasks.OVERDUE`). The plan had estimates always after
  them, which on 2 cores would show a full panel's first approximation later than 1 core does.
- The search's relabel keys for "Any target" itself are a task of their own, beside the hands' odds,
  rather than part of the plan task.
- `can_miss` isn't a task of its own: a choice's task tries it first when odds of 1 have been found.
- Quick work takes a worker from a search only when the search is spread over them (its tasks say so).
  A whole search in one task (`tasks.BestDiscard`) is never stopped for it.
- `tests/perf` has no report-level scenarios (B6's panel timed to its first value and last
  approximation). It was measured by hand.

**Not done:**

- An unexplained cost from phase 1: a 190 ms search took about 240 ms as one task in a worker. With the
  search spread over workers that task is no longer used above 1 core, so it wasn't chased.

**Checked in game** (2026-11-16): no stutter under `start /affinity 3`. With 2 usable cores the app
holds "Cores used" to 1, so that's the app on its own at below-normal priority.

## Considered, and not chosen

- **Free-threaded Python (3.14t).** Threads would run in parallel and share the caches, with no pickling. But
  python.org has no free-threaded embeddable package, only the full `python-3.14.Xt-amd64.zip`. The engine's
  caches and `Reuse` would need making thread-safe, and a thread can't be ended the way a process can.
  Revisit if an embeddable package arrives.
- **`concurrent.futures.ProcessPoolExecutor`.** It has no way to stop a running task. It can't tell how much
  memory each worker uses, and can't end or replace one worker. If one worker crashes, the whole pool breaks.
- **Using a per-process CPU-time limit in place of wall-clock time.** With each task in its own process and no
  more workers than cores, wall-clock limits stop depending on what else is running. That was B5. Wall-clock is
  also what the player waits for.

## Tests

- **Runner tests** (`tests/test_pool.py`, Windows only for processes):
  - A task's result, failure, time limit, cancel latency, and memory stop.
  - A worker crash and its replacement, and the fallback after repeated crashes.
  - Workers end when the app is killed: by the pipe alone, and by the job object alone.
  - Each task's arguments and results pickle.
  - A failing task's traceback, and a line a worker prints to stderr, reach the app's log once.
  - A result for a cancelled task, or from a replaced worker, is dropped.
- **`test_process.py`:** `usable_cores` under an affinity mask (a child started with one), and `auto_cores`
  for each number of cores and memory limit.
- **`test_watch.py`:** changing `cores` keeps the finished rows (`odds_basis`).
- **`test_cancel.py`:** run the same 15 cases through `ProcessRunner`, timing from the cancel to the reply.
- **`test_report.py`, `test_watch.py`:** pass unchanged with `InProcessRunner`. Add cases with a 2-worker
  `ProcessRunner` for the scheduling order, the sorts' check done once, the memory retry, and (phase 3) quick
  work taking a worker from a search.
- **`tests/perf`:** the search scenarios' exact results with `--cores 1, 2, 4` must all match `expected.json`.
  Add report-level scenarios: the B6 panel, timed to its first value, first approximation and last exact value.
- **The mod:**
  - `test_settings` for the new row and the clamping.
  - `test_state` for the field and schema 18.
  - `MOCK_G` / `mocked_game` gains `love.system.getProcessorCount`.
  - `test_lua_globals`, if anything new is read.
- **In game** (only someone with the game can do this):
  - The settings row's layout.
  - With `start /affinity 3` and a 1 GB limit: no stutter during a heavy panel. Judge by eye or with an FPS
    counter.
  - On a normal PC with 4 cores used: the panel fills sooner, and Task Manager shows the workers below normal
    priority and gone after the game exits.

## Docs to update as each phase lands

- `docs/architecture.md`: the Jobs, Limits, garbage collector, caches and session sections, the new
  workers' starting and stopping, and the priority.
- `docs/panel.md`: the settings page.
- `docs/performance/README.md`: `--cores`.
- `docs/performance/best-discard.md`: "What's still slow".
- `AGENTS.md`, rules that must not break: a task's arguments and results must pickle, and
  `calculatro/__main__.py` does nothing on import.
- `TODO.md`: P1, B4, B5 and P2.
- The README's settings (D7) once it's rewritten.

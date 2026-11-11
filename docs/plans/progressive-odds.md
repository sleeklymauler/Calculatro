# Plan: quick approximations, then exact odds

Status: drafted 2026-11-10. Stage 1 done and tried in game 2026-11-10 (see
[What stage 1 settled](#what-stage-1-settled)). Stage 2 done and tried in game 2026-11-11 (see
[What stage 2 settled](#what-stage-2-settled)). Face-down stage 5b done and tried in game 2026-11-11
(see [its notes](face-down-windows.md#what-5b-settled)): it brought the approximation pass and the first
estimates from sampling. Stage 3 next; see [Recommended order](#recommended-order).

## Summary

Some odds take seconds or minutes to work out exactly, and a few would never
finish in a useful time. Most have nothing to do with face-down cards: Four
Fingers + Shortcut with Straight Flush, "Any target" on a hand of 10-11 cards,
and best-discard searches on either. While one runs, the panel says
"Calculating...", and the values it shows are greyed out and are for the
previous selection.

This plan has the panel show every row quickly, and work out the exact odds
afterwards:

1. Rows that are quick to work out exactly (most of them) show exactly, as now.
2. A row that isn't shows an **approximation** at once, marked with an
   asterisk. Hovering over the value says it's approximate, with a range
   where there is one, and that the exact odds are still being worked out.
3. The app keeps working out the exact odds in the background. When they're
   ready, they replace the approximation and the asterisk goes away.
4. An exact calculation that runs over a time or memory limit stops. The
   approximation stays, and the popup says the exact odds would take too
   long.

Decisions already made (2026-11-10):

- **No accuracy setting.** The player never has to choose between speed and
  accuracy: they get the exact odds whenever they can be worked out within
  the limits, and an approximation, marked as one, until then.
- **The mark is an asterisk** on the value, with a popup on hover, as the
  settings' names have.
- **A plan of its own.** Showing an approximation first is for every row, not
  only those with face-down cards. The face-down plan's stage 5b is now only
  the approximations for face-down cards, plugged into what this plan builds.
- **"Face-down windows" will control only the hint.** Once the slow face-down
  cases have approximations, the odds always use the hand's sorts. That's done
  in the face-down plan's stage 5b.

## Today

- `run_watch` (`__main__.py`) starts a `worker.Job` for each settled snapshot.
  The job runs `build_report`, which works out every row (Keep, then Discard)
  one after another and returns them together. The panel gets a `pending`
  payload at once, then all the rows when the last is done. **One slow row
  holds every row**, even those that took milliseconds.
- `known` keeps each finished row by `odds_basis`, so a cancelled job's
  finished rows aren't lost, and selecting the same cards again is instant.
- In the mod, `panel_model` (`odds.lua`) greys out every value once the app
  has taken more than `patience` (0.3 s), and the title row says
  "Calculating...". Values are the previous snapshot's until the new ones
  arrive.
- A best-discard search runs in a job of its own. Its reply is final: the mod
  selects the first option as soon as it arrives.
- Nothing stops a calculation except a new snapshot. A player who stops to
  think leaves it running, for up to minutes, and in the worst face-down
  case using 9 GB of memory.

## Design

### What a value can be

Each value (a row's Keep or Discard) is one of:

- **exact**, as now;
- **approximate, refining**: an approximation, with the exact value still
  being worked out;
- **approximate, final**: an approximation, with the exact value given up on
  (over a limit);
- **too slow**: given up on, with no approximation. Shown as "?".
- **not yet**: nothing for this snapshot yet. The previous value stays,
  greyed out, as today, but for this row only.

### The job's passes

A job works through its rows in three passes:

1. **Quick pass.** Each row's exact odds, cancelled after `QUICK` seconds
   (about 0.25 s, just under the mod's `patience`, so a quick row never
   greys out). Most rows finish here, so they never show an approximation,
   and the panel doesn't flicker.
2. **Approximations** for the rows the quick pass didn't finish, each with a
   limit of its own (about 1 s).
3. **Exact pass.** Each remaining row's exact odds from the start, with
   `EXACT_LIMIT` (about 30 s) for each row.

A row the quick pass cancels loses at most `QUICK` seconds of work. The
engine's caches that live between calls (`_usable`, the windows cache)
survive it. It's simpler and safer than estimating the cost of each row
beforehand. Estimating it might come later if measurements show the waste
matters.

The order of the rows in each pass stays as today: Keep, then Discard, in the
panel's order.

### Limits

- **Time**: `EXACT_LIMIT` for each row's exact pass, and a limit for a
  best-discard search (about 30 s too).
- **Memory**: a limit on the app's memory (about 2 GB, or half the
  computer's memory if that's less). Windows reports both through
  `kernel32` (`K32GetProcessMemoryInfo`, `GlobalMemoryStatusEx`), which
  `process.py` already loads. Check what's returned after a calculation is
  given up: if Python's allocator keeps it, the limit has to be on the growth
  since the calculation began, or the next one would stop at once.
- **How**: a watchdog thread sets the job's `cancel` event when a limit is
  reached, saying why (`Job.cancel(reason)`). The engine raises `Cancelled` as
  now, so it needs no change; the job tells a new snapshot from a limit by the
  reason. Cancellation is under 75 ms everywhere measured.
- **Two jobs over the memory limit** (the odds and a search): stop the exact
  work of both. Their approximations are already done.
- **Garbage collector**: `engine._gc_paused` turns it off while any search
  runs. With exact passes running longer and more often, check that this
  doesn't let memory grow, and consider `gc.freeze()` or a raised threshold
  instead (the TODO's item).
- A row given up on is remembered for its `odds_basis`, like a finished one,
  so returning to the same selection doesn't try again.

### Rows as they finish

The job sends rows as they finish rather than all at the end. Each update is
a payload for the same snapshot id with the rows known so far, and
`"partial": true` until the last. Writes are at most about 10 a second (the
mod reads the file every 0.1 s). This is worth having before any
approximation: a quick row no longer waits on a slow one.

In the mod, staleness goes from the whole panel to each row: a row the
payload doesn't have keeps its previous value, greyed out after `patience`.
The title row says "Calculating..." while any row is still to come.

### The cache

`known` keeps, for each row: its exact odds, or its approximation and
whether the exact pass gave up. An approximation is shown at once when the
same cards are selected again, and its exact pass carries on.

### The odds file (`odds_schema` 6)

A value that isn't exact gets a mark beside it, which says what the popup
needs:

```json
{"key": "Straight Flush", "keep": "1.2%",
 "discard": "4%", "discard_mark": {"state": "refining", "how": "bounds", "low": "3.1%", "high": "5.8%"}}
```

- `state`: `refining` or `final`. A too-slow value is `"?"` with
  `{"state": "final"}`.
- `how`: what kind of approximation, as a code. The mod owns the popup's
  wording, as it does the settings'.
- `low` and `high`: the range, when the approximation gives one.
- The payload gets `"partial": true` while rows are still to come. No `null`,
  as always: anything absent is left out.
- A search's reply gets the same mark when its options come from
  approximations, and `"options": []` with `{"state": "final"}` when it gave
  up.

Bump `overlay.SCHEMA` and `odds_schema` together.

### The panel

- **The value.** An approximate value drops the decimal ("4%*" rather than
  "4.2%"). That says it's less precise, and leaves room for the asterisk in
  the same width (`DISCARD_VALUE_W` fits ">99.9%"). Check in game that `*`
  reads well in the game's font at that size; if not, draw a mark, as the
  arrow is drawn.
- **The popup** is on the whole value cell, since an asterisk alone is hard
  to hover, through `hover_popups` in the panel's colours. Each box is
  followed by `register_once`, like every other. Its lines, broken by hand
  and in ASCII, say:
  - that the value is approximate, and the range if there is one ("Between
    3.1% and 5.8%.");
  - while refining: "Still working out the exact odds.";
  - when final: that the exact odds would take too long, and why (time or
    memory).
- **Layout.** Values are updated in place (`calculatro_value`). A mark
  appearing or going changes the layout, so it's part of `layout_key`, and the
  rebuild animates.
- **Trend colour.** Green or red only when the comparison is certain: both
  values exact, or the ranges don't overlap. Otherwise the neutral colour.
- **Best discard.** Options found from approximations put the asterisk on the
  title row's "1/3", with the popup. A search that gave up says so on the title
  row ("Too slow", to fit `MESSAGE_W`).
- The console output (`format_report`) adds the asterisk too, with the range.

### Approximations

An approximation must be quick (about 1 s at most), and it must never be
shown as exact. In order of preference:

1. **A range that's certain**, from bounds. The value shown is a single
   number; the popup gives the range. Which number to show from bounds alone
   is a [decision](#decisions-to-make).
2. **An estimate with a margin**, from sampling deals and draws. Seeded from
   the state, so the same state always shows the same number. Using sampling
   at all is a [decision](#decisions-to-make).
3. **A simpler model**, like ignoring the hand's sorts for face-down cards.
   No range, so the popup can only say it's approximate.

Where they come from:

- **"Any target"** with several hands: at least the best single hand's
  odds, and at most their sum (or 1). Each single hand is a row of its own,
  so the bounds come almost free once those rows are done. `best_discards`
  already uses these bounds. The range can be wide (a Pair at 80% gives "80%
  to 100%"), which is why the value shown needs deciding.
- **Single targets that are slow** (Four Fingers + Shortcut with Straight
  Flush, The Mark): sampling draws, each checked with `_holds`, is the general
  fallback. Look for cheaper bounds first, like dropping Shortcut's or Four
  Fingers' patterns (fewer patterns can only lower the odds, so that's a lower
  bound).
- **Best discard**: compare the choices by their approximations and bounds,
  and work out exactly only those that could still be best, within the
  search's limit. When the limit is reached, the answer is the best by
  approximation, marked.
- **Face-down cards**: in the [face-down plan](face-down-windows.md#stages),
  stage 5b.

### The exact-odds rule

`AGENTS.md` says the engine counts exactly. It still does: what changes is
that the panel may show an approximation first. Reword the rule when stage 2
lands: exact odds are what the app works out, and any value that isn't exact
is marked as such, on the panel and in the console.

## Tests

- **The job's passes**, with a fake engine whose rows take set times: quick
  rows arrive in the first payload, slow ones show an approximation, then the
  exact value; a row over its limit ends final; a new snapshot cancels as
  now.
- **Limits**: the watchdog stops a calculation over its time limit, and over
  the memory limit (with a stand-in for the memory reading), giving the
  reason. A given-up row isn't tried again for the same `odds_basis`.
- **Every approximation against the exact odds** on the benchmark scenarios
  that finish: a range always holds the exact value; a sampled estimate's
  margin holds it in nearly all cases (seeded, so the test is stable).
- **The odds file**: the marks' shape, no `null`, partial payloads.
- **The mod** (`tests/test_overlay.py`): a partial payload greys only the
  missing rows; a marked value has the asterisk, the popup's text, and the
  neutral trend colour when the ranges overlap; the search's marked reply.
- **Benchmarks**: each scenario's approximation time, and how far off it is.
  `benchmark.py` keeps timing the exact odds, as now.
- In game: the asterisk and popup at every panel width, the flicker (or not)
  as rows go from approximate to exact, and the game's frame rate while an
  exact pass runs.

## Stages

1. **Rows as they finish, and limits.** No approximations yet. Each stage is
   worth having on its own, and this one most of all:
   - payloads per row (`partial`), and staleness per row in the mod;
   - the watchdog: time and memory limits, `Job.cancel(reason)`;
   - "too slow" values ("?") and a search that gives up, with their popups;
   - the cache keeps given-up rows;
   - `odds_schema` 6;
   - the garbage collector check.

   After this stage nothing runs forever or exhausts memory, a quick row is
   never held up by a slow one, and the panel says when something is too
   slow rather than "Calculating..." for ever.
2. **Approximations, starting with "Any target".**
   - The three passes, the marks in the odds file, the asterisk and popup,
     the trend colour rule, the console output.
   - The first approximation: "Any target"'s bounds from the single rows.
     It's the cheapest, so this stage is about the mechanism, end to end.
   - Reword the exact-odds rule in `AGENTS.md`, and document it in
     `docs/probability-model.md` and `docs/panel.md`.
3. **Approximations for the other slow cases**, in order of how often players
   meet them, each with its benchmark scenario's error:
   - best discard with approximations (the 11-card "Any target", Four
     Fingers + Shortcut);
   - single targets with Four Fingers + Shortcut;
   - The Mark.

   Sampling, if it's decided on, comes here.

## What stage 1 settled

Decided or found while doing stage 1, where it differs from or adds to the design above:

- **Two of the three passes.** Without the quick pass a slow row still held up the rows after it for
  up to `EXACT_LIMIT`, so stage 1 has the quick pass and the exact pass, and stage 2 adds only the
  approximations between them (`report.build_report`).
- **Values, not rows.** Partial payloads go by value: a value still to come is left out and marked
  `{"state": "pending"}`, so the mod can tell it from a row with no Discard value (nothing selected)
  and keep the arrow. A value given up on is `"?"` with `{"state": "final", "limit": "time"}` (or
  `"memory"`); the popup needs the limit. Stage 2's marks add `refining`, `how`, `low` and `high`.
- **The memory limit is on the app's memory**, not its growth: Python gave the 9 GB case's memory back
  to Windows as it unwound (2 GB to 25 MB, in 0.4 s). After a memory stop the watchdog waits until the
  memory falls below the limit, or 2 s, before stopping anything else for memory, so another job's
  next part isn't stopped for the memory of the one before.
- **The garbage collector** needed no change: see "The garbage collector" in
  [architecture.md](../architecture.md#the-app).
- **Searches given up on** are remembered until their `search_basis` changes, like values, so asking
  again is answered at once. The title row's "Too slow" lasts until the selection changes.
- **The time limit stays per value** (decided 2026-11-10, after trying it in game). With every value
  slow, as on a 10-card hand under The House sorted, the "?"s arrive one by one, up to 30 s apart, and
  the rest show "-" meanwhile: up to 14 x 30 s in all. A shared budget for the whole exact pass was
  considered and turned down: once the approximations fill every row, waiting costs the player
  nothing, and a shared budget would give up on values that would finish. The "-" are fine until the
  whole plan is done.
- **Tried in game** (2026-11-10): values arriving as they're found without flicker, the greying of
  values still to come, "?" from both limits with their popups, and searches giving up with "Too
  slow" and its popup. Still open: the limits' values on a slower computer (see
  [Decisions to make](#decisions-to-make)).

## What stage 2 settled

Decided or found while doing stage 2, where it differs from or adds to the design above:

- **The value shown from bounds** is the recommended one: the lower bound with a "+", in whole
  percents rounded down so it stays certain ("80%+*"). Below 1% it's the upper bound instead, with a
  "<" ("<6%*"), which says more than "0%+". The app sends it without the asterisk
  (`overlay.approximate`); the mod adds it.
- **No approximation pass yet.** "Any target"'s bounds come from the hands' values, so `build_report`
  derives them whenever it makes a report, rather than storing them: they narrow as each hand is
  found, and come back at once with the same cards. The pass between the quick and exact passes
  arrives with the first approximation that takes time to work out (face-down stage 5b, or stage 3),
  and with it a place in `known` for approximations.
- **"Any target" goes after the hands** in both passes, not first as in the panel: its bounds need
  theirs. When they meet (a hand is certain, or none can be made, which is common: holding a pair
  makes "Any target" with Pair certain) they're the exact odds, and it isn't worked out at all.
- **Only a value its quick pass found slow shows an approximation**, so a quick "Any target" never
  flickers through one. `known` remembers it as found slow (`report.SLOW`): with the same cards again
  it shows its approximation at once and skips the quick try.
- **A popup's text is updated in place.** Whether a value has a popup is layout, but what it says
  isn't, so the range narrowing doesn't rebuild the panel (`calculatro_popup_of`; `hover_popups`
  reads the popup when hovered). An approximation appearing or going still rebuilds it once.
- **The console** shows the value as the panel does, with the range in the line's note, naming Keep
  and Discard when both are approximate.
- **A search's marks** (the asterisk on "1/3") wait for stage 3, which is when a search first answers
  from approximations.
- **`odds_schema` 7**, for the new marks.
- **Tried in game** (2026-11-11), with Four Fingers + Shortcut tracking Full House and Straight Flush:
  under Cerulean Bell and The Mark (an approximation for a few seconds, narrowing in place when
  Straight Flush arrives, then exact), and under The House with 4 Wild Cards in the deck (about 25 s
  of approximation), and with the exact limit lowered to see one given up on. The asterisk reads well
  at the value's width, the popups' lines fit, and the panel rebuilds cleanly as an approximation
  appears and goes. Still open: how often players meet a slow "Any target" in real runs.

## Recommended order

Across this plan and the face-down plan:

1. **This plan's stage 1.** It makes the face-down slow cases safe (they
   stop instead of running for minutes or exhausting memory), and it helps
   everyone.
2. **This plan's stage 2** (done). Stage 5b needs somewhere to show its
   approximations. This stage builds that with the simplest approximation, so
   5b is only about face-down cards.
3. **Face-down stage 5b** (done): approximations for the face-down cases, then
   "Face-down windows" controlling only the hint. The odds then always use the
   sorts, so every player meets those cases, which is why it waits for the
   limits and the approximations. The plan's details are fresh, and its slow
   cases are the worst (minutes, gigabytes).
4. **This plan's stage 3**: the other slow cases. These could come before 5b
   instead: Four Fingers + Shortcut and big hands don't depend on a boss.
   But their slow cases take seconds rather than minutes, and stage 1 already
   stops them blocking the quick rows.
5. **Then revisit parallelism** (the TODO). With slow rows no longer holding
   up the panel, what's left to gain is only how soon the exact odds arrive,
   so measure first.

## Decisions to make

- ~~**Sampling.**~~ Allowed (decided 2026-11-11), only for approximations,
  never in place of the exact pass: face-down stage 5b's estimates sample deals
  of the face-down cards. Their range is only likely, so it's "Likely ... to
  ..." in the popup and never colours the trend
  ([5b's notes](face-down-windows.md#what-5b-settled)).
- ~~**The value shown from bounds alone.**~~ Settled in stage 2: the lower
  bound with a "+" (see [What stage 2 settled](#what-stage-2-settled)).
- **Whether a search answers before its exact pass.** Recommended: no. The
  mod selects cards when a reply arrives, and changing the selection under
  the player when the exact answer differs is worse than waiting. A search
  waits up to its limit, then answers by approximation, marked.
- **The limits' values**: `QUICK`, `EXACT_LIMIT` and the memory limit, tuned
  in game on a slower computer than the development one.

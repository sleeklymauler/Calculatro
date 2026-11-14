# Architecture

How the two halves of Calculatro are built, how they start and fail, and the contract between
them. The panel has its own page, [panel.md](panel.md), and the odds theirs,
[probability-model.md](probability-model.md).

Two halves are connected by two JSON files in `%AppData%\Balatro`:

```
Lovely mod (Lua)  -- calculatro_state.json -->  Python app
                  <-- calculatro_odds.json ---
```

## The mod

`mod/Calculatro/lovely.toml` registers each Lua file as a module, `calculatro.<file>` (`calculatro`
for `calculatro.lua`). Lovely's names are keys in `package.preload`, which every mod shares, so a
bare `json` would clash with other mods'. A new file must be registered there. It also patches
`Game:update` to call `calculatro.lua` every frame, and `Controller:key_press_update` for the
panel's hotkey (`C`).

`Calculatro.json` and `steamodded.lua` are only for Steamodded, which the mod doesn't need: with
Steamodded installed, they give the mod's entry in its mod list a version, author, description and
icon (`assets/1x/icon.png`, 34×34, and `assets/2x`, 68×68), instead of "(Lovely Mod)".
`steamodded.lua` isn't a Lovely module, so it's left out of `lovely.toml` and the modules' tests.

The modules share one table, `Calculatro` (`core.lua`; it's what `require('calculatro')` returns).
Each adds its functions to it and calls the others' through it, requires the modules it uses (they
have no cycles, and each loads on its own), and returns only what other modules need besides
(helpers and constants, imported into locals).

| Module | What it holds |
|---|---|
| `core` | settings and state |
| `json` | the encoder and decoder |
| `tracked` | what's tracked |
| `builder` | the card query builder |
| `snapshot` | what's exported |
| `search` | best discard's mod side |
| `worker` | the thread that uses the state, odds and settings files |
| `files` | the state and odds files, the Windows FFI |
| `log` | `guard`, `report` |
| `app` | starting the app |
| `widgets` | the definition nodes and sizes the views share |
| `uibox` | what's done to a built box, `remove_box` |
| `anim` | the panel's animation |
| `memory` | the "Memory limit" setting's choices, from the computer's memory (through the FFI) |
| `odds`, `editor`, `settings` | the panel's three views: model, definition (`settings` also keeps the settings file) |
| `panel` | placing and refreshing it |
| `observe` | what the hand's order has shown about its face-down cards |
| `hint` | the box over a hovered face-down card |
| `calculatro.lua` | only the hooks |

### Snapshots

`Calculatro.update` throttles itself to ~5 snapshots/sec, serializes Balatro's global `G` with its
own JSON encoder, and writes `%AppData%\Balatro\calculatro_state.json` only when the output changes:
it writes to `.tmp`, then does `MoveFileExW` over it. A write that fails, as when the app has the file
open, is tried again with the next snapshot, and reported after 10 in a row. Each written snapshot
gets an `id` (`<session>:<seq>`).

The files are used on a thread of its own (`worker.lua`), since creating the `.tmp` now and then takes
Windows hundreds of milliseconds, which froze the game on the main thread. Reading the odds file has
to be there too: `love.filesystem` takes a lock (PhysFS's) that a stalled write in another thread holds,
so a read on the main thread froze the game just as long. It skips to the newest snapshot it was given,
and `Calculatro.check_worker` hears each frame what was written (`state_id` is the last one written, not
the last one handed over) and gets any new odds file. The thread is its own Lua state, so it has its
own copy of the FFI calls. Nothing may escape it, since an error in a thread stops the game
(`love.threaderror`). LÖVE doesn't stop a thread when the game restarts (`love.event.quit('restart')`,
as Steamodded does after its mods are changed), and named channels outlive the restart too. So each
start numbers its thread in the `calculatro_file_thread` channel and names the thread's channels by that
number. A thread from before the restart sees the number change and ends, and it can't take the new
thread's requests in the meantime. Without `love.thread`, or once the thread has stopped, the mod uses the files
itself (`Calculatro.replace_file`, `read_odds`).

The player's settings ([panel.md](panel.md#settings)) are kept in `calculatro_settings.json`, which only
the mod uses. It's read once as the mod starts, before the thread does, and written on the thread
(its newest settings, in the same pass as the newest snapshot), or by the mod itself without one. The
app learns the one it needs, "Memory limit", from the snapshot (`memory_gb`).

### File access

All the mod's file access goes through `love.filesystem` with names relative to the save directory,
or Windows' wide functions through the FFI, never `io.open`/`os.rename`/`os.remove`: the save
directory's path is UTF-8, which those read in the ANSI code page, so they fail if the Windows
username isn't plain ASCII (`test_mod_never_uses_a_function_taking_an_ansi_path`).

## The app

`source.py` polls the file's mtime/size → `state.py` parses it into frozen `GameState`/`Card`
dataclasses → `engine.py` computes exact odds → `report.py` collects them into a `Report` →
`__main__.py` formats it for the console and `overlay.py` for the mod.

- **Jobs.** `__main__.run_watch` runs the odds in a `worker.Job` (daemon thread). Each new state
  cancels the running job (the engine checks the `cancel` Event and raises `engine.Cancelled`), and
  odds slower than ~0.25 s print the summary first with "Calculating odds..." and follow when ready.
  Keep new slow code paths cancellable. Best-discard searches run in a second job, cancelled only
  when `search_basis` changes ([panel.md](panel.md#best-discard-button)).
- **Values as they're found.** `build_report` works out each value (a row's Keep or Discard) in three
  passes, so a quick one is never held up by a slow one: each first with `Limits.quick` (0.25 s), unless
  a try before it ran out of time still checking the hand's sorts, which it would check first too
  (`engine.evidence_unchecked`); then
  an estimate of each that didn't finish, if it has one, in about `Limits.approx` (1 s); then those,
  from the start, with `Limits.exact` (30 s) each. The order in each pass is
  the panel's, Keep then Discard, but with "Any target" after the hands. Once the quick tries that
  ran out have taken `Limits.quick_pass` (1 s) in all, the quick pass estimates the values it has
  found slow, and then each one straight after its quick try, so a panel of many slow values shows
  its first approximation in about 2 s rather than after every value's quick try (8 s for 30 values). The job reports each partial
  `Report` that shows something new (`Control.progress`), and `run_watch` sends it to the panel as a
  `partial` payload, at most every `PARTIAL_INTERVAL` (0.1 s), none if every value is found sooner.
  The console prints only the finished report.
- **Approximations.** A value that doesn't finish its quick try shows an approximation
  (`report.Approx`: its range `low` to `high`, `how` it was found, an `estimate` if it's one, and the
  `limit` once the exact odds are given up on) until its exact odds arrive, and keeps it if they're
  given up on ([probability-model.md](probability-model.md#approximations)). "Any target"'s bounds
  come from its hands' values, so they need no pass of their own: `build_report` derives them whenever
  it makes a report. A target's estimate (`engine.estimate_discard`), from a sample of the face-down
  cards' deals or else of final hands, is made once, in the pass between, and kept in `known`.
- **Limits.** A job's function gets a `worker.Control`, which is the engine's `cancel` (an Event's
  `is_set` and `wait`) and runs parts with limits (`Control.limited`, `worker.limited`): each part is an
  `Attempt`, a cancel event of its own that the job's cancellation, its deadline or the memory limit
  sets, saying which (`reason`: `TIME`, `MEMORY`, or the job's). One `worker.Watchdog` thread checks
  them every 20 ms: a deadline passed, or the app's committed memory (`process.memory_used`) over
  `process.memory_limit`, which stops every running attempt, of both jobs. The limit is the player's
  "Memory limit" setting (1, 2, 4 or 8 GB; 2 by default), or half the computer's memory as installed
  (`process.installed_memory`) if that's less. Each state sets it (`run_watch`'s `memory_limit`), and
  it's part of `odds_basis` and `search_basis`, so changing it tries again what was given up on. A part stopped by a limit is `worker.TooSlow`; one stopped by a new state still
  raises `Cancelled`. Python gives a stopped calculation's memory back to Windows as it unwinds (2 GB in
  about 0.4 s), so the limit is on the app's memory, not its growth; the watchdog stops nothing for
  memory again until it falls below the limit or 2 s pass (`grace`), so another job's next part isn't
  stopped for the memory of the one before. A search is one part, with `Limits.search` (30 s), and if
  it's given up on, a second answers from estimates of what it had found, in about
  `Limits.search_estimate` (5 s; `__main__.search_within`, `search.estimated_best`). The engine needs
  nothing for any of this: it raises `Cancelled` as before.
- **The garbage collector.** `engine._gc_paused` turns it off while any search runs, and with the
  limits none runs for more than about 30 s. The worst case measured (the 9 GB face-down case, stopped
  at 2 GB after 18 s) left 6 MB of cycles for the collector once it was back on, so pausing it doesn't
  let memory grow.
- **Odds already worked out.** `build_report` stores each value in `known` as soon as it's
  done, given up on (`TooSlow`), or found slow by the quick pass (`SLOW`: a repeat goes straight to the
  exact pass, showing its approximation at once), and its estimate (under `report.ESTIMATE`, or None if
  it has none), by the row and the cards selected, so going back to an earlier
  selection calculates nothing, and values finished before a job was cancelled aren't lost. A value
  given up on isn't tried again until `known` starts afresh, nor a search given up on (answered from
  estimates or not) until its `search_basis` changes: asked for again, it's answered at once. `run_watch` starts a new `known` whenever
  `odds_basis` changes (`search_basis` without the hand's order, since sorting the hand doesn't change
  the odds, `test_odds_do_not_depend_on_the_hands_order`). A row's key uses the cards, not their
  positions, and `row_key`: a query's text, not the query, since two parses of one may not be equal. There's no size limit:
  one hand has only so many selections, and everything goes at the next discard.
- **The engine's caches.** The hands' patterns, and what the engine works out from them, are kept in
  functools caches with no size limit. What they hold is small, but it's built in the middle of a calculation,
  and keeps the memory of that calculation's temporary objects from going back to Windows (about 200 MB after
  every benchmark scenario once), which the memory limit counts. So `run_watch` empties them
  (`engine.clear_caches`) whenever the rule jokers held change (`rule_jokers`: Four Fingers, Shortcut, Smeared,
  Pareidolia, debuffed or not, since Crimson Heart moves its debuff every hand). Building them again costs up to
  about 0.2 s, with Four Fingers and Shortcut.
- **Failures in a job.** A job that fails (anything but `Cancelled`) doesn't stop the app: `run_watch`
  logs the traceback on stderr (once per run for each exception type and stack; a failure to log is
  ignored) and answers as if there were nothing to show, odds without values
  (`overlay.odds(state, None)`) or a search with no options, so the panel isn't left "Calculating..."
  or "Searching...".
- **Failures on the main loop.** Any other error on `run_watch`'s main loop (a bad state that got past
  the parser, the console failing) is logged the same way and the loop carries on, so the next state
  is still answered.
- **Settled hands only.** Odds are only calculated while the hand is `settled` (`SELECTING_HAND`, or
  no state given). While cards are dealt, a played hand scores or a pack is open, the hand changes
  card by card, so the app only answers `pending` and the panel keeps the last odds greyed.

## Starting and stopping the app

`powershell -ExecutionPolicy Bypass -File scripts\install_mod.ps1` (5.1 or 7) copies `mod/Calculatro` to `%AppData%\Balatro\Mods` (it requires the
Lovely injector DLL next to `Balatro.exe`). It also writes the venv's `pythonw.exe` path to the
installed mod's `launch.txt` (skip with `-NoAutoStart`).

- **Starting.** On its first frame the mod starts `pythonw -m calculatro watch --game-pid <pid>`
  through LuaJIT's FFI (`ShellExecuteW`; `os.execute` would flash a console). Lovely loads a mod from
  a folder of any name without saying which, so `Calculatro.mod_folder` looks in `Mods` for the folder
  whose `lovely.toml` registers `calculatro.core`, skipping any with a `.lovelyignore`. With several
  copies it prefers `Calculatro` and logs a warning. A custom `--mod-dir` isn't supported, since
  `love.filesystem` can't reach it.
- **Restarting.** It starts it again after 5, 15 and 45 seconds with no answer to the latest snapshot,
  counted afresh once it answers (`Calculatro.check_app`). A start Windows refuses isn't retried.
- **Stopping.** With `--game-pid` the app exits when the game does (`process.exited`).
- **One instance per state file.** With `--game-pid` the app exits at once if another instance is
  watching the same state file: a `Local\Calculatro-<hash of the path>` mutex
  (`process.instance_name`, `process.claim_instance`). Every `watch` claims its own, so a `--file`
  elsewhere doesn't get in the game's way. One started by hand beside another on the same file only
  prints, leaving the odds file to the first, since two writers would clash over it.
- **App log.** With `--game-pid` errors go to `%AppData%\Balatro\calculatro.log` (`log.open_log`). It's
  kept across restarts, each session starting with a `log.header` line, and over 256 KB at startup
  it moves to `calculatro.log.1`. The home folder is written as `~`, since players post the log
  publicly. A log that can't be opened doesn't stop the app, which runs without it.
- **Mod errors.** Mod errors (`guard`, once each) also go to `calculatro.log`, with the home folder as
  `~` (`Calculatro.log_error`).
- **The pool check.** For each new snapshot while selecting, `Calculatro.check_unseen` compares `unseen`
  with View Deck's rule (of `G.playing_cards`, those in the deck or marked `wheel_flipped`). A mismatch is
  logged once a session, as counts only, since players post the log. It's there because the tests only know
  the game through its mock. Look for `the unseen pool doesn't match` after trying a change in game.

By hand, `.venv/Scripts/python -m calculatro watch` shows the console output (`--file` overrides the
state file path). What to calculate is set in game, with the panel's target editor.

## Releases

`powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1` (5.1 or 7) builds
`dist\Calculatro-<version>-windows.zip` for players (not `Calculatro-<version>.zip`, the name of the
"Source code" zip GitHub adds to every release): the mod plus a pinned, hash-checked embeddable
Python (`$PythonVersion`/`$PythonSha256`) with the `calculatro` package in the mod's `python` folder,
which the mod runs when there's no `launch.txt` (`Calculatro.launch_command`). The zip's `lovely.toml`
has `dump_lua = false`; the repository's keeps `true`, for development.

The versions in `pyproject.toml`, `lovely.toml`, `Calculatro.json` and `calculatro/__init__.py` must
match (and `-Tag`, if given). Pushing a `v*` tag runs `.github/workflows/release.yml`: tests, build, and a draft GitHub
release with the zip. CI doesn't run the build script otherwise, so run it by hand before pushing the tag.

## The contract between the halves

### Schemas

`SCHEMA` in `state.py` must match `schema` in `core.lua`, and `overlay.SCHEMA` must match
`odds_schema`; bump both sides when changing either JSON shape.

A snapshot of another schema (the previous version's file at startup, or a mod not reinstalled after
an update) raises `state.SchemaMismatch`, which `FileStateSource.poll` skips with one message on
stderr until the file changes, rather than stopping the app. So does one of the right schema that
can't be decoded (invalid UTF-8, not JSON) or whose fields have the wrong shapes
(`state.MalformedState`, from `FileStateSource.read` or `GameState.from_json`). Only shapes are
checked, not ranges, and of the scalars only those that must be text, since the encoder can write the
game's numbers as floats or `null`. The message isn't repeated while the same error recurs.

The mod ignores an odds file of another schema. If it answers one of this session's snapshots (an app of
another version), the mod reports it once to the Lovely console and `calculatro.log`. One that doesn't
may be the last session's file, which the app replaces once it answers, so it's ignored silently.

### Shapes

- The odds file never contains `null` (the Lua decoder doesn't support it).
- The odds file (`odds_schema` 9) answers a snapshot with `{id, pending: true}`, then with its odds as
  they're found: `partial: true` until the last (an approximation still being refined counts as to
  come). Each row is `{key, keep, discard, trend}`, card rows without `keep`. A value that isn't exact
  odds has a mark beside it, `keep_mark` or `discard_mark` (`overlay.mark`): `{state: "pending"}` with
  no value for one still to come; for an approximation, `{state: "refining", how, low, high}` (the
  range, as percents), or `state: "final"` with its `limit` once the exact odds are given up on, with
  the value in whole percents (`overlay.approximate`). From `how: "bounds"` they're certain ("80%+", or
  "<6%" below 1%); from `how: "sample"` (deals of the face-down cards) or `"draws"` (final hands), an
  estimate whose range is only likely, they're rounded ("84%",
  never "0%" or "100%": "<1%", ">99%"). Or `{state: "final", limit: "time"}` (or `"memory"`) with the value `"?"` for one given
  up on with no approximation. A row has no such value (nothing selected) when it has neither.
  `trend` is there only when the comparison is certain: both values exact, or their ranges certain
  and not overlapping (never from an estimate). A search given up on that answered from estimates has
  its options and a `mark` like an approximation's, `{state: "final", how, low, high, limit}`, the
  range being the best option's likely odds (`overlay.search`); one that couldn't is answered with
  `options: []` and a `mark` without a `how`.
- The snapshot also sends `select_limit` (`G.hand.config.highlighted_limit`) and the latest `search`
  request; the odds file's `search` answers it ([panel.md](panel.md#best-discard-button)).
- Every snapshot, in a run or not, sends `memory_gb`, the "Memory limit" setting as the panel shows it
  (`Calculatro.memory_gb`: already no more than half the computer's memory). Anything but a whole
  number above 0 parses as `None`, the default.
- Empty Lua tables can arrive as `{}` rather than `[]`; the encoder tags arrays with an `ARRAY`
  metatable, and `state._cards` also handles it.
- Card fields mirror Balatro internals: `rank_id` 2..14 (Ace=14), `enhancement` is the center key
  (`m_stone`, `m_wild`, `c_base`, ...).
- `observations` (whatever the "Face-down hints" setting, which is only the hint's; `[]` under The
  Mark with the `flipped_cards` modifier) is every sort of the hand
  seen this round, in the order recorded, where a mode may repeat (a draw re-sorts the hand, but an
  earlier sort is still true of the cards in it), less those `observe.lua`'s `prune` drops as saying
  nothing more, from `Calculatro.export_observations`: each card an `id` and one of
  a face-up card's printed `rank_id` and `suit`, `"stone": true`, `"hand": i` for a card face down
  at that hand index, or `"discarded": true` for one since discarded face down (still in the discard
  pile with `wheel_flipped`), with `"marked": true` if The Mark drew it as a face card (as on the
  pile's `{hidden: true}` cards). A card in several sorts has the same id. `GameState.from_json` checks them
  more than other fields (`state._observations`: ids, kinds, ranks, suits and hand indices must agree,
  and there can't be more discarded ones, marked or not, than the discard pile's `{hidden: true}` cards
  marked alike) and
  renumbers the ids by first appearance into `GameState.observations` and `observed`, and gives each
  face-down hand card in them its id (`Card.latent`). The odds use them
  ([probability-model.md](probability-model.md#face-down-cards-in-the-hands-sorts)), so they're part of
  `search_basis`; `odds_basis` takes them by id, so a drag keeps the finished rows. None are sent
  under The Mark (not disabled) with the challenges' `flipped_cards` modifier, which the odds' Mark
  model doesn't allow for.

### Information hiding

Information hiding is deliberate. Don't add anything to the export that reveals information a player
couldn't see.

- The undrawn deck and the face-down cards, in hand or discarded (the game doesn't reveal a discarded
  face-down card up; View Deck still counts it as unplayed), go out together as one **sorted
  multiset**, `unseen` (`unseen_pool`). So neither draw order nor which cards are face down or were
  discarded face down leaks, not even by comparing snapshots as a card is drawn face down.
- The jokers are sorted too (Amber Acorn shuffles them face down).
- The face-down hint ([panel.md](panel.md#face-down-hint)) isn't exported, but keeps to the same rule. It
  works from `unseen_pool` and the observations (`observe.lua`), never a face-down card's own values, and
  trusts the hand's order because of public events (draws, Sort presses, drags, consumables), never by
  checking it against the true order.
- The observations are exported as the player saw them: face-up cards' printed values, Stones, and where
  the face-down cards were, which are referred to by hand index only, or once discarded face down as
  `discarded`, without saying which of the discard pile's face-down cards it is. Their ids are numbered by first
  appearance, so they say only which card in one sort is which in another, as the player saw them
  move, and each sort is one the player saw, at a time that's public. `test_which_cards_are_face_down_changes_nothing_sent` (`tests/test_observations.py`) swaps which
  unseen cards are face down and compares every snapshot, frame by frame.
- Face-down cards in hand and in the discard pile are exported as `{hidden: true}`, with `marked: true`
  if The Mark drew it face down as a face card (Pareidolia not held then; [blinds.md](blinds.md#face-down-cards-blindstay_flipped)):
  that goes by when it was drawn, which the player saw, never by what it is. The game turns every
  card in the discard pile over, so `facing` says nothing there: a card discarded face down is one with
  `ability.wheel_flipped`, as in View Deck, and the rest have been seen and are exported as they are.
  The deck's size is
  `len(unseen)` less their number (`GameState.deck_size`, `hidden`, `discarded_hidden`), and the marked
  ones, discarded or not, count as face cards (`GameState.marked`).
- The engine draws kept face-down cards from `unseen` like the new cards, except that the marked ones are
  known to be face cards, so they're drawn from the unseen face cards (`engine._mark_split`).

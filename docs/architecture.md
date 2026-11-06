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
| `files` | the state and odds files, the Windows FFI |
| `log` | `guard`, `report` |
| `app` | starting the app |
| `widgets` | the definition nodes and sizes the views share |
| `uibox` | what's done to a built box, `remove_box` |
| `anim` | the panel's animation |
| `odds`, `editor` | the panel's two views: model, definition |
| `panel` | placing and refreshing it |
| `calculatro.lua` | only the hooks |

### Snapshots

`Calculatro.update` throttles itself to ~5 snapshots/sec, serializes Balatro's global `G` with its
own JSON encoder, and writes `%AppData%\Balatro\calculatro_state.json` only when the output changes:
it writes to `.tmp`, then does `MoveFileExW` over it (`Calculatro.replace_file`). A write that
fails, as when the app has the file open, is tried again with the next snapshot, and reported after
10 in a row. Each written snapshot gets an `id` (`<session>:<seq>`).

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
- **Odds already worked out.** `build_report` stores each row's odds in `known` as soon as they're
  done, by the row and the cards selected, so going back to an earlier selection calculates nothing,
  and rows finished before a job was cancelled aren't lost. `run_watch` starts a new `known` whenever
  `odds_basis` changes (`search_basis` without the hand's order, since sorting the hand doesn't change
  the odds, `test_odds_do_not_depend_on_the_hands_order`). A row's key uses the cards, not their
  positions, and `row_key`: a query's text, not the query, since two parses of one may not be equal. There's no size limit:
  one hand has only so many selections, and everything goes at the next discard.
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

`pwsh scripts/install_mod.ps1` copies `mod/Calculatro` to `%AppData%\Balatro\Mods` (it requires the
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

By hand, `.venv/Scripts/python -m calculatro watch` shows the console output (`--file` overrides the
state file path). What to calculate is set in game, with the panel's target editor.

## Releases

`powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1` (5.1 or 7) builds
`dist\Calculatro-<version>-windows.zip` for players (not `Calculatro-<version>.zip`, the name of the
"Source code" zip GitHub adds to every release): the mod plus a pinned, hash-checked embeddable
Python (`$PythonVersion`/`$PythonSha256`) with the `calculatro` package in the mod's `python` folder,
which the mod runs when there's no `launch.txt` (`Calculatro.launch_command`).

The versions in `pyproject.toml`, `lovely.toml` and `calculatro/__init__.py` must match (and `-Tag`,
if given). Pushing a `v*` tag runs `.github/workflows/release.yml`: tests, build, and a draft GitHub
release with the zip.

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

### Shapes

- The odds file never contains `null` (the Lua decoder doesn't support it).
- The snapshot also sends `select_limit` (`G.hand.config.highlighted_limit`) and the latest `search`
  request; the odds file's `search` answers it ([panel.md](panel.md#best-discard-button)).
- Empty Lua tables can arrive as `{}` rather than `[]`; the encoder tags arrays with an `ARRAY`
  metatable, and `state._cards` also handles it.
- Card fields mirror Balatro internals: `rank_id` 2..14 (Ace=14), `enhancement` is the center key
  (`m_stone`, `m_wild`, `c_base`, ...).

### Information hiding

Information hiding is deliberate. Don't add anything to the export that reveals information a player
couldn't see.

- The undrawn deck and the face-down cards, in hand or discarded (the game doesn't turn a discarded
  face-down card up; View Deck still counts it as unplayed), go out together as one **sorted
  multiset**, `unseen` (`unseen_pool`). So neither draw order nor which cards are face down or were
  discarded face down leaks, not even by comparing snapshots as a card is drawn face down.
- The jokers are sorted too (Amber Acorn shuffles them face down).
- Face-down cards in hand and in the discard pile are exported as `{hidden: true}`. The deck's size is
  `len(unseen)` less their number (`GameState.deck_size`, `hidden`, `discarded_hidden`), and under The
  Mark the discarded ones count as face cards too.
- The engine draws kept face-down cards from `unseen` like the new cards, except that under The Mark
  (`GameState.mark`) they're known to be face cards, so they're drawn from the unseen face cards
  (`engine._mark_split`).

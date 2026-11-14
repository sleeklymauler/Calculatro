# AGENTS.md

Guidance for coding agents working on Calculatro, a live odds calculator for Balatro. This is the
file to edit: `CLAUDE.md` and `GEMINI.md` only import it. It's loaded into every session, so keep it
short, and put what only matters to part of the code in `docs/`.

## Commands

Windows-only project (paths rely on `%APPDATA%`). Use the venv interpreter. These paths work in both
PowerShell and Bash.

- Setup: `python -m venv .venv; .venv/Scripts/pip install -e ".[dev]"`
- All tests: `.venv/Scripts/python -m pytest` (in parallel with `pytest-xdist`, one worker per core: about 30 s
  on 32 cores, 5 minutes on one)
- Single test: `.venv/Scripts/python -m pytest -n0 tests/test_engine.py::test_name` (`-n0` skips starting the
  workers, a few seconds)
- Fuzz: `.venv/Scripts/python -m pytest -m fuzz` (1000 random states against brute force, about 15 s;
  `--fuzz-seeds N --fuzz-base B` for others). Run it after changing the engine or the search.
- Lint: `.venv/Scripts/ruff check .` (`--fix` applies the safe fixes, including import sorting). Configured in
  `pyproject.toml`; there's no formatter.
- Performance: `.venv/Scripts/python tests/perf/benchmark.py` (timings; `-k` filters, `--compare tests/perf/baseline.json`)
  and `.venv/Scripts/python -m pytest -m perf` (exact results and timings; needs the git-ignored
  `tests/perf/baseline.json`, from `benchmark.py --save`). Run them before and after changing the
  engine or the search. A plain `pytest` checks the exact results of all but the slowest scenarios,
  without timings. See `docs/performance/README.md`.
- Install the mod into the game: `powershell -ExecutionPolicy Bypass -File scripts\install_mod.ps1`. Re-run after any change to the Lua.
- Run by hand: `.venv/Scripts/python -m calculatro watch` (normally the mod starts the app with the game).
- Debug a state: `.venv/Scripts/python -m calculatro odds [FILE] [--search ROW]` prints a state file's odds as
  text (the game's by default, or a bug report's `state.json`).
- Build a release: `powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1`.

No runtime dependencies; `pytest`, `lupa` and `ruff` are dev-only.

**On Linux** (as in many cloud agents' sandboxes) the venv's tools are in `.venv/bin/`, and the app
can't run. Every test file collects and `ruff` works, but about 110 tests of Windows behaviour fail:
all of `test_process` (it calls `kernel32`), the LuaJIT cases of the mod's file access in
`test_state` and `test_overlay` (`[luajit]`, and those using the `luajit` or `threaded` fixture) (Windows' wide functions through the FFI), and `test_log`'s Windows
paths and game start. Everything else passes, including the app's main loop (`test_watch`) and the
mod's `[lua]` cases. Only a failure outside those is yours. Changes to the mod's file access, starting
the app or the process helpers can't be tested there, so say so when reporting back.

## Architecture

Two halves connected by two JSON files in `%AppData%\Balatro\calculatro_data` (with the settings and the log):

1. **Lovely mod** (`mod/Calculatro/`, Lua): writes the game state to `state.json`
   (~5×/sec, only when it changes), reads the odds back from `odds.json` and draws the
   panel. It owns what's tracked, set in game with the panel's target editor.
2. **Python app** (`calculatro/`): `source.py` polls the state file → `state.py` parses it →
   `engine.py` computes exact odds → `report.py` collects them → `overlay.py` turns them into the
   odds file's payload (`text.py` into text, for `calculatro odds`). Odds and best-discard searches run in cancellable background
   jobs (`worker.Job`), whose parts stop at time and memory limits (`worker.Watchdog`).

## Read before changing

| When working on | Read |
|---|---|
| The mod's modules and snapshots, the app's loop, starting the app, logging, releases, the JSON contract | [docs/architecture.md](docs/architecture.md) |
| The panel, the target editor, the best-discard button | [docs/panel.md](docs/panel.md) |
| Odds, hand patterns, card queries, joker and boss rules, what best discard compares | [docs/probability-model.md](docs/probability-model.md) |
| The speed of the engine or the search | [docs/performance/](docs/performance/README.md) |
| How the game behaves: jokers, blinds | [docs/jokers.md](docs/jokers.md), [docs/blinds.md](docs/blinds.md) (use these instead of the wiki, which they correct from the game's code) |

## Rules that must not break

- **Information hiding.** Never export anything a player couldn't see. The undrawn deck and all
  face-down cards go out merged as one sorted multiset (`unseen`), so neither draw order nor which
  cards are face down leaks; jokers are sorted too. The hand's sorts (`observations`) refer to face-down
  cards by hand index only, or as discarded face down without saying which. See
  [the contract](docs/architecture.md#information-hiding).
- **Schemas.** `SCHEMA` in `state.py` must match `schema` in `core.lua`, and `overlay.SCHEMA` must match
  `odds_schema`. Bump both sides when changing either JSON shape. The files' folder and names are in
  `calculatro/paths.py` and `core.lua` (`data_dir`, `data_files`), which must match too. The odds file never contains `null`
  (the Lua decoder doesn't support it).
- **Cancellable.** Slow code paths must check the `cancel` Event and raise `engine.Cancelled`, at most 0.1 s
  of CPU apart (`tests/test_cancel.py`; add a case for a new slow path).
- **Exact odds.** The engine counts exactly (`Fraction`); it isn't Monte Carlo. The panel may show an
  approximation while the exact odds are worked out, or once they're given up on, but a value that isn't
  exact is always marked as such (`report.Approx`: an asterisk, on the panel and in `calculatro odds`).
- **Lua 5.1.** The game runs LuaJIT, so no Lua 5.2+ features. The mod may not assign globals, and may
  read only LuaJIT's and the game's. Register every new Lua file in `lovely.toml`.
- **File access in the mod.** Use `love.filesystem` or Windows' wide functions through the FFI, never
  `io.open`/`os.rename`/`os.remove`, which fail on a non-ASCII Windows username.
- **Don't change Balatro's code.** Work around game bugs from the mod's side. Ask before adding a Lovely
  patch into a game file or overriding a game function globally.
- **Any screen.** Place UI relative to game objects in the game's units (a 20×11.5-unit room), never in
  pixels or relative to the window edges, so it works on 16:9 as well as ultrawide.
- **Panel text is ASCII.** The game's font has no arrows, suit symbols or ≤/≥, so those are drawn.
- **New rules.** A new boss rule must check `blind_disabled`. A new joker rule needs a benchmark scenario
  in `tests/perf/scenarios.py`. Both belong in `docs/jokers.md` or `docs/blinds.md`, with the game's
  exact behaviour.

## Tests

- Fixtures shared between test modules are in `tests/conftest.py`; shared helpers are plain modules
  beside it (`mocked_game`, `panel_driver`, `hand_driver`, `poker`, `search_oracle`, `state_oracle`,
  `rounds`, `fakes`). Never import from a `test_*` module.
- A test that checks an estimate's values takes the `fake_clock` fixture: estimates stop at a deadline, so by
  the real clock a busy machine gets other (seeded) samples.
- `tests/mocked_game.py` loads the real mod into `lupa` from the modules `lovely.toml` lists, as Lovely
  loads them (`load_mod`), against a mocked `G` and `love.filesystem`; `tests/test_state.py` parses
  what it writes, so mod changes are testable without the game. Extend `MOCK_G` there when exporting
  new game fields.
- `MOCK_G` also mocks `UIBox` (recording every box in `BOXES`). `tests/test_overlay.py` checks the
  panel's text and colours end to end.
- The `lua` fixture runs every Lua test in both LuaJIT, as in the game, and `lupa`'s newer Lua (`[lua]`
  and `[luajit]`; each runtime has its own `LuaError`), and skips it without `lupa`, so modules mixing
  Lua and pure-Python tests don't skip as a whole. A test of what the mod does through LuaJIT's FFI takes the
  `luajit` fixture instead (LuaJIT only), so the newer Lua has no copy of it to skip. pytest's faulthandler is off (`addopts`), since
  LuaJIT's errors are Windows exceptions it would report by the thousand.
- `tests/test_lua_globals.py` lints each module's globals from its LuaJIT bytecode: a local another
  module defines, not imported, fails here. Add to `GAME_GLOBALS` when the mod uses another game global.
- `tests/test_mod_modules.py` checks the split: every Lua file registered, each module loading on its
  own with no game around, and requiring the modules whose functions it calls.

The panel's real layout and shadows aren't covered by tests, so mod changes need trying in game too.
Only someone with the game can do that, so say what still needs checking.

## Keeping docs current

When a change makes a doc wrong, update that doc in the same change. New details go in the page from
the table above, not here.

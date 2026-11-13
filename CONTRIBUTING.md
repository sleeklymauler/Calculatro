# Contributing to Calculatro

Thanks for your interest! Bug reports, ideas and pull requests are all welcome. For anything bigger than a small
fix, consider opening an issue first so we can talk about the approach.

## How it fits together

Calculatro has two halves, connected by two JSON files in `%AppData%\Balatro`:

- **The mod** (`mod/Calculatro/`, Lua, loaded by [Lovely](https://github.com/ethangreen-dev/lovely-injector)):
  writes the game state to `calculatro_state.json` (at most ~5×/sec, only when something changes), reads the odds
  back from `calculatro_odds.json` and draws the panel. It also owns what's tracked, through the panel's target
  editor.
- **The app** (`calculatro/`, Python, no runtime dependencies): reads the state, computes exact odds and the best
  discards, and writes the odds file.

The detailed developer reference is in `docs/`: [architecture.md](docs/architecture.md) (both halves and the contract
between them), [panel.md](docs/panel.md), [probability-model.md](docs/probability-model.md),
[jokers.md](docs/jokers.md) and [blinds.md](docs/blinds.md) (the game's rules, from its code) and
[performance](docs/performance/README.md).

If you use a coding agent, [AGENTS.md](AGENTS.md) is its short guide: the commands, the rules that must not break and
which page to read for what. Most agents read it on their own. `CLAUDE.md` and `GEMINI.md` only import it, for the
tools that look for those names, so edit `AGENTS.md` rather than them.

## Setup

You need Windows, Balatro with the [Lovely injector](https://github.com/ethangreen-dev/lovely-injector/releases)
(see the [README](README.md#installation-users)), [Python 3.11+](https://www.python.org/downloads/) and
[PowerShell 7](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows).

1. Clone the repository and install the app with its dev tools (`pytest`, `lupa`, `ruff`):
   ```pwsh
   git clone https://github.com/sleeklymauler/Calculatro.git
   cd Calculatro
   python -m venv .venv
   .venv\Scripts\pip install -e ".[dev]"
   ```
2. Install the mod into the game:
   ```pwsh
   pwsh scripts/install_mod.ps1
   ```
   This replaces `%AppData%\Balatro\Mods\Calculatro` (a release install included) with `mod/Calculatro`, and
   writes the venv's `pythonw.exe` to the mod's `launch.txt`, so the game starts the app from your checkout. Pass
   `-NoAutoStart` to start the app yourself instead (until you do, the panel says "App didn't start"), and `-GameDir <folder>` if Balatro isn't in Steam's default
   folder (it's only used to check that Lovely is installed).
3. Launch Balatro. The app runs in the background and exits with the game; errors go to
   `%AppData%\Balatro\calculatro.log`.

Python changes take effect the next time the game starts the app (the install is editable). **Lua changes need
`install_mod.ps1` again**, then a restart of the game.

### Running the app by hand

```pwsh
.venv\Scripts\python -m calculatro watch
```

This prints the game state and the odds to the console, which is handy for debugging. Only one app writes the odds
file: if the game already started one, this one only prints. To see your changes on the panel, install with
`-NoAutoStart` and start the app by hand. `--file` reads the state from another path.

## Tests and lint

```pwsh
.venv\Scripts\python -m pytest       # everything but the performance timings and slowest scenarios
.venv\Scripts\ruff check .           # lint (--fix applies the safe fixes)
```

The tests cover the app, and run the mod's real Lua (via `lupa`, under both LuaJIT and a newer Lua) against a mocked
game: the exported snapshot, the panel and its animation, and a lint of the mod's globals. So most mod changes can be tested without the game, but
please also try them in game, and on a 16:9 screen if you can, since the panel's layout and shadows aren't covered.

A plain `pytest` also checks the exact results of the performance scenarios, bar the slowest, without timing them.
The full performance tests are separate:

```pwsh
.venv\Scripts\python tests\perf\benchmark.py                                # time the engine on a set of game situations
.venv\Scripts\python tests\perf\benchmark.py --save tests\perf\baseline.json  # record your machine's baseline
.venv\Scripts\python -m pytest -m perf                                      # check exact results, and timings against it
```

Run them before and after changing the engine or the search. See [docs/performance](docs/performance/README.md).

## Things to keep in mind

- **No cheating.** The mod must never export anything the player can't see: the deck goes out as a sorted multiset
  (no draw order), merged with the face-down cards so which cards are face down doesn't show. Odds have to come from what's on screen.
- **The mod runs on LuaJIT (Lua 5.1)**, so avoid Lua 5.2+ features: the Lua tests run under LuaJIT too. The mod may
  not assign globals, and reads only the game's and LuaJIT's (`tests/test_lua_globals.py`).
- **JSON shapes are versioned.** If you change what the state or odds file contains, bump the schema on both
  sides (`SCHEMA` in `state.py` with `schema` in `core.lua`, `overlay.SCHEMA` with `odds_schema`).
- **Panel text must be ASCII:** the game's font has no arrows or suit symbols, so those are drawn instead.
  Place UI relative to game objects in the game's own units, never in pixels, so it works on any screen.
- **Keep slow work cancellable:** a new selection cancels the odds being calculated (`engine.Cancelled`).
- **New joker or boss rules** belong in `docs/jokers.md` or `docs/blinds.md` with the game's exact behaviour, and
  joker rules need a benchmark scenario in `tests/perf/scenarios.py`.

## Pull requests

- Keep each one to one change, with tests for it, and `pytest` and `ruff check .` passing. [CI](.github/workflows/ci.yml)
  runs both on every pull request, the tests on Python 3.11 and 3.14.
- Update AGENTS.md, the README or `docs/` when you change what they describe.

## Releases

For maintainers: set the same version in `pyproject.toml` and `mod/Calculatro/lovely.toml`, commit and push, then
tag it:

```pwsh
git tag -a v0.2.0 -m "Calculatro 0.2.0"
git push origin v0.2.0
```

The [Release workflow](.github/workflows/release.yml) runs the tests, builds `Calculatro-0.2.0-windows.zip` with
`scripts/build_release.ps1` (the mod with an embeddable Python and the app inside, so players don't need Python)
and attaches it to a draft release, to check and publish on GitHub. To build the zip locally:
`powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1`, which writes it to `dist\`.

By contributing, you agree that your contributions are licensed under the [GPL-3.0](LICENSE), like the rest of
Calculatro.

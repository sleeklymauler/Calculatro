# Calculatro
Interactive probability calculator for Balatro

## Setup

1. Install the [Lovely injector](https://github.com/ethangreen-dev/lovely-injector/releases): put the DLL from the release zip (`winmm.dll` in current releases, `version.dll` in older ones) next to `Balatro.exe`.
2. Install the mod: `pwsh scripts/install_mod.ps1` (copies `mod/Calculatro` to `%AppData%\Balatro\Mods`).
3. Install the Python app: `python -m venv .venv; .venv\Scripts\pip install -e ".[dev]"`.
4. Launch Balatro, start a run, and run `.venv\Scripts\python -m calculatro watch`.

The mod writes the live game state to `%AppData%\Balatro\calculatro_state.json` (at most ~5×/sec, only when something changes).
The deck is exported as a sorted multiset, so draw order is never leaked, and face-down cards in hand are hidden.

## Tests

`.venv\Scripts\python -m pytest` runs the mod's Lua (via `lupa`) against a mocked game state and parses the result.

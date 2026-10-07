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

Performance tests are separate: `.venv\Scripts\python tests\perf\benchmark.py` times the probability engine on a set of
game situations, and `.venv\Scripts\python -m pytest -m perf` checks them against a saved baseline.
See [docs/performance](docs/performance/README.md).

## Usage

```
.venv\Scripts\python -m calculatro watch -t trips -t "full house" -c "queen" -c "no face cards"
```

Select the cards you're considering discarding in game; Calculatro shows the odds after discarding them.
Target odds are shown for keeping your hand vs. discarding the selection; card odds cover only the drawn cards.

- `-t/--target`: hand types (`pair`, `two pair`, `trips`, `straight`, `flush`, `full house`, `quads`,
  `straight flush`, `royal`, `five of a kind`, `flush house`, `flush five`). A target counts if you could
  play a hand Balatro scores as containing it (a Straight Flush counts as a Straight). Several targets also
  show the odds of hitting any of them.
- `-c/--cards`: counts over the newly drawn cards only — `queen`, `2+ queens`, `at most 1 spade`,
  `exactly 2 hearts`, `no face cards`, `2 of clubs` / `2c`. A bare number means "at least";
  `QS`/`10s` are poker notation for a specific card (use `tens` for the rank).
  Special cards: enhancements (`bonus`, `mult`, `wild`, `glass`, `steel`, `stone`, `gold`, `lucky`, or any
  `enhanced`), editions (`foil`, `holo`, `polychrome`, `negative`, or any `edition`) and seals (`red seal`,
  `blue seal`, `gold seal`, `purple seal`, or `any seal`). These can prefix a rank, suit or card to narrow it:
  `2+ lucky`, `no glass cards`, `foil hearts`, `lucky red seal queen`. `gold` is the Gold Card and `gold seal` the seal.

# Calculatro
Interactive probability calculator for Balatro on Windows.

<img width="2560" height="1440" alt="The Calculatro odds panel above the deck during a blind" src="https://github.com/user-attachments/assets/2e2a8866-32a2-41dd-955b-d6395e722c93" />

## Features
- Calculate the probability of getting a target hand (e.g. two pair, flush, straight, etc.) for a given discard selection.
  - Also supports the secret hands: five of a kind, flush house, and flush five.
- Calculate the probability of drawing specific card types (e.g. 2+ red seals, exactly 1 Lucky card, at most 3 face cards, etc.) for a given discard selection.
- Find the optimal discard selection for a target hand.
- No cheating. The deck is exported by the mod to the app as a multiset: draw order is never leaked, and face-down cards in hand are hidden. All probabilities could be calculated by hand by the player, as all information needed to do so is already available in-game.
- Joker aware: probabilities are accurate when using Jokers like **Four Fingers**, **Shortcut**, **Smeared Joker**, and **Pareidolia**. See [docs/jokers.md](docs/jokers.md#rules-that-matter-to-calculatro).
- Boss blind aware:
  - Strikes out hands that can't score when facing **The Eye** or **The Mouth**.
  - Handles always having a card selected when facing **The Cerulean Bell**.
  - Averages probabilities over all possible 2-card discards when playing cards against **The Hook**.
  - Handles unknown face-down cards when facing **The House**, **The Wheel**, **The Fish**, or **The Mark**, *without* cheating by looking at face-down cards.
  - Handles always drawing 3 cards after play or discard when facing **The Serpent**.
  - Handles all boss blinds that debuff cards, like **The Pillar** and **The Plant**.
  - See [docs/blinds.md](docs/blinds.md) for more.

## Setup

### Prerequisites
- [Balatro 1.0.1o-FULL](https://www.playbalatro.com/)
- [Python 3.11+](https://www.python.org/downloads/)
- [Lovely injector](https://github.com/ethangreen-dev/lovely-injector/releases)
- [PowerShell 7](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows?view=powershell-7.6)
- Windows only (for now)

### Installation

1. Install the [Lovely injector](https://github.com/ethangreen-dev/lovely-injector/releases): put the DLL from the release zip (`winmm.dll` in current releases, `version.dll` in older ones) next to `Balatro.exe`.
2. Clone the repository:
   ```pwsh
   git clone https://github.com/sleeklymauler/Calculatro.git
   cd Calculatro
   ```
3. Install the Python app:
   ```pwsh
   python -m venv .venv
   .venv\Scripts\pip install -e .
   ```
4. Install the mod:
   ```pwsh
   pwsh scripts/install_mod.ps1
   ```
   This copies `mod/Calculatro` to `%AppData%\Balatro\Mods` and tells the mod where the venv is, so do this after
   step 3 (or re-run it). Pass `-NoAutoStart` if you'd rather start the app yourself, and `-GameDir` if Balatro isn't
   in Steam's default folder. Re-run it after any change to the mod.
5. Launch Balatro. The mod starts the app in the background (no console window), and it exits with the game. Errors go to `%AppData%\Balatro\calculatro.log`.

The mod writes the live game state to `%AppData%\Balatro\calculatro_state.json` (at most ~5×/sec, only when something changes). The app writes the odds back to `calculatro_odds.json` next to it, and the mod shows them in a panel above the deck.

## Usage

Choose what to track in game: press the `+` beside "CALCULATRO" on the panel above the deck. Its Hands tab
toggles target hands; its Cards tab builds card counts (a count, rank, suit and special) and lists the tracked
ones. Done goes back to the odds, and the `x` on a row stops tracking it. Targets last for the run (they're saved
with it) and a new run starts with none. The `+` is greyed out once the panel is full (3 columns of 6 rows).

Select the cards you're considering discarding in game; Calculatro shows the odds after discarding them.
Target odds are shown for keeping your hand vs. discarding the selection; card odds cover only the drawn cards.

The panel is only shown while playing a blind (not in the shop, blind select, booster packs or menus, nor while
a played hand is scored); press `C` to hide or show it. A discard's odds are green when they beat keeping the
hand and red when they're worse. Odds still being calculated for your latest selection are greyed out, and the
panel says "Waiting for app" if the app isn't answering.

- Target hands: a target counts if you could play a hand Balatro scores as containing it (a Straight Flush
  counts as a Straight). Several targets also show the odds of hitting any of them.
- Card counts cover the newly drawn cards only: at least / exactly / at most a number of cards of a rank
  (or any face card), suit, specific card, and/or special: an enhancement (or any `Enhanced`), edition
  (or any edition) or seal (or `Sealed`).

### Running the app by hand

```pwsh
.venv\Scripts\python -m calculatro watch
```

This prints the game state and the odds to the console. If the game already started the app, this one only
prints: the panel keeps showing the odds from the first. `--file` reads the state from another path instead of
`%AppData%\Balatro\calculatro_state.json`.

## Tests

`.venv\Scripts\python -m pytest` runs the app's tests, and the mod's Lua (via `lupa`) against a mocked game state:
the exported snapshot, the panel and its animation.

Performance tests are separate: `.venv\Scripts\python tests\perf\benchmark.py` times the probability engine on a set of
game situations, and `.venv\Scripts\python -m pytest -m perf` checks their exact results, and their timings against
your machine's own baseline, which the first run records.
See [docs/performance](docs/performance/README.md).

## License

Calculatro is licensed under the [GNU General Public License v3.0](LICENSE).

It's a fan-made mod, not affiliated with or endorsed by LocalThunk or Playstack.

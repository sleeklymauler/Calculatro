
# Calculatro

<img width="1280" height="640" alt="B · Pocket Calculator@1x" src="https://github.com/user-attachments/assets/f38edcbf-dfbd-4ccd-a189-71abfaaf7476" />

## Features
- Calculate the probability of getting a target hand (e.g. two pair, flush, straight, etc.) for a given discard selection.
  - Also supports the secret hands: five of a kind, flush house, and flush five.
- Calculate the probability of drawing specific card types (e.g. 2+ red seals, exactly 1 Lucky card, at most 3 face cards, etc.) for a given discard selection.
- Find the optimal discard selection for a target hand.
- No cheating. The deck is exported by the mod to the app as a multiset: draw order is never leaked, and face-down cards, in hand or discarded, are hidden. All probabilities could be calculated by hand by the player, as all information needed to do so is already available in-game.
- Joker aware: probabilities are accurate when using Jokers like **Four Fingers**, **Shortcut**, **Smeared Joker**, and **Pareidolia**. See [docs/jokers.md](docs/jokers.md#rules-that-matter-to-calculatro).
- Boss blind aware:
  - Strikes out hands that can't score when facing **The Eye** or **The Mouth**.
  - Handles always having a card selected when facing **The Cerulean Bell**.
  - Averages probabilities over all possible 2-card discards when playing cards against **The Hook**.
  - Handles unknown face-down cards when facing **The House**, **The Wheel**, **The Fish**, or **The Mark**, *without* cheating by looking at face-down cards.
  - Handles always drawing 3 cards after play or discard when facing **The Serpent**.
  - Handles all boss blinds that debuff cards, like **The Pillar** and **The Plant**.
  - See [docs/blinds.md](docs/blinds.md) for more.

## Installation (Users)

You need:
- Windows (other platforms aren't supported, for now).
- [Balatro](https://www.playbalatro.com/) 1.0.1o-FULL.
- The [Lovely injector](https://github.com/ethangreen-dev/lovely-injector/releases), which loads mods into Balatro.

You don't need to install Python: the release comes with its own.

1. **Install Lovely** if you haven't already. Download the Windows zip from its
   [releases](https://github.com/ethangreen-dev/lovely-injector/releases) and put the DLL inside (`winmm.dll`, or
   `version.dll` in older releases) next to `Balatro.exe`. To find that folder in Steam, right-click Balatro, then
   **Manage** → **Browse local files**.
2. **Download Calculatro.** From the [latest release](https://github.com/sleeklymauler/Calculatro/releases/latest),
   download `Calculatro-<version>-windows.zip` (not "Source code").
3. **Extract it into Balatro's Mods folder.** Right-click the zip, choose **Extract All...**, and enter
   `%AppData%\Balatro\Mods` as the destination (that's `C:\Users\<you>\AppData\Roaming\Balatro\Mods`; create the
   `Mods` folder if it isn't there). You should end up with `Mods\Calculatro\lovely.toml`. Explorer suggests a
   folder named after the zip by default, which puts the mod one folder too deep for Lovely to find it.
4. **Launch Balatro.** Calculatro starts in the background with the game and closes with it. Start a blind and
   its panel appears above the deck.

**Updating:** close Balatro, delete the `Mods\Calculatro` folder, and extract the new release as above.

Calculatro keeps its settings and log in `%AppData%\Balatro\calculatro_data`, so updating doesn't lose them.

**Uninstalling:** delete the `Mods\Calculatro` folder and the `calculatro_data` folder beside `Mods`. Leave the other
files in `%AppData%\Balatro` alone: they're Balatro's settings and saves.

### Troubleshooting

- **No panel in a blind:** check that Lovely is working (Balatro opens a console window next to the game when it
  is) and that the mod is at `Mods\Calculatro\lovely.toml`, not in a folder inside that.
- **The panel says "Waiting for app":** the app isn't answering. If it stopped, the mod starts it again within a
  few seconds.
- **The panel says "App didn't start":** the mod couldn't start the app, started it several times without an
  answer, or can't write its files to `%AppData%\Balatro\calculatro_data`. Errors go to `calculatro.log` in that
  folder; please include it if you [open an
  issue](https://github.com/sleeklymauler/Calculatro/issues), along with your Balatro and Lovely versions and any
  other mods you have installed.

## Usage
- Choose what to track in game by selecting the `+` button. Switch between the `Hands` and `Cards` tab to track specific hand or card types.
- Click on specific hands in the `Hands` tab to toggle tracking. Or, remove tracked hands by clicking the red `X`'s.
<img width="426" height="240" alt="demo_hands" src="https://github.com/user-attachments/assets/2dd53a4e-fde8-4b8d-845d-c91baa92eab3" />

- Build custom card types to track in the `Cards` tab. Pick any ranks (or a preset like Face, Even or Fibonacci), suits and a special (which `not` negates), and get probabilities for at least, exactly, at most, or between two counts of them. Hover over a tracked card type to see its full name and what it means.
<img width="426" height="240" alt="demo_cards" src="https://github.com/user-attachments/assets/98fa9f63-6a1c-4c38-9843-9a3c12490990" />



Choose what to track in game: press the `+` beside "CALCULATRO" on the panel above the deck. Its Hands tab
toggles target hands; its Cards tab builds card counts (a count, ranks, suits and a special) and lists the
tracked ones; hovering over one shows its full name and what it means, and its pencil loads it back into the
builder to edit (Add becomes Save). Done goes back to the odds, and the `x` on a row stops tracking it. Targets last for the run (they're saved
with it) and a new run starts with none. The `+` is greyed out once the panel is full (3 columns of 6 rows).

Select the cards you're considering discarding in game; Calculatro shows the odds after discarding them.
Target odds are shown for keeping your hand vs. discarding the selection; card counts show the odds after discarding.
With no discards left, the odds are for playing the selection instead, as you draw after that too; on your last
hand nothing more is drawn, so there are no discard odds ('-').

The green magnifier on a row selects the discard with the best odds for it. If several are equally good, the panel
shows "1/n" and clicking again cycles through them; "Keep hand" means keeping your hand is best.

The panel is only shown while playing a blind (not in the shop, blind select, booster packs or menus, nor while
a played hand is scored); press `C` to hide or show it. A discard's odds are green when they beat keeping the
hand and red when they're worse. Odds still being calculated for your latest selection are greyed out, and the
panel says "Waiting for app" if the app isn't answering, and "App didn't start" if starting it again hasn't helped.

- Target hands: a target counts if you could play a hand Balatro scores as containing it (a Straight Flush
  counts as a Straight). Several targets also show the odds of hitting any of them.
- Card counts cover your hand after the draw (the cards you keep and the ones you draw): at least / exactly /
  at most / between numbers of cards of
  any of a set of ranks (or face, non-face, even, odd or Fibonacci cards), any of a set of suits, and/or a
  special: an enhancement (or any `Enhanced`), edition (or any edition) or seal (or `Sealed`), or `not` one.

## Contributing

To run Calculatro from source, run the tests or change it, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Calculatro is licensed under the [GNU General Public License v3.0](LICENSE).

It's a fan-made mod, not affiliated with or endorsed by LocalThunk or Playstack.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Windows-only project (paths rely on `%APPDATA%`). Use the venv interpreter:

- Setup: `python -m venv .venv; .venv\Scripts\pip install -e ".[dev]"`
- All tests: `.venv\Scripts\python -m pytest`
- Single test: `.venv\Scripts\python -m pytest tests/test_engine.py::test_name`
- Performance: `.venv\Scripts\python tests\perf\benchmark.py` (timings; `-k` filters, `--compare tests\perf\baseline.json`), and `.venv\Scripts\python -m pytest -m perf` (exact results and timings against `tests/perf/baseline.json`; skipped by a plain `pytest`). See `docs/performance/README.md`.
- Run: `.venv\Scripts\python -m calculatro watch -t trips -t "full house" -c "2+ queens"` (`--file` overrides the state file path)
- Install the mod into the game: `pwsh scripts/install_mod.ps1` (copies `mod/Calculatro` to `%AppData%\Balatro\Mods`; requires the Lovely injector DLL next to `Balatro.exe`). Re-run after any change to the Lua.

No runtime dependencies; `pytest` and `lupa` are dev-only. There is no linter configured.

## Architecture

Two halves connected by two JSON files:

1. **Lovely mod** (`mod/Calculatro/`): `lovely.toml` registers `calculatro.lua` as a module, patches `Game:update` to call it every frame, and `Controller:key_press_update` for the panel's hotkey (`C`). The module throttles itself to ~5 snapshots/sec, serializes Balatro's global `G` with its own JSON encoder, and writes `%AppData%\Balatro\calculatro_state.json` only when the output changes (write to `.tmp`, remove, rename — the Python reader tolerates the file briefly missing). Each written snapshot gets an `id` (`<session>:<seq>`).
2. **Python app** (`calculatro/`): `source.py` polls the file's mtime/size → `state.py` parses it into frozen `GameState`/`Card` dataclasses → `engine.py` computes exact odds → `report.py` collects them into a `Report` → `__main__.py` formats it for the console and `overlay.py` for the mod. `__main__.run_watch` runs the odds in a `worker.Job` (daemon thread): each new state cancels the running job (the engine checks the `cancel` Event and raises `engine.Cancelled`), and odds slower than ~0.25 s print the summary first with "Calculating odds..." and follow when ready. Keep new slow code paths cancellable.
3. **Odds panel**: the app answers each snapshot in `calculatro_odds.json` (same directory, atomic `os.replace`) — first `{id, pending: true}`, then the odds, echoing the snapshot's `id`. The mod reads it ~20×/sec with a small JSON decoder and draws a Balatro `UIBox` right-aligned above `G.deck`, rebuilt only when its model changes. Odds whose `id` isn't the latest snapshot's are still shown, greyed with "Calculating..." after 0.3 s; with no reply at all for 2 s it says it's waiting for the app; odds from another game session are ignored. The panel only exists in a blind (`Calculatro.in_blind`: `SELECTING_HAND`/`HAND_PLAYED`/`DRAW_TO_HAND`, or `PLAY_TAROT` interrupting one, and no `G.OVERLAY_MENU`). Python sends display-ready strings (`overlay.percent`, `card_label`, messages) so the Lua stays dumb. Balatro's font (m6x11) has no suit symbols, arrows or ≤/≥, so panel text must be ASCII.

### Contract between the halves

- `SCHEMA` in `state.py` must match `schema` in `calculatro.lua`, and `overlay.SCHEMA` must match `odds_schema`; bump both sides when changing either JSON shape. The odds file never contains `null` (the Lua decoder doesn't support it).
- Information hiding is deliberate: the deck is exported as a **sorted multiset** so draw order never leaks, and face-down hand cards are exported as `{hidden: true}` with their identities only in a separate sorted `hidden_pool`. The engine treats `hidden_pool` cards as part of the unseen pool (same as undrawn deck). Don't add anything to the export that reveals information a player couldn't see.
- Empty Lua tables can arrive as `{}` rather than `[]`; the encoder tags arrays with an `ARRAY` metatable, and `state._cards` also handles it.
- Card fields mirror Balatro internals: `rank_id` 2..14 (Ace=14), `enhancement` is the center key (`m_stone`, `m_wild`, `c_base`, ...).

### Probability model

- `hands.py` expresses each poker-hand target as OR-of-patterns, each pattern an AND of `Requirement`s ("at least `need` cards whose class key is in `keys`"). Requirements are usually disjoint; they may overlap (a card then counts towards each). Class keys are `(rank_id, suit)` with suit `"Wild"` for wild cards; with Smeared, Diamonds are keyed as Hearts and Clubs as Spades, and flush-type patterns use only those two suits (`flush_suits`). Containment follows Balatro's rules (Straight Flush contains Straight; Four of a Kind does *not* contain Two Pair). Stone/hidden cards have no key; a debuffed Wild loses wildness. `combine()` builds an "any of these targets" Target, pruning implied patterns.
- `engine.probability()` is exact (`Fraction`), not Monte Carlo: pool cards are merged into classes by which requirements they count toward, then enumerated class-by-class hypergeometrically with early-accept (satisfied ⇒ binomial of remaining), pruning of unreachable patterns, and memoization on a canonical key of what the live patterns still lack. Only patterns touched by the current class are re-assessed. Targets with patterns of several suits are split by suit with inclusion-exclusion (`_union_by_suit`), so each search tracks one or two suits instead of all four; this is skipped when an unseen card is Wild, which links the suits again. Terms that are the same up to relabelling suits (same ranks kept and unseen, e.g. a fresh deck in The House) are computed once. See the module docstring and `docs/performance/` (design notes, per-joker notes, benchmark tables) before modifying, and add benchmark scenarios to `tests/perf/scenarios.py` for new joker rules.
- How a pattern is stated drives the search's size: it memoizes on what each live pattern still needs, so prefer requirements that leave little to remember. Four Fingers' Straight Flush says "a suited card of another rank" rather than "4 suited cards in all", so the search tracks whether a suit has shown up rather than its count, and runs sharing their 3 suited ranks are one pattern ("any card that completes the run"). Classes are swept rank by rank (Wild Cards first in their rank), or suit by suit when no requirement spans several suits; the order patterns are listed in doesn't matter. A pattern joins the search at its first single-rank class; draws before that towards its other requirements are deferred counts in the memo key. In the search, a pattern's state is an interned "part" (unmet requirements as bitmask of remaining classes + deficit), moved past each class by a cached transition; parts implied by a one-card part are dropped from the state.
- `cards.py` parses `-c` queries (`"2+ queens"`, `"at most 1 spade"`, `"QS"`) into `CardQuery`; these count only over the newly drawn cards (`engine.evaluate_cards`), whereas targets cover the whole final hand (`engine.evaluate_discard`).
- Joker rules (Four Fingers, Shortcut, Smeared, Pareidolia) live in `rules.Rules`, built by `GameState.rules` from non-debuffed jokers. Pareidolia affects only face-card queries (`cards._is_face`: every non-debuffed card, Stone included), never hand patterns. A `Target` only names hand types; `Target.patterns(rules)` builds (and caches) the patterns for a ruleset, and `card_key`, `probability`, `count_probability` and `CardFilter.test` all take the `Rules`.
- The "discard" scenario uses the cards currently highlighted in game (`GameState.selected`).
- `docs/jokers.md` is the joker reference (all 150, with internal keys and the exact game rules for Four Fingers / Shortcut / Smeared / Pareidolia). Use it instead of the wiki PDF.

### Tests

`tests/test_state.py` runs the real `calculatro.lua` inside `lupa` against a mocked `G` and `love.filesystem`, then parses the written file — so mod changes are testable without the game. Extend `MOCK_G` there when exporting new game fields. `MOCK_G` also mocks `UIBox` (recording every box in `BOXES`); `tests/test_overlay.py` checks the panel's text and colours end to end. The game runs LuaJIT (Lua 5.1) while `lupa.LuaRuntime` is newer Lua, so avoid 5.2+ features; to check, run the Lua tests with `lupa.LuaRuntime`/`lupa.LuaError` swapped for `lupa.luajit21`'s.

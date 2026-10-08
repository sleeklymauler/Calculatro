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

Two halves connected by a JSON file:

1. **Lovely mod** (`mod/Calculatro/`): `lovely.toml` registers `calculatro.lua` as a module and patches `Game:update` to call it every frame. The module throttles itself to ~5 snapshots/sec, serializes Balatro's global `G` with its own JSON encoder, and writes `%AppData%\Balatro\calculatro_state.json` only when the output changes (write to `.tmp`, remove, rename — the Python reader tolerates the file briefly missing).
2. **Python app** (`calculatro/`): `source.py` polls the file's mtime/size → `state.py` parses it into frozen `GameState`/`Card` dataclasses → `engine.py` computes exact odds → `__main__.py` formats output. `__main__.run_watch` runs the odds in a `worker.Job` (daemon thread): each new state cancels the running job (the engine checks the `cancel` Event and raises `engine.Cancelled`), and odds slower than ~0.25 s print the summary first with "Calculating odds..." and follow when ready. Keep new slow code paths cancellable.

### Contract between the halves

- `SCHEMA` in `state.py` must match `schema` in `calculatro.lua`; bump both when changing the JSON shape.
- Information hiding is deliberate: the deck is exported as a **sorted multiset** so draw order never leaks, and face-down hand cards are exported as `{hidden: true}` with their identities only in a separate sorted `hidden_pool`. The engine treats `hidden_pool` cards as part of the unseen pool (same as undrawn deck). Don't add anything to the export that reveals information a player couldn't see.
- Empty Lua tables can arrive as `{}` rather than `[]`; the encoder tags arrays with an `ARRAY` metatable, and `state._cards` also handles it.
- Card fields mirror Balatro internals: `rank_id` 2..14 (Ace=14), `enhancement` is the center key (`m_stone`, `m_wild`, `c_base`, ...).

### Probability model

- `hands.py` expresses each poker-hand target as OR-of-patterns, each pattern an AND of `Requirement`s ("at least `need` cards whose class key is in `keys`"). Requirements are usually disjoint; they may overlap (a card then counts towards each). Class keys are `(rank_id, suit)` with suit `"Wild"` for wild cards. Containment follows Balatro's rules (Straight Flush contains Straight; Four of a Kind does *not* contain Two Pair). Stone/hidden cards have no key; a debuffed Wild loses wildness. `combine()` builds an "any of these targets" Target, pruning implied patterns.
- `engine.probability()` is exact (`Fraction`), not Monte Carlo: pool cards are merged into classes by which requirements they count toward, then enumerated class-by-class hypergeometrically with early-accept (satisfied ⇒ binomial of remaining), pruning of unreachable patterns, and memoization on a canonical key of what the live patterns still lack. Only patterns touched by the current class are re-assessed. Targets with patterns of several suits are split by suit with inclusion-exclusion (`_union_by_suit`), so each search tracks one or two suits instead of all four; this is skipped when an unseen card is Wild, which links the suits again. See the module docstring and `docs/performance/` (design notes, per-joker notes, benchmark tables) before modifying, and add benchmark scenarios to `tests/perf/scenarios.py` for new joker rules.
- How a pattern is stated drives the search's size: it memoizes on what each live pattern still needs, so prefer requirements that leave little to remember. Four Fingers' Straight Flush says "a suited card of another rank" rather than "4 suited cards in all", so the search tracks whether a suit has shown up rather than its count, and runs sharing their 3 suited ranks are one pattern ("any card that completes the run"). Classes are ordered by the first pattern they count towards through a single-rank requirement, so list patterns that cross suits rank by rank, not suit by suit, or the search sweeps suit by suit. A pattern joins the search at its first single-rank class; draws before that towards its other requirements are deferred counts in the memo key.
- `cards.py` parses `-c` queries (`"2+ queens"`, `"at most 1 spade"`, `"QS"`) into `CardQuery`; these count only over the newly drawn cards (`engine.evaluate_cards`), whereas targets cover the whole final hand (`engine.evaluate_discard`).
- Joker rules (Four Fingers, Shortcut, Smeared, Pareidolia) live in `rules.Rules`, built by `GameState.rules` from non-debuffed jokers. A `Target` only names hand types; `Target.patterns(rules)` builds (and caches) the patterns for a ruleset, and `card_key`, `probability`, `count_probability` and `CardFilter.test` all take the `Rules`.
- The "discard" scenario uses the cards currently highlighted in game (`GameState.selected`).
- `docs/jokers.md` is the joker reference (all 150, with internal keys and the exact game rules for Four Fingers / Shortcut / Smeared / Pareidolia). Use it instead of the wiki PDF.

### Tests

`tests/test_state.py` runs the real `calculatro.lua` inside `lupa` against a mocked `G` and `love.filesystem`, then parses the written file — so mod changes are testable without the game. Extend `MOCK_G` there when exporting new game fields.

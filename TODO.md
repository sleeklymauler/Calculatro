- [ ] add progress bar/meter for benchmark.py
- [ ] improve face-down card probabilities by looking at their "windows"
  - The plan (`docs/plans/face-down-windows.md`) now works from ordered "observations" rather than per-card windows (since 2026-10-15). Consider renaming the file and this item. The review found it consistent with the information-hiding contract and with the current code.
  - Also fixes several DNA copies of a face-down card, which count as separate unseen cards for now. The plan's "Evaluation" section says what it needs, and `test_dna_several_copies_of_a_face_down_card` pins today's behaviour until then.
- [ ] remove python terminal part?

# Code review (2026-11-02)

Every reviewer that tested a claim confirmed it with a scratch reproducer, unless the item says "unverified". Severity: **[high]**, **[med]**, **[low]**. At review time the suite passed (1869 tests) and `ruff` was clean.

## Bugs

### Odds / search correctness

- [x] **[med]** Keep counts refill draws that never happen when the hand is short (`engine.py` `_discard`: `draws = hand_size - len(kept)` even with no discard)
  - The game only draws after a play or discard, at round start, or when The Manacle is disabled. Using a consumable never refills the hand.
  - How the hand ends up short while selecting: Hanged Man, Immolate, Grim/Familiar/Incantation, or a hand-size joker gained mid-blind (Judgement or Wraith giving Juggler, Troubadour or Turtle Bean).
  - Repro: hand 2♠ 5♥ 9♣, hand_size 8, rest of the deck unseen. Keep Pair shows 86.5% (5 draws); in game it's 0%. `best_discards` then favours "Keep hand" too often.
  - Odds only run while settled, so the "first deal fills the hand" case these draws were meant for never applies. Decide whether it was intended. If not, Keep should draw nothing once the hand is settled (except Serpent edge cases).
  - Update `report.py:20` ("drawing only to refill it"), `test_short_deck_limits_draws` and `test_serpent_keeping_a_short_hand_draws_nothing`.
- [x] **[low]** A Stone card's hidden base rank shows up in best-discard options (`search.py` `_identity` ~276, `_order` ~301)
  - Two Stones that look identical in game are offered as separate options, and `_order` sorts Stones by `rank_id`.
  - Repro: hand [Stone 5♥, Stone 9♣, A♠, K♦], target Pair, only Stones left to draw → options `[[], [0], [1], [3], [2], …]`.
  - Fix: treat Stones as rank 0 with no suit in `_identity`/`_order`, or stop exporting their rank and suit (see the Stone item under Information hiding).
- [x] **[med]** The builder can make queries that never match: the "Stone" special together with rank or suit chips (`calculatro.lua` `build_options` ~313)
  - `at least 1 stone queen` always shows 0% (100% for at most/none), since Stone cards have no rank or suit.
  - Fix: grey out rank and suit chips while Stone is chosen (or the reverse), or have the app reject the query.
  - `test_builder_queries_parse_as_labelled` only checks plain cards, which is why it passes. Assert that every builder query can match at least one possible card.
- [x] **[low]** Rule changes partway through a blind aren't modelled
  - [x] Crimson Heart: after a hand played as a discard (no discards left), the game re-enables the debuffed joker and debuffs another. Now modelled (`engine.rules_after`).
  - The Mark: if Pareidolia is gained or lost mid-blind, `_mark_split`'s face-down face cards were flipped under different rules. Documented as a known limitation in `docs/blinds.md`, not fixed (it would need the observation tracking of `docs/plans/face-down-windows.md`).
- [x] **[low]** DNA isn't modelled when a hand is played as the discard (`docs/jokers.md`)
  - Now modelled (`engine._dna_copies`, the mod's `dna_copies`, commit 2177da4): a lone card played as the round's first hand comes back as copies instead of being replaced by a draw, so its odds normally equal Keep's. Counts Blueprint and Brainstorm chains, and doesn't fire under The Psychic. It wasn't rare: using every discard before the first hand is enough.
  - Not yet checked in game.
  - Known limitations:
    - Several copies of a face-down card count as separate unseen cards, though they're the same card (see the windows item above).
    - With a joker face down (Amber Acorn), only DNA itself counts, since the jokers' order is hidden. This can undercount the copies.

### App robustness (Python)

- [x] **[low-med]** Some malformed snapshots got past `GameState.from_json` and crashed `run_watch`, so the app exited and the panel said "Waiting for app" for the rest of the session
  - The mod can't produce these shapes, so it took a hand-edited file or a mod bug. Crashes confirmed:
    - a non-string `id` together with a `search` request → `AttributeError` in `.split`;
    - a non-string joker `key` → `TypeError` in `summarize`;
    - a list `rank` or `suit` → unhashable `TypeError` in `rank_counts`/`suit_counts`/`short()`;
    - a lone surrogate in a string → `UnicodeEncodeError` when printing or writing the odds file.
  - Fixed: `run_watch` logs any error on its main loop through `failed()` and carries on, and `from_json` rejects non-string `id`, `state`, `blind`, `rank`, `suit` and joker `key` (`state._text`). Numbers aren't checked, since the encoder can write the game's as floats or `null`.
- [x] **[med]** `FileStateSource.read` only caught `JSONDecodeError`
  - `UnicodeDecodeError` (invalid UTF-8) and `RecursionError` (deep nesting) escaped and stopped the app. Invalid UTF-8 is reachable: the Lua encoder writes bytes ≥128 unchanged (`calculatro.lua:51`).
  - A permanently bad file (a BOM, for example) wasn't cached in `_last_key`, so it was re-read 20 times a second forever without a message.
  - Fixed: a file that can't be decoded raises `MalformedState`, so it's reported once and not read again until it changes. `read` no longer returns None for a partial file; the mod replaces the file whole, so it never sees one.
- [ ] **[med]** Any instance claims the global `Local\Calculatro` mutex, whatever `--file` it watches (`__main__.py:347`, `process.py:195`)
  - A by-hand `watch --file some\other.json` (for example, reproducing a bug report) makes the game's own app exit, and the panel stays on "Waiting for app".
  - Fix: name the mutex after the resolved state path, or claim it only for the default path.
- [ ] **[med]** `calculatro.log` is opened with `"w"` (`__main__.py:359`), so restarting the game erases the traceback the README and issue template ask players for
  - It also has no size cap. `failed()` deduplicates on the full traceback text, so a message with changing values, or odds and search errors alternating (they share `last_error`), logs at about 5 lines per second.
  - Fix: append with rotation (`calculatro.log.1`), deduplicate by exception type and location, and write a startup header (app version, Python, pid, paths).
- [ ] **[low]** Startup failures under pythonw go nowhere (`__main__.py:355-360`)
  - Silent failures: `APPDATA` unset (`KeyError`), the log failing to open, and argparse errors before stderr is redirected.
  - `process.exited()` returns "exited" when `OpenProcess` fails (`process.py:189-191`), so the app quits at once without saying why.
- [ ] **[low]** Every `OSError` from `overlay.write` is reported as "something else has it open; retrying" and retried every 0.5 s forever (`overlay.py:328-342`, `__main__.py:232`)
  - Separate `PermissionError` from other errors, and stop retrying on errors that won't go away (missing directory).
- [ ] **[low]** `source.poll` stats the file before reading it, so if the file is replaced in between, the newer content is delivered under the older key, and again on the next poll (`source.py:75-90`)
  - The only cost is a restarted odds job. Fix: stat again after reading, or deduplicate on the snapshot `id`.

### Mod (Lua)

- [ ] **[high, verify in game]** The mod can't read or write its files when the Windows user path has non-ASCII characters (`calculatro.lua:740, 745-746, 789, 945, 952`)
  - `love.filesystem.getSaveDirectory()` is UTF-8, but `io.open`/`os.remove`/`os.rename` go through the C runtime in the ANSI code page. In Lua and LuaJIT, `io.open('…/Łukasz é/x.json', 'wb')` fails.
  - In game this would mean no state file, no odds, and no `launch.txt`, so the panel says "Waiting for app" forever with nothing logged.
  - Fix: use `love.filesystem.read`/`write` relative to the save dir, and FFI `MoveFileExW(…, MOVEFILE_REPLACE_EXISTING)` for an atomic replace. The mod already has `wide()`. That also closes the remove-then-rename gap in `write_state`.
  - Add a test with a non-ASCII `getSaveDirectory`.
- [ ] **[med]** Nothing restarts the app, and the player isn't told why it's missing (`calculatro.lua:943-984`, `2400-2406`)
  - The mod launches the app only on its first frame. After a crash (see above), or after a by-hand instance holding the mutex is closed, the panel says "Waiting for app" until the game restarts.
  - A failed `ShellExecuteW` (stale `launch.txt`, a missing `python` folder, a renamed mod folder) and `guard` errors only `print` to the Lovely console.
  - Suggestions:
    - after the "Waiting for app" timeout, relaunch with backoff (the mutex already prevents duplicates);
    - show "App didn't start" on the title row;
    - append mod errors to `calculatro.log`.
- [ ] **[med]** The mod only works from a folder named exactly `Calculatro` (`launch_command` hardcodes `Mods/Calculatro/…`)
  - Lovely loads a mod from a folder of any name, so a player who extracts to `Mods\Calculatro-0.1.0-windows\` gets "Waiting for app" and no log.
  - Have the mod find its own folder. At least document it under Troubleshooting.
- [ ] **[low-med]** A search reply with an out-of-range index freezes the panel (`calculatro.lua:827-834, 906-910`)
  - `{"nonce":1,"options":[[9]]}` → `chosen[nil] = true` → "table index is nil" every frame until the hand changes.
  - Python never sends this today, so it's hardening. Fix: drop options whose indices aren't integers in `0..#hand-1` in `receive_search`.
- [ ] **[low-med, unverified]** `ffi.cdef` of `MultiByteToWideChar`/`ShellExecuteW`/`GetCurrentProcessId` can fail with "attempt to redefine" if another mod declared them first (`calculatro.lua:965-971`)
  - Auto-start would then fail silently. Wrap each declaration in its own `pcall`, or check `ffi.C.X` before declaring it.
- [ ] **[low]** The editor closes, losing what was being edited, whenever the panel is hidden briefly (pause menu, Run Info, View Deck; `calculatro.lua:997, 2351`)
  - Keep the editor's state until the blind ends rather than clearing it whenever `G.OVERLAY_MENU` is set.

## Information hiding

- [x] **[low]** Stone cards' `rank`, `rank_id` and `suit` are exported (`calculatro.lua` `card_info` ~632-680)
  - The player can't see a Stone card's base rank. The engine ignores it for odds, but it breaks the "nothing a player couldn't see" rule and does reach best-discard option identity and order (see Bugs).
  - Omit them for Stone cards.
- [ ] **[low]** The `flipped_cards` challenge modifier (random face-down cards) breaks `_mark_split`'s assumption that face-down cards under The Mark are face cards
  - Only `docs/plans/face-down-windows.md` mentions it. Add it to `docs/blinds.md` as a known limitation, and consider disabling The Mark's split in that challenge.

## Performance

- [ ] **[med]** Cache odds between snapshots
  - Nothing is cached between snapshots, so clicking a card (changing only the selection) cancels the job and recomputes every Keep row, which doesn't depend on the selection. Heavy rows take 0.4–14 s.
  - An LRU keyed on `(search_basis(state), target, discard)` in `report`/`__main__` would make selection changes nearly free. Deselecting back to an earlier selection would also hit.
- [ ] **[med]** Consider parallelism
  - Each target's rows, "Any target", the `_union_by_suit` terms and best-discard choices are independent, but all run on one thread under the GIL.
  - A persistent process pool (patterns built per worker, cancel through a shared event) could scale with cores. The docs never discuss it.
  - Don't retry conditioning on drawn Wild Cards: the docs record it was tried twice and dropped.
- [ ] **[med]** "Any target" best-discard is slow when the best odds are 1 (`search.py:83-100`)
  - A random 11-card hand with Flush + Straight + Full House and no jokers took 3.9 s: 389 exact unions (3.83 s) against about 0.1 s of single-hand bounds.
  - Hands of 10–11 cards are reachable (Juggler, Troubadour, Turtle Bean).
  - `docs/performance/best-discard.md`'s "What's still slow" only mentions Four Fingers + Shortcut, so widen it. Its untried idea (cheaply proving a choice is below 1) would help here.
  - Add a benchmark scenario for it.
- [ ] **[low]** `_relabelled` turns relabelling off whenever `state.bell` is set, even when `bell_matters(target, rules)` is false (`search.py:199-200`)
  - Use the same condition as `_groups`/`evaluate_discard`. The gain is small today.
- [ ] **[low]** `engine._gc_paused` turns the garbage collector off for the whole process while any job runs
  - With overlapping long searches, it can stay off for long stretches. Count nested pauses, and consider `gc.freeze()` or a raised threshold instead of disabling it.
- [ ] **[low]** The Lua panel does avoidable work every frame (`calculatro.lua:2352-2358`)
  - `refresh_panel` builds the model and encodes it twice per frame. `layout_key` is computed before the `key == panel_key` early return, so that work is wasted whenever nothing changed.
  - `panel_model`/`editor_model` call `builder_for` for every query every frame.
  - About 77 µs per unchanged frame in LuaJIT with 5 hands and 5 queries: small, but it's allocation churn on a heap that has already hit GC trouble.
  - Fix: compute `layout_key` after the early return, and store `editable` on the tracked entry when it's added or replaced.
- [ ] **[low]** Each changed snapshot is encoded twice, once without its `id` and once with it (`calculatro.lua:751-756`)
  - About 150 µs each with a full deck. Splice the `id` into the first encoding instead.

## Architecture / cleanup

- [ ] **[med]** Split `calculatro.lua` (2439 lines) into modules: `json`, `builder`, `snapshot`, `search`, `panel`, `editor`, `anim`, `uibox`
  - Register each with its own `[[patches]] module` in `lovely.toml`, so each can be tested on its own and is easier to navigate.
  - `Calculatro.definition`, `cards_tab` and `refresh_panel` are the biggest functions to break up.
- [ ] **[med]** Slim `CLAUDE.md` (about 4.5k words, loaded into every agent session; the "Odds panel" item alone is one 1,335-word line)
  - Move panel and editor internals (shadow depths, `FONT_PX`, `ARROW_CELL_W`, `register_once` history…) to e.g. `docs/mod-panel.md`, and best-discard details to `docs/performance/best-discard.md`.
  - Keep commands, the contract between the two halves, a short list of must-not-break invariants (schemas, information hiding, cancellability, Lua 5.1, no globals, game units not pixels), and pointers to the docs.
- [ ] **[low]** Remove `dump_lua = true` from `lovely.toml` in releases
  - It makes Lovely write dumps of the patched game files on every player's launch. Strip it in `build_release.ps1`.
- [ ] **[low]** Engine tidy-ups
  - `engine.py:582` fills `_Layout` from `locals()`, so renaming a local breaks it silently. Build it explicitly.
  - `_search` is a ~280-line closure.
  - Five near-duplicate greedy "disjoint requirements" helpers could share code: `hands._at_least` ~220, `hands._disjoint` ~312, `engine._cards_within` ~291, `_bound` ~875, `_disjoint_groups` ~893.
  - `count_probability` (`engine.py:1078-1084`): rename the loop's `k` (it's the total face count, not `_mark_split`'s `k`) to match the docstring.
- [ ] **[low]** Dead or loose Python code
  - Unused by the app: `FileStateSource.watch()`, `poll_interval`, and the `StateSource` protocol's `read`/`watch`.
  - `__main__.odds()` exists only for tests and duplicates `calculate()`. Its `queries: list[CardQuery] = ()` default doesn't match the annotation.
  - Missing blank lines between `settled()` and `RETRY` (`__main__.py:167-168`).
  - `_search` accepts `nonce=True`, since bool is an int (`state.py:84`).
  - `_strings("Pair")` splits a string into characters instead of rejecting it.

## Tooling / CI / release

- [ ] **[med]** CI never builds the release, and never checks that `pyproject.toml` and `lovely.toml` versions match until a tag is pushed
  - Run `build_release.ps1` on PRs touching `scripts/`, `mod/` or `calculatro/`, and add a pytest that the two versions match.
- [ ] **[low]** Pin or bound the dev dependencies (`ruff`, `pytest`, `lupa`) and add `cache: pip` to CI
  - Unpinned, a new ruff rule or a lupa release without Windows `luajit21` wheels would break CI with no change in the repo.
  - Dependabot only watches GitHub Actions; add pip.
  - `release.yml` runs tests but not ruff, and tests on "3.14" rather than the bundled 3.14.8.
- [ ] **[low]** `install_mod.ps1:30` uses `-Encoding utf8NoBOM`, which doesn't exist in Windows PowerShell 5.1
  - Under 5.1 the mod is copied, then the script throws before writing `launch.txt`. Add `#Requires -Version 7`, or use `[IO.File]::WriteAllText` as `build_release.ps1` does.
- [ ] **[low]** `build_release.ps1:101`: `Copy-Item mod\Calculatro\* $mod` has no `-Recurse`, so a future mod subfolder (assets, localisation) would ship empty
- [ ] **[low]** The plain test suite takes about 3.5 minutes; consider `pytest-xdist` (`-n auto`) as a dev dependency
  - The slowest single test is 6.8 s (`test_matches_trying_every_discard_with_jokers[jokers1]`). The time is spread over many brute-force and Lua tests that would parallelise well.
  - LuaJIT runtimes are per-test, so check that the `lua` fixture is safe across worker processes.

## Tests to add / change

- [ ] **[med]** Seeded fuzz tests against brute force for the engine and search, as a `-m fuzz` tier or a time-boxed default
  - The review's fuzzers ran about 13k `probability` cases (ranks 2–14, all 16 rule sets), about 210k Reuse-against-alone cases, and about 2k best-discard states against every subset. They found only a Bell + Royal Flush bug (about 1e-4), gone with Royal Flush.
  - Existing brute-force tests mostly use `random_card`'s ranks 9–14 and fixed 10–14-card decks. They miss `select_limit` 0/1, hands of 1–3 cards, empty or tiny decks, The Mark + Pareidolia, and modifiers with The Mark/The Hook.
  - 100 random best-discard states take about 5 s.
- [ ] **[med]** Python app robustness tests
  - [x] `run_watch` surviving bad snapshots on the main loop.
  - [x] `poll` with invalid UTF-8, a BOM, or a permanently corrupt file.
  - The normal `main(--game-pid)` path: log created, stdout to devnull, loop stopping when the game exits. Only the "already running" exit is tested.
- [ ] **[med]** Lua tests
  - Encoder edge cases: NaN/inf → `null`, control characters/DEL → `\u00xx`, `2^60`. They behave correctly today but nothing asserts it.
  - Decoder rejection: trailing commas, `null`, truncated input.
  - `write_state` failing (`last_json` stays unset, the snapshot is retried, `seq`/`round_seq` stay right).
  - `guard` reports once and the game continues.
  - Malformed or out-of-range search replies.
  - Non-ASCII save directory.
  - Optional: a test that `refresh_panel` with an unchanged model returns early.
- [ ] **[low]** Split `tests/test_state.py`
  - Its module-level `importorskip("lupa")` also skips the pure-Python `GameState` tests when lupa is missing. Separate the parse tests from the Lua tests.
- [ ] **[low]** `test_fast_odds_print_with_summary` (`tests/test_watch.py:122-126`) is likely flaky
  - It depends on a fake job finishing within `patience=0.05` s. Raise it.
  - `watch_thread` tests leave `run_watch` threads running if an assertion fails.
- [ ] **[low]** Lines no test reaches
  - `engine.py:140-141` (Mark fallback when fewer face cards are unseen than face down).
  - `engine.py:626-629` (626 may be dead, since `_union_by_suit` checks first).
  - `engine.py:1026` (Bell with an empty final hand).
  - `hands.py:324`.
  - Cancellation is only tested with the event already set. Add a mid-search latency test; it measured 4–62 ms on the heaviest scenarios.
- [ ] **[low]** Widen `test_shared_work_gives_the_same_odds` (6 fixed setups) to cover Serpent, Shortcut alone and Smeared+Bell, or make it randomized
- [ ] **[low]** Card query tests
  - Pin the poker-notation readings that are right today but untested: `as` → A♠, `ts` → T♠, `10 s` → at least 10 spades, `2 c` → at least 2 clubs.
  - [x] Add Stone + rank/suit builder queries (see Bugs).
- [ ] **[low]** Perf scenarios to add
  - The Serpent (none today).
  - A single Hook odds row (only best-discard ones exist).
  - The 11-card "Any target" case (see Performance).
  - `benchmarks.md` was last updated 2026-10-11, but the engine changed on 10-13 and 10-15; re-run it.

## Query parser (terminal input)

- [ ] **[low]** Normalise whitespace in `parse_query`/`parse_filter` (`cards.py:175, 331`)
  - `"red  cards"` (two spaces) fails: the `(?<!red)\s+cards?$` lookbehind sees `"ed "`. `"2 + queens"` fails while `"2+queens"` works.
- [ ] **[low]** Make the grammar consistent
  - Negation works for suits and modifiers but not ranks: `not queen`, `not face`, `not 5`, `not red cards` fail, though the app's own labels say `not 5/9/K`.
  - Missing forms: `red`, `black`, `red queens`, `queen of red`, `evens`/`odds`, `plain` (the app's own name for "not enhanced"), and `stone of hearts` (a modifier, "of", then suits).
  - `no edition` means "count is zero" while the label `No Edition card` means the negation. Same for seals.
  - `at least 2 of clubs` fails with `unknown card filter 'of clubs'`.
  - `0+ queens` and `at least 100 queens` are accepted silently.

## Docs

- [ ] **[med]** Every commit hash the perf docs cite (`7d2af71`, `e239e4d`, `62eb51c`, `0c93930`, `80cb7d7`, `6b93aac`) no longer exists, since history was rewritten
  - In: `docs/performance/README.md:116`, `benchmarks.md` (19, 22, 112, 115, 197, 364, 418, 445), `best-discard.md:121`. The "Comparing with an older version" `git worktree add … 7d2af71` recipe fails.
  - Map each to its current hash (e.g. Four Fingers perf work → `ff937b7`), or name the commit by message.
- [ ] **[med]** Broken anchor: `CONTRIBUTING.md:24` and `.github/ISSUE_TEMPLATE/config.yml` link `README.md#installation`, but the heading's anchor is `#installation-users`
- [ ] **[med]** README Usage says everything twice (`README.md:59-80`)
  - A bullet list, then a prose block that restates it, with stray blank lines and an overlong leftover line. Merge them.
- [ ] **[med]** Add a "Known limitations" section to the README
  - Best discard looks only at this discard and draw, not the round's later discards ("optimal" overstates it).
  - Four Fingers + Shortcut Straight Flush searches take seconds.
  - The Psychic under 5 cards.
  - Windows only.
  - Steamodded and other mods are untested (custom suits and ranks, `rank_id` > 14, modded enhancements and bosses aren't handled).
  - The mod folder name must be `Calculatro`.
  - Under Troubleshooting: copy `calculatro.log` before restarting the game, until the log stops being truncated.
- [ ] **[low]** README:9 "Find the optimal discard selection for a target hand" leaves out that card query rows can be searched too
- [ ] **[low]** `docs/blinds.md:33` lists Serpent, Mark and Bell as checking `blind_disabled`, but leaves out `GameState.hook`, which does too
- [ ] **[low]** `docs/jokers.md`: Splash is tagged `rule` (legend line ~123, table ~217), but its own section and `rules.RULE_JOKERS` treat it as not changing hand types. Untag it or make it `cond`
- [ ] **[low]** Stale or misplaced comments
  - `calculatro.lua:2151-2154`: the card-name popup paragraph is merged into `register_once`'s comment; it belongs above `hover_popups` at ~2184.
  - `calculatro.lua:805-806`: "each target hand's search button" (card rows have them too).
  - `calculatro.lua:132`: `list(open, close, item)` never uses `open`.
  - `engine.py:77, 1064`: stray line breaks in docstrings.
  - `tests/perf/scenarios.py:246`: "The Mark" comment sits above the Bell scenarios.
  - `report.py:21, 28`: `discard` is also None on the last hand with no discards.
  - Document that under Cerulean Bell the Discard column always shows "discard the forced card", since it's always highlighted.
- [ ] **[low]** Bug report template: also ask for `calculatro_odds.json` (or a screenshot taken at the same moment) to tell app bugs from panel bugs

## Feature ideas (roughly by value to a player)

- [ ] Look ahead across all remaining discards: "odds by the end of the round if you play your discards well", and a best discard aimed at that
  - Today's search optimises this discard only, which can be the wrong move with 2+ discards left. Expensive; would probably need a depth limit or approximations.
- [ ] Show that a best "discard" is really a play when no discards are left, and account for its side effects
  - It can win the blind outright, and triggers play-only boss penalties: The Ox ($0), The Arm (hand level down), The Tooth ($1/card), The Psychic (needs 5 cards). At least show "Play i/n", or skip such bosses.
- [ ] Presets for jokers' per-round targets
  - The Idol's card, Mail-In Rebate's rank, Castle's and Ancient Joker's suit, To Do List's hand. All are public (shown on the joker, in `G.GAME.current_round`); this needs a small export plus builder presets or auto-suggested targets.
- [ ] Show Keep for card queries when the hand has face-down cards
  - It's only 0%/100% with a full face-up hand. Under The House, The Wheel, The Fish and The Mark it's a real probability, which `evaluate_cards` computes and `overlay.odds` (`overlay.py:80-85`) throws away.
- [ ] Targets for joker conditions (`cond` in `docs/jokers.md`)
  - Flush in a given suit (Castle, Ancient, Bloodstone), Flower Pot's four suits, Seeing Double, Square Joker's exactly-4-card hand, Blackboard's all-black hand. Some can already be written as card queries; first-class presets would make them discoverable.
- [ ] Keep search options tied to card objects rather than positions, so sorting the hand doesn't lose the "i/n" cycle (`search_basis` would need to ignore hand order too)
- [ ] Send the panel's visibility with the snapshot, so the app can skip calculating while it's hidden
- [ ] Remember the panel's shown/hidden state between sessions (`Calculatro.visible` isn't saved)
- [ ] Make the `C` hotkey configurable; add controller support for the panel's buttons (mouse only today)
- [ ] CLI: a one-shot `calculatro odds FILE [--target …]` that prints a report and exits, to reproduce a bug report's state file; and `--version`, with the mod sending its version so a mismatch beyond the schema number is logged
- [ ] Linux/macOS/Steam Deck (Proton) support: needs alternatives to `%APPDATA%`, `ShellExecuteW` and the Windows mutex

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
- [x] **[low]** Any instance claimed the global `Local\Calculatro` mutex, whatever `--file` it watched
  - A by-hand `watch --file some\other.json` (for example, reproducing a bug report) already running when the game launched made the game's own app exit, and the panel stayed on "Waiting for app". Started after the game's app, it wrongly said "already running" and wrote no odds file.
  - Fixed: the mutex is named after a hash of the resolved state path (`process.instance_name`).
- [x] **[med]** `calculatro.log` was opened with `"w"`, so restarting the game erased the traceback the README and issue template ask players for
  - `failed()` also deduplicated on the full traceback text, so a message with changing values, or odds and search errors alternating, logged a traceback with every snapshot.
  - Fixed: `log.open_log` appends, moving a log over 256 KB to `calculatro.log.1` at startup (not during a session, so there's no strict cap), each session starts with a header (`log.header`), and the home folder is written as `~`, since it holds the player's username. `failed()` logs each exception type and stack once per session, and a failure to log no longer stops the app.
- [x] **[low]** Startup failures under pythonw went nowhere
  - Fixed: a log that can't be opened no longer stops the app (it runs without one), and a game process that can't be found or opened is logged before the app exits.
  - Won't fix: `APPDATA` unset and argparse errors. The app inherits the game's environment and the mod hardcodes the arguments, so neither is realistic, and the panel's "App didn't start" and the mod's log line cover them, like a failed import of the package.
- [ ] **[low]** Every `OSError` from `overlay.write` is reported as "something else has it open; retrying" and retried every 0.5 s forever (`overlay.py:328-342`, `__main__.py:232`)
  - Separate `PermissionError` from other errors, and stop retrying on errors that won't go away (missing directory).
- [ ] **[low]** `source.poll` stats the file before reading it, so if the file is replaced in between, the newer content is delivered under the older key, and again on the next poll (`source.py:71-85`)
  - The only cost is a restarted odds job. Fix: stat again after reading, or deduplicate on the snapshot `id`.

### Mod (Lua)

- [ ] **[high, verify release build]** The mod couldn't read or write its files when the Windows user path has non-ASCII characters
  - `love.filesystem.getSaveDirectory()` is UTF-8, but `io.open`/`os.remove`/`os.rename` go through the C runtime in the ANSI code page (`Balatro.exe`'s manifest has no `activeCodePage`).
  - Reproduced with the mod in the game's own `lua51.dll` and `love.dll`, with `APPDATA` set to a non-ASCII folder: no state file, no app started, nothing logged, and the panel on "Calculating..." forever.
  - Fixed: files go through `love.filesystem`, and `Calculatro.replace_file` (FFI `MoveFileExW`) replaces the state file in one step, closing the remove-then-rename gap. A state file that can't be written is logged after 10 snapshots in a row. With no snapshot written, the panel no longer says "Calculating...".
  - The same setup with the fix: the app starts and the odds come back, in `Luké`-style and `Łukasz光`-style folders (LÖVE 11.5, LuaJIT 2.0.5). `love.filesystem.append` works while the app holds the log open.
  - Checked in game (2026-11-03) with `APPDATA` set to a non-ASCII folder, running the venv's Python through `launch.txt`. Not yet checked: a release build's bundled Python running from such a folder. Close this once it has been.
- [x] **[med]** Nothing restarted the app, and the player wasn't told why it was missing
  - The mod launched the app only on its first frame. After a crash, or after a by-hand instance holding the mutex was closed, the panel said "Waiting for app" until the game restarted.
  - A failed `ShellExecuteW` (stale `launch.txt`, a missing `python` folder, a renamed mod folder) and `guard` errors only `print`ed to the Lovely console.
  - Fixed: `check_app` starts the app again after 5, 15 and 45 seconds of silence, counted afresh once it answers. The title row says "App didn't start" if Windows refused the start or nothing could be started (neither is retried), or after the second restart. Mod errors go to `calculatro.log` with the home folder as `~`.
  - Real launches checked outside the game through LuaJIT's FFI (kill the app, the mod restarts it). Not yet checked in game.
  - Known limitation: a schema mismatch (mod not reinstalled after an update) also says "App didn't start", though the app is running and rejecting the snapshots; the log has its message.
- [x] **[low]** The mod only worked from a folder named exactly `Calculatro` (`launch_command` hardcoded `Mods/Calculatro/…`)
  - Lovely loads a mod from a folder of any name, so a renamed folder got "App didn't start". This doesn't cover the zip extracted to Explorer's suggested destination: that's one folder too deep for Lovely, so no mod code runs. The zip keeps its top-level `Calculatro` folder.
  - Fixed: `Calculatro.mod_folder` looks in `Mods` for the folder whose `lovely.toml` registers `calculatro.core`, skipping any with a `.lovelyignore`. With several copies it prefers `Calculatro` and logs a warning.
  - Not yet checked in game, including what several copies do when Lovely loads them all.
- [ ] **[low-med]** A search reply with an out-of-range index freezes the panel (`search.lua:26-28, 95`)
  - `{"nonce":1,"options":[[9]]}` → `chosen[nil] = true` → "table index is nil" every frame until the hand changes.
  - Python never sends this today, so it's hardening. Fix: drop options whose indices aren't integers in `0..#hand-1` in `receive_search`.
- [x] **[low-med]** `ffi.cdef` of `MultiByteToWideChar`/`ShellExecuteW`/`GetCurrentProcessId` could fail with "attempt to redefine" if another mod declared them first
  - Not a bug: on the game's LuaJIT 2.0.5, functions can be declared again, even with other spellings of the types (`LPCWSTR`, `DWORD`). Only struct clashes raise it, and the mod declares only functions.
- [ ] **[low]** The editor closes, losing what was being edited, whenever the panel is hidden briefly (pause menu, Run Info, View Deck; `panel.lua:26, 183`)
  - Keep the editor's state until the blind ends rather than clearing it whenever `G.OVERLAY_MENU` is set.

## Information hiding

- [x] **[low]** Stone cards' `rank`, `rank_id` and `suit` are exported (`calculatro.lua` `card_info` ~632-680)
  - The player can't see a Stone card's base rank. The engine ignores it for odds, but it breaks the "nothing a player couldn't see" rule and does reach best-discard option identity and order (see Bugs).
  - Omit them for Stone cards.
- [ ] **[low]** The `flipped_cards` challenge modifier (random face-down cards) breaks `_mark_split`'s assumption that face-down cards under The Mark are face cards
  - Only `docs/plans/face-down-windows.md` mentions it. Add it to `docs/blinds.md` as a known limitation, and consider disabling The Mark's split in that challenge.

## Performance

- [x] **[med]** Cache odds between snapshots
  - Each row's odds are kept until `odds_basis` changes (`build_report`'s `known`), so going back to an earlier selection is instant (a 2 s row took 0.08 ms), including after sorting the hand. Rows finished before a job is cancelled are kept; one cancelled midway starts again.
  - Keep rows turned out to be cheap (a full face-up hand draws nothing), so the gain is in Discard rows.
  - Not yet checked in game.
  - Not done: a best-discard search's result isn't stored, so the row it selects is calculated again.
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
- [ ] **[low]** The Lua panel does avoidable work every frame (`panel.lua:183-190`)
  - `refresh_panel` builds the model and encodes it twice per frame. `layout_key` is computed before the `key == panel_key` early return, so that work is wasted whenever nothing changed.
  - `panel_model`/`editor_model` call `builder_for` for every query every frame.
  - About 77 µs per unchanged frame in LuaJIT with 5 hands and 5 queries: small, but it's allocation churn on a heap that has already hit GC trouble.
  - Fix: compute `layout_key` after the early return, and store `editable` on the tracked entry when it's added or replaced.
- [ ] **[low]** Each changed snapshot is encoded twice, once without its `id` and once with it (`files.lua:74-78`)
  - About 150 µs each with a full deck. Splice the `id` into the first encoding instead.

## Architecture / cleanup

- [x] **[med]** Split `calculatro.lua` (2644 lines) into modules
  - Now `core`, `json`, `tracked`, `builder`, `snapshot`, `search`, `files`, `log`, `app`, `widgets`, `uibox`, `anim`, `odds`, `editor`, `panel` and `calculatro.lua` for the hooks; none over about 350 lines. Each is registered in `lovely.toml` as `calculatro.<file>`, since `package.preload` is shared with other mods.
  - They still share one `Calculatro` table, which kept the tests' use of the mod nearly unchanged and the move verbatim, but require each other without cycles, so each loads on its own (`tests/test_mod_modules.py`). The tests load the mod from `lovely.toml`, so a file missing there fails them.
  - `Calculatro.definition`, `cards_tab` and `refresh_panel` are broken up.
  - Ran in the game's own `lua51.dll` (LuaJIT 2.0.5), registered as Lovely's `module.rs` does, against the mocked game. Not yet checked in game.
- [x] **[med]** Slim `CLAUDE.md` (about 5k words, loaded into every agent session; the "Odds panel" item alone was one 1,335-word line)
  - Now `AGENTS.md` (about 1k words: commands, a short architecture, must-not-break rules, tests, and which doc to read for what), which `CLAUDE.md` and `GEMINI.md` import.
  - The details moved to `docs/architecture.md`, `docs/panel.md` and `docs/probability-model.md`.
  - Not yet checked with the other agents' own tools (Codex, Cursor, Gemini CLI, Copilot); that they read these files comes from their docs.
- [ ] **[low]** Remove `dump_lua = true` from `lovely.toml` in releases
  - It makes Lovely write dumps of the patched game files on every player's launch. Strip it in `build_release.ps1`.
- [x] **[low]** Engine tidy-ups
  - `_Layout` is a dataclass built explicitly, so renaming a local in `_layout` can't break it silently.
  - `_search`'s setup moved to `_initial_parts`, `_join_tables` and `_class_signatures`, and pruning to `_drop_implied`. The recursion (`ways`) stays a closure for speed, so `_search` is still about 185 lines.
  - `hands.disjoint_groups` is now the greedy grouping behind `_at_least`, `_bound`, `_disjoint_groups` and `_cards_within`. `hands._disjoint` doesn't use it: it splits a pattern into an OR of patterns, which isn't grouping.
  - `count_probability`'s loop variable is now `a`, as in `_mark_split`.
  - All 559 benchmark scenarios gave their expected results, with total time unchanged (100.4 s before, 99.9 s after, adding each scenario's fastest run).
- [x] **[low]** Dead or loose Python code
  - Removed `FileStateSource.watch()`, its `poll_interval`, and `read`/`watch` from the `StateSource` protocol. `FileStateSource.read` stays, since `poll` uses it.
  - Removed `__main__.odds()`; its tests now go through `calculate()`.
  - Added the missing blank lines between `settled()` and `RETRY`.
  - `_search` in `state.py` ignores a request with `nonce=true`.
  - `_strings` rejects anything but a list, so `"Pair"` or a non-empty object makes the snapshot malformed. An empty Lua table (`{}`) still means an empty list.

## Tooling / CI / release

- [ ] **[med]** CI never builds the release until a tag is pushed
  - Run `build_release.ps1` on PRs touching `scripts/`, `mod/` or `calculatro/`.
  - [x] A pytest that the versions match (`test_versions_match`, which also covers `calculatro/__init__.py`).
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
  - [x] `write_state` failing: the snapshot is retried, and repeated failures are reported once. `seq`/`round_seq` staying right is still untested.
  - Malformed or out-of-range search replies.
  - [x] Non-ASCII save directory (with a stand-in for `love.filesystem`; the real one was only checked by hand).
  - Optional: a test that `refresh_panel` with an unchanged model returns early.
- [ ] **[low]** Split `tests/test_state.py`
  - Its module-level `importorskip("lupa")` also skips the pure-Python `GameState` tests when lupa is missing. Separate the parse tests from the Lua tests.
- [ ] **[low]** `test_fast_odds_print_with_summary` (`tests/test_watch.py:122-126`) is likely flaky
  - It depends on a fake job finishing within `patience=0.05` s. Raise it.
  - `watch_thread` tests leave `run_watch` threads running if an assertion fails.
- [ ] **[low]** Lines no test reaches
  - `engine.py:151-152` (Mark fallback when fewer face cards are unseen than face down).
  - `engine.py:788-791` (788 may be dead, since `_union_by_suit` checks first).
  - `engine.py:1162` (Bell with an empty final hand).
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
- [ ] **[low]** README:9 "Find the optimal discard selection for a target hand" leaves out that card query rows can be searched too
- [ ] **[low]** `docs/blinds.md:33` lists Serpent, Mark and Bell as checking `blind_disabled`, but leaves out `GameState.hook`, which does too
- [ ] **[low]** `docs/jokers.md`: Splash is tagged `rule` (legend line ~123, table ~217), but its own section and `rules.RULE_JOKERS` treat it as not changing hand types. Untag it or make it `cond`
- [ ] **[low]** Stale or misplaced comments
  - `uibox.lua:107-110`: the card-name popup paragraph is merged into `register_once`'s comment; it belongs above `hover_popups` at 140.
  - `search.lua:4`: "each target hand's search button" (card rows have them too).
  - `json.lua:93`: `list(open, close, item)` never uses `open`.
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

# Code review (2026-11-12)

A read-through of the whole project at `d993a13`: the app (`calculatro/`), the mod (`mod/Calculatro/`),
the tests, the scripts, CI and the docs. `editor.lua`, `widgets.lua` and `anim.lua` were only skimmed,
and the tests were read for structure, not line by line.

Nothing here was reproduced. Each item says how sure it is:

- **seen**: the code is as described; whether it matters is for you to judge.
- **guess**: a suspicion from reading, which may turn out to be nothing.
- **carried**: from the review of 2026-11-02, and still as it was in the code.

Severity is **[high]**, **[med]** or **[low]**. Line numbers are as of this commit.

Each item has a label, a letter for its section and a number: **B** bugs, **R** app robustness, **M** mod,
**P** performance, **A** architecture, **T** tests, **C** tooling and CI, **D** docs, **I** ideas.

## Recommended order

Ten steps, each a batch that fits in one or two changes. The order puts what's cheap and certain first, then
what makes the later steps safer, and leaves the large refactors and the performance work until tests cover
them. Within a step, the items are in the order to take them. Every item is in exactly one step.

1. **Docs that are wrong today.** D1, D2, D5, D9, D10, D3, D11.
   - No code changes and no risk, and D1 and D2 mislead anyone reading the code now. D3 takes longest: each
     dead hash has to be matched to a commit by its message.
2. **Tooling.** C2, C3, C4, C1.
   - Small script and workflow edits. C1 comes last so it tests the build script with C2 and C3 already in
     it. All four should be done before the first public release.
3. **Test layout.** T1 (which covers T2), T10, T6, C5, then C6.
   - Every later step adds tests, so the fixtures should be in `conftest.py` first. C6 (`pytest-xdist`) comes
     after T1 and T6: shared helpers and timing-dependent tests are what break under parallel runs. A faster
     suite then pays back on every step after this one.
4. **Small, certain fixes, each with its test.** B1, B2, T7, B9, B10, B11, R1, R2, R3, B8, M2.
   - All are seen in the code, need no design, and touch a few lines. T7 goes with B1 and B2, whose tests it
     lists.
5. **Settle the guesses.** B3, B12, M4, P8, P3, P4, P7, B6.
   - Reproduce or time each before changing anything, and delete the ones that come to nothing. B3 and B12
     could be real bugs, so they go first. P3, P4, P7 and P8 need only a profiler and a benchmark run; fix
     the ones that show up here, while the measurements are fresh. Save a benchmark baseline before starting.
6. **A safety net for the refactors.** T4, T3, T5, T8, T9.
   - T4 is the test A3 asks for, and T3 is what would catch a mistake in step 8. T9's scenarios give the
     performance work in step 9 something to measure.
7. **Small cleanups.** A9 (the decision only), P13, A6, A4 with P6, P5, P9, A8.
   - Decide A9 first: without the terminal output, P13 goes away and A1 gets smaller. A6 bumps the state
     schema, so do it together with anything else that changes the snapshot. A4 and P6 are one change.
8. **Refactors.** A3, A5, A2, A1, A7, then B13.
   - Behaviour shouldn't change, which steps 3 and 6 let the tests show. A3 and A5 first, since they decide
     where A2's lines are cut. B13 (a hung app) is easier to add once A1 has split the loop up.
9. **Performance.** P10, P11, P12, then P1, which settles B4, B5 and P2.
   - Parallelism (P1) is the largest item in the file and changes how jobs, limits and memory are handled,
     so it wants the split-up loop from step 8. Look at B4, B5 and P2 again only after it: a search in its
     own process may remove all three.
10. **What the player sees, and the README.** M1, M3, B7 with I1, then D6, D7, D8.
    - The README goes last so it describes the panel as it ends up. M1 and M3 need trying in game.

The remaining ideas (I2 to I10) come after all of this, in whatever order suits. I10 (`calculatro odds FILE`)
is worth pulling forward if bug reports start arriving, and I2 if step 9 leaves slow reports as they are.

## Bugs and correctness

- [x] **B1** **[low-med] (carried)** A search reply with an out-of-range index breaks the panel until the hand changes (`search.lua:35-42`)
  - `is_selected` does `chosen[cards[i + 1]] = true`, which raises "table index is nil" for an index past the hand. `Calculatro.select` already guards with `if card`.
  - The app never sends one today. Drop options with indices outside `0..#hand-1` in `receive_search`.
  - Done: `valid_options` drops any option with an index that isn't a whole number in the hand, both on
    arrival and when comparing later payloads. A reply left with none is ignored, not taken as given up on.
    Tested in `test_search_options_outside_the_hand_dropped`.
- [x] **B2** **[low] (seen)** The mod ignores an odds file of another schema without saying so (`files.lua:163`)
  - After a partial update (new mod, old app, or the reverse) the panel says "Waiting for app" then "App didn't start", and nothing is logged on the mod's side. `report()` it once, as the app does for a state schema it can't read.
  - Done: `receive_odds` reports it once when the file answers one of this session's snapshots. Another
    session's file is still ignored silently, since it's usually the last session's, left until the app
    answers. Tested in `test_odds_of_another_schema_are_reported_once`.
- [ ] **B3** **[low] (guess)** `probability` and `count_probability` clamp the draws differently under The Mark (`engine.py:109`, `engine.py:2183`)
  - `probability` clamps to `len(pool)`, `count_probability` to `len(pool) - marked + marked_kept`. In `_mark_split`, `comb(n - marked, d)` is 0 when `d > n - marked`, which would be a division by zero.
  - `_discard` seems to keep the draws within range for real states, so this may be unreachable. DNA's `added_hidden` with The Mark is the case to check.
- [ ] **B4** **[low] (guess)** A value can be given up on for memory another job used (`worker.py:111-114`, `report.py:273`)
  - The watchdog stops every running attempt when the process is over the limit. An odds value in its exact pass that was stopped because a best-discard search filled the memory is stored as `TooSlow(MEMORY)` until `odds_basis` changes.
  - Possible fix: retry a value stopped for memory once, after the other job has ended.
- [ ] **B5** **[low] (guess)** Time limits are wall-clock, but the odds job and a search share one interpreter lock (`worker.py`, `__main__.py:366`)
  - With a search running, a value's quick try (0.25 s) gets about half the work done, so which values show as slow depends on what else is running. The same goes for the 30 s limits.
  - See P1: a search in its own process would fix both.
- [ ] **B6** **[low] (guess)** With many slow rows, the first approximation may be late (`report.py:281-290`)
  - Every value gets its quick try (0.25 s each) before any estimate is made. A full panel has up to 36 values, so in the worst case that's about 9 s of "Calculating..." before the first asterisked value.
  - `unchecked` only shortens this for the hand's sorts. Consider estimating a value straight after its quick try fails, or a budget for the whole quick pass.
- [ ] **B7** **[low] (seen)** When every choice ties, a best-discard search returns every discard (`search.py:187`, `search.py:236`)
  - For a target that can't be made (0% whatever is thrown away), the options are every distinct discard: over 200 for 8 cards, about 1000 for 11. The panel then shows "1/219".
  - "Keep hand" is first, since options are ordered by size. Consider returning only that when the best odds equal Keep's, or capping the list.
- [ ] **B8** **[low] (seen)** The hint over a face-down card isn't refreshed when the rules change (`hint.lua:271`)
  - Its key is the observations and the piles' sizes. Selling Pareidolia, or Chicot or Luchador disabling The Mark, changes `only_face_cards()` but not the key, so the box stays as it was until another card is hovered.
- [ ] **B9** **[low] (seen)** Snapshot numbers aren't coerced (`state.py:312-328`)
  - `select_limit` of `null` becomes `None` and fails in `min()` in the search. A float `hand_size` would fail in `comb`. The mod writes neither today, and `run_watch` logs and carries on, but every state would fail the same way.
  - `int()` the numeric fields in `_parse`, falling back to the default.
- [ ] **B10** **[low] (seen)** `tracked()` compares a query's raw text with the stripped text of those already parsed (`__main__.py:132`)
  - `"queen"` and `"queen "` both get rows. The builder never sends padded text.
- [ ] **B11** **[low] (seen)** A payload can carry `"id": null` (`overlay.py:74`, `overlay.py:142`)
  - Only for a hand-written state file without an `id`, but it breaks the "no `null` in the odds file" rule. Leave the key out.
- [ ] **B12** **[low] (guess)** The file thread may outlive a restart of the game's Lua state (`worker.lua`)
  - If Balatro restarts through `love.event.quit('restart')`, the old thread may keep running on the same named channels, beside the new one: two writers of the same `.tmp` file, and results read by either. Check what LÖVE does with threads on restart.
- [ ] **B13** **[low] (guess)** Nothing recovers from an app that hangs without exiting
  - The mod starts it again, but the new one finds the mutex held and exits. A heartbeat in the odds file, or the mod ending a silent app's process, would cover it. No hang is known.

## App robustness (Python)

- [ ] **R1** **[low] (carried)** Every `OSError` from `overlay.write` is reported as "something else has it open; retrying" and retried every 0.5 s for good (`overlay.py:183-197`, `__main__.py:329`)
  - Tell `PermissionError` from the rest, and stop retrying what won't go away (a missing folder, a full disk).
- [ ] **R2** **[low] (carried)** `FileStateSource.poll` stats the file before reading it (`source.py:71-93`)
  - A file replaced in between is delivered under the older key, then again on the next poll. The cost is one restarted job. Stat again after reading, or deduplicate on the snapshot's `id`.
- [ ] **R3** **[low] (seen)** `engine._ignored` grows without limit (`engine.py:1208`)
  - One entry per set of observations no deal fits. Small, but the other caches beside it are bounded.

## Mod (Lua)

- [ ] **M1** **[low] (carried)** The editor and the settings page close whenever the panel is hidden for a moment (`panel.lua:202`)
  - The pause menu, Run Info and View Deck all set `G.OVERLAY_MENU`. Looking at the deck while building a card query loses the query. Keep the state until the blind ends.
- [ ] **M2** **[low] (seen)** The builder's counts stop at 8 (`builder.lua:45-50`)
  - Hands of 10 or 11 cards are reachable (Juggler, Troubadour, Turtle Bean). Take the largest count from the hand size, or raise it.
- [ ] **M3** **[low] (seen)** `Calculatro.visible` isn't saved
  - There's a settings file now (`calculatro_settings.json`), so the panel's shown or hidden state could go in it.
- [ ] **M4** **[low] (seen)** The hint's search runs on the game's thread, up to 20000 steps in one frame (`hint.lua:23`)
  - Worth timing in LuaJIT for the worst hand (eight face-down cards sorted both ways). If it's more than a few milliseconds, spread it over frames.

## Performance

- [ ] **P1** **[med] (carried)** Consider parallelism
  - Rows, "Any target", `_union_by_suit`'s terms and a search's choices are independent, but everything runs on one thread. A persistent process pool, or at least the search in its own process, would also make the time limits mean the same thing whatever else is running.
- [ ] **P2** **[med] (carried)** "Any target" best-discard on 10 or 11 cards when the best odds are below 1 (`search.py`)
  - See "What's still slow" in `docs/performance/best-discard.md`. It now ends in estimates after 30 s rather than never, but it's still the slowest common case.
- [ ] **P3** **[low-med] (guess)** Large pattern tuples are hashed again and again as cache keys
  - `_split_by_suit(patterns)` and `_interchangeable(patterns)` are `@cache`d on the tuple (`engine.py:277`, `291`), and `probability` calls the first every time. `_layout`'s key and `reuse.necessary`'s include it too (`engine.py:355`, `498`), and so does `_merged`'s (`engine.py:2013`).
  - A tuple's hash isn't stored, and `Requirement` is a dataclass, so each lookup hashes every requirement. With Four Fingers and Shortcut a flush-type target has thousands of patterns, and a search or a posterior calls `probability` hundreds of times.
  - Time it first. If it shows, key these by the tuple's identity (the tuples come from `_patterns`' cache, so they're already unique), or wrap them in an object with a stored hash.
- [ ] **P4** **[low-med] (guess)** `_evidence_key` builds and hashes `tuple(state.unseen)` on every call (`engine.py:1215`)
  - `usable` is called two or three times for each value (`_windows_for`, `posterior`, `evidence_unchecked`), and `_windows_for` builds its own key the same way. `Card`'s hash isn't stored either. This only runs with face-down cards in the sorts, which are the slow states anyway.
  - Work the key out once per state (a field set in `GameState._parse`, or a small cache by `id(state)`).
- [ ] **P5** **[low] (seen)** `GameState.rules` is worked out on every access (`state.py:366`)
  - It's read in `_allowed`, `_evidence_key`, `_rule_classes`, `_final_hands` and elsewhere. `functools.cached_property` doesn't work on a frozen dataclass with these fields as is, so set it in `_parse` or cache `Rules.from_jokers` by the jokers' tuple.
- [ ] **P6** **[low] (seen)** `_can_miss` tests each draw with a slow loop over every pattern (`engine.py:2076-2087`)
  - `_holds_test` (`engine.py:1918`) does the same much faster and is already cached by `_target_test`.
- [ ] **P7** **[low] (seen)** Caches that are never emptied count towards the memory limit
  - `hands._patterns`, `_bell_parts`, `_forced_patterns` (one entry per forced class), `engine._memberships`, `_forced_group`, `_split_by_suit`, `_interchangeable` and `posterior.orders` are plain `@cache`. Each set of tracked hands and each set of joker rules adds entries, and "Any target"'s can be large.
  - The watchdog reads the process's private bytes, so what these hold comes out of every calculation's allowance. Measure a long session with the targets changed often, then use `lru_cache` with a size where it matters.
- [ ] **P8** **[low] (guess)** Building patterns can't be cancelled or timed out (`hands.py:59-65`)
  - `Target.patterns` holds a lock while `_minimal` runs, and neither checks `cancel`. A value's quick try can overrun its 0.25 s, and a cancelled job keeps the lock from the next.
  - Time the worst build (Four Fingers with Shortcut, "Any target" over the flush-type hands). If it's well under 0.25 s, leave it.
- [ ] **P9** **[low] (carried)** `_relabelled` turns relabelling off whenever `state.bell` is set, even when `bell_matters(target, rules)` is false (`search.py:387`)
- [ ] **P10** **[low] (carried)** `engine._gc_paused` turns the collector off for the whole process while any search runs (`engine.py:251`)
- [ ] **P11** **[low] (carried)** The panel does avoidable work every frame (`panel.lua:199-220`, `odds.lua:192`)
  - `refresh_panel` builds the model and encodes it twice (`encode(model)`, `layout_key(model)`) before the early return, and `panel_model` calls `builder_for` for every card query.
- [ ] **P12** **[low] (carried)** Each changed snapshot is encoded twice, without and with its `id` (`files.lua:89-96`)
  - `sorted_cards` also encodes every unseen card for each snapshot, changed or not (`snapshot.lua:102-109`).
- [ ] **P13** **[low] (seen)** The game-started app formats console text nobody reads (`__main__.py:380`, `143-145`)
  - With `--game-pid`, stdout is the null device, but `summarize`, `format_report` and `format_search` still run for every state. It's cheap. It's also one more reason to decide what the console half is for.

## Architecture and cleanup

- [ ] **A1** **[med] (seen)** `run_watch` is one 215-line function with about 18 variables shared by its closures (`__main__.py:239-453`)
  - The odds job, the search job, resending and failure logging are each a small state machine. A class for the odds side and one for the search side would make each testable on its own, and the loop a dozen lines.
- [ ] **A2** **[med] (seen)** `engine.py` is 2276 lines doing four jobs
  - The counting search (`probability` to `_bound`, about 1000 lines), the discard model (`_discard`, `rules_after`, The Hook, Cerulean Bell, card queries), what the hand's sorts say (`usable` to `_split`), and the estimates (`_sampled` to `_holds_test`). They'd split along those lines with few imports between them.
- [ ] **A3** **[med] (seen)** The game's rules for a final hand are written twice (`engine.py:1832-1905` and `2019-2035`)
  - `_final_hands` "mirrors `_discard_odds` and what it calls": The Hook, The Mark, DNA, Cerulean Bell and Crimson Heart each have an exact version and a sampled one. A rule added to one and not the other gives estimates that disagree with the exact odds, without failing anything.
  - At least add a test that, for a small state under each boss and with DNA, `_final_hands`' share over many draws agrees with `_evaluate_discard` (T4).
- [ ] **A4** **[low] (seen)** Three functions answer "do these cards make one of these patterns" (`_holds` at `engine.py:141`, `holds` inside `_can_miss` at `2076`, `_holds_test` at `1918`)
  - Keep `_holds_test` and build the others on it.
- [ ] **A5** **[low] (seen)** The engine keeps state in module globals (`_usable`, `_deals`, `_windows`, `_ignored`, `_gc_searches`; `engine.py:246-248`, `1205-1212`, `1545`)
  - Tests share them between cases, and nothing ties them to a session. An object passed down (as `Reuse` is) would hold them.
- [ ] **A6** **[low] (seen)** Dead or unused
  - `cards._has_rank` (`cards.py:57`) is never called.
  - `GameState.played` and `discards_used` are exported, parsed and never read. Drop them from the contract (a schema bump), or note in `docs/architecture.md` what they're kept for.
  - `json.lua:93`: `list(open, close, item)` never uses `open`. (carried)
- [ ] **A7** **[low] (seen)** `report.build_report`'s closures call each other in a loop (`value` → `bounds` → `value`) and rebuild the whole report after each value (`report.py:205-254`)
  - It works, and the sizes are small. It's the hardest part of the file to follow; a small class holding `known` with `value()` and `bounds()` as methods would read better.
- [ ] **A8** **[low] (seen)** Lua and Python each have their own copy of a few rules
  - The rank labels (`builder.lua` `ranks_label`, `cards._ranks_label`), the panel's capacity (`tracked.lua`, `overlay.columns_needed`), the sort order (`observe.lua` `sort_key`, `posterior.sort_value`), The Mark's face cards (`hint.lua`, `engine.flipped_by_mark`) and the memory limit (`memory.lua`, `process.memory_limit`).
  - Some have a test that they agree (`test_builder_queries_parse_as_labelled`). Check each pair has one.
- [ ] **A9** **[low] (carried)** Decide whether to keep the terminal output at all

## Tests

- [x] **T1** **[med] (seen)** The test modules import from each other, and there's no `tests/conftest.py`
  - `test_state.py` holds the `lua` fixture, `MOCK_G`, `load_mod` and `new_lua`, which six other files import (with `F401` and `F811` silenced). `test_hint` imports from `test_overlay`, `test_observations` from `test_hint`, `test_posterior` from `test_engine` and `test_search`, `test_watch` from `test_report`, `test_settings` from three files.
  - Importing a test module to get a helper also makes its autouse fixtures apply (`no_estimates`). Move the fixtures to `conftest.py` and the helpers (`card`, `Game`, `FakeSource`, `TEXTS`, the mocked game) to plain modules beside `game_order.py`.
- [x] **T2** **[low] (carried)** `test_state.py`'s module-level `importorskip("lupa")` also skips its pure-Python tests of `GameState` and `FileStateSource`
  - T1's split fixes this too.
- [ ] **T3** **[med] (carried)** Seeded fuzz tests against brute force, as a `-m fuzz` tier
  - `random_card` only deals ranks 9 to 14, into decks of 8 to 12 cards (`test_engine.py:191-219`). Small hands, `select_limit` of 0 or 1, empty decks, and modifiers under The Mark or The Hook aren't reached.
- [ ] **T4** **[med] (seen)** A test that the sampled model agrees with the exact one
  - See A3. `test_posterior.py` checks `estimate_discard` on a few states; nothing goes through every boss and DNA with `_estimate_draws` against `_evaluate_discard`.
- [ ] **T5** **[low] (seen)** Cancelling is only tested with the event already set (`test_search.py:249`, `364`)
  - Add one that sets it partway through a heavy scenario and checks how long the job takes to end. (carried)
- [x] **T6** **[low] (carried)** `test_watch.py` depends on fake jobs finishing within `patience=0.05` s (`:105`, `:121`, `:186`)
  - Likely to flake on a busy CI runner. Raise it, or drive the loop with a fake clock.
  - Done: long patience except where a test needs it to run out, states queued one at a time, and partial payloads
    not counted as answers. The failure seen under load was another one: `run_watch` left its jobs running when it
    stopped, and one called the next test's patched `evaluate_discard`. It now cancels them, and an autouse fixture
    waits for each test's jobs.
- [x] **T7** **[low] (carried)** Lua tests still missing
  - The encoder's edge cases (NaN and infinity as `null`, control characters, `2^60`).
  - The decoder rejecting trailing commas, `null` and truncated input.
  - A search reply with bad indices (B1).
  - ~~An odds file of another schema (B2).~~ Done with B2.
  - Done: `test_encode_numbers` and `test_encode_control_characters` in `test_state.py`, `test_decode_rejects`
    in `test_overlay.py` (each rejection checked for its reason). B1's was done with B1.
- [ ] **T8** **[low] (carried)** The normal `main(--game-pid)` path: the log opened, stdout to the null device, the loop ending when the game exits
- [ ] **T9** **[low] (carried)** Perf scenarios to add: The Serpent (none today), and a single odds row under The Hook without the sorts
- [x] **T10** **[low] (seen)** `tests/perf/*.py` put their folder on `sys.path` by hand (`benchmark.py:50`, `test_expected.py:15`, `test_perf.py:20`)
  - A `pythonpath` entry in `pyproject.toml`'s pytest options would do it for the tests.

## Tooling, CI and releases

- [ ] **C1** **[med] (carried)** CI doesn't build the release until a tag is pushed
  - A broken `build_release.ps1` is found on release day. Run it on pull requests that touch `scripts/`, `mod/` or `calculatro/`.
- [x] **C2** **[low] (carried)** `install_mod.ps1:30` uses `-Encoding utf8NoBOM`, which Windows PowerShell 5.1 doesn't have
  - The mod is copied, then the script fails before `launch.txt` is written. Add `#Requires -Version 7`, or write the file as `build_release.ps1` does.
- [x] **C3** **[low] (carried)** `dump_lua = true` ships in releases (`lovely.toml:3`)
  - Lovely then writes its dumps of the patched game files on every player's launch. Strip it in `build_release.ps1`.
- [x] **C4** **[low] (carried)** The dev dependencies are unpinned, and Dependabot only watches the Actions
  - A new ruff rule, or a `lupa` release without a Windows `luajit21` wheel, breaks CI with no change in the repo. Add upper bounds, a `pip` entry in `dependabot.yml`, and `cache: pip` to the workflows.
  - `release.yml` runs the tests but not ruff.
- [x] **C5** **[low] (seen)** `calculatro/process.py` loads `kernel32` on import, so 7 test files can't be collected on Linux
  - `AGENTS.md` documents it as a limit for cloud agents. Loading the DLL on first use (or `sys.platform` checks around the definitions) would let the engine-independent ones run there: `test_log`, `test_state`'s parsing tests, most of `test_watch`.
- [x] **C6** **[low] (carried)** The suite takes about 3.5 minutes; try `pytest-xdist`
  - Done: `-n auto` in `addopts`. 300 s serial, 25 s on 32 cores, with no failures in repeated runs or at 64
    workers. `-m perf` runs serially (`tests/conftest.py`), and `-n0` skips the workers' few seconds of startup.

## Docs

- [x] **D1** **[med] (seen)** Fourteen references to `docs/plans/face-down-windows.md` and `docs/plans/progressive-odds.md`, which are deleted in the working tree
  - `engine.py:1203`, `1675`; `posterior.py:3`; `search.py:23`; `state.py:140`; `observe.lua:15`, `360`; `tests/game_order.py:3`; `tests/perf/scenarios.py:56`; `test_observations.py:4`; `test_posterior.py:2`, `915`; `test_search.py:404`; `docs/jokers.md:112`.
  - If the plans are done, point these at `docs/probability-model.md` (and check it has what the code comments lean on: the snapshot's shape for observations, the tie rules, the tests' reference).
- [x] **D2** **[med] (seen)** `docs/jokers.md:112` still calls several DNA copies of a face-down card a known limitation
  - The engine branches on what the card is now (`engine._dna_branch`), and `docs/probability-model.md:156` says so. The two pages disagree.
- [x] **D3** **[med] (carried)** The perf docs cite commits that no longer exist
  - `7d2af71`, `e239e4d`, `62eb51c`, `0c93930`, `80cb7d7`, `6b93aac` aren't in the history. They're in `docs/performance/README.md` ("Comparing with an older version"). The "Comparing with an older version" recipe fails as written.
- [x] **D4** **[med] (carried)** `docs/performance/benchmarks.md` was measured on 2026-10-07 to 10-11
  - Removed: the perf docs no longer keep tables of timings (current ones are in `tests/perf/baseline.json`).
- [x] **D5** **[med] (carried)** Broken anchor: `CONTRIBUTING.md:29` and `.github/ISSUE_TEMPLATE/config.yml:4` link `#installation`; the heading is "Installation (Users)", so `#installation-users`
- [ ] **D6** **[med] (carried)** The README's Usage section says everything twice (`README.md:61-95`)
  - A bullet list with screenshots, then prose that repeats it, with stray blank lines. Merge them.
- [ ] **D7** **[low] (guess)** Check the README covers what's been added since it was written
  - The settings page (the gear, face-down hints, the memory limit), approximate values (`*`), values given up on (`?`, "Too slow"), and what an estimated best discard means. The Usage section mentions none of them.
- [ ] **D8** **[low] (carried)** Add "Known limitations" to the README
  - Best discard looks one discard ahead. Windows only. Other mods (custom ranks, suits, enhancements, bosses) are untested. The slowest odds end as estimates.
- [x] **D9** **[low] (carried)** `docs/jokers.md:130`, `224`: Splash is tagged `rule`, but `rules.RULE_JOKERS` doesn't have it and its own section says it changes no hand
- [x] **D10** **[low] (seen)** Small ones
  - `search.lua:4`: "each target hand's search button"; card rows have one too. (carried)
  - `posterior.py:434`: `Deals` says it draws "exactly, not approximately", but `_table` turns the weights into floats for `bisect`. True to about 1e-16; say so or drop "exactly".
  - `report.py:174`: "Share it only between states with the same `__main__.odds_basis`" has the lower module naming the upper one. `odds_basis` could live in `report.py`.
  - `core.lua:8-9` and `worker.py:26`: the mod's patience (0.3 s) and the app's quick limit (0.25 s) depend on each other by a comment on one side only.
- [x] **D11** **[low] (carried)** The bug report template could also ask for `calculatro_odds.json`, to tell the app's bugs from the panel's

## Ideas

Not findings, and none of them new since the last review except the first two.

- [ ] **I1** Say when a best-discard option doesn't beat keeping the hand (B7)
- [ ] **I2** A budget for a whole report's exact pass, so 36 slow values don't mean 18 minutes of a busy core for odds the player has stopped looking at
- [ ] **I3** Look ahead across the remaining discards
- [ ] **I4** Show a best "discard" as a play when no discards are left, with its side effects (The Ox, The Arm, The Tooth, The Psychic)
- [ ] **I5** Presets for jokers' per-round targets (The Idol, Mail-In Rebate, Castle, Ancient Joker, To Do List)
- [ ] **I6** Show Keep for card queries when the hand has face-down cards (`overlay.py:155-162` drops it)
- [ ] **I7** Search options tied to cards, not positions, so sorting the hand keeps the "i/n" cycle
- [ ] **I8** Send the panel's visibility with the snapshot, so the app can idle while it's hidden
- [ ] **I9** A configurable hotkey, and controller support for the panel's buttons
- [ ] **I10** `calculatro odds FILE` to print one report and exit, for reproducing a bug report's state file; `--version`

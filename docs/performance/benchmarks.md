# Benchmarks

Time to compute one odds row: one target, for one keep-or-discard choice. The
app computes each row twice (keep hand, discard the selection), and once more
for "Any target" when there are several targets. Anything slower than
~0.25 s shows "Calculating odds..." first; see [README.md](README.md).

## Setup

- **Machine:** AMD Ryzen 7 7700X, 32 GB, Windows 11, Python 3.14.3.
  The Mark and Cerulean Bell were measured on 2026-10-09, the
  partial-state pass on 2026-10-08, and every earlier table on 2026-10-07;
  each section says at which point.
- **Method:** `tests/perf/benchmark.py --repeat 5 --budget 120`. Each
  scenario ran 5 times after an untimed warm-up run, and rows with a
  standard deviation over 25% of the mean were re-measured once
  (`--rerun-noisy`). Times are **mean ± standard deviation**. The raw times
  of [Current numbers](#current-numbers) are in `tests/perf/baseline.json`.
- **Before** (in "The second pass" and "All scenarios after the second
  pass"): the engine at commit `62eb51c`, after the Shortcut work,
  measured the same day side by side (`--repeat 5 --budget 60`, so the two slowest rows ran once). Both
  versions give exactly the same probabilities in every scenario.
- The machine's speed drifted by up to about 30% during the day, which is
  why the "before" column here differs a little from the "now" columns of
  the older tables below. Compare within a table.

Scenarios (`tests/perf/scenarios.py`):

- **Hand:** 7♥ 8♥ 9♠ Q♣ K♦ 3♠ 5♦ J♥, hand size 8, from a 52-card deck.
- **Decks:**
  - *standard*: a plain 52-card deck;
  - *wild*: the same deck with 4♣ 9♦ J♠ A♥ made Wild Cards. All four stay
    unseen, which is what makes it slow.
- **Unknown cards:** face-down cards kept, plus cards drawn after discarding.
  - *Keep 3, draw 5*: keep 7♥ 8♥ 9♠, discard the rest. The common case.
  - *6 unknown*: J♥ is face-down (The Wheel, The Fish); keep 7♥ 8♥ and
    discard 5.
  - *7 unknown*: 5♦ J♥ are face-down; keep 7♥ and discard 5.
  - *8 unknown, The House*: the whole hand is face-down, so nothing is
    known.
  - *The Mark*: Q♣ K♦ J♥ are face-down, known to be face cards. *6
    unknown* keeps 7♥ 8♥ and discards 3; *8 unknown* discards all 5 known
    cards.
  - *Cerulean Bell*: nothing face-down. *Keep 3, draw 5* keeps 7♥ 8♥ 9♠;
    *Keep 6, draw 2* keeps the first 6. The first card discarded is the
    forced one.
- **"Any":** several targets combined, as in the "Any target" row.
- **Nothing cached:** every cache is cleared before each run, so building
  the target's patterns is timed too, as when a target is first tracked or a
  joker changes the rules. Their situation keeps the search itself small.

## Current numbers

The best-discard search's own scenarios ("Best discard: ...") are in
[best-discard.md](best-discard.md). Adding them (2026-10-11) re-measured the
whole baseline; every other scenario gave the same result, within a few
percent of the times below.

### Cerulean Bell (2026-10-09)

The Cerulean Bell scenarios, added to `tests/perf/baseline.json` when it was
modelled (`--repeat 5 --budget 120`; the rest of the baseline wasn't
re-measured). Both discard cards from the hand, so the forced card is a
random one of the final hand and the odds average one search per kept card
and per group of unseen cards (see [engine.md](engine.md#the-model)).
*Keep 3, draw 5* keeps 7♥ 8♥ 9♠; *Keep 6, draw 2* keeps the first 6.

The 6 slowest rows; "Without" is the same row without the boss (*Keep 3,
draw 5* in the baseline):

| Situation | Target | Jokers | Cerulean Bell | Without |
|---|---|---|---|---|
| Keep 3, draw 5 | Full House + Straight Flush (any) | Four Fingers + Shortcut | 1.69 s ± 157 ms | - |
| Keep 3, draw 5 | Straight Flush | Four Fingers + Shortcut | 1.34 s ± 131 ms | 79 ms |
| Keep 3, draw 5 | Full House + Straight Flush (any) | Four Fingers | 624 ms ± 6 ms | - |
| Keep 3, draw 5 | Full House + Straight Flush (any) | Four Fingers + Shortcut + Smeared | 548 ms ± 9 ms | - |
| Keep 3, draw 5 | Straight Flush | Four Fingers + Shortcut + Smeared | 495 ms ± 9 ms | 40 ms |
| Keep 3, draw 5 | Straight Flush | Four Fingers | 381 ms ± 6 ms | 26 ms |

Every other Bell scenario takes under 0.3 s, and 77 of the 96 under 50 ms.

### The Mark (2026-10-09)

The Mark's scenarios, added to `tests/perf/baseline.json` when it was
modelled (`--repeat 5 --budget 120`; the rest of the baseline wasn't
re-measured). The search covers every number of face cards among the
unknown cards at once (see [engine.md](engine.md#the-model)). "Without"
is the same state without The Mark, so face-down cards are any unseen card,
timed once each in the same session. A separate search per number of face
cards took 2-2.5x "Without" in these rows.

The 8 slowest rows:

| Situation | Target | Jokers | The Mark | Without |
|---|---|---|---|---|
| 8 unknown | Full House + Straight Flush (any) | Four Fingers + Shortcut | 1.85 s ± 20 ms | 1.22 s |
| 8 unknown | Straight Flush | Four Fingers + Shortcut | 1.12 s ± 18 ms | 807 ms |
| 8 unknown | Full House + Straight Flush (any) | Four Fingers | 789 ms ± 9 ms | 467 ms |
| 8 unknown | Full House + Straight Flush (any) | Four Fingers + Shortcut + Smeared | 558 ms ± 8 ms | 436 ms |
| 8 unknown | Straight Flush | Four Fingers + Shortcut + Smeared | 422 ms ± 3 ms | 344 ms |
| 8 unknown | Straight Flush | Four Fingers | 354 ms ± 5 ms | 231 ms |
| 6 unknown | Full House + Straight Flush (any) | Four Fingers + Shortcut | 301 ms ± 5 ms | 312 ms |
| 6 unknown | Straight Flush | Four Fingers + Shortcut | 200 ms ± 9 ms | 199 ms |

Every other Mark scenario takes under 0.25 s, most under 15 ms. The
existing scenarios were unchanged within noise, with the same results.

### Partial-state reductions (2026-10-08)

The engine at commit `0c93930` (before) and the partial-state reductions
(after), measured side by side the same day, `--repeat 5 --budget 120`
then `--rerun-noisy` for each. Every probability is the same in both. These
runs are `tests/perf/baseline.json`.

The changes stay inside the existing search: one-card alternatives in a
plain OR merge into a single set of outs, deferred counts are dropped from
the memo key once the patterns that need them are out of reach, and
joining parts that are out of reach are filtered before they move. See
[engine.md](engine.md).

The 14 slowest rows before; the last column is the reduction in time:

| Deck | Situation | Target | Jokers | Before | After | Reduction |
|---|---|---|---|---|---|---|
| wild | 8 unknown | Full House + Straight Flush (any) | Four Fingers + Shortcut | 18.19 s | 13.71 s ± 70 ms | 25% |
| wild | 8 unknown | Straight Flush | Four Fingers + Shortcut | 13.69 s | 10.55 s ± 68 ms | 23% |
| wild | 8 unknown | Full House + Straight Flush (any) | Four Fingers | 3.36 s | 2.69 s ± 74 ms | 20% |
| wild | 8 unknown | Straight Flush | Four Fingers | 2.23 s | 1.78 s ± 25 ms | 20% |
| wild | 7 unknown | Straight Flush | Four Fingers + Shortcut | 2.19 s | 1.68 s ± 13 ms | 23% |
| wild | 8 unknown | Full House + Straight Flush (any) | Four Fingers + Shortcut + Smeared | 937 ms | 733 ms ± 26 ms | 22% |
| wild | 8 unknown | Straight Flush | Four Fingers + Shortcut + Smeared | 714 ms | 547 ms ± 4 ms | 23% |
| standard | 8 unknown | Full House + Straight Flush (any) | Four Fingers + Shortcut + Smeared | 680 ms | 541 ms ± 26 ms | 20% |
| wild | 8 unknown | Full House + Straight Flush (any) | Shortcut | 550 ms | 486 ms ± 4 ms | 12% |
| standard | 8 unknown | Straight Flush | Four Fingers + Shortcut + Smeared | 510 ms | 400 ms ± 3 ms | 21% |
| wild | 6 unknown | Straight Flush | Four Fingers + Shortcut | 475 ms | 354 ms ± 10 ms | 25% |
| standard | 8 unknown | Full House + Straight Flush (any) | Four Fingers + Shortcut | 414 ms | 369 ms ± 5 ms | 11% |
| wild | 7 unknown | Straight Flush | Four Fingers | 381 ms | 279 ms ± 1 ms | 27% |
| standard | 7 unknown | Straight Flush | Four Fingers + Shortcut | 305 ms | 309 ms ± 8 ms | -1% |

The searches that are still slow (unseen Wild Cards with Four Fingers) are
20–27% faster; the wild House with Four Fingers + Shortcut now takes 10.5 s
for Straight Flush and 13.7 s with Full House. The standard deck's rows
without Smeared, which split by suit, gain at most 15%, and the ones over
50 ms at 6 or 7 unknown cards nothing. The sum of all scenario
means fell from 49.5 s to 38.8 s (21%).

One small slowdown is consistent: Pair + Flush on the wild deck's House
with Four Fingers (with or without Shortcut) takes 48–49 ms instead of
46 ms, measured alternately three times with 10 runs each. Other rows that
looked slower in the sweep (up to 8%) were within 1% when re-measured the
same way.

| Deck | Situation | Target | No jokers | Four Fingers | Shortcut | Four Fingers + Shortcut | Smeared | Four Fingers + Smeared | Shortcut + Smeared | Four Fingers + Shortcut + Smeared |
|---|---|---|---|---|---|---|---|---|---|---|
| standard | Keep 3, draw 5 | Pair | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.5 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Two Pair | 1.7 ms ± 0.2 ms | 1.9 ms ± 0.3 ms | 1.8 ms ± 0.3 ms | 1.8 ms ± 0.3 ms | 1.9 ms ± 0.3 ms | 1.6 ms ± 0.2 ms | 1.9 ms ± 0.1 ms | 1.6 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Three of a Kind | 0.5 ms ± 0.1 ms | 0.5 ms ± 0.0 ms | 0.6 ms ± 0.1 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.6 ms ± 0.1 ms | 0.5 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Straight | 0.8 ms ± 0.1 ms | 0.8 ms ± 0.1 ms | 3.2 ms ± 0.4 ms | 1.9 ms ± 0.3 ms | 0.8 ms ± 0.1 ms | 0.8 ms ± 0.1 ms | 3.3 ms ± 0.3 ms | 2.4 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Flush | 1.3 ms ± 0.1 ms | 1.4 ms ± 0.3 ms | 1.4 ms ± 0.1 ms | 1.5 ms ± 0.2 ms | 0.2 ms ± 0.0 ms | 0.2 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.2 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Full House | 2.7 ms ± 0.6 ms | 2.6 ms ± 0.5 ms | 2.6 ms ± 0.4 ms | 2.7 ms ± 0.2 ms | 2.8 ms ± 0.1 ms | 2.7 ms ± 0.3 ms | 2.6 ms ± 0.3 ms | 2.9 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Four of a Kind | 0.4 ms ± 0.0 ms | 0.4 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.1 ms | 0.5 ms ± 0.0 ms | 0.4 ms ± 0.0 ms | 0.4 ms ± 0.0 ms | 0.4 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Straight Flush | 2.9 ms ± 0.2 ms | 26 ms ± 0.4 ms | 11 ms ± 0.2 ms | 79 ms ± 8.6 ms | 1.6 ms ± 0.1 ms | 13 ms ± 0.4 ms | 6.9 ms ± 0.2 ms | 40 ms ± 6.5 ms |
| standard | Keep 3, draw 5 | Royal Flush | 1.2 ms ± 0.2 ms | 4.3 ms ± 0.1 ms | 1.1 ms ± 0.1 ms | 6.8 ms ± 2.2 ms | 0.3 ms ± 0.0 ms | 2.1 ms ± 0.3 ms | 0.3 ms ± 0.0 ms | 2.8 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Five of a Kind | 0.3 ms ± 0.1 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Flush House | 5.8 ms ± 0.4 ms | 22 ms ± 4.9 ms | 5.5 ms ± 0.2 ms | 23 ms ± 4.3 ms | 2.2 ms ± 0.2 ms | 8.6 ms ± 0.2 ms | 2.3 ms ± 0.3 ms | 9.8 ms ± 2.3 ms |
| standard | Keep 3, draw 5 | Flush Five | 1.8 ms ± 0.2 ms | 2.5 ms ± 0.3 ms | 1.5 ms ± 0.1 ms | 3.0 ms ± 0.2 ms | 0.4 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.4 ms ± 0.0 ms | 0.5 ms ± 0.0 ms |
| standard | 8 unknown: The House (all face-down) | Pair | 0.7 ms ± 0.0 ms | 0.7 ms ± 0.0 ms | 0.7 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.7 ms ± 0.1 ms | 0.7 ms ± 0.0 ms | 0.6 ms ± 0.0 ms | 0.6 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair | 2.1 ms ± 0.3 ms | 2.1 ms ± 0.4 ms | 2.0 ms ± 0.3 ms | 2.1 ms ± 0.2 ms | 2.1 ms ± 0.2 ms | 2.2 ms ± 0.1 ms | 2.2 ms ± 0.3 ms | 2.0 ms ± 0.3 ms |
| standard | 8 unknown: The House (all face-down) | Three of a Kind | 0.7 ms ± 0.0 ms | 0.7 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.7 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.8 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.7 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight | 1.8 ms ± 0.1 ms | 1.7 ms ± 0.2 ms | 6.5 ms ± 0.3 ms | 5.4 ms ± 1.0 ms | 1.8 ms ± 0.1 ms | 1.8 ms ± 0.2 ms | 7.1 ms ± 0.2 ms | 5.3 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Flush | 1.1 ms ± 0.1 ms | 1.9 ms ± 0.2 ms | 1.1 ms ± 0.1 ms | 1.7 ms ± 0.4 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms |
| standard | 8 unknown: The House (all face-down) | Full House | 3.1 ms ± 0.3 ms | 3.3 ms ± 0.4 ms | 3.3 ms ± 0.4 ms | 3.4 ms ± 0.4 ms | 3.7 ms ± 0.3 ms | 3.5 ms ± 0.2 ms | 3.4 ms ± 0.3 ms | 3.4 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Four of a Kind | 0.7 ms ± 0.1 ms | 0.6 ms ± 0.0 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.0 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.7 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight Flush | 2.1 ms ± 0.3 ms | 72 ms ± 3.7 ms | 6.7 ms ± 0.4 ms | 250 ms ± 6.6 ms | 3.0 ms ± 0.2 ms | 83 ms ± 0.8 ms | 11 ms ± 0.4 ms | 400 ms ± 3.2 ms |
| standard | 8 unknown: The House (all face-down) | Royal Flush | 1.1 ms ± 0.1 ms | 5.3 ms ± 0.2 ms | 1.1 ms ± 0.1 ms | 6.0 ms ± 0.2 ms | 0.5 ms ± 0.1 ms | 4.8 ms ± 0.3 ms | 0.5 ms ± 0.1 ms | 5.6 ms ± 0.5 ms |
| standard | 8 unknown: The House (all face-down) | Five of a Kind | 0.3 ms ± 0.1 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms |
| standard | 8 unknown: The House (all face-down) | Flush House | 3.2 ms ± 0.3 ms | 14 ms ± 1.4 ms | 3.1 ms ± 0.2 ms | 14 ms ± 0.2 ms | 2.2 ms ± 0.3 ms | 14 ms ± 2.0 ms | 1.9 ms ± 0.3 ms | 13 ms ± 0.2 ms |
| standard | 8 unknown: The House (all face-down) | Flush Five | 1.3 ms ± 0.2 ms | 3.2 ms ± 0.8 ms | 1.2 ms ± 0.1 ms | 2.8 ms ± 0.1 ms | 0.4 ms ± 0.0 ms | 0.5 ms ± 0.1 ms | 0.4 ms ± 0.0 ms | 0.5 ms ± 0.1 ms |
| standard | 6 unknown: 1 face-down, draw 5 | Straight Flush | 2.7 ms ± 0.4 ms | 62 ms ± 1.7 ms | 11 ms ± 0.4 ms | 198 ms ± 18 ms | 1.8 ms ± 0.3 ms | 21 ms ± 0.1 ms | 8.0 ms ± 1.1 ms | 68 ms ± 0.8 ms |
| standard | 7 unknown: 2 face-down, draw 5 | Straight Flush | 3.1 ms ± 0.3 ms | 89 ms ± 8.9 ms | 12 ms ± 0.3 ms | 309 ms ± 7.5 ms | 1.9 ms ± 0.4 ms | 38 ms ± 1.5 ms | 9.2 ms ± 0.6 ms | 159 ms ± 1.9 ms |
| standard | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 13 ms ± 1.1 ms | 148 ms ± 6.7 ms | 26 ms ± 2.9 ms | 369 ms ± 5.3 ms | 17 ms ± 0.3 ms | 139 ms ± 7.3 ms | 54 ms ± 2.6 ms | 541 ms ± 26 ms |
| standard | 8 unknown: The House (all face-down) | Flush + Straight Flush (any) | 1.1 ms ± 0.2 ms | 1.8 ms ± 0.3 ms | 1.2 ms ± 0.2 ms | 1.6 ms ± 0.2 ms | 0.3 ms ± 0.0 ms | 0.2 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair + Flush (any) | 8.5 ms ± 0.7 ms | 22 ms ± 0.4 ms | 8.3 ms ± 0.6 ms | 22 ms ± 0.5 ms | 9.6 ms ± 0.7 ms | 8.0 ms ± 0.3 ms | 9.0 ms ± 0.2 ms | 8.1 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight + Flush (any) | 10 ms ± 0.3 ms | 32 ms ± 0.5 ms | 38 ms ± 6.3 ms | 70 ms ± 3.3 ms | 11 ms ± 0.1 ms | 9.1 ms ± 1.7 ms | 32 ms ± 0.6 ms | 19 ms ± 0.2 ms |
| wild | Keep 3, draw 5 | Straight Flush | 3.9 ms ± 0.4 ms | 41 ms ± 0.5 ms | 25 ms ± 3.2 ms | 135 ms ± 18 ms | 2.9 ms ± 0.2 ms | 16 ms ± 2.6 ms | 13 ms ± 0.1 ms | 48 ms ± 11 ms |
| wild | 6 unknown: 1 face-down, draw 5 | Straight Flush | 6.0 ms ± 0.3 ms | 101 ms ± 8.2 ms | 37 ms ± 1.0 ms | 354 ms ± 9.8 ms | 4.4 ms ± 0.2 ms | 27 ms ± 0.3 ms | 21 ms ± 0.4 ms | 89 ms ± 4.7 ms |
| wild | 7 unknown: 2 face-down, draw 5 | Straight Flush | 7.8 ms ± 0.7 ms | 279 ms ± 1.5 ms | 62 ms ± 0.9 ms | 1.68 s ± 13 ms | 6.7 ms ± 0.5 ms | 48 ms ± 0.3 ms | 40 ms ± 0.3 ms | 216 ms ± 2.1 ms |
| wild | 8 unknown: The House (all face-down) | Straight Flush | 20 ms ± 0.3 ms | 1.78 s ± 25 ms | 170 ms ± 6.7 ms | 10.55 s ± 68 ms | 11 ms ± 1.6 ms | 120 ms ± 1.9 ms | 69 ms ± 1.0 ms | 547 ms ± 3.5 ms |
| wild | 8 unknown: The House (all face-down) | Flush House | 3.9 ms ± 0.4 ms | 18 ms ± 0.5 ms | 4.3 ms ± 0.3 ms | 21 ms ± 3.5 ms | 8.9 ms ± 0.7 ms | 21 ms ± 0.5 ms | 8.1 ms ± 0.2 ms | 22 ms ± 0.5 ms |
| wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 80 ms ± 3.1 ms | 2.69 s ± 74 ms | 486 ms ± 3.6 ms | 13.71 s ± 70 ms | 21 ms ± 0.5 ms | 188 ms ± 1.3 ms | 72 ms ± 0.7 ms | 733 ms ± 26 ms |
| wild | 8 unknown: The House (all face-down) | Pair + Flush (any) | 43 ms ± 0.9 ms | 53 ms ± 7.3 ms | 43 ms ± 1.5 ms | 48 ms ± 1.0 ms | 6.7 ms ± 0.9 ms | 4.7 ms ± 0.3 ms | 5.8 ms ± 0.4 ms | 4.5 ms ± 0.4 ms |

## After Smeared Joker (2026-10-07)

Measured at commit `80cb7d7` (after the Smeared Joker and Pareidolia work).
These runs were the baseline before the partial-state pass. Every probability
is the same as in the previous baseline. Pareidolia has no scenarios: it doesn't change any
hand's patterns.

The slowest rows all have unseen Wild Cards and Four Fingers:

- Four Fingers + Shortcut on the wild deck: Straight Flush takes 487 ms at 6
  unknown cards, 2.33 s at 7 and 13.7 s at 8 (The House); 17.4 s with Full
  House as well.
- Four Fingers alone on the wild deck: 138 ms, 376 ms and 2.19 s; 3.30 s
  with Full House as well.
- Adding Smeared Joker to either makes them faster: at most 924 ms (Four
  Fingers + Shortcut + Smeared, wild deck, The House, Full House + Straight
  Flush).
- Shortcut alone has two rows over 100 ms, both on the wild deck's The
  House: Straight Flush (182 ms) and Full House + Straight Flush (572 ms).
  Smeared alone, Shortcut + Smeared and no jokers stay under 100 ms
  everywhere.

**The standard deck's House rows are a best case** (see "Splitting by
suit" in [engine.md](engine.md)): with a full deck and nothing known, the
suits are interchangeable, and terms that are equal up to relabelling suits
are computed once. Measured side by side with that check turned off
(`engine._interchangeable` patched to return False, 3–5 runs each), as
happens once the suits differ:

| Target | Jokers | Normal | Suits not interchangeable |
|---|---|---|---|
| Straight Flush | Four Fingers | 72 ms | 401 ms |
| Straight Flush | Four Fingers + Shortcut | 270 ms | 1.34 s |
| Full House + Straight Flush (any) | Four Fingers | 150 ms | 789 ms |
| Full House + Straight Flush (any) | Four Fingers + Shortcut | 383 ms | 2.08 s |

With Smeared Joker the check makes no difference, since two colours aren't
split by suit.

Positions visited (counted through the search's `cancel.is_set()` call,
made once per new position) and time per position, for the rows over
100 ms: Four Fingers alone costs 6–10 µs per position, Four Fingers +
Shortcut 11–15 µs, and Four Fingers + Shortcut + Smeared 19–23 µs. The
largest search, the wild deck's The House with Four Fingers + Shortcut
(Full House + Straight Flush), visits 1.17 million positions.

| Deck | Situation | Target | No jokers | Four Fingers | Shortcut | Four Fingers + Shortcut | Smeared | Four Fingers + Smeared | Shortcut + Smeared | Four Fingers + Shortcut + Smeared |
|---|---|---|---|---|---|---|---|---|---|---|
| standard | Keep 3, draw 5 | Pair | 0.5 ms ± 0.1 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.1 ms | 0.5 ms ± 0.1 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.1 ms | 0.5 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Two Pair | 1.7 ms ± 0.1 ms | 1.7 ms ± 0.2 ms | 1.8 ms ± 0.2 ms | 1.5 ms ± 0.2 ms | 1.8 ms ± 0.2 ms | 1.8 ms ± 0.4 ms | 1.6 ms ± 0.2 ms | 1.5 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Three of a Kind | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.1 ms | 0.5 ms ± 0.1 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.4 ms ± 0.1 ms | 0.5 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Straight | 0.7 ms ± 0.1 ms | 0.7 ms ± 0.1 ms | 3.3 ms ± 0.4 ms | 2.0 ms ± 0.3 ms | 0.8 ms ± 0.0 ms | 0.8 ms ± 0.1 ms | 3.4 ms ± 0.5 ms | 2.2 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Flush | 1.3 ms ± 0.1 ms | 1.2 ms ± 0.2 ms | 1.6 ms ± 0.9 ms | 1.4 ms ± 0.2 ms | 0.2 ms ± 0.0 ms | 0.2 ms ± 0.0 ms | 0.2 ms ± 0.0 ms | 0.2 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Full House | 2.5 ms ± 0.6 ms | 2.8 ms ± 0.1 ms | 2.7 ms ± 0.3 ms | 2.6 ms ± 0.3 ms | 2.7 ms ± 0.2 ms | 3.1 ms ± 0.1 ms | 3.0 ms ± 0.3 ms | 2.7 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Four of a Kind | 0.4 ms ± 0.0 ms | 0.5 ms ± 0.1 ms | 0.4 ms ± 0.0 ms | 0.4 ms ± 0.1 ms | 0.4 ms ± 0.0 ms | 0.4 ms ± 0.0 ms | 0.4 ms ± 0.0 ms | 0.4 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Straight Flush | 2.6 ms ± 0.2 ms | 25 ms ± 1.3 ms | 12 ms ± 1.1 ms | 73 ms ± 1.9 ms | 1.3 ms ± 0.2 ms | 13 ms ± 0.2 ms | 6.4 ms ± 0.4 ms | 41 ms ± 0.8 ms |
| standard | Keep 3, draw 5 | Royal Flush | 1.3 ms ± 0.3 ms | 4.4 ms ± 0.8 ms | 1.3 ms ± 0.2 ms | 5.9 ms ± 0.4 ms | 0.3 ms ± 0.0 ms | 1.9 ms ± 0.3 ms | 0.3 ms ± 0.0 ms | 2.8 ms ± 0.5 ms |
| standard | Keep 3, draw 5 | Five of a Kind | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Flush House | 5.3 ms ± 0.3 ms | 19 ms ± 0.5 ms | 6.2 ms ± 0.4 ms | 21 ms ± 1.0 ms | 1.9 ms ± 0.2 ms | 8.2 ms ± 0.4 ms | 2.1 ms ± 0.2 ms | 8.4 ms ± 1.0 ms |
| standard | Keep 3, draw 5 | Flush Five | 1.5 ms ± 0.2 ms | 2.5 ms ± 0.5 ms | 1.5 ms ± 0.2 ms | 2.8 ms ± 0.3 ms | 0.4 ms ± 0.1 ms | 0.5 ms ± 0.1 ms | 0.4 ms ± 0.1 ms | 0.5 ms ± 0.0 ms |
| standard | 8 unknown: The House (all face-down) | Pair | 0.6 ms ± 0.0 ms | 0.5 ms ± 0.0 ms | 0.5 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.5 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair | 1.7 ms ± 0.1 ms | 2.0 ms ± 0.3 ms | 1.9 ms ± 0.4 ms | 1.9 ms ± 0.2 ms | 2.1 ms ± 0.2 ms | 1.8 ms ± 0.2 ms | 2.0 ms ± 0.1 ms | 1.9 ms ± 0.2 ms |
| standard | 8 unknown: The House (all face-down) | Three of a Kind | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.5 ms ± 0.0 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.7 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight | 1.6 ms ± 0.3 ms | 1.7 ms ± 0.2 ms | 7.4 ms ± 0.1 ms | 5.0 ms ± 0.3 ms | 1.4 ms ± 0.1 ms | 1.6 ms ± 0.1 ms | 7.3 ms ± 0.4 ms | 5.0 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Flush | 1.0 ms ± 0.1 ms | 1.8 ms ± 0.2 ms | 1.2 ms ± 0.2 ms | 1.8 ms ± 0.3 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Full House | 3.2 ms ± 0.4 ms | 3.3 ms ± 0.4 ms | 3.6 ms ± 0.1 ms | 3.6 ms ± 0.5 ms | 3.5 ms ± 0.1 ms | 3.2 ms ± 0.2 ms | 3.3 ms ± 0.3 ms | 3.7 ms ± 0.2 ms |
| standard | 8 unknown: The House (all face-down) | Four of a Kind | 0.6 ms ± 0.0 ms | 0.7 ms ± 0.1 ms | 0.5 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.6 ms ± 0.1 ms | 0.5 ms ± 0.1 ms | 0.6 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight Flush | 2.0 ms ± 0.3 ms | 76 ms ± 5.5 ms | 6.8 ms ± 0.4 ms | 260 ms ± 13 ms | 2.7 ms ± 0.3 ms | 97 ms ± 0.9 ms | 11 ms ± 0.2 ms | 503 ms ± 9.6 ms |
| standard | 8 unknown: The House (all face-down) | Royal Flush | 1.1 ms ± 0.1 ms | 4.9 ms ± 0.3 ms | 1.1 ms ± 0.1 ms | 6.2 ms ± 0.2 ms | 0.4 ms ± 0.0 ms | 4.5 ms ± 0.6 ms | 0.4 ms ± 0.0 ms | 5.0 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Five of a Kind | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms | 0.3 ms ± 0.0 ms | 0.4 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Flush House | 3.0 ms ± 0.4 ms | 14 ms ± 2.4 ms | 3.6 ms ± 0.4 ms | 15 ms ± 2.0 ms | 2.0 ms ± 0.3 ms | 13 ms ± 0.4 ms | 2.3 ms ± 0.2 ms | 14 ms ± 2.1 ms |
| standard | 8 unknown: The House (all face-down) | Flush Five | 1.2 ms ± 0.1 ms | 2.5 ms ± 0.2 ms | 1.3 ms ± 0.1 ms | 3.1 ms ± 0.8 ms | 0.4 ms ± 0.0 ms | 0.5 ms ± 0.1 ms | 0.4 ms ± 0.0 ms | 0.5 ms ± 0.1 ms |
| standard | 6 unknown: 1 face-down, draw 5 | Straight Flush | 2.6 ms ± 0.3 ms | 61 ms ± 2.1 ms | 12 ms ± 1.1 ms | 186 ms ± 10 ms | 1.6 ms ± 0.2 ms | 23 ms ± 0.3 ms | 7.4 ms ± 0.1 ms | 82 ms ± 1.9 ms |
| standard | 7 unknown: 2 face-down, draw 5 | Straight Flush | 2.6 ms ± 0.2 ms | 87 ms ± 2.8 ms | 14 ms ± 3.0 ms | 305 ms ± 33 ms | 2.1 ms ± 0.1 ms | 43 ms ± 0.5 ms | 9.0 ms ± 0.4 ms | 198 ms ± 1.9 ms |
| standard | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 15 ms ± 1.6 ms | 164 ms ± 8.9 ms | 27 ms ± 2.5 ms | 420 ms ± 21 ms | 17 ms ± 0.2 ms | 164 ms ± 1.6 ms | 60 ms ± 0.4 ms | 670 ms ± 3.8 ms |
| standard | 8 unknown: The House (all face-down) | Flush + Straight Flush (any) | 1.1 ms ± 0.2 ms | 1.9 ms ± 0.3 ms | 1.1 ms ± 0.1 ms | 1.8 ms ± 0.2 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms | 0.3 ms ± 0.0 ms | 0.3 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair + Flush (any) | 8.3 ms ± 0.6 ms | 23 ms ± 3.5 ms | 8.6 ms ± 0.4 ms | 24 ms ± 3.3 ms | 8.8 ms ± 0.3 ms | 7.4 ms ± 0.4 ms | 9.2 ms ± 0.3 ms | 7.5 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Straight + Flush (any) | 10 ms ± 0.5 ms | 33 ms ± 1.1 ms | 37 ms ± 1.2 ms | 71 ms ± 3.5 ms | 9.5 ms ± 0.2 ms | 8.1 ms ± 0.3 ms | 33 ms ± 0.5 ms | 20 ms ± 0.6 ms |
| wild | Keep 3, draw 5 | Straight Flush | 3.5 ms ± 0.4 ms | 58 ms ± 1.9 ms | 23 ms ± 0.6 ms | 174 ms ± 4.1 ms | 2.1 ms ± 0.1 ms | 16 ms ± 0.2 ms | 13 ms ± 0.1 ms | 50 ms ± 0.9 ms |
| wild | 6 unknown: 1 face-down, draw 5 | Straight Flush | 5.9 ms ± 0.7 ms | 138 ms ± 1.0 ms | 39 ms ± 1.1 ms | 487 ms ± 1.4 ms | 4.2 ms ± 0.2 ms | 34 ms ± 8.4 ms | 22 ms ± 3.0 ms | 105 ms ± 3.2 ms |
| wild | 7 unknown: 2 face-down, draw 5 | Straight Flush | 7.5 ms ± 0.2 ms | 376 ms ± 3.7 ms | 63 ms ± 1.0 ms | 2.33 s ± 47 ms | 6.5 ms ± 0.3 ms | 57 ms ± 0.4 ms | 41 ms ± 2.7 ms | 274 ms ± 1.5 ms |
| wild | 8 unknown: The House (all face-down) | Straight Flush | 21 ms ± 0.3 ms | 2.19 s ± 14 ms | 182 ms ± 10 ms | 13.57 s ± 26 ms | 9.9 ms ± 0.5 ms | 141 ms ± 0.8 ms | 68 ms ± 0.5 ms | 705 ms ± 8.7 ms |
| wild | 8 unknown: The House (all face-down) | Flush House | 4.0 ms ± 0.1 ms | 17 ms ± 0.5 ms | 4.2 ms ± 0.4 ms | 19 ms ± 1.4 ms | 8.5 ms ± 0.4 ms | 23 ms ± 3.8 ms | 8.7 ms ± 0.2 ms | 22 ms ± 0.5 ms |
| wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 83 ms ± 0.9 ms | 3.30 s ± 8.8 ms | 572 ms ± 5.8 ms | 17.85 s ± 65 ms | 22 ms ± 1.5 ms | 237 ms ± 2.0 ms | 80 ms ± 0.5 ms | 942 ms ± 16 ms |
| wild | 8 unknown: The House (all face-down) | Pair + Flush (any) | 42 ms ± 0.7 ms | 45 ms ± 1.5 ms | 44 ms ± 0.5 ms | 44 ms ± 0.4 ms | 5.7 ms ± 0.2 ms | 4.5 ms ± 0.6 ms | 5.4 ms ± 0.3 ms | 4.3 ms ± 0.1 ms |

## Smeared Joker

Measured 2026-10-07 on the same machine with `--repeat 3 --budget 20`; these
runs were the baseline until [Current numbers](#current-numbers). "Before" is the first
Smeared implementation (one run each) for the Smeared columns, and the
previous baseline for the others. Every probability is unchanged. Rows where
some column took 100 ms or more; see [jokers/smeared.md](jokers/smeared.md)
for the changes.

| Deck | Situation | Target | Four Fingers | Four Fingers + Shortcut | Four Fingers + Smeared | Four Fingers + Shortcut + Smeared |
|---|---|---|---|---|---|---|
| standard | 6 unknown | Straight Flush | 68 ms → 58 ms | 234 ms → 187 ms | 59 ms → 23 ms | 232 ms → 94 ms |
| standard | 7 unknown | Straight Flush | 93 ms → 88 ms | 354 ms → 278 ms | 110 ms → 43 ms | 522 ms → 200 ms |
| standard | 8 unknown (The House) | Straight Flush | 85 ms → 75 ms | 315 ms → 254 ms | 240 ms → 96 ms | 1.26 s → 500 ms |
| standard | 8 unknown (The House) | Full House + Straight Flush (any) | 189 ms → 156 ms | 520 ms → 418 ms | 464 ms → 164 ms | 1.92 s → 680 ms |
| wild | Keep 3, draw 5 | Straight Flush | 75 ms → 56 ms | 250 ms → 171 ms | — → 55 ms | 73 ms → 59 ms |
| wild | 6 unknown | Straight Flush | 180 ms → 139 ms | 714 ms → 468 ms | — → 31 ms | 153 ms → 106 ms |
| wild | 7 unknown | Straight Flush | 468 ms → 363 ms | 3.20 s → 2.20 s | 81 ms → 57 ms | 419 ms → 283 ms |
| wild | 8 unknown (The House) | Straight Flush | 2.85 s → 2.28 s | 19.25 s → 13.80 s | 196 ms → 141 ms | 1.13 s → 694 ms |
| wild | 8 unknown (The House) | Full House + Straight Flush (any) | 4.42 s → 3.39 s | 25.48 s → 18.00 s | 335 ms → 231 ms | 1.43 s → 929 ms |

Smeared alone and Shortcut + Smeared stay under 100 ms everywhere; Shortcut
alone has two rows over 100 ms on the wild deck's The House (Straight Flush
203 → 161 ms, any of Full House / Straight Flush 772 → 550 ms).

## The second pass

Every scenario that took over 250 ms before. See [engine.md](engine.md) for
what changed.

| Situation | Jokers | Before | Now | |
|---|---|---|---|---|
| Straight Flush, 6 unknown | Four Fingers + Shortcut | 506 ms | 234 ms ± 18 ms | 2.2× |
| Straight Flush, 7 unknown | Four Fingers + Shortcut | 941 ms | 354 ms ± 35 ms | 2.7× |
| Straight Flush, 8 unknown (The House) | Four Fingers | 978 ms | 85 ms ± 4 ms | 11.5× |
| Straight Flush, 8 unknown | Four Fingers + Shortcut | 6.82 s | 315 ms ± 10 ms | 21.6× |
| Any of Full House / Straight Flush, 8 unknown | Four Fingers | 1.75 s | 189 ms ± 8 ms | 9.2× |
| Any of Full House / Straight Flush, 8 unknown | Four Fingers + Shortcut | 6.22 s | 520 ms ± 19 ms | 12.0× |
| Any of Straight / Flush, 8 unknown | Four Fingers | 268 ms | 32 ms ± 2 ms | 8.3× |
| Any of Straight / Flush, 8 unknown | Shortcut | 370 ms | 42 ms ± 2 ms | 8.8× |
| Any of Straight / Flush, 8 unknown | Four Fingers + Shortcut | 893 ms | 74 ms ± 2 ms | 12.1× |
| *Wild deck:* Straight Flush, keep 3, draw 5 | Four Fingers + Shortcut | 980 ms | 250 ms ± 2 ms | 3.9× |
| *Wild deck:* Straight Flush, 6 unknown | Four Fingers | 562 ms | 180 ms ± 3 ms | 3.1× |
| *Wild deck:* Straight Flush, 6 unknown | Four Fingers + Shortcut | 3.42 s | 714 ms ± 1 ms | 4.8× |
| *Wild deck:* Straight Flush, 7 unknown | Four Fingers | 1.54 s | 468 ms ± 11 ms | 3.3× |
| *Wild deck:* Straight Flush, 7 unknown | Four Fingers + Shortcut | 13.1 s | 3.20 s ± 0.04 s | 4.1× |
| *Wild deck:* Straight Flush, 8 unknown | Shortcut | 324 ms | 203 ms ± 9 ms | 1.6× |
| *Wild deck:* Straight Flush, 8 unknown | Four Fingers | 9.25 s | 2.85 s ± 0.02 s | 3.2× |
| *Wild deck:* Straight Flush, 8 unknown | Four Fingers + Shortcut | 81.4 s | 19.2 s ± 0.1 s | 4.2× |
| *Wild deck:* any of Full House / Straight Flush, 8 unknown | Shortcut | 1.54 s | 772 ms ± 6 ms | 2.0× |
| *Wild deck:* any of Full House / Straight Flush, 8 unknown | Four Fingers | 12.6 s | 4.42 s ± 0.03 s | 2.9× |
| *Wild deck:* any of Full House / Straight Flush, 8 unknown | Four Fingers + Shortcut | 93.7 s | 25.5 s ± 0.1 s | 3.7× |

**The standard deck's House rows are a best case.** With a full deck and
nothing known, all four suits are interchangeable, so most of the split by
suit's terms are computed once (see "Splitting by suit" in
[engine.md](engine.md)). Once the suits differ, that doesn't apply. Without
it, the same rows take 1.76 s (Straight Flush, Four Fingers + Shortcut),
2.72 s (any of Full House / Straight Flush, Four Fingers + Shortcut) and
447 ms (Straight Flush, Four Fingers): 2–4× faster than before rather than
10–20×.

Step by step, during development (single runs; the machine's speed varied,
so only the ratios between neighbouring rows mean much):

| Change | Standard deck, Straight Flush, 8 unknown, Four Fingers + Shortcut | Wild deck, Straight Flush, 7 unknown, Four Fingers + Shortcut |
|---|---|---|
| Before | 7.2 s | 13.2 s |
| Bound on cards needed worked out over the classes still ahead | 7.1 s | 10.4 s |
| Parts implied by a one-card part dropped | 6.1 s | 10.3 s |
| Classes sorted by card, rank by rank | 3.4 s | 8.0 s |
| Interned parts, cached transitions | 1.75 s | 3.9 s |
| Only one-card parts (not Full House's "2 more 7s"); suit by suit when suits are independent | 1.49 s | 2.5 s |
| Equal terms of interchangeable suits computed once | 0.30 s | (not split) |

**Still slow after the second pass: unseen Wild Cards with Four Fingers
and Shortcut.** The suits can't be split, so the wild deck took 3.2 s at 7
unknown cards and about 20 s for The House (25 s with Full House as well).
With Four Fingers alone, The House took about 3–4.5 s. See
[Current numbers](#current-numbers) for today's times.

## All scenarios after the second pass

Before (commit `62eb51c`) → after the second pass (mean ± standard
deviation); see "Setup". For today's times see
[Current numbers](#current-numbers).

| Deck | Situation | Target | No jokers | Four Fingers | Shortcut | Four Fingers + Shortcut |
|---|---|---|---|---|---|---|
| standard | Keep 3, draw 5 | Pair | 0.6 ms → 0.5 ms ± 0.0 ms | 0.6 ms → 0.5 ms ± 0.1 ms | 0.6 ms → 0.6 ms ± 0.1 ms | 0.5 ms → 0.5 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Two Pair | 2.3 ms → 1.9 ms ± 0.2 ms | 2.4 ms → 1.9 ms ± 0.2 ms | 2.5 ms → 1.9 ms ± 0.3 ms | 2.3 ms → 1.9 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Three of a Kind | 0.6 ms → 0.4 ms ± 0.0 ms | 0.5 ms → 0.5 ms ± 0.1 ms | 0.5 ms → 0.5 ms ± 0.1 ms | 0.6 ms → 0.5 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Straight | 1.2 ms → 0.8 ms ± 0.1 ms | 1.2 ms → 0.7 ms ± 0.1 ms | 6.8 ms → 3.4 ms ± 0.4 ms | 4.2 ms → 2.2 ms ± 0.3 ms |
| standard | Keep 3, draw 5 | Flush | 1.2 ms → 1.3 ms ± 0.0 ms | 1.2 ms → 1.3 ms ± 0.1 ms | 1.1 ms → 1.3 ms ± 0.1 ms | 1.6 ms → 1.2 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Full House | 4.1 ms → 3.1 ms ± 0.2 ms | 3.8 ms → 3.3 ms ± 0.0 ms | 4.0 ms → 3.0 ms ± 0.5 ms | 3.9 ms → 3.3 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Four of a Kind | 0.5 ms → 0.4 ms ± 0.1 ms | 0.4 ms → 0.5 ms ± 0.1 ms | 0.4 ms → 0.4 ms ± 0.0 ms | 0.5 ms → 0.4 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Straight Flush | 2.9 ms → 2.5 ms ± 0.3 ms | 44 ms → 27 ms ± 1.8 ms | 12 ms → 11 ms ± 0.4 ms | 156 ms → 81 ms ± 1.2 ms |
| standard | Keep 3, draw 5 | Royal Flush | 1.0 ms → 1.3 ms ± 0.1 ms | 3.9 ms → 4.8 ms ± 0.7 ms | 1.0 ms → 1.3 ms ± 0.1 ms | 6.6 ms → 5.6 ms ± 0.3 ms |
| standard | Keep 3, draw 5 | Five of a Kind | 0.3 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.3 ms ± 0.1 ms | 0.3 ms → 0.3 ms ± 0.1 ms | 0.3 ms → 0.3 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Flush House | 5.5 ms → 5.7 ms ± 0.3 ms | 21 ms → 19 ms ± 0.5 ms | 5.2 ms → 5.5 ms ± 0.3 ms | 25 ms → 21 ms ± 0.8 ms |
| standard | Keep 3, draw 5 | Flush Five | 1.5 ms → 2.0 ms ± 0.3 ms | 3.1 ms → 2.5 ms ± 0.2 ms | 2.4 ms → 2.0 ms ± 0.9 ms | 2.3 ms → 2.6 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Pair | 0.6 ms → 0.6 ms ± 0.1 ms | 0.8 ms → 0.6 ms ± 0.1 ms | 0.7 ms → 0.6 ms ± 0.1 ms | 0.7 ms → 0.6 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair | 3.7 ms → 2.1 ms ± 0.3 ms | 3.3 ms → 2.1 ms ± 0.3 ms | 3.4 ms → 2.1 ms ± 0.2 ms | 3.3 ms → 2.2 ms ± 0.2 ms |
| standard | 8 unknown: The House (all face-down) | Three of a Kind | 0.7 ms → 0.6 ms ± 0.1 ms | 0.7 ms → 0.6 ms ± 0.1 ms | 0.7 ms → 0.6 ms ± 0.0 ms | 0.7 ms → 0.6 ms ± 0.0 ms |
| standard | 8 unknown: The House (all face-down) | Straight | 3.3 ms → 1.7 ms ± 0.2 ms | 3.5 ms → 1.8 ms ± 0.3 ms | 22 ms → 8.3 ms ± 0.5 ms | 15 ms → 6.0 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Flush | 1.1 ms → 1.0 ms ± 0.1 ms | 2.9 ms → 1.7 ms ± 0.2 ms | 1.8 ms → 1.1 ms ± 0.2 ms | 2.4 ms → 1.6 ms ± 0.3 ms |
| standard | 8 unknown: The House (all face-down) | Full House | 6.0 ms → 4.1 ms ± 0.3 ms | 5.8 ms → 4.2 ms ± 0.2 ms | 5.8 ms → 4.0 ms ± 0.3 ms | 5.9 ms → 4.2 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Four of a Kind | 0.7 ms → 0.6 ms ± 0.0 ms | 0.8 ms → 0.6 ms ± 0.1 ms | 0.7 ms → 0.6 ms ± 0.1 ms | 0.7 ms → 0.6 ms ± 0.0 ms |
| standard | 8 unknown: The House (all face-down) | Straight Flush | 5.4 ms → 2.0 ms ± 0.2 ms | 978 ms → 85 ms ± 4.1 ms | 27 ms → 6.7 ms ± 0.3 ms | 6.82 s → 315 ms ± 10 ms |
| standard | 8 unknown: The House (all face-down) | Royal Flush | 2.2 ms → 1.1 ms ± 0.1 ms | 15 ms → 5.2 ms ± 0.3 ms | 1.6 ms → 1.1 ms ± 0.1 ms | 20 ms → 6.2 ms ± 0.2 ms |
| standard | 8 unknown: The House (all face-down) | Five of a Kind | 0.3 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.3 ms ± 0.0 ms |
| standard | 8 unknown: The House (all face-down) | Flush House | 6.2 ms → 3.2 ms ± 0.2 ms | 57 ms → 14 ms ± 1.9 ms | 6.2 ms → 3.6 ms ± 0.3 ms | 61 ms → 15 ms ± 1.8 ms |
| standard | 8 unknown: The House (all face-down) | Flush Five | 1.5 ms → 1.1 ms ± 0.2 ms | 5.1 ms → 2.6 ms ± 0.5 ms | 1.7 ms → 1.2 ms ± 0.1 ms | 5.6 ms → 4.2 ms ± 4.0 ms |
| standard | 6 unknown: 1 face-down, draw 5 | Straight Flush | 2.9 ms → 2.9 ms ± 0.5 ms | 109 ms → 68 ms ± 1.9 ms | 14 ms → 12 ms ± 0.2 ms | 506 ms → 234 ms ± 18 ms |
| standard | 7 unknown: 2 face-down, draw 5 | Straight Flush | 4.0 ms → 3.0 ms ± 0.3 ms | 163 ms → 93 ms ± 2.1 ms | 16 ms → 13 ms ± 0.4 ms | 941 ms → 354 ms ± 35 ms |
| standard | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 86 ms → 16 ms ± 1.0 ms | 1.75 s → 189 ms ± 7.7 ms | 226 ms → 35 ms ± 2.3 ms | 6.22 s → 520 ms ± 19 ms |
| standard | 8 unknown: The House (all face-down) | Flush + Straight Flush (any) | 1.0 ms → 1.1 ms ± 0.2 ms | 2.4 ms → 1.7 ms ± 0.3 ms | 1.1 ms → 1.2 ms ± 0.2 ms | 2.6 ms → 1.6 ms ± 0.3 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair + Flush (any) | 44 ms → 9.1 ms ± 0.6 ms | 157 ms → 24 ms ± 2.7 ms | 44 ms → 10 ms ± 1.2 ms | 156 ms → 23 ms ± 2.3 ms |
| standard | 8 unknown: The House (all face-down) | Straight + Flush (any) | 61 ms → 10 ms ± 0.6 ms | 268 ms → 32 ms ± 1.8 ms | 370 ms → 42 ms ± 1.6 ms | 893 ms → 74 ms ± 1.5 ms |
| wild | Keep 3, draw 5 | Straight Flush | 3.2 ms → 3.8 ms ± 0.4 ms | 214 ms → 75 ms ± 1.8 ms | 30 ms → 23 ms ± 0.3 ms | 980 ms → 250 ms ± 1.5 ms |
| wild | 6 unknown: 1 face-down, draw 5 | Straight Flush | 6.2 ms → 6.1 ms ± 0.5 ms | 562 ms → 180 ms ± 3.1 ms | 57 ms → 42 ms ± 0.6 ms | 3.42 s → 714 ms ± 1.4 ms |
| wild | 7 unknown: 2 face-down, draw 5 | Straight Flush | 9.8 ms → 7.5 ms ± 0.4 ms | 1.54 s → 468 ms ± 11 ms | 117 ms → 70 ms ± 0.6 ms | 13.13 s → 3.20 s ± 36 ms |
| wild | 8 unknown: The House (all face-down) | Straight Flush | 30 ms → 22 ms ± 0.7 ms | 9.25 s → 2.85 s ± 16 ms | 324 ms → 203 ms ± 9.0 ms | 81.37 s → 19.25 s ± 146 ms |
| wild | 8 unknown: The House (all face-down) | Flush House | 4.2 ms → 4.0 ms ± 0.1 ms | 28 ms → 19 ms ± 1.8 ms | 4.1 ms → 4.0 ms ± 0.3 ms | 36 ms → 21 ms ± 2.5 ms |
| wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 173 ms → 95 ms ± 1.7 ms | 12.65 s → 4.42 s ± 30 ms | 1.54 s → 772 ms ± 5.7 ms | 93.74 s → 25.48 s ± 101 ms |
| wild | 8 unknown: The House (all face-down) | Pair + Flush (any) | 52 ms → 41 ms ± 0.6 ms | 57 ms → 46 ms ± 0.6 ms | 53 ms → 43 ms ± 2.3 ms | 56 ms → 48 ms ± 0.6 ms |

### Small slowdowns

A few rows that take 1–5 ms got slower by 0.1–0.9 ms (e.g. Royal Flush with
Four Fingers, keeping 3: 3.9 ms → 4.8 ms; Flush Five, keeping 3: 1.5 ms →
2.0 ms). Each search now does a little more setup per pattern (interning
parts, the bound on cards needed). It wasn't worth chasing at this size.

## Earlier work

These tables are from before the second pass: "before" is the engine
before that piece of work, and "after Shortcut work" is commit `62eb51c`,
measured on 2026-10-07 before the second pass.

### Shortcut's slow cases

Shortcut alone was never slow. With Four Fingers as well, every Straight
Flush case was (see [jokers/shortcut.md](jokers/shortcut.md)):

| Situation | Before | After Shortcut work |
|---|---|---|
| Straight Flush, keep 3, draw 5 | 1.43 s | 182 ms ± 5 ms |
| Straight Flush, 6 unknown | 3.95 s | 546 ms ± 20 ms |
| Straight Flush, 7 unknown | 6.09 s | 1.06 s ± 0.01 s |
| Straight Flush, 8 unknown (The House) | 39.1 s | 7.54 s ± 0.06 s |
| Any of Full House / Straight Flush, 8 unknown | 48.3 s | 6.68 s ± 0.25 s |
| Any of Straight / Flush, 8 unknown | 2.12 s | 904 ms ± 15 ms |
| *Wild deck:* Straight Flush, keep 3, draw 5 | 6.55 s | 991 ms ± 13 ms |
| *Wild deck:* Straight Flush, 6 unknown | 20.0 s | 3.52 s ± 0.04 s |
| *Wild deck:* Straight Flush, 7 unknown | 72.1 s | 13.5 s ± 0.1 s |
| *Wild deck:* Straight Flush, 8 unknown | > 120 s | 84.4 s ± 1.0 s |
| *Wild deck:* any of Full House / Straight Flush, 8 unknown | > 120 s | 97.1 s ± 1.5 s |

The same changes also halved Four Fingers' own slow cases (below).

### Four Fingers' slow cases

The cases from the original report, plus the related ones. *Before* is the
engine before the Four Fingers work (commit `7d2af71`), *after* is the end
of that work.

| Situation | Before | After | After Shortcut work |
|---|---|---|---|
| Straight Flush, keep 3, draw 5 | 243 ms | 78 ms | 45 ms ± 5 ms |
| Straight Flush, 6 unknown | 1.79 s | 239 ms | 123 ms ± 12 ms |
| Straight Flush, 7 unknown | 7.74 s | 424 ms | 178 ms ± 9 ms |
| Straight Flush, 8 unknown (The House) | 60.2 s | 2.84 s | 1.04 s ± 0.02 s |
| Royal Flush, 8 unknown | 93 ms | 16 ms | 16 ms ± 1 ms |
| Flush House, 8 unknown | 42 ms | 51 ms | 63 ms ± 8 ms |
| Any of Full House / Straight Flush, 8 unknown | 21.9 s | 3.80 s | 1.81 s ± 0.01 s |
| Any of Straight / Flush, 8 unknown | 494 ms | 310 ms | 281 ms ± 5 ms |
| Any of Two Pair / Flush, 8 unknown | 282 ms | 201 ms | 163 ms ± 5 ms |
| *Wild deck:* Straight Flush, keep 3, draw 5 | 670 ms | 313 ms | 218 ms ± 6 ms |
| *Wild deck:* Straight Flush, 6 unknown | 3.57 s | 987 ms | 582 ms ± 10 ms |
| *Wild deck:* Straight Flush, 7 unknown | 14.1 s | 3.53 s | 1.59 s ± 0.01 s |
| *Wild deck:* Straight Flush, 8 unknown | 107.5 s | 19.7 s | 9.61 s ± 0.09 s |
| *Wild deck:* any of Full House / Straight Flush, 8 unknown | 43.5 s | 27.2 s | 13.3 s ± 0.5 s |

# Benchmarks

Time to compute one odds row: one target, for one keep-or-discard choice. The
app computes each row twice (keep hand, discard the selection), and once more
for "Any target" when there are several targets. Anything slower than
~0.25 s shows "Calculating odds..." first; see [README.md](README.md).

## Setup

- **Machine:** AMD Ryzen 7 7700X, 32 GB, Windows 11, Python 3.14.3.
  Measured 2026-10-07, after the Shortcut work.
- **Method:** `tests/perf/benchmark.py --repeat 5 --budget 120`. Each
  scenario ran 5 times after an untimed warm-up run, and rows with a
  standard deviation over 25% of the mean were re-measured once
  (`--rerun-noisy`). Times are **mean ± standard deviation**; the raw times
  are in `tests/perf/baseline.json`.
- **Before** (in "All scenarios"): the engine just before the Shortcut
  work. For no jokers and Four Fingers, that's the previous
  `tests/perf/baseline.json` (commit `888db55`, measured the same way). For
  Shortcut and Four Fingers + Shortcut, it's a single run of each scenario
  at commit `dc8ac33`, which first supported Shortcut; "> 120 s" means
  it was cancelled. Both versions give exactly the same probabilities in
  every scenario.

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
- **"Any":** several targets combined, as in the "Any target" row.

## Shortcut's slow cases

Shortcut alone was never slow. With Four Fingers as well, every Straight
Flush case was (see [jokers/shortcut.md](jokers/shortcut.md)):

| Situation | Before | Now |
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

**Still slow: Four Fingers and Shortcut with many unknown cards.** On the
standard deck, The House takes about 7 s. With unseen Wild Cards, the suits
can't be split, so it's 3.5 s at 6 unknown cards, 13.5 s at 7, and over a
minute at 8.

## Four Fingers' slow cases

The cases from the original report, plus the related ones. *Before* is the
engine before the Four Fingers work (commit `7d2af71`), *after* is the end
of that work, and *now* is after the Shortcut work.

| Situation | Before | After | Now |
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

**Still slow: Four Fingers with unseen Wild Cards.** These can't be split by
suit (see [jokers/four-fingers.md](jokers/four-fingers.md)), so they take
seconds from 7 unknown cards on. **Any-target rows** mixing a
non-suit hand with Four Fingers' Straight Flush (e.g. Full House) carry both
through every term, so they take a little longer than Straight Flush alone.

## All scenarios

Before → now (mean ± standard deviation); see "Setup" for what *before* is.

| Deck | Situation | Target | No jokers | Four Fingers | Shortcut | Four Fingers + Shortcut |
|---|---|---|---|---|---|---|
| standard | Keep 3, draw 5 | Pair | 0.7 ms → 0.7 ms ± 0.3 ms | 0.5 ms → 0.6 ms ± 0.0 ms | 0.5 ms → 0.6 ms ± 0.0 ms | 0.5 ms → 0.7 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Two Pair | 2.5 ms → 2.3 ms ± 0.3 ms | 2.4 ms → 2.4 ms ± 0.2 ms | 2.9 ms → 2.5 ms ± 0.4 ms | 2.6 ms → 2.3 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Three of a Kind | 0.4 ms → 0.6 ms ± 0.1 ms | 0.5 ms → 0.5 ms ± 0.1 ms | 0.6 ms → 0.5 ms ± 0.0 ms | 0.5 ms → 0.6 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Straight | 1.2 ms → 1.1 ms ± 0.1 ms | 1.2 ms → 1.2 ms ± 0.1 ms | 8.8 ms → 7.1 ms ± 0.9 ms | 4.1 ms → 4.2 ms ± 0.3 ms |
| standard | Keep 3, draw 5 | Flush | 1.1 ms → 1.0 ms ± 0.1 ms | 1.3 ms → 1.2 ms ± 0.0 ms | 0.9 ms → 1.0 ms ± 0.1 ms | 0.9 ms → 1.3 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Full House | 4.1 ms → 3.9 ms ± 0.3 ms | 4.2 ms → 3.9 ms ± 0.2 ms | 4.4 ms → 3.9 ms ± 0.4 ms | 4.4 ms → 4.1 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Four of a Kind | 0.4 ms → 0.5 ms ± 0.0 ms | 0.4 ms → 0.4 ms ± 0.0 ms | 0.5 ms → 0.5 ms ± 0.1 ms | 0.5 ms → 0.5 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Straight Flush | 2.5 ms → 2.4 ms ± 0.3 ms | 78 ms → 45 ms ± 4.7 ms | 14 ms → 12 ms ± 0.6 ms | 1.43 s → 182 ms ± 5.4 ms |
| standard | Keep 3, draw 5 | Royal Flush | 1.1 ms → 1.1 ms ± 0.0 ms | 3.8 ms → 3.9 ms ± 0.5 ms | 1.3 ms → 1.0 ms ± 0.1 ms | 6.6 ms → 6.3 ms ± 0.9 ms |
| standard | Keep 3, draw 5 | Five of a Kind | 0.2 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.2 ms ± 0.0 ms | 0.4 ms → 0.3 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Flush House | 4.8 ms → 5.4 ms ± 0.2 ms | 20 ms → 24 ms ± 2.8 ms | 4.8 ms → 7.0 ms ± 1.2 ms | 18 ms → 27 ms ± 3.9 ms |
| standard | Keep 3, draw 5 | Flush Five | 1.3 ms → 1.6 ms ± 0.1 ms | 2.7 ms → 2.4 ms ± 0.3 ms | 1.4 ms → 1.4 ms ± 0.2 ms | 2.3 ms → 2.4 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Pair | 0.7 ms → 0.7 ms ± 0.1 ms | 0.6 ms → 0.7 ms ± 0.0 ms | 0.6 ms → 0.7 ms ± 0.1 ms | 0.7 ms → 0.8 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair | 3.6 ms → 3.5 ms ± 0.3 ms | 3.7 ms → 3.4 ms ± 0.1 ms | 3.6 ms → 3.4 ms ± 0.1 ms | 3.4 ms → 3.4 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Three of a Kind | 0.7 ms → 0.8 ms ± 0.1 ms | 0.6 ms → 0.7 ms ± 0.1 ms | 0.7 ms → 0.7 ms ± 0.1 ms | 0.7 ms → 0.9 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight | 3.6 ms → 3.7 ms ± 0.6 ms | 4.0 ms → 3.9 ms ± 0.2 ms | 27 ms → 26 ms ± 3.3 ms | 17 ms → 17 ms ± 3.2 ms |
| standard | 8 unknown: The House (all face-down) | Flush | 1.0 ms → 1.1 ms ± 0.1 ms | 2.5 ms → 2.7 ms ± 0.8 ms | 1.1 ms → 1.2 ms ± 0.2 ms | 1.8 ms → 2.4 ms ± 0.3 ms |
| standard | 8 unknown: The House (all face-down) | Full House | 6.1 ms → 6.0 ms ± 0.2 ms | 6.5 ms → 5.8 ms ± 0.2 ms | 6.2 ms → 5.6 ms ± 0.3 ms | 6.4 ms → 6.1 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Four of a Kind | 0.6 ms → 0.7 ms ± 0.1 ms | 0.6 ms → 0.6 ms ± 0.1 ms | 0.5 ms → 0.8 ms ± 0.1 ms | 0.6 ms → 0.6 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight Flush | 5.5 ms → 5.3 ms ± 0.2 ms | 2.84 s → 1.04 s ± 15 ms | 30 ms → 29 ms ± 5.1 ms | 39.06 s → 7.54 s ± 64 ms |
| standard | 8 unknown: The House (all face-down) | Royal Flush | 1.2 ms → 1.4 ms ± 0.1 ms | 16 ms → 16 ms ± 1.2 ms | 4.9 ms → 1.3 ms ± 0.2 ms | 23 ms → 22 ms ± 2.1 ms |
| standard | 8 unknown: The House (all face-down) | Five of a Kind | 0.2 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.3 ms ± 0.0 ms | 0.3 ms → 0.3 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Flush House | 4.9 ms → 5.6 ms ± 0.8 ms | 51 ms → 63 ms ± 7.7 ms | 5.1 ms → 6.8 ms ± 1.2 ms | 51 ms → 65 ms ± 14 ms |
| standard | 8 unknown: The House (all face-down) | Flush Five | 1.6 ms → 1.5 ms ± 0.2 ms | 5.3 ms → 5.5 ms ± 0.2 ms | 1.7 ms → 1.6 ms ± 0.3 ms | 4.4 ms → 6.0 ms ± 1.6 ms |
| standard | 6 unknown: 1 face-down, draw 5 | Straight Flush | 3.0 ms → 2.9 ms ± 0.3 ms | 239 ms → 123 ms ± 12 ms | 18 ms → 15 ms ± 1.4 ms | 3.95 s → 546 ms ± 20 ms |
| standard | 7 unknown: 2 face-down, draw 5 | Straight Flush | 3.0 ms → 3.4 ms ± 0.3 ms | 424 ms → 178 ms ± 9.2 ms | 20 ms → 15 ms ± 0.2 ms | 6.09 s → 1.06 s ± 9.9 ms |
| standard | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 98 ms → 92 ms ± 5.5 ms | 3.80 s → 1.81 s ± 14 ms | 283 ms → 242 ms ± 10 ms | 48.26 s → 6.68 s ± 247 ms |
| standard | 8 unknown: The House (all face-down) | Flush + Straight Flush (any) | 1.0 ms → 1.1 ms ± 0.1 ms | 2.3 ms → 2.2 ms ± 0.3 ms | 1.3 ms → 1.4 ms ± 0.1 ms | 2.8 ms → 2.6 ms ± 0.2 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair + Flush (any) | 49 ms → 48 ms ± 4.2 ms | 201 ms → 163 ms ± 5.2 ms | 52 ms → 47 ms ± 3.3 ms | 237 ms → 160 ms ± 3.4 ms |
| standard | 8 unknown: The House (all face-down) | Straight + Flush (any) | 63 ms → 63 ms ± 0.6 ms | 310 ms → 281 ms ± 5.0 ms | 520 ms → 396 ms ± 17 ms | 2.12 s → 904 ms ± 15 ms |
| wild | Keep 3, draw 5 | Straight Flush | 3.7 ms → 3.8 ms ± 0.4 ms | 313 ms → 218 ms ± 5.8 ms | 46 ms → 31 ms ± 3.1 ms | 6.55 s → 991 ms ± 13 ms |
| wild | 6 unknown: 1 face-down, draw 5 | Straight Flush | 6.8 ms → 6.6 ms ± 0.3 ms | 987 ms → 582 ms ± 10 ms | 87 ms → 63 ms ± 7.6 ms | 19.95 s → 3.52 s ± 37 ms |
| wild | 7 unknown: 2 face-down, draw 5 | Straight Flush | 12 ms → 11 ms ± 0.6 ms | 3.53 s → 1.59 s ± 7.8 ms | 150 ms → 124 ms ± 4.0 ms | 72.07 s → 13.53 s ± 111 ms |
| wild | 8 unknown: The House (all face-down) | Straight Flush | 35 ms → 33 ms ± 0.9 ms | 19.70 s → 9.61 s ± 89 ms | 376 ms → 356 ms ± 7.7 ms | > 120 s → 84.38 s ± 1.01 s |
| wild | 8 unknown: The House (all face-down) | Flush House | 3.5 ms → 4.0 ms ± 0.2 ms | 67 ms → 26 ms ± 1.2 ms | 3.5 ms → 4.6 ms ± 2.2 ms | 68 ms → 23 ms ± 0.3 ms |
| wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 245 ms → 188 ms ± 5.7 ms | 27.23 s → 13.33 s ± 488 ms | 3.00 s → 1.67 s ± 14 ms | > 120 s → 97.08 s ± 1.48 s |
| wild | 8 unknown: The House (all face-down) | Pair + Flush (any) | 57 ms → 56 ms ± 1.5 ms | 50 ms → 59 ms ± 1.8 ms | 48 ms → 55 ms ± 0.4 ms | 348 ms → 57 ms ± 3.6 ms |

### Small slowdowns

Flush House with Four Fingers got slower by a few milliseconds: side by
side, 49 ms → 54 ms with 8 unknown cards and 18 ms → 20 ms keeping 3 (the
table's means are noisier). It has 1,872 patterns, so this is probably the
per-search setup that "Patterns join the search late" (see
[engine.md](engine.md)) added for every pattern; it wasn't profiled. A few
other rows moved by a fraction of a millisecond either way, which is within
the noise.

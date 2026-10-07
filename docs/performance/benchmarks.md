# Benchmarks

Time to compute one odds row: one target, for one keep-or-discard choice. The
app computes each row twice (keep hand, discard the selection), and once more
for "Any target" when there are several targets. Anything slower than
~0.25 s shows "Calculating odds..." first; see [README.md](README.md).

## Setup

- **Machine:** AMD Ryzen 7 7700X, 32 GB, Windows 11, Python 3.14.3.
  Measured 2026-10-06.
- **Method:** `tests/perf/benchmark.py --repeat 5 --budget 120`. Each
  scenario ran 5 times after an untimed warm-up run, and rows with a
  standard deviation over 25% of the mean were re-measured once
  (`--rerun-noisy`). Times are **mean ± standard deviation**; the raw times
  are in `tests/perf/baseline.json`.
- **Before:** the engine at commit `7d2af71`, before the Four Fingers work,
  measured the same way on the same scenarios. Runs stop once they add up to
  120 s, so its slowest rows are means of fewer runs: Four Fingers Straight
  Flush with 8 unknown cards (3 runs, 2 with Wild Cards), and Full House +
  Straight Flush with Wild Cards (3 runs). Both versions give exactly the
  same probabilities in every scenario.

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

## Four Fingers' slow cases

The cases from the original report, plus the related ones:

| Situation | Before | Now |
|---|---|---|
| Straight Flush, keep 3, draw 5 | 243 ms | 78 ms ± 7 ms |
| Straight Flush, 6 unknown | 1.79 s | 239 ms ± 9 ms |
| Straight Flush, 7 unknown | 7.74 s | 424 ms ± 14 ms |
| Straight Flush, 8 unknown (The House) | 60.2 s | 2.84 s ± 0.12 s |
| Royal Flush, 8 unknown | 93 ms | 16 ms ± 2 ms |
| Flush House, 8 unknown | 42 ms | 51 ms ± 10 ms |
| Any of Full House / Straight Flush, 8 unknown | 21.9 s | 3.80 s ± 0.04 s |
| Any of Straight / Flush, 8 unknown | 494 ms | 310 ms ± 3 ms |
| Any of Two Pair / Flush, 8 unknown | 282 ms | 201 ms ± 23 ms |
| *Wild deck:* Straight Flush, keep 3, draw 5 | 670 ms | 313 ms ± 19 ms |
| *Wild deck:* Straight Flush, 6 unknown | 3.57 s | 987 ms ± 132 ms |
| *Wild deck:* Straight Flush, 7 unknown | 14.1 s | 3.53 s ± 0.42 s |
| *Wild deck:* Straight Flush, 8 unknown | 107.5 s | 19.7 s ± 2.7 s |
| *Wild deck:* any of Full House / Straight Flush, 8 unknown | 43.5 s | 27.2 s ± 3.7 s |

**Still slow: Four Fingers with unseen Wild Cards.** These can't be split by
suit (see [jokers/four-fingers.md](jokers/four-fingers.md)), so they are
several seconds from 7 unknown cards on. **Any-target rows** mixing a
non-suit hand with Four Fingers' Straight Flush (e.g. Full House) carry both
through every term, so they take a little longer than Straight Flush alone.

## All scenarios

Before → now (mean ± standard deviation).

| Deck | Situation | Target | No jokers | Four Fingers |
|---|---|---|---|---|
| standard | Keep 3, draw 5 | Pair | 0.8 ms → 0.7 ms ± 0.3 ms | 0.7 ms → 0.5 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Two Pair | 3.3 ms → 2.5 ms ± 0.3 ms | 2.8 ms → 2.4 ms ± 0.3 ms |
| standard | Keep 3, draw 5 | Three of a Kind | 0.7 ms → 0.4 ms ± 0.0 ms | 0.6 ms → 0.5 ms ± 0.1 ms |
| standard | Keep 3, draw 5 | Straight | 1.7 ms → 1.2 ms ± 0.1 ms | 1.3 ms → 1.2 ms ± 0.2 ms |
| standard | Keep 3, draw 5 | Flush | 0.3 ms → 1.1 ms ± 0.3 ms | 0.2 ms → 1.3 ms ± 0.3 ms |
| standard | Keep 3, draw 5 | Full House | 4.3 ms → 4.1 ms ± 0.3 ms | 4.1 ms → 4.2 ms ± 0.8 ms |
| standard | Keep 3, draw 5 | Four of a Kind | 0.4 ms → 0.4 ms ± 0.1 ms | 0.5 ms → 0.4 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Straight Flush | 2.9 ms → 2.5 ms ± 0.4 ms | 243 ms → 78 ms ± 7.4 ms |
| standard | Keep 3, draw 5 | Royal Flush | 0.3 ms → 1.1 ms ± 0.2 ms | 2.6 ms → 3.8 ms ± 0.4 ms |
| standard | Keep 3, draw 5 | Five of a Kind | 0.2 ms → 0.2 ms ± 0.0 ms | 0.2 ms → 0.3 ms ± 0.0 ms |
| standard | Keep 3, draw 5 | Flush House | 9.9 ms → 4.8 ms ± 0.2 ms | 46 ms → 20 ms ± 1.1 ms |
| standard | Keep 3, draw 5 | Flush Five | 0.8 ms → 1.3 ms ± 0.1 ms | 1.5 ms → 2.7 ms ± 0.7 ms |
| standard | 8 unknown: The House (all face-down) | Pair | 1.1 ms → 0.7 ms ± 0.1 ms | 1.0 ms → 0.6 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair | 5.7 ms → 3.6 ms ± 0.3 ms | 5.3 ms → 3.7 ms ± 0.2 ms |
| standard | 8 unknown: The House (all face-down) | Three of a Kind | 1.1 ms → 0.7 ms ± 0.1 ms | 1.1 ms → 0.6 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight | 4.4 ms → 3.6 ms ± 0.2 ms | 5.8 ms → 4.0 ms ± 0.7 ms |
| standard | 8 unknown: The House (all face-down) | Flush | 0.4 ms → 1.0 ms ± 0.1 ms | 0.4 ms → 2.5 ms ± 0.2 ms |
| standard | 8 unknown: The House (all face-down) | Full House | 9.8 ms → 6.1 ms ± 0.4 ms | 9.3 ms → 6.5 ms ± 0.7 ms |
| standard | 8 unknown: The House (all face-down) | Four of a Kind | 1.0 ms → 0.6 ms ± 0.1 ms | 0.9 ms → 0.6 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Straight Flush | 12 ms → 5.5 ms ± 0.9 ms | 60.17 s → 2.84 s ± 123 ms |
| standard | 8 unknown: The House (all face-down) | Royal Flush | 0.8 ms → 1.2 ms ± 0.1 ms | 93 ms → 16 ms ± 2.1 ms |
| standard | 8 unknown: The House (all face-down) | Five of a Kind | 0.2 ms → 0.2 ms ± 0.0 ms | 0.2 ms → 0.3 ms ± 0.1 ms |
| standard | 8 unknown: The House (all face-down) | Flush House | 11 ms → 4.9 ms ± 0.4 ms | 42 ms → 51 ms ± 10 ms |
| standard | 8 unknown: The House (all face-down) | Flush Five | 0.7 ms → 1.6 ms ± 0.3 ms | 1.8 ms → 5.3 ms ± 1.0 ms |
| standard | 6 unknown: 1 face-down, draw 5 | Straight Flush | 4.1 ms → 3.0 ms ± 0.4 ms | 1.79 s → 239 ms ± 8.7 ms |
| standard | 7 unknown: 2 face-down, draw 5 | Straight Flush | 4.9 ms → 3.0 ms ± 0.4 ms | 7.74 s → 424 ms ± 14 ms |
| standard | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 311 ms → 98 ms ± 2.2 ms | 21.88 s → 3.80 s ± 42 ms |
| standard | 8 unknown: The House (all face-down) | Flush + Straight Flush (any) | 20 ms → 1.0 ms ± 0.2 ms | 0.4 ms → 2.3 ms ± 0.4 ms |
| standard | 8 unknown: The House (all face-down) | Two Pair + Flush (any) | 229 ms → 49 ms ± 2.6 ms | 282 ms → 201 ms ± 23 ms |
| standard | 8 unknown: The House (all face-down) | Straight + Flush (any) | 378 ms → 63 ms ± 1.1 ms | 494 ms → 310 ms ± 2.7 ms |
| wild | Keep 3, draw 5 | Straight Flush | 4.4 ms → 3.7 ms ± 0.8 ms | 670 ms → 313 ms ± 19 ms |
| wild | 6 unknown: 1 face-down, draw 5 | Straight Flush | 8.1 ms → 6.8 ms ± 1.1 ms | 3.57 s → 987 ms ± 132 ms |
| wild | 7 unknown: 2 face-down, draw 5 | Straight Flush | 14 ms → 12 ms ± 1.0 ms | 14.10 s → 3.53 s ± 419 ms |
| wild | 8 unknown: The House (all face-down) | Straight Flush | 43 ms → 35 ms ± 2.9 ms | 107.49 s → 19.70 s ± 2.70 s |
| wild | 8 unknown: The House (all face-down) | Flush House | 12 ms → 3.5 ms ± 0.3 ms | 99 ms → 67 ms ± 1.2 ms |
| wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | 396 ms → 245 ms ± 11 ms | 43.45 s → 27.23 s ± 3.68 s |
| wild | 8 unknown: The House (all face-down) | Pair + Flush (any) | 111 ms → 57 ms ± 11 ms | 102 ms → 50 ms ± 0.5 ms |

### Small slowdowns

A few rows that were already well under 10 ms got slower by a millisecond
or so: Flush, Royal Flush and Flush Five, and Flush + Straight Flush with
Four Fingers. All of these targets are split by suit (see
[engine.md](engine.md)), and a split runs several small searches, each with
its own setup cost. That's a fixed cost of a few milliseconds, and it's what
makes the slow cases fast.

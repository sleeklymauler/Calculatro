# Benchmarks

Time to compute one odds row: one target, for one keep-or-discard choice. The
app computes each row twice (keep hand, discard the selection), and once more
for "Any target" when there are several targets. Anything slower than
~0.25 s shows "Calculating odds..." first; see [README.md](README.md).

## Setup

- **Machine:** AMD Ryzen 7 7700X, 32 GB, Windows 11, Python 3.14.3.
  Measured 2026-10-07, after the second performance pass.
- **Method:** `tests/perf/benchmark.py --repeat 5 --budget 120`. Each
  scenario ran 5 times after an untimed warm-up run, and rows with a
  standard deviation over 25% of the mean were re-measured once
  (`--rerun-noisy`). Times are **mean ± standard deviation**; the raw times
  are in `tests/perf/baseline.json`.
- **Before** (in "The second pass" and "All scenarios"): the engine at
  commit `62eb51c`, after the Shortcut work, measured the same day side by
  side (`--repeat 5 --budget 60`, so the two slowest rows ran once). Both
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
- **"Any":** several targets combined, as in the "Any target" row.

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

**Still slow: unseen Wild Cards with Four Fingers and Shortcut.** The
suits can't be split, so the wild deck takes 3.2 s at 7 unknown cards and
about 20 s for The House (25 s with Full House as well). With Four Fingers
alone, The House takes about 3–4.5 s.

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

## All scenarios

Before (commit `62eb51c`) → now (mean ± standard deviation); see "Setup".

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

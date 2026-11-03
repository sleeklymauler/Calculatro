# Smeared Joker

Smeared Joker (`j_smeared`) makes Hearts and Diamonds one suit for flushes,
and Spades and Clubs another. The exact game rules are in
[docs/jokers.md](../../jokers.md#smeared-joker-j_smeared). This page covers
what that does to the engine's performance; see [../engine.md](../engine.md)
for the general design.

## What it does to the patterns

`card_key` classes a Diamond as a Heart and a Club as a Spade, and the
flush-type hands (Flush, Straight Flush, Flush House, Flush Five) are built
for those two suits only (`hands.flush_suits`). Each colour then has two
cards of every rank where a suit had one, and there are two groups of suited
patterns instead of four.

Mostly this makes things faster. Half as many suited patterns means less
work, and with Wild Cards unseen (when the split by suit is off anyway), the
search follows two colours instead of four suits: the wild deck's The House
with Four Fingers + Shortcut takes 0.55 s for Straight Flush, against 10.5 s
without Smeared.

The cost shows with Four Fingers, whose Straight Flush patterns have
variants for "a second suited card of a run rank". Without Smeared those
need a Wild Card or a duplicate card, so they die at once on a normal deck.
With Smeared every colour has two cards of each rank, so they stay live, and
a position carries up to 150 live parts.

## Changes

Measured on the scenarios with Four Fingers + Shortcut + Smeared, 8 unknown
cards (The House). "Before" is the first implementation, one run each.

| Scenario | Before | Two colours as one search | + Joins cached | + Faster one-group positions |
|---|---|---|---|---|
| standard, Straight Flush | 1.26 s | 0.81 s | 0.56 s | 0.50 s |
| standard, Full House + Straight Flush | 1.92 s | 1.10 s | 0.76 s | 0.68 s |
| wild, Straight Flush | 1.13 s | (not split) | 0.78 s | 0.69 s |
| wild, Full House + Straight Flush | 1.43 s | (not split) | 1.03 s | 0.93 s |

Every result is exactly the same as before. Full tables are in
[../benchmarks.md](../benchmarks.md#smeared-joker).

### 1. Two colours aren't split

With two groups, inclusion–exclusion computes three terms: one per colour
and one with both. The two-colour term has to carry both colours' parts at
once, which is everything one search over the whole target carries, but it
can only stop once *both* colours have a pattern, where one search stops at
the first. On the standard deck it alone took longer (0.88 s of 1.16 s)
than one search over everything (0.81 s). One search was faster in every
Smeared scenario but one (Shortcut + Smeared, Full House + Straight Flush:
65 ms split, 86 ms not), so `probability()` only splits with more than two
suit groups. Without Smeared a target's suited patterns always cover four
suits, so nothing else changes.

### 2. Joining parts are cached

Each position recomputed the parts of every pattern joining at its class:
about 23 calls to `join` per position, 22% of the profile. Those parts follow
from the deferred counts, capped the way the memo key caps them, so they are
cached by `(class, todo, free, deferred counts)`. This had been tried before
with no measurable gain, but with Four
Fingers + Shortcut many patterns join at each rank. It made every slow
scenario 1.2–1.5× faster, with or without Smeared (the wild deck's The House
with Four Fingers + Shortcut: 19.2 s → 13.8 s).

### 3. Faster one-group positions

When the target isn't split, there is one group and no constraint, so a
part is live just when it needs no more cards than are left. The live parts
and the moving and still parts are then built with set and list
comprehensions, and parts that need one card are found with a set
intersection. About 10%.

## Tried and dropped

- **Dropping every live part that implies another** (each of its
  requirements is a subset of the classes of one of the other's, with at
  least the deficit), checked at every memo miss: the redundancy looked
  large in sample keys, but it removed only 7% of positions and made the
  search 4× slower.

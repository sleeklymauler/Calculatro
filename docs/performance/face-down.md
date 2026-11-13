# Face-down cards in the hand's sorts

What the odds cost when the hand's sorts narrow down its face-down cards, and which cases are still
slow. How the model works is in
[probability-model.md](../probability-model.md#face-down-cards-in-the-hands-sorts). The benchmarks are
the "sorted" scenarios in `tests/perf/scenarios.py`.

This page was moved here from the plan the sorts were built from, now removed
(`git show a4e85fb^:docs/plans/face-down-windows.md`, "Performance"), which also has the numbers from
each stage before these.

## Measured

On 2026-11-09, after the exact optimizations (windows, splitting a window by its blocks' edge cards,
`_says_nothing_here`), on an AMD Ryzen 7 7700X, with `benchmark.py -k face-down --repeat 3`, against
the same scenarios measured before them. Every existing scenario's result was unchanged, and the new
scenarios' results matched the deal-by-deal sum (no windows), which took up to 7 minutes:

- One or two face-down cards in sorts: 1-30 ms for single targets, 0.05-0.25 s for "any" (from
  0.15-2.4 s), and 0.3-2.2 s for Four Fingers + Shortcut with Straight Flush or "any" (from 0.7-13 s).
- Best discard with two face-down cards: 0.06-0.8 s (from 0.04-6.6 s). Four Fingers + Shortcut with
  Straight Flush or "any", new scenarios: 1.4-20 s. The only slowdown measured: sorted both ways,
  Flush, 36-44 ms to 61-70 ms.
- Five face-down cards kept around three face-up ones (The House after its first discard): 1-3 ms
  (from 1-2.3 s). Straight Flush 3 ms, or 64 ms with Four Fingers + Shortcut (24 s and 7 minutes
  summing every deal). The Fish with earlier sorts: 6-27 ms (from 0.5-1.5 s).
- The House after its first discard with the earlier sort of all 8 (`HOUSE_EARLIER`), about 1.7 * 10^8
  deals: 0.4-0.7 s (measured with other jobs running; it didn't finish in 9 minutes before). Sorted
  both ways, then 3 discarded face down and 3 drawn re-sorted by suit: 0.3-0.4 s (from 14-54 s).
- A discard that splits The House's first hand: the 3 highest thrown away, 0.04-0.34 s, Straight
  Flush included (summing every deal, Pair took 40 s, Full House 4 minutes); the 3 in the middle, Pair
  1 s (44 s), Flush 2.1-2.4 s, Straight Flush 10 s (over 2 minutes).
- The Mark is unchanged (no windows there): 11 ms-7.9 s.
- Cancellation: under 75 ms everywhere measured, the slowest part being freeing the partial sums of a
  big `summed`.

## What's still slow

The cases still slow after those optimizations. They're why the panel shows an estimate first
([probability-model.md](../probability-model.md#approximations)); the times here are for the exact
odds. Times are for one odds row (or one best-discard search), measured as above.

1. **Discards that split The House's first hand into several blocks**, for targets that tell most
   cards apart. Throwing away every other card of the sort (K, J, 8, 5 of K Q J 9 8 7 5 3): Pair 62 s,
   Flush 4 s, Straight Flush over 3 minutes, using 9 GB of memory before it's cancelled. Blocks of more
   than two make too many ways to split (`engine.SPLITS`), so their deals are summed
   (`posterior.summed`), and with fine-grained labels the partial sums grow huge. The 3 in the middle
   thrown away: Straight Flush 10 s. The 3 highest, with Four Fingers + Shortcut and Straight Flush:
   about 2 minutes.
2. **Best discard on The House's first hand** (all 8 face down, sorted): it tries discards of every
   shape, many of them case 1. Flush took 554 s.
3. **After The House's first discard, a discard that draws** (the `HOUSE_EARLIER` hand with its 3
   face-up cards thrown away), so the 3 cards discarded face down earlier matter to what's left to
   draw: Pair 13 s, Full House 72 s, Straight Flush over 3 minutes. Its first sort has all 8 cards in a
   row with different fates and another sort, so it's no window.
4. **Four Fingers + Shortcut best-discard searches with face-down cards**: Straight Flush 9-16 s and
   "any" 1.4-20 s with two face-down cards (2.0-2.6 s with none). Each discard is one search, but
   weighing a window's draw makes the engine's search slower than with no face-down cards.
5. **The Mark** with sorted face-down cards: windows aren't used there, as The Mark's split already
   weighs the draw by face cards. Four Fingers + Shortcut with Straight Flush or "any": 6.4-7.9 s with 3
   face-down cards.
6. **Memory.** `summed` keeps one layer of partial sums at a time, but for long mixed-fate runs and
   fine-grained targets a layer can hold millions (case 1). Nothing bounds it but the app's memory limit
   (`worker.Watchdog`), which gives the value up.

Measured on 2026-11-11, face-down cards in sorts under The Hook and Cerulean Bell, which take no
windows (their deals are summed): with two face-down cards, at most 1.2 s (Cerulean Bell's Full House);
with five kept (`HOUSE_AFTER`), The Hook's Pair 3.5 s, Full House 22 s and Straight Flush over a
minute, and Cerulean Bell's Flush, Full House and Straight Flush over a minute. The estimate covers
them all in about 1 s. Not measured: DNA's copies of a face-down card (branched on with every unseen
card), which the estimate covers too (`test_estimates_hold_the_exact_odds`).

Found in game (2026-11-12): **The House's first hand sorted both ways**, kept. Two sorts with no
face-up card say how the cards split into suits, so `_says_nothing_here` doesn't apply, and nothing
narrows the deals down. With the suit sort in runs of 3, 2 and 3 (`THE_HOUSE_BOTH`): Pair 6.4 s,
Straight Flush 17 s. Building the estimate's sampler of deals took 1.2 s, once for each value, and with
its 1 s of drawing that went over the estimate's 2 s limit, so most rows had no estimate, and the first
one came after 12 s. The sampler is now built once for every value of a state (`engine._deals_for`)
and counted in the estimate's time: the first estimate comes after 3.9 s, and every row has one after
9.6 s (all of them holding the exact odds), where it took 91 s for every row to show anything. Then the
quick pass stopped trying values once one had run out of time checking the sorts, which every other one
would too (`engine.evidence_unchecked`): 1.7 s to the first estimate, 7.4 s to the last.

# Face-down cards in the hand's sorts

What the odds cost when the hand's sorts narrow down its face-down cards, and which cases are still
slow. How the model works is in
[probability-model.md](../probability-model.md#face-down-cards-in-the-hands-sorts). The benchmarks are
the "sorted" scenarios in `tests/perf/scenarios.py`.

This page was moved here from the plan the sorts were built from, now removed
(`git show a4e85fb^:docs/plans/face-down-windows.md`, "Performance"), which also has each stage's
numbers.

## What made it fast

The exact optimizations (windows, splitting a window by its blocks' edge cards, `_says_nothing_here`)
were checked against the deal-by-deal sum (no windows), which took up to 7 minutes on the new
scenarios, and left every existing scenario's result unchanged. They made:

- one or two face-down cards in sorts 2-10x faster for "any" and for Four Fingers + Shortcut, and
  their slowest best-discard searches about 8x faster;
- five face-down cards kept around three face-up ones (The House after its first discard) go from
  1-2 s to a few milliseconds, and The Fish with earlier sorts about 50x faster;
- The House after its first discard with the earlier sort of all 8 (`HOUSE_EARLIER`), about 1.7 * 10^8
  deals, finish in under a second, where it hadn't finished in 9 minutes;
- a discard that throws away The House's 3 highest cards take under 0.5 s, where summing every deal
  took minutes.

The only slowdown measured was small: sorted both ways, Flush, by about 25 ms. The Mark is unchanged,
as it uses no windows. Cancelling takes under 75 ms everywhere measured, the slowest part being freeing
the partial sums of a big `summed`.

**The House's first hand sorted both ways** (found in game, 2026-11-12). Two sorts with no face-up card
say how the cards split into suits, so `_says_nothing_here` doesn't apply and nothing narrows the deals
down: the exact odds take seconds (`THE_HOUSE_BOTH`). Building the estimate's sampler of deals took
over a second for each value, so with its own drawing most values went over the estimate's time limit
and had no estimate. The sampler is now built once for every value of a state (`engine._deals_for`) and
counted in the estimate's time, and the quick pass stops trying values once one has run out of time
checking the sorts, which every other one would too (`engine.evidence_unchecked`). Every row then had
an estimate after 7.4 s, where it took 91 s for every row to show anything.

## What's still slow

The cases still slow after those optimizations, in rough order. They're why the panel shows an
estimate first ([probability-model.md](../probability-model.md#approximations)), which covers them all
in about a second.

1. **Discards that split The House's first hand into several blocks**, for targets that tell most
   cards apart, such as throwing away every other card of the sort (K, J, 8, 5 of K Q J 9 8 7 5 3):
   Straight Flush runs for minutes and uses gigabytes of memory before it's cancelled. Blocks of more
   than two make too many ways to split (`engine.SPLITS`), so their deals are summed
   (`posterior.summed`), and with fine-grained labels the partial sums grow huge. Throwing away the 3 in
   the middle, or the 3 highest with Four Fingers + Shortcut, is slow too.
2. **Best discard on The House's first hand** (all 8 face down, sorted): it tries discards of every
   shape, many of them case 1. Flush took about 9 minutes.
3. **After The House's first discard, a discard that draws** (the `HOUSE_EARLIER` hand with its 3
   face-up cards thrown away), so the 3 cards discarded face down earlier matter to what's left to
   draw: from seconds for Pair to minutes for Straight Flush. Its first sort has all 8 cards in a row
   with different fates and another sort, so it's no window.
4. **Four Fingers + Shortcut best-discard searches with face-down cards**, for Straight Flush or "any".
   Each discard is one search, but weighing a window's draw makes the engine's search slower than with
   no face-down cards.
5. **The Mark** with sorted face-down cards: windows aren't used there, as The Mark's split already
   weighs the draw by face cards. Four Fingers + Shortcut with Straight Flush or "any" takes seconds.
6. **Face-down cards in sorts under The Hook and Cerulean Bell**, which take no windows (their deals are
   summed). Two face-down cards are fine; five kept (`HOUSE_AFTER`) takes from seconds to over a minute.
   Not measured: DNA's copies of a face-down card (branched on with every unseen card), which the
   estimate covers too (`test_estimates_hold_the_exact_odds`).
7. **The House's first hand sorted both ways**, kept (see above).
8. **Memory.** `summed` keeps one layer of partial sums at a time, but for long mixed-fate runs and
   fine-grained targets a layer can hold millions (case 1). Nothing bounds it but the app's memory limit
   (`worker.Watchdog`), which gives the value up.

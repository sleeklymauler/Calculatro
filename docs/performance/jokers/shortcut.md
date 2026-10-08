# Shortcut

Shortcut (`j_shortcut`) lets a Straight skip one missing rank between cards,
so `10 8 6 4 2` is a Straight. The exact game rules are in
[docs/jokers.md](../../jokers.md#rules-that-matter-to-calculatro). This page
covers what that does to the engine's performance; see
[../engine.md](../engine.md) for the general design.

## What it does to the patterns

A Straight is a set of 5 ranks (4 with Four Fingers), each one or two above
the last (`_straight_windows`). Without Shortcut there are 10 such sets (11
with Four Fingers); with it there are 128 (76 with Four Fingers). Every hand
built on straights takes them all: Straight, Straight Flush and Royal Flush.

## Shortcut alone

Shortcut alone was never slow. Every scenario took about half a second or
less, except the wild deck's "any of Full House / Straight Flush" with 8 unknown
cards, at about 3 s, which Four Fingers alone already takes 20+ s for. A
5-card run spans up to 9 ranks, but without Four Fingers every card of a
Straight Flush must be suited, so a suit's patterns only remember which of
the last few ranks have a suited card.

## Four Fingers and Shortcut together

This is where it got slow. Four Fingers' Straight Flush is "3 suited ranks of
a run, any card of the 4th rank, and a 4th suited card" (see
[four-fingers.md](four-fingers.md)), for every 4-rank run. With 76 runs that
was 5,168 patterns, and The House took 46 s (wild deck: over 120 s).

Each step measured on the standard deck's Straight Flush with 8 unknown
cards (The House), during development. The middle rows are rough: other work
was sometimes running on the machine. The last two are side by side on an
idle machine.

| Change | Time |
|---|---|
| Original | 46 s |
| Only live patterns follow the draw | 23.6 s |
| Patterns wait for their own ranks before joining | 17.6 s |
| Runs that share 3 suited ranks are one pattern | 12.8 s |
| Deferred counts kept as running totals; patterns listed rank by rank | 7.6 s |
| Garbage collector paused during the search | 7.2 s |

Listing the patterns rank by rank (step 3) mattered most on the wild deck,
where the suits aren't split: The House went from 130 s to 101 s.

### 1. Only live patterns follow the draw

"A suited card of another rank" counts every suited card, so each suited
class counted towards nearly every pattern of its suit: thousands of
(pattern, requirement) slots. The search updated all of them for every card
it drew, although almost all of those patterns had long been dropped or
merged into another. Now only live patterns and the patterns joining at that
class are updated, since no other pattern is looked at again in that subtree.

### 2. Patterns wait for their own ranks

A pattern joins the search at its first class. Because "a suited card of
another rank" includes the suit's 2s, every run joined at the very first
class, each with its own copy of that requirement, so about 300 patterns
were live from the start. Now a pattern joins at the first class of one of
its single-rank requirements. Until then, the memo key holds how many cards
were drawn from the classes its other requirements cover, capped at what it
needs. For runs above the current rank that's the same set, all of the
suit's cards so far, so it costs one bit per suit. See "Patterns join the
search late" in [../engine.md](../engine.md).

### 3. One pattern per 3 suited ranks

Runs that share their 3 suited ranks differ only in the 4th rank. With
Shortcut, 5 7 9 is completed by a 3, 4, 6, 8, 10 or Jack. They are now one
pattern: "a card of any rank that completes the run" and "a suited card of
another rank". If that suited card has the completing rank, the whole run is
suited, so the separate "all 4 suited" pattern is no longer needed. That's
1,856 patterns instead of 5,168 (544 instead of 748 with Four Fingers alone).

The patterns are listed by their ranks, then by suit. The engine orders
classes by the first pattern they count towards through a single-rank
requirement, and "any card that completes the run" now spans several ranks.
Listed suit by suit, the 7♠ would only be placed by the spades patterns. The
search would then sweep the deck suit by suit and have to remember every
rank (see "Class order" in [../engine.md](../engine.md)).

### 4. Running totals, and the garbage collector

The deferred counts were first recomputed from the draw at every position.
They're now running totals. Pausing Python's cyclic garbage collector during
a search (`engine._gc_paused`) saves another 5–15% on the largest searches
(13.1 s instead of 15.2 s for the wild deck with 7 unknown cards). See
"Garbage collection" in [../engine.md](../engine.md).

## Tried and dropped

- **Dropping live patterns that another live pattern makes redundant** (its
  unmet requirements are a subset of theirs). This cut live patterns by
  about 30% but barely changed the number of positions, and the subset checks
  made it slower overall.
- **Conditioning on which Wild Cards are drawn**, so the wild deck can be
  split by suit (the idea noted in [four-fingers.md](four-fingers.md)). It
  took the wild House from over 120 s to about 115 s, but made small cases
  2–4× slower and the wild deck's Flush House and Pair + Flush 20× slower:
  with Wild Cards kept, terms with 3 and 4 suits become possible.

## Still slow

See [../benchmarks.md](../benchmarks.md) for current numbers.

- **Unseen Wild Cards.** As with Four Fingers alone, these use one search
  over all suits, and with Shortcut each suit has far more partial runs to
  remember. After the second performance pass, the wild deck's Straight
  Flush takes about 0.7 s at 6 unknown cards, 3.2 s at 7 and 19 s at 8 (The
  House); 25 s with Full House as well.
- **The House on the standard deck** took about 7 s before the second pass,
  mostly in the six two-suit terms of the split by suit. It's now 0.3 s with
  a full deck, where the suits are interchangeable and equal terms are
  computed once, and about 1.8 s once the suits differ.

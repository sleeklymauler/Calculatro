# How the probability engine stays fast

`calculatro/engine.py` computes **exact** odds (a `Fraction`), not Monte Carlo
estimates. The work is counting: of all the ways to draw the unknown cards,
how many give a final hand that contains the target? A full deck with 8
unknown cards has C(52, 8) ≈ 750 million draws, so the engine can never list
draws one by one. Every technique below exists to count large groups of
draws at once, or to avoid looking at them at all.

This page describes the design as of the Four Fingers work. Joker-specific
notes are in [jokers/](jokers/) and measured timings in
[benchmarks.md](benchmarks.md).

## The model

- **Targets are patterns.** `hands.py` turns each hand type into an OR of
  patterns. Each pattern is an AND of requirements, and a requirement means
  "at least `need` cards whose class is in `keys`". A class is
  `(rank_id, suit)`, with suit `"Wild"` for Wild Cards. Each requirement is
  counted on its own: a card counts towards every requirement whose keys it
  matches.
- **Unknown cards are one draw.** Kept face-down cards are treated as drawn
  from the unseen pool (undrawn deck + `hidden_pool`). By symmetry the
  distribution is the same, so "unknown cards" = face-down cards kept + cards
  drawn after the discard.
- **Integers until the end.** The search counts favourable draws as Python
  ints and divides by C(pool, draws) once at the end, so no fractions are
  built along the way.

## Counting many draws at once

### Card classes

Pool cards that count towards exactly the same requirements are merged into
one class: Pair only distinguishes 13 ranks, and Flush only distinguishes 4
suits. Cards that count towards nothing go into a single dead class that
never needs enumerating. The search then decides *how many* cards come from
each class, and C(class size, k) counts the ways to pick them
(multivariate hypergeometric).

### Early accept

Once any pattern is satisfied, every way of drawing the remaining cards
counts. The whole subtree is one binomial coefficient, C(cards left, draws
left).

### Pruning

A pattern is dropped once it needs more cards than are left to draw, or more
cards for one requirement than the remaining classes can supply. Both only
get worse deeper in the search, so dropped patterns stay dropped. When no
pattern is left, the subtree counts 0.

"Cards still needed" is a lower bound. If a pattern's requirements overlap,
one card can count towards several of them. The bound is therefore the
largest total deficit over groups of requirements that no drawable card
counts twice towards (`_disjoint_groups`). The groups are computed only over
classes that are actually in the pool, so a kept Wild Card doesn't weaken the
bound.

### Memoization on a canonical key

Many draw orders reach the same position, so subtrees are memoized. The key
holds:

- the class index and the number of draws left;
- for each live pattern, its unmet requirements. Each one is identified by
  the *set of remaining classes* that count towards it (an id shared by every
  requirement with the same set) plus its deficit.

The key is a frozenset, so patterns left with the same remainder collapse into
one entry. Live patterns with identical remainders are also merged in the
search state itself, not just in the key, so the duplicates aren't carried
around and re-examined.

## Keeping the search small

### Class order: rank by rank

Classes are sorted by the first pattern they count towards, considering only
requirements on a **single rank**. Requirements spanning many ranks (a
suit's count, "a suited card outside the run") would otherwise pull every
card of a suit to the front. In practice the search sweeps the ranks in
order, so a straight's window is decided within a few classes and patterns
settle quickly instead of staying open across the whole deck.

### What the search has to remember

The memo key is effectively what the search must remember about the classes
it has passed. Its size multiplies across everything still open, so the
number of positions grows with how much has to be carried from one rank to
the next. Two consequences have shaped the code:

- **How a pattern is stated matters.** Four Fingers' Straight Flush used to
  require "at least 4 suited cards in all". That count spans every rank, so
  the search carried each suit's count (0–4) the whole way through. Saying
  "the 4th suited card is the run's 4th rank, a second card of a run rank, or
  a suited card outside the run" is the same condition, but leaves one bit
  per suit to carry: has a suited card shown up yet? See
  [jokers/four-fingers.md](jokers/four-fingers.md).
- **Suits that are linked multiply.** If patterns of different suits depend
  on each other's cards, the search must carry every suit's state at once.
  That's what splitting by suit (below) avoids.

### Patterns join the search late

A pattern joins the search state at the first class that counts towards it.
Before that, its status depends only on the kept cards and the draws left, so
it stays out of the state and the memo key. A precomputed table says which
groups those waiting patterns could still cover for a given number of draws
left. Without this, every future straight window would sit in every memo key
from the first card on.

### Cheap per-position work

- Only patterns that the current class counts towards are re-assessed when
  moving past it.
- A pattern being satisfied is noticed when it's assessed, so positions
  never rescan the whole state.
- Each pattern's supply and future-class tables are indexed by how many of
  *its own* classes are behind (found with `bisect`), not by global class
  index. Building them is O(total memberships) rather than
  patterns × classes, which matters for targets with thousands of patterns
  (Four Fingers' Flush House has 1,872).
- `assess` results are cached by (pattern, position among its classes, its
  counts), since the same local situation recurs across many positions.
- The search checks the `cancel` event at every new position, so the
  background worker can abandon it as soon as the game state changes.

## Splitting by suit

When a target has patterns of two or more suits, `_union_by_suit` uses
inclusion–exclusion instead of one search over everything.

- Let A_s = "a non-suit pattern (`rest`, e.g. Full House) or one of suit
  s's patterns". The target is the union of the A_s, and the intersection of
  the A_s over a set of suits S is "rest, or a pattern of *every* suit in S".
- **Single-suit terms are small.** Only suit s's cards are told apart (plus
  whatever `rest` needs); the other suits merge into one class per rank.
- **Multi-suit terms are cheap or skipped.** A pattern of suit s needs at
  least x cards of that suit (4 for a Four Fingers flush hand, 5 otherwise),
  so a term with several suits needs that many of *each*.
  - If even that is impossible, the term equals the `rest`-only term. That
    term is computed once, and supersets of an impossible set are impossible
    too.
  - Otherwise the combined count is a **constraint** that prunes the search.
    It is implied by the patterns, so it never changes a position's count.
    It is kept out of the memo key, so positions that differ only in suit
    counts share an entry.
- `_favourable` handles "every group covered" with a bitmask per pattern
  (`rest` patterns cover all groups). A plain OR is the one-group case.

**It's skipped when an unseen card is Wild.** An undrawn Wild Card counts as
every suit, so the constraint barely prunes. The all-suit term is then as
hard as the original problem, and splitting is slower. Kept Wild Cards are
fine, because their contribution is already fixed.

## Pruning targets before the search

`combine()` drops patterns that imply another pattern in the same OR
(`_minimal`). Requirements are separate counts, so pattern a implies
pattern b when, for every requirement of b, a guarantees enough cards in its
keys. For each
requirement of b, `_at_least` sums a's requirements that lie within its keys
and share no class. This lets Flush absorb every Four Fingers flush hand: 4
suited cards of any shape make a Four Fingers Flush. Among equivalent
patterns the first is kept. Results are cached per (hands, rules), so this
cost is paid once per process.

## Approaches that didn't pay off

Recorded so they aren't retried blindly:

- **Multi-suit terms as product patterns** (every pair of suit-s and suit-t
  patterns ANDed into one): the pattern count squares, and the lower bound
  is weak. Much slower than the bitmask groups.
- **Appending the other suits' count to every pattern** instead of one
  shared constraint: every card of one suit then touches every pattern of
  the others, so everything is re-assessed at every class.
- **Constraint state in the memo key:** splits positions by exact suit
  counts for no benefit.
- **Splitting by suit with unseen Wild Cards:** 3–10× slower than one search
  in the benchmarks.
- **Trying one search first, with a size budget, before splitting:** once
  the split's setup was cheap, it was never meaningfully slower than one
  search, so the budget only added complexity.
- **Ordering classes by any requirement:** a requirement spanning many ranks
  pulled whole suits to the front. That turned the rank sweep into a suit
  sweep that has to remember every rank seen.

## Ideas not yet tried

- **Pausing the garbage collector during a search.** Big searches create
  millions of tuples and frozensets, and Python's cyclic collector keeps
  traversing them. Disabling it took Four Fingers' 8-unknown Straight Flush
  from 2.55 s to 2.17 s (about 15%). The memo holds no reference cycles, but
  `gc.disable()` affects the whole process, so it needs care with the
  background worker.
- **Conditioning on drawn Wild Cards**, so that unseen Wild Cards don't stop
  the split by suit. See [jokers/four-fingers.md](jokers/four-fingers.md).

## When adding a joker rule

1. Write its patterns so that little has to be remembered between ranks
   (see above), and check them against the brute-force evaluator in
   `tests/test_engine.py`.
2. Add scenarios to `tests/perf/scenarios.py` (a new `JOKERS` entry is
   usually enough) and run the benchmark (see [README.md](README.md)).
3. If something is slow, count memo entries per class index to see where
   positions pile up, and look at a sample of keys to see what's being
   remembered. That is how both Four Fingers problems were found.
4. Write up what you found in `jokers/<joker>.md`.

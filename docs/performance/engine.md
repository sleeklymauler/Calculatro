# How the probability engine stays fast

`calculatro/engine/` computes **exact** odds (a `Fraction`), not Monte Carlo
estimates. The work is counting: of all the ways to draw the unknown cards,
how many give a final hand that contains the target? A full deck with 8
unknown cards has C(52, 8) ≈ 750 million draws, so the engine can never list
draws one by one. Every technique below exists to count large groups of
draws at once, or to avoid looking at them at all.

Joker-specific notes are in [jokers/](jokers/), and how to measure in
[README.md](README.md).

Most of what's below came from two passes over the slow cases. The second
pass made Four Fingers' and Shortcut's slow cases 2-20x faster. Step by
step, on the standard deck's The House with Four Fingers + Shortcut and
Straight Flush (single runs during development): 7.2 s at the start; 7.1 s
with the bound on cards needed worked out over the classes still ahead;
6.1 s with parts implied by a one-card part dropped; 3.4 s with classes
sorted by card, rank by rank; 1.75 s with interned parts and cached
transitions; 1.49 s with only one-card parts used for that, and suit by suit
when suits are independent; 0.30 s with equal terms of interchangeable suits
computed once. The partial-state reductions after it took another 20-27% off
the slowest searches (those with unseen Wild Cards), and 21% off the sum of
all scenarios. A few searches of 1-5 ms each got up to 1 ms slower from the
extra setup per pattern, which wasn't worth chasing.

## The model

- **Targets are patterns.** `hands.py` turns each hand type into an OR of
  patterns. Each pattern is an AND of requirements, and a requirement means
  "at least `need` cards whose class is in `keys`". A class is
  `(rank_id, suit)`, with suit `"Wild"` for Wild Cards. Each requirement is
  counted on its own: a card counts towards every requirement whose keys it
  matches.
- **Unknown cards are one draw.** Kept face-down cards are treated as drawn
  from the unseen pool (`unseen`: undrawn deck + face-down cards in hand or discarded). By symmetry the
  distribution is the same, so "unknown cards" = face-down cards kept + cards
  drawn after the discard.
- **Except under The Mark.** Its face-down cards are known to be face cards,
  so the unknown cards aren't one uniform draw. Given how many of them are
  face cards, though, they are a uniform draw of that many face cards and
  one of the other cards (`_mark_split`). The face cards then go in classes
  of their own and the search tracks how many are still to be drawn. A
  single search covers every face-card count, weighted by its probability:
  the counts share the memo, since drawing a face card when 3 are left
  reaches the same position as drawing another card when 2 are left. That
  makes it 1-1.5x the time of the same cards without The Mark, against
  2-2.5x for a separate search per count.
- **Windows of face-down cards in a sort work the same way.** A run of
  latent cards between face-up ones in a sort is a uniform choice of the
  cards between them (see the [probability model](../probability-model.md)),
  so a draw's weight depends on how many of each window's candidates it has
  (`_window_split`). Each window is a mark of its own: a class is its cards'
  membership and their mark (-1 for none), and the search tracks how many of
  each mark are still to be drawn, packed into one int (`_Layout.bases`; The
  Mark is the case of one mark). With one mark the search runs as before.
- **Cerulean Bell averages searches.** After a discard it forces a random
  card of the final hand, so the odds are an average over which card that
  is: one search per kept card, and one per unseen card, each with the
  target's patterns rewritten for that forced card (`forced_patterns`).
  Unseen cards that the rewritten patterns can't tell apart share a search
  (`_forced_group`): 4 suits for Flush, 13 ranks for Full House, but every
  card for Straight Flush, so a plain deck needs about 47 searches of 4
  draws each. That makes Four Fingers' Straight Flush 12-17x slower than
  without the boss (1.3 s with Shortcut); most targets stay under 50 ms.
  Keeping the hand, the forced card is known and it's one search.
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
largest total deficit over groups of requirements that no card still to come
counts twice towards (`_bound`). The groups are worked out again as the
search moves on, over the classes still ahead only. Four Fingers' "any card
of the run's 4th rank" and "a suited card of another rank" overlap on the
suited card of that rank, but once that class is behind they can't share a
card, and the pattern needs one more card than the first grouping said. This
matters most with unseen Wild Cards, which count towards most of a suit's
requirements: on the wild deck it cut positions by about 30%.

### Memoization on a canonical key

Many draw orders reach the same position, so subtrees are memoized. The
search doesn't follow patterns as such but **parts**: a part is what a
pattern still needs, as its unmet requirements, each a bitmask of the
classes still ahead that count towards it plus a deficit (and, when
splitting by suit, the groups the pattern covers). Patterns left needing the
same thing share a part. Parts are interned as small ints, so the memo key
is cheap to build and hash. It holds:

- the class index, the number of draws left and the groups still to cover;
- the frozenset of live parts;
- the deferred counts (see "Patterns join the search late").

Moving a part past a class is a table lookup, `transitions[i][k][part]`,
filled in the first time it's needed: requirements that class i counts
towards lose it from their bitmask and drop by the k cards drawn.

### Dropping implied parts

Two live parts are often such that one implies the other: needing "the 9♥
and the J♥" implies needing "the 9♥", and needing "the Wild 9" implies
needing "the 9♥ or the Wild 9". In an OR, dropping the part that implies
another changes nothing, and it lets more positions share a memo entry.
Checking every pair of parts costs more than it saves, so only parts that
need **one card** from some classes are used: any other part with a
requirement whose classes all lie among those (and that covers no other
group) is dropped. That finds most of what checking every pair would (on
the wild deck with 6 unknown cards and Four Fingers + Shortcut, 60k
positions instead of 55k with every pair and 86k with none). The check only
runs when the memo lookup misses, and the count is stored under both the
full key and the reduced one.

For a plain OR (one coverage group), all one-card parts are first merged
into one: "a card from A or a card from B" is exactly "a card from A ∪ B".
Equivalent sets of outs then share a memo entry even when they arose from
different patterns. The union also absorbs a part whose requirement lies
within A ∪ B but not within A or B alone. This is one bitmask union followed
by the existing implication check, not an all-pairs comparison. Parts from
different coverage groups are kept separate.

## Keeping the search small

### Class order: rank by rank, or suit by suit

Classes are sorted by their cards: rank by rank (Ace high), with Wild Cards
first in each rank. A straight's window, or a set of a kind, is then decided
within a few classes, and patterns settle quickly instead of staying open
across the whole deck.

When no requirement spans more than one suit (Wild Cards aside), the suits
don't interact: a plain Straight Flush, Flush or Flush House. The classes
then go suit by suit (Wild Cards first, since they count towards every
suit), so the search only remembers the current suit's progress. Rank by
rank, it would carry all four suits' partial runs at once: the wild deck's
Shortcut Straight Flush takes twice as long that way.

Before the second pass, the order came from the patterns: classes were
sorted by the first pattern they count towards through a single-rank
requirement, so it depended on the order `hands.py` listed patterns in. In
the two-suit terms of the split by suit, one suit's patterns came before the
other's, and the search swept spades several ranks ahead of hearts and left
the 2♥ and A♥ to the very end. Sorting by card halved the positions of
those terms.

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

A pattern joins the search state at the first class that counts towards one
of its **single-rank** requirements (or towards it at all, if it has none).
Before that, it stays out of the state and the memo key. A precomputed table
says which groups those waiting patterns could still cover for a given
number of draws left. Without this, every future straight window would sit
in every memo key from the first card on.

A waiting pattern can still have requirements spanning many ranks that
earlier classes count towards, such as Four Fingers' "a suited card of
another rank". Its state then also depends on how many cards were drawn from
those classes. Those counts are deferred: the search keeps a running total
per set of classes, the memo key holds each one (capped at the most any
waiting pattern needs), and a pattern's counts are filled in from them when
it joins. Waiting patterns usually share these sets: for every run above the
current rank, it's "the suit's cards so far". So this costs one bit per suit,
where joining at the first class put every run of the suit into the state
with its own copy of the requirement. While a pattern waits, the coverage
table uses a lower bound on the cards it needs (its deferred requirements
counted as met), so it never prunes wrongly. See
[jokers/shortcut.md](jokers/shortcut.md).

Which deferred counts the memo key holds depends on the **draws left**
as well as the class. A waiting pattern whose lower bound already exceeds
the draws left can never be satisfied, so its deferred counts can't affect
anything that follows: they're left out of the memo key, and each shared
count is capped at the most needed by the waiting patterns still in reach.

The cache of joining parts (see "Cheap per-position work") is keyed by the
draws left too. It leaves out the out-of-reach patterns before reading
their counts, which the key no longer holds, and checks each joined part's
bound before moving it, so parts that would die at the next position are
never moved.

### Cheap per-position work

Before the partial-state reductions, a position cost 6–25 µs of Python on
the benchmark machine, more when
more patterns join and move at each class (Four Fingers alone 6–10 µs, with
Shortcut 11–15 µs, with Shortcut and Smeared 19–23 µs), and the largest
search in the benchmarks visited about 1.2 million positions. So this is
where the second pass gained the most after the class order (about 2×).

- Only live parts that the current class counts towards (one bit test) are
  moved past it; the others are passed on as they are. Patterns that were
  dropped long ago cost nothing, though a class can count towards thousands
  of them.
- A part's bound on the cards it needs, and whether it needs just one card,
  are worked out once, when it's interned.
- A part being satisfied is noticed when it's moved, so positions never
  rescan the whole state.
- Patterns join with a part built from the kept cards and the deferred
  counts (`join`, cached). The list of parts joining at a class is cached
  too, by the class, draws left, the groups to cover and the deferred counts as the
  memo key caps them; with Four Fingers + Shortcut a couple of dozen
  patterns join per position, and this saved 20–30%. (Tried early on, it
  gained nothing measurable; it paid off once that many patterns joined, see
  [jokers/smeared.md](jokers/smeared.md).)
- With one group (no split by suit), a part is live just when it needs no
  more cards than are left, so live, moving and still parts are built with
  comprehensions, and one-card parts are found by set intersection.
- Each pattern's supply and future-class tables are indexed by how many
  of *its own* classes are behind, which matters for targets with
  thousands of patterns (Four Fingers' Flush House has 1,872).
- The search checks the `cancel` event at every new position, so the
  background worker can abandon it as soon as the game state changes.

### Garbage collection

A big search creates millions of tuples and frozensets, and Python's cyclic
garbage collector keeps traversing them. `probability()` pauses the
collector while it runs (`gc_paused`), as does each of the app's jobs for the
whole job (`worker.Job`, see [the architecture](../architecture.md)), which saves 5–8% on the largest
searches (5–15% when it was added, before the Smeared Joker work) for a little more peak memory. The
collector is process-wide, and a cancelled search can still be unwinding
in the background worker when the next one starts. So running searches
are counted under a lock, and the last one to finish restores the
collector's previous state. `ways` refers to itself through its closure, so
each search's frame is a reference cycle; `_favourable` clears its memo and
part tables on the way out so that the cycle holds little until the
collector runs again.

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

**Interchangeable suits.** If swapping any two suits maps the target's
patterns onto themselves (checked once per target, `_interchangeable`), two
terms whose suits have the same ranks kept and unseen are equal. Terms are
keyed by the sorted ranks of their suits and of the other suits, and each is
computed once. With a full deck and nothing known, as in The House's first
hand, that's 2 searches instead of 10 for Four Fingers' Straight Flush
(0.27 s instead of 1.34 s with Shortcut as well, measured with the check
turned off). It does
nothing once the suits differ, which is most of the time.

**It's skipped with only two suits.** That only happens with Smeared Joker,
whose two colours are the suits. The term with both of them carries what one
search over everything does, but can only stop once both colours have a
pattern, so one search is faster. See [jokers/smeared.md](jokers/smeared.md).

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

That once still matters: it's paid when a target is first tracked or a joker
changes the rules, and with Four Fingers and Shortcut the flush-type hands
have thousands of patterns (Straight Flush 1856, Flush House 1872).
Comparing every pair took up to 6 s (Straight Flush + Flush House). Since
`_at_least` only counts a's requirements within b's keys, a can only imply
b if every requirement of b has one of a's within it. `_minimal` keeps a
bitmask of the patterns with a requirement within each set of keys, and
compares b only with the patterns common to all of its requirements. The
result is the same, and the worst combination now takes about 40 ms. The
"nothing cached" benchmark scenarios time this.

The build isn't cancellable. Instead, `Target.patterns` builds one at a time
behind a lock: a calculation that needs patterns another is building (often
one just cancelled for a newer state) waits for them rather than building
them again in parallel. Without that, each new selection during a slow
build started another copy, and the copies shared the GIL.

## Many discards at once

The best-discard search calls the engine for up to hundreds of discards of
the same hand, which all draw from the same pool. With an `engine.Reuse`,
`_favourable`'s pool-dependent setup (`_layout`), its results and its memo
are shared between those calls; see [best-discard.md](best-discard.md).
Ordinary odds rows don't pass one, and build a fresh layout each time.

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
  in the benchmarks, and still about 3× slower when re-tested after the
  second pass.
- **Trying one search first, with a size budget, before splitting:** once
  the split's setup was cheap, it was never meaningfully slower than one
  search, so the budget only added complexity.
- **Ordering classes by any requirement:** a requirement spanning many ranks
  pulled whole suits to the front. That turned the rank sweep into a suit
  sweep that has to remember every rank seen. The same thing happens if
  patterns that cross suits are listed suit by suit; see
  [jokers/shortcut.md](jokers/shortcut.md).
- **Dropping dominated live patterns** (those whose unmet requirements are a
  superset of another live pattern's): about 30% fewer live patterns, but
  barely fewer positions, and the subset checks made it slower.
- **Conditioning on drawn Wild Cards**, so that unseen Wild Cards don't stop
  the split by suit: for each way of drawing them, move them into the kept
  cards and split the rest by suit. With Wild Cards kept, terms with 3 and 4
  suits become possible. Only The House with Four Fingers and Shortcut got
  faster (by about 5%), while small cases were 2–4× slower and some 20×.
- **Listing two-suit terms' patterns in the target's order** (rank by rank,
  instead of one suit's then the other's), with the old pattern-based class
  order: slower (66k positions instead of 48k), because cards that only
  count towards "any card of the run's 4th rank" were placed by
  tie-breaks. Sorting classes by card fixed both.
- **Checking every pair of live parts for implication:** about 1.5× fewer
  positions, but 3–6× slower overall.
- **Dropping parts implied by parts that need several cards** (Full House's
  "2 more 7s"): few parts dropped, and Full House + Straight Flush took
  twice as long. A looser test for one-card parts (a requirement for d cards
  of which fewer than d can lie outside the one-card part's classes) gained
  nothing either.
- **Closed forms at the end of the search**: when every live part needs one
  card, count the draws that miss them all; with one card left to draw,
  count the cards that satisfy some part. Waiting patterns almost always
  rule it out, and it saved about 3%.
- **Dropping every live part that implies another** (checked per
  requirement, at each memo miss): 7% fewer positions, 4× slower.

## Ideas not yet tried

- **Unseen Wild Cards.** They stop the split by suit, so Four Fingers with
  Shortcut still takes seconds from 7 unknown cards on the wild deck, and
  more than 10 s for The House. Conditioning on the Wild Cards drawn has been
  tried twice; something that keeps the suits apart without making 3- and
  4-suit terms possible would be needed.
- **Further canonicalization of partial states.** Merging one-card outs
  and forgetting unreachable patterns' deferred counts made the largest
  searches 20–25% faster. More reductions must remain cheaper than the
  searches they avoid; general all-pairs implication checks still cost too much.

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

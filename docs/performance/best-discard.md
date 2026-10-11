# Best-discard search

How `search.best_discards` (the panel's search button) is kept fast. It
compares keeping the hand with every discard the game allows, so one click
costs up to a few hundred odds calculations: 219 choices for an 8-card hand
whose cards are all different to the target, 638 for 10 cards. Each is an
`evaluate_discard` call like an odds row (see [engine.md](engine.md)), so the
work is in calling the engine less often and sharing what the calls have in
common. The search runs in its own job and a new hand cancels it (see
`CLAUDE.md`), so a slow search never freezes the app; the button just stays
dimmed until it's done.

## Fewer calculations

### Groups of hand cards

Unchanged from the first version: hand cards that count towards the same
requirements of the target are interchangeable, so the search enumerates how
many to discard from each group (`_groups`) rather than which cards. A hand
with a pair or a few cards that count towards nothing has far fewer choices.

### Relabelling ranks and suits

`_relabelled` gives each discard a key that's equal for two discards only if
their odds are, and each key is worked out once. Two things make discards
equal:

- **Labels the target can't see.** Full House doesn't look at suits and
  Flush doesn't look at ranks (every requirement holding one card of a rank
  holds every suit of it, or the other way round), so those are dropped
  from the cards' classes.
- **Symmetries of the patterns.** If swapping any two ranks maps the
  target's patterns onto themselves (the "of a kind" hands, and Flush), the
  odds only depend on what's kept and unseen of each rank, not on which rank
  it is: throwing away a 7 is as good as throwing away an 8 when as many of
  each are unseen. The same goes for suits (Flush, Straight Flush). The key
  is then the sorted list of (kept, unseen) per rank, or per suit. Swaps of
  adjacent labels generate every relabelling, so checking those is enough.

Ranks are used if they're symmetric, else suits, never both, since sorting
by one breaks the other. The Mark draws face cards apart, so there face and
other ranks are only swapped among themselves, and ranks are never dropped.
Cerulean Bell's forced card makes the exact class matter, so it gets no
relabelling (it already groups by class).

On the 8-card benchmark hand this takes the "of a kind" hands from 219
calculations to 6 (one per number of cards discarded), and Flush from 58 to
38.

### Bounds for "Any target"

A union of hands is at least as likely as each of them and at most as likely
as their sum (or 1). Each hand alone is much cheaper to work out than all of
them together: the union is split by suit with the other hands' patterns in
every term (see [engine.md](engine.md#splitting-by-suit)). So the search
first works out each hand's odds for every choice, then takes the choices by
that upper bound, highest first, and stops as soon as the bound is below the
best exact odds found (or below the best single hand's odds anywhere). A
choice whose bounds meet (one hand alone is certain, say) needs no exact
calculation either. Ties must be exact, so only bounds strictly below are
cut.

How much this saves depends on how likely the hands are. Without jokers,
Flush + Straight + Full House on 8 cards needs the exact odds of a few
choices out of 219. With Four Fingers and Shortcut the benchmark hands
already hold a Straight, so the best odds are 1, and over 100 choices have a
sum of at least 1 that has to be checked exactly.

## Sharing the engine's work: `engine.Reuse`

Every discard draws from the same unseen cards, so a lot of each engine
search is the same from one discard to the next. `best_discards` passes one
`Reuse` to every `evaluate_discard` call, and the engine keeps three things
in it:

- **Layouts.** `_favourable` was split into `_layout` (everything that
  depends only on the patterns and the pool: classes, per-pattern tables,
  deferred sets, and the interned parts with their transition tables) and
  `_search` (the kept cards' contributions, the starting state, and the
  recursion). Building the layout was most of the cost for many-pattern
  targets like Four Fingers' Straight Flush (about half the time); with
  `Reuse` it's built once
  per pattern set and pool. Without `Reuse` (ordinary odds rows) a fresh
  layout is built and dropped as before.
- **Results.** A `_favourable` count depends on the kept cards only through
  how many count towards each requirement (`kept_have`), so the count is kept
  by layout, `kept_have`, draws and face weights. Inclusion-exclusion terms
  for one suit don't see the other suits' cards, so they repeat a lot across
  discards.
- **A shared memo.** The recursion's memo entries depend on the kept cards
  only through the patterns that haven't joined the search yet at that class
  (the others' are in their parts): through each one's deficits, or none if
  it can't be satisfied. `_search` gives each class a `signature` standing
  for that and the number of draws, puts it in the memo key in place of the
  class index, and shares the memo between every search with the layout.
  Discards that differ only in cards below a class share everything from it
  on. It took "Any target" without jokers on 8 cards from 1.5 s to 1.1 s
  (before the bounds below were added), but saved only about 10% on Four
  Fingers + Shortcut's Straight Flush, whose runs span too many ranks.

`_union_by_suit`'s per-suit minimum card counts were also being worked out
for every discard. They're only bounds used for pruning, and they stay valid
if computed for more cards than can be present, so with `Reuse` they're
computed once from the whole hand plus the pool (`Reuse.cards`). The
pruning is as strong as before on the benchmarks: the same terms are
computed.

Everything kept in a `Reuse` lives as long as one search, so memory doesn't
build up across clicks.

## Results

Scenarios "Best discard: ..." in `tests/perf/scenarios.py`: the 8-card
benchmark hand (7♥ 8♥ 9♠ Q♣ K♦ 3♠ 5♦ J♥), the same with two pairs
(7♥ 7♠ 9♠ Q♣ Q♦ 3♠ 5♦ J♥), a 10-card hand (adding 2♣ 10♦), and the 8-card
hand with 2 cards face down, under Cerulean Bell, and under The Hook with no
discards left (so the discard is a played hand). Each result is the best
odds plus the number of tied options and the first one, so the regression
tests check the options too.

Before is the first version (commit `6b93aac`), measured the same day with
`--repeat 3 --budget 20` (so the slowest rows ran once or twice). Both give
the same results in every scenario.

| Hand | Target | No jokers | Four Fingers + Shortcut | Smeared |
|---|---|---|---|---|
| 8 cards | Pair | 67 ms → 6.2 ms | 70 ms → 6.3 ms | 111 ms → 6.3 ms |
| 8 cards | Two Pair | 234 ms → 13 ms | 244 ms → 13 ms | 389 ms → 14 ms |
| 8 cards | Straight | 98 ms → 40 ms | 300 ms → 112 ms | 148 ms → 43 ms |
| 8 cards | Flush | 33 ms → 9.4 ms | 47 ms → 12 ms | 2.3 ms → 1.8 ms |
| 8 cards | Full House | 358 ms → 21 ms | 368 ms → 20 ms | 582 ms → 20 ms |
| 8 cards | Straight Flush | 294 ms → 46 ms | 8.83 s → 2.56 s | 227 ms → 44 ms |
| 8 cards | Flush + Straight + Full House (any) | 2.86 s → 86 ms | 5.49 s → 2.25 s | 1.53 s → 93 ms |
| 8 cards, two pairs | Pair | 26 ms → 7.8 ms | 27 ms → 6.6 ms | 42 ms → 6.9 ms |
| 8 cards, two pairs | Two Pair | 118 ms → 16 ms | 121 ms → 17 ms | 191 ms → 18 ms |
| 8 cards, two pairs | Straight | 56 ms → 18 ms | 172 ms → 56 ms | 82 ms → 19 ms |
| 8 cards, two pairs | Flush | 35 ms → 9.3 ms | 45 ms → 13 ms | 3.2 ms → 1.2 ms |
| 8 cards, two pairs | Full House | 196 ms → 26 ms | 195 ms → 28 ms | 304 ms → 26 ms |
| 8 cards, two pairs | Straight Flush | 291 ms → 46 ms | 8.84 s → 2.03 s | 226 ms → 47 ms |
| 8 cards, two pairs | Flush + Straight + Full House (any) | 3.37 s → 94 ms | 5.58 s → 1.66 s | 1.29 s → 106 ms |
| 10 cards | Pair | 204 ms → 20 ms | 206 ms → 19 ms | 222 ms → 19 ms |
| 10 cards | Two Pair | 755 ms → 25 ms | 775 ms → 26 ms | 996 ms → 27 ms |
| 10 cards | Straight | 326 ms → 115 ms | 671 ms → 190 ms | 348 ms → 121 ms |
| 10 cards | Flush | 62 ms → 10 ms | 91 ms → 15 ms | 4.4 ms → 3.5 ms |
| 10 cards | Full House | 1.39 s → 33 ms | 1.22 s → 33 ms | 1.56 s → 36 ms |
| 10 cards | Straight Flush | 1.60 s → 140 ms | 54.91 s → 15.14 s | 604 ms → 152 ms |
| 10 cards | Flush + Straight + Full House (any) | 16.83 s → 236 ms | 24.49 s → 7.07 s | 2.83 s → 176 ms |
| 8 cards, 2 face-down | Flush | 89 ms → 12 ms | - | - |
| 8 cards, 2 face-down | Full House | 382 ms → 22 ms | - | - |
| 8 cards, 2 face-down | Straight Flush | 373 ms → 44 ms | - | - |
| 8 cards, 2 face-down | Flush + Straight + Full House (any) | 4.86 s → 92 ms | - | - |
| 8 cards, Cerulean Bell | Flush | 70 ms → 18 ms | - | - |
| 8 cards, Cerulean Bell | Full House | 932 ms → 290 ms | - | - |
| 8 cards, Cerulean Bell | Straight Flush | 564 ms → 89 ms | - | - |
| 8 cards, Cerulean Bell | Flush + Straight + Full House (any) | 5.15 s → 453 ms | - | - |
| 8 cards, The Hook, played | Flush | 384 ms → 61 ms | - | - |
| 8 cards, The Hook, played | Full House | 3.57 s → 50 ms | - | - |
| 8 cards, The Hook, played | Straight Flush | 3.24 s → 329 ms | - | - |
| 8 cards, The Hook, played | Flush + Straight + Full House (any) | 52.04 s → 470 ms | - | - |

### What's still slow

- **Four Fingers + Shortcut with Straight Flush**: 2.0–2.6 s on 8 cards, about
  15 s on 10. Every hand card counts towards some run of every suit ("any
  card that completes the run"), so no two discards look alike to the
  per-suit terms, and each of about 200 choices costs four 3 ms searches.
  Four Fingers alone is under 1 s.
- **"Any target" with Four Fingers + Shortcut** when the hand already holds
  one of the hands: the sum bound is 1 for most choices, so they all need
  exact odds (1.7–2.3 s on 8 cards, 7 s on 10).

Ideas not yet tried:

- Proving cheaply that a choice's odds are below 1 (a single draw that makes
  none of the hands), which would cut the second case.
- Ordering by a cheap estimate and bounding single targets too, e.g.
  Straight Flush by the per-suit terms of the choices already computed.

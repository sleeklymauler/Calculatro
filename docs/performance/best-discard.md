# Best-discard search

How `search.best_discards` (the panel's search button) is kept fast. It
compares keeping the hand with every discard the game allows, so one click
costs up to a few hundred odds calculations: 219 choices for an 8-card hand
whose cards are all different to the target, 638 for 10 cards. Each is an
`evaluate_discard` call like an odds row (see [engine.md](engine.md)), so the
work is in calling the engine less often and sharing what the calls have in
common. The search runs in its own job and a new hand cancels it (see
[panel.md](../panel.md#best-discard-button)), so a slow search never freezes
the app; the button just stays dimmed until it's done. What the search
compares is in
[probability-model.md](../probability-model.md#best-discard).

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
Cerulean Bell's forced card makes the exact class matter when it can change
whether the target is played (`hands.bell_matters`: some pattern needs all 5
cards), so then it gets no relabelling (it already groups by class). For
smaller hands (Pair to Four of a Kind) the engine ignores the force, and the
search relabels as without it.

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
sum of at least 1. The next section is what saves those.

### A draw that misses

Once odds of 1 are known (from a hand certain alone, or from an exact
calculation), a choice only matters if it's also at 1, and a single draw that
makes none of the target's hands shows it isn't. `engine.can_miss` tries up to
200 random draws for one, which is much quicker than the exact odds. Every
choice below 1 on the random 8- and 11-card hands measured was ruled out this way, none needing
over 200 tries. It never changes a result: if no miss turns up, the search
works out the exact odds as it would have. The random draws only decide how
quickly a miss is found, and are seeded so that a search takes the same path
each time. It also covers single hands, where it takes Straight from 120 ms to
36 ms on 10 cards.

A miss only proves the odds are below 1 if the draw could really happen, so
`can_miss` draws final hands as estimates do (`engine.sampling._final_hands`, which
models The Mark's split, The Hook, DNA and Crimson Heart as the exact odds do).
It gives up (and the exact odds are worked out) under Cerulean Bell, where each
forced card's patterns take a while to build. It tests the kept hands first,
since a choice that already holds the target can't miss, and sampling for it
would be wasted.

The check costs a little where it can't help: choices whose odds were already
known through relabelling now pay for it too. "10 cards, Flush" with Smeared
went from 4 ms to 14 ms, and a few other single hands by under 10 ms.

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
  it can't be satisfied. `_class_signatures` gives each class a signature standing
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
(7♥ 7♠ 9♠ Q♣ Q♦ 3♠ 5♦ J♥), a 10-card hand (adding 2♣ 10♦), two 11-card
hands (one holding a Full House, one holding no target), and the 8-card
hand with 2 cards face down, under Cerulean Bell, and under The Hook with no
discards left (so the discard is a played hand). Each result is the best
odds plus the number of tied options and the first one, so the regression
tests check the options too.

The work on this page made most scenarios 3-30x faster than the first
version of the search, with the same results. The gains were largest for
"Any target" and under The Hook (a played hand), where most choices are now
bounded or grouped rather than calculated: The Hook's "Any target" went from
52 s to under 0.5 s. They were smallest for Four Fingers + Shortcut with
Straight Flush (about 3.5x; see below) and for targets that were already
quick, such as Flush with Smeared Joker. A draw that misses (`can_miss`)
took the 11-card hand holding a Full House from 3.5 s to about 0.5 s.

### What's still slow

- **Four Fingers + Shortcut with Straight Flush**: seconds on 8 cards, and
  over 10 s on 10. Every hand card counts towards some run of every suit ("any
  card that completes the run"), so no two discards look alike to the
  per-suit terms, and each of about 200 choices costs four 3 ms searches.
  Four Fingers alone is under 1 s.
- **"Any target" on a big hand whose best odds are below 1**: several
  seconds on 11-card hands (the scenario "11 cards, holding no target"). The
  sum bound is 1 for over 400 of about 1000 choices, a miss proves nothing since the best is below 1 too, and each exact union
  costs about 10 ms, so that's almost all the time. Four Fingers + Shortcut on
  8 cards is the same (about 2 s, 185 choices of 219).
- **Crimson Heart with no discards left**, where the selection is played and
  the boss then swaps which joker is debuffed (`engine.rules_after`): each
  odds value averages over the rules left by the swap, and the search can
  only treat cards as alike under all of them (`search._rule_sets`). Rule sets
  the target can't tell apart are worked out once (`engine.discarding._merged`; Shortcut
  for a Flush, say), and in a search `Reuse` shares the rest, so the averaging
  itself costs little (the "Crimson Heart, played" scenarios: 17–210 ms on
  8 cards outside Straight Flush and "Any target"). The cost is that the swap
  can turn on Four Fingers beside Shortcut, the first case above: with Shortcut
  on and Four Fingers debuffed, Straight Flush takes 3.7 s, against 0.1 s
  without the boss, where Four Fingers stays off.

None of these reach the search's limit (`Limits.search`, 30 s) on the
development machine. A search that does answers from estimates of the choices
that could still be best (`search.estimated_best`, in about 5 s), marked: see
[probability-model.md](../probability-model.md#best-discard).
`benchmark.py --estimates -k "Best discard"` gives each scenario's search up as
soon as it has its choices, the worst case, and says how far below the best
its discard is: on 2026-11-11, 100 of the 107 scenarios found the best discard, and the rest were at most 0.32 points below it (0.01 on average), each in at most 5 s.

Ideas not yet tried:

- Ordering by a cheap estimate and bounding single targets too, e.g.
  Straight Flush by the per-suit terms of the choices already computed.

Tried for "Any target" with best odds below 1, and dropped:

- **Tighter upper bounds from pairs of hands.** The Hunter-Worsley bound using
  every pair leaves only about 7 of 422 exact unions, but the pairs cost as much
  as the unions they save (Flush + Full House alone took 3.4 s for all the choices). Using only the cheap pairs,
  P(Flush or Straight) + P(Straight or Full House) - P(Straight), took 11 cards
  from 4.5 s to 3.3 s but 10 cards from 450 ms to 675 ms. P(Flush) + P(Straight
  or Full House), the cheapest, barely cuts any (422 to 356).

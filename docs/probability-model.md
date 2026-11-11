# The probability model

What the app calculates and the rules it follows. How it's kept fast is in
[performance/](performance/README.md): read [engine.md](performance/engine.md) (design notes),
`performance/jokers/` (per-joker notes) and [benchmarks.md](performance/benchmarks.md) before
modifying the engine, and add benchmark scenarios to `tests/perf/scenarios.py` for new joker rules.

## Hand patterns

`hands.py` expresses each poker-hand target as OR-of-patterns, each pattern an AND of `Requirement`s
("at least `need` cards whose class key is in `keys`"). Requirements are usually disjoint; they may
overlap (a card then counts towards each).

- Class keys are `(rank_id, suit)` with suit `"Wild"` for wild cards. With Smeared, Diamonds are keyed
  as Hearts and Clubs as Spades, and flush-type patterns use only those two suits (`flush_suits`).
- Containment follows Balatro's rules (Straight Flush contains Straight; Four of a Kind does *not*
  contain Two Pair).
- Stone/hidden cards have no key; a debuffed Wild loses wildness.
- `combine()` builds an "any of these targets" Target, pruning implied patterns.

## The search

`engine.probability()` is exact (`Fraction`), not Monte Carlo. Pool cards are merged into classes by
which requirements they count toward, then enumerated class-by-class hypergeometrically with
early-accept (satisfied ⇒ binomial of remaining), pruning of unreachable patterns, and memoization on
a canonical key of what the live patterns still lack. Only patterns touched by the current class are
re-assessed.

Targets with patterns of several suits are split by suit with inclusion-exclusion (`_union_by_suit`),
so each search tracks one or two suits instead of all four. This is skipped when an unseen card is
Wild, which links the suits again. Terms that are the same up to relabelling suits (same ranks kept
and unseen, e.g. a fresh deck in The House) are computed once. See the module docstring too.

### How a pattern is stated

How a pattern is stated drives the search's size: it memoizes on what each live pattern still needs,
so prefer requirements that leave little to remember. Four Fingers' Straight Flush says "a suited card
of another rank" rather than "4 suited cards in all", so the search tracks whether a suit has shown up
rather than its count, and runs sharing their 3 suited ranks are one pattern ("any card that completes
the run").

Classes are swept rank by rank (Wild Cards first in their rank), or suit by suit when no requirement
spans several suits; the order patterns are listed in doesn't matter. A pattern joins the search at
its first single-rank class; draws before that towards its other requirements are deferred counts in
the memo key. In the search, a pattern's state is an interned "part" (unmet requirements as bitmask of
remaining classes + deficit), moved past each class by a cached transition; parts implied by a
one-card part are dropped from the state.

## Card queries

`cards.py` parses card queries (`"2+ queens"`, `"at most 1 spade"`, `"QS"`, `"1-3 even of red suits"`,
`"not sealed"`; the editor writes them as `"at least 2 lucky queen of spades"`, `"between 1 and 3 not
sealed queen or king of hearts or diamonds"`) into `CardQuery` (`text` keeps what was parsed, the
odds' key).

Like targets (`engine.evaluate_discard`), these count the final hand, kept cards and draw
(`engine.evaluate_cards`: the kept matches plus a hypergeometric count over the face-down kept cards
and the draw, with The Mark's split and an average over The Hook's picks). A single query never goes
through the pattern search.

## Joker rules

Joker rules (Four Fingers, Shortcut, Smeared, Pareidolia) live in `rules.Rules`, built by
`GameState.rules` from non-debuffed jokers. Pareidolia affects only face-card queries
(`cards._is_face`: every non-debuffed card, Stone included), never hand patterns. A `Target` only
names hand types; `Target.patterns(rules)` builds (and caches) the patterns for a ruleset, and
`card_key`, `probability`, `count_probability` and `CardFilter.test` all take the `Rules`.

`docs/jokers.md` is the joker reference (all 150, with internal keys and the exact game rules for Four
Fingers / Shortcut / Smeared / Pareidolia). Use it instead of the wiki, which it corrects from the
game's code.

### DNA

A lone card played as the round's first hand (no discards left, so `evaluate_discard` predicts across
a play) comes back as `dna_copies` known cards instead of being replaced by a draw
(`engine._dna_copies`), so the odds normally equal Keep's. The mod counts the copies, following
Blueprint and Brainstorm chains (`jokers` hides the order; with a joker face down, only DNA itself
counts). See `docs/jokers.md`.

## Boss rules

`docs/blinds.md` is the same for all 30 blinds as `docs/jokers.md` is for the jokers (exported as
`blind` = `G.GAME.blind.name`, plus `blind_disabled`): which bosses the engine models, which arrive
through the exported state, and which it gets wrong. A new boss rule must check `blind_disabled` like
these:

- **The Serpent:** its draws (`GameState.serpent`).
- **The Mark:** its face-down face cards (`GameState.mark`, `engine._mark_split`).
- **Cerulean Bell:** its forced card (`GameState.bell`/`forced`, from the hand cards' exported `forced`
  flag; `hands.forced_patterns`, `engine._bell_after_draw`).
- **The Hook:** its 2 random cards thrown away after a play, which applies when the selection has to be
  played for want of discards (`GameState.hook`, `engine._hooked`/`_after_hook`).
- **Crimson Heart:** its joker swap after such a play, which changes the rules the final hand is judged
  under (`GameState.crimson`, `engine.rules_after`, `search._rule_sets`; run-out Perishables, which it
  can't swap, are exported as `perished`).
- **The Eye and The Mouth:** their banned hands don't change the odds. The mod strikes those targets
  out in the panel itself (`Calculatro.banned_hands`, read from `G.GAME.blind`).

## Face-down cards in the hand's sorts

The snapshot has every sort of the hand seen this round
(`GameState.observations`, [architecture.md](architecture.md#shapes)), a mode perhaps several times: a
draw re-sorts the hand, but an earlier sort is still true of the cards in it, so a card kept face down
while its neighbours were discarded is still between them. The mod drops only sorts that say nothing
more than the rest (`observe.lua`'s `prune`: one whose cards are in another of its mode in the same
order, or one with no face-down card and no two cards alike in print), which gives the same odds. A face-down card in one of them
is *latent* (`Card.latent`, its id there): its place says what it can be. Between a 9 and a 7 in a rank
sort it's a 7, 8 or 9, and of two side by side the left one is the higher. One discarded face down
after the sort stays in it (`ObservedCard.discarded`): it's out of play, but what it is still bounds
the cards around it, and says what's left to draw. The design and its reasoning are in
[the plan](plans/face-down-windows.md).

- **The posterior** (`posterior.py`). The prior deals the unseen cards to the latent ones uniformly at
  random (under The Mark, the unseen face cards, `flipped_by_mark`). The likelihood is 0 unless every
  sort is in the game's order: by printed rank then suit, or suit then rank, with Diamonds < Clubs <
  Hearts < Spades and Stones lowest. Cards alike in print (copies, or Stones) tie: the game orders them
  by hidden values, taken as random but the same in every sort. Each tie group gives a factor, the share
  of its orders every sort agrees with (`tie_factor`, a count of linear extensions). `summed` deals the
  latent cards one at a time, between their face-up neighbours, and sums the partial deals that the rest
  can't tell apart as it goes (the cards bounding the latent cards still to come, the cards they've used
  that those could be, open tie groups, and what the caller sees of them so far: its `label`). So a run of
  8 face-down cards, 10^8 deals, takes a fraction of a second. `reference` lists every deal one unseen
  card at a time, for the tests.
- **The odds** (`engine.evaluate_discard`, `evaluate_cards`). They're the average of today's odds over
  the branches, each with its latent cards turned up (`_resolved`: the card takes its place in hand, or
  one of the discard pile's `{hidden: true}` cards for one discarded before, and leaves `unseen`, so the
  deck size, hand size and indices don't change). Deals that turn up as many cards of each class, kept
  or gone (discarded now or before; nothing of those if nothing is drawn), and forced or not, give the
  same odds, so `summed` makes them one branch and they share one evaluation (`engine._averaged`,
  `_labels`, `_rule_classes`). A card's class there is what the target can tell of it, under every rule
  set the final hand may be judged under (`_counts_towards`: which requirements of the patterns it counts
  towards, as in `search._groups`; the class itself under Cerulean Bell), or for a card query whether it
  matches, and under The Mark its face status. So for Pair, deals that differ only in suits share one
  evaluation. Nothing is sampled or cut off. `can_miss` picks a branch for each try, so a draw it finds
  is one the evidence allows.
- **Windows** (`posterior.windows`, `engine._window_split`). Latent cards that depend on no others (not
  side by side with them in a sort, and unable to be cards alike in print) whose posterior is uniform
  over the sets of their candidates are a *window*: a run of face-down cards in one sort between face-up
  cards none can tie with, say, or one face-down card. If they're all kept or all gone, the odds don't go
  through them: the search weighs each draw by how many of each window's cards it has, as The Mark's
  split does for face cards (`_Layout.bases` packs a count for each window into one int). Kept, a draw with
  a of a window's w cards holds its r in C(a, r) ways; gone, it misses them in C(w - a, r). A window
  whose cards meet different fates, side by side in a sort, is split by the cards at the edges of its
  blocks (`engine._split`): given those, each block's other cards are a window of the cards between. Not
  under The Mark, The Hook, Cerulean Bell or DNA's copies of a face-down card, which branch as above.
- **No evidence needed.** A single sort with no face-up card (The House's first hand) only says which
  of its cards is higher, not what they are. So while they all meet the same fate (all kept, or all
  gone: thrown away now or discarded face down before), the odds are today's exactly
  (`_says_nothing_here`). A discard that splits them throws away the higher or the lower cards: it's
  split as a window (above), or its deals summed. So is keeping the rest after some were discarded, and
  DNA copying one is branched on.
- **Evidence set aside.** If no deal fits the sorts (a card changed while face down, say), the odds are
  today's, and it's logged once on stderr (`engine.posterior`). The search then treats the face-down
  cards as interchangeable again (`with_usable_evidence`).
- **DNA.** With 2 or more copies of a face-down card played alone, they're the same unknown card, not
  that many separate unseen ones: it's branched on too, with no evidence (`_posterior_for`).
- **The Mark** with the challenges' `flipped_cards` modifier can flip any card, which the Mark model
  doesn't allow, so the mod sends no sorts then. Selling Pareidolia, or changing a face-down card's rank,
  mid-round can leave a face-down card whose face status differs from when it was flipped. That isn't
  modelled.
- **Speed.** Measured in stage 5 of [the plan](plans/face-down-windows.md#performance). One or two
  face-down cards in sorts take milliseconds for a single target, or 0.3-2.2 s for Four Fingers + Shortcut
  with Straight Flush or "any", and the best-discard search 0.06-0.8 s, or 1.4-20 s for Four Fingers +
  Shortcut with Straight Flush or "any". The House's hand after its first discard, kept or with earlier
  sorts, takes under a second, and so does a discard that throws away the highest or the lowest cards of
  its first hand; one that splits it in the middle takes 1-10 s. What's still slow is listed there. It can
  always be cancelled (`check()` in `summed`, each branch in `_averaged`, and the engine's own checks).
  Benchmarks are the "sorted" scenarios in `tests/perf/scenarios.py`.
- **Tests.** `tests/test_posterior.py` checks the posterior and the odds against dealing every way:
  every deal of the unseen cards, every order of the tie-breakers, sorted by the game's `get_nominal`
  (`tests/game_order.py`), keeping the deals that show what the player saw, then every draw. That
  covers copies and Stones in both sorts, cards discarded face down after the sorts, Wild Cards and
  Smeared, The House, The Mark, The Hook, The Serpent, Cerulean Bell, DNA and an exhausted deck, plus
  the search against trying every discard. Rounds are also played out from the shuffled deck (`Round`):
  every first hand, sort, discard or play, draw and sort again, against the posterior with every sort
  kept. `tests/test_hint.py` replays them in the mod, to check that its pruning gives the same odds.
  `tests/test_hint.py` checks that the odds and the hint agree on what each card can be.

## Approximations

The panel shows an approximation of a value whose exact odds are slow, until they arrive
([architecture.md](architecture.md#the-app)), never shown as exact (`report.Approx`). There are three:

- **"Any target"** (`how: "bounds"`): its odds are at least each tracked hand's on its own and at most
  their sum (or 1), as `best_discards` uses too. `build_report` takes them from the hands' values,
  with a hand not yet known counting as anything from 0 to 1, so the range narrows as they're found.
  When the bounds meet (a hand is certain, or none can be made) they're the exact odds, and "Any
  target" isn't worked out at all. `test_any_target_bounds_hold_the_exact_odds` checks the bounds on
  random hands under jokers and bosses.
- **Face-down cards in the hand's sorts** (`how: "sample"`): an estimate, from deals of the latent
  cards drawn at random, each exactly as likely as its weight in the posterior (`posterior.deals`). It's
  `summed` telling no deals apart (quick even for The House's first hand, as `usable` is), keeping how
  it reached each partial state, so a deal is drawn from the last card back. Each deal's odds are then
  exact and quick, every face-down card turned up, and deals alike in what the odds see of them share
  an evaluation (`_labels`), so the estimate is off only by which deals were drawn
  (`engine._estimate`). Where a deal's exact odds are slow too (Four Fingers + Shortcut), they're given
  up after 1/200 of the time (`DEAL_SHARE`), and so are every other deal's from then on: each deal
  drawn is then one final hand drawn from its resolved state, as below, which still gives an unbiased
  value. It draws until its range is within half a point either side, or 4096 deals (131,072 when
  deals may be drawing hands), or `Limits.approx` passes. The range is 3 standard errors either side, counting a deal at 0 and one at
  1 besides those drawn, so a few deals all alike can't look certain. It's likely, not certain, so
  the trend colour never comes from it, and "Any target"'s bounds only from exact values; "Any
  target"'s own estimate is kept within its bounds. The draws are seeded (`Random(0)`), so the same
  state gives the same estimate, unless `Limits.approx` runs out first. The windows aren't used:
  every deal is turned up. `test_deals_are_drawn_by_their_posterior_weight` checks the draws against
  the oracle, and `test_estimates_hold_the_exact_odds` (in `test_posterior.py` and `test_report.py`)
  each estimate's range against the exact odds, under the bosses and DNA too;
  `test_estimates_from_deals_drawing_hands_hold_the_exact_odds` does it with every deal drawing hands.
- **Any other target value** (`how: "draws"`): an estimate from final hands drawn at random, the share
  of them that make the target (`engine._estimate_draws`), for what's slow however few the face-down
  cards are (Four Fingers + Shortcut, The Mark, Cerulean Bell). `_final_hands` draws a hand as
  `_discard_odds` models the game dealing it: the rules it's judged under drawn by their weight
  (`rules_after`), The Hook's kept cards taken at random, the face-down kept cards and the draw a
  uniform sample of the pool (under The Mark the face-down cards a sample of the pool's face cards,
  and the draw from the rest), DNA's copies of a face-down card all one card, and Cerulean Bell's
  forced card a random card of the final hand (or the forced card kept). A hand is checked against the
  target's patterns as bit masks of the requirements it meets (`_holds_test`, about 20 µs with Four
  Fingers + Shortcut's 1,856 Straight Flush patterns, against 1 ms for `_holds`), so 1 s draws about
  55,000 hands: about half a point either side. The same stopping rule and range as above, and seeded
  too. Card queries have none: without the sorts they're always quick. `test_estimates_from_draws_*`
  (in `test_report.py`) check it against the exact odds on random hands under every boss, DNA,
  The Serpent and the joker rules.

A best-discard search given up on answers from estimates too: see [Best discard](#best-discard).

## The discard scenario

The "discard" scenario uses the cards currently highlighted in game (`GameState.selected`), except on
the last hand with no discards left (`search.can_redraw` false): nothing is drawn after it, so
`build_report` gives no discard odds and the panel shows '-'.

## Best discard

`search.best_discards` compares keeping the hand with every discard (or hand played as one,
`can_redraw`) of up to `select_limit` cards (always including Cerulean Bell's forced card), for this
discard and draw only, using `evaluate_discard`.

- It enumerates counts per group of hand cards the target can't tell apart (`_groups`: by which
  requirements a card's key counts towards, face-down cards together; by class under Cerulean Bell,
  whose random forced card makes the class matter). A face-down card in the hand's sorts is a group of
  its own (`_hidden_group`), since where it is says what it can be, and then discards aren't
  relabelled (`_relabelled`), nor are they when only cards discarded face down are in the sorts: what's
  left to draw then isn't the same for a rank and the one it would be swapped with. Options are hand indices, so it says which face-down card to throw away.
- It then expands the best counts into every distinct tied discard (identical cards collapsed, leftmost
  first), ordered fewest cards, then lowest ranks thrown away, then leftmost.
- `tests/test_search.py` checks it against trying every subset.
- It works out each distinct discard once (`_relabelled`: ranks or suits the target can't tell apart
  are dropped, and relabelled when the patterns are symmetric), bounds "Any target" by its hands' own
  odds so few need the exact union, and passes one `engine.Reuse` to every `evaluate_discard` so the
  engine shares its pool-dependent setup (`_layout`), results and memo between discards
  (`_class_signatures`). See [best-discard.md](performance/best-discard.md). Four Fingers + Shortcut
  with Straight Flush still takes seconds.

`search.best_card_discards` groups hand cards only by whether they match (face-down ones apart, and
each latent one on its own), so it's a handful of evaluations.

A search records what it has found as it goes (`search.Progress`: its choices, their bounds, the exact
odds found), so one given up on (`Limits.search`, or the memory limit) can still answer, from estimates
(`search.estimated_best`, in about `Limits.search_estimate`, 5 s). The choices that could still be
best are those whose upper bound reaches the highest lower bound or exact odds found. Each is
estimated as a value is ([Approximations](#approximations), kept within its bounds), in rounds that
give each choice twice the time of the round before, starting at 20 ms at most, and drop those whose
likely range falls below another's, then all but the half of the estimated ones with the highest upper
bounds, until one is left or the next round wouldn't fit. The answer is the one with the highest
likely lower bound (or exact odds), so that one estimated from a few lucky samples can't win, with
its options, and marked as from estimates (`Search.estimated`: the best's likely range). It isn't certainly the best, though in the benchmark
scenarios it nearly always is (`benchmark.py --estimates`). If every choice it looked at turned out
quick to work out exactly (card queries without the hand's sorts), the answer is exact and unmarked.
`tests/test_search.py` checks it against the exact search, stopped part of the way.

How the panel's button asks for this and uses the reply is in
[panel.md](panel.md#best-discard-button).

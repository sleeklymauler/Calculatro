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

With the "Face-down windows" setting on, the snapshot has the latest sort of the hand in each mode
(`GameState.observations`, [architecture.md](architecture.md#shapes)). A face-down card in one of them
is *latent* (`Card.latent`, its id there): its place says what it can be. Between a 9 and a 7 in a rank
sort it's a 7, 8 or 9, and of two side by side the left one is the higher. The design and its reasoning
are in [the plan](plans/face-down-windows.md).

- **The posterior** (`posterior.py`). The prior deals the unseen cards to the latent ones uniformly at
  random (under The Mark, the unseen face cards, `flipped_by_mark`). The likelihood is 0 unless every
  sort is in the game's order: by printed rank then suit, or suit then rank, with Diamonds < Clubs <
  Hearts < Spades and Stones lowest. Cards alike in print (copies, or Stones) tie: the game orders them
  by hidden values, taken as random but the same in both sorts. Each tie group gives a factor, the share
  of its orders every sort agrees with (`tie_factor`, a count of linear extensions). `branches` lists
  every deal with a positive weight, merging copies, and goes through the latent cards between their
  face-up neighbours. `reference` does the same one unseen card at a time, for the tests.
- **The odds** (`engine.evaluate_discard`, `evaluate_cards`). They're the average of today's odds over
  the branches, each with its latent cards turned up (`_resolved`: the card takes its place in hand and
  leaves `unseen`, so the deck size, hand size and indices don't change). Branches that turn up as many
  cards of each class, kept or discarded and forced or not, give the same odds, so they share one
  evaluation (`_over_branches`, `_rule_classes`). A card's class there is what the target can tell of
  it, under every rule set the final hand may be judged under (`_counts_towards`: which requirements
  of the patterns it counts towards, as in `search._groups`; the class itself under Cerulean Bell), or
  for a card query whether it matches, and under The Mark its face status. So for Pair, deals that
  differ only in suits share one evaluation. Nothing is sampled or cut off. `can_miss` picks a branch
  for each try, so a draw it finds is one the evidence allows.
- **No evidence needed.** A single sort with no face-up card (The House's first hand) only says which
  of its cards is higher, not what they are. So keeping all of them, or throwing all of them away,
  gives today's odds exactly (`_says_nothing_here`). A discard that splits them throws away the higher
  or the lower cards, and goes through every deal.
- **Evidence set aside.** If no deal fits the sorts (a card changed while face down, say), the odds are
  today's, and it's logged once on stderr (`engine.posterior`). The search then treats the face-down
  cards as interchangeable again (`with_usable_evidence`).
- **DNA.** With 2 or more copies of a face-down card played alone, they're the same unknown card, not
  that many separate unseen ones: it's branched on too, with no evidence (`_posterior_for`).
- **The Mark** with the challenges' `flipped_cards` modifier can flip any card, which the Mark model
  doesn't allow, so the mod sends no sorts then. Selling Pareidolia, or changing a face-down card's rank,
  mid-round can leave a face-down card whose face status differs from when it was flipped. That isn't
  modelled.
- **Speed.** One or two face-down cards in a narrow window are a few branches. Long face-down runs in
  wide windows are many: The House's hand after its first discard (5 face-down cards around 3 face-up
  ones) is about 260,000. Building them takes about a second, and the odds 1-2 s for Pair, Flush or
  Full House. Straight Flush tells almost every card apart, so it takes minutes there, as does a
  discard that splits The House's first hand. Four Fingers + Shortcut with Straight Flush takes 6-12 s
  with two or three face-down cards, and its best-discard search a minute or more. It can always be
  cancelled (`check()` in `branches`, each branch in `_over_branches`). Benchmarks are the "sorted"
  scenarios in `tests/perf/scenarios.py`.
- **Tests.** `tests/test_posterior.py` checks the posterior and the odds against dealing every way:
  every deal of the unseen cards, every order of the tie-breakers, sorted by the game's `get_nominal`
  (`tests/game_order.py`), keeping the deals that show what the player saw, then every draw. That
  covers copies and Stones in both sorts, Wild Cards and Smeared, The House, The Mark, The Hook, The
  Serpent, Cerulean Bell, DNA and an exhausted deck, plus the search against trying every discard.
  `tests/test_hint.py` checks that the odds and the hint agree on what each card can be.

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
  relabelled (`_relabelled`). Options are hand indices, so it says which face-down card to throw away.
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

How the panel's button asks for this and uses the reply is in
[panel.md](panel.md#best-discard-button).

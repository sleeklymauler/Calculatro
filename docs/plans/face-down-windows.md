# Plan: face-down cards narrowed by the hand's sort order

Status: in progress. The in-game hint (stage 0) is done, behind a setting (off by default), and checked
in game under The Wheel, The Fish and The House (not yet The Mark); the odds are unchanged. Revised
2026-10-15 after review: the first draft's per-card windows were replaced by observations that keep
the order of face-down cards (see [the model](#what-the-player-learns)). Revised 2026-11-08 after
review against the code: the odds use the same observations as the hint, both sort modes, with
exact tie handling across them; accuracy comes before speed in the first pass; and three places that
treat face-down cards as interchangeable are listed for fixing.

## Summary

Balatro sorts the hand by each card's true rank and suit, face-down cards
included. So a sorted hand tells the player something about its face-down
cards. One between a 7 and a 9 in a rank sort is a 7, 8 or 9; one between two
spades in a suit sort is a spade. And where several face-down cards sit
together, the left one is the higher. The engine ignores all of this today:
every face-down card is a uniformly random unseen card (or, under The Mark, a
random unseen face card).

The mod already records the hand's order when it can trust it to be the
game's sort (an **observation**), keeping only what the player sees: the
face-up cards' printed rank and suit, Stones, and where the face-down cards
sit among them. It keeps the latest observation of each sort mode, and the
hint shows what each face-down card can be given both. This plan exports
those observations, and Python conditions the face-down cards' identities on
them, so the engine and the best-discard search use that posterior.

It applies under The Wheel, The Fish, The Mark and The House, and the
`flipped_cards` challenge modifier. It covers only hands with face-down cards.
The House belongs here too: with the whole first hand face down, the order
still says which card is the highest, and after a discard the face-down cards
that stay get face-up neighbours. Where it applies it can change the odds a
lot. Four spades and a face-down card sorted between two spades is already a
Flush. With two face-down cards in a run, discarding the left one or the right
one can differ by a lot (see [the example](#worked-example)).

Decisions already made:

- **No hooks on game code.** The mod only reads game state (see
  [Detecting sorts](#detecting-sorts-without-hooks)). No wrapper around
  `CardArea:sort`.
- **No prompt** asking the player to sort by both rank and suit. The panel
  deliberately doesn't hint at what to do; the mod uses whatever sorts the
  player makes.
- **The hint comes first** (decided 2026-11-06, replacing "a visible marker is
  out of scope until the feature works"): hovering a face-down card shows what
  it can be, before the odds use any of this. See [stage 0](#stages).
- **It's a setting, off by default** (decided 2026-11-07): "Face-down windows"
  on the panel's settings page ([panel.md](../panel.md#settings)) turns on the
  hint and, from stage 1, the export of observations. With it off the snapshot
  carries none, so the odds and the search are today's. `observe.lua` watches
  the hand either way, so turning it on mid-hand takes effect at once.
- **The odds know everything the hint knows** (decided 2026-11-08): they use
  the same observations, the latest of each sort mode, whether the player has
  sorted by rank, by suit or both. They weigh whole deals consistent with all
  of them, not the hint's per-card lists (see
  [why not](#why-not-the-hints-lists)). The only exception is The Mark with
  the `flipped_cards` modifier (see [The Mark's prior](#the-marks-prior)).
- **Accuracy first** (decided 2026-11-08). The first pass computes the exact
  posterior and exact odds, however long that takes:
  - no approximation, cutoff, sampling or fallback to today's odds to save
    time;
  - optimizations that give the identical `Fraction` are allowed, but each one
    is tested against the ungrouped reference;
  - calculations stay cancellable, so a slow one never blocks the app;
  - shortcuts that trade a little accuracy for speed are a later decision,
    taken with the measurements from the first pass (see [stage 4](#stages)).

## Game behaviour (verified in the game's source)

From the Lua packed in `Balatro.exe`:

- **The sort key ignores facing.** `CardArea:sort` (`cardarea.lua:577`) sorts
  by `Card:get_nominal` (`card.lua:950`):
  ```lua
  local mult = 1
  if mod == 'suit' then mult = 1000 end
  if self.ability.effect == 'Stone Card' then mult = -1000 end
  return self.base.nominal + self.base.suit_nominal*mult + (self.base.suit_nominal_original or 0)*0.0001*mult
       + self.base.face_nominal + 0.000001*self.unique_val
  ```
  With `card.lua:122-141`: `nominal` is 2..10 for 2..10, 10 for J/Q/K and 11
  for Ace; `face_nominal` is 0.1/0.2/0.3/0.4 for J/Q/K/A, so all 13 ranks are
  distinct; `suit_nominal` is Diamonds 0.01 < Clubs 0.02 < Hearts 0.03 <
  Spades 0.04.
  - Rank sort (`'desc'`): by rank, then suit.
  - Suit sort (`'suit desc'`): by suit (×1000), then rank.
  - **Ties** between cards of the same printed rank and suit are broken by the
    original suit (`suit_nominal_original`, for cards whose suit was changed)
    and then by creation order (`unique_val = 1 - ID/1603301`, `card.lua:40`).
    The player can't see either. A standard deck has no duplicates, so ties
    need copies (Cryptid, DNA, Death, Standard packs).
    - Both tie-breakers have a positive multiplier in both modes, so **tied
      cards are in the same order in a rank sort and a suit sort**.
  - **Stone Cards** use `mult = -1000` in both modes, so their key is below
    1.4 while every other card's is at least 2.01 (rank sort) or 12.01 (suit
    sort): Stones always sort to the low end (rightmost). Among themselves they
    sort by their underlying rank and suit, which the player can't see.
    - `mult` is -1000 whichever mode sorts, so a Stone's key is **the same in
      both modes**: Stones are in the same order among themselves in a rank
      sort and a suit sort, like copies.
  - Wild Cards sort by their printed suit, debuffed cards normally, and other
    enhancements, editions and seals don't count.
  - `G.hand.cards[1]` is the leftmost card and the highest in both sorts
    (`'desc'`, `'suit desc'`). `'asc'`/`'suit asc'`/`'order'` exist, but
    nothing sets them for the hand; treat them as unsupported.
- **Draws re-sort the whole hand.** Every card drawn from the deck to the hand
  is drawn with `sort = true` (`state_events.lua:372-374`), and `draw_card`
  then calls `to:sort()` (`common_events.lua:419`) in the current mode
  (`G.hand.config.sort`, `'desc'` unless a button changed it). Hand-size
  increases mid-round (`cardarea.lua:104`) and Certificate (`card.lua:2476`)
  sort too.
- **Sort buttons**: `G.FUNCS.sort_hand_value` / `sort_hand_suit`
  (`button_callbacks.lua:36-47`) call `G.hand:sort('desc' / 'suit desc')`.
  They're UI buttons in `G.buttons` (`UI_definitions.lua:1011, 1014`), and
  nothing else calls them (a controller presses the same UI elements).
  `UIElement:click` (`engine/ui.lua:965`) sets `self.last_clicked =
  G.TIMERS.REAL` just before running the button's function, and only when it
  runs it.
- **Dragging reorders immediately.** While selecting, `CardArea:align_cards`
  re-sorts `G.hand.cards` by on-screen x every frame (`cardarea.lua:464`), so a
  dragged card changes the order at once, and nothing re-sorts until the next
  draw or Sort.
- **Unsorted changes:** Tarots and Spectrals change cards in place (Strength,
  the suit Tarots, Death, Sigil, Ouija; they can target face-down cards, which
  flip back face down), and Familiar/Grim/Incantation (`card.lua:1336`),
  Cryptid (`:1212`) and DNA (`:3508`) put face-up cards into the hand without
  sorting.
- **Using a consumable** in a blind sets `G.TAROT_INTERRUPT` and `G.STATE =
  PLAY_TAROT` (`button_callbacks.lua:2178-2185`), Planets included.
- **Which cards are face down:** `Blind:stay_flipped` (`blind.lua:605`) does
  it for The Wheel (1 in 7 at random), The House (the first hand), The Mark
  (`Card:is_face(true)`) and The Fish (cards drawn after a play). **Separately**,
  the `flipped_cards` challenge modifier (`cardarea.lua:601`,
  `common_events.lua:403`) flips cards at random, so with both, a non-face card
  can be face down under The Mark. Except for The Mark, being face down doesn't
  depend on the card.
- **Face-down cards stay hidden when discarded.** `CardArea:emplace`
  (`cardarea.lua:38`) flips a face-down card up when it enters any area but
  the deck or the discard pile, so played cards are revealed and discarded
  ones (including The Hook's) aren't. The game marks face-down cards
  `ability.wheel_flipped` (`cardarea.lua:42`), cleared when the card flips up
  (`card.lua:4119`) or the round ends (`state_events.lua:308`). View Deck
  counts them as unplayed, with a "?" (`UI_definitions.lua:3264, 3295`). The
  mod already treats them that way: they're in `unseen` and go out as
  `{hidden: true}` in `discard` (`GameState.discarded_hidden`). `CardArea:align_cards`
  turns every card in the discard pile over (`cardarea.lua:427`), so only
  `wheel_flipped` tells them from cards discarded face up, never `facing`.
- **Update order:** the mod's `lovely.toml` calls `Calculatro.update` at the
  start of `Game:update`. So each frame the mod sees what the previous frame
  did, possibly several things at once.

## What the player learns

### Latent cards

A **latent card** is a card whose identity the player hasn't seen: face down
in hand, or discarded face down this round. Every other unseen card is in the
deck. The engine's job is a posterior over which unseen cards the latent ones
are.

### Observations

An **observation** is the hand's order at a moment when it's known to be the
game's sort (see the [state machine](#observation-state-machine)), in mode
`'desc'` or `'suit desc'`. It's a sequence, left to right, of entries, each
one physical card:

- a face-up card: its printed rank and suit, or "Stone" (a face-up Stone shows
  neither);
- a latent card: a reference to it.

That's exactly what the player sees. It holds more than per-card intervals:
it also says that, of two face-down cards side by side, the left one is the
higher, and it bounds each latent card by its face-up neighbours.

The mod keeps **the latest observation of each mode**, so at most two. A card
that's in both is the same card in both, and the export says so (each card
has an id, see [Snapshot](#snapshot)). The player can see this: cards move
into place when sorted, and tied cards keep their relative order between the
sorts anyway.

### Posterior

Prior: the latent cards are a uniformly random injective assignment of the
unseen cards (under The Mark, see [its limits](#the-marks-prior)). Being face
down doesn't depend on the card, so seeing which cards are face down changes
nothing.

Likelihood of the observations given an assignment, which gives every latent
card a printed key:

1. **Order.** In each observation, the printed keys in its mode (Stones
   lowest) must be non-increasing left to right. If not, it's 0.
2. **Ties.** A **tie group** is the observed cards (in any observation) that
   the player can't order by what's printed: the same printed rank and suit,
   or all Stones. The same groups apply in both modes, and each group's hidden
   order is the same in both (see [Game behaviour](#game-behaviour-verified-in-the-games-source)).
   Under the **tie policy** below, a group of `t` cards has each of its `t!`
   orders equally likely, so its factor is
   `(orders of the group consistent with every observation) / t!`.
   The likelihood is the product of the groups' factors.
   - With one observation each group's factor is `1/t!`.
   - The count of consistent orders is exact. Count it over subsets of the
     group (a small DP); groups are at most the observed cards.
3. **Tie policy.** Unknown tie-breakers are treated as uniformly random. That's
   the original suit, creation order, and Stones' underlying values.
   - It's a modelling assumption, not a performance shortcut: the real
     tie-breakers are deterministic, and the player can't see them (a player
     could in principle reconstruct creation order).
   - It only matters when tied cards differ in something a target or query
     counts, e.g. a Wild 9♥ and a plain 9♥.
   - Fixed tie-breakers across the two sorts are **not** approximated: a group
     is one order, shared by both observations.

The posterior is the prior times the likelihood, normalized once. The
normalization depends on the observations alone, not on the discard being
evaluated or the target. What happens after (The Hook's throw-away, the
draw) doesn't reweigh it.

Observed cards that have left the hand stay in their observations: a
face-up card that was played or discarded keeps its printed entry, and a
latent card that was played is now face up, so its entry takes its printed
values. Their positions still bound the cards around them.

### Worked example

The unseen cards are ordinary 7♣, 8♣ and 9♣. Two are face down between a
face-up 10 and 6 in a rank sort; the third is the whole deck. The equally
likely face-down pairs, in displayed order, are (8, 7), (9, 7) and (9, 8). The
left card is a 9 with probability 2/3, and the right one never is.

- Discarding the right card and drawing leaves a 9 in hand with probability 1.
- Discarding the left one leaves a 9 with probability 1/3.

A model with one window per card ("both are in 7♣–9♣") gives 2/3 for both
and can't tell the better discard. This is why observations keep the order,
and it's the first case for the [oracle](#tests).

### Why not the hint's lists

The hint shows each face-down card's possible values on its own. Treating
those lists as independent windows loses the link between the cards. The
unseen cards are 7♣, 8♣, 9♣ and 10♣; two are face down between a J and a 6 in
a rank sort, and the other two are the deck. The hint says left 8–10, right
7–9.

| | Deals | P(left is the 10) | Hold the 10 after discarding the right | after discarding the left |
|---|---|---|---|---|
| Consistent with the sort | (8,7) (9,7) (10,7) (9,8) (10,8) (10,9) | 1/2 | 3/4 | 1/4 |
| Hint lists as independent windows | those, and (8,9) | 3/7 | 5/7 | 2/7 |

The lists also can't say how likely each value is, and they're deliberately
too wide in places (copies, and the hint's step limit). So the odds start from
the observations the hint uses and weigh whole deals; a
[test](#tests) checks the two agree on what's possible.

### Which observations

- **Stages 1-2** use the latest observation of each mode, exactly what the hint
  uses. Their latent entries are restricted to cards still face down in hand.
  - A latent card that has since been revealed (played, or flipped up) has its
    entry replaced by its printed values, which are now public.
  - One discarded face down is left out of every observation. It's then just
    another latent card with no evidence. The hint does the same.
  - Leaving cards out of an observation is conditioning on part of what was
    seen (a subsequence of a sorted order is the sorted order of those cards).
    So the posterior is correct for that information.
- **Stage 3** (later) keeps latent cards discarded face down in the
  observations, in the hint as well as the odds. That's more evidence, not more
  accuracy for the same evidence, which is why it can come after the first
  pass.

Every deliberate coarsening is listed here and in the code: the tie policy,
dropping latent cards discarded face down (until stage 3), dropping all
observations on a consumable (see the [state machine](#observation-state-machine)),
and The Mark with `flipped_cards` (below). Each is a defined subset of what
the player saw, or a stated modelling assumption. None is a shortcut for
speed.

### The Mark's prior

The engine's Mark model (face-down cards are face cards, `_mark_split`)
is an existing approximation:

- With the `flipped_cards` modifier also active, a non-face card can be face
  down.
- Selling Pareidolia, or a Tarot changing a face-down card's rank, can make a
  card's face status now differ from when it was flipped.

The feature must not turn this into an exactness claim:

- Under The Mark, latent cards' candidates are the face cards as today (all
  cards with Pareidolia, as `flipped_by_mark` says).
- With `flipped_cards` also active, ignore observations (today's odds,
  unchanged) until the Mark model handles it. This is the one case where the
  hint (which then allows every card) knows more than the odds.
- Document the Pareidolia case as a known limitation.

In the restricted model every latent card in hand takes a face card. So the
ways to place the latent cards without evidence, C(face cards left, u), are
the same for every assignment and cancel.

## Information hiding

Observations are made of what the player sees: printed values of face-up
cards, Stones, positions, which card is which across sorts, the sort mode,
and public events (draws, Sort presses, drags, consumables, cards played or
discarded). The mod must never use a latent card's true values to build, keep
or drop an observation:

- **Don't check whether the current order is the true sorted order.** After a
  drag, that check's answer depends on the face-down cards' values. The
  face-up-order check below uses printed values only, and can't catch two
  face-down cards swapped, which is why trust comes from events.
- **Don't drop evidence because a face-down card's values changed.** A suit
  Tarot on a face-down card that's already that suit changes nothing, so
  watching for changes would leak whether it was. Drop it on the public event
  instead: a consumable being used, no-op or not.

Observations go out with the hand, whose face-down cards already go out in
their positions as `{hidden: true}`. Latent cards are referenced by hand index
only; no deck ids, no sort keys. Card ids are numbered by first appearance in
the exported observations, which are public, so they carry nothing else. The
unseen pool stays a sorted multiset.

## The mod

### Observation state machine

Done in stage 0 (`observe.lua`); unchanged by this revision except that
entries keep their card for ids (see [stage 1](#stages)).

Two things are kept apart: **evidence** (the latest observation of each mode,
true until an event makes it false) and **readiness** (whether the current
order may be recorded as a new observation). A drag ends readiness but leaves
the evidence; a consumable ends both.

Every frame, in `Calculatro.update`, before the snapshot throttle:

1. **Reset** evidence and readiness when the run, `G.GAME`, `G.hand` or
   `G.GAME.round` changes, which covers a new run, a load and a new blind.
2. **Consumables:** while `G.STATE == PLAY_TAROT` or `G.TAROT_INTERRUPT` is
   set, clear evidence and readiness and record nothing. Leaving that state
   restores neither: it takes a fresh sort.
3. **Events since last frame:**
   - a *sort*: a card arrived from the deck (it was in `G.deck.cards` last
     frame), or a Sort button's `last_clicked` changed;
   - a *drag*: any hand card has `states.drag.is`, now or last frame;
   - a *foreign arrival*: a card entered the hand from anywhere else;
   - a *reorder*: the relative order of cards present both frames changed.
4. **Readiness:**
   - A sort with no drag and no foreign arrival in the same window makes the
     mod *pending*.
   - A drag, a foreign arrival, or a reorder with no sort clears *pending*.
   - A frame with both a sort and anything else is ambiguous and never grants
     it.
5. **Record:** if *pending*, the observation replaces the evidence for its mode
   when all of these hold:
   - this frame's hand is the same card objects in the same order as last
     frame's (stable);
   - `G.STATE == SELECTING_HAND` (not `DRAW_TO_HAND`: the odds aren't worked
     out while drawing anyway);
   - the mode is supported;
   - no drag is in progress;
   - the face-up cards are in sorted order by printed values (a safety net
     that leaks nothing).

   Then *pending* clears. A mode change on its own is not a sort.
6. **Departures:** when a latent card in the evidence leaves the hand:
   - if it's now face up (played), its entry becomes its printed values;
   - otherwise (discarded face down), its entry is dropped (until stage 3).

   A latent card turning face up in hand is treated the same way.

Removing cards (discards, plays, The Hook, Hanged Man) keeps the order of the
rest, so it's no reorder.

### Detecting sorts without hooks

Find the two sort buttons in `G.buttons` by `config.button ==
'sort_hand_value'` / `'sort_hand_suit'`. Cache the elements per `G.buttons`
object, since it's rebuilt between hands (`button_callbacks.lua:829-830`). If
an element's `last_clicked` differs from the value seen last frame for **the
same element**, a sort happened.

A new element (box rebuilt) only sets the baseline, and so does a missing
`G.buttons`; draws still count as sorts then. This is read-only, like the
mod's other reads of `G`.

### Snapshot

The snapshot gains `observations`, a list of at most two, `"desc"` first:

```json
"observations": [
  {"mode": "desc",
   "cards": [{"id": 0, "rank_id": 10, "suit": "Clubs"}, {"id": 1, "hand": 3}, {"id": 2, "hand": 4},
             {"id": 3, "rank_id": 6, "suit": "Hearts"}, {"id": 4, "stone": true}]},
  {"mode": "suit desc",
   "cards": [{"id": 2, "hand": 4}, {"id": 3, "rank_id": 6, "suit": "Hearts"}, {"id": 0, "rank_id": 10, "suit": "Clubs"},
             {"id": 1, "hand": 3}, {"id": 4, "stone": true}]}
]
```

- `mode` is `"desc"` or `"suit desc"`, each at most once.
- `cards` is the observed sequence, left to right. Each entry has an `id` and
  exactly one of:
  - a face-up card's printed `rank_id` (2..14) and `suit`;
  - `"stone": true` for a face-up Stone;
  - `"hand": i`: a latent card by its **current** 0-based hand index, which
    must be a `{hidden: true}` card.
- `id` is the physical card: the same id in both observations is the same
  card, with the same content. Ids are numbered 0, 1, ... by first appearance,
  reading the observations in order, so they say nothing the order doesn't.
  An id appears at most once per observation, and two ids never share a hand
  index.
- Exported only with the "Face-down windows" setting on. No observation means
  the field is an empty array (the encoder's `ARRAY` tag; Python also accepts
  `{}` as usual).
- Stone information can't get lost: a Stone is an entry like any other, and
  its position carries what's known.
- Stage 3 adds `"discarded": true` (in place of `hand`) for a latent card
  discarded face down.

Bump `schema` in `core.lua` and `SCHEMA` in `state.py` (11 to 12) in stage 1,
and make Python parse and validate observations then, even before it uses
them.

## Python

### State

`GameState` gains `observations: tuple[Observation, ...]`, frozen and
canonical (tuples, no lists), parsed in `GameState.from_json`. An
`Observation` holds its mode and its entries by id; the ids' contents (printed
values, or latent with a hand index) are stored once.

**Malformed** input raises `MalformedState`:
- an unknown or repeated mode;
- a `rank_id` outside 2..14 or an unknown suit;
- an entry that isn't exactly one of the three kinds, or has no integer `id`;
- an id repeated within an observation, or with different contents in the two;
- a `hand` index out of range, not face down, or shared by two ids.

**Well-shaped but impossible** evidence is not malformed. Examples are
face-up entries out of order, or observations with no consistent assignment
(e.g. a card changed while face down). For those, ignore the observations for
that state and log once per distinct failure on stderr. The fallback is the
same everywhere (Keep, Discard, card queries, search): today's odds, with no
partial use of the evidence.

Candidates keep multiplicity: two 9♥ in the unseen pool are two candidates,
whatever their enhancements.

From stage 2, a face-down hand card with evidence carries its id
(`Card.latent`, None otherwise), so everything that compares cards tells it
apart from other face-down cards (see [Card identity](#card-identity)). The
id doesn't change when the hand is dragged.

### Evaluation

A new layer above `engine._discard`, used by `evaluate_discard` and
`evaluate_cards` (and so by `build_report` and the search):

1. **Enumerate** the assignments of the latent cards in the observations to
   unseen cards, with their posterior weight (prior × likelihood). Keep an
   **ungrouped reference** that enumerates actual cards with backtracking.
   - The likelihood depends on printed keys and tie groups, so assignments
     can only be merged where those, what the target counts (as
     `search._groups`), whether a query matches, The Mark's face status and
     Cerulean Bell's class are all the same.
   - Any such merging, or summing branches by what the evaluation sees (the
     kept latent cards' classes and the residual pool) before evaluating,
     is an exact optimization: allowed, and tested against the reference.
2. **Resolve** each branch. Each resolved latent card:
   - has one occurrence removed from `unseen`, **whether it's kept or
     discarded**;
   - replaces its `{hidden: true}` placeholder in the hand with the resolved
     card, keeping `highlighted` and `forced`. A kept one then counts as a
     known kept card; a discarded one is gone from the draw.
3. **Evaluate** each branch exactly as today (`probability`, `_after_hook`,
   `_bell_after_draw`, `count_probability`). Latent cards without evidence
   stay unseen as they are now (and marked under The Mark).
4. **Average** the branches by weight (the law of total probability).

No branch is ever skipped, sampled or capped for time. A calculation that
can't finish is cancelled by the next state, as today.

DNA adds to this (`engine._discard`, see `docs/jokers.md`). A lone face-down
card played as the round's first hand comes back as face-up copies, which today
are counted as that many separate unseen cards. That's exact for one copy and
wrong for several, since they're the same card. So:

- With 2 or more copies, the played card must be enumerated **even with no
  observation**: this is a branch of its own, not evidence.
- A resolved played card isn't "gone from the draw" under DNA: in that branch
  its copies are known cards in the final hand. So one copy is only "the same as
  keeping it" while the card has no evidence.
- Remove the temporary count, including its extra `marked` under The Mark, and
  the test that pins it (`test_dna_several_copies_of_a_face_down_card`).

Invariants, asserted in tests:

- Resolving identities changes none of the physical deck size, the hand size,
  the discard's indices, or the number drawn. That holds with an exhausted
  deck and under The Serpent too.
- Every physical card is counted exactly once, duplicates included.
- Branch weights sum to the normalization, which depends on the observations
  alone.
- Resolved identities never reach the output: `DiscardOutcome.discarded` holds
  the hand's own cards (placeholders included), as the player sees them.

Every stage of the enumeration checks `cancel`: generating assignments,
checking feasibility, counting tie orders, normalizing. `evaluate_cards` has
no `cancel` today, so add one and thread it through `build_report` and
`best_card_discards`. Reject impossible evidence before enumerating.

### Card identity

Code written since the first revision assumes face-down cards are
interchangeable, and would give wrong results with evidence. Each needs
fixing in stage 2:

- **The odds cache** (`report.build_report`, `__main__.odds_basis`). A row's
  odds are cached by the multiset of discarded cards, and `odds_basis` drops the
  hand's order. Every face-down card is the same `Card(hidden=True)`, so
  discarding the left or the right card of the [worked example](#worked-example)
  would hit one entry. With `Card.latent` the cards differ. `odds_basis` must
  hold the observations by id, not hand index, so a drag still keeps the cache.
- **`engine.can_miss`**. It draws the kept face-down cards uniformly from the
  pool. A miss found in a deal the evidence rules out would wrongly prove odds
  below 1 and drop a discard whose odds are 1. With a kept latent card that has
  evidence, it returns False (proves nothing), so the discard is evaluated
  exactly.
- **`search._relabelled`**. Its key counts how many face-down cards are
  discarded, not which. With observations it doesn't relabel, and the key is the
  sorted discard, as it already is under Cerulean Bell.

### Best discard

`search.best_discards` and `best_card_discards`:

- **Groups.** `_groups`, and the `"hidden"` group in `best_card_discards`, put
  all face-down cards together. A latent card with evidence has a place of
  its own in a sorted order, so it's a group of its own. Merging two would be
  an exact optimization only if the posterior is unchanged by swapping them;
  don't merge in the first pass.
- **`_expand` and `_identity`.** These collapse cards alike in every field.
  Two latent cards with evidence must not collapse: their positions mean
  different things. `Card.latent` keeps them apart.
- **`_relabelled`**: see [Card identity](#card-identity). Later, relabel only
  if it maps the observations onto themselves.
- **`engine.Reuse`.** Key it by the complete residual pool after removing
  every resolved card, with the rules and grouping it depends on. It's an exact
  cache, so it can grow; if memory becomes the limit, a bounded LRU only
  costs time.
- **"Any target"** bounds and the exact union use the same posterior as its
  hands.
- **`search_basis`.** It keeps `observations` (an immutable field survives its
  `dataclasses.replace`). Test that a new observation cancels a stale search
  even when the hand's cards and selection are unchanged (a Sort press after
  a drag).
- **`tests/test_search.py`** compares winning card multisets through
  `_identity`. Add assertions on hand indices, so a wrong positional
  recommendation can't pass as an equivalent discard.

## Performance

The first pass is exact, whatever it costs (see [Decisions](#summary)).
Some cases will be slow, and some may not finish in a useful time:

- Hands without observations, or whose face-down cards aren't in one: no
  change (no branches).
- Card queries: `count_probability` is cheap, so many branches are fine.
- Targets: one evaluation per branch (or per merged branch). That's a few for
  one face-down card in a narrow window, and many more for long face-down runs
  in wide windows or several runs.
- **The House** is the worst case: eight face-down cards with no face-up
  neighbours are a sorted 8-subset of about 44 unseen cards, around 10^8
  assignments, before any merging. Listing them won't finish; summing by what
  the evaluation sees, computed over the sorted runs, is the exact way to try.
- Best discard multiplies all of this by the discards it compares, and Four
  Fingers + Shortcut + Straight Flush takes seconds today.

Benchmarks (`tests/perf/scenarios.py`) measure, they don't gate the first
pass. Worst cases as well as typical ones:

- one and two face-down cards in narrow windows, each mode and both;
- The House's all-face-down hand, and after its first discard;
- long runs in wide windows;
- duplicates, ties and Stones across both modes;
- The Mark;
- Four Fingers + Shortcut searches.

Record branch counts, peak cache size, latency and cancellation latency.
Cancellation must stay prompt (aim for 50 ms) even when the calculation itself
is slow. Speed targets, and any shortcut that gives up accuracy, are for
[stage 4](#stages), with these numbers.

## Tests

- **Mod** (lupa). The state machine's cases are in `tests/test_hint.py` from
  stage 0: draws and Sort presses, rebuilt or missing `G.buttons`, drags,
  sorts and drags in one frame, consumables (no-op ones too), departures,
  resets, unsupported modes, the face-up-order safety net and Stones. Stage 1
  adds, in `tests/test_state.py`:
  - the exported shape, with ids shared across both modes and numbered by
    first appearance;
  - `[]` with the setting off, and the export following the setting mid-hand;
  - hand indices after a drag (same ids, new indices);
  - played latent cards exported with printed values, discarded ones gone.
- **Information hiding:** keep the public trace fixed (the face-up cards,
  positions, events) and swap which physical cards are face down in hand
  versus in the deck (or face down in the discard). The unseen multiset is
  unchanged, so the **whole sequence of exports, frame by frame**, must be
  identical. That includes resets and no-op consumables. This extends
  `test_which_card_is_face_down_changes_nothing_shown` from the hint to the
  snapshot. Changing a face-down card's values instead legitimately changes the
  unseen multiset, so it's not the test.
- **Python:** parsing and validation, malformed versus impossible evidence,
  and multiplicity.
- **Independent oracle** for the engine. Testing against assignments
  consistent with the observations would just recheck the model's own
  assumptions. Instead, for small decks:
  - deal every way;
  - give each card a hidden tie-breaker order, every order weighted equally per
    the tie policy, and the same order for both modes;
  - sort with the game's `get_nominal` rule in each observed mode;
  - keep the deals whose public view matches the observations;
  - enumerate discards and draws;
  - compare exact `Fraction`s.

  `test_against_dealing_every_way` in `tests/test_hint.py` does the first
  steps for the hint (no ties); share its game-order helpers. Start with the
  [worked example](#worked-example) (1 and 1/3) and the
  [hint example](#why-not-the-hints-lists). Cover:
  - duplicates and ties, in one mode and both;
  - Stones, in one mode and both;
  - Wild and Smeared (printed versus scoring suit);
  - an all-face-down House hand;
  - both modes, overlapping candidates, impossible evidence;
  - an exhausted deck;
  - The Mark, The Hook, Cerulean Bell, The Serpent.

  Also check that the posterior's mass is 1, and that states without
  observations give today's results exactly. The ungrouped reference is
  checked against the oracle, and every optimization against the reference.
- **Odds and hint agree:** for the oracle's cases, the values with positive
  posterior weight for each latent card are the ones the hint lists, except
  where the hint is deliberately wider (copies tied in opposite orders, its
  step limit).
- **Card identity:** the odds cache, `can_miss` and the search's keys tell
  latent cards with evidence apart (the worked example's two discards).
- **Search:** the existing every-subset check with observations, with the
  index-sensitive assertions above.

## Stages

0. **The hint** ([panel.md](../panel.md#face-down-hint)), done: the state
   machine in `observe.lua` and a box over a hovered face-down card, all in the
   mod, with no export and no schema bump.
   - It keeps the latest observation of each mode, and asks only what's
     possible. Copies are allowed in opposite orders in the two sorts, so its
     list can be too wide there, but never too narrow; the same goes for its
     step limit.
   - A latent card discarded face down is dropped from the observations.
   - Checked in game (2026-11-07) under The Wheel, The Fish and The House, with
     drags and both Sort buttons. Still to check under The Mark.
   - Behind the "Face-down windows" setting, off by default.
1. **Export and parsing**, unused by the odds:
   - `observe.lua` keeps each entry's card for face-up cards too (with the
     printed values seen), so the export can give ids; `hint.lua` reads
     entries the new way.
   - Observations in the snapshot, only with the setting on.
   - Python parsing and validation into `GameState.observations` (schema 12 on
     both sides).
2. **Odds, search and the reference, together:**
   - the posterior with both modes and exact ties, the ungrouped reference,
     the oracle tests;
   - `Card.latent` and the [card identity](#card-identity) fixes;
   - the search changes, cancellation (`evaluate_cards` too), DNA, benchmarks.

   The search must not recommend from a different posterior than the panel
   shows. If the search isn't ready, ship the odds only with the search
   **disabled** for states with observations: the button answers with no
   options and the title row says so.
3. **Discarded face-down cards:** keep latent cards discarded face down in the
   observations (`"discarded": true`), in the hint and the odds.
4. **Performance, later and by decision:** with the first pass's
   measurements, choose any shortcuts that give up accuracy for speed, and
   exact ones not done yet (such as generalizing `_mark_split` from a face
   predicate to any subset of the pool, for a run whose candidates are one set).

Update `docs/architecture.md` (information hiding, the snapshot contract),
`docs/probability-model.md`, `docs/panel.md` (the hint and the setting),
`AGENTS.md` (its information-hiding rule) and `docs/blinds.md` (The Wheel,
The Fish, The Mark, The House) as each stage lands.

## Open questions

- What the panel should show for a calculation that won't finish in a useful
  time (The House's first hand, before any exact optimization makes it
  tractable): "Calculating..." indefinitely, as the first pass would, or
  something more telling.
- The search-disabled message in stage 2, if the search isn't ready with the
  odds (it must be short: the title row's message space is fixed).
- Whether stage 3 should come forward: how much dropping discarded face-down
  cards costs in practice (The Fish, The Wheel).
- How The Mark should handle `flipped_cards` properly (an existing engine
  limitation; this plan only avoids leaning on it).
- Whether the hint should adopt the exact tie model, so it's never wider than
  the odds' support.

# Plan: face-down cards narrowed by the hand's sort order

Status: in progress. The in-game hint (stage 0) is done, behind a setting (off by default), and checked
in game under The Wheel, The Fish and The House (not yet The Mark). Stage 1 (the export and its
parsing, schema 12) is done and checked in game (2026-11-08). Stage 2 (the odds and the search use
the observations) is done, tested against dealing every way, and checked in game under The Fish
(2026-11-08). Stage 3 (cards discarded face down stay in the observations, schema 13) is done and
tested against dealing every way, not yet checked in game. Stage 4 (earlier sorts are kept, schema
14) is done, tested against playing rounds out, and checked in game (2026-11-08). Stage 5 is in two parts: 5a,
exact optimizations, is done, tested against dealing every way (2026-11-09) and checked in game (2026-11-10);
5b, approximations for the cases [still slow](#still-slow) (estimates from sampled deals) and the setting
controlling only the hint, is done and checked in game (2026-11-11). Revised
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
sit among them. It keeps every sort seen this round (from stage 4; before,
the latest of each mode), and the hint shows what each face-down card can be
given all of them. This plan exports
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
  Revised 2026-11-10: from stage 5b the setting controls only the hint, and
  the observations are always exported (see [stage 5](#stages)).
- **The odds know everything the hint knows** (decided 2026-11-08): they use
  the same observations, every sort seen this round (from stage 4), whether
  the player has sorted by rank, by suit or both. They weigh whole deals consistent with all
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
    taken with the measurements from the first pass (see [stage 5](#stages)).

  Revised 2026-11-10: the panel shows a marked approximation first where the
  exact odds are slow, then the exact odds, for every row and not only those
  with face-down cards ([the progressive-odds plan](progressive-odds.md)).
  The exact odds are still what the app works out; there's no setting to
  turn the approximations off.

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

The mod keeps **every observation of the round** (from [stage 4](#stages);
until then the latest of each mode), less those the others imply. A card
that's in several is the same card in each, and the export says so (each card
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

- The odds use every observation the mod keeps, exactly what the hint
  uses. Their latent entries are cards still face down in hand, or discarded
  face down since (from stage 3; stages 1-2 left those out).
  - A latent card that has since been revealed (played, or flipped up) has its
    entry replaced by its printed values, which are now public.
  - One discarded face down stays where it was seen, as `discarded`: it's out
    of play, but it's still the card that sat there, so it bounds the cards
    around it, and what it is says what's left to draw. The hint deals it like
    the cards in hand.
  - One that leaves the hand face down any other way, or is turned up in the
    discard pile (nothing the mod knows of does either without a consumable), is
    left out. Leaving cards out of an observation is conditioning on part of
    what was seen (a subsequence of a sorted order is the sorted order of those
    cards), so the posterior is still correct for that information.
- A draw re-sorts the hand in the current mode and adds an observation, which
  holds only the cards in hand. The earlier ones stay ([stage 4](#stages);
  until then the draw replaced that mode's observation), so a card discarded
  face down, or kept while its neighbours were discarded, keeps what it was
  seen next to.
- The mod keeps only what isn't implied by the rest (both exact): no
  observation that's a subsequence of another of its mode, as its entries are
  now (of two equal ones, the earlier), and none with no latent entries left
  unless two of its cards are alike in print (copies or Stones).

Every deliberate coarsening is listed here and in the code: the tie policy,
dropping a latent card that leaves the hand face down other than by a discard,
dropping all observations on a consumable (see the [state machine](#observation-state-machine)),
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
only, or once discarded face down as `discarded`, which doesn't say which of
the discard pile's face-down cards it is (the player saw which, but the pile's
order isn't exported); no deck ids, no sort keys. Card ids are numbered by first appearance in
the exported observations, which are public, so they carry nothing else. The
unseen pool stays a sorted multiset.

## The mod

### Observation state machine

Done in stage 0 (`observe.lua`). Since stage 1, entries keep their card for
ids, face up too (`{card, latent = true}` or `{card, rank_id, suit}` /
`{card, stone = true}`).

Two things are kept apart: **evidence** (every observation of the round, in
the order recorded, true until an event makes it false; until stage 4 the
latest of each mode) and **readiness** (whether the current
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

   Then *pending* clears. A mode change on its own is not a sort. A card in both modes' observations
   must be seen alike in both, as it has one id: if one isn't (nothing the mod knows of does that
   without a consumable), the other mode's observation is dropped as out of date.
6. **Departures:** when a latent card in the evidence leaves the hand:
   - if it's now face up (played), its entry becomes its printed values;
   - if it's in the discard pile with `ability.wheel_flipped` (discarded face
     down, The Hook's too), its entry stays latent, marked `discarded` (stage 3);
     the game turns every card there over, so `facing` says nothing;
   - otherwise its entry is dropped, and so is a `discarded` one whose card
     leaves the discard pile's face-down cards.

   A latent card turning face up in hand is treated the same way, and a
   `discarded` one back in hand face down is latent in hand again (no game card
   does that).

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

The snapshot gains `observations`, a list in the order recorded (from
stage 4, schema 14; until then at most two, `"desc"` first):

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

- `mode` is `"desc"` or `"suit desc"`, and may repeat (until stage 4, each at
  most once).
- `cards` is the observed sequence, left to right. Each entry has an `id` and
  exactly one of:
  - a face-up card's printed `rank_id` (2..14) and `suit`;
  - `"stone": true` for a face-up Stone;
  - `"hand": i`: a latent card by its **current** 0-based hand index, which
    must be a `{hidden: true}` card.
- `id` is the physical card: the same id in several observations is the same
  card, with the same content. Ids are numbered 0, 1, ... by first appearance,
  reading the observations in order, so they say nothing the order doesn't.
  An id appears at most once per observation, and two ids never share a hand
  index.
- `"discarded": true` (stage 3, schema 13) is a latent card discarded face down
  since, still in the discard pile with `wheel_flipped`. It says only that it's
  one of the discard pile's `{hidden: true}` cards, not which, so there can't
  be more of them than those.
- Exported only with the "Face-down windows" setting on. No observation means
  the field is an empty array (the encoder's `ARRAY` tag; Python also accepts
  `{}` as usual).
- Stone information can't get lost: a Stone is an entry like any other, and
  its position carries what's known.

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
[stage 5](#stages), with these numbers.

Measured in stage 2 (the "sorted" scenarios, 2026-11-08):

- One or two face-down cards in narrow windows: 2-300 ms; sorted both ways,
  at most about 1 s. Four Fingers + Shortcut with Straight Flush: 6-12 s.
- The House's first hand, kept: at most 0.4 s (`_says_nothing_here`).
- Five face-down cards kept around three face-up ones (The House after its
  first discard): about 260,000 branches, built in about 1 s; Pair, Flush and
  Full House in 1-2 s.
- Best discard with two face-down cards: 0.04-6 s, Four Fingers + Shortcut
  Flush and Full House included.
- Don't finish in minutes, so not in the scenarios: Straight Flush with those
  five face-down cards (its patterns tell almost every card apart, so few
  branches merge), a discard that splits The House's first hand, and the
  Four Fingers + Shortcut searches for Straight Flush or "any".
- Cancellation: 9-21 ms in all of those.

Measured in stage 3 (2026-11-08), with cards discarded face down kept:

- Two face-down cards in narrow windows, one since discarded (the "since
  discarded" scenarios): 1 ms-2 s, and 9 s for Four Fingers + Shortcut with
  Full House or Straight Flush sorted by rank.
- The House sorted both ways, then 3 of its cards discarded face down and 3
  drawn (re-sorted by suit): the rank sort still holds all 8, so it's about
  1.5 million branches, built in 11 s; Keep's Pair takes 14 s and Full House
  54 s. Leaving the 3 out, as stage 2 did, is about 7,000 branches and 0.1-3 s,
  but the odds differ a lot (Pair 83.4% against 87.3%, Full House 1.5% against
  2.7%). Not in the scenarios (their hands can't be drawn into); measured by
  hand. Cancellation: under 10 ms.

Measured in stage 4 (2026-11-08), with earlier sorts kept (before stage 5a, below):

- The Fish, five face-down cards around three face-up ones, two of them kept
  from the hand before, whose earlier sort still bounds them (the "The Fish"
  scenarios): 148,512 branches, built in 0.7 s; Pair, Flush and Full House in
  0.6-1.6 s, Four Fingers + Shortcut included. Without the earlier sort it's
  the 263,340 above, so more sorts leave fewer deals here.
- The House sorted by rank, then 3 of its cards discarded face down and 3
  drawn face up, re-sorted by rank: the first sort holds all 8, the 3
  discarded with only their order to bound them. Building its posterior
  doesn't finish in 9 minutes (cancelled in 18 ms), against 11 s when the
  draw is re-sorted by suit (stage 3, above), whose suit order bounds the 5
  kept far more than 7H, 8H and 9S do by rank. Not in the scenarios
  (`HOUSE_EARLIER`); for stage 5.

Measured in stage 5a (2026-11-09), after the exact optimizations (see
[stage 5](#stages)), on the same machine (AMD Ryzen 7 7700X), with
`benchmark.py -k face-down --repeat 3`, against the same scenarios measured
before the stage. Every existing scenario's result is unchanged, and the new
scenarios' results match the deal-by-deal sum (no windows), which took up to
7 minutes:

- One or two face-down cards in sorts: 1-30 ms for single targets, 0.05-0.25
  s for "any" (from 0.15-2.4 s), and 0.3-2.2 s for Four Fingers + Shortcut
  with Straight Flush or "any" (from 0.7-13 s).
- Best discard with two face-down cards: 0.06-0.8 s (from 0.04-6.6 s). Four
  Fingers + Shortcut with Straight Flush or "any", new scenarios: 1.4-20 s.
  The only slowdown measured: sorted both ways, Flush, 36-44 ms to 61-70 ms.
- Five face-down cards kept around three face-up ones (The House after its
  first discard): 1-3 ms (from 1-2.3 s). Straight Flush 3 ms, or 64 ms with
  Four Fingers + Shortcut (24 s and 7 minutes summing every deal). The Fish
  with earlier sorts: 6-27 ms (from 0.5-1.5 s).
- The House after its first discard with the earlier sort of all 8
  (`HOUSE_EARLIER`), about 1.7 * 10^8 deals: 0.4-0.7 s (measured with other
  jobs running; it didn't finish in 9 minutes before). Sorted both ways,
  then 3 discarded face down and 3 drawn re-sorted by suit: 0.3-0.4 s (from
  14-54 s).
- A discard that splits The House's first hand: the 3 highest thrown away,
  0.04-0.34 s, Straight Flush included (summing every deal, Pair took 40 s,
  Full House 4 minutes); the 3 in the middle, Pair 1 s (44 s), Flush 2.1-2.4
  s, Straight Flush 10 s (over 2 minutes).
- The Mark is unchanged (no windows there): 11 ms-7.9 s.
- Cancellation: under 75 ms everywhere measured, the slowest part being
  freeing the partial sums of a big `summed`.

### Still slow

The cases that are still slow after stage 5a, for the approximations of stage 5b.
Times are for one odds row (or one best-discard search), measured as above.

1. **Discards that split The House's first hand into several blocks**, for
   targets that tell most cards apart. Throwing away every other card of
   the sort (K, J, 8, 5 of K Q J 9 8 7 5 3): Pair 62 s, Flush 4 s, Straight
   Flush over 3 minutes, using 9 GB of memory before it's cancelled. Blocks
   of more than two make too many ways to split (`engine.SPLITS`), so their
   deals are summed (`posterior.summed`), and with fine-grained labels the
   partial sums grow huge. The 3 in the middle thrown away: Straight Flush
   10 s. The 3 highest, with Four Fingers + Shortcut and Straight Flush:
   about 2 minutes.
2. **Best discard on The House's first hand** (all 8 face down, sorted): it
   tries discards of every shape, many of them case 1. Flush took 554 s.
3. **After The House's first discard, a discard that draws** (the
   `HOUSE_EARLIER` hand with its 3 face-up cards thrown away), so the 3
   cards discarded face down earlier matter to what's left to draw: Pair
   13 s, Full House 72 s, Straight Flush over 3 minutes. Its first sort has
   all 8 cards in a row with different fates and another sort, so it's no
   window.
4. **Four Fingers + Shortcut best-discard searches with face-down cards**:
   Straight Flush 9-16 s and "any" 1.4-20 s with two face-down cards (2.0-2.6 s
   with none). Each discard is one search, but weighing a window's draw
   makes the engine's search slower than with no face-down cards.
5. **The Mark** with sorted face-down cards: windows aren't used there, as
   The Mark's split already weighs the draw by face cards. Four Fingers +
   Shortcut with Straight Flush or "any": 6.4-7.9 s with 3 face-down cards.
6. **Memory.** `summed` keeps one layer of partial sums at a time, but for
   long mixed-fate runs and fine-grained targets a layer can hold millions
   (case 1). There's no limit: a calculation that would need more memory than
   there is fails instead of finishing slowly.

Measured in stage 5b (2026-11-11), face-down cards in sorts under The Hook and
Cerulean Bell, which take no windows (their deals are summed): with two
face-down cards, at most 1.2 s (Cerulean Bell's Full House); with five kept
(`HOUSE_AFTER`), The Hook's Pair 3.5 s, Full House 22 s and Straight Flush
over a minute, and Cerulean Bell's Flush, Full House and Straight Flush over
a minute. The estimate covers them all in about 1 s. Not measured: DNA's
copies of a face-down card (branched on with every unseen card), which the
estimate covers too (`test_estimates_hold_the_exact_odds`).

Found in game (2026-11-12): **The House's first hand sorted both ways**, kept.
Two sorts with no face-up card say how the cards split into suits, so
`_says_nothing_here` doesn't apply, and nothing narrows the deals down. With
the suit sort in runs of 3, 2 and 3 (`THE_HOUSE_BOTH`): Pair 6.4 s, Straight
Flush 17 s. Building the estimate's sampler of deals took 1.2 s, once for each
value, and with its 1 s of drawing that went over the estimate's 2 s limit, so
most rows had no estimate, and the first one came after 12 s. The sampler is
now built once for every value of a state (`engine._deals_for`) and counted in
the estimate's time: the first estimate comes after 3.9 s, and every row has
one after 9.6 s (all of them holding the exact odds), where it took 91 s for
every row to show anything.

## Tests

- **Mod** (lupa). The state machine's cases are in `tests/test_hint.py` from
  stage 0: draws and Sort presses, rebuilt or missing `G.buttons`, drags,
  sorts and drags in one frame, consumables (no-op ones too), departures,
  resets, unsupported modes, the face-up-order safety net and Stones. Stage 1
  adds, in `tests/test_observations.py` (it shares `test_hint.py`'s `Game`,
  which `test_state.py` can't import):
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
1. **Export and parsing**, unused by the odds (done, checked in game 2026-11-08):
   - `observe.lua` keeps each entry's card for face-up cards too (with the
     printed values seen), so the export can give ids; `hint.lua` reads
     entries the new way.
   - Observations in the snapshot, only with the setting on.
   - Python parsing and validation into `GameState.observations` (schema 12 on
     both sides). Ids are renumbered by first appearance, and each id's content
     is in `GameState.observed`.
   - `search_basis` (and so `odds_basis`) leaves the observations out until
     stage 2, so a sort doesn't restart the odds or a search for nothing.
2. **Odds, search and the reference, together:**
   - the posterior with both modes and exact ties, the ungrouped reference,
     the oracle tests;
   - `Card.latent` and the [card identity](#card-identity) fixes;
   - the search changes, cancellation (`evaluate_cards` too), DNA, benchmarks.

   The search must not recommend from a different posterior than the panel
   shows. If the search isn't ready, ship the odds only with the search
   **disabled** for states with observations: the button answers with no
   options and the title row says so.

   Done (2026-11-08), search included, so it isn't disabled. Checked in game
   under The Fish with 5-7 face-down cards: a rank sort alone was slow, and a
   suit sort after it fast, since both sorts leave far fewer deals. Sorting by
   rank again then changes nothing (the mod keeps the latest sort of each
   mode, and the order is the same), so the panel rightly keeps those odds.
   See [probability-model.md](../probability-model.md#face-down-cards-in-the-hands-sorts).
   - `posterior.py` (`branches`, `reference`), averaged in `engine` by
     `_over_branches`; `tests/test_posterior.py` is the oracle, and
     `tests/test_hint.py` checks the odds against the hint.
   - Branches share an evaluation when the target can't tell their cards
     apart (`engine._counts_towards`, which requirements a card counts
     towards, as `search._groups`; the class itself under Cerulean Bell).
   - One exact shortcut: a single sort of face-down cards only (The House's
     first hand) leaves Keep, and discards taking all or none of them, at
     today's odds (`engine._says_nothing_here`).
   - The Mark with `flipped_cards`: the mod sends no observations.
   - Still slow: see [Performance](#performance).
3. **Discarded face-down cards**, done (2026-11-08), not yet checked in game:
   latent cards discarded face down stay in the observations (`"discarded":
   true`, schema 13), in the hint and the odds.
   - `observe.lua` keeps the entry while the card is in the discard pile with
     `wheel_flipped`; `hint.lua` deals it like the cards in hand.
   - `Branch.discarded` holds what they are; `_resolved` puts each in place of
     one of the discard pile's hidden cards, so `discarded_hidden` (and under
     The Mark, `marked`) goes down by one as the card leaves `unseen`.
     `_over_branches` keeps them apart from the cards discarded now.
   - `_says_nothing_here` goes by fates: a single sort of face-down cards only
     says nothing while all of them are kept, all gone (thrown away now or
     discarded before), or the one card DNA copies.
   - The search doesn't relabel discards when any latent card is in the
     observations, in hand or not (`search._relabelled`).
   - Tests: the oracle in `tests/test_posterior.py` (cards discarded after the
     sorts, alone, The House, DNA, The Mark, the search), the hint against the
     odds in `tests/test_hint.py`, and the export and parsing in
     `tests/test_observations.py`.
   - Still to check in game: under The Wheel or The Fish, sort both ways,
     discard a face-down card, and hover its neighbour; and The House's speed
     after its first discard (see [Performance](#performance)).
4. **Earlier sorts**, done and checked in game (2026-11-08): keep every sort
   seen this round, not just the latest of each mode.
   - Why: a draw re-sorts the hand and replaces that mode's observation, so
     what an earlier sort said about a face-down card still in hand is lost. A
     card seen between a 9♣ and a 7♣ in a rank sort, kept while both are
     discarded, may sit between a K and a 4 after the draw. The hint then
     widens to what the K and the 4 allow, and the odds lose the same. That's
     still correct (it conditions on part of what was seen), but not all of it.
   - An earlier sort is still true of the cards in it. Their printed values
     change only by a consumable, which ends all the evidence, and sorts in one
     mode share the hidden tie-breakers, so every sort of a mode in a round is
     part of one order. The [posterior](#posterior) is unchanged: every sort in
     order, and each tie group's orders consistent with all of them.
   - **A list**, in the order recorded, where a mode may repeat. Sorts of one
     mode aren't merged into one sequence: cards seen in only one of two sorts
     can't always be placed against each other (a card discarded face down
     against one drawn since, face-down cards drawn under The Fish, copies), so
     together they're a partial order, and the list of sorts is that, as the
     player saw it. `posterior.py` already takes any number of observations.
   - **Pruning**, both exact:
     - Keep no observation that another of the same mode implies: its entries,
       as they are now, are a subsequence of the other's. A Sort press or a
       draw that adds nothing then changes nothing, so the odds and a search
       aren't restarted.
     - Drop an observation with no latent entries left, unless two of its cards
       are alike in print (copies, or Stones). It then constrains no deal and
       orders no tie group.

     So the list grows by at most one per sort, and resets with the blind as
     now.
   - **Consistency:** `record` checks a new observation against every kept one,
     not just the other mode's, and drops any where a card is seen differently,
     as out of date (as now).
   - **The mod:** `Calculatro.observations` becomes a list of `{mode, cards}`.
     `update_entries` and the export go over all of them, the export in the
     order recorded with ids by first appearance as now. `hint.lua`'s
     `read_observations` keeps that order instead of sorting by mode;
     `possible_cards` already asks what fits every observation.
   - **Snapshot** (schema 14): `observations` holds any number, in the order
     recorded, and a mode may repeat. `state.py` stops rejecting a repeated
     mode; an id still has the same content everywhere. Information hiding is
     unchanged: each observation is a sort the player saw, and when they saw
     it is public.
   - **Cost:** The House's first sort, all face down, now stays after its first
     discard and draw. So the roughly 1.5 million branches measured in stage 3
     (14 s for Pair, 54 s for Full House) become the usual case there, not only
     after sorting both ways. Accuracy first still holds; measure it for stage
     5. With one or two face-down cards in narrow windows, more sorts usually
     leave fewer deals.
   - **Tests:**
     - The oracle plays out a round: deal every way, sort, discard, draw every
       way, sort again (in each mode), then compare. Start with the example
       above.
     - Each pruning rule gives the same `Fraction`s as keeping every sort.
     - The mod's state machine: a draw adds an observation, a Sort press that
       adds nothing doesn't, pruning, the consistency drop, resets. The export's
       shape, and the information-hiding test frame by frame over several
       draws.
     - The odds and the hint agree with several sorts.
     - Benchmarks: The House sorted by rank only, after its first discard; The
       Fish over several hands.
   - Done as planned: `observe.lua`'s `prune`, `subsequence` and
     `says_something`; `record` keeps what's consistent and bumps `observed`
     only when the list changes. The oracle is `Round` in
     `tests/test_posterior.py` (rounds played out from a shuffled deck: every
     draw order and tiebreak), and `tests/test_hint.py` replays the same rounds
     in the mod, checking its pruned snapshot gives the same odds as every sort
     kept, and the hint the posterior's support. Keeping only the latest sort
     of each mode changed the posterior in 13 of 14 such rounds.
   - Benchmarks: The Fish (`FISH_EARLIER`) is in the scenarios; The House
     (`HOUSE_EARLIER`) isn't, being too slow (see [Performance](#performance)).
   - Checked in game (2026-11-08): under The Fish, sorted by rank, some
     face-down cards played and one kept, its hint stays as narrow after the
     draw; a second Sort press doesn't restart the odds; and The House after
     its first discard.
   - **Docs** that say "the latest of each mode": `docs/architecture.md` (the
     snapshot), `docs/probability-model.md`, `docs/panel.md` (the hint), and
     this plan's model sections.
5. **Performance**, in two parts (decided 2026-11-09): exact optimizations
   first, then shortcuts that give up accuracy for the cases still slow.
   - **5a, exact**, done (2026-11-09), checked in game (2026-11-10). Measurements
     in [Performance](#performance).
     - `posterior.summed` replaces `branches`. It deals the latent cards one
       at a time and sums the partial deals that the rest can't tell apart as
       it goes: the cards that bound the latent cards still to come (the
       nearest dealt one on each side in each sort), the cards used that one
       still to come could be, open tie groups, and the multiset of labels
       so far. The label is what the odds see of a card (`engine._labels`):
       its class and whether it's kept, copied by DNA or gone (gone cards not
       at all when nothing is drawn). So the posterior is built already summed
       by what the evaluation sees, and a run of 8 face-down cards (10^8
       deals) takes a fraction of a second. `engine.usable` checks the
       evidence with every label alike: one branch.
     - Windows (`posterior.windows`): latent cards that depend on no others
       (not side by side with them in a sort, and unable to share a printed
       key with them) and whose posterior is uniform over the sets of their
       candidates. A run in one sort, and in no other, between face-up cards
       none of them can tie with is one; other groups are checked by their
       sum (at most `WINDOW_SETS` sets). Each is independent of the rest of
       the deal, so the odds needn't go through its cards: when they're all
       kept or all gone, the engine weighs each draw by how many of each
       window's cards it has (`engine._window_split`, the generalization of
       `_mark_split` the first draft of this stage named). The search packs
       a count for each window into one int (`_Layout.bases`). Kept, a draw
       with a of a window's w cards holds its r in C(a, r) ways; gone, it
       misses them in C(w - a, r); out of C(w, r) C(N - R, m).
     - A window whose cards meet different fates, side by side in a sort, is
       split by the card at the bottom of each block of one fate
       (`engine._split`): given those, each block's other cards are a window
       of the cards between. Used when there are at most `SPLITS` ways and
       they're likely fewer than the deals `summed` would go through.
     - No windows under The Mark, The Hook, Cerulean Bell or DNA's copies of
       a face-down card: those branch as before.
     - Smaller ones: with nothing to draw, `probability` checks the kept
       cards (`_holds`) instead of building a search; `_resolver` resolves
       many branches without searching `unseen` each time; a card's
       requirements per target are cached (`_memberships`).
     - Tests: `summed` against `reference` and the oracle as before (so is
       every window, through the odds), `test_windows_weigh_the_draw`, and
       `test_dna_one_copy_with_nothing_drawn` (a card DNA copies once, with
       nothing drawn, isn't gone). Checked by hand: every window found in 550
       random cases is independent of the other latent cards and uniform,
       against `reference`. New benchmark scenarios: Straight Flush after The
       House's first discard, `HOUSE_EARLIER`, The House's first hand split
       at the top and in the middle, and the Four Fingers + Shortcut
       searches.
   - **5b, approximations**, done and checked in game (2026-11-11); see
     [what it settled](#what-5b-settled) below. Planned (2026-11-10) for the cases
     [still slow](#still-slow). It plugs into
     [the progressive-odds plan](progressive-odds.md), whose stages 1 and 2
     come first ([Recommended order](progressive-odds.md#recommended-order)).
     That plan provides everything around an approximation: the quick pass,
     the time and memory limits, the asterisk and its popup, the marks in the
     odds file, and the exact pass that replaces the approximation. This stage
     adds only the approximations for face-down cards, and the setting change.
     - **No accuracy setting** (decided 2026-11-10): an approximation is shown
       only while the exact odds are being worked out, or once they've been
       given up on, and it's always marked.
     - **Cases 1-3** (a discard that splits The House's first hand, best
       discard on that hand, and a discard that draws after The House's first
       discard): `summed` grows too big. Candidates, to choose between by
       measurement, ranges first:
       - **Sampling deals from the exact posterior.** Counting the deals
         without labels is quick (`engine.usable` does it in one branch, even
         for The House's first hand). Kept for each step, those counts would
         let deals be drawn in proportion to their weight, exactly. Each
         sampled deal's odds are then exact and quick (no face-down cards
         left), so the estimate's margin comes only from which deals were
         drawn. Check first that the counts for each step stay small.
       - **Leaving out the observations that make it slow** (the oldest
         sorts, or the cards discarded face down). Quick, but not a bound,
         and it can be far off: stage 2 measured Pair at 83.4% against 87.3%
         when the cards discarded face down were left out.
       - **Ignoring the sorts**, as with the setting off today. Always quick,
         and the last resort.
     - **Cases 4 and 5** (Four Fingers + Shortcut searches, and The Mark): the
       engine's search is what's slow, as without face-down cards, so the
       progressive-odds plan's stage 3 covers them. Check here that they're
       marked and stop at their limit.
     - **Case 6** (memory): the progressive-odds plan's memory limit. Check
       that the 9 GB case (throwing away every other card of The House's
       sorted hand) stops cleanly and frees what it used.
     - **Not yet measured**: face-down cards in sorts under The Hook and
       Cerulean Bell, and DNA's copies of a face-down card. Measure them, add
       benchmark scenarios, and give them an approximation if they're slow.
     - **The setting**, last: "Face-down windows" controls only the hint, and
       `observe.lua` exports the observations whether it's on or off (still
       none under The Mark with `flipped_cards`). Consider renaming it
       "Face-down hints", and decide its default. Update its popup (`about`
       in `settings.lua`), `docs/panel.md`, `docs/architecture.md` and
       `docs/probability-model.md`. With it, every player meets the slow
       cases, which is why it waits for the limits and the approximations.
     - **Tests**: each approximation against the exact odds on the scenarios
       that finish, through the oracle in `tests/test_posterior.py` (a range
       holds the exact value; a seeded sample's margin does in nearly all
       cases); the snapshot with the setting off still carries the
       observations; the benchmark scenarios record each approximation's time
       and error.

#### What 5b settled

Decided or found while doing it, where it differs from or adds to the plan above:

- **Sampling deals from the exact posterior** (decided 2026-11-11, after measuring the candidates).
  Ignoring the sorts was quick but off by up to 25 points on the scenarios (Pair 86% against an
  exact 61% with five face-down cards kept); sampling was within 2 standard errors on every
  scenario. `posterior.deals` is `summed` telling no deals apart, keeping how it reached each partial
  state: its layers stayed small (at most about 600 states, 22,000 ways between them, built in 0.1 s),
  so the counts the plan asked to check first are no problem. Leaving out observations wasn't tried:
  it's not a bound, and sampling was already good.
- **Every value with face-down cards in the sorts** gets an estimate when its quick try is slow, not
  only cases 1-3: Keep, Discard, card queries and "Any target" (within its bounds), under any boss
  and with DNA's copies (`engine.estimate_discard`, `estimate_cards`). Each deal is turned up and
  evaluated exactly, so the windows aren't used; deals alike in what the odds see share an
  evaluation. On the 97 scenarios it applies to (`benchmark.py --estimates`): off by 0.26 points
  on average, 1.73 at most, none outside its range, each in at most about 1 s.
- **Its range is likely, not certain** (3 standard errors, counting one deal at 0 and one at 1 too):
  the panel shows the estimate rounded ("84%*"), the popup "Likely 83.4% to 84.8%.", and the trend
  colour never comes from it (decided 2026-11-11). `report.interval` gives only certain ranges, so
  "Any target"'s bounds don't use estimates either. `odds_schema` 8, for `how: "sample"`.
- **The approximation pass** between the quick and exact passes, which the progressive-odds plan's
  stage 2 left for the first approximation that takes time: each value found slow is estimated
  once, with `Limits.approx` (1 s), and the estimate is kept in `known` (`report.ESTIMATE`).
- **Still slow, with an estimate:** the values of cases 1 and 3, and the slow ones under The Hook
  and Cerulean Bell. Measured end to end with the app's limits on case 1 (every other card of The
  House's sorted hand thrown away): the estimates arrive within 5.5 s, then the exact pass gives up
  on most values by time or memory, and the memory is back to 43 MB after.
- **Not covered:** a best-discard search (case 2, and case 4) still waits for its exact answer, and
  says "Too slow" past its limit. Answering from approximations is
  [the progressive-odds plan's stage 3](progressive-odds.md#stages). Neither is The Mark without
  sorts, nor Four Fingers + Shortcut, whose estimates are wide when each deal's exact odds are slow
  (about 17 points either side with Straight Flush, 12-16 deals in 1 s).
- **The setting**, renamed "Face-down hints" (decided 2026-11-11), off by default as before, and
  only the hint's: `export_observations` sends the sorts whether it's on or off (still none under
  The Mark with `flipped_cards`). The saved key stays `face_down`, so players keep their choice. Its
  popup adds "The odds use this either way."
- **Benchmarks:** `benchmark.py --estimates` times each scenario's estimate and how far off it is;
  new scenarios for The Hook and Cerulean Bell with face-down cards in a sort (`SORTED_2_HOOK`,
  `SORTED_2_BELL`, `HOUSE_AFTER_HOOK`, `HOUSE_AFTER_BELL`), with their slow targets left out.
- **Checked in game** (2026-11-11): an estimate's asterisk and its popup's lines under The House
  after a discard that splits its first hand, the panel as estimates give way to exact odds, and the
  setting's new name and popup.

Update `docs/architecture.md` (information hiding, the snapshot contract),
`docs/probability-model.md`, `docs/panel.md` (the hint and the setting),
`AGENTS.md` (its information-hiding rule) and `docs/blinds.md` (The Wheel,
The Fish, The Mark, The House) as each stage lands.

## Open questions

- How The Mark should handle `flipped_cards` properly (an existing engine
  limitation; this plan only avoids leaning on it).
- Whether the hint should adopt the exact tie model, so it's never wider than
  the odds' support.

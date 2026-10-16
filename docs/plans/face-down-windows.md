# Plan: face-down cards narrowed by the hand's sort order

Status: proposed, not started. Revised 2026-10-15 after review: the first
draft's per-card windows were replaced by observations that keep the order of
face-down cards (see [the model](#what-the-player-learns)).

## Summary

Balatro sorts the hand by each card's true rank and suit, face-down cards
included. So a sorted hand tells the player something about its face-down
cards. One between a 7 and a 9 in a rank sort is a 7, 8 or 9; one between two
spades in a suit sort is a spade. And where several face-down cards sit
together, the left one is the higher. The engine ignores all of this today:
every face-down card is a uniformly random unseen card (or, under The Mark, a
random unseen face card).

This plan has the mod record the hand's order when it can trust it to be the
game's sort (an **observation**), keeping only what the player sees: the
face-up cards' printed rank and suit, Stones, and where the face-down cards
sit among them. Python conditions the face-down cards' identities on it, and
the engine and the best-discard search use that posterior.

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
- A visible marker of what's known about a face-down card is out of scope
  until the feature works.

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
  - **Stone Cards** use `mult = -1000` in both modes, so their key is below
    1.4 while every other card's is at least 2.01 (rank sort) or 12.01 (suit
    sort): Stones always sort to the low end (rightmost). Among themselves they
    sort by their underlying rank and suit, which the player can't see.
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
  `{hidden: true}` in `discard` (`GameState.discarded_hidden`).
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
`'desc'` or `'suit desc'`. It's a sequence, left to right, of entries:

- a face-up card: its printed rank and suit, or "Stone" (a face-up Stone shows
  neither);
- a latent card: a reference to it.

That's exactly what the player sees. It holds more than per-card intervals:
it also says that, of two face-down cards side by side, the left one is the
higher, and it bounds each latent card by its face-up neighbours.

### Posterior

Prior: the latent cards are a uniformly random injective assignment of the
unseen cards (under The Mark, see [its limits](#the-marks-prior)). Being face
down doesn't depend on the card, so seeing which cards are face down changes
nothing.

Likelihood of an observation given an assignment: sort the observed cards by
their printed keys in the observation's mode (Stones lowest). If the observed
sequence's printed keys aren't non-increasing, it's 0. Otherwise it's
`Π_g 1/t_g!` over the groups `g` of entries with the same printed key (`t_g`
cards each, face-up ones included), under the **tie policy**:

- **Tie policy.** Unknown tie-breakers are treated as uniformly random. That's
  the original suit, creation order, and Stones' underlying values.
  - So any order within a group of equal printed keys is equally likely.
  - It's a modelling assumption, not the game's rule. The real tie-breakers
    are deterministic, and a player could in principle reconstruct creation
    order.
  - It only matters when tied cards differ in something a target or query
    counts, e.g. a Wild 9♥ and a plain 9♥.
  - Inclusive bounds keep every possibility; this policy says how to weigh
    them.

The posterior is the prior times the likelihood, normalized once. The
normalization depends on the observations alone, not on the discard being
evaluated or the target. What happens after (The Hook's throw-away, the
draw) doesn't reweigh it.

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

### Scope: one observation first

- **Stage A** uses one observation: the last one recorded. Its latent entries
  are restricted to cards still face down in hand.
  - A latent card that has since been revealed (played, or flipped up) has its
    entry replaced by its printed values, which are now public.
  - One discarded face down is left out of the sequence. It's then just
    another latent card with no evidence.
  - Leaving cards out of an observation is conditioning on part of what was
    seen (a subsequence of a sorted order is the sorted order of those cards).
    So the posterior is correct for that information, and the plan doesn't
    claim it uses everything the player saw.
- **Stage B** (later) uses the latest observation in each mode, and keeps
  discarded latent cards in them. This needs:
  - **Fixed tie-breakers across observations.** Two cards of the same printed
    rank and suit tie in both modes, broken the same way both times. So the
    likelihood is the fraction of tie orders consistent with both
    observations, not a product of per-observation factors.
  - **Stones' order** depends on their underlying rank and suit, which differ
    between the modes. Treat each mode's Stone order as independently
    uniform: an approximation, to be stated where it's used.
  - **References** to discarded latent cards (see [Snapshot](#snapshot)).

Every deliberate coarsening is listed here and in the code: the tie policy,
dropping discarded latent cards (stage A), and dropping all observations on
a consumable (see the [state machine](#observation-state-machine)). Each is a
defined subset of what the player saw. None is "exact".

### The Mark's prior

The engine's Mark model (face-down cards are face cards, `_mark_split`)
is an existing approximation:

- With the `flipped_cards` modifier also active, a non-face card can be face
  down.
- Selling Pareidolia, or a Tarot changing a face-down card's rank, can make a
  card's face status now differ from when it was flipped.

The feature must not turn this into an exactness claim:

- Under The Mark, latent cards' candidates are the face cards as today.
- With `flipped_cards` also active, ignore observations (today's odds,
  unchanged) until the Mark model handles it.
- Document the Pareidolia case as a known limitation.

In the restricted model every latent card in hand takes a face card. So the
ways to place the latent cards without evidence, C(face cards left, u), are
the same for every assignment and cancel.

## Information hiding

Observations are made of what the player sees: printed values of face-up
cards, Stones, positions, the sort mode, and public events (draws, Sort
presses, drags, consumables, cards played or discarded). The mod must never
use a latent card's true values to build, keep or drop an observation:

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
only; no deck ids, no sort keys. The unseen pool stays a sorted multiset.

## The mod

### Observation state machine

Two things are kept apart: **evidence** (the last observation, true until an
event makes it false) and **readiness** (whether the current order may be
recorded as a new observation). A drag ends readiness but leaves the
evidence; a consumable ends both.

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
5. **Record:** if *pending*, the observation replaces the evidence when all
   of these hold:
   - this frame's hand is the same card objects in the same order as last
     frame's (stable);
   - `G.STATE == SELECTING_HAND`;
   - the mode is supported;
   - no drag is in progress;
   - the face-up cards are in sorted order by printed values (a safety net
     that leaks nothing).

   Then *pending* clears. A mode change on its own is not a sort.
6. **Departures:** when a latent card in the evidence leaves the hand:
   - if it's now face up (played), its entry becomes its printed values;
   - otherwise (discarded face down), its entry is dropped (stage A).

   A latent card turning face up in hand is treated the same way.

Removing cards (discards, plays, The Hook, Hanged Man) keeps the order of the
rest, so it's no reorder.

Tests drive these orderings explicitly, frame by frame, including a sort and
a drag between two updates, rather than waiting a frame and hoping.

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

The snapshot gains `observations`, a list (at most one entry until stage B):

```json
"observations": [
  {"mode": "desc",
   "cards": [{"rank_id": 10, "suit": "Clubs"}, {"hand": 3}, {"hand": 4},
             {"rank_id": 6, "suit": "Hearts"}, {"stone": true}]}
]
```

- `mode` is `"desc"` or `"suit desc"`.
- `cards` is the observed sequence, left to right. Each entry is exactly one
  of:
  - a face-up card's printed `rank_id` (2..14) and `suit`;
  - `{"stone": true}` for a face-up Stone;
  - `{"hand": i}`: a latent card by its **current** 0-based hand index, which
    must be a `{hidden: true}` card, at most once per observation.
- No observation means the field is an empty array (the encoder's `ARRAY`
  tag; Python also accepts `{}` as usual).
- Stone information can't get lost: a Stone is an entry like any other, and
  its position carries what's known. The first draft's rule of omitting an
  interval with no bounds is gone with the intervals.
- Stage B adds references to discarded latent cards (e.g. `{"gone": n}` with a
  round-local number, never a deck id), defined then.

Bump `schema` in `calculatro.lua` and `SCHEMA` in `state.py` in stage 1, and
make Python parse and validate observations then, even before it uses them.

## Python

### State

`GameState` gains `observations: tuple[Observation, ...]`, frozen and
canonical (tuples, no lists), parsed in `GameState.from_json`.

**Malformed** input raises `MalformedState`:
- an unknown mode;
- a `rank_id` outside 2..14 or an unknown suit;
- an entry that isn't exactly one of the three kinds;
- a `hand` index out of range, not face down, or repeated.

**Well-shaped but impossible** evidence is not malformed. Examples are
face-up entries out of order, or an observation with no consistent assignment
(e.g. a card changed while face down). For those, ignore the observations for
that state and log once per distinct failure on stderr. The fallback is the
same everywhere (Keep, Discard, card queries, search): today's odds, with no
partial use of the evidence.

Candidates keep multiplicity: two 9♥ in the unseen pool are two candidates,
whatever their enhancements.

### Evaluation

A new layer above `engine._discard`, used by `evaluate_discard` and
`evaluate_cards` (and so by `build_report` and the search):

1. **Enumerate** the assignments of the latent cards in the observation to
   unseen cards, with their posterior weight (prior × likelihood). Keep an
   **ungrouped reference** that enumerates actual cards with backtracking. The
   optimized version groups cards into kinds that leave the likelihood, the
   target and the boss rules unchanged, and is tested against the reference.
   - With one observation, each run of k latent cards between face-up
     neighbours takes a sorted k-multiset of what fits between them. Runs only
     share candidates at tied endpoints. So this is nearly a product over runs.
   - Kinds must keep everything the likelihood depends on (printed key, ties),
     as well as what the target counts (as `search._groups`), whether a query
     matches, The Mark's face status, and Cerulean Bell's class.
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
checking feasibility, normalizing. `evaluate_cards` has no `cancel` today, so
add one and thread it through `build_report` and `best_card_discards`. Reject
impossible evidence before enumerating.

Cheaper case for later: when one run's candidates are a single set and
nothing else interacts, `_mark_split` generalized from a face predicate to
any subset could replace the enumeration for it. That's only an optimization,
checked against the reference.

### Best discard

`search.best_discards` and `best_card_discards`:

- **Groups.** `_groups`, and the `"hidden"` group in `best_card_discards`, put
  all face-down cards together. A latent card in an observation has a place of
  its own in a sorted run, so it's a group of its own. Merge two only if the
  posterior is unchanged by swapping them; start with no merging.
- **`_expand` and `_identity`.** These collapse cards alike in every field.
  Two latent cards with evidence must not collapse: their positions mean
  different things. Give `_identity` the hand index for such cards.
- **`_relabelled`.** Observations break the symmetries it uses: printed
  keys distinguish ranks and suits. Don't relabel when the state has
  observations to start with. Later, relabel only if it maps the observations
  onto themselves.
- **`engine.Reuse`.** Key it by the complete residual pool after removing
  every resolved card, with the rules and grouping it depends on. Keep it
  bounded (a small LRU), not one per assignment.
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

- Hands without observations, or whose face-down cards aren't in one: no
  change (no branches).
- Card queries: `count_probability` is cheap, so many branches are fine.
- Targets: one evaluation per branch, after grouping into kinds. That's a few
  for one face-down card in a narrow window, and more for long face-down runs
  in wide windows (The House) or several runs.
- Best discard is the risk: it already evaluates many discards, and Four
  Fingers + Shortcut + Straight Flush takes seconds today.

Benchmarks (`tests/perf/scenarios.py`), worst cases as well as typical ones:

- one and two face-down cards in narrow windows, both modes;
- The House's all-face-down hand, and after its first discard;
- long runs in wide windows;
- duplicates and ties;
- The Mark;
- Four Fingers + Shortcut searches.

Record branch counts, peak cache size, latency and cancellation latency.

Proposed release thresholds, to confirm before stage 3:

- Keep and Discard odds for every tracked target within the panel's 0.25 s
  "Calculating..." threshold for one or two face-down cards in narrow windows.
- A search no more than about 2× the same scenario without observations.
- Cancellation within 50 ms.

## Tests

- **Mod** (`tests/test_state.py`, lupa): extend `MOCK_G` with `G.buttons`
  (sort elements with `last_clicked`), `G.hand.config.sort`, card `facing` and
  `states.drag`, `G.TAROT_INTERRUPT`, and frames advancing. Cases:
  - an observation after draws settle, and after a Sort press;
  - a rebuilt or missing `G.buttons` not counting as a press;
  - a drag ending readiness but keeping the evidence;
  - a sort and a drag between two updates not granting readiness;
  - `PLAY_TAROT` clearing both, and leaving it restoring neither;
  - a no-op consumable clearing too;
  - played latent cards replaced by printed values, discarded ones dropped;
  - resets on a new run, a load, a new blind and a replaced `G.hand`;
  - unsupported modes;
  - the face-up-order safety net;
  - Stones.
- **Information hiding:** keep the public trace fixed (the face-up cards,
  positions, events) and swap which physical cards are face down in hand
  versus in the deck (or face down in the discard). The unseen multiset is
  unchanged, so the **whole sequence of exports, frame by frame**, must be
  identical. That includes resets and no-op consumables. Changing a face-down
  card's values instead legitimately changes the unseen multiset, so it's not
  the test.
- **Python:** parsing and validation, malformed versus impossible evidence,
  and multiplicity.
- **Independent oracle** for the engine. Testing against assignments
  consistent with the observation would just recheck the model's own
  assumptions. Instead, for small decks:
  - deal every way;
  - sort with the game's `get_nominal` rule, with every tie-breaker order
    weighted equally per the tie policy;
  - keep the deals whose public view matches the observation;
  - enumerate discards and draws;
  - compare exact `Fraction`s.

  Start with the [worked example](#worked-example) (1 and 1/3). Cover:
  - duplicates and ties;
  - Wild and Smeared (printed versus scoring suit);
  - Stone-only positions;
  - an all-face-down House hand;
  - both modes, overlapping candidates, impossible evidence;
  - an exhausted deck;
  - The Mark, The Hook, Cerulean Bell, The Serpent.

  Also check that the posterior's mass is 1, and that states without
  observations give today's results exactly. The ungrouped reference is
  checked against the oracle, and the grouped engine against the reference.
- **Search:** the existing every-subset check with observations, with the
  index-sensitive assertions above.

## Stages

1. **Mod: state machine and observations**, plus Python parsing and validation
   (schema bump on both sides), unused by the odds. Tests above, and a check in
   game under The Wheel, The Fish and The House with drags and both Sort
   buttons.
2. **Odds, search and the reference, together:**
   - stage A posterior, ungrouped reference, oracle tests;
   - grouped evaluation, the search changes, cancellation, benchmarks.

   The search must not recommend from a different posterior than the panel
   shows. If the search isn't ready, ship the odds only with the search
   **disabled** for states with observations: the button answers with no
   options and the title row says so.
3. **Stage B:** both modes' latest observations, discarded latent cards kept,
   fixed tie-breakers across observations.
4. **Optional:** the `_mark_split` shortcut; a visible marker of what's known.

Update `CLAUDE.md` (the information-hiding bullet, the snapshot contract, the
probability model) and `docs/blinds.md` (The Wheel, The Fish, The Mark, The
House) as each stage lands.

## Open questions

- The search-disabled message in stage 2, if the search isn't ready with the
  odds (it must be short: the title row's message space is fixed).
- Whether to also record observations in `DRAW_TO_HAND` (every draw sorts),
  or keep `SELECTING_HAND` only; the panel doesn't calculate odds while drawing
  anyway.
- Whether the stage A coarsening of discarded latent cards costs enough in
  practice (The Fish, The Wheel) to bring stage 3 forward.
- How The Mark should handle `flipped_cards` properly (an existing engine
  limitation; this plan only avoids leaning on it).

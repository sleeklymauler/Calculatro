# Balatro Blinds reference

Reference for all 30 blinds in Balatro **1.0.1o-FULL** (2 regular blinds, 23 Boss Blinds and 5 Showdown Blinds), written for Calculatro work. It is the companion to [jokers.md](jokers.md).

Sources:
- Balatro Wiki "Blinds and Antes" page (revision `oldid=30018`), for effect text, minimum antes, rewards and Matador compatibility.
- The game's own Lua (`Balatro.exe` is a LÖVE zip; `game.lua` `P_BLINDS`, `blind.lua`, `functions/state_events.lua`, `functions/common_events.lua`, `cardarea.lua`, `card.lua`), for the internal keys, score multipliers and the exact rules in [Rules that matter to Calculatro](#rules-that-matter-to-calculatro). Where the two disagree, the game code wins.

The mod exports the current blind as `blind` = `G.GAME.blind.name` (the **Name** column below, from `P_BLINDS`: always English, not localised) and `blind_disabled` = `G.GAME.blind.disabled`. `state.py` keeps them as `GameState.blind` / `GameState.blind_disabled`. The internal keys (`bl_hook`, ...) are not exported.

## Summary

| Effect on the odds | Blinds |
|---|---|
| Modelled by the engine | The Serpent, The Mark, Cerulean Bell, The Hook (when the selection has to be played) |
| Handled through the exported state (hand size, hands, discards, debuff flags, face-down cards) | The Manacle, The Water, The Needle, The Club, The Goad, The Window, The Head, The Plant, The Pillar, Verdant Leaf, Crimson Heart, The Wheel, The House, The Fish |
| **Not modelled, odds are wrong** | None |
| Odds are right; the panel strikes out the hands that can't score | The Eye, The Mouth |
| Odds are right but the hand may not score (by design) | The Psychic (only with fewer than 5 cards in hand) |
| No effect | Small Blind, Big Blind, The Ox, The Arm, The Flint, The Tooth, The Wall, Violet Vessel, Amber Acorn |

## Rules that matter to Calculatro

### Disabling a Boss Blind

A boss is disabled by **Chicot** (when the blind is selected, or when Chicot is added during one), by selling **Luchador**, or, for **Verdant Leaf** only, by selling any joker. All of them call `Blind:disable()`, which sets `disabled = true` and undoes the effect:
- Face-down hand cards are flipped face up (The Wheel, The House, The Mark, The Fish) and their `wheel_flipped` flags cleared. Face-down jokers (Amber Acorn) are flipped back.
- The Water gives its discards back and The Needle its hands.
- The Manacle gives its hand size back and **immediately draws 1 card**.
- Cerulean Bell clears `forced_selection`.
- The Wall's chips are halved and Violet Vessel's divided by 3, back to 2x base. The Needle stays at 1x.
- Every card's debuff is recomputed, which clears the boss's debuffs.

Every boss effect checks `disabled`, so a disabled boss behaves like a Small Blind (apart from its score and reward). `GameState.serpent`, `GameState.mark` and `GameState.bell` check `blind_disabled`; any new boss rule must do the same.

### Face-down cards (`Blind:stay_flipped`)

When a card is drawn to the hand, `draw_card` asks `Blind:stay_flipped(area, card)`; if it returns true, the card stays face down and gets `ability.wheel_flipped = true` (`CardArea:emplace`). Four bosses use it:

| Boss | Which cards stay face down |
|---|---|
| The Wheel | Each drawn card, independently, with probability `G.GAME.probabilities.normal / 7` (Oops! All 6s doubles it, so 2 in 7, 4 in 7, ...). |
| The House | Every card drawn while `hands_played == 0 and discards_used == 0`: the opening hand. |
| The Fish | Every card drawn after a hand is played. `press_play` sets `prepped`, and `drawn_to_hand` clears it after the draw, so cards drawn after a discard are face up. The opening hand is face up. |
| The Mark | Every card for which `card:is_face(true)` is true: Jacks, Queens and Kings **whether debuffed or not**, or every card (Stone Cards included) with Pareidolia. |

The mod exports face-down hand cards as `{hidden: true}`, and cards discarded face down too (the game doesn't turn them up when they're discarded). Their identities go out only merged with the undrawn deck, sorted, in `unseen`, so which cards are face down, or were discarded face down, never shows; the engine draws the kept face-down cards from that pool along with the new cards (`_discard`), and the deck's size is what's left once the face-down cards, in hand and discarded, are taken out (`GameState.deck_size`, `hidden`, `discarded_hidden`). The game agrees that face-down cards count as unseen: its View Deck screen counts `wheel_flipped` cards with the undrawn deck (`UI_definitions.lua`, "unplayed only").

This is exact only when **whether a card is face down doesn't depend on what it is**, so The Mark is modelled apart:
- **The Wheel, The House and The Fish:** correct. A card is face down because of a coin flip or the moment it was drawn, never its identity, so from the player's view the face-down cards are a uniform sample of the unseen pool.
- **The Mark: modelled.** A face-down card is known to be a face card, and a face-up card is known not to be. The face-down cards are a uniform sample of the unseen *face cards* (by `is_face(true)`), and the deck is what's left. Drawing them from the whole unseen pool would be wrong in both directions: a target that wants the face-down cards to be Kings would be underestimated, and a card query for face cards wrong too (the face-down cards counted as any unseen card, and their face cards as still drawable). `GameState.mark` (the boss, not disabled) makes `engine._discard` pass the number of face-down cards (`marked`: those in hand, whether kept or discarded now, and those discarded face down before, since all of them have left the deck) to `probability` and `count_probability` (with how many of them are kept, `marked_kept`), which draw the face-down cards from the unseen face cards (`engine.flipped_by_mark`) and the new cards from the rest of the pool (`_mark_split`). No extra export is needed: the face cards are found in `unseen`. With Pareidolia (as long as it isn't debuffed) every card is a face card, so the face-down cards are again uniform and the plain model is used. Edge cases, all rare and not modelled: a face-down card whose rank was changed afterwards (Strength, Death; if that leaves fewer unseen face cards than face-down cards, the plain model is used), or Pareidolia gained or lost mid-round.

### Card debuffs (`Blind:debuff_card`)

`Blind:set_blind` and `Blind:disable` run `debuff_card` on every card in `G.playing_cards` (the deck as well as the hand), and `Card:set_base`, `Card:set_ability`, `Card:change_suit` and Certificate re-run it on cards that change, so the exported `debuff` flag of every card is always the boss's verdict.

| Boss | Debuffs |
|---|---|
| The Club / The Goad / The Window / The Head | Cards with `is_suit(suit, true)` for Clubs / Spades / Diamonds / Hearts. This is the *normal* suit mode with `bypass_debuff`, so **every Wild Card is debuffed by all four**, Smeared Joker extends it to the other suit of the colour (The Club then debuffs Spades too), and Stone Cards are never debuffed. |
| The Plant | Cards with `is_face(true)`: Jacks, Queens and Kings, or **every card** (Stone and Wild included) with Pareidolia. |
| The Pillar | Cards with `ability.played_this_ante`: set on each card played, cleared once the ante's Boss Blind is beaten. |
| Verdant Leaf | Every playing card, until a joker is sold (which disables it). Jokers are not debuffed. |

What a debuff changes, per [jokers.md](jokers.md#how-hands-are-evaluated-evaluate_poker_hand):
- **Ranks:** nothing. `get_id` ignores debuff, so pairs, straights and so on form as usual.
- **Flushes:** a debuffed non-Wild card still counts toward flushes in its base suit (`is_suit` with `flush_calc`). A debuffed Wild Card loses its wildness and counts as its base suit only. So the suit bosses never stop a flush from forming; they only turn Wild Cards back into ordinary cards. `hands.card_key` and `cards._has_suit` already follow this.
- **Face cards:** a debuffed card is never a face card (`Card:is_face`), Pareidolia or not, so face-card queries ("In hand" rows) count none of them (`cards._is_face`). Under The Plant and Verdant Leaf, "1+ face card" is therefore always 0%, as the game's face-card jokers see it, even though Kings ("1+ king") are still counted. Rank and enhancement queries ignore debuffs.
- **Scoring:** debuffed cards score no chips and trigger nothing. Calculatro computes whether a hand type can be formed, not its score, so this doesn't matter.

Nothing here needs special handling in the engine: the debuff flags arrive with the cards.

### Jokers debuffed or flipped

- **Crimson Heart** debuffs one random joker in `drawn_to_hand` when `prepped`: on the opening draw and after every hand played, **not** after discards. It picks from the jokers that weren't debuffed (unless there's only one), so the same joker is never hit twice in a row. A debuffed rule joker (Four Fingers, Shortcut, Smeared, Pareidolia) has no effect, and a debuffed hand-size joker loses its bonus (`remove_from_deck`). Both arrive in the exported state (`Joker.debuff`, skipped by `Rules.from_jokers`; `hand_size`). Since the debuffed joker only changes on a play, the odds of a discard's draw are computed under the right rules.
- **Amber Acorn** flips every joker face down and shuffles them. `find_joker` doesn't check `facing`, so the jokers keep working and the rules are unchanged, and the player knows which jokers they own. The mod exports the jokers sorted (like the deck), never in `G.jokers.cards` order, so the shuffled positions the player can't see don't leak. Nothing in the engine uses joker order; anything that ever needs it (Blueprint, Brainstorm) can't get it from the export.

### Draws

- **The Serpent** (`G.FUNCS.draw_from_deck_to_hand`): once the round has had a play or a discard (`hands_played > 0 or discards_used > 0`), every draw is `min(#deck, 3)` cards, however many were played or discarded and even past the hand size. The opening hand is drawn as usual. **Modelled:** `GameState.serpent` (the boss, not disabled) makes `engine._discard` draw exactly 3 after any discard, and 0 when keeping the hand after a play or discard (the hand isn't refilled until the next one). `hands_played` and `discards_used` are exported for this.
- **The Hook** (`Blind:press_play`): when a hand is played, discards 2 random cards from the cards left in hand, before the refill. It's not a real discard (`discards_used` is unchanged, and jokers see `hook = true`). The odds of discarding the selection assume a discard while there are any left; without one, the selection can only be got rid of by playing it (the best-discard search does this when another hand is left after it; on the last hand there are no discard odds at all). **Modelled** then: `GameState.hook` (the boss, not disabled) with `discards_left == 0` makes `engine._discard` draw 2 more (`hooked`, fewer if fewer cards are kept), and `engine._after_hook` averages the odds over which 2 kept cards it takes, uniformly at random, face-down ones included. Keeping the hand plays nothing, so it's unaffected.

### Hand size, hands and discards

These are already reflected in the exported `hand_size` (`G.hand.config.card_limit`), `hands_left` and `discards_left`, so the engine doesn't model them:
- **The Manacle:** `G.hand:change_size(-1)` when the blind is set, given back when it's defeated or disabled.
- **The Water:** sets the round's discards to 0 (`discards_sub`), so the selection's odds are for playing it, which also draws cards back (`search.can_redraw`); on the last hand nothing is drawn after it, so the panel has no discard odds to show. It doesn't say so, since the game already makes it clear.
- **The Needle:** sets the round's hands to 1 (`hands_sub`). Hands left don't affect the odds of the next draw.

### Hands that don't score (`Blind:debuff_hand`)

Calculatro's targets mean "the final hand *contains* this hand type" (`hands.py`). These bosses don't stop a hand from being formed or played; they make it score nothing (the hand still counts as played):
- **The Psychic** (`h_size_ge = 5`): fewer than 5 cards played. Adding cards to a selection of at most 5 never loses a hand type it contains (a Pair padded with a matching card becomes Three of a Kind, which contains a Pair), so any target found in a hand of 5 or more cards can be played as 5 cards. **No effect** unless the final hand has fewer than 5 cards (hand size below 5, or the deck running out); the target can't score then, but the odds still count it. **Left as is by design:** the odds still answer what they claim (the final hand contains the target), the player can see when they hold fewer than 5 cards, and it only happens near the end of a round. The panel's alternatives (a per-column message, greyed or 0% values) weren't worth the extra UI.
- **The Eye:** a hand type already played this round (`G.GAME.blind.hands[name]`).
- **The Mouth:** any hand type other than the first one played this round (`G.GAME.blind.only_hand`). The comparison is on the hand's exact type, so after a Pair, Two Pair doesn't score.

For The Eye and The Mouth the odds are right, but a target the boss has banned can't score. **The panel strikes it out:** the mod reads the bans from `G.GAME.blind` (`Calculatro.banned_hands`; nothing is exported, so the app is unchanged) and draws a banned target's name greyed with a line through it, still with its odds. A Royal Flush is played as a Straight Flush (only its displayed name differs), so the two are banned together by The Eye and both allowed by a Mouth on Straight Flush. "Any target" is struck out only when every tracked hand is. The player sees both lists: The Mouth names its hand in the blind's text, and they know what they've played. A banned target can still be part of a hand that scores: after a Pair under The Eye, Three of a Kind still scores, though it contains a Pair.

### Forced selection (Cerulean Bell)

**Modelled.** In `drawn_to_hand`, if no hand card has `ability.forced_selection`, Cerulean Bell unhighlights the hand, picks a uniformly random hand card (face-down ones included), flags it and highlights it. `CardArea:remove_from_highlighted` refuses to unhighlight it, so it is part of **every play and discard**. Playing or discarding clears the flag for every card, so the next draw picks a new one, from the whole new hand.

Effect on the odds:
- **Discard:** the selection always includes the forced card, so `GameState.selected` already discards it, and the draw count is right.
- **Targets:** any hand played must include the forced card, so a target counts only if it fits in 5 cards *together with* the forced card. Adding a card to 4 or fewer never loses a contained hand type (see The Psychic), with one exception: with Four Fingers, a 4-card Royal Flush plus a card below 10 that is of its suit or extends its run (9, or 8 with Shortcut) scores as a plain Straight Flush. So targets of 4 cards or fewer (Pair, Two Pair, Three of a Kind, Four of a Kind; Flush, Straight and Straight Flush with Four Fingers) are unaffected. Targets that need 5 cards (Straight, Flush, Full House, Straight Flush, Flush House, Five of a Kind, Flush Five) need the forced card to be one of them.
- **How it's modelled:** the mod exports the flag as each hand card's `forced` (`GameState.forced`; the player sees the card raised and can't unselect it), and `GameState.bell` is the boss, not disabled. `hands.forced_patterns` turns a target into patterns for the hand without the forced card: a pattern the forced card counts towards needs one card fewer; one it doesn't count towards stays only if it needs at most 4 cards (and isn't a 4-card Royal Flush the card spoils, `_spoils`). Four Fingers' Straight Flush has overlapping requirements, which are split into disjoint ones first (`_disjoint`), so a pattern's size is the sum of its requirements. When keeping, the forced card is the one already highlighted (`engine.evaluate_discard`); after a discard it is a uniformly random card of the final hand, so the odds are the average over the final hand's cards of the odds with that card forced (`engine._bell_after_draw`): one search per kept card, and one per group of unseen cards that the forced patterns can't tell apart. Targets with no 5-card pattern skip all this (`hands.bell_matters`).

### No effect

Scoring, money and hand levels aren't part of the odds, so these bosses change nothing:
- **The Ox** (playing the most played hand sets money to $0), **The Arm** (lowers the played hand's level), **The Flint** (halves base chips and mult, `Blind:modify_hand`), **The Tooth** (−$1 per card played).
- **The Wall** (4x base) and **Violet Vessel** (6x base): only the score requirement. **The Needle**'s 1x score is the same.
- **Small Blind** and **Big Blind**.

### "Calc" tags in the table

- `draw`: changes how many cards are drawn.
- `hidden`: draws cards face down.
- `debuff`: debuffs playing cards (arrives as each card's `debuff` flag).
- `joker`: debuffs or flips jokers.
- `size`: changes hand size, hands or discards. Already reflected in the exported state.
- `play`: makes some hands score nothing; doesn't change whether they can be formed.
- `forced`: forces a card into every play and discard.

## General facts

- **Score required:** `get_blind_amount(ante) * mult * ante_scaling` (`Blind:set_blind`), where `mult` is the blind's multiplier (the **Score** column) and `ante_scaling` is 2 on Plasma Deck and 1 otherwise.
- **Rewards:** $3 for the Small Blind, $4 for the Big Blind, $5 for a Boss Blind, $8 for a Showdown Blind. From Red Stake up the Small Blind pays nothing (`G.GAME.modifiers.no_blind_reward`).
- **Ante:** a Small Blind, a Big Blind and a Boss Blind. Small and Big Blinds can be skipped for a Tag; Boss Blinds can't. Beating the Ante 8 Showdown Blind wins the run; Endless Mode continues from Ante 9.
- **Choosing the boss** (`get_new_boss`):
  - A regular boss is eligible once the ante reaches its `boss.min` (antes below 1 count as 1, so Ante 0 and lower draw from the 8 Ante-1 bosses), except on antes that are a multiple of `win_ante` (8).
  - Showdown Blinds are eligible only on antes that are a multiple of 8 (8, 16, 24, ...). Their `boss.min` of 10 in `P_BLINDS` is unused.
  - Of the eligible bosses, only the ones used the fewest times (`G.GAME.bosses_used`) can be picked, so no boss repeats until every other has appeared (by being defeated or rerolled).
  - Director's Cut and Retcon reroll the boss; rerolling on a multiple of 8 always gives another Showdown Blind.
- **Matador** earns $8 when the played hand triggers the boss (`G.GAME.blind.triggered`); the **Matador** column says which bosses can.

### Base chip requirement per ante

| Ante | Base | Green Stake and up | Purple Stake and up |
|---|---|---|---|
| 0 and lower | 100 | 100 | 100 |
| 1 | 300 | 300 | 300 |
| 2 | 800 | 900 | 1,000 |
| 3 | 2,000 | 2,600 | 3,200 |
| 4 | 5,000 | 8,000 | 9,000 |
| 5 | 11,000 | 20,000 | 25,000 |
| 6 | 20,000 | 36,000 | 60,000 |
| 7 | 35,000 | 60,000 | 110,000 |
| 8 | 50,000 | 100,000 | 200,000 |

From Ante 9 the requirement grows faster than exponentially; Ante 39 overflows to `nan` and can't be beaten.

## All blinds

Columns:
- **Name** is `G.GAME.blind.name`, as exported. **Key** is the `P_BLINDS` key.
- **Min ante** is `boss.min` ("Any" for 1). Showdown Blinds appear on Ante 8, 16, 24, ...
- **Score** is the blind's `mult` × the ante's base chips.
- **Matador** = the boss can trigger Matador (from the wiki).
- **Odds** is how Calculatro handles it; see the sections above.

| # | Name | Key | Min ante | Score | Reward | Effect | Matador | Calc | Odds |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Small Blind | `bl_small` | Any | 1x | $3 | No effect; can be skipped for a Tag | N |  | No effect |
| 2 | Big Blind | `bl_big` | Any | 1.5x | $4 | No effect; can be skipped for a Tag | N |  | No effect |
| 3 | The Hook | `bl_hook` | Any | 2x | $5 | Discards 2 random cards per hand played | N | draw | Handled (selection played without discards left) |
| 4 | The Ox | `bl_ox` | 6 | 2x | $5 | Playing your most played hand sets money to $0 | Y |  | No effect |
| 5 | The House | `bl_house` | 2 | 2x | $5 | First hand is drawn face down | N | hidden | Handled (hidden pool) |
| 6 | The Wall | `bl_wall` | 2 | 4x | $5 | Extra large blind | N |  | No effect |
| 7 | The Wheel | `bl_wheel` | 2 | 2x | $5 | 1 in 7 cards get drawn face down | N | hidden | Handled (hidden pool) |
| 8 | The Arm | `bl_arm` | 2 | 2x | $5 | Decrease level of played poker hand | Y |  | No effect |
| 9 | The Club | `bl_club` | Any | 2x | $5 | All Club cards are debuffed | Y | debuff | Handled (debuff flags) |
| 10 | The Fish | `bl_fish` | 2 | 2x | $5 | Cards drawn face down after each hand played | N | hidden | Handled (hidden pool) |
| 11 | The Psychic | `bl_psychic` | Any | 2x | $5 | Must play 5 cards | Y | play | No effect with 5+ cards in hand; under 5, odds still count (by design) |
| 12 | The Goad | `bl_goad` | Any | 2x | $5 | All Spade cards are debuffed | Y | debuff | Handled (debuff flags) |
| 13 | The Water | `bl_water` | 2 | 2x | $5 | Start with 0 discards | N | size | Handled (`discards_left`) |
| 14 | The Window | `bl_window` | Any | 2x | $5 | All Diamond cards are debuffed | Y | debuff | Handled (debuff flags) |
| 15 | The Manacle | `bl_manacle` | Any | 2x | $5 | −1 hand size | N | size | Handled (`hand_size`) |
| 16 | The Eye | `bl_eye` | 3 | 2x | $5 | No repeat hand types this round | Y | play | Banned hands struck out in the panel |
| 17 | The Mouth | `bl_mouth` | 2 | 2x | $5 | Play only 1 hand type this round | Y | play | Banned hands struck out in the panel |
| 18 | The Plant | `bl_plant` | 4 | 2x | $5 | All face cards are debuffed | Y | debuff | Handled (debuff flags) |
| 19 | The Serpent | `bl_serpent` | 5 | 2x | $5 | After a play or discard, always draw 3 cards | N | draw | **Modelled** (`GameState.serpent`) |
| 20 | The Pillar | `bl_pillar` | Any | 2x | $5 | Cards played previously this ante are debuffed | Y | debuff | Handled (debuff flags) |
| 21 | The Needle | `bl_needle` | 2 | 1x | $5 | Play only 1 hand | N | size | Handled (`hands_left`) |
| 22 | The Head | `bl_head` | Any | 2x | $5 | All Heart cards are debuffed | Y | debuff | Handled (debuff flags) |
| 23 | The Tooth | `bl_tooth` | 3 | 2x | $5 | Lose $1 per card played | N |  | No effect |
| 24 | The Flint | `bl_flint` | 2 | 2x | $5 | Base chips and mult are halved | Y |  | No effect |
| 25 | The Mark | `bl_mark` | 2 | 2x | $5 | All face cards are drawn face down | N | hidden | **Modelled** (face-down cards are face cards, `GameState.mark`) |
| 26 | Amber Acorn | `bl_final_acorn` | Showdown | 2x | $8 | Flips and shuffles all Joker cards | N | joker | No effect (jokers exported sorted) |
| 27 | Verdant Leaf | `bl_final_leaf` | Showdown | 2x | $8 | All cards debuffed until 1 Joker sold | Y | debuff | Handled (debuff flags) |
| 28 | Violet Vessel | `bl_final_vessel` | Showdown | 6x | $8 | Very large blind | N |  | No effect |
| 29 | Crimson Heart | `bl_final_heart` | Showdown | 2x | $8 | One random Joker disabled every hand | N | joker | Handled (`Joker.debuff`) |
| 30 | Cerulean Bell | `bl_final_bell` | Showdown | 2x | $8 | Forces 1 card to always be selected | N | forced | **Modelled** (`GameState.forced`, `hands.forced_patterns`) |

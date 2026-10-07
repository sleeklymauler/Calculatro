# Balatro Jokers reference

Reference for all 150 jokers in Balatro **1.0.1o-FULL**, written for Calculatro work.

Sources:
- Balatro Wiki "Jokers" page (revision `oldid=29533`), for effect text, joker type, activation and unlock conditions.
- The game's own Lua (`Balatro.exe` is a LÖVE zip; `game.lua` `P_CENTERS`, `localization/en-us.lua`, `card.lua`, `functions/misc_functions.lua`), for the internal keys, rarity, cost, Blueprint compatibility and the exact hand-evaluation rules in [Rules that matter to Calculatro](#rules-that-matter-to-calculatro). Where the two disagree, the game code wins.

The mod exports jokers as `{key, debuff}`, with `key` = `card.config.center_key` (the **Key** column below). Display names and keys don't always match: Canio is `j_caino`, Showman is `j_ring_master`, Gluttonous Joker is `j_gluttenous_joker`, Seltzer is `j_selzer`, Golden Ticket is `j_ticket`, and so on.

## Rules that matter to Calculatro

### How the game checks for a joker

Rule-changing jokers are checked with `find_joker(name)`, which matches on `card.ability.name` and **skips debuffed jokers**. A debuffed joker also runs `remove_from_deck`, so its hand-size or discard bonus is removed while it stays debuffed. For example, the Crimson Heart boss debuffs a random joker each hand.

Consequences:
- A rule joker that is debuffed has no effect. `state.py` keeps the exported `debuff` flag (`Joker.debuff`), and `Rules.from_jokers` (`rules.py`) skips debuffed jokers.
- Having more than one copy (through Showman or Invisible Joker) changes nothing for rule jokers; the check is just "is one present".
- Blueprint and Brainstorm can't give you Four Fingers, Shortcut, Smeared, Pareidolia or Splash. All of them have `blueprint_compat = false`, and the check is by name anyway.

### How hands are evaluated (`evaluate_poker_hand`)

The function takes the played cards (at most 5) and builds the following parts:
- `_5`, `_4`, `_3`, `_2` (`get_X_same`): groups of exactly N cards that share `get_id()`. Stone cards get a random negative id, so they never match anything. **Debuff does not matter for ranks.**
- `_flush` (`get_flush`): needs `5 - FF` cards that are `is_suit(s, nil, flush_calc=true)` for a single suit `s`, where FF = 1 if Four Fingers is present. It returns only the suited cards. It returns nothing if the hand has more than 5 or fewer than `5 - FF` cards.
- `_straight` (`get_straight`): see below. It has the same card-count limits.

Hand types are then decided by:

| Hand | Condition |
|---|---|
| Flush Five | `_5` and `_flush` |
| Flush House | `_3` and `_2` and `_flush` |
| Five of a Kind | `_5` |
| Straight Flush | `_flush` and `_straight`. **The two can be made of different cards**; the scoring set is their union. |
| Four of a Kind | `_4` |
| Full House | `_3` and `_2` |
| Flush / Straight / Three of a Kind | the matching part |
| Two Pair | two `_2` groups, or one `_3` plus one `_2` |
| Pair | `_2` |

Afterwards, a Five of a Kind also fills Four of a Kind, which fills Three of a Kind, which fills Pair. Two Pair is **not** filled by Four or Five of a Kind. This is the "contains" relation that jokers like Jolly Joker and The Duo test against.

### Suit rules (`Card:is_suit`)

There are two modes:
- **flush_calc = true** (used by `get_flush` and Blackboard):
  - Stone cards are never any suit.
  - A *non-debuffed* Wild card is every suit.
  - With Smeared Joker, Hearts and Diamonds match each other, and Spades and Clubs match each other.
  - Otherwise the card's base suit is used. **Debuffed non-Wild cards still count toward flushes.**
- **Normal mode** (suit-based scoring jokers, Flower Pot, Seeing Double):
  - A debuffed card is no suit (unless `bypass_debuff` is passed).
  - Stone cards are never any suit, and Wild cards are every suit.
  - Smeared works the same way as in flush_calc.

### Four Fingers (`j_four_fingers`)

- A Flush needs only 4 cards of one suit, and a Straight needs only 4 consecutive ranks. You can still play 5 cards; the fifth card just won't score unless it is a Stone card or Splash is present.
- **Straight Flush:** with Four Fingers the flush cards and the straight cards don't have to be the same cards. Any played set of at most 5 cards that has 4 cards of one suit **and** a 4-rank straight counts. For example, `2♥ 3♥ 4♥ 9♥ 5♠` has a flush of the 4 hearts and a straight of 2-3-4-5, so it is a Straight Flush.
  - The engine's requirement is therefore "some at-most-5-card subset with ≥4 suited and ≥4 in sequence" (so at least 3 cards overlap). It is not "4 suited cards in sequence".
- **Flush House and Flush Five:** these become "the full house or five of a kind, with 4 of the 5 cards sharing a suit". In a standard deck the three-of-a-kind cards all have different suits, so this needs Wild cards, Smeared Joker or duplicate cards.

### Shortcut (`j_shortcut`)

`get_straight` scans the ranks in the order A(as 1), 2, 3, …, K, A(as 14). Each rank that is present adds to the run. With Shortcut, **one missing rank between two present ranks** is skipped without breaking the run. The skip allowance resets after every present rank, so `10 8 6 4 2` is a straight. Two missing ranks in a row break the run. A straight needs `5 - FF` present ranks in one run.

In other words, a straight exists when there is a chain of `5 - FF` distinct present ranks in which each step is +1, or +1 or +2 with Shortcut. Ace counts as low or high, and the chain never wraps (no K-A-2). Stone cards have no rank. Duplicate ranks don't lengthen the run, but all of those cards are part of the straight's scoring cards.

### Smeared Joker (`j_smeared`)

For flushes, the suits collapse to Red (♥/♦) and Black (♠/♣). For suit-based jokers, a ♥ card counts as both ♥ and ♦, and so on. Rank is unaffected.

In engine terms, a card's flush class becomes `(rank, "Red"|"Black")`, and Wild stays Wild. A debuffed Wild card loses its wildness but still counts as its base colour.

Edge case: Flower Pot assigns each card to the first suit that hasn't been claimed yet, so under Smeared two red cards plus two black cards satisfy it.

### Pareidolia (`j_pareidolia`)

`Card:is_face()` returns true for **every non-debuffed card**, Stone cards included. Debuffed cards are never face cards, whether or not Pareidolia is present. This affects every joker or query that checks for face cards (Scary Face, Smiley Face, Sock and Buskin, Business Card, Photograph, Ride the Bus, Faceless Joker, Reserved Parking, Midas Mask, Canio), as well as boss blinds such as The Plant.

### Splash (`j_splash`)

Every played card is added to the scoring hand. It does not change which hand type is detected.

### Hand size, hands and discards

These jokers are all already reflected in the exported `hand_size` (`G.hand.config.card_limit`), `hands_left` and `discards_left`, so the engine doesn't need to model them:
- Hand size: Juggler +1, Turtle Bean +5 shrinking by 1 per round, Troubadour +2, Stuntman −2, Merry Andy −1. All of these go through `G.hand:change_size`.
- Discards: Drunkard +1, Merry Andy +3 (both change `round_resets.discards`), and Burglar (sets discards to 0 and gives +3 hands when the Blind is selected).
- Hands: Troubadour −1.

### Jokers that add, remove or change playing cards

These change the deck or hand outside the draw. Because the exported state is a snapshot, they only matter if we predict across one of these events:
- Marble Joker adds a Stone card to the deck when a Blind is selected.
- DNA: if the first hand of the round is a single card, a copy goes into the deck and is drawn to the hand.
- Certificate adds a random sealed card to the hand when the round begins.
- Sixth Sense destroys a single 6 played as the first hand of the round.
- Trading Card destroys a single card used as the first discard of the round.
- Vampire strips enhancements from scored cards.
- Midas Mask turns scored face cards into Gold cards.

### Per-round targets (needed to evaluate these jokers' conditions)

| Joker | Where the current target lives |
|---|---|
| The Idol | `G.GAME.current_round.idol_card` = `{rank, suit, id}`, chosen from non-Stone cards in `G.playing_cards` |
| Mail-In Rebate | `G.GAME.current_round.mail_card` = `{rank, id}` (non-Stone) |
| Castle | `G.GAME.current_round.castle_card.suit` (from a non-Stone card) |
| Ancient Joker | `G.GAME.current_round.ancient_card.suit` (always a different suit from last round) |
| To Do List | `joker.ability.to_do_poker_hand`, stored on that joker card |

### Probabilities shown on cards

"1 in N" effects actually use `G.GAME.probabilities.normal` in N. **Oops! All 6s** doubles that value each time it is added, so copies stack. This doesn't affect draw odds; it only matters if we ever model Lucky, Glass, Bloodstone, Business Card and similar effects.

### "Calc" tags in the table

- `rule`: changes how hands are detected or how cards are classified. Can't be ignored when computing odds: Four Fingers, Shortcut, Smeared, Pareidolia, Splash.
- `size`: changes hand size, hands or discards. Already reflected in the exported state.
- `deck`: adds, removes or changes playing cards.
- `cond`: the effect depends on what is played, held or discarded. These are candidates for "chance this joker triggers" targets.

## Terminology (from the wiki)

- **When scored:** triggers only for scoring cards. Cards that aren't part of the scoring hand (without Splash) or are debuffed don't trigger it.
- **Contains vs. is:** "contains a Pair" uses the containment rules above, so a Three of a Kind contains a Pair. "Is a Pair" means the hand's top type is Pair.
- **In deck vs. in full deck:** "deck" means the cards remaining to draw. "Full deck" means every card the player owns, whether drawn, in hand, played or discarded (`G.playing_cards`). For Erosion, it counts cards removed from the starting deck.
- **When Blind is selected:** doesn't trigger when the Blind is skipped. Madness triggers only on the Small and Big Blinds.
- **Add / Destroy / Create:** these are permanent. Destroying a joker gives no sell value. Creating needs free room, except for Perkeo's Negative copies.
- **Gains:** a permanent change, usually to the joker itself (Hiker changes the cards instead).
- **Retrigger:** scores the card again, including enhancements, editions, seals (except Red Seal) and other jokers. Mime retriggers held-in-hand abilities only.
- **Debuffed (joker):** the joker and its edition don't trigger, but it keeps its sell value. Random effects can still target it, but Crimson Heart doesn't. Jokers that reference other jokers, such as Baseball Card, still see it.
- **Activation column:** Indep. means it triggers once after the cards are scored. On Scored, On Held, On Played, On Discard, On Blind Select and On Other Jokers name the moment it fires. Mixed means it scales at one moment and pays out at another. N/A means a passive rule.

## General facts

- **Rarity:** Common 61, Uncommon 64, Rare 20, Legendary 5.
  - Shop and random generation odds are Common 70%, Uncommon 25% and Rare 5%.
  - Legendary jokers come only from The Soul spectral card (about 0.3% per Arcana or Spectral pack card). Their base cost is $20 but they are never sold in the shop.
- **Joker editions:** Foil (+50 Chips), Holographic (+10 Mult), Polychrome (X1.5 Mult), Negative (+1 Joker slot).
  - Stickers at higher stakes: Eternal, Perishable, Rental.
  - Editions can be added to existing jokers only by Wheel of Fortune, Ectoplasm or Hex.
- **Joker slots:**
  - 5 by default; 6 on Black Deck and 4 on Painted Deck.
  - Antimatter adds +1, and each Negative joker effectively adds +1.
  - Some challenges change the count: Jokerless and Typecast (after Ante 4) set it to 0, Cruelty to 3, Blast Off to 4 and Five-Card Draw to 7.
- **Duplicates:** a joker that is already on screen won't appear again unless you have Showman (Ankh and Invisible Joker can still duplicate). If every joker of a rarity is already on screen, the plain "Joker" is generated instead.
- 105 jokers are unlocked from the start; the other 45 have unlock conditions (last column).

## All jokers

Columns:
- **Rarity** and **Cost** are the game's base values.
- **BP** = Blueprint/Brainstorm can copy it.
- **Type** is the wiki's category.
- An Unlock of `-` means it is available from the start.
- Bracketed values like `[suit]` change during the run; see the per-round targets above.

| # | Joker | Key | Rarity | Cost | Effect | Type | Activation | BP | Calc | Unlock |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Joker | `j_joker` | Common | $2 | +4 Mult | +Mult | Indep. | Y |  | - |
| 2 | Greedy Joker | `j_greedy_joker` | Common | $5 | Played cards with Diamond suit give +3 Mult when scored | +Mult | On Scored | Y | cond | - |
| 3 | Lusty Joker | `j_lusty_joker` | Common | $5 | Played cards with Heart suit give +3 Mult when scored | +Mult | On Scored | Y | cond | - |
| 4 | Wrathful Joker | `j_wrathful_joker` | Common | $5 | Played cards with Spade suit give +3 Mult when scored | +Mult | On Scored | Y | cond | - |
| 5 | Gluttonous Joker | `j_gluttenous_joker` | Common | $5 | Played cards with Club suit give +3 Mult when scored | +Mult | On Scored | Y | cond | - |
| 6 | Jolly Joker | `j_jolly` | Common | $3 | +8 Mult if played hand contains a Pair | +Mult | Indep. | Y | cond | - |
| 7 | Zany Joker | `j_zany` | Common | $4 | +12 Mult if played hand contains a Three of a Kind | +Mult | Indep. | Y | cond | - |
| 8 | Mad Joker | `j_mad` | Common | $4 | +10 Mult if played hand contains a Two Pair | +Mult | Indep. | Y | cond | - |
| 9 | Crazy Joker | `j_crazy` | Common | $4 | +12 Mult if played hand contains a Straight | +Mult | Indep. | Y | cond | - |
| 10 | Droll Joker | `j_droll` | Common | $4 | +10 Mult if played hand contains a Flush | +Mult | Indep. | Y | cond | - |
| 11 | Sly Joker | `j_sly` | Common | $3 | +50 Chips if played hand contains a Pair | Chips | Indep. | Y | cond | - |
| 12 | Wily Joker | `j_wily` | Common | $4 | +100 Chips if played hand contains a Three of a Kind | Chips | Indep. | Y | cond | - |
| 13 | Clever Joker | `j_clever` | Common | $4 | +80 Chips if played hand contains a Two Pair | Chips | Indep. | Y | cond | - |
| 14 | Devious Joker | `j_devious` | Common | $4 | +100 Chips if played hand contains a Straight | Chips | Indep. | Y | cond | - |
| 15 | Crafty Joker | `j_crafty` | Common | $4 | +80 Chips if played hand contains a Flush | Chips | Indep. | Y | cond | - |
| 16 | Half Joker | `j_half` | Common | $5 | +20 Mult if played hand contains 3 or fewer cards | +Mult | Indep. | Y | cond | - |
| 17 | Joker Stencil | `j_stencil` | Uncommon | $8 | X1 Mult for each empty Joker slot; Joker Stencil itself counts as empty | xMult | Indep. | Y |  | - |
| 18 | Four Fingers | `j_four_fingers` | Uncommon | $7 | All Flushes and Straights can be made with 4 cards | Effect | N/A | N | rule | - |
| 19 | Mime | `j_mime` | Uncommon | $5 | Retrigger all card held in hand abilities | Retrigger | On Held | Y |  | - |
| 20 | Credit Card | `j_credit_card` | Common | $1 | Go up to -$20 in debt | Economy | N/A | N |  | - |
| 21 | Ceremonial Dagger | `j_ceremonial` | Uncommon | $6 | When Blind is selected, destroy Joker to the right and permanently add double its sell value to this Mult (starts +0) | +Mult | Indep. | Y |  | - |
| 22 | Banner | `j_banner` | Common | $5 | +30 Chips for each remaining discard | Chips | Indep. | Y |  | - |
| 23 | Mystic Summit | `j_mystic_summit` | Common | $5 | +15 Mult when 0 discards remaining | +Mult | Indep. | Y |  | - |
| 24 | Marble Joker | `j_marble` | Uncommon | $6 | Adds one Stone card to the deck when Blind is selected | Effect | On Blind Select | Y | deck | - |
| 25 | Loyalty Card | `j_loyalty_card` | Uncommon | $5 | X4 Mult every 6 hands played (counter shows hands remaining) | xMult | Indep. | Y |  | - |
| 26 | 8 Ball | `j_8_ball` | Common | $5 | 1 in 4 chance for each played 8 to create a Tarot card when scored (must have room) | Effect | On Scored | Y | cond | - |
| 27 | Misprint | `j_misprint` | Common | $4 | +0 to +23 Mult (random) | +Mult | Indep. | Y |  | - |
| 28 | Dusk | `j_dusk` | Uncommon | $5 | Retrigger all played cards in final hand of the round | Retrigger | On Scored | Y |  | - |
| 29 | Raised Fist | `j_raised_fist` | Common | $5 | Adds double the rank of the lowest ranked card held in hand to Mult | +Mult | On Held | Y | cond | - |
| 30 | Chaos the Clown | `j_chaos` | Common | $4 | 1 free Reroll per shop | Effect | N/A | N |  | - |
| 31 | Fibonacci | `j_fibonacci` | Uncommon | $8 | Each played Ace, 2, 3, 5, or 8 gives +8 Mult when scored | +Mult | On Scored | Y | cond | - |
| 32 | Steel Joker | `j_steel_joker` | Uncommon | $7 | Gives X0.2 Mult for each Steel Card in your full deck | xMult | Indep. | Y |  | - |
| 33 | Scary Face | `j_scary_face` | Common | $4 | Played face cards give +30 Chips when scored | Chips | On Scored | Y | cond | - |
| 34 | Abstract Joker | `j_abstract` | Common | $4 | +3 Mult for each Joker card | +Mult | Indep. | Y |  | - |
| 35 | Delayed Gratification | `j_delayed_grat` | Common | $4 | Earn $2 per discard if no discards are used by end of the round | Economy | N/A | N |  | - |
| 36 | Hack | `j_hack` | Uncommon | $6 | Retrigger each played 2, 3, 4, or 5 | Retrigger | On Scored | Y | cond | - |
| 37 | Pareidolia | `j_pareidolia` | Uncommon | $5 | All cards are considered face cards | Effect | N/A | N | rule | - |
| 38 | Gros Michel | `j_gros_michel` | Common | $5 | +15 Mult; 1 in 6 chance this card is destroyed at end of round | +Mult | Indep. | Y |  | - |
| 39 | Even Steven | `j_even_steven` | Common | $4 | Played cards with even rank (10, 8, 6, 4, 2) give +4 Mult when scored | +Mult | On Scored | Y | cond | - |
| 40 | Odd Todd | `j_odd_todd` | Common | $4 | Played cards with odd rank (A, 9, 7, 5, 3) give +31 Chips when scored | Chips | On Scored | Y | cond | - |
| 41 | Scholar | `j_scholar` | Common | $4 | Played Aces give +20 Chips and +4 Mult when scored | Chips+Mult | On Scored | Y | cond | - |
| 42 | Business Card | `j_business` | Common | $4 | Played face cards have a 1 in 2 chance to give $2 when scored | Economy | On Scored | Y | cond | - |
| 43 | Supernova | `j_supernova` | Common | $5 | Adds the number of times poker hand has been played this run to Mult | +Mult | Indep. | Y |  | - |
| 44 | Ride the Bus | `j_ride_the_bus` | Common | $6 | Gains +1 Mult per consecutive hand played without a scoring face card; resets otherwise | +Mult | Mixed | Y | cond | - |
| 45 | Space Joker | `j_space` | Uncommon | $5 | 1 in 4 chance to upgrade level of played poker hand | Effect | On Played | Y |  | - |
| 46 | Egg | `j_egg` | Common | $4 | Gains $3 of sell value at end of round | Economy | N/A | N |  | - |
| 47 | Burglar | `j_burglar` | Uncommon | $6 | When Blind is selected, gain +3 Hands and lose all discards | Effect | On Blind Select | Y | size | - |
| 48 | Blackboard | `j_blackboard` | Uncommon | $6 | X3 Mult if all cards held in hand are Spades or Clubs | xMult | Indep. | Y | cond | - |
| 49 | Runner | `j_runner` | Common | $5 | Gains +15 Chips if played hand contains a Straight | Chips | Mixed | Y | cond | - |
| 50 | Ice Cream | `j_ice_cream` | Common | $5 | +100 Chips; -5 Chips for every hand played | Chips | Indep. | Y |  | - |
| 51 | DNA | `j_dna` | Rare | $8 | If first hand of round has only 1 card, add a permanent copy to deck and draw it to hand | Effect | On Played | Y | deck | - |
| 52 | Splash | `j_splash` | Common | $3 | Every played card counts in scoring | Effect | N/A | N | rule | - |
| 53 | Blue Joker | `j_blue_joker` | Common | $5 | +2 Chips for each remaining card in deck | Chips | Indep. | Y |  | - |
| 54 | Sixth Sense | `j_sixth_sense` | Uncommon | $6 | If first hand of round is a single 6, destroy it and create a Spectral card (must have room) | Effect | N/A | N | deck | - |
| 55 | Constellation | `j_constellation` | Uncommon | $6 | Gains X0.1 Mult every time a Planet card is used | xMult | Indep. | Y |  | - |
| 56 | Hiker | `j_hiker` | Uncommon | $5 | Every played card permanently gains +5 Chips when scored | Chips | On Scored | Y |  | - |
| 57 | Faceless Joker | `j_faceless` | Common | $4 | Earn $5 if 3 or more face cards are discarded at the same time | Economy | On Discard | Y | cond | - |
| 58 | Green Joker | `j_green_joker` | Common | $4 | +1 Mult per hand played; -1 Mult per discard | +Mult | Mixed | Y |  | - |
| 59 | Superposition | `j_superposition` | Common | $4 | Create a Tarot card if poker hand contains an Ace and a Straight (must have room) | Effect | Indep. | Y | cond | - |
| 60 | To Do List | `j_todo_list` | Common | $4 | Earn $4 if poker hand is a [Poker Hand]; poker hand changes at end of round | Economy | On Played | Y | cond | - |
| 61 | Cavendish | `j_cavendish` | Common | $4 | X3 Mult; 1 in 1000 chance this card is destroyed at end of round | xMult | Indep. | Y |  | - |
| 62 | Card Sharp | `j_card_sharp` | Uncommon | $6 | X3 Mult if played poker hand has already been played this round | xMult | Indep. | Y |  | - |
| 63 | Red Card | `j_red_card` | Common | $5 | Gains +3 Mult when any Booster Pack is skipped | +Mult | Indep. | Y |  | - |
| 64 | Madness | `j_madness` | Uncommon | $7 | When Small Blind or Big Blind is selected, gain X0.5 Mult and destroy a random Joker | xMult | Indep. | Y |  | - |
| 65 | Square Joker | `j_square` | Common | $4 | Gains +4 Chips if played hand has exactly 4 cards | Chips | Mixed | Y | cond | - |
| 66 | Séance | `j_seance` | Uncommon | $6 | If poker hand is a Straight Flush, create a random Spectral card (must have room) | Effect | Indep. | Y | cond | - |
| 67 | Riff-Raff | `j_riff_raff` | Common | $6 | When Blind is selected, create 2 Common Jokers (must have room) | Effect | On Blind Select | Y |  | - |
| 68 | Vampire | `j_vampire` | Uncommon | $7 | Gains X0.1 Mult per scoring Enhanced card played; removes the card's Enhancement | xMult | Mixed | Y | deck | - |
| 69 | Shortcut | `j_shortcut` | Uncommon | $7 | Allows Straights to be made with gaps of 1 rank (ex: 10 8 6 5 3) | Effect | N/A | N | rule | - |
| 70 | Hologram | `j_hologram` | Uncommon | $7 | Gains X0.25 Mult every time a playing card is added to your deck | xMult | Indep. | Y |  | - |
| 71 | Vagabond | `j_vagabond` | Rare | $8 | Create a Tarot card if hand is played with $4 or less | Effect | Indep. | Y |  | - |
| 72 | Baron | `j_baron` | Rare | $8 | Each King held in hand gives X1.5 Mult | xMult | On Held | Y | cond | - |
| 73 | Cloud 9 | `j_cloud_9` | Uncommon | $7 | Earn $1 for each 9 in your full deck at end of round | Economy | N/A | N |  | - |
| 74 | Rocket | `j_rocket` | Uncommon | $6 | Earn $1 at end of round; payout increases by $2 when Boss Blind is defeated | Economy | N/A | N |  | - |
| 75 | Obelisk | `j_obelisk` | Rare | $8 | Gains X0.2 Mult per consecutive hand played without playing your most played poker hand | xMult | Mixed | Y |  | - |
| 76 | Midas Mask | `j_midas_mask` | Uncommon | $7 | All played face cards become Gold cards when scored | Effect | On Played | N | deck | - |
| 77 | Luchador | `j_luchador` | Uncommon | $5 | Sell this card to disable the current Boss Blind | Effect | N/A | Y |  | - |
| 78 | Photograph | `j_photograph` | Common | $5 | First played face card gives X2 Mult when scored | xMult | On Scored | Y | cond | - |
| 79 | Gift Card | `j_gift` | Uncommon | $6 | Add $1 of sell value to every Joker and Consumable card at end of round | Economy | N/A | N |  | - |
| 80 | Turtle Bean | `j_turtle_bean` | Uncommon | $6 | +5 hand size, reduces by 1 each round (destroyed at 0) | Effect | N/A | N | size | - |
| 81 | Erosion | `j_erosion` | Uncommon | $6 | +4 Mult for each card below [the deck's starting size] in your full deck | +Mult | Indep. | Y |  | - |
| 82 | Reserved Parking | `j_reserved_parking` | Common | $6 | Each face card held in hand has a 1 in 2 chance to give $1 | Economy | On Held | Y | cond | - |
| 83 | Mail-In Rebate | `j_mail` | Common | $4 | Earn $5 for each discarded [rank]; rank changes every round | Economy | On Discard | Y | cond | - |
| 84 | To the Moon | `j_to_the_moon` | Uncommon | $5 | Earn an extra $1 of interest for every $5 you have at end of round | Economy | N/A | N |  | - |
| 85 | Hallucination | `j_hallucination` | Common | $4 | 1 in 2 chance to create a Tarot card when any Booster Pack is opened (must have room) | Effect | N/A | Y |  | - |
| 86 | Fortune Teller | `j_fortune_teller` | Common | $6 | +1 Mult per Tarot card used this run | +Mult | Indep. | Y |  | - |
| 87 | Juggler | `j_juggler` | Common | $4 | +1 hand size | Effect | N/A | N | size | - |
| 88 | Drunkard | `j_drunkard` | Common | $4 | +1 discard each round | Effect | N/A | N | size | - |
| 89 | Stone Joker | `j_stone` | Uncommon | $6 | Gives +25 Chips for each Stone Card in your full deck | Chips | Indep. | Y |  | - |
| 90 | Golden Joker | `j_golden` | Common | $6 | Earn $4 at end of round | Economy | N/A | N |  | - |
| 91 | Lucky Cat | `j_lucky_cat` | Uncommon | $6 | Gains X0.25 Mult every time a Lucky card successfully triggers | xMult | Mixed | Y |  | - |
| 92 | Baseball Card | `j_baseball` | Rare | $8 | Uncommon Jokers each give X1.5 Mult | xMult | On Other Jokers | Y |  | - |
| 93 | Bull | `j_bull` | Uncommon | $6 | +2 Chips for each $1 you have | Chips | Indep. | Y |  | - |
| 94 | Diet Cola | `j_diet_cola` | Uncommon | $6 | Sell this card to create a free Double Tag | Effect | N/A | Y |  | - |
| 95 | Trading Card | `j_trading` | Uncommon | $6 | If first discard of round has only 1 card, destroy it and earn $3 | Economy | On Discard | N | deck | - |
| 96 | Flash Card | `j_flash` | Uncommon | $5 | Gains +2 Mult per reroll in the shop | +Mult | Indep. | Y |  | - |
| 97 | Popcorn | `j_popcorn` | Common | $5 | +20 Mult; -4 Mult per round played | +Mult | Indep. | Y |  | - |
| 98 | Spare Trousers | `j_trousers` | Uncommon | $6 | Gains +2 Mult if played hand contains a Two Pair | +Mult | Mixed | Y | cond | - |
| 99 | Ancient Joker | `j_ancient` | Rare | $8 | Each played card with [suit] gives X1.5 Mult when scored; suit changes at end of round | xMult | On Scored | Y | cond | - |
| 100 | Ramen | `j_ramen` | Uncommon | $6 | X2 Mult; loses X0.01 Mult per card discarded | xMult | Mixed | Y |  | - |
| 101 | Walkie Talkie | `j_walkie_talkie` | Common | $4 | Each played 10 or 4 gives +10 Chips and +4 Mult when scored | Chips+Mult | On Scored | Y | cond | - |
| 102 | Seltzer | `j_selzer` | Uncommon | $6 | Retrigger all cards played for the next 10 hands | Retrigger | On Scored | Y |  | - |
| 103 | Castle | `j_castle` | Uncommon | $6 | Gains +3 Chips per discarded [suit] card; suit changes every round | Chips | Mixed | Y | cond | - |
| 104 | Smiley Face | `j_smiley` | Common | $4 | Played face cards give +5 Mult when scored | +Mult | On Scored | Y | cond | - |
| 105 | Campfire | `j_campfire` | Rare | $9 | Gains X0.25 Mult for each card sold; resets when Boss Blind is defeated | xMult | Indep. | Y |  | - |
| 106 | Golden Ticket | `j_ticket` | Common | $5 | Played Gold cards earn $4 when scored | Economy | On Scored | Y | cond | Play a 5 card hand that contains only Gold cards |
| 107 | Mr. Bones | `j_mr_bones` | Uncommon | $5 | Prevents Death if chips scored are at least 25% of required chips; self destructs | Effect | N/A | N |  | Lose 5 runs |
| 108 | Acrobat | `j_acrobat` | Uncommon | $6 | X3 Mult on final hand of round | xMult | Indep. | Y |  | Play 200 hands |
| 109 | Sock and Buskin | `j_sock_and_buskin` | Uncommon | $6 | Retrigger all played face cards | Retrigger | On Scored | Y | cond | Play a total of 300 face cards |
| 110 | Swashbuckler | `j_swashbuckler` | Common | $4 | Adds the sell value of all other owned Jokers to Mult | +Mult | Indep. | Y |  | Sell a total of 20 Joker cards |
| 111 | Troubadour | `j_troubadour` | Uncommon | $6 | +2 hand size, -1 hand each round | Effect | N/A | N | size | Win 5 consecutive rounds by playing only 1 hand |
| 112 | Certificate | `j_certificate` | Uncommon | $6 | When round begins, add a random playing card with a random seal to your hand | Effect | N/A | Y | deck | Have a Gold playing card with a Gold Seal |
| 113 | Smeared Joker | `j_smeared` | Uncommon | $7 | Hearts and Diamonds count as the same suit; Spades and Clubs count as the same suit | Effect | N/A | N | rule | Have at least 3 Wild Cards in your deck |
| 114 | Throwback | `j_throwback` | Uncommon | $6 | X0.25 Mult for each Blind skipped this run | xMult | Indep. | Y |  | Continue a saved run from the main menu |
| 115 | Hanging Chad | `j_hanging_chad` | Common | $4 | Retrigger first played card used in scoring 2 additional times | Retrigger | On Scored | Y |  | Beat a Boss Blind with a High Card |
| 116 | Rough Gem | `j_rough_gem` | Uncommon | $7 | Played cards with Diamond suit earn $1 when scored | Economy | On Scored | Y | cond | Have at least 30 cards with Diamond suit in your deck |
| 117 | Bloodstone | `j_bloodstone` | Uncommon | $7 | 1 in 2 chance for played cards with Heart suit to give X1.5 Mult when scored | xMult | On Scored | Y | cond | Have at least 30 cards with Heart suit in your deck |
| 118 | Arrowhead | `j_arrowhead` | Uncommon | $7 | Played cards with Spade suit give +50 Chips when scored | Chips | On Scored | Y | cond | Have at least 30 cards with Spade suit in your deck |
| 119 | Onyx Agate | `j_onyx_agate` | Uncommon | $7 | Played cards with Club suit give +7 Mult when scored | +Mult | On Scored | Y | cond | Have at least 30 cards with Club suit in your deck |
| 120 | Glass Joker | `j_glass` | Uncommon | $6 | Gains X0.75 Mult for every Glass Card that is destroyed | xMult | Indep. | Y |  | Have at least 5 Glass Cards in your deck |
| 121 | Showman | `j_ring_master` | Uncommon | $5 | Joker, Tarot, Planet, and Spectral cards may appear multiple times | Effect | N/A | N |  | Reach Ante level 4 |
| 122 | Flower Pot | `j_flower_pot` | Uncommon | $6 | X3 Mult if poker hand contains a Diamond card, Club card, Heart card, and Spade card | xMult | Indep. | Y | cond | Reach Ante level 8 |
| 123 | Blueprint | `j_blueprint` | Rare | $10 | Copies ability of Joker to the right | Effect | (copied) | Y |  | Win a run |
| 124 | Wee Joker | `j_wee` | Rare | $8 | Gains +8 Chips when each played 2 is scored | Chips | Mixed | Y | cond | Win a run in 18 or fewer rounds |
| 125 | Merry Andy | `j_merry_andy` | Uncommon | $7 | +3 discards each round, -1 hand size | Effect | N/A | N | size | Win a run in 12 or fewer rounds |
| 126 | Oops! All 6s | `j_oops` | Uncommon | $4 | Doubles all listed probabilities (ex: 1 in 3 -> 2 in 3) | Effect | N/A | N |  | In one hand, earn at least 10,000 chips |
| 127 | The Idol | `j_idol` | Uncommon | $6 | Each played [rank] of [suit] gives X2 Mult when scored; card changes every round | xMult | On Scored | Y | cond | In one hand, earn at least 1,000,000 chips |
| 128 | Seeing Double | `j_seeing_double` | Uncommon | $6 | X2 Mult if played hand has a scoring Club card and a scoring card of any other suit | xMult | Indep. | Y | cond | Play a hand that contains four 7 of Clubs |
| 129 | Matador | `j_matador` | Uncommon | $7 | Earn $8 if played hand triggers the Boss Blind ability | Economy | Indep. | Y |  | Defeat a Boss Blind in 1 hand without using any discards |
| 130 | Hit the Road | `j_hit_the_road` | Rare | $8 | Gains X0.5 Mult for every Jack discarded this round; resets at end of round | xMult | Mixed | Y | cond | Discard 5 Jacks at the same time |
| 131 | The Duo | `j_duo` | Rare | $8 | X2 Mult if played hand contains a Pair | xMult | Indep. | Y | cond | Win a run without playing a Pair |
| 132 | The Trio | `j_trio` | Rare | $8 | X3 Mult if played hand contains a Three of a Kind | xMult | Indep. | Y | cond | Win a run without playing a Three of a Kind |
| 133 | The Family | `j_family` | Rare | $8 | X4 Mult if played hand contains a Four of a Kind | xMult | Indep. | Y | cond | Win a run without playing a Four of a Kind |
| 134 | The Order | `j_order` | Rare | $8 | X3 Mult if played hand contains a Straight | xMult | Indep. | Y | cond | Win a run without playing a Straight |
| 135 | The Tribe | `j_tribe` | Rare | $8 | X2 Mult if played hand contains a Flush | xMult | Indep. | Y | cond | Win a run without playing a Flush |
| 136 | Stuntman | `j_stuntman` | Rare | $7 | +250 Chips, -2 hand size | Chips | Indep. | Y | size | Earn at least 100,000,000 Chips in a single hand |
| 137 | Invisible Joker | `j_invisible` | Rare | $8 | After 2 rounds, sell this card to Duplicate a random Joker (removes Negative from copy) | Effect | N/A | N |  | Win a run without ever having more than 4 Jokers |
| 138 | Brainstorm | `j_brainstorm` | Rare | $10 | Copies the ability of leftmost Joker | Effect | (copied) | Y |  | Discard a Royal Flush |
| 139 | Satellite | `j_satellite` | Uncommon | $6 | Earn $1 at end of round per unique Planet card used this run | Economy | N/A | N |  | Have at least $400 |
| 140 | Shoot the Moon | `j_shoot_the_moon` | Common | $5 | Each Queen held in hand gives +13 Mult | +Mult | On Held | Y | cond | Play every Heart card in your deck in one round |
| 141 | Driver's License | `j_drivers_license` | Rare | $7 | X3 Mult if you have at least 16 Enhanced cards in your full deck | xMult | Indep. | Y |  | Enhance 16 cards in your deck |
| 142 | Cartomancer | `j_cartomancer` | Uncommon | $6 | Create a Tarot card when Blind is selected (must have room) | Effect | On Blind Select | Y |  | Discover every Tarot card |
| 143 | Astronomer | `j_astronomer` | Uncommon | $8 | All Planet cards and Celestial Packs in the shop are free | Effect | N/A | N |  | Discover every Planet card |
| 144 | Burnt Joker | `j_burnt` | Rare | $8 | Upgrade the level of the first discarded poker hand each round | Effect | On Discard | Y | cond | Sell 50 cards |
| 145 | Bootstraps | `j_bootstraps` | Uncommon | $7 | +2 Mult for every $5 you have | +Mult | Indep. | Y |  | Have at least 2 Polychrome Jokers at the same time |
| 146 | Canio | `j_caino` | Legendary | — | Gains X1 Mult when a face card is destroyed | xMult | Mixed | Y |  | Find from The Soul |
| 147 | Triboulet | `j_triboulet` | Legendary | — | Played Kings and Queens each give X2 Mult when scored | xMult | On Scored | Y | cond | Find from The Soul |
| 148 | Yorick | `j_yorick` | Legendary | — | Gains X1 Mult every 23 cards discarded | xMult | Mixed | Y |  | Find from The Soul |
| 149 | Chicot | `j_chicot` | Legendary | — | Disables effect of every Boss Blind | Effect | On Blind Select | N |  | Find from The Soul |
| 150 | Perkeo | `j_perkeo` | Legendary | — | Creates a Negative copy of 1 random consumable card in your possession at the end of the shop | Effect | N/A | Y |  | Find from The Soul |

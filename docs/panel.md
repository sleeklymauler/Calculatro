# The odds panel

The panel the mod draws in game: how it talks to the app, where it goes, how it's built without
slowing the game down, and its target editor and best-discard button. How the rest of the mod and
the app work is in [architecture.md](architecture.md).

## Talking to the app

The app answers each snapshot in `calculatro_odds.json` (same directory, atomic `os.replace`): first
`{id, pending: true}`, then the odds as they're found (`partial` until the last; see
[the shapes](architecture.md#shapes)), echoing the snapshot's `id`. `overlay.write` returns False
if the file stays locked, and raises any other `OSError` (a missing folder, a full disk); `run_watch`
logs why, then resends the latest payload every `RETRY` seconds for a lock, or `RETRY_ERROR` for
anything else, until it's written or a newer one replaces it. The mod reads the file ~10×/sec (on its file thread) with
a small JSON decoder.

Python sends display-ready values (`overlay.percent`), but the mod names the rows itself, since it
owns what's tracked and shows rows before the app has answered. Each odds row carries a `key`, the
hand's name ("Three of a Kind", "Any target") or the card query's text, and the panel shows a row per
tracked target with the odds for its key ('-' until there are some).

### Stale odds and a missing app

- Odds whose `id` isn't the latest snapshot's are still shown, greyed with "Calculating..." after
  0.3 s.
- So are the values a partial answer doesn't have yet (marked `pending`), each on its own: as it
  arrives, `receive_odds` keeps each of them from the odds before (`merged`: a Discard value keeps
  its trend too), and `panel_model` greys them (`keep_stale`, `discard_stale`) and says
  "Calculating..." after 0.3 s, until the last answer. A Discard value still to come has its arrow
  (`has_discard`), so the panel isn't rebuilt when it arrives.
- With no reply at all for 2 s it says "Waiting for app", or "App didn't start" if Windows refused the
  start, there was nothing to start, or it has been restarted twice to no avail
  (`Calculatro.app_failed`).
- Odds from another game session are ignored, and so are those for snapshots taken before the round
  began (`Calculatro.check_round`, on `G.GAME.round`, which the game increments as a blind is
  selected), so the first hand of a blind shows '-' while it's dealt rather than the last round's
  odds.

## When it's shown

The panel only exists in a blind (`Calculatro.in_blind`: `SELECTING_HAND`/`DRAW_TO_HAND`, or
`PLAY_TAROT` interrupting one, and no `G.OVERLAY_MENU`). It's hidden while a played hand scores
(`HAND_PLAYED`), since the played cards reach it.

## Placement and size

The UI must work on standard 16:9 screens as well as ultrawide. Balatro lays everything out in a fixed
20×11.5-unit room scaled to the window, so place UI relative to game objects in those units, never in
pixels or relative to the window edges.

It's a Balatro `UIBox`, right-aligned above `G.deck` and high enough to clear the raised hand
(`panel_lift`). Its height is capped by the consumables' count label (`max_panel_height`, measured
from the label's own UIBox, `G.consumeables.children.area_uibox`, once it exists).

- **Sections.** Target hands and card queries are separate sections, each filling columns of its own
  under its own header ("Hands  Keep  Discard" / "In hand  Discard"), so "Keep" never sits over cards.
- **Columns.** It's built with each section in one column, then one fewer row per column, and so on
  until it fits, so each column is filled as far as fits before the next starts (6 rows in game),
  growing leftwards over the play area (empty while selecting).
- **The limit.** At most 3 columns (`max_columns`; a 4th reaches the HUD sidebar) of 6 rows fit, so
  the mod won't track targets + card queries needing more (`Calculatro.columns_needed`, mirrored by
  `overlay.columns_needed`, `ROWS_PER_COLUMN`, `MAX_COLUMNS`, which the app also enforces; "Any
  target" adds a hand row when there are several targets). Keep both in step if the panel's sizes
  change.
- **The message.** The message ("Calculating...", "Waiting for app") must never change the panel's
  size, or the columns would flip as it comes and goes. Beside the odds it sits on the title row in a
  space kept for it whether or not there's one (`MESSAGE_W`, so messages there must be short). Only
  with nothing tracked ("Nothing tracked") does it get a row of its own. There's deliberately no hint
  to select cards, and no "No discards left" (the game shows that).

## Building it without slowing the game

- **Rebuilds.** The box is rebuilt only when its layout changes (`layout_key`: the model without the
  odds values, their staleness and the title row's message; popups are on cells, so whether a value
  has one is layout, but not what it says), since a rebuild costs the game tens of milliseconds.
  Otherwise the value text nodes (tagged `config.calculatro_value`) get the new text and colour in
  place and the box is laid out again (`update_values`, the game's `recalculate`), and the cells with
  a popup (tagged `config.calculatro_popup_of`, with the same id) get the new popup, which
  `hover_popups` reads when hovered: an approximation narrowing doesn't rebuild the panel. The new
  values come from a definition built without the suit icons' sprites (`widgets.without_sprites`),
  since the game keeps every `Sprite` made until it's removed.
- **Fitting.** A rebuild reuses the rows per column fitted before for that layout and room
  (`Calculatro.fits`).
- **Removal.** Boxes are removed with `remove_box`, which hands the game empty object lists for the
  removal (it unregisters each element with a linear search) and then drops the removed objects in
  one pass.
- **The leak.** Every box the mod builds (and each hover popup) is followed by `register_once`. The
  game registers an element in `G.MOVEABLES`/`G.STAGE_OBJECTS` each time it lays it out, twice inside
  a `maxw`/`maxh` cell whose contents are shrunk, and unregisters it once on removal. Without it every
  rebuilt panel leaked until the heap passed the game's 300 MB ceiling (`nuGC`), past which it
  collects garbage every frame and runs at a few fps.

## Rows and text

### Card rows

Card rows have no Keep value (with a full hand and nothing face down it's 0% or 100%), so their names
span the hands' name and Keep widths (`CARD_NAME_W`, as in the editor's list) less the pencil's
(`EDIT_W`, see [the target editor](#target-editor)), since text wider than its cell is shrunk. Their
Discard value is still green/red against keeping (`trend`).

Card query labels too long even for that are cut short instead (`Calculatro.fit_label`, measured with
the game's font: the name loses letters, never its suit icons or count). Every card name, in the odds
and the editor's list, shows a popup while hovered (`card_name_cell`, `hover_popups`: the game's
`h_popup`, built anew on each hover) in the panel's colours (light text on `inset_colour`, deeper
shadows) with the full label and what it means (`about`, written by `builder_about` when the query is
added and kept with it; queries added before that have none). Mouse only for now.

### Approximations and values given up on

A value the app is still working out exactly, or gave up on (over its time or memory limit, see
[architecture.md](architecture.md#the-app)), may come with an approximation: the app sends it in whole
percents, certain from bounds ("80%+", or "<6%") and rounded from an estimate ("84%", "<1%"), and the
panel adds an asterisk ("80%+*"), which still fits `DISCARD_VALUE_W`. Hovering it shows a popup
("Approximate", the range, "Between ... and ..." from bounds or "Likely ... to ..." from an estimate,
where it comes from by the mark's `how` (each hand's own odds, a sample of the deals the hand's sorts
allow, or a sample of the hands that could be drawn), then whether the exact odds are still being worked out or why they were given up on;
`approximate_popup`, `HOW` and `EXACT` in `odds.lua`). An approximation still being refined isn't
greyed out: it's this snapshot's. One given up on with no approximation shows "?", and its popup says
"Too slow", then that the exact odds would take too long or use too much memory, by its mark's
`limit` (`TOO_SLOW`). The popup is on the whole value cell (`calculatro_keep:<key>`,
`calculatro_discard:<key>`, `calculatro_card:<key>`), since a "*" or "?" alone is hard to hover. A
Discard value is green or red only when the comparison with Keep is certain: both exact, or their
ranges apart (the app sends no `trend` otherwise).

### The arrow

The Keep→Discard arrow (`arrow`) is drawn as boxes the size of the font's pixels (`FONT_PX`, 0.02
units at the panel's text scale), since a text `->` is too wide for its gap and would be shrunk.
Target rows split the gap and the Discard cell differently from the header: `DISCARD_VALUE_W` just
fits the widest value (">99.9%"), and the arrow sits in the rest (`ARROW_CELL_W`), midway between the
Keep value and a typical one ("88.3%").

### Shadows

The panel draws its own shadows (`deepen_shadows`, on the built UIBox's elements): the game drops text
shadows 0.5 parallax units away, nearly invisible on the dark inset, and box shadows 1.5. Text and the
arrow both use `SHADOW_DEPTH` instead. The mock `UIBox` has no `UIRoot`, so this isn't covered by the
tests.

### Colours

The frame takes the boss's colours like the HUD, but the inset behind the odds is always dark
(`inset_colour`, what `BOSS_MAIN` is outside boss blinds): `BOSS_MAIN` is the raw boss colour, too
light for the text on bosses like The Club and the same hue as the green/red values on others.
Header/footer text uses solid `G.C.JOKER_GREY` and stale values `G.C.UI.BACKGROUND_INACTIVE`, since
the translucent `TEXT_INACTIVE` disappears on some boss colours.

### Text

Balatro's font (m6x11) has no suit symbols, arrows or ≤/≥, so panel text must be ASCII: draw anything
else, like the arrow and the `x` (`glyph`). The exception is ♠♥♣♦ in card query labels (e.g. "Q♠: 1+"):
the mod's `name_label` draws those as the game's own suit icons, sprites from the `ui_1` atlas (`ui_2`
with the colourblind option) at `{x, y = 1}`, as in the View Deck screen, each on a small white tile
so the black suits show on the dark inset. Long hand names are shortened in `SHORT_NAMES` (Lua).

## Face-down hint

Hovering a face-down card in hand while selecting shows a small box above it with what that card can
be, like "6-8 ♥♦" (`hint.lua`), in the popups' colours. It's off unless the player turns on
"Face-down hints" in [the settings](#settings) (`Calculatro.settings.face_down`). It goes with the
panel (the `C` hotkey), hides while the card is dragged, and needs no answer from the app.
`observe.lua` watches the hand's order either way, so turning it on mid-hand shows the box at once.

- **No hook.** `refresh_hint` reads `states.hover.is` every frame and builds its own box, set up like
  the game's card popups (`Card:align_h_popup`). The game shows no popup of its own for a face-down
  card (`Card:hover`), so the two never meet.
- **What's known.** `observe.lua` keeps every sort of the hand seen this round, less those that say
  nothing more than the rest (`prune`), so a card kept while its neighbours are discarded keeps its
  window after the draw re-sorts the hand. With no observation of the card there's no box, as after
  loading a save or using a consumable (until the next sort), or when the card was drawn during a drag.
- **When the hand's order is trusted** (`Calculatro.track_hand`, every frame). Only public events
  decide it, never the face-down cards' values. Two things are kept apart: the *evidence* (the sorts
  recorded this round) and whether the hand is *pending* (the next stable order may be recorded).
  - A draw from the deck or a Sort press (its button's `last_clicked` changed) makes it pending. A
    drag, a card arriving from anywhere but the deck, or a reorder without a sort ends that, and so
    does a frame with a sort and any of those (each frame sees all the last one did, so that proves
    nothing). A drag keeps the evidence, which stays true of those cards wherever they're moved.
  - A pending hand is recorded once it's the same cards in the same order as last frame, in
    `SELECTING_HAND`, under a sort mode the mod knows, with nothing dragged, and its face-up cards in
    order by printed values (a safety net that leaks nothing). A mode change on its own isn't a sort.
  - Using a consumable (`PLAY_TAROT`, `G.TAROT_INTERRUPT`) clears the evidence too, since it may have
    changed a face-down card; it takes a fresh sort after. So does a new run, a load or a new round.
  - A face-down card that leaves the hand face up becomes its printed values. One discarded face down
    (in the discard pile with `wheel_flipped`) stays where it was, as `discarded`. One gone anywhere
    else is dropped from the sorts: what's left of a sorted order is still sorted.
- **What's possible.** `possible_cards` lists the printed rank and suit pairs in the unseen pool for
  which the other face-down cards can still all be dealt consistently with every sort (a card The Mark
  drew as a face card only a face card, as the engine models them: `Calculatro.marked`). Face-down
  cards in no sort are taken to fit, which may list too much when marked ones need face cards that
  others took. Those discarded face down since a sort are still in it, and
  dealt like the rest: of two face-down cards side by side, the left one is still the higher once the
  right one has been discarded. It's a bounded search on the game's thread (`SEARCH_STEPS`),
  run only when the hovered card or what's known changes. What it can't rule out in time stays listed.
  It deals the card with the fewest values left next, and drops a value at once if it leaves another
  card nothing, or the cards left too few different pool cards to share. With all eight cards face down
  and sorted both ways (The House), the limit cut short about 3% of hovers, and the slowest took about
  30 ms; with twelve, up to about 60 ms.
  It can also list too much when two copies of a card would have to tie in opposite orders in two
  sorts. It never leaves out a card that's possible. The odds read the same sorts and agree with it
  otherwise; under The Mark with the challenges' `flipped_cards` modifier the hint allows every card,
  while the odds don't use the sorts at all.
- **The text.** Suits that can be the same ranks share a line, and others get their own, so the box
  never implies a card that can't be there: "2, K-A ♠" and "4 ♥" rather than "2, 4, K-A ♠♥". "Stone"
  gets its own line.

## Animation

Rebuilds are animated (`Calculatro.animate`, run on the final box in `refresh_panel`). It's off with
the game's Reduced Motion setting. `tests/test_animation.py` runs it on hand-built element trees.

- Elements tagged `config.calculatro_anim` (the frame, body, inset, title row, each odds row by key,
  headers by section and column, the editor's rows) are matched by key between the old and new box.
- Matched ones move and resize from where the old box drew them (`drawn_layout`, so a rebuild
  mid-animation carries on, and a move whose destination is unchanged, as when only the odds change,
  isn't restarted). New ones pop in afterwards, slightly staggered, and a panel that appears pops in
  whole. Untagged elements follow the nearest tagged one around them; removed ones vanish.
- It's done only while drawing (the box's `draw` is replaced), by adjusting each element's `VT` and
  putting it back after, so layout, collision and buttons see only the final places.

Give new panel rows a unique `calculatro_anim` key, or they'll move with their column/inset instead
of gliding.

### Bouncing text

"Calculating..." and "Searching..." on the title row float like the game's text (`DynaText`'s `float`,
as on the blind's name and the hand being scored): the letters bob in a wave running left to right,
each 0.4 s behind the one before, every ~2.4 s (`uibox.float`, the game's formula). The message node's `config.calculatro_bounce` (from `BOUNCING` in
`odds.lua`) says whether it bounces; `update_values` copies it in place with the text, and
`bounce_text` reads it as it draws, letter by letter with its own shadow. It isn't a `DynaText`: the
game keeps those in its lists of objects until removed, and the definitions built only for their
values (`without_sprites`) would leak them. Still with the game's Reduced Motion setting, like the game's.

## Settings

The gear after the title's `+` (`settings_cell`) opens the settings page (`settings.lua`:
`settings_model`/`settings_definition`), which replaces the odds like the editor until Done, and is
rebuilt only by its own buttons. Each setting (`SETTINGS`) is a row of its name and its control: an
On/Off toggle, blue while on, or (`kind = 'cycle'`) the editor's `<` value `>` cycle (`widgets.cycle`)
through its `choices`, wrapping around, inert while there's only one. Hovering the name shows a popup
saying what it does (its `about`: lines of ASCII broken by hand, since the game's text doesn't wrap,
passed to `popup_definition` as `text`; a function where they depend on the computer).

Settings are the player's, not the run's, so they're kept in `calculatro_settings.json` in the save
directory rather than on `G.GAME` (see [architecture.md](architecture.md#snapshots) for when it's read
and written). Their defaults are in `core.lua`; a file that can't be read, or a setting of the wrong
type, keeps the default. There are two:

- **Face-down hints** (`face_down`, off by default): the [face-down hint](#face-down-hint), and
  only that. The snapshot's `observations` ([architecture.md](architecture.md#shapes)), which the
  odds and the best-discard search use
  ([probability-model.md](probability-model.md#face-down-cards-in-the-hands-sorts)), are sent either
  way (before face-down stage 5b, this setting turned them on too, as "Face-down windows"). Where the
  exact odds are slow (many face-down cards in wide windows, as The House after its first discard),
  the panel shows an estimate, marked, until they arrive.
- **Memory limit** (`memory_gb`, 2 by default): the most the app's exact odds and searches may use, 1, 2,
  4 or 8 GB ([architecture.md](architecture.md#the-app), Limits). Only the sizes up to half the
  computer's memory are offered (`memory.lua`: `memory_choices`), read through the FFI as installed
  (`GetPhysicallyInstalledSystemMemory`, so 16 GB offers 8), or as usable if Windows won't say; 1 and
  2 GB if it can't be read at all. The app holds to the same half (`process.memory_limit`). A saved
  size that isn't offered shows, and is sent, as the largest offered below it (`memory_gb`), and the
  file keeps it, so it comes back on a computer with more. The popup says the half in GB. Slow cases
  measured by the benchmark need up to about 2 GB (`docs/performance/README.md`, peak memory).

## Target editor

Targets are set in game and owned by the mod. `Calculatro.tracked()` keeps `{hands = {names in HANDS
order}, cards = {{query, label}}}` on `G.GAME.calculatro`, so the game saves them with the run and a
new run starts empty (hand names not in `HANDS`, like Royal Flush from older saves, are dropped). The
snapshot sends them as `tracked` (`GameState.tracked_hands`/`tracked_cards`), and `__main__.tracked`
validates them (unknown or repeated ones and any past the column limit are skipped).

### Removing and editing rows

Each odds row has an `x` to stop tracking it (not "Any target"). Card query rows, in the odds and the
editor's list, also have a gold pencil (`edit_cell`, `PENCIL`) between the search button and the `x`.
It opens the Cards tab on that query's page with the builder set to make it (`Calculatro.edit`;
`Calculatro.builder_for` reads a query back into the builder's choices, and only accepts one
`builder_query` gives back, so the pencil is inert for queries the builder didn't make).

While editing (`editor.editing`, outlined pencil), Add is Save (`save_card`,
`Calculatro.replace_card`: in the edited one's place, inert until the query changes or if it's tracked
already) and editing stops after. The edited one's pencil stops editing, keeping the builder's
choices.

### The editor

The `+` after the title opens the editor (`editor_model`/`editor_definition`) on the tab last shown
(`Calculatro.editor_tab`, Hands at first), which replaces the odds in the same frame until Done:

- A **Hands** tab of 11 toggles (no Royal Flush: the game scores it as a Straight Flush).
- A **Cards** tab whose builder (`OPTIONS`, `Calculatro.builder`) builds a query string
  `cards.parse_query` accepts plus its label (`builder_query`, matching how the app names queries;
  Python stays the only parser), and the tracked queries 5 to a page.

`+`, toggles and Add are greyed and inert when another wouldn't fit.

### The builder

The builder has a count (comparison cycle and number, with a second number for "between", up to the
hand size or the cards held, and at least 8: `largest_count`), 13 rank
chips under a preset cycle (`OPTIONS.preset`; the chips show "Custom" when they match none; presets
with a `query`, like Face and Even, are sent by name so the app matches them as the game does), 4 suit
chips (all on by default; turning off the last turns them all back on) and a special with a `not`
toggle. Under Stone (not negated) the rank cycle and chips show any and are inert, since Stone Cards
have no rank or suit (`stone_only`; the choices are kept for other specials). Chips are ORed; none or
all of them means any.

### Buttons

Buttons are `C` nodes with `button = 'calculatro_button'` (`G.FUNCS`, set each frame) and the action
in `config.calculatro_action`, dispatched by `Calculatro.act`; the panel UIBox has `can_collide =
true` for them. They don't shake the screen: `steady_buttons` gives each a `click` that undoes the
`G.ROOM.jiggle` the game's `UIElement:click` adds (the click sound stays).

Buttons need a box that isn't replaced while they're used, so the editor is rebuilt only when its own
model changes (its buttons), never by odds. It closes when the panel goes away.

### The editor's size

The editor's tabs take the header's place, but its toggles and option cycles have `TEXT_PAD` above and
below their text (`padded`), since m6x11's descenders hang below a text node's box. That makes it
taller than the odds panel with 6-row columns, so `refresh_panel` lowers it by whatever doesn't fit
under the consumables, into the room kept for raised cards.

Both tabs are the same size: `EDITOR_W` is the Cards tab's width worked out from its cells and
paddings, and both are given the Cards tab's height (`EDITOR_H`: five builder rows and Add, from
`TEXT_H`, a text node's exact height in the game's layout). Add and the page cycle share a row under
the builder and the list, so Add takes the width the page cycle leaves (`ADD_W`; its label is cut to
fit with `fit_label`, like the list's names, but has no popup: the builder above it shows every
choice) and the page cycle doesn't move between pages.

## Best-discard button

Each odds row (including "Any target" and card queries) has a green search button (`search_cell`, a
`MAGNIFIER` `glyph`, in a `SEARCH_W` column before the `x`). What the search itself compares is in
[probability-model.md](probability-model.md#best-discard), and how it stays fast in
[best-discard.md](performance/best-discard.md).

### The request

Clicking the button sends `search = {key, nonce}` with the next snapshot (`Calculatro.request`, sent
with every snapshot; the app acts on each nonce once) and remembers the hand's card objects
(`Calculatro.searching`). The button is dimmed and inert and the title row says "Searching..." until
the reply.

### The app's side

`run_watch` runs `search.best_discards` for the row's target, or `search.best_card_discards` for a
card query (`__main__.search_target`, which looks hands up first: no query's text is a hand's name, so
the request has no kind; `find_best`), in a job of its own. The job is cancelled only when the hand,
deck, jokers, blind or discards change (`search_basis` ignores the selection, so the search's own
selection doesn't cancel it) or the row's target does (`search_target`, compared by `report.row_key`: "Any target"'s hands). Its
result (`overlay.search`: every tied option as 0-based hand indices, best first; no options for an
untracked key) goes on every payload until then.

A cancelled search runs again if the snapshot still asks for it: the mod sends the request only until
it's answered or its hand is gone (`check_search`, run before the snapshot), so it's never left
"Searching..." by a change it can't see in the hand's card objects (a Tarot changing a card in place,
a joker sold).

Python's `search_basis` is the one definition of a result still standing: the mod drops its options
(`Calculatro.found`, kept with their nonce) once a payload comes without them or with others
(`receive_search`), and the button then asks again.

### The reply

The mod applies a reply only if its nonce is the one asked for and the hand is still the same card
objects in the same order (`receive_search`), selecting the first option (`Calculatro.select`:
`unhighlight_all`, which leaves Cerulean Bell's forced card selected, then `add_to_highlighted` with
one click sound). A reply that arrives while it can't search (a Tarot choosing its cards) is kept with
`index = 0` and selects nothing; the button's next click selects its first option.

Clicking again cycles through the options (`Calculatro.found`) while the hand is the same and it can
search: the next if the selection is still the last one made, else the first. The title row shows
"i/n" with several options and "Keep hand" when keeping is the only best; "Calculating..." and
"Waiting for app" take priority.

A search the app gave up on (30 s, or the memory limit) answers from estimates of the discards that
could still be best ([probability-model.md](probability-model.md#best-discard)), up to 5 s later. Its
reply has options as usual, and a `mark` with a `how` (`found.estimated`): the title row's word gets
an asterisk ("1/3*", "Keep hand*", and "1/1*" for a single discard, which otherwise shows nothing),
with a popup (`search_popup`): "Approximate", the best's odds ("Odds likely 38.2% to 41.0%.", or one
value if they're exact), and that it's the best by estimates since comparing every discard exactly
would take too long or use too much memory (`SEARCH_ESTIMATED`). One that couldn't answer even so
(given up before it had its discards) comes with no options and a `mark` without a `how`. It selects
nothing, and the title row says "Too slow", with a popup saying why (`SEARCH_TOO_SLOW`), until the
selection changes (`found.gave_up`, `found.selection`). Either way the app remembers the answer until
`search_basis` changes, so asking again is answered at once.

Buttons are inert outside `SELECTING_HAND`, or with no way left to throw cards away before the last
hand (`search.can_redraw`/`Calculatro.can_search`: a discard, or a hand played as one with another
left after it). There's no cap on tied options for now (a hand that already has a Pair ties on dozens
of discards); revisit if it's a nuisance in play.

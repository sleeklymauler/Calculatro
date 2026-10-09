# In-game target editor (planned)

Status: designed, not implemented. This replaces setting targets with the
CLI's `-t`/`-c` flags by setting them in the game's odds panel.

Mockups: <https://claude.ai/artifact/DdWZgJLCNgFB9ygGgqkea7> (a design canvas;
private to the owner). Copies of its boards are in [`mockups/`](mockups/).
They're HTML approximations, not the game: a stand-in pixel font for m6x11,
approximate boss-frame colours, sizes only roughly to scale. Build the real
thing from the panel's existing sizes and colours (`mod/Calculatro/calculatro.lua`).

## Decisions

- **Opening it**: a `+` button right after the "CALCULATRO" title
  ([`Main.dc.html`](mockups/Main.dc.html)). It's greyed out and inert once
  the panel is at its 3-column limit (`overlay.columns_needed`, `max_columns`)
  ([`States.dc.html`](mockups/States.dc.html)).
- **In place**: the editor replaces the panel's contents in the same frame and
  position. It isn't drawn on top of the panel. A Done button (orange, like the
  game's Back) returns to the odds. While editing, the panel must not be rebuilt
  by odds updates, or the editor would reset under the cursor.
- **Tabs**: `Hands | Cards` at the top of the editor, opening on Hands; no
  separate "which kind?" step.
- **Hands tab** ([`Hands.dc.html`](mockups/Hands.dc.html)): the 12 hand
  types as toggles in 2 columns of 6, with tracked ones highlighted (blue).
  Clicking one adds or removes it. A hand that would push the panel past
  3 columns is greyed out like the `+`.
- **Cards tab** ([`Cards.dc.html`](mockups/Cards.dc.html)): a builder on
  the left, the tracked queries on the right.
  - The builder uses `< value >` option cycles in the game's style. Rows:
    Count (comparison: at least / exactly / at most, plus a number), Rank
    (Any, 2..A, Face), Suit (Any, 4 suits) and Special (Any, the 8
    enhancements, Enhanced, the 4 editions, Edition, the 4 seals, Sealed). An
    Add button shows the label it will add ("Add  Queen: 2+").
  - It builds query strings that `cards.parse_query` already accepts, so
    Python stays the only parser. Known gaps: Face can't take a suit (grey out
    Suit when Rank is Face), and the parser's combined modifiers ("lucky red
    seal queen") get only one Special. Add stays greyed while Rank, Suit and
    Special are all Any, since that's always true. "No X" is "exactly 0".
  - The tracked list shows 5 queries per page, each with a red `x` to remove it.
    The 6th row is a `< Page 1/2 >` cycle, shown only when there's a second
    page. Pages instead of scrolling, because Balatro's UI can't clip a
    scrolling container (the Collection screens page the same way). Pages
    instead of more columns, because only about 2 columns of 5 fit beside the
    builder, short of the ~16 queries the panel allows, and the editor's width
    would change. Adding a query jumps to its page.
- **Ownership**: the mod owns the target list and sends it to the app in the
  state file (bump `SCHEMA` in `state.py` and `schema` in `calculatro.lua`).
  The app still validates it.
- **Lifetime**: targets last for a run and reset on a new run. Keeping the list
  on `G.GAME` gets this for free: the game saves it with the run, so it
  survives quit and continue, and a new run starts without it.
- **Where**: only in a blind, like the panel (`Calculatro.in_blind`); not in
  the shop.
- **Style**: match the panel's custom pixel arrow and its shadows
  (`SHADOW_DEPTH`). The cycles' `<`/`>` and the `x` could be drawn the same
  way as the arrow.

## Open questions

- Should the CLI's `-t`/`-c` flags be removed, or only seed the list of a run
  that has none?
- Count range: probably 0..8, with "at least 0" and "at most" over the draw
  count ruled out.

## Implementation notes

- The panel is built with `can_collide = false` and rebuilt as a whole UIBox
  whenever its model changes. Buttons need collision, and need a box that isn't
  replaced up to ~20 times a second, so they don't lose hover and click state.
- Panel resize animation is a separate TODO, left until after the editor.

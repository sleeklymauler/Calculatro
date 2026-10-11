# Best discard: implementation plan

Agreed 2026-10-11. Each target-hand row on the panel (including "Any target")
gets a green search button. Clicking it finds the discard that gives the best
chance of making that hand after this discard's draw, and selects those cards
in game. Clicking again cycles through the other discards with the same odds.
Card queries get no button.

## Behaviour

- **One step.** It compares keeping the hand (discarding nothing) with every
  discard the game allows, for this discard and draw only. It doesn't look
  ahead to later discards or hands.
- **Ties.** Exact odds (`Fraction`), so a tie is a real tie. The fewest cards
  discarded comes first. Among ties of the same size, keeping higher ranks
  comes first (more chips), then the leftmost cards in hand, so the order is
  predictable.
- **Cycling.** The app sends every tied option in one reply, so further clicks
  are instant. A click moves to the next option if the current selection is
  the one the search last made and the hand hasn't changed. Otherwise it
  starts again at the best one. The list wraps around.
- **Distinct options.** Options differ in which cards they throw away,
  counting identical cards (alike in every visible way, or both face down) as
  one. No cap on how many for now (decided 2026-10-11): cap or merge them
  later if play shows silly blow-ups. A hand that already has a Pair ties on
  over 100 discards.
- **Keeping wins.** If discarding nothing is best, the search deselects
  everything and the message slot says "Keep hand".
- **Greyed and inert:** outside `SELECTING_HAND`, with an empty hand, or with
  no discards left and only one hand. Without discards, playing a hand throws
  cards away like a discard (user request 2026-10-11), as long as another
  hand is left after it.
- **Limits.** Discards are at most the game's selection limit
  (`G.hand.config.highlighted_limit`, exported as `select_limit`), not a fixed 5.
- **Bosses.** These fall out of `engine._discard`/`evaluate_discard`, so the
  search just has to respect them:
  - Cerulean Bell: the forced card is in every discard and counts towards the
    limit.
  - The Serpent: always draws 3.
  - Face-down cards (The House, The Wheel, The Fish, The Mark): valid to
    discard. All of them are interchangeable, since the app doesn't know which
    is which, so the leftmost ones are picked. That leaks nothing.

## Messages between the halves

Both schemas get bumped: `state.SCHEMA`/`schema` 6 → 7 and
`overlay.SCHEMA`/`odds_schema` 3 → 4.

**Snapshot** (mod → app), new fields:

```json
"select_limit": 5,
"search": {"key": "Flush", "nonce": 3}
```

- `key` is the row's key: a hand name or "Any target".
- `nonce` goes up by one on each click that needs a new search.
- The field stays in later snapshots, and the app only acts on a nonce it
  hasn't seen. A click that only cycles sends nothing.

**Odds file** (app → mod), on every payload once a search has finished, until
the hand changes:

```json
"search": {"nonce": 3, "key": "Flush", "options": [[0, 4], [0, 6]]}
```

- `options` are hand indices in the order of the snapshot the search ran on.
  `[[]]` means keep.
- No `null` anywhere, as before.
- It's attached to every payload, `pending` ones included. Selecting the cards
  writes a new snapshot, which gets new odds payloads, and those mustn't wipe
  out the options the mod is cycling through.

## Python

### `state.py`

- `select_limit` (default 5) and `search: tuple[str, int] | None` on `GameState`.
- Hidden cards already keep their position in `hand`, so indices line up with
  the mod's hand.

### `search.py` (new)

`best_discards(state, target, cancel) -> Search(options: list[list[int]], probability: Fraction)`

1. **Group hand cards** by what the target can tell apart: which of its
   patterns' requirements the card's key counts towards, plus hidden or not.
   The odds depend only on how many of each group are kept, so this is the
   unit to enumerate. Under Cerulean Bell, group by full `card_key` instead,
   since `_bell_after_draw` and `forced_patterns` look at the key itself. The
   forced card is a group of its own and is always discarded.
2. **Enumerate counts per group**: how many to discard from each group, with
   the total at most `select_limit` (0 included, for keeping). Evaluate one
   representative of each with `evaluate_discard`. This is a few dozen
   searches instead of up to 638.
3. **Expand the winners.** Turn the best groups-level choices into concrete,
   distinct options (identical cards collapsed, as above), ordered as in
   Behaviour. These cost nothing extra, since cards in the same group give the
   same odds.
4. **Cancellable** throughout. `evaluate_discard` already raises `Cancelled`.

If benchmarks show it's needed:

- Order candidates so likely winners come first, and share pattern/part
  caches across evaluations (same pool and rules, only the kept cards differ).
- Discarding a card that counts towards nothing never lowers the odds (except
  when the hand can't refill), which can prune some candidates. Their odds are
  still needed for the fewest-cards tie-break, so this only helps in
  combination with ordering.

### `__main__.run_watch`

- **A second job slot for the search**, beside the odds job.
- **Start** one when a snapshot carries a nonce not seen before, for a key
  that `tracked` accepts (or "Any target" via `combine`).
- **Basis.** The search's basis is the state with highlight flags, `tracked`,
  `search` and `state` cleared (`dataclasses.replace`). Each new snapshot
  cancels the search only if its basis differs, i.e. the hand, deck, jokers,
  blind or discards changed. Selecting cards, including the selection the
  search itself makes, doesn't cancel it.
- **Results.** A finished result is kept while the basis matches and attached
  to every payload (`overlay.odds`/`overlay.pending` take it). It's dropped
  when the basis changes. A cancelled search isn't restarted: the player
  clicks again.
- **Console.** Prints one line, e.g. `Best discard for Flush: 7♣ K♦ (34.5%), 1 of 3`.

### `overlay.py`

- Adds `search` to the payloads.
- `columns_needed` is unchanged (it counts rows). Only widths change, on the
  mod side.

## Mod (`calculatro.lua`)

- **Snapshot:** `select_limit`, and `search` from `Calculatro.search_request`.
- **Search button.** A magnifying glass drawn with `glyph` (m6x11 has no
  icon), on a green button like `remove_cell`'s red one. It sits in its own
  `SEARCH_W` column before the `x` column on every hand row, so the icons line
  up; "Any target" has a button and a blank `x` cell. Hand columns get wider
  by `SEARCH_W + ROW_PAD`.
  - Check that `COLUMN_W`, `CARD_NAME_W`, the header widths and
    `EDITOR_W`/`CARDS_TAB_W` (which depend on `CARD_NAME_W`) still work.
  - Check that 3 columns still fit on 16:9 (reason in room units, then a 16:9
    check in game).
  - Apply the same `deepen_shadows` treatment as the arrow and `x`.
- **Action `search`.** If there's a result for this key and the hand is the
  same card objects in the same order as at the click, select the next option
  (or the first, if the selection isn't the last one made). Otherwise send a
  request:
  - Bump the nonce.
  - Remember the hand's card objects and the key.
  - Show the button as searching (dimmed, pulsing) until the reply.
  - A click on another row replaces the request.
- **Applying an option:** `G.hand:unhighlight_all()`, then
  `G.hand:add_to_highlighted(card)` for each index, mapped through the
  remembered card objects. A reply is applied only if its nonce is the current
  request's and the hand still matches. Otherwise it's dropped silently.
  - Check in the game's code that `unhighlight_all` leaves Cerulean Bell's
    forced card selected, and that these calls play the usual select sound.
- **Message slot.** Shows "2/3" while cycling through several options, and
  "Keep hand" for a keep result. These stay within `MESSAGE_W`. "Calculating..."
  takes priority, since the odds are stale while it shows.
- **Animation:** the button gets a `calculatro_anim` key (`search:<key>`).

## Tests

- **`tests/test_search.py`:**
  - Brute force over every subset matches `best_discards` on small hands:
    plain, Smeared, Four Fingers, Wild cards, Stone, debuffed cards, The
    Serpent, The Mark with face-down cards, Cerulean Bell.
  - Tie order (fewest cards first, then higher ranks).
  - Identical cards collapse into one option.
  - Keep-is-best, the selection limit, cancelling.
- **`tests/test_watch.py`:**
  - The search survives selection-only snapshots and is cancelled by hand
    changes.
  - Its result rides on later payloads and is dropped with the basis.
  - A repeated nonce doesn't restart it.
- **`tests/test_state.py`:** `select_limit` and `search` in the snapshot. Add
  `highlighted_limit`, `unhighlight_all` and `add_to_highlighted` to `MOCK_G`.
- **`tests/test_overlay.py`:**
  - Search buttons on hand rows only, greyed when they should be.
  - A click sends a request, and the reply selects the cards.
  - Cycling, and a stale reply being ignored.
  - The "2/3" and "Keep hand" messages.
- **`tests/perf/scenarios.py`:** best-discard scenarios with 8 and 10 cards
  for Flush, Straight, Full House, "Any target" with 3 hands, and Four
  Fingers + Shortcut. Add them to `baseline.json`.
- Run the Lua tests under `lupa.luajit21` too.

## Docs

- `CLAUDE.md`:
  - the new snapshot and odds fields;
  - the search button and its column;
  - `search.py` under the probability model;
  - the second job in `run_watch`.
- `docs/performance/`: a note with the timings.

## Order of work

1. `search.py` and its brute-force tests (engine only, no UI).
2. Perf scenarios. Optimize until the worst realistic case is comfortably
   interactive (target: under ~1 s).
3. Schema bump, `state.py`, `run_watch`, `overlay.py`, and their tests.
4. The mod: button, request and reply, applying options, cycling, and tests.
5. Install, and check in game on ultrawide and 16:9 (width, Bell, The House,
   Serpent).
6. Docs.

## Status

Implemented 2026-10-11 without the performance work (step 2), at the user's
request: get it working first. "2/3" stays on the title row (user decision).
Not yet checked in game.

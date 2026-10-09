"""The odds panel: the app's odds file, and the mod reading it and drawing the panel."""

from __future__ import annotations

import json
from fractions import Fraction

import pytest

import calculatro.__main__ as cli
from calculatro import overlay
from calculatro.cards import parse_query
from calculatro.hands import get_target
from calculatro.report import build_report
from calculatro.state import GameState
from test_state import lua  # noqa: F401  (fixture: the mod running against a mocked game)
from test_watch import FakeSource

lupa = pytest.importorskip("lupa")

TEXTS = r"""
return function(node)  -- text nodes of a UI definition, in order, as 'text@colour', and icons as '[atlas x,y]'
  local out = {}
  local function walk(n)
    if n.n == G.UIT.T then out[#out + 1] = n.config.text .. '@' .. tostring(n.config.colour) end
    if n.n == G.UIT.O then
      local s = n.config.object
      out[#out + 1] = string.format('[%s %d,%d]', s.atlas, s.pos.x, s.pos.y)
    end
    for _, child in ipairs(n.nodes or {}) do walk(child) end
  end
  walk(node)
  return out
end
"""


def read_state(tmp_path) -> GameState:
    return GameState.from_json(json.loads((tmp_path / "calculatro_state.json").read_text(encoding="utf-8")))


def panel_texts(rt) -> list[str] | None:
    """Text of the live panel, or None if there is none."""
    box = rt.eval("BOXES[#BOXES]")
    if box is None or box.REMOVED:
        return None
    return list(rt.execute(TEXTS)(box.definition).values())


def answer(lua, tmp_path, targets=("trips", "pair"), queries=("no face",)):
    """What the app does: read the mod's state, write its odds."""
    state = read_state(tmp_path)
    _, payload = cli.calculate(state, [get_target(t) for t in targets], [parse_query(q) for q in queries])
    assert overlay.write(tmp_path / "calculatro_odds.json", payload)


# --- Python side -----------------------------------------------------------

def test_percent_never_rounds_to_certainty():
    assert overlay.percent(Fraction(0)) == "0%"
    assert overlay.percent(Fraction(1)) == "100%"
    assert overlay.percent(Fraction(1, 3)) == "33.3%"
    assert overlay.percent(Fraction(1, 10**6)) == "<0.1%"
    assert overlay.percent(1 - Fraction(1, 10**6)) == ">99.9%"


def test_card_labels_are_short_and_ascii_but_for_suits():
    labels = {q: overlay.card_label(parse_query(q)) for q in
              ["2+ queens", "at most 1 spade", "no face cards", "exactly 2 hearts", "QS", "at most 0 clubs",
               "foil diamonds", "10 of hearts"]}
    assert labels == {
        "2+ queens": "Queen: 2+",
        "at most 1 spade": "♠: 0-1",
        "no face cards": "Face card: none",
        "exactly 2 hearts": "♥: 2",
        "QS": "Q♠: 1+",
        "at most 0 clubs": "♣: none",
        "foil diamonds": "Foil ♦: 1+",
        "10 of hearts": "T♥: 1+",
    }
    for label in labels.values():  # the mod draws these four as icons
        assert all(ch.isascii() or ch in "♠♥♣♦" for ch in label)


def test_panel_draws_suits_as_icons(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=(), queries=("QS", "at most 1 heart", "foil diamonds", "clubs", "queen"))
    mod.update(0.1)
    texts = panel_texts(rt)
    rows = [t for t in texts[texts.index("Discard@muted") + 1:] if "%" not in t]  # without the odds
    assert rows == ["Q@light", "[ui_1 3,1]", ": 1+@light",
                    "[ui_1 0,1]", ": 0-1@light",
                    "Foil @light", "[ui_1 1,1]", ": 1+@light",
                    "[ui_1 2,1]", ": 1+@light",
                    "Queen: 1+@light"]
    # Each icon on a white tile, so the black suits show on the dark inset.
    tiles = rt.execute("""return function(node)
      local out = {}
      local function walk(n)
        for _, c in ipairs(n.nodes or {}) do
          if c.n == G.UIT.O then out[#out + 1] = n.config.colour end
          walk(c)
        end
      end
      walk(node)
      return out
    end""")(rt.eval("BOXES[#BOXES]").definition)
    assert list(tiles.values()) == ["white"] * 4
    # The high-contrast icons with the colourblind option.
    rt.execute("G.SETTINGS.colourblind_option = true")
    assert "[ui_2 3,1]" in list(rt.execute(TEXTS)(mod.definition(mod.panel_model())).values())


def test_payloads(lua, tmp_path):
    _, mod = lua
    mod.write_snapshot()
    state = read_state(tmp_path)
    assert state.id == mod.state_id and state.selected == [2, 3]
    report = build_report(state, [get_target("trips"), get_target("pair")], [parse_query("no face")])
    payload = overlay.odds(state, report)
    assert payload == {
        "schema": overlay.SCHEMA, "id": state.id,
        "targets": [
            {"name": "Any target", "keep": "100%", "discard": "100%", "trend": 0},
            {"name": "Trips", "keep": "80.0%", "discard": "100%", "trend": 1},
            {"name": "Pair", "keep": "100%", "discard": "100%", "trend": 0},
        ],
        "cards": [{"name": "Face card: none", "discard": "20.0%"}],
    }
    assert overlay.pending(state) == {"schema": overlay.SCHEMA, "id": state.id, "pending": True}

    out_of_discards = GameState(**{**state.__dict__, "discards_left": 0})
    payload = overlay.odds(out_of_discards, build_report(out_of_discards, [get_target("trips")]))
    assert payload["message"] == overlay.NO_DISCARDS
    unselected = GameState(**{**state.__dict__, "hand": [c.__class__(**{**c.__dict__, "highlighted": False})
                                                          for c in state.hand]})
    payload = overlay.odds(unselected, build_report(unselected, [get_target("trips")]))
    assert "message" not in payload
    assert payload["targets"] == [{"name": "Trips", "keep": "80.0%"}]
    assert overlay.odds(state, None, configured=False)["message"] == overlay.NO_TARGETS
    assert "message" not in overlay.odds(GameState(in_run=False, id="x"), None, configured=False)


def test_watch_sends_pending_then_odds():
    state = GameState(in_run=True, id="s:1", hand_size=1)
    sent: list[dict] = []
    cli.run_watch(FakeSource(state), [get_target("pair")], [], lambda text: None, sent.append,
                  poll_interval=0.005, stop=lambda: len(sent) >= 2)
    assert sent == [overlay.pending(state), overlay.odds(state, None)]

    sent.clear()
    cli.run_watch(FakeSource(state), [], [], lambda text: None, sent.append,
                  poll_interval=0.005, stop=lambda: bool(sent))
    assert sent == [overlay.odds(state, None, configured=False)]


# --- Lua side --------------------------------------------------------------

def test_decode_round_trip(lua):
    _, mod = lua
    text = ' {"s":"a\\"b\\\\c\\n\\t\\/\\u00e9\\u4e2d","n":-1.5e2,"i":42,"t":true,"f":false,\n' \
           '  "list":[1,"x",{"k":"v"}],"empty":[]} '
    decoded = mod.decode(text)
    assert json.loads(mod.encode(decoded)) == json.loads(text)  # "empty" stays an array
    assert decoded.s == 'a"b\\c\n\t/é中'
    for bad in ['{"a":1', '{"a" 1}', '[1,]', '"open', '{"a":nul}', '1 2']:
        with pytest.raises(lupa.LuaError):
            mod.decode(bad)


def test_snapshot_ids_change_with_the_state(lua, tmp_path):
    rt, mod = lua
    mod.write_snapshot()
    first = read_state(tmp_path).id
    mod.write_snapshot()
    assert read_state(tmp_path).id == first  # unchanged: not rewritten
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.write_snapshot()
    second = read_state(tmp_path).id
    assert second != first and second == mod.state_id
    assert first.split(":")[0] == second.split(":")[0] == mod.session


def test_in_blind(lua):
    rt, mod = lua
    for state, shown in [("SELECTING_HAND", True), ("DRAW_TO_HAND", True), ("HAND_PLAYED", False),
                         ("SHOP", False), ("BLIND_SELECT", False), ("ROUND_EVAL", False), ("TAROT_PACK", False)]:
        rt.execute(f"G.STATE = G.STATES.{state}")
        assert mod.in_blind() is shown, state
    # Using a consumable: shown if it was used during the blind.
    rt.execute("G.STATE = G.STATES.PLAY_TAROT; G.TAROT_INTERRUPT = G.STATES.SELECTING_HAND")
    assert mod.in_blind()
    rt.execute("G.TAROT_INTERRUPT = G.STATES.SHOP")
    assert not mod.in_blind()
    rt.execute("G.STATE = G.STATES.SELECTING_HAND; G.OVERLAY_MENU = {}")  # run info, deck view, options
    assert not mod.in_blind()
    rt.execute("G.OVERLAY_MENU = nil; G.STAGE = G.STAGES.MAIN_MENU")
    assert not mod.in_blind()


def test_panel_shows_current_odds(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    assert panel_texts(rt) is None  # no odds yet
    answer(lua, tmp_path)
    mod.update(0.1)
    assert panel_texts(rt) == [
        "CALCULATRO@light",
        "Hands@muted", "Keep@muted", "Discard@muted",
        "Any target@light", "100%@light", ">@muted", "100%@light",
        "Trips@light", "80.0%@light", ">@muted", "100%@green",
        "Pair@light", "100%@light", ">@muted", "100%@light",
        "Drawn cards@muted", "Discard@muted",  # a column of their own
        "Face card: none@light", "20.0%@light",
    ]
    box = rt.eval("BOXES[#BOXES]")
    assert rt.eval("BOXES[#BOXES].config.major == G.deck") and box.config.align == "tm"
    assert box.alignment.offset.x == pytest.approx((2.25 - 2 * 3.25) / 2)  # right edge on the deck's
    # Above the raised hand's cards, highlighted ones included.
    assert box.alignment.offset.y == pytest.approx(-(1.9 + 0.2 + 0.5 + 0.1))

    boxes = rt.eval("#BOXES")
    mod.update(0.1)
    assert rt.eval("#BOXES") == boxes  # unchanged: not rebuilt


COLUMNS = r"""
return function(node)  -- names in each odds column (the column's first label), in order
  local columns = {}
  local function first_texts(n, out)
    if n.n == G.UIT.R then
      local first
      local function find(m) if first then return end
        if m.n == G.UIT.T then first = m.config.text end
        for _, c in ipairs(m.nodes or {}) do find(c) end
      end
      find(n)
      out[#out + 1] = first
      return
    end
    for _, c in ipairs(n.nodes or {}) do first_texts(c, out) end
  end
  local function walk(n)
    if n.config and n.config.id == 'calculatro_column' then
      local out = {}
      first_texts(n, out)
      columns[#columns + 1] = table.concat(out, '|')
    end
    for _, c in ipairs(n.nodes or {}) do walk(c) end
  end
  walk(node)
  return columns
end
"""


def columns(rt, definition) -> list[list[str]]:
    return [c.split("|") for c in rt.execute(COLUMNS)(definition).values()]


def test_panel_spills_into_columns(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    targets = ("pair", "two pair", "trips", "straight", "flush", "full house", "quads",
               "straight flush", "royal", "five of a kind", "flush house")
    answer(lua, tmp_path, targets=targets, queries=("queen",))
    mod.update(0.1)
    box = rt.eval("BOXES[#BOXES]")
    # 12 hand rows (11 targets and "Any target") and a query: columns of 6
    # (2.6 tall) are the tallest to fit in the 2.85 under the consumables, so
    # each is filled first; the query starts a column of its own.
    assert columns(rt, box.definition) == [
        ["Hands", "Any target", "Pair", "Two Pair", "Trips", "Straight", "Flush"],
        ["Hands", "Full House", "Quads", "Str. Flush", "Royal Flush", "5 of a Kind", "Flush House"],
        ["Drawn cards", "Queen: 1+"],
    ]
    assert box.T.h <= mod.max_panel_height()
    assert box.alignment.offset.x == pytest.approx((2.25 - 3 * 3.25) / 2)  # grows leftwards
    assert sum(1 for b in rt.eval("BOXES").values() if b.REMOVED) == 6  # 12 to 7 rows per column


def test_panel_fills_a_column_before_starting_another(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=("pair", "two pair", "trips", "straight", "flush", "full house"), queries=())
    mod.update(0.1)
    # 7 rows: 6 in the first column, not 4 and 3.
    assert [len(c) - 1 for c in columns(rt, rt.eval("BOXES[#BOXES]").definition)] == [6, 1]


@pytest.mark.parametrize("targets", [
    ("pair", "two pair", "trips", "straight", "flush"),  # 6 rows: one full column
    ("pair", "two pair", "trips", "straight", "flush", "full house", "quads",
     "straight flush", "royal", "five of a kind", "flush house", "flush five"),  # 13 rows
])
def test_message_does_not_change_the_panel(lua, tmp_path, targets):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=targets, queries=())
    mod.update(0.1)
    assert panel_texts(rt)[1] == "Hands@muted"  # no message
    box = rt.eval("BOXES[#BOXES]")
    layout = [len(c) for c in columns(rt, box.definition)]
    assert layout == ([7] if len(targets) == 5 else [7, 7, 2])

    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.2)  # new snapshot
    mod.update(0.4)  # no answer yet: "Calculating..." beside the title
    assert panel_texts(rt)[1] == "Calculating...@muted"
    calculating = rt.eval("BOXES[#BOXES]")
    assert [len(c) for c in columns(rt, calculating.definition)] == layout
    assert (calculating.T.w, calculating.T.h) == (box.T.w, box.T.h)


def test_panel_height_measured_from_the_count_label(lua):
    rt, mod = lua
    lift = 1.9 + 0.2 + 0.5 + 0.1
    # Until the consumables' label exists, it's estimated to hang 0.6 below them.
    assert mod.max_panel_height() == pytest.approx(8.9 - lift - (2.75 + 0.6))
    rt.execute("G.consumeables.children = {area_uibox = {T = {y = -0.15, h = 2.9}}}")
    assert mod.max_panel_height() == pytest.approx(8.9 - lift - (2.75 - 0.03))
    rt.execute("G.consumeables.children.area_uibox.REMOVED = true")
    assert mod.max_panel_height() == pytest.approx(8.9 - lift - (2.75 + 0.6))


def test_odds_inset_stays_dark_in_boss_blinds(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    find = rt.execute(r"""
      return function(n)
        local function walk(m)
          if m.config and m.config.id == 'calculatro_inset' then return m end
          for _, c in ipairs(m.nodes or {}) do local f = walk(c); if f then return f end end
        end
        return walk(n)
      end""")
    colour = find(rt.eval("BOXES[#BOXES]").definition).config.colour
    # G.C.BLACK darkened 5%, as BOSS_MAIN is outside boss blinds, never BOSS_MAIN itself.
    assert list(colour.values()) == pytest.approx([0.19, 0.38, 0.57, 1])


def test_columns_needed():
    assert overlay.columns_needed(0, 0) == 0
    assert overlay.columns_needed(1, 0) == 1
    assert overlay.columns_needed(5, 6) == 2  # 6 hand rows with "Any target"
    assert overlay.columns_needed(6, 1) == 3  # 7 hand rows
    assert overlay.columns_needed(11, 6) == 3
    assert overlay.columns_needed(5, 12) == 3
    assert overlay.columns_needed(0, 18) == 3
    assert overlay.columns_needed(6, 12) == 4
    assert overlay.columns_needed(2, 13) == 4


def test_tracking_limit(capsys):
    queries = ["-c", "queen"] * 13
    with pytest.raises(SystemExit):
        cli.main(["watch", "-t", "pair", "-t", "flush", *queries])
    assert "these need 4" in capsys.readouterr().err


def test_panel_never_wider_than_three_columns(lua, tmp_path):
    rt, mod = lua
    rt.execute("G.consumeables.T.h = 5")  # leaves 0.6: nothing fits
    mod.update(1.0)
    answer(lua, tmp_path, targets=("pair", "two pair", "trips", "straight", "flush"), queries=("queen",))
    mod.update(0.1)
    assert len(columns(rt, rt.eval("BOXES[#BOXES]").definition)) == 3


def test_hands_and_cards_in_columns_of_their_own(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=("pair", "two pair", "trips", "straight"), queries=("queen", "no face"))
    mod.update(0.1)
    # 7 rows would fit one column of 7, but the cards start their own.
    assert columns(rt, rt.eval("BOXES[#BOXES]").definition) == [
        ["Hands", "Any target", "Pair", "Two Pair", "Trips", "Straight"],
        ["Drawn cards", "Queen: 1+", "Face card: none"],
    ]
    # Each section fills its own columns: the hands' last isn't topped up with cards.
    assert columns(rt, mod.definition(mod.panel_model(), 3)) == [
        ["Hands", "Any target", "Pair", "Two Pair"],
        ["Hands", "Trips", "Straight"],
        ["Drawn cards", "Queen: 1+", "Face card: none"],
    ]


def test_panel_without_selection(lua, tmp_path):
    rt, mod = lua
    rt.execute("for _, c in ipairs(G.hand.cards) do c.highlighted = nil end")
    mod.update(1.0)
    answer(lua, tmp_path, targets=("trips",))
    mod.update(0.1)
    assert panel_texts(rt) == [
        "CALCULATRO@light", "Hands@muted", "Keep@muted", "Discard@muted",
        "Trips@light", "80.0%@light", "-@light",
        "Drawn cards@muted", "Discard@muted", "Face card: none@light", "-@light",
    ]


def test_panel_with_only_card_queries(lua, tmp_path):
    rt, mod = lua
    rt.execute("for _, c in ipairs(G.hand.cards) do c.highlighted = nil end")
    mod.update(1.0)
    answer(lua, tmp_path, targets=(), queries=("no face",))
    mod.update(0.1)
    # Listed without a selection too, so the panel doesn't vanish between discards.
    assert panel_texts(rt) == [
        "CALCULATRO@light", "Drawn cards@muted", "Discard@muted",
        "Face card: none@light", "-@light",
    ]
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.2)
    answer(lua, tmp_path, targets=(), queries=("no face",))
    mod.update(0.1)
    assert panel_texts(rt)[-2:] == ["Face card: none@light", "20.0%@light"]


CELL_WIDTHS = r"""
return function(node)  -- maxw of the cell around each label, keyed by its text
  local out = {}
  local function walk(n, w)
    if n.n == G.UIT.T then out[n.config.text] = w end
    for _, c in ipairs(n.nodes or {}) do walk(c, n.config and n.config.maxw or w) end
  end
  walk(node)
  return out
end
"""


@pytest.mark.parametrize("targets", [(), ("trips",)])
def test_card_names_use_the_keep_columns_space(lua, tmp_path, targets):
    # Text wider than its cell is shrunk, and modifier labels are too long for
    # a hand name's cell; card rows have no Keep value, so they take its space.
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=targets, queries=("lucky", "sealed"))
    mod.update(0.1)
    widths = rt.execute(CELL_WIDTHS)(rt.eval("BOXES[#BOXES]").definition)
    assert widths["Lucky card: 1+"] == widths["Sealed card: 1+"] == pytest.approx(1.4 + 0.75 + 0.25)
    if targets:
        assert widths["Trips"] == pytest.approx(1.4)


def test_panel_greys_out_old_odds(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, queries=())
    mod.update(0.1)
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.2)  # new snapshot; the app acknowledges it
    overlay.write(tmp_path / "calculatro_odds.json", overlay.pending(read_state(tmp_path)))
    mod.update(0.1)
    texts = panel_texts(rt)
    assert "80.0%@light" in texts and "Discard@muted" in texts  # old odds, not yet greyed
    mod.update(0.3)
    texts = panel_texts(rt)
    assert "80.0%@stale" in texts and texts[1] == "Calculating...@muted"  # beside the title
    answer(lua, tmp_path, queries=())
    mod.update(0.1)
    texts = panel_texts(rt)
    assert not any(t.endswith("@stale") for t in texts) and "Calculating...@muted" not in texts


def test_panel_without_the_app(lua, tmp_path):
    rt, mod = lua
    (tmp_path / "calculatro_odds.json").write_text(json.dumps(
        {"schema": overlay.SCHEMA, "id": "an-earlier-session:4", "targets": [{"name": "Pair", "keep": "1%"}]}))
    mod.update(0.5)
    assert panel_texts(rt) is None
    mod.update(0.5)
    assert panel_texts(rt) == ["CALCULATRO@light", "Calculating...@muted"]
    mod.update(2.0)
    assert panel_texts(rt) == ["CALCULATRO@light", "Waiting for the Calculatro app...@muted"]


def test_panel_hidden_outside_blinds_and_by_hotkey(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    assert panel_texts(rt)
    rt.execute("G.STATE = G.STATES.SHOP")
    mod.update(0.1)
    assert panel_texts(rt) is None
    mod.key_press("c")  # ignored outside blinds
    rt.execute("G.STATE = G.STATES.SELECTING_HAND")
    mod.update(0.1)
    assert panel_texts(rt)

    mod.key_press("c")
    mod.update(0.1)
    assert panel_texts(rt) is None
    rt.execute("G.CONTROLLER.text_input_hook = {}")
    mod.key_press("c")  # typing
    mod.update(0.1)
    assert panel_texts(rt) is None
    rt.execute("G.CONTROLLER.text_input_hook = nil")
    mod.key_press("c")
    mod.update(0.1)
    assert panel_texts(rt)


def test_panel_rebuilt_after_the_game_removes_it(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    rt.execute("BOXES[#BOXES].REMOVED = true")  # deleting the run removes every UI box
    mod.update(0.1)
    assert panel_texts(rt)
    boxes = rt.eval("#BOXES")
    rt.execute("G.deck = {cards = G.deck.cards, T = {w = 2.25, y = 8.9}}")  # a new run's deck
    mod.update(0.1)
    assert rt.eval("#BOXES") == boxes + 1 and rt.eval("BOXES[#BOXES].config.major == G.deck")

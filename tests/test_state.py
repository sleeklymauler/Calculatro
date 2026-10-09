"""Runs the real mod Lua against a mocked Balatro `G` and parses the output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from calculatro.__main__ import odds, summarize
from calculatro.cards import parse_query
from calculatro.hands import get_target
from calculatro.source import FileStateSource
from calculatro.rules import Rules
from calculatro.state import GameState, Joker

lupa = pytest.importorskip("lupa")

MOD = Path(__file__).resolve().parents[1] / "mod" / "Calculatro" / "calculatro.lua"

MOCK_G = r"""
local function card(value, id, suit, opts)
  opts = opts or {}
  return {
    base = {value = value, id = id, suit = suit},
    config = {center_key = opts.center or 'c_base'},
    ability = {},
    edition = opts.edition and {type = opts.edition} or nil,
    seal = opts.seal,
    debuff = opts.debuff,
    facing = opts.facing or 'front',
    highlighted = opts.highlighted,
  }
end
G = {
  STAGES = {MAIN_MENU = 1, RUN = 2}, STAGE = 2,
  STATES = {SELECTING_HAND = 1, HAND_PLAYED = 2, DRAW_TO_HAND = 3, SHOP = 5, PLAY_TAROT = 6,
            BLIND_SELECT = 7, ROUND_EVAL = 8, TAROT_PACK = 9},
  STATE = 1,
  GAME = {current_round = {hands_left = 4, discards_left = 3}, blind = {name = 'Big Blind'}},
  hand = {config = {card_limit = 8}, cards = {
    card('Queen', 12, 'Hearts'), card('Queen', 12, 'Spades'),
    card('9', 9, 'Clubs', {seal = 'Red', highlighted = true}),
    card('9', 9, 'Diamonds', {edition = 'foil', highlighted = true}),
    card('2', 2, 'Hearts', {facing = 'back'}),
  }},
  play = {cards = {}},
  deck = {T = {w = 2.25}, cards = {
    card('Queen', 12, 'Clubs'), card('Ace', 14, 'Spades', {center = 'm_wild'}),
    card('5', 5, 'Hearts', {center = 'm_stone'}), card('10', 10, 'Diamonds', {debuff = true}),
  }},
  discard = {cards = {card('3', 3, 'Clubs')}},
  jokers = {cards = {
    {config = {center_key = 'j_four_fingers'}},
    {config = {center_key = 'j_smeared'}, debuff = true},
  }},
  CONTROLLER = {},
  HIGHLIGHT_H = 0.5,
  -- UI, for the odds panel
  UIT = {ROOT = 'ROOT', R = 'R', C = 'C', T = 'T', B = 'B', O = 'O'},
  ASSET_ATLAS = {ui_1 = 'ui_1', ui_2 = 'ui_2'},
  SETTINGS = {colourblind_option = false},
  C = {UI = {TEXT_LIGHT = 'light', BACKGROUND_INACTIVE = 'stale'}, JOKER_GREY = 'muted', WHITE = 'white',
       GREEN = 'green', RED = 'red', BLUE = 'blue', ORANGE = 'orange', GREY = 'grey', L_BLACK = 'l_black',
       CLEAR = 'clear', BLACK = {0.2, 0.4, 0.6, 1},
       DYN_UI ={MAIN = 'main', BOSS_MAIN = 'boss_main', BOSS_DARK = 'boss_dark'}},
  FUNCS = {},
  -- Panel space: from 3.35 (under the consumables' label) to 6.2 (above the raised hand).
  consumeables = {T = {y = 0, h = 2.75}},
}
G.deck.T.y = 8.9
BOXES = {}  -- every UIBox created, newest last
-- Mock layout: the panel is 3.25 wide per column, and 0.5 + 0.3 per row of its
-- tallest column, plus 0.3 for a message row after the odds (there should be none).
local function panel_size(node)
  local columns, rows = 0, 0
  local function walk(n)
    if n.config and n.config.id == 'calculatro_column' then
      columns, rows = columns + 1, math.max(rows, #n.nodes)
    end
    for _, child in ipairs(n.nodes or {}) do walk(child) end
  end
  walk(node)
  local body = node.nodes[1].nodes[1].nodes  -- title, odds, footer
  local footer = (columns > 0 and #body > 2) and 0.3 or 0
  return 3.25 * math.max(columns, 1), 0.5 + 0.3 * rows + footer
end
function Sprite(x, y, w, h, atlas, pos)
  return {T = {x = x, y = y, w = w, h = h}, atlas = atlas, pos = pos,
          states = {drag = {}, hover = {}, collide = {}}}
end
function UIBox(args)
  local w, h = panel_size(args.definition)
  local box = {definition = args.definition, config = args.config, T = {w = w, h = h},
               alignment = {offset = args.config.offset}}
  function box:remove() self.REMOVED = true end
  function box:align_to_major() end
  function box:hard_set_VT() end
  BOXES[#BOXES + 1] = box
  return box
end
"""


@pytest.fixture
def lua(tmp_path):
    rt = lupa.LuaRuntime()
    rt.execute(MOCK_G)
    save_dir = str(tmp_path).replace("\\", "/")
    rt.execute(f"love = {{filesystem = {{getSaveDirectory = function() return '{save_dir}' end}}}}")
    mod = rt.execute(MOD.read_text(encoding="utf-8"))
    return rt, mod


def snapshot_json(lua) -> dict:
    _, mod = lua
    return json.loads(mod.encode(mod.snapshot()))


def test_snapshot_parses(lua):
    state = GameState.from_json(snapshot_json(lua))
    assert state.in_run and state.state == "SELECTING_HAND"
    assert (state.hand_size, state.hands_left, state.discards_left) == (8, 4, 3)
    assert state.blind == "Big Blind"
    assert [c.short() for c in state.hand] == ["Q♥", "Q♠", "9♣", "9♦", "??"]
    assert state.hand[2].seal == "Red" and state.hand[3].edition == "foil"
    assert len(state.deck) == 4 and len(state.discard) == 1
    assert state.jokers == [Joker("j_four_fingers"), Joker("j_smeared", debuff=True)]
    assert state.rules == Rules(four_fingers=True)  # a debuffed joker has no effect
    assert "Jokers: j_four_fingers, j_smeared (debuffed)" in summarize(state)
    assert state.selected == [2, 3]
    assert [c.short() for c in state.hidden_pool] == ["2♥"]


def test_snapshot_sends_what_is_tracked(lua):
    rt, _ = lua
    assert GameState.from_json(snapshot_json(lua)).tracked_hands == []  # a new run tracks nothing
    rt.execute("""G.GAME.calculatro = {hands = {'Pair', 'Flush'},
                                       cards = {{query = 'at least 2 queen', label = 'Queen: 2+'}}}""")
    state = GameState.from_json(snapshot_json(lua))
    assert state.tracked_hands == ["Pair", "Flush"] and state.tracked_cards == ["at least 2 queen"]



def test_snapshot_sends_the_serpents_state(lua):
    rt, _ = lua
    state = GameState.from_json(snapshot_json(lua))
    assert (state.blind_disabled, state.hands_played, state.discards_used) == (False, 0, 0)
    assert not state.serpent
    rt.execute("""G.GAME.blind = {name = 'The Serpent'}
                  G.GAME.current_round.hands_played = 1
                  G.GAME.current_round.discards_used = 2""")
    state = GameState.from_json(snapshot_json(lua))
    assert (state.blind, state.hands_played, state.discards_used) == ("The Serpent", 1, 2)
    assert state.serpent
    rt.execute("G.GAME.blind.disabled = true")  # by Chicot or Luchador
    state = GameState.from_json(snapshot_json(lua))
    assert state.blind_disabled and not state.serpent


def test_rules_from_jokers():
    assert Rules.from_jokers([]) == Rules()
    # Copies change nothing; Splash and other jokers don't change the odds.
    jokers = [Joker("j_shortcut"), Joker("j_shortcut"), Joker("j_splash"), Joker("j_joker")]
    assert Rules.from_jokers(jokers) == Rules(shortcut=True)
    # One debuffed copy doesn't cancel another that isn't.
    jokers = [Joker("j_pareidolia", debuff=True), Joker("j_pareidolia"), Joker("j_smeared", debuff=True)]
    assert Rules.from_jokers(jokers) == Rules(pareidolia=True)


def test_odds_for_selection(lua):
    state = GameState.from_json(snapshot_json(lua))
    # Hand holds 5 of 8. Keeping it draws 3 of the 4-card deck, so 4 of the 5
    # unseen cards (Q♣ A* St T♦ 2♥, counting the face-down one) end up in hand:
    # trips needs Q♣, 4 in 5. Discarding 99 draws the whole deck: Q♣ guaranteed.
    out = odds(state, [get_target("trips"), get_target("pair")], [parse_query("no face")])
    assert out.splitlines() == [
        "Odds: keep hand (draw 3)  ->  discard 9♣ 9♦ and draw 4",
        "  Any target       100.00%  ->  100.00%",
        "  Three of a Kind   80.00%  ->  100.00%",
        "  Pair             100.00%  ->  100.00%",
        "  Drawn cards:",
        "  Face card: none                20.00%",  # only if the face-down card is Q♣: 1 in 5
    ]


def test_odds_without_selection(lua):
    rt, _ = lua
    rt.execute("for _, c in ipairs(G.hand.cards) do c.highlighted = nil end")
    state = GameState.from_json(snapshot_json(lua))
    assert odds(state, [get_target("trips")], [parse_query("queen")]).splitlines() == [
        "Odds (current hand, drawing 3):",
        "  Three of a Kind   80.00%",
        "Select cards in game to see the odds after discarding them.",
    ]
    assert odds(state, [], [parse_query("queen")]) == (
        "Select cards in game to see the odds after discarding them.")


def test_odds_cards_only(lua):
    out = odds(GameState.from_json(snapshot_json(lua)), [], [parse_query("queen")])
    assert out.splitlines() == [
        "Odds: discard 9♣ 9♦ and draw 4",
        "  Queen: at least 1   80.00%",
    ]


def test_counts_exclude_stone_and_wild(lua):
    state = GameState.from_json(snapshot_json(lua))
    assert state.rank_counts() == {"Queen": 1, "Ace": 1, "10": 1}
    assert state.suit_counts() == {"Clubs": 1, "Diamonds": 1}


def test_empty_areas_encode_as_arrays(lua):
    rt, mod = lua
    rt.execute("G.hand.cards = {}")
    data = snapshot_json(lua)
    assert data["hand"] == [] and data["played"] == []


def test_deck_order_not_leaked(lua):
    rt, mod = lua
    before = snapshot_json(lua)["deck"]
    rt.execute("local c = G.deck.cards; c[1], c[4] = c[4], c[1]")
    assert snapshot_json(lua)["deck"] == before


def test_not_in_run(lua):
    rt, _ = lua
    rt.execute("G.STAGE = G.STAGES.MAIN_MENU")
    state = GameState.from_json(snapshot_json(lua))
    assert not state.in_run
    assert summarize(state) == "Not in a run."


def test_update_writes_file_only_on_change(lua, tmp_path):
    rt, mod = lua
    path = tmp_path / "calculatro_state.json"
    mod.update(1.0)
    state = FileStateSource(path).read()
    assert state is not None and len(state.hand) == 5
    assert "Deck (4 cards remaining)" in summarize(state)

    path.write_text("sentinel")
    mod.update(1.0)  # unchanged state: no rewrite
    assert path.read_text() == "sentinel"

    rt.execute("G.GAME.current_round.discards_left = 2")
    mod.update(1.0)
    assert FileStateSource(path).read().discards_left == 2


def test_missing_or_partial_file_returns_none(tmp_path):
    path = tmp_path / "calculatro_state.json"
    assert FileStateSource(path).read() is None
    path.write_text('{"schema": 1, "in_ru')
    assert FileStateSource(path).read() is None


def test_launch_command(lua, tmp_path):
    _, mod = lua
    assert mod.launch_command(1234) is None  # no launch.txt: the app isn't started
    mod_dir = tmp_path / "Mods" / "Calculatro"
    mod_dir.mkdir(parents=True)
    (mod_dir / "launch.txt").write_text("C:\venv\Scripts\pythonw.exe\r\n", encoding="utf-8")
    assert tuple(mod.launch_command(1234)) == (
        "C:\venv\Scripts\pythonw.exe", "-m calculatro watch --game-pid 1234")

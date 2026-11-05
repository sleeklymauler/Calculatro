"""Runs the real mod Lua against a mocked Balatro `G` and parses the output."""

from __future__ import annotations

import functools
import importlib
import json
import re
import shutil
import tomllib
from pathlib import Path

import pytest

from calculatro.__main__ import calculate, summarize
from calculatro.cards import parse_query
from calculatro.hands import get_target
from calculatro.rules import Rules
from calculatro.source import FileStateSource
from calculatro.state import SCHEMA, GameState, Joker, MalformedState, SchemaMismatch

pytest.importorskip("lupa")

MOD_DIR = Path(__file__).resolve().parents[1] / "mod" / "Calculatro"


@functools.cache
def mod_sources() -> dict[str, str]:
    """The mod's modules' code by module name, from the same list Lovely registers them from."""
    patches = tomllib.loads((MOD_DIR / "lovely.toml").read_text(encoding="utf-8"))["patches"]
    return {p["module"]["name"]: (MOD_DIR / p["module"]["source"]).read_text(encoding="utf-8")
            for p in patches if "module" in p}


# What Lovely does with a module patch, and starting over if it's been required.
REGISTER = """
return function(name, source)
  package.loaded[name] = nil
  package.preload[name] = assert(load(source, '=' .. name))
end
"""


def load_mod(rt, name: str = "calculatro"):
    """A new instance of the mod's module `name`, in the Lua runtime `rt`."""
    register = rt.execute(REGISTER)
    for module, source in mod_sources().items():
        register(module, source)
    return rt.execute(f"return (require('{name}'))")  # just the module: Lua 5.4's returns a second value


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
  C = {UI = {TEXT_LIGHT = 'light', BACKGROUND_INACTIVE = 'stale'}, JOKER_GREY = 'muted',
       WHITE = 'white', GREEN = 'green', RED = 'red', BLUE = 'blue', ORANGE = 'orange', GOLD = 'gold', GREY = 'grey',
       L_BLACK = 'l_black', CLEAR = 'clear', BLACK = {0.2, 0.4, 0.6, 1},
       DYN_UI ={MAIN = 'main', BOSS_MAIN = 'boss_main', BOSS_DARK = 'boss_dark'}},
  FUNCS = {},
  -- Panel space: from 3.35 (under the consumables' label) to 6.2 (above the raised hand).
  consumeables = {T = {y = 0, h = 2.75}},
}
G.deck.T.y = 8.9
G.hand.config.highlighted_limit = 5
SOUNDS = 0  -- click sounds selecting cards
function G.hand:unhighlight_all()  -- Cerulean Bell's forced card stays selected
  for _, c in ipairs(self.cards) do
    if not c.ability.forced_selection then c.highlighted = false end
  end
end
function G.hand:add_to_highlighted(card, silent)
  card.highlighted = true
  if not silent then SOUNDS = SOUNDS + 1 end
end
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


# The game's love.filesystem. Lua's own file functions stand in for it, which
# can't take a non-ASCII path; see test_non_ascii_save_directory for those.
MOCK_FILESYSTEM = r"""
local dir = ...
local function open(name, mode) return io.open(dir .. '/' .. name, mode) end
local function put(name, mode, text)
  local f = open(name, mode)
  if not f then return false, 'could not open ' .. name end
  f:write(text)
  f:close()
  return true
end
love = {filesystem = {
  getSaveDirectory = function() return dir end,
  read = function(name)
    local f = open(name, 'rb')
    if not f then return nil, 'does not exist' end
    local text = f:read('*a')
    f:close()
    return text, #text
  end,
  write = function(name, text) return put(name, 'wb', text) end,
  append = function(name, text) return put(name, 'ab', text) end,
  getInfo = function(name)
    local f = open(name, 'rb')
    if f then f:close(); return {type = 'file'} end
  end,
}}
"""

# Outside LuaJIT there's no FFI for the mod's MoveFileExW.
REPLACE_WITHOUT_FFI = r"""
return function(from, to)
  local dir = love.filesystem.getSaveDirectory()
  os.remove(dir .. '/' .. to)
  return os.rename(dir .. '/' .. from, dir .. '/' .. to)
end
"""


# Lua's own functions can't list a directory.
def directory_items(rt, save_dir):
    def items(name):
        path = save_dir / name
        return rt.table_from([p.name for p in path.iterdir()] if path.is_dir() else [])
    return items


# The game runs LuaJIT (Lua 5.1) and lupa's default runtime is a newer Lua, so the
# mod's tests run in both: a 5.2+ feature fails in the LuaJIT run.
@pytest.fixture(params=["lupa", "lupa.luajit21"], ids=["lua", "luajit"])
def lua(request, tmp_path):
    rt = importlib.import_module(request.param).LuaRuntime()
    rt.execute(MOCK_G)
    save_dir = str(tmp_path).replace("\\", "/")
    rt.execute(MOCK_FILESYSTEM, save_dir)
    rt.eval("love.filesystem").getDirectoryItems = directory_items(rt, tmp_path)
    mod = load_mod(rt)
    if rt.eval("jit") is None:
        mod.replace_file = rt.execute(REPLACE_WITHOUT_FFI)
    mod.launch = rt.eval("function() return false end")  # no test should start the app by accident
    return rt, mod


def snapshot_json(lua) -> dict:
    rt, mod = lua
    return json.loads(rt.eval("require('calculatro.json')").encode(mod.snapshot()))


def test_snapshot_parses(lua):
    state = GameState.from_json(snapshot_json(lua))
    assert state.in_run and state.state == "SELECTING_HAND"
    assert (state.hand_size, state.hands_left, state.discards_left) == (8, 4, 3)
    assert state.blind == "Big Blind"
    assert [c.short() for c in state.hand] == ["Q♥", "Q♠", "9♣", "9♦", "??"]
    assert state.hand[2].seal == "Red" and state.hand[3].edition == "foil"
    assert len(state.unseen) == 5 and state.deck_size == 4 and len(state.discard) == 1
    assert state.jokers == [Joker("j_four_fingers"), Joker("j_smeared", debuff=True)]
    assert state.rules == Rules(four_fingers=True)  # a debuffed joker has no effect
    assert "Jokers: j_four_fingers, j_smeared (debuffed)" in summarize(state)
    assert state.selected == [2, 3]
    assert state.hidden == 1 and "2♥" in [c.short() for c in state.unseen]


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


def test_snapshot_sends_the_bells_forced_card(lua):
    rt, _ = lua
    state = GameState.from_json(snapshot_json(lua))
    assert state.forced is None and not state.bell
    rt.execute("""G.GAME.blind = {name = 'Cerulean Bell'}
                  G.hand.cards[3].ability.forced_selection = true""")
    raw = snapshot_json(lua)
    state = GameState.from_json(raw)
    assert state.bell and state.forced == 2
    assert [c.forced for c in state.hand] == [False, False, True, False, False]
    # Only hand cards say whether they're forced.
    rt.execute("G.deck.cards[1].ability.forced_selection = true")
    raw = snapshot_json(lua)
    assert not any("forced" in c for c in raw["unseen"])
    rt.execute("G.GAME.blind.disabled = true")
    assert not GameState.from_json(snapshot_json(lua)).bell


def test_snapshot_hides_joker_order(lua):
    # Amber Acorn shuffles the jokers face down: their order mustn't leak.
    rt, _ = lua
    before = snapshot_json(lua)["jokers"]
    rt.execute("""local c = G.jokers.cards
                  table.insert(c, 1, {config = {center_key = 'j_shortcut'}})
                  c[1], c[3] = c[3], c[1]""")
    shuffled = snapshot_json(lua)["jokers"]
    rt.execute("local c = G.jokers.cards; c[1], c[2], c[3] = c[3], c[1], c[2]")
    assert snapshot_json(lua)["jokers"] == shuffled == sorted(shuffled, key=lambda j: json.dumps(j, sort_keys=True))
    assert len(shuffled) == 3 and all(j in shuffled for j in before)


def test_snapshot_marks_perished_jokers(lua):
    # Crimson Heart can't lift a run-out Perishable joker's debuff, so the app needs to know.
    rt, _ = lua
    rt.execute("""table.insert(G.jokers.cards, {config = {center_key = 'j_shortcut'}, debuff = true,
                                                ability = {perishable = true, perish_tally = 0}})
                  table.insert(G.jokers.cards, {config = {center_key = 'j_joker'},
                                                ability = {perishable = true, perish_tally = 2}})""")
    state = GameState.from_json(snapshot_json(lua))
    assert state.jokers == [Joker("j_four_fingers"), Joker("j_joker"), Joker("j_shortcut", debuff=True, perished=True),
                            Joker("j_smeared", debuff=True)]


def test_rules_from_jokers():
    assert Rules.from_jokers([]) == Rules()
    # Copies change nothing; Splash and other jokers don't change the odds.
    jokers = [Joker("j_shortcut"), Joker("j_shortcut"), Joker("j_splash"), Joker("j_joker")]
    assert Rules.from_jokers(jokers) == Rules(shortcut=True)
    # One debuffed copy doesn't cancel another that isn't.
    jokers = [Joker("j_pareidolia", debuff=True), Joker("j_pareidolia"), Joker("j_smeared", debuff=True)]
    assert Rules.from_jokers(jokers) == Rules(pareidolia=True)


def odds(state, targets, queries=()) -> str:
    """The odds as the console shows them."""
    return calculate(state, targets, list(queries))[0]


def test_odds_for_selection(lua):
    state = GameState.from_json(snapshot_json(lua))
    # Hand holds 5 of 8, but keeping it draws nothing (the game only refills it
    # after a play or discard): trips needs the face-down card to be Q♣, 1 in the
    # 5 unseen (Q♣ A* St T♦ 2♥). Discarding 99 draws the whole deck: Q♣ guaranteed.
    # Card queries count the final hand too: a third queen is the same Q♣, and
    # the nines are only in hand if kept.
    out = odds(state, [get_target("trips"), get_target("pair")], [parse_query("3+ queens"),
                                                                  parse_query("at most 1 nine")])
    assert out.splitlines() == [
        "Odds: keep hand  ->  discard 9♣ 9♦ and draw 4",
        "  Any target         100.00%  ->  100.00%",
        "  Three of a Kind     20.00%  ->  100.00%",
        "  Pair               100.00%  ->  100.00%",
        "  In hand:",
        "  Queen: at least 3   20.00%  ->  100.00%",
        "  9: at most 1         0.00%  ->  100.00%",
    ]


def test_odds_without_selection(lua):
    rt, _ = lua
    rt.execute("for _, c in ipairs(G.hand.cards) do c.highlighted = nil end")
    state = GameState.from_json(snapshot_json(lua))
    assert odds(state, [get_target("trips")], [parse_query("3+ queens")]).splitlines() == [
        "Odds (current hand):",
        "  Three of a Kind     20.00%",
        "  In hand:",
        "  Queen: at least 3   20.00%",
        "Select cards in game to see the odds after discarding them.",
    ]
    assert odds(state, [], [parse_query("queen")]).splitlines() == [
        "Odds (current hand):",
        "  Queen: at least 1  100.00%",
        "Select cards in game to see the odds after discarding them.",
    ]


def test_odds_on_the_last_hand(lua):
    # With no discards and no hand after this one, nothing is drawn after the
    # selection: it's the hand to be played, so there are no discard odds.
    rt, _ = lua
    rt.execute("G.GAME.current_round.hands_left = 1; G.GAME.current_round.discards_left = 0")
    state = GameState.from_json(snapshot_json(lua))
    assert state.selected
    assert odds(state, [get_target("trips")]).splitlines() == [
        "Odds (current hand):",
        "  Three of a Kind   20.00%",
        "Last hand, with no discards left: nothing is drawn after it.",
    ]


def test_odds_cards_only(lua):
    out = odds(GameState.from_json(snapshot_json(lua)), [], [parse_query("3+ queens")])
    assert out.splitlines() == [
        "Odds: keep hand  ->  discard 9♣ 9♦ and draw 4",
        "  Queen: at least 3   20.00%  ->  100.00%",
    ]


def test_counts_exclude_stone_and_wild(lua):
    state = GameState.from_json(snapshot_json(lua))
    assert state.rank_counts() == {"Queen": 1, "Ace": 1, "10": 1, "2": 1}
    assert state.suit_counts() == {"Clubs": 1, "Diamonds": 1, "Hearts": 1}


def test_stone_base_card_not_leaked(lua):
    # A Stone card hides its rank and suit in game, so they aren't exported.
    rt, _ = lua
    rt.execute("G.hand.cards[1].config.center_key = 'm_stone'")
    data = snapshot_json(lua)
    stones = [c for c in data["hand"] + data["unseen"] if c.get("enhancement") == "m_stone"]
    assert len(stones) == 2
    assert not any(k in c for c in stones for k in ("rank", "rank_id", "suit"))


def test_empty_areas_encode_as_arrays(lua):
    rt, mod = lua
    rt.execute("G.hand.cards = {}")
    data = snapshot_json(lua)
    assert data["hand"] == [] and data["played"] == []


def test_deck_order_not_leaked(lua):
    rt, mod = lua
    before = snapshot_json(lua)["unseen"]
    rt.execute("local c = G.deck.cards; c[1], c[4] = c[4], c[1]")
    assert snapshot_json(lua)["unseen"] == before


def test_face_down_cards_not_told_from_the_deck(lua):
    # Which unseen card is face down in hand isn't public, so swapping it with
    # a deck card (selected or not) must give the same snapshot.
    rt, _ = lua
    before = snapshot_json(lua)
    rt.execute("""local hand, deck = G.hand.cards, G.deck.cards
                  hand[5], deck[1] = deck[1], hand[5]
                  hand[5].facing, deck[1].facing = 'back', 'front'""")
    assert snapshot_json(lua) == before
    rt.execute("G.hand.cards[5].highlighted = true")
    after = snapshot_json(lua)
    assert after["unseen"] == before["unseen"] and after["hand"][4] == {"hidden": True, "highlighted": True}
    assert "deck" not in after and "hidden_pool" not in after


def test_face_down_discards_not_told_from_the_deck(lua):
    # The game doesn't turn a face-down card up when it's discarded (View Deck
    # still counts it as unplayed), so which unseen card it was isn't public:
    # swapping it with a deck card must give the same snapshot.
    rt, _ = lua
    rt.execute("""local c = table.remove(G.deck.cards, 1)
                  c.facing = 'back'
                  G.discard.cards[2] = c""")
    before = snapshot_json(lua)
    assert before["discard"][1] == {"hidden": True, "highlighted": False}
    rt.execute("""local deck, discard = G.deck.cards, G.discard.cards
                  discard[2], deck[1] = deck[1], discard[2]
                  discard[2].facing, deck[1].facing = 'back', 'front'""")
    assert snapshot_json(lua) == before
    state = GameState.from_json(before)
    assert len(state.unseen) == 5 and "Q♣" in [c.short() for c in state.unseen]
    assert (state.deck_size, state.hidden, state.discarded_hidden) == (3, 1, 1)
    assert "(3 cards in deck, 1 face down in hand, 1 discarded face down)" in summarize(state)


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
    assert "Unseen (4 cards in deck, 1 face down in hand)" in summarize(state)

    path.write_text("sentinel")
    mod.update(1.0)  # unchanged state: no rewrite
    assert path.read_text() == "sentinel"

    rt.execute("G.GAME.current_round.discards_left = 2")
    mod.update(1.0)
    assert FileStateSource(path).read().discards_left == 2


def test_missing_file_returns_none(tmp_path):
    assert FileStateSource(tmp_path / "calculatro_state.json").read() is None


@pytest.mark.parametrize("content", [
    b'{"schema": 1, "in_ru',  # truncated
    b'\xef\xbb\xbf{"schema": 1}',  # a byte order mark
    b'{"blind": "caf\xe9"}',  # not UTF-8
    b"[" * 200_000,  # nested too deep to parse
], ids=["truncated", "bom", "invalid-utf8", "deep-nesting"])
def test_undecodable_file_reported_once_until_it_changes(tmp_path, capsys, monkeypatch, content):
    path = tmp_path / "calculatro_state.json"
    path.write_bytes(content)
    source = FileStateSource(path)
    with pytest.raises(MalformedState):
        source.read()

    reads = []
    read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: reads.append(a) or read_text(*a, **k))
    assert source.poll() is None
    assert source.poll() is None  # unchanged: not read again
    assert len(reads) == 1
    assert capsys.readouterr().err.count("isn't a valid snapshot") == 1

    path.write_text(json.dumps({"schema": SCHEMA, "id": "x:1", "in_run": False}))
    state = source.poll()
    assert state is not None and state.id == "x:1"


def test_old_schema_waits_for_a_current_snapshot(lua, tmp_path, capsys):
    """A file from another version of the mod (the last session's, or a mod not
    reinstalled after an update) is skipped with one message, not fatal."""
    _, mod = lua
    path = tmp_path / "calculatro_state.json"
    source = FileStateSource(path)
    path.write_text(json.dumps({"schema": SCHEMA - 1, "id": "old:1", "in_run": False}))
    with pytest.raises(SchemaMismatch):
        source.read()
    assert source.poll() is None
    assert source.poll() is None  # unchanged: not read or reported again
    err = capsys.readouterr().err
    assert err.count("Ignoring") == 1 and "install_mod.ps1" in err

    path.write_text(json.dumps({"schema": SCHEMA - 1, "id": "old:2", "in_run": False}))
    assert source.poll() is None
    assert capsys.readouterr().err == ""  # the same schema is reported once

    mod.update(1.0)  # the mod's first snapshot
    state = source.poll()
    assert state is not None and state.in_run and len(state.hand) == 5


def test_newer_schema_suggests_updating_the_app(tmp_path, capsys):
    path = tmp_path / "calculatro_state.json"
    path.write_text(json.dumps({"schema": SCHEMA + 1, "in_run": False}))
    assert FileStateSource(path).poll() is None
    assert "update the app" in capsys.readouterr().err


@pytest.mark.parametrize("raw", [
    [],
    "snapshot",
    {"schema": SCHEMA, "in_run": True, "hand": [None]},
    {"schema": SCHEMA, "in_run": True, "tracked": ["Pair"]},
    {"schema": SCHEMA, "in_run": True, "tracked": {"hands": "Pair"}},
    {"schema": SCHEMA, "in_run": True, "tracked": {"cards": {"at least 2 queen": True}}},
    {"schema": SCHEMA, "in_run": True, "jokers": ["j_joker"]},
    {"schema": SCHEMA, "in_run": False, "id": 5},
    {"schema": SCHEMA, "in_run": True, "id": 5},
    {"schema": SCHEMA, "in_run": True, "state": ["SELECTING_HAND"]},
    {"schema": SCHEMA, "in_run": True, "blind": 1},
    {"schema": SCHEMA, "in_run": True, "jokers": [{"key": 5}]},
    {"schema": SCHEMA, "in_run": True, "hand": [{"rank": ["Ace"]}]},
    {"schema": SCHEMA, "in_run": True, "unseen": [{"suit": ["Spades"]}]},
])
def test_malformed_snapshot_raises(raw):
    with pytest.raises(MalformedState):
        GameState.from_json(raw)


def test_empty_lua_table_is_an_empty_list():
    state = GameState.from_json({"schema": SCHEMA, "in_run": True, "tracked": {"hands": {}, "cards": {}}})
    assert state.tracked_hands == [] and state.tracked_cards == []


@pytest.mark.parametrize(("search", "parsed"), [
    ({"key": "Pair", "nonce": 3}, ("Pair", 3)),
    ({"key": "Pair", "nonce": True}, None),
    ({"key": "Pair", "nonce": 3.5}, None),
    ({"key": 5, "nonce": 3}, None),
    ("Pair", None),
])
def test_search_request(search, parsed):
    assert GameState.from_json({"schema": SCHEMA, "in_run": True, "search": search}).search == parsed


def test_malformed_snapshot_skipped_until_a_valid_one(lua, tmp_path, capsys):
    """Valid JSON of the wrong shape is skipped with one message, not fatal."""
    _, mod = lua
    path = tmp_path / "calculatro_state.json"
    source = FileStateSource(path)
    path.write_text(json.dumps({"schema": SCHEMA, "id": "x:1", "in_run": True, "hand": [None]}))
    assert source.poll() is None
    assert source.poll() is None  # unchanged: not read again
    path.write_text(json.dumps({"schema": SCHEMA, "id": "x:2", "in_run": True, "hand": [None]}))
    assert source.poll() is None  # the same error is reported once
    assert capsys.readouterr().err.count("isn't a valid snapshot") == 1

    mod.update(1.0)
    state = source.poll()
    assert state is not None and state.in_run and len(state.hand) == 5


def test_select_limit_zero_kept():
    raw = {"schema": SCHEMA, "in_run": True}
    assert GameState.from_json(raw).select_limit == 5  # missing: the game's default
    assert GameState.from_json({**raw, "select_limit": 0}).select_limit == 0


def test_poll_tolerates_a_locked_file(tmp_path, monkeypatch):
    path = tmp_path / "calculatro_state.json"
    path.write_text(json.dumps({"schema": SCHEMA, "in_run": False}))
    source = FileStateSource(path)

    def locked(*args, **kwargs):
        raise PermissionError

    monkeypatch.setattr(type(path), "stat", locked)
    assert source.poll() is None
    monkeypatch.undo()
    assert source.poll() is not None


def install(save_dir, folder: str = "Calculatro"):
    mod_dir = save_dir / "Mods" / folder
    mod_dir.mkdir(parents=True)
    shutil.copy(MOD_DIR / "lovely.toml", mod_dir)
    return mod_dir


@pytest.mark.parametrize("folder", ["Calculatro", "Calculatro-0.1.0 - Copy"])
def test_launch_command(lua, tmp_path, folder):
    _, mod = lua
    assert mod.launch_command(1234) is None  # no mod folder: the app isn't started
    mod_dir = install(tmp_path, folder)
    assert mod.launch_command(1234) is None  # no launch.txt or python folder
    (mod_dir / "python").mkdir()
    (mod_dir / "python" / "pythonw.exe").write_bytes(b"")
    # A release: its own Python, with a Windows path.
    assert tuple(mod.launch_command(1234)) == (
        str(mod_dir / "python" / "pythonw.exe"), "-m calculatro watch --game-pid 1234")
    # launch.txt (install_mod.ps1) takes priority.
    (mod_dir / "launch.txt").write_text(r"C:\venv\Scripts\pythonw.exe" "\r\n", encoding="utf-8")
    assert tuple(mod.launch_command(1234)) == (
        r"C:\venv\Scripts\pythonw.exe", "-m calculatro watch --game-pid 1234")


def test_mod_folder_is_the_one_holding_the_mods_lovely_toml(lua, tmp_path):
    _, mod = lua
    (tmp_path / "Mods" / "lovely").mkdir(parents=True)  # Lovely's own
    (tmp_path / "Mods" / "notes.txt").write_text("not a folder", encoding="utf-8")
    other = tmp_path / "Mods" / "Another Mod"
    other.mkdir()
    (other / "lovely.toml").write_text('[manifest]\nversion = "1.0.0"\n', encoding="utf-8")
    assert mod.mod_folder() is None
    install(tmp_path, "Renamed")
    assert mod.mod_folder() == "Mods/Renamed"
    assert not (tmp_path / "calculatro.log").exists()


def test_mod_folder_skips_one_lovely_ignores(lua, tmp_path):
    _, mod = lua
    (install(tmp_path) / ".lovelyignore").write_bytes(b"")
    assert mod.mod_folder() is None
    install(tmp_path, "Renamed")
    assert mod.mod_folder() == "Mods/Renamed"


def test_mod_folder_with_several_copies_is_reported_once(lua, tmp_path):
    _, mod = lua
    install(tmp_path, "A copy")
    install(tmp_path, "Z copy")
    assert mod.mod_folder() == "Mods/A copy"
    install(tmp_path)
    assert mod.mod_folder() == "Mods/Calculatro"
    log = (tmp_path / "calculatro.log").read_text(encoding="utf-8")
    assert log.count("more than one copy of the mod") == 1
    assert "A copy, Z copy" in log


# What the game's love.filesystem can do and Lua's own functions can't: a UTF-8 path.
def unicode_filesystem(rt, save_dir):
    def read(name):
        path = save_dir / name
        return path.read_bytes().decode("utf-8") if path.is_file() else None

    def write(name, text):
        (save_dir / name).write_bytes(text.encode("utf-8"))
        return True

    def append(name, text):
        with (save_dir / name).open("ab") as f:
            f.write(text.encode("utf-8"))
        return True

    fs = rt.eval("love.filesystem")
    fs.getSaveDirectory = rt.eval("function(dir) return function() return dir end end")(save_dir.as_posix())
    fs.read, fs.write, fs.append = read, write, append
    fs.getInfo = lambda name: True if (save_dir / name).exists() else None
    fs.getDirectoryItems = directory_items(rt, save_dir)


@pytest.mark.parametrize("name", ["Luké", "Łukasz", "光"])
def test_non_ascii_save_directory(lua, tmp_path, request, name):
    if "luajit" not in request.node.name:
        pytest.skip("the state file is replaced through LuaJIT's FFI")
    rt, mod = lua
    save_dir = tmp_path / name
    mod_dir = install(save_dir, f"Calculatro {name}")  # the folder's name can be non-ASCII too
    unicode_filesystem(rt, save_dir)
    state_file = save_dir / "calculatro_state.json"

    mod.write_snapshot()  # the first creates the file, the next replaces it
    first = mod.state_id
    assert json.loads(state_file.read_text(encoding="utf-8"))["id"] == first
    rt.execute("G.GAME.current_round.hands_left = 1")
    mod.write_snapshot()
    assert mod.state_id != first
    assert json.loads(state_file.read_text(encoding="utf-8"))["id"] == mod.state_id
    assert not state_file.with_suffix(".json.tmp").exists()

    (save_dir / "calculatro_odds.json").write_text(
        json.dumps({"schema": mod.odds_schema, "id": mod.state_id, "pending": True}), encoding="utf-8")
    mod.read_odds()
    assert mod.ack_id == mod.state_id

    exe = rf"C:\{name}\pythonw.exe"
    (mod_dir / "launch.txt").write_text(exe + "\r\n", encoding="utf-8")
    command, _ = mod.launch_command(1234)
    assert command == exe

    mod.log_error("something broke")
    assert "[mod] something broke" in (save_dir / "calculatro.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("call", ["io.open", "io.lines", "os.rename", "os.remove", "loadfile", "dofile"])
def test_mod_never_uses_a_function_taking_an_ansi_path(call):
    # They can't find the save directory if the Windows username isn't plain ASCII.
    used = [name for name, source in mod_sources().items() if re.search(rf"\b{re.escape(call)}\b", source)]
    assert not used


def test_snapshot_that_cannot_be_written_is_tried_again(lua, tmp_path):
    rt, mod = lua
    mod.write_snapshot()
    written = mod.state_id
    (tmp_path / "calculatro_state.json.tmp").mkdir()  # in the new one's way
    rt.execute("G.GAME.current_round.hands_left = 1")
    mod.write_snapshot()
    assert mod.state_id == written
    (tmp_path / "calculatro_state.json.tmp").rmdir()
    mod.write_snapshot()
    assert mod.state_id != written
    assert json.loads((tmp_path / "calculatro_state.json").read_text(encoding="utf-8"))["id"] == mod.state_id


def test_snapshots_that_keep_failing_are_reported_once(lua, tmp_path):
    rt, mod = lua
    (tmp_path / "calculatro_state.json.tmp").mkdir()
    for _ in range(9):  # now and then is normal, while the app reads the file
        mod.update(mod.interval)
    assert not (tmp_path / "calculatro.log").exists()
    for _ in range(10):
        mod.update(mod.interval)
    log = (tmp_path / "calculatro.log").read_text(encoding="utf-8")
    assert log.count("could not write the state file") == 1


def test_launch_with_nothing_to_start(lua, request):
    rt, _ = lua
    real = load_mod(rt).launch  # the fixture replaces the mod's
    if "luajit" in request.node.name:
        ok, message = rt.eval("pcall")(real)
        assert not ok and "nothing to start" in message
    else:
        assert real() is False  # no FFI outside LuaJIT


@pytest.mark.parametrize("jokers, copies", [
    ([], 0),
    (["j_joker"], 0),
    (["j_dna"], 1),
    (["j_dna", "j_dna"], 2),
    (["j_blueprint", "j_dna"], 2),  # Blueprint copies the joker to its right
    (["j_dna", "j_blueprint"], 1),
    (["j_blueprint", "j_joker", "j_dna"], 1),
    (["j_blueprint", "j_blueprint", "j_dna"], 3),  # along a chain
    (["j_dna", "j_brainstorm"], 2),  # Brainstorm copies the leftmost
    (["j_joker", "j_brainstorm", "j_dna"], 1),
    (["j_brainstorm", "j_dna"], 1),  # the leftmost Brainstorm has nothing to copy
    (["j_blueprint", "j_brainstorm", "j_dna"], 1),  # Blueprint -> Brainstorm -> Blueprint: a loop
    (["j_brainstorm", "j_blueprint", "j_dna"], 2),  # Brainstorm has nothing to copy, but Blueprint reaches DNA
    (["j_dna", "j_blueprint", "j_blueprint", "j_brainstorm"], 4),  # a chain, by way of Brainstorm, back to DNA
])
def test_snapshot_counts_dna_copies(lua, jokers, copies):
    rt, _ = lua
    items = ", ".join(f"{{config = {{center_key = '{key}'}}}}" for key in jokers)
    rt.execute(f"G.jokers.cards = {{{items}}}")
    snapshot = snapshot_json(lua)
    assert snapshot["dna_copies"] == copies
    assert GameState.from_json(snapshot).dna_copies == copies


@pytest.mark.parametrize("debuffed, copies", [
    (0, 1),  # the Blueprint: it does nothing, and DNA still acts alone
    (1, 0),  # DNA: nothing can copy it either
])
def test_snapshot_dna_copies_with_debuffs(lua, debuffed, copies):
    rt, _ = lua
    rt.execute(f"""G.jokers.cards = {{{{config = {{center_key = 'j_blueprint'}}}},
                                      {{config = {{center_key = 'j_dna'}}}}}}
                   G.jokers.cards[{debuffed + 1}].debuff = true""")
    assert snapshot_json(lua)["dna_copies"] == copies


def test_snapshot_dna_copies_debuffed_link_ends_the_chain(lua):
    rt, _ = lua
    rt.execute("""G.jokers.cards = {{config = {center_key = 'j_blueprint'}},
                                    {config = {center_key = 'j_blueprint'}, debuff = true},
                                    {config = {center_key = 'j_dna'}}}""")
    assert snapshot_json(lua)["dna_copies"] == 1


@pytest.mark.parametrize("jokers", [["j_blueprint", "j_dna"], ["j_dna", "j_blueprint"]])
def test_snapshot_dna_copies_hide_the_order_of_face_down_jokers(lua, jokers):
    # Amber Acorn: the player can't see what Blueprint copies, so it mustn't count.
    rt, _ = lua
    items = ", ".join(f"{{config = {{center_key = '{key}'}}, facing = 'back'}}" for key in jokers)
    rt.execute(f"G.jokers.cards = {{{items}}}")
    assert snapshot_json(lua)["dna_copies"] == 1

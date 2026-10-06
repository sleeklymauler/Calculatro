"""Runs the real mod Lua against a mocked Balatro `G` and parses the output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from calculatro.__main__ import odds, summarize
from calculatro.hands import get_target
from calculatro.source import FileStateSource
from calculatro.state import GameState

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
  STATES = {SELECTING_HAND = 1, SHOP = 5}, STATE = 1,
  GAME = {current_round = {hands_left = 4, discards_left = 3}, blind = {name = 'Big Blind'}},
  hand = {config = {card_limit = 8}, cards = {
    card('Queen', 12, 'Hearts'), card('Queen', 12, 'Spades'),
    card('9', 9, 'Clubs', {seal = 'Red', highlighted = true}),
    card('9', 9, 'Diamonds', {edition = 'foil', highlighted = true}),
    card('2', 2, 'Hearts', {facing = 'back'}),
  }},
  play = {cards = {}},
  deck = {cards = {
    card('Queen', 12, 'Clubs'), card('Ace', 14, 'Spades', {center = 'm_wild'}),
    card('5', 5, 'Hearts', {center = 'm_stone'}), card('10', 10, 'Diamonds', {debuff = true}),
  }},
  discard = {cards = {card('3', 3, 'Clubs')}},
  jokers = {cards = {{config = {center_key = 'j_four_fingers'}}}},
}
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
    assert state.jokers == ["j_four_fingers"]
    assert state.selected == [2, 3]
    assert [c.short() for c in state.hidden_pool] == ["2♥"]


def test_odds_for_selection(lua):
    state = GameState.from_json(snapshot_json(lua))
    # Hand holds 5 of 8, so discarding 99 draws the whole 4-card deck. The
    # unknowns (4 draws + the face-down card) are the entire 5-card pool
    # Q♣ A* St T♦ 2♥, so Q♣ is guaranteed; keeping draws 3 + 1 of 5 (80%).
    out = odds(state, [get_target("trips"), get_target("pair")])
    assert "discard 9♣ 9♦ and draw 4" in out
    assert "Three of a Kind   80.00%  ->  100.00%" in out
    assert len(out.splitlines()) == 4


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

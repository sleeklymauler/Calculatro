"""The settings: the gear's page in the panel, the settings file, and what each setting turns on."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("lupa")

from test_hint import Game
from test_overlay import TEXTS, answer, click, find, panel_texts
from test_state import MOCK_THREAD, lua  # noqa: F401  (fixture: the mod running against a mocked game)

SETTINGS_FILE = "calculatro_settings.json"


def open_settings(lua, tmp_path):  # noqa: F811
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    assert click(lua, "calculatro_settings")
    return rt, mod


def test_gear_opens_the_settings_in_place_of_the_odds_until_done(lua, tmp_path):  # noqa: F811
    rt, mod = open_settings(lua, tmp_path)
    assert mod.settings_open and mod.editor is None
    assert panel_texts(rt) == ["CALCULATRO@light", "Done@light", "Settings@muted",
                               "Face-down windows@light", "Off@light"]
    assert rt.eval("BOXES[#BOXES].config.can_collide")
    # New odds and snapshots don't rebuild it under the cursor.
    boxes = rt.eval("#BOXES")
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(3.0)
    assert rt.eval("#BOXES") == boxes
    assert click(lua, "calculatro_done")
    assert not mod.settings_open and "Hands@muted" in panel_texts(rt)


def test_gear_works_with_nothing_tracked(lua):  # noqa: F811
    rt, mod = lua
    mod.update(1.0)
    assert panel_texts(rt)[2] == "[settings]"
    assert click(lua, "calculatro_settings")
    assert "Face-down windows@light" in panel_texts(rt)


def test_settings_close_with_the_panel(lua, tmp_path):  # noqa: F811
    rt, mod = open_settings(lua, tmp_path)
    mod.key_press("c")
    mod.update(0.1)
    mod.key_press("c")
    mod.update(0.1)
    assert not mod.settings_open and "Hands@muted" in panel_texts(rt)


def test_toggle_turns_a_setting_on_and_off_and_saves_it(lua, tmp_path):  # noqa: F811
    rt, mod = open_settings(lua, tmp_path)
    toggle = "calculatro_setting_face_down"
    assert not mod.settings.face_down and find(rt, toggle).config.colour == "l_black"
    assert not (tmp_path / SETTINGS_FILE).exists()  # nothing saved until something changes

    assert click(lua, toggle)
    assert mod.settings.face_down
    assert panel_texts(rt)[-1] == "On@light" and find(rt, toggle).config.colour == "blue"
    assert json.loads((tmp_path / SETTINGS_FILE).read_text(encoding="utf-8")) == {"face_down": True}

    assert click(lua, toggle)
    assert not mod.settings.face_down and panel_texts(rt)[-1] == "Off@light"
    assert json.loads((tmp_path / SETTINGS_FILE).read_text(encoding="utf-8")) == {"face_down": False}


def test_setting_name_explains_it_when_hovered(lua, tmp_path):  # noqa: F811
    rt, mod = open_settings(lua, tmp_path)
    cell = find(rt, "calculatro_setting_name_face_down")
    assert cell.config.collideable
    rt.execute("UIElement = {hover = function(self) end}")
    mod.hover_popups(cell)
    cell.hover(cell)
    lines = [t.split("@")[0] for t in rt.execute(TEXTS)(cell.config.h_popup).values()]
    assert lines[0] == "Face-down windows" and len(lines) > 2
    assert all(line.isascii() for line in lines)  # the game's font has nothing else


def test_settings_are_read_as_the_mod_starts(lua, tmp_path):  # noqa: F811
    rt, mod = lua
    (tmp_path / SETTINGS_FILE).write_text('{"face_down": true}', encoding="utf-8")
    mod.update(1.0)
    assert mod.settings.face_down


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", '{"face_down": "yes"}', '{"other": true}'])
def test_unreadable_or_unknown_settings_keep_the_defaults(lua, tmp_path, content):  # noqa: F811
    rt, mod = lua
    (tmp_path / SETTINGS_FILE).write_text(content, encoding="utf-8")
    mod.update(1.0)
    assert mod.settings.face_down is False
    assert not (tmp_path / "calculatro.log").exists()  # not an error


def test_face_down_hint_needs_the_setting(lua):  # noqa: F811
    game = Game(lua)
    game.mod.settings.face_down = False
    down = game.card("8C", "back")
    game.deal([game.card("9S"), down, game.card("7H")], [game.card("8H"), game.card("2D")])
    game.hover(down)
    assert game.hint() is None
    # Turned on while hovering it: the hand's order was watched all along.
    game.mod.settings.face_down = True
    game.frame()
    assert game.hint() is not None
    game.mod.settings.face_down = False
    game.frame()
    assert game.hint() is None


# --- On the file thread -------------------------------------------------------

@pytest.fixture
def threaded(lua, request):  # noqa: F811
    if "luajit" not in request.node.name:
        pytest.skip("the thread replaces the state file through LuaJIT's FFI")
    lua[0].execute(MOCK_THREAD)
    return lua


def test_settings_are_saved_on_the_file_thread(threaded, tmp_path):
    rt, mod = threaded
    mod.write_snapshot()  # starts the thread
    filesystem = rt.eval("love.filesystem")
    real = filesystem.write
    filesystem.write = rt.eval("function() error('write on the game thread') end")
    mod.set_setting("face_down", True)
    filesystem.write = real
    assert not (tmp_path / SETTINGS_FILE).exists()  # only handed over

    rt.eval("RUN_THREAD")()
    mod.check_worker()
    assert json.loads((tmp_path / SETTINGS_FILE).read_text(encoding="utf-8")) == {"face_down": True}
    # The snapshot handed over with them was written too.
    assert mod.state_id is not None


def test_settings_that_cannot_be_saved_are_reported(threaded, tmp_path):
    rt, mod = threaded
    mod.write_snapshot()
    rt.eval("RUN_THREAD")()
    mod.check_worker()
    real = rt.eval("love.filesystem").write
    rt.eval("love.filesystem").write = rt.eval(
        "function(name, ...) if name == 'calculatro_settings.json' then return false, 'locked' end end")
    mod.set_setting("face_down", True)
    rt.eval("RUN_THREAD")()
    mod.check_worker()
    rt.eval("love.filesystem").write = real
    assert mod.settings.face_down  # still on for this session
    assert "could not save the settings: locked" in (tmp_path / "calculatro.log").read_text(encoding="utf-8")

"""The settings: the gear's page in the panel, the settings file, and what each setting turns on."""

import json

import pytest

from hand_driver import Game
from panel_driver import TEXTS, answer, click, find, panel_texts

pytest.importorskip("lupa")

SETTINGS_FILE = "calculatro_settings.json"


def open_settings(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    assert click(lua, "calculatro_settings")
    return rt, mod


def test_gear_opens_the_settings_in_place_of_the_odds_until_done(lua, tmp_path):
    with_memory(lua, 16 * GB)
    rt, mod = open_settings(lua, tmp_path)
    assert mod.settings_open and mod.editor is None
    assert panel_texts(rt) == ["CALCULATRO@light", "Done@light", "Settings@muted",
                               "Face-down hints@light", "Off@light",
                               "Memory limit@light", "<@light", "2 GB@light", ">@light"]
    assert rt.eval("BOXES[#BOXES].config.can_collide")
    # New odds and snapshots don't rebuild it under the cursor.
    boxes = rt.eval("#BOXES")
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(3.0)
    assert rt.eval("#BOXES") == boxes
    assert click(lua, "calculatro_done")
    assert not mod.settings_open and "Hands@muted" in panel_texts(rt)


def test_gear_works_with_nothing_tracked(lua):
    rt, mod = lua
    mod.update(1.0)
    assert panel_texts(rt)[2] == "[settings]"
    assert click(lua, "calculatro_settings")
    assert "Face-down hints@light" in panel_texts(rt)


def test_settings_close_with_the_panel(lua, tmp_path):
    rt, mod = open_settings(lua, tmp_path)
    mod.key_press("c")
    mod.update(0.1)
    mod.key_press("c")
    mod.update(0.1)
    assert not mod.settings_open and "Hands@muted" in panel_texts(rt)


def test_toggle_turns_a_setting_on_and_off_and_saves_it(lua, tmp_path):
    rt, mod = open_settings(lua, tmp_path)
    toggle = "calculatro_setting_face_down"
    assert not mod.settings.face_down and find(rt, toggle).config.colour == "l_black"
    assert not (tmp_path / SETTINGS_FILE).exists()  # nothing saved until something changes

    assert click(lua, toggle)
    assert mod.settings.face_down
    assert panel_texts(rt)[4] == "On@light" and find(rt, toggle).config.colour == "blue"
    assert json.loads((tmp_path / SETTINGS_FILE).read_text(encoding="utf-8")) == {"face_down": True, "memory_gb": 2}

    assert click(lua, toggle)
    assert not mod.settings.face_down and panel_texts(rt)[4] == "Off@light"
    assert json.loads((tmp_path / SETTINGS_FILE).read_text(encoding="utf-8")) == {"face_down": False, "memory_gb": 2}


def test_setting_name_explains_it_when_hovered(lua, tmp_path):
    rt, mod = open_settings(lua, tmp_path)
    cell = find(rt, "calculatro_setting_name_face_down")
    assert cell.config.collideable
    rt.execute("UIElement = {hover = function(self) end}")
    mod.hover_popups(cell)
    cell.hover(cell)
    lines = [t.split("@")[0] for t in rt.execute(TEXTS)(cell.config.h_popup).values()]
    assert lines[0] == "Face-down hints" and len(lines) > 2
    assert all(line.isascii() for line in lines)  # the game's font has nothing else


def test_settings_are_read_as_the_mod_starts(lua, tmp_path):
    rt, mod = lua
    (tmp_path / SETTINGS_FILE).write_text('{"face_down": true}', encoding="utf-8")
    mod.update(1.0)
    assert mod.settings.face_down


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", '{"face_down": "yes"}', '{"other": true}'])
def test_unreadable_or_unknown_settings_keep_the_defaults(lua, tmp_path, content):
    rt, mod = lua
    (tmp_path / SETTINGS_FILE).write_text(content, encoding="utf-8")
    mod.update(1.0)
    assert mod.settings.face_down is False
    assert not (tmp_path / "calculatro.log").exists()  # not an error


# --- Memory -------------------------------------------------------------------

GB = 2**30


def with_memory(lua, installed: int | None):
    """The mod as if the computer had `installed` bytes of memory (None: it can't tell)."""
    rt, mod = lua
    mod.installed_memory = rt.eval(f"function() return {'nil' if installed is None else installed} end")
    return rt, mod


def choices(mod) -> list[int]:
    return list(mod.memory_choices().values())


@pytest.mark.parametrize(("installed", "offered"), [
    (64 * GB, [1, 2, 4, 8]), (16 * GB, [1, 2, 4, 8]), (15 * GB, [1, 2, 4]), (8 * GB, [1, 2, 4]),
    (4 * GB, [1, 2]), (2 * GB, [1]), (GB, [1]), (None, [1, 2]),
])
def test_memory_offers_up_to_half_the_computers(lua, installed, offered):
    _, mod = with_memory(lua, installed)
    assert choices(mod) == offered


@pytest.mark.parametrize(("saved", "applies"), [(2, 2), (8, 4), (4, 4), (3, 2), (0.5, 1), (-1, 1)])
def test_memory_applies_the_largest_choice_up_to_the_saved_one(lua, saved, applies):
    _, mod = with_memory(lua, 8 * GB)
    mod.settings.memory_gb = saved
    assert mod.memory_gb() == applies
    assert mod.settings.memory_gb == saved  # kept for a computer with more


def test_installed_memory_is_read_in_the_game(lua, request):
    _, mod = lua
    if "luajit" not in request.node.name:
        assert mod.installed_memory() is None  # no FFI: offers up to the default
        assert choices(mod) == [1, 2]
        return
    assert mod.installed_memory() >= GB
    assert mod.installed_memory() == mod.installed_memory()  # read once


def test_memory_cycle_steps_through_the_choices_and_saves(lua, tmp_path):
    with_memory(lua, 16 * GB)
    rt, mod = open_settings(lua, tmp_path)
    shown = lambda: panel_texts(rt)[7].split("@")[0]  # between the cycle's arrows
    assert shown() == "2 GB"
    assert click(lua, "calculatro_setting_memory_gb_next")
    assert mod.settings.memory_gb == 4 and shown() == "4 GB"
    assert json.loads((tmp_path / SETTINGS_FILE).read_text(encoding="utf-8")) == {"face_down": False, "memory_gb": 4}
    assert click(lua, "calculatro_setting_memory_gb_next")
    assert click(lua, "calculatro_setting_memory_gb_next")  # wraps around
    assert mod.settings.memory_gb == 1 and shown() == "1 GB"
    assert click(lua, "calculatro_setting_memory_gb_prev")
    assert mod.settings.memory_gb == 8 and shown() == "8 GB"


def test_memory_cycle_steps_from_what_it_shows(lua, tmp_path):
    """A saved 8 GB shows as 4 on a computer with 8, and steps on from there."""
    _, mod = with_memory(lua, 8 * GB)
    mod.settings.memory_gb = 8
    rt, mod = open_settings(lua, tmp_path)
    assert click(lua, "calculatro_setting_memory_gb_prev")
    assert mod.settings.memory_gb == 2


def test_memory_cycle_is_inert_with_one_choice(lua, tmp_path):
    with_memory(lua, 2 * GB)
    rt, mod = open_settings(lua, tmp_path)
    assert find(rt, "calculatro_setting_memory_gb_next").config.button is None
    assert not click(lua, "calculatro_setting_memory_gb_next")
    assert mod.settings.memory_gb == 2 and mod.memory_gb() == 1


def test_memory_explains_its_limit_when_hovered(lua, tmp_path):
    with_memory(lua, 16 * GB)
    rt, mod = open_settings(lua, tmp_path)
    cell = find(rt, "calculatro_setting_name_memory_gb")
    rt.execute("UIElement = {hover = function(self) end}")
    mod.hover_popups(cell)
    cell.hover(cell)
    lines = [t.split("@")[0] for t in rt.execute(TEXTS)(cell.config.h_popup).values()]
    assert lines[0] == "Memory limit" and "Up to half your memory (16 GB here)." in lines
    assert all(line.isascii() for line in lines)


def test_memory_setting_is_read_as_the_mod_starts(lua, tmp_path):
    rt, mod = lua
    (tmp_path / SETTINGS_FILE).write_text('{"memory_gb": 8}', encoding="utf-8")
    mod.update(1.0)
    assert mod.settings.memory_gb == 8


def test_face_down_hint_needs_the_setting(lua):
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
    assert json.loads((tmp_path / SETTINGS_FILE).read_text(encoding="utf-8")) == {"face_down": True, "memory_gb": 2}
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

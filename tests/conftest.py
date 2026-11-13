"""Fixtures shared by the test modules. Shared helpers that aren't fixtures are plain modules
beside this one: `mocked_game`, `panel_driver`, `hand_driver`, `poker`, `search_oracle` and `fakes`."""

from __future__ import annotations

import pytest

from calculatro import report
from hand_driver import Game
from mocked_game import MOCK_THREAD, new_lua


@pytest.fixture(params=["lupa", "lupa.luajit21"], ids=["lua", "luajit"])
def lua(request, tmp_path):
    """The mod running against a mocked game, in each Lua runtime: LuaJIT as in the game, and a newer Lua.
    Skipped without `lupa`."""
    return new_lua(request.param, tmp_path)


@pytest.fixture
def threaded(lua, request):
    """The mod with a mocked `love.thread`, whose thread is run by hand (`RUN_THREAD`)."""
    if "luajit" not in request.node.name:
        pytest.skip("the thread replaces the state file through LuaJIT's FFI")
    lua[0].execute(MOCK_THREAD)
    return lua


@pytest.fixture
def game(lua):
    """A hand moved frame by frame."""
    return Game(lua)


@pytest.fixture
def no_estimates(monkeypatch) -> None:
    """build_report gets no estimates unless a test gives it some (`estimating`): with the engine stubbed, the real
    ones would estimate odds other than the stub's."""
    monkeypatch.setattr(report, "estimate_discard", lambda *args, **kw: None)

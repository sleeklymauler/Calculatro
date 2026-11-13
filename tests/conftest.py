"""Fixtures shared by the test modules. Shared helpers that aren't fixtures are plain modules
beside this one: `mocked_game`, `panel_driver`, `hand_driver`, `poker`, `search_oracle` and `fakes`."""

from __future__ import annotations

import pytest

from calculatro import engine, report, search
from fakes import FakeClock
from hand_driver import Game
from mocked_game import MOCK_THREAD, new_lua


@pytest.hookimpl(tryfirst=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    """No workers for `-n auto` when the perf tests are selected (`-m perf`): their timings are compared with a
    baseline measured on an otherwise idle machine. pyproject.toml's `-m 'not perf'` is the only expression
    without them."""
    markexpr = config.getoption("markexpr")
    return 0 if "perf" in markexpr and "not perf" not in markexpr else None


@pytest.fixture(params=["lupa", "lupa.luajit21"], ids=["lua", "luajit"])
def lua(request, tmp_path):
    """The mod running against a mocked game, in each Lua runtime: LuaJIT as in the game, and a newer Lua.
    Skipped without `lupa`."""
    return new_lua(request.param, tmp_path)


@pytest.fixture
def luajit(tmp_path):
    """The mod against a mocked game in LuaJIT only, for what the mod does through LuaJIT's FFI, which the newer Lua
    doesn't have: replacing and reading files with Windows' wide functions. Skipped without `lupa`."""
    return new_lua("lupa.luajit21", tmp_path)


@pytest.fixture
def threaded(luajit):
    """The mod with a mocked `love.thread`, whose thread is run by hand (`RUN_THREAD`). LuaJIT only: the thread
    replaces the state file through the FFI."""
    luajit[0].execute(MOCK_THREAD)
    return luajit


@pytest.fixture
def game(lua):
    """A hand moved frame by frame."""
    return Game(lua)


@pytest.fixture
def no_estimates(monkeypatch) -> None:
    """build_report gets no estimates unless a test gives it some (`estimating`): with the engine stubbed, the real
    ones would estimate odds other than the stub's."""
    monkeypatch.setattr(report, "estimate_discard", lambda *args, **kw: None)


@pytest.fixture
def fake_clock(monkeypatch) -> FakeClock:
    """The engine's and the search's clock, moving on 10 µs each time it's read (about a fast machine's time
    between reads). Seeded estimates stop at a deadline: by the real clock, a busy machine (a CI runner running
    the tests in parallel) draws fewer samples, and gets other ranges, which can miss the exact odds."""
    clock = FakeClock(1e-5)
    monkeypatch.setattr(engine, "monotonic", clock)
    monkeypatch.setattr(search, "monotonic", clock)
    return clock

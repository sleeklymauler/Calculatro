"""The mod's split into modules: what each must have for the game to load it.

The game only finds a module through lovely.toml, and only when something requires
it, so a mistake here would otherwise first show in game.
"""

import importlib
import re

import pytest

pytest.importorskip("lupa")

from mocked_game import MOD_DIR, load_mod, mod_sources

MODULES = sorted(mod_sources())


@pytest.fixture(params=["lupa", "lupa.luajit21"], ids=["lua", "luajit"])
def runtime(request):
    return importlib.import_module(request.param).LuaRuntime()


def test_every_lua_file_is_registered():
    registered = {name.removeprefix("calculatro.") for name in MODULES}
    # steamodded.lua is Steamodded's entry point, which only Steamodded runs.
    assert registered == {path.stem for path in MOD_DIR.glob("*.lua")} - {"steamodded"}


def test_every_module_has_the_mods_name():
    # Lovely's names are keys in package.preload, shared with every other mod.
    assert all(name == "calculatro" or name.startswith("calculatro.") for name in MODULES)


def test_the_mod_loads_every_module(runtime):
    load_mod(runtime)
    loaded = runtime.eval("package.loaded")
    assert [name for name in MODULES if loaded[name] is None] == []


@pytest.mark.parametrize("name", MODULES)
def test_module_loads_on_its_own(runtime, name):
    # With no game around: nothing may use it before the first frame's hooks, or
    # count on another module having been loaded first. A require loop fails here too.
    load_mod(runtime, name)


@pytest.mark.parametrize("name", MODULES)
def test_module_requires_what_it_calls(runtime, name):
    # The modules share one table, so a missing require would still work in game,
    # where they're all loaded by then. It would just no longer say what uses what.
    load_mod(runtime, name)
    shared = runtime.eval("require('calculatro.core')")
    code = re.sub(r"--.*", "", mod_sources()[name])
    called = set(re.findall(r"\bCalculatro\.(\w+)\(", code))
    assert sorted(f for f in called if shared[f] is None) == []

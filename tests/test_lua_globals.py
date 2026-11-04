"""Lints the mod's globals from its LuaJIT bytecode, without running it.

The mod shares the game's global table with Balatro and every other mod, and Lua
reads a misspelled name as nil and makes a missing `local` a global, silently.
So the mod must assign no globals, and read only LuaJIT's own and the game's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

luajit = pytest.importorskip("lupa.luajit21")

MOD = sorted((Path(__file__).resolve().parents[1] / "mod" / "Calculatro").glob("*.lua"))

# The game's globals the mod uses; add to this when it uses another.
GAME_GLOBALS = {"G", "love", "Sprite", "UIBox", "UIElement", "prep_draw"}

SCAN = r"""
local util, bit = require("jit.util"), require("bit")

-- Each instruction's opcode in a chunk's main function.
function opcodes(src)
  local f, out, pc = assert(loadstring(src)), {}, 1
  while util.funcbc(f, pc) do
    out[#out + 1] = bit.band(util.funcbc(f, pc), 0xff)
    pc = pc + 1
  end
  return out
end

-- {op, name, line} for each global read or assigned in the chunk, nested functions included.
function globals(src, chunkname, gget, gset)
  local found = {}
  local function walk(fn)
    local pc = 1
    while util.funcbc(fn, pc) do
      local ins = util.funcbc(fn, pc)
      local op = bit.band(ins, 0xff)
      if op == gget or op == gset then
        -- The name is the string constant numbered by operand D.
        local name = util.funck(fn, -bit.rshift(ins, 16) - 1)
        found[#found + 1] = {op == gset and "assigns" or "reads", name, util.funcinfo(fn, pc).currentline}
      end
      pc = pc + 1
    end
    for i = 1, util.funcinfo(fn).gcconsts do
      local k = util.funck(fn, -i)
      if type(k) == "proto" then walk(k) end
    end
  end
  walk(assert(loadstring(src, "@" .. chunkname)))
  return found
end
"""


@pytest.fixture(scope="module")
def scan():
    rt = luajit.LuaRuntime()
    rt.execute(SCAN)
    lua = rt.globals()
    # Opcode numbers vary between LuaJIT versions: find them from code that reads a global, and assigns one.
    gget = lua.opcodes("return y")[1]
    gset = lua.opcodes("x = 0")[2]
    builtins = {name for name, _ in rt.eval("_G").items()} - {"python"}  # lupa's own

    def globals_of(src: str, chunkname: str = "test") -> list[tuple[str, str, int]]:
        found = lua.globals(src, chunkname, gget, gset)
        return [tuple(found[i][j] for j in (1, 2, 3)) for i in range(1, len(found) + 1)]

    globals_of.allowed = builtins | GAME_GLOBALS
    return globals_of


def test_scan_finds_globals_in_nested_functions(scan):
    src = """local M = {}
function M.f(a)
  cuont = (count or 0) + 1
  return function() return tabel.concat(a) end
end
return M
"""
    assert sorted(scan(src)) == [("assigns", "cuont", 3), ("reads", "count", 3), ("reads", "tabel", 4)]


def test_mod_has_modules_to_scan():
    assert len(MOD) > 1


def test_mod_assigns_no_globals(scan):
    assigned = [f"{path.name} line {line}: {name}" for path in MOD
                for op, name, line in scan(path.read_text(encoding="utf-8"), path.name) if op == "assigns"]
    assert not assigned, "the mod assigns globals (missing `local`?):\n" + "\n".join(assigned)


def test_mod_reads_only_known_globals(scan):
    # LuaJIT's own globals are Lua 5.1's (plus bit, jit), so names new in Lua 5.2+ (utf8, ...) fail here too.
    unknown = [f"{path.name} line {line}: {name}" for path in MOD
               for op, name, line in scan(path.read_text(encoding="utf-8"), path.name)
               if op == "reads" and name not in scan.allowed]
    assert not unknown, "the mod reads unknown globals (a typo, or add it to GAME_GLOBALS):\n" + "\n".join(unknown)

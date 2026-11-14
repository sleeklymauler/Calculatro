"""The real mod, loaded as Lovely loads it, into a Lua runtime with a mocked Balatro `G`,
`love.filesystem` and `love.thread`.

`new_lua` and the `lua` fixture (conftest.py) skip the test when `lupa` isn't installed,
so a test module that also has pure-Python tests needn't skip as a whole.
"""

import functools
import json
import tomllib
from pathlib import Path

import pytest

from calculatro.paths import data_dir

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
    ability = {wheel_flipped = opts.wheel_flipped},
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
  discard = {cards = {card('3', 3, 'Clubs', {facing = 'back'})}},  -- the game turns every discard over
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


# ... or create one.
def create_directory(save_dir):
    def create(name):
        try:
            (save_dir / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return True
    return create


# The game runs LuaJIT (Lua 5.1) and lupa's default runtime is a newer Lua, so the
# mod's tests run in both: a 5.2+ feature fails in the LuaJIT run.
def new_lua(runtime: str, save_dir: Path):
    """A Lua runtime of the module `runtime` with a mocked game and the mod loaded, and the
    data folder the mod creates as it starts (Calculatro.create_data_dir) already there."""
    rt = pytest.importorskip(runtime).LuaRuntime()  # lupa is a dev dependency
    data_dir(save_dir).mkdir(parents=True, exist_ok=True)
    rt.execute(MOCK_G)
    rt.execute(MOCK_FILESYSTEM, str(save_dir).replace("\\", "/"))
    rt.eval("love.filesystem").getDirectoryItems = directory_items(rt, save_dir)
    rt.eval("love.filesystem").createDirectory = create_directory(save_dir)
    mod = load_mod(rt)
    if rt.eval("jit") is None:
        mod.replace_file = rt.execute(REPLACE_WITHOUT_FFI)
    mod.launch = rt.eval("function() return false end")  # no test should start the app by accident
    return rt, mod


def snapshot_json(lua) -> dict:
    rt, mod = lua
    return json.loads(rt.eval("require('calculatro.json')").encode(mod.snapshot()))


# love.thread, with the "thread" run by hand and in the same Lua state: RUN_THREAD does
# what's been asked of it, up to the point it would wait for more (a coroutine's yield).
MOCK_THREAD = r"""
local channels = {}
local function channel(name)
  channels[name] = channels[name] or {
    items = {},
    getCount = function(self) return #self.items end,
    push = function(self, item) self.items[#self.items + 1] = item end,
    pop = function(self) return table.remove(self.items, 1) end,
    peek = function(self) return self.items[1] end,
    clear = function(self) self.items = {} end,
    demand = function(self)
      if #self.items == 0 then coroutine.yield() end
      return table.remove(self.items, 1)
    end,
  }
  return channels[name]
end
THREAD_RUNNING = true
THREADS = {}
love.thread = {
  getChannel = channel,
  newThread = function(code)
    local thread = {
      code = code,
      start = function(self, ...) self.args = {...} end,
      isRunning = function() return THREAD_RUNNING end,
    }
    THREADS[#THREADS + 1] = thread
    return thread
  end,
}
for _, name in ipairs({'love.thread', 'love.filesystem'}) do
  package.preload[name] = function() return love end
end
function RUN_THREAD(n)
  local thread = THREADS[n or 1]
  thread.co = thread.co or coroutine.create(assert(load(thread.code)))
  assert(coroutine.resume(thread.co, unpack(thread.args)))
end
"""

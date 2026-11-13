"""Reading the mod's panel, clicking its buttons, and answering it as the app would."""

import json
import re

import calculatro.__main__ as cli
from calculatro import overlay
from calculatro.cards import CardQuery, parse_query
from calculatro.hands import get_target
from calculatro.state import SUIT_SHORT, GameState

TEXTS = r"""
return function(node)  -- text nodes of a UI definition, in order, as 'text@colour', icons as '[atlas x,y]',
  local out = {}       -- the arrow drawn in boxes as '->@colour', and other drawn icons as '[name]'
  local function walk(n)
    if n.config and n.config.glyph then out[#out + 1] = '[' .. n.config.glyph .. ']'; return end
    if n.n == G.UIT.T and n.config.text ~= '' then  -- the title row's empty message slot shows nothing
      out[#out + 1] = n.config.text .. '@' .. tostring(n.config.colour)
    end
    if n.n == G.UIT.R and n.nodes[1] and n.nodes[1].n == G.UIT.B then
      out[#out + 1] = '->@' .. tostring(n.nodes[1].config.colour)
    end
    if n.n == G.UIT.O then
      local s = n.config.object
      out[#out + 1] = string.format('[%s %d,%d]', s.atlas, s.pos.x, s.pos.y)
    end
    for _, child in ipairs(n.nodes or {}) do walk(child) end
  end
  walk(node)
  return out
end
"""


def read_state(tmp_path) -> GameState:
    return GameState.from_json(json.loads((tmp_path / "calculatro_state.json").read_text(encoding="utf-8")))


def panel_texts(rt) -> list[str] | None:
    """Text of the live panel, or None if there is none."""
    box = rt.eval("BOXES[#BOXES]")
    if box is None or box.REMOVED:
        return None
    return list(rt.execute(TEXTS)(box.definition).values())


_SUIT_SYMBOLS = {suit[:-1]: symbol for suit, symbol in SUIT_SHORT.items()}


def label_for(query: CardQuery | str) -> str:
    """How the panel names a card query: "Queen: 2+", "♠: 0-1", "Q♠: 1+", "Face card: none"."""
    q = parse_query(query) if isinstance(query, str) else query
    name = re.sub(r"\b(" + "|".join(_SUIT_SYMBOLS) + r")\b", lambda m: _SUIT_SYMBOLS[m[1]], q.filter.name)
    if q.count == 0 and q.op in ("==", "<="):
        count = "none"
    elif q.op == "between":
        count = f"{q.count}-{q.high}"
    else:
        count = {">=": f"{q.count}+", "<=": f"0-{q.count}", "==": str(q.count)}[q.op]
    return f"{name}: {count}"


def track(lua, targets=(), queries=()):
    """Set what the run tracks, as the editor would."""
    rt, _ = lua
    rt.execute("G.GAME.calculatro = {hands = {}, cards = {}}")
    tracked = rt.eval("G.GAME.calculatro")
    for i, t in enumerate(targets, 1):
        tracked.hands[i] = get_target(t).name
    for i, q in enumerate(queries, 1):
        tracked.cards[i] = rt.table_from({"query": q, "label": label_for(q)})


def tracked(rt) -> tuple[list[str], list[str]]:
    t = rt.eval("G.GAME.calculatro")
    return list(t.hands.values()), [c.query for c in t.cards.values()]


def answer(lua, tmp_path, targets=("trips", "pair"), queries=("at most 1 nine",)):
    """Track these, then do what the app does: read the mod's state, write its odds."""
    _, mod = lua
    track(lua, targets, queries)
    mod.write_snapshot()
    reply(tmp_path)


def reply(tmp_path):
    """The app's odds for the mod's latest state."""
    state = read_state(tmp_path)
    _, payload = cli.calculate(state, *cli.tracked(state))
    assert overlay.write(tmp_path / "calculatro_odds.json", payload)


FIND = r"""
return function(node, id)  -- the node with this id
  local function walk(n)
    if n.config and n.config.id == id then return n end
    for _, c in ipairs(n.nodes or {}) do local found = walk(c); if found then return found end end
  end
  return walk(node)
end
"""


def find(rt, id: str):
    return rt.execute(FIND)(rt.eval("BOXES[#BOXES]").definition, id)


PATH = r"""
return function(node, id)  -- child indexes down to the node with this id
  local function walk(n, path)
    if n.config and n.config.id == id then return path end
    for i, c in ipairs(n.nodes or {}) do
      local found = walk(c, path .. '/' .. i); if found then return found end
    end
  end
  return walk(node, '')
end
"""


def path(rt, id: str) -> str | None:
    """Where a node of the live panel sits in its definition, as child indexes."""
    return rt.execute(PATH)(rt.eval("BOXES[#BOXES]").definition, id)


def click(lua, id: str, dt: float = 0.01) -> bool:
    """Click a button of the live panel, as the game would; False if it's inert."""
    rt, mod = lua
    node = find(rt, id)
    assert node is not None, id
    if not node.config.button:
        return False
    rt.eval("G.FUNCS")[node.config.button](node)
    mod.update(dt)
    return True

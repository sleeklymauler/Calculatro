"""The odds panel's motion: a rebuilt panel moves from where the old one was drawn, new rows pop in, and its
buttons don't shake the screen.

The mocked UIBox has no layout, so these run the animation on hand-built element trees: each
element has a place (T), what's drawn (VT) and its children, like the game's UIElements.
"""

import pytest

pytest.importorskip("lupa")

HELPERS = r"""
DRAWN = {}  -- what each draw saw: DRAWN[n][key] = {x, y, w, h, scale}
UIBox = setmetatable({
  draw = function(panel)
    local seen = {}
    local function walk(e)
      local key = e.config.calculatro_anim or e.config.name
      if key then seen[key] = {x = e.VT.x, y = e.VT.y, w = e.VT.w, h = e.VT.h, scale = e.VT.scale} end
      if e.config.object then
        local o = e.config.object.VT
        seen[key .. ':object'] = {x = o.x, y = o.y, w = o.w, h = o.h, scale = o.scale}
      end
      for _, c in ipairs(e.children) do walk(c) end
    end
    walk(panel.UIRoot)
    DRAWN[#DRAWN + 1] = seen
  end,
}, {__call = function() error('not built here') end})

-- An element at (x, y), w by h: keyed if `key` starts with a letter, else just named (untagged).
function el(key, x, y, w, h, children, object)
  local config = {object = object}
  if key:match('^%a') then config.calculatro_anim = key else config.name = key end
  return {UIT = 'C', config = config, T = {x = x, y = y, w = w, h = h},
          VT = {x = x, y = y, w = w, h = h, scale = 1}, children = children or {}}
end

MAJOR = {T = {x = 10, y = 20}}
-- A panel around `root`. Like the game's, each element is placed by its offset in the box (role.offset).
function panel(root)
  local function offsets(e)
    e.role = {offset = {x = e.T.x - root.T.x, y = e.T.y - root.T.y}}
    for _, c in ipairs(e.children) do offsets(c) end
  end
  offsets(root)
  return {UIRoot = root, T = {x = root.T.x, y = root.T.y, w = root.T.w, h = root.T.h}, role = {major = MAJOR}}
end

-- Moves a panel's box by dx after it's built, as build_panel does to line it up with the deck: its
-- elements' T and VT only catch up a frame later.
function shift_box(p, dx)
  p.T.x = p.T.x + dx
end
function catch_up(p)
  local function walk(e)
    e.T.x, e.T.y = p.T.x + e.role.offset.x, p.T.y + e.role.offset.y
    e.VT.x, e.VT.y = e.T.x, e.T.y
    for _, c in ipairs(e.children) do walk(c) end
  end
  walk(p.UIRoot)
end
"""


@pytest.fixture
def anim(lua):
    rt, mod = lua
    rt.execute(HELPERS)
    mod.clock = 100
    return rt, mod


def draw(rt, mod, panel, at: float) -> dict:
    mod.clock = at
    panel.draw(panel)
    seen = rt.eval("DRAWN[#DRAWN]")
    return {k: dict(v) for k, v in seen.items()}


def small(rt):
    """A panel 3 wide with one row, its bottom-right corner at (13, 20)."""
    return rt.eval("""panel(el('frame', 10, 18, 3, 2, {
        el('hand:Pair', 10.1, 18.5, 2.8, 0.3, {el('_value', 12, 18.5, 0.5, 0.3)}),
    }))""")


def big(rt):
    """The same panel, 6 wide with a second row: it grows up and left."""
    return rt.eval("""panel(el('frame', 7, 17.7, 6, 2.3, {
        el('hand:Pair', 7.1, 18.2, 2.8, 0.3, {el('_value', 9, 18.2, 0.5, 0.3)}),
        el('hand:Flush', 7.1, 18.5, 2.8, 0.3, {el('_flush_value', 9, 18.5, 0.5, 0.3)}),
    }))""")


def approx(place: dict, x, y, w=None, h=None):
    assert place["x"] == pytest.approx(x) and place["y"] == pytest.approx(y)
    if w is not None:
        assert place["w"] == pytest.approx(w) and place["h"] == pytest.approx(h)


def test_appearing_panel_pops_in(anim):
    rt, mod = anim
    p = small(rt)
    mod.animate(p, None)
    first = draw(rt, mod, p, 100)
    assert first["frame"]["scale"] == 0 and first["hand:Pair"]["scale"] == 0 and first["_value"]["scale"] == 0
    middle = draw(rt, mod, p, 100.1)
    assert 0 < middle["frame"]["scale"] < 1.2
    # Everything shrinks towards the frame's middle.
    centre = 10 + 3 / 2
    s = middle["frame"]["scale"]
    assert middle["_value"]["x"] + 0.25 == pytest.approx(centre + (12.25 - centre) * s)
    done = draw(rt, mod, p, 101)
    assert done["frame"] == {"x": 10, "y": 18, "w": 3, "h": 2, "scale": 1}
    assert p.calculatro_anim is None


def test_drawing_leaves_the_layout_alone(anim):
    rt, mod = anim
    p = small(rt)
    mod.animate(p, None)
    draw(rt, mod, p, 100.05)
    assert dict(p.UIRoot.VT) == {"x": 10, "y": 18, "w": 3, "h": 2, "scale": 1}
    assert dict(p.UIRoot.children[1].children[1].VT)["scale"] == 1


def test_growing_panel_moves_from_the_old_one(anim):
    rt, mod = anim
    old = small(rt)
    from_ = mod.drawn_layout(old, 100)
    new = big(rt)
    mod.animate(new, from_)

    start = draw(rt, mod, new, 100)
    approx(start["frame"], 10, 18, 3, 2)          # where the old frame was
    approx(start["hand:Pair"], 10.1, 18.5)        # and its row
    approx(start["_value"], 12, 18.5)             # untagged elements go with their row
    assert start["hand:Flush"]["scale"] == 0      # the new row waits for the frame

    middle = draw(rt, mod, new, 100.1)
    assert 7 < middle["frame"]["x"] < 10 and 3 < middle["frame"]["w"] < 6
    # Its bottom-right corner stays put.
    assert middle["frame"]["x"] + middle["frame"]["w"] == pytest.approx(13)
    assert middle["frame"]["y"] + middle["frame"]["h"] == pytest.approx(20)

    end = draw(rt, mod, new, 101)
    approx(end["frame"], 7, 17.7, 6, 2.3)
    approx(end["hand:Pair"], 7.1, 18.2)
    assert end["hand:Flush"]["scale"] == 1
    assert new.calculatro_anim is None


def test_new_rows_pop_one_after_another(anim):
    rt, mod = anim
    old = rt.eval("panel(el('frame', 10, 18, 3, 2))")
    new = rt.eval("""panel(el('frame', 10, 18, 3, 2, {
        el('card:a', 10, 18, 3, 0.3), el('card:b', 10, 18.3, 3, 0.3),
    }))""")
    mod.animate(new, mod.drawn_layout(old, 100))
    tracks = new.calculatro_anim.tracks
    # Nothing moved, so they don't wait.
    assert tracks["card:a"].pop == 100
    assert tracks["card:b"].pop > tracks["card:a"].pop


def test_rows_inside_a_new_row_pop_with_it(anim):
    rt, mod = anim
    old = rt.eval("panel(el('frame', 10, 18, 3, 2))")
    new = rt.eval("""panel(el('frame', 10, 18, 3, 2, {
        el('inset', 10, 18, 3, 1, {el('card:a', 10, 18, 3, 0.3)}),
    }))""")
    mod.animate(new, mod.drawn_layout(old, 100))
    tracks = new.calculatro_anim.tracks
    assert tracks["inset"].pop is not None and tracks["card:a"].pop is None
    seen = draw(rt, mod, new, 100.1)
    assert seen["card:a"]["scale"] == pytest.approx(seen["inset"]["scale"])


def test_objects_move_with_their_element(anim):
    rt, mod = anim
    old = rt.eval("panel(el('frame', 10, 18, 3, 2, {el('card:a', 10, 18, 3, 0.3)}))")
    new = rt.eval("""panel(el('frame', 10, 18, 3, 2, {el('card:a', 10, 18.5, 3, 0.3, {
        el('_icon', 11, 18.5, 0.2, 0.2, nil, {VT = {x = 11, y = 18.5, w = 0.2, h = 0.2, scale = 1}}),
    })}))""")
    mod.animate(new, mod.drawn_layout(old, 100))
    seen = draw(rt, mod, new, 100)
    approx(seen["_icon:object"], 11, 18)


def test_rebuilding_mid_move_carries_on(anim):
    rt, mod = anim
    old = small(rt)
    new = big(rt)
    mod.animate(new, mod.drawn_layout(old, 100))

    # The odds change: the same layout, built again. The move goes on as it was.
    again = big(rt)
    mod.animate(again, mod.drawn_layout(new, 100.1))
    assert again.calculatro_anim.tracks["frame"].move.start == 100
    assert draw(rt, mod, again, 100.15)["frame"] == draw(rt, mod, new, 100.15)["frame"]

    # Back to the small layout: it turns round from where it's drawn, without a jump.
    before = draw(rt, mod, again, 100.15)["frame"]
    back = small(rt)
    mod.animate(back, mod.drawn_layout(again, 100.15))
    assert draw(rt, mod, back, 100.15)["frame"] == pytest.approx(before)
    approx(draw(rt, mod, back, 101)["frame"], 10, 18, 3, 2)


def test_unchanged_panel_does_not_animate(anim):
    rt, mod = anim
    old = small(rt)
    new = small(rt)
    mod.animate(new, mod.drawn_layout(old, 100))
    seen = draw(rt, mod, new, 100)
    approx(seen["frame"], 10, 18, 3, 2)
    assert all(v["scale"] == 1 for v in seen.values())


def test_box_moved_after_building_does_not_animate(anim):
    """build_panel lines the box up with the deck after building it, before its elements catch up.
    Rebuilding the same layout (new odds, a toggled hand) must not move anything."""
    rt, mod = anim
    old = small(rt)
    new = rt.eval("""panel(el('frame', 9, 18, 3, 2, {
        el('hand:Pair', 9.1, 18.5, 2.8, 0.3, {el('_value', 11, 18.5, 0.5, 0.3)}),
    }))""")
    rt.globals().shift_box(new, 1)
    mod.animate(new, mod.drawn_layout(old, 100))
    assert new.calculatro_anim.tracks["frame"].move is None
    assert new.calculatro_anim.tracks["hand:Pair"].move is None
    rt.globals().catch_up(new)
    seen = draw(rt, mod, new, 100)
    approx(seen["frame"], 10, 18, 3, 2)
    approx(seen["hand:Pair"], 10.1, 18.5)


def test_reduced_motion(anim):
    rt, mod = anim
    rt.execute("G.SETTINGS.reduced_motion = true")
    p = small(rt)
    mod.animate(p, None)
    assert p.calculatro_anim is None and p.draw is None


def test_mock_panel_is_left_alone(lua):
    """The tests' mocked UIBox has no elements to animate."""
    rt, mod = lua
    mod.update(1.0)
    box = rt.eval("BOXES[#BOXES]")
    assert box is not None and box.draw is None


def test_panel_buttons_do_not_shake_the_screen(anim):
    rt, mod = anim
    rt.execute("""
      G.ROOM = {jiggle = 0.2}
      CLICKED = 0
      UIElement = {click = function(self) CLICKED = CLICKED + 1; G.ROOM.jiggle = G.ROOM.jiggle + 0.5 end}
    """)
    p = rt.eval("""panel(el('frame', 10, 18, 3, 2, {el('_plus', 10, 18, 0.3, 0.3), el('_other', 11, 18, 0.3, 0.3)}))""")
    plus, other = p.UIRoot.children[1], p.UIRoot.children[2]
    plus.config.button = "calculatro_button"
    other.config.button = "some_game_button"
    mod.steady_buttons(p.UIRoot)
    plus.click(plus)
    assert rt.eval("CLICKED") == 1 and rt.eval("G.ROOM.jiggle") == 0.2
    assert other.click is None  # only the panel's own buttons

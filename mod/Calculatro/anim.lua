local Calculatro = require('calculatro.core')
local ui_children = require('calculatro.uibox').ui_children

---------------------------------------------------------------------------
-- Panel animation. The panel is rebuilt whenever what it shows changes, and
-- a new box would just jump to its new layout. Instead, elements tagged
-- `calculatro_anim` with the same key in the old and new box move from where
-- the old one drew them: the frame, its body and the inset morph to their
-- new size (the panel stays anchored by its bottom-right corner, so it grows
-- up and left), and rows glide to their new places. Keyed elements the old
-- box didn't have pop in once the frame has mostly moved, a little apart
-- (POP_STAGGER); untagged elements go with the nearest tagged one around
-- them. A panel that appears pops in whole. Nothing leaves: removed rows
-- vanish, and the rest move into the gap.
--
-- It all happens while drawing, on the visible transforms (VT) the game
-- draws from, which are put back straight after: layout, collision and
-- buttons only ever see the final places. Off with the game's Reduced
-- Motion setting.
---------------------------------------------------------------------------
local MORPH_TIME, POP_DELAY, POP_TIME, POP_STAGGER = 0.25, 0.12, 0.22, 0.025
local MAX_STAGGER = 8  -- pops after this many start together

local function clamp01(t) return math.max(0, math.min(1, t)) end
local function ease_out(t) return 1 - (1 - t) ^ 3 end
-- Overshoots a little before settling, like the game's own pops.
local function ease_out_back(t)
    local c = 1.70158
    return 1 + (c + 1) * (t - 1) ^ 3 + c * (t - 1) ^ 2
end

-- How much of a move is left (1 at its start, 0 when done), and a pop's
-- scale (0 before it starts).
function Calculatro.move_left(move, now)
    return 1 - ease_out(clamp01((now - move.start) / MORPH_TIME))
end

function Calculatro.pop_scale(start, now)
    local t = (now - start) / POP_TIME
    if t <= 0 then return 0 elseif t >= 1 then return 1 end
    return ease_out_back(t)
end

-- Keyed elements, outermost first, each with the nearest keyed element
-- around it (`parent`).
local function keyed(root)
    local list = {}
    local function walk(e, parent)
        local key = e.config and e.config.calculatro_anim
        local entry = parent
        if key then
            entry = { key = key, e = e, parent = parent }
            list[#list + 1] = entry
        end
        for _, child in ipairs(ui_children(e)) do walk(child, entry) end
    end
    walk(root, nil)
    return list
end

-- An element's place relative to the panel's major (the deck), from the
-- box's place and the element's offset in it (role.offset): its own T only
-- catches up with the box a frame later, and build_panel moves the box
-- after building it.
local function relative(e, panel)
    local major, offset = panel.role.major, e.role.offset
    return { x = panel.T.x + offset.x - major.T.x, y = panel.T.y + offset.y - major.T.y, w = e.T.w, h = e.T.h }
end

local FIELDS = { 'x', 'y', 'w', 'h' }

-- Where a panel draws each keyed element right now, so a panel rebuilt
-- mid-animation carries on from there, with the moves and pops still under
-- way: the odds rebuild the panel several times a second, and a move whose
-- element ends up in the same place again goes on as it was.
function Calculatro.drawn_layout(panel, now)
    local anim = panel.calculatro_anim or { tracks = {} }
    local out = {}
    for _, k in ipairs(keyed(panel.UIRoot)) do
        local place = relative(k.e, panel)
        local track = anim.tracks[k.key] or {}
        if track.move and now < track.move.start + MORPH_TIME then
            place.final, place.move = relative(k.e, panel), track.move
            local left = Calculatro.move_left(track.move, now)
            for _, f in ipairs(FIELDS) do place[f] = place[f] + track.move[f] * left end
        end
        if track.pop and now < track.pop + POP_TIME then place.pop = track.pop end
        out[k.key] = place
    end
    return out
end

-- Plans the new panel's animation from the old one's drawn layout (nil: the
-- panel is appearing): tracks keyed by element key, each with a move (what
-- the place still differs by, from `start`) and/or a pop (its start time).
function Calculatro.plan(list, from, panel, now)
    local tracks, moving, pops = {}, false, {}
    if not from then
        if list[1] then tracks[list[1].key] = { pop = now } end
        return tracks
    end
    for _, k in ipairs(list) do
        local old, place = from[k.key], relative(k.e, panel)
        if old then
            local move, same, moved = { start = now }, old.final ~= nil, false
            for _, f in ipairs(FIELDS) do
                move[f] = old[f] - place[f]
                moved = moved or math.abs(move[f]) > 0.001
                same = same and math.abs(old.final[f] - place[f]) <= 0.001
            end
            if same then move = old.move elseif not moved then move = nil end
            moving = moving or move ~= nil
            tracks[k.key] = { move = move, pop = old.pop }
        else
            tracks[k.key] = { new = true }
            -- A new element inside a new element pops with it.
            if not (k.parent and tracks[k.parent.key].new) then pops[#pops + 1] = tracks[k.key] end
        end
    end
    local delay = moving and POP_DELAY or 0
    for i, track in ipairs(pops) do
        track.pop = now + delay + POP_STAGGER * math.min(i - 1, MAX_STAGGER)
    end
    return tracks
end

-- The transform each element is drawn with: moved by `dx, dy` and grown by
-- `dw, dh` (keyed elements' own moves; others take their nearest keyed
-- element's move), and scaled by `s` around (cx, cy), the centre of the
-- popping element around it.
local function apply(e, anim, now, saved, inherited)
    local key = e.config and e.config.calculatro_anim
    local tr = inherited
    local track = key and anim.tracks[key]
    if key then
        tr = { dx = 0, dy = 0, dw = 0, dh = 0, s = inherited.s, cx = inherited.cx, cy = inherited.cy }
        if track and track.move then
            local left = Calculatro.move_left(track.move, now)
            tr.dx, tr.dy = track.move.x * left, track.move.y * left
            tr.dw, tr.dh = track.move.w * left, track.move.h * left
        elseif not track then
            tr.dx, tr.dy = inherited.dx, inherited.dy
        end
        if track and track.pop then
            local s = Calculatro.pop_scale(track.pop, now)
            if s < 1 then
                tr.s = s * inherited.s
                tr.cx = e.VT.x + tr.dx + (e.VT.w + tr.dw) / 2
                tr.cy = e.VT.y + tr.dy + (e.VT.h + tr.dh) / 2
            end
        end
    end
    local function shift(vt)
        saved[#saved + 1] = { vt, vt.x, vt.y, vt.w, vt.h, vt.scale }
        vt.x, vt.y = vt.x + tr.dx, vt.y + tr.dy
        if key then vt.w, vt.h = vt.w + tr.dw, vt.h + tr.dh end
        if tr.s ~= 1 then
            vt.x = tr.cx + (vt.x + vt.w / 2 - tr.cx) * tr.s - vt.w / 2
            vt.y = tr.cy + (vt.y + vt.h / 2 - tr.cy) * tr.s - vt.h / 2
            vt.scale = (vt.scale or 1) * tr.s
        end
    end
    shift(e.VT)
    local object = e.config and e.config.object
    if object and object.VT then shift(object.VT) end
    for _, child in ipairs(ui_children(e)) do apply(child, anim, now, saved, tr) end
end

local function animation_done(anim, now)
    for _, track in pairs(anim.tracks) do
        if track.move and now < track.move.start + MORPH_TIME then return false end
        if track.pop and now < track.pop + POP_TIME then return false end
    end
    return true
end

local function draw_animated(panel)
    local anim = panel.calculatro_anim
    local now = Calculatro.clock
    if not anim or animation_done(anim, now) then
        panel.calculatro_anim = nil
        return UIBox.draw(panel)
    end
    local saved = {}
    apply(panel.UIRoot, anim, now, saved, { dx = 0, dy = 0, dw = 0, dh = 0, s = 1 })
    local ok, err = pcall(UIBox.draw, panel)
    for i = #saved, 1, -1 do
        local s = saved[i]
        s[1].x, s[1].y, s[1].w, s[1].h, s[1].scale = s[2], s[3], s[4], s[5], s[6]
    end
    if not ok then error(err, 0) end
end

-- Animates a freshly built panel from `from` (Calculatro.drawn_layout of
-- the one it replaces; nil when it's appearing).
function Calculatro.animate(panel, from)
    if not panel.UIRoot or (G.SETTINGS and G.SETTINGS.reduced_motion) then return end
    local now = Calculatro.clock
    local tracks = Calculatro.plan(keyed(panel.UIRoot), from, panel, now)
    if next(tracks) == nil then return end
    panel.calculatro_anim = { tracks = tracks }
    panel.draw = draw_animated
end

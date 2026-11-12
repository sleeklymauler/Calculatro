---------------------------------------------------------------------------
-- What the mod does to a box once it's built, and how it removes it: its own
-- shadows, struck-out text, steady buttons and hover popups, and keeping the
-- game's lists of objects from growing with every rebuild.
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')
require('calculatro.widgets')

-- Deeper shadows than the game's. It drops text shadows 0.5 parallax units
-- away, which barely shows on the dark inset, and boxes' (the arrow) 1.5,
-- which is a lot for something this small. The panel draws both itself, at
-- SHADOW_DEPTH, in place of the game's.
local SHADOW_DEPTH = 1.0

local function draw_shadow(e)
    local c = e.config
    local dx = -e.shadow_parrallax.x * SHADOW_DEPTH / G.TILESIZE
    local dy = -e.shadow_parrallax.y * SHADOW_DEPTH / G.TILESIZE
    prep_draw(e, 1)
    love.graphics.setColor(0, 0, 0, 0.3 * c.colour[4])
    if e.UIT == G.UIT.T then
        local font = c.lang.font
        local k = c.scale * font.FONTSCALE / G.TILESIZE
        love.graphics.draw(c.text_drawable, font.TEXT_OFFSET.x * k + dx, font.TEXT_OFFSET.y * k + dy, 0, k * font.squish, k)
    else
        love.graphics.rectangle('fill', dx, dy, e.VT.w, e.VT.h)
    end
    love.graphics.pop()
end

local function deepen_shadows(e)
    if (e.UIT == G.UIT.T or e.UIT == G.UIT.B) and e.config.shadow then
        e.config.shadow = false
        local draw_self = e.draw_self
        function e:draw_self()
            local c = self.config
            if self.states.visible and c.colour and G.SETTINGS.GRAPHICS.shadows == 'On'
                    and (self.UIT == G.UIT.B or c.text_drawable) then
                draw_shadow(self)
            end
            draw_self(self)
        end
    end
    for _, child in pairs(e.children or {}) do
        if type(child) == 'table' and child.UIT then deepen_shadows(child) end
    end
end

-- A line through text (config.calculatro_strike), for hands the boss won't
-- let score: one font pixel thick, through the middle of m6x11's lowercase
-- letters (its baseline is 12 of the em's 16 pixels down, x-height 6, and the
-- game draws text TEXT_OFFSET.y above its box), with a shadow like the text's.
local function strike_through(e)
    if e.UIT == G.UIT.T and e.config.calculatro_strike then
        local draw_self = e.draw_self
        function e:draw_self()
            local c = self.config
            local shown = self.states.visible and c.colour and c.text_drawable
            local font = shown and c.lang.font
            local k = shown and c.scale * font.FONTSCALE / G.TILESIZE
            local em = shown and font.FONT:getHeight()
            local function line(colour, dx, dy)
                prep_draw(self, 1)
                love.graphics.setColor(colour)
                love.graphics.rectangle('fill', dx, (font.TEXT_OFFSET.y + em / 2) * k + dy, self.VT.w, em / 16 * k)
                love.graphics.pop()
            end
            if shown and G.SETTINGS.GRAPHICS.shadows == 'On' then
                line({ 0, 0, 0, 0.3 * c.colour[4] }, -self.shadow_parrallax.x * SHADOW_DEPTH / G.TILESIZE,
                     -self.shadow_parrallax.y * SHADOW_DEPTH / G.TILESIZE)
            end
            draw_self(self)
            if shown then line(c.colour, 0, 0) end
        end
    end
    for _, child in pairs(e.children or {}) do
        if type(child) == 'table' and child.UIT then strike_through(child) end
    end
end
Calculatro.strike_through = strike_through

-- The game's floating text (DynaText's `float`, as on the blind's name and
-- the hand being scored): the letters bob in a wave that runs left to right,
-- each 0.4 s behind the one before, every ~2.4 s. For text nodes with
-- config.calculatro_bounce set (the title row's
-- "Calculating..." and "Searching..."), checked as they're drawn, since
-- update_values turns it on and off in place. Off with Reduced Motion, like
-- the game's. Drawn here letter by letter rather than with a DynaText, which
-- the game keeps in its lists of objects until removed: definitions built
-- only for their values (without_sprites) would leak them.
local FLOAT_RATE = 2.666

-- How far letter `i` is lifted at `now`, in DynaText's units (see
-- draw_bouncing), for text at `scale` in a font of `fontscale`.
local function float(scale, fontscale, i, now)
    return math.sqrt(scale) * (2 + fontscale / G.TILESIZE * 2000 * math.sin(FLOAT_RATE * now + 200 * i))
end

-- A text node's letters, each as a drawable and its x in font pixels,
-- made again only when its text changes.
local function letters(e)
    local c = e.config
    local text, font = tostring(c.text), c.lang.font
    local cache = e.calculatro_letters
    if cache and cache.text == text and cache.font == font then return cache end
    cache = { text = text, font = font }
    for i = 1, #text do  -- panel text is ASCII
        cache[i] = { drawable = love.graphics.newText(font.FONT, text:sub(i, i)), x = font.FONT:getWidth(text:sub(1, i - 1)) }
    end
    e.calculatro_letters = cache
    return cache
end

-- Draws the text where the game's draw_self would, each letter lifted by
-- float, with a shadow at SHADOW_DEPTH like deepen_shadows'.
local function draw_bouncing(e)
    local c = e.config
    local font = c.lang.font
    local k = c.scale * font.FONTSCALE / G.TILESIZE
    local now = G.TIMERS.REAL
    local list = letters(e)
    local passes = {}
    if G.SETTINGS.GRAPHICS.shadows == 'On' then
        passes[1] = { { 0, 0, 0, 0.3 * c.colour[4] }, -e.shadow_parrallax.x * SHADOW_DEPTH / G.TILESIZE,
                      -e.shadow_parrallax.y * SHADOW_DEPTH / G.TILESIZE }
    end
    passes[#passes + 1] = { c.colour, 0, 0 }
    for _, pass in ipairs(passes) do
        prep_draw(e, 1)
        love.graphics.setColor(pass[1])
        for i, letter in ipairs(list) do
            local lift = 0.5 * float(c.scale, font.FONTSCALE, i, now) * font.FONTSCALE / G.TILESIZE
            love.graphics.draw(letter.drawable, (font.TEXT_OFFSET.x + letter.x * font.squish) * k + pass[2],
                               font.TEXT_OFFSET.y * k - lift + pass[3], 0, k * font.squish, k)
        end
        love.graphics.pop()
    end
end

local function bounce_text(e)
    if e.UIT == G.UIT.T and e.config.calculatro_bounce ~= nil then
        local draw_self = e.draw_self
        function e:draw_self()
            local c = self.config
            if c.calculatro_bounce and self.states.visible and c.colour and c.colour[4] > 0.01 and c.lang
                    and not G.SETTINGS.reduced_motion then
                draw_bouncing(self)
            else
                draw_self(self)
            end
        end
    end
    for _, child in pairs(e.children or {}) do
        if type(child) == 'table' and child.UIT then bounce_text(child) end
    end
end

local function ui_children(e)
    local out = {}
    for _, child in ipairs(e.children or {}) do
        if type(child) == 'table' and child.UIT then out[#out + 1] = child end
    end
    return out
end

-- The game shakes the screen a little on every button click
-- (UIElement:click adds to G.ROOM.jiggle after running the button), which is
-- too much for buttons pressed as often as the panel's. They keep the click
-- sound and the press.
local function steady_buttons(e)
    if e.config and e.config.button == 'calculatro_button' then
        function e:click()
            local room = G.ROOM
            local jiggle = room and room.jiggle
            UIElement.click(self)
            if room then room.jiggle = jiggle end
        end
    end
    for _, child in ipairs(ui_children(e)) do steady_buttons(child) end
end
Calculatro.steady_buttons = steady_buttons

-- Card query and setting names show their popup (config.calculatro_popup, see
-- popup_definition) above them while hovered, through the game's own hover
-- popups (Node:hover, h_popup). It's built anew each time: removing a popup
-- removes its suit icons' sprites too.
-- Balatro registers an element in G.MOVEABLES and G.STAGE_OBJECTS each time
-- it lays it out (UIElement:set_values runs Moveable.init), and it lays out a
-- container with a maxw or maxh twice when its contents don't fit, to shrink
-- them: everything in a cell whose text is shrunk is registered twice.
-- Removing an element unregisters it once, so the entry left behind keeps it,
-- and through the definition's button_UIE links its whole box, alive. The
-- panel is rebuilt so often that this filled the heap past the game's 300 MB
-- ceiling, beyond which it collects garbage every frame (nuGC) and runs at a
-- few frames a second. Run after building a box: keeps each object's first
-- entry.
local function register_once()
    local stages = G.STAGE_OBJECTS
    for _, list in ipairs({ G.MOVEABLES or false, stages and stages[G.STAGE] or false }) do
        if list then
            local seen, n, len = {}, 0, #list
            for i = 1, len do
                local v = list[i]
                if not seen[v] then
                    seen[v] = true
                    n = n + 1
                    list[n] = v
                end
            end
            for i = n + 1, len do list[i] = nil end
        end
    end
end
Calculatro.register_once = register_once

local function hover_popups(e)
    if e.config and e.config.calculatro_popup then
        function e:hover()
            -- Read now: the panel may have updated it in place since (update_values).
            local popup = self.config.calculatro_popup
            self.config.h_popup = Calculatro.popup_definition(popup.name, popup.about, popup.text)
            self.config.h_popup_config = { align = 'tm', offset = { x = 0, y = -0.1 }, parent = self }
            UIElement.hover(self)
            local box = self.children and self.children.h_popup
            if box and box.UIRoot then
                register_once()
                deepen_shadows(box.UIRoot)
            end
        end
    end
    for _, child in ipairs(ui_children(e)) do hover_popups(child) end
end
Calculatro.hover_popups = hover_popups

-- Removes a box built by the mod. The game unregisters each of its
-- elements with a search of G.MOVEABLES and G.STAGE_OBJECTS, about 600
-- elements in lists of over a thousand: 20 ms for the panel. Instead, it
-- removes the box with empty lists in their place and then drops everything
-- removed from them in one pass (with any duplicates; see register_once).
local function remove_box(box)
    local stages = G.STAGE_OBJECTS
    local moveables, staged = G.MOVEABLES, stages and stages[G.STAGE]
    if moveables then G.MOVEABLES = {} end
    if staged then stages[G.STAGE] = {} end
    local ok, err = pcall(box.remove, box)
    if moveables then G.MOVEABLES = moveables end
    if staged then stages[G.STAGE] = staged end
    for _, list in ipairs({ moveables or false, staged or false }) do
        if list then
            local seen, n, len = {}, 0, #list
            for i = 1, len do
                local v = list[i]
                if not seen[v] and not (type(v) == 'table' and v.REMOVED) then
                    seen[v] = true
                    n = n + 1
                    list[n] = v
                end
            end
            for i = n + 1, len do list[i] = nil end
        end
    end
    if not ok then error(err, 0) end
end

return {
    ui_children = ui_children, deepen_shadows = deepen_shadows, strike_through = strike_through,
    bounce_text = bounce_text, float = float,
    steady_buttons = steady_buttons, register_once = register_once, hover_popups = hover_popups,
    remove_box = remove_box,
}

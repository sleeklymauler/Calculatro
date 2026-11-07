---------------------------------------------------------------------------
-- What the odds and the target editor are both built from: the sizes they share
-- and the definition nodes of their text, cells, rows, buttons and frame.
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')

local NAME_W, VALUE_W, GAP_W, DISCARD_W = 1.4, 0.75, 0.25, 0.95  -- DISCARD_W fits "Discard"
-- Card query rows have no Keep value (with a full hand and nothing face
-- down, it's 0% or 100%), so their names take its place: labels like "Lucky
-- card: 1+" are too long for NAME_W, and text wider than its cell is shrunk.
local CARD_NAME_W = NAME_W + VALUE_W + GAP_W
local REMOVE_W = 0.3  -- each row ends with an x button that stops tracking it
local SEARCH_W = 0.3  -- before it, the best-discard search button
-- Card query rows have a pencil between the two that edits the query; it
-- comes out of their names' width.
local EDIT_W = 0.3
local TITLE_W = 1.6
local SCALE = 0.32

local function label(text, colour, scale)
    return { n = G.UIT.T, config = { text = text, scale = scale or SCALE, colour = colour or G.C.UI.TEXT_LIGHT, shadow = true } }
end

-- Text on a button. The game draws button text's shadows itself.
local function plain(text, colour, scale)
    return { n = G.UIT.T, config = { text = text, scale = scale or SCALE, colour = colour or G.C.UI.TEXT_LIGHT } }
end

-- The game's suit icons, in the atlas row the View Deck screen uses
-- (G.UIDEF.view_deck), keyed by the UTF-8 symbols the app sends for them:
-- the font has no suit symbols.
local SUIT_ICONS = {
    ['\226\153\165'] = 0,  -- U+2665 Hearts
    ['\226\153\166'] = 1,  -- U+2666 Diamonds
    ['\226\153\163'] = 2,  -- U+2663 Clubs
    ['\226\153\160'] = 3,  -- U+2660 Spades
}
local SUIT_PATTERN = '\226\153[\160-\166]'
-- Each icon sits on a small white tile, like a pip on a card: the black
-- suits vanish against the dark inset otherwise. Tile and icon together are
-- no taller than the text, so rows don't grow. ICON_GAP keeps it off the
-- letters either side: a wider transparent cell rather than padding on the
-- row, which would make the row taller too.
local ICON_SIZE, ICON_PAD, ICON_GAP = 0.18, 0.015, 0.035

-- Set while building a definition only for its values (without_sprites):
-- suit icons are then left without their sprite, since the game keeps every
-- sprite made until it's removed, and that definition is never built.
local values_only = false

-- What `build(...)` returns, a definition made only to read its values from.
local function without_sprites(build, ...)
    values_only = true
    local ok, definition = pcall(build, ...)
    values_only = false
    if not ok then error(definition, 0) end
    return definition
end

-- `tile_colour` is the tile's colour (default: white).
local function suit_icon(x, tile_colour)
    local icon = {}
    if not values_only then
        local atlas = G.ASSET_ATLAS['ui_' .. (G.SETTINGS.colourblind_option and 2 or 1)]
        local sprite = Sprite(0, 0, ICON_SIZE, ICON_SIZE, atlas, { x = x, y = 1 })
        sprite.states.drag.can, sprite.states.hover.can, sprite.states.collide.can = false, false, false
        icon[1] = { n = G.UIT.O, config = { object = sprite, can_collide = false } }
    end
    local tile = { n = G.UIT.C, config = { align = 'cm', padding = ICON_PAD, r = 0.03, colour = tile_colour or G.C.WHITE },
                   nodes = icon }
    return { n = G.UIT.C, config = { align = 'cm', padding = 0, minw = ICON_SIZE + 2 * (ICON_PAD + ICON_GAP) }, nodes = { tile } }
end

-- A label whose suit symbols are drawn as the game's suit icons ("Q♠: 1+").
-- `text_node` makes its text (default: label).
local function name_label(text, text_node)
    text_node = text_node or label
    local nodes, pos = {}, 1
    while true do
        local i, j = text:find(SUIT_PATTERN, pos)
        while i and not SUIT_ICONS[text:sub(i, j)] do i, j = text:find(SUIT_PATTERN, j + 1) end
        if not i then break end
        if i > pos then nodes[#nodes + 1] = text_node(text:sub(pos, i - 1)) end
        nodes[#nodes + 1] = suit_icon(SUIT_ICONS[text:sub(i, j)])
        pos = j + 1
    end
    if #nodes == 0 then return text_node(text) end
    if pos <= #text then nodes[#nodes + 1] = text_node(text:sub(pos)) end
    return { n = G.UIT.R, config = { align = 'cm', padding = 0 }, nodes = nodes }
end

-- Text's width at `scale`, as the game lays it out (UIBox's text nodes), or
-- nil without the game's font.
local function text_width(text, scale)
    local font = G.LANG and G.LANG.font
    if not (font and font.FONT and font.FONT.getWidth) then return nil end
    return font.FONT:getWidth(text) * font.squish * scale * font.FONTSCALE / G.TILESIZE
end

-- A label's width as name_label draws it, suit icons and all.
local function label_width(text, scale)
    local letters, icons = text:gsub(SUIT_PATTERN, '')
    local w = text_width(letters, scale)
    return w and w + icons * (ICON_SIZE + 2 * (ICON_PAD + ICON_GAP))
end

-- `text` (a card query's label) cut short to fit `w` at `scale`, as the game
-- would shrink it instead: "Plain not 5/9.. ♥♣♦: 1-3". The cut comes out of
-- the name, never its suit symbols or the count after them; the full label
-- is in the row's popup. As it is without the game's font.
function Calculatro.fit_label(text, w, scale)
    local full = label_width(text, scale)
    if not full or full <= w then return text end
    local name, tail = text:match('^(.*)(: [^:]*)$')
    if not name then name, tail = text, '' end
    -- The suit symbols ending the name, and the space before them, stay.
    while #name >= 3 and name:sub(-3):find('^' .. SUIT_PATTERN .. '$') do
        tail, name = name:sub(-3) .. tail, name:sub(1, -4)
    end
    if name:sub(-1) == ' ' then tail, name = ' ' .. tail, name:sub(1, -2) end
    for keep = #name - 1, 1, -1 do
        local cut = name:sub(1, keep):gsub('[%s/%-]+$', '') .. '..' .. tail
        if label_width(cut, scale) <= w then return cut end
    end
    return '..' .. tail
end

-- The inset behind the odds: what BOSS_MAIN is outside boss blinds
-- (Blind:change_colour). BOSS_MAIN itself is the boss's colour, too light
-- behind light text for some (The Club, The Water, The Psychic, ...) and
-- the same hue as the green/red discard values for others.
local function inset_colour()
    local black = G.C.BLACK
    return { black[1] * 0.95, black[2] * 0.95, black[3] * 0.95, black[4] }
end

-- The popup over a card query's name: its full label, and what each part
-- of it means (`about`, lines of "Field: value", from the builder) if known,
-- in the panel's colours: light text on its dark inset, in its frame.
local POPUP_SCALE = 0.36

local function popup_line(nodes)
    return { n = G.UIT.R, config = { align = 'cl', padding = 0.02 }, nodes = nodes }
end

local function popup_name(name)
    return name_label(name, function(text) return label(text, G.C.UI.TEXT_LIGHT, POPUP_SCALE) end)
end

local function popup_box(rows)
    local C = G.C
    return { n = G.UIT.ROOT, config = { align = 'cm', padding = 0.05, r = 0.1, colour = C.DYN_UI.MAIN, emboss = 0.05 }, nodes = {
        { n = G.UIT.C, config = { align = 'cl', padding = 0.08, r = 0.1, colour = inset_colour(), emboss = 0.05 }, nodes = rows },
    } }
end

function Calculatro.popup_definition(name, about)
    local rows = { popup_line({ popup_name(name) }) }
    for text in (about or ''):gmatch('[^\n]+') do
        local field, value = text:match('^(.-): (.*)$')
        rows[#rows + 1] = popup_line({ label(field .. ': ', G.C.JOKER_GREY), label(value) })
    end
    return popup_box(rows)
end

-- The box over a face-down card: what it can be, a line for each group of suits.
function Calculatro.hint_definition(lines)
    local rows = {}
    for i, text in ipairs(lines) do rows[i] = popup_line({ popup_name(text) }) end
    return popup_box(rows)
end

-- A card query's name in a cell `w` wide, cut to fit (fit_label), that shows
-- its popup while hovered (see hover_popups). `card`: {key, name, about}.
local function card_name_cell(card, w)
    return { n = G.UIT.C, config = { id = 'calculatro_name_' .. card.key, align = 'cl', minw = w, maxw = w,
                                     collideable = true, calculatro_popup = { name = card.name, about = card.about } }, nodes = {
        name_label(Calculatro.fit_label(card.name, w, SCALE)),
    } }
end

-- The font's pixels, in the room's units (m6x11 at SCALE: 64 of its 1024 units
-- per em, rendered at 200 px per em). The arrow and the glyphs are drawn in boxes
-- this size.
local FONT_PX = 200 * 64 / 1024 * SCALE * 0.1 / 20
-- A text node's height at SCALE: the game makes it the font's line height
-- (m6x11's em, 200 px) times its TEXT_HEIGHT_SCALE, 0.83.
local TEXT_H = 200 * 0.83 * SCALE * 0.1 / 20

local function cell(w, align, node)
    return { n = G.UIT.C, config = { align = align, minw = w, maxw = w }, nodes = { node } }
end

local function blank(w)
    return { n = G.UIT.C, config = { minw = w }, nodes = {} }
end

local ROW_PAD = 0.03  -- around and between a row's cells

local function row(nodes, config)
    local copy = {}
    for k, v in pairs(config or {}) do copy[k] = v end
    config = copy
    config.align, config.padding = config.align or 'cl', ROW_PAD
    return { n = G.UIT.R, config = config, nodes = nodes }
end

-- A pixel-art icon drawn in boxes the size of the font's pixels, like the
-- arrow: `bitmap` is rows of '#' (a pixel) and '.'. Runs of pixels share a box.
local function glyph(name, bitmap, colour)
    local rows = {}
    for i, bits in ipairs(bitmap) do
        local nodes, x = {}, 1
        while x <= #bits do
            local lit = bits:sub(x, x) == '#'
            local run = bits:match(lit and '^#+' or '^[^#]+', x)
            nodes[#nodes + 1] = { n = G.UIT.B, config = { w = #run * FONT_PX, h = FONT_PX,
                                                          colour = lit and colour or G.C.CLEAR, shadow = lit or nil } }
            x = x + #run
        end
        rows[i] = { n = G.UIT.R, config = { align = 'cl', padding = 0 }, nodes = nodes }
    end
    return { n = G.UIT.C, config = { align = 'cm', padding = 0, glyph = name }, nodes = rows }
end

local CROSS = { '#...#', '.#.#.', '..#..', '.#.#.', '#...#' }
local MAGNIFIER = { '.##...', '#..#..', '#..#..', '.##...', '....#.', '.....#' }
local PENCIL = { '....##', '...###', '..###.', '.###..', '.##...', '#.....' }

-- Room above and below text in the editor's boxes: m6x11's descenders (g,
-- p, y) hang below a text node's box, so they'd poke out of a box only as
-- tall as its text.
local TEXT_PAD = 0.05

-- `nodes` as a row with TEXT_PAD above and below it, for a box's contents.
local function padded(nodes, align)
    local function space() return { n = G.UIT.R, config = { minh = TEXT_PAD, padding = 0 }, nodes = {} } end
    return { space(), { n = G.UIT.R, config = { align = align or 'cm', padding = 0 }, nodes = nodes }, space() }
end

-- A button in the game's style. Clicking it runs Calculatro.act(action);
-- without an action it's inert and drawn in `off_colour` (default: dark).
-- opts: id, w and h (its least size; also its widest, so wider text shrinks),
-- align, colour, off_colour, outline (white, for the chosen tab), padded
-- (room for descenders, see TEXT_PAD).
local function button(nodes, action, opts)
    local enabled = action ~= nil
    if opts.padded then nodes = padded(nodes, opts.align) end
    return { n = G.UIT.C, config = {
        id = opts.id, align = opts.align or 'cm', minw = opts.w, maxw = opts.w, minh = opts.h, r = 0.06,
        colour = enabled and opts.colour or opts.off_colour or G.C.BLACK,
        outline = opts.outline, outline_colour = opts.outline and G.C.WHITE or nil,
        shadow = true, hover = enabled or nil,
        button = enabled and 'calculatro_button' or nil, calculatro_action = action,
    }, nodes = nodes }
end

-- The x that stops tracking a row's target or card query.
local function remove_cell(key, action)
    return { n = G.UIT.C, config = { align = 'cr', minw = REMOVE_W }, nodes = {
        button({ glyph('x', CROSS, G.C.UI.TEXT_LIGHT) }, action,
               { id = 'calculatro_remove_' .. key, w = 0.2, h = 0.2, colour = G.C.RED }),
    } }
end

-- The search button that finds a row's best discard: green while it
-- can search, dimmed while it does, dark and inert otherwise.
local function search_cell(t, colours)
    local action = t.search == 'on' and { 'search', t.key } or nil
    return { n = G.UIT.C, config = { align = 'cr', minw = SEARCH_W }, nodes = {
        button({ glyph('search', MAGNIFIER, action and G.C.UI.TEXT_LIGHT or colours.muted) }, action,
               { id = 'calculatro_search_' .. t.key, w = 0.2, h = 0.2, colour = G.C.GREEN,
                 off_colour = t.search == 'busy' and G.C.UI.BACKGROUND_INACTIVE or nil }),
    } }
end

-- The pencil that opens the editor on a card query, to change it: on the
-- one being edited (`editing`), it's outlined and stops editing it. Dark and
-- inert for queries the builder didn't make.
local function edit_cell(c, colours, editing)
    local action = c.editable and { 'edit', c.key } or nil
    return { n = G.UIT.C, config = { align = 'cr', minw = EDIT_W }, nodes = {
        button({ glyph('edit', PENCIL, action and G.C.UI.TEXT_LIGHT or colours.muted) }, action,
               { id = 'calculatro_edit_' .. c.key, w = 0.2, h = 0.2, colour = G.C.GOLD, outline = editing and 1 or nil }),
    } }
end

local TITLE_SCALE = 0.36
-- The title row's config: the odds and the editor both have one.
local TITLE_ROW = { calculatro_anim = 'title' }

local function title()
    return { n = G.UIT.C, config = { align = 'cl', minw = TITLE_W }, nodes = {
        label('CALCULATRO', G.C.UI.TEXT_LIGHT, TITLE_SCALE),
    } }
end

-- The frame around the panel's rows, styled like the HUD sidebar: MAIN
-- frame and BOSS_DARK body.
local function frame(body)
    local C = G.C
    return { n = G.UIT.ROOT, config = { align = 'cm', padding = 0.05, r = 0.1, colour = C.DYN_UI.MAIN,
                                        calculatro_anim = 'frame' }, nodes = {
        { n = G.UIT.R, config = { align = 'cm', padding = 0.08, r = 0.1, colour = C.DYN_UI.BOSS_DARK,
                                  calculatro_anim = 'body' }, nodes = {
            { n = G.UIT.C, config = { align = 'cm' }, nodes = body },
        } },
    } }
end

-- The dark inset behind the odds or the editor's contents (inset_colour,
-- whatever the blind).
local INSET_PAD = 0.06

local function inset(nodes, minw)
    return { n = G.UIT.R, config = { align = 'cm', padding = INSET_PAD, r = 0.1, colour = inset_colour(), emboss = 0.05,
                                     minw = minw, id = 'calculatro_inset', calculatro_anim = 'inset' }, nodes = nodes }
end

return {
    NAME_W = NAME_W, VALUE_W = VALUE_W, GAP_W = GAP_W, DISCARD_W = DISCARD_W, CARD_NAME_W = CARD_NAME_W,
    REMOVE_W = REMOVE_W, SEARCH_W = SEARCH_W, EDIT_W = EDIT_W, TITLE_W = TITLE_W, SCALE = SCALE, FONT_PX = FONT_PX,
    TEXT_H = TEXT_H, TEXT_PAD = TEXT_PAD, ROW_PAD = ROW_PAD, INSET_PAD = INSET_PAD,
    TITLE_SCALE = TITLE_SCALE, TITLE_ROW = TITLE_ROW, SUIT_ICONS = SUIT_ICONS,
    label = label, plain = plain, suit_icon = suit_icon, name_label = name_label,
    card_name_cell = card_name_cell, cell = cell, blank = blank, row = row, padded = padded, button = button,
    remove_cell = remove_cell, search_cell = search_cell, edit_cell = edit_cell,
    title = title, frame = frame, inset = inset, without_sprites = without_sprites,
}

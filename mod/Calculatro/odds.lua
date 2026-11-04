---------------------------------------------------------------------------
-- The odds: what the panel shows outside the target editor (see panel.lua).
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')
local array = require('calculatro.json').array
local tracked = require('calculatro.tracked')
local widgets = require('calculatro.widgets')
require('calculatro.builder')
require('calculatro.search')
require('calculatro.app')

local HANDS, SHORT_NAMES = tracked.HANDS, tracked.SHORT_NAMES
local NAME_W, VALUE_W, GAP_W = widgets.NAME_W, widgets.VALUE_W, widgets.GAP_W
local DISCARD_W, CARD_NAME_W, REMOVE_W = widgets.DISCARD_W, widgets.CARD_NAME_W, widgets.REMOVE_W
local SEARCH_W, EDIT_W, TITLE_W = widgets.SEARCH_W, widgets.EDIT_W, widgets.TITLE_W
local SCALE, FONT_PX, TITLE_SCALE, TITLE_ROW = widgets.SCALE, widgets.FONT_PX, widgets.TITLE_SCALE, widgets.TITLE_ROW
local label, plain, cell, blank, row = widgets.label, widgets.plain, widgets.cell, widgets.blank, widgets.row
local button, card_name_cell, remove_cell = widgets.button, widgets.card_name_cell, widgets.remove_cell
local search_cell, edit_cell, title = widgets.search_cell, widgets.edit_cell, widgets.title
local frame, inset = widgets.frame, widgets.inset

-- Hand types the boss won't let score this round, as a set of HANDS names:
-- The Eye bans those already played, The Mouth all but the first one played
-- (Blind:debuff_hand). The player sees both: The Mouth names its hand, and
-- they know what they've played.
function Calculatro.banned_hands()
    local blind, banned = G.GAME and G.GAME.blind, {}
    if not blind or blind.disabled then return banned end
    for _, h in ipairs(HANDS) do
        if blind.name == 'The Eye' and blind.hands then
            banned[h] = blind.hands[h] or nil
        elseif blind.name == 'The Mouth' and blind.only_hand then
            banned[h] = blind.only_hand ~= h or nil
        end
    end
    return banned
end

-- What the odds panel shows: a row for each tracked target hand ("Any
-- target" first when there are several) and card query, with the odds the
-- app keyed by them ('-' until it has). Odds for an older state stay up until
-- the new ones arrive, greyed out if that takes longer than `patience`.
-- Hands the boss won't let score are `banned` (struck out, odds still shown);
-- "Any target" is too when all of them are.
function Calculatro.panel_model()
    local t = Calculatro.tracked()
    local model = { targets = array(), cards = array(),
                    can_add = Calculatro.can_add_hand() or Calculatro.can_add_card() }
    if #t.hands == 0 and #t.cards == 0 then
        model.message = 'Nothing tracked'
        return model
    end
    local odds = Calculatro.odds
    if not (odds and odds.id == Calculatro.state_id) then
        local waited = Calculatro.clock - Calculatro.changed_at
        -- With no snapshot written yet, there's nothing for the app to answer.
        local unanswered = not Calculatro.state_id or Calculatro.ack_id ~= Calculatro.state_id
        if unanswered and waited >= Calculatro.timeout then
            model.message = Calculatro.app_failed() and "App didn't start" or 'Waiting for app'
        elseif waited >= Calculatro.patience then
            model.message = 'Calculating...'
        end
        model.stale = model.message ~= nil or nil
    end
    model.message = model.message or Calculatro.search_message()
    local hand_odds, card_odds = {}, {}
    if odds then
        for _, o in ipairs(odds.targets or {}) do hand_odds[o.key] = o end
        for _, o in ipairs(odds.cards or {}) do card_odds[o.key] = o end
    end
    local banned, all_banned = Calculatro.banned_hands(), true
    for _, h in ipairs(t.hands) do all_banned = all_banned and banned[h] ~= nil end
    local can_search, searching = Calculatro.can_search(), Calculatro.searching
    -- A row's search button: 'busy' while searching, 'on' if it can search, else off.
    local function search_state(key)
        return (searching and searching.key == key and 'busy') or (can_search and 'on') or nil
    end
    local function hand(key, name, removable, is_banned)
        local o = hand_odds[key] or {}
        model.targets[#model.targets + 1] = { key = key, name = name, keep = o.keep, discard = o.discard,
                                              trend = o.trend, removable = removable, banned = is_banned or nil,
                                              search = search_state(key) }
    end
    if #t.hands > 1 then hand('Any target', 'Any target', nil, all_banned) end
    for _, h in ipairs(t.hands) do hand(h, SHORT_NAMES[h] or h, true, banned[h]) end
    for _, c in ipairs(t.cards) do
        local o = card_odds[c.query] or {}
        model.cards[#model.cards + 1] = { key = c.query, name = c.label, about = c.about, discard = o.discard,
                                          trend = o.trend, search = search_state(c.query),
                                          editable = Calculatro.builder_for(c.query) and true or nil }
    end
    return model
end

-- Card-only columns are as wide as target columns too.
local COLUMN_W = CARD_NAME_W + DISCARD_W + SEARCH_W + REMOVE_W
local PLUS_W, COLUMN_GAP = 0.35, 0.2
-- Fits "Calculating..." and "Waiting for app" beside the title and its +
-- button. A column leaves more than this, enough for "App didn't start".
local MESSAGE_W = 1.5

-- The arrow between Keep and Discard, drawn in boxes the size of the font's
-- pixels: a 2-pixel shaft like its '-' and a stepped head, 9 by 8 pixels.
-- A text "->" is 13 pixels wide, too wide for GAP_W, so it would be shrunk;
-- this fits with room either side and is shorter than the digits beside it.
local ARROW = { 2, 2, 2, 2, 2, 8, 6, 4, 2 }  -- column heights, in font pixels

-- Discard values are right-aligned, so the arrow takes the left of the
-- discard cell too: its rows split GAP_W + DISCARD_W into ARROW_CELL_W and
-- a cell that just fits the widest value (">99.9%", 37 pixels). The arrow
-- sits midway between the Keep value (whose '%' ends a pixel short of its
-- cell) and the usual discard value ("88.3%", 31 pixels).
local DISCARD_VALUE_W = 38 * FONT_PX
local ARROW_CELL_W = GAP_W + DISCARD_W - DISCARD_VALUE_W
local ARROW_LEFT = (ARROW_CELL_W - #ARROW * FONT_PX + DISCARD_VALUE_W - 31 * FONT_PX - FONT_PX) / 2

local function arrow(colour)
    local nodes = {}
    for i, h in ipairs(ARROW) do
        nodes[i] = { n = G.UIT.B, config = { w = FONT_PX, h = h * FONT_PX, colour = colour, shadow = true } }
    end
    return { n = G.UIT.C, config = { align = 'cl', minw = ARROW_CELL_W, maxw = ARROW_CELL_W }, nodes = {
        { n = G.UIT.R, config = { align = 'cm', padding = 0 }, nodes = {
            { n = G.UIT.C, config = { minw = ARROW_LEFT }, nodes = {} },
            { n = G.UIT.R, config = { align = 'cm', padding = 0 }, nodes = nodes },
        } },
    } }
end

-- The odds rows in sections, each filling columns of its own: the target
-- hands (kind 'hands'), then the card queries ('cards'). Empty
-- sections are left out. Each row is a function building it.
local function sections(model, colours)
    local C = G.C
    -- Odds values carry an id (config.calculatro_value), by which
    -- refresh_panel updates them in place.
    local function value(text, colour, id)
        local node = label(text or '-', model.stale and colours.stale or colour)
        node.config.calculatro_value = id
        return node
    end
    -- Discard values are green when better than keeping the hand, red when worse.
    local function trend_colour(t)
        return (t.trend == 1 and C.GREEN) or (t.trend == -1 and C.RED) or C.UI.TEXT_LIGHT
    end
    local hands, cards = {}, {}
    for _, t in ipairs(model.targets) do
        local colour = trend_colour(t)
        hands[#hands + 1] = function()
            local name = label(t.name, t.banned and colours.muted)
            name.config.calculatro_strike = t.banned
            return row({
                cell(NAME_W, 'cl', name),
                cell(VALUE_W, 'cr', value(t.keep, C.UI.TEXT_LIGHT, 'keep:' .. t.key)),
                t.discard and arrow(colours.muted) or blank(ARROW_CELL_W),
                cell(DISCARD_VALUE_W, 'cr', value(t.discard, colour, 'discard:' .. t.key)),
                search_cell(t, colours),
                t.removable and remove_cell(t.key, { 'remove_hand', t.key }) or blank(REMOVE_W),
            }, { calculatro_anim = 'hand:' .. t.key })
        end
    end
    for _, c in ipairs(model.cards) do
        cards[#cards + 1] = function()
            return row({
                card_name_cell(c, CARD_NAME_W - EDIT_W),
                cell(DISCARD_W, 'cr', value(c.discard, trend_colour(c), 'card:' .. c.key)),
                search_cell(c, colours),
                edit_cell(c, colours),
                remove_cell(c.key, { 'remove_card', c.key }),
            }, { calculatro_anim = 'card:' .. c.key })
        end
    end
    local out = {}
    if #hands > 0 then out[#out + 1] = { kind = 'hands', rows = hands } end
    if #cards > 0 then out[#out + 1] = { kind = 'cards', rows = cards } end
    return out
end

-- Rows in the longest section: the most a column ever needs.
function Calculatro.row_count(model)
    local most = 0
    for _, section in ipairs(sections(model, {})) do most = math.max(most, #section.rows) end
    return most
end

-- The sections split into columns of at most `per` rows, as
-- {section, first, last} spans; each section starts a new column.
local function split(list, per)
    local spans = {}
    for _, section in ipairs(list) do
        for first = 1, #section.rows, per do
            spans[#spans + 1] = { section = section, first = first, last = math.min(first + per - 1, #section.rows) }
        end
    end
    return spans
end

-- Number of columns with at most `per` rows each.
function Calculatro.column_count(model, per)
    return #split(sections(model, {}), per)
end

-- A column's header, in its section's words ("Hands  Keep  Discard" or "In
-- hand  Discard"). `n`: which of its section's columns it heads.
local function header(kind, n, colours)
    local discard = cell(DISCARD_W, 'cr', label('Discard', colours.muted))
    local config = { calculatro_anim = 'header:' .. kind .. ':' .. n }
    if kind == 'cards' then
        return row({ cell(CARD_NAME_W - EDIT_W, 'cl', label('In hand', colours.muted)), discard, blank(SEARCH_W),
                     blank(EDIT_W), blank(REMOVE_W) }, config)
    end
    return row({
        cell(NAME_W, 'cl', label('Hands', colours.muted)),
        cell(VALUE_W, 'cr', label('Keep', colours.muted)),
        blank(GAP_W),
        discard,
        blank(SEARCH_W),
        blank(REMOVE_W),
    }, config)
end

-- The nodes of the columns `spans` (see split), with gaps between them.
local function columns(spans, colours)
    local nodes, section_columns = {}, {}
    for _, span in ipairs(spans) do
        local kind = span.section.kind
        section_columns[kind] = (section_columns[kind] or 0) + 1
        local rows = { header(kind, section_columns[kind], colours) }
        for k = span.first, span.last do
            rows[#rows + 1] = span.section.rows[k]()
        end
        if #nodes > 0 then nodes[#nodes + 1] = blank(COLUMN_GAP) end
        nodes[#nodes + 1] = { n = G.UIT.C, config = { align = 'tl', id = 'calculatro_column' }, nodes = rows }
    end
    return nodes
end

-- The + beside the title, which opens the target editor; it's greyed out and
-- inert once nothing more fits (model.can_add).
local function plus_cell(model, colours)
    local C = G.C
    return { n = G.UIT.C, config = { align = 'cl', minw = PLUS_W }, nodes = {
        button({ plain('+', model.can_add and C.UI.TEXT_LIGHT or colours.muted, TITLE_SCALE) },
               model.can_add and { 'open' } or nil,
               { id = 'calculatro_open', w = 0.3, colour = C.BLUE, off_colour = C.UI.BACKGROUND_INACTIVE }),
    } }
end

-- The odds panel: frame (see frame) and an inset with the odds rows, which
-- fill columns of at most `per` rows (default: each section in one) top to
-- bottom, left to right, target hands and card queries in columns of their
-- own, each with its section's header.
--
-- The message (model.message) must not change the panel's size, or the
-- columns would change whenever it comes and goes: beside the odds it goes
-- on the title row, in a space kept for it (at least MESSAGE_W, so the panel
-- may be wider than its columns) whether or not there's a message. With
-- nothing tracked, it goes on a row of its own.
function Calculatro.definition(model, per)
    local C = G.C
    -- Solid greys: the game's semi-transparent TEXT_INACTIVE vanishes on some
    -- bosses' panel colours.
    local colours = { muted = C.JOKER_GREY, stale = C.UI.BACKGROUND_INACTIVE }
    local list = sections(model, colours)
    local column_nodes = columns(split(list, math.max(1, per or Calculatro.row_count(model))), colours)
    local count = math.max(1, (#column_nodes + 1) / 2)
    local width = COLUMN_W * count + COLUMN_GAP * (count - 1)
    local plus = plus_cell(model, colours)
    local message = label(model.message or '', colours.muted, SCALE * 0.85)
    local body = {}
    if #list > 0 then
        local message_w = math.max(width - TITLE_W - PLUS_W, MESSAGE_W)
        message.config.calculatro_value = 'message'
        body[1] = row({ title(), plus, cell(message_w, 'cr', message) }, TITLE_ROW)
        body[2] = inset(column_nodes)
    else
        body[1] = row({ title(), plus }, TITLE_ROW)
        body[2] = row({ message }, { align = 'cm', calculatro_anim = 'empty' })
    end
    return frame(body)
end

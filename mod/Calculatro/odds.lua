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
local settings_cell, GEAR_W = widgets.settings_cell, widgets.GEAR_W
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

-- A value's mark (see overlay.mark), if it's of this state.
local function mark_state(o, field)
    local mark = o[field .. '_mark']
    return type(mark) == 'table' and mark.state or nil
end

-- What a value given up on shows in its popup, by what stopped it (the
-- mark's limit): lines broken by hand, in ASCII, like the settings'.
local TOO_SLOW = {
    time = { 'Working these odds out exactly', 'would take too long, so the', 'app has given up on them.' },
    memory = { 'Working these odds out exactly', 'would use too much memory, so', 'the app has given up on them.' },
}
-- And a best-discard search given up on, on the title row.
local SEARCH_TOO_SLOW = {
    time = { 'Comparing every discard would', 'take too long, so the app has', 'given up on this search.' },
    memory = { 'Comparing every discard would', 'use too much memory, so the app', 'has given up on this search.' },
}

-- The popup of something given up on (`mark`), with `lines` by its limit.
local function too_slow_popup(mark, lines)
    return { name = 'Too slow', text = lines[mark.limit] or lines.time }
end

-- And a search given up on that answered from estimates (a `mark` with a
-- `how`): the best option's odds, likely in a range (one value if they're
-- exact), then why it's by estimates, by the mark's limit.
local SEARCH_ESTIMATED = {
    time = { 'The best discard by estimates:', 'comparing every discard exactly', 'would take too long.' },
    memory = { 'The best discard by estimates:', 'comparing every discard exactly', 'would use too much memory.' },
}

local function search_popup(mark)
    if not mark.how then return too_slow_popup(mark, SEARCH_TOO_SLOW) end
    local text = {}
    if mark.low and mark.high then
        text[1] = mark.low == mark.high and string.format('Odds %s.', mark.low)
            or string.format('Odds likely %s to %s.', mark.low, mark.high)
    end
    for _, line in ipairs(SEARCH_ESTIMATED[mark.limit] or SEARCH_ESTIMATED.time) do text[#text + 1] = line end
    return { name = 'Approximate', text = text }
end

-- What an approximation is, by its mark's `how`: its range (certain from
-- bounds, only likely from a sample of deals or of draws) and where it comes
-- from. And what's become of its exact odds: still being worked out, or given
-- up on (by the mark's limit).
local HOW = {
    bounds = { range = 'Between %s and %s.', text = { "From each hand's own odds." } },
    sample = { range = 'Likely %s to %s.', text = { 'Estimated from a sample of the', "deals the hand's sorts allow." } },
    draws = { range = 'Likely %s to %s.', text = { 'Estimated from a sample of the', 'hands that could be drawn.' } },
}
local EXACT = {
    refining = { 'Still working out the exact', 'odds.' },
    time = { 'Working them out exactly would', 'take too long, so the app has', 'given up on them.' },
    memory = { 'Working them out exactly would', 'use too much memory, so the app', 'has given up on them.' },
}

-- The popup of an approximation (a `mark` with a `how`): its range, where
-- it comes from, and whether the exact odds are still to come.
local function approximate_popup(mark)
    local text = {}
    local how = HOW[mark.how] or HOW.bounds
    if mark.low and mark.high then text[1] = string.format(how.range, mark.low, mark.high) end
    for _, line in ipairs(how.text) do text[#text + 1] = line end
    local exact = mark.state == 'refining' and EXACT.refining or EXACT[mark.limit] or EXACT.time
    for _, line in ipairs(exact) do text[#text + 1] = line end
    return { name = 'Approximate', text = text }
end

-- What the odds panel shows: a row for each tracked target hand ("Any
-- target" first when there are several) and card query, with the odds the
-- app keyed by them ('-' until it has). Odds for an older state stay up until
-- the new ones arrive, greyed out if that takes longer than `patience`. So do
-- the values a partial answer doesn't have yet (`keep_stale`,
-- `discard_stale`), each on its own. An approximation has an asterisk; it and
-- a value given up on ("?") have a popup from their mark (`keep_mark`,
-- `discard_mark`).
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
    local waited = Calculatro.clock - Calculatro.changed_at
    local late = false  -- the values still to come are greyed out
    if not (odds and odds.id == Calculatro.state_id) then
        -- With no snapshot written yet, there's nothing for the app to answer.
        local unanswered = not Calculatro.state_id or Calculatro.ack_id ~= Calculatro.state_id
        if unanswered and waited >= Calculatro.timeout then
            model.message = Calculatro.app_failed() and "App didn't start" or 'Waiting for app'
        elseif waited >= Calculatro.patience then
            model.message = 'Calculating...'
        end
        model.stale = model.message ~= nil or nil
    elseif odds.partial and waited >= Calculatro.patience then
        model.message, late = 'Calculating...', true
    end
    if not model.message then
        local message, mark = Calculatro.search_message()
        model.message = message
        model.message_popup = mark and search_popup(mark)
    end
    -- Whether a value is still to come and greyed out, and the popup of one given up on.
    local function stale(o, field)
        return late and mark_state(o, field) == 'pending' or nil
    end
    local function popup(o, field)
        local mark, state = o[field .. '_mark'], mark_state(o, field)
        if (state == 'refining' or state == 'final') and mark.how then return approximate_popup(mark) end
        return state == 'final' and too_slow_popup(mark, TOO_SLOW) or nil
    end
    -- A value's text: an approximation's with its asterisk.
    local function text(o, field)
        local mark = o[field .. '_mark']
        if o[field] and type(mark) == 'table' and mark.how then return o[field] .. '*' end
        return o[field]
    end
    local function has_discard(o)
        return (o.discard ~= nil or o.discard_mark ~= nil) or nil
    end
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
        model.targets[#model.targets + 1] = {
            key = key, name = name, keep = text(o, 'keep'), discard = text(o, 'discard'), trend = o.trend,
            keep_stale = stale(o, 'keep'), discard_stale = stale(o, 'discard'),
            keep_popup = popup(o, 'keep'), discard_popup = popup(o, 'discard'), has_discard = has_discard(o),
            removable = removable, banned = is_banned or nil, search = search_state(key) }
    end
    if #t.hands > 1 then hand('Any target', 'Any target', nil, all_banned) end
    for _, h in ipairs(t.hands) do hand(h, SHORT_NAMES[h] or h, true, banned[h]) end
    for _, c in ipairs(t.cards) do
        local o = card_odds[c.query] or {}
        model.cards[#model.cards + 1] = {
            key = c.query, name = c.label, about = c.about, discard = text(o, 'discard'), trend = o.trend,
            discard_stale = stale(o, 'discard'), discard_popup = popup(o, 'discard'), has_discard = has_discard(o),
            search = search_state(c.query), editable = Calculatro.builder_for(c.query) and true or nil }
    end
    return model
end

-- Card-only columns are as wide as target columns too.
local COLUMN_W = CARD_NAME_W + DISCARD_W + SEARCH_W + REMOVE_W
local PLUS_W, COLUMN_GAP = 0.35, 0.2
-- Fits "Calculating..." and "Waiting for app" beside the title and its +
-- and gear. A column leaves more than this, enough for "App didn't start".
local MESSAGE_W = 1.5
-- Messages that bounce like the game's text while the app works (see
-- bounce_text); the rest stay still.
local BOUNCING = { ['Calculating...'] = true, ['Searching...'] = true }

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
    -- refresh_panel updates them in place. In a cell `w` wide, with its
    -- popup while hovered (see hover_popups) if it's approximate or was given
    -- up on: on the whole cell, since a "*" or "?" alone is hard to hover. The
    -- cell carries the id too (config.calculatro_popup_of), so the popup is
    -- updated in place as well, as an approximation narrows.
    local function value(w, text, colour, id, stale, popup)
        local node = label(text or '-', (model.stale or stale) and colours.stale or colour)
        node.config.calculatro_value = id
        local out = cell(w, 'cr', node)
        if popup then
            out.config.id, out.config.collideable, out.config.calculatro_popup = 'calculatro_' .. id, true, popup
            out.config.calculatro_popup_of = id
        end
        return out
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
                value(VALUE_W, t.keep, C.UI.TEXT_LIGHT, 'keep:' .. t.key, t.keep_stale, t.keep_popup),
                t.has_discard and arrow(colours.muted) or blank(ARROW_CELL_W),
                value(DISCARD_VALUE_W, t.discard, colour, 'discard:' .. t.key, t.discard_stale, t.discard_popup),
                search_cell(t, colours),
                t.removable and remove_cell(t.key, { 'remove_hand', t.key }) or blank(REMOVE_W),
            }, { calculatro_anim = 'hand:' .. t.key })
        end
    end
    for _, c in ipairs(model.cards) do
        cards[#cards + 1] = function()
            return row({
                card_name_cell(c, CARD_NAME_W - EDIT_W),
                value(DISCARD_W, c.discard, trend_colour(c), 'card:' .. c.key, c.discard_stale, c.discard_popup),
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
    local plus, gear = plus_cell(model, colours), settings_cell()
    local message = label(model.message or '', colours.muted, SCALE * 0.85)
    local body = {}
    if #list > 0 then
        local message_w = math.max(width - TITLE_W - PLUS_W - GEAR_W, MESSAGE_W)
        message.config.calculatro_value = 'message'
        message.config.calculatro_bounce = BOUNCING[model.message] or false
        local message_cell = cell(message_w, 'cr', message)
        if model.message_popup then  -- a search given up on
            message_cell.config.id, message_cell.config.collideable = 'calculatro_message', true
            message_cell.config.calculatro_popup = model.message_popup
        end
        body[1] = row({ title(), plus, gear, message_cell }, TITLE_ROW)
        body[2] = inset(column_nodes)
    else
        body[1] = row({ title(), plus, gear }, TITLE_ROW)
        body[2] = row({ message }, { align = 'cm', calculatro_anim = 'empty' })
    end
    return frame(body)
end

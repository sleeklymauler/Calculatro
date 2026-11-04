local Calculatro = require('calculatro.core')
local array = require('calculatro.json').array
local tracked = require('calculatro.tracked')
local builder = require('calculatro.builder')
local widgets = require('calculatro.widgets')
require('calculatro.search')

local HANDS, SHORT_NAMES, set_of = tracked.HANDS, tracked.SHORT_NAMES, tracked.set_of
local OPTIONS, RANKS, SUITS = builder.OPTIONS, builder.RANKS, builder.SUITS
local SUIT_SYMBOLS, stone_only = builder.SUIT_SYMBOLS, builder.stone_only
local CARD_NAME_W, REMOVE_W, EDIT_W, TITLE_W = widgets.CARD_NAME_W, widgets.REMOVE_W, widgets.EDIT_W, widgets.TITLE_W
local SCALE, TEXT_H, TEXT_PAD = widgets.SCALE, widgets.TEXT_H, widgets.TEXT_PAD
local ROW_PAD, INSET_PAD, TITLE_ROW, SUIT_ICONS = widgets.ROW_PAD, widgets.INSET_PAD, widgets.TITLE_ROW, widgets.SUIT_ICONS
local label, plain, suit_icon, name_label = widgets.label, widgets.plain, widgets.suit_icon, widgets.name_label
local card_name_cell, cell, blank, row = widgets.card_name_cell, widgets.cell, widgets.blank, widgets.row
local padded, button, remove_cell, edit_cell = widgets.padded, widgets.button, widgets.remove_cell, widgets.edit_cell
local title, frame, inset = widgets.title, widgets.frame, widgets.inset

---------------------------------------------------------------------------
-- Target editor: replaces the odds in the panel while it's open (`+` opens
-- it, Done closes it). The Hands tab toggles target hands; the Cards tab
-- builds card queries on the left and lists the tracked ones on the right,
-- CARDS_PER_PAGE to a page (Balatro's UI can't clip a scrolling list).
---------------------------------------------------------------------------
local CARDS_PER_PAGE = 5
local TAB_W, DONE_W = 1.3, 0.95
local TOGGLE_W, TOGGLE_GAP = 1.9, 0.25     -- the Hands tab's hand toggles
local FIELD_W = 0.8                        -- the builder's row names ("Special")
local CYCLE_ARROW_W, CYCLE_GAP = 0.25, 0.03
local OPTION_W = 1.6                       -- a cycle's value ("Holographic")
local CMP_W, COUNT_W, COUNT_GAP = 1.2, 0.25, 0.05  -- CMP_W leaves room around "at least"
local AND_W = 0.4                          -- "and", before a range's top
local CHIP_W, SUIT_CHIP_W = 0.28, 0.45     -- the rank chips fit "10"
local NOT_W = 0.45                         -- the toggle that negates the special
local BUILDER_GAP = 0.3                    -- between the builder and the tracked list
local PAGE_W = 1.25                        -- fits "Page 2/2"
local ADD_GAP = 0.1                        -- between the builder and Add

local function cycle_w(w) return w + 2 * (CYCLE_ARROW_W + CYCLE_GAP) end
-- The Cards tab: the builder beside the tracked list, with the rows' padding
-- around and between their cells; under them, Add and the page cycle, which
-- stays put as pages change. The rank chips start at the builder's left
-- edge, so its rows fit in their width, but for Count with a range, which is
-- wider. The list takes what the builder leaves.
local CHIPS_W = 13 * CHIP_W + 14 * ROW_PAD
local RANGE_ROW_W = FIELD_W + cycle_w(CMP_W) + COUNT_GAP + cycle_w(COUNT_W) + AND_W + cycle_w(COUNT_W) + 7 * ROW_PAD
local BUILDER_W = math.max(CHIPS_W, RANGE_ROW_W)
local CARDS_TAB_W = BUILDER_W + BUILDER_GAP + (CARD_NAME_W + REMOVE_W + 3 * ROW_PAD)
-- Add takes what the page cycle leaves; its label is cut to fit (fit_label).
local PAGE_CELL_W = cycle_w(PAGE_W)
local ADD_W = CARDS_TAB_W - PAGE_CELL_W - 3 * ROW_PAD
-- The inset's width, the same on both tabs: the Hands tab is narrower, and
-- centred in it.
local EDITOR_W = CARDS_TAB_W + 2 * INSET_PAD
-- Both tabs are as tall as the Cards tab: its builder's five rows of padded
-- buttons, then Add. The Hands tab's (at most) six rows of toggles are a little shorter.
local EDITOR_ROW_H = TEXT_H + 2 * TEXT_PAD + 2 * ROW_PAD
local EDITOR_H = 6 * EDITOR_ROW_H + ADD_GAP

-- What the editor shows; like the odds panel's model, it's rebuilt only
-- when this changes.
function Calculatro.editor_model()
    local editor, t = Calculatro.editor, Calculatro.tracked()
    local model = { editor = true, tab = editor.tab }
    if editor.tab == 'hands' then
        local on, can_add = set_of(t.hands), Calculatro.can_add_hand()
        model.hands = array()
        for i, h in ipairs(HANDS) do
            model.hands[i] = { key = h, name = SHORT_NAMES[h] or h,
                               state = (on[h] and 'on') or (can_add and 'off') or 'full' }
        end
        return model
    end
    local b = Calculatro.builder
    local cmp, special = OPTIONS.cmp[b.cmp], OPTIONS.special[b.special]
    local query, name = Calculatro.builder_query()
    local tracked = set_of(t.cards, 'query')
    if not tracked[editor.editing] then editor.editing = nil end
    -- Editing, Add saves the query in the edited one's place.
    local can_add
    if editor.editing then
        can_add = query and query ~= editor.editing and not tracked[query]
    else
        can_add = query and not tracked[query] and Calculatro.can_add_card()
    end
    -- Under Stone, the chips show any (no ranks, every suit) and are inert.
    local stone = stone_only()
    local ranks, suits = array(), array()
    for r = 2, 14 do ranks[#ranks + 1] = { key = r, text = RANKS[r].chip, on = not stone and b.ranks[r] or nil } end
    for i, s in ipairs(SUITS) do suits[i] = { key = s, on = (stone or b.suits[s]) or nil } end
    model.builder = {
        cmp = cmp.text, count = tostring(b.count), high = cmp.query == 'between' and tostring(b.high) or nil,
        preset = Calculatro.rank_choice(), ranks = ranks, suits = suits, fixed = stone or nil,
        special = special.text, negate = b.negate or nil, can_negate = special.query and true or nil, add = name,
        can_add = can_add or nil, save = editor.editing and true or nil,
    }
    local pages = math.max(1, math.ceil(#t.cards / CARDS_PER_PAGE))
    editor.page = math.max(1, math.min(editor.page or 1, pages))
    model.page, model.pages, model.cards = editor.page, pages, array()
    for i = (editor.page - 1) * CARDS_PER_PAGE + 1, math.min(#t.cards, editor.page * CARDS_PER_PAGE) do
        local c = t.cards[i]
        model.cards[#model.cards + 1] = { key = c.query, name = c.label, about = c.about,
                                          editable = Calculatro.builder_for(c.query) and true or nil,
                                          editing = c.query == editor.editing or nil }
    end
    return model
end

-- An option cycle in the game's style: '<' value '>'.
local function cycle(id, text, w, prev, next)
    local C = G.C
    local text_colour = prev and C.UI.TEXT_LIGHT or C.UI.BACKGROUND_INACTIVE
    local function step(symbol, action, suffix)
        return button({ plain(symbol, text_colour) }, action,
                      { id = id .. suffix, w = CYCLE_ARROW_W, colour = C.RED, padded = true })
    end
    return { n = G.UIT.C, config = { align = 'cm', padding = 0 }, nodes = {
        step('<', prev, '_prev'),
        blank(CYCLE_GAP),
        { n = G.UIT.C, config = { id = id, align = 'cm', minw = w, maxw = w, r = 0.06, colour = C.L_BLACK },
          nodes = padded({ plain(text, text_colour) }) },
        blank(CYCLE_GAP),
        step('>', next, '_next'),
    } }
end

local function builder_cycle(field, text, w, enabled)
    return cycle('calculatro_' .. field, text, w, enabled and { 'cycle', { field, -1 } } or nil,
                 enabled and { 'cycle', { field, 1 } } or nil)
end

local function hands_tab(model)
    local C = G.C
    local columns = {}
    -- In the game's order down the first column, then the second.
    local first = math.ceil(#HANDS / 2)
    for c = 0, 1 do
        local rows = {}
        for r = 1, c == 0 and first or #HANDS - first do
            local h = model.hands[c * first + r]
            local full = h.state == 'full'
            rows[r] = row({ button(
                { blank(0.1), plain(h.name, full and C.UI.BACKGROUND_INACTIVE or C.UI.TEXT_LIGHT) },
                not full and { 'toggle_hand', h.key } or nil,
                { id = 'calculatro_hand_' .. h.key, w = TOGGLE_W, align = 'cl', padded = true,
                  colour = h.state == 'on' and C.BLUE or C.L_BLACK }) }, { calculatro_anim = 'toggle:' .. h.key })
        end
        if c > 0 then columns[#columns + 1] = blank(TOGGLE_GAP) end
        columns[#columns + 1] = { n = G.UIT.C, config = { align = 'tl' }, nodes = rows }
    end
    return { { n = G.UIT.C, config = { align = 'tm', minh = EDITOR_H }, nodes = {
        { n = G.UIT.R, config = { align = 'tm', padding = 0 }, nodes = columns },
    } } }
end

-- The builder's rows: the count, the rank cycle with the rank chips that it sets
-- to its presets, the suit chips and the special.
local function builder_rows(b)
    local C = G.C
    local function field(name, nodes)
        table.insert(nodes, 1, cell(FIELD_W, 'cl', label(name, C.JOKER_GREY)))
        return row(nodes, { calculatro_anim = 'field:' .. name })
    end
    -- A toggle chip: blue while on.
    local function chip(id, nodes, on, action, w)
        return button(nodes, action, { id = id, w = w, h = TEXT_H + 2 * TEXT_PAD, padded = true,
                                       colour = on and C.BLUE or C.L_BLACK })
    end
    local count = { builder_cycle('cmp', b.cmp, CMP_W, true), blank(COUNT_GAP),
                    builder_cycle('count', b.count, COUNT_W, true) }
    if b.high then
        count[#count + 1] = cell(AND_W, 'cm', label('and', C.JOKER_GREY))
        count[#count + 1] = builder_cycle('high', b.high, COUNT_W, true)
    end
    -- Under Stone (b.fixed), the chips are dark and inert.
    local chip_text = b.fixed and C.UI.BACKGROUND_INACTIVE or nil
    local ranks = {}
    for _, r in ipairs(b.ranks) do
        ranks[#ranks + 1] = chip('calculatro_rank_' .. r.key, { plain(r.text, chip_text) }, r.on,
                                 not b.fixed and { 'toggle_rank', r.key } or nil, CHIP_W)
    end
    local suits = {}
    for _, s in ipairs(b.suits) do
        suits[#suits + 1] = chip('calculatro_suit_' .. s.key, { suit_icon(SUIT_ICONS[SUIT_SYMBOLS[s.key]], chip_text) },
                                 s.on, not b.fixed and { 'toggle_suit', s.key } or nil, SUIT_CHIP_W)
    end
    local negate = button(
        { plain('not', b.can_negate and C.UI.TEXT_LIGHT or C.UI.BACKGROUND_INACTIVE) },
        b.can_negate and { 'toggle_negate' } or nil,
        { id = 'calculatro_negate', w = NOT_W, colour = b.negate and C.BLUE or C.L_BLACK, padded = true })
    return {
        field('Count', count),
        field('Rank', { builder_cycle('preset', b.preset, OPTION_W, not b.fixed) }),
        row(ranks, { calculatro_anim = 'field:Ranks' }),
        field('Suit', suits),
        field('Special', { negate, builder_cycle('special', b.special, OPTION_W, true) }),
    }
end

-- Add, or Save while editing, showing what it would add.
local function add_button(b)
    local C = G.C
    local add_text = b.can_add and C.UI.TEXT_LIGHT or C.UI.BACKGROUND_INACTIVE
    return button(
        { name_label(Calculatro.fit_label((b.save and 'Save' or 'Add') .. (b.add and '  ' .. b.add or ''), ADD_W, SCALE),
                     function(text) return plain(text, add_text) end) },
        b.can_add and { b.save and 'save_card' or 'add_card' } or nil,
        { id = 'calculatro_add', w = ADD_W, colour = C.GREEN, padded = true })
end

-- The rows of this page's tracked queries, their names `name_w` wide.
local function listed_rows(model, name_w)
    local C = G.C
    local list = {}
    local colours = { muted = C.JOKER_GREY }
    for _, c in ipairs(model.cards) do
        list[#list + 1] = row({ card_name_cell(c, name_w), edit_cell(c, colours, c.editing),
                                remove_cell(c.key, { 'remove_card', c.key }) },
                              { calculatro_anim = 'listed:' .. c.key })
    end
    if #list == 0 then list[1] = row({ label('Nothing tracked', C.JOKER_GREY) }, { calculatro_anim = 'listed:none' }) end
    return list
end

local function cards_tab(model)
    local b = model.builder
    local builder_w = b.high and RANGE_ROW_W or CHIPS_W
    local name_w = CARD_NAME_W + BUILDER_W - builder_w - EDIT_W - ROW_PAD
    local page = model.pages > 1
        and cycle('calculatro_page', 'Page ' .. model.page .. '/' .. model.pages, PAGE_W, { 'page', -1 }, { 'page', 1 })
    return {
        { n = G.UIT.C, config = { align = 'tl', minh = EDITOR_H }, nodes = {
            { n = G.UIT.R, config = { align = 'tl', padding = 0 }, nodes = {
                { n = G.UIT.C, config = { align = 'tl', minw = builder_w }, nodes = builder_rows(b) },
                blank(BUILDER_GAP),
                { n = G.UIT.C, config = { align = 'tl' }, nodes = listed_rows(model, name_w) },
            } },
            { n = G.UIT.R, config = { minh = ADD_GAP, padding = 0 }, nodes = {} },
            row({ add_button(b), { n = G.UIT.C, config = { align = 'cm', minw = PAGE_CELL_W, calculatro_anim = 'pages' },
                                   nodes = { page or nil } } }, { calculatro_anim = 'field:Add' }),
        } },
    }
end

-- The editor, in the same frame as the odds: the title with Done, the
-- Hands | Cards tabs, and the tab's contents in the inset. The tabs take the
-- column headers' place, but its rows are taller than odds rows (TEXT_PAD),
-- so it's taller than the odds panel with full columns: refresh_panel moves
-- it down to fit.
function Calculatro.editor_definition(model)
    local C = G.C
    local function tab(text, name)
        local chosen = model.tab == name
        return button({ plain(text) }, not chosen and { 'tab', name } or nil,
                      { id = 'calculatro_tab_' .. name, w = TAB_W, colour = C.GREY, off_colour = C.RED, padded = true,
                        outline = chosen and 1 or nil })
    end
    return frame({
        row({
            title(),
            { n = G.UIT.C, config = { align = 'cr', minw = EDITOR_W - TITLE_W }, nodes = {
                button({ plain('Done') }, { 'done' }, { id = 'calculatro_done', w = DONE_W, colour = C.ORANGE, padded = true }),
            } },
        }, TITLE_ROW),
        row({ tab('Hands', 'hands'), blank(0.1), tab('Cards', 'cards') }, { align = 'cm', calculatro_anim = 'tabs' }),
        inset(model.tab == 'hands' and hands_tab(model) or cards_tab(model), EDITOR_W),
    })
end

-- Opens the editor on the Cards tab, on a tracked card query's page, with
-- the builder set to make it, so that Save puts what it's changed to in its
-- place. Again on the query being edited, it stops editing (the builder
-- keeps its choices, to add as another).
function Calculatro.edit(query)
    local builder = Calculatro.builder_for(query)
    if not builder then return end
    local editor = Calculatro.editor
    if editor and editor.editing == query then
        editor.editing = nil
        return
    end
    local index
    for i, c in ipairs(Calculatro.tracked().cards) do
        if c.query == query then index = i end
    end
    if not index then return end
    Calculatro.builder, Calculatro.editor_tab = builder, 'cards'
    Calculatro.editor = { tab = 'cards', page = math.ceil(index / CARDS_PER_PAGE), editing = query }
end

-- What the panel's buttons do: Calculatro.act{name, argument}.
Calculatro.actions = {
    open = function() Calculatro.editor = { tab = Calculatro.editor_tab, page = 1 } end,
    done = function() Calculatro.editor = nil end,
    tab = function(tab) Calculatro.editor.tab, Calculatro.editor_tab = tab, tab end,
    toggle_hand = function(name) Calculatro.toggle_hand(name) end,
    remove_hand = function(name) Calculatro.remove_hand(name) end,
    remove_card = function(query) Calculatro.remove_card(query) end,
    search = function(key) Calculatro.search(key) end,
    edit = function(query) Calculatro.edit(query) end,
    cycle = function(arg) Calculatro.cycle(arg[1], arg[2]) end,
    toggle_rank = function(r) Calculatro.toggle_rank(r) end,
    toggle_suit = function(s) Calculatro.toggle_suit(s) end,
    toggle_negate = function() Calculatro.builder.negate = not Calculatro.builder.negate end,
    -- Shows the page it's added to.
    add_card = function()
        local query, name = Calculatro.builder_query()
        if query and Calculatro.add_card(query, name, Calculatro.builder_about()) and Calculatro.editor then
            Calculatro.editor.page = math.ceil(#Calculatro.tracked().cards / CARDS_PER_PAGE)
        end
    end,
    save_card = function()
        local query, name = Calculatro.builder_query()
        if query and Calculatro.replace_card(Calculatro.editor.editing, query, name, Calculatro.builder_about()) then
            Calculatro.editor.editing = nil
        end
    end,
    page = function(dir)
        local editor = Calculatro.editor
        local pages = math.max(1, math.ceil(#Calculatro.tracked().cards / CARDS_PER_PAGE))
        editor.page = ((editor.page or 1) - 1 + dir) % pages + 1
    end,
}

function Calculatro.act(action)
    if type(action) ~= 'table' or not Calculatro.actions[action[1]] then return end
    -- Buttons of a panel being replaced can still be clicked for a frame.
    if (action[1] == 'tab' or action[1] == 'page' or action[1] == 'save_card') and not Calculatro.editor then return end
    Calculatro.actions[action[1]](action[2])
end

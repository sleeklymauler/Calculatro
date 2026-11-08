---------------------------------------------------------------------------
-- The panel: its place above the deck, and what's in it: the odds, the target
-- editor or the settings, rebuilt or updated in place as they change.
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')
local json = require('calculatro.json')
local uibox = require('calculatro.uibox')
local without_sprites = require('calculatro.widgets').without_sprites
local guard = require('calculatro.log').guard
require('calculatro.odds')
require('calculatro.editor')
require('calculatro.settings')
require('calculatro.anim')

local array, encode = json.array, json.encode
local deepen_shadows, strike_through = uibox.deepen_shadows, uibox.strike_through
local steady_buttons, hover_popups = uibox.steady_buttons, uibox.hover_popups
local register_once, remove_box = uibox.register_once, uibox.remove_box

-- Only while playing a blind: not in the shop, blind select, booster packs,
-- round results, or with a menu (run info, deck view, options) open. Also
-- not while a played hand is scored: the played cards reach the panel.
function Calculatro.in_blind()
    if not (G and G.STAGES and G.STATES and G.STAGE == G.STAGES.RUN and G.deck and G.hand) then
        return false
    end
    if G.OVERLAY_MENU then return false end
    local S, state = G.STATES, G.STATE
    if state == S.PLAY_TAROT then state = G.TAROT_INTERRUPT end  -- using a consumable
    return state == S.SELECTING_HAND or state == S.DRAW_TO_HAND
end

-- How far above the deck's top the panel sits. The panel is wider than the
-- gap between the hand and the screen edge, so it has to clear the hand's
-- cards: while selecting, CardArea:move slides the hand 1.9 above its resting
-- place (level with the deck), and align_cards draws cards up to 0.2 above
-- their slot, highlighted ones another HIGHLIGHT_H higher.
function Calculatro.panel_lift()
    return 1.9 + 0.2 + (G.HIGHLIGHT_H or 0) + 0.1
end

-- The tallest the panel can be: from its bottom (see panel_lift) up to just
-- under the consumables' card count label ("0/2"). The label is the bottom
-- row of the area's own UIBox (CardArea:draw), centred on the area, so it
-- hangs below it; before that box exists, allow a generous estimate.
-- The label's row has 0.03 padding under its text, which the panel may
-- use: in game that's what lets columns of 6 rows fit.
-- nil if there's no consumable area to avoid.
local LABEL_MARGIN, LABEL_ESTIMATE = -0.03, 0.6
function Calculatro.max_panel_height()
    local area = G.consumeables
    if not (area and area.T) then return nil end
    local label = area.children and area.children.area_uibox
    local top = area.T.y + area.T.h + LABEL_ESTIMATE
    if label and label.T and not label.REMOVED then
        top = label.T.y + label.T.h + LABEL_MARGIN
    end
    return G.deck.T.y - Calculatro.panel_lift() - top
end

-- The panel collides (can_collide) for its buttons. They need a box that
-- isn't replaced while they're used, so it's only rebuilt when what it shows
-- changes, and the editor and settings only by their own buttons.
local function build_panel(definition)
    local panel = UIBox{
        definition = definition,
        config = { align = 'tm', offset = { x = 0, y = -Calculatro.panel_lift() }, major = G.deck, bond = 'Strong', can_collide = true },
    }
    register_once()
    -- 'tm' centres it above the deck; shift it to line up with the deck's
    -- right edge, so extra columns grow leftwards over the empty play area.
    panel.alignment.offset.x = (G.deck.T.w - panel.T.w) / 2
    panel:align_to_major()
    panel:hard_set_VT()
    if panel.UIRoot then
        deepen_shadows(panel.UIRoot)
        strike_through(panel.UIRoot)
        steady_buttons(panel.UIRoot)
        hover_popups(panel.UIRoot)
    end
    return panel
end

-- What of the odds panel's model changes its layout: everything but the
-- odds values (their text and colour), whether they're stale, and the title
-- row's message, which refresh_panel updates in place. Whether a target hand
-- has a Discard value is layout: its arrow comes and goes with it.
local VALUE_FIELDS = { keep = true, discard = true, trend = true }
local function layout_key(model)
    local function rows(list)
        local out = array()
        for i, r in ipairs(list) do
            local copy = { has_discard = r.discard ~= nil or nil }
            for k, v in pairs(r) do
                if not VALUE_FIELDS[k] then copy[k] = v end
            end
            out[i] = copy
        end
        return out
    end
    local empty = #model.targets == 0 and #model.cards == 0
    return encode({ targets = rows(model.targets), cards = rows(model.cards), can_add = model.can_add,
                    message = empty and model.message or nil })
end
Calculatro.layout_key = layout_key

-- The value nodes' configs in a definition, by id (config.calculatro_value).
-- A box's elements share their definition's configs.
local function values_of(node, out)
    out = out or {}
    local id = node.config and node.config.calculatro_value
    if id then out[id] = node.config end
    for _, child in ipairs(node.nodes or {}) do values_of(child, out) end
    return out
end

-- Puts the values of `definition` (the panel's layout) into the panel:
-- their text and colour, laying the box out again if any text changed.
-- Laying it out again doesn't register its elements again (see
-- register_once), unlike building it.
local function update_values(panel, definition)
    local live, changed = panel.calculatro_values, false
    for id, config in pairs(values_of(definition)) do
        local now = live[id]
        if now then
            if now.text ~= config.text then
                now.text, changed = config.text, true
            end
            now.colour = config.colour
        end
    end
    if changed and panel.recalculate then panel:recalculate() end
end

-- How many rows per column the panel fitted with, by layout and room:
-- trying out a layout means building a box for it.
Calculatro.fits, Calculatro.fit_count = {}, 0
local MAX_FITS = 64

local function build_odds(model, layout, max_h)
    local fit_key = layout .. '@' .. string.format('%.3f', max_h or -1)
    local per = Calculatro.fits[fit_key] or math.max(1, Calculatro.row_count(model))
    local definition = Calculatro.definition(model, per)
    local panel = build_panel(definition)
    while max_h and panel.T.h > max_h and per > 1
            and Calculatro.column_count(model, per - 1) <= Calculatro.max_columns do
        remove_box(panel)
        per = per - 1
        definition = Calculatro.definition(model, per)
        panel = build_panel(definition)
    end
    if not Calculatro.fits[fit_key] then
        if Calculatro.fit_count >= MAX_FITS then Calculatro.fits, Calculatro.fit_count = {}, 0 end
        Calculatro.fits[fit_key], Calculatro.fit_count = per, Calculatro.fit_count + 1
    end
    panel.calculatro_values, panel.calculatro_per = values_of(definition), per
    return panel
end

-- The editor or the settings. Too tall to fit under the consumables at the
-- odds' place, the editor grows downwards instead, into the room kept for
-- raised cards: there's no need to select cards while choosing what to track.
local function build_page(definition)
    local panel = build_panel(definition)
    local max_h = Calculatro.max_panel_height()
    if max_h and panel.T.h > max_h then
        panel.alignment.offset.y = panel.alignment.offset.y + panel.T.h - max_h
        panel:align_to_major()
        panel:hard_set_VT()
    end
    return panel
end

-- Rebuilds the panel when its layout changes, and only updates its values
-- when they're all that changed (most often, the odds); removes it outside
-- blinds, closing the editor or settings. The odds fill each column with as many rows
-- as keep it clear of the consumables before starting the next; a box's
-- size is only known once it's built, so it tries each section in one
-- column, then one fewer row per column, and so on (remembered in
-- Calculatro.fits).
function Calculatro.refresh_panel()
    if G.FUNCS then G.FUNCS.calculatro_button = Calculatro.button_pressed end
    local shown = Calculatro.visible and Calculatro.in_blind()
    if not shown then Calculatro.editor, Calculatro.settings_open = nil, nil end
    local model
    if shown then
        model = (Calculatro.editor and Calculatro.editor_model())
             or (Calculatro.settings_open and Calculatro.settings_model())
             or Calculatro.panel_model()
    end
    local page = model and (model.editor or model.settings)  -- rebuilt only by its own buttons
    local key = model and encode(model)
    local layout = model and not page and layout_key(model) or key
    local panel = Calculatro.panel
    if panel and panel.REMOVED then panel = nil end  -- the game removes it with the run
    local same_place = panel and Calculatro.panel_major == G.deck
    if same_place and key == Calculatro.panel_key then return end
    if same_place and layout == Calculatro.panel_layout and not page then
        update_values(panel, without_sprites(Calculatro.definition, model, panel.calculatro_per))
        Calculatro.panel_key = key
        return
    end
    -- The new panel animates from where this one is drawn.
    local from = panel and panel.UIRoot and same_place and Calculatro.drawn_layout(panel, Calculatro.clock) or nil
    if panel then remove_box(panel) end
    Calculatro.panel, Calculatro.panel_key, Calculatro.panel_layout, Calculatro.panel_major = nil, nil, nil, nil
    if not model then return end

    if model.editor then
        panel = build_page(Calculatro.editor_definition(model))
    elseif model.settings then
        panel = build_page(Calculatro.settings_definition(model))
    else
        panel = build_odds(model, layout, Calculatro.max_panel_height())
    end
    Calculatro.animate(panel, from)
    Calculatro.panel, Calculatro.panel_key, Calculatro.panel_layout, Calculatro.panel_major = panel, key, layout, G.deck
end

function Calculatro.key_pressed(key)
    if key ~= Calculatro.toggle_key or not Calculatro.in_blind() then return end
    if G.CONTROLLER and G.CONTROLLER.text_input_hook then return end
    Calculatro.visible = not Calculatro.visible
end

-- G.FUNCS.calculatro_button, run by the panel's buttons (see `button`).
function Calculatro.button_pressed(e)
    guard('button', Calculatro.act, e.config.calculatro_action)
end

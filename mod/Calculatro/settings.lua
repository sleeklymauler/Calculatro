---------------------------------------------------------------------------
-- Settings: the player's, not the run's, so they're kept in their own file in
-- the save directory rather than on G.GAME. The gear on the odds panel's title
-- row opens their page, which replaces the odds in the panel until Done, like
-- the target editor.
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')
local json = require('calculatro.json')
local worker = require('calculatro.worker')
local report = require('calculatro.log').report
local widgets = require('calculatro.widgets')
require('calculatro.editor')

local array, encode, decode = json.array, json.encode, json.decode
local SETTINGS_FILE = worker.SETTINGS_FILE
local CARD_NAME_W, TITLE_W, DONE_W = widgets.CARD_NAME_W, widgets.TITLE_W, widgets.DONE_W
local ROW_PAD, INSET_PAD, TITLE_ROW = widgets.ROW_PAD, widgets.INSET_PAD, widgets.TITLE_ROW
local label, plain, row, button = widgets.label, widgets.plain, widgets.row, widgets.button
local title, frame, inset = widgets.title, widgets.frame, widgets.inset

-- The page's rows, in order: each setting's key in Calculatro.settings (and
-- the file), the name it's shown by, and its popup's lines. The game's text
-- doesn't wrap, so the lines are broken by hand, and are ASCII (its font has
-- nothing else).
local SETTINGS = {
    { key = 'face_down', name = 'Face-down hints', about = {
        "Balatro sorts your hand by each card's",
        'true rank and suit, even face down.',
        'Where a face-down card ends up after',
        'a sort narrows down what it can be.',
        "Hover over one to see what's possible.",
        'The odds use this either way.',
    } },
}

local NAME_W, TOGGLE_W = CARD_NAME_W, 0.6
local SETTINGS_W = NAME_W + TOGGLE_W + 3 * ROW_PAD + 2 * INSET_PAD

-- Reads the settings file, keeping each known setting of the right type
-- (the defaults otherwise). Run once, as the mod starts and before the file
-- thread does, so the read can't wait on the lock a write there holds.
function Calculatro.load_settings()
    local raw = love.filesystem.read(SETTINGS_FILE)
    if not raw then return end
    local ok, saved = pcall(decode, raw)
    if not ok or type(saved) ~= 'table' then return end
    local settings = Calculatro.settings
    for key, value in pairs(settings) do
        if type(saved[key]) == type(value) then settings[key] = saved[key] end
    end
end

-- Saves the settings on the file thread, or here if there isn't one.
local function save()
    local text = encode(Calculatro.settings)
    local thread = worker.start()
    if thread then
        thread.requests:push({ settings = text })
        return
    end
    local ok, err = love.filesystem.write(SETTINGS_FILE, text)
    if not ok then report('saving settings', 'could not save the settings: ' .. tostring(err)) end
end

function Calculatro.set_setting(key, value)
    if Calculatro.settings[key] == nil or Calculatro.settings[key] == value then return end
    Calculatro.settings[key] = value
    save()
end

-- What the page shows; like the editor, it's rebuilt only when this changes.
function Calculatro.settings_model()
    local rows = array()
    for i, s in ipairs(SETTINGS) do rows[i] = { key = s.key, on = Calculatro.settings[s.key] and true or false } end
    return { settings = true, rows = rows }
end

-- The page, in the same frame as the odds: the title with Done, a header, and
-- a row for each setting in the inset, its name (with a popup saying what it
-- does) and an On/Off toggle, blue while on.
function Calculatro.settings_definition(model)
    local C = G.C
    local rows = {}
    for i, r in ipairs(model.rows) do
        local s = SETTINGS[i]
        local name = { n = G.UIT.C, config = { id = 'calculatro_setting_name_' .. r.key, align = 'cl', minw = NAME_W,
                                               maxw = NAME_W, collideable = true,
                                               calculatro_popup = { name = s.name, text = s.about } },
                       nodes = { label(s.name) } }
        local toggle = button({ plain(r.on and 'On' or 'Off') }, { 'toggle_setting', r.key },
                              { id = 'calculatro_setting_' .. r.key, w = TOGGLE_W, padded = true,
                                colour = r.on and C.BLUE or C.L_BLACK })
        rows[i] = row({ name, toggle }, { calculatro_anim = 'setting:' .. r.key })
    end
    return frame({
        row({
            title(),
            { n = G.UIT.C, config = { align = 'cr', minw = SETTINGS_W - TITLE_W }, nodes = {
                button({ plain('Done') }, { 'done' }, { id = 'calculatro_done', w = DONE_W, colour = C.ORANGE, padded = true }),
            } },
        }, TITLE_ROW),
        row({ label('Settings', C.JOKER_GREY) }, { align = 'cm', calculatro_anim = 'settings' }),
        inset({ { n = G.UIT.C, config = { align = 'tl' }, nodes = rows } }, SETTINGS_W),
    })
end

-- The gear opens the page (the panel shows the odds then, so the editor's closed).
Calculatro.actions.settings = function() Calculatro.editor, Calculatro.settings_open = nil, true end
Calculatro.actions.toggle_setting = function(key)
    if Calculatro.settings_open then Calculatro.set_setting(key, not Calculatro.settings[key]) end
end

return { SETTINGS = SETTINGS }

-- Calculatro: exports live Balatro game state to calculatro_state.json in the
-- save directory (%AppData%\Balatro) for the Python companion app, and shows
-- the odds it writes back to calculatro_odds.json in a panel above the deck.

local Calculatro = {
    interval = 0.2,       -- seconds between snapshots
    odds_interval = 0.05, -- seconds between reads of the odds file
    patience = 0.3,       -- seconds before odds for an older state are greyed out
    timeout = 2,          -- seconds without a reply before assuming the app isn't running
    toggle_key = 'c',     -- shows/hides the panel
    elapsed = 0,
    odds_elapsed = 0,
    clock = 0,
    last_json = nil,
    schema = 3,
    odds_schema = 1,
    -- Snapshot ids are '<session>:<seq>'; the app echoes them back with its odds.
    session = string.format('%d-%d', os.time(), math.floor(os.clock() * 1000)),
    seq = 0,
    state_id = nil,  -- id of the last snapshot written
    changed_at = 0,  -- clock when it was written
    ack_id = nil,    -- id the odds file last answered (pending or done)
    odds = nil,      -- last finished odds from this session
    odds_raw = nil,
    visible = true,
}

---------------------------------------------------------------------------
-- Minimal JSON encoder. Tables tagged with ARRAY (or non-empty 1..n tables)
-- become arrays, so an empty hand still encodes as [] rather than {}.
---------------------------------------------------------------------------
local ARRAY = {}
local function array(t) return setmetatable(t or {}, ARRAY) end
local escapes = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
                  ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }

local function encode_string(s)
    return '"' .. s:gsub('[%c"\\]', function(c)
        -- Some locales count bytes of UTF-8 sequences as control characters.
        if c:byte() >= 128 then return c end
        return escapes[c] or string.format('\\u%04x', c:byte())
    end) .. '"'
end

local function is_array(t)
    local n = 0
    for _ in pairs(t) do n = n + 1 end
    for i = 1, n do if t[i] == nil then return false end end
    return true
end

local function encode(v)
    local tv = type(v)
    if v == nil then return 'null'
    elseif tv == 'boolean' then return tostring(v)
    elseif tv == 'number' then
        if v ~= v or v == math.huge or v == -math.huge then return 'null' end
        if v == math.floor(v) and math.abs(v) < 1e15 then return string.format('%d', v) end
        return string.format('%.14g', v)
    elseif tv == 'string' then return encode_string(v)
    elseif tv == 'table' then
        local out = {}
        if getmetatable(v) == ARRAY or (next(v) ~= nil and is_array(v)) then
            for i = 1, #v do out[i] = encode(v[i]) end
            return '[' .. table.concat(out, ',') .. ']'
        end
        local keys = {}
        for k in pairs(v) do keys[#keys + 1] = k end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)  -- stable output
        for _, k in ipairs(keys) do
            out[#out + 1] = encode_string(tostring(k)) .. ':' .. encode(v[k])
        end
        return '{' .. table.concat(out, ',') .. '}'
    end
    return 'null'
end
Calculatro.encode = encode

---------------------------------------------------------------------------
-- Minimal JSON decoder for the odds file. Arrays come back tagged ARRAY;
-- null is not supported (the app never writes it).
---------------------------------------------------------------------------
local unescapes = { ['"'] = '"', ['\\'] = '\\', ['/'] = '/', b = '\b', f = '\f',
                    n = '\n', r = '\r', t = '\t' }

local function utf8_char(cp)
    if cp < 0x80 then return string.char(cp) end
    if cp < 0x800 then return string.char(0xC0 + math.floor(cp / 64), 0x80 + cp % 64) end
    return string.char(0xE0 + math.floor(cp / 4096), 0x80 + math.floor(cp / 64) % 64, 0x80 + cp % 64)
end

local function decode(s)
    local pos = 1
    local function fail(what) error('JSON: ' .. what .. ' at ' .. pos, 0) end
    local function skip() pos = s:find('[^ \t\r\n]', pos) or #s + 1 end
    local value

    local function string_value()
        local out = {}
        pos = pos + 1
        while true do
            local i = s:find('["\\]', pos)
            if not i then fail('unterminated string') end
            out[#out + 1] = s:sub(pos, i - 1)
            if s:sub(i, i) == '"' then
                pos = i + 1
                return table.concat(out)
            end
            local c = s:sub(i + 1, i + 1)
            if c == 'u' then
                local cp = tonumber(s:sub(i + 2, i + 5), 16) or fail('bad escape')
                out[#out + 1] = utf8_char(cp)
                pos = i + 6
            else
                out[#out + 1] = unescapes[c] or fail('bad escape')
                pos = i + 2
            end
        end
    end

    local function list(open, close, item)
        pos = pos + 1
        skip()
        if s:sub(pos, pos) == close then pos = pos + 1; return end
        while true do
            item()
            skip()
            local c = s:sub(pos, pos)
            pos = pos + 1
            if c == close then return end
            if c ~= ',' then fail('expected , or ' .. close) end
        end
    end

    function value()
        skip()
        local c = s:sub(pos, pos)
        if c == '{' then
            local t = {}
            list('{', '}', function()
                skip()
                if s:sub(pos, pos) ~= '"' then fail('expected key') end
                local k = string_value()
                skip()
                if s:sub(pos, pos) ~= ':' then fail('expected :') end
                pos = pos + 1
                t[k] = value()
            end)
            return t
        elseif c == '[' then
            local t = array()
            list('[', ']', function() t[#t + 1] = value() end)
            return t
        elseif c == '"' then
            return string_value()
        end
        for word, v in pairs({ ['true'] = true, ['false'] = false }) do
            if s:sub(pos, pos + #word - 1) == word then pos = pos + #word; return v end
        end
        local num = s:match('^-?%d+%.?%d*[eE]?[-+]?%d*', pos)
        if not num or num == '' then fail('unexpected ' .. (c == '' and 'end' or c)) end
        pos = pos + #num
        return tonumber(num) or fail('bad number')
    end

    local v = value()
    skip()
    if pos <= #s then fail('trailing data') end
    return v
end
Calculatro.decode = decode

---------------------------------------------------------------------------
-- Snapshot
---------------------------------------------------------------------------
local function card_info(card, hide_face_down)
    local highlighted = card.highlighted and true or false
    if hide_face_down and card.facing == 'back' then
        return { hidden = true, highlighted = highlighted }
    end
    return {
        rank = card.base and card.base.value,   -- '2'..'10', 'Jack', 'Queen', 'King', 'Ace'
        rank_id = card.base and card.base.id,   -- 2..14 (Ace = 14)
        suit = card.base and card.base.suit,    -- 'Spades', 'Hearts', 'Clubs', 'Diamonds'
        enhancement = card.config and card.config.center_key or nil,  -- 'c_base', 'm_stone', 'm_wild', ...
        edition = card.edition and card.edition.type or nil,
        seal = card.seal,
        debuff = card.debuff and true or false,
        highlighted = highlighted,
    }
end

local function area_cards(area, hide_face_down)
    local out = array()
    if area and area.cards then
        for i, card in ipairs(area.cards) do out[i] = card_info(card, hide_face_down) end
    end
    return out
end

-- Export a list of cards as a sorted multiset so order/position never leaks.
local function sorted_cards(cards)
    local keys = {}
    for i, c in ipairs(cards) do keys[i] = { encode(c), c } end
    table.sort(keys, function(a, b) return a[1] < b[1] end)
    local out = array()
    for i, k in ipairs(keys) do out[i] = k[2] end
    return out
end

local function sorted_deck(area)
    return sorted_cards(area_cards(area, false))
end

-- Identities of face-down hand cards. The player can't tell which is which,
-- but knows they come from the unseen pool, so the engine treats them like
-- undrawn deck cards.
local function hidden_pool(area)
    local cards = array()
    if area and area.cards then
        for _, card in ipairs(area.cards) do
            if card.facing == 'back' then
                local info = card_info(card, false)
                info.highlighted = nil
                cards[#cards + 1] = info
            end
        end
    end
    return sorted_cards(cards)
end

local function state_name(state)
    if G.STATES then
        for name, v in pairs(G.STATES) do if v == state then return name end end
    end
    return tostring(state)
end

function Calculatro.snapshot()
    if not (G and G.GAME and G.deck and G.hand and G.STAGE == G.STAGES.RUN) then
        return { schema = Calculatro.schema, in_run = false }
    end
    local round = G.GAME.current_round or {}
    local jokers = array()
    if G.jokers and G.jokers.cards then
        for i, j in ipairs(G.jokers.cards) do
            jokers[i] = { key = j.config and j.config.center_key, debuff = j.debuff and true or false }
        end
    end
    return {
        schema = Calculatro.schema,
        in_run = true,
        state = state_name(G.STATE),
        hand_size = G.hand.config.card_limit,
        hands_left = round.hands_left,
        discards_left = round.discards_left,
        blind = G.GAME.blind and G.GAME.blind.name or nil,
        hand = area_cards(G.hand, true),
        played = area_cards(G.play, true),
        deck = sorted_deck(G.deck),
        hidden_pool = hidden_pool(G.hand),
        discard = area_cards(G.discard, false),
        jokers = jokers,
    }
end

---------------------------------------------------------------------------
-- Files
---------------------------------------------------------------------------
local function save_path(name)
    return love.filesystem.getSaveDirectory() .. '/' .. name
end

local function write_state(json)
    local path = save_path('calculatro_state.json')
    local tmp = path .. '.tmp'
    local f = io.open(tmp, 'wb')
    if not f then return false end
    f:write(json)
    f:close()
    -- Windows os.rename won't overwrite; the reader retries on a missing file.
    os.remove(path)
    return os.rename(tmp, path)
end

-- Writes the snapshot if it changed, tagged with a new id.
function Calculatro.write_snapshot()
    local snapshot = Calculatro.snapshot()
    local json = encode(snapshot)
    if json == Calculatro.last_json then return end
    Calculatro.seq = Calculatro.seq + 1
    snapshot.id = Calculatro.session .. ':' .. Calculatro.seq
    if write_state(encode(snapshot)) then
        Calculatro.last_json = json
        Calculatro.state_id = snapshot.id
        Calculatro.changed_at = Calculatro.clock
    end
end

local function own_id(id)
    local prefix = Calculatro.session .. ':'
    return type(id) == 'string' and id:sub(1, #prefix) == prefix
end

-- The app answers each snapshot first with {id, pending = true}, then with
-- the odds once they're calculated. Odds left over from an earlier game
-- session are ignored.
function Calculatro.read_odds()
    local f = io.open(save_path('calculatro_odds.json'), 'rb')
    if not f then return end
    local raw = f:read('*a')
    f:close()
    if raw == Calculatro.odds_raw then return end
    Calculatro.odds_raw = raw
    local ok, odds = pcall(decode, raw)
    if not ok or type(odds) ~= 'table' or odds.schema ~= Calculatro.odds_schema or not own_id(odds.id) then
        return
    end
    Calculatro.ack_id = odds.id
    if not odds.pending then Calculatro.odds = odds end
end

---------------------------------------------------------------------------
-- Odds panel
---------------------------------------------------------------------------

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

-- What the panel shows, or nil to hide it. Odds for an older state stay up
-- until the new ones arrive, greyed out if that takes longer than `patience`.
function Calculatro.panel_model()
    local odds = Calculatro.odds
    local message, stale
    if not (odds and odds.id == Calculatro.state_id) then
        local waited = Calculatro.clock - Calculatro.changed_at
        if Calculatro.ack_id ~= Calculatro.state_id and waited >= Calculatro.timeout then
            message = 'Waiting for the Calculatro app...'
        elseif waited >= Calculatro.patience then
            message = 'Calculating...'
        end
        stale = message ~= nil or nil
    end
    local model = { targets = array(), cards = array(), message = message, stale = stale, discard = 'Discard' }
    if odds then
        model.targets, model.cards = odds.targets or array(), odds.cards or array()
        if (odds.selected or 0) > 0 then model.discard = 'Discard ' .. odds.selected end
        model.message = message or odds.message
    end
    if #model.targets == 0 and #model.cards == 0 and not model.message then return nil end
    return model
end

local NAME_W, VALUE_W, GAP_W, DISCARD_W = 1.4, 0.75, 0.25, 0.95  -- DISCARD_W fits "Discard 5"
local TITLE_W, COLUMN_GAP = 1.6, 0.2
local MESSAGE_W = 1.5  -- fits "No discards left" and "Calculating..." beside the title
local SCALE = 0.32

local function label(text, colour, scale)
    return { n = G.UIT.T, config = { text = text, scale = scale or SCALE, colour = colour or G.C.UI.TEXT_LIGHT, shadow = true } }
end

local function cell(w, align, node)
    return { n = G.UIT.C, config = { align = align, minw = w, maxw = w }, nodes = { node } }
end

local function blank(w)
    return { n = G.UIT.C, config = { minw = w }, nodes = {} }
end

local function row(nodes, config)
    config = config or {}
    config.align, config.padding = config.align or 'cl', 0.03
    return { n = G.UIT.R, config = config, nodes = nodes }
end

-- The odds rows in order: targets, then the drawn-card queries (under a
-- subheader when there are targets too). Each is {build = fn} or {subheader = true}.
local function entries(model, colours)
    local C = G.C
    local has_keep = #model.targets > 0
    local function value(text, colour) return label(text or '-', model.stale and colours.stale or colour) end
    local out = {}
    for _, t in ipairs(model.targets) do
        local colour = (t.trend == 1 and C.GREEN) or (t.trend == -1 and C.RED) or C.UI.TEXT_LIGHT
        out[#out + 1] = { build = function()
            return row({
                cell(NAME_W, 'cl', label(t.name)),
                cell(VALUE_W, 'cr', value(t.keep, C.UI.TEXT_LIGHT)),
                t.discard and cell(GAP_W, 'cm', label('>', colours.muted)) or blank(GAP_W),
                cell(DISCARD_W, 'cr', value(t.discard, colour)),
            })
        end }
    end
    if #model.cards > 0 and has_keep then
        out[#out + 1] = { subheader = true }
    end
    for _, c in ipairs(model.cards) do
        out[#out + 1] = { build = function()
            local r = { cell(NAME_W, 'cl', label(c.name)) }
            if has_keep then r[#r + 1] = blank(VALUE_W + GAP_W) end
            r[#r + 1] = cell(DISCARD_W, 'cr', value(c.discard, C.UI.TEXT_LIGHT))
            return row(r)
        end }
    end
    return out
end

-- Number of odds rows.
function Calculatro.row_count(model)
    return #entries(model, {})
end

-- The odds rows split into columns of at most `per` rows, as {first, last}
-- spans. A column never ends on the subheader (it belongs with the rows
-- below it), so that one is a row short.
local function split(list, per)
    local spans, i = {}, 1
    while i <= #list do
        local last = math.min(i + per - 1, #list)
        if last > i and list[last].subheader then last = last - 1 end
        spans[#spans + 1] = { i, last }
        i = last + 1
    end
    return spans
end

-- Number of columns with at most `per` rows each.
function Calculatro.column_count(model, per)
    return #split(entries(model, {}), per)
end

-- The inset behind the odds: what BOSS_MAIN is outside boss blinds
-- (Blind:change_colour). BOSS_MAIN itself is the boss's colour, too light
-- behind light text for some (The Club, The Water, The Psychic, ...) and
-- the same hue as the green/red discard values for others.
local function inset_colour()
    local black = G.C.BLACK
    return { black[1] * 0.95, black[2] * 0.95, black[3] * 0.95, black[4] }
end

-- Styled like the HUD sidebar: MAIN frame, BOSS_DARK body, and an inset that
-- stays dark whatever the blind (inset_colour). The odds rows fill columns
-- of at most `per` rows (default: all in one) top to bottom, left to right,
-- each column with its own Keep/Discard header.
--
-- The message (model.message) must not change the panel's size, or the
-- columns would change whenever it comes and goes: beside the odds it goes
-- on the title row, in a space kept for it (at least MESSAGE_W, so the panel
-- may be wider than its columns) whether or not there's a message. Without
-- odds (e.g. waiting for the app), it goes on a row of its own.
function Calculatro.definition(model, per)
    local C = G.C
    -- Solid greys: the game's semi-transparent TEXT_INACTIVE vanishes on some
    -- bosses' panel colours.
    local colours = { muted = C.JOKER_GREY, stale = C.UI.BACKGROUND_INACTIVE }
    local has_keep = #model.targets > 0
    local list = entries(model, colours)
    local spans = split(list, math.max(1, per or #list))

    local function header()
        local nodes = { blank(NAME_W) }
        if has_keep then
            nodes[#nodes + 1] = cell(VALUE_W, 'cr', label('Keep', colours.muted))
            nodes[#nodes + 1] = blank(GAP_W)
        end
        nodes[#nodes + 1] = cell(DISCARD_W, 'cr', label(model.discard, colours.muted))
        return row(nodes)
    end

    local column_nodes = {}
    for _, span in ipairs(spans) do
        local rows = { header() }
        for k = span[1], span[2] do
            local entry = list[k]
            if entry.subheader then
                rows[#rows + 1] = row({ label('Drawn cards', colours.muted, SCALE * 0.85) })
            else
                rows[#rows + 1] = entry.build()
            end
        end
        if #column_nodes > 0 then column_nodes[#column_nodes + 1] = blank(COLUMN_GAP) end
        column_nodes[#column_nodes + 1] = { n = G.UIT.C, config = { align = 'tl', id = 'calculatro_column' }, nodes = rows }
    end

    local count = math.max(1, (#column_nodes + 1) / 2)
    local column_w = NAME_W + (has_keep and VALUE_W + GAP_W or 0) + DISCARD_W
    local width = column_w * count + COLUMN_GAP * (count - 1)
    local title = label('CALCULATRO', C.UI.TEXT_LIGHT, 0.36)
    local message = model.message and label(model.message, colours.muted, SCALE * 0.85)
    local body = {}
    if #list > 0 then
        body[1] = row({
            { n = G.UIT.C, config = { align = 'cl', minw = TITLE_W }, nodes = { title } },
            message and cell(math.max(width - TITLE_W, MESSAGE_W), 'cr', message)
                or blank(math.max(width - TITLE_W, MESSAGE_W)),
        })
        body[2] = { n = G.UIT.R, config = { align = 'cm', padding = 0.06, r = 0.1, colour = inset_colour(), emboss = 0.05, id = 'calculatro_inset' }, nodes = column_nodes }
    else
        body[1] = row({ title })
        body[2] = row({ message }, { align = 'cm' })
    end
    return { n = G.UIT.ROOT, config = { align = 'cm', padding = 0.05, r = 0.1, colour = C.DYN_UI.MAIN }, nodes = {
        { n = G.UIT.R, config = { align = 'cm', padding = 0.08, r = 0.1, colour = C.DYN_UI.BOSS_DARK }, nodes = {
            { n = G.UIT.C, config = { align = 'cm' }, nodes = body },
        } },
    } }
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

local function build_panel(model, per)
    local panel = UIBox{
        definition = Calculatro.definition(model, per),
        config = { align = 'tm', offset = { x = 0, y = -Calculatro.panel_lift() }, major = G.deck, bond = 'Strong', can_collide = false },
    }
    -- 'tm' centres it above the deck; shift it to line up with the deck's
    -- right edge, so extra columns grow leftwards over the empty play area.
    panel.alignment.offset.x = (G.deck.T.w - panel.T.w) / 2
    panel:align_to_major()
    panel:hard_set_VT()
    return panel
end

-- Most columns: a 4th reaches the HUD sidebar. The app limits what can be
-- tracked (overlay.MAX_TRACKED) so 3 columns of 6 rows fit; this is only a
-- safety net.
Calculatro.max_columns = 3

-- Rebuilds the panel when what it shows changes; removes it outside blinds.
-- Fills each column with as many rows as keep it clear of the consumables
-- before starting the next; a box's size is only known once it's built, so it
-- tries all rows in one column, then one fewer per column, and so on.
function Calculatro.refresh_panel()
    local model = Calculatro.visible and Calculatro.in_blind() and Calculatro.panel_model() or nil
    local key = model and encode(model)
    local panel = Calculatro.panel
    if panel and panel.REMOVED then panel = nil end  -- the game removes it with the run
    if panel and key == Calculatro.panel_key and Calculatro.panel_major == G.deck then return end
    if panel then panel:remove() end
    Calculatro.panel, Calculatro.panel_key, Calculatro.panel_major = nil, nil, nil
    if not model then return end

    local max_h = Calculatro.max_panel_height()
    local per = math.max(1, Calculatro.row_count(model))
    panel = build_panel(model, per)
    while max_h and panel.T.h > max_h and per > 1
            and Calculatro.column_count(model, per - 1) <= Calculatro.max_columns do
        panel:remove()
        per = per - 1
        panel = build_panel(model, per)
    end
    Calculatro.panel, Calculatro.panel_key, Calculatro.panel_major = panel, key, G.deck
end

function Calculatro.key_pressed(key)
    if key ~= Calculatro.toggle_key or not Calculatro.in_blind() then return end
    if G.CONTROLLER and G.CONTROLLER.text_input_hook then return end
    Calculatro.visible = not Calculatro.visible
end

---------------------------------------------------------------------------
-- Hooks (see lovely.toml)
---------------------------------------------------------------------------
Calculatro.reported = {}

-- Runs fn, printing its first error only: a broken step must not crash the game.
local function guard(what, fn, ...)
    local ok, err = pcall(fn, ...)
    if not ok and not Calculatro.reported[what] then
        Calculatro.reported[what] = true
        print('[Calculatro] ' .. what .. ' failed: ' .. tostring(err))
    end
end

function Calculatro.update(dt)
    dt = dt or 0
    Calculatro.clock = Calculatro.clock + dt
    Calculatro.elapsed = Calculatro.elapsed + dt
    Calculatro.odds_elapsed = Calculatro.odds_elapsed + dt
    if Calculatro.elapsed >= Calculatro.interval then
        Calculatro.elapsed = 0
        guard('snapshot', Calculatro.write_snapshot)
    end
    if Calculatro.odds_elapsed >= Calculatro.odds_interval then
        Calculatro.odds_elapsed = 0
        guard('reading odds', Calculatro.read_odds)
    end
    guard('panel', Calculatro.refresh_panel)
end

function Calculatro.key_press(key)
    guard('hotkey', Calculatro.key_pressed, key)
end

return Calculatro

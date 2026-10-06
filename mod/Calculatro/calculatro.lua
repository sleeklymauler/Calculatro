-- Calculatro: exports live Balatro game state to calculatro_state.json in the
-- save directory (%AppData%\Balatro) for the Python companion app.

local Calculatro = {
    interval = 0.2,  -- seconds between snapshots
    elapsed = 0,
    last_json = nil,
    schema = 2,
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
-- Output
---------------------------------------------------------------------------
local function write_state(json)
    local dir = love.filesystem.getSaveDirectory()
    local path = dir .. '/calculatro_state.json'
    local tmp = path .. '.tmp'
    local f = io.open(tmp, 'wb')
    if not f then return false end
    f:write(json)
    f:close()
    -- Windows os.rename won't overwrite; the reader retries on a missing file.
    os.remove(path)
    return os.rename(tmp, path)
end

function Calculatro.update(dt)
    Calculatro.elapsed = Calculatro.elapsed + (dt or 0)
    if Calculatro.elapsed < Calculatro.interval then return end
    Calculatro.elapsed = 0

    local ok, err = pcall(function()
        local json = encode(Calculatro.snapshot())
        if json ~= Calculatro.last_json and write_state(json) then
            Calculatro.last_json = json
        end
    end)
    if not ok and not Calculatro.reported_error then
        Calculatro.reported_error = true
        print('[Calculatro] snapshot failed: ' .. tostring(err))
    end
end

return Calculatro

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
    schema = 4,
    odds_schema = 3,
    -- Snapshot ids are '<session>:<seq>'; the app echoes them back with its odds.
    session = string.format('%d-%d', os.time(), math.floor(os.clock() * 1000)),
    seq = 0,
    state_id = nil,  -- id of the last snapshot written
    changed_at = 0,  -- clock when it was written
    ack_id = nil,    -- id the odds file last answered (pending or done)
    odds = nil,      -- last finished odds from this session
    odds_raw = nil,
    visible = true,
    editor = nil,    -- {tab = 'hands' or 'cards', page = n} while the target editor is open
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
-- What's tracked
---------------------------------------------------------------------------

-- The poker hands, in the game's order, and shorter names where the full
-- ones are too long for the panel.
local HANDS = { 'Pair', 'Two Pair', 'Three of a Kind', 'Straight', 'Flush', 'Full House',
                'Four of a Kind', 'Straight Flush', 'Royal Flush', 'Five of a Kind', 'Flush House', 'Flush Five' }
local SHORT_NAMES = { ['Three of a Kind'] = 'Trips', ['Four of a Kind'] = 'Quads',
                      ['Five of a Kind'] = '5 of a Kind', ['Straight Flush'] = 'Str. Flush' }

-- The run's targets: hands (names from HANDS, in that order) and card
-- queries ({query = text for cards.parse_query, label = what the panel
-- shows}). They're kept on G.GAME, so the game saves them with the run and a
-- new run starts without any. The snapshot sends the names and queries to
-- the app, which keys its odds by them.
function Calculatro.tracked()
    G.GAME.calculatro = G.GAME.calculatro or {}
    local t = G.GAME.calculatro
    t.hands, t.cards = t.hands or {}, t.cards or {}
    return t
end

-- The panel fits at most `max_columns` columns (a 4th reaches the HUD
-- sidebar) of ROWS_PER_COLUMN rows under the consumables; target hands, with
-- "Any target" above several, and card queries fill columns of their own.
-- Nothing more can be tracked than fits. Keep in step with
-- overlay.columns_needed.
local ROWS_PER_COLUMN = 6
Calculatro.max_columns = 3

function Calculatro.columns_needed(hands, cards)
    local hand_rows = hands + (hands > 1 and 1 or 0)
    return math.ceil(hand_rows / ROWS_PER_COLUMN) + math.ceil(cards / ROWS_PER_COLUMN)
end

function Calculatro.can_add_hand()
    local t = Calculatro.tracked()
    return Calculatro.columns_needed(#t.hands + 1, #t.cards) <= Calculatro.max_columns
end

function Calculatro.can_add_card()
    local t = Calculatro.tracked()
    return Calculatro.columns_needed(#t.hands, #t.cards + 1) <= Calculatro.max_columns
end

local function set_of(list, field)
    local set = {}
    for _, v in ipairs(list) do set[field and v[field] or v] = true end
    return set
end

function Calculatro.toggle_hand(name)
    local t = Calculatro.tracked()
    local on = set_of(t.hands)
    if on[name] then
        on[name] = nil
    elseif Calculatro.can_add_hand() then
        on[name] = true
    end
    local hands = {}
    for _, h in ipairs(HANDS) do if on[h] then hands[#hands + 1] = h end end
    t.hands = hands
end

function Calculatro.remove_hand(name)
    local hands = Calculatro.tracked().hands
    for i, h in ipairs(hands) do
        if h == name then table.remove(hands, i); return end
    end
end

-- Adds a card query unless it's already tracked or wouldn't fit; returns
-- whether it did.
function Calculatro.add_card(query, label)
    local cards = Calculatro.tracked().cards
    if set_of(cards, 'query')[query] or not Calculatro.can_add_card() then return false end
    cards[#cards + 1] = { query = query, label = label }
    return true
end

function Calculatro.remove_card(query)
    local cards = Calculatro.tracked().cards
    for i, c in ipairs(cards) do
        if c.query == query then table.remove(cards, i); return end
    end
end

---------------------------------------------------------------------------
-- Card query builder: option cycles for the count, rank, suit and a
-- special (enhancement, edition or seal), building a query that
-- cards.parse_query accepts, so the app stays the only parser, and its
-- label, as the app would name it ("Lucky Q♠: 2+"; the panel draws the
-- suit symbols as icons).
---------------------------------------------------------------------------
local SUIT_SYMBOLS = { Spades = '\226\153\160', Hearts = '\226\153\165',
                       Clubs = '\226\153\163', Diamonds = '\226\153\166' }

local function build_options()
    local any = { text = 'Any' }
    local ranks = { any }
    for r = 2, 10 do
        ranks[#ranks + 1] = { text = tostring(r), query = tostring(r), name = tostring(r), short = r == 10 and 'T' or tostring(r) }
    end
    for _, name in ipairs({ 'Jack', 'Queen', 'King', 'Ace' }) do
        ranks[#ranks + 1] = { text = name, query = name:lower(), name = name, short = name:sub(1, 1) }
    end
    -- A face card can't have a suit in the query language.
    ranks[#ranks + 1] = { text = 'Face', query = 'face', name = 'Face card', face = true }

    local suits = { any }
    for _, suit in ipairs({ 'Spades', 'Hearts', 'Clubs', 'Diamonds' }) do
        suits[#suits + 1] = { text = suit, query = suit:lower(), name = SUIT_SYMBOLS[suit] }
    end

    local specials = { any }
    local function special(text, query, name)
        specials[#specials + 1] = { text = text, query = query, name = name or text }
    end
    for _, e in ipairs({ 'Bonus', 'Mult', 'Wild', 'Glass', 'Steel', 'Stone', 'Gold', 'Lucky' }) do
        special(e, e:lower())
    end
    special('Enhanced', 'enhanced')
    for _, e in ipairs({ 'Foil', 'Holographic', 'Polychrome', 'Negative' }) do special(e, e:lower()) end
    special('Any edition', 'edition', 'Edition')
    for _, s in ipairs({ 'Red', 'Blue', 'Gold', 'Purple' }) do special(s .. ' Seal', s:lower() .. ' seal') end
    special('Sealed', 'sealed')

    return {
        cmp = {
            -- "at least 0" is always true, and so is "at most 8" with 8 or fewer drawn.
            { text = 'at least', query = 'at least', min = 1, max = 8 },
            { text = 'exactly', query = 'exactly', min = 0, max = 8 },
            { text = 'at most', query = 'at most', min = 0, max = 7 },
        },
        rank = ranks,
        suit = suits,
        special = specials,
    }
end
local OPTIONS = build_options()
Calculatro.OPTIONS = OPTIONS

-- The builder's choices, as indexes into OPTIONS (count is the number).
Calculatro.builder = { cmp = 1, count = 1, rank = 1, suit = 1, special = 1 }

-- The chosen rank, suit and special; the suit is Any with a face card.
local function builder_choice()
    local b = Calculatro.builder
    local rank = OPTIONS.rank[b.rank]
    local suit = rank.face and OPTIONS.suit[1] or OPTIONS.suit[b.suit]
    return OPTIONS.cmp[b.cmp], rank, suit, OPTIONS.special[b.special]
end

-- The query and label the builder makes; nil while rank, suit and special
-- are all Any, which every card matches.
function Calculatro.builder_query()
    local cmp, rank, suit, special = builder_choice()
    local n = Calculatro.builder.count
    local words, name = {}, nil
    if rank.query and suit.query then
        words[1], name = rank.query .. ' of ' .. suit.query, rank.short .. suit.name
    elseif rank.query or suit.query then
        local base = rank.query and rank or suit
        words[1], name = base.query, base.name
    end
    if special.query then
        table.insert(words, 1, special.query)
        name = special.name .. ' ' .. (name or 'card')
    end
    if not name then return nil end
    local count = (n == 0 and 'none') or (cmp.query == 'at least' and n .. '+')
        or (cmp.query == 'at most' and '0-' .. n) or tostring(n)
    return cmp.query .. ' ' .. n .. ' ' .. table.concat(words, ' '), name .. ': ' .. count
end

-- Steps one of the builder's cycles (cmp, count, rank, suit, special) by
-- `dir`, wrapping around. The count stays in its comparison's range.
function Calculatro.cycle(field, dir)
    local b = Calculatro.builder
    local cmp = OPTIONS.cmp[b.cmp]
    if field == 'count' then
        b.count = b.count + dir
        if b.count > cmp.max then b.count = cmp.min elseif b.count < cmp.min then b.count = cmp.max end
        return
    end
    b[field] = (b[field] - 1 + dir) % #OPTIONS[field] + 1
    cmp = OPTIONS.cmp[b.cmp]
    b.count = math.max(cmp.min, math.min(cmp.max, b.count))
end

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
    local t = Calculatro.tracked()
    local tracked = { hands = array(), cards = array() }
    for i, h in ipairs(t.hands) do tracked.hands[i] = h end
    for i, c in ipairs(t.cards) do tracked.cards[i] = c.query end
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
        tracked = tracked,
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

-- What the odds panel shows: a row for each tracked target hand ("Any
-- target" first when there are several) and card query, with the odds the
-- app keyed by them ('-' until it has). Odds for an older state stay up until
-- the new ones arrive, greyed out if that takes longer than `patience`.
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
        if Calculatro.ack_id ~= Calculatro.state_id and waited >= Calculatro.timeout then
            model.message = 'Waiting for app'
        elseif waited >= Calculatro.patience then
            model.message = 'Calculating...'
        end
        model.stale = model.message ~= nil or nil
    end
    local hand_odds, card_odds = {}, {}
    if odds then
        model.message = model.message or odds.message
        for _, o in ipairs(odds.targets or {}) do hand_odds[o.key] = o end
        for _, o in ipairs(odds.cards or {}) do card_odds[o.key] = o end
    end
    local function hand(key, name, removable)
        local o = hand_odds[key] or {}
        model.targets[#model.targets + 1] = { key = key, name = name, keep = o.keep, discard = o.discard,
                                              trend = o.trend, removable = removable }
    end
    if #t.hands > 1 then hand('Any target', 'Any target', nil) end
    for _, h in ipairs(t.hands) do hand(h, SHORT_NAMES[h] or h, true) end
    for _, c in ipairs(t.cards) do
        model.cards[#model.cards + 1] = { key = c.query, name = c.label, discard = (card_odds[c.query] or {}).discard }
    end
    return model
end

local NAME_W, VALUE_W, GAP_W, DISCARD_W = 1.4, 0.75, 0.25, 0.95  -- DISCARD_W fits "Discard"
-- Card query rows have no Keep value, so their names also take its place:
-- labels like "Lucky card: 1+" are too long for NAME_W, and text wider than
-- its cell is shrunk. Card-only columns are as wide as target columns too.
local CARD_NAME_W = NAME_W + VALUE_W + GAP_W
local REMOVE_W = 0.3  -- each row ends with an x button that stops tracking it
local COLUMN_W = CARD_NAME_W + DISCARD_W + REMOVE_W
local TITLE_W, PLUS_W, COLUMN_GAP = 1.6, 0.35, 0.2
-- Fits "No discards left", "Calculating..." and "Waiting for app" beside the
-- title and its + button.
local MESSAGE_W = 1.5
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

local function suit_icon(x)
    local atlas = G.ASSET_ATLAS['ui_' .. (G.SETTINGS.colourblind_option and 2 or 1)]
    local sprite = Sprite(0, 0, ICON_SIZE, ICON_SIZE, atlas, { x = x, y = 1 })
    sprite.states.drag.can, sprite.states.hover.can, sprite.states.collide.can = false, false, false
    local tile = { n = G.UIT.C, config = { align = 'cm', padding = ICON_PAD, r = 0.03, colour = G.C.WHITE }, nodes = {
        { n = G.UIT.O, config = { object = sprite, can_collide = false } },
    } }
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

-- The arrow between Keep and Discard, drawn in boxes the size of the font's
-- pixels (m6x11 at SCALE: 64 of its 1024 units per em, rendered at 200 px
-- per em): a 2-pixel shaft like its '-' and a stepped head, 9 by 8 pixels.
-- A text "->" is 13 pixels wide, too wide for GAP_W, so it would be shrunk;
-- this fits with room either side and is shorter than the digits beside it.
local FONT_PX = 200 * 64 / 1024 * SCALE * 0.1 / 20
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

-- A button in the game's style. Clicking it runs Calculatro.act(action);
-- without an action it's inert and drawn in `off_colour` (default: dark).
-- opts: id, w and h (its least size; also its widest, so wider text shrinks),
-- align, colour, off_colour, outline (white, for the chosen tab).
local function button(nodes, action, opts)
    local enabled = action ~= nil
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

-- The odds rows in sections, each filling columns of its own: the target
-- hands (kind 'hands'), then the drawn-card queries ('cards'). Empty
-- sections are left out. Each row is a function building it.
local function sections(model, colours)
    local C = G.C
    local function value(text, colour) return label(text or '-', model.stale and colours.stale or colour) end
    local hands, cards = {}, {}
    for _, t in ipairs(model.targets) do
        local colour = (t.trend == 1 and C.GREEN) or (t.trend == -1 and C.RED) or C.UI.TEXT_LIGHT
        hands[#hands + 1] = function()
            return row({
                cell(NAME_W, 'cl', label(t.name)),
                cell(VALUE_W, 'cr', value(t.keep, C.UI.TEXT_LIGHT)),
                t.discard and arrow(colours.muted) or blank(ARROW_CELL_W),
                cell(DISCARD_VALUE_W, 'cr', value(t.discard, colour)),
                t.removable and remove_cell(t.key, { 'remove_hand', t.key }) or blank(REMOVE_W),
            })
        end
    end
    for _, c in ipairs(model.cards) do
        cards[#cards + 1] = function()
            return row({
                cell(CARD_NAME_W, 'cl', name_label(c.name)),
                cell(DISCARD_W, 'cr', value(c.discard, C.UI.TEXT_LIGHT)),
                remove_cell(c.key, { 'remove_card', c.key }),
            })
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

-- The inset behind the odds: what BOSS_MAIN is outside boss blinds
-- (Blind:change_colour). BOSS_MAIN itself is the boss's colour, too light
-- behind light text for some (The Club, The Water, The Psychic, ...) and
-- the same hue as the green/red discard values for others.
local function inset_colour()
    local black = G.C.BLACK
    return { black[1] * 0.95, black[2] * 0.95, black[3] * 0.95, black[4] }
end

local TITLE_SCALE = 0.36

local function title()
    return { n = G.UIT.C, config = { align = 'cl', minw = TITLE_W }, nodes = {
        label('CALCULATRO', G.C.UI.TEXT_LIGHT, TITLE_SCALE),
    } }
end

-- The frame around the panel's rows, styled like the HUD sidebar: MAIN
-- frame and BOSS_DARK body.
local function frame(body)
    local C = G.C
    return { n = G.UIT.ROOT, config = { align = 'cm', padding = 0.05, r = 0.1, colour = C.DYN_UI.MAIN }, nodes = {
        { n = G.UIT.R, config = { align = 'cm', padding = 0.08, r = 0.1, colour = C.DYN_UI.BOSS_DARK }, nodes = {
            { n = G.UIT.C, config = { align = 'cm' }, nodes = body },
        } },
    } }
end

-- The dark inset behind the odds or the editor's contents (inset_colour,
-- whatever the blind).
local function inset(nodes, minw)
    return { n = G.UIT.R, config = { align = 'cm', padding = 0.06, r = 0.1, colour = inset_colour(), emboss = 0.05,
                                     minw = minw, id = 'calculatro_inset' }, nodes = nodes }
end

-- The odds panel: frame (see frame) and an inset with the odds rows, which
-- fill columns of at most `per` rows (default: each section in one) top to
-- bottom, left to right, target hands and drawn cards in columns of their
-- own, each with its section's header ("Hands  Keep  Discard" or "Drawn
-- cards  Discard"). The + beside the title opens the target editor; it's
-- greyed out and inert once nothing more fits (model.can_add).
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
    local spans = split(list, math.max(1, per or Calculatro.row_count(model)))

    local function header(kind)
        local discard = cell(DISCARD_W, 'cr', label('Discard', colours.muted))
        if kind == 'cards' then
            return row({ cell(CARD_NAME_W, 'cl', label('Drawn cards', colours.muted)), discard, blank(REMOVE_W) })
        end
        return row({
            cell(NAME_W, 'cl', label('Hands', colours.muted)),
            cell(VALUE_W, 'cr', label('Keep', colours.muted)),
            blank(GAP_W),
            discard,
            blank(REMOVE_W),
        })
    end

    local column_nodes = {}
    for _, span in ipairs(spans) do
        local rows = { header(span.section.kind) }
        for k = span.first, span.last do
            rows[#rows + 1] = span.section.rows[k]()
        end
        if #column_nodes > 0 then column_nodes[#column_nodes + 1] = blank(COLUMN_GAP) end
        column_nodes[#column_nodes + 1] = { n = G.UIT.C, config = { align = 'tl', id = 'calculatro_column' }, nodes = rows }
    end

    local count = math.max(1, (#column_nodes + 1) / 2)
    local width = COLUMN_W * count + COLUMN_GAP * (count - 1)
    local plus = { n = G.UIT.C, config = { align = 'cl', minw = PLUS_W }, nodes = {
        button({ plain('+', model.can_add and C.UI.TEXT_LIGHT or colours.muted, TITLE_SCALE) },
               model.can_add and { 'open' } or nil,
               { id = 'calculatro_open', w = 0.3, colour = C.BLUE, off_colour = C.UI.BACKGROUND_INACTIVE }),
    } }
    local message = model.message and label(model.message, colours.muted, SCALE * 0.85)
    local body = {}
    if #list > 0 then
        local message_w = math.max(width - TITLE_W - PLUS_W, MESSAGE_W)
        body[1] = row({ title(), plus, message and cell(message_w, 'cr', message) or blank(message_w) })
        body[2] = inset(column_nodes)
    else
        body[1] = row({ title(), plus })
        body[2] = row({ message }, { align = 'cm' })
    end
    return frame(body)
end

---------------------------------------------------------------------------
-- Target editor: replaces the odds in the panel while it's open (`+` opens
-- it, Done closes it). The Hands tab toggles target hands; the Cards tab
-- builds card queries on the left and lists the tracked ones on the right,
-- CARDS_PER_PAGE to a page (Balatro's UI can't clip a scrolling list).
---------------------------------------------------------------------------
local CARDS_PER_PAGE = 5
local EDITOR_W = 6.4                       -- the inset's width, the same on both tabs
local TAB_W, DONE_W = 1.2, 0.8
local TOGGLE_W, TOGGLE_GAP = 1.9, 0.25     -- the Hands tab's hand toggles
local FIELD_W = 0.8                        -- the builder's row names ("Special")
local CYCLE_ARROW_W, CYCLE_GAP = 0.25, 0.03
local OPTION_W = 1.6                       -- a cycle's value ("Holographic")
local CMP_W, COUNT_W, COUNT_GAP = 0.95, 0.25, 0.05
local BUILDER_GAP = 0.3                    -- between the builder and the tracked list
local PAGE_W = 1.0

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
    local cmp, rank, suit, special = builder_choice()
    local query, name = Calculatro.builder_query()
    model.builder = {
        cmp = cmp.text, count = tostring(Calculatro.builder.count), rank = rank.text, suit = suit.text,
        special = special.text, suit_off = rank.face or nil, add = name,
        can_add = (query and not set_of(t.cards, 'query')[query] and Calculatro.can_add_card()) or nil,
    }
    local pages = math.max(1, math.ceil(#t.cards / CARDS_PER_PAGE))
    editor.page = math.max(1, math.min(editor.page or 1, pages))
    model.page, model.pages, model.cards = editor.page, pages, array()
    for i = (editor.page - 1) * CARDS_PER_PAGE + 1, math.min(#t.cards, editor.page * CARDS_PER_PAGE) do
        model.cards[#model.cards + 1] = { key = t.cards[i].query, name = t.cards[i].label }
    end
    return model
end

-- An option cycle in the game's style: '<' value '>'.
local function cycle(id, text, w, prev, next)
    local C = G.C
    local text_colour = prev and C.UI.TEXT_LIGHT or C.UI.BACKGROUND_INACTIVE
    local function step(symbol, action, suffix)
        return button({ plain(symbol, text_colour) }, action, { id = id .. suffix, w = CYCLE_ARROW_W, colour = C.RED })
    end
    return { n = G.UIT.C, config = { align = 'cm', padding = 0 }, nodes = {
        step('<', prev, '_prev'),
        blank(CYCLE_GAP),
        { n = G.UIT.C, config = { id = id, align = 'cm', minw = w, maxw = w, r = 0.06, colour = C.L_BLACK }, nodes = {
            plain(text, text_colour),
        } },
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
    for c = 0, 1 do
        local rows = {}
        for r = 1, #HANDS / 2 do
            local h = model.hands[c * #HANDS / 2 + r]
            local full = h.state == 'full'
            rows[r] = row({ button(
                { blank(0.1), plain(h.name, full and C.UI.BACKGROUND_INACTIVE or C.UI.TEXT_LIGHT) },
                not full and { 'toggle_hand', h.key } or nil,
                { id = 'calculatro_hand_' .. h.key, w = TOGGLE_W, align = 'cl',
                  colour = h.state == 'on' and C.BLUE or C.L_BLACK }) })
        end
        if c > 0 then columns[#columns + 1] = blank(TOGGLE_GAP) end
        columns[#columns + 1] = { n = G.UIT.C, config = { align = 'tl' }, nodes = rows }
    end
    return columns
end

local function cards_tab(model)
    local C = G.C
    local b = model.builder
    local function field(name, nodes)
        table.insert(nodes, 1, cell(FIELD_W, 'cl', label(name, C.JOKER_GREY)))
        return row(nodes)
    end
    local add_text = b.can_add and C.UI.TEXT_LIGHT or C.UI.BACKGROUND_INACTIVE
    local builder = {
        field('Count', { builder_cycle('cmp', b.cmp, CMP_W, true), blank(COUNT_GAP),
                         builder_cycle('count', b.count, COUNT_W, true) }),
        field('Rank', { builder_cycle('rank', b.rank, OPTION_W, true) }),
        field('Suit', { builder_cycle('suit', b.suit, OPTION_W, not b.suit_off) }),
        field('Special', { builder_cycle('special', b.special, OPTION_W, true) }),
        -- The whole row's width: labels like "Add  Holographic Q♠: 0-7" are long.
        row({ button(
            { name_label(b.add and 'Add  ' .. b.add or 'Add', function(text) return plain(text, add_text) end) },
            b.can_add and { 'add_card' } or nil,
            { id = 'calculatro_add', w = FIELD_W + OPTION_W + 2 * (CYCLE_ARROW_W + CYCLE_GAP) + 0.03,
              colour = C.GREEN }) }),
    }

    local list = {}
    for _, c in ipairs(model.cards) do
        list[#list + 1] = row({ cell(CARD_NAME_W, 'cl', name_label(c.name)), remove_cell(c.key, { 'remove_card', c.key }) })
    end
    if #list == 0 then list[1] = row({ label('Nothing tracked', C.JOKER_GREY) }) end
    if model.pages > 1 then
        list[#list + 1] = row({ cycle('calculatro_page', 'Page ' .. model.page .. '/' .. model.pages, PAGE_W,
                                      { 'page', -1 }, { 'page', 1 }) }, { align = 'cm' })
    end
    return {
        { n = G.UIT.C, config = { align = 'tl' }, nodes = builder },
        blank(BUILDER_GAP),
        { n = G.UIT.C, config = { align = 'tl' }, nodes = list },
    }
end

-- The editor, in the same frame as the odds: the title with Done, the
-- Hands | Cards tabs, and the tab's contents in the inset. It's no taller
-- than the odds panel with full columns: the tabs take the column headers'
-- place, and its rows are as tall as the odds rows.
function Calculatro.editor_definition(model)
    local C = G.C
    local function tab(text, name)
        local chosen = model.tab == name
        return button({ plain(text) }, not chosen and { 'tab', name } or nil,
                      { id = 'calculatro_tab_' .. name, w = TAB_W, colour = C.GREY, off_colour = C.RED,
                        outline = chosen and 1 or nil })
    end
    return frame({
        row({
            title(),
            { n = G.UIT.C, config = { align = 'cr', minw = EDITOR_W - TITLE_W }, nodes = {
                button({ plain('Done') }, { 'done' }, { id = 'calculatro_done', w = DONE_W, colour = C.ORANGE }),
            } },
        }),
        row({ tab('Hands', 'hands'), blank(0.1), tab('Cards', 'cards') }, { align = 'cm' }),
        inset(model.tab == 'hands' and hands_tab(model) or cards_tab(model), EDITOR_W),
    })
end

-- What the panel's buttons do: Calculatro.act{name, argument}.
Calculatro.actions = {
    open = function() Calculatro.editor = { tab = 'hands', page = 1 } end,
    done = function() Calculatro.editor = nil end,
    tab = function(tab) Calculatro.editor.tab = tab end,
    toggle_hand = function(name) Calculatro.toggle_hand(name) end,
    remove_hand = function(name) Calculatro.remove_hand(name) end,
    remove_card = function(query) Calculatro.remove_card(query) end,
    cycle = function(arg) Calculatro.cycle(arg[1], arg[2]) end,
    -- Shows the page it's added to.
    add_card = function()
        local query, name = Calculatro.builder_query()
        if query and Calculatro.add_card(query, name) and Calculatro.editor then
            Calculatro.editor.page = math.ceil(#Calculatro.tracked().cards / CARDS_PER_PAGE)
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
    if (action[1] == 'tab' or action[1] == 'page') and not Calculatro.editor then return end
    Calculatro.actions[action[1]](action[2])
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
-- changes, and the editor only by its own buttons.
local function build_panel(definition)
    local panel = UIBox{
        definition = definition,
        config = { align = 'tm', offset = { x = 0, y = -Calculatro.panel_lift() }, major = G.deck, bond = 'Strong', can_collide = true },
    }
    -- 'tm' centres it above the deck; shift it to line up with the deck's
    -- right edge, so extra columns grow leftwards over the empty play area.
    panel.alignment.offset.x = (G.deck.T.w - panel.T.w) / 2
    panel:align_to_major()
    panel:hard_set_VT()
    if panel.UIRoot then deepen_shadows(panel.UIRoot) end
    return panel
end

-- Rebuilds the panel when what it shows changes; removes it outside blinds,
-- closing the editor. The odds fill each column with as many rows as keep it
-- clear of the consumables before starting the next; a box's size is only
-- known once it's built, so it tries each section in one column, then one
-- fewer row per column, and so on.
function Calculatro.refresh_panel()
    if G.FUNCS then G.FUNCS.calculatro_button = Calculatro.button_pressed end
    local shown = Calculatro.visible and Calculatro.in_blind()
    if not shown then Calculatro.editor = nil end
    local model = shown and (Calculatro.editor and Calculatro.editor_model() or Calculatro.panel_model()) or nil
    local key = model and encode(model)
    local panel = Calculatro.panel
    if panel and panel.REMOVED then panel = nil end  -- the game removes it with the run
    if panel and key == Calculatro.panel_key and Calculatro.panel_major == G.deck then return end
    if panel then panel:remove() end
    Calculatro.panel, Calculatro.panel_key, Calculatro.panel_major = nil, nil, nil
    if not model then return end

    if model.editor then
        Calculatro.panel, Calculatro.panel_key, Calculatro.panel_major =
            build_panel(Calculatro.editor_definition(model)), key, G.deck
        return
    end
    local max_h = Calculatro.max_panel_height()
    local per = math.max(1, Calculatro.row_count(model))
    panel = build_panel(Calculatro.definition(model, per))
    while max_h and panel.T.h > max_h and per > 1
            and Calculatro.column_count(model, per - 1) <= Calculatro.max_columns do
        panel:remove()
        per = per - 1
        panel = build_panel(Calculatro.definition(model, per))
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

-- G.FUNCS.calculatro_button, run by the panel's buttons (see `button`).
function Calculatro.button_pressed(e)
    guard('button', Calculatro.act, e.config.calculatro_action)
end

return Calculatro

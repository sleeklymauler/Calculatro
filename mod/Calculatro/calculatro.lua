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
    schema = 11,
    odds_schema = 5,
    -- Snapshot ids are '<session>:<seq>'; the app echoes them back with its odds.
    session = string.format('%d-%d', os.time(), math.floor(os.clock() * 1000)),
    seq = 0,
    state_id = nil,  -- id of the last snapshot written
    changed_at = 0,  -- clock when it was written
    ack_id = nil,    -- id the odds file last answered (pending or done)
    app_started = false,  -- whether Windows started the app, the last time the mod tried
    restarts = 0,    -- times the app was started again since it last answered
    silent_since = 0,  -- clock at its last answer or start, whichever is later
    odds = nil,      -- last finished odds from this session and round
    round = nil,     -- G.GAME.round, and Calculatro.seq when it last changed
    round_seq = 0,
    odds_raw = nil,
    visible = true,
    editor = nil,    -- {tab = 'hands' or 'cards', page = n} while the target editor is open
    editor_tab = 'hands',  -- the tab last shown, which the editor reopens on
    -- Best-discard searches (see "Best discard"): the latest asked for, sent
    -- with every snapshot; the one waiting for the app's reply; and the
    -- options the last reply found, which the search button cycles through.
    search_nonce = 0,
    request = nil,   -- {key, nonce}, until it's answered or dropped
    searching = nil, -- {key, nonce, hand = the hand's cards when it was asked for}
    found = nil,     -- {key, nonce, options = {{hand indices, 0-based}, ...}, index, hand}
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
                'Four of a Kind', 'Straight Flush', 'Five of a Kind', 'Flush House', 'Flush Five' }
local IS_HAND = {}
for _, h in ipairs(HANDS) do IS_HAND[h] = true end
local SHORT_NAMES = { ['Three of a Kind'] = 'Trips', ['Four of a Kind'] = 'Quads',
                      ['Five of a Kind'] = '5 of a Kind', ['Straight Flush'] = 'Str. Flush' }

-- The run's targets: hands (names from HANDS, in that order) and card
-- queries ({query = text for cards.parse_query, label = what the panel
-- shows}). They're kept on G.GAME, so the game saves them with the run and a
-- new run starts without any. The snapshot sends the names and queries to
-- the app, which keys its odds by them. Hands no longer offered (Royal
-- Flush, in runs saved by older versions) are dropped.
function Calculatro.tracked()
    G.GAME.calculatro = G.GAME.calculatro or {}
    local t = G.GAME.calculatro
    t.hands, t.cards = t.hands or {}, t.cards or {}
    for _, h in ipairs(t.hands) do
        if not IS_HAND[h] then
            local hands = {}
            for _, k in ipairs(t.hands) do if IS_HAND[k] then hands[#hands + 1] = k end end
            t.hands = hands
            break
        end
    end
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
-- whether it did. `about` (optional) explains it in its popup.
function Calculatro.add_card(query, label, about)
    local cards = Calculatro.tracked().cards
    if set_of(cards, 'query')[query] or not Calculatro.can_add_card() then return false end
    cards[#cards + 1] = { query = query, label = label, about = about }
    return true
end

-- Puts a card query in the place of a tracked one, `old`, unless it's
-- tracked already; returns whether it did.
function Calculatro.replace_card(old, query, label, about)
    local cards = Calculatro.tracked().cards
    if query ~= old and set_of(cards, 'query')[query] then return false end
    for i, c in ipairs(cards) do
        if c.query == old then
            cards[i] = { query = query, label = label, about = about }
            return true
        end
    end
    return false
end

function Calculatro.remove_card(query)
    local cards = Calculatro.tracked().cards
    for i, c in ipairs(cards) do
        if c.query == query then table.remove(cards, i); return end
    end
end

---------------------------------------------------------------------------
-- Card query builder: the count (a comparison and a number, or a range),
-- rank chips with presets, suit chips, and a special (enhancement, edition
-- or seal) that `not` can negate. It builds a query that cards.parse_query
-- accepts, so the app stays the only parser, and its label, as the app
-- would name it ("No Seal Q/K: 1-3"; the panel draws the suit symbols as
-- icons). Ranks and suits are ORed within themselves; none or all of them
-- means any.
---------------------------------------------------------------------------
local SUIT_SYMBOLS = { Spades = '\226\153\160', Hearts = '\226\153\165',
                       Clubs = '\226\153\163', Diamonds = '\226\153\166' }
local SUITS = { 'Spades', 'Hearts', 'Clubs', 'Diamonds' }

-- By rank id: its word in queries, its name alone ("Queen"), before a suit
-- ("Q♠") or among others ("Q/K"), and its chip's text.
local RANKS = {}
for r = 2, 10 do
    RANKS[r] = { query = tostring(r), name = tostring(r), short = r == 10 and 'T' or tostring(r), chip = tostring(r) }
end
for i, name in ipairs({ 'Jack', 'Queen', 'King', 'Ace' }) do
    RANKS[10 + i] = { query = name:lower(), name = name, short = name:sub(1, 1), chip = name:sub(1, 1) }
end

local function build_options()
    local specials = { { text = 'Any' } }
    local function special(text, query, name)
        specials[#specials + 1] = { text = text, query = query, name = name or text }
    end
    for _, e in ipairs({ 'Bonus', 'Mult', 'Wild', 'Glass', 'Steel', 'Stone', 'Gold', 'Lucky' }) do
        special(e, e:lower())
    end
    special('Enhanced', 'enhanced')
    for _, e in ipairs({ { 'Foil' }, { 'Holographic', 'Holo' }, { 'Polychrome', 'Poly' }, { 'Negative' } }) do
        special(e[1], e[1]:lower(), e[2])
    end
    special('Any edition', 'edition', 'Edition')
    for _, s in ipairs({ 'Red', 'Blue', 'Gold', 'Purple' }) do special(s .. ' Seal', s:lower() .. ' seal') end
    special('Sealed', 'sealed')

    return {
        cmp = {
            -- "at least 0" is always true, and so is "at most 8" with 8 or fewer in hand.
            { text = 'at least', query = 'at least', min = 1, max = 8 },
            { text = 'exactly', query = 'exactly', min = 0, max = 8 },
            { text = 'at most', query = 'at most', min = 0, max = 7 },
            -- From the count to `high`, which is above it and at most 8. From 0 it
            -- would be "at most".
            { text = 'between', query = 'between', min = 1, max = 7 },
        },
        -- Sets of ranks the rank cycle sets the chips to, lowest first. Those
        -- with a query are groups the app knows by name, which it matches as
        -- the game does (face cards by Card:is_face); the others are spelt out.
        preset = {
            { text = 'Any', ranks = {} },
            { text = 'Face', ranks = { 11, 12, 13 }, query = 'face', name = 'Face card', prefix = 'Face' },
            { text = 'Non-face', ranks = { 2, 3, 4, 5, 6, 7, 8, 9, 10, 14 }, query = 'non-face', name = 'Non-face' },
            { text = 'Even', ranks = { 2, 4, 6, 8, 10 }, query = 'even', name = 'Even' },        -- Even Steven
            { text = 'Odd', ranks = { 3, 5, 7, 9, 14 }, query = 'odd', name = 'Odd' },           -- Odd Todd
            { text = 'Fibonacci', ranks = { 2, 3, 5, 8, 14 }, query = 'fibonacci', name = 'Fib' },
            { text = '2 to 5', ranks = { 2, 3, 4, 5 } },                                         -- Hack
            { text = '10 or 4', ranks = { 4, 10 } },                                             -- Walkie Talkie
        },
        special = specials,
    }
end
local OPTIONS = build_options()
Calculatro.OPTIONS = OPTIONS

local function all_suits()
    local set = {}
    for _, s in ipairs(SUITS) do set[s] = true end
    return set
end

-- The builder's choices: indexes into OPTIONS.cmp and .special, the count
-- (and `high`, a range's top), the chosen ranks and suits as sets, and
-- whether the special is negated.
function Calculatro.reset_builder()
    Calculatro.builder = { cmp = 1, count = 1, high = 2, ranks = {}, suits = all_suits(), special = 1, negate = false }
end
Calculatro.reset_builder()

-- Whether the special is Stone (not negated). Stone Cards have no rank or
-- suit, so ranks and suits then count as any: the chips and the rank cycle
-- are inert and shown as any, but keep their choices for another special.
local function stone_only()
    local b = Calculatro.builder
    return OPTIONS.special[b.special].query == 'stone' and not b.negate
end

-- The chosen ranks, lowest first, and suits, in SUITS order: empty for any.
local function chosen_ranks()
    if stone_only() then return {} end
    local ids = {}
    for r = 2, 14 do
        if Calculatro.builder.ranks[r] then ids[#ids + 1] = r end
    end
    return #ids == 13 and {} or ids
end

local function chosen_suits()
    if stone_only() then return {} end
    local list = {}
    for _, s in ipairs(SUITS) do
        if Calculatro.builder.suits[s] then list[#list + 1] = s end
    end
    return #list == #SUITS and {} or list
end

-- The preset the chosen ranks are, if any.
local function preset_index()
    local ids = chosen_ranks()
    for i, p in ipairs(OPTIONS.preset) do
        local same = #p.ranks == #ids
        for j = 1, #ids do same = same and p.ranks[j] == ids[j] end
        if same then return i end
    end
end

-- What the rank cycle shows: the preset, a single rank, or 'Custom'.
function Calculatro.rank_choice()
    local i, ids = preset_index(), chosen_ranks()
    return (i and OPTIONS.preset[i].text) or (#ids == 1 and RANKS[ids[1]].name) or 'Custom'
end

-- Several ranks, as the app names them: runs of 3 or more as "2-5", the
-- rest joined by "/" ("4/T").
local function ranks_label(ids)
    local parts, i = {}, 1
    while i <= #ids do
        local j = i
        while ids[j + 1] == ids[j] + 1 do j = j + 1 end
        if j - i >= 2 then
            parts[#parts + 1] = RANKS[ids[i]].short .. '-' .. RANKS[ids[j]].short
            i = j + 1
        else
            parts[#parts + 1] = RANKS[ids[i]].short
            i = i + 1
        end
    end
    return table.concat(parts, '/')
end

-- What a negated special is called; the rest are "No <special>".
local NEGATED_NAMES = { Sealed = 'No Seal', Enhanced = 'Plain' }

-- The query and label the builder makes; nil while ranks, suits and special
-- are all any, which every card matches.
function Calculatro.builder_query()
    local b = Calculatro.builder
    local cmp, special = OPTIONS.cmp[b.cmp], OPTIONS.special[b.special]
    local ids, suits = chosen_ranks(), chosen_suits()
    local preset = OPTIONS.preset[preset_index() or 0]
    -- The ranks' query words, and their name alone and before suit symbols.
    local ranks, rank_name, prefix
    if #ids == 1 then
        local r = RANKS[ids[1]]
        ranks, rank_name, prefix = r.query, r.name, r.short
    elseif preset and preset.query then
        ranks, rank_name, prefix = preset.query, preset.name, (preset.prefix or preset.name) .. ' '
    elseif #ids > 1 then
        local words = {}
        for i, r in ipairs(ids) do words[i] = RANKS[r].query end
        ranks, rank_name = table.concat(words, ' or '), ranks_label(ids)
        -- Named by the ranks left out when that's shorter: "not 5/9/K".
        local left_out, chosen = {}, Calculatro.builder.ranks
        for r = 2, 14 do
            if not chosen[r] then left_out[#left_out + 1] = r end
        end
        local excluded = 'not ' .. ranks_label(left_out)
        if #excluded < #rank_name then rank_name = excluded end
        prefix = rank_name .. ' '
    end
    local words, name
    if #suits > 0 then
        local suit_words, symbols = {}, {}
        for i, s in ipairs(suits) do suit_words[i], symbols[i] = s:lower(), SUIT_SYMBOLS[s] end
        words, name = table.concat(suit_words, ' or '), table.concat(symbols)
        if ranks then words, name = ranks .. ' of ' .. words, prefix .. name end
    elseif ranks then
        words, name = ranks, rank_name
    end
    if special.query then
        local special_name = b.negate and (NEGATED_NAMES[special.name] or 'No ' .. special.name) or special.name
        words = (b.negate and 'not ' or '') .. special.query .. (words and ' ' .. words or '')
        name = special_name .. ' ' .. (name or 'card')
    end
    if not name then return nil end
    local n = b.count
    if cmp.query == 'between' then
        return 'between ' .. n .. ' and ' .. b.high .. ' ' .. words, name .. ': ' .. n .. '-' .. b.high
    end
    local count = (n == 0 and 'none') or (cmp.query == 'at least' and n .. '+')
        or (cmp.query == 'at most' and '0-' .. n) or tostring(n)
    return cmp.query .. ' ' .. n .. ' ' .. words, name .. ': ' .. count
end

-- "a, b or c".
local function either(list)
    if #list < 2 then return list[1] end
    return table.concat(list, ', ', 1, #list - 1) .. ' or ' .. list[#list]
end

-- The chosen ones of `all` (set `chosen`) named by `name`, or those left out
-- when that's shorter: "Q or K", "any but 5, 9 or K".
local function chosen_or_not(all, chosen, name)
    local on, off = {}, {}
    for _, x in ipairs(all) do
        local list = chosen[x] and on or off
        list[#list + 1] = name(x)
    end
    local shown, but = either(on), 'any but ' .. either(off)
    return #but < #shown and but or shown
end

-- What a negated special means, where "not <special>" doesn't say it well.
local NEGATED_ABOUT = { Sealed = 'no seal', Enhanced = 'no enhancement', ['Any edition'] = 'no edition' }

-- What the builder's query means, for its popup: "In hand: between 1 and 3",
-- then whichever of Rank, Suit and Special aren't any, one per line. Labels
-- abbreviate ("Fib", "T", "Plain"); this spells it out.
function Calculatro.builder_about()
    local b = Calculatro.builder
    local cmp, special = OPTIONS.cmp[b.cmp], OPTIONS.special[b.special]
    local lines = { 'In hand: ' .. (cmp.query == 'between' and 'between ' .. b.count .. ' and ' .. b.high
                                  or cmp.text .. ' ' .. b.count) }
    local ids, suits = chosen_ranks(), chosen_suits()
    if #ids > 0 then
        local all = {}
        for r = 2, 14 do all[#all + 1] = r end
        local ranks = chosen_or_not(all, b.ranks, function(r) return #ids == 1 and RANKS[r].name or RANKS[r].chip end)
        local preset = OPTIONS.preset[preset_index() or 0]
        if preset and preset.query then ranks = preset.text .. ' (' .. ranks .. ')' end
        lines[#lines + 1] = 'Rank: ' .. ranks
    end
    if #suits > 0 then
        lines[#lines + 1] = 'Suit: ' .. chosen_or_not(SUITS, b.suits, function(s) return s end)
    end
    if special.query then
        lines[#lines + 1] = 'Special: ' .. (b.negate and (NEGATED_ABOUT[special.text] or 'not ' .. special.text)
                                            or special.text)
    end
    return table.concat(lines, '\n')
end

-- Steps one of the builder's cycles (cmp, count, high, preset, special) by
-- `dir`, wrapping around. The count stays in its comparison's range, and a
-- range's top above its count.
function Calculatro.cycle(field, dir)
    local b = Calculatro.builder
    if field == 'preset' then
        if stone_only() then return end
        -- From ranks that aren't a preset, as if from Any.
        local i = ((preset_index() or 1) - 1 + dir) % #OPTIONS.preset + 1
        b.ranks = {}
        for _, r in ipairs(OPTIONS.preset[i].ranks) do b.ranks[r] = true end
        return
    end
    local cmp = OPTIONS.cmp[b.cmp]
    if field == 'count' then
        b.count = b.count + dir
        if b.count > cmp.max then b.count = cmp.min elseif b.count < cmp.min then b.count = cmp.max end
    elseif field == 'high' then
        b.high = b.high + dir
        if b.high > 8 then b.high = b.count + 1 elseif b.high <= b.count then b.high = 8 end
    else
        b[field] = (b[field] - 1 + dir) % #OPTIONS[field] + 1
        cmp = OPTIONS.cmp[b.cmp]
        b.count = math.max(cmp.min, math.min(cmp.max, b.count))
    end
    b.high = math.max(b.count + 1, math.min(8, b.high))
end

-- The rank and suit chips. Turning off the last suit turns them all back
-- on: no suits means any, as all of them does. Both are inert under Stone.
function Calculatro.toggle_rank(r)
    if stone_only() then return end
    Calculatro.builder.ranks[r] = not Calculatro.builder.ranks[r] or nil
end

function Calculatro.toggle_suit(s)
    if stone_only() then return end
    local b = Calculatro.builder
    b.suits[s] = not b.suits[s] or nil
    if next(b.suits) == nil then b.suits = all_suits() end
end

-- Words of a query part joined by " or " ("queen or king"), as a list.
local function or_words(text)
    local words = {}
    for word in (text .. ' or '):gmatch('(.-) or ') do words[#words + 1] = word end
    return words
end

local RANK_WORDS, SUIT_WORDS = {}, {}
for r = 2, 14 do RANK_WORDS[RANKS[r].query] = { r } end
for _, p in ipairs(OPTIONS.preset) do
    if p.query then RANK_WORDS[p.query] = p.ranks end
end
for _, s in ipairs(SUITS) do SUIT_WORDS[s:lower()] = s end

-- The builder's choices that make `query`, so a tracked query can be edited;
-- nil unless it's one builder_query makes (it must give the query back).
function Calculatro.builder_for(query)
    local b = { cmp = 1, count = 1, high = 2, ranks = {}, suits = {}, special = 1, negate = false }
    local rest
    for i, cmp in ipairs(OPTIONS.cmp) do
        if query:sub(1, #cmp.query + 1) == cmp.query .. ' ' then b.cmp, rest = i, query:sub(#cmp.query + 2) end
    end
    if not rest then return nil end
    local count, high
    if OPTIONS.cmp[b.cmp].query == 'between' then
        count, high, rest = rest:match('^(%d+) and (%d+) (.*)$')
    else
        count, rest = rest:match('^(%d+) (.*)$')
    end
    if not rest then return nil end
    b.count, b.high = tonumber(count), tonumber(high) or math.min(8, tonumber(count) + 1)
    if rest:sub(1, 4) == 'not ' then b.negate, rest = true, rest:sub(5) end
    -- The longest special that starts it: "gold seal" rather than "gold".
    for i, s in ipairs(OPTIONS.special) do
        if s.query and (rest == s.query or rest:sub(1, #s.query + 1) == s.query .. ' ')
                and (b.special == 1 or #s.query > #OPTIONS.special[b.special].query) then
            b.special = i
        end
    end
    if b.special > 1 then rest = rest:sub(#OPTIONS.special[b.special].query + 2) end
    local ranks, suits = rest:match('^(.-) of (.*)$')
    if not ranks and rest ~= '' then
        ranks = rest
        if SUIT_WORDS[or_words(rest)[1]] then ranks, suits = nil, rest end
    end
    for _, word in ipairs(ranks and or_words(ranks) or {}) do
        if not RANK_WORDS[word] then return nil end
        for _, r in ipairs(RANK_WORDS[word]) do b.ranks[r] = true end
    end
    for _, word in ipairs(suits and or_words(suits) or {}) do
        if not SUIT_WORDS[word] then return nil end
        b.suits[SUIT_WORDS[word]] = true
    end
    if next(b.suits) == nil then b.suits = all_suits() end
    local current = Calculatro.builder
    Calculatro.builder = b
    local made = Calculatro.builder_query()
    Calculatro.builder = current
    return made == query and b or nil
end

---------------------------------------------------------------------------
-- Snapshot
---------------------------------------------------------------------------
-- Whether the joker at `i` ends up running DNA: itself, or by Blueprint and
-- Brainstorm copying along a chain (Card:calculate_joker). A debuffed joker does
-- nothing, so it ends the chain, and a loop never gets anywhere.
local function runs_dna(cards, i)
    for _ = 1, #cards do
        local joker = cards[i]
        if not joker or joker.debuff then return false end
        local key = joker.config and joker.config.center_key
        if key == 'j_dna' then return true end
        if key == 'j_blueprint' then
            i = i + 1
        elseif key == 'j_brainstorm' and i ~= 1 then
            i = 1
        else
            return false
        end
    end
    return false
end

-- How many copies of a lone first hand DNA would make. With a joker face down
-- (Amber Acorn) their order is hidden from the player, so it mustn't count here:
-- each active DNA makes one, and Blueprint and Brainstorm are left out.
local function dna_copies()
    local cards = G.jokers and G.jokers.cards or {}
    local face_down = false
    for _, joker in ipairs(cards) do
        face_down = face_down or joker.facing == 'back'
    end
    local copies = 0
    for i, joker in ipairs(cards) do
        local key = joker.config and joker.config.center_key
        local runs
        if face_down then
            runs = key == 'j_dna' and not joker.debuff
        else
            runs = runs_dna(cards, i)
        end
        if runs then copies = copies + 1 end
    end
    return copies
end

-- `in_hand`: also says whether Cerulean Bell forces the card into every
-- play and discard (the player sees it raised and can't unselect it).
local function card_info(card, hide_face_down, in_hand)
    local highlighted = card.highlighted and true or false
    local forced = in_hand and card.ability and card.ability.forced_selection and true or nil
    if hide_face_down and card.facing == 'back' then
        return { hidden = true, highlighted = highlighted, forced = forced }
    end
    local enhancement = card.config and card.config.center_key or nil  -- 'c_base', 'm_stone', 'm_wild', ...
    -- A Stone card shows no rank or suit, so its base card isn't exported.
    local base = enhancement ~= 'm_stone' and card.base or nil
    return {
        rank = base and base.value,   -- '2'..'10', 'Jack', 'Queen', 'King', 'Ace'
        rank_id = base and base.id,   -- 2..14 (Ace = 14)
        suit = base and base.suit,    -- 'Spades', 'Hearts', 'Clubs', 'Diamonds'
        enhancement = enhancement,
        edition = card.edition and card.edition.type or nil,
        seal = card.seal,
        debuff = card.debuff and true or false,
        highlighted = highlighted,
        forced = forced,
    }
end

local function area_cards(area, hide_face_down, in_hand)
    local out = array()
    if area and area.cards then
        for i, card in ipairs(area.cards) do out[i] = card_info(card, hide_face_down, in_hand) end
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

-- The cards the player hasn't seen: the undrawn deck and the face-down cards
-- in hand or discarded (the game doesn't turn those up), merged and sorted, so
-- neither which cards are face down or were discarded face down, nor which
-- were just drawn face down leaks (the game's View Deck counts them together
-- too). The face-down counts are public, from the hand's and the discard
-- pile's `{hidden = true}`.
local function unseen_pool()
    local cards = array()
    for _, area in ipairs({ G.deck, G.hand, G.discard }) do
        for _, card in ipairs(area and area.cards or {}) do
            if area == G.deck or card.facing == 'back' then
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
    -- Sorted like the deck: Amber Acorn flips the jokers face down and
    -- shuffles them, so their order is hidden from the player.
    local jokers = array()
    if G.jokers and G.jokers.cards then
        for i, j in ipairs(G.jokers.cards) do
            -- Perished: debuffed for good, which Crimson Heart can't undo (Card:set_debuff).
            local ability = j.ability or {}
            jokers[i] = { key = j.config and j.config.center_key, debuff = j.debuff and true or false,
                          perished = (ability.perishable and (ability.perish_tally or 0) <= 0) and true or false }
        end
    end
    jokers = sorted_cards(jokers)
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
        blind_disabled = G.GAME.blind and G.GAME.blind.disabled and true or false,
        hands_played = round.hands_played or 0,
        discards_used = round.discards_used or 0,
        hand = area_cards(G.hand, true, true),
        played = area_cards(G.play, true),
        unseen = unseen_pool(),
        discard = area_cards(G.discard, true),
        jokers = jokers,
        dna_copies = dna_copies(),
        tracked = tracked,
        select_limit = G.hand.config.highlighted_limit or 5,
        search = Calculatro.request,
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

-- Odds are for one round's hand: once another round starts (G.GAME.round
-- counts the blinds played), those for snapshots taken before it are dropped,
-- so the panel shows '-' rather than the last round's odds while the first
-- hand is dealt (the app only answers once it is). Snapshots after
-- `round_seq` were taken in this round; checked before each snapshot.
local function this_round(id)
    local seq = own_id(id) and tonumber(id:match(':(%d+)$'))
    return seq and seq > Calculatro.round_seq or false
end

function Calculatro.check_round()
    local round = G and G.GAME and G.GAME.round
    if round == Calculatro.round then return end
    Calculatro.round, Calculatro.round_seq = round, Calculatro.seq
    if Calculatro.odds and not this_round(Calculatro.odds.id) then Calculatro.odds = nil end
end

-- The app answers each snapshot first with {id, pending = true}, then with
-- the odds once they're calculated. Odds left over from an earlier game
-- session or round are ignored.
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
    if not odds.pending and this_round(odds.id) then Calculatro.odds = odds end
    Calculatro.receive_search(odds.search)
end

---------------------------------------------------------------------------
-- Best discard: each target hand's search button asks the app for the
-- discards with the best odds of making it (the request goes with the next
-- snapshot), and selects the first once they arrive. Clicking it again
-- selects the next, as long as the hand and the selection are still the
-- search's. The app's indices are positions in the hand as it was when the
-- search was asked for, so a reply for another hand is dropped.
---------------------------------------------------------------------------
local function hand_cards()
    local cards = {}
    for i, card in ipairs(G.hand.cards) do cards[i] = card end
    return cards
end

local function same_hand(cards)
    local now = G.hand and G.hand.cards
    if not now or #now ~= #cards then return false end
    for i, card in ipairs(now) do
        if card ~= cards[i] then return false end
    end
    return true
end

local function is_selected(option, cards)
    local chosen = {}
    for _, i in ipairs(option) do chosen[cards[i + 1]] = true end
    for _, card in ipairs(cards) do
        if (card.highlighted and true or false) ~= (chosen[card] or false) then return false end
    end
    return true
end

-- Selects the hand cards at `option` (0-based indices into `cards`) and
-- nothing else, as clicking them would, with one click sound. Cerulean
-- Bell's forced card stays selected through unhighlight_all.
function Calculatro.select(option, cards)
    G.hand:unhighlight_all()
    local silent = false
    for _, i in ipairs(option) do
        local card = cards[i + 1]
        if card and not card.highlighted then
            G.hand:add_to_highlighted(card, silent)
            silent = true
        end
    end
end

-- Whether a search can be asked for: while selecting, with a way to throw
-- cards away and draw before the last hand, a discard or a hand played for
-- it with another left after (search.can_redraw).
function Calculatro.can_search()
    local S = G.STATES
    local round = G.GAME and G.GAME.current_round
    local can_redraw = round and ((round.discards_left or 0) > 0 or (round.hands_left or 0) > 1)
    return G.STATE == S.SELECTING_HAND and can_redraw and G.hand and #G.hand.cards > 0 or false
end

-- The search button of the row `key`.
function Calculatro.search(key)
    if not Calculatro.can_search() then return end
    local found = Calculatro.found
    if found and found.key == key and same_hand(found.hand) then
        local current = found.index > 0 and is_selected(found.options[found.index], found.hand)
        found.index = current and found.index % #found.options + 1 or 1
        Calculatro.select(found.options[found.index], found.hand)
        return
    end
    Calculatro.found = nil
    Calculatro.search_nonce = Calculatro.search_nonce + 1
    Calculatro.request = { key = key, nonce = Calculatro.search_nonce }
    Calculatro.searching = { key = key, nonce = Calculatro.search_nonce, hand = hand_cards() }
    Calculatro.elapsed = Calculatro.interval  -- send it with the next frame's snapshot
end

local function same_options(a, b)
    if type(a) ~= 'table' or #a ~= #b then return false end
    for i, option in ipairs(b) do
        if type(a[i]) ~= 'table' or #a[i] ~= #option then return false end
        for j, index in ipairs(option) do
            if a[i][j] ~= index then return false end
        end
    end
    return true
end

-- The app's reply to a search, on every odds payload while it still stands:
-- once one comes without it (or with other options, worked out again), what
-- it was worked out from has changed (a Tarot changed a card, a joker was
-- sold), so its options are dropped and the button asks again. A reply that
-- arrives while the cards can't be selected for a discard (a Tarot is choosing
-- its cards) is kept without selecting anything (index 0), and the button's
-- next click selects its first option.
function Calculatro.receive_search(reply)
    local found = Calculatro.found
    if found and not (type(reply) == 'table' and reply.nonce == found.nonce
                      and same_options(reply.options, found.options)) then
        Calculatro.found = nil
    end
    local searching = Calculatro.searching
    if type(reply) ~= 'table' or not searching or reply.nonce ~= searching.nonce then return end
    Calculatro.searching, Calculatro.request = nil, nil
    local options = reply.options or {}
    if #options == 0 or not same_hand(searching.hand) then return end
    local apply = Calculatro.can_search()
    Calculatro.found = { key = searching.key, nonce = searching.nonce, options = options,
                         index = apply and 1 or 0, hand = searching.hand }
    if apply then Calculatro.select(options[1], searching.hand) end
end

-- A search, or its options, only stand while the hand they're for does. The
-- request goes with the search, so the app doesn't search a hand that's gone.
function Calculatro.check_search()
    if Calculatro.searching and not same_hand(Calculatro.searching.hand) then
        Calculatro.searching, Calculatro.request = nil, nil
    end
    if Calculatro.found and not same_hand(Calculatro.found.hand) then Calculatro.found = nil end
end

-- The title row's word on the search: while it runs, and while the selection
-- is one of its options ("2/3", or "Keep hand" for keeping it).
function Calculatro.search_message()
    if Calculatro.searching then return 'Searching...' end
    local found = Calculatro.found
    if not found or found.index == 0 or not same_hand(found.hand)
        or not is_selected(found.options[found.index], found.hand) then
        return nil
    end
    if #found.options > 1 then return found.index .. '/' .. #found.options end
    if #found.options[found.index] == 0 then return 'Keep hand' end
end

---------------------------------------------------------------------------
-- Starting the app
---------------------------------------------------------------------------
-- install_mod.ps1 leaves the Python interpreter to run (the venv's
-- pythonw.exe) in the mod's launch.txt. Without one, a release's own Python
-- (build_release.ps1 puts it and the app in the mod's python folder) is run if
-- it's there. The app exits with the game (by its pid), and on its own if one
-- is already running, so starting it again when it's there does nothing (see
-- `check_app`).
function Calculatro.launch_command(pid)
    local exe
    local f = io.open(save_path('Mods/Calculatro/launch.txt'), 'rb')
    if f then
        exe = (f:read('*l') or ''):match('^%s*(.-)%s*$')
        f:close()
        if exe == '' then return end
    else
        exe = save_path('Mods/Calculatro/python/pythonw.exe')
        f = io.open(exe, 'rb')
        if not f then return end
        f:close()
        exe = exe:gsub('/', '\\')
    end
    return exe, '-m calculatro watch --game-pid ' .. string.format('%d', pid)
end

-- Through Windows itself rather than os.execute, whose cmd.exe would flash a
-- console window over the game. Returns true if Windows started it, which
-- says nothing about whether it's still running a moment later.
function Calculatro.launch()
    local ok, ffi = pcall(require, 'ffi')
    if not ok then return false end  -- not LuaJIT (the tests)
    if not Calculatro.declared then
        ffi.cdef [[
            unsigned long GetCurrentProcessId(void);
            int MultiByteToWideChar(unsigned int page, unsigned long flags, const char *s, int n,
                                    uint16_t *out, int size);
            void *ShellExecuteW(void *hwnd, const uint16_t *op, const uint16_t *file,
                                const uint16_t *params, const uint16_t *dir, int show);
        ]]
        Calculatro.declared = true
    end
    local exe, params = Calculatro.launch_command(ffi.C.GetCurrentProcessId())
    if not exe then
        error("nothing to start: no launch.txt or python folder in Mods/Calculatro (is that the mod's folder name?)")
    end
    local function wide(s)
        local n = ffi.C.MultiByteToWideChar(65001, 0, s, -1, nil, 0)
        local out = ffi.new('uint16_t[?]', n)
        ffi.C.MultiByteToWideChar(65001, 0, s, -1, out, n)
        return out
    end
    local result = ffi.load('shell32').ShellExecuteW(nil, wide('open'), wide(exe), wide(params), nil, 0)
    local code = tonumber(ffi.cast('intptr_t', result))
    if code <= 32 then
        error(string.format('could not start %s (ShellExecuteW error %d)', exe, code))
    end
    return true
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
        if Calculatro.ack_id ~= Calculatro.state_id and waited >= Calculatro.timeout then
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

local NAME_W, VALUE_W, GAP_W, DISCARD_W = 1.4, 0.75, 0.25, 0.95  -- DISCARD_W fits "Discard"
-- Card query rows have no Keep value (with a full hand and nothing face
-- down, it's 0% or 100%), so their names take its place: labels like "Lucky
-- card: 1+" are too long for NAME_W, and text wider than its cell is shrunk.
-- Card-only columns are as wide as target columns too.
local CARD_NAME_W = NAME_W + VALUE_W + GAP_W
local REMOVE_W = 0.3  -- each row ends with an x button that stops tracking it
local SEARCH_W = 0.3  -- before it, the best-discard search button
local COLUMN_W = CARD_NAME_W + DISCARD_W + SEARCH_W + REMOVE_W
-- Card query rows have a pencil between the two that edits the query; it
-- comes out of their names' width.
local EDIT_W = 0.3
local TITLE_W, PLUS_W, COLUMN_GAP = 1.6, 0.35, 0.2
-- Fits "Calculating..." and "Waiting for app" beside the title and its +
-- button. A column leaves more than this, enough for "App didn't start".
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

-- Set while building a definition only for its values (values_definition):
-- suit icons are then left without their sprite, since the game keeps every
-- sprite made until it's removed, and that definition is never built.
local values_only = false

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
function Calculatro.popup_definition(name, about)
    local C = G.C
    local function line(nodes) return { n = G.UIT.R, config = { align = 'cl', padding = 0.02 }, nodes = nodes } end
    local rows = { line({ name_label(name, function(text) return label(text, C.UI.TEXT_LIGHT, POPUP_SCALE) end) }) }
    for text in (about or ''):gmatch('[^\n]+') do
        local field, value = text:match('^(.-): (.*)$')
        rows[#rows + 1] = line({ label(field .. ': ', C.JOKER_GREY), label(value) })
    end
    return { n = G.UIT.ROOT, config = { align = 'cm', padding = 0.05, r = 0.1, colour = C.DYN_UI.MAIN, emboss = 0.05 }, nodes = {
        { n = G.UIT.C, config = { align = 'cl', padding = 0.08, r = 0.1, colour = inset_colour(), emboss = 0.05 }, nodes = rows },
    } }
end

-- A card query's name in a cell `w` wide, cut to fit (fit_label), that shows
-- its popup while hovered (see hover_popups). `card`: {key, name, about}.
local function card_name_cell(card, w)
    return { n = G.UIT.C, config = { id = 'calculatro_name_' .. card.key, align = 'cl', minw = w, maxw = w,
                                     collideable = true, calculatro_popup = { name = card.name, about = card.about } }, nodes = {
        name_label(Calculatro.fit_label(card.name, w, SCALE)),
    } }
end

-- The arrow between Keep and Discard, drawn in boxes the size of the font's
-- pixels (m6x11 at SCALE: 64 of its 1024 units per em, rendered at 200 px
-- per em): a 2-pixel shaft like its '-' and a stepped head, 9 by 8 pixels.
-- A text "->" is 13 pixels wide, too wide for GAP_W, so it would be shrunk;
-- this fits with room either side and is shorter than the digits beside it.
local FONT_PX = 200 * 64 / 1024 * SCALE * 0.1 / 20
-- A text node's height at SCALE: the game makes it the font's line height
-- (m6x11's em, 200 px) times its TEXT_HEIGHT_SCALE, 0.83.
local TEXT_H = 200 * 0.83 * SCALE * 0.1 / 20
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

-- The odds panel: frame (see frame) and an inset with the odds rows, which
-- fill columns of at most `per` rows (default: each section in one) top to
-- bottom, left to right, target hands and card queries in columns of their
-- own, each with its section's header ("Hands  Keep  Discard" or "In hand
-- Discard"). The + beside the title opens the target editor; it's
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

    local function header(kind, n)
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

    local column_nodes, section_columns = {}, {}
    for _, span in ipairs(spans) do
        local kind = span.section.kind
        section_columns[kind] = (section_columns[kind] or 0) + 1
        local rows = { header(kind, section_columns[kind]) }
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

local function cards_tab(model)
    local C = G.C
    local b = model.builder
    local function field(name, nodes)
        table.insert(nodes, 1, cell(FIELD_W, 'cl', label(name, C.JOKER_GREY)))
        return row(nodes, { calculatro_anim = 'field:' .. name })
    end
    -- A toggle chip: blue while on.
    local function chip(id, nodes, on, action, w)
        return button(nodes, action, { id = id, w = w, h = TEXT_H + 2 * TEXT_PAD, padded = true,
                                       colour = on and C.BLUE or C.L_BLACK })
    end
    local add_text = b.can_add and C.UI.TEXT_LIGHT or C.UI.BACKGROUND_INACTIVE
    local count = { builder_cycle('cmp', b.cmp, CMP_W, true), blank(COUNT_GAP),
                    builder_cycle('count', b.count, COUNT_W, true) }
    if b.high then
        count[#count + 1] = cell(AND_W, 'cm', label('and', C.JOKER_GREY))
        count[#count + 1] = builder_cycle('high', b.high, COUNT_W, true)
    end
    -- The rank chips, under the rank cycle that sets them to its presets.
    -- Under Stone (b.fixed), they and the suit chips are dark and inert.
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
    local builder = {
        field('Count', count),
        field('Rank', { builder_cycle('preset', b.preset, OPTION_W, not b.fixed) }),
        row(ranks, { calculatro_anim = 'field:Ranks' }),
        field('Suit', suits),
        field('Special', { negate, builder_cycle('special', b.special, OPTION_W, true) }),
    }
    local add = button(
        { name_label(Calculatro.fit_label((b.save and 'Save' or 'Add') .. (b.add and '  ' .. b.add or ''), ADD_W, SCALE),
                     function(text) return plain(text, add_text) end) },
        b.can_add and { b.save and 'save_card' or 'add_card' } or nil,
        { id = 'calculatro_add', w = ADD_W, colour = C.GREEN, padded = true })

    local builder_w = b.high and RANGE_ROW_W or CHIPS_W
    local name_w = CARD_NAME_W + BUILDER_W - builder_w - EDIT_W - ROW_PAD
    local list = {}
    local colours = { muted = C.JOKER_GREY }
    for _, c in ipairs(model.cards) do
        list[#list + 1] = row({ card_name_cell(c, name_w), edit_cell(c, colours, c.editing),
                                remove_cell(c.key, { 'remove_card', c.key }) },
                              { calculatro_anim = 'listed:' .. c.key })
    end
    if #list == 0 then list[1] = row({ label('Nothing tracked', C.JOKER_GREY) }, { calculatro_anim = 'listed:none' }) end
    local page = model.pages > 1
        and cycle('calculatro_page', 'Page ' .. model.page .. '/' .. model.pages, PAGE_W, { 'page', -1 }, { 'page', 1 })
    return {
        { n = G.UIT.C, config = { align = 'tl', minh = EDITOR_H }, nodes = {
            { n = G.UIT.R, config = { align = 'tl', padding = 0 }, nodes = {
                { n = G.UIT.C, config = { align = 'tl', minw = builder_w }, nodes = builder },
                blank(BUILDER_GAP),
                { n = G.UIT.C, config = { align = 'tl' }, nodes = list },
            } },
            { n = G.UIT.R, config = { minh = ADD_GAP, padding = 0 }, nodes = {} },
            row({ add, { n = G.UIT.C, config = { align = 'cm', minw = PAGE_CELL_W, calculatro_anim = 'pages' },
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

---------------------------------------------------------------------------
-- Panel animation. The panel is rebuilt whenever what it shows changes, and
-- a new box would just jump to its new layout. Instead, elements tagged
-- `calculatro_anim` with the same key in the old and new box move from where
-- the old one drew them: the frame, its body and the inset morph to their
-- new size (the panel stays anchored by its bottom-right corner, so it grows
-- up and left), and rows glide to their new places. Keyed elements the old
-- box didn't have pop in once the frame has mostly moved, a little apart
-- (POP_STAGGER); untagged elements go with the nearest tagged one around
-- them. A panel that appears pops in whole. Nothing leaves: removed rows
-- vanish, and the rest move into the gap.
--
-- It all happens while drawing, on the visible transforms (VT) the game
-- draws from, which are put back straight after: layout, collision and
-- buttons only ever see the final places. Off with the game's Reduced
-- Motion setting.
---------------------------------------------------------------------------
local MORPH_TIME, POP_DELAY, POP_TIME, POP_STAGGER = 0.25, 0.12, 0.22, 0.025
local MAX_STAGGER = 8  -- pops after this many start together

local function clamp01(t) return math.max(0, math.min(1, t)) end
local function ease_out(t) return 1 - (1 - t) ^ 3 end
-- Overshoots a little before settling, like the game's own pops.
local function ease_out_back(t)
    local c = 1.70158
    return 1 + (c + 1) * (t - 1) ^ 3 + c * (t - 1) ^ 2
end

-- How much of a move is left (1 at its start, 0 when done), and a pop's
-- scale (0 before it starts).
function Calculatro.move_left(move, now)
    return 1 - ease_out(clamp01((now - move.start) / MORPH_TIME))
end

function Calculatro.pop_scale(start, now)
    local t = (now - start) / POP_TIME
    if t <= 0 then return 0 elseif t >= 1 then return 1 end
    return ease_out_back(t)
end

local function ui_children(e)
    local out = {}
    for _, child in ipairs(e.children or {}) do
        if type(child) == 'table' and child.UIT then out[#out + 1] = child end
    end
    return out
end

-- Keyed elements, outermost first, each with the nearest keyed element
-- around it (`parent`).
local function keyed(root)
    local list = {}
    local function walk(e, parent)
        local key = e.config and e.config.calculatro_anim
        local entry = parent
        if key then
            entry = { key = key, e = e, parent = parent }
            list[#list + 1] = entry
        end
        for _, child in ipairs(ui_children(e)) do walk(child, entry) end
    end
    walk(root, nil)
    return list
end

-- An element's place relative to the panel's major (the deck), from the
-- box's place and the element's offset in it (role.offset): its own T only
-- catches up with the box a frame later, and build_panel moves the box
-- after building it.
local function relative(e, panel)
    local major, offset = panel.role.major, e.role.offset
    return { x = panel.T.x + offset.x - major.T.x, y = panel.T.y + offset.y - major.T.y, w = e.T.w, h = e.T.h }
end

local FIELDS = { 'x', 'y', 'w', 'h' }

-- Where a panel draws each keyed element right now, so a panel rebuilt
-- mid-animation carries on from there, with the moves and pops still under
-- way: the odds rebuild the panel several times a second, and a move whose
-- element ends up in the same place again goes on as it was.
function Calculatro.drawn_layout(panel, now)
    local anim = panel.calculatro_anim or { tracks = {} }
    local out = {}
    for _, k in ipairs(keyed(panel.UIRoot)) do
        local place = relative(k.e, panel)
        local track = anim.tracks[k.key] or {}
        if track.move and now < track.move.start + MORPH_TIME then
            place.final, place.move = relative(k.e, panel), track.move
            local left = Calculatro.move_left(track.move, now)
            for _, f in ipairs(FIELDS) do place[f] = place[f] + track.move[f] * left end
        end
        if track.pop and now < track.pop + POP_TIME then place.pop = track.pop end
        out[k.key] = place
    end
    return out
end

-- Plans the new panel's animation from the old one's drawn layout (nil: the
-- panel is appearing): tracks keyed by element key, each with a move (what
-- the place still differs by, from `start`) and/or a pop (its start time).
function Calculatro.plan(list, from, panel, now)
    local tracks, moving, pops = {}, false, {}
    if not from then
        if list[1] then tracks[list[1].key] = { pop = now } end
        return tracks
    end
    for _, k in ipairs(list) do
        local old, place = from[k.key], relative(k.e, panel)
        if old then
            local move, same, moved = { start = now }, old.final ~= nil, false
            for _, f in ipairs(FIELDS) do
                move[f] = old[f] - place[f]
                moved = moved or math.abs(move[f]) > 0.001
                same = same and math.abs(old.final[f] - place[f]) <= 0.001
            end
            if same then move = old.move elseif not moved then move = nil end
            moving = moving or move ~= nil
            tracks[k.key] = { move = move, pop = old.pop }
        else
            tracks[k.key] = { new = true }
            -- A new element inside a new element pops with it.
            if not (k.parent and tracks[k.parent.key].new) then pops[#pops + 1] = tracks[k.key] end
        end
    end
    local delay = moving and POP_DELAY or 0
    for i, track in ipairs(pops) do
        track.pop = now + delay + POP_STAGGER * math.min(i - 1, MAX_STAGGER)
    end
    return tracks
end

-- The transform each element is drawn with: moved by `dx, dy` and grown by
-- `dw, dh` (keyed elements' own moves; others take their nearest keyed
-- element's move), and scaled by `s` around (cx, cy), the centre of the
-- popping element around it.
local function apply(e, anim, now, saved, inherited)
    local key = e.config and e.config.calculatro_anim
    local tr = inherited
    local track = key and anim.tracks[key]
    if key then
        tr = { dx = 0, dy = 0, dw = 0, dh = 0, s = inherited.s, cx = inherited.cx, cy = inherited.cy }
        if track and track.move then
            local left = Calculatro.move_left(track.move, now)
            tr.dx, tr.dy = track.move.x * left, track.move.y * left
            tr.dw, tr.dh = track.move.w * left, track.move.h * left
        elseif not track then
            tr.dx, tr.dy = inherited.dx, inherited.dy
        end
        if track and track.pop then
            local s = Calculatro.pop_scale(track.pop, now)
            if s < 1 then
                tr.s = s * inherited.s
                tr.cx = e.VT.x + tr.dx + (e.VT.w + tr.dw) / 2
                tr.cy = e.VT.y + tr.dy + (e.VT.h + tr.dh) / 2
            end
        end
    end
    local function shift(vt)
        saved[#saved + 1] = { vt, vt.x, vt.y, vt.w, vt.h, vt.scale }
        vt.x, vt.y = vt.x + tr.dx, vt.y + tr.dy
        if key then vt.w, vt.h = vt.w + tr.dw, vt.h + tr.dh end
        if tr.s ~= 1 then
            vt.x = tr.cx + (vt.x + vt.w / 2 - tr.cx) * tr.s - vt.w / 2
            vt.y = tr.cy + (vt.y + vt.h / 2 - tr.cy) * tr.s - vt.h / 2
            vt.scale = (vt.scale or 1) * tr.s
        end
    end
    shift(e.VT)
    local object = e.config and e.config.object
    if object and object.VT then shift(object.VT) end
    for _, child in ipairs(ui_children(e)) do apply(child, anim, now, saved, tr) end
end

local function animation_done(anim, now)
    for _, track in pairs(anim.tracks) do
        if track.move and now < track.move.start + MORPH_TIME then return false end
        if track.pop and now < track.pop + POP_TIME then return false end
    end
    return true
end

local function draw_animated(panel)
    local anim = panel.calculatro_anim
    local now = Calculatro.clock
    if not anim or animation_done(anim, now) then
        panel.calculatro_anim = nil
        return UIBox.draw(panel)
    end
    local saved = {}
    apply(panel.UIRoot, anim, now, saved, { dx = 0, dy = 0, dw = 0, dh = 0, s = 1 })
    local ok, err = pcall(UIBox.draw, panel)
    for i = #saved, 1, -1 do
        local s = saved[i]
        s[1].x, s[1].y, s[1].w, s[1].h, s[1].scale = s[2], s[3], s[4], s[5], s[6]
    end
    if not ok then error(err, 0) end
end

-- Animates a freshly built panel from `from` (Calculatro.drawn_layout of
-- the one it replaces; nil when it's appearing).
function Calculatro.animate(panel, from)
    if not panel.UIRoot or (G.SETTINGS and G.SETTINGS.reduced_motion) then return end
    local now = Calculatro.clock
    local tracks = Calculatro.plan(keyed(panel.UIRoot), from, panel, now)
    if next(tracks) == nil then return end
    panel.calculatro_anim = { tracks = tracks }
    panel.draw = draw_animated
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

-- Card query names show their popup (config.calculatro_popup, see
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
    local popup = e.config and e.config.calculatro_popup
    if popup then
        function e:hover()
            self.config.h_popup = Calculatro.popup_definition(popup.name, popup.about)
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

-- The panel collides (can_collide) for its buttons. They need a box that
-- isn't replaced while they're used, so it's only rebuilt when what it shows
-- changes, and the editor only by its own buttons.
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

-- The panel's definition for `model`, made only to read its values from:
-- without the sprites a box would need (see values_only).
local function values_definition(model, per)
    values_only = true
    local ok, definition = pcall(Calculatro.definition, model, per)
    values_only = false
    if not ok then error(definition, 0) end
    return definition
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

-- Rebuilds the panel when its layout changes, and only updates its values
-- when they're all that changed (most often, the odds); removes it outside
-- blinds, closing the editor. The odds fill each column with as many rows
-- as keep it clear of the consumables before starting the next; a box's
-- size is only known once it's built, so it tries each section in one
-- column, then one fewer row per column, and so on (remembered in
-- Calculatro.fits).
function Calculatro.refresh_panel()
    if G.FUNCS then G.FUNCS.calculatro_button = Calculatro.button_pressed end
    local shown = Calculatro.visible and Calculatro.in_blind()
    if not shown then Calculatro.editor = nil end
    local model = shown and (Calculatro.editor and Calculatro.editor_model() or Calculatro.panel_model()) or nil
    local key = model and encode(model)
    local layout = model and not model.editor and layout_key(model) or key
    local panel = Calculatro.panel
    if panel and panel.REMOVED then panel = nil end  -- the game removes it with the run
    local same_place = panel and Calculatro.panel_major == G.deck
    if same_place and key == Calculatro.panel_key then return end
    if same_place and layout == Calculatro.panel_layout and not model.editor then
        update_values(panel, values_definition(model, panel.calculatro_per))
        Calculatro.panel_key = key
        return
    end
    -- The new panel animates from where this one is drawn.
    local from = panel and panel.UIRoot and same_place and Calculatro.drawn_layout(panel, Calculatro.clock) or nil
    if panel then remove_box(panel) end
    Calculatro.panel, Calculatro.panel_key, Calculatro.panel_layout, Calculatro.panel_major = nil, nil, nil, nil
    if not model then return end

    if model.editor then
        panel = build_panel(Calculatro.editor_definition(model))
        -- Too tall to fit under the consumables at the odds' place, it grows
        -- downwards instead, into the room kept for raised cards: there's no
        -- need to select cards while choosing what to track.
        local max_h = Calculatro.max_panel_height()
        if max_h and panel.T.h > max_h then
            panel.alignment.offset.y = panel.alignment.offset.y + panel.T.h - max_h
            panel:align_to_major()
            panel:hard_set_VT()
        end
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

---------------------------------------------------------------------------
-- Hooks (see lovely.toml)
---------------------------------------------------------------------------
Calculatro.reported = {}

-- Writes the home folder as `~`, as the app's log does (log.py): players post
-- the log publicly, and the folder's path holds their Windows username. The
-- game's name for it, since it's UTF-8 like launch.txt and the save directory.
local function hide_home(text)
    local function folded(s) return (s:lower():gsub('/', '\\')) end  -- each byte stays in its place
    local get = love.filesystem.getUserDirectory
    local home = get and (folded(get()):gsub('\\+$', ''))
    if not home or home == '' then return text end
    local seen, out, at, from = folded(text), {}, 1, 1
    while true do
        local first, last = seen:find(home, from, true)
        if not first then break end
        if seen:sub(last + 1):find('^[\\\'"%s,:;%)%]]') or last == #seen then
            out[#out + 1] = text:sub(at, first - 1) .. '~'
            at = last + 1
        end
        from = last + 1
    end
    out[#out + 1] = text:sub(at)
    return table.concat(out)
end

-- Adds a line to the app's log, where a player is told to look; when the app
-- never starts, it's the only thing there. The app has the file open too, which
-- Windows allows since both only append.
function Calculatro.log_error(text)
    local f = io.open(save_path('calculatro.log'), 'ab')
    if not f then return end
    if not Calculatro.log_started then
        Calculatro.log_started = true
        f:write('=== Calculatro mod, session ', Calculatro.session, ', ', os.date('%Y-%m-%d %H:%M:%S'), '\n')
    end
    f:write('[mod] ', hide_home(text), '\n')
    f:close()
end

-- Reports `what` the first time only.
local function report(what, text)
    if Calculatro.reported[what] then return end
    Calculatro.reported[what] = true
    print('[Calculatro] ' .. text)
    pcall(Calculatro.log_error, text)
end

-- Runs fn, reporting its first error only: a broken step must not crash the game.
local function guard(what, fn, ...)
    local ok, err = pcall(fn, ...)
    if not ok then report(what, what .. ' failed: ' .. tostring(err)) end
end

-- Seconds without an answer before starting the app again, each counted from
-- the last start. Nothing notices it's gone but its silence: ShellExecuteW
-- succeeds even if Python dies straight away, or once it crashes later on.
-- Each start stalls a frame, so it stops after these until the app answers.
local RESTART_AFTER = { 5, 15, 45 }
local FAILED_AFTER = 2  -- restarts before the panel says so, about 20 seconds in

function Calculatro.start_app()
    Calculatro.app_started = false
    guard('starting the app', function() Calculatro.app_started = Calculatro.launch() end)
    Calculatro.silent_since = Calculatro.clock
end

-- Whether the panel should say the app isn't coming: Windows couldn't start it,
-- or it's been started again and again without answering.
function Calculatro.app_failed()
    return not Calculatro.app_started or Calculatro.restarts >= FAILED_AFTER
end

-- An answer to the latest snapshot means it's running, whoever started it, and
-- it gets restarts afresh if it stops later. A start Windows refused isn't
-- tried again: a path that's wrong now will still be wrong in 5 seconds.
function Calculatro.check_app()
    if Calculatro.state_id and Calculatro.ack_id == Calculatro.state_id then
        Calculatro.app_started, Calculatro.restarts, Calculatro.silent_since = true, 0, Calculatro.clock
        return
    end
    if not Calculatro.app_started then return end
    local wait = RESTART_AFTER[Calculatro.restarts + 1]
    if wait and Calculatro.clock - Calculatro.silent_since >= wait then
        Calculatro.restarts = Calculatro.restarts + 1
        Calculatro.start_app()
        if Calculatro.app_started and Calculatro.restarts >= FAILED_AFTER then
            local exe = Calculatro.launch_command(0)
            report('the app answering', string.format('the app is not answering after %d starts of %s',
                                                      Calculatro.restarts + 1, tostring(exe)))
        end
    end
end

function Calculatro.update(dt)
    dt = dt or 0
    if not Calculatro.launched then
        Calculatro.launched = true
        Calculatro.start_app()
    end
    Calculatro.clock = Calculatro.clock + dt
    Calculatro.elapsed = Calculatro.elapsed + dt
    Calculatro.odds_elapsed = Calculatro.odds_elapsed + dt
    guard('round', Calculatro.check_round)
    guard('search', Calculatro.check_search)  -- before the snapshot, which sends its request
    if Calculatro.elapsed >= Calculatro.interval then
        Calculatro.elapsed = 0
        guard('snapshot', Calculatro.write_snapshot)
    end
    if Calculatro.odds_elapsed >= Calculatro.odds_interval then
        Calculatro.odds_elapsed = 0
        guard('reading odds', Calculatro.read_odds)
    end
    guard('checking on the app', Calculatro.check_app)
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

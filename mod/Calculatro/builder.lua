local Calculatro = require('calculatro.core')

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

return { OPTIONS = OPTIONS, RANKS = RANKS, SUITS = SUITS, SUIT_SYMBOLS = SUIT_SYMBOLS, stone_only = stone_only }

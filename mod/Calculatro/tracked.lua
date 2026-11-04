local Calculatro = require('calculatro.core')

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

return { HANDS = HANDS, SHORT_NAMES = SHORT_NAMES, set_of = set_of }

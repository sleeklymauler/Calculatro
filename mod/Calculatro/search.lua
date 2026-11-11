local Calculatro = require('calculatro.core')

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

-- The hand cards selected, as 0-based indices into `cards`.
local function selection(cards)
    local out = {}
    for i, card in ipairs(cards) do
        if card.highlighted then out[#out + 1] = i - 1 end
    end
    return out
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
    if found and found.key == key and not found.gave_up and same_hand(found.hand) then
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
-- next click selects its first option. A search the app gave up on (its
-- `mark`) has no options, and is kept as `gave_up` with the selection then,
-- for the title row; the button asks again, and the app answers at once. One
-- it gave up on but answered from estimates has options and a mark with a
-- `how`, kept as `estimated` for the title row.
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
    if not same_hand(searching.hand) then return end
    local mark = type(reply.mark) == 'table' and reply.mark or nil
    if mark and mark.state == 'final' and #options == 0 then
        Calculatro.found = { key = searching.key, nonce = searching.nonce, options = {}, index = 0,
                             hand = searching.hand, gave_up = mark, selection = selection(searching.hand) }
        return
    end
    if #options == 0 then return end
    local apply = Calculatro.can_search()
    Calculatro.found = { key = searching.key, nonce = searching.nonce, options = options,
                         index = apply and 1 or 0, hand = searching.hand,
                         estimated = mark and mark.how and mark or nil }
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
-- is one of its options ("2/3", or "Keep hand" for keeping it). For a search
-- given up on, "Too slow" and its mark (for the popup), until the selection
-- changes. Options from estimates have an asterisk, and their mark (for the
-- popup): "2/3*", "Keep hand*", or "1/1*" for the one discard, which
-- otherwise shows nothing.
function Calculatro.search_message()
    if Calculatro.searching then return 'Searching...' end
    local found = Calculatro.found
    if found and found.gave_up then
        if same_hand(found.hand) and is_selected(found.selection, found.hand) then
            return 'Too slow', found.gave_up
        end
        return nil
    end
    if not found or found.index == 0 or not same_hand(found.hand)
        or not is_selected(found.options[found.index], found.hand) then
        return nil
    end
    local estimated = found.estimated
    local mark = estimated and '*' or ''
    if #found.options > 1 then return found.index .. '/' .. #found.options .. mark, estimated end
    if #found.options[found.index] == 0 then return 'Keep hand' .. mark, estimated end
    if estimated then return '1/1*', estimated end
end

---------------------------------------------------------------------------
-- What the hand's order has shown the player about its face-down cards.
--
-- The game sorts the hand by its cards' true ranks and suits, face-down ones
-- included (Card:get_nominal ignores facing), so when the hand is known to be in
-- the game's order, that order says something about them. An observation is the
-- hand at such a moment, as the player saw it: the face-up cards' printed values,
-- and which card was face down where. The latest of each sort mode is kept.
--
-- It reads the game's state and hooks nothing. Whether the order is the game's
-- comes only from public events (draws, Sort presses, drags, consumables), never
-- from the face-down cards' values: asking if the hand is in its true order, or if
-- a face-down card changed, would tell what they are. See
-- docs/plans/face-down-windows.md.
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')
local ui_children = require('calculatro.uibox').ui_children

-- By mode: {cards = {entry, ...}}, left to right. An entry is {card = a card
-- face down when it was seen}, {rank_id =, suit =} or {stone = true}.
Calculatro.observations = {}
-- Goes up whenever the observations change.
Calculatro.observed = 0

local MODES = { desc = true, ['suit desc'] = true }
local SUIT_ORDER = { Diamonds = 1, Clubs = 2, Hearts = 3, Spades = 4 }
local SORT_BUTTONS = { sort_hand_value = true, sort_hand_suit = true }

-- Where an entry or card's printed values sort in `mode`, highest first, as
-- Card:get_nominal orders them. Stones sort below everything, and among themselves
-- by a rank and suit nobody can see, so they all tie here. So do cards of the same
-- rank and suit, whose order depends on things the player can't see either.
local function sort_key(mode, printed)
    if printed.stone then return -1 end
    local suit = SUIT_ORDER[printed.suit] or 0
    if mode == 'desc' then return printed.rank_id * 10 + suit end
    return suit * 100 + printed.rank_id
end

local function printed_values(card)
    if card.config and card.config.center_key == 'm_stone' then return { stone = true } end
    return { rank_id = card.base.id, suit = card.base.suit }
end

local function forget()
    if next(Calculatro.observations) ~= nil then
        Calculatro.observations = {}
        Calculatro.observed = Calculatro.observed + 1
    end
end

local function set_of(cards)
    local set = {}
    for _, card in ipairs(cards) do set[card] = true end
    return set
end

local function copy(cards)
    local out = {}
    for i, card in ipairs(cards) do out[i] = card end
    return out
end

-- The hand's Sort buttons' elements: only their last_clicked is read, which
-- UIElement:click sets just before running the button.
local function sort_buttons(e, out)
    if e.config and SORT_BUTTONS[e.config.button] then out[#out + 1] = e end
    for _, child in ipairs(ui_children(e)) do sort_buttons(child, out) end
    return out
end

-- Whether a Sort button was pressed since last frame. A box rebuilt since then
-- (the game rebuilds it between hands) has new elements, which only set what's
-- compared with next frame.
local function sort_pressed(t)
    local box = G.buttons
    if box ~= t.buttons then
        t.buttons, t.sort_elements, t.clicks = box, box and box.UIRoot and sort_buttons(box.UIRoot, {}) or {}, {}
        for _, e in ipairs(t.sort_elements) do t.clicks[e] = e.last_clicked or false end
        return false
    end
    local pressed = false
    for _, e in ipairs(t.sort_elements) do
        local clicked = e.last_clicked or false
        if clicked ~= t.clicks[e] then
            pressed = true
            t.clicks[e] = clicked
        end
    end
    return pressed
end

local function any_dragged(cards)
    for _, card in ipairs(cards) do
        local states = card.states
        if states and states.drag and states.drag.is then return true end
    end
    return false
end

-- Whether cards in both lists are in a different order in them.
local function reordered(before, after, before_set, after_set)
    local j = 1
    for _, card in ipairs(before) do
        if after_set[card] then
            while after[j] and not before_set[after[j]] do j = j + 1 end
            if after[j] ~= card then return true end
            j = j + 1
        end
    end
    return false
end

local function same_list(a, b)
    if #a ~= #b then return false end
    for i, card in ipairs(a) do
        if b[i] ~= card then return false end
    end
    return true
end

-- A face-down card that has since been turned up is its printed values, now
-- public. One gone from the hand still face down (discarded) is left out: what's
-- left of a sorted order is still the sorted order of what's left, so it's true,
-- though it doesn't use all the player saw.
local function update_entries(in_hand)
    local changed = false
    for _, observation in pairs(Calculatro.observations) do
        local cards = observation.cards
        for i = #cards, 1, -1 do
            local card = cards[i].card
            if card then
                if card.facing ~= 'back' then
                    cards[i] = printed_values(card)
                    changed = true
                elseif not in_hand[card] then
                    table.remove(cards, i)
                    changed = true
                end
            end
        end
    end
    if changed then Calculatro.observed = Calculatro.observed + 1 end
end

-- The hand as an observation, or nil if its face-up cards aren't in order for
-- `mode`, which the events should have ruled out: a check that leaks nothing,
-- as it uses printed values only.
local function observe(cards, mode)
    local entries, last = {}, nil
    for i, card in ipairs(cards) do
        if card.facing == 'back' then
            entries[i] = { card = card }
        else
            local printed = printed_values(card)
            local key = sort_key(mode, printed)
            if last and key > last then return nil end
            entries[i], last = printed, key
        end
    end
    return { cards = entries }
end

local function start_tracking()
    local t = { game = G.GAME, area = G.hand, round = G.GAME.round, hand = copy(G.hand.cards),
                in_hand = set_of(G.hand.cards), in_deck = set_of(G.deck.cards), deck_count = #G.deck.cards,
                dragged = false, pending = false, sort_elements = {}, clicks = {} }
    sort_pressed(t)
    forget()
    return t
end

-- Run every frame. Each frame it sees what the last one did, perhaps several
-- things at once, so a frame with both a sort and anything else that could move a
-- card proves nothing. A drag ends the wait for a sort to record but keeps what's
-- been observed, which stays true of those cards wherever they're moved. Using a
-- consumable, even one that changes nothing here (a change itself would be a leak),
-- ends both: it may have changed a face-down card.
function Calculatro.track_hand()
    if not (G and G.GAME and G.hand and G.deck and G.STAGES and G.STAGE == G.STAGES.RUN) then
        Calculatro.tracking = nil
        forget()
        return
    end
    local t = Calculatro.tracking
    if not t or t.game ~= G.GAME or t.area ~= G.hand or t.round ~= G.GAME.round then
        t = start_tracking()
        Calculatro.tracking = t
    end

    local cards = G.hand.cards
    local stable = same_list(cards, t.hand)
    local in_hand = t.in_hand
    local sorted, foreign, moved = sort_pressed(t), false, false
    if not stable then
        in_hand = set_of(cards)
        for _, card in ipairs(cards) do
            if not t.in_hand[card] then
                if t.in_deck[card] then sorted = true else foreign = true end
            end
        end
        moved = reordered(t.hand, cards, t.in_hand, in_hand)
    end
    local dragging = any_dragged(cards)

    if G.STATE == G.STATES.PLAY_TAROT or G.TAROT_INTERRUPT then
        forget()
        t.pending = false
    else
        update_entries(in_hand)
        if dragging or t.dragged or foreign or (moved and not sorted) then
            t.pending = false
        elseif sorted then
            t.pending = true
        end
        local mode = G.hand.config.sort
        if t.pending and stable and G.STATE == G.STATES.SELECTING_HAND and MODES[mode] then
            local observation = observe(cards, mode)
            if observation then
                Calculatro.observations[mode] = observation
                Calculatro.observed = Calculatro.observed + 1
            end
            t.pending = false
        end
    end

    if not stable then t.hand, t.in_hand = copy(cards), in_hand end
    if not stable or #G.deck.cards ~= t.deck_count then
        t.in_deck, t.deck_count = set_of(G.deck.cards), #G.deck.cards
    end
    t.dragged = dragging
end

return { sort_key = sort_key }

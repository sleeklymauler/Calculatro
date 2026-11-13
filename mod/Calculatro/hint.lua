---------------------------------------------------------------------------
-- The box above a hovered face-down card: what the hand's order leaves it able to
-- be ("6-8 ♥♦").
--
-- It works from the unseen pool, the sorted multiset the snapshot exports, and the
-- observations, never from the card itself or any other face-down card's values, so
-- it shows nothing a player couldn't work out from the screen and View Deck.
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')
local observe = require('calculatro.observe')
local builder = require('calculatro.builder')
local uibox = require('calculatro.uibox')
require('calculatro.snapshot')
require('calculatro.panel')
require('calculatro.widgets')

local sort_key = observe.sort_key
local RANKS, SUITS, SUIT_SYMBOLS = builder.RANKS, builder.SUITS, builder.SUIT_SYMBOLS

-- A search step is one card tried for one face-down card. A hand that uses them
-- all up (many face-down cards, both sorts) mustn't freeze the game, so what it
-- couldn't rule out in time stays possible: too much, but never wrong. For eight
-- face-down cards sorted both ways, 10000 steps took at most about 30 ms in LuaJIT,
-- and cut short about 3% of them (docs/panel.md).
local SEARCH_STEPS = 10000

-- Whether `card` can only be a face card: The Mark drew it face down as one
-- (observe.lua's marks, by whether Pareidolia was held then). The challenge's random
-- flips (flipped_cards) can flip any card as well. Same model as the engine's
-- (`Card.marked`, `engine.flipped_by_mark`).
local function face_card_only(card)
    if G.GAME.modifiers and G.GAME.modifiers.flipped_cards then return false end
    return Calculatro.marked(card)
end

-- The pool's different cards as printed, with how many of each: {rank_id, suit,
-- stone, face, count}. Copies count as one candidate each; enhancements don't matter here.
local function candidates()
    local list, by_key = {}, {}
    for _, info in ipairs(Calculatro.unseen_pool()) do
        local stone = info.enhancement == 'm_stone'
        local key = stone and 'stone' or (info.rank_id .. info.suit)
        local c = by_key[key]
        if not c then
            c = { rank_id = info.rank_id, suit = info.suit, stone = stone or nil, count = 0,
                  face = not stone and info.rank_id >= 11 and info.rank_id <= 13 }
            by_key[key] = c
            list[#list + 1] = c
        end
        c.count = c.count + 1
    end
    return list
end

-- Each observation's mode, and for every face-down card in it, its position and
-- the sort keys of the face-up cards it lies between, in the order recorded (so
-- the same hand always searches alike). A mode may repeat: every sort is true of
-- the cards in it. Face-up cards are in order, so the nearest ones are the
-- tightest bounds.
local function read_observations()
    local list = {}
    for _, observation in ipairs(Calculatro.observations) do
        local mode = observation.mode
        local position, high, low = {}, {}, {}
        local bound = math.huge
        for i, entry in ipairs(observation.cards) do
            if entry.latent then
                position[entry.card], high[entry.card] = i, bound
            else
                bound = sort_key(mode, entry)
            end
        end
        bound = -math.huge
        for i = #observation.cards, 1, -1 do
            local entry = observation.cards[i]
            if entry.latent then low[entry.card] = bound else bound = sort_key(mode, entry) end
        end
        list[#list + 1] = { mode = mode, position = position, high = high, low = low, entries = observation.cards }
    end
    return list
end

-- What `card`, face down in an observation, can be: the entries of candidates()
-- it can be the printed values of, with the other face-down cards all getting a
-- different pool card consistent with what was seen. nil if nothing shows it.
--
-- Those discarded face down since are dealt like the ones in hand: where they were
-- still says what they are. Face-down cards not in an observation always fit: they
-- get whatever's left, which there's enough of, since the pool holds every
-- face-down card (though The Mark's need face cards, which the others may have
-- taken: too much again, never wrong). Two cards of the same rank and suit tie here in both modes, though
-- the game breaks that tie the same way each time, so in the rare case they'd have to
-- be in opposite orders in two sorts, this lets them be: too much again, never
-- wrong.
function Calculatro.possible_cards(card)
    local observations = read_observations()
    local cards, seen = {}, {}
    local function add(c) if not seen[c] then seen[c] = true; cards[#cards + 1] = c end end
    add(card)
    for _, o in ipairs(observations) do
        for _, entry in ipairs(o.entries) do
            if entry.latent then add(entry.card) end
        end
    end
    local observed = false
    for _, o in ipairs(observations) do observed = observed or o.position[card] ~= nil end
    if not observed then return nil end

    -- Highest first in the first sort, so the search mostly finds a way at once. Each
    -- candidate keeps its key in each mode, by the mode's name, for the search.
    local all = candidates()
    for _, value in ipairs(all) do
        for _, o in ipairs(observations) do value[o.mode] = sort_key(o.mode, value) end
    end
    if observations[1] then
        local mode = observations[1].mode
        table.sort(all, function(a, b)
            local x, y = a[mode], b[mode]
            if x ~= y then return x > y end
            return (a.stone and 'stone' or a.rank_id .. a.suit) < (b.stone and 'stone' or b.rank_id .. b.suit)
        end)
    end

    local function fits_bounds(c, value)
        if value.face == false and face_card_only(c) then return false end
        for _, o in ipairs(observations) do
            if o.position[c] then
                local key = value[o.mode]
                if key > o.high[c] or key < o.low[c] then return false end
            end
        end
        return true
    end
    local domains = {}
    for _, c in ipairs(cards) do
        local domain = {}
        for _, value in ipairs(all) do
            if fits_bounds(c, value) then domain[#domain + 1] = value end
        end
        domains[c] = domain
    end

    -- For each card, the others a sort places it beside: { other, mode, above }, where
    -- `above` says it's above the other in that sort, so its key can't be lower.
    local pairs_of = {}
    for _, c in ipairs(cards) do
        local list = {}
        for _, other in ipairs(cards) do
            if other ~= c then
                for _, o in ipairs(observations) do
                    local here, there = o.position[c], o.position[other]
                    if here and there then list[#list + 1] = { other, o.mode, here < there } end
                end
            end
        end
        pairs_of[c] = list
    end

    -- Whether the cards in `left` can each get a different pool card from what's `live`
    -- for them (a matching, with copies counted), ignoring how they're ordered.
    local function matched(left, live)
        local holders = {}
        local function place(c, visited)
            for _, v in ipairs(live[c]) do
                if v.count > 0 and not visited[v] then
                    visited[v] = true
                    local held = holders[v]
                    if not held then held = {}; holders[v] = held end
                    if #held < v.count then held[#held + 1] = c; return true end
                    for i, d in ipairs(held) do
                        if place(d, visited) then held[i] = c; return true end
                    end
                end
            end
            return false
        end
        for _, c in ipairs(left) do
            if not place(c, {}) then return false end
        end
        return true
    end

    -- Each card left to deal keeps the values still in order with those dealt (forward
    -- checking): a choice that leaves another card nothing, or the cards left too few
    -- to share, fails at once, and the card with the fewest values left is dealt next.
    local steps = 0
    local function search(left, live)
        if #left == 0 then return true end
        if not matched(left, live) then return false end
        local best, size = 1, math.huge
        for i, c in ipairs(left) do
            local n = 0
            for _, value in ipairs(live[c]) do
                if value.count > 0 then n = n + 1 end
            end
            if n == 0 then return false end
            if n < size then best, size = i, n end
        end
        local c = left[best]
        local rest, pending = {}, {}
        for i, other in ipairs(left) do
            if i ~= best then rest[#rest + 1], pending[other] = other, true end
        end
        for _, value in ipairs(live[c]) do
            steps = steps + 1
            if steps > SEARCH_STEPS then return true end
            if value.count > 0 then
                local narrowed = setmetatable({}, { __index = live })
                for _, link in ipairs(pairs_of[c]) do
                    local other, mode, above = link[1], link[2], link[3]
                    if pending[other] then
                        local domain = narrowed[other]
                        local mine, kept = value[mode], {}
                        for _, v in ipairs(domain) do
                            local theirs = v[mode]
                            if (above and theirs <= mine) or (not above and theirs >= mine) then kept[#kept + 1] = v end
                        end
                        narrowed[other] = kept
                    end
                end
                value.count = value.count - 1
                local found = search(rest, narrowed)
                value.count = value.count + 1
                if found then return true end
            end
        end
        return false
    end

    local out = {}
    for _, value in ipairs(domains[card]) do
        if steps > SEARCH_STEPS then
            out[#out + 1] = value
        elseif search(cards, setmetatable({ [card] = { value } }, { __index = domains })) then
            out[#out + 1] = value
        end
    end
    return out
end

-- "2-4, 7, J-A", for a set of rank ids.
local function ranks_text(ranks)
    local parts, id = {}, 2
    while id <= 14 do
        if ranks[id] then
            local last = id
            while ranks[last + 1] do last = last + 1 end
            parts[#parts + 1] = RANKS[id].chip .. (last > id and '-' .. RANKS[last].chip or '')
            id = last + 1
        else
            id = id + 1
        end
    end
    return table.concat(parts, ', ')
end

-- What possible_cards found, in lines the panel's labels can draw: suits able to
-- be the same ranks share a line ("6-8 ♥♦"), so nothing's implied that isn't so.
-- Stones have no rank or suit.
function Calculatro.hint_lines(possible)
    local by_suit, stone = {}, false
    for _, value in ipairs(possible) do
        if value.stone then
            stone = true
        else
            by_suit[value.suit] = by_suit[value.suit] or {}
            by_suit[value.suit][value.rank_id] = true
        end
    end
    local lines, line_of = {}, {}
    for _, suit in ipairs(SUITS) do
        if by_suit[suit] then
            local text = ranks_text(by_suit[suit])
            local i = line_of[text]
            if not i then
                i = #lines + 1
                line_of[text], lines[i] = i, text .. ' '
            end
            lines[i] = lines[i] .. SUIT_SYMBOLS[suit]
        end
    end
    if stone then lines[#lines + 1] = 'Stone' end
    return lines
end

-- The face-down card in hand being hovered, while the player can act on it. The
-- game shows no popup of its own for one (Card:hover), so nothing's in the way.
local function hovered_card()
    if not Calculatro.settings.face_down then return nil end  -- off in the settings
    if not (Calculatro.visible and Calculatro.in_blind() and G.STATE == G.STATES.SELECTING_HAND) then return nil end
    for _, card in ipairs(G.hand.cards) do
        local states = card.states
        if card.facing == 'back' and states and states.hover.is and not states.drag.is then return card end
    end
    return nil
end

local function remove_hint()
    local box = Calculatro.hint_box
    if box and not box.REMOVED then uibox.remove_box(box) end
    Calculatro.hint_box, Calculatro.hint_card, Calculatro.hint_key = nil, nil, nil
end

-- Set up like the game's own card popups (Card:align_h_popup, Node:hover).
local function build_hint(card, lines)
    local box = UIBox{
        definition = Calculatro.hint_definition(lines),
        config = { align = 'tm', offset = { x = 0, y = -0.13 }, major = card, parent = card, xy_bond = 'Strong',
                   r_bond = 'Weak', wh_bond = 'Weak', instance_type = 'POPUP', can_collide = false },
    }
    uibox.register_once()
    if box.UIRoot then uibox.deepen_shadows(box.UIRoot) end
    return box
end

-- Run every frame; only does anything when the card hovered or what's known about it
-- changes. Draws and discards change the pool, and so what's possible, without
-- necessarily changing an observation, so the sizes of the piles are part of that.
function Calculatro.refresh_hint()
    local card = hovered_card()
    if not card then
        if Calculatro.hint_card then remove_hint() end
        return
    end
    local key = table.concat({ Calculatro.observed, #G.deck.cards, #G.hand.cards, G.discard and #G.discard.cards or 0 }, ':')
    if card == Calculatro.hint_card and key == Calculatro.hint_key then return end
    remove_hint()
    Calculatro.hint_card, Calculatro.hint_key = card, key
    local possible = Calculatro.possible_cards(card)
    if possible and #possible > 0 then
        Calculatro.hint_box = build_hint(card, Calculatro.hint_lines(possible))
    end
end

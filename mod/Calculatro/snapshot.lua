local Calculatro = require('calculatro.core')
local json = require('calculatro.json')
local report = require('calculatro.log').report
require('calculatro.tracked')
require('calculatro.observe')
require('calculatro.memory')

local array, encode = json.array, json.encode

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

-- Whether the player hasn't seen this card of `area`. The game turns every card
-- in the discard pile over (CardArea:align_cards), so `facing` says nothing there:
-- it marks those drawn face down with wheel_flipped, and clears it if they're turned
-- up, which is how View Deck tells them apart too. In hand, `facing` is what the
-- player sees: a Tarot can leave a card face down without the mark.
local function face_down(card, area)
    if area == G.discard then return card.ability and card.ability.wheel_flipped end
    return card.facing == 'back'
end

-- `hide`: export only that it's there, and whether The Mark drew it as a face card
-- (observe.lua's marks: by when it was drawn, which the player saw). `in_hand`: also
-- says whether Cerulean Bell forces the card into every play and discard (the player
-- sees it raised and can't unselect it).
local function card_info(card, hide, in_hand)
    local highlighted = card.highlighted and true or false
    local forced = in_hand and card.ability and card.ability.forced_selection and true or nil
    if hide then
        return { hidden = true, marked = Calculatro.marked(card) or nil, highlighted = highlighted, forced = forced }
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
        for i, card in ipairs(area.cards) do
            out[i] = card_info(card, hide_face_down and face_down(card, area), in_hand)
        end
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
-- in hand or discarded (the game doesn't reveal those), merged and sorted, so
-- neither which cards are face down or were discarded face down, nor which
-- were just drawn face down leaks (the game's View Deck counts them together
-- too). The face-down counts are public, from the hand's and the discard
-- pile's `{hidden = true}`.
local function unseen_pool()
    local cards = array()
    for _, area in ipairs({ G.deck, G.hand, G.discard }) do
        for _, card in ipairs(area and area.cards or {}) do
            if area == G.deck or face_down(card, area) then
                local info = card_info(card, false)
                info.highlighted = nil
                cards[#cards + 1] = info
            end
        end
    end
    return sorted_cards(cards)
end
Calculatro.unseen_pool = unseen_pool

-- What a card counts as in check_unseen: as printed, so copies match each other.
local function pool_key(info)
    if info.enhancement == 'm_stone' then return 'stone' end
    return tostring(info.rank_id) .. ' ' .. tostring(info.suit)
end

-- Compares the unseen pool with the game's own idea of it, View Deck's: of all the
-- playing cards, those in the deck or marked wheel_flipped (G.UIDEF.view_deck). The
-- pool is everything the odds rest on, and it's worked out differently, area by area,
-- so this notices when the two part, as they did when every discard counted as unseen.
-- The tests can't: they only know the game as far as its mock does.
--
-- Run for each new snapshot while selecting, as cards are on the move otherwise, until
-- it finds a mismatch. It says only how many cards differ: players post the log
-- publicly, and which cards they were could give away the face-down ones.
function Calculatro.check_unseen()
    if Calculatro.seq == Calculatro.checked_seq or Calculatro.reported['unseen pool'] then return end
    Calculatro.checked_seq = Calculatro.seq
    if not (G and G.GAME and G.deck and G.hand and G.playing_cards and G.STAGES and G.STAGE == G.STAGES.RUN
            and G.STATE == G.STATES.SELECTING_HAND) then
        return
    end
    local pool = unseen_pool()
    local counts, game_total = {}, 0
    for _, info in ipairs(pool) do
        local key = pool_key(info)
        counts[key] = (counts[key] or 0) + 1
    end
    for _, card in ipairs(G.playing_cards) do
        -- A Tarot can leave a card in hand face down without the mark. The game then
        -- calls it played, but the player sees a card face down, so the mod doesn't.
        local marked_down = card.ability and card.ability.wheel_flipped
        if card.area == G.deck or marked_down or (card.area == G.hand and card.facing == 'back') then
            local key = pool_key(card_info(card, false))
            counts[key] = (counts[key] or 0) - 1
            game_total = game_total + 1
        end
    end
    local extra, missing = 0, 0
    for _, n in pairs(counts) do
        if n > 0 then extra = extra + n else missing = missing - n end
    end
    if extra > 0 or missing > 0 then
        local blind = G.GAME.blind and G.GAME.blind.name or 'no blind'
        report('unseen pool', string.format("the unseen pool doesn't match the game's: mod %d, game %d (%d extra, %d missing) in %s",
                                            #pool, game_total, extra, missing, blind))
    end
end

local function state_name(state)
    if G.STATES then
        for name, v in pairs(G.STATES) do if v == state then return name end end
    end
    return tostring(state)
end

function Calculatro.snapshot()
    if not (G and G.GAME and G.deck and G.hand and G.STAGE == G.STAGES.RUN) then
        return { schema = Calculatro.schema, in_run = false, memory_gb = Calculatro.memory_gb() }
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
        -- What the hand's order showed of its face-down cards, with the setting on.
        observations = Calculatro.export_observations(),
        -- The "Memory limit" setting as it applies here (memory.lua), for the app's memory limit.
        memory_gb = Calculatro.memory_gb(),
    }
end

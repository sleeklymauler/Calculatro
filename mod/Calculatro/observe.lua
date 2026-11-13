---------------------------------------------------------------------------
-- What the hand's order has shown the player about its face-down cards.
--
-- The game sorts the hand by its cards' true ranks and suits, face-down ones
-- included (Card:get_nominal ignores facing), so when the hand is known to be in
-- the game's order, that order says something about them. An observation is the
-- hand at such a moment, as the player saw it: the face-up cards' printed values,
-- and which card was face down where. Every sort seen this round is kept, less
-- those the others imply (`prune`).
--
-- It reads the game's state and hooks nothing. Whether the order is the game's
-- comes only from public events (draws, Sort presses, drags, consumables), never
-- from the face-down cards' values: asking if the hand is in its true order, or if
-- a face-down card changed, would tell what they are. See
-- docs/panel.md#face-down-hint.
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')
local array = require('calculatro.json').array
local ui_children = require('calculatro.uibox').ui_children

-- A list of {mode =, cards = {entry, ...}}, in the order recorded, where a mode
-- may repeat: a draw re-sorts the hand, but what an earlier sort said is still
-- true of the cards in it. `cards` are left to right. Each entry is one card,
-- `card`, and what the player saw of it: {latent = true} for a card face down, or
-- the printed {rank_id =, suit =} or {stone = true} of one face up. The card is
-- kept for face-up ones too, as the snapshot says which card in one sort is which
-- in another (the player sees them move into place). A face-down card since
-- discarded face down is still latent, and `discarded` too.
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

-- A face-up card's entry: what's printed on it.
local function printed_values(card)
    if card.config and card.config.center_key == 'm_stone' then return { card = card, stone = true } end
    return { card = card, rank_id = card.base.id, suit = card.base.suit }
end

local function same_entry(a, b)
    return a.latent == b.latent and a.stone == b.stone and a.rank_id == b.rank_id and a.suit == b.suit
end

-- Whether every entry of `a` is in `b`, seen alike and in the same order. For two
-- sorts of one mode, `b` then says all `a` does: its cards' order, and so their
-- places and the order of any that tie.
local function subsequence(a, b)
    local j = 1
    for _, entry in ipairs(a) do
        while b[j] and b[j].card ~= entry.card do j = j + 1 end
        if not (b[j] and same_entry(b[j], entry)) then return false end
        j = j + 1
    end
    return true
end

-- Whether an observation says anything: where a face-down card was (in hand or
-- since discarded), or the order of two cards alike in print (copies, or Stones),
-- which a card face down elsewhere may tie with. One of face-up cards all printed
-- differently fits every deal, and orders no tie.
local function says_something(observation)
    local printed = {}
    for _, entry in ipairs(observation.cards) do
        if entry.latent then return true end
        local key = entry.stone and 'stone' or (entry.rank_id .. entry.suit)
        if printed[key] then return true end
        printed[key] = true
    end
    return false
end

-- `list` without the observations that say nothing the rest don't, in the same
-- order: one that says nothing, and one another of its mode implies (of two the
-- same, the earlier is kept). Both are exact, and they keep a sort that shows
-- nothing new from changing anything, so the odds and a search aren't restarted.
local function prune(list)
    local kept = {}
    for i, o in ipairs(list) do
        local keep = says_something(o)
        for j, other in ipairs(list) do
            if not keep then break end
            if j ~= i and other.mode == o.mode and subsequence(o.cards, other.cards)
                and not (j > i and subsequence(other.cards, o.cards)) then
                keep = false
            end
        end
        if keep then kept[#kept + 1] = o end
    end
    return kept
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

-- Whether `card` is in the discard pile face down: the game turns every card
-- there over, so `facing` says nothing, and it marks those drawn face down with
-- wheel_flipped until they're turned up (snapshot.lua's face_down).
local function discarded_face_down(card, in_discard)
    return in_discard[card] and card.ability and card.ability.wheel_flipped and true or false
end

local function discard_set()
    return G.discard and set_of(G.discard.cards or {}) or {}
end

-- A face-down card that has since been turned up is its printed values, now
-- public. One discarded face down (The Hook's too) stays where it was seen, as
-- `discarded`: the game doesn't turn it up, and it's still the card that sat
-- there. One gone anywhere else face down is left out: what's left of a sorted
-- order is still the sorted order of what's left, so it's true, though it doesn't
-- use all the player saw.
local function update_entries(in_hand)
    local changed, in_discard = false, nil
    for _, observation in ipairs(Calculatro.observations) do
        local cards = observation.cards
        for i = #cards, 1, -1 do
            local entry = cards[i]
            local card = entry.card
            if entry.latent then
                if in_hand[card] then
                    if card.facing ~= 'back' then
                        cards[i] = printed_values(card)
                        changed = true
                    elseif entry.discarded then
                        entry.discarded = nil
                        changed = true
                    end
                else
                    in_discard = in_discard or discard_set()
                    if discarded_face_down(card, in_discard) then
                        if not entry.discarded then
                            entry.discarded = true
                            changed = true
                        end
                    elseif card.facing ~= 'back' and not in_discard[card] then
                        cards[i] = printed_values(card)  -- played
                        changed = true
                    else
                        table.remove(cards, i)
                        changed = true
                    end
                end
            end
        end
    end
    if changed then
        Calculatro.observations = prune(Calculatro.observations)
        Calculatro.observed = Calculatro.observed + 1
    end
end

-- The hand as an observation, or nil if its face-up cards aren't in order for
-- `mode`, which the events should have ruled out: a check that leaks nothing,
-- as it uses printed values only.
local function observe(cards, mode)
    local entries, last = {}, nil
    for i, card in ipairs(cards) do
        if card.facing == 'back' then
            entries[i] = { card = card, latent = true }
        else
            local printed = printed_values(card)
            local key = sort_key(mode, printed)
            if last and key > last then return nil end
            entries[i], last = printed, key
        end
    end
    return { mode = mode, cards = entries }
end

-- Records `observation`, after the others. A card in several sorts must be seen
-- alike in all, as the snapshot gives it one id. Only a change the mod doesn't know
-- of (a consumable ends all the evidence) could break that, and it's public anyway,
-- so a sort that saw it otherwise is dropped as out of date. Nothing changes if the
-- sorts kept already say all this one does.
local function record(observation)
    local seen = {}
    for _, entry in ipairs(observation.cards) do seen[entry.card] = entry end
    local list = {}
    for _, other in ipairs(Calculatro.observations) do
        local alike = true
        for _, entry in ipairs(other.cards) do
            local now = seen[entry.card]
            if now and not same_entry(now, entry) then
                alike = false
                break
            end
        end
        if alike then list[#list + 1] = other end
    end
    list[#list + 1] = observation
    list = prune(list)
    -- Pruning only drops, and this one is last if it's kept.
    if list[#list] == observation or #list ~= #Calculatro.observations then
        Calculatro.observations = list
        Calculatro.observed = Calculatro.observed + 1
    end
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
            if observation then record(observation) end
            t.pending = false
        end
    end

    if not stable then t.hand, t.in_hand = copy(cards), in_hand end
    if not stable or #G.deck.cards ~= t.deck_count then
        t.in_deck, t.deck_count = set_of(G.deck.cards), #G.deck.cards
    end
    t.dragged = dragging
end

-- The observations as the snapshot sends them, in the order recorded: each card
-- with an id, numbered by first appearance so that it says nothing the order
-- doesn't, and the same for a card in several. A face-down card is given by its place in the
-- hand now (0-based), where the snapshot sends it as {hidden = true}, and one
-- discarded face down only as `discarded` (it's one of the discard pile's
-- {hidden = true}, but which isn't said). Sent whether the hint's setting is on
-- or off: the odds always use them. See docs/architecture.md#shapes.
-- The odds take face-down cards under The Mark to be face cards (as
-- `engine.flipped_by_mark` says), which the challenges' random flips
-- (flipped_cards) make untrue: then the sorts go unused, and the odds are those
-- without them. The hint, which allows every card then, still uses them.
local function mark_with_random_flips()
    local blind = G.GAME and G.GAME.blind
    return blind and blind.name == 'The Mark' and not blind.disabled
        and G.GAME.modifiers and G.GAME.modifiers.flipped_cards and true or false
end

function Calculatro.export_observations()
    local out = array()
    if not (G and G.hand) or mark_with_random_flips() then return out end
    local index = {}
    for i, card in ipairs(G.hand.cards or {}) do index[card] = i - 1 end
    local in_discard = discard_set()
    local ids, count = {}, 0
    for _, observation in ipairs(Calculatro.observations) do
        local cards = array()
        for _, entry in ipairs(observation.cards) do
            local card = entry.card
            -- update_entries has turned up or dropped any face-down card no
            -- longer face down in hand or the discard pile; one that isn't
            -- would be left out.
            local in_hand = entry.latent and index[card] and card.facing == 'back'
            local discarded = entry.latent and not in_hand and entry.discarded
                and discarded_face_down(card, in_discard)
            if not entry.latent or in_hand or discarded then
                if not ids[card] then ids[card], count = count, count + 1 end
                local e = { id = ids[card] }
                if in_hand then
                    e.hand = index[card]
                elseif discarded then
                    e.discarded = true
                elseif entry.stone then
                    e.stone = true
                else
                    e.rank_id, e.suit = entry.rank_id, entry.suit
                end
                cards[#cards + 1] = e
            end
        end
        out[#out + 1] = { mode = observation.mode, cards = cards }
    end
    return out
end

return { sort_key = sort_key }

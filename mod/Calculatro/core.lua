-- The table the mod's modules share: its settings, and what it remembers from one
-- frame to the next. Each module adds its functions to it and they call each other
-- through it, so it's all one namespace, and it's what require('calculatro') returns.

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
    write_failures = 0,  -- snapshots in a row that couldn't be written
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

return Calculatro

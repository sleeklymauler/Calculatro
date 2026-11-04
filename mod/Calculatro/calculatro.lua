-- Calculatro: exports live Balatro game state to calculatro_state.json in the
-- save directory (%AppData%\Balatro) for the Python companion app, and shows
-- the odds it writes back to calculatro_odds.json in a panel above the deck.

-- This module is what the game's hooks call (see lovely.toml). The rest are its
-- parts, each requiring the ones it needs: core (shared table), json, tracked,
-- builder, snapshot, search, files, log, app, widgets, uibox, anim, odds, editor,
-- panel. Every one must be registered in lovely.toml, under calculatro.<name>:
-- other mods share package.preload, and some of them have a json too.

local Calculatro = require('calculatro.core')
local guard = require('calculatro.log').guard
require('calculatro.files')
require('calculatro.search')
require('calculatro.app')
require('calculatro.panel')

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

return Calculatro

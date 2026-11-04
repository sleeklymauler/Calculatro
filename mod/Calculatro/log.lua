-- The mod's errors: to the Lovely console and the app's log, and never into the game.

local Calculatro = require('calculatro.core')

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
    local header = ''
    if not Calculatro.log_started then
        header = '=== Calculatro mod, session ' .. Calculatro.session .. ', ' .. os.date('%Y-%m-%d %H:%M:%S') .. '\n'
    end
    if love.filesystem.append('calculatro.log', header .. '[mod] ' .. hide_home(text) .. '\n') then
        Calculatro.log_started = true
    end
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

return { report = report, guard = guard }

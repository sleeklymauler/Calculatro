local Calculatro = require('calculatro.core')
local files = require('calculatro.files')
local log = require('calculatro.log')

local save_path, windows, wide = files.save_path, files.windows, files.wide
local report, guard = log.report, log.guard

---------------------------------------------------------------------------
-- Starting the app
---------------------------------------------------------------------------
-- install_mod.ps1 leaves the Python interpreter to run (the venv's
-- pythonw.exe) in the mod's launch.txt. Without one, a release's own Python
-- (build_release.ps1 puts it and the app in the mod's python folder) is run if
-- it's there. The app exits with the game (by its pid), and on its own if one
-- is already running, so starting it again when it's there does nothing (see
-- `check_app`).
function Calculatro.launch_command(pid)
    local exe
    local text = love.filesystem.read('Mods/Calculatro/launch.txt')
    if text then
        exe = text:match('^[^\n]*'):match('^%s*(.-)%s*$')
        if exe == '' then return end
    else
        if not love.filesystem.getInfo('Mods/Calculatro/python/pythonw.exe') then return end
        exe = save_path('Mods/Calculatro/python/pythonw.exe'):gsub('/', '\\')
    end
    return exe, '-m calculatro watch --game-pid ' .. string.format('%d', pid)
end

-- Through Windows itself rather than os.execute, whose cmd.exe would flash a
-- console window over the game. Returns true if Windows started it, which
-- says nothing about whether it's still running a moment later.
function Calculatro.launch()
    local ffi = windows()
    if not ffi then return false end  -- not LuaJIT (the tests)
    local exe, params = Calculatro.launch_command(ffi.C.GetCurrentProcessId())
    if not exe then
        error("nothing to start: no launch.txt or python folder in Mods/Calculatro (is that the mod's folder name?)")
    end
    local result = ffi.load('shell32').ShellExecuteW(nil, wide(ffi, 'open'), wide(ffi, exe), wide(ffi, params), nil, 0)
    local code = tonumber(ffi.cast('intptr_t', result))
    if code <= 32 then
        error(string.format('could not start %s (ShellExecuteW error %d)', exe, code))
    end
    return true
end

-- Seconds without an answer before starting the app again, each counted from
-- the last start. Nothing notices it's gone but its silence: ShellExecuteW
-- succeeds even if Python dies straight away, or once it crashes later on.
-- Each start stalls a frame, so it stops after these until the app answers.
local RESTART_AFTER = { 5, 15, 45 }
local FAILED_AFTER = 2  -- restarts before the panel says so, about 20 seconds in

function Calculatro.start_app()
    Calculatro.app_started = false
    guard('starting the app', function() Calculatro.app_started = Calculatro.launch() end)
    Calculatro.silent_since = Calculatro.clock
end

-- Whether the panel should say the app isn't coming: Windows couldn't start it,
-- or it's been started again and again without answering.
function Calculatro.app_failed()
    return not Calculatro.app_started or Calculatro.restarts >= FAILED_AFTER
end

-- An answer to the latest snapshot means it's running, whoever started it, and
-- it gets restarts afresh if it stops later. A start Windows refused isn't
-- tried again: a path that's wrong now will still be wrong in 5 seconds.
function Calculatro.check_app()
    if Calculatro.state_id and Calculatro.ack_id == Calculatro.state_id then
        Calculatro.app_started, Calculatro.restarts, Calculatro.silent_since = true, 0, Calculatro.clock
        return
    end
    if not Calculatro.app_started then return end
    local wait = RESTART_AFTER[Calculatro.restarts + 1]
    if wait and Calculatro.clock - Calculatro.silent_since >= wait then
        Calculatro.restarts = Calculatro.restarts + 1
        Calculatro.start_app()
        if Calculatro.app_started and Calculatro.restarts >= FAILED_AFTER then
            local exe = Calculatro.launch_command(0)
            report('the app answering', string.format('the app is not answering after %d starts of %s',
                                                      Calculatro.restarts + 1, tostring(exe)))
        end
    end
end

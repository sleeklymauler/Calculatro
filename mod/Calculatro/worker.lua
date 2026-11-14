-- The thread that writes the state and settings files and reads the odds file. Creating
-- a file now and then takes Windows hundreds of milliseconds, which on the game's main
-- thread is a visible freeze. Reading has to be here too: love.filesystem takes a lock
-- (PhysFS's) that a write stalled in another thread holds, so a read would wait just as long.

local Calculatro = require('calculatro.core')

local REQUESTS, RESULTS = 'calculatro_state_requests', 'calculatro_state_results'
-- LOVE doesn't stop a thread when the game restarts (love.event.quit('restart'), as
-- Steamodded does after its mods are changed), and named channels outlive the restart
-- with it. So each start numbers its thread, in this channel, and names the thread's
-- channels by that number: an older thread finds the number changed and ends, and
-- whatever it was still doing goes to channels nobody reads.
local GENERATIONS = 'calculatro_file_thread'
-- The files, relative to the save directory (see Calculatro.data_dir).
local DATA_DIR, FILES = Calculatro.data_dir, Calculatro.data_files
local STATE_FILE = DATA_DIR .. '/' .. FILES.state
local ODDS_FILE = DATA_DIR .. '/' .. FILES.odds
local SETTINGS_FILE = DATA_DIR .. '/' .. FILES.settings

-- A thread has a Lua state of its own, so none of the mod's modules. An error
-- escaping a thread would stop the game (love.threaderror), so nothing may: this
-- reports it, and ends, and the mod goes back to writing the file itself.
-- With a newline in it, love.thread.newThread takes this for code, not a file name.
local CODE = [[
local REQUESTS, RESULTS, INTERVAL, GENERATIONS, GENERATION, DATA_DIR, STATE_FILE, ODDS_FILE, SETTINGS_FILE = ...
local results

-- Writes a file in the data folder, creating the folder again if the player deleted it.
local function write_file(name, text)
    local ok, err = love.filesystem.write(name, text)
    if ok or not love.filesystem.createDirectory(DATA_DIR) then return ok, err end
    return love.filesystem.write(name, text)
end

local function setup()
    require('love.filesystem')
    local ffi = require('ffi')
    ffi.cdef [=[
        unsigned long GetLastError(void);
        int MoveFileExW(const uint16_t *from, const uint16_t *to, unsigned long flags);
        int MultiByteToWideChar(unsigned int page, unsigned long flags, const char *s, int n,
                                uint16_t *out, int size);
    ]=]
    local function wide(s)
        local n = ffi.C.MultiByteToWideChar(65001, 0, s, -1, nil, 0)
        local out = ffi.new('uint16_t[?]', n)
        ffi.C.MultiByteToWideChar(65001, 0, s, -1, out, n)
        return out
    end
    local dir = love.filesystem.getSaveDirectory() .. '/'
    local tmp = STATE_FILE .. '.tmp'
    return function(json)
        local ok, err = write_file(tmp, json)
        if not ok then return false, tostring(err) end
        if ffi.C.MoveFileExW(wide(dir .. tmp), wide(dir .. STATE_FILE), 1) ~= 0 then return true end
        return false, string.format('MoveFileExW error %d', tonumber(ffi.C.GetLastError()))
    end
end

local function run()
    require('love.thread')
    local requests = love.thread.getChannel(REQUESTS)
    results = love.thread.getChannel(RESULTS)
    local generations = love.thread.getChannel(GENERATIONS)
    local write = setup()
    local last_odds
    while true do
        local job = requests:demand(INTERVAL)  -- nil once that's passed, with nothing to write
        if generations:peek() ~= GENERATION then return end  -- the game restarted, and started another
        -- A newer snapshot makes any older one pointless, and newer settings older ones.
        local snapshot, settings
        while job ~= nil do
            if job.settings then settings = job.settings else snapshot = job end
            job = requests:pop()
        end
        if settings then
            local done, ok, err = pcall(write_file, SETTINGS_FILE, settings)
            if not (done and ok) then results:push({ settings_failed = tostring(done and err or ok) }) end
        end
        if snapshot then
            local done, ok, err = pcall(write, snapshot.json)
            results:push({ id = snapshot.id, ok = done and ok or false, err = tostring(done and err or ok) })
        end
        local done, odds = pcall(love.filesystem.read, ODDS_FILE)
        if done and odds and odds ~= last_odds then
            last_odds = odds
            results:push({ odds = odds })
        end
    end
end

local fine, err = pcall(run)
if not fine and results then pcall(results.push, results, { failed = tostring(err) }) end
]]

-- The thread's channels and the thread, started once; nil if it can't be, as when
-- there's no love.thread (the tests that don't pretend there is).
local function start()
    if Calculatro.worker == nil then
        Calculatro.worker = false
        local threads = love.thread
        if threads then
            local generations = threads.getChannel(GENERATIONS)
            local generation = (generations:peek() or 0) + 1
            generations:clear()
            generations:push(generation)
            local requests, results = REQUESTS .. generation, RESULTS .. generation
            local ok, thread = pcall(threads.newThread, CODE)
            if ok and pcall(thread.start, thread, requests, results, Calculatro.odds_interval, GENERATIONS,
                            generation, DATA_DIR, STATE_FILE, ODDS_FILE, SETTINGS_FILE) then
                Calculatro.worker = { thread = thread, requests = threads.getChannel(requests),
                                      results = threads.getChannel(results) }
            end
        end
    end
    return Calculatro.worker or nil
end

-- The same, on the game's thread, for when the file thread isn't running.
local function write_file(name, text)
    local ok, err = love.filesystem.write(name, text)
    if ok or not love.filesystem.createDirectory(DATA_DIR) then return ok, err end
    return love.filesystem.write(name, text)
end

return { start = start, write_file = write_file, DATA_DIR = DATA_DIR, STATE_FILE = STATE_FILE, ODDS_FILE = ODDS_FILE,
         SETTINGS_FILE = SETTINGS_FILE }

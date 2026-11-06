local Calculatro = require('calculatro.core')
local json = require('calculatro.json')
local worker = require('calculatro.worker')
local report = require('calculatro.log').report
require('calculatro.snapshot')
require('calculatro.search')

local encode, decode = json.encode, json.decode

---------------------------------------------------------------------------
-- Files
---------------------------------------------------------------------------
-- Files go through love.filesystem, with names relative to the save directory,
-- or Windows' wide functions, never Lua's own: the save directory's path is
-- UTF-8, which those read in the system's ANSI code page, so they can't find it
-- if the Windows username isn't plain ASCII.
local function save_path(name)
    return love.filesystem.getSaveDirectory() .. '/' .. name
end

-- LuaJIT's FFI with the Windows functions the mod calls declared, or nil outside
-- LuaJIT (the tests). Another mod declaring these too is fine: LuaJIT lets a
-- function be declared again, unlike a struct.
local function windows()
    local ok, ffi = pcall(require, 'ffi')
    if not ok then return nil end
    if not Calculatro.declared then
        ffi.cdef [[
            unsigned long GetCurrentProcessId(void);
            unsigned long GetLastError(void);
            int MoveFileExW(const uint16_t *from, const uint16_t *to, unsigned long flags);
            int MultiByteToWideChar(unsigned int page, unsigned long flags, const char *s, int n,
                                    uint16_t *out, int size);
            void *ShellExecuteW(void *hwnd, const uint16_t *op, const uint16_t *file,
                                const uint16_t *params, const uint16_t *dir, int show);
        ]]
        Calculatro.declared = true
    end
    return ffi
end

local function wide(ffi, s)
    local n = ffi.C.MultiByteToWideChar(65001, 0, s, -1, nil, 0)
    local out = ffi.new('uint16_t[?]', n)
    ffi.C.MultiByteToWideChar(65001, 0, s, -1, out, n)
    return out
end

-- Replaces one file in the save directory with another in one step, so the app
-- never finds the file missing. It fails while the app has the file open; the
-- next snapshot tries again.
function Calculatro.replace_file(from, to)
    local ffi = windows()
    if not ffi then return false, 'no FFI' end
    local MOVEFILE_REPLACE_EXISTING = 1
    if ffi.C.MoveFileExW(wide(ffi, save_path(from)), wide(ffi, save_path(to)), MOVEFILE_REPLACE_EXISTING) ~= 0 then
        return true
    end
    return false, string.format('MoveFileExW error %d', tonumber(ffi.C.GetLastError()))
end

local function write_state(json)
    local path = 'calculatro_state.json'
    local ok, err = love.filesystem.write(path .. '.tmp', json)
    if not ok then return false, err end
    return Calculatro.replace_file(path .. '.tmp', path)
end

-- Snapshots in a row that couldn't be written (2 seconds' worth) before it's
-- reported: one now and then is normal, while the app is reading the file.
local WRITE_FAILURES = 10

local function write_succeeded(id)
    Calculatro.state_id = id
    Calculatro.changed_at = Calculatro.clock
    Calculatro.write_failures = 0
end

local function write_failed(err)
    Calculatro.write_failures = Calculatro.write_failures + 1
    if Calculatro.write_failures >= WRITE_FAILURES then
        error('could not write the state file: ' .. tostring(err))
    end
end

-- Writes the snapshot if it changed, tagged with a new id: on the worker's thread
-- (`Calculatro.check_worker` hears how it went), or here if there isn't one.
function Calculatro.write_snapshot()
    local snapshot = Calculatro.snapshot()
    local json = encode(snapshot)
    if json == Calculatro.last_json then return end
    Calculatro.seq = Calculatro.seq + 1
    snapshot.id = Calculatro.session .. ':' .. Calculatro.seq
    local thread = worker.start()
    if thread then
        thread.requests:push({ id = snapshot.id, json = encode(snapshot) })
        Calculatro.last_json, Calculatro.writing = json, snapshot.id
        return
    end
    local ok, err = write_state(encode(snapshot))
    if ok then
        Calculatro.last_json = json
        write_succeeded(snapshot.id)
    else
        write_failed(err)
    end
end

local function own_id(id)
    local prefix = Calculatro.session .. ':'
    return type(id) == 'string' and id:sub(1, #prefix) == prefix
end

-- Odds are for one round's hand: once another round starts (G.GAME.round
-- counts the blinds played), those for snapshots taken before it are dropped,
-- so the panel shows '-' rather than the last round's odds while the first
-- hand is dealt (the app only answers once it is). Snapshots after
-- `round_seq` were taken in this round; checked before each snapshot.
local function this_round(id)
    local seq = own_id(id) and tonumber(id:match(':(%d+)$'))
    return seq and seq > Calculatro.round_seq or false
end

function Calculatro.check_round()
    local round = G and G.GAME and G.GAME.round
    if round == Calculatro.round then return end
    Calculatro.round, Calculatro.round_seq = round, Calculatro.seq
    if Calculatro.odds and not this_round(Calculatro.odds.id) then Calculatro.odds = nil end
end

-- The app answers each snapshot first with {id, pending = true}, then with
-- the odds once they're calculated. Odds left over from an earlier game
-- session or round are ignored.
local function receive_odds(raw)
    if not raw or raw == Calculatro.odds_raw then return end
    Calculatro.odds_raw = raw
    local ok, odds = pcall(decode, raw)
    if not ok or type(odds) ~= 'table' or odds.schema ~= Calculatro.odds_schema or not own_id(odds.id) then
        return
    end
    Calculatro.ack_id = odds.id
    if not odds.pending and this_round(odds.id) then Calculatro.odds = odds end
    Calculatro.receive_search(odds.search)
end

function Calculatro.read_odds()
    if worker.start() then return end  -- `check_worker` gets them from the thread
    receive_odds(love.filesystem.read('calculatro_odds.json'))
end

-- What the worker's thread has done, each frame. Only the latest snapshot's failure
-- matters: clearing last_json sends it again with the next snapshot. If the thread
-- has stopped, the mod goes back to using the files itself, starting with whatever
-- the thread didn't get to.
function Calculatro.check_worker()
    local thread = Calculatro.worker
    if not thread then return end
    while true do
        local result = thread.results:pop()
        if not result then break end
        if result.odds then
            receive_odds(result.odds)
        elseif result.failed then
            report('the file thread', 'the file thread stopped: ' .. result.failed)
        elseif result.ok then
            write_succeeded(result.id)
        elseif result.id == Calculatro.writing then
            Calculatro.last_json = nil
            write_failed(result.err)
        end
    end
    if not thread.thread:isRunning() then
        Calculatro.worker, Calculatro.last_json = false, nil
    end
end

return { save_path = save_path, windows = windows, wide = wide }

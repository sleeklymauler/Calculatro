---------------------------------------------------------------------------
-- The "Cores used" setting's choices: how many of the CPU's cores the app's
-- calculations may use. 0 is Auto, where the app chooses from the cores it may
-- use and the memory limit (process.auto_cores); 1 is the app on its own; more
-- are that many worker processes. One core is always left to the game, so the
-- most offered is one fewer than the computer has, up to 8, which the app
-- holds to as well (process.cores_used).
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')
require('calculatro.memory')

local MOST = 8

-- The computer's logical cores, or nil if the game's engine can't say.
function Calculatro.processor_count()
    local ok, count = pcall(function() return love.system.getProcessorCount() end)
    if ok and type(count) == 'number' and count >= 1 then return math.floor(count) end
end

-- What the setting offers, Auto (0) first: never fewer than Auto and 1.
function Calculatro.cores_choices()
    local count = Calculatro.processor_count()
    local out = { 0 }
    for n = 1, math.max(1, math.min(MOST, (count or 1) - 1)) do out[#out + 1] = n end
    return out
end

-- How many cores Auto uses here, as the app works it out (process.auto_cores):
-- a quarter of the computer's, 4 at most, and no more than the memory limit has
-- half a GB for each; nil if the cores can't be counted. For the setting's
-- popup only: the app decides, and counts only the cores the game may run on,
-- which the game's engine can't see.
function Calculatro.auto_cores()
    local count = Calculatro.processor_count()
    if not count then return nil end
    return math.max(1, math.min(math.floor(count / 4), 4, Calculatro.memory_gb() * 2))
end

-- The setting as it applies here: the largest choice up to the saved one, so
-- a saved 8 shows (and is sent as) 3 on a computer with 4 cores, and comes back
-- on one with more. The file keeps what was saved.
function Calculatro.cores()
    local saved, cores = Calculatro.settings.cores, 0
    for _, c in ipairs(Calculatro.cores_choices()) do
        if c <= saved then cores = c end
    end
    return cores
end

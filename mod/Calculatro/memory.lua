---------------------------------------------------------------------------
-- The "Memory limit" setting's choices: the most the app's calculations may use, in
-- GB. Only those up to half the computer's memory are offered, which the app
-- holds to as well (process.memory_limit), so the panel never shows a limit
-- the app won't keep.
---------------------------------------------------------------------------
local Calculatro = require('calculatro.core')

local GB = 2 ^ 30
local CHOICES = { 1, 2, 4, 8 }
local UNKNOWN = { 1, 2 }  -- without the computer's memory: up to the default

local installed  -- the computer's memory in bytes once read, false if it can't be

-- The computer's memory in bytes, as installed (16 GB for 16 GB, where Windows
-- reports a little less as usable), or what Windows can use if it won't say
-- that, as the app reads it (process.installed_memory); nil if neither, or
-- outside LuaJIT. Read once: it doesn't change while the game runs.
function Calculatro.installed_memory()
    if installed == nil then
        installed = false
        local ok, bytes = pcall(function()
            local ffi = require('ffi')
            -- Functions only, through void and integer pointers: LuaJIT lets
            -- another mod declare a function again, unlike a struct.
            ffi.cdef [[
                int GetPhysicallyInstalledSystemMemory(uint64_t *kb);
                int GlobalMemoryStatusEx(void *status);
            ]]
            local kb = ffi.new('uint64_t[1]')
            if ffi.C.GetPhysicallyInstalledSystemMemory(kb) ~= 0 and kb[0] > 0 then
                return tonumber(kb[0]) * 1024
            end
            -- MEMORYSTATUSEX: two 32-bit fields (its length, then the load), then
            -- seven 64-bit ones, the first the usable memory.
            local status = ffi.new('uint64_t[8]')
            status[0] = 64
            if ffi.C.GlobalMemoryStatusEx(status) ~= 0 and status[1] > 0 then return tonumber(status[1]) end
        end)
        if ok and bytes then installed = bytes end
    end
    return installed or nil
end

-- The sizes the setting offers, in GB, smallest first: never none, since 1 GB
-- is always offered.
function Calculatro.memory_choices()
    local bytes = Calculatro.installed_memory()
    if not bytes then return UNKNOWN end
    local out = {}
    for _, gb in ipairs(CHOICES) do
        if gb == 1 or gb * GB <= bytes / 2 then out[#out + 1] = gb end
    end
    return out
end

-- The setting as it applies here: the largest choice up to the saved one, so
-- a saved 8 GB shows (and is sent as) 4 GB on a computer with 8, and comes back
-- if the memory does. The file keeps what was saved.
function Calculatro.memory_gb()
    local saved, choices = Calculatro.settings.memory_gb, Calculatro.memory_choices()
    local gb = choices[1]
    for _, c in ipairs(choices) do
        if c <= saved then gb = c end
    end
    return gb
end

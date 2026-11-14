---------------------------------------------------------------------------
-- Minimal JSON encoder. Tables tagged with ARRAY (or non-empty 1..n tables)
-- become arrays, so an empty hand still encodes as [] rather than {}.
---------------------------------------------------------------------------
local ARRAY = {}
local function array(t) return setmetatable(t or {}, ARRAY) end
local escapes = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
                  ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }

local function encode_string(s)
    return '"' .. s:gsub('[%c"\\]', function(c)
        -- Some locales count bytes of UTF-8 sequences as control characters.
        if c:byte() >= 128 then return c end
        return escapes[c] or string.format('\\u%04x', c:byte())
    end) .. '"'
end

local function is_array(t)
    local n = 0
    for _ in pairs(t) do n = n + 1 end
    for i = 1, n do if t[i] == nil then return false end end
    return true
end

local function encode(v)
    local tv = type(v)
    if v == nil then return 'null'
    elseif tv == 'boolean' then return tostring(v)
    elseif tv == 'number' then
        if v ~= v or v == math.huge or v == -math.huge then return 'null' end
        if v == math.floor(v) and math.abs(v) < 1e15 then return string.format('%d', v) end
        return string.format('%.14g', v)
    elseif tv == 'string' then return encode_string(v)
    elseif tv == 'table' then
        local out = {}
        if getmetatable(v) == ARRAY or (next(v) ~= nil and is_array(v)) then
            for i = 1, #v do out[i] = encode(v[i]) end
            return '[' .. table.concat(out, ',') .. ']'
        end
        local keys = {}
        for k in pairs(v) do keys[#keys + 1] = k end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)  -- stable output
        for _, k in ipairs(keys) do
            out[#out + 1] = encode_string(tostring(k)) .. ':' .. encode(v[k])
        end
        return '{' .. table.concat(out, ',') .. '}'
    end
    return 'null'
end

---------------------------------------------------------------------------
-- Minimal JSON decoder for the odds file. Arrays come back tagged ARRAY;
-- null is not supported (the app never writes it).
---------------------------------------------------------------------------
local unescapes = { ['"'] = '"', ['\\'] = '\\', ['/'] = '/', b = '\b', f = '\f',
                    n = '\n', r = '\r', t = '\t' }

local function utf8_char(cp)
    if cp < 0x80 then return string.char(cp) end
    if cp < 0x800 then return string.char(0xC0 + math.floor(cp / 64), 0x80 + cp % 64) end
    return string.char(0xE0 + math.floor(cp / 4096), 0x80 + math.floor(cp / 64) % 64, 0x80 + cp % 64)
end

local function decode(s)
    local pos = 1
    local function fail(what) error('JSON: ' .. what .. ' at ' .. pos, 0) end
    local function skip() pos = s:find('[^ \t\r\n]', pos) or #s + 1 end
    local value

    local function string_value()
        local out = {}
        pos = pos + 1
        while true do
            local i = s:find('["\\]', pos)
            if not i then fail('unterminated string') end
            out[#out + 1] = s:sub(pos, i - 1)
            if s:sub(i, i) == '"' then
                pos = i + 1
                return table.concat(out)
            end
            local c = s:sub(i + 1, i + 1)
            if c == 'u' then
                local cp = tonumber(s:sub(i + 2, i + 5), 16) or fail('bad escape')
                out[#out + 1] = utf8_char(cp)
                pos = i + 6
            else
                out[#out + 1] = unescapes[c] or fail('bad escape')
                pos = i + 2
            end
        end
    end

    local function list(close, item)
        pos = pos + 1
        skip()
        if s:sub(pos, pos) == close then pos = pos + 1; return end
        while true do
            item()
            skip()
            local c = s:sub(pos, pos)
            pos = pos + 1
            if c == close then return end
            if c ~= ',' then fail('expected , or ' .. close) end
        end
    end

    function value()
        skip()
        local c = s:sub(pos, pos)
        if c == '{' then
            local t = {}
            list('}', function()
                skip()
                if s:sub(pos, pos) ~= '"' then fail('expected key') end
                local k = string_value()
                skip()
                if s:sub(pos, pos) ~= ':' then fail('expected :') end
                pos = pos + 1
                t[k] = value()
            end)
            return t
        elseif c == '[' then
            local t = array()
            list(']', function() t[#t + 1] = value() end)
            return t
        elseif c == '"' then
            return string_value()
        end
        for word, v in pairs({ ['true'] = true, ['false'] = false }) do
            if s:sub(pos, pos + #word - 1) == word then pos = pos + #word; return v end
        end
        local num = s:match('^-?%d+%.?%d*[eE]?[-+]?%d*', pos)
        if not num or num == '' then fail('unexpected ' .. (c == '' and 'end' or c)) end
        pos = pos + #num
        return tonumber(num) or fail('bad number')
    end

    local v = value()
    skip()
    if pos <= #s then fail('trailing data') end
    return v
end

return { array = array, encode = encode, decode = decode }

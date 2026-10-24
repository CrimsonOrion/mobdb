--[[
* /mobdb query item <name|id> [here | in <zone>]
* Lists the mobs that drop (or can be robbed of) an item, optionally only in some zones, using data/drops.lua
* written by tools/update_lsb_data.py. The index is loaded on first use.
]]--

local query = {
    Index = nil,
    MaxZoneLines = 20,
    MaxMatches = 10,
};

local resMgr = AshitaCore:GetResourceManager();

local function PrintError(text)
    print(chat.header('MobDB') .. chat.error(text));
end

local function LoadIndex(self)
    if self.Index then
        return self.Index;
    end

    local path = string.format('%saddons/mobdb/data/drops.lua', AshitaCore:GetInstallPath());
    if not ashita.fs.exists(path) then
        PrintError('No drop index found. Regenerate data with tools/update_lsb_data.py to create data/drops.lua.');
        return nil;
    end

    local chunk, loadError = loadfile(path);
    if not chunk then
        PrintError('Failed to load drop index: ' .. tostring(loadError));
        return nil;
    end

    local success, output = pcall(chunk);
    if not success or type(output) ~= 'table' then
        PrintError('Failed to read drop index: ' .. tostring(output));
        return nil;
    end

    self.Index = output;
    return output;
end

local function ItemName(itemId)
    local item = resMgr:GetItemById(itemId);
    if item and item.Name and item.Name[1] then
        return item.Name[1];
    end
    return string.format('Item %u', itemId);
end

local function ZoneName(zoneId)
    local name = resMgr:GetString(gCompatibility.Resource.Zone, zoneId);
    if (type(name) == 'string') and (name ~= '') then
        return (string.gsub(name, '%z', ''));
    end
    return string.format('Zone %u', zoneId);
end

-- Returns a list of item ids matching the search text: an id, an exact name, or a partial name.
local function FindItems(index, text)
    local itemId = tonumber(text);
    if itemId then
        return { itemId };
    end

    local item = resMgr:GetItemByName(text, 0);
    if item and item.Id and (item.Id > 0) then
        return { item.Id };
    end

    local needle = string.lower(text);
    local exact, partial = {}, {};
    for id, _ in pairs(index) do
        local name = string.lower(ItemName(id));
        if (name == needle) then
            table.insert(exact, id);
        elseif string.find(name, needle, 1, true) then
            table.insert(partial, id);
        end
    end
    if (#exact > 0) then
        return exact;
    end
    table.sort(partial, function(a, b) return ItemName(a) < ItemName(b) end);
    return partial;
end

local CURRENT_ZONE_WORDS = { ['here'] = true, ['current'] = true, ['current zone'] = true };
local MAX_ZONE_ID = 300;

local function CurrentZone()
    return AshitaCore:GetMemoryManager():GetParty():GetMemberZone(0);
end

-- Splits the words after 'item' into the item text and an optional zone text.
-- '<item> in <zone>' names a zone; a trailing 'here', 'current' or 'current zone' means the current zone.
local function SplitItemAndZone(words)
    for i = #words - 1, 2, -1 do
        if (string.lower(words[i]) == 'in') then
            return table.concat(words, ' ', 1, i - 1), table.concat(words, ' ', i + 1);
        end
    end

    local count = #words;
    if (count >= 3) and (string.lower(words[count - 1]) == 'current') and (string.lower(words[count]) == 'zone') then
        return table.concat(words, ' ', 1, count - 2), 'current zone';
    end
    if (count >= 2) and CURRENT_ZONE_WORDS[string.lower(words[count])] then
        return table.concat(words, ' ', 1, count - 1), words[count];
    end
    return table.concat(words, ' '), nil;
end

-- Returns a set of zone ids for the zone text (current zone, an id, an exact name, or a partial name).
local function FindZones(text)
    if CURRENT_ZONE_WORDS[string.lower(text)] then
        return { [CurrentZone()] = true };
    end

    local zoneId = tonumber(text);
    if zoneId then
        return { [zoneId] = true };
    end

    local needle = string.lower(text);
    local exact, partial = {}, {};
    for id = 1, MAX_ZONE_ID do
        local name = resMgr:GetString(gCompatibility.Resource.Zone, id);
        if (type(name) == 'string') and (name ~= '') then
            name = string.lower(string.gsub(name, '%z', ''));
            if (name == needle) then
                exact[id] = true;
            elseif string.find(name, needle, 1, true) then
                partial[id] = true;
            end
        end
    end
    if next(exact) then
        return exact;
    end
    return partial;
end

local function PrintResults(self, itemId, entries, zoneFilter, zoneText)
    local currentZone = CurrentZone();

    local zones = {};
    local zoneOrder = {};
    local count = 0;
    for _, entry in ipairs(entries) do
        if (zoneFilter == nil) or zoneFilter[entry.Zone] then
            if not zones[entry.Zone] then
                zones[entry.Zone] = {};
                table.insert(zoneOrder, entry.Zone);
            end
            table.insert(zones[entry.Zone], entry);
            count = count + 1;
        end
    end

    if (count == 0) then
        print(chat.header('MobDB') .. chat.message('No mobs with ') .. chat.color1(2, ItemName(itemId))
            .. chat.message(' in ') .. chat.color1(2, zoneText) .. chat.message('.'));
        return;
    end

    -- Current zone first, then alphabetical by zone name.
    table.sort(zoneOrder, function(a, b)
        if (a == currentZone) ~= (b == currentZone) then
            return (a == currentZone);
        end
        return ZoneName(a) < ZoneName(b);
    end);

    print(chat.header('MobDB') .. chat.message('Mobs with ') .. chat.color1(2, ItemName(itemId))
        .. chat.message(string.format(': %u in %u zone%s', count, #zoneOrder, (#zoneOrder == 1) and '' or 's')));

    for i, zoneId in ipairs(zoneOrder) do
        if (i > self.MaxZoneLines) then
            print(chat.header('MobDB') .. chat.message(string.format('...and %u more zones.', #zoneOrder - self.MaxZoneLines)));
            break;
        end
        local mobs = {};
        for _, entry in ipairs(zones[zoneId]) do
            table.insert(mobs, string.format('%s (%s)', entry.Mob, entry.Rate));
        end
        print(chat.header('MobDB') .. chat.color1(2, ZoneName(zoneId)) .. chat.message(': ' .. table.concat(mobs, ', ')));
    end
end

function query:Command(args)
    if (#args < 4) or (string.lower(args[3]) ~= 'item') then
        PrintError('Usage: /mobdb query item <item name or id> [here | in <zone name>]');
        return;
    end

    local index = LoadIndex(self);
    if not index then
        return;
    end

    local words = {};
    for i = 4, #args do
        table.insert(words, args[i]);
    end
    local text, zoneText = SplitItemAndZone(words);

    local zoneFilter = nil;
    if zoneText then
        zoneFilter = FindZones(zoneText);
        if not next(zoneFilter) then
            PrintError(string.format('No zone matching "%s".', zoneText));
            return;
        end
        if CURRENT_ZONE_WORDS[string.lower(zoneText)] then
            zoneText = ZoneName(CurrentZone());
        end
    end

    local matches = FindItems(index, text);
    if (#matches == 0) then
        PrintError(string.format('No item matching "%s" is dropped by any mob in the data.', text));
        return;
    end

    if (#matches > 1) then
        local names = {};
        for i = 1, math.min(#matches, self.MaxMatches) do
            table.insert(names, ItemName(matches[i]));
        end
        local more = (#matches > self.MaxMatches) and string.format(' (+%u more)', #matches - self.MaxMatches) or '';
        print(chat.header('MobDB') .. chat.message(string.format('%u items match "%s": ', #matches, text))
            .. chat.color1(2, table.concat(names, ', ')) .. chat.message(more .. '. Be more specific or use the exact name.'));
        return;
    end

    local entries = index[matches[1]];
    if not entries then
        print(chat.header('MobDB') .. chat.color1(2, ItemName(matches[1])) .. chat.message(' is not dropped by any mob in the data.'));
        return;
    end
    PrintResults(self, matches[1], entries, zoneFilter, zoneText);
end

return query;

# MobDB
MobDB is an addon that displays monster information onscreen, in the same vein as ibar.  For most users, you should not need to do anything besides loading the addon.<br><br>

## About This Fork ##
This is a fork of [ThornyFFXI/mobdb](https://github.com/ThornyFFXI/mobdb), created by Thorny. It adds:
- Mob data regenerated from current LandSandBoat YAML by `tools/update_lsb_data.py` (the original data dates from 2022, and the in-game importer can't read LSB's current format).
- Elemental resistance ranks in each mob's Notes, shown one note per line.
- `/mobdb query item` to find which mobs drop an item.

## License ##
The addon code is MIT licensed; see `LICENSE`. The files in `data/` are generated from [LandSandBoat](https://github.com/LandSandBoat/server) and are licensed under GPL-3.0-or-later; see `data/COPYING`.

## /mobdb config ##
You can type this command to edit the appearance of mobdb and change the tokens, either for a specific character or for all characters.  The 'Edit Tokens' button will allow you to change the display strings.

## /md reset ##
This will return the bar to the top left of the screen if it's dragged offscreen or lost due to resolution changes.

## /mobdb query item [name or id] [here | in zone] ##
Lists the mobs that drop an item (or can have it stolen), grouped by zone with your current zone first, e.g. `/mobdb query item "Iron Ore"`. Quotes are optional. A partial name lists the matching items if more than one matches. Uses `data/drops.lua`, which `tools/update_lsb_data.py` writes alongside the zone files.
- Add `here` (or `current zone`) to only show your current zone: `/mobdb query item Iron Ore here`
- Add `in` and a zone name, full or partial, to only show that zone: `/mobdb query item Iron Ore in Gustav Tunnel`. A partial name such as `in altepa` includes every matching zone. Quote the item name if it contains the word "in".

## To-Do ##
1. Data parser already handles drop lists and spell lists, among other information.  A later version will include the ability to display a detailed window with far more information about the monster.  This would be larger onscreen, so it will be entirely optional and driven by a command to toggle it on/off.
2. Manual revisions for Dynamis-D and Odyssey to increase usefulness for retail players.
3. Add editor window so mobs can be edited in realtime, and quick note command to save notes on any target.

## Regenerating Data From Current LandSandBoat ##
LandSandBoat now keeps mob data in YAML (data/ecosystems.yaml and data/zones/*/mobs.yaml) instead of the SQL tables the in-game importer reads. `tools/update_lsb_data.py` builds `data/` from that YAML. It needs Python 3.10+ and PyYAML:
```
python -m pip install -r tools/requirements.txt
python tools/update_lsb_data.py
```
- The LSB commit is pinned in `tools/lsb_source.json`. Add `--refresh` to move the pin to the head of LSB's `base` branch, or `--source <path>` to use a local checkout.
- Mob names come from your client's entity DATs (install folder read from the registry, or pass `--ffxi <path>`), so names match the game exactly.
- Elemental resistance ranks, which affect how often spells land rather than their damage, are written to each mob's Notes. Add `$notes` to your layout to see them.
- Zones LSB has no spawns for keep their existing files unless you pass `--prune`. `tools/last_generation_report.txt` lists spawns skipped because LSB placed them on a different entity than the client shows.

## Generating Data For Your Custom Server ##
This process is only intended for advanced users, and will allow you to generate data files that exactly match your LSB or wings based private server.  To do this, follow these directions:
1. Change line 2 of import_lsb.lua, import_wings.lua, or import_custom.lua to reflect your current FFXI install path.
2. Load the addon to generate initial configurations if you haven't.  Reload to update path if you changed it.
3. Copy the following files into Ashita/config/addons/mobdb/input/ from your LSB server source:
- mob_droplist.sql
- mob_family_system.sql
- mob_groups.sql
- mob_pools.sql
- mob_resistances.sql (only for LSB-based servers, wings based won't have this)
- mob_spawn_points.sql
- mob_spell_lists.sql
4. Type the command "/mobdb import wings" or "/mobdb import lsb" or "/mobdb import custom" ingame.  This may take several minutes depending on your system.
5. Copy the files generated from Ashita/config/addons/mobdb/output/ to Ashita/addons/mobdb/data.
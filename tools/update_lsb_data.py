"""Generate MobDB data files from LandSandBoat's YAML mob data.

LandSandBoat keeps mob data in data/ecosystems.yaml and data/zones/<zone>/mobs.yaml.
This script reads a pinned LSB commit (or a local checkout), resolves each spawn's
attributes, takes names from the client's zone entity DATs and writes data/<zone>.lua
in the format mobdb.lua loads (Names and Indices tables).
"""

import argparse
import io
import json
import re
import sys
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: python -m pip install -r tools/requirements.txt")

ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / "tools/lsb_source.json"
DAT_TABLE = ROOT / "import_lsb.lua"
OUTPUT = ROOT / "data"
LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

ELEMENTS = [("fire", "Fire"), ("ice", "Ice"), ("wind", "Wind"), ("earth", "Earth"),
            ("thunder", "Lightning"), ("water", "Water"), ("light", "Light"), ("dark", "Dark")]
PHYSICAL = [("slashing", "Slashing", "slash_sdt"), ("piercing", "Piercing", "pierce_sdt"),
            ("h2h", "H2H", "hth_sdt"), ("blunt", "Impact", "impact_sdt")]
MODIFIER_ORDER = ["Slashing", "Piercing", "H2H", "Impact", "Fire", "Ice", "Wind", "Earth",
                  "Lightning", "Water", "Light", "Dark"]

# MobDB's immunity bits (tokens.lua PrintImmunities) keyed by LSB immunity names.
IMMUNITY_BITS = {
    "gravity": 0x02, "bind": 0x04, "stun": 0x08, "silence": 0x10, "paralyze": 0x20,
    "blind": 0x40, "slow": 0x80, "poison": 0x100, "elegy": 0x200, "requiem": 0x400,
    "light_sleep": 0x800, "dark_sleep": 0x1000, "petrify": 0x2000,
}
SLEEP_BIT = 0x01

# Magic evasion multiplier per elemental resistance rank, from LSB
# scripts/combat/basic/magic_hit_rate.lua. Used only to label ranks in Notes.
RANK_MAGIC_EVASION = {
    -3: 0.95, -2: 0.96019, -1: 0.98, 0: 1.0, 1: 1.023, 2: 1.049, 3: 1.0905, 4: 1.126,
    5: 1.2075, 6: 1.3475, 7: 1.70065, 8: 2.141, 9: 2.2, 10: 2.275, 11: 2.35,
}


def read_yaml(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=LOADER) or {}


def merge(base, override):
    """Nested maps merge key by key; lists and scalars replace what they override."""
    result = deepcopy(base) if base else {}
    for key, value in (override or {}).items():
        if value is None:
            continue
        if isinstance(value, dict):
            result[key] = merge(result.get(key, {}), value)
        else:
            result[key] = deepcopy(value)
    return result


def load_species(source):
    species = {}
    for ecosystem in read_yaml(source / "data/ecosystems.yaml")["ecosystems"].values():
        for family in ecosystem["families"].values():
            family_attributes = merge(ecosystem.get("attributes"), family.get("attributes"))
            for name, entry in family["species"].items():
                species[name] = merge(family_attributes, entry.get("attributes"))
    return species


def load_sql_rows(path, columns):
    """Yield tuples of the first `columns` values from each INSERT row."""
    value = r"(?:'(?:[^'\\]|\\.|'')*'|[^,()]+)"
    row = re.compile(r"INSERT INTO `\w+` VALUES \(\s*(" + value + r"(?:\s*,\s*" + value + r")*)\s*\)")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = row.match(line)
        if not match:
            continue
        fields = re.findall(value, match.group(1))
        yield tuple(f.strip().strip("'") for f in fields[:columns])


def load_item_ids(source):
    items = {}
    for item_id, _subid, name in load_sql_rows(source / "sql/item_basic.sql", 3):
        items.setdefault(name, int(item_id))
    return items


def load_spell_lists(source):
    lists = {}
    for _name, list_id, spell_id, min_level, max_level in load_sql_rows(source / "sql/mob_spell_lists.sql", 5):
        lists.setdefault(int(list_id), []).append((int(spell_id), int(min_level), int(max_level)))
    return lists


def load_dat_paths():
    text = DAT_TABLE.read_text(encoding="utf-8")
    return {int(zone): path.replace("\\\\", "\\") for zone, path in re.findall(r"\[(\d+)\]\s*=\s*'([^']+\.DAT)'", text)}


def read_entity_dat(path):
    """Entity DATs are 32-byte records: a 28-byte name followed by the uint32 entity id."""
    names = {}
    data = path.read_bytes()
    for offset in range(0, len(data) - 31, 32):
        raw_name = data[offset:offset + 28].split(b"\x00", 1)[0]
        entity_id = int.from_bytes(data[offset + 28:offset + 32], "little")
        index = entity_id & 0xFFF
        if entity_id and index:
            names[index] = raw_name.decode("latin-1")
    return names


def find_ffxi_path():
    try:
        import winreg
    except ImportError:
        return None
    for key in (r"SOFTWARE\WOW6432Node\PlayOnlineUS\InstallFolder", r"SOFTWARE\WOW6432Node\PlayOnlineEU\InstallFolder",
                r"SOFTWARE\WOW6432Node\PlayOnline\InstallFolder"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
                return Path(winreg.QueryValueEx(handle, "0001")[0])
        except OSError:
            continue
    return None


def normalize(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def damage_multiplier(value):
    return round(max(0.0, min(3.0, 1 + value / 10000)), 6)


def collect_drops(entries, items, rates, unknown):
    """Return (item id, rate label) pairs in listed order; one_of groups contribute every possible item."""
    found = []
    for entry in entries or []:
        rate = rate_label(entry.get("chance"), rates) if isinstance(entry, dict) else None
        if isinstance(entry, str):
            candidates = [entry]
        elif isinstance(entry, dict) and "item" in entry:
            candidates = [entry["item"]]
        elif isinstance(entry, dict) and "one_of" in entry:
            candidates = []
            for option in entry["one_of"]:
                candidates.extend(option.keys() if isinstance(option, dict) else [option])
            rate = f"{rate}, 1 of {len(candidates)}" if rate else f"1 of {len(candidates)}"
        elif isinstance(entry, dict):
            candidates = list(entry.keys())  # weighted one_of entry: {item: weight, ...}
        else:
            continue
        for name in candidates:
            if name == "nothing":
                continue
            item_id = items.get(name)
            if item_id is None:
                unknown.add(name)
            elif item_id not in (seen for seen, _ in found):
                found.append((item_id, rate or ""))
    return found


def rate_label(chance, rates):
    """Label a drop chance: a named tier ('rare 5%') or a raw per-mille value ('4.6%')."""
    if chance is None:
        return None
    permille = rates.get(chance) if isinstance(chance, str) else int(chance)
    if permille is None:
        return str(chance).replace("_", " ")
    percent = f"{permille / 10:.1f}".rstrip("0").rstrip(".") + "%"
    return f"{chance.replace('_', ' ')} {percent}" if isinstance(chance, str) else percent


def collect_steals(steal, items, unknown):
    names = [steal] if isinstance(steal, str) else list(steal or [])
    found = []
    for name in names:
        item_id = items.get(name)
        if item_id is None:
            unknown.add(name)
        elif item_id not in found:
            found.append(item_id)
    return found


def rank_notes(ranks):
    groups = {}
    for element, rank in ranks.items():
        if rank:
            groups.setdefault(rank, []).append(element)
    notes = []
    for rank in sorted(groups):
        evasion = round((RANK_MAGIC_EVASION[max(-3, min(11, rank))] - 1) * 100)
        notes.append(f"Resist rank {rank:+d} ({evasion:+d}% m.eva): {' '.join(groups[rank])}")
    return notes


def make_record(name, template, spawn, species, ctx):
    attributes = merge(species[template["species"]], template.get("attributes"))
    attributes = merge(attributes, spawn.get("attributes"))
    behaviors = attributes.get("behaviors", {})
    resists = attributes.get("resists", {})
    mods = attributes.get("mods", {})

    detection = set(attributes.get("detects", []))
    mask = attributes.get("mob_mods", {}).get("detection")
    if mask is not None:
        detection = {flag for flag, bit in ctx["detects"].items() if bit and mask & bit}
    true_detection = behaviors.get("true_detection", False)

    jobs = attributes.get("jobs", ["none", "none"])
    level = spawn.get("level") or [0, 0]
    loot = template.get("loot") or {}
    drops = collect_drops(loot.get("drops"), ctx["items"], ctx["drop_rates"], ctx["unknown_items"])
    steals = collect_steals(loot.get("steal"), ctx["items"], ctx["unknown_items"])

    immunities = 0
    other_immunities = []
    statuses = set(resists.get("immune_status", []))
    for status in sorted(statuses):
        if status in IMMUNITY_BITS:
            immunities |= IMMUNITY_BITS[status]
        else:
            other_immunities.append(status.replace("_", " ").title())
    if {"light_sleep", "dark_sleep"} <= statuses:
        immunities |= SLEEP_BIT

    modifiers = {}
    for source_key, target, mod_key in PHYSICAL:
        modifiers[target] = damage_multiplier(resists.get("dmg_physical", {}).get(source_key, 0) + mods.get(mod_key, 0))
    magic = resists.get("dmg_magic", {})
    all_magic = damage_multiplier(magic.get("all", 0) + mods.get("udmgmagic", 0))
    ranks = {}
    for source_key, target in ELEMENTS:
        modifiers[target] = round(damage_multiplier(magic.get(source_key, 0) + mods.get(source_key + "_sdt", 0)) * all_magic, 6)
        ranks[target] = resists.get("rank_element", {}).get(source_key, 0) + mods.get(source_key + "_res_rank", 0)

    spells = []
    for spell_id, min_level, max_level in ctx["spell_lists"].get(template.get("spell_list_id", 0), []):
        in_range = level == [0, 0] or (level[0] <= max_level and level[1] >= min_level)
        if in_range and spell_id not in spells:
            spells.append(spell_id)

    notes = rank_notes(ranks)
    if level == [0, 0]:
        notes.append("Level not in LSB data (set by script)")
    if other_immunities:
        notes.append("Also immune: " + ", ".join(other_immunities))

    return {
        "Name": name,
        "Notorious": "notorious" in (template.get("type") or []),
        "Aggro": bool(behaviors.get("aggressive", False)),
        "Link": bool(behaviors.get("links", False)),
        "TrueSight": "sight" in detection and true_detection,
        "Job": ctx["jobs"].get(jobs[0], 0),
        # LSB lists most mobs as main/sub of the same job; only a distinct sub job is worth showing.
        "SubJob": ctx["jobs"].get(jobs[1], 0) if len(jobs) > 1 and jobs[1] != jobs[0] else 0,
        "MinLevel": level[0],
        "MaxLevel": level[1],
        "Immunities": immunities,
        "Respawn": int(attributes.get("spawn", {}).get("respawn", 0)),
        "Sight": "sight" in detection,
        "Sound": "hearing" in detection,
        "Blood": "lowhp" in detection,
        "Magic": "magic" in detection,
        "JA": "ability" in detection,
        "Scent": "scent" in detection,
        "Drops": [item_id for item_id, _ in drops],
        "Spells": spells,
        "Modifiers": modifiers,
        "Notes": notes,
        # Not written to zone files; feeds the drop index (data/drops.lua).
        "_loot": drops + [(item_id, "steal") for item_id in steals],
    }


def lua_string(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def lua_number(value):
    text = f"{value:f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def render_record(r):
    flags = lambda key: "true" if r[key] else "false"
    modifiers = ", ".join(f"{key}={lua_number(r['Modifiers'][key])}" for key in MODIFIER_ORDER)
    notes = ", ".join(lua_string(note) for note in r["Notes"])
    return (f"{{ Name={lua_string(r['Name'])}, Notorious={flags('Notorious')}, Aggro={flags('Aggro')}, "
            f"Link={flags('Link')}, TrueSight={flags('TrueSight')}, Job={r['Job']}, SubJob={r['SubJob']}, "
            f"MinLevel={r['MinLevel']}, MaxLevel={r['MaxLevel']}, Immunities={r['Immunities']}, Respawn={r['Respawn']}, "
            f"Sight={flags('Sight')}, Sound={flags('Sound')}, Blood={flags('Blood')}, Magic={flags('Magic')}, "
            f"JA={flags('JA')}, Scent={flags('Scent')}, Drops={{{','.join(map(str, r['Drops']))}}}, "
            f"Spells={{{','.join(map(str, r['Spells']))}}}, Modifiers={{{modifiers}}}, Notes={{{notes}}} }}")


def compile_zone(zone_id, document, species, ctx, dat_names, stats):
    templates = document.get("templates", {})
    template_keys = [normalize(t.get("display_name", key).replace("_", " ")) for key, t in templates.items()]
    by_index = {}
    for entity_id, spawn in sorted((document.get("spawns") or {}).items()):
        template_name = spawn.get("template")
        if not template_name:
            continue
        template = templates[template_name]
        lsb_name = template.get("display_name", template_name).replace("_", " ")
        index = int(entity_id) & 0xFFF
        name = lsb_name
        if dat_names is not None:
            dat_name = dat_names.get(index)
            if dat_name is None:
                stats["missing_dat_entry"] += 1
            # LSB template names often carry a variant suffix (Ghoul_blm, Chigoe_past); the DAT name is what the client shows.
            elif normalize(lsb_name).startswith(normalize(dat_name)):
                name = dat_name
            # A shared template (Indoor_aern_war for Eo'aern) is fine; but if another template in this zone
            # is named for the DAT mob, LSB placed this template on the wrong entity, so skip it.
            elif any(key.startswith(normalize(dat_name)) for key in template_keys):
                stats["name_mismatch"] += 1
                ctx["mismatches"].append(f"zone {zone_id} index {index}: DAT '{dat_name}' vs LSB '{lsb_name}'")
                continue
            else:
                stats["shared_template"] += 1
                name = dat_name
        else:
            stats["no_dat_zone_spawns"] += 1
        by_index[index] = make_record(name, template, spawn, species, ctx)

    for record in by_index.values():
        for item_id, rate in record["_loot"]:
            ctx["drop_index"].setdefault(item_id, {}).setdefault((zone_id, record["Name"]), rate)

    names = {}
    for index, record in sorted(by_index.items()):
        generic = names.get(record["Name"])
        if generic is None:
            names[record["Name"]] = deepcopy(record)
            continue
        levels = [lv for lv in (generic["MinLevel"], record["MinLevel"]) if lv]
        generic["MinLevel"] = min(levels) if levels else 0
        generic["MaxLevel"] = max(generic["MaxLevel"], record["MaxLevel"])
    indices = {index: record for index, record in by_index.items() if record != names[record["Name"]]}
    return names, indices


def zone_title(zone_id, enum_name):
    existing = OUTPUT / f"{zone_id}.lua"
    if existing.exists():
        first = existing.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        if first.startswith("--Zone: "):
            return first[len("--Zone: "):].strip()
    return enum_name.replace("_", " ").title()


def render_zone(zone_id, title, names, indices, pin):
    lines = [f"--Zone: {title}", f"--Zone ID: {zone_id}",
             f"--Generated from {pin['repository']} @ {pin['commit']} (GPL-3.0-or-later data)",
             "return {", "    Names = {"]
    lines += [f"        [{lua_string(name)}] = {render_record(r)}," for name, r in sorted(names.items())]
    lines += ["    },", "    Indices = {"]
    lines += [f"        [{index}] = {render_record(r)}," for index, r in sorted(indices.items())]
    lines += ["    },", "};", ""]
    return "\n".join(lines)


def render_drop_index(drop_index, pin):
    """Item id -> every mob (zone, name) that drops or can be robbed of it, for /mobdb query."""
    lines = ["--Drop index: item id -> mobs that drop it (Rate is the LSB drop tier, or 'steal')",
             f"--Generated from {pin['repository']} @ {pin['commit']} (GPL-3.0-or-later data)",
             "return {"]
    for item_id in sorted(drop_index):
        entries = sorted(drop_index[item_id].items(), key=lambda entry: (entry[0][0], entry[0][1]))
        rows = ", ".join(f"{{ Zone={zone}, Mob={lua_string(mob)}, Rate={lua_string(rate)} }}" for (zone, mob), rate in entries)
        lines.append(f"    [{item_id}] = {{ {rows} }},")
    lines += ["};", ""]
    return "\n".join(lines)


def fetch_source(pin, destination):
    url = f"https://codeload.github.com/{pin['repository']}/zip/{pin['commit']}"
    print(f"Downloading {pin['repository']} @ {pin['commit'][:10]}...", flush=True)
    with urlopen(url, timeout=120) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    with archive:
        for entry in archive.infolist():
            relative = PurePosixPath(*PurePosixPath(entry.filename).parts[1:])
            if entry.is_dir() or ".." in relative.parts:
                continue
            wanted = str(relative).startswith("data/") or str(relative) in ("sql/item_basic.sql", "sql/mob_spell_lists.sql")
            if wanted:
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))


def generate(source, pin, output, ffxi, prune):
    species = load_species(source)
    zones = read_yaml(source / "data/enums/zone.yaml")["values"]
    ctx = {
        "jobs": read_yaml(source / "data/enums/job.yaml")["values"],
        "detects": read_yaml(source / "data/enums/detects.yaml")["values"],
        "items": load_item_ids(source),
        "spell_lists": load_spell_lists(source),
        "drop_rates": read_yaml(source / "data/enums/drop_rate.yaml")["values"],
        "drop_index": {},
        "unknown_items": set(),
        "mismatches": [],
    }
    dat_paths = load_dat_paths() if ffxi else {}
    stats = {"missing_dat_entry": 0, "name_mismatch": 0, "no_dat_zone_spawns": 0, "shared_template": 0}
    files, spawn_count = {}, 0
    for enum_name, zone_id in sorted(zones.items(), key=lambda entry: entry[1]):
        mobs = source / "data/zones" / enum_name / "mobs.yaml"
        if not mobs.exists():
            continue
        dat_names = None
        if zone_id in dat_paths and (ffxi / dat_paths[zone_id]).exists():
            dat_names = read_entity_dat(ffxi / dat_paths[zone_id])
        names, indices = compile_zone(zone_id, read_yaml(mobs), species, ctx, dat_names, stats)
        if names:
            files[f"{zone_id}.lua"] = render_zone(zone_id, zone_title(zone_id, enum_name), names, indices, pin)
            spawn_count += len(names) + len(indices)

    output.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        (output / filename).write_text(content, encoding="utf-8", newline="\n")
    (output / "drops.lua").write_text(render_drop_index(ctx["drop_index"], pin), encoding="utf-8", newline="\n")
    stale =sorted((p.name for p in output.glob("[0-9]*.lua") if p.name not in files), key=lambda n: int(n[:-4]))
    if prune:
        for name in stale:
            (output / name).unlink()

    print(f"Wrote {len(files)} zone files ({spawn_count} Names/Indices entries) to {output}")
    print(f"Wrote drops.lua: {len(ctx['drop_index'])} items, "
          f"{sum(len(mobs) for mobs in ctx['drop_index'].values())} mob/item pairs")
    print(f"Spawns named from a shared LSB template: {stats['shared_template']}; "
          f"skipped as placed on the wrong entity: {stats['name_mismatch']}; "
          f"spawns with no DAT entry (LSB name used): {stats['missing_dat_entry']}; "
          f"spawns in zones without a DAT (LSB name used): {stats['no_dat_zone_spawns']}")
    if ctx["unknown_items"]:
        print(f"Drop items not found in item_basic.sql ({len(ctx['unknown_items'])}): "
              + ", ".join(sorted(ctx["unknown_items"])[:20]) + (" ..." if len(ctx["unknown_items"]) > 20 else ""))
    if stale:
        print(f"{'Removed' if prune else 'Left untouched (no LSB spawns; use --prune to remove)'}: {', '.join(stale)}")
    report = ROOT / "tools/last_generation_report.txt"
    report.write_text("\n".join(ctx["mismatches"]) + "\n", encoding="utf-8", newline="\n")
    if ctx["mismatches"]:
        print(f"Name mismatch details: {report}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, help="Local LandSandBoat checkout instead of downloading the pinned commit")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Output directory (default: data/)")
    parser.add_argument("--ffxi", type=Path, help="FINAL FANTASY XI install folder (default: read from the registry)")
    parser.add_argument("--refresh", action="store_true", help="Update the pin to the head of its branch before generating")
    parser.add_argument("--prune", action="store_true", help="Delete zone files that LSB no longer has spawns for")
    args = parser.parse_args()

    pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    if args.refresh:
        if args.source:
            parser.error("--refresh downloads a new commit and cannot be combined with --source")
        request = Request(f"https://api.github.com/repos/{pin['repository']}/commits/{pin['branch']}",
                          headers={"User-Agent": "mobdb-data-generator"})
        with urlopen(request, timeout=30) as response:
            pin["commit"] = json.load(response)["sha"]
        PIN_FILE.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Pinned {pin['repository']} {pin['branch']} @ {pin['commit']}")

    ffxi = args.ffxi or find_ffxi_path()
    if ffxi is None or not ffxi.exists():
        print("FFXI install not found; names will come from LSB and may lack apostrophes. Pass --ffxi to fix.")
        ffxi = None

    if args.source:
        generate(args.source.resolve(), pin, args.output.resolve(), ffxi, args.prune)
    else:
        with tempfile.TemporaryDirectory(prefix="mobdb-lsb-") as temp:
            fetch_source(pin, Path(temp))
            generate(Path(temp), pin, args.output.resolve(), ffxi, args.prune)


if __name__ == "__main__":
    main()

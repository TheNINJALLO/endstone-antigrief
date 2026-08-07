# AntiGrief Plugin v1.5.1

A player-behavior audit, exact container backup, rollback, and security dashboard plugin for Endstone Minecraft Bedrock servers.

## What changed in v1.5.1

AntiGrief now uses **Endstone BlockData API** as its native block and container data source. Container records are no longer limited to item IDs and counts. The plugin captures the live block actor and stores:

- Block type, runtime ID, block states, dimension, coordinates, and revision
- Canonical block-entity NBT and raw SNBT
- Every occupied container slot
- Full item payloads, including names, lore, enchantments, durability, nested shulker contents, and other supported tags
- Before-and-after snapshots for container access auditing
- Snapshot IDs that link event rows to the full NBT record in the WebUI

Rollback recreates the block in the correct dimension, waits one server tick for its block actor to exist, then atomically restores its states, block-entity NBT, and exact inventory through BlockData.

## Required dependency

Install a **matching release** of [`TheNINJALLO/endstone-blockdata-api`](https://github.com/TheNINJALLO/endstone-blockdata-api) before AntiGrief.

The BlockData installation must include both files from the complete platform ZIP:

1. The native `blockdata_api` plugin (`.dll` on Windows or `.so` on Linux)
2. The matching platform-specific `endstone_blockdata_inspector` wheel containing the Python native bridge

The BDS version, Endstone version, Python ABI, platform, and BlockData release must match. AntiGrief declares `depend = ["blockdata_api"]` and dynamically loads the bridge shipped in the matching inspector wheel.

For the current BlockData v0.4.6 release, use its complete ZIP for BDS 1.26.33, Endstone 0.11.6, and CPython 3.14. Use an older matching BlockData build when running an older BDS adapter.

## Installation

1. Stop the server.
2. Remove older duplicate BlockData native plugins, inspector wheels, and AntiGrief wheels from `plugins/`.
3. Copy the two matching BlockData files into `plugins/`.
4. Copy `endstone_antigrief-1.5.0-py3-none-any.whl` into `plugins/`.
5. Start the server and verify the console reports `BlockData API connected`.
6. Change the WebUI secret in `plugins/antigrief_data/config.json` before exposing the dashboard.

The old `antigrief_companion_bp` is no longer required for v1.5.1. It remains in the source package only as a rollback compatibility path for database records made by older AntiGrief versions.

## Configuration

```json
{
  "record_nature_block": true,
  "record_human_block": true,
  "only_record_important_animal": true,
  "10s_message_max": 6,
  "10s_command_max": 12,
  "enable_web_ui": true,
  "no_log_mobs": ["minecraft:item", "minecraft:xp_orb"],
  "web_ui_port": 8098,
  "web_ui_secret": "change_this_secret_key",
  "require_blockdata_api": true,
  "capture_container_open_close": true,
  "store_raw_snbt": true
}
```

## Database layout

### `interactions`

Stores the compact event stream for block breaks, placements, explosions, interactions, attacks, joins, commands, and slot-level container changes. BlockData-backed rows include a snapshot ID or exact before/after item payloads in `blockdata`.

### `container_snapshots`

Stores complete detached BlockData snapshots:

- `snapshot_id`
- player/reason/time
- position and dimension
- block type and revision
- occupied-slot and item totals
- canonical-NBT flag
- full snapshot JSON
- raw SNBT

Indexes are created for event time, snapshot time, and world position. `/agclean` removes expired interaction rows and container snapshots together.

## Exact container auditing

When a player opens a supported container, AntiGrief stores a native baseline snapshot. When the container-close packet arrives, it captures another native snapshot and compares exact slots. Logged events include:

- `Container Add`
- `Container Take`
- `Container Change`
- `Container NBT Change` for non-inventory actor metadata such as custom names or locks

Slot changes contain the slot, amount, full before item, full after item, revisions, and links to the before/after complete snapshots. Metadata changes include the before/after canonical actor NBT with inventory and coordinate fields removed for a clean comparison.

Supported containers include chests, trapped chests, barrels, hoppers, droppers, dispensers, furnaces, blast furnaces, smokers, brewing stands, chiseled bookshelves, crafters, decorated pots, and all shulker-box colors supported by the installed BlockData adapter.

## Rollback

```text
/agback <hours> <x y z> <radius> [player]
```

Examples:

```text
/agback 1 100 64 -200 10
/agback 24 100 64 -200 20 Steve
```

For a broken or exploded container, rollback:

1. Recreates the original block and block states in its recorded dimension.
2. Schedules restoration one server tick later on the Endstone server thread.
3. Captures the new block actor revision.
4. Applies canonical block-entity NBT and exact slot contents using optimistic revision checking.
5. Retries a revision conflict once with an explicit force policy.
6. Stores a new `rollback_restored` snapshot for auditing.

## WebUI

Open `http://SERVER-IP:8098` and authenticate with the configured secret.

The event table now has a **VIEW NBT** control. The detail viewer exposes:

- Full stored event or snapshot JSON
- Canonical block-entity NBT
- Exact inventory slots and item NBT
- Raw SNBT, when enabled

API routes:

```text
GET /api/logs
GET /api/logs/{log_id}/blockdata
GET /api/container-snapshots
GET /api/container-snapshots/{snapshot_id}
GET /api/stats
GET /api/bans
```

Pass the secret through the `X-Secret-Key` header or the existing `secret` query parameter.

## Commands

| Command | Description | Permission |
|---|---|---|
| `/ag` | Query logs | Member |
| `/ags` | Search logs | OP |
| `/agback` | Roll back block and container changes | OP |
| `/agcontainer` | View container access changes | OP |
| `/agclean` | Remove old events and snapshots | OP |
| `/agban`, `/agunban`, `/agbanlist` | Player bans | OP |
| `/ban-id`, `/unban-id`, `/banlist-id` | Device bans | OP |
| `/density` | Find entity-density hotspots | OP |

## Building

```bash
python -m pip install build hatchling
python -m build
pytest -q
```

The AntiGrief wheel is pure Python. The BlockData native bridge is not bundled into it and must come from the matching BlockData platform release.

## License

MIT License. The BlockData API dependency is distributed under its own Apache-2.0 license.

## v1.5.1 startup crash fix

- Removed access to Endstone's native `Plugin.logger` property from the Python constructor.
- Runtime state and the BlockData adapter are now initialized in `on_load()`, after Endstone attaches the native plugin wrapper.
- This fixes the Linux SIGSEGV seen in `pybind11::type_caster_base<endstone::Logger>` during `PyPluginLoader::loadPlugins`.

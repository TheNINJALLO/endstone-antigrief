# Configuration

AntiGrief creates `plugins/antigrief_data/config.json`. Existing files are migrated with new defaults.

## Core logging

| Key | Purpose |
|---|---|
| `record_nature_block` | Records natural/environment block activity. |
| `record_human_block` | Records player block placement and breaking. |
| `only_record_important_animal` | Limits animal records to important entities. |
| `no_log_mobs` | Entity identifiers excluded from logging. |

## WebUI

| Key | Purpose |
|---|---|
| `enable_web_ui` | Enables the FastAPI dashboard. |
| `web_ui_port` | TCP port, default `8098`. |
| `web_ui_secret` | Shared access secret. Change it before remote access. |

## BlockData and containers

| Key | Purpose |
|---|---|
| `require_blockdata_api` | Treats BlockData as required for exact functions. |
| `capture_container_open_close` | Captures before/after container snapshots. |
| `store_raw_snbt` | Stores raw SNBT when available. |
| `blockdata_connect_retry_ticks` | Retry interval if the service registers late. |
| `blockdata_connect_log_every` | Reduces repeated startup warnings. |

## Player inventories

| Key | Purpose |
|---|---|
| `capture_player_inventories` | Stores online inventory and Ender Chest snapshots. |
| `player_inventory_capture_ticks` | Scan interval. |
| `player_inventory_capture_batch_size` | Players processed per scan. |
| `player_inventory_decode_warning_cooldown_seconds` | Backoff for malformed UTF-8 warnings. |

## Recovery

Recovery items are queued only when an operator confirms an incident with `/agback`. `recover_stolen_items_on_rollback` controls the confirmed recovery phase.

## Recommended production changes

- Replace the default WebUI secret with a long random value.
- Bind or firewall the WebUI so it is not publicly reachable.
- Keep ordinary record retention appropriate for disk size.
- Back up the database before large rollbacks.

## Complete default configuration

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
  "store_raw_snbt": true,
  "container_ownership_enabled": true,
  "recover_stolen_items_on_rollback": true,
}
```

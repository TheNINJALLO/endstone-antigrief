# 🩺 Troubleshooting Matrix

This guide provides solutions for common issues, error messages, and operational questions.

---

## 🔍 Diagnostic Matrix

| Issue / Symptom | Possible Cause | Resolution Step |
|---|---|---|
| **Console warning: `BlockData API not available`** | Complete inspector bundle missing, mixed native/bridge versions, or unsupported runtime. | Install the complete BlockData 0.6.9 inspector wheel on Linux x86-64 with Endstone 0.11.15, BDS 1.26.60.6, and CPython 3.14. Remove older inspector wheels and standalone BlockData libraries while stopped. Check the preceding native loader message. |
| **WebUI loads with `401 Unauthorized`** | Secret key mismatch in request header or query parameter. | Check `"web_ui_secret"` in `plugins/antigrief_data/config.json` and submit the exact key in the WebUI login page. |
| **Events are recorded but dashboard searches stay blank** | Older dashboards do not submit filters on Enter, do not trim player names, use root API paths behind proxies, and hide request failures. | Upgrade to 1.5.20. Press Enter or APPLY; check the displayed search error if a request fails. Successful empty searches explicitly say no events match. Clear player/area filters and widen the time range to inspect existing records. |
| **`Degraded Metadata` badge in WebUI** | Item in container or player inventory contains non-standard binary NBT or malformed legacy color bytes. | No action required; AntiGrief safely fell back to clean Endstone text parsing to prevent dashboard crashes. |
| **`/agback` reports `Container verification mismatch`** | Target container was moved, destroyed, or obscured by newly placed blocks during rollback. | Run `/agstop <recovery ID>` and inspect the target and saved inventory before starting another rollback. The plugin does not replay uncertain inventory writes. |
| **Repeated recovery warnings or containers refilling after `/agback`** | Older recovery sweeps wrote container contents again on every retry. | Upgrade to 1.5.19 and run `/agstop` to cancel the old queue. Before upgrading, stop the server, disable `recover_stolen_items_on_rollback` in the config, then restart. |
| **Pending item confiscation not running** | Offending player is offline or has empty slots. | Recovery rows remain safely queued in `agdata.db`. Execute `/agconfiscate <player>` when player logs back on. |
| **Container opens appear but item moves are missing** | Older versions omit copper chests, lit furnace types, and wood shelves from container detection; they also only compare the opening and closing state, so moves can cancel out. Records wait for the database flush. | Install 1.5.20, keep `capture_container_open_close` enabled, confirm the BlockData connection, then move items, wait up to 20 seconds, and refresh with the Container filter. Changes within one server tick are recorded as its net committed slot state. |
| **Multiple wheels found error on startup** | Older `endstone_antigrief-*.whl` files left in `plugins/`. | Delete all older AntiGrief wheel files from `plugins/` so only `endstone_antigrief-1.5.22-py3-none-any.whl` remains. |

---

## 🛠️ Verification Commands

Run these diagnostic commands to verify server health:

```text
# 1. Verify plugin registration and version
/aghelp

# 2. Test log search engine
/ags break stone 1

# 3. Check container inspection
/agcontainer

# 4. View player inventory summary
/ago <PlayerName>
```

# 🩺 Troubleshooting Matrix

This guide provides solutions for common issues, error messages, and operational questions.

---

## 🔍 Diagnostic Matrix

| Issue / Symptom | Possible Cause | Resolution Step |
|---|---|---|
| **Console warning: `BlockData API not available`** | Native extension `blockdata_api.so` / `blockdata_api.dll` or Python wheel missing in `plugins/`. | Download matching BlockData binary and wheel for your server OS and place both in `plugins/`. |
| **WebUI loads with `401 Unauthorized`** | Secret key mismatch in request header or query parameter. | Check `"web_ui_secret"` in `plugins/antigrief_data/config.json` and submit the exact key in the WebUI login page. |
| **`Degraded Metadata` badge in WebUI** | Item in container or player inventory contains non-standard binary NBT or malformed legacy color bytes. | No action required; AntiGrief safely fell back to clean Endstone text parsing to prevent dashboard crashes. |
| **`/agback` reports `Container verification mismatch`** | Target container was moved, destroyed, or obscured by newly placed blocks during rollback. | Clear obstruction above/around container and re-run `/agback`. |
| **Pending item confiscation not running** | Offending player is offline or has empty slots. | Recovery rows remain safely queued in `agdata.db`. Execute `/agconfiscate <player>` when player logs back on. |
| **Multiple wheels found error on startup** | Older `endstone_antigrief-*.whl` files left in `plugins/`. | Delete all older AntiGrief wheel files from `plugins/` so only `endstone_antigrief-1.5.13-py3-none-any.whl` remains. |

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

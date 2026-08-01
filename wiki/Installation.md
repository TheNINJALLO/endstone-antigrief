# 🚀 Installation & Upgrade Guide

This guide covers installing and upgrading **AntiGrief v1.5.13** on Linux x64 and Windows x64 Endstone Bedrock Dedicated Servers (BDS).

---

## 📋 System Requirements & Compatibility

Before installing AntiGrief, verify that your server environment meets all version requirements:

| Component | Required Version | Notes |
|---|---|---|
| **Minecraft BDS** | `1.26.33.1` | Bedrock Dedicated Server |
| **Endstone API** | `0.11.6` | C++ Endstone Server Host |
| **Python Runtime** | `3.14` | Embedded CPython runtime |
| **BlockData API** | `0.4.8+` | Exact platform binary (`.so` / `.dll`) & wheel |

> [!IMPORTANT]
> AntiGrief depends on the native plugin named `blockdata_api`. Both the BlockData native library and its matching Python inspector wheel MUST be installed in `plugins/` before AntiGrief will start.

---

## 📦 Required Plugin Artifacts

Your server's `plugins/` directory must contain the following files:

1. **BlockData Native Extension**: `blockdata_api.so` (Linux) or `blockdata_api.dll` (Windows)
2. **BlockData Python Inspector Wheel**: `endstone_blockdata_api-0.4.8-py3-none-any.whl` (or matching platform package)
3. **AntiGrief Plugin Wheel**: `endstone_antigrief-1.5.13-py3-none-any.whl`

> [!CAUTION]
> **Remove Older Wheels**: Do not leave older AntiGrief `.whl` files in `plugins/`. Endstone scans the directory on startup and may attempt to load multiple versions simultaneously.

---

## 🐧 Linux & Pterodactyl Setup Procedure

1. **Stop Server**: Ensure your BDS server is completely stopped via systemd or Pterodactyl panel.
2. **Clean Plugins Directory**: Navigate to `plugins/` and delete any legacy `endstone_antigrief-*.whl` files.
3. **Upload Files**: Transfer the matching Linux `blockdata_api.so`, the BlockData wheel, and `endstone_antigrief-1.5.13-py3-none-any.whl` into `plugins/`.
4. **Start Server**: Boot the server and inspect console startup logs. Confirm you see:
   ```text
   [AntiGrief] BlockData API connected (v0.4.8)
   [AntiGrief] Player Inventory API connected
   [AntiGrief] WebUI operational on port 8098
   ```
5. **Configure WebUI Secret**: Open `plugins/antigrief_data/config.json`, change `"web_ui_secret"` to a strong random passphrase, and restart the server.
6. **Firewall / Reverse Proxy**: Ensure port `8098` is properly firewalled or exposed via an authenticated reverse proxy (Nginx, Caddy, etc.).

---

## 🪟 Windows Setup Procedure

1. **Stop Server**: Terminate the running Endstone executable.
2. **Place Windows Artifacts**: Copy `blockdata_api.dll`, the BlockData Python wheel, and `endstone_antigrief-1.5.13-py3-none-any.whl` into `plugins/`.
3. **Start Server**: Launch Endstone and verify plugin initialization messages in command prompt.
4. **Update Config**: Edit `plugins\antigrief_data\config.json` to configure the WebUI secret key.

---

## 🔄 Upgrade Procedure

Upgrading AntiGrief from a previous version is smooth and non-destructive:

1. **Backup Database**: Create a copy of `plugins/antigrief_data/agdata.db` and `config.json`.
2. **Stop Server**: Stop Endstone.
3. **Replace Wheel**: Delete the old AntiGrief `.whl` file from `plugins/` and place `endstone_antigrief-1.5.13-py3-none-any.whl`.
4. **Restart Server**: Start Endstone. AntiGrief automatically runs schema migrations on `agdata.db` without data loss.

---

## ✅ Post-Installation Verification Checklist

Verify your installation by performing the following checks:

- [ ] Execute `/aghelp` in game or console to confirm command registration.
- [ ] Open and close a test container (Chest/Barrel) in game, then run `/agcontainer` to verify event logging.
- [ ] Open a web browser to `http://SERVER_IP:8098`, enter your secret key, and verify the WebUI loads.
- [ ] Inspect container item details in the WebUI to confirm NBT parsing.
- [ ] Test a small rollback using `/agback 1 <x y z> 5` in a staging environment.

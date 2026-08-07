# 🦖 Pterodactyl Panel Deployment Guide

This document explains how to deploy and run **AntiGrief v1.5.13** on servers managed by Pterodactyl Panel.

---

## ⚙️ Port Mapping Setup

The AntiGrief WebUI runs on TCP port `8098` by default.

1. **Allocate Port in Panel**: In Pterodactyl Panel, open your server page, click **Network**, and allocate an additional port (e.g. `8098`).
2. **Set Port in Config**: Open `plugins/antigrief_data/config.json` and set `"web_ui_port"` to match your allocated port:
   ```json
   "web_ui_port": 8098,
   "web_ui_secret": "your_secure_random_passphrase"
   ```
3. **Restart Container**: Restart the server container for port binding to take effect.

---

## 📂 File Manager Setup

Ensure your Pterodactyl server file manager reflects the following structure inside `plugins/`:

```text
plugins/
├── blockdata_api.so                          (Linux C++ native extension)
├── endstone_blockdata_api-0.4.8-py3-none-any.whl (BlockData inspector wheel)
└── endstone_antigrief-1.5.13-py3-none-any.whl    (AntiGrief plugin wheel)
```

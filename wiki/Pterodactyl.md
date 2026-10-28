# 🦖 Pterodactyl Panel Deployment Guide

This document explains how to deploy and run **AntiGrief v1.5.23** with **BlockData 0.6.9** on servers managed by Pterodactyl Panel. Use Endstone 0.11.15, BDS 1.26.60.6, and CPython 3.14 on Linux x86-64.

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
├── endstone_blockdata_inspector-0.6.9-cp314-cp314-linux_x86_64.whl
└── endstone_antigrief-1.5.23-py3-none-any.whl
```

The complete inspector wheel bundles and loads its matching native provider. Remove older inspector wheels and standalone BlockData libraries while the server is stopped; preserve plugin data folders. See [installation](Installation.md).

# 🌐 WebUI Security Operations Dashboard

AntiGrief includes a built-in, lightweight **FastAPI WebUI Security Operations Center** running on port `8098`. The WebUI enables server operators to inspect real-time interaction logs, examine container NBT, review live player Ender Chests, and generate printable PDF evidence reports.

---

## 🔑 Authentication & Access Control

Access to the WebUI is protected by secret key authentication:

- **Browser Access**: Browse to `http://YOUR_SERVER_IP:8098`, enter your configured `web_ui_secret` from `config.json`, and click **Sign In**.
- **API Access**: Include the HTTP header `X-Secret-Key: YOUR_SECRET` or append `?secret=YOUR_SECRET` on API requests.

> [!CAUTION]
> **Production Security**: Never expose the WebUI port publicly with the default secret `change_this_secret_key`. Change the secret in `plugins/antigrief_data/config.json` and place the WebUI behind an authenticated reverse proxy (Nginx, Caddy, Cloudflare Tunnel) or private VPN.

---

## 📊 Dashboard Modules

### 1. Real-Time Event Log
- **Multi-Param Filtering**: Search interaction events by player, action type (break, place, container, kill), coordinate radius, keyword, and time window.
- **Container Detail Inspector**: Click **VIEW NBT** on any container event card to open canonical item counts, custom names, enchantments, lore, and raw SNBT.
- **Clean Item Rendering**: Empty slots are filtered automatically. Bundles and nested storage items render expandable contents without clutter.

### 2. Live Player Inventories & Ender Chests
- **Snapshot Navigation**: Switch seamlessly between **Main Inventory**, **Armor**, **Offhand**, **Ender Chest**, and **Full Composite Snapshot**.
- **Online vs. Cached Badges**: Displays `Online` for live player captures and `Cached Offline` for players who have logged off.
- **UTF-8 Safety Indicator**: Malformed legacy item metadata automatically falls back to clean Endstone text parsing without crashing the dashboard.

### 3. Grief Proof Reports
- **Automated Case Generation**: Reports are automatically created whenever an operator runs `/agback`.
- **Printable PDF Export**: Click **VIEW / PRINT** to render a clean, print-styled evidence page with timelines, coordinate bounds, affected blocks, item recovery summaries, and a SHA-256 evidence hash.

---

## 🔌 REST API Endpoints

The WebUI exposes standard REST endpoints for external integrations and monitoring scripts:

| Endpoint | Method | Description |
|---|---|---|
| `/api/logs` | `GET` | Queries historical interaction logs with filters. |
| `/api/logs/{id}/blockdata` | `GET` | Fetches raw BlockData NBT for a specific event log. |
| `/api/container-snapshots` | `GET` | Lists stored container snapshots. |
| `/api/player-inventories` | `GET` | Returns list of player inventory snapshots. |
| `/api/player-inventories/{player}` | `GET` | Returns full inventory & Ender Chest snapshot for a player. |
| `/api/grief-reports` | `GET` | Lists all generated grief proof reports. |
| `/api/grief-reports/{id}` | `GET` | Returns raw JSON payload of a grief proof report. |
| `/reports/{id}` | `GET` | Renders printable HTML evidence page for a report. |
| `/api/stats` | `GET` | Returns server activity totals, event rates, and DB stats. |
| `/api/bans` | `GET` | Lists active player and device ID bans. |

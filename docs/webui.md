# WebUI Guide

## Sign in

Open `http://SERVER:PORT`, enter the configured secret, and submit. API requests accept the secret in the `X-Secret-Key` header or `secret` query parameter. Do not share dashboard URLs containing the secret.

## Event Log

Filter by time, action, player, keyword, or position. Use **VIEW NBT** on container records to open readable slot contents, canonical NBT, and raw SNBT. Bundle and backpack cards show only occupied entries.

## Player Inventories & Ender Chests

Select a player and switch between Main, Armor, Offhand, Ender Chest, and Full Snapshot. `Online` means a live capture; `Cached Offline` is the last saved snapshot. A degraded badge means malformed native text required the readable Endstone fallback.

## Grief Proof Reports

Reports are created automatically by `/agback`. Use **VIEW / PRINT** to open the evidence page, then use the browser print dialog to print or save a PDF.

## API routes

- `GET /api/logs`
- `GET /api/logs/{log_id}/blockdata`
- `GET /api/container-snapshots`
- `GET /api/container-snapshots/{snapshot_id}`
- `GET /api/player-inventories`
- `GET /api/player-inventories/{player_key}`
- `GET /api/grief-reports`
- `GET /api/grief-reports/{report_id}`
- `GET /reports/{report_id}`
- `GET /api/stats`
- `GET /api/bans`

## Network security

Prefer a VPN, private management network, SSH tunnel, or reverse proxy with TLS and an additional authentication layer. The built-in shared secret is not a replacement for network isolation.

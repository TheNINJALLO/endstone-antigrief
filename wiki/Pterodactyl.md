# Pterodactyl Deployment

## File placement

Use the server Files page and upload all plugin artifacts to the same `plugins/` directory used by Endstone. A typical Linux installation contains the BlockData native `.so`, the matching CPython 3.14 BlockData inspector wheel, and the AntiGrief wheel.

## Allocation

The WebUI uses TCP, not the Bedrock UDP allocation. Add a separate TCP allocation for `web_ui_port`, or place the WebUI behind another internal proxy. Do not expose the default secret publicly.

## Startup verification

Watch the console for the BlockData native adapter, player inventory service, AntiGrief connection, and WebUI address. A delayed AntiGrief connection is acceptable if it later reports `BlockData API connected`.

## Updating

Stop the server before replacing wheels. Delete old AntiGrief wheels, keep `plugins/antigrief_data/`, upload the replacement, and start the server.

## Backups

Include the world and `plugins/antigrief_data/agdata.db` in the same backup schedule. Keep a backup before running a large rollback.

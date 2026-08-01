# Troubleshooting

## `No module named endstone_blockdata`

Install the matching BlockData inspector wheel as well as the native library. AntiGrief checks the supported bridge module layouts, but it cannot load a bridge that is not installed.

## Waiting for BlockData services

BlockData registers services during its enable phase. AntiGrief retries on the server thread. Confirm that the BlockData native plugin enabled successfully and that only one version is installed.

## Container restored empty

Look for BlockData restore warnings. Current restoration separates state, metadata, and inventory writes. Verify the source snapshot exists, the container actor becomes available, and the installed BlockData package exactly matches the server runtime.

## Invalid UTF-8 warning

An item contains malformed native text. AntiGrief stores a degraded readable snapshot and rate-limits the warning. Move items out one at a time to identify the damaged stack, then replace or repair it.

## WebUI does not open

Check `enable_web_ui`, `web_ui_port`, console startup messages, firewall rules, and whether another service already uses the port.

## VIEW NBT does nothing

Use the current wheel, clear the browser cache, and verify the API request returns `200` rather than `401`. Older releases contained a broken inline click handler.

## SQLite integer overflow

Current releases store unsigned BlockData revisions as text. Upgrade and keep the existing database so the migration can run.

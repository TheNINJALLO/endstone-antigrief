# AntiGrief 1.5.20

Fix blank or stale dashboard searches. Enter now applies filters; player names are trimmed; area filters accept spaces or commas and support radius zero. Invalid filters and API failures produce visible messages, and a slow previous search cannot replace newer results. Statistics failures no longer block event searches.

Dashboard API requests now send the access key in a header and preserve the dashboard's URL prefix. This fixes requests with special characters in the access key and supports dashboards served under a proxy path. The status badge now shows connection/search state instead of always claiming monitoring.

Fix missing container tracking for copper chests, including all oxidation/waxing variants, lit furnace/smoker block types, and wood shelves. Copper-chest opens previously appeared only as ordinary interactions because they were absent from the container list. Direct shelf/bookshelf/pot interactions now schedule a committed inventory comparison as well.

Record committed item additions and removals while a container is open, using inventory transactions and authoritative item-stack responses to trigger native snapshot comparisons on the following server tick. Each change retains exact before/after item NBT and snapshot IDs for the dashboard and rollback. Changes committed in the same server tick are combined into one slot comparison.

Prevent delayed close callbacks from consuming a newly opened container's tracking session. Preserve the existing baseline across duplicate interactions, and capture final changes on client/server closes, disconnects, and plugin shutdown. Unchanged or rejected transaction notifications do not create duplicate snapshot rows.

Replace the older AntiGrief wheel with `endstone_antigrief-1.5.20-py3-none-any.whl` and restart the server, keeping the existing database and configuration. Confirm `capture_container_open_close` is enabled and BlockData connects. After moving items, allow the database writer up to 20 seconds and refresh the dashboard with the Container filter.

Validation: 107 automated tests pass, including nine headless Chrome tests against a local SQLite-backed API, 30 new simulated container-event tests, and a transaction-to-SQLite-to-dashboard API regression. Ruff checks pass. The updated plugin has not been installed on The Kingdom or validated with live player transfers. It retains the 1.5.19 recovery duplication fixes and `/agstop` command.

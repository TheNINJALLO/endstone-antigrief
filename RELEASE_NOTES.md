# AntiGrief 1.5.20

Record committed item additions and removals while a container is open, using inventory transactions and authoritative item-stack responses to trigger native snapshot comparisons on the following server tick. Each change retains exact before/after item NBT and snapshot IDs for the dashboard and rollback. Changes committed in the same server tick are combined into one slot comparison.

Prevent delayed close callbacks from consuming a newly opened container's tracking session. Preserve the existing baseline across duplicate interactions, and capture final changes on client/server closes, disconnects, and plugin shutdown. Unchanged or rejected transaction notifications do not create duplicate snapshot rows.

Replace the older AntiGrief wheel with `endstone_antigrief-1.5.20-py3-none-any.whl` and restart the server, keeping the existing database and configuration. Confirm `capture_container_open_close` is enabled and BlockData connects. After moving items, allow the database writer up to 20 seconds and refresh the dashboard with the Container filter.

Validation: 84 automated tests pass, including 16 new simulated event tests and a transaction-to-SQLite-to-dashboard API regression. Ruff checks pass. This release candidate has not been installed or validated on the Kingdom server or a live BDS/player session. It retains the 1.5.19 recovery duplication fixes and `/agstop` command.

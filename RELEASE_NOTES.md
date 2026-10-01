# AntiGrief 1.5.19

Fix repeated item duplication during pending `/agback` recovery. Recovery retries now verify the original destination slot without writing container contents. Unavailable destinations leave player inventories untouched and log at most once per minute per rollback/container.

Use `/agstop` (or `agstop` in the console) to cancel all pending rollback work and item recovery. Use `/agstop <recovery ID>` to cancel one batch; unique ID prefixes are accepted. Cancellation persists across restarts, covers offline players, and preserves evidence and already-applied changes.

For an existing duplication loop, stop the server and set `"recover_stolen_items_on_rollback": false` in `plugins/antigrief_data/config.json`. Replace the older AntiGrief wheel with the 1.5.19 wheel, start the server, and run `agstop` in the console. Recovery can then be re-enabled in the config with another restart. Existing duplicated items are not automatically removed.

Container actor readiness still retries, but an attempted inventory write is no longer replayed after uncertain verification. The release retains the 1.5.18 BlockData dependency fixes. Automated SQLite/inventory/scheduler regression coverage is included; this hotfix has not been tested on a live BDS server.

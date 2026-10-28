# Changelog

All notable changes are documented here. This project follows semantic versioning.

## [1.5.23] - 2026-10-28

- Fix startup failure while creating `idx_interactions_coordinates_time` on a large history database with insufficient database, quota, or temporary-disk space. Disable this additional index build by default; retain existing indexes and all audit data.
- Add explicit `build_legacy_history_index: false` configuration. Administrators can opt in after confirming sufficient storage; container ownership/trust caching works without the index.
- Commit required schema and recovery-safety migrations before optional index maintenance. Roll back and report an optional index OperationalError without aborting startup or undoing completed migrations. Required database failures remain visible.
- Add real SQLite page-limit regressions for `SQLITE_FULL`, default/opt-in startup, committed recovery-safety updates, preserved audit history, and logging after capacity is restored.
- Avoid forcing an existing configuration rewrite for the optional index flag on storage-constrained upgrades. All 137 local tests, CI Ruff checks, build, and package verification pass.

## [1.5.22] - 2026-10-28

- Remove routine container open, access, ownership, slot-change, legacy-diff, and backup-cache console messages. Unauthorized activity remains in the database and WebUI; native failures and rollback status remain visible.
- Cache container ownership and trust for 30 seconds in a bounded 2,048-container LRU cache, including unowned legacy containers. Ownership/trust commands and placement invalidate the affected entry immediately. Index historical placement lookups by coordinates and time.
- Skip item diffing and redundant close snapshots when native revisions are unchanged. Compare occupied slots without copying unchanged item NBT; retain detached payloads for changed items and recovery callers. Remove duplicate JSON conversions before snapshot storage and skip player inventory summaries for unchanged revisions.
- Distribute periodic player inventory captures across the configured scan period, resolve players again when tasks run, and stop pending scan work during shutdown. Stagger reconnect captures as well.
- Avoid scheduling native container checks for known ordinary placed blocks, and replace repeated scans of recent placements with a per-player timestamp lookup.
- Target BlockData API 0.6.9 on Endstone 0.11.15, BDS 1.26.60.6, and CPython 3.14 on Linux x86-64. Raise the Endstone package minimum to 0.11.15 and update current installation, dashboard, package, and plugin version metadata.
- Recognize the bridge's `canonical` and `snbt` block-entity fields in snapshot summaries, container audit records, and WebUI SNBT views. Preserve support for older saved `canonical_nbt` and `raw_snbt` fields without modifying canonical restore payloads.
- Exclude both SNBT aliases from queued container snapshots when `store_raw_snbt` is disabled.
- Correct bundle installation instructions: install the complete 0.6.9 inspector wheel, which registers its matching native provider, and remove older standalone BlockData libraries while stopped.
- Add regressions for the matching 0.6.9 bridge/provider handshake, mixed-version rejection, service disconnection, native snapshot metadata, SNBT storage settings, and SQLite-to-WebUI inspection with current and legacy field names.
- Validation: 131 tests and CI Ruff checks pass locally on Python 3.11. A 54-slot nested-bundle benchmark reduces Python inventory diff work from about 16 ms to 0.2–0.5 ms per call; this excludes native capture and is not a live server tick measurement. Native BDS 1.26.60.6 and live player validation remain pending.

## [1.5.20] - 2026-10-15

- Fix dashboard search submission and display: Enter applies filters, player names are trimmed, areas accept spaces or commas, and radius zero searches the exact position. Invalid filters and failed requests show a visible error instead of leaving a blank or stale table. Older responses cannot replace a newer search.
- Send dashboard access keys in request headers and resolve API/report paths relative to the dashboard, including installations behind a path prefix. Statistics failures no longer block event searches; the status badge reports connection/search state instead of always saying monitoring.
- Recognize all eight copper-chest variants, lit furnaces/smokers, and wood shelves as containers. Previously, copper-chest interactions could appear in the dashboard without starting item tracking. Compare the next committed state after direct shelf/bookshelf/pot interactions as well.
- Capture committed container slot changes after inventory transactions and authoritative stack responses, instead of waiting exclusively for a client close packet. Keep exact before/after item NBT and snapshot links for the dashboard and rollback.
- Keep a running baseline so additions and removals across successive server ticks remain separate evidence even when the final inventory matches its opening state. Coalesce notifications in the same tick and avoid storing unchanged transaction snapshots.
- Bind delayed captures to their original container session; flush the old session before opening another container. Capture final changes on server closes, disconnects, and plugin shutdown, and reconnect BlockData when a native session closes after a service interruption.
- Add 39 regression cases, including nine headless Chrome cases against a local SQLite-backed API and 30 simulated container-event cases. The full 107-test suite passes. The updated plugin has not been installed on the live server.

## [1.5.19] - 2026-10-01

- Stop recovery sweeps from refilling containers. Recovery now only verifies the original restored slot before removing matching player items, and uses the rollback target count instead of a later event's stack count.
- Add operator/console command `/agstop [all|recovery ID]` to cancel delayed block/container work and persistently cancel pending recovery, including offline players and queues created by older versions. Applied changes and evidence remain recorded.
- Group unavailable-destination warnings by rollback/container and limit them to once per minute, with the cancellation command included.
- Wait for container actors to become ready, but do not replay an attempted inventory restore after uncertain verification.

## [1.5.18] - 2026-09-27

- Read native provider metadata through Endstone's bound description method so incompatible BlockData versions are rejected before bridge calls. Verified against the real native plugin wrapper.

## [1.5.17] - 2026-09-27

### Fixed
- Prefer the current BlockData inspector bridge and detect mixed native/bridge versions before calling the native API.
- Clear stale capability state and reconnect after the native service becomes unavailable.
- Document the complete BlockData 0.6.6 bundle wheel and its exact Linux runtime requirements.
- Retain the 1.5.16 SQLite Unicode fixes; 48 regression tests pass.

## [1.5.16] - 2026-09-09

### Fixed
- Prevented repeated SQLite flush failures when BlockData captures non-UTF-8 NBT strings. Container, player-inventory, interaction, recovery, and report JSON now escape surrogate characters while preserving the original bytes for restore.
- Escaped diagnostic raw SNBT at the database boundary and handled already-buffered unescaped JSON without dropping records or changing the database schema.
- Added persistence and batch-flush regressions for the reported `0x8a` byte at position 6125, all byte values, malformed UTF-8, valid Unicode, embedded NULs, and literal escape sequences.

## [1.5.13] - 2026-08-01

### Added
- Complete public documentation, command reference, WebUI guide, troubleshooting guide, and Wiki source.
- GitHub Actions workflows for validation, releases, Pages documentation, and Wiki synchronization.
- Repository governance files, issue templates, pull-request template, and security policy.

### Changed
- Unified the package, plugin, and module version metadata.
- Reorganized release documentation around installation, daily administration, rollback, and evidence workflows.
- Removed generated cache files and build artifacts from the source package.

### Included from 1.5.12
- UTF-8-safe fallback capture for malformed player inventory NBT.
- Exact BlockData container and player inventory inspection.
- Recursive bundle and custom storage-item rendering.
- Coordinated rollback, confirmed item recovery, and printable grief reports.

[Unreleased]: https://github.com/TheNINJALLO/endstone-antigrief/compare/v1.5.23...HEAD
[1.5.23]: https://github.com/TheNINJALLO/endstone-antigrief/releases/tag/v1.5.23
[1.5.22]: https://github.com/TheNINJALLO/endstone-antigrief/releases/tag/v1.5.22
[1.5.16]: https://github.com/TheNINJALLO/endstone-antigrief/releases/tag/v1.5.16
[1.5.13]: https://github.com/TheNINJALLO/endstone-antigrief/releases/tag/v1.5.13

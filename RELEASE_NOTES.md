# AntiGrief 1.5.23

Fix the 1.5.22 startup failure at `idx_interactions_coordinates_time` when SQLite reports `database or disk is full`. Building this additional index over a large audit history requires database and temporary storage and must not be required just to load AntiGrief.

The new index is disabled by default. Existing indexes and audit records are retained. Administrators can opt in with `"build_legacy_history_index": true` after confirming sufficient database, container-quota, and temporary-disk space. Required schema and recovery-safety migrations commit before optional maintenance; an optional index OperationalError rolls back only that maintenance, emits one startup diagnostic, and allows startup to continue. Required database failures remain visible.

While stopped, replace the 1.5.22 wheel with `endstone_antigrief-1.5.23-py3-none-any.whl`. Keep the existing `plugins/antigrief_data` folder and database. No manual SQL or index rebuild is needed. If storage is actually full, free space or increase the server's storage quota before expecting audit writes to resume. This hotfix avoids the forced index build; it cannot create storage capacity.

The optional setting can be omitted; this upgrade does not force an existing configuration file to be rewritten just to add it.

Retain BlockData 0.6.9, Endstone 0.11.15, BDS 1.26.60.6, and CPython 3.14 on Linux x86-64. The quiet container logging, ownership cache, snapshot-copy optimizations, and distributed player inventory scans from 1.5.22 remain included.

Validation includes real SQLite page-limit exhaustion during an optional index build, default startup without the index, preservation of committed recovery-safety updates and audit history, successful writes after restoring capacity, and opt-in index creation. See [installation](docs/installation.md) and [troubleshooting](docs/troubleshooting.md).

Local validation: 137 tests, CI Ruff checks, build, and package verification pass on CPython 3.11.9. The affected live server filesystem has not been inspected or modified.

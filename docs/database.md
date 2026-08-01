# Database and Retention

AntiGrief stores data in `plugins/antigrief_data/agdata.db` using SQLite.

Important record groups include ordinary interactions, container snapshots, player inventory snapshots, container ownership records, confirmed recovery rows, bans, and immutable grief reports.

## Backups

Stop the server or use a SQLite-safe backup method before copying the database. Keep database backups with world backups so rollback evidence and world state remain aligned.

## Cleanup

`/agclean <hours>` removes ordinary records older than the selected age. Completed grief reports are retained. Review retention and disk use regularly on active servers.

## Migration

Schema changes are applied automatically during startup. Preserve the database when replacing the wheel.

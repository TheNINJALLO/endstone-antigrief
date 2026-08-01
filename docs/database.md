# 🗄️ Database Architecture & Data Retention

AntiGrief stores interaction records, container NBT snapshots, player inventory history, and grief proof reports in SQLite using Write-Ahead Logging (WAL) mode.

---

## 📂 Storage Location

Database files are stored inside `plugins/antigrief_data/`:

- `agdata.db`: SQLite database file containing logs, snapshots, and reports.
- `agdata.db-wal`: SQLite Write-Ahead Log (temporary write journal).
- `agdata.db-shm`: Shared memory index file for concurrent WAL readers.

---

## 📊 Database Tables

| Table Name | Description | Key Indexes |
|---|---|---|
| `interactions` | Stores block breaks, places, kills, and container accesses. | `time`, `name`, `(x, y, z)` |
| `container_snapshots` | Stores exact NBT snapshots for containers. | `pos_key`, `time` |
| `player_inventories` | Stores snapshots of online player inventories and Ender Chests. | `name`, `time` |
| `grief_reports` | Stores immutable `/agback` report payloads and SHA-256 hashes. | `report_id`, `created_at` |
| `confiscation_queue` | Stores pending item recovery tasks created by `/agback`. | `player_name`, `status` |

---

## 🧹 Maintenance & Retention (`/agclean`)

To prevent database file growth from impacting server disk space, operators can execute `/agclean`:

```text
/agclean <hours>
```

- **What gets cleaned**: Deletes ordinary interaction logs older than the specified hours.
- **What stays protected**: Immutable `grief_reports` and active `confiscation_queue` rows are **NEVER** deleted by `/agclean`.

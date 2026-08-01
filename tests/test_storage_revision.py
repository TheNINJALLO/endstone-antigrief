import ast
from pathlib import Path
import sqlite3


def load_storage_functions(db_path):
    source_path = Path(__file__).parents[1] / "src/endstone_antigrief/antigrief_plugin.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {"_sqlite_signed_int", "insert_container_snapshots"}
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"sqlite3": sqlite3, "DB_FILE": str(db_path)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace


def test_unsigned_blockdata_revision_is_stored_exactly_as_text(tmp_path):
    db_path = tmp_path / "agdata.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE container_snapshots (
                snapshot_id TEXT PRIMARY KEY, player_name TEXT, reason TEXT,
                x INTEGER, y INTEGER, z INTEGER, world TEXT, block_type TEXT,
                revision INTEGER, revision_text TEXT, captured_at TEXT,
                occupied_slots INTEGER, item_count INTEGER, canonical_nbt INTEGER,
                snapshot_json TEXT NOT NULL, raw_snbt TEXT
            )"""
        )

    functions = load_storage_functions(db_path)
    huge_revision = (1 << 64) - 1
    functions["insert_container_snapshots"]([
        {
            "snapshot_id": "huge",
            "player_name": "Steve",
            "reason": "block_break",
            "x": 1, "y": 2, "z": 3,
            "world": "overworld",
            "block_type": "minecraft:chest",
            "revision": huge_revision,
            "captured_at": "2026-07-27T22:50:00-04:00",
            "occupied_slots": 1,
            "item_count": 64,
            "canonical_nbt": True,
            "snapshot_json": "{}",
            "raw_snbt": "{}",
        }
    ])

    with sqlite3.connect(db_path) as db:
        revision, revision_text = db.execute(
            "SELECT revision, revision_text FROM container_snapshots WHERE snapshot_id='huge'"
        ).fetchone()
    assert revision is None
    assert revision_text == str(huge_revision)


def test_legacy_snapshot_table_is_migrated_with_revision_text(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, action TEXT,
                x INTEGER, y INTEGER, z INTEGER, type TEXT, world TEXT,
                time TEXT, blockdata TEXT
            );
            CREATE TABLE container_snapshots (
                snapshot_id TEXT PRIMARY KEY, player_name TEXT, reason TEXT,
                x INTEGER, y INTEGER, z INTEGER, world TEXT, block_type TEXT,
                revision INTEGER, captured_at TEXT, occupied_slots INTEGER,
                item_count INTEGER, canonical_nbt INTEGER,
                snapshot_json TEXT NOT NULL, raw_snbt TEXT
            );
            """
        )
        db.execute(
            "INSERT INTO container_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy", "Steve", "open", 1, 2, 3, "overworld",
                "minecraft:chest", 42, "2026-07-27T20:00:00-04:00",
                0, 0, 1, "{}", "{}",
            ),
        )
        db.commit()

    source_path = Path(__file__).parents[1] / "src/endstone_antigrief/antigrief_plugin.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    init_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "init_database"
    )
    namespace = {"sqlite3": sqlite3, "DB_FILE": str(db_path)}
    exec(compile(ast.Module(body=[init_node], type_ignores=[]), str(source_path), "exec"), namespace)
    conn, _ = namespace["init_database"]()
    conn.close()

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(container_snapshots)")}
        revision_text = db.execute(
            "SELECT revision_text FROM container_snapshots WHERE snapshot_id='legacy'"
        ).fetchone()[0]
    assert "revision_text" in columns
    assert revision_text == "42"

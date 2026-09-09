import ast
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import threading

import pytest


def load_writers(db_path):
    source_path = Path(__file__).parents[1] / "src/endstone_antigrief/antigrief_plugin.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = {
        "init_database", "_sqlite_text", "_sqlite_signed_int", "insert_records",
        "insert_container_snapshots", "insert_player_inventory_snapshots",
        "update_player_inventory_presence", "_take_buffer", "_requeue_buffer",
        "flush_data_to_db",
    }
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        "sqlite3": sqlite3, "DB_FILE": str(db_path), "is_cleaning": False,
        "buffer_lock": threading.Lock(), "db_write_lock": threading.Lock(),
        "data_buffers": defaultdict(list),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source_path), "exec"), namespace)
    connection, _ = namespace["init_database"]()
    connection.close()
    return namespace


@pytest.mark.parametrize("raw", [
    b"x" * 6125 + b"\x8a", bytes(range(256)), b"\xd0", b"\xc2A", b"\xf3",
    b"\xed\xa0\x80", b"", b"NUL\0end", "Name: \u00a7\U0001f48e".encode("utf-8"),
    b"literal \\udc8a and \\x8a",
], ids=["reported-byte", "all-bytes", "truncated-2", "invalid-continuation", "truncated-4",
        "surrogate-utf8", "empty", "nul", "unicode", "literal-escapes"])
def test_buffered_nbt_flushes_and_restores_exact_bytes(tmp_path, raw):
    db_path = tmp_path / "agdata.db"
    writers = load_writers(db_path)
    value = raw.decode("utf-8", "surrogateescape")
    payload = {"Items": [{"tag": {value: {"Name": value, "Lore": [value]}}}]}
    # Reproduce old buffered records, including unescaped canonical JSON.
    snapshot_json = json.dumps(payload, ensure_ascii=False)
    buffers = writers["data_buffers"]
    for snapshot_id, content in [("before", "{}"), ("invalid", snapshot_json), ("after", "{}")]:
        buffers["container_snapshot"].append({
            "snapshot_id": snapshot_id, "x": 1, "y": 64, "z": 2,
            "world": "overworld", "captured_at": "2026-09-09T12:00:00-04:00",
            "snapshot_json": content, "raw_snbt": value if snapshot_id == "invalid" else None,
        })
    buffers["player_inventory_snapshot"].append({
        "player_key": "steve", "snapshot_id": "inventory", "player_name": "Steve",
        "captured_at": "2026-09-09T12:00:00-04:00", "snapshot_json": snapshot_json,
    })
    buffers["container_access"].append({
        "name": "Steve", "action": "container_access",
        "coordinates": {"x": 1, "y": 64, "z": 2}, "type": "minecraft:barrel",
        "world": "overworld", "time": "2026-09-09T12:00:00-04:00", "blockdata": snapshot_json,
    })

    writers["flush_data_to_db"]()
    assert not any(buffers.values())
    writers["flush_data_to_db"]()  # Empty retries do not duplicate records.
    with sqlite3.connect(db_path) as database:
        assert database.execute("SELECT count(*) FROM container_snapshots").fetchone()[0] == 3
        saved_json, snbt = database.execute(
            "SELECT snapshot_json, raw_snbt FROM container_snapshots WHERE snapshot_id='invalid'"
        ).fetchone()
        inventory_json = database.execute("SELECT snapshot_json FROM player_inventory_snapshots").fetchone()[0]
        interaction_json = database.execute("SELECT blockdata FROM interactions").fetchone()[0]
        assert database.execute(
            "SELECT raw_snbt FROM container_snapshots WHERE snapshot_id='before'"
        ).fetchone()[0] is None
    for serialized in (saved_json, inventory_json, interaction_json):
        serialized.encode("utf-8")
        restored = json.loads(serialized)
        assert restored == payload
        assert restored["Items"][0]["tag"][value]["Name"].encode("utf-8", "surrogateescape") == raw
    assert snbt == value.encode("utf-8", "backslashreplace").decode("utf-8")
    snbt.encode("utf-8")

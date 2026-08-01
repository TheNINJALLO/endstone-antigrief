from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient


def load_webui():
    path = Path(__file__).parents[1] / "src/endstone_antigrief/webui.py"
    spec = spec_from_file_location("webui_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_snapshot_log_detail_and_dashboard_button(tmp_path):
    webui = load_webui()
    db_path = tmp_path / "agdata.db"
    huge_revision = (1 << 64) - 1
    snapshot = {
        "location": {"dimension": "overworld", "x": 1, "y": 2, "z": 3},
        "type": "minecraft:chest",
        "revision": huge_revision,
        "block_entity": {
            "nbt": {"CustomName": "Vault"},
            "raw_snbt": '{CustomName:"Vault"}',
            "canonical_nbt": True,
            "is_container": True,
            "container_size": 27,
            "inventory": [
                {
                    "slot": 0,
                    "item": {
                        "id": "minecraft:diamond_sword",
                        "count": 1,
                        "tag": {"display": {"Name": "Blade"}},
                    },
                }
            ],
        },
    }
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE interactions(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,action TEXT,x INTEGER,y INTEGER,z INTEGER,type TEXT,world TEXT,time TEXT,blockdata TEXT);
            CREATE TABLE container_snapshots(snapshot_id TEXT PRIMARY KEY,player_name TEXT,reason TEXT,x INTEGER,y INTEGER,z INTEGER,world TEXT,block_type TEXT,revision INTEGER,revision_text TEXT,captured_at TEXT,occupied_slots INTEGER,item_count INTEGER,canonical_nbt INTEGER,snapshot_json TEXT NOT NULL,raw_snbt TEXT);
            CREATE TABLE bans(player_name TEXT, reason TEXT, banned_at TEXT);
            """
        )
        db.execute(
            "INSERT INTO container_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "snap1", "Steve", "container_open", 1, 2, 3, "overworld",
                "minecraft:chest", None, str(huge_revision),
                "2026-07-27T20:00:00-04:00", 1, 1, 1,
                json.dumps(snapshot), '{CustomName:"Vault"}',
            ),
        )
        cursor = db.execute(
            "INSERT INTO interactions(name,action,x,y,z,type,world,time,blockdata) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "Steve", "Interact", 1, 2, 3, "minecraft:chest",
                "overworld", "2026-07-27T20:00:01-04:00",
                json.dumps({
                    "provider": "endstone-blockdata-api",
                    "snapshot_id": "snap1",
                    "reason": "container_open",
                }),
            ),
        )
        interact_id = cursor.lastrowid
        db.execute(
            "INSERT INTO interactions(name,action,x,y,z,type,world,time,blockdata) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "Steve", "Container Take", 1, 2, 3, "minecraft:diamond_sword",
                "overworld", "2026-07-27T20:01:00-04:00",
                json.dumps({
                    "provider": "endstone-blockdata-api",
                    "before_snapshot_id": "snap0",
                    "after_snapshot_id": "snap1",
                    "before_item": snapshot["block_entity"]["inventory"][0]["item"],
                }),
            ),
        )
        db.commit()

    webui.DB_FILE = str(db_path)
    webui.get_web_config = lambda: {"secret": "secret", "max_results": 10000}
    client = TestClient(webui.create_app())

    logs = client.get("/api/logs?secret=secret&hours=10000")
    assert logs.status_code == 200
    assert any(log["snapshot_id"] == "snap1" for log in logs.json()["logs"])

    detail = client.get("/api/container-snapshots/snap1?secret=secret")
    assert detail.status_code == 200
    assert detail.json()["metadata"]["revision"] == str(huge_revision)
    assert detail.json()["snapshot"]["block_entity"]["nbt"]["CustomName"] == "Vault"
    assert detail.json()["snapshot"]["block_entity"]["inventory"][0]["item"]["tag"]["display"]["Name"] == "Blade"

    log_detail = client.get(f"/api/logs/{interact_id}/blockdata?secret=secret")
    assert log_detail.status_code == 200
    assert log_detail.json()["resolved_snapshot_id"] == "snap1"
    assert log_detail.json()["snapshot"]["block_entity"]["inventory"][0]["item"]["id"] == "minecraft:diamond_sword"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert 'data-log-id="' in dashboard.text
    assert 'data-snapshot-id="' in dashboard.text
    assert 'onclick="openNbt(' not in dashboard.text
    assert "button.nbt-button[data-log-id]" in dashboard.text
    assert "Item List" in dashboard.text
    assert "function renderInventory(payload)" in dashboard.text
    assert "Show raw item NBT" in dashboard.text
    assert "Enchantments" in dashboard.text
    assert "showNbtTab('items')" in dashboard.text

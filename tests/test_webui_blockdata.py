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


def test_snapshot_and_log_detail_endpoints(tmp_path):
    webui = load_webui()
    db_path = tmp_path / "agdata.db"
    snapshot = {
        "location": {"dimension": "overworld", "x": 1, "y": 2, "z": 3},
        "type": "minecraft:chest",
        "revision": 11,
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
            CREATE TABLE container_snapshots(snapshot_id TEXT PRIMARY KEY,player_name TEXT,reason TEXT,x INTEGER,y INTEGER,z INTEGER,world TEXT,block_type TEXT,revision INTEGER,captured_at TEXT,occupied_slots INTEGER,item_count INTEGER,canonical_nbt INTEGER,snapshot_json TEXT NOT NULL,raw_snbt TEXT);
            CREATE TABLE bans(player_name TEXT, reason TEXT, banned_at TEXT);
            """
        )
        db.execute(
            "INSERT INTO container_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "snap1", "Steve", "container_open", 1, 2, 3, "overworld",
                "minecraft:chest", 11, "2026-07-27T20:00:00-04:00", 1, 1, 1,
                json.dumps(snapshot), '{CustomName:"Vault"}',
            ),
        )
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
    assert logs.json()["logs"][0]["snapshot_id"] == "snap1"

    detail = client.get("/api/container-snapshots/snap1?secret=secret")
    assert detail.status_code == 200
    assert detail.json()["snapshot"]["block_entity"]["nbt"]["CustomName"] == "Vault"
    assert detail.json()["snapshot"]["block_entity"]["inventory"][0]["item"]["tag"]["display"]["Name"] == "Blade"

import ast
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient


ROOT = Path(__file__).parents[1]
PLUGIN_PATH = ROOT / "src/endstone_antigrief/antigrief_plugin.py"
PLUGIN_SOURCE = PLUGIN_PATH.read_text(encoding="utf-8")
PLUGIN_TREE = ast.parse(PLUGIN_SOURCE)


def load_webui():
    path = ROOT / "src/endstone_antigrief/webui.py"
    spec = spec_from_file_location("webui_report_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_grief_report_database_schema_is_migrated(tmp_path):
    init_node = next(
        node for node in PLUGIN_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "init_database"
    )
    db_path = tmp_path / "agdata.db"
    namespace = {"sqlite3": sqlite3, "DB_FILE": str(db_path)}
    exec(
        compile(ast.Module(body=[init_node], type_ignores=[]), str(PLUGIN_PATH), "exec"),
        namespace,
    )
    connection, _ = namespace["init_database"]()
    connection.close()

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(grief_reports)")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(grief_reports)")}

    assert {
        "report_id", "rollback_id", "admin_name", "status", "primary_player",
        "event_count", "containers_looted", "containers_broken",
        "items_reported", "items_recovered", "evidence_hash", "report_json",
    } <= columns
    assert "idx_grief_reports_created" in indexes


def test_agback_automatically_creates_and_finalizes_report():
    assert "_build_grief_report(" in PLUGIN_SOURCE
    assert "report_id = self._store_grief_report(report)" in PLUGIN_SOURCE
    assert "self._schedule_grief_report_finalize(report_id, rollback_id, targets)" in PLUGIN_SOURCE
    assert "Grief proof report" in PLUGIN_SOURCE
    assert "hashlib.sha256" in PLUGIN_SOURCE
    assert "antigrief-grief-report-v1" in PLUGIN_SOURCE


def test_report_api_dashboard_and_print_page(tmp_path):
    webui = load_webui()
    db_path = tmp_path / "agdata.db"
    report = {
        "schema_version": "antigrief-grief-report-v1",
        "report_id": "AGR-20260729-010203-ABC123",
        "rollback_id": "rollback-1",
        "evidence_hash": "f" * 64,
        "created_at": "2026-07-29T01:02:03-04:00",
        "completed_at": "2026-07-29T01:02:15-04:00",
        "status": "completed",
        "admin": "AdminOne",
        "primary_player": "GrieferOne",
        "players": [{"name": "GrieferOne", "event_count": 4}],
        "worlds": ["Overworld"],
        "area": {
            "query": {
                "hours": 1.0,
                "center": {"x": 48, "y": 22, "z": 68},
                "radius": 20.0,
                "player_filter": "GrieferOne",
            },
            "bounds_by_world": {
                "Overworld": {
                    "min_x": 46, "min_y": 21, "min_z": 68,
                    "max_x": 52, "max_y": 22, "max_z": 75,
                }
            },
        },
        "summary": {
            "event_count": 4,
            "affected_positions": 3,
            "blocks_broken": 1,
            "blocks_placed": 0,
            "explosions": 0,
            "containers_looted": 1,
            "containers_tampered": 0,
            "containers_broken": 1,
            "items_reported": 7,
            "items_recovered": 7,
            "actions": {"Container Take": 1, "Break": 2},
            "block_types": {"minecraft:chest": 2, "minecraft:grass_block": 1},
        },
        "containers": [{
            "world": "Overworld", "x": 48, "y": 22, "z": 73,
            "container_type": "minecraft:chest", "players": ["GrieferOne"],
            "actions": ["Container Take", "Break"], "broken": True,
            "items": [{
                "slot": 4, "item_id": "minecraft:diamond_sword", "count": 1,
                "custom_name": "Blade of Proof", "lore": ["Recovered evidence"],
                "enchantments": [{"id": "sharpness", "level": 5}],
                "item": {"id": "minecraft:diamond_sword", "count": 1},
            }],
        }],
        "events": [{
            "interaction_id": 1, "player": "GrieferOne", "action": "Container Take",
            "category": "container_loot", "time": "2026-07-29T01:01:00-04:00",
            "world": "Overworld", "position": {"x": 48, "y": 22, "z": 73},
            "target": "minecraft:diamond_sword",
            "items": [{"slot": 4, "item_id": "minecraft:diamond_sword", "count": 1}],
        }],
        "rollback": {
            "execution": {"initial_blocks_verified": 3},
            "verification": {
                "verified_blocks": 1, "failed_blocks": 0,
                "verified_containers": 1, "failed_containers": 0,
                "positions": [{
                    "world": "Overworld", "x": 48, "y": 22, "z": 73,
                    "expected_block": "minecraft:chest", "actual_block": "minecraft:chest",
                    "block_restored": True, "container_inventory_restored": True,
                }],
            },
            "recovery": {"returned_to_containers": 7, "pending_rows": 0},
        },
    }
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE grief_reports(
                report_id TEXT PRIMARY KEY,rollback_id TEXT UNIQUE,created_at TEXT,
                completed_at TEXT,admin_name TEXT,status TEXT,center_x INTEGER,
                center_y INTEGER,center_z INTEGER,radius REAL,hours REAL,
                player_filter TEXT,primary_player TEXT,event_count INTEGER,
                affected_positions INTEGER,blocks_broken INTEGER,blocks_placed INTEGER,
                explosions INTEGER,containers_looted INTEGER,containers_broken INTEGER,
                items_reported INTEGER,items_recovered INTEGER,evidence_hash TEXT,
                players_json TEXT,worlds_json TEXT,summary_json TEXT,report_json TEXT
            );
            CREATE TABLE pending_confiscations(
                rollback_id TEXT,requested_amount INTEGER,removed_amount INTEGER,
                returned_amount INTEGER,status TEXT
            );
            """
        )
        db.execute(
            "INSERT INTO grief_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                report["report_id"], report["rollback_id"], report["created_at"],
                report["completed_at"], report["admin"], report["status"],
                48, 22, 68, 20.0, 1.0, "GrieferOne", "GrieferOne", 4, 3,
                1, 0, 0, 1, 1, 7, 7, report["evidence_hash"],
                json.dumps(report["players"]), json.dumps(report["worlds"]),
                json.dumps(report["summary"]), json.dumps(report),
            ),
        )
        db.execute(
            "INSERT INTO pending_confiscations VALUES (?,?,?,?,?)",
            ("rollback-1", 7, 7, 7, "complete"),
        )
        db.commit()

    webui.DB_FILE = str(db_path)
    webui.get_web_config = lambda: {"secret": "secret", "max_results": 10000}
    client = TestClient(webui.create_app())

    listing = client.get("/api/grief-reports?secret=secret")
    assert listing.status_code == 200
    assert listing.json()["reports"][0]["primary_player"] == "GrieferOne"
    assert listing.json()["reports"][0]["items_recovered"] == 7

    detail = client.get(f"/api/grief-reports/{report['report_id']}?secret=secret")
    assert detail.status_code == 200
    assert detail.json()["report"]["evidence_hash"] == "f" * 64
    assert detail.json()["live_recovery"]["returned"] == 7

    printed = client.get(f"/reports/{report['report_id']}?secret=secret")
    assert printed.status_code == 200
    assert "Grief Incident Report" in printed.text
    assert "GrieferOne" in printed.text
    assert "Blade of Proof" in printed.text
    assert "Print / Save PDF" in printed.text
    assert "SHA-256" in printed.text

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Grief Proof Reports" in dashboard.text
    assert "function loadGriefReports()" in dashboard.text
    assert "VIEW / PRINT" in dashboard.text
    assert "/reports/${encodeURIComponent(reportId)}" in dashboard.text

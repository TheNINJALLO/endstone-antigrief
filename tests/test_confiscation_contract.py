import ast
from pathlib import Path
import sqlite3


SOURCE_PATH = Path(__file__).parents[1] / "src/endstone_antigrief/antigrief_plugin.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
PLUGIN = next(
    node for node in TREE.body
    if isinstance(node, ast.ClassDef) and node.name == "AntiGriefPlugin"
)


def method_source(name: str) -> str:
    node = next(
        node for node in PLUGIN.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    return ast.get_source_segment(SOURCE, node) or ""


def test_rollback_recovery_schema_is_migrated_and_legacy_rows_are_cancelled(tmp_path):
    init_node = next(
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "init_database"
    )
    db_path = tmp_path / "agdata.db"
    namespace = {"sqlite3": sqlite3, "DB_FILE": str(db_path)}
    exec(compile(ast.Module(body=[init_node], type_ignores=[]), str(SOURCE_PATH), "exec"), namespace)
    conn, _ = namespace["init_database"]()
    conn.close()

    with sqlite3.connect(db_path) as db:
        db.execute(
            """INSERT INTO pending_confiscations
               (theft_key,player_name,owner_name,world,x,y,z,item_json,
                requested_amount,removed_amount,status,reason,created_at,updated_at)
               VALUES ('legacy','Friend','Owner','overworld',1,2,3,'{}',1,0,
                       'pending','unauthorized_container_access:slot:0','a','a')"""
        )
        db.commit()

    conn, _ = namespace["init_database"]()
    conn.close()
    with sqlite3.connect(db_path) as db:
        pending_columns = {
            row[1] for row in db.execute("PRAGMA table_info(pending_confiscations)")
        }
        legacy_status = db.execute(
            "SELECT status FROM pending_confiscations WHERE theft_key='legacy'"
        ).fetchone()[0]

    assert {
        "destination_slot", "rollback_id", "returned_amount", "trigger_action"
    } <= pending_columns
    assert legacy_status == "cancelled"


def test_open_close_break_and_join_never_create_unconfirmed_recoveries():
    assert "_queue_confiscation" not in method_source("on_block_break")
    assert "_queue_snapshot_confiscations" not in method_source("on_block_break")
    assert "_queue_confiscation" not in method_source("_diff_player_inventory")
    assert "AUTO_CONFISCATE = False" in SOURCE


def test_queue_rejects_any_non_agback_recovery():
    queue_source = method_source("_queue_confiscation")
    assert "if not rollback_id" in queue_source
    assert "startswith('rollback_recovery:')" in queue_source
    assert "trigger_action" in queue_source
    assert "'agback'" in queue_source


def test_pending_processing_is_limited_to_confirmed_rollback_rows():
    apply_source = method_source("_apply_pending_confiscations")
    assert "reason LIKE 'rollback_recovery:%'" in apply_source
    assert "rollback_id IS NOT NULL" in apply_source
    assert "_ensure_recovery_destination" in apply_source
    assert apply_source.index("_ensure_recovery_destination") < apply_source.index(
        "_remove_canonical_item_from_player"
    )
    assert "Rollback Item Recovered" in apply_source


def test_agback_restores_container_history_and_creates_recovery_batch():
    rollback_source = method_source("_execute_rollback")
    assert "'Container Add','Container Take','Container Change','Container NBT Change'" in rollback_source
    assert "before_snapshot_id" in rollback_source
    assert "rollback_recovery:" in rollback_source
    assert "rollback_id=rollback_id" in rollback_source
    assert "run_recovery_batch" in rollback_source
    assert "delay=40" in rollback_source


def test_owner_and_manual_retry_commands_remain_registered():
    assert '"agowner"' in SOURCE
    assert '"agconfiscate"' in SOURCE
    assert "/agowner <info|set|trust|untrust|clear>" in SOURCE

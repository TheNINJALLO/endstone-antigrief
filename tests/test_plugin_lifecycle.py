from pathlib import Path
import ast


def test_plugin_does_not_override_constructor():
    source = Path(__file__).parents[1] / "src" / "endstone_antigrief" / "antigrief_plugin.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AntiGriefPlugin")
    method_names = {node.name for node in plugin.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "__init__" not in method_names


def test_runtime_state_is_initialized_in_on_load():
    source = Path(__file__).parents[1] / "src" / "endstone_antigrief" / "antigrief_plugin.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AntiGriefPlugin")
    on_load = next(node for node in plugin.body if isinstance(node, ast.FunctionDef) and node.name == "on_load")
    body = ast.get_source_segment(source.read_text(encoding="utf-8"), on_load) or ""
    assert "self.blockdata = BlockDataAdapter()" in body
    assert "self._blockdata_ready = False" in body


def test_blockdata_dependency_and_delayed_service_retry_are_declared():
    source = Path(__file__).parents[1] / "src" / "endstone_antigrief" / "antigrief_plugin.py"
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text)
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AntiGriefPlugin")
    assignments = {
        target.id: node.value
        for node in plugin.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assignments["depend"]) == ["blockdata_api"]
    methods = {node.name for node in plugin.body if isinstance(node, ast.FunctionDef)}
    assert {
        "_connect_blockdata_services",
        "_schedule_blockdata_connect_retry",
        "_ensure_blockdata_ready",
    } <= methods
    on_enable = next(node for node in plugin.body if isinstance(node, ast.FunctionDef) and node.name == "on_enable")
    body = ast.get_source_segment(text, on_enable) or ""
    assert "_connect_blockdata_services(initial=True)" in body
    assert "_schedule_blockdata_connect_retry()" in body


def test_blockdata_retry_is_server_thread_scheduled_and_inventory_sweeper_is_idempotent():
    source = Path(__file__).parents[1] / "src" / "endstone_antigrief" / "antigrief_plugin.py"
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text)
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AntiGriefPlugin")
    retry = next(node for node in plugin.body if isinstance(node, ast.FunctionDef) and node.name == "_schedule_blockdata_connect_retry")
    retry_body = ast.get_source_segment(text, retry) or ""
    assert "self.server.scheduler.run_task" in retry_body
    assert "threading" not in retry_body
    sweeper = next(node for node in plugin.body if isinstance(node, ast.FunctionDef) and node.name == "_start_player_inventory_snapshot_sweeper")
    sweeper_body = ast.get_source_segment(text, sweeper) or ""
    assert "self._player_inventory_sweeper_started" in sweeper_body


def test_utf8_capture_failure_has_public_fallback_and_warning_backoff():
    source = Path(__file__).parents[1] / "src" / "endstone_antigrief" / "antigrief_plugin.py"
    text = source.read_text(encoding="utf-8")
    assert "except UnicodeDecodeError as error" in text
    assert "public_player_inventory_snapshot" in text
    assert "PLAYER_INVENTORY_DECODE_WARNING_COOLDOWN" in text
    assert "_player_inventory_capture_warning_times" in text

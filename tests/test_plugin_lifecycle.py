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

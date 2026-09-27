from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def module():
    path = Path(__file__).parents[1] / "src/endstone_antigrief/blockdata_adapter.py"
    spec = spec_from_file_location("bridge_lifecycle_test", path)
    result = module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_current_bundled_bridge_precedes_stale_sdk_copy(monkeypatch):
    loaded = module()
    bridge = SimpleNamespace(available=lambda server: True, capabilities=lambda server: {"inventory": True})
    importer = Mock(return_value=bridge)
    monkeypatch.setattr(loaded.importlib, "import_module", importer)
    adapter = loaded.BlockDataAdapter()
    assert adapter.connect(object())
    importer.assert_called_once_with("endstone_blockdata_inspector._endstone_blockdata_live")


def test_version_mismatch_is_rejected_before_calling_native_bridge(monkeypatch):
    loaded = module()
    bridge = SimpleNamespace(__version__="0.6.6", available=Mock())
    monkeypatch.setattr(loaded.importlib, "import_module", lambda name: bridge)
    server = SimpleNamespace(plugin_manager=SimpleNamespace(
        get_plugin=lambda name: SimpleNamespace(_get_description=lambda: SimpleNamespace(version="0.4.8"))))
    adapter = loaded.BlockDataAdapter()
    assert not adapter.connect(server)
    assert "do not match" in adapter.error
    bridge.available.assert_not_called()


def test_failed_reconnect_clears_previous_native_capabilities(monkeypatch):
    loaded = module()
    adapter = loaded.BlockDataAdapter()
    adapter.bridge = SimpleNamespace(available=lambda server: True)
    adapter.capabilities = {"inventory": True}
    adapter.player_inventory_capabilities = {"main": True}
    monkeypatch.setattr(loaded.importlib, "import_module", Mock(side_effect=ImportError("ABI mismatch")))
    assert not adapter.connect(object())
    assert not adapter.available
    assert not adapter.capabilities
    assert not adapter.player_inventory_available


def test_native_disable_is_detected_before_more_reads():
    adapter = module().BlockDataAdapter()
    adapter.bridge = SimpleNamespace(available=lambda server: False)
    adapter.capabilities = {"inventory": True}
    adapter.player_inventory_capabilities = {"main": True}
    assert not adapter.check_connection(object())
    assert not adapter.available
    assert not adapter.capabilities

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


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


@pytest.mark.parametrize("bridge_version,provider_version", [
    ("0.6.6", "0.4.8"), ("0.6.8", "0.6.9"), ("0.6.9", "0.6.8"),
])
def test_version_mismatch_is_rejected_before_calling_native_bridge(
    monkeypatch, bridge_version, provider_version,
):
    loaded = module()
    bridge = SimpleNamespace(__version__=bridge_version, available=Mock())
    monkeypatch.setattr(loaded.importlib, "import_module", lambda name: bridge)
    server = SimpleNamespace(plugin_manager=SimpleNamespace(
        get_plugin=lambda name: SimpleNamespace(_get_description=lambda: SimpleNamespace(version=provider_version))))
    adapter = loaded.BlockDataAdapter()
    assert not adapter.connect(server)
    assert "do not match" in adapter.error
    bridge.available.assert_not_called()


def test_matching_069_bundle_connects_block_and_player_inventory_services(monkeypatch):
    loaded = module()
    bridge = SimpleNamespace(
        __version__="0.6.9",
        available=Mock(return_value=True),
        capabilities=Mock(return_value={
            "adapter": "bds-26.60-exact-nbt", "block_entity_nbt": True, "inventory": True,
        }),
        player_inventory_available=Mock(return_value=True),
        player_inventory_capabilities=Mock(return_value={
            "main": True, "armor": True, "offhand": True, "ender_chest": True,
            "storage_items": True,
        }),
        capture_player_inventory=Mock(return_value={"main": [], "revision": (1 << 64) - 1}),
    )
    monkeypatch.setattr(loaded.importlib, "import_module", lambda name: bridge)
    server = SimpleNamespace(plugin_manager=SimpleNamespace(
        get_plugin=lambda name: SimpleNamespace(
            _get_description=lambda: SimpleNamespace(version="0.6.9"),
        ),
    ))
    adapter = loaded.BlockDataAdapter()
    assert adapter.connect(server)
    assert adapter.capabilities["adapter"] == "bds-26.60-exact-nbt"
    assert adapter.player_inventory_available
    player = object()
    assert adapter.capture_player_inventory(server, player)["revision"] == (1 << 64) - 1
    bridge.capture_player_inventory.assert_called_once_with(server, player)
    bridge.available.return_value = False
    assert not adapter.check_connection(server)
    assert not adapter.player_inventory_available


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

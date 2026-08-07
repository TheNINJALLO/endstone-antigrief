from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def load_adapter():
    path = Path(__file__).parents[1] / "src/endstone_antigrief/blockdata_adapter.py"
    spec = spec_from_file_location("blockdata_adapter_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.BlockDataAdapter


def snapshot(revision, entries, *, direction="north", custom_name="Vault"):
    return {
        "location": {"dimension": "overworld", "x": 1, "y": 64, "z": 2},
        "type": "minecraft:chest",
        "runtime_id": 10,
        "revision": revision,
        "states": {"minecraft:cardinal_direction": direction},
        "block_entity": {
            "type": "Chest",
            "canonical_nbt": True,
            "is_container": True,
            "container_size": 27,
            "nbt": {
                "id": "Chest",
                "x": 1,
                "y": 64,
                "z": 2,
                "CustomName": custom_name,
                "Items": [],
                "_endstone_bds_build": "1.26.33.1",
                "_endstone_adapter": "bds-26.30-exact-nbt",
            },
            "inventory": entries,
        },
    }


def test_diff_inventory_and_two_phase_restore_preserve_item_nbt():
    adapter = load_adapter()
    sword = {
        "Name": "minecraft:diamond_sword",
        "Count": 1,
        "tag": {
            "display": {"Name": "§6Blade", "Lore": ["Line one"]},
            "ench": [{"id": 9, "lvl": 5}],
        },
    }
    before = snapshot(
        10,
        [
            {"slot": 0, "item": sword},
            {"slot": 1, "item": {"id": "minecraft:diamond", "count": 12}},
        ],
        direction="north",
    )
    after = snapshot(
        11,
        [
            {"slot": 1, "item": {"id": "minecraft:diamond", "count": 7}},
            {"slot": 2, "item": {"id": "minecraft:gold_ingot", "count": 3}},
        ],
        direction="south",
        custom_name="Temporary",
    )

    changes = adapter.diff_inventory(before, after)
    assert [change["action"] for change in changes] == [
        "Container Take",
        "Container Take",
        "Container Add",
    ]

    state_patch = adapter.build_state_restore_patch(after, before)
    assert state_patch["state_updates"] == {"minecraft:cardinal_direction": "north"}
    assert state_patch["nbt_updates"] == {}
    assert state_patch["inventory_updates"] == {}

    metadata_patch = adapter.build_metadata_restore_patch(after, before)
    assert metadata_patch["nbt_updates"] == {"CustomName": "Vault"}
    assert not any(key.startswith("_endstone_") for key in metadata_patch["nbt_updates"])
    assert not {"id", "x", "y", "z", "Items", "items"} & set(metadata_patch["nbt_updates"])

    inventory_patch = adapter.build_inventory_restore_patch(after, before)
    assert inventory_patch["nbt_updates"] == {}
    restored_sword = inventory_patch["inventory_updates"][0]
    assert restored_sword["id"] == "minecraft:diamond_sword"
    assert restored_sword["tag"]["display"]["Name"] == "§6Blade"
    assert restored_sword["tag"]["ench"][0]["lvl"] == 5
    assert inventory_patch["inventory_removals"] == [2]
    assert inventory_patch["state_updates"] == {}

    # The compatibility method may combine safe metadata and inventory, but it
    # must never include public block-state mutations or protected actor keys.
    compatibility_patch = adapter.build_restore_patch(after, before)
    assert compatibility_patch["nbt_updates"] == {"CustomName": "Vault"}
    assert compatibility_patch["inventory_updates"] == inventory_patch["inventory_updates"]
    assert not (
        compatibility_patch["state_updates"]
        and (
            compatibility_patch["nbt_updates"]
            or compatibility_patch["inventory_updates"]
        )
    )


def test_patch_has_changes_ignores_envelope_only():
    adapter = load_adapter()
    current = snapshot(1, [])
    empty = adapter._empty_patch(current)
    assert adapter.patch_has_changes(empty) is False
    empty["inventory_removals"] = [0]
    assert adapter.patch_has_changes(empty) is True


def test_bridge_loader_continues_when_top_level_candidate_package_is_missing(monkeypatch):
    import importlib.util

    path = Path(__file__).parents[1] / "src/endstone_antigrief/blockdata_adapter.py"
    spec = importlib.util.spec_from_file_location("blockdata_adapter_import_test", path)
    loaded = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(loaded)

    class Bridge:
        @staticmethod
        def available(_server):
            return True

        @staticmethod
        def capabilities(_server):
            return {"block_entity_nbt": True, "inventory": True}

    attempts = []

    def fake_import(name):
        attempts.append(name)
        if name == "endstone_blockdata._endstone_blockdata_live":
            error = ModuleNotFoundError("No module named 'endstone_blockdata'")
            error.name = "endstone_blockdata"
            raise error
        if name == "endstone_blockdata_inspector._endstone_blockdata_live":
            return Bridge
        raise AssertionError(name)

    monkeypatch.setattr(loaded.importlib, "import_module", fake_import)
    adapter = loaded.BlockDataAdapter()
    assert adapter.connect(object()) is True
    assert attempts[:2] == [
        "endstone_blockdata._endstone_blockdata_live",
        "endstone_blockdata_inspector._endstone_blockdata_live",
    ]


def test_bridge_loader_does_not_mask_internal_missing_dependency(monkeypatch):
    import importlib.util

    path = Path(__file__).parents[1] / "src/endstone_antigrief/blockdata_adapter.py"
    spec = importlib.util.spec_from_file_location("blockdata_adapter_internal_error_test", path)
    loaded = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(loaded)

    attempts = []

    def fake_import(name):
        attempts.append(name)
        error = ModuleNotFoundError("No module named 'libmissing_inside_bridge'")
        error.name = "libmissing_inside_bridge"
        raise error

    monkeypatch.setattr(loaded.importlib, "import_module", fake_import)
    adapter = loaded.BlockDataAdapter()
    assert adapter.connect(object()) is False
    assert attempts == ["endstone_blockdata._endstone_blockdata_live"]
    assert "libmissing_inside_bridge" in adapter.error

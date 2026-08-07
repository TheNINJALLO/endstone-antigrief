from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def load_adapter():
    path = Path(__file__).parents[1] / "src/endstone_antigrief/blockdata_adapter.py"
    spec = spec_from_file_location("blockdata_adapter_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.BlockDataAdapter


def snapshot(revision, entries):
    return {
        "location": {"dimension": "overworld", "x": 1, "y": 64, "z": 2},
        "type": "minecraft:chest",
        "runtime_id": 10,
        "revision": revision,
        "states": {"minecraft:cardinal_direction": "north"},
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
                "CustomName": "Vault",
                "Items": [],
            },
            "inventory": entries,
        },
    }


def test_diff_inventory_and_restore_patch_preserve_item_nbt():
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
    )
    after = snapshot(
        11,
        [
            {"slot": 1, "item": {"id": "minecraft:diamond", "count": 7}},
            {"slot": 2, "item": {"id": "minecraft:gold_ingot", "count": 3}},
        ],
    )

    changes = adapter.diff_inventory(before, after)
    assert [change["action"] for change in changes] == [
        "Container Take",
        "Container Take",
        "Container Add",
    ]

    patch = adapter.build_restore_patch(after, before)
    restored_sword = patch["inventory_updates"][0]
    assert restored_sword["id"] == "minecraft:diamond_sword"
    assert restored_sword["tag"]["display"]["Name"] == "§6Blade"
    assert restored_sword["tag"]["ench"][0]["lvl"] == 5
    assert patch["inventory_removals"] == [2]
    assert patch["nbt_updates"] == {"CustomName": "Vault"}

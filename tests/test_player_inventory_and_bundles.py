from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient


def load_adapter_module():
    path = Path(__file__).parents[1] / "src/endstone_antigrief/blockdata_adapter.py"
    spec = spec_from_file_location("blockdata_adapter_player_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_webui():
    path = Path(__file__).parents[1] / "src/endstone_antigrief/webui.py"
    spec = spec_from_file_location("webui_player_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def bundle_item():
    return {
        "Name": "minecraft:bundle",
        "Count": 1,
        "tag": {
            "storage_item_component_content": [
                {"Slot": 0, "Name": "minecraft:diamond", "Count": 4},
                {
                    "Slot": 1,
                    "Name": "minecraft:red_bundle",
                    "Count": 1,
                    "tag": {
                        "storage_item_component_content": [
                            {"Slot": 0, "Name": "minecraft:emerald", "Count": 2}
                        ]
                    },
                },
            ]
        },
    }


def player_snapshot():
    return {
        "player_name": "Steve",
        "xuid": "12345",
        "selected_hotbar_slot": 2,
        "main_size": 36,
        "armor_size": 4,
        "offhand_size": 1,
        "ender_chest_size": 27,
        "main": [{"slot": 2, "item": bundle_item(), "revision": 1}],
        "armor": [{"slot": 0, "item": {"Name": "minecraft:diamond_helmet", "Count": 1}}],
        "offhand": [],
        "ender_chest": [
            {
                "slot": 5,
                "item": {
                    "Name": "ninjos:backpack",
                    "Count": 1,
                    "tag": {
                        "storage_item_component_content": [
                            {"Slot": 4, "Name": "minecraft:gold_ingot", "Count": 8}
                        ]
                    },
                },
            }
        ],
        "revision": (1 << 64) - 3,
    }


def test_adapter_understands_player_sections_and_nested_storage_items():
    module = load_adapter_module()
    adapter = module.BlockDataAdapter()
    snapshot = player_snapshot()

    assert adapter.player_inventory_capacity(snapshot, "main") == 36
    assert adapter.player_inventory_map(snapshot, "main")[2]["Name"] == "minecraft:bundle"
    assert adapter.storage_item_contents(bundle_item())[0]["Name"] == "minecraft:diamond"
    assert adapter.is_storage_item(bundle_item()) is True

    summary = adapter.player_inventory_summary(snapshot)
    assert summary["sections"]["ender_chest"]["occupied_slots"] == 1
    assert summary["total_item_count"] == 3
    assert summary["storage_item_count"] == 3  # outer bundle, nested bundle, custom backpack


def test_live_player_inventory_bridge_wrapper():
    module = load_adapter_module()

    class Bridge:
        def capture_player_inventory(self, server, player):
            return player_snapshot()

        def apply_player_inventory(self, server, player, patch, policy):
            return {"ok": True, "status": "applied", "policy": policy, "patch": patch}

    adapter = module.BlockDataAdapter()
    adapter.bridge = Bridge()
    adapter.player_inventory_capabilities = {
        "main": True,
        "armor": True,
        "offhand": True,
        "ender_chest": True,
        "storage_items": True,
    }
    captured = adapter.capture_player_inventory(object(), object())
    assert captured["player_name"] == "Steve"
    result = adapter.apply_player_inventory(object(), object(), {"main_removals": [2]})
    assert result["ok"] is True


def test_player_inventory_webui_endpoints_and_bundle_view(tmp_path):
    webui = load_webui()
    db_path = tmp_path / "agdata.db"
    snapshot = player_snapshot()
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE player_inventory_snapshots(
                player_key TEXT PRIMARY KEY,snapshot_id TEXT UNIQUE,player_name TEXT,xuid TEXT,
                captured_at TEXT,online INTEGER,revision INTEGER,revision_text TEXT,
                selected_hotbar_slot INTEGER,main_size INTEGER,armor_size INTEGER,
                offhand_size INTEGER,ender_chest_size INTEGER,occupied_main INTEGER,
                occupied_armor INTEGER,occupied_offhand INTEGER,occupied_ender_chest INTEGER,
                item_count INTEGER,storage_item_count INTEGER,snapshot_json TEXT
            );
            CREATE TABLE interactions(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,action TEXT,x INTEGER,y INTEGER,z INTEGER,type TEXT,world TEXT,time TEXT,blockdata TEXT);
            CREATE TABLE bans(player_name TEXT, reason TEXT, banned_at TEXT);
            """
        )
        db.execute(
            "INSERT INTO player_inventory_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "xuid:12345", "player-snap", "Steve", "12345",
                "2026-07-29T01:00:00-04:00", 1, None, str(snapshot["revision"]),
                2, 36, 4, 1, 27, 1, 1, 0, 1, 3, 3, json.dumps(snapshot),
            ),
        )
        db.commit()

    webui.DB_FILE = str(db_path)
    webui.get_web_config = lambda: {"secret": "secret", "max_results": 10000}
    client = TestClient(webui.create_app())

    listing = client.get("/api/player-inventories?secret=secret")
    assert listing.status_code == 200
    assert listing.json()["players"][0]["ender_chest"]["occupied"] == 1
    assert listing.json()["players"][0]["storage_item_count"] == 3

    detail = client.get("/api/player-inventories/xuid%3A12345?secret=secret")
    assert detail.status_code == 200
    contents = detail.json()["snapshot"]["main"][0]["item"]["tag"]["storage_item_component_content"]
    assert contents[1]["Name"] == "minecraft:red_bundle"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Player Inventories & Ender Chests" in dashboard.text
    assert "storage_item_component_content" in dashboard.text
    assert "Bundle / Storage Contents" in dashboard.text
    assert "function renderPlayerInventorySection" in dashboard.text
    assert "data-player-key" in dashboard.text


def test_empty_bundle_placeholders_are_filtered_from_human_views():
    module = load_adapter_module()
    adapter = module.BlockDataAdapter()
    bundle = {
        "Name": "minecraft:bundle",
        "Count": 1,
        "tag": {
            "storage_item_component_content": [
                {"Slot": 0, "Name": "minecraft:diamond", "Count": 2},
                {"Slot": 1, "empty": True},
                {"Slot": 2, "Name": "minecraft:air", "Count": 0},
                {"slot": 3, "item": {"Name": "minecraft:emerald", "Count": 1}},
                {"Slot": 4},
            ]
        },
    }

    contents = adapter.storage_item_contents(bundle)
    assert [item["Name"] for item in contents] == [
        "minecraft:diamond",
        "minecraft:emerald",
    ]

    webui = load_webui()
    groups = webui._item_nested_groups(bundle)
    assert len(groups) == 1
    assert [item["Name"] for item in groups[0][1]] == [
        "minecraft:diamond",
        "minecraft:emerald",
    ]


def test_dashboard_filters_empty_bundle_slots_before_rendering(tmp_path):
    webui = load_webui()
    db_path = tmp_path / "agdata.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE interactions(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,action TEXT,x INTEGER,y INTEGER,z INTEGER,type TEXT,world TEXT,time TEXT,blockdata TEXT);
            CREATE TABLE bans(player_name TEXT, reason TEXT, banned_at TEXT);
            """
        )
    webui.DB_FILE = str(db_path)
    webui.get_web_config = lambda: {"secret": "secret", "max_results": 10000}
    dashboard = TestClient(webui.create_app()).get("/")
    assert dashboard.status_code == 200
    assert "function isEmptyItemNbt" in dashboard.text
    assert "occupiedContainedItems(storage)" in dashboard.text
    assert "occupied)</summary>" in dashboard.text


def test_public_inventory_fallback_preserves_readable_items_and_metadata():
    module = load_adapter_module()

    class FakeType:
        def __init__(self, identifier):
            self.id = identifier

    class FakeEnchant:
        id = "minecraft:sharpness"

    class FakeMeta:
        has_display_name = True
        display_name = "Fallback Blade"
        has_lore = True
        lore = ["Recovered without raw NBT"]
        enchants = {FakeEnchant(): 5}
        has_damage = True
        damage = 7
        is_unbreakable = False

    class FakeStack:
        def __init__(self, identifier, amount=1, data=0):
            self.type = FakeType(identifier)
            self.amount = amount
            self.data = data
            self.item_meta = FakeMeta()

    class FakeInventory:
        def __init__(self, contents):
            self.contents = contents
            self.size = len(contents)
            self.helmet = None
            self.chestplate = None
            self.leggings = None
            self.boots = None
            self.item_in_off_hand = None
            self.held_item_slot = 2

    class FakePlayer:
        name = "GothiccaRose"
        xuid = "998877"
        inventory = FakeInventory([None, FakeStack("minecraft:diamond_sword"), None])
        ender_chest = FakeInventory([FakeStack("minecraft:emerald", 4)])

    snapshot = module.BlockDataAdapter.public_player_inventory_snapshot(
        FakePlayer(), warning="invalid UTF-8 test"
    )
    assert snapshot["capture_mode"] == "public_fallback"
    assert snapshot["capture_warning"] == "invalid UTF-8 test"
    assert snapshot["main"][0]["slot"] == 1
    item = snapshot["main"][0]["item"]
    assert item["Name"] == "minecraft:diamond_sword"
    assert item["tag"]["display"]["Name"] == "Fallback Blade"
    assert item["tag"]["display"]["Lore"] == ["Recovered without raw NBT"]
    assert item["tag"]["ench"][0] == {"id": "minecraft:sharpness", "lvl": 5}
    assert snapshot["ender_chest"][0]["item"]["Count"] == 4
    assert isinstance(snapshot["revision"], int)


def test_dashboard_marks_degraded_inventory_capture(tmp_path):
    webui = load_webui()
    db_path = tmp_path / "agdata.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE interactions(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,action TEXT,x INTEGER,y INTEGER,z INTEGER,type TEXT,world TEXT,time TEXT,blockdata TEXT);
            CREATE TABLE bans(player_name TEXT, reason TEXT, banned_at TEXT);
            """
        )
    webui.DB_FILE = str(db_path)
    webui.get_web_config = lambda: {"secret": "secret", "max_results": 10000}
    dashboard = TestClient(webui.create_app()).get("/")
    assert dashboard.status_code == 200
    assert "Degraded readable capture" in dashboard.text
    assert "snapshot.capture_mode === 'public_fallback'" in dashboard.text

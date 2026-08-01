"""Native Endstone BlockData bridge integration for AntiGrief.

The BlockData native bridge must only be called from the Endstone server thread.
This module keeps bridge-specific payload handling out of the event/database code.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json
from typing import Any


class BlockDataUnavailable(RuntimeError):
    """Raised when the matching BlockData native bridge is unavailable."""


class BlockDataAdapter:
    """Thin compatibility layer over the BlockData v2 native Python bridge."""

    PROVIDER = "endstone-blockdata-api"
    SCHEMA_VERSION = 2
    EXPECTED_VERSION = "0.4.8"

    _BRIDGE_MODULES = (
        "endstone_blockdata._endstone_blockdata_live",
        "endstone_blockdata_inspector._endstone_blockdata_live",
        "_endstone_blockdata_live",
    )

    @staticmethod
    def _target_module_missing(error: ModuleNotFoundError, module_name: str) -> bool:
        """Return True when the requested module/package itself is absent.

        Importing ``pkg.extension`` can report either ``pkg.extension`` or just
        ``pkg`` in ``ModuleNotFoundError.name``. Both mean the candidate layout
        is absent and the loader should continue to the next supported layout.
        A different missing dependency means the candidate exists but failed
        internally, so that error must be preserved instead of masked.
        """
        missing = str(getattr(error, "name", "") or "")
        if not missing:
            return False
        parts = module_name.split(".")
        candidates = {".".join(parts[:index]) for index in range(1, len(parts) + 1)}
        return missing in candidates

    def __init__(self) -> None:
        self.bridge: Any | None = None
        self.capabilities: dict[str, Any] = {}
        self.player_inventory_capabilities: dict[str, Any] = {}
        self.player_inventory_error = "player inventory service has not been connected"
        self.error = "BlockData bridge has not been connected"

    @property
    def available(self) -> bool:
        return self.bridge is not None

    def connect(self, server: Any) -> bool:
        """Load the bundled bridge and connect it to the native BlockData service."""
        last_error: Exception | None = None
        bridge = None
        for module_name in self._BRIDGE_MODULES:
            try:
                bridge = importlib.import_module(module_name)
                break
            except ModuleNotFoundError as error:
                last_error = error
                if self._target_module_missing(error, module_name):
                    continue
                # The candidate module exists, but one of its own imports is
                # missing. Stop here so the real dependency failure is visible.
                break
            except Exception as error:  # pragma: no cover - native import failures vary by platform
                last_error = error
                break

        if bridge is None:
            self.error = (
                "matching BlockData inspector wheel/native bridge is not installed"
                + (f": {last_error}" if last_error else "")
            )
            return False

        try:
            if not bridge.available(server):
                self.error = "endstone:blockdata:v2 native service is not registered"
                return False
            self.capabilities = dict(bridge.capabilities(server))
        except Exception as error:
            self.error = f"failed to connect to native BlockData service: {error}"
            return False

        self.bridge = bridge
        self.error = ""
        self.player_inventory_capabilities = {}
        self.player_inventory_error = "player inventory service is not registered"
        if all(
            hasattr(bridge, name)
            for name in (
                "player_inventory_available",
                "player_inventory_capabilities",
                "capture_player_inventory",
            )
        ):
            try:
                if bridge.player_inventory_available(server):
                    self.player_inventory_capabilities = dict(
                        bridge.player_inventory_capabilities(server)
                    )
                    self.player_inventory_error = ""
            except Exception as error:
                self.player_inventory_error = (
                    f"failed to connect to live player inventory service: {error}"
                )
        else:
            self.player_inventory_error = (
                "installed BlockData bridge predates player inventory support; "
                f"install BlockData {self.EXPECTED_VERSION} or newer"
            )
        return True

    def require(self, *capabilities: str) -> None:
        if self.bridge is None:
            raise BlockDataUnavailable(self.error)
        missing = [name for name in capabilities if not self.capabilities.get(name, False)]
        if missing:
            raise BlockDataUnavailable(
                "native BlockData adapter is missing capabilities: " + ", ".join(missing)
            )

    @staticmethod
    def json_safe(value: Any) -> Any:
        """Return a detached, JSON-safe representation of a native bridge payload."""
        return json.loads(json.dumps(value, default=str, ensure_ascii=False))

    def capture(self, server: Any, dimension: str, x: int, y: int, z: int) -> dict[str, Any] | None:
        self.require("block_entity_nbt")
        snapshot = self.bridge.capture(server, str(dimension), int(x), int(y), int(z))
        if snapshot is None:
            return None
        return self.json_safe(dict(snapshot))

    def apply(
        self,
        server: Any,
        patch: dict[str, Any],
        policy: str = "fail_if_changed",
    ) -> dict[str, Any]:
        self.require("inventory")
        return self.json_safe(dict(self.bridge.apply(server, patch, policy)))

    @property
    def player_inventory_available(self) -> bool:
        return bool(self.bridge is not None and self.player_inventory_capabilities)

    def require_player_inventory(self, *capabilities: str) -> None:
        if not self.player_inventory_available:
            raise BlockDataUnavailable(self.player_inventory_error)
        missing = [
            name for name in capabilities
            if not self.player_inventory_capabilities.get(name, False)
        ]
        if missing:
            raise BlockDataUnavailable(
                "native player inventory adapter is missing capabilities: "
                + ", ".join(missing)
            )

    def capture_player_inventory(self, server: Any, player: Any) -> dict[str, Any] | None:
        """Capture one online player's main, armor, offhand, and Ender Chest sections."""
        self.require_player_inventory("main", "armor", "offhand", "ender_chest")
        snapshot = self.bridge.capture_player_inventory(server, player)
        if snapshot is None:
            return None
        return self.json_safe(dict(snapshot))

    @staticmethod
    def _safe_text(value: Any, default: str = "") -> str:
        try:
            return str(value)
        except (UnicodeDecodeError, UnicodeError, ValueError, TypeError):
            return default

    @classmethod
    def _public_item_identifier(cls, stack: Any) -> str:
        try:
            item_type = getattr(stack, "type", None)
            identifier = getattr(item_type, "id", item_type)
            rendered = cls._safe_text(identifier).strip()
            return rendered or "minecraft:unknown"
        except Exception:
            return "minecraft:unknown"

    @classmethod
    def _public_item_to_nbt(cls, stack: Any) -> dict[str, Any] | None:
        """Build a readable canonical-shaped item from Endstone's public API.

        This is intentionally a degraded fallback for the rare case where the
        exact BlockData bridge encounters malformed non-UTF-8 text in a native
        StringTag. It preserves type, count, data, readable display metadata,
        lore, and enchantments without allowing one corrupt field to drop the
        player's entire inventory snapshot.
        """
        if stack is None:
            return None
        try:
            amount = int(getattr(stack, "amount", 1))
        except Exception:
            amount = 1
        if amount <= 0:
            return None
        try:
            data = int(getattr(stack, "data", 0))
        except Exception:
            data = 0

        item: dict[str, Any] = {
            "Name": cls._public_item_identifier(stack),
            "Count": amount,
            "Damage": data,
            "_antigrief_public_fallback": True,
        }
        tag: dict[str, Any] = {}
        display: dict[str, Any] = {}
        try:
            meta = getattr(stack, "item_meta", None)
        except Exception:
            meta = None
        if meta is not None:
            try:
                if bool(getattr(meta, "has_display_name", False)):
                    name = cls._safe_text(getattr(meta, "display_name", "")).strip()
                    if name:
                        display["Name"] = name
            except Exception:
                pass
            try:
                if bool(getattr(meta, "has_lore", False)):
                    lore = []
                    for value in list(getattr(meta, "lore", []) or []):
                        rendered = cls._safe_text(value).strip()
                        if rendered:
                            lore.append(rendered)
                    if lore:
                        display["Lore"] = lore
            except Exception:
                pass
            enchantments = []
            try:
                for enchantment, level in dict(getattr(meta, "enchants", {}) or {}).items():
                    enchantment_id = cls._safe_text(
                        getattr(enchantment, "id", enchantment), "unknown"
                    )
                    enchantments.append({"id": enchantment_id, "lvl": int(level)})
            except Exception:
                enchantments = []
            if enchantments:
                tag["ench"] = enchantments
            try:
                if bool(getattr(meta, "has_damage", False)):
                    item["Damage"] = int(getattr(meta, "damage", data))
            except Exception:
                pass
            try:
                if bool(getattr(meta, "is_unbreakable", False)):
                    tag["Unbreakable"] = 1
            except Exception:
                pass
        if display:
            tag["display"] = display
        if tag:
            item["tag"] = tag
        return item

    @classmethod
    def _public_inventory_entries(cls, inventory: Any) -> tuple[int, list[dict[str, Any]]]:
        try:
            size = max(0, int(getattr(inventory, "size", 0)))
        except Exception:
            size = 0
        try:
            contents = list(getattr(inventory, "contents", []) or [])
        except Exception:
            contents = []
        if size <= 0:
            size = len(contents)
        entries: list[dict[str, Any]] = []
        for slot, stack in enumerate(contents[:size]):
            item = cls._public_item_to_nbt(stack)
            if item is not None:
                entries.append({"slot": slot, "item": item, "revision": 0})
        return size, entries

    @classmethod
    def public_player_inventory_snapshot(
        cls,
        player: Any,
        *,
        warning: str = "native canonical NBT capture failed",
    ) -> dict[str, Any]:
        """Capture a readable fallback snapshot through Endstone's public API."""
        inventory = getattr(player, "inventory")
        ender_chest = getattr(player, "ender_chest")
        main_size, main = cls._public_inventory_entries(inventory)
        ender_size, ender = cls._public_inventory_entries(ender_chest)

        armor = []
        armor_names = ("helmet", "chestplate", "leggings", "boots")
        for slot, attribute in enumerate(armor_names):
            try:
                stack = getattr(inventory, attribute, None)
            except Exception:
                stack = None
            item = cls._public_item_to_nbt(stack)
            if item is not None:
                armor.append({"slot": slot, "item": item, "revision": 0})

        try:
            offhand_stack = getattr(inventory, "item_in_off_hand", None)
        except Exception:
            offhand_stack = None
        offhand_item = cls._public_item_to_nbt(offhand_stack)
        offhand = (
            [{"slot": 0, "item": offhand_item, "revision": 0}]
            if offhand_item is not None else []
        )
        try:
            selected = int(getattr(inventory, "held_item_slot", 0))
        except Exception:
            selected = 0

        snapshot: dict[str, Any] = {
            "player_name": cls._safe_text(getattr(player, "name", "Unknown"), "Unknown"),
            "xuid": cls._safe_text(getattr(player, "xuid", "")),
            "selected_hotbar_slot": selected,
            "main_size": main_size,
            "armor_size": 4,
            "offhand_size": 1,
            "ender_chest_size": ender_size,
            "main": main,
            "armor": armor,
            "offhand": offhand,
            "ender_chest": ender,
            "capture_mode": "public_fallback",
            "capture_warning": warning,
            "canonical_nbt": False,
        }
        revision_payload = json.dumps(
            snapshot, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        snapshot["revision"] = int.from_bytes(
            hashlib.sha256(revision_payload).digest()[:8], "big", signed=False
        )
        return snapshot

    def apply_player_inventory(
        self,
        server: Any,
        player: Any,
        patch: dict[str, Any],
        policy: str = "fail_if_changed",
    ) -> dict[str, Any]:
        """Apply a validated optimistic patch to a live online player inventory."""
        self.require_player_inventory("main", "armor", "offhand", "ender_chest")
        if not hasattr(self.bridge, "apply_player_inventory"):
            raise BlockDataUnavailable(
                "installed BlockData bridge does not expose player inventory writes"
            )
        return self.json_safe(
            dict(self.bridge.apply_player_inventory(server, player, patch, policy))
        )

    PLAYER_INVENTORY_SECTIONS = ("main", "armor", "offhand", "ender_chest")
    STORAGE_ITEM_CONTENTS_KEY = "storage_item_component_content"

    @classmethod
    def player_inventory_entries(
        cls,
        snapshot: dict[str, Any] | None,
        section: str,
    ) -> list[dict[str, Any]]:
        if section not in cls.PLAYER_INVENTORY_SECTIONS or not isinstance(snapshot, dict):
            return []
        entries = snapshot.get(section)
        if not isinstance(entries, (list, tuple)):
            return []
        result = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item")
            if cls.is_empty_item(item):
                continue
            result.append(deepcopy(dict(entry)))
        return result

    @classmethod
    def player_inventory_map(
        cls,
        snapshot: dict[str, Any] | None,
        section: str,
    ) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for entry in cls.player_inventory_entries(snapshot, section):
            try:
                slot = int(entry.get("slot"))
            except (TypeError, ValueError):
                continue
            item = entry.get("item")
            if isinstance(item, dict):
                result[slot] = deepcopy(item)
        return result

    @classmethod
    def player_inventory_capacity(
        cls,
        snapshot: dict[str, Any] | None,
        section: str,
    ) -> int:
        if section not in cls.PLAYER_INVENTORY_SECTIONS or not isinstance(snapshot, dict):
            return 0
        try:
            return max(0, int(snapshot.get(f"{section}_size", 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _item_tag(item: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        tag = item.get("tag", item.get("user_data"))
        return deepcopy(tag) if isinstance(tag, dict) else {}

    @classmethod
    def storage_item_contents(cls, item: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Return detached bundle/custom storage-item contents in slot order."""
        tag = cls._item_tag(item)
        contents = tag.get(cls.STORAGE_ITEM_CONTENTS_KEY)
        if not isinstance(contents, list):
            return []
        entries = []
        for entry in contents:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
            if cls.is_empty_item(item):
                continue
            entries.append(deepcopy(item))
        return sorted(
            entries,
            key=lambda entry: int(entry.get("Slot", entry.get("slot", 0)))
            if str(entry.get("Slot", entry.get("slot", 0))).lstrip("-").isdigit()
            else 0,
        )

    @classmethod
    def shulker_item_contents(cls, item: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Return serialized shulker/container-item contents when present."""
        tag = cls._item_tag(item)
        block_entity = tag.get("BlockEntityTag", tag.get("block_entity_tag", {}))
        if not isinstance(block_entity, dict):
            block_entity = {}
        contents = (
            tag.get("Items") or tag.get("items")
            or block_entity.get("Items") or block_entity.get("items")
        )
        if not isinstance(contents, list):
            return []
        occupied = []
        for entry in contents:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
            if cls.is_empty_item(item):
                continue
            occupied.append(deepcopy(item))
        return occupied

    @classmethod
    def is_storage_item(cls, item: dict[str, Any] | None) -> bool:
        identifier = cls.item_id(item)
        name = identifier.removeprefix("minecraft:")
        return bool(
            name == "bundle"
            or name.endswith("_bundle")
            or cls.STORAGE_ITEM_CONTENTS_KEY in cls._item_tag(item)
        )

    @classmethod
    def count_storage_items(cls, snapshot: dict[str, Any] | None) -> int:
        """Count bundle/storage stacks recursively across a player snapshot."""
        def count_item(item: dict[str, Any], depth: int = 0) -> int:
            if depth > 8:
                return 0
            total = 1 if cls.is_storage_item(item) else 0
            for nested in cls.storage_item_contents(item):
                total += count_item(nested, depth + 1)
            return total

        total = 0
        if isinstance(snapshot, dict) and any(
            section in snapshot for section in cls.PLAYER_INVENTORY_SECTIONS
        ):
            for section in cls.PLAYER_INVENTORY_SECTIONS:
                for entry in cls.player_inventory_entries(snapshot, section):
                    item = entry.get("item")
                    if isinstance(item, dict):
                        total += count_item(item)
        else:
            for item in cls.inventory_map(snapshot).values():
                total += count_item(item)
        return total

    @classmethod
    def player_inventory_summary(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        sections: dict[str, Any] = {}
        total_items = 0
        for section in cls.PLAYER_INVENTORY_SECTIONS:
            entries = cls.player_inventory_entries(snapshot, section)
            item_count = sum(cls.item_count(entry.get("item")) for entry in entries)
            total_items += item_count
            sections[section] = {
                "capacity": cls.player_inventory_capacity(snapshot, section),
                "occupied_slots": len(entries),
                "item_count": item_count,
            }
        return {
            "player_name": str(snapshot.get("player_name") or "Unknown"),
            "xuid": str(snapshot.get("xuid") or ""),
            "revision": snapshot.get("revision"),
            "selected_hotbar_slot": snapshot.get("selected_hotbar_slot", 0),
            "sections": sections,
            "total_item_count": total_items,
            "storage_item_count": cls.count_storage_items(snapshot),
        }

    @staticmethod
    def block_entity(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(snapshot, dict):
            return None
        entity = snapshot.get("block_entity")
        return dict(entity) if isinstance(entity, dict) else None

    @classmethod
    def inventory_entries(cls, snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
        entity = cls.block_entity(snapshot)
        if not entity:
            return []
        entries = entity.get("inventory")
        if not isinstance(entries, (list, tuple)):
            return []
        return [dict(entry) for entry in entries if isinstance(entry, dict)]

    @staticmethod
    def is_empty_item(item: Any) -> bool:
        if item is None or item == {}:
            return True
        if not isinstance(item, dict):
            return False
        if item.get("empty") is True:
            return True
        item_id = item.get("id", item.get("name", item.get("Name")))
        if str(item_id or "").casefold() in {"", "air", "minecraft:air"}:
            return True
        count = item.get("count", item.get("Count", 1))
        try:
            return int(count) <= 0
        except (TypeError, ValueError):
            return False

    @classmethod
    def inventory_map(cls, snapshot: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for entry in cls.inventory_entries(snapshot):
            if "slot" not in entry:
                continue
            item = entry.get("item")
            if cls.is_empty_item(item) or not isinstance(item, dict):
                continue
            try:
                result[int(entry["slot"])] = deepcopy(item)
            except (TypeError, ValueError):
                continue
        return result

    @classmethod
    def container_capacity(cls, snapshot: dict[str, Any] | None) -> int:
        entity = cls.block_entity(snapshot)
        if not entity:
            return 0
        try:
            return max(0, int(entity.get("container_size", 0)))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def is_container(cls, snapshot: dict[str, Any] | None) -> bool:
        entity = cls.block_entity(snapshot)
        if not entity:
            return False
        if entity.get("is_container") is True:
            return True
        return cls.container_capacity(snapshot) > 0 or bool(cls.inventory_entries(snapshot))

    @staticmethod
    def item_id(item: dict[str, Any] | None) -> str:
        if not item:
            return "minecraft:air"
        return str(item.get("id", item.get("name", item.get("Name", "minecraft:air"))))

    @staticmethod
    def item_count(item: dict[str, Any] | None) -> int:
        if not item:
            return 0
        try:
            return int(item.get("count", item.get("Count", 1)))
        except (TypeError, ValueError):
            return 1

    @classmethod
    def normalize_item_for_patch(cls, item: dict[str, Any]) -> dict[str, Any]:
        """Preserve canonical fields while adding the aliases the bridge accepts."""
        payload = deepcopy(item)
        payload.setdefault("id", cls.item_id(item))
        payload.setdefault("count", cls.item_count(item))
        return payload

    @classmethod
    def diff_inventory(
        cls,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        left = cls.inventory_map(before)
        right = cls.inventory_map(after)
        changes: list[dict[str, Any]] = []
        for slot in sorted(set(left) | set(right)):
            old = left.get(slot)
            new = right.get(slot)
            if old == new:
                continue

            old_empty = cls.is_empty_item(old)
            new_empty = cls.is_empty_item(new)
            if old_empty and not new_empty:
                action = "Container Add"
                amount = cls.item_count(new)
            elif not old_empty and new_empty:
                action = "Container Take"
                amount = cls.item_count(old)
            elif cls.item_id(old) == cls.item_id(new):
                delta = cls.item_count(new) - cls.item_count(old)
                if delta > 0:
                    action = "Container Add"
                    amount = delta
                elif delta < 0:
                    action = "Container Take"
                    amount = abs(delta)
                else:
                    action = "Container Change"
                    amount = max(cls.item_count(old), cls.item_count(new))
            else:
                action = "Container Change"
                amount = max(cls.item_count(old), cls.item_count(new))

            changes.append(
                {
                    "slot": slot,
                    "action": action,
                    "amount": amount,
                    "before": deepcopy(old),
                    "after": deepcopy(new),
                }
            )
        return changes

    @classmethod
    def snapshot_summary(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        entity = cls.block_entity(snapshot) or {}
        inventory = cls.inventory_map(snapshot)
        return {
            "type": snapshot.get("type"),
            "revision": snapshot.get("revision"),
            "states": deepcopy(snapshot.get("states") or {}),
            "block_entity_type": entity.get("type"),
            "block_entity_status": snapshot.get("block_entity_status"),
            "canonical_nbt": bool(entity.get("canonical_nbt", False)),
            "is_container": cls.is_container(snapshot),
            "container_size": cls.container_capacity(snapshot),
            "occupied_slots": len(inventory),
            "item_count": sum(cls.item_count(item) for item in inventory.values()),
        }

    @staticmethod
    def _empty_patch(current: dict[str, Any]) -> dict[str, Any]:
        """Return the native bridge patch envelope for one live block revision."""
        return {
            "location": deepcopy(current["location"]),
            "expected_revision": current.get("revision"),
            "replacement_type": None,
            "state_updates": {},
            "state_removals": [],
            "nbt_updates": {},
            "nbt_removals": [],
            "inventory_updates": {},
            "inventory_removals": [],
        }

    @classmethod
    def build_state_restore_patch(
        cls,
        current: dict[str, Any],
        saved: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the block-state-only phase of a restore.

        BlockData intentionally rejects a patch that mixes block-state writes with
        block-entity NBT or inventory writes. Keeping this phase isolated avoids
        the native error: "mixed block-state and block-entity patches are not atomic".
        """
        patch = cls._empty_patch(current)
        current_states = dict(current.get("states") or {})
        saved_states = dict(saved.get("states") or {})
        patch["state_updates"] = {
            key: deepcopy(value)
            for key, value in saved_states.items()
            if current_states.get(key) != value
        }
        return patch

    @staticmethod
    def _is_read_only_actor_nbt_key(key: Any) -> bool:
        """Return whether the exact native adapter rejects this actor NBT key.

        The BDS 26.30 adapter treats actor identity (id/coordinates) and every
        ``_endstone_*`` adapter marker as read-only. ``Items`` is managed by the
        per-slot inventory patch API and is therefore excluded from metadata.
        """
        text = str(key)
        return text in {"id", "x", "y", "z", "Items", "items"} or text.startswith("_endstone_")

    @classmethod
    def writable_actor_metadata(cls, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        entity = cls.block_entity(snapshot) or {}
        nbt = deepcopy(entity.get("nbt") or {})
        return {
            str(key): value
            for key, value in nbt.items()
            if not cls._is_read_only_actor_nbt_key(key)
        }

    @classmethod
    def build_metadata_restore_patch(
        cls,
        current: dict[str, Any],
        saved: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a writable actor-metadata-only restore patch.

        Arbitrary NBT removals are intentionally avoided because the exact
        adapter only supports removing ``CustomName`` and ``Items``. Inventory
        is restored in a separate phase, so metadata failure can never block
        the higher-value item recovery transaction.
        """
        patch = cls._empty_patch(current)
        current_nbt = cls.writable_actor_metadata(current)
        saved_nbt = cls.writable_actor_metadata(saved)
        patch["nbt_updates"] = {
            key: deepcopy(value)
            for key, value in saved_nbt.items()
            if current_nbt.get(key) != value
        }
        if "CustomName" in current_nbt and "CustomName" not in saved_nbt:
            patch["nbt_removals"] = ["CustomName"]
        elif "custom_name" in current_nbt and "custom_name" not in saved_nbt:
            patch["nbt_removals"] = ["custom_name"]
        return patch

    @classmethod
    def build_inventory_restore_patch(
        cls,
        current: dict[str, Any],
        saved: dict[str, Any],
    ) -> dict[str, Any]:
        """Build an inventory-only exact slot restore patch."""
        patch = cls._empty_patch(current)
        current_inventory = cls.inventory_map(current)
        saved_inventory = cls.inventory_map(saved)
        patch["inventory_updates"] = {
            slot: cls.normalize_item_for_patch(item)
            for slot, item in saved_inventory.items()
            if current_inventory.get(slot) != item
        }
        patch["inventory_removals"] = sorted(
            slot for slot in set(current_inventory) - set(saved_inventory)
        )
        return patch

    @classmethod
    def build_block_entity_restore_patch(
        cls,
        current: dict[str, Any],
        saved: dict[str, Any],
    ) -> dict[str, Any]:
        """Compatibility combined patch containing only adapter-safe fields.

        The plugin itself applies metadata and inventory separately so a bad or
        unsupported metadata field can never prevent slot restoration.
        """
        metadata = cls.build_metadata_restore_patch(current, saved)
        inventory = cls.build_inventory_restore_patch(current, saved)
        metadata["inventory_updates"] = inventory["inventory_updates"]
        metadata["inventory_removals"] = inventory["inventory_removals"]
        return metadata

    @classmethod
    def build_restore_patch(
        cls,
        current: dict[str, Any],
        saved: dict[str, Any],
    ) -> dict[str, Any]:
        """Compatibility alias for the block-entity phase.

        Callers that also need saved block states must apply
        :meth:`build_state_restore_patch` first, recapture, and then apply this patch.
        """
        return cls.build_block_entity_restore_patch(current, saved)

    @staticmethod
    def patch_has_changes(patch: dict[str, Any]) -> bool:
        """Return whether a patch contains any mutation rather than only its envelope."""
        return any(
            bool(patch.get(key))
            for key in (
                "state_updates",
                "state_removals",
                "nbt_updates",
                "nbt_removals",
                "inventory_updates",
                "inventory_removals",
            )
        )

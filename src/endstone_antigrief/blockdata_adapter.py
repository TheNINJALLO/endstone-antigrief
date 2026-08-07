"""Native Endstone BlockData bridge integration for AntiGrief.

The BlockData native bridge must only be called from the Endstone server thread.
This module keeps bridge-specific payload handling out of the event/database code.
"""

from __future__ import annotations

from copy import deepcopy
import importlib
import json
from typing import Any


class BlockDataUnavailable(RuntimeError):
    """Raised when the matching BlockData native bridge is unavailable."""


class BlockDataAdapter:
    """Thin compatibility layer over the BlockData v2 native Python bridge."""

    PROVIDER = "endstone-blockdata-api"
    SCHEMA_VERSION = 2
    EXPECTED_VERSION = "0.4.6"

    _BRIDGE_MODULES = (
        "endstone_blockdata_inspector._endstone_blockdata_live",
        "_endstone_blockdata_live",
    )

    def __init__(self) -> None:
        self.bridge: Any | None = None
        self.capabilities: dict[str, Any] = {}
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
                if error.name != module_name:
                    last_error = error
                    break
                last_error = error
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

    @classmethod
    def build_restore_patch(
        cls,
        current: dict[str, Any],
        saved: dict[str, Any],
    ) -> dict[str, Any]:
        """Build an exact inventory/NBT restore patch from a stored snapshot."""
        current_inventory = cls.inventory_map(current)
        saved_inventory = cls.inventory_map(saved)
        saved_entity = cls.block_entity(saved) or {}
        saved_nbt = deepcopy(saved_entity.get("nbt") or {})

        # Coordinates, actor id and Items are managed by the live block actor/inventory API.
        for key in ("x", "y", "z", "id", "Items", "items"):
            saved_nbt.pop(key, None)

        return {
            "location": deepcopy(current["location"]),
            "expected_revision": current.get("revision"),
            "replacement_type": None,
            "state_updates": deepcopy(saved.get("states") or {}),
            "state_removals": [],
            "nbt_updates": saved_nbt,
            "nbt_removals": [],
            "inventory_updates": {
                slot: cls.normalize_item_for_patch(item)
                for slot, item in saved_inventory.items()
            },
            "inventory_removals": sorted(set(current_inventory) - set(saved_inventory)),
        }

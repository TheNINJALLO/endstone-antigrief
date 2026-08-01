# Containers, Player Inventories, and Bundles

## Container snapshots

BlockData records the block type, states, revision, actor NBT, container size, occupied slots, canonical item NBT, and raw SNBT when supported.

## Player snapshots

Online captures include Main inventory and hotbar, Armor, Offhand, and Ender Chest. Offline records are cached snapshots, not edits to offline player files.

## Bundles and storage items

Vanilla bundles and custom `minecraft:storage_item` contents are read from `tag.storage_item_component_content`. Nested storage items are rendered recursively. Empty markers, air items, zero-count entries, and slot-only placeholders are omitted from readable views while remaining available in raw NBT.

## Malformed UTF-8

If one native item string cannot be decoded, AntiGrief stores a degraded public snapshot containing readable identifiers, counts, damage, names, lore, and enchantments. Exact raw NBT for the malformed field may be unavailable until the item is repaired or removed.

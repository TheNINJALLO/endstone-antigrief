# 📦 Containers, Inventories & Bundles Guide

AntiGrief provides complete visibility into container states, player inventories, Ender Chests, and complex nested storage items.

---

## 🗃️ Supported Containers

AntiGrief captures exact block actor NBT and inventory contents for all Bedrock container types:

- **Standard Containers**: Chests, Trapped Chests, Double Chests, Barrels.
- **Redstone & Utility**: Hoppers, Droppers, Dispensers, Crafters, Brewing Stands, Furnaces, Smokers, Blast Furnaces.
- **Decorative & Specialized**: All 17 Shulker Box variants, Chiseled Bookshelves, Decorated Pots.

---

## 🎒 Live Player Inventories & Ender Chests

When `capture_player_inventories` is enabled in `config.json`:

- **Periodic Scans**: AntiGrief scans online player inventories every `100` ticks (5 seconds) in configurable batch sizes (`20` players per tick).
- **Tracked Categories**: Captures Main inventory slots 0-35, Armor (Helmet, Chestplate, Leggings, Boots), Offhand slot, and all 27 Ender Chest slots.
- **NBT Parsing**: Captures item count, damage, custom name, lore, enchantments, and raw SNBT.

---

## 👝 Bundles & Nested Storage Items

AntiGrief handles complex nested container items without empty-slot noise:

- **Bundle Inspection**: Recursively parses `minecraft:bundle` and `minecraft:storage_item` NBT tags.
- **Noise Filter**: Empty slots (count = 0 or `minecraft:air`) are filtered out automatically so inspectors only see active items.
- **Nested Card Rendering**: The WebUI displays nested items inside interactive, color-coded item cards showing custom names, lore, and enchantment levels.

---

## 🛡️ UTF-8 Metadata Protection & Fallbacks

Bedrock Edition allows players to format item names and lore using legacy color codes (`§a`, `§l`) or raw binary metadata bytes.

- **Safe Decoding**: AntiGrief uses a resilient UTF-8 decoder that sanitizes legacy formatting and strip invalid byte sequences.
- **Endstone API Fallback**: If native NBT decoding encounters malformed binary metadata, AntiGrief falls back to clean Endstone API text parsing, logs a throttled warning, and displays a `Degraded Metadata` indicator badge in the WebUI.

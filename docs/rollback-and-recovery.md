# 🔄 Rollback Engine & Item Recovery System

AntiGrief features a high-fidelity **3-Phase Atomic Rollback Engine** controlled exclusively by the operator `/agback` command. 

> **Operator Confirmation Boundary**: Simply opening or inspecting a container NEVER triggers item recovery or confiscation. Item recovery is initiated ONLY when an operator explicitly executes `/agback` to confirm an incident.

---

## 🏗️ 3-Phase Atomic Rollback Execution

When `/agback` is executed, AntiGrief isolates the target volume, queries historical block states, and restores the area through three sequential, non-blocking execution phases:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Phase 1: Structure & State                      │
│   • Bottom-to-top layer sorting (prevents block gravity drops)        │
│   • Removes placed grief blocks & restores original block states       │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       Phase 2: Metadata & NBT                         │
│   • BlockData C++ native state injection                               │
│   • Restores command block scripts, sign text, and block actor NBT     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Phase 3: Inventory & Storage                       │
│   • Restores chest, barrel, hopper, shulker, & bookshelf items          │
│   • Verifies container slot state & creates item recovery queue        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Item Recovery Pipeline

When container theft is confirmed via `/agback`:

1. **Snapshots Compared**: Historical container NBT is compared against live container contents to identify stolen items.
2. **Container Restored**: Target container items are restored to their exact pre-incident slot locations.
3. **Confiscation Queued**: Stolen items are flagged for recovery from the offender's inventory.
4. **Verification Safety**: Confiscation occurs ONLY if the destination container was successfully restored and verified. If the destination container is destroyed or unreachable, player inventory is left untouched.
5. **Offline Support**: If the offender is offline during `/agback`, recovery rows remain safely queued in `agdata.db` and process automatically upon the player's next login or via `/agconfiscate`.

---

## 🛡️ Recovery Safety Invariants

To guarantee server stability and prevent accidental item deletion, AntiGrief enforces strict recovery invariants:

- **No False Accusations**: Container access alone is logged as neutral evidence. Confiscation logic cannot run without `/agback`.
- **Slot Verification**: Items are placed in target containers before offending player inventories are modified.
- **SHA-256 Evidence Hashing**: Every `/agback` execution compiles an immutable report stamped with a SHA-256 cryptographic hash of all involved events, coordinates, and NBT snapshots.

---

## 💡 Best Practices for Server Administrators

- **Staging Test**: Test rollbacks in a staging copy of your world after major BDS or Endstone updates.
- **Use Player Filters**: When the offender's name is known, supply the `[player]` argument (`/agback 12 100 64 -200 15 OffenderName`) to scope rollback strictly to their actions.
- **Keep Radii Practical**: Use the smallest radius that covers the griefed build to minimize unnecessary block updates.
- **Database Backup**: Keep regular backups of `plugins/antigrief_data/agdata.db`.

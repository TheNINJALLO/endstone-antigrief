# 📜 Command Reference

This document provides the complete command reference for **AntiGrief v1.5.13**, organized by administrative role and execution scope.

---

## 👥 Member Commands

Permission node: `antigrief.command.member`

| Command | Arguments | Purpose | Description |
|---|---|---|---|
| `/ag` | `[x y z] [hours] [radius]` | Nearby Activity | Queries interaction logs near the player. Running without parameters opens the interaction menu. |
| `/aghelp` | None | Help Menu | Displays interactive command help and parameter descriptions. |

---

## 🔍 Operator Investigation Commands

Permission node: `antigrief.command.op`

| Command | Arguments | Purpose | Description |
|---|---|---|---|
| `/ags` | `[type] [keyword] [hours]` | Log Search | Searches activity logs by action type (break, place, container, kill) and keyword filter. |
| `/agcontainer` | `[player] [hours] [radius]` | Container Audit | Displays exact container opens, item additions, and item removals with canonical item names. |
| `/ago` | `[player]` | Inventory Overview | Displays a live summary of a player's Main inventory, Armor, Offhand, and Ender Chest. |
| `/density` | `[size]` | Activity Density | Scans and computes player activity concentration around the operator's current location. |

---

## 🔄 Rollback, Confiscation & Maintenance Commands

Permission node: `antigrief.command.op`

| Command | Arguments | Purpose | Description |
|---|---|---|---|
| `/agback` | `<hours> <x y z> <radius> [player]` | Coordinated Rollback | Confirms an incident, executes a 3-phase atomic area rollback, queues item recovery, and generates a printable report with SHA-256 evidence hash. |
| `/agconfiscate` | `<player>` | Manual Confiscation | Retries item recovery tasks created by a prior `/agback` execution. Cannot create new confiscation accusations without `/agback`. |
| `/agclean` | `<hours>` | Data Purge | Removes ordinary interaction logs older than the specified hours while strictly preserving immutable grief proof reports. |

### 💡 Example Administrative Workflows

```text
# 1. Investigate container theft near coordinates (100, 64, -200) within 30 blocks over the past 24 hours
/agcontainer GrieferName 24 30

# 2. Search for diamond block mining within 12 hours
/ags break diamond 12

# 3. Roll back a griefed build within a 20-block radius at (48, 22, 68) over the past hour
/agback 1 48 22 68 20

# 4. Roll back damage caused specifically by player "GrieferName" over 24 hours
/agback 24 100 64 -200 30 GrieferName

# 5. Clean up ordinary logs older than 72 hours (3 days)
/agclean 72
```

---

## 🔐 Container Ownership Management

Permission node: `antigrief.command.op`

| Command | Parameters | Description |
|---|---|---|
| `/agowner info` | `<x y z>` | Displays registered container owner and trusted access list. |
| `/agowner set` | `<x y z> <player>` | Sets the primary registered owner of a container. |
| `/agowner trust` | `<x y z> <player>` | Grants trusted status to a player for a container. |
| `/agowner untrust` | `<x y z> <player>` | Revokes trusted status from a player for a container. |
| `/agowner clear` | `<x y z>` | Removes all ownership and trust records for a container. |

> [!NOTE]
> Ownership records provide contextual evidence for operators. AntiGrief does NOT automatically punish or confiscate items simply because a non-owner opened a container.

---

## 🔨 Moderation & Ban Commands

Permission node: `antigrief.command.op`

| Command | Arguments | Description |
|---|---|---|
| `/agban` | `<player> [reason]` | Bans a player from the server and logs the reason. |
| `/agunban` | `<player>` | Removes a player ban. |
| `/agbanlist` | None | Lists active player bans. |
| `/ban-id` | `<deviceID>` | Bans a specific client device ID. |
| `/unban-id` | `<deviceID>` | Unbans a client device ID. |
| `/banlist-id` | None | Lists active device ID bans. |

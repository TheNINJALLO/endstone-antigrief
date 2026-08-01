# Command Reference

## Member commands

| Command | Description |
|---|---|
| `/ag [x y z] [hours] [radius]` | Queries nearby activity. With no arguments, opens the available interface. |
| `/aghelp` | Displays the command summary. |

## Operator investigation commands

| Command | Description |
|---|---|
| `/ags [type] [keyword] [hours]` | Searches activity by type and text. |
| `/agcontainer [player] [hours] [radius]` | Lists container opens and exact item changes. |
| `/ago [player]` | Displays a compact live inventory summary. |
| `/density [size]` | Reviews activity density around the sender. |

## Rollback and recovery

| Command | Description |
|---|---|
| `/agback <hours> <x y z> <radius> [player]` | Confirms an incident, restores the selected area, queues verified item recovery, and creates a report. |
| `/agconfiscate <player>` | Retries recovery rows previously created by `/agback`; it cannot create new accusations. |
| `/agclean <hours>` | Removes old ordinary records while keeping immutable grief reports. |

### Examples

```text
/agback 1 48 22 68 20
/agback 24 100 64 -200 30 GrieferName
/agcontainer GrieferName 6 40
/ags break diamond 12
```

## Container ownership records

```text
/agowner info <x y z>
/agowner set <x y z> <player>
/agowner trust <x y z> <player>
/agowner untrust <x y z> <player>
/agowner clear <x y z>
```

Ownership data is informational. It does not automatically remove items from friends who use a container.

## Player bans

| Command | Description |
|---|---|
| `/agban <player> [reason]` | Adds a player ban. |
| `/agunban <player>` | Removes a player ban. |
| `/agbanlist` | Lists player bans. |
| `/ban-id <deviceID>` | Bans a device identifier. |
| `/unban-id <deviceID>` | Removes a device ban. |
| `/banlist-id` | Lists device bans. |

Permissions are `antigrief.command.member` and `antigrief.command.op`.

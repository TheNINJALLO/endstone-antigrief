# Rollback and Item Recovery

`/agback` is the administrator confirmation boundary. Opening a container is logged, but it never triggers automatic confiscation.

## Rollback stages

1. Select matching events by time, center, radius, and optional player.
2. Resolve the earliest pre-change state for each coordinate.
3. Remove matching placed blocks.
4. Restore support and ordinary blocks from bottom to top.
5. Place containers and verify their live block actors.
6. Restore block state, writable metadata, and inventory in separate BlockData transactions.
7. Verify exact slots and retry transient failures.
8. Create a grief report and confirmed recovery rows.

## Recovery safety

A player item is removed only after the destination container contains the historical item in the expected slot or a verified safe slot. If the container cannot receive it, the player inventory is left unchanged. Offline players are retried after reconnecting.

## Player filter

Adding a player name limits both evidence and recovery to that player. Without a filter, the report chooses the player with the highest number of matching events as the primary actor and lists all involved players.

## Before production rollback

- Back up the world and database.
- Review the WebUI evidence.
- Use the smallest practical radius and time window.
- Supply a player filter when the actor is known.
- Test after dependency upgrades.

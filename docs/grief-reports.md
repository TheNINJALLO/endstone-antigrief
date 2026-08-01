# Grief Proof Reports

Every successful `/agback` creates a case record. Reports preserve the selected command scope and evidence even after ordinary event cleanup.

## Included evidence

- Administrator and primary actor
- Other involved players
- Center, radius, time window, world, and affected bounds
- Event timeline
- Broken, placed, exploded, looted, and changed targets
- Container type, coordinates, slots, item NBT, lore, and enchantments
- Block and inventory verification results
- Returned and pending item recovery
- SHA-256 evidence integrity hash

## Status values

- `PROCESSING`
- `COMPLETED`
- `COMPLETED PENDING RECOVERY`
- `COMPLETED WITH FAILURES`

The same report updates when an offline player later reconnects and completes recovery.

Reports are operational server evidence, not an independent legal finding.

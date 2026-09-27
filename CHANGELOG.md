# Changelog

All notable changes are documented here. This project follows semantic versioning.

## [1.5.18] - 2026-09-27

- Read native provider metadata through Endstone's bound description method so incompatible BlockData versions are rejected before bridge calls. Verified against the real native plugin wrapper.

## [1.5.17] - 2026-09-27

### Fixed
- Prefer the current BlockData inspector bridge and detect mixed native/bridge versions before calling the native API.
- Clear stale capability state and reconnect after the native service becomes unavailable.
- Document the complete BlockData 0.6.6 bundle wheel and its exact Linux runtime requirements.
- Retain the 1.5.16 SQLite Unicode fixes; 48 regression tests pass.

## [1.5.16] - 2026-09-09

### Fixed
- Prevented repeated SQLite flush failures when BlockData captures non-UTF-8 NBT strings. Container, player-inventory, interaction, recovery, and report JSON now escape surrogate characters while preserving the original bytes for restore.
- Escaped diagnostic raw SNBT at the database boundary and handled already-buffered unescaped JSON without dropping records or changing the database schema.
- Added persistence and batch-flush regressions for the reported `0x8a` byte at position 6125, all byte values, malformed UTF-8, valid Unicode, embedded NULs, and literal escape sequences.

## [1.5.13] - 2026-08-01

### Added
- Complete public documentation, command reference, WebUI guide, troubleshooting guide, and Wiki source.
- GitHub Actions workflows for validation, releases, Pages documentation, and Wiki synchronization.
- Repository governance files, issue templates, pull-request template, and security policy.

### Changed
- Unified the package, plugin, and module version metadata.
- Reorganized release documentation around installation, daily administration, rollback, and evidence workflows.
- Removed generated cache files and build artifacts from the source package.

### Included from 1.5.12
- UTF-8-safe fallback capture for malformed player inventory NBT.
- Exact BlockData container and player inventory inspection.
- Recursive bundle and custom storage-item rendering.
- Coordinated rollback, confirmed item recovery, and printable grief reports.

[Unreleased]: https://github.com/TheNINJALLO/endstone-antigrief/compare/v1.5.16...HEAD
[1.5.16]: https://github.com/TheNINJALLO/endstone-antigrief/releases/tag/v1.5.16
[1.5.13]: https://github.com/TheNINJALLO/endstone-antigrief/releases/tag/v1.5.13

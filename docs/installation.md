# Install AntiGrief 1.5.17

The supported native release set is Linux x86-64, Endstone 0.11.12, BDS 1.26.51.1, and CPython 3.14.

1. Stop Endstone and back up the AntiGrief data folder.
2. Replace the previous AntiGrief wheel with `endstone_antigrief-1.5.17-py3-none-any.whl`.
3. Install `endstone_blockdata_inspector-0.6.6-cp314-cp314-linux_x86_64.whl` from [BlockData 0.6.6](https://github.com/TheNINJALLO/endstone-blockdata-api/releases/tag/v0.6.6) in `plugins/`. This wheel includes and registers its matching native provider automatically.
4. Remove older BlockData provider libraries and inspector wheels from `plugins/` while stopped. Keep data folders. Do not mix bridge and native versions or copy the optional standalone `.so` alongside the bundle wheel.
5. Restart and confirm AntiGrief reports its BlockData connection before testing container logging and rollback.

The portable `endstone_blockdata` SDK alone is insufficient. A Windows native bundle is not provided for this release; the Python AntiGrief wheel does not make an incompatible native provider work on Windows.

If connection fails, inspect the preceding BlockData native loader message. AntiGrief prefers the current inspector bridge, checks matching versions before native calls, clears stale connections, and retries when the service becomes available. `/aghelp` and the WebUI remain the entry points for normal use; retain your existing database and configuration.

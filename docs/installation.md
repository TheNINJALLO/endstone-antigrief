# Installation

## Requirements

- BDS 1.26.33.1
- Endstone 0.11.6
- CPython 3.14
- BlockData API 0.4.8 or newer, built for the same BDS, Endstone, Python ABI, operating system, and architecture

## Files required

Your `plugins/` directory must contain:

1. The BlockData native plugin (`.so` on Linux or `.dll` on Windows)
2. The matching BlockData inspector wheel containing `_endstone_blockdata_live`
3. `endstone_antigrief-1.5.13-py3-none-any.whl`

Do not leave old versions beside the new wheel. Endstone may discover them in an undefined order.

## Linux or Pterodactyl

1. Stop the server from the panel.
2. Open the server `plugins/` directory.
3. Remove older AntiGrief wheels.
4. Upload the two matching BlockData files and the AntiGrief wheel.
5. Start the server.
6. Confirm the console includes `BlockData API connected` and `Player Inventory API connected`.
7. Edit `plugins/antigrief_data/config.json` and change `web_ui_secret`.
8. Expose the configured TCP port only to trusted networks or place it behind an authenticated reverse proxy.

## Windows

Follow the same process using the Windows x64 BlockData DLL and CPython 3.14 Windows wheel. Do not mix Linux and Windows artifacts.

## Upgrade

Back up `plugins/antigrief_data/`, stop the server, replace only the AntiGrief wheel, and restart. Database migrations run automatically. Never delete `agdata.db` unless you intentionally want to erase history.

## Verification

- Run `/aghelp`.
- Open and close a test chest, then check `/agcontainer`.
- Open the WebUI and inspect the test chest NBT.
- Put a bundle in the chest and confirm only occupied bundle entries are shown.
- Use a disposable test area before running `/agback` in production.

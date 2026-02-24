# AntiGrief Plugin v1.3.1


[![Python](https://img.shields.io/badge/python-3.9+-green.svg)](https://www.python.org/)

A comprehensive player behavior logging, rollback, and anti-grief plugin for Endstone Minecraft Bedrock servers with a built-in WebUI dashboard.

## ✨ Features

### 🔍 Behavior Logging
- **Block Events**: Break, place, explosions — all logged with full block state data
- **Container Tracking**: Tracks every item added/removed from chests, shulkers, barrels, etc.
- **Combat**: Entity damage and attack logging
- **Players**: Join events, command usage, chat messages
- **Anti-Spam**: Automatic message/command rate limiting with configurable thresholds

### ⏪ Block Rollback
- **Area Rollback**: Restore broken/placed blocks within a radius and time window
- **Block States**: Restores block orientation, open state, and other properties
- **Container Items**: Restores items inside containers using `/replaceitem` commands
- **Player Filter**: Rollback changes from a specific player only
- **GUI Support**: Interactive form for easy rollback without memorizing syntax

### 🌐 WebUI Dashboard
- Aurora-themed dark mode interface
- Real-time log filtering and search
- Statistics overview
- Ban list management
- No additional setup required — just works!

### 🛡️ Security
- Player banning (by name)
- Device banning (by device ID)
- Anti-spam protection with automatic detection
- Configurable rate limits

### 🎮 Commands
| Command | Description | Permission |
|---------|-------------|------------|
| `/ag` | Query logs (GUI if no args) | Member |
| `/ags` | Keyword search (GUI if no args) | OP |
| `/aghelp` | Show all commands | Member |
| `/agban <player> [reason]` | Ban a player | OP |
| `/agunban <player>` | Unban a player | OP |
| `/agbanlist` | List banned players | OP |
| `/ban-id <deviceID>` | Ban a device | OP |
| `/unban-id <deviceID>` | Unban a device | OP |
| `/banlist-id` | List banned devices | OP |
| `/density [size]` | Find entity density hotspot | OP |
| `/agclean <hours>` | Clean old database records | OP |
| `/ago [player]` | View player inventory | OP |
| `/agback <hours> <x y z> <radius> [player]` | Rollback block changes | OP |
| `/agcontainer [player] [hours] [radius]` | View container access logs | OP |

## 📦 Installation

### Quick Install
```bash
pip install endstone_antigrief-1.3.1-py3-none-any.whl
```

That's it! The plugin includes everything needed:
- FastAPI for the WebUI
- uvicorn as the web server
- Bedrock protocol packet decoder for container tracking
- All required dependencies

## ⚙️ Configuration

On first run, a config file is created at `plugins/antigrief_data/config.json`:

```json
{
    "record_nature_block": true,
    "record_human_block": true,
    "only_record_important_animal": true,
    "10s_message_max": 6,
    "10s_command_max": 12,
    "enable_web_ui": true,
    "no_log_mobs": ["minecraft:item", "minecraft:xp_orb"],
    "web_ui_port": 8098,
    "web_ui_secret": "change_this_secret_key"
}
```

> ⚠️ **Important**: Change `web_ui_secret` before exposing the WebUI!

## 🌐 WebUI Access

1. Start your server with the plugin
2. Open `http://localhost:8098` in your browser
3. Enter your secret key (from config.json)
4. Browse logs, view stats, manage bans

## ⏪ Rollback Usage

### Command Syntax
```
/agback <hours> <x y z> <radius> [player]
```

### Examples
```
/agback 1 100 64 -200 10          # Rollback 1 hour, 10-block radius
/agback 24 100 64 -200 20 Steve   # Rollback Steve's changes, 24 hours
/agback                           # Opens GUI form
```

### What Rollback Can Do
- ✅ Restore broken blocks with correct block states (orientation, etc.)
- ✅ Remove placed blocks (set to air)
- ✅ Restore basic container items (type + quantity)
- ✅ Handle large stacks (auto-splits at 64)

### Rollback Limitations (Bedrock)
- ⚠️ Enchantments on restored items are lost (Bedrock command limitation)
- ⚠️ Custom names/lore on items are lost
- ⚠️ Shulker box contents inside chests are not preserved
- ⚠️ Items only tracked if the container was opened while the plugin was active

## 🔧 Building from Source

```bash
pip wheel --no-deps -w dist .
```

## 📝 Version History

### v1.3.1
- 🆕 Full rebrand: commands now use `/ag` prefix instead of `/ty`
- 🆕 Block rollback with container item restoration
- 🆕 Container access tracking (items taken/added via packet decoding)
- 🆕 Block state preservation during rollback (orientation, etc.)
- 🆕 Stack overflow protection (auto-splits amounts > 64)
- 🔧 Eastern Time (EST/EDT) with automatic DST handling
- 🔧 Database auto-migration for blockdata column

### v1.2.0
- 🆕 Built-in WebUI dashboard
- 🆕 GUI menus for query and search (when called without args)
- 🆕 Entity density detection (/density)
- 🆕 English-only for simplicity
- 🆕 All dependencies bundled

## 📄 License

MIT License

## 🙏 Credits

Original Author: [yuhangle](https://github.com/yuhangle)

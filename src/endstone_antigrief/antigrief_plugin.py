"""
AntiGrief Plugin v1.3.0 - English Only Edition
Player behavior logging, analysis, and WebUI dashboard for Endstone
Full container rollback support (chests, barrels, shulker boxes)
"""

from endstone.command import Command, CommandSender
from endstone.plugin import Plugin
from endstone import ColorFormat, Player
from endstone.event import (
    event_handler, BlockBreakEvent, PlayerInteractEvent, ActorKnockbackEvent,
    BlockPlaceEvent, PlayerCommandEvent, PlayerJoinEvent, PlayerChatEvent,
    PlayerInteractActorEvent, ActorExplodeEvent, PacketReceiveEvent
)
from endstone.form import ModalForm, Dropdown, ActionForm, TextInput, Button
import endstone.form
from endstone.inventory import ItemStack
from endstone.nbt import (
    CompoundTag, ListTag, ByteTag, ShortTag, IntTag, LongTag,
    FloatTag, DoubleTag, StringTag, ByteArrayTag, IntArrayTag
)

import os
import json
import threading
import sqlite3
import random
import re
import time as tm
import signal

# Bedrock protocol packet decoding for container item tracking
try:
    from bedrock_protocol.packets import MinecraftPackets, MinecraftPacketIds
    HAS_PACKET_LIB = True
except ImportError:
    HAS_PACKET_LIB = False
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import traceback

# Import local modules
from endstone_antigrief.lang import lang
from endstone_antigrief import ag_clean

# ============================================================================
# TIMEZONE — Eastern Time (America/Detroit) with automatic DST handling
# ============================================================================
try:
    from zoneinfo import ZoneInfo
    EASTERN_TZ = ZoneInfo('America/Detroit')
except ImportError:
    # Python < 3.9 fallback
    EASTERN_TZ = timezone(timedelta(hours=-5))

def now_est():
    """Return current time in Eastern Time (EST/EDT with auto DST)."""
    return datetime.now(EASTERN_TZ)

# ============================================================================
# CONFIGURATION
# ============================================================================

PLUGIN_VERSION = "v1.3.1"
DATA_DIR = "plugins/antigrief_data"
DB_FILE = os.path.join(DATA_DIR, "agdata.db")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
BANLIST_FILE = os.path.join(DATA_DIR, "banlist.json")
BANIDLIST_FILE = os.path.join(DATA_DIR, "banidlist.json")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Default configuration
DEFAULT_CONFIG = {
    "record_nature_block": True,
    "record_human_block": True,
    "only_record_important_animal": True,
    "10s_message_max": 6,
    "10s_command_max": 12,
    "enable_web_ui": True,
    "no_log_mobs": ["minecraft:item", "minecraft:xp_orb"],
    "web_ui_port": 8098,
    "web_ui_secret": "change_this_secret_key"
}

def load_config():
    """Load or create configuration file"""
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG.copy()
    
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # Migrate missing keys from defaults
    updated = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
            updated = True
    
    if updated:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    
    return config

# Load configuration
config = load_config()

# Extract config values
RECORD_NATURE = config.get("record_nature_block", True)
RECORD_HUMAN = config.get("record_human_block", True)
ONLY_IMPORTANT_ANIMAL = config.get("only_record_important_animal", True)
MESSAGE_MAX = config.get("10s_message_max", 6)
COMMAND_MAX = config.get("10s_command_max", 12)
ENABLE_WEBUI = config.get("enable_web_ui", True)
NO_LOG_MOBS = config.get("no_log_mobs", [])
WEBUI_PORT = config.get("web_ui_port", 8098)
WEBUI_SECRET = config.get("web_ui_secret", "change_this_secret_key")

# Container block types that may hold inventories
CONTAINER_BLOCKS = {
    "minecraft:chest", "minecraft:trapped_chest", "minecraft:barrel",
    "minecraft:ender_chest",
    "minecraft:undyed_shulker_box", "minecraft:shulker_box",
    "minecraft:white_shulker_box",
    "minecraft:orange_shulker_box", "minecraft:magenta_shulker_box",
    "minecraft:light_blue_shulker_box", "minecraft:yellow_shulker_box",
    "minecraft:lime_shulker_box", "minecraft:pink_shulker_box",
    "minecraft:gray_shulker_box", "minecraft:silver_shulker_box",
    "minecraft:light_gray_shulker_box",
    "minecraft:cyan_shulker_box", "minecraft:purple_shulker_box",
    "minecraft:blue_shulker_box", "minecraft:brown_shulker_box",
    "minecraft:green_shulker_box", "minecraft:red_shulker_box",
    "minecraft:black_shulker_box",
    "minecraft:hopper", "minecraft:dropper", "minecraft:dispenser",
    "minecraft:furnace", "minecraft:blast_furnace", "minecraft:smoker",
    "minecraft:lit_furnace", "minecraft:lit_blast_furnace", "minecraft:lit_smoker",
    "minecraft:brewing_stand",
}

# Anti-spam tracking
player_commands = defaultdict(list)
player_messages = defaultdict(list)



# ============================================================================
# NBT SERIALIZATION HELPERS
# ============================================================================

def nbt_to_dict(tag):
    """Recursively convert an NBT tag to a JSON-serializable Python object."""
    if isinstance(tag, CompoundTag):
        result = {}
        for key, value in tag.items():
            result[str(key)] = nbt_to_dict(value)
        return {"__nbt_type": "compound", "value": result}
    elif isinstance(tag, ListTag):
        items = []
        for i in range(tag.size()):
            items.append(nbt_to_dict(tag[i]))
        return {"__nbt_type": "list", "value": items}
    elif isinstance(tag, ByteTag):
        return {"__nbt_type": "byte", "value": int(tag)}
    elif isinstance(tag, ShortTag):
        return {"__nbt_type": "short", "value": int(tag)}
    elif isinstance(tag, IntTag):
        return {"__nbt_type": "int", "value": int(tag)}
    elif isinstance(tag, LongTag):
        return {"__nbt_type": "long", "value": int(tag)}
    elif isinstance(tag, FloatTag):
        return {"__nbt_type": "float", "value": float(tag)}
    elif isinstance(tag, DoubleTag):
        return {"__nbt_type": "double", "value": float(tag)}
    elif isinstance(tag, StringTag):
        return {"__nbt_type": "string", "value": str(tag)}
    elif isinstance(tag, ByteArrayTag):
        return {"__nbt_type": "byte_array", "value": list(tag)}
    elif isinstance(tag, IntArrayTag):
        return {"__nbt_type": "int_array", "value": list(tag)}
    else:
        # Fallback: try str()
        return {"__nbt_type": "unknown", "value": str(tag)}


def dict_to_nbt(data):
    """Recursively convert a dict (from nbt_to_dict) back to an NBT tag."""
    if not isinstance(data, dict) or "__nbt_type" not in data:
        # Legacy or unknown format
        return None

    nbt_type = data["__nbt_type"]
    value = data["value"]

    if nbt_type == "compound":
        tag = CompoundTag()
        for k, v in value.items():
            child = dict_to_nbt(v)
            if child is not None:
                tag[k] = child
        return tag
    elif nbt_type == "list":
        tag = ListTag()
        for item in value:
            child = dict_to_nbt(item)
            if child is not None:
                tag.append(child)
        return tag
    elif nbt_type == "byte":
        return ByteTag(value)
    elif nbt_type == "short":
        return ShortTag(value)
    elif nbt_type == "int":
        return IntTag(value)
    elif nbt_type == "long":
        return LongTag(value)
    elif nbt_type == "float":
        return FloatTag(value)
    elif nbt_type == "double":
        return DoubleTag(value)
    elif nbt_type == "string":
        return StringTag(value)
    elif nbt_type == "byte_array":
        return ByteArrayTag(value)
    elif nbt_type == "int_array":
        return IntArrayTag(value)
    else:
        return None


def serialize_block_data(block):
    """Serialize a block's full data for rollback, including container inventory.
    Returns a JSON string with block_states and optional container items.
    """
    result = {}

    # Save block states (orientation, open state, etc.)
    try:
        block_data = block.data
        if block_data:
            result["block_states"] = block_data.block_states
    except Exception:
        pass

    # Capture container inventory if this is a container block
    block_type = str(block.type)
    # Normalize to minecraft: prefix format (str(block.type) may return dot notation)
    if "." in block_type and ":" not in block_type:
        block_type = "minecraft:" + block_type.split(".")[-1].lower()
    elif ":" not in block_type:
        block_type = "minecraft:" + block_type.lower()
    if block_type in CONTAINER_BLOCKS:
        print(f"[Serialize] Block type {block_type} is container, attempting inventory capture...")
        try:
            state = block.capture_state()
            has_inv = hasattr(state, 'inventory')
            print(f"[Serialize] capture_state() ok, has inventory attr: {has_inv}")
            if has_inv and state.inventory is not None:
                inv = state.inventory
                print(f"[Serialize] Inventory size: {inv.size}")
                items = []
                for slot in range(inv.size):
                    try:
                        item = inv.get_item(slot)
                        if item is not None and str(item.type) != 'minecraft:air':
                            entry = {
                                "slot": slot,
                                "type": str(item.type),
                                "amount": item.amount if hasattr(item, 'amount') else 1,
                                "data": item.data if hasattr(item, 'data') else 0,
                            }
                            # Serialize full NBT (enchantments, custom names, lore, nested items)
                            try:
                                if item.nbt is not None:
                                    entry["nbt"] = nbt_to_dict(item.nbt)
                            except Exception:
                                pass
                            items.append(entry)
                    except Exception as slot_err:
                        print(f"[Serialize] Slot {slot} read failed: {slot_err}")
                        continue
                print(f"[Serialize] Found {len(items)} items in container")
                if items:
                    result["container_items"] = items
            else:
                print(f"[Serialize] No inventory on state. State attrs: {[a for a in dir(state) if not a.startswith('_')]}")
        except Exception as e:
            print(f"[Serialize] Container inventory capture failed: {e}")

    return json.dumps(result) if result else ""

# ============================================================================
# DATABASE SETUP
# ============================================================================

def init_database():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    
    # Create main interactions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        action TEXT,
        x INTEGER,
        y INTEGER,
        z INTEGER,
        type TEXT,
        world TEXT,
        time TEXT,
        blockdata TEXT
    )
    """)
    
    # Ensure blockdata column exists (migration from older versions)
    cursor.execute("PRAGMA table_info(interactions)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'blockdata' not in columns:
        cursor.execute("ALTER TABLE interactions ADD COLUMN blockdata TEXT")
    
    conn.commit()
    return conn, cursor

# Initialize database
conn, cursor = init_database()

# Data buffers for batch writing
data_buffers = {
    'chest': [],
    'break': [],
    'animal': [],
    'place': [],
    'bomb': [],
    'container_access': []
}
buffer_lock = threading.Lock()
db_write_lock = threading.Lock()
is_cleaning = False

def insert_records(records, has_blockdata=False):
    """Insert records into database"""
    with sqlite3.connect(DB_FILE) as db:
        cur = db.cursor()
        for data in records:
            if has_blockdata and 'blockdata' in data:
                cur.execute("""
                    INSERT INTO interactions (name, action, x, y, z, type, world, time, blockdata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (data['name'], data['action'], data['coordinates']['x'], 
                      data['coordinates']['y'], data['coordinates']['z'],
                      data['type'], data['world'], data['time'], data['blockdata']))
            else:
                cur.execute("""
                    INSERT INTO interactions (name, action, x, y, z, type, world, time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (data['name'], data['action'], data['coordinates']['x'],
                      data['coordinates']['y'], data['coordinates']['z'],
                      data['type'], data['world'], data['time']))
        db.commit()

def flush_data_to_db():
    """Write buffered data to database"""
    global is_cleaning
    
    with buffer_lock:
        if is_cleaning:
            return
    
    with db_write_lock:
        with buffer_lock:
            if data_buffers['place']:
                insert_records(data_buffers['place'])
                data_buffers['place'].clear()
            if data_buffers['chest']:
                insert_records(data_buffers['chest'])
                data_buffers['chest'].clear()
            if data_buffers['break']:
                insert_records(data_buffers['break'], has_blockdata=True)
                data_buffers['break'].clear()
            if data_buffers['animal']:
                insert_records(data_buffers['animal'])
                data_buffers['animal'].clear()
            if data_buffers['bomb']:
                insert_records(data_buffers['bomb'], has_blockdata=True)
                data_buffers['bomb'].clear()
            if data_buffers['container_access']:
                insert_records(data_buffers['container_access'], has_blockdata=True)
                data_buffers['container_access'].clear()

def periodic_writer():
    """Background thread for periodic database writes"""
    while True:
        flush_data_to_db()
        tm.sleep(20)

# Start background writer thread
writer_thread = threading.Thread(target=periodic_writer, daemon=True)
writer_thread.start()

# ============================================================================
# PLUGIN CLASS
# ============================================================================

class AntiGriefPlugin(Plugin):
    api_version = "0.11"
    
    # Command definitions with English descriptions
    commands = {
        "ag": {
            "description": lang["cmd_ty_desc"],
            "usages": ["/ag [pos:pos] [time:float] [radius:float]"],
            "permissions": ["antigrief.command.member"],
        },
        "aghelp": {
            "description": lang["cmd_tyhelp_desc"],
            "usages": ["/aghelp"],
            "permissions": ["antigrief.command.member"],
        },
        "agban": {
            "description": lang["cmd_tyban_desc"],
            "usages": ["/agban <player:str> [reason:str]"],
            "permissions": ["antigrief.command.op"],
        },
        "agunban": {
            "description": lang["cmd_tyunban_desc"],
            "usages": ["/agunban <player:str>"],
            "permissions": ["antigrief.command.op"],
        },
        "agbanlist": {
            "description": lang["cmd_tybanlist_desc"],
            "usages": ["/agbanlist"],
            "permissions": ["antigrief.command.op"],
        },
        "ban-id": {
            "description": lang["cmd_banid_desc"],
            "usages": ["/ban-id <deviceID:str>"],
            "permissions": ["antigrief.command.op"],
        },
        "unban-id": {
            "description": lang["cmd_unbanid_desc"],
            "usages": ["/unban-id <deviceID:str>"],
            "permissions": ["antigrief.command.op"],
        },
        "banlist-id": {
            "description": lang["cmd_banidlist_desc"],
            "usages": ["/banlist-id"],
            "permissions": ["antigrief.command.op"],
        },
        "ags": {
            "description": lang["cmd_tys_desc"],
            "usages": ["/ags [type:str] [keyword:str] [time:float]"],
            "permissions": ["antigrief.command.op"],
        },
        "agback": {
            "description": lang["cmd_tyback_desc"],
            "usages": ["/agback <time:float> [pos:pos] <radius:float> [player:str]"],
            "permissions": ["antigrief.command.op"],
        },
        "ago": {
            "description": lang["cmd_tyo_desc"],
            "usages": ["/ago [player:str]"],
            "permissions": ["antigrief.command.op"],
        },
        "agclean": {
            "description": lang["cmd_tyclean_desc"],
            "usages": ["/agclean <hours:float>"],
            "permissions": ["antigrief.command.op"],
        },
        "density": {
            "description": lang["cmd_density_desc"],
            "usages": ["/density [size:int]"],
            "permissions": ["antigrief.command.op"],
        },
        "agcontainer": {
            "description": "View container access logs (items taken/added)",
            "usages": ["/agcontainer [player:str] [hours:float] [radius:float]"],
            "permissions": ["antigrief.command.op"],
        },
    }
    
    permissions = {
        "antigrief.command.op": {
            "description": "Operator commands",
            "default": "op",
        },
        "antigrief.command.member": {
            "description": "Member commands",
            "default": True,
        },
    }
    
    def on_load(self) -> None:
        self.logger.info("AntiGrief Plugin loading...")
    
    def on_enable(self) -> None:
        self.logger.info(f'{ColorFormat.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        self.logger.info(f'{ColorFormat.GREEN}  AntiGrief Plugin {PLUGIN_VERSION}')
        self.logger.info(f'{ColorFormat.YELLOW}  Player Behavior Logging & Analysis')
        self.logger.info(f'{ColorFormat.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        self.logger.info(f'{ColorFormat.AQUA}  Config: {CONFIG_FILE}')
        self.logger.info(f'{ColorFormat.AQUA}  Data: {DATA_DIR}/')
        
        # Start WebUI if enabled
        if ENABLE_WEBUI:
            self._start_webui()
        
        self.register_events(self)
        self.logger.info(f'{ColorFormat.GREEN}  Plugin enabled successfully!')
    
    def _start_webui(self):
        """Start the WebUI server"""
        try:
            from endstone_antigrief.webui import start_webui
            if start_webui(self.logger, WEBUI_PORT, WEBUI_SECRET):
                self.logger.info(f'{ColorFormat.GREEN}  WebUI: http://localhost:{WEBUI_PORT}')
            else:
                self.logger.warning(f'{ColorFormat.YELLOW}  WebUI startup failed')
        except Exception as e:
            self.logger.warning(f'{ColorFormat.YELLOW}  WebUI error: {e}')
    
    def on_disable(self) -> None:
        flush_data_to_db()
        self.logger.info("AntiGrief Plugin disabled, data saved.")
    
    # ========================================================================
    # GUI METHODS
    # ========================================================================
    
    def show_query_gui(self, sender):
        """Show coordinate query GUI"""
        player = self.server.get_player(sender.name)
        if not player:
            return
        
        px, py, pz = int(player.location.x), int(player.location.y), int(player.location.z)
        
        def on_submit(player, *args):
            if not args:
                return  # Form closed
            try:
                # Parse response - may be JSON string, list, or separate args
                response = args[0]
                if isinstance(response, str):
                    try:
                        values = json.loads(response)
                    except json.JSONDecodeError:
                        values = [response] + list(args[1:]) if len(args) > 1 else [response]
                elif isinstance(response, (list, tuple)):
                    values = list(response)
                else:
                    values = list(args)
                
                x = float(values[0]) if values[0] else px
                y = float(values[1]) if values[1] else py
                z = float(values[2]) if values[2] else pz
                hours = float(values[3]) if values[3] else 1.0
                radius = float(values[4]) if values[4] else 10.0
                
                if radius > 100:
                    player.send_error_message(lang["error_radius_max"])
                    return
                
                self._execute_query(player, x, y, z, hours, radius)
            except Exception as e:
                self.logger.warning(f"Query GUI error: {e}, args: {args}")
                player.send_error_message(lang["error_invalid_params"])
        
        form = ModalForm(
            title=lang["gui_query_title"],
            controls=[
                TextInput(label=f'{lang["gui_enter_coords"]} X', placeholder=str(px), default_value=str(px)),
                TextInput(label=f'{lang["gui_enter_coords"]} Y', placeholder=str(py), default_value=str(py)),
                TextInput(label=f'{lang["gui_enter_coords"]} Z', placeholder=str(pz), default_value=str(pz)),
                TextInput(label=lang["gui_enter_time"], placeholder="1", default_value="1"),
                TextInput(label=lang["gui_enter_radius"], placeholder="10", default_value="10"),
            ],
            on_submit=on_submit
        )
        player.send_form(form)
    
    def show_search_gui(self, sender):
        """Show keyword search GUI"""
        player = self.server.get_player(sender.name)
        if not player:
            return
        
        search_types = ["player", "action", "object"]
        
        def on_submit(player, *args):
            if not args:
                return  # Form closed
            try:
                response = args[0]
                if isinstance(response, str):
                    try:
                        values = json.loads(response)
                    except json.JSONDecodeError:
                        values = [response] + list(args[1:]) if len(args) > 1 else [response]
                elif isinstance(response, (list, tuple)):
                    values = list(response)
                else:
                    values = list(args)
                
                hours = float(values[0]) if values[0] else 24.0
                type_idx = int(values[1]) if values[1] else 0
                keyword = values[2] if len(values) > 2 else ""
                search_type = search_types[type_idx]
                
                self._execute_search(player, search_type, keyword, hours)
            except Exception as e:
                self.logger.warning(f"Search GUI error: {e}, args: {args}")
                player.send_error_message(lang["error_invalid_params"])
        
        form = ModalForm(
            title=lang["gui_search_title"],
            controls=[
                TextInput(label=lang["gui_enter_time"], placeholder="24", default_value="24"),
                Dropdown(label=lang["gui_search_type"], options=search_types),
                TextInput(label=lang["gui_enter_keyword"], placeholder=lang["label_keyword"]),
            ],
            on_submit=on_submit
        )
        player.send_form(form)
    
    def show_rollback_gui(self, sender):
        """Show rollback GUI form"""
        player = self.server.get_player(sender.name)
        if not player:
            return
        
        # Get player position as default
        loc = player.location
        px, py, pz = int(loc.x), int(loc.y), int(loc.z)
        
        def on_submit(player, *args):
            if not args:
                return  # Form closed
            try:
                response = args[0]
                if isinstance(response, str):
                    try:
                        values = json.loads(response)
                    except json.JSONDecodeError:
                        values = [response] + list(args[1:]) if len(args) > 1 else [response]
                elif isinstance(response, (list, tuple)):
                    values = list(response)
                else:
                    values = list(args)
                
                x = float(values[0]) if values[0] else float(px)
                y = float(values[1]) if values[1] else float(py)
                z = float(values[2]) if values[2] else float(pz)
                hours = float(values[3]) if values[3] else 1.0
                radius = float(values[4]) if values[4] else 10.0
                player_filter = values[5].strip() if len(values) > 5 and values[5] else None
                
                self._execute_rollback(player, x, y, z, hours, radius, player_filter)
            except Exception as e:
                self.logger.warning(f"Rollback GUI error: {e}, args: {args}")
                player.send_error_message(lang["error_invalid_params"])
        
        form = ModalForm(
            title="Block Rollback",
            controls=[
                TextInput(label="Center X", placeholder=str(px), default_value=str(px)),
                TextInput(label="Center Y", placeholder=str(py), default_value=str(py)),
                TextInput(label="Center Z", placeholder=str(pz), default_value=str(pz)),
                TextInput(label="Hours to rollback", placeholder="1", default_value="1"),
                TextInput(label="Radius (blocks)", placeholder="10", default_value="10"),
                TextInput(label="Player name (optional)", placeholder="Leave blank for all"),
            ],
            on_submit=on_submit
        )
        player.send_form(form)
    
    def _execute_query(self, player, x, y, z, hours, radius):
        """Execute a coordinate-based query"""
        time_threshold = now_est() - timedelta(hours=hours)
        radius_sq = radius ** 2
        
        results = []
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            cur.execute("""
                SELECT name, action, x, y, z, type, world, time FROM interactions
                WHERE (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
                AND time >= ?
                ORDER BY time DESC
                LIMIT 1000
            """, (x, x, y, y, z, z, radius_sq, time_threshold.isoformat()))
            
            for row in cur.fetchall():
                results.append({
                    'name': row[0], 'action': row[1],
                    'x': row[2], 'y': row[3], 'z': row[4],
                    'type': row[5], 'world': row[6], 'time': row[7]
                })
        
        if not results:
            player.send_message(f'{ColorFormat.YELLOW}{lang["error_no_results"]}')
            return
        
        player.send_message(f'{ColorFormat.GREEN}Found {len(results)} records within {radius} blocks, {hours} hours')
        
        # Build output
        content = ""
        for r in results[:100]:  # Limit display
            content += f'{ColorFormat.YELLOW}{r["name"]} - {r["action"]}\n'
            content += f'  Pos: {r["x"]}, {r["y"]}, {r["z"]} | {r["world"]}\n'
            content += f'  Target: {r["type"]} | {r["time"]}\n'
            content += "─" * 30 + "\n"
        
        player.send_form(ActionForm(
            title=f"Query Results ({len(results)} records)",
            content=content[:10000]
        ))
    
    def _execute_search(self, player, search_type, keyword, hours):
        """Execute a keyword search"""
        time_threshold = now_est() - timedelta(hours=hours)
        
        if search_type == "player":
            column = "name"
        elif search_type == "action":
            column = "action"
        else:
            column = "type"
        
        results = []
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            cur.execute(f"""
                SELECT name, action, x, y, z, type, world, time FROM interactions
                WHERE {column} LIKE ?
                AND time >= ?
                ORDER BY time DESC
                LIMIT 1000
            """, (f"%{keyword}%", time_threshold.isoformat()))
            
            for row in cur.fetchall():
                results.append({
                    'name': row[0], 'action': row[1],
                    'x': row[2], 'y': row[3], 'z': row[4],
                    'type': row[5], 'world': row[6], 'time': row[7]
                })
        
        if not results:
            player.send_message(f'{ColorFormat.YELLOW}{lang["error_no_results"]}')
            return
        
        player.send_message(f'{ColorFormat.GREEN}Found {len(results)} records for "{keyword}"')
        
        content = ""
        for r in results[:100]:
            content += f'{ColorFormat.YELLOW}{r["name"]} - {r["action"]}\n'
            content += f'  Pos: {r["x"]}, {r["y"]}, {r["z"]} | {r["world"]}\n'
            content += f'  Target: {r["type"]} | {r["time"]}\n'
            content += "─" * 30 + "\n"
        
        player.send_form(ActionForm(
            title=f'Search: "{keyword}" ({len(results)} records)',
            content=content[:10000]
        ))
    
    # ========================================================================
    # COMMAND HANDLER
    # ========================================================================
    
    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        cmd = command.name.lower()
        
        # /aghelp - Show help
        if cmd == "aghelp":
            self._show_help(sender)
            return True
        
        # /ag - Query logs
        if cmd == "ag":
            if len(args) == 0:
                if isinstance(sender, Player):
                    self.show_query_gui(sender)
                else:
                    sender.send_message(f'{ColorFormat.RED}{lang["error_console_only"]}')
                return True
            
            if len(args) < 3:
                sender.send_message(f'{ColorFormat.RED}{lang["error_format"]}')
                return True
            
            try:
                pos_parts = args[0].split()
                x, y, z = float(pos_parts[0]), float(pos_parts[1]) if len(pos_parts) > 1 else float(args[1]), float(pos_parts[2]) if len(pos_parts) > 2 else float(args[2])
                hours = float(args[1]) if len(pos_parts) == 3 else float(args[3]) if len(args) > 3 else 1.0
                radius = float(args[2]) if len(pos_parts) == 3 else float(args[4]) if len(args) > 4 else 10.0
                
                if radius > 100:
                    sender.send_message(f'{ColorFormat.RED}{lang["error_radius_max"]}')
                    return True
                
                if isinstance(sender, Player):
                    self._execute_query(sender, x, y, z, hours, radius)
                else:
                    sender.send_message(f'{ColorFormat.RED}{lang["error_console_only"]}')
            except (ValueError, IndexError):
                sender.send_message(f'{ColorFormat.RED}{lang["error_format"]}')
            return True
        
        # /ags - Keyword search
        if cmd == "ags":
            if len(args) == 0:
                if isinstance(sender, Player):
                    self.show_search_gui(sender)
                else:
                    sender.send_message(f'{ColorFormat.RED}{lang["error_console_only"]}')
                return True
            
            if len(args) < 3:
                sender.send_message(f'{ColorFormat.RED}{lang["error_format"]}')
                return True
            
            try:
                search_type = args[0]
                keyword = args[1]
                hours = float(args[2])
                
                if isinstance(sender, Player):
                    self._execute_search(sender, search_type, keyword, hours)
                else:
                    sender.send_message(f'{ColorFormat.RED}{lang["error_console_only"]}')
            except ValueError:
                sender.send_message(f'{ColorFormat.RED}{lang["error_format"]}')
            return True
        
        # /agban - Ban player
        if cmd == "agban":
            if len(args) == 0:
                sender.send_message(f'{ColorFormat.RED}{lang["format_error"]}')
                return True
            
            player_name = args[0]
            reason = " ".join(args[1:]) if len(args) > 1 else "No reason provided"
            
            # Load or create banlist
            banlist = {}
            if os.path.exists(BANLIST_FILE):
                with open(BANLIST_FILE, 'r', encoding='utf-8') as f:
                    banlist = json.load(f)
            
            if player_name in banlist:
                sender.send_message(f'{ColorFormat.YELLOW}{lang["player"]} {player_name} {lang["already_banned"]}')
                return True
            
            banlist[player_name] = {
                "timestamp": now_est().isoformat(),
                "reason": reason
            }
            
            with open(BANLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(banlist, f, indent=4)
            
            sender.send_message(f'{ColorFormat.GREEN}{lang["player"]} {player_name} {lang["banned_reason"]} {reason}')
            return True
        
        # /agunban - Unban player
        if cmd == "agunban":
            if len(args) == 0:
                sender.send_message(f'{ColorFormat.RED}{lang["format_error"]}')
                return True
            
            player_name = args[0]
            
            if not os.path.exists(BANLIST_FILE):
                sender.send_message(f'{ColorFormat.YELLOW}{lang["blacklist_not_exist"]}')
                return True
            
            with open(BANLIST_FILE, 'r', encoding='utf-8') as f:
                banlist = json.load(f)
            
            if player_name not in banlist:
                sender.send_message(f'{ColorFormat.YELLOW}{lang["player"]} {player_name} {lang["not_in_blacklist"]}')
                return True
            
            del banlist[player_name]
            
            with open(BANLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(banlist, f, indent=4)
            
            sender.send_message(f'{ColorFormat.GREEN}{lang["player"]} {player_name} {lang["removed_from_blacklist"]}')
            return True
        
        # /agbanlist - List banned players
        if cmd == "agbanlist":
            if not os.path.exists(BANLIST_FILE):
                sender.send_message(f'{ColorFormat.YELLOW}{lang["no_banned_players"]}')
                return True
            
            with open(BANLIST_FILE, 'r', encoding='utf-8') as f:
                banlist = json.load(f)
            
            if not banlist:
                sender.send_message(f'{ColorFormat.YELLOW}{lang["no_banned_players"]}')
                return True
            
            sender.send_message(f'{ColorFormat.GREEN}━━━ Banned Players ━━━')
            for name, data in banlist.items():
                sender.send_message(f'{ColorFormat.YELLOW}{name} - {data.get("reason", "No reason")} ({data.get("timestamp", "Unknown")})')
            return True
        
        # /ban-id - Ban device
        if cmd == "ban-id":
            if len(args) == 0:
                sender.send_message(f'{ColorFormat.RED}{lang["format_error"]}')
                return True
            
            device_id = args[0]
            
            banlist = {}
            if os.path.exists(BANIDLIST_FILE):
                with open(BANIDLIST_FILE, 'r', encoding='utf-8') as f:
                    banlist = json.load(f)
            
            if device_id in banlist:
                sender.send_message(f'{ColorFormat.YELLOW}{lang["device_id"]} {device_id} {lang["device_already_banned"]}')
                return True
            
            banlist[device_id] = {"timestamp": now_est().isoformat()}
            
            with open(BANIDLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(banlist, f, indent=4)
            
            sender.send_message(f'{ColorFormat.GREEN}{lang["device_id"]} {device_id} {lang["device_banned"]}')
            return True
        
        # /unban-id - Unban device
        if cmd == "unban-id":
            if len(args) == 0:
                sender.send_message(f'{ColorFormat.RED}{lang["format_error"]}')
                return True
            
            device_id = args[0]
            
            if not os.path.exists(BANIDLIST_FILE):
                sender.send_message(f'{ColorFormat.YELLOW}{lang["device_blacklist_not_exist"]}')
                return True
            
            with open(BANIDLIST_FILE, 'r', encoding='utf-8') as f:
                banlist = json.load(f)
            
            if device_id not in banlist:
                sender.send_message(f'{ColorFormat.YELLOW}{lang["device_id"]} {device_id} {lang["not_in_blacklist"]}')
                return True
            
            del banlist[device_id]
            
            with open(BANIDLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(banlist, f, indent=4)
            
            sender.send_message(f'{ColorFormat.GREEN}{lang["device_id"]} {device_id} {lang["removed_from_blacklist"]}')
            return True
        
        # /banlist-id - List banned devices
        if cmd == "banlist-id":
            if not os.path.exists(BANIDLIST_FILE):
                sender.send_message(f'{ColorFormat.YELLOW}{lang["no_banned_devices"]}')
                return True
            
            with open(BANIDLIST_FILE, 'r', encoding='utf-8') as f:
                banlist = json.load(f)
            
            if not banlist:
                sender.send_message(f'{ColorFormat.YELLOW}{lang["no_banned_devices"]}')
                return True
            
            sender.send_message(f'{ColorFormat.GREEN}━━━ Banned Devices ━━━')
            for device_id, data in banlist.items():
                sender.send_message(f'{ColorFormat.YELLOW}{device_id} ({data.get("timestamp", "Unknown")})')
            return True
        
        # /density - Entity density
        if cmd == "density":
            try:
                size = int(args[0]) if args else 20
            except ValueError:
                size = 20
                sender.send_message(f'{ColorFormat.YELLOW}{lang["density_default"]}')
            
            self._calculate_density(sender, size)
            return True
        
        # /agclean - Clean database
        if cmd == "agclean":
            if len(args) == 0:
                sender.send_message(f'{ColorFormat.RED}{lang["error_invalid_params"]}')
                return True
            
            try:
                hours = float(args[0])
                self._start_cleanup(hours)
                sender.send_message(f'{ColorFormat.GREEN}Database cleanup started for records older than {hours} hours...')
            except ValueError:
                sender.send_message(f'{ColorFormat.RED}{lang["error_invalid_params"]}')
            return True
        
        # /agcontainer - View container access logs
        if cmd == "agcontainer":
            if not isinstance(sender, Player):
                sender.send_message(f'{ColorFormat.RED}This command must be used in-game.')
                return True
            
            player_filter = args[0] if len(args) > 0 else None
            hours = 24.0
            radius = 50.0
            try:
                if len(args) > 1:
                    hours = float(args[1])
                if len(args) > 2:
                    radius = float(args[2])
            except ValueError:
                pass
            
            self._execute_container_query(sender, hours, radius, player_filter)
            return True
        
        # /ago - View inventory
        if cmd == "ago":
            if not isinstance(sender, Player):
                sender.send_message(f'{ColorFormat.RED}{lang["error_console_only"]}')
                return True
            
            target_name = args[0] if args else sender.name
            target = self.server.get_player(target_name)
            
            if not target:
                sender.send_message(f'{ColorFormat.RED}{lang["error_player_offline"]}')
                return True
            
            self._show_inventory(sender, target)
            return True
        
        # /agback - Rollback (experimental)
        if cmd == "agback":
            if not isinstance(sender, Player):
                sender.send_message(f'{ColorFormat.RED}{lang["error_console_only"]}')
                return True
            
            if len(args) == 0:
                # Show rollback GUI
                self.show_rollback_gui(sender)
                return True
            
            if len(args) < 3:
                sender.send_message(f'{ColorFormat.RED}Usage: /agback <hours> <x y z> <radius> [player]')
                return True
            
            try:
                self.logger.info(f"[Rollback] Raw args ({len(args)}): {args}")
                
                # Command definition: /agback <time:float> [pos:pos] <radius:float> [player:str]
                # Endstone passes: args[0]=time, args[1]="x y z" (pos as single string), args[2]=radius
                hours = float(args[0])
                pos_parts = str(args[1]).split()
                if len(pos_parts) >= 3:
                    x = float(pos_parts[0])
                    y = float(pos_parts[1])
                    z = float(pos_parts[2])
                else:
                    sender.send_message(f'{ColorFormat.RED}Invalid position. Usage: /agback <hours> <x y z> <radius> [player]')
                    return True
                radius = float(args[2])
                player_filter = args[3].strip() if len(args) > 3 and args[3] else None
                
                self._execute_rollback(sender, x, y, z, hours, radius, player_filter)
            except (ValueError, IndexError):
                sender.send_message(f'{ColorFormat.RED}{lang["error_format"]}')
            return True
        
        return False
    
    def _show_help(self, sender):
        """Display help information"""
        sender.send_message(f'{ColorFormat.GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        sender.send_message(f'{ColorFormat.AQUA}AntiGrief {PLUGIN_VERSION} - Commands')
        sender.send_message(f'{ColorFormat.GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        sender.send_message(f'{ColorFormat.YELLOW}/ag [x y z] [hours] [radius] - Query logs (no args = GUI)')
        sender.send_message(f'{ColorFormat.YELLOW}/ags [type] [keyword] [hours] - Keyword search (no args = GUI)')
        sender.send_message(f'{ColorFormat.YELLOW}/agcontainer [player] [hours] [radius] - Container access logs')
        sender.send_message(f'{ColorFormat.YELLOW}/agback <x y z> <hours> <radius> - Rollback changes')
        sender.send_message(f'{ColorFormat.YELLOW}/ago [player] - View player inventory')
        sender.send_message(f'{ColorFormat.YELLOW}/agban <player> [reason] - Ban a player')
        sender.send_message(f'{ColorFormat.YELLOW}/agunban <player> - Unban a player')
        sender.send_message(f'{ColorFormat.YELLOW}/agbanlist - List banned players')
        sender.send_message(f'{ColorFormat.YELLOW}/ban-id <deviceID> - Ban a device')
        sender.send_message(f'{ColorFormat.YELLOW}/unban-id <deviceID> - Unban a device')
        sender.send_message(f'{ColorFormat.YELLOW}/banlist-id - List banned devices')
        sender.send_message(f'{ColorFormat.YELLOW}/density [size] - Find entity density hotspot')
        sender.send_message(f'{ColorFormat.YELLOW}/agclean <hours> - Clean old database records')
        if ENABLE_WEBUI:
            sender.send_message(f'{ColorFormat.GREEN}WebUI: http://localhost:{WEBUI_PORT}')
    
    def _execute_container_query(self, sender, hours=24.0, radius=50.0, player_filter=None):
        """Query container access logs (Container Take / Container Add) near the player."""
        time_threshold = now_est() - timedelta(hours=hours)
        
        # Get player pos for radius search
        px = int(sender.location.x)
        py = int(sender.location.y)
        pz = int(sender.location.z)
        radius_sq = radius ** 2
        
        query = """
            SELECT name, action, x, y, z, type, world, time, blockdata FROM interactions
            WHERE (action = 'Container Take' OR action = 'Container Add')
            AND time >= ?
            AND (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
        """
        params = [time_threshold.isoformat(), px, px, py, py, pz, pz, radius_sq]
        
        if player_filter:
            query += " AND name LIKE ?"
            params.append(f"%{player_filter}%")
        
        query += " ORDER BY time DESC LIMIT 500"
        
        results = []
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            cur.execute(query, params)
            results = cur.fetchall()
        
        if not results:
            filter_msg = f" by '{player_filter}'" if player_filter else ""
            sender.send_message(f'{ColorFormat.YELLOW}No container access logs found{filter_msg} within {radius} blocks, {hours}h.')
            return
        
        sender.send_message(f'{ColorFormat.GREEN}Found {len(results)} container access records')
        
        # Build formatted output for the form
        content = ""
        for r in results[:100]:  # Limit display for performance
            name, action, x, y, z, item_type, world, time_str, blockdata_str = r
            
            # Color code: red for take, green for add
            if action == "Container Take":
                action_marker = f"{ColorFormat.RED}▼ TAKE"
            else:
                action_marker = f"{ColorFormat.GREEN}▲ ADD"
            
            # Parse item details from blockdata
            container_type = ""
            if blockdata_str:
                try:
                    bd = json.loads(blockdata_str)
                    ctype = bd.get("container_type", "")
                    if ctype:
                        container_type = ctype.replace("minecraft:", "")
                except Exception:
                    pass
            
            # Format time more readably
            try:
                dt = datetime.fromisoformat(time_str)
                time_display = dt.strftime("%m/%d %H:%M")
            except Exception:
                time_display = time_str[:16] if time_str else "?"
            
            content += f'{action_marker} {ColorFormat.YELLOW}{name}\n'
            content += f'  {ColorFormat.AQUA}{item_type}\n'
            content += f'  {ColorFormat.WHITE}@ {x}, {y}, {z}'
            if container_type:
                content += f' ({container_type})'
            content += f' | {time_display}\n'
            content += "─" * 30 + "\n"
        
        # Show in an ActionForm with pagination info
        title = f"Container Access ({len(results)} records)"
        if player_filter:
            title += f" - {player_filter}"
        
        sender.send_form(ActionForm(
            title=title,
            content=content[:10000]
        ))
    
    def _calculate_density(self, sender, size):
        """Calculate entity density"""
        actors = self.server.level.actors
        if not actors:
            sender.send_message(f'{ColorFormat.YELLOW}{lang["density_none"]}')
            return
        
        # Group by region
        regions = {}
        for actor in actors:
            rx = int(actor.location.x // size)
            ry = int(actor.location.y // size)
            rz = int(actor.location.z // size)
            dim = actor.location.dimension.name
            key = (rx, ry, rz, dim)
            
            if key not in regions:
                regions[key] = []
            regions[key].append(actor)
        
        # Find densest region
        densest = max(regions.items(), key=lambda x: len(x[1]))
        key, entities = densest
        
        # Calculate actual midpoint as average of entity positions
        cx = sum(e.location.x for e in entities) / len(entities)
        cy = sum(e.location.y for e in entities) / len(entities)
        cz = sum(e.location.z for e in entities) / len(entities)
        dim = key[3]
        
        # Find the entity closest to the centroid (real teleportable position)
        closest = min(entities, key=lambda e: (e.location.x - cx)**2 + (e.location.y - cy)**2 + (e.location.z - cz)**2)
        hx, hy, hz = int(closest.location.x), int(closest.location.y), int(closest.location.z)
        
        # Most common type
        types = Counter(e.type for e in entities)
        most_common = types.most_common(1)[0][0] if types else "Unknown"
        
        sender.send_message(f'{ColorFormat.GREEN}━━━ {lang["density_results"]} ━━━')
        sender.send_message(f'{ColorFormat.YELLOW}{lang["density_dimension"]}: {dim}')
        sender.send_message(f'{ColorFormat.YELLOW}{lang["density_midpoint"]}: {hx}, {hy}, {hz}')
        sender.send_message(f'{ColorFormat.YELLOW}{lang["density_count"]}: {len(entities)}')
        sender.send_message(f'{ColorFormat.YELLOW}{lang["density_most_common"]}: {most_common}')
    
    def _start_cleanup(self, hours):
        """Start database cleanup in background"""
        global is_cleaning
        
        def cleanup():
            global is_cleaning
            is_cleaning = True
            try:
                self.logger.info(f"[AntiGrief] agclean: starting cleanup for records older than {hours} hours")
                ag_clean.clean_old_interactions(DB_FILE, hours)
                self.logger.info(f"[AntiGrief] agclean: result={ag_clean.msg1}")
                self.logger.info(f"[AntiGrief] agclean: vacuum={ag_clean.vac_msg}")
            except Exception as e:
                self.logger.error(f"[AntiGrief] agclean: FAILED with error: {e}")
            finally:
                is_cleaning = False
        
        thread = threading.Thread(target=cleanup, daemon=True)
        thread.start()
    
    def _show_inventory(self, sender, target):
        """Display player inventory — disabled due to native crash risk"""
        sender.send_message(f'{ColorFormat.YELLOW}Inventory viewing is disabled to prevent server crashes.')
        sender.send_message(f'{ColorFormat.YELLOW}Use chest containers to inspect items safely.')
        return
    
    def _execute_rollback(self, sender, x, y, z, hours, radius, player_filter=None):
        """Execute block rollback with container inventory restoration"""
        # Flush any pending records to DB so recent actions are available
        try:
            flush_data_to_db()
            self.logger.info(f"[Rollback] Flushed pending data to DB")
        except Exception as e:
            self.logger.warning(f"[Rollback] Flush failed: {e}")
        
        time_threshold = now_est() - timedelta(hours=hours)
        radius_sq = radius ** 2
        
        # Diagnostic logging
        self.logger.info(f"[Rollback] Params: center=({x},{y},{z}), hours={hours}, radius={radius}, radius_sq={radius_sq}")
        self.logger.info(f"[Rollback] Time threshold: {time_threshold.isoformat()}")
        self.logger.info(f"[Rollback] Current time: {now_est().isoformat()}")
        
        # Debug: Check for ANY records near coords (without time filter)
        try:
            with sqlite3.connect(DB_FILE) as debug_db:
                debug_cur = debug_db.cursor()
                debug_cur.execute("""
                    SELECT COUNT(*), MIN(time), MAX(time) FROM interactions 
                    WHERE (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
                """, (x, x, y, y, z, z, radius_sq))
                debug_row = debug_cur.fetchone()
                self.logger.info(f"[Rollback] Records near coords (no time filter): count={debug_row[0]}, earliest={debug_row[1]}, latest={debug_row[2]}")
                
                # Also check: any records at all with Break/Place/Explode action?
                debug_cur.execute("""
                    SELECT COUNT(*) FROM interactions 
                    WHERE (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
                    AND (action LIKE '%Break%' OR action LIKE '%Place%' OR action LIKE '%Explode%')
                """, (x, x, y, y, z, z, radius_sq))
                action_count = debug_cur.fetchone()[0]
                self.logger.info(f"[Rollback] Records near coords with Break/Place/Explode action: {action_count}")
                
                # Sample most recent 3 records near coords
                debug_cur.execute("""
                    SELECT name, action, x, y, z, type, time FROM interactions 
                    WHERE (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
                    ORDER BY rowid DESC LIMIT 3
                """, (x, x, y, y, z, z, radius_sq))
                samples = debug_cur.fetchall()
                for s in samples:
                    self.logger.info(f"[Rollback] Sample record: name={s[0]}, action={s[1]}, pos=({s[2]},{s[3]},{s[4]}), type={s[5]}, time={s[6]}")
                
                # Global check: show most recent 5 records from the ENTIRE table
                debug_cur.execute("SELECT COUNT(*) FROM interactions")
                total = debug_cur.fetchone()[0]
                debug_cur.execute("""
                    SELECT name, action, x, y, z, type, time FROM interactions 
                    ORDER BY rowid DESC LIMIT 5
                """)
                global_samples = debug_cur.fetchall()
                self.logger.info(f"[Rollback] GLOBAL: {total} total records in DB. Most recent 5:")
                for s in global_samples:
                    self.logger.info(f"[Rollback]   {s[0]} | {s[1]} | ({s[2]},{s[3]},{s[4]}) | {s[5]} | {s[6]}")
        except Exception as e:
            self.logger.warning(f"[Rollback] Debug query failed: {e}")
        
        results = []
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            if player_filter:
                cur.execute("""
                    SELECT name, action, x, y, z, type, world, time, blockdata FROM interactions
                    WHERE (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
                    AND time >= ?
                    AND name LIKE ?
                    AND (action LIKE '%Break%' OR action LIKE '%Place%' OR action LIKE '%Explode%')
                    ORDER BY time DESC
                """, (x, x, y, y, z, z, radius_sq, time_threshold.isoformat(), f'%{player_filter}%'))
            else:
                cur.execute("""
                    SELECT name, action, x, y, z, type, world, time, blockdata FROM interactions
                    WHERE (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
                    AND time >= ?
                    AND (action LIKE '%Break%' OR action LIKE '%Place%' OR action LIKE '%Explode%')
                    ORDER BY time DESC
                """, (x, x, y, y, z, z, radius_sq, time_threshold.isoformat()))
            results = cur.fetchall()
        
        self.logger.info(f"[Rollback] Main query returned {len(results)} results (before dedup)")
        
        # Deduplicate: keep only the MOST RECENT record per position.
        # The most recent action tells us the current state:
        #   - Most recent = Break → block is gone → restore it
        #   - Most recent = Place → block is there → remove it
        # Results are ORDER BY time DESC, so first occurrence = most recent.
        seen_positions = {}
        for row in results:
            bx, by, bz = int(row[2]), int(row[3]), int(row[4])
            pos_key = (bx, by, bz)
            if pos_key not in seen_positions:
                seen_positions[pos_key] = row  # Keep first (most recent)
        results = list(seen_positions.values())
        self.logger.info(f"[Rollback] After dedup: {len(results)} unique positions")
        
        if not results:
            filter_msg = f" for player '{player_filter}'" if player_filter else ""
            sender.send_message(f'{ColorFormat.YELLOW}No block changes found{filter_msg} in the specified area/time.')
            return
        
        filter_msg = f" by '{player_filter}'" if player_filter else ""
        sender.send_message(f'{ColorFormat.GREEN}{lang["rollback_start"]} {len(results)} records{filter_msg} in {radius} blocks, {hours} hours...')
        
        count = 0
        container_count = 0
        errors = 0
        skipped_types = set()
        
        # Build dimension lookup map so we can rollback blocks in the correct dimension
        dim_map = {}
        try:
            for d in self.server.level.dimensions:
                dim_map[d.name] = d
        except Exception:
            pass
        # Sender's dimension as fallback
        sender_dim = None
        try:
            sender_dim = sender.location.dimension
        except Exception:
            pass
        
        # Collect container restoration tasks to run after all blocks are placed
        container_restore_queue = []
        
        for row in results:
            action = row[1]
            bx, by, bz = int(row[2]), int(row[3]), int(row[4])
            block_type_raw = str(row[5]) if row[5] else "air"
            world_name = str(row[6]) if row[6] else ""
            blockdata_str = row[8] if row[8] else ""
            
            # Clean block type - handle various formats
            block_type = block_type_raw
            if "." in block_type and ":" not in block_type:
                block_type = "minecraft:" + block_type.split(".")[-1].lower()
            elif ":" not in block_type:
                block_type = "minecraft:" + block_type.lower()
            # Remove any angle brackets or type wrappers
            block_type = block_type.replace("<", "").replace(">", "").strip()
            
            # Skip if block type still looks invalid
            if not block_type or block_type == "minecraft:none" or len(block_type) < 4:
                skipped_types.add(block_type_raw)
                errors += 1
                continue
            
            # Resolve the correct dimension for this record
            dim = dim_map.get(world_name) or sender_dim
            if dim is None:
                # Last resort fallback — try Overworld
                dim = dim_map.get("Overworld")
            
            try:
                if "Break" in action or "Explode" in action:
                    # Restore broken/exploded block
                    # Parse saved blockdata for block_states and container items
                    saved_data = None
                    if blockdata_str:
                        try:
                            saved_data = json.loads(blockdata_str)
                        except (json.JSONDecodeError, ValueError):
                            pass
                    
                    # Build a single setblock command with block states
                    states_str = ""
                    if saved_data and "block_states" in saved_data:
                        states = saved_data["block_states"]
                        state_parts = []
                        for k, v in states.items():
                            if isinstance(v, bool):
                                state_parts.append(f'"{k}"={str(v).lower()}')
                            elif isinstance(v, str):
                                state_parts.append(f'"{k}"="{v}"')
                            else:
                                state_parts.append(f'"{k}"={v}')
                        if state_parts:
                            states_str = "[" + ",".join(state_parts) + "]"
                    
                    # Use setblock command with 'replace' to overwrite existing blocks
                    cmd = f'setblock {bx} {by} {bz} {block_type}{states_str} replace'
                    result = self.server.dispatch_command(self.server.command_sender, cmd)
                    if not result:
                        self.logger.warning(f"[Rollback] setblock FAILED at ({bx},{by},{bz}): {block_type}{states_str}")
                        errors += 1
                    else:
                        count += 1
                    
                    # Queue container restoration for after block is placed
                    if saved_data and "container_items" in saved_data:
                        self.logger.info(f"[Rollback] Queued container restore at {bx},{by},{bz} with {len(saved_data['container_items'])} items")
                        container_restore_queue.append((dim, bx, by, bz, saved_data["container_items"]))
                    elif saved_data:
                        self.logger.info(f"[Rollback] Block at {bx},{by},{bz} has blockdata but NO container_items (keys: {list(saved_data.keys())})")
                        
                elif "Place" in action:
                    # Remove placed blocks — use setblock air directly
                    result = self.server.dispatch_command(
                        self.server.command_sender,
                        f'setblock {bx} {by} {bz} air replace'
                    )
                    if not result:
                        self.logger.warning(f"[Rollback] setblock air FAILED at ({bx},{by},{bz})")
                        errors += 1
                    else:
                        count += 1
            except Exception as e:
                skipped_types.add(block_type_raw)
                errors += 1
                self.logger.warning(f"Rollback error at {bx},{by},{bz} ({block_type}): {e}")
        
        # Restore container inventories after all blocks have been placed
        self.logger.info(f"[Rollback] Container restore queue: {len(container_restore_queue)} containers to restore")
        for dim, bx, by, bz, items_data in container_restore_queue:
            if dim is None:
                self.logger.warning(f"Cannot restore container at {bx},{by},{bz}: no dimension reference")
                continue
            try:
                self.logger.info(f"[Rollback] Restoring container at {bx},{by},{bz} with {len(items_data)} items...")
                self._restore_container_items(dim, bx, by, bz, items_data)
                container_count += 1
                self.logger.info(f"[Rollback] Container at {bx},{by},{bz} restored successfully")
            except Exception as e:
                self.logger.warning(f"Failed to restore container at {bx},{by},{bz}: {e}")
                import traceback
                self.logger.warning(traceback.format_exc())
        
        result_msg = f'{ColorFormat.GREEN}Rollback complete: {count} blocks processed'
        if container_count > 0:
            result_msg += f' {ColorFormat.AQUA}({container_count} containers restored with items)'
        if errors > 0:
            result_msg += f' {ColorFormat.YELLOW}({errors} skipped)'
            if skipped_types:
                self.logger.warning(f"Rollback skipped block types: {list(skipped_types)[:5]}")
        sender.send_message(result_msg)
    
    def _reconstruct_container_inventory(self, x, y, z, world):
        """Reconstruct a container's inventory from Container Add/Take records in the DB.
        Returns a list of items suitable for container_items in blockdata.
        Net approach: adds increase item counts, takes decrease them."""
        items = {}  # key: item_type, value: {'type': str, 'amount': int, ...}
        
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            # Get all Container Add/Take records at this exact position
            cur.execute("""
                SELECT action, type, blockdata FROM interactions
                WHERE x = ? AND y = ? AND z = ? AND world = ?
                AND (action = 'Container Add' OR action = 'Container Take')
                ORDER BY rowid ASC
            """, (x, y, z, world))
            
            for row in cur.fetchall():
                action, display_type, bd_str = row
                if not bd_str:
                    continue
                try:
                    bd = json.loads(bd_str)
                except (json.JSONDecodeError, ValueError):
                    continue
                
                item_type = bd.get('item')
                amount = bd.get('amount', 1)
                if not item_type:
                    continue
                
                if action == 'Container Add':
                    if item_type in items:
                        items[item_type]['amount'] += amount
                    else:
                        entry = {'type': item_type, 'amount': amount, 'slot': len(items)}
                        if bd.get('custom_name'):
                            entry['custom_name'] = bd['custom_name']
                        if bd.get('enchantments'):
                            entry['enchantments'] = bd['enchantments']
                        if bd.get('shulker_contents'):
                            entry['shulker_contents'] = bd['shulker_contents']
                        items[item_type] = entry
                elif action == 'Container Take':
                    if item_type in items:
                        items[item_type]['amount'] -= amount
                        if items[item_type]['amount'] <= 0:
                            del items[item_type]
        
        # Convert to list, reassign slots
        result = []
        for i, item in enumerate(items.values()):
            if item['amount'] > 0:
                item['slot'] = i
                result.append(item)
        return result

    def _restore_container_items(self, dim, bx, by, bz, items_data):
        """Restore container inventory items using /replaceitem block commands.
        This works even though capture_state().inventory is not available in Endstone 0.11."""
        next_overflow_slot = 27  # Use slots 27+ for overflow from large stacks
        for item_data in items_data:
            try:
                slot = item_data.get("slot", 0)
                item_type = item_data.get("type", "")
                amount = item_data.get("amount", 1)
                
                if not item_type or item_type == 'minecraft:air':
                    continue
                
                # Ensure minecraft: prefix
                if ':' not in item_type:
                    item_type = f'minecraft:{item_type}'
                
                # Cap at 64 per slot (Bedrock max stack), split overflow
                remaining = amount
                current_slot = slot
                while remaining > 0:
                    batch = min(remaining, 64)
                    cmd = f'replaceitem block {bx} {by} {bz} slot.container {current_slot} {item_type} {batch}'
                    self.server.dispatch_command(self.server.command_sender, cmd)
                    self.logger.info(f"[Rollback] Restored {item_type} x{batch} to slot {current_slot} at {bx},{by},{bz}")
                    remaining -= batch
                    if remaining > 0:
                        next_overflow_slot += 1
                        current_slot = next_overflow_slot
            except Exception as e:
                self.logger.warning(f"Failed to restore item in slot {item_data.get('slot', '?')}: {e}")
    
    
    # ========================================================================
    # CONTAINER ACCESS TRACKING (Packet-Based)
    # ========================================================================
    # Uses bedrock-protocol-packets to decode ItemStackRequest packets (147)
    # which contain Take/Place actions with amount + slot info. Combined with
    # player inventory reads to resolve item types.
    #
    # Container slot enum values (Bedrock protocol):
    #   7  = LevelEntityContainer (chest, generic container)
    #   12 = CombinedHotbarAndInventoryContainer
    #   28 = HotbarContainer
    #   29 = InventoryContainer
    #   30 = ShulkerBoxContainer
    #   34 = OffhandContainer
    #   58 = BarrelContainer
    #   59 = CursorContainer
    
    # Container enum values that represent PLAYER inventory (not the container)
    PLAYER_CONTAINER_ENUMS = {12, 28, 29, 34, 59}  # inventory, hotbar, offhand, cursor
    
    def _extract_item_meta(self, item):
        """Extract metadata (custom name, enchantments, lore) from an item.
        Returns dict with 'custom_name', 'enchantments', 'lore' keys.
        Uses ItemMeta API first, with structured NBT fallback."""
        meta = {'custom_name': None, 'enchantments': [], 'lore': []}
        
        # --- Try ItemMeta API first ---
        try:
            item_meta = item.item_meta
            if item_meta is not None:
                # Custom display name
                try:
                    dn = item_meta.display_name
                    if dn:
                        meta['custom_name'] = str(dn)
                except Exception:
                    pass
                # Enchantments via ItemMeta
                try:
                    if hasattr(item_meta, 'has_enchants') and item_meta.has_enchants():
                        enchants = item_meta.enchants
                        if enchants:
                            for ench, level in enchants.items():
                                meta['enchantments'].append({
                                    'name': str(ench).replace('minecraft:', ''),
                                    'level': int(level)
                                })
                except Exception:
                    pass
                # Lore via ItemMeta
                try:
                    lore = item_meta.lore
                    if lore:
                        meta['lore'] = [str(line) for line in lore]
                except Exception:
                    pass
        except Exception:
            pass
        
        # --- Structured NBT fallback (safe — no str(nbt)) ---
        if not meta['enchantments'] or not meta['lore'] or not meta['custom_name']:
            try:
                nbt = item.nbt
                if nbt is not None:
                    # Custom name fallback via NBT
                    if not meta['custom_name']:
                        try:
                            tag = nbt.get_compound('tag')
                            if tag:
                                display = tag.get_compound('display')
                                if display:
                                    dn = display.get_string('Name')
                                    if dn:
                                        meta['custom_name'] = str(dn)
                        except Exception:
                            pass
                    # Enchantments fallback via NBT
                    if not meta['enchantments']:
                        try:
                            tag = nbt.get_compound('tag')
                            if tag:
                                ench_tag = None
                                try:
                                    ench_tag = tag.get_list('ench')
                                except Exception:
                                    pass
                                if ench_tag is None:
                                    try:
                                        ench_tag = tag.get_list('Enchantments')
                                    except Exception:
                                        pass
                                if ench_tag is not None:
                                    for ei in range(len(ench_tag)):
                                        try:
                                            ec = ench_tag.get_compound(ei)
                                            if ec:
                                                eid = ''
                                                elvl = 0
                                                try:
                                                    eid = ec.get_string('id')
                                                except Exception:
                                                    pass
                                                try:
                                                    elvl = ec.get_short('lvl')
                                                except Exception:
                                                    pass
                                                if eid:
                                                    meta['enchantments'].append({
                                                        'name': str(eid).replace('minecraft:', ''),
                                                        'level': int(elvl)
                                                    })
                                        except Exception:
                                            pass
                        except Exception:
                            pass
                    # Lore fallback via NBT
                    if not meta['lore']:
                        try:
                            tag = nbt.get_compound('tag')
                            if tag:
                                display = tag.get_compound('display')
                                if display:
                                    lore_tag = display.get_list('Lore')
                                    if lore_tag:
                                        for li in range(len(lore_tag)):
                                            try:
                                                meta['lore'].append(str(lore_tag.get_string(li)))
                                            except Exception:
                                                pass
                        except Exception:
                            pass
            except Exception:
                pass
        
        return meta
    
    def _try_read_inventory_slot(self, player, slot, container_enum):
        """Try to read an item from the player's inventory at the given slot.
        Returns dict {'type': str, 'shulker_contents': list|None, 
                      'custom_name': str|None, 'enchantments': list, 'lore': list} or None on failure."""
        try:
            inv = player.inventory
            # Map container_enum + slot to actual inventory index
            actual_slot = slot
            if container_enum == 29:  # InventoryContainer
                actual_slot = slot + 9
            elif container_enum == 59:  # CursorContainer
                # Try to read cursor item if available
                try:
                    cursor_item = inv.get_item(slot)
                    if cursor_item is not None:
                        item_type = str(cursor_item.type)
                        if item_type and item_type != 'minecraft:air':
                            result = {'type': item_type, 'shulker_contents': None}
                            result.update(self._extract_item_meta(cursor_item))
                            self._read_shulker_contents(cursor_item, result)
                            return result
                except Exception:
                    pass
                return None
            elif container_enum == 34:  # OffhandContainer
                try:
                    offhand = inv.get_item(40)  # offhand slot
                    if offhand is not None:
                        item_type = str(offhand.type)
                        if item_type and item_type != 'minecraft:air':
                            result = {'type': item_type, 'shulker_contents': None}
                            result.update(self._extract_item_meta(offhand))
                            return result
                except Exception:
                    pass
                return None
            
            item = inv.get_item(actual_slot)
            if item is not None:
                item_type = str(item.type)
                if not item_type or item_type == 'minecraft:air':
                    return None
                result = {'type': item_type, 'shulker_contents': None}
                result.update(self._extract_item_meta(item))
                self._read_shulker_contents(item, result)
                return result
        except Exception as e:
            self.logger.warning(f"[AntiGrief] Inventory slot read failed: {e}")
        return None
    
    def _snapshot_player_inventory(self, player):
        """Read all items in the player's inventory.
        Returns dict {slot: {'type': str, 'count': int, 'custom_name': str|None, 
                             'enchantments': list, 'lore': list, 'shulker_contents': list|None}}."""
        snapshot = {}
        try:
            inv = player.inventory
            for i in range(36):
                try:
                    item = inv.get_item(i)
                    if item is not None:
                        t = str(item.type)
                        if t and t != 'minecraft:air':
                            entry = {
                                'type': t,
                                'count': item.amount if hasattr(item, 'amount') else 1,
                                'shulker_contents': None
                            }
                            entry.update(self._extract_item_meta(item))
                            # Read shulker contents for shulker box items
                            self._read_shulker_contents(item, entry)
                            snapshot[i] = entry
                except Exception:
                    continue
        except Exception as e:
            self.logger.warning(f"[AntiGrief] Inventory snapshot failed: {e}")
        return snapshot
    
    def _diff_inventory_snapshots(self, player, old_snapshot, new_snapshot):
        """Compare two inventory snapshots. Returns list of dicts for items gained by the player.
        Each dict: {'type': str, 'count': int, 'shulker_contents': list|None,
                    'custom_name': str|None, 'enchantments': list, 'lore': list}."""
        gained = []
        try:
            inv = player.inventory
            for slot, new_info in new_snapshot.items():
                old_info = old_snapshot.get(slot)
                if old_info is None:
                    # Completely new slot — item was gained
                    item_result = {
                        'type': new_info['type'], 'count': new_info['count'],
                        'shulker_contents': None,
                        'custom_name': new_info.get('custom_name'),
                        'enchantments': new_info.get('enchantments', []),
                        'lore': new_info.get('lore', [])
                    }
                    # Try to read shulker contents from the actual item
                    try:
                        item = inv.get_item(slot)
                        if item is not None:
                            self._read_shulker_contents(item, item_result)
                            # Refresh metadata from live item
                            fresh_meta = self._extract_item_meta(item)
                            item_result.update(fresh_meta)
                    except Exception:
                        pass
                    gained.append(item_result)
                elif old_info['type'] != new_info['type']:
                    # Different item type — gained new item
                    item_result = {
                        'type': new_info['type'], 'count': new_info['count'],
                        'shulker_contents': None,
                        'custom_name': new_info.get('custom_name'),
                        'enchantments': new_info.get('enchantments', []),
                        'lore': new_info.get('lore', [])
                    }
                    try:
                        item = inv.get_item(slot)
                        if item is not None:
                            self._read_shulker_contents(item, item_result)
                            fresh_meta = self._extract_item_meta(item)
                            item_result.update(fresh_meta)
                    except Exception:
                        pass
                    gained.append(item_result)
                elif new_info['count'] > old_info['count']:
                    # Same type but more items — gained some
                    delta = new_info['count'] - old_info['count']
                    item_result = {
                        'type': new_info['type'], 'count': delta,
                        'shulker_contents': None,
                        'custom_name': new_info.get('custom_name'),
                        'enchantments': new_info.get('enchantments', []),
                        'lore': new_info.get('lore', [])
                    }
                    try:
                        item = inv.get_item(slot)
                        if item is not None:
                            self._read_shulker_contents(item, item_result)
                            fresh_meta = self._extract_item_meta(item)
                            item_result.update(fresh_meta)
                    except Exception:
                        pass
                    gained.append(item_result)
        except Exception as e:
            self.logger.warning(f"[AntiGrief] Inventory diff failed: {e}")
        return gained
    
    def _read_shulker_contents(self, item, result):
        """Try to read shulker box contents using structured NBT API (no str(nbt) to avoid segfault)."""
        item_type = result.get('type', '')
        if 'shulker_box' not in item_type:
            return
        
        try:
            nbt = item.nbt
            if nbt is None:
                return
            
            # Use structured NBT API — NEVER call str(nbt) as it segfaults on nested tags
            items_tag = None
            try:
                items_tag = nbt.get_list('Items')
            except Exception:
                pass
            
            if items_tag is None:
                return
            
            contents = []
            for i in range(len(items_tag)):
                try:
                    entry = items_tag.get_compound(i)
                    if entry is None:
                        continue
                    
                    # Get item name
                    item_name = None
                    try:
                        item_name = entry.get_string('Name')
                    except Exception:
                        pass
                    if not item_name:
                        continue
                    
                    # Get count and slot
                    item_count = 1
                    item_slot = -1
                    try:
                        item_count = entry.get_byte('Count')
                    except Exception:
                        pass
                    try:
                        item_slot = entry.get_byte('Slot')
                    except Exception:
                        pass
                    
                    item_entry = {
                        'name': item_name,
                        'count': int(item_count) if item_count else 1,
                        'slot': int(item_slot) if item_slot is not None else -1,
                        'custom_name': None,
                        'enchantments': [],
                        'lore': []
                    }
                    
                    # Extract metadata from tag compound
                    try:
                        tag = entry.get_compound('tag')
                        if tag is not None:
                            # Custom display name
                            try:
                                display = tag.get_compound('display')
                                if display is not None:
                                    try:
                                        dn = display.get_string('Name')
                                        if dn:
                                            item_entry['custom_name'] = str(dn)
                                    except Exception:
                                        pass
                                    # Lore
                                    try:
                                        lore_tag = display.get_list('Lore')
                                        if lore_tag is not None:
                                            for li in range(len(lore_tag)):
                                                try:
                                                    item_entry['lore'].append(str(lore_tag.get_string(li)))
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            # Enchantments
                            try:
                                ench_tag = None
                                try:
                                    ench_tag = tag.get_list('ench')
                                except Exception:
                                    pass
                                if ench_tag is None:
                                    try:
                                        ench_tag = tag.get_list('Enchantments')
                                    except Exception:
                                        pass
                                if ench_tag is not None:
                                    for ei in range(len(ench_tag)):
                                        try:
                                            ec = ench_tag.get_compound(ei)
                                            if ec is not None:
                                                eid = ''
                                                elvl = 0
                                                try:
                                                    eid = ec.get_string('id')
                                                except Exception:
                                                    pass
                                                try:
                                                    elvl = ec.get_short('lvl')
                                                except Exception:
                                                    pass
                                                if eid:
                                                    item_entry['enchantments'].append({
                                                        'name': str(eid).replace('minecraft:', ''),
                                                        'level': int(elvl)
                                                    })
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                    except Exception:
                        pass
                    
                    contents.append(item_entry)
                except Exception:
                    continue
            
            if contents:
                result['shulker_contents'] = contents
        except Exception as e:
            self.logger.warning(f"[AntiGrief] Shulker NBT read failed: {e}")
    
    def _resolve_and_log_container_action(self, player_name, actions_batch,
                                           container_info, inv_snapshot=None):
        """Process a batch of Take/Place actions from one ItemStackRequest.
        Only handles Container Add events (player puts item into container).
        inv_snapshot: open-time inventory snapshot — the PRIMARY source for item identification.
        Container Take events (player gains item) are handled by the open/close inventory diff."""
        
        # --- Helper: reliable item lookup ---
        def lookup_item(src_enum, src_slot):
            """Look up item info from snapshot FIRST, then live inventory as fallback.
            Returns dict with 'type', 'custom_name', 'enchantments', 'lore', 'shulker_contents' or None."""
            # Map container_enum + slot to snapshot key (inventory index 0-35)
            snapshot_key = None
            if src_enum == 12:       # HotbarContainer: slot 0-8 → inv 0-8
                snapshot_key = src_slot
            elif src_enum == 28:     # InventoryContainer: slot → direct
                snapshot_key = src_slot
            elif src_enum == 29:     # InventoryContainer: slot 0-26 → inv 9-35
                snapshot_key = src_slot + 9
            elif src_enum == 34:     # OffhandContainer
                snapshot_key = 40
            # Try snapshot first (always reliable — taken before any moves)
            if inv_snapshot and snapshot_key is not None:
                snap = inv_snapshot.get(snapshot_key)
                if snap:
                    # Snapshot now contains full data: type, count, enchantments, lore, shulker_contents
                    return snap
                # Also try the raw src_slot in case enum mapping is off
                snap = inv_snapshot.get(src_slot)
                if snap:
                    return snap
            # Last resort: try live inventory read 
            try:
                player = None
                for p in self.server.online_players:
                    if p.name == player_name:
                        player = p
                        break
                if player:
                    return self._try_read_inventory_slot(player, src_slot, src_enum)
            except Exception:
                pass
            return None
        
        # Classify actions into adds only
        direct_adds = []           # Place from player inventory → container (shift-click)
        player_to_cursor = []      # Take from player inventory → cursor
        cursor_to_container = []   # Place from cursor → container
        
        for amount, src_enum, src_slot, dst_enum, dst_slot in actions_batch:
            src_is_player = src_enum in self.PLAYER_CONTAINER_ENUMS
            dst_is_player = dst_enum in self.PLAYER_CONTAINER_ENUMS
            src_is_cursor = (src_enum == 59)
            dst_is_cursor = (dst_enum == 59)
            
            if src_is_player and not src_is_cursor and not dst_is_player:
                direct_adds.append((amount, src_enum, src_slot, dst_enum, dst_slot))
            elif src_is_player and not src_is_cursor and dst_is_cursor:
                player_to_cursor.append((amount, src_enum, src_slot, dst_enum, dst_slot))
            elif src_is_cursor and not dst_is_player:
                cursor_to_container.append((amount, src_enum, src_slot, dst_enum, dst_slot))
        
        # --- Handle DIRECT shift-click adds (inventory → container) ---
        for amount, src_enum, src_slot, dst_enum, dst_slot in direct_adds:
            item_type = lookup_item(src_enum, src_slot)
            self._log_container_event(player_name, 'Container Add', amount, item_type, src_slot, dst_slot, container_info)
        
        # --- Handle CLICK-MOVE adds: inventory → cursor → container ---
        if player_to_cursor and cursor_to_container:
            for amount, src_enum, src_slot, dst_enum, dst_slot in player_to_cursor:
                item_type = lookup_item(src_enum, src_slot)
                orig_dst_slot = cursor_to_container[0][4] if cursor_to_container else dst_slot
                self._log_container_event(player_name, 'Container Add', amount, item_type, src_slot, orig_dst_slot, container_info)
        elif player_to_cursor:
            # Inventory → cursor only — store as pending add
            if not hasattr(self, '_pending_cursor'):
                self._pending_cursor = {}
            for amount, src_enum, src_slot, dst_enum, dst_slot in player_to_cursor:
                item_type = lookup_item(src_enum, src_slot)
                self._pending_cursor[player_name] = {
                    'direction': 'add', 'amount': amount,
                    'src_slot': src_slot, 'item_type': item_type,
                    'container_info': container_info,
                    'time': tm.time()
                }
        
        # --- Handle ORPHANED cursor → container (from a previous request) ---
        if cursor_to_container and not player_to_cursor:
            if not hasattr(self, '_pending_cursor'):
                self._pending_cursor = {}
            pending = self._pending_cursor.pop(player_name, None)
            if pending and pending.get('direction') == 'add':
                for amount, src_enum, src_slot, dst_enum, dst_slot in cursor_to_container:
                    self._log_container_event(player_name, 'Container Add', amount,
                                              pending.get('item_type'), pending['src_slot'], dst_slot,
                                              pending.get('container_info', container_info))
    
    def _log_container_event(self, player_name, action, amount, item_info,
                              src_slot, dst_slot, container_info):
        """Write a container action to data_buffers.
        item_info can be a dict {'type': str, 'shulker_contents': list|None,
        'custom_name': str|None, 'enchantments': list, 'lore': list}, a string, or None."""
        # Extract item type, shulker contents, and metadata from item_info
        if isinstance(item_info, dict):
            item_type = item_info.get('type')
            shulker_contents = item_info.get('shulker_contents')
            custom_name = item_info.get('custom_name')
            enchantments = item_info.get('enchantments', [])
            lore = item_info.get('lore', [])
        elif isinstance(item_info, str):
            item_type = item_info
            shulker_contents = None
            custom_name = None
            enchantments = []
            lore = []
        else:
            item_type = None
            shulker_contents = None
            custom_name = None
            enchantments = []
            lore = []
        
        type_str = item_type if item_type else f"slot {src_slot}"
        display = f"{type_str} x{amount}"
        
        # Show custom name if renamed
        if custom_name:
            display = f'"{custom_name}" ({type_str}) x{amount}'
        
        # If shulker, show compact indicator (full contents in blockdata for web UI)
        if shulker_contents:
            display += f" \U0001f4e6 {len(shulker_contents)} items inside"
        
        blockdata = {
            'container_type': container_info.get('block_type', 'unknown'),
            'amount': amount,
            'item': item_type,
            'src_slot': src_slot,
            'dst_slot': dst_slot
        }
        if custom_name:
            blockdata['custom_name'] = custom_name
        if enchantments:
            blockdata['enchantments'] = enchantments
        if lore:
            blockdata['lore'] = lore
        if shulker_contents:
            blockdata['shulker_contents'] = shulker_contents
        
        data_buffers['container_access'].append({
            'name': player_name,
            'action': action,
            'coordinates': container_info.get('coordinates', {'x': 0, 'y': 0, 'z': 0}),
            'type': display,
            'world': container_info.get('world', 'unknown'),
            'time': now_est().isoformat(),
            'blockdata': json.dumps(blockdata)
        })


    # ========================================================================
    # EVENT HANDLERS
    # ========================================================================
    
    @event_handler
    def on_block_break(self, event: BlockBreakEvent):
        """Log block break events with container inventory preservation"""
        if not RECORD_HUMAN and not RECORD_NATURE:
            return
        
        player = event.player
        block = event.block
        
        # Get block type as proper string identifier
        block_type_str = str(block.type)
        if "." in block_type_str and ":" not in block_type_str:
            block_type_str = block_type_str.split(".")[-1].lower()
        
        # Serialize full block data including container inventory BEFORE it's destroyed
        blockdata_json = ""
        try:
            blockdata_json = serialize_block_data(block)
        except Exception as e:
            self.logger.warning(f"Failed to serialize block data at {block.x},{block.y},{block.z}: {e}")
        
        # If this is a container block and serialize_block_data didn't capture inventory
        # (which happens in Endstone 0.11 where capture_state() has no inventory),
        # reconstruct contents from Container Add/Take records in the DB.
        container_keywords = ('shulker_box', 'chest', 'barrel', 'hopper', 
                              'dispenser', 'dropper', 'furnace', 'smoker', 'brewing_stand')
        is_container = any(kw in block_type_str for kw in container_keywords) or \
                       (block_type_str.startswith('minecraft:') and block_type_str in CONTAINER_BLOCKS)
        
        if is_container:
            try:
                parsed_bd = json.loads(blockdata_json) if blockdata_json else {}
            except (json.JSONDecodeError, ValueError):
                parsed_bd = {}
            
            if 'container_items' not in parsed_bd:
                # Flush pending data first so recent interactions are in the DB
                try:
                    flush_data_to_db()
                except Exception:
                    pass
                
                try:
                    reconstructed = self._reconstruct_container_inventory(
                        block.x, block.y, block.z, player.location.dimension.name
                    )
                    if reconstructed:
                        parsed_bd['container_items'] = reconstructed
                        blockdata_json = json.dumps(parsed_bd)
                        self.logger.info(f"[AntiGrief] Reconstructed {len(reconstructed)} items for container at {block.x},{block.y},{block.z}")
                except Exception as e:
                    self.logger.warning(f"[AntiGrief] Container inventory reconstruction failed: {e}")
        
        data_buffers['break'].append({
            'name': player.name,
            'action': lang["action_break"],
            'coordinates': {'x': block.x, 'y': block.y, 'z': block.z},
            'type': block_type_str,
            'world': player.location.dimension.name,
            'time': now_est().isoformat(),
            'blockdata': blockdata_json
        })

        # Log shulker box contents as container events for web UI visibility
        if 'shulker_box' in block_type_str:
            try:
                # Read inventory directly from the live block
                state = block.capture_state()
                if hasattr(state, 'inventory') and state.inventory is not None:
                    inv = state.inventory
                    for slot in range(inv.size):
                        try:
                            item = inv.get_item(slot)
                            if item is not None and str(item.type) != 'minecraft:air':
                                item_type = str(item.type)
                                amount = item.amount if hasattr(item, 'amount') else 1
                                display = f"{item_type} x{amount}"
                                
                                bd = {
                                    'container_type': block_type_str,
                                    'amount': amount,
                                    'item': item_type,
                                    'src_slot': slot,
                                    'dst_slot': -1
                                }
                                # Extract metadata via ItemMeta API
                                try:
                                    meta = self._extract_item_meta(item)
                                    if meta.get('custom_name'):
                                        bd['custom_name'] = meta['custom_name']
                                        display = f'"{ meta["custom_name"] }" ({item_type}) x{amount}'
                                    if meta.get('enchantments'):
                                        bd['enchantments'] = meta['enchantments']
                                    if meta.get('lore'):
                                        bd['lore'] = meta['lore']
                                except Exception:
                                    pass
                                
                                data_buffers['container_access'].append({
                                    'name': player.name,
                                    'action': 'Shulker Break',
                                    'coordinates': {'x': block.x, 'y': block.y, 'z': block.z},
                                    'type': display,
                                    'world': player.location.dimension.name,
                                    'time': now_est().isoformat(),
                                    'blockdata': json.dumps(bd)
                                })
                        except Exception as e:
                            self.logger.warning(f"[AntiGrief] Shulker break slot {slot} read failed: {e}")
                else:
                    # Introspect the block state to find alternative inventory access
                    state_attrs = [a for a in dir(state) if not a.startswith('_')]
                    self.logger.info(f"[AntiGrief] Shulker state at {block.x},{block.y},{block.z} attrs: {state_attrs}")
                    # Check alternative properties
                    for attr in ['block_entity', 'nbt', 'container', 'items', 'contents', 'data', 'block_data']:
                        if hasattr(state, attr):
                            try:
                                val = getattr(state, attr)
                                self.logger.info(f"[AntiGrief] Shulker state.{attr} = {type(val).__name__}: {repr(val)[:200]}")
                            except Exception as e:
                                self.logger.info(f"[AntiGrief] Shulker state.{attr} access error: {e}")
                    # Also check the block object itself
                    block_attrs = [a for a in dir(block) if not a.startswith('_')]
                    self.logger.info(f"[AntiGrief] Shulker block attrs: {block_attrs}")
                    for attr in ['block_entity', 'nbt', 'container', 'inventory', 'items']:
                        if hasattr(block, attr):
                            try:
                                val = getattr(block, attr)
                                self.logger.info(f"[AntiGrief] Shulker block.{attr} = {type(val).__name__}: {repr(val)[:200]}")
                            except Exception as e:
                                self.logger.info(f"[AntiGrief] Shulker block.{attr} access error: {e}")
            except Exception as e:
                self.logger.warning(f"[AntiGrief] Shulker break content logging failed: {e}")
    
    @event_handler
    def on_block_place(self, event: BlockPlaceEvent):
        """Log block place events"""
        player = event.player
        block = event.block
        
        # Get the ACTUAL placed block type — event.block is the position which
        # may still show air at the time of the event. Try multiple approaches.
        block_type_str = "minecraft:air"
        
        # Method 1: block_placed_state (most reliable)
        try:
            block_type_str = str(event.block_placed_state.type)
        except (AttributeError, Exception):
            pass
        
        # Method 2: block_against (the block clicked on, hinting what was placed nearby)
        # Method 3: Fall back to event.block.type
        if block_type_str == "minecraft:air":
            try:
                block_type_str = str(block.type)
            except Exception:
                pass
        
        # If still air, try to get the block at the position after a tick delay
        # For now, log what we have and mark it
        if ":" not in block_type_str and "." in block_type_str:
            block_type_str = block_type_str.split(".")[-1].lower()
        
        # Track recent placements so interact handler can suppress duplicates
        if not hasattr(self, '_recent_placements'):
            self._recent_placements = {}
        placement_key = f"{player.name}:{block.x},{block.y},{block.z}"
        self._recent_placements[placement_key] = tm.time()
        
        data_buffers['place'].append({
            'name': player.name,
            'action': lang["action_place"],
            'coordinates': {'x': block.x, 'y': block.y, 'z': block.z},
            'type': block_type_str,
            'world': player.location.dimension.name,
            'time': now_est().isoformat()
        })
        
        # Log shulker box contents on place for web UI visibility
        if 'shulker_box' in block_type_str:
            try:
                # Read inventory from the placed block entity
                placed_block = player.location.dimension.get_block_at(block.x, block.y, block.z)
                state = placed_block.capture_state()
                if hasattr(state, 'inventory') and state.inventory is not None:
                    inv = state.inventory
                    for slot in range(inv.size):
                        try:
                            item = inv.get_item(slot)
                            if item is not None and str(item.type) != 'minecraft:air':
                                item_type = str(item.type)
                                amount = item.amount if hasattr(item, 'amount') else 1
                                display = f"{item_type} x{amount}"
                                
                                bd = {
                                    'container_type': block_type_str,
                                    'amount': amount,
                                    'item': item_type,
                                    'src_slot': slot,
                                    'dst_slot': -1
                                }
                                try:
                                    meta = self._extract_item_meta(item)
                                    if meta.get('custom_name'):
                                        bd['custom_name'] = meta['custom_name']
                                        display = f'"{ meta["custom_name"] }" ({item_type}) x{amount}'
                                    if meta.get('enchantments'):
                                        bd['enchantments'] = meta['enchantments']
                                    if meta.get('lore'):
                                        bd['lore'] = meta['lore']
                                except Exception:
                                    pass
                                
                                data_buffers['container_access'].append({
                                    'name': player.name,
                                    'action': 'Shulker Place',
                                    'coordinates': {'x': block.x, 'y': block.y, 'z': block.z},
                                    'type': display,
                                    'world': player.location.dimension.name,
                                    'time': now_est().isoformat(),
                                    'blockdata': json.dumps(bd)
                                })
                        except Exception:
                            continue
            except Exception:
                pass
    
    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent):
        """Log player interaction events and track container access"""
        player = event.player
        block = event.block
        
        if not block:
            return
        
        block_type_str = str(block.type)
        if block_type_str == "minecraft:air":
            return
        
        # Skip logging interactions that are actually block placements.
        # When a player places a block, both InteractEvent and PlaceEvent fire.
        # We suppress the interact if this block+player was just logged as a placement.
        if hasattr(self, '_recent_placements'):
            # Check the block clicked ON (interact coords) and adjacent blocks
            # The interact is on the block clicked, placement is on the adjacent position
            pname = player.name
            now = tm.time()
            # Clean old entries (> 2 seconds)
            expired = [k for k, v in self._recent_placements.items() if now - v > 2]
            for k in expired:
                del self._recent_placements[k]
            # Check if this player just placed a block within 0.5 seconds
            for key, place_time in self._recent_placements.items():
                if key.startswith(f"{pname}:") and now - place_time < 0.5:
                    return  # Suppress this interact — it's from a block placement
        
        # Log the interaction
        data_buffers['chest'].append({
            'name': player.name,
            'action': lang["action_interact"],
            'coordinates': {'x': block.x, 'y': block.y, 'z': block.z},
            'type': block.type,
            'world': player.location.dimension.name,
            'time': now_est().isoformat()
        })
        
        # Container access tracking
        block_type = str(block.type)
        is_container = block_type in CONTAINER_BLOCKS
        # Fallback: substring match for block types that might not match exactly
        if not is_container:
            container_keywords = ('shulker_box', 'chest', 'barrel', 'hopper', 
                                  'dispenser', 'dropper', 'furnace', 'smoker', 'brewing_stand')
            for kw in container_keywords:
                if kw in block_type:
                    is_container = True
                    break
        if is_container:
            # Track active container for this player (used by packet handler)
            if not hasattr(self, '_active_containers'):
                self._active_containers = {}
            
            # Snapshot inventory at open time for close-time diff
            inv_snapshot = {}
            snapshot_taken = False
            try:
                inv_snapshot = self._snapshot_player_inventory(player)
                snapshot_taken = True
            except Exception as e:
                self.logger.warning(f"[AntiGrief] ContainerOpen: snapshot failed: {e}")
            
            # DIAGNOSTIC: Check if player has any container/open_inventory references
            try:
                player_attrs = [a for a in dir(player) if not a.startswith('_') and 'inv' in a.lower() or 'container' in a.lower() or 'open' in a.lower()]
                print(f"[ContainerDiag] Player attrs with inv/container/open: {player_attrs}")
                # Try all promising attributes
                for attr_name in player_attrs:
                    try:
                        val = getattr(player, attr_name)
                        if not callable(val):
                            print(f"[ContainerDiag] player.{attr_name} = {val} (type: {type(val).__name__})")
                    except Exception as ae:
                        print(f"[ContainerDiag] player.{attr_name} -> ERROR: {ae}")
            except Exception as de:
                print(f"[ContainerDiag] Introspection failed: {de}")
            
            self._active_containers[player.name] = {
                'coordinates': {'x': block.x, 'y': block.y, 'z': block.z},
                'block_type': block_type,
                'world': player.location.dimension.name,
                'time': tm.time(),
                'inv_snapshot': inv_snapshot,
                'snapshot_taken': snapshot_taken
            }
            # Log the container open event
            data_buffers['container_access'].append({
                'name': player.name,
                'action': 'Container Open',
                'coordinates': {'x': block.x, 'y': block.y, 'z': block.z},
                'type': block_type,
                'world': player.location.dimension.name,
                'time': now_est().isoformat()
            })
    
    @event_handler
    def on_packet_receive(self, event: PacketReceiveEvent):
        """Intercept packets for container item tracking."""
        if not HAS_PACKET_LIB:
            return
        
        if not hasattr(self, '_active_containers'):
            self._active_containers = {}
        
        packet_id = event.packet_id
        
        # ContainerClose (47) — player closed a container
        if packet_id == MinecraftPacketIds.ContainerClose:
            try:
                player = event.player
                if player and player.name in self._active_containers:
                    container_info = self._active_containers.pop(player.name)
                    
                    # Diff inventory to find what was taken FROM the container
                    old_snapshot = container_info.get('inv_snapshot', {})
                    snapshot_was_taken = container_info.get('snapshot_taken', False)
                    
                    if snapshot_was_taken and player:
                        try:
                            new_snapshot = self._snapshot_player_inventory(player)
                            gained_items = self._diff_inventory_snapshots(player, old_snapshot, new_snapshot)
                            for item_info in gained_items:
                                self._log_container_event(
                                    player.name, 'Container Take',
                                    item_info['count'], item_info,
                                    -1, -1, container_info
                                )
                        except Exception as e:
                            self.logger.warning(f"[AntiGrief] Close-time inventory diff failed: {e}")
                    else:
                        self.logger.warning(f"[AntiGrief] ContainerClose: skipping diff - snapshot_taken={snapshot_was_taken}, player={'yes' if player else 'no'}")
                    
                    # Clean up pending cursor data
                    if hasattr(self, '_pending_cursor') and player.name in self._pending_cursor:
                        del self._pending_cursor[player.name]
                    
                    # Log container close event
                    data_buffers['container_access'].append({
                        'name': player.name,
                        'action': 'Container Close',
                        'coordinates': container_info.get('coordinates', {'x': 0, 'y': 0, 'z': 0}),
                        'type': container_info.get('block_type', 'unknown'),
                        'world': container_info.get('world', 'unknown'),
                        'time': now_est().isoformat()
                    })
            except Exception as e:
                self.logger.warning(f"[AntiGrief] ContainerClose handling failed: {e}")
        
        # ItemStackRequest (147) — item movement actions
        elif packet_id == MinecraftPacketIds.ItemStackRequest:
            try:
                player = event.player
                if not player or player.name not in self._active_containers:
                    return
                
                container_info = self._active_containers[player.name]
                
                # Parse the packet
                packet = MinecraftPackets.create_packet(MinecraftPacketIds.ItemStackRequest)
                packet.deserialize(event.payload)
                
                # Process each request's actions as a BATCH
                for req_data in packet.request.request_data:
                    if not req_data.is_parsable_action:
                        continue
                    actions_batch = []
                    for action in req_data.request_actions:
                        if action.action_data is None:
                            continue
                        # Only process Take (0) and Place (1) actions
                        action_type = action.action_type
                        if action_type not in (0, 1):  # Take, Place
                            continue
                        
                        data = action.action_data
                        actions_batch.append((
                            data.amount,
                            data.source.container.container_enum,
                            data.source.slot,
                            data.distination.container.container_enum,  # typo in lib
                            data.distination.slot
                        ))
                    
                    if actions_batch:
                        self._resolve_and_log_container_action(
                            player.name, actions_batch, container_info,
                            inv_snapshot=container_info.get('inv_snapshot')
                        )
            except Exception as e:
                self.logger.warning(f"[AntiGrief] ItemStackRequest handling failed: {e}")
    
    @event_handler
    def on_actor_knockback(self, event: ActorKnockbackEvent):
        """Log entity damage events"""
        if ONLY_IMPORTANT_ANIMAL:
            important = ["minecraft:horse", "minecraft:pig", "minecraft:wolf", 
                        "minecraft:cat", "minecraft:sniffer", "minecraft:parrot",
                        "minecraft:donkey", "minecraft:mule", "minecraft:villager"]
            if event.actor.type not in important:
                return
        
        actor = event.actor
        source = event.source if hasattr(event, 'source') else None
        
        data_buffers['animal'].append({
            'name': source.name if source and hasattr(source, 'name') else "Unknown",
            'action': lang["action_attack"],
            'coordinates': {'x': int(actor.location.x), 'y': int(actor.location.y), 'z': int(actor.location.z)},
            'type': actor.type,
            'world': actor.location.dimension.name,
            'time': now_est().isoformat()
        })
    
    @event_handler
    def on_explosion(self, event: ActorExplodeEvent):
        """Log explosion events with container inventory preservation"""
        actor = event.actor
        actor_type = str(actor.type) if hasattr(actor, 'type') else "explosion"
        
        # Log each affected block individually so they can be rolled back
        if hasattr(event, 'block_list') and event.block_list:
            for block in event.block_list:
                # Get block type as proper string identifier
                block_type_str = str(block.type)
                if "." in block_type_str and ":" not in block_type_str:
                    block_type_str = block_type_str.split(".")[-1].lower()
                
                # Serialize full block data including container inventory
                blockdata_json = ""
                try:
                    blockdata_json = serialize_block_data(block)
                except Exception:
                    pass
                
                data_buffers['bomb'].append({
                    'name': actor_type,
                    'action': lang["action_explode"],
                    'coordinates': {'x': block.x, 'y': block.y, 'z': block.z},
                    'type': block_type_str,
                    'world': actor.location.dimension.name if hasattr(actor, 'location') else "Overworld",
                    'time': now_est().isoformat(),
                    'blockdata': blockdata_json
                })
        else:
            # Fallback - just log the explosion center
            data_buffers['bomb'].append({
                'name': actor_type,
                'action': lang["action_explode"],
                'coordinates': {'x': int(actor.location.x), 'y': int(actor.location.y), 'z': int(actor.location.z)},
                'type': "explosion",
                'world': actor.location.dimension.name,
                'time': now_est().isoformat(),
                'blockdata': ""
            })
    
    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        """Handle player join - check bans"""
        player = event.player
        
        # Check player ban
        if os.path.exists(BANLIST_FILE):
            with open(BANLIST_FILE, 'r', encoding='utf-8') as f:
                banlist = json.load(f)
            
            if player.name in banlist:
                reason = banlist[player.name].get("reason", "Banned")
                player.kick(f'{lang["you_are_banned"]} {reason}')
                return
        
        # Check device ban
        if os.path.exists(BANIDLIST_FILE):
            with open(BANIDLIST_FILE, 'r', encoding='utf-8') as f:
                banlist = json.load(f)
            
            device_id = player.device_id if hasattr(player, 'device_id') else None
            if device_id and device_id in banlist:
                player.kick(f'{lang["device_banned_at"]} {banlist[device_id].get("timestamp", "Unknown")}')
                return
        
        # Log join
        self.logger.info(f'{ColorFormat.GREEN}{player.name} ({lang["system_name"]}: {player.device_os if hasattr(player, "device_os") else "Unknown"}) {lang["joined_game"]}')
    
    @event_handler
    def on_player_chat(self, event: PlayerChatEvent):
        """Anti-spam for chat messages"""
        player = event.player
        now = tm.time()
        
        # Clean old entries
        player_messages[player.name] = [t for t in player_messages[player.name] if now - t < 10]
        player_messages[player.name].append(now)
        
        if len(player_messages[player.name]) > MESSAGE_MAX:
            # Auto-ban for spam
            banlist = {}
            if os.path.exists(BANLIST_FILE):
                with open(BANLIST_FILE, 'r', encoding='utf-8') as f:
                    banlist = json.load(f)
            
            banlist[player.name] = {
                "timestamp": now_est().isoformat(),
                "reason": lang["spam_msg_ban"]
            }
            
            with open(BANLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(banlist, f, indent=4)
            
            player.kick(lang["spam_msg_ban"])
            self.logger.warning(f'{ColorFormat.RED}{player.name} {lang["spam_msg_notify"]}')
    
    @event_handler
    def on_player_command(self, event: PlayerCommandEvent):
        """Anti-spam for commands"""
        player = event.player
        now = tm.time()
        
        # Clean old entries
        player_commands[player.name] = [t for t in player_commands[player.name] if now - t < 10]
        player_commands[player.name].append(now)
        
        if len(player_commands[player.name]) > COMMAND_MAX:
            # Auto-ban for command spam
            banlist = {}
            if os.path.exists(BANLIST_FILE):
                with open(BANLIST_FILE, 'r', encoding='utf-8') as f:
                    banlist = json.load(f)
            
            banlist[player.name] = {
                "timestamp": now_est().isoformat(),
                "reason": lang["spam_cmd_ban"]
            }
            
            with open(BANLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(banlist, f, indent=4)
            
            player.kick(lang["spam_cmd_ban"])
            self.logger.warning(f'{ColorFormat.RED}{player.name} {lang["spam_cmd_notify"]}')
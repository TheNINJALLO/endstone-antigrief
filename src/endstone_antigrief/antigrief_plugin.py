"""
AntiGrief Plugin v1.5.0 - BlockData Edition
Player behavior logging, analysis, and WebUI dashboard for Endstone
"""

from endstone.command import Command, CommandSender
from endstone.plugin import Plugin
from endstone import ColorFormat, Player
from endstone.event import (
    event_handler, BlockBreakEvent, PlayerInteractEvent, ActorKnockbackEvent,
    BlockPlaceEvent, PlayerCommandEvent, PlayerJoinEvent, PlayerChatEvent,
    PlayerInteractActorEvent, ActorExplodeEvent, PacketReceiveEvent, ScriptMessageEvent
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
from uuid import uuid4

# Import local modules
from endstone_antigrief.lang import lang
from endstone_antigrief import ag_clean
from endstone_antigrief.blockdata_adapter import BlockDataAdapter, BlockDataUnavailable

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

PLUGIN_VERSION = "v1.5.1"
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
    "web_ui_secret": "change_this_secret_key",
    "require_blockdata_api": True,
    "capture_container_open_close": True,
    "store_raw_snbt": True
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
REQUIRE_BLOCKDATA = config.get("require_blockdata_api", True)
CAPTURE_CONTAINER_OPEN_CLOSE = config.get("capture_container_open_close", True)
STORE_RAW_SNBT = config.get("store_raw_snbt", True)



# Anti-spam tracking
player_commands = defaultdict(list)
player_messages = defaultdict(list)

# Container tracking — blocks whose inventories we can snapshot
CONTAINER_BLOCKS = {
    "minecraft:chest", "minecraft:trapped_chest", "minecraft:barrel",
    "minecraft:hopper", "minecraft:dropper", "minecraft:dispenser",
    "minecraft:furnace", "minecraft:blast_furnace", "minecraft:smoker",
    "minecraft:brewing_stand", "minecraft:chiseled_bookshelf",
    "minecraft:crafter", "minecraft:decorated_pot", "minecraft:shulker_box",
    "minecraft:white_shulker_box", "minecraft:orange_shulker_box",
    "minecraft:magenta_shulker_box", "minecraft:light_blue_shulker_box",
    "minecraft:yellow_shulker_box", "minecraft:lime_shulker_box",
    "minecraft:pink_shulker_box", "minecraft:gray_shulker_box",
    "minecraft:light_gray_shulker_box", "minecraft:cyan_shulker_box",
    "minecraft:purple_shulker_box", "minecraft:blue_shulker_box",
    "minecraft:brown_shulker_box", "minecraft:green_shulker_box",
    "minecraft:red_shulker_box", "minecraft:black_shulker_box",
    "minecraft:undyed_shulker_box",
}

# Per-player container inventory snapshots: {player_name: {pos_key, block_type, dimension, snapshot}}
container_snapshots = {}



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
        list_tag = ListTag()
        for item in value:
            child = dict_to_nbt(item)
            if child is not None:
                list_tag.append(child)
        return list_tag
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
    """Serialize a block's data for rollback (block states only).
    Returns a JSON string with block_states for orientation etc.
    """
    result = {}

    # Save block states (orientation, open state, etc.)
    try:
        block_data = block.data
        if block_data:
            result["block_states"] = block_data.block_states
    except Exception:
        pass

    return json.dumps(result) if result else ""

# ============================================================================
# DATABASE SETUP
# ============================================================================

def init_database():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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

    # Full canonical BlockData snapshots are stored separately from the compact event row.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS container_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        player_name TEXT,
        reason TEXT,
        x INTEGER,
        y INTEGER,
        z INTEGER,
        world TEXT,
        block_type TEXT,
        revision INTEGER,
        captured_at TEXT,
        occupied_slots INTEGER DEFAULT 0,
        item_count INTEGER DEFAULT 0,
        canonical_nbt INTEGER DEFAULT 0,
        snapshot_json TEXT NOT NULL,
        raw_snbt TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interactions_time ON interactions(time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interactions_position ON interactions(world, x, y, z)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_container_snapshots_time ON container_snapshots(captured_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_container_snapshots_position ON container_snapshots(world, x, y, z)")
    
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
    'container_access': [],
    'container_snapshot': []
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

def insert_container_snapshots(records):
    """Insert full canonical BlockData snapshots into the dedicated table."""
    if not records:
        return
    with sqlite3.connect(DB_FILE) as db:
        db.execute("PRAGMA busy_timeout=5000")
        cur = db.cursor()
        for record in records:
            cur.execute("""
                INSERT OR REPLACE INTO container_snapshots (
                    snapshot_id, player_name, reason, x, y, z, world, block_type,
                    revision, captured_at, occupied_slots, item_count, canonical_nbt,
                    snapshot_json, raw_snbt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record['snapshot_id'], record.get('player_name'), record.get('reason'),
                record['x'], record['y'], record['z'], record['world'],
                record.get('block_type'), record.get('revision'), record['captured_at'],
                record.get('occupied_slots', 0), record.get('item_count', 0),
                1 if record.get('canonical_nbt') else 0, record['snapshot_json'],
                record.get('raw_snbt')
            ))
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
            if data_buffers['container_snapshot']:
                insert_container_snapshots(data_buffers['container_snapshot'])
                data_buffers['container_snapshot'].clear()


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
    version = "1.5.1"
    depend = ["blockdata_api"]
    
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
        # Endstone's native Plugin wrapper is not fully attached while Python is
        # constructing this class. In particular, accessing self.logger from
        # __init__ can dereference an uninitialised native logger and crash BDS.
        # Initialise runtime state here, after the plugin has been loaded.
        self._container_backups = {}  # Legacy behavior-pack fallback only.
        self.blockdata = BlockDataAdapter()
        self._blockdata_ready = False
        self.logger.info("AntiGrief Plugin loading...")
    
    def on_enable(self) -> None:
        self.logger.info(f'{ColorFormat.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        self.logger.info(f'{ColorFormat.GREEN}  AntiGrief Plugin {PLUGIN_VERSION}')
        self.logger.info(f'{ColorFormat.YELLOW}  Player Behavior Logging & Analysis')
        self.logger.info(f'{ColorFormat.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        self.logger.info(f'{ColorFormat.AQUA}  Config: {CONFIG_FILE}')
        self.logger.info(f'{ColorFormat.AQUA}  Data: {DATA_DIR}/')
        
        self._blockdata_ready = self.blockdata.connect(self.server)
        if self._blockdata_ready:
            adapter = self.blockdata.capabilities.get("adapter", "unknown")
            self.logger.info(
                f'{ColorFormat.GREEN}  BlockData API connected: adapter={adapter}, '
                f'capabilities={self.blockdata.capabilities}'
            )
        else:
            level = self.logger.error if REQUIRE_BLOCKDATA else self.logger.warning
            level(
                f'{ColorFormat.RED}  BlockData API unavailable: {self.blockdata.error}. '
                'Install the matching native plugin and platform inspector wheel.'
            )

        # Start WebUI even when the bridge is unavailable so historical records remain viewable.
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
        try:
            self.server.scheduler.cancel_tasks(self)
        except Exception:
            pass
        self.logger.info("AntiGrief Plugin disabled, data saved.")

    # ========================================================================
    # BLOCKDATA API HELPERS
    # ========================================================================

    @staticmethod
    def _command_dimension(dimension):
        value = str(dimension or "overworld").strip().lower()
        value = value.replace("minecraft:", "").replace(" ", "_")
        aliases = {
            "overworld": "overworld",
            "nether": "nether",
            "the_nether": "nether",
            "end": "the_end",
            "the_end": "the_end",
        }
        return aliases.get(value, value)

    @staticmethod
    def _block_states_argument(states):
        if not isinstance(states, dict) or not states:
            return ""
        parts = []
        for key, value in states.items():
            key_text = json.dumps(str(key), ensure_ascii=False)
            if isinstance(value, bool):
                value_text = str(value).lower()
            elif isinstance(value, str):
                value_text = json.dumps(value, ensure_ascii=False)
            elif value is None:
                continue
            else:
                value_text = str(value)
            parts.append(f"{key_text}={value_text}")
        return "[" + ",".join(parts) + "]" if parts else ""

    def _dispatch_in_dimension(self, dimension, command):
        dim = self._command_dimension(dimension)
        return self.server.dispatch_command(
            self.server.command_sender, f"execute in {dim} run {command}"
        )

    def _queue_container_snapshot(self, snapshot, player_name, reason, captured_at=None):
        if not self.blockdata.is_container(snapshot):
            return None
        snapshot_id = uuid4().hex
        location = dict(snapshot.get("location") or {})
        entity = self.blockdata.block_entity(snapshot) or {}
        summary = self.blockdata.snapshot_summary(snapshot)
        snapshot_for_storage = self.blockdata.json_safe(snapshot)
        raw_snbt = str(entity.get("raw_snbt") or "") if STORE_RAW_SNBT else ""
        if not STORE_RAW_SNBT and isinstance(snapshot_for_storage.get("block_entity"), dict):
            snapshot_for_storage["block_entity"].pop("raw_snbt", None)

        data_buffers['container_snapshot'].append({
            'snapshot_id': snapshot_id,
            'player_name': str(player_name or "System"),
            'reason': str(reason),
            'x': int(location.get('x', 0)),
            'y': int(location.get('y', 0)),
            'z': int(location.get('z', 0)),
            'world': str(location.get('dimension') or 'overworld'),
            'block_type': str(snapshot.get('type') or 'unknown'),
            'revision': snapshot.get('revision'),
            'captured_at': captured_at or now_est().isoformat(),
            'occupied_slots': summary['occupied_slots'],
            'item_count': summary['item_count'],
            'canonical_nbt': summary['canonical_nbt'],
            'snapshot_json': json.dumps(snapshot_for_storage, ensure_ascii=False, separators=(',', ':')),
            'raw_snbt': raw_snbt,
        })
        return snapshot_id

    def _capture_native_snapshot(self, dimension, x, y, z, player_name="System", reason="capture", store=True):
        if not self._blockdata_ready:
            return None, None
        try:
            snapshot = self.blockdata.capture(self.server, dimension, x, y, z)
            if snapshot is None:
                return None, None
            snapshot_id = (
                self._queue_container_snapshot(snapshot, player_name, reason)
                if store else None
            )
            return snapshot, snapshot_id
        except (BlockDataUnavailable, RuntimeError, SystemError, OSError) as error:
            self.logger.warning(
                f"[BlockData] Capture failed at {x},{y},{z} in {dimension}: {error}"
            )
            return None, None
        except Exception as error:
            self.logger.warning(
                f"[BlockData] Unexpected capture failure at {x},{y},{z} in {dimension}: {error}"
            )
            return None, None

    def _build_block_backup(self, block, dimension, player_name, reason):
        snapshot, snapshot_id = self._capture_native_snapshot(
            dimension, block.x, block.y, block.z, player_name, reason, store=True
        )
        if snapshot is not None:
            backup = {
                'schema_version': BlockDataAdapter.SCHEMA_VERSION,
                'provider': BlockDataAdapter.PROVIDER,
                'snapshot_id': snapshot_id,
                'block_type': snapshot.get('type'),
                'revision': snapshot.get('revision'),
                'block_states': snapshot.get('states') or {},
            }
            # Full container payloads live in container_snapshots. Keep an inline
            # fallback only when no durable snapshot row was queued.
            if self.blockdata.is_container(snapshot) and snapshot_id is None:
                backup['block_snapshot'] = snapshot
            return backup

        # Legacy fallback retains block states and behavior-pack item data.
        saved_data = {}
        try:
            if block.data:
                saved_data['block_states'] = block.data.block_states
        except Exception:
            pass
        cache_key = (block.x, block.y, block.z, dimension)
        if cache_key in self._container_backups:
            saved_data['container_items'] = self._container_backups.pop(cache_key)
        return saved_data

    @staticmethod
    def _load_container_snapshot(snapshot_id):
        if not snapshot_id:
            return None
        try:
            with sqlite3.connect(DB_FILE) as db:
                row = db.execute(
                    "SELECT snapshot_json FROM container_snapshots WHERE snapshot_id = ?",
                    (str(snapshot_id),),
                ).fetchone()
            if row and row[0]:
                decoded = json.loads(row[0])
                return decoded if isinstance(decoded, dict) else None
        except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError):
            return None
        return None

    def _saved_native_snapshot(self, saved_data):
        if not isinstance(saved_data, dict):
            return None
        snapshot = saved_data.get('block_snapshot')
        if isinstance(snapshot, dict):
            return snapshot
        snapshot = self._load_container_snapshot(saved_data.get('snapshot_id'))
        if snapshot is not None:
            return snapshot
        provider_data = saved_data.get('blockdata_api')
        if isinstance(provider_data, dict):
            snapshot = provider_data.get('snapshot')
            if isinstance(snapshot, dict):
                return snapshot
            return self._load_container_snapshot(provider_data.get('snapshot_id'))
        return None

    def _restore_native_snapshot(self, saved_snapshot, dimension, x, y, z, actor_name="Rollback"):
        if not self._blockdata_ready or not self.blockdata.is_container(saved_snapshot):
            return
        current, _ = self._capture_native_snapshot(
            dimension, x, y, z, actor_name, 'rollback_pre_apply', store=False
        )
        if current is None:
            self.logger.warning(
                f"[Rollback] BlockData could not capture recreated container at {x},{y},{z}"
            )
            return
        try:
            patch = self.blockdata.build_restore_patch(current, saved_snapshot)
            result = self.blockdata.apply(self.server, patch, 'fail_if_changed')
            if not result.get('ok') and result.get('status') == 'conflict':
                current, _ = self._capture_native_snapshot(
                    dimension, x, y, z, actor_name, 'rollback_conflict_retry', store=False
                )
                if current is not None:
                    patch = self.blockdata.build_restore_patch(current, saved_snapshot)
                    result = self.blockdata.apply(self.server, patch, 'force')
            if not result.get('ok'):
                self.logger.warning(
                    f"[Rollback] Native container restore failed at {x},{y},{z}: "
                    f"{result.get('message', result)}"
                )
                return

            restored, _ = self._capture_native_snapshot(
                dimension, x, y, z, actor_name, 'rollback_restored', store=True
            )
            restored_items = len(self.blockdata.inventory_map(restored or saved_snapshot))
            self.logger.info(
                f"[Rollback] Restored canonical NBT and {restored_items} occupied slots "
                f"at {x},{y},{z} in {dimension}"
            )
        except Exception as error:
            self.logger.warning(
                f"[Rollback] Native container restore exception at {x},{y},{z}: {error}"
            )

    def _schedule_native_restore(self, saved_snapshot, dimension, x, y, z, actor_name="Rollback"):
        def restore_task():
            self._restore_native_snapshot(saved_snapshot, dimension, x, y, z, actor_name)

        try:
            self.server.scheduler.run_task(self, restore_task, delay=1)
        except Exception as error:
            self.logger.warning(f"[Rollback] Scheduler unavailable, restoring immediately: {error}")
            restore_task()
    
    # ========================================================================
    # GUI METHODS
    # ========================================================================
    
    def show_query_gui(self, sender):
        """Show coordinate query GUI"""
        player = self.server.get_player(sender.name)
        if not player:
            return
        
        player_name = player.name  # Capture as string — safe across async boundary
        px, py, pz = int(player.location.x), int(player.location.y), int(player.location.z)
        
        def on_submit(_player, *args):
            if not args:
                return  # Form closed
            # Re-resolve player from name — the pybind11 proxy passed to the
            # callback may point to a destroyed C++ object if the player
            # disconnected while the form was open (causes SIGSEGV).
            player = self.server.get_player(player_name)
            if not player:
                return
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
                try:
                    player.send_error_message(lang["error_invalid_params"])
                except Exception:
                    pass
        
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
        
        player_name = player.name  # Capture as string — safe across async boundary
        search_types = ["player", "action", "object"]
        
        def on_submit(_player, *args):
            if not args:
                return  # Form closed
            player = self.server.get_player(player_name)
            if not player:
                return
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
                try:
                    player.send_error_message(lang["error_invalid_params"])
                except Exception:
                    pass
        
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
        
        player_name = player.name  # Capture as string — safe across async boundary
        # Get player position as default
        loc = player.location
        px, py, pz = int(loc.x), int(loc.y), int(loc.z)
        
        def on_submit(_player, *args):
            if not args:
                return  # Form closed
            player = self.server.get_player(player_name)
            if not player:
                return
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
                try:
                    player.send_error_message(lang["error_invalid_params"])
                except Exception:
                    pass
        
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
        sender.send_message(f'{ColorFormat.YELLOW}/agback <hours> <x y z> <radius> [player] - Rollback changes')
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
            WHERE action IN ('Container Take', 'Container Add', 'Container Change', 'Container NBT Change')
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
            
            # Color code the exact container operation.
            if action == "Container Take":
                action_marker = f"{ColorFormat.RED}▼ TAKE"
            elif action == "Container Add":
                action_marker = f"{ColorFormat.GREEN}▲ ADD"
            elif action == "Container NBT Change":
                action_marker = f"{ColorFormat.AQUA}◆ NBT"
            else:
                action_marker = f"{ColorFormat.YELLOW}↔ CHANGE"
            
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
        
        # Group by region — actors may despawn mid-iteration, guard each access
        regions = {}
        for actor in actors:
            try:
                rx = int(actor.location.x // size)
                ry = int(actor.location.y // size)
                rz = int(actor.location.z // size)
                dim = actor.location.dimension.name
                key = (rx, ry, rz, dim)
            except Exception:
                continue  # Actor was destroyed, skip
            
            if key not in regions:
                regions[key] = []
            regions[key].append(key)  # Store the key tuple, not the actor reference
        
        if not regions:
            sender.send_message(f'{ColorFormat.YELLOW}{lang["density_none"]}')
            return
        
        # Find densest region
        densest = max(regions.items(), key=lambda x: len(x[1]))
        key, entries = densest
        
        # Region midpoint from grid coordinates
        rx, ry, rz, dim = key
        hx = int((rx + 0.5) * size)
        hy = int((ry + 0.5) * size)
        hz = int((rz + 0.5) * size)
        
        entity_count = len(entries)
        
        sender.send_message(f'{ColorFormat.GREEN}━━━ {lang["density_results"]} ━━━')
        sender.send_message(f'{ColorFormat.YELLOW}{lang["density_dimension"]}: {dim}')
        sender.send_message(f'{ColorFormat.YELLOW}{lang["density_midpoint"]}: {hx}, {hy}, {hz}')
        sender.send_message(f'{ColorFormat.YELLOW}{lang["density_count"]}: {entity_count}')
    
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
        """Restore block state plus canonical container NBT/inventory snapshots."""
        try:
            flush_data_to_db()
        except Exception as error:
            self.logger.warning(f"[Rollback] Flush failed: {error}")

        time_threshold = now_est() - timedelta(hours=hours)
        radius_sq = radius ** 2
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            sql = """
                SELECT name, action, x, y, z, type, world, time, blockdata
                FROM interactions
                WHERE (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
                AND time >= ?
                AND (action LIKE '%Break%' OR action LIKE '%Place%' OR action LIKE '%Explode%')
            """
            params = [x, x, y, y, z, z, radius_sq, time_threshold.isoformat()]
            if player_filter:
                sql += " AND name LIKE ?"
                params.append(f"%{player_filter}%")
            sql += " ORDER BY time DESC"
            cur.execute(sql, params)
            rows = cur.fetchall()

        # The latest event for each world-position defines the state to undo.
        latest = {}
        for row in rows:
            key = (str(row[6]), int(row[2]), int(row[3]), int(row[4]))
            latest.setdefault(key, row)
        results = list(latest.values())

        if not results:
            filter_msg = f" for player '{player_filter}'" if player_filter else ""
            sender.send_message(
                f'{ColorFormat.YELLOW}No block changes found{filter_msg} in the specified area/time.'
            )
            return

        filter_msg = f" by '{player_filter}'" if player_filter else ""
        sender.send_message(
            f'{ColorFormat.GREEN}{lang["rollback_start"]} {len(results)} records'
            f'{filter_msg} in {radius} blocks, {hours} hours...'
        )

        count = 0
        errors = 0
        native_restores = 0
        skipped_types = set()

        for row in results:
            actor_name, action = str(row[0]), str(row[1])
            bx, by, bz = int(row[2]), int(row[3]), int(row[4])
            block_type_raw = str(row[5]) if row[5] else "air"
            dimension = str(row[6] or "overworld")
            blockdata_str = row[8] if row[8] else ""
            saved_data = {}
            if blockdata_str:
                try:
                    decoded = json.loads(blockdata_str)
                    if isinstance(decoded, dict):
                        saved_data = decoded
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            saved_snapshot = self._saved_native_snapshot(saved_data)
            if saved_snapshot:
                block_type_raw = str(saved_snapshot.get('type') or block_type_raw)
                states = saved_snapshot.get('states') or saved_data.get('block_states') or {}
            else:
                states = saved_data.get('block_states') or {}

            block_type = block_type_raw
            if "." in block_type and ":" not in block_type:
                block_type = "minecraft:" + block_type.split(".")[-1].lower()
            elif ":" not in block_type:
                block_type = "minecraft:" + block_type.lower()
            block_type = block_type.replace("<", "").replace(">", "").strip()
            if not block_type or block_type == "minecraft:none" or len(block_type) < 4:
                skipped_types.add(block_type_raw)
                errors += 1
                continue

            try:
                if "Break" in action or "Explode" in action:
                    states_arg = self._block_states_argument(states)
                    result = self._dispatch_in_dimension(
                        dimension,
                        f"setblock {bx} {by} {bz} {block_type}{states_arg} replace",
                    )
                    if not result:
                        self.logger.warning(
                            f"[Rollback] setblock failed at ({bx},{by},{bz}) "
                            f"in {dimension}: {block_type}{states_arg}"
                        )
                        errors += 1
                        continue
                    count += 1

                    if saved_snapshot and self.blockdata.is_container(saved_snapshot):
                        self._schedule_native_restore(
                            saved_snapshot, dimension, bx, by, bz, actor_name
                        )
                        native_restores += 1
                    elif saved_data.get('container_items'):
                        # Compatibility with pre-v1.5 behavior-pack backups.
                        items = saved_data['container_items']
                        for index in range(0, len(items), 6):
                            payload = json.dumps({
                                'x': bx, 'y': by, 'z': bz, 'dim': dimension,
                                'items': items[index:index + 6], 'clear': index == 0,
                            }, separators=(',', ':'))
                            self.server.dispatch_command(
                                self.server.command_sender,
                                f"scriptevent antigrief:container_restore {payload}",
                            )

                elif "Place" in action:
                    result = self._dispatch_in_dimension(
                        dimension, f"setblock {bx} {by} {bz} air replace"
                    )
                    if result:
                        count += 1
                    else:
                        errors += 1
                        self.logger.warning(
                            f"[Rollback] setblock air failed at ({bx},{by},{bz}) in {dimension}"
                        )
            except Exception as error:
                skipped_types.add(block_type_raw)
                errors += 1
                self.logger.warning(
                    f"Rollback error at {bx},{by},{bz} ({block_type}) in {dimension}: {error}"
                )

        result_msg = f'{ColorFormat.GREEN}Rollback complete: {count} blocks processed'
        if native_restores:
            result_msg += f', {native_restores} full-NBT container restores queued'
        if errors:
            result_msg += f' {ColorFormat.YELLOW}({errors} skipped)'
            if skipped_types:
                self.logger.warning(f"Rollback skipped block types: {list(skipped_types)[:5]}")
        sender.send_message(result_msg)

    # ========================================================================
    # EVENT HANDLERS
    # ========================================================================
    
    @event_handler
    def on_block_break(self, event: BlockBreakEvent):
        """Capture the live block/container before destruction and log the break."""
        if not RECORD_HUMAN and not RECORD_NATURE:
            return
        try:
            player = event.player
            block = event.block
            player_name = str(player.name)
            dimension = str(player.location.dimension.name)
            bx, by, bz = int(block.x), int(block.y), int(block.z)
            block_type_str = str(block.type)
        except (RuntimeError, SystemError, OSError):
            return

        if "." in block_type_str and ":" not in block_type_str:
            block_type_str = block_type_str.split(".")[-1].lower()

        try:
            saved_data = self._build_block_backup(
                block, dimension, player_name, 'block_break'
            )
            blockdata_json = json.dumps(
                saved_data, ensure_ascii=False, separators=(',', ':')
            ) if saved_data else ""
        except Exception as error:
            blockdata_json = ""
            self.logger.warning(
                f"Failed to capture block data at {bx},{by},{bz}: {error}"
            )

        data_buffers['break'].append({
            'name': player_name,
            'action': lang["action_break"],
            'coordinates': {'x': bx, 'y': by, 'z': bz},
            'type': block_type_str,
            'world': dimension,
            'time': now_est().isoformat(),
            'blockdata': blockdata_json,
        })

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
        
    
    def _snapshot_player_inventory(self, player_name):
        """Take a snapshot of a player's inventory as {item_type_str: count}.
        Accepts a player NAME (string) and re-resolves to avoid stale proxies."""
        try:
            player = self.server.get_player(player_name)
            if player is None:
                return None
            snapshot = {}
            for item in player.inventory.contents:
                if item is not None:
                    item_type = str(item.type)
                    snapshot[item_type] = snapshot.get(item_type, 0) + item.amount
            return snapshot
        except (RuntimeError, SystemError, OSError) as e:
            self.logger.warning(f"[Container] Inventory snapshot stale proxy for {player_name}: {e}")
            return None
        except Exception as e:
            self.logger.warning(f"[Container] Player inventory snapshot failed for {player_name}: {e}")
            return None

    def _diff_player_inventory(self, player_name):
        """Compare exact native container snapshots when the UI closes."""
        if player_name not in container_snapshots:
            return

        tracked = container_snapshots.pop(player_name)
        sx, sy, sz = tracked['x'], tracked['y'], tracked['z']
        stype = tracked['block_type']
        dimension = tracked['dimension']
        before_native = tracked.get('blockdata_snapshot')

        if before_native is not None and self._blockdata_ready:
            after_native, after_snapshot_id = self._capture_native_snapshot(
                dimension, sx, sy, sz, player_name, 'container_close', store=True
            )
            if after_native is None:
                return

            changes = self.blockdata.diff_inventory(before_native, after_native)
            before_entity = self.blockdata.block_entity(before_native) or {}
            after_entity = self.blockdata.block_entity(after_native) or {}
            before_snapshot_id = tracked.get('snapshot_id')
            event_time = now_est().isoformat()

            def actor_metadata(entity):
                value = dict(entity.get('nbt') or {})
                for key in ('Items', 'items', 'x', 'y', 'z'):
                    value.pop(key, None)
                return value

            for change in changes:
                item = change.get('after') or change.get('before') or {}
                item_type = self.blockdata.item_id(item)
                payload = {
                    'schema_version': BlockDataAdapter.SCHEMA_VERSION,
                    'provider': BlockDataAdapter.PROVIDER,
                    'container_type': stype,
                    'slot': change.get('slot'),
                    'item': item_type,
                    'amount': change.get('amount', 0),
                    'before_item': change.get('before'),
                    'after_item': change.get('after'),
                    'before_snapshot_id': before_snapshot_id,
                    'after_snapshot_id': after_snapshot_id,
                    'before_revision': before_native.get('revision'),
                    'after_revision': after_native.get('revision'),
                    'canonical_nbt': bool(
                        before_entity.get('canonical_nbt') or after_entity.get('canonical_nbt')
                    ),
                }
                data_buffers['container_access'].append({
                    'name': player_name,
                    'action': change['action'],
                    'coordinates': {'x': sx, 'y': sy, 'z': sz},
                    'type': item_type,
                    'world': dimension,
                    'time': event_time,
                    'blockdata': json.dumps(
                        payload, ensure_ascii=False, separators=(',', ':')
                    ),
                })

            before_metadata = actor_metadata(before_entity)
            after_metadata = actor_metadata(after_entity)
            metadata_changed = before_metadata != after_metadata
            if metadata_changed:
                data_buffers['container_access'].append({
                    'name': player_name,
                    'action': 'Container NBT Change',
                    'coordinates': {'x': sx, 'y': sy, 'z': sz},
                    'type': stype,
                    'world': dimension,
                    'time': event_time,
                    'blockdata': json.dumps({
                        'schema_version': BlockDataAdapter.SCHEMA_VERSION,
                        'provider': BlockDataAdapter.PROVIDER,
                        'container_type': stype,
                        'before_snapshot_id': before_snapshot_id,
                        'after_snapshot_id': after_snapshot_id,
                        'before_revision': before_native.get('revision'),
                        'after_revision': after_native.get('revision'),
                        'before_nbt': before_metadata,
                        'after_nbt': after_metadata,
                        'canonical_nbt': bool(
                            before_entity.get('canonical_nbt')
                            or after_entity.get('canonical_nbt')
                        ),
                    }, ensure_ascii=False, separators=(',', ':')),
                })

            if changes or metadata_changed:
                self.logger.info(
                    f"[Container] {player_name}: {len(changes)} exact slot changes, "
                    f"metadata_changed={metadata_changed} at {sx},{sy},{sz} ({stype})"
                )
            return

        # Legacy fallback when no native baseline was available.
        old_snapshot = tracked.get('snapshot')
        if old_snapshot is None:
            return
        new_snapshot = self._snapshot_player_inventory(player_name)
        if new_snapshot is None:
            return
        all_items = set(old_snapshot) | set(new_snapshot)
        changes = 0
        for item_type in all_items:
            diff = new_snapshot.get(item_type, 0) - old_snapshot.get(item_type, 0)
            if diff == 0:
                continue
            action = "Container Take" if diff > 0 else "Container Add"
            amount = abs(diff)
            data_buffers['container_access'].append({
                'name': player_name,
                'action': action,
                'coordinates': {'x': sx, 'y': sy, 'z': sz},
                'type': item_type,
                'world': dimension,
                'time': now_est().isoformat(),
                'blockdata': json.dumps({
                    'schema_version': 1,
                    'provider': 'legacy-player-inventory-diff',
                    'container_type': stype,
                    'item': item_type,
                    'amount': amount,
                }, separators=(',', ':')),
            })
            changes += 1
        if changes:
            self.logger.info(
                f"[Container] {player_name}: {changes} legacy item changes "
                f"at {sx},{sy},{sz} ({stype})"
            )

    @event_handler
    def on_packet_receive(self, event: PacketReceiveEvent):
        """Intercept ContainerClose packets to detect when a player closes a container UI."""
        # ContainerClose packet id = 47
        if event.packet_id != 47:
            return

        # Extract the player name (primitive) immediately to avoid stale proxy later
        try:
            player = event.player
            if player is None:
                return
            player_name = str(player.name)
        except (RuntimeError, SystemError, OSError):
            return  # Stale proxy — player already disconnected

        def capture_after_close():
            try:
                self._diff_player_inventory(player_name)
            except Exception as error:
                self.logger.warning(
                    f"[Container] Close diff error for {player_name}: {error}"
                )
                container_snapshots.pop(player_name, None)

        # PacketReceiveEvent can arrive before the inventory transaction is fully
        # committed. Capture one server tick later to get the authoritative state.
        try:
            self.server.scheduler.run_task(self, capture_after_close, delay=1)
        except Exception:
            capture_after_close()

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent):
        """Log player interaction events and track container access"""
        # Extract all primitives from C++ proxies immediately to avoid stale access
        try:
            player = event.player
            block = event.block
            if not block:
                return
            player_name = str(player.name)
            block_type_str = str(block.type)
            bx, by, bz = block.x, block.y, block.z
            dim_name = player.location.dimension.name
        except (RuntimeError, SystemError, OSError):
            return  # Stale proxy — skip silently
        
        if block_type_str == "minecraft:air":
            return
        
        # Skip logging interactions that are actually block placements.
        # When a player places a block, both InteractEvent and PlaceEvent fire.
        # We suppress the interact if this block+player was just logged as a placement.
        if hasattr(self, '_recent_placements'):
            now = tm.time()
            # Clean old entries (> 2 seconds)
            expired = [k for k, v in self._recent_placements.items() if now - v > 2]
            for k in expired:
                del self._recent_placements[k]
            # Check if this player just placed a block within 0.5 seconds
            for key, place_time in self._recent_placements.items():
                if key.startswith(f"{player_name}:") and now - place_time < 0.5:
                    return  # Suppress this interact — it's from a block placement
        
        # Capture the actual container actor, every occupied slot, and canonical NBT.
        if (block_type_str in CONTAINER_BLOCKS or block_type_str.endswith("_shulker_box")) and CAPTURE_CONTAINER_OPEN_CLOSE:
            native_snapshot, snapshot_id = self._capture_native_snapshot(
                dim_name, bx, by, bz, player_name, 'container_open', store=True
            )
            if native_snapshot is not None and self.blockdata.is_container(native_snapshot):
                container_snapshots[player_name] = {
                    'x': bx, 'y': by, 'z': bz,
                    'block_type': block_type_str,
                    'dimension': dim_name,
                    'blockdata_snapshot': native_snapshot,
                    'snapshot_id': snapshot_id,
                    'time': tm.time(),
                }
                self.logger.info(
                    f"[Container] Native tracking open: {player_name} @ "
                    f"{bx},{by},{bz} ({block_type_str})"
                )
            else:
                legacy_snapshot = self._snapshot_player_inventory(player_name)
                if legacy_snapshot is not None:
                    container_snapshots[player_name] = {
                        'x': bx, 'y': by, 'z': bz,
                        'block_type': block_type_str,
                        'dimension': dim_name,
                        'snapshot': legacy_snapshot,
                        'time': tm.time(),
                    }

        # Log the interaction
        data_buffers['chest'].append({
            'name': player_name,
            'action': lang["action_interact"],
            'coordinates': {'x': bx, 'y': by, 'z': bz},
            'type': block_type_str,
            'world': dim_name,
            'time': now_est().isoformat()
        })
        
    
    
    @event_handler
    def on_actor_knockback(self, event: ActorKnockbackEvent):
        """Log entity damage events"""
        # Extract ALL primitives from C++ proxies in one guarded block
        try:
            actor_type = str(event.actor.type)
            if ONLY_IMPORTANT_ANIMAL:
                important = ["minecraft:horse", "minecraft:pig", "minecraft:wolf", 
                            "minecraft:cat", "minecraft:sniffer", "minecraft:parrot",
                            "minecraft:donkey", "minecraft:mule", "minecraft:villager"]
                if actor_type not in important:
                    return
            
            ax = int(event.actor.location.x)
            ay = int(event.actor.location.y)
            az = int(event.actor.location.z)
            dim_name = event.actor.location.dimension.name
            source = event.source if hasattr(event, 'source') else None
            source_name = source.name if source and hasattr(source, 'name') else "Unknown"
        except (RuntimeError, SystemError, OSError):
            return  # Actor/source proxy is stale — silently skip
        except Exception:
            return  # Any other proxy access failure
        
        data_buffers['animal'].append({
            'name': source_name,
            'action': lang["action_attack"],
            'coordinates': {'x': ax, 'y': ay, 'z': az},
            'type': actor_type,
            'world': dim_name,
            'time': now_est().isoformat()
        })
    
    @event_handler
    def on_explosion(self, event: ActorExplodeEvent):
        """Capture every affected block and container before explosion damage."""
        try:
            actor = event.actor
            actor_type = str(actor.type) if hasattr(actor, 'type') else "explosion"
            actor_dim = str(actor.location.dimension.name) if hasattr(actor, 'location') else "overworld"
        except (RuntimeError, SystemError, OSError):
            return
        except Exception:
            actor_type = "explosion"
            actor_dim = "overworld"

        if hasattr(event, 'block_list') and event.block_list:
            for block in event.block_list:
                try:
                    block_type_str = str(block.type)
                    if "." in block_type_str and ":" not in block_type_str:
                        block_type_str = block_type_str.split(".")[-1].lower()
                    bx, by, bz = int(block.x), int(block.y), int(block.z)
                    saved_data = self._build_block_backup(
                        block, actor_dim, actor_type, 'explosion'
                    )
                    blockdata_json = json.dumps(
                        saved_data, ensure_ascii=False, separators=(',', ':')
                    ) if saved_data else ""
                except (RuntimeError, SystemError, OSError):
                    continue
                except Exception as error:
                    self.logger.warning(f"[Explosion] Snapshot failed: {error}")
                    continue

                data_buffers['bomb'].append({
                    'name': actor_type,
                    'action': lang["action_explode"],
                    'coordinates': {'x': bx, 'y': by, 'z': bz},
                    'type': block_type_str,
                    'world': actor_dim,
                    'time': now_est().isoformat(),
                    'blockdata': blockdata_json,
                })
            return

        try:
            cx = int(actor.location.x)
            cy = int(actor.location.y)
            cz = int(actor.location.z)
        except (RuntimeError, SystemError, OSError, AttributeError):
            return
        data_buffers['bomb'].append({
            'name': actor_type,
            'action': lang["action_explode"],
            'coordinates': {'x': cx, 'y': cy, 'z': cz},
            'type': "explosion",
            'world': actor_dim,
            'time': now_est().isoformat(),
            'blockdata': "",
        })

    @event_handler
    def on_script_message(self, event: ScriptMessageEvent):
        """Handle incoming script messages from the Bedrock Script API behavior pack."""
        msg_id = event.message_id
        if msg_id == "antigrief:container_backup":
            try:
                payload = json.loads(event.message)
                x = int(payload.get("x"))
                y = int(payload.get("y"))
                z = int(payload.get("z"))
                dim = str(payload.get("dim"))
                items = payload.get("items", [])
                
                # Cache the backup
                self._container_backups[(x, y, z, dim)] = items
                self.logger.info(f"[AntiGrief] Cached {len(items)} items for container at {x},{y},{z} in {dim}")
            except Exception as e:
                self.logger.warning(f"[AntiGrief] Failed to parse container backup payload: {e}")

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

"""
AntiGrief Plugin v1.5.13 - BlockData Edition
Player behavior logging, analysis, and WebUI dashboard for Endstone
"""

from endstone.command import Command, CommandSender
from endstone.plugin import Plugin
from endstone import ColorFormat, Player
from endstone.event import (
    event_handler, BlockBreakEvent, PlayerInteractEvent, ActorKnockbackEvent,
    BlockPlaceEvent, PlayerCommandEvent, PlayerJoinEvent, PlayerQuitEvent, PlayerChatEvent,
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
import hashlib

# Bedrock protocol packet decoding for container item tracking
try:
    from bedrock_protocol.packets import MinecraftPackets, MinecraftPacketIds
    HAS_PACKET_LIB = True
except ImportError:
    HAS_PACKET_LIB = False
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
from copy import deepcopy
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

PLUGIN_VERSION = "v1.5.13"
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
    "store_raw_snbt": True,
    "container_ownership_enabled": True,
    "auto_confiscate_unauthorized_container_theft": False,
    "recover_stolen_items_on_rollback": True,
    "confiscation_allow_type_fallback_for_tagged_items": True,
    "confiscation_sweep_ticks": 200,
    "capture_player_inventories": True,
    "player_inventory_capture_ticks": 100,
    "player_inventory_capture_batch_size": 20,
    "player_inventory_decode_warning_cooldown_seconds": 300,
    "blockdata_connect_retry_ticks": 20,
    "blockdata_connect_log_every": 10
}

def load_config():
    """Load or create configuration file"""
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG.copy()
    
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # Migrate missing keys from defaults. Automatic confiscation merely from
    # opening or changing a container is permanently disabled in v1.5.6.
    updated = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
            updated = True
    if config.get("auto_confiscate_unauthorized_container_theft") is not False:
        config["auto_confiscate_unauthorized_container_theft"] = False
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
CONTAINER_OWNERSHIP_ENABLED = config.get("container_ownership_enabled", True)
AUTO_CONFISCATE = False  # Deprecated: evidence alone never removes player items.
ROLLBACK_RECOVERY_ENABLED = config.get("recover_stolen_items_on_rollback", True)
CONFISCATION_TYPE_FALLBACK = config.get("confiscation_allow_type_fallback_for_tagged_items", True)
CONFISCATION_SWEEP_TICKS = max(20, int(config.get("confiscation_sweep_ticks", 200)))
CAPTURE_PLAYER_INVENTORIES = bool(config.get("capture_player_inventories", True))
PLAYER_INVENTORY_CAPTURE_TICKS = max(20, int(config.get("player_inventory_capture_ticks", 100)))
PLAYER_INVENTORY_CAPTURE_BATCH_SIZE = max(1, int(config.get("player_inventory_capture_batch_size", 20)))
PLAYER_INVENTORY_DECODE_WARNING_COOLDOWN = max(10, int(config.get("player_inventory_decode_warning_cooldown_seconds", 300)))
BLOCKDATA_CONNECT_RETRY_TICKS = max(1, int(config.get("blockdata_connect_retry_ticks", 20)))
BLOCKDATA_CONNECT_LOG_EVERY = max(1, int(config.get("blockdata_connect_log_every", 10)))



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
        revision_text TEXT,
        captured_at TEXT,
        occupied_slots INTEGER DEFAULT 0,
        item_count INTEGER DEFAULT 0,
        canonical_nbt INTEGER DEFAULT 0,
        snapshot_json TEXT NOT NULL,
        raw_snbt TEXT
    )
    """)
    # v1.5.3: BlockData revisions are unsigned 64-bit fingerprints and can exceed
    # SQLite's signed INTEGER range. Preserve the exact value in TEXT while keeping
    # the legacy INTEGER column for older dashboards and databases.
    cursor.execute("PRAGMA table_info(container_snapshots)")
    snapshot_columns = {col[1] for col in cursor.fetchall()}
    if 'revision_text' not in snapshot_columns:
        cursor.execute("ALTER TABLE container_snapshots ADD COLUMN revision_text TEXT")
        cursor.execute(
            "UPDATE container_snapshots SET revision_text = CAST(revision AS TEXT) "
            "WHERE revision_text IS NULL AND revision IS NOT NULL"
        )

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interactions_time ON interactions(time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interactions_position ON interactions(world, x, y, z)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_container_snapshots_time ON container_snapshots(captured_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_container_snapshots_position ON container_snapshots(world, x, y, z)")

    # v1.5.4: persistent ownership and exact stolen-item recovery queue.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS container_owners (
        world TEXT NOT NULL,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        z INTEGER NOT NULL,
        owner_name TEXT NOT NULL,
        source TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (world, x, y, z)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS container_trusted (
        world TEXT NOT NULL,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        z INTEGER NOT NULL,
        player_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (world, x, y, z, player_name)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pending_confiscations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        theft_key TEXT NOT NULL UNIQUE,
        player_name TEXT NOT NULL,
        owner_name TEXT,
        world TEXT NOT NULL,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        z INTEGER NOT NULL,
        item_json TEXT NOT NULL,
        requested_amount INTEGER NOT NULL,
        removed_amount INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
    cursor.execute("PRAGMA table_info(pending_confiscations)")
    pending_columns = {col[1] for col in cursor.fetchall()}
    pending_migrations = {
        "destination_slot": "INTEGER",
        "rollback_id": "TEXT",
        "returned_amount": "INTEGER NOT NULL DEFAULT 0",
        "trigger_action": "TEXT NOT NULL DEFAULT 'agback'",
    }
    for column_name, column_type in pending_migrations.items():
        if column_name not in pending_columns:
            cursor.execute(
                f"ALTER TABLE pending_confiscations ADD COLUMN {column_name} {column_type}"
            )
    # v1.5.4-v1.5.5 could queue removals solely because ownership inferred that a
    # friend was unauthorized. Those records must never execute after upgrading.
    cursor.execute(
        """UPDATE pending_confiscations
           SET status='cancelled', updated_at=CURRENT_TIMESTAMP
           WHERE status='pending'
             AND (rollback_id IS NULL OR reason NOT LIKE 'rollback_recovery:%')"""
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_confiscations_player ON pending_confiscations(player_name, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_confiscations_rollback ON pending_confiscations(rollback_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_container_owners_owner ON container_owners(owner_name)")

    # v1.5.8: latest live player inventory, equipment, Ender Chest, and storage-item snapshots.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS player_inventory_snapshots (
        player_key TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL UNIQUE,
        player_name TEXT NOT NULL,
        xuid TEXT,
        captured_at TEXT NOT NULL,
        online INTEGER NOT NULL DEFAULT 1,
        revision INTEGER,
        revision_text TEXT,
        selected_hotbar_slot INTEGER NOT NULL DEFAULT 0,
        main_size INTEGER NOT NULL DEFAULT 0,
        armor_size INTEGER NOT NULL DEFAULT 4,
        offhand_size INTEGER NOT NULL DEFAULT 1,
        ender_chest_size INTEGER NOT NULL DEFAULT 0,
        occupied_main INTEGER NOT NULL DEFAULT 0,
        occupied_armor INTEGER NOT NULL DEFAULT 0,
        occupied_offhand INTEGER NOT NULL DEFAULT 0,
        occupied_ender_chest INTEGER NOT NULL DEFAULT 0,
        item_count INTEGER NOT NULL DEFAULT 0,
        storage_item_count INTEGER NOT NULL DEFAULT 0,
        snapshot_json TEXT NOT NULL
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_player_inventory_name "
        "ON player_inventory_snapshots(player_name)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_player_inventory_captured "
        "ON player_inventory_snapshots(captured_at)"
    )

    # v1.5.8: immutable, printable evidence reports generated by /agback.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS grief_reports (
        report_id TEXT PRIMARY KEY,
        rollback_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        admin_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'processing',
        center_x INTEGER NOT NULL,
        center_y INTEGER NOT NULL,
        center_z INTEGER NOT NULL,
        radius REAL NOT NULL,
        hours REAL NOT NULL,
        player_filter TEXT,
        primary_player TEXT,
        event_count INTEGER NOT NULL DEFAULT 0,
        affected_positions INTEGER NOT NULL DEFAULT 0,
        blocks_broken INTEGER NOT NULL DEFAULT 0,
        blocks_placed INTEGER NOT NULL DEFAULT 0,
        explosions INTEGER NOT NULL DEFAULT 0,
        containers_looted INTEGER NOT NULL DEFAULT 0,
        containers_broken INTEGER NOT NULL DEFAULT 0,
        items_reported INTEGER NOT NULL DEFAULT 0,
        items_recovered INTEGER NOT NULL DEFAULT 0,
        evidence_hash TEXT NOT NULL,
        players_json TEXT NOT NULL,
        worlds_json TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        report_json TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grief_reports_created ON grief_reports(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grief_reports_player ON grief_reports(primary_player)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grief_reports_status ON grief_reports(status)")
    
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
    'container_snapshot': [],
    'player_inventory_snapshot': [],
    'player_inventory_presence': []
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

def _sqlite_signed_int(value):
    """Return value only when it fits SQLite's signed 64-bit INTEGER range."""
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if -(1 << 63) <= parsed <= (1 << 63) - 1 else None


def insert_container_snapshots(records):
    """Insert full canonical BlockData snapshots without truncating u64 revisions."""
    if not records:
        return
    with sqlite3.connect(DB_FILE) as db:
        db.execute("PRAGMA busy_timeout=5000")
        cur = db.cursor()
        for record in records:
            revision = record.get('revision')
            revision_text = None if revision is None else str(revision)
            cur.execute("""
                INSERT OR REPLACE INTO container_snapshots (
                    snapshot_id, player_name, reason, x, y, z, world, block_type,
                    revision, revision_text, captured_at, occupied_slots, item_count,
                    canonical_nbt, snapshot_json, raw_snbt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record['snapshot_id'], record.get('player_name'), record.get('reason'),
                record['x'], record['y'], record['z'], record['world'],
                record.get('block_type'), _sqlite_signed_int(revision), revision_text,
                record['captured_at'], record.get('occupied_slots', 0),
                record.get('item_count', 0), 1 if record.get('canonical_nbt') else 0,
                record['snapshot_json'], record.get('raw_snbt')
            ))
        db.commit()



def insert_player_inventory_snapshots(records):
    """Upsert the latest exact live inventory snapshot for each player."""
    if not records:
        return
    with sqlite3.connect(DB_FILE) as db:
        db.execute("PRAGMA busy_timeout=5000")
        cur = db.cursor()
        for record in records:
            revision = record.get('revision')
            cur.execute("""
                INSERT OR REPLACE INTO player_inventory_snapshots (
                    player_key, snapshot_id, player_name, xuid, captured_at, online,
                    revision, revision_text, selected_hotbar_slot,
                    main_size, armor_size, offhand_size, ender_chest_size,
                    occupied_main, occupied_armor, occupied_offhand,
                    occupied_ender_chest, item_count, storage_item_count, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record['player_key'], record['snapshot_id'], record['player_name'],
                record.get('xuid'), record['captured_at'], 1 if record.get('online', True) else 0,
                _sqlite_signed_int(revision), None if revision is None else str(revision),
                record.get('selected_hotbar_slot', 0),
                record.get('main_size', 0), record.get('armor_size', 4),
                record.get('offhand_size', 1), record.get('ender_chest_size', 0),
                record.get('occupied_main', 0), record.get('occupied_armor', 0),
                record.get('occupied_offhand', 0), record.get('occupied_ender_chest', 0),
                record.get('item_count', 0), record.get('storage_item_count', 0),
                record['snapshot_json'],
            ))
        db.commit()


def update_player_inventory_presence(records):
    """Update online/offline state without replacing the last captured inventory."""
    if not records:
        return
    with sqlite3.connect(DB_FILE) as db:
        db.execute("PRAGMA busy_timeout=5000")
        for record in records:
            db.execute(
                "UPDATE player_inventory_snapshots SET online = ? WHERE player_key = ?",
                (1 if record.get('online') else 0, record['player_key']),
            )
        db.commit()


def _take_buffer(name):
    """Atomically swap a live append buffer so new events cannot be cleared."""
    with buffer_lock:
        records = data_buffers[name]
        data_buffers[name] = []
        return records


def _requeue_buffer(name, records):
    if not records:
        return
    with buffer_lock:
        data_buffers[name] = records + data_buffers[name]


def flush_data_to_db():
    """Write buffered data to the database while preserving failed batches."""
    global is_cleaning

    with buffer_lock:
        if is_cleaning:
            return

    jobs = (
        ('place', lambda rows: insert_records(rows)),
        ('chest', lambda rows: insert_records(rows, has_blockdata=True)),
        ('break', lambda rows: insert_records(rows, has_blockdata=True)),
        ('animal', lambda rows: insert_records(rows)),
        ('bomb', lambda rows: insert_records(rows, has_blockdata=True)),
        ('container_access', lambda rows: insert_records(rows, has_blockdata=True)),
        ('container_snapshot', insert_container_snapshots),
        ('player_inventory_snapshot', insert_player_inventory_snapshots),
        ('player_inventory_presence', update_player_inventory_presence),
    )
    with db_write_lock:
        for name, writer in jobs:
            records = _take_buffer(name)
            if not records:
                continue
            try:
                writer(records)
            except Exception:
                _requeue_buffer(name, records)
                raise


def periodic_writer():
    """Background writer that survives one malformed record or transient DB error."""
    while True:
        try:
            flush_data_to_db()
        except Exception as error:
            print(f"[Antigrief] [Database] Periodic flush failed; will retry: {error}")
            traceback.print_exc()
        tm.sleep(20)

# Start background writer thread
writer_thread = threading.Thread(target=periodic_writer, daemon=True)
writer_thread.start()

# ============================================================================
# PLUGIN CLASS
# ============================================================================

class AntiGriefPlugin(Plugin):
    api_version = "0.11"
    version = "1.5.13"
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
        "agowner": {
            "description": "Set, inspect, trust, or clear a container owner",
            "usages": ["/agowner <action:str> [pos:pos] [player:str]"],
            "permissions": ["antigrief.command.op"],
        },
        "agconfiscate": {
            "description": "Retry item recovery from an administrator-confirmed rollback",
            "usages": ["/agconfiscate <player:str>"],
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
        self._last_player_inventory_revisions = {}
        self._player_inventory_keys_by_name = {}
        self._player_inventory_cursor = 0
        self._player_inventory_sweeper_started = False
        self._player_inventory_capture_warning_times = {}
        self._player_inventory_degraded_players = set()
        self._blockdata_retry_scheduled = False
        self._blockdata_connect_attempts = 0
        self._blockdata_connected_once = False
        self._shutting_down = False
        self.logger.info("AntiGrief Plugin loading...")

    def _connect_blockdata_services(self, *, initial=False) -> bool:
        """Connect after blockdata_api has enabled and registered its services.

        Endstone can construct/load Python plugins before a native dependency has
        finished ``onEnable``. The hard dependency remains declared, but service
        readiness is the real boundary for BlockData calls.
        """
        if self._blockdata_ready:
            return True

        self._blockdata_connect_attempts += 1
        self._blockdata_ready = self.blockdata.connect(self.server)
        if not self._blockdata_ready:
            if initial or self._blockdata_connect_attempts % BLOCKDATA_CONNECT_LOG_EVERY == 0:
                level = self.logger.warning if REQUIRE_BLOCKDATA else self.logger.info
                level(
                    f'{ColorFormat.YELLOW}  Waiting for blockdata_api services: '
                    f'{self.blockdata.error} (attempt {self._blockdata_connect_attempts})'
                )
            return False

        self._blockdata_retry_scheduled = False
        adapter = self.blockdata.capabilities.get("adapter", "unknown")
        reconnect_word = "reconnected" if self._blockdata_connected_once else "connected"
        self._blockdata_connected_once = True
        self.logger.info(
            f'{ColorFormat.GREEN}  BlockData API {reconnect_word}: adapter={adapter}, '
            f'capabilities={self.blockdata.capabilities}'
        )

        if self.blockdata.player_inventory_available:
            self.logger.info(
                f'{ColorFormat.GREEN}  Player Inventory API connected: '
                f'{self.blockdata.player_inventory_capabilities}'
            )
            self._start_player_inventory_snapshot_sweeper()
            for player in list(self.server.online_players):
                try:
                    self.server.scheduler.run_task(
                        self,
                        lambda current=player: self._capture_player_inventory(current, force=True),
                        delay=20,
                    )
                except Exception:
                    pass
        elif CAPTURE_PLAYER_INVENTORIES:
            self.logger.warning(
                f'{ColorFormat.YELLOW}  Player Inventory API unavailable: '
                f'{self.blockdata.player_inventory_error}'
            )
        return True

    def _schedule_blockdata_connect_retry(self, *, delay=None) -> None:
        if self._shutting_down or self._blockdata_ready or self._blockdata_retry_scheduled:
            return
        self._blockdata_retry_scheduled = True
        retry_delay = BLOCKDATA_CONNECT_RETRY_TICKS if delay is None else max(1, int(delay))

        def retry_connection():
            self._blockdata_retry_scheduled = False
            if self._shutting_down or self._blockdata_ready:
                return
            if not self._connect_blockdata_services():
                self._schedule_blockdata_connect_retry()

        try:
            self.server.scheduler.run_task(self, retry_connection, delay=retry_delay)
        except Exception as error:
            self._blockdata_retry_scheduled = False
            self.logger.warning(
                f"[BlockData] Could not schedule dependency reconnect: {error}"
            )

    def _ensure_blockdata_ready(self) -> bool:
        if self._blockdata_ready:
            return True
        if self._connect_blockdata_services():
            return True
        self._schedule_blockdata_connect_retry()
        return False
    
    def on_enable(self) -> None:
        self.logger.info(f'{ColorFormat.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        self.logger.info(f'{ColorFormat.GREEN}  AntiGrief Plugin {PLUGIN_VERSION}')
        self.logger.info(f'{ColorFormat.YELLOW}  Player Behavior Logging & Analysis')
        self.logger.info(f'{ColorFormat.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        self.logger.info(f'{ColorFormat.AQUA}  Config: {CONFIG_FILE}')
        self.logger.info(f'{ColorFormat.AQUA}  Data: {DATA_DIR}/')
        
        if not self._connect_blockdata_services(initial=True):
            self.logger.warning(
                f'{ColorFormat.YELLOW}  Native BlockData features are paused until '
                'blockdata_api finishes enabling; AntiGrief will reconnect automatically.'
            )
            self._schedule_blockdata_connect_retry()

        try:
            with sqlite3.connect(DB_FILE) as db:
                db.execute("UPDATE player_inventory_snapshots SET online = 0")
                db.commit()
        except sqlite3.Error as error:
            self.logger.warning(f"[PlayerInventory] Could not reset presence state: {error}")

        # Start WebUI even when the bridge is unavailable so historical records remain viewable.
        if ENABLE_WEBUI:
            self._start_webui()
        
        self.register_events(self)
        self._start_confiscation_sweeper()
        self._start_player_inventory_snapshot_sweeper()
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
        self._shutting_down = True
        self._blockdata_retry_scheduled = False
        flush_data_to_db()
        try:
            self.server.scheduler.cancel_tasks(self)
        except Exception:
            pass
        try:
            with sqlite3.connect(DB_FILE) as db:
                db.execute("UPDATE player_inventory_snapshots SET online = 0")
                db.commit()
        except sqlite3.Error:
            pass
        self.logger.info("AntiGrief Plugin disabled, data saved.")

    # ========================================================================
    # LIVE PLAYER INVENTORY / ENDER CHEST SNAPSHOTS
    # ========================================================================

    @staticmethod
    def _player_inventory_key(player_or_snapshot):
        if isinstance(player_or_snapshot, dict):
            xuid = str(player_or_snapshot.get('xuid') or '').strip()
            name = str(player_or_snapshot.get('player_name') or 'unknown')
        else:
            xuid = str(getattr(player_or_snapshot, 'xuid', '') or '').strip()
            name = str(getattr(player_or_snapshot, 'name', 'unknown'))
        return f"xuid:{xuid}" if xuid else f"name:{name.casefold()}"

    def _queue_player_inventory_snapshot(self, snapshot, *, force=False):
        if not isinstance(snapshot, dict):
            return None
        summary = self.blockdata.player_inventory_summary(snapshot)
        player_key = self._player_inventory_key(snapshot)
        revision_text = str(snapshot.get('revision'))
        if not force and self._last_player_inventory_revisions.get(player_key) == revision_text:
            return None
        self._last_player_inventory_revisions[player_key] = revision_text
        self._player_inventory_keys_by_name[summary['player_name'].casefold()] = player_key
        sections = summary['sections']
        snapshot_id = uuid4().hex
        data_buffers['player_inventory_snapshot'].append({
            'player_key': player_key,
            'snapshot_id': snapshot_id,
            'player_name': summary['player_name'],
            'xuid': summary['xuid'],
            'captured_at': now_est().isoformat(),
            'online': True,
            'revision': snapshot.get('revision'),
            'selected_hotbar_slot': summary['selected_hotbar_slot'],
            'main_size': sections['main']['capacity'],
            'armor_size': sections['armor']['capacity'],
            'offhand_size': sections['offhand']['capacity'],
            'ender_chest_size': sections['ender_chest']['capacity'],
            'occupied_main': sections['main']['occupied_slots'],
            'occupied_armor': sections['armor']['occupied_slots'],
            'occupied_offhand': sections['offhand']['occupied_slots'],
            'occupied_ender_chest': sections['ender_chest']['occupied_slots'],
            'item_count': summary['total_item_count'],
            'storage_item_count': summary['storage_item_count'],
            'snapshot_json': json.dumps(
                self.blockdata.json_safe(snapshot), ensure_ascii=False,
                separators=(',', ':'),
            ),
        })
        return snapshot_id

    def _capture_player_inventory(self, player, *, force=False):
        if not (
            CAPTURE_PLAYER_INVENTORIES
            and self._blockdata_ready
            and self.blockdata.player_inventory_available
        ):
            return None
        player_name = str(getattr(player, 'name', 'unknown'))
        player_key = self._player_inventory_key(player)
        try:
            snapshot = self.blockdata.capture_player_inventory(self.server, player)
            if snapshot is None:
                return None
            if player_key in self._player_inventory_degraded_players:
                self._player_inventory_degraded_players.discard(player_key)
                self._player_inventory_capture_warning_times.pop(player_key, None)
                self.logger.info(
                    f"[PlayerInventory] Exact canonical NBT capture recovered for {player_name}."
                )
            self._queue_player_inventory_snapshot(snapshot, force=force)
            return snapshot
        except UnicodeDecodeError as error:
            warning = (
                "Native BlockData encountered malformed non-UTF-8 text in one item; "
                "this snapshot uses Endstone's readable public inventory fallback. "
                f"Exact raw NBT for the affected field is unavailable: {error}"
            )
            try:
                snapshot = self.blockdata.public_player_inventory_snapshot(
                    player, warning=warning
                )
                self._queue_player_inventory_snapshot(snapshot, force=force)
            except Exception as fallback_error:
                snapshot = None
                warning += f"; public fallback also failed: {fallback_error}"

            now = tm.monotonic()
            last_warning = self._player_inventory_capture_warning_times.get(player_key, 0.0)
            if now - last_warning >= PLAYER_INVENTORY_DECODE_WARNING_COOLDOWN:
                self._player_inventory_capture_warning_times[player_key] = now
                outcome = "stored a degraded readable snapshot" if snapshot else "could not store a fallback snapshot"
                self.logger.warning(
                    f"[PlayerInventory] UTF-8 NBT decode failure for {player_name}; {outcome}. "
                    f"Further identical warnings are suppressed for "
                    f"{PLAYER_INVENTORY_DECODE_WARNING_COOLDOWN}s. {error}"
                )
            self._player_inventory_degraded_players.add(player_key)
            return snapshot
        except (BlockDataUnavailable, RuntimeError, SystemError, OSError) as error:
            self.logger.warning(
                f"[PlayerInventory] Capture failed for {player_name}: {error}"
            )
            return None
        except Exception as error:
            self.logger.warning(
                f"[PlayerInventory] Unexpected capture failure for {player_name}: {error}"
            )
            return None

    def _start_player_inventory_snapshot_sweeper(self):
        if self._player_inventory_sweeper_started:
            return
        if not (
            CAPTURE_PLAYER_INVENTORIES
            and self._blockdata_ready
            and self.blockdata.player_inventory_available
        ):
            return
        self._player_inventory_sweeper_started = True

        def sweep():
            players = list(self.server.online_players)
            if not players:
                self._player_inventory_cursor = 0
                return
            batch_size = min(PLAYER_INVENTORY_CAPTURE_BATCH_SIZE, len(players))
            start = self._player_inventory_cursor % len(players)
            for offset in range(batch_size):
                player = players[(start + offset) % len(players)]
                self._capture_player_inventory(player)
            self._player_inventory_cursor = (start + batch_size) % len(players)

        try:
            self.server.scheduler.run_task(
                self,
                sweep,
                delay=20,
                period=PLAYER_INVENTORY_CAPTURE_TICKS,
            )
        except Exception:
            self._player_inventory_sweeper_started = False
            raise

    def _mark_player_inventory_offline(self, player):
        name = str(getattr(player, 'name', 'unknown'))
        player_key = self._player_inventory_keys_by_name.get(
            name.casefold(), self._player_inventory_key(player)
        )
        data_buffers['player_inventory_presence'].append({
            'player_key': player_key,
            'online': False,
        })

    # ========================================================================
    # CONTAINER OWNERSHIP & STOLEN-ITEM RECOVERY
    # ========================================================================

    @staticmethod
    def _normalise_world_key(world):
        value = str(world or "overworld").strip().casefold().replace("minecraft:", "")
        value = value.replace(" ", "_")
        return {"end": "the_end", "the_nether": "nether"}.get(value, value)

    @classmethod
    def _container_position_key(cls, world, x, y, z):
        return cls._normalise_world_key(world), int(x), int(y), int(z)

    @staticmethod
    def _is_container_block_type(block_type):
        value = str(block_type or "").casefold().strip()
        if "." in value and ":" not in value:
            value = value.split(".")[-1]
        if value and ":" not in value:
            value = "minecraft:" + value
        return value in CONTAINER_BLOCKS or value.endswith("_shulker_box")

    def _set_container_owner(self, world, x, y, z, owner_name, source="placement"):
        if not CONTAINER_OWNERSHIP_ENABLED or not owner_name:
            return
        key = self._container_position_key(world, x, y, z)
        now = now_est().isoformat()
        with sqlite3.connect(DB_FILE) as db:
            db.execute(
                """INSERT INTO container_owners
                   (world,x,y,z,owner_name,source,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(world,x,y,z) DO UPDATE SET
                     owner_name=excluded.owner_name,
                     source=excluded.source,
                     updated_at=excluded.updated_at""",
                (*key, str(owner_name), str(source), now, now),
            )
            db.commit()

    def _clear_container_owner(self, world, x, y, z):
        key = self._container_position_key(world, x, y, z)
        with sqlite3.connect(DB_FILE) as db:
            db.execute("DELETE FROM container_trusted WHERE world=? AND x=? AND y=? AND z=?", key)
            db.execute("DELETE FROM container_owners WHERE world=? AND x=? AND y=? AND z=?", key)
            db.commit()

    def _set_container_trust(self, world, x, y, z, player_name, trusted=True):
        key = self._container_position_key(world, x, y, z)
        with sqlite3.connect(DB_FILE) as db:
            if trusted:
                db.execute(
                    """INSERT OR REPLACE INTO container_trusted
                       (world,x,y,z,player_name,created_at) VALUES (?,?,?,?,?,?)""",
                    (*key, str(player_name), now_est().isoformat()),
                )
            else:
                db.execute(
                    """DELETE FROM container_trusted
                       WHERE world=? AND x=? AND y=? AND z=? AND lower(player_name)=lower(?)""",
                    (*key, str(player_name)),
                )
            db.commit()

    def _infer_container_owner_from_logs(self, world, x, y, z):
        normal_world = self._normalise_world_key(world)
        with sqlite3.connect(DB_FILE) as db:
            rows = db.execute(
                """SELECT name,world,type FROM interactions
                   WHERE x=? AND y=? AND z=? AND action LIKE '%Place%'
                   ORDER BY time ASC LIMIT 100""",
                (int(x), int(y), int(z)),
            ).fetchall()
        for name, logged_world, block_type in rows:
            if self._normalise_world_key(logged_world) != normal_world:
                continue
            if self._is_container_block_type(block_type):
                self._set_container_owner(world, x, y, z, name, "historical_place_log")
                return str(name)
        return None

    def _get_container_owner(self, world, x, y, z):
        if not CONTAINER_OWNERSHIP_ENABLED:
            return None
        key = self._container_position_key(world, x, y, z)
        with sqlite3.connect(DB_FILE) as db:
            row = db.execute(
                "SELECT owner_name FROM container_owners WHERE world=? AND x=? AND y=? AND z=?",
                key,
            ).fetchone()
        if row and row[0]:
            return str(row[0])
        return self._infer_container_owner_from_logs(world, x, y, z)

    def _is_container_authorized(self, player, world, x, y, z):
        owner = self._get_container_owner(world, x, y, z)
        if not owner:
            # Unknown legacy ownership is never treated as theft automatically.
            return None, True
        player_name = str(getattr(player, "name", ""))
        op_state = getattr(player, "is_op", False)
        try:
            is_operator = bool(op_state() if callable(op_state) else op_state)
        except Exception:
            is_operator = False
        if player_name.casefold() == owner.casefold() or is_operator:
            return owner, True
        key = self._container_position_key(world, x, y, z)
        with sqlite3.connect(DB_FILE) as db:
            trusted = db.execute(
                """SELECT 1 FROM container_trusted
                   WHERE world=? AND x=? AND y=? AND z=? AND lower(player_name)=lower(?)""",
                (*key, player_name),
            ).fetchone()
        return owner, bool(trusted)

    @staticmethod
    def _unwrap_nbt_json(value):
        if isinstance(value, dict):
            if "__nbt_type" in value and "value" in value:
                return AntiGriefPlugin._unwrap_nbt_json(value["value"])
            return {str(k): AntiGriefPlugin._unwrap_nbt_json(v) for k, v in value.items()}
        if isinstance(value, list):
            return [AntiGriefPlugin._unwrap_nbt_json(v) for v in value]
        return value

    @classmethod
    def _deep_contains(cls, actual, expected):
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(
                key in actual and cls._deep_contains(actual[key], value)
                for key, value in expected.items()
            )
        if isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) < len(expected):
                return False
            return all(cls._deep_contains(a, e) for a, e in zip(actual, expected))
        return actual == expected

    @staticmethod
    def _normalise_item_identifier(value):
        text = str(value or "").casefold()
        match = re.search(r"minecraft:[a-z0-9_]+", text)
        if match:
            return match.group(0)
        text = text.replace("itemtype.", "").replace("<", "").replace(">", "").strip()
        if text and ":" not in text:
            text = "minecraft:" + text
        return text

    def _stack_matches_canonical_item(self, stack, expected):
        if stack is None or not isinstance(expected, dict):
            return False
        expected_id = self._normalise_item_identifier(self.blockdata.item_id(expected))
        actual_id = self._normalise_item_identifier(getattr(stack, "type", ""))
        if actual_id != expected_id:
            return False

        expected_data = expected.get("Damage", expected.get("Aux", expected.get("data")))
        if expected_data not in (None, 0, "0"):
            try:
                if int(getattr(stack, "data", 0)) != int(expected_data):
                    return False
            except (TypeError, ValueError):
                return False

        expected_tag = expected.get("tag")
        expected_custom_name = expected.get("CustomName")
        if not expected_tag and not expected_custom_name:
            return True

        actual_nbt = None
        try:
            actual_nbt = self._unwrap_nbt_json(nbt_to_dict(stack.nbt))
        except Exception:
            actual_nbt = None
        if isinstance(actual_nbt, dict):
            if expected_tag and (
                self._deep_contains(actual_nbt, expected_tag)
                or self._deep_contains(actual_nbt.get("tag"), expected_tag)
            ):
                return True
            if expected_custom_name and expected_custom_name in json.dumps(actual_nbt, ensure_ascii=False):
                return True

        try:
            meta = stack.item_meta
            display_name = str(getattr(meta, "display_name", "") or "")
            if expected_custom_name and display_name == str(expected_custom_name):
                return True
            display = expected_tag.get("display", {}) if isinstance(expected_tag, dict) else {}
            if isinstance(display, dict) and display.get("Name") and display_name == str(display["Name"]):
                return True
        except Exception:
            pass
        return bool(CONFISCATION_TYPE_FALLBACK)

    def _queue_confiscation(
        self, player_name, owner_name, world, x, y, z, item, amount, reason,
        theft_key=None, destination_slot=None, rollback_id=None,
    ):
        """Queue an administrator-confirmed rollback recovery.

        Merely opening, changing, or breaking a container never calls this method.
        The queue is created only by ``/agback`` after the administrator selects the
        incident window and area.
        """
        if not rollback_id or not str(reason).startswith('rollback_recovery:'):
            return None
        try:
            amount = max(1, int(amount))
        except (TypeError, ValueError):
            return None
        if not isinstance(item, dict) or self.blockdata.is_empty_item(item):
            return None
        theft_key = str(theft_key or uuid4().hex)
        now = now_est().isoformat()
        with sqlite3.connect(DB_FILE) as db:
            cur = db.execute(
                """INSERT OR IGNORE INTO pending_confiscations
                   (theft_key,player_name,owner_name,world,x,y,z,item_json,
                    requested_amount,removed_amount,status,reason,created_at,updated_at,
                    destination_slot,rollback_id,returned_amount,trigger_action)
                   VALUES (?,?,?,?,?,?,?,?,?,0,'pending',?,?,?,?,?,0,'agback')""",
                (
                    theft_key, str(player_name), str(owner_name or ""),
                    self._normalise_world_key(world), int(x), int(y), int(z),
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    amount, str(reason), now, now,
                    int(destination_slot) if destination_slot is not None else None,
                    str(rollback_id),
                ),
            )
            db.commit()
            return cur.lastrowid if cur.rowcount else None

    @classmethod
    def _canonical_item_signature(cls, item):
        if not isinstance(item, dict):
            return ""
        normalized = deepcopy(item)
        item_id = str(normalized.get("Name", normalized.get("name", normalized.get("id", ""))))
        for key in ("Count", "count", "Slot", "slot", "id", "name"):
            normalized.pop(key, None)
        if item_id:
            normalized["Name"] = item_id
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _same_canonical_item(self, left, right):
        return (
            self._normalise_item_identifier(self.blockdata.item_id(left))
            == self._normalise_item_identifier(self.blockdata.item_id(right))
            and self._canonical_item_signature(left) == self._canonical_item_signature(right)
        )

    def _ensure_recovery_destination(self, world, x, y, z, item, destination_slot):
        """Ensure the reported container contains the historical item before removal.

        Restoring first means a native write failure can never delete an item from a
        player without a destination. The preferred historical slot is used when it
        is empty or already contains the same canonical stack. Otherwise an empty
        slot is selected rather than overwriting later legitimate contents.
        """
        if not self._ensure_blockdata_ready():
            return False, None
        current, _ = self._capture_native_snapshot(
            world, int(x), int(y), int(z), 'RollbackRecovery',
            'rollback_recovery_destination', store=False,
        )
        if current is None or not self.blockdata.is_container(current):
            return False, None

        inventory = self.blockdata.inventory_map(current)
        capacity = self.blockdata.container_capacity(current)
        desired = self.blockdata.normalize_item_for_patch(deepcopy(item))
        desired_count = max(1, self.blockdata.item_count(desired))
        try:
            preferred = int(destination_slot)
        except (TypeError, ValueError):
            preferred = -1

        if 0 <= preferred < capacity:
            existing = inventory.get(preferred)
            if existing and self._same_canonical_item(existing, desired):
                if self.blockdata.item_count(existing) >= desired_count:
                    return True, preferred
                chosen_slot = preferred
            elif existing is None:
                chosen_slot = preferred
            else:
                chosen_slot = None
        else:
            chosen_slot = None

        if chosen_slot is None:
            for slot, existing in inventory.items():
                if self._same_canonical_item(existing, desired):
                    if self.blockdata.item_count(existing) >= desired_count:
                        return True, slot
                    chosen_slot = slot
                    break
        if chosen_slot is None:
            chosen_slot = next((slot for slot in range(capacity) if slot not in inventory), None)
        if chosen_slot is None:
            return False, None

        patch = self.blockdata._empty_patch(current)
        patch['inventory_updates'] = {chosen_slot: desired}
        result = self.blockdata.apply(self.server, patch, 'fail_if_changed')
        if not result.get('ok') and result.get('status') == 'conflict':
            refreshed, _ = self._capture_native_snapshot(
                world, int(x), int(y), int(z), 'RollbackRecovery',
                'rollback_recovery_conflict', store=False,
            )
            if refreshed is not None:
                patch = self.blockdata._empty_patch(refreshed)
                patch['inventory_updates'] = {chosen_slot: desired}
                result = self.blockdata.apply(self.server, patch, 'force')
        if not result.get('ok'):
            self.logger.warning(
                f"[Rollback Recovery] Could not prepare destination at {x},{y},{z}: "
                f"{result.get('message', result)}"
            )
            return False, None

        verified, _ = self._capture_native_snapshot(
            world, int(x), int(y), int(z), 'RollbackRecovery',
            'rollback_recovery_verified', store=False,
        )
        restored = self.blockdata.inventory_map(verified).get(chosen_slot) if verified else None
        return bool(restored and self._same_canonical_item(restored, desired)), chosen_slot

    def _remove_canonical_item_from_player(self, player, expected_item, amount):
        remaining = max(0, int(amount))
        removed = 0
        inventory = player.inventory
        for slot in range(int(inventory.size)):
            if remaining <= 0:
                break
            try:
                stack = inventory.get_item(slot)
            except Exception:
                continue
            if not self._stack_matches_canonical_item(stack, expected_item):
                continue
            try:
                stack_amount = max(0, int(stack.amount))
            except Exception:
                continue
            take = min(stack_amount, remaining)
            if take <= 0:
                continue
            if take >= stack_amount:
                inventory.clear(slot)
            else:
                stack.amount = stack_amount - take
                inventory.set_item(slot, stack)
            removed += take
            remaining -= take
        return removed

    def _apply_pending_confiscations(self, player_name, rollback_id=None):
        """Recover items only from an administrator-confirmed ``/agback`` batch."""
        if not ROLLBACK_RECOVERY_ENABLED:
            return 0
        player = self.server.get_player(str(player_name))
        if player is None:
            return 0
        sql = """SELECT id,item_json,requested_amount,removed_amount,owner_name,reason,
                        world,x,y,z,destination_slot,rollback_id,returned_amount
                 FROM pending_confiscations
                 WHERE lower(player_name)=lower(?) AND status='pending'
                   AND reason LIKE 'rollback_recovery:%'
                   AND rollback_id IS NOT NULL"""
        params = [str(player_name)]
        if rollback_id:
            sql += " AND rollback_id=?"
            params.append(str(rollback_id))
        sql += " ORDER BY id ASC LIMIT 200"
        with sqlite3.connect(DB_FILE) as db:
            rows = db.execute(sql, params).fetchall()

        total_removed = 0
        touched_rollback_ids = set()
        for (
            row_id, item_json, requested, already_removed, owner_name, reason,
            world, x, y, z, destination_slot, row_rollback_id, returned_amount,
        ) in rows:
            try:
                item = json.loads(item_json)
            except Exception:
                item = None
            remaining = max(0, int(requested or 0) - int(already_removed or 0))
            if remaining <= 0 or not isinstance(item, dict):
                continue

            destination_ready, actual_slot = self._ensure_recovery_destination(
                world, x, y, z, item, destination_slot
            )
            if not destination_ready:
                self.logger.warning(
                    f"[Rollback Recovery] Waiting for destination container at {x},{y},{z} "
                    f"before touching {player_name}'s inventory"
                )
                continue

            removed = self._remove_canonical_item_from_player(player, item, remaining)
            if removed <= 0:
                continue
            new_removed = int(already_removed or 0) + removed
            new_returned = int(returned_amount or 0) + removed
            status = "complete" if new_removed >= int(requested or 0) else "pending"
            with sqlite3.connect(DB_FILE) as db:
                db.execute(
                    """UPDATE pending_confiscations
                       SET removed_amount=?,returned_amount=?,destination_slot=?,
                           status=?,updated_at=? WHERE id=?""",
                    (
                        new_removed, new_returned, actual_slot, status,
                        now_est().isoformat(), int(row_id),
                    ),
                )
                db.commit()
            total_removed += removed
            touched_rollback_ids.add(str(row_rollback_id))
            item_id = self.blockdata.item_id(item)
            self.logger.warning(
                f"[Rollback Recovery] Returned {removed}x {item_id} from {player_name} "
                f"to container {x},{y},{z} slot {actual_slot}; rollback={row_rollback_id}"
            )
            data_buffers['container_access'].append({
                'name': str(player_name),
                'action': 'Rollback Item Recovered',
                'coordinates': {'x': int(x), 'y': int(y), 'z': int(z)},
                'type': item_id,
                'world': str(world),
                'time': now_est().isoformat(),
                'blockdata': json.dumps({
                    'schema_version': BlockDataAdapter.SCHEMA_VERSION,
                    'provider': BlockDataAdapter.PROVIDER,
                    'owner_name': owner_name,
                    'reason': reason,
                    'rollback_id': row_rollback_id,
                    'removed_amount': removed,
                    'returned_amount': removed,
                    'destination_slot': actual_slot,
                    'item': item,
                }, ensure_ascii=False, separators=(',', ':')),
            })
        for touched_rollback_id in touched_rollback_ids:
            try:
                self._refresh_grief_report_recovery(touched_rollback_id)
            except Exception as error:
                self.logger.warning(
                    f"[GriefReport] Could not refresh recovery {touched_rollback_id}: {error}"
                )
        if total_removed:
            try:
                player.send_message(
                    f"{ColorFormat.RED}AntiGrief recovered {total_removed} item(s) for an administrator-confirmed rollback."
                )
            except Exception:
                pass
        return total_removed

    def _start_confiscation_sweeper(self):
        """Retry only recovery batches previously created by an administrator's /agback."""
        if not ROLLBACK_RECOVERY_ENABLED:
            return
        def sweep():
            for player in list(self.server.online_players):
                try:
                    self._apply_pending_confiscations(str(player.name))
                except Exception as error:
                    self.logger.warning(f"[Rollback Recovery] Sweep failed for player: {error}")
        self.server.scheduler.run_task(
            self, sweep, delay=CONFISCATION_SWEEP_TICKS, period=CONFISCATION_SWEEP_TICKS
        )

    def _queue_snapshot_confiscations(
        self, player_name, owner_name, world, x, y, z, snapshot, reason,
        rollback_id=None, key_prefix=None,
    ):
        queued = 0
        for slot, item in self.blockdata.inventory_map(snapshot).items():
            theft_key = f"{key_prefix or rollback_id}:slot:{slot}"
            if self._queue_confiscation(
                player_name, owner_name, world, x, y, z, item,
                self.blockdata.item_count(item), f"rollback_recovery:{reason}:slot:{slot}",
                theft_key=theft_key, destination_slot=slot, rollback_id=rollback_id,
            ):
                queued += 1
        if queued:
            self.logger.warning(
                f"[Rollback Recovery] Queued {queued} historical container stack(s) "
                f"for {player_name} at {x},{y},{z}; rollback={rollback_id}"
            )
        return queued

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

    def _live_block_matches(self, dimension, x, y, z, expected_type):
        """Check whether a failed setblock already has the intended block type."""
        if not self._ensure_blockdata_ready():
            return False
        try:
            snapshot = self.blockdata.capture(self.server, dimension, x, y, z)
        except Exception:
            return False
        if not isinstance(snapshot, dict):
            return False
        actual = str(snapshot.get('type') or '').strip().casefold()
        expected = str(expected_type or '').strip().casefold()
        if ':' not in actual and actual:
            actual = 'minecraft:' + actual
        if ':' not in expected and expected:
            expected = 'minecraft:' + expected
        return actual == expected

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
        if not self._ensure_blockdata_ready():
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
            # Destructive events keep an inline exact snapshot as a second recovery
            # path. The dedicated snapshot table remains the primary indexed store,
            # but rollback can still restore inventory if a DB batch is interrupted.
            if self.blockdata.is_container(snapshot):
                backup['block_snapshot'] = self.blockdata.json_safe(snapshot)
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

    def _apply_native_restore_phase(
        self,
        current,
        saved_snapshot,
        dimension,
        x,
        y,
        z,
        actor_name,
        phase_name,
        patch_builder,
    ):
        """Apply one native restore phase with an optimistic retry.

        BlockData requires block-state mutations and block-entity mutations to be
        separate calls. This helper keeps both calls revision-safe and recaptures
        after a conflict before forcing the intended historical snapshot.
        """
        patch = patch_builder(current, saved_snapshot)
        if not self.blockdata.patch_has_changes(patch):
            return True, current

        result = self.blockdata.apply(self.server, patch, 'fail_if_changed')
        if not result.get('ok') and result.get('status') == 'conflict':
            current, _ = self._capture_native_snapshot(
                dimension, x, y, z, actor_name,
                f'rollback_{phase_name}_conflict_retry', store=False
            )
            if current is not None:
                patch = patch_builder(current, saved_snapshot)
                if not self.blockdata.patch_has_changes(patch):
                    return True, current
                result = self.blockdata.apply(self.server, patch, 'force')

        if not result.get('ok'):
            self.logger.warning(
                f"[Rollback] Native {phase_name} restore failed at {x},{y},{z}: "
                f"{result.get('message', result)}"
            )
            return False, current

        refreshed, _ = self._capture_native_snapshot(
            dimension, x, y, z, actor_name,
            f'rollback_post_{phase_name}', store=False
        )
        return True, refreshed or current

    def _restore_native_snapshot(self, saved_snapshot, dimension, x, y, z, actor_name="Rollback"):
        if not self._ensure_blockdata_ready() or not self.blockdata.is_container(saved_snapshot):
            return False
        current, _ = self._capture_native_snapshot(
            dimension, x, y, z, actor_name, 'rollback_pre_apply', store=False
        )
        if current is None:
            self.logger.warning(
                f"[Rollback] BlockData could not capture recreated container at {x},{y},{z}"
            )
            return False
        try:
            # Phase 1: orientation and other block states only. A failure here does
            # not prevent inventory recovery; the item payload is the higher-value
            # part of the rollback and uses a separate native transaction below.
            states_ok, current = self._apply_native_restore_phase(
                current, saved_snapshot, dimension, x, y, z, actor_name,
                'block-state', self.blockdata.build_state_restore_patch
            )
            if not states_ok:
                self.logger.warning(
                    f"[Rollback] Continuing with container NBT/items at {x},{y},{z} "
                    "after block-state restore failure"
                )

            # Phase 2: writable actor metadata only. Identity fields such as
            # id/x/y/z and _endstone_* markers are read-only in the exact adapter.
            # Metadata failure is deliberately non-fatal so it cannot block items.
            metadata_ok, current = self._apply_native_restore_phase(
                current, saved_snapshot, dimension, x, y, z, actor_name,
                'metadata', self.blockdata.build_metadata_restore_patch
            )
            if not metadata_ok:
                self.logger.warning(
                    f"[Rollback] Continuing with exact inventory restore at {x},{y},{z} "
                    "after writable metadata restore failure"
                )

            # Phase 3: inventory only. This is isolated from every actor NBT field
            # so protected metadata can never turn a valid chest snapshot into an
            # empty restored shell.
            inventory_ok, current = self._apply_native_restore_phase(
                current, saved_snapshot, dimension, x, y, z, actor_name,
                'inventory', self.blockdata.build_inventory_restore_patch
            )
            if not inventory_ok:
                return False

            restored, _ = self._capture_native_snapshot(
                dimension, x, y, z, actor_name, 'rollback_restored', store=True
            )
            expected_inventory = self.blockdata.inventory_map(saved_snapshot)
            restored_inventory = self.blockdata.inventory_map(restored)
            restored_items = len(restored_inventory)
            if restored_inventory != expected_inventory:
                missing_slots = sorted(set(expected_inventory) - set(restored_inventory))
                changed_slots = sorted(
                    slot for slot in set(expected_inventory) & set(restored_inventory)
                    if expected_inventory[slot] != restored_inventory[slot]
                )
                self.logger.warning(
                    f"[Rollback] Container verification mismatch at {x},{y},{z}: "
                    f"expected={len(expected_inventory)} restored={restored_items} "
                    f"missing_slots={missing_slots[:10]} changed_slots={changed_slots[:10]}"
                )
                return False

            self.logger.info(
                f"[Rollback] Restored writable metadata and {restored_items} occupied slots "
                f"at {x},{y},{z} in {dimension}"
            )
            return True
        except Exception as error:
            self.logger.warning(
                f"[Rollback] Native container restore exception at {x},{y},{z}: {error}"
            )
            return False

    def _schedule_native_restore(self, saved_snapshot, dimension, x, y, z, actor_name="Rollback"):
        """Restore after the recreated block actor exists, retrying short startup races."""
        attempts = {'count': 0}

        def restore_task():
            attempts['count'] += 1
            current, _ = self._capture_native_snapshot(
                dimension, x, y, z, actor_name, 'rollback_actor_ready_check', store=False
            )
            if current is not None and self.blockdata.is_container(current):
                restored = self._restore_native_snapshot(
                    saved_snapshot, dimension, x, y, z, actor_name
                )
                if restored:
                    return
                self.logger.warning(
                    f"[Rollback] Container restore attempt {attempts['count']} did not verify "
                    f"at {x},{y},{z}; retrying"
                )
            if attempts['count'] >= 10:
                self.logger.warning(
                    f"[Rollback] Container restore did not verify at {x},{y},{z} "
                    f"in {dimension} after {attempts['count']} attempts"
                )
                return
            try:
                self.server.scheduler.run_task(
                    self, restore_task, delay=min(12, 2 + attempts['count'])
                )
            except Exception as error:
                self.logger.warning(
                    f"[Rollback] Could not queue container restore retry at "
                    f"{x},{y},{z}: {error}"
                )

        try:
            self.server.scheduler.run_task(self, restore_task, delay=2)
        except Exception as error:
            self.logger.warning(f"[Rollback] Scheduler unavailable, restoring immediately: {error}")
            self._restore_native_snapshot(saved_snapshot, dimension, x, y, z, actor_name)
    
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
        
        # /agowner - Container ownership administration
        if cmd == "agowner":
            if not isinstance(sender, Player):
                sender.send_message(f'{ColorFormat.RED}Use this command in-game so a dimension is available.')
                return True
            if not args:
                sender.send_message(
                    f'{ColorFormat.YELLOW}Usage: /agowner <info|set|trust|untrust|clear> <x y z> [player]'
                )
                return True
            action = str(args[0]).casefold()
            pos_text = str(args[1]) if len(args) > 1 else ""
            parts = pos_text.split()
            if len(parts) != 3:
                sender.send_message(f'{ColorFormat.RED}Position must be supplied as x y z.')
                return True
            try:
                bx, by, bz = (int(float(value)) for value in parts)
            except ValueError:
                sender.send_message(f'{ColorFormat.RED}Position must contain three numbers.')
                return True
            world = str(sender.location.dimension.name)
            target_name = str(args[2]).strip() if len(args) > 2 and args[2] else ""
            if action == "info":
                owner = self._get_container_owner(world, bx, by, bz)
                key = self._container_position_key(world, bx, by, bz)
                with sqlite3.connect(DB_FILE) as db:
                    trusted = [row[0] for row in db.execute(
                        "SELECT player_name FROM container_trusted WHERE world=? AND x=? AND y=? AND z=? ORDER BY player_name",
                        key,
                    ).fetchall()]
                sender.send_message(
                    f'{ColorFormat.AQUA}Container {bx},{by},{bz}: owner={owner or "unassigned"}; '
                    f'trusted={", ".join(trusted) if trusted else "none"}'
                )
                return True
            if action == "clear":
                self._clear_container_owner(world, bx, by, bz)
                sender.send_message(f'{ColorFormat.GREEN}Cleared owner and trusted players at {bx},{by},{bz}.')
                return True
            if action == "set":
                if not target_name:
                    sender.send_message(f'{ColorFormat.RED}Usage: /agowner set <x y z> <player>')
                    return True
                self._set_container_owner(world, bx, by, bz, target_name, "admin_command")
                sender.send_message(f'{ColorFormat.GREEN}Owner set to {target_name} at {bx},{by},{bz}.')
                return True
            if action in {"trust", "untrust"}:
                if not target_name:
                    sender.send_message(f'{ColorFormat.RED}Usage: /agowner {action} <x y z> <player>')
                    return True
                self._set_container_trust(world, bx, by, bz, target_name, action == "trust")
                sender.send_message(
                    f'{ColorFormat.GREEN}{target_name} is now '
                    f'{"trusted" if action == "trust" else "untrusted"} at {bx},{by},{bz}.'
                )
                return True
            sender.send_message(f'{ColorFormat.RED}Unknown action: {action}')
            return True

        # /agconfiscate - Force a pending confiscation sweep for an online player
        if cmd == "agconfiscate":
            if not args:
                sender.send_message(f'{ColorFormat.RED}Usage: /agconfiscate <player>')
                return True
            target_name = str(args[0])
            if self.server.get_player(target_name) is None:
                sender.send_message(
                    f'{ColorFormat.YELLOW}{target_name} is offline. Pending rollback recovery will run when they join.'
                )
                return True
            removed = self._apply_pending_confiscations(target_name)
            sender.send_message(
                f'{ColorFormat.GREEN}Confiscation sweep completed for {target_name}: {removed} item(s) removed.'
            )
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
        sender.send_message(f'{ColorFormat.YELLOW}/agowner <info|set|trust|untrust|clear> <x y z> [player] - Container ownership')
        sender.send_message(f'{ColorFormat.YELLOW}/agconfiscate <player> - Retry a pending rollback recovery')
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
        """Display a safe live BlockData inventory summary for an online player."""
        snapshot = self._capture_player_inventory(target, force=True)
        if snapshot is None:
            sender.send_message(
                f'{ColorFormat.RED}Player inventory capture is unavailable. '
                f'{self.blockdata.player_inventory_error}'
            )
            return
        summary = self.blockdata.player_inventory_summary(snapshot)
        lines = [
            f"Player: {summary['player_name']}",
            f"Selected hotbar slot: {summary['selected_hotbar_slot']}",
            f"Bundles / storage items: {summary['storage_item_count']}",
            "",
        ]
        labels = {
            'main': 'Main + Hotbar', 'armor': 'Armor',
            'offhand': 'Offhand', 'ender_chest': 'Ender Chest',
        }
        for section, label in labels.items():
            values = summary['sections'][section]
            lines.append(
                f"{label}: {values['occupied_slots']} / {values['capacity']} occupied "
                f"({values['item_count']} items)"
            )
        lines.extend([
            "",
            "Open the AntiGrief WebUI Player Inventories section for exact slots,",
            "lore, enchantments, Ender Chest contents, and nested bundles.",
        ])
        sender.send_form(ActionForm(
            title=f"Inventory: {summary['player_name']}",
            content="\n".join(lines),
        ))
    
    @staticmethod
    def _report_admin_name(sender):
        name = getattr(sender, 'name', None)
        return str(name) if name else 'Console'

    def _report_item_summary(self, item, *, slot=None, amount=None):
        if not isinstance(item, dict) or self.blockdata.is_empty_item(item):
            return None
        count = self.blockdata.item_count(item)
        try:
            reported_count = max(0, int(amount if amount is not None else count))
        except (TypeError, ValueError):
            reported_count = max(0, int(count))
        summary = {
            'slot': slot,
            'item_id': self.blockdata.item_id(item),
            'count': reported_count,
            'canonical_count': count,
            'item': deepcopy(item),
        }
        tag = item.get('tag') if isinstance(item.get('tag'), dict) else {}
        display = tag.get('display') if isinstance(tag.get('display'), dict) else {}
        custom_name = item.get('CustomName') or display.get('Name')
        if custom_name:
            summary['custom_name'] = str(custom_name)
        lore = display.get('Lore') or tag.get('Lore') or item.get('Lore')
        if isinstance(lore, (list, tuple)):
            summary['lore'] = [str(line) for line in lore]
        enchantments = (
            tag.get('ench') or tag.get('Enchantments') or item.get('ench')
            or item.get('Enchantments') or []
        )
        if isinstance(enchantments, (list, tuple)):
            summary['enchantments'] = [
                dict(entry) if isinstance(entry, dict) else {'value': str(entry)}
                for entry in enchantments
            ]
        damage = item.get('Damage', item.get('damage', item.get('Aux', item.get('aux'))))
        if damage is not None:
            summary['damage'] = damage
        return summary

    def _report_event_category(self, action, block_type, payload):
        lowered = str(action or '').casefold()
        if lowered.startswith('container '):
            if 'take' in lowered or 'change' in lowered:
                return 'container_loot'
            if 'add' in lowered:
                return 'container_tamper'
            return 'container_metadata'
        if 'explode' in lowered:
            return 'explosion'
        if 'place' in lowered:
            return 'block_place'
        if 'break' in lowered:
            snapshot = self._saved_native_snapshot(payload)
            if (
                snapshot is not None and self.blockdata.is_container(snapshot)
            ) or self._is_container_block_type(block_type):
                return 'container_break'
            return 'block_break'
        return 'other'

    def _build_grief_report(
        self, rollback_id, sender, x, y, z, hours, radius, player_filter,
        rows, targets,
    ):
        created_at = now_est()
        report_id = (
            f"AGR-{created_at.strftime('%Y%m%d-%H%M%S')}-"
            f"{str(rollback_id)[:6].upper()}"
        )
        player_counts = Counter()
        action_counts = Counter()
        block_counts = Counter()
        category_counts = Counter()
        worlds = set()
        positions = set()
        bounds = {}
        events = []
        containers = {}
        items_reported = 0

        for row in rows:
            row_id, player_name, action = int(row[0]), str(row[1]), str(row[2])
            bx, by, bz = int(row[3]), int(row[4]), int(row[5])
            block_type = str(row[6] or 'minecraft:air')
            world = str(row[7] or 'overworld')
            event_time = str(row[8] or '')
            try:
                payload = json.loads(row[9]) if row[9] else {}
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            category = self._report_event_category(action, block_type, payload)
            player_counts[player_name] += 1
            action_counts[action] += 1
            block_counts[block_type] += 1
            category_counts[category] += 1
            world_key = self._normalise_world_key(world)
            worlds.add(world)
            positions.add((world_key, bx, by, bz))
            current_bounds = bounds.setdefault(world, {
                'min_x': bx, 'min_y': by, 'min_z': bz,
                'max_x': bx, 'max_y': by, 'max_z': bz,
            })
            current_bounds['min_x'] = min(current_bounds['min_x'], bx)
            current_bounds['min_y'] = min(current_bounds['min_y'], by)
            current_bounds['min_z'] = min(current_bounds['min_z'], bz)
            current_bounds['max_x'] = max(current_bounds['max_x'], bx)
            current_bounds['max_y'] = max(current_bounds['max_y'], by)
            current_bounds['max_z'] = max(current_bounds['max_z'], bz)

            item_summaries = []
            if category in {'container_loot', 'container_tamper'}:
                before_item = payload.get('before_item')
                after_item = payload.get('after_item')
                slot = payload.get('slot')
                amount = payload.get('amount')
                selected_item = before_item if category == 'container_loot' else after_item
                item_summary = self._report_item_summary(
                    selected_item, slot=slot, amount=amount
                )
                if item_summary:
                    item_summaries.append(item_summary)
                    if category == 'container_loot':
                        items_reported += max(0, int(item_summary.get('count') or 0))
            elif category == 'container_break':
                snapshot = self._saved_native_snapshot(payload)
                if snapshot is not None:
                    for slot, item in self.blockdata.inventory_map(snapshot).items():
                        item_summary = self._report_item_summary(item, slot=slot)
                        if item_summary:
                            item_summaries.append(item_summary)
                            items_reported += max(0, int(item_summary.get('count') or 0))

            evidence_payload = {
                key: value for key, value in payload.items()
                if key not in {'before_snapshot', 'after_snapshot', 'block_snapshot'}
            }
            event = {
                'interaction_id': row_id,
                'player': player_name,
                'action': action,
                'category': category,
                'time': event_time,
                'world': world,
                'position': {'x': bx, 'y': by, 'z': bz},
                'target': block_type,
                'items': item_summaries,
                'snapshot_id': (
                    payload.get('before_snapshot_id') or payload.get('snapshot_id')
                    or payload.get('after_snapshot_id')
                ),
                'evidence': evidence_payload,
            }
            events.append(event)

            if category.startswith('container_'):
                key = f'{world_key}:{bx}:{by}:{bz}'
                container = containers.setdefault(key, {
                    'world': world,
                    'x': bx, 'y': by, 'z': bz,
                    'container_type': block_type,
                    'actions': [],
                    'players': set(),
                    'items': [],
                    'broken': False,
                })
                container['actions'].append(action)
                container['players'].add(player_name)
                container['items'].extend(item_summaries)
                if category == 'container_break':
                    container['broken'] = True

        primary_player = str(player_filter or '') or (
            player_counts.most_common(1)[0][0] if player_counts else 'Unknown'
        )
        container_list = []
        for value in containers.values():
            value = dict(value)
            value['players'] = sorted(value['players'], key=str.casefold)
            value['actions'] = list(dict.fromkeys(value['actions']))
            container_list.append(value)
        container_list.sort(key=lambda entry: (
            str(entry['world']).casefold(), entry['y'], entry['x'], entry['z']
        ))

        target_evidence = []
        for target in targets:
            target_evidence.append({
                'interaction_id': target.get('row_id'),
                'world': target.get('dimension'),
                'x': target.get('x'), 'y': target.get('y'), 'z': target.get('z'),
                'expected_block': target.get('block_type'),
                'is_container': bool(target.get('is_container')),
                'expected_slots': len(
                    self.blockdata.inventory_map(target.get('saved_snapshot'))
                ) if target.get('saved_snapshot') else 0,
            })

        query = {
            'hours': float(hours),
            'center': {'x': int(x), 'y': int(y), 'z': int(z)},
            'radius': float(radius),
            'player_filter': player_filter,
        }
        evidence_core = {
            'query': query,
            'events': events,
            'targets': target_evidence,
        }
        evidence_hash = hashlib.sha256(
            json.dumps(
                evidence_core, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), default=str,
            ).encode('utf-8')
        ).hexdigest()
        looted_containers = sum(
            1 for entry in container_list
            if any(action in {'Container Take', 'Container Change'} for action in entry['actions'])
        )
        broken_containers = sum(1 for entry in container_list if entry.get('broken'))
        summary = {
            'event_count': len(events),
            'affected_positions': len(positions),
            'blocks_broken': category_counts['block_break'],
            'blocks_placed': category_counts['block_place'],
            'explosions': category_counts['explosion'],
            'containers_looted': looted_containers,
            'container_loot_events': category_counts['container_loot'],
            'containers_tampered': category_counts['container_tamper'] + category_counts['container_metadata'],
            'containers_broken': broken_containers,
            'container_break_events': category_counts['container_break'],
            'items_reported': items_reported,
            'items_recovered': 0,
            'actions': dict(action_counts),
            'block_types': dict(block_counts),
        }
        report = {
            'schema_version': 'antigrief-grief-report-v1',
            'report_id': report_id,
            'rollback_id': rollback_id,
            'evidence_hash': evidence_hash,
            'created_at': created_at.isoformat(),
            'completed_at': None,
            'status': 'processing',
            'admin': self._report_admin_name(sender),
            'primary_player': primary_player,
            'players': [
                {'name': name, 'event_count': count}
                for name, count in player_counts.most_common()
            ],
            'worlds': sorted(worlds, key=str.casefold),
            'area': {'query': query, 'bounds_by_world': bounds},
            'summary': summary,
            'containers': container_list,
            'events': events,
            'rollback': {
                'targets': target_evidence,
                'execution': {},
                'verification': {},
                'recovery': {},
            },
        }
        return report

    def _store_grief_report(self, report):
        summary = report.get('summary') or {}
        query = ((report.get('area') or {}).get('query') or {})
        center = query.get('center') or {}
        with sqlite3.connect(DB_FILE) as db:
            db.execute(
                """INSERT OR REPLACE INTO grief_reports
                   (report_id,rollback_id,created_at,completed_at,admin_name,status,
                    center_x,center_y,center_z,radius,hours,player_filter,
                    primary_player,event_count,affected_positions,blocks_broken,
                    blocks_placed,explosions,containers_looted,containers_broken,
                    items_reported,items_recovered,evidence_hash,players_json,
                    worlds_json,summary_json,report_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    report['report_id'], report['rollback_id'], report['created_at'],
                    report.get('completed_at'), report.get('admin') or 'Console',
                    report.get('status') or 'processing', int(center.get('x', 0)),
                    int(center.get('y', 0)), int(center.get('z', 0)),
                    float(query.get('radius', 0)), float(query.get('hours', 0)),
                    query.get('player_filter'), report.get('primary_player'),
                    int(summary.get('event_count', 0)),
                    int(summary.get('affected_positions', 0)),
                    int(summary.get('blocks_broken', 0)),
                    int(summary.get('blocks_placed', 0)),
                    int(summary.get('explosions', 0)),
                    int(summary.get('containers_looted', 0)),
                    int(summary.get('containers_broken', 0)),
                    int(summary.get('items_reported', 0)),
                    int(summary.get('items_recovered', 0)), report['evidence_hash'],
                    json.dumps(report.get('players') or [], ensure_ascii=False),
                    json.dumps(report.get('worlds') or [], ensure_ascii=False),
                    json.dumps(summary, ensure_ascii=False, separators=(',', ':')),
                    json.dumps(report, ensure_ascii=False, separators=(',', ':'), default=str),
                ),
            )
            db.commit()
        return report['report_id']

    def _update_grief_report_execution(self, report_id, execution):
        with sqlite3.connect(DB_FILE) as db:
            row = db.execute(
                'SELECT report_json FROM grief_reports WHERE report_id=?',
                (str(report_id),),
            ).fetchone()
            if not row:
                return
            try:
                report = json.loads(row[0])
            except Exception:
                return
            report.setdefault('rollback', {})['execution'] = dict(execution)
            db.execute(
                'UPDATE grief_reports SET report_json=? WHERE report_id=?',
                (
                    json.dumps(report, ensure_ascii=False, separators=(',', ':'), default=str),
                    str(report_id),
                ),
            )
            db.commit()

    def _refresh_grief_report_recovery(self, rollback_id):
        if not rollback_id:
            return
        with sqlite3.connect(DB_FILE) as db:
            row = db.execute(
                'SELECT report_id,status,report_json FROM grief_reports WHERE rollback_id=?',
                (str(rollback_id),),
            ).fetchone()
            if not row:
                return
            recovery_row = db.execute(
                """SELECT COALESCE(SUM(requested_amount),0),
                          COALESCE(SUM(removed_amount),0),
                          COALESCE(SUM(returned_amount),0),
                          SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),
                          COUNT(*)
                   FROM pending_confiscations WHERE rollback_id=?""",
                (str(rollback_id),),
            ).fetchone() or (0, 0, 0, 0, 0, 0)
            try:
                report = json.loads(row[2])
            except Exception:
                return
            recovery = {
                'requested_items': int(recovery_row[0] or 0),
                'removed_from_players': int(recovery_row[1] or 0),
                'returned_to_containers': int(recovery_row[2] or 0),
                'completed_rows': int(recovery_row[3] or 0),
                'pending_rows': int(recovery_row[4] or 0),
                'recovery_rows': int(recovery_row[5] or 0),
            }
            report.setdefault('rollback', {})['recovery'] = recovery
            report.setdefault('summary', {})['items_recovered'] = recovery['returned_to_containers']
            status = str(row[1] or report.get('status') or 'processing')
            verification = report.get('rollback', {}).get('verification') or {}
            has_failures = bool(
                int(verification.get('failed_blocks') or 0)
                or int(verification.get('failed_containers') or 0)
            )
            if status != 'processing':
                if has_failures:
                    status = 'completed_with_failures'
                elif recovery['pending_rows']:
                    status = 'completed_pending_recovery'
                else:
                    status = 'completed'
                report['status'] = status
            db.execute(
                """UPDATE grief_reports
                   SET status=?,items_recovered=?,summary_json=?,report_json=?
                   WHERE report_id=?""",
                (
                    status, recovery['returned_to_containers'],
                    json.dumps(report['summary'], ensure_ascii=False, separators=(',', ':')),
                    json.dumps(report, ensure_ascii=False, separators=(',', ':'), default=str),
                    str(row[0]),
                ),
            )
            db.commit()

    def _finalize_grief_report(self, report_id, rollback_id, targets):
        verification_rows = []
        failed_blocks = 0
        failed_containers = 0
        verified_blocks = 0
        verified_containers = 0
        for target in targets:
            expected_type = self._normalize_rollback_block_type(target.get('block_type'))
            world = target.get('dimension')
            bx, by, bz = int(target.get('x')), int(target.get('y')), int(target.get('z'))
            current, _ = self._capture_native_snapshot(
                world, bx, by, bz, 'GriefReport', 'report_verification', store=False
            )
            actual_type = (
                self._normalize_rollback_block_type(current.get('type'))
                if isinstance(current, dict) and current.get('type')
                else 'capture_unavailable'
            )
            block_ok = actual_type == expected_type
            inventory_ok = None
            expected_slots = 0
            actual_slots = 0
            if target.get('is_container') and target.get('saved_snapshot'):
                expected_inventory = self.blockdata.inventory_map(target['saved_snapshot'])
                current_inventory = self.blockdata.inventory_map(current)
                expected_slots = len(expected_inventory)
                actual_slots = len(current_inventory)
                inventory_ok = current_inventory == expected_inventory
                if block_ok and inventory_ok:
                    verified_containers += 1
                else:
                    failed_containers += 1
            elif block_ok:
                verified_blocks += 1
            else:
                failed_blocks += 1
            verification_rows.append({
                'world': world, 'x': bx, 'y': by, 'z': bz,
                'expected_block': expected_type, 'actual_block': actual_type,
                'block_restored': block_ok, 'container_inventory_restored': inventory_ok,
                'expected_occupied_slots': expected_slots,
                'actual_occupied_slots': actual_slots,
            })

        with sqlite3.connect(DB_FILE) as db:
            recovery_row = db.execute(
                """SELECT COALESCE(SUM(requested_amount),0),
                          COALESCE(SUM(removed_amount),0),
                          COALESCE(SUM(returned_amount),0),
                          SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),
                          COUNT(*)
                   FROM pending_confiscations WHERE rollback_id=?""",
                (str(rollback_id),),
            ).fetchone() or (0, 0, 0, 0, 0, 0)
            row = db.execute(
                'SELECT report_json FROM grief_reports WHERE report_id=?',
                (str(report_id),),
            ).fetchone()
            if not row:
                return
            try:
                report = json.loads(row[0])
            except Exception:
                return

            recovery = {
                'requested_items': int(recovery_row[0] or 0),
                'removed_from_players': int(recovery_row[1] or 0),
                'returned_to_containers': int(recovery_row[2] or 0),
                'completed_rows': int(recovery_row[3] or 0),
                'pending_rows': int(recovery_row[4] or 0),
                'recovery_rows': int(recovery_row[5] or 0),
            }
            verification = {
                'verified_blocks': verified_blocks,
                'failed_blocks': failed_blocks,
                'verified_containers': verified_containers,
                'failed_containers': failed_containers,
                'positions': verification_rows,
            }
            if failed_blocks or failed_containers:
                status = 'completed_with_failures'
            elif recovery['pending_rows']:
                status = 'completed_pending_recovery'
            else:
                status = 'completed'
            completed_at = now_est().isoformat()
            report['status'] = status
            report['completed_at'] = completed_at
            report.setdefault('rollback', {})['verification'] = verification
            report['rollback']['recovery'] = recovery
            report.setdefault('summary', {})['items_recovered'] = recovery['returned_to_containers']
            db.execute(
                """UPDATE grief_reports
                   SET completed_at=?,status=?,items_recovered=?,summary_json=?,report_json=?
                   WHERE report_id=?""",
                (
                    completed_at, status, recovery['returned_to_containers'],
                    json.dumps(report['summary'], ensure_ascii=False, separators=(',', ':')),
                    json.dumps(report, ensure_ascii=False, separators=(',', ':'), default=str),
                    str(report_id),
                ),
            )
            db.commit()
        self.logger.info(
            f"[GriefReport] Finalized {report_id}: status={status} "
            f"blocks_ok={verified_blocks} containers_ok={verified_containers} "
            f"items_recovered={recovery['returned_to_containers']}"
        )

    def _schedule_grief_report_finalize(self, report_id, rollback_id, targets):
        def finalize():
            try:
                self._finalize_grief_report(report_id, rollback_id, targets)
            except Exception as error:
                self.logger.warning(
                    f"[GriefReport] Could not finalize {report_id}: {error}"
                )
        try:
            self.server.scheduler.run_task(self, finalize, delay=240)
        except Exception:
            finalize()

    @staticmethod
    def _normalize_rollback_block_type(block_type_raw):
        block_type = str(block_type_raw or "air")
        if "." in block_type and ":" not in block_type:
            block_type = "minecraft:" + block_type.split(".")[-1].lower()
        elif ":" not in block_type:
            block_type = "minecraft:" + block_type.lower()
        return block_type.replace("<", "").replace(">", "").strip()

    def _attempt_rollback_block(self, target):
        """Apply and verify one rollback block target.

        Bedrock can return ``False`` when the desired block already exists, while
        block actors can take several ticks to materialize after a neighboring
        block changes. Every attempt therefore verifies the live type through
        BlockData before deciding that placement actually failed.
        """
        dimension = target['dimension']
        bx, by, bz = target['x'], target['y'], target['z']
        block_type = target['block_type']

        if block_type == 'minecraft:air':
            result = self._dispatch_in_dimension(
                dimension, f"setblock {bx} {by} {bz} air replace"
            )
            if not result and self._live_block_matches(
                dimension, bx, by, bz, 'minecraft:air'
            ):
                self.logger.info(
                    f"[Rollback] Block already matched minecraft:air at "
                    f"({bx},{by},{bz}) in {dimension}"
                )
                return True
            if not result:
                self.logger.warning(
                    f"[Rollback] setblock air failed at ({bx},{by},{bz}) in {dimension}"
                )
            return bool(result)

        states_arg = self._block_states_argument(target.get('states') or {})
        result = self._dispatch_in_dimension(
            dimension,
            f"setblock {bx} {by} {bz} {block_type}{states_arg} replace",
        )
        if not result and states_arg:
            result = self._dispatch_in_dimension(
                dimension,
                f"setblock {bx} {by} {bz} {block_type} replace",
            )
            if result:
                self.logger.info(
                    f"[Rollback] Placed {block_type} without command states at "
                    f"({bx},{by},{bz}); native state restore will follow"
                )

        if not result and self._live_block_matches(
            dimension, bx, by, bz, block_type
        ):
            self.logger.info(
                f"[Rollback] Block already matched {block_type} at "
                f"({bx},{by},{bz}) in {dimension}"
            )
            return True

        if not result:
            self.logger.warning(
                f"[Rollback] setblock failed at ({bx},{by},{bz}) "
                f"in {dimension}: {block_type}{states_arg}"
            )
        return bool(result)

    def _queue_post_block_restore(self, target):
        """Restore container contents only after its block placement is verified."""
        snapshot = target.get('saved_snapshot')
        if snapshot and self.blockdata.is_container(snapshot):
            self._schedule_native_restore(
                snapshot,
                target['dimension'],
                target['x'],
                target['y'],
                target['z'],
                target['actor_name'],
            )
            return True

        items = target.get('legacy_items') or []
        if not items:
            return False
        for index in range(0, len(items), 6):
            payload = json.dumps({
                'x': target['x'], 'y': target['y'], 'z': target['z'],
                'dim': target['dimension'],
                'items': items[index:index + 6], 'clear': index == 0,
            }, separators=(',', ':'))
            self.server.dispatch_command(
                self.server.command_sender,
                f"scriptevent antigrief:container_restore {payload}",
            )
        return True

    def _schedule_rollback_block_retry(self, target, max_attempts=10):
        """Retry one failed target and restore its inventory after success."""
        attempts = {'count': 0}

        def retry_task():
            attempts['count'] += 1
            if self._attempt_rollback_block(target):
                self.logger.info(
                    f"[Rollback] Retry {attempts['count']} restored "
                    f"{target['block_type']} at ({target['x']},{target['y']},{target['z']}) "
                    f"in {target['dimension']}"
                )
                self._queue_post_block_restore(target)
                return

            if attempts['count'] >= max_attempts:
                self.logger.error(
                    f"[Rollback] Permanently failed to restore {target['block_type']} at "
                    f"({target['x']},{target['y']},{target['z']}) in "
                    f"{target['dimension']} after {attempts['count']} retries"
                )
                return

            try:
                self.server.scheduler.run_task(
                    self,
                    retry_task,
                    delay=min(20, 2 + attempts['count'] * 2),
                )
            except Exception as error:
                self.logger.error(
                    f"[Rollback] Could not queue block retry at "
                    f"({target['x']},{target['y']},{target['z']}): {error}"
                )

        try:
            self.server.scheduler.run_task(self, retry_task, delay=2)
            return True
        except Exception as error:
            self.logger.warning(
                f"[Rollback] Scheduler unavailable for block retry at "
                f"({target['x']},{target['y']},{target['z']}): {error}"
            )
            return False

    def _execute_rollback(self, sender, x, y, z, hours, radius, player_filter=None):
        """Restore selected block/container history and then recover reported theft.

        Container evidence remains passive until this OP-only command is executed.
        The command creates a rollback ID, restores the earliest pre-change snapshot
        at each coordinate, and only then queues exact player-to-container recovery.
        """
        try:
            flush_data_to_db()
        except Exception as error:
            self.logger.warning(f"[Rollback] Flush failed: {error}")

        rollback_id = uuid4().hex
        time_threshold = now_est() - timedelta(hours=hours)
        radius_sq = radius ** 2
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            sql = """
                SELECT id, name, action, x, y, z, type, world, time, blockdata
                FROM interactions
                WHERE (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?
                AND time >= ?
                AND (
                    action LIKE '%Break%' OR action LIKE '%Place%' OR
                    action LIKE '%Explode%' OR action IN (
                        'Container Add','Container Take','Container Change','Container NBT Change'
                    )
                )
            """
            params = [x, x, y, y, z, z, radius_sq, time_threshold.isoformat()]
            if player_filter:
                sql += " AND name LIKE ?"
                params.append(f"%{player_filter}%")
            sql += " ORDER BY time ASC, id ASC"
            cur.execute(sql, params)
            rows = cur.fetchall()

        earliest = {}
        for row in rows:
            key = (str(row[7]), int(row[3]), int(row[4]), int(row[5]))
            earliest.setdefault(key, row)
        results = list(earliest.values())
        if not results:
            filter_msg = f" for player '{player_filter}'" if player_filter else ""
            sender.send_message(
                f'{ColorFormat.YELLOW}No block or container changes found{filter_msg} in the specified area/time.'
            )
            return

        filter_msg = f" by '{player_filter}'" if player_filter else ""
        sender.send_message(
            f'{ColorFormat.GREEN}{lang["rollback_start"]} {len(results)} positions'
            f'{filter_msg} in {radius} blocks, {hours} hours... '
            f'{ColorFormat.YELLOW}Recovery ID: {rollback_id[:8]}'
        )
        self.logger.info(
            f"[Rollback] Recovery {rollback_id}: reduced {len(rows)} event(s) to "
            f"{len(results)} earliest pre-change target(s)"
        )

        targets = []
        targets_by_position = {}
        skipped_types = set()
        for row in results:
            row_id, actor_name, action = int(row[0]), str(row[1]), str(row[2])
            bx, by, bz = int(row[3]), int(row[4]), int(row[5])
            block_type_raw = str(row[6]) if row[6] else "air"
            dimension = str(row[7] or "overworld")
            blockdata_str = row[9] if row[9] else ""
            saved_data = {}
            if blockdata_str:
                try:
                    decoded = json.loads(blockdata_str)
                    if isinstance(decoded, dict):
                        saved_data = decoded
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            if action.startswith('Container '):
                saved_snapshot = self._load_container_snapshot(
                    saved_data.get('before_snapshot_id') or saved_data.get('snapshot_id')
                )
                if saved_snapshot is None and isinstance(saved_data.get('before_snapshot'), dict):
                    saved_snapshot = saved_data['before_snapshot']
                if saved_snapshot is None:
                    skipped_types.add(f"container_snapshot:{row_id}")
                    continue
                block_type_raw = str(saved_snapshot.get('type') or block_type_raw)
                states = saved_snapshot.get('states') or {}
                block_type = self._normalize_rollback_block_type(block_type_raw)
            else:
                saved_snapshot = self._saved_native_snapshot(saved_data)
                if "Place" in action:
                    block_type = "minecraft:air"
                    states = {}
                    saved_snapshot = None
                else:
                    if saved_snapshot:
                        block_type_raw = str(saved_snapshot.get('type') or block_type_raw)
                        states = saved_snapshot.get('states') or saved_data.get('block_states') or {}
                    else:
                        states = saved_data.get('block_states') or {}
                    block_type = self._normalize_rollback_block_type(block_type_raw)

            if not block_type or block_type == "minecraft:none" or len(block_type) < 4:
                skipped_types.add(block_type_raw)
                continue

            target = {
                'row_id': row_id,
                'actor_name': actor_name,
                'action': action,
                'x': bx, 'y': by, 'z': bz,
                'dimension': dimension,
                'block_type': block_type,
                'states': states,
                'saved_snapshot': saved_snapshot,
                'legacy_items': saved_data.get('container_items') or [],
                'is_container': bool(
                    saved_snapshot and self.blockdata.is_container(saved_snapshot)
                ) or bool(saved_data.get('container_items')),
            }
            targets.append(target)
            targets_by_position[(self._normalise_world_key(dimension), bx, by, bz)] = target

        report = self._build_grief_report(
            rollback_id, sender, x, y, z, hours, radius, player_filter,
            rows, targets,
        )
        report_id = self._store_grief_report(report)
        self.logger.info(
            f"[GriefReport] Created {report_id} for rollback {rollback_id} "
            f"with {len(rows)} evidence event(s)"
        )

        air_targets = sorted(
            (target for target in targets if target['block_type'] == 'minecraft:air'),
            key=lambda target: (-target['y'], target['x'], target['z']),
        )
        solid_targets = sorted(
            (
                target for target in targets
                if target['block_type'] != 'minecraft:air' and not target['is_container']
            ),
            key=lambda target: (target['y'], target['x'], target['z']),
        )
        container_targets = sorted(
            (
                target for target in targets
                if target['block_type'] != 'minecraft:air' and target['is_container']
            ),
            key=lambda target: (target['y'], target['x'], target['z']),
        )

        processed = 0
        retry_queued = 0
        successful_containers = []
        for target in [*air_targets, *solid_targets, *container_targets]:
            try:
                if self._attempt_rollback_block(target):
                    processed += 1
                    if target['is_container']:
                        successful_containers.append(target)
                elif self._schedule_rollback_block_retry(target):
                    retry_queued += 1
            except Exception as error:
                if self._schedule_rollback_block_retry(target):
                    retry_queued += 1
                self.logger.warning(
                    f"Rollback error at {target['x']},{target['y']},{target['z']} "
                    f"({target['block_type']}) in {target['dimension']}: {error}"
                )

        native_restores = 0
        for target in successful_containers:
            if self._queue_post_block_restore(target):
                native_restores += 1

        # Build recovery candidates from exact container deltas and broken-container
        # snapshots. They become active only because this OP executed /agback.
        recovery_candidates = []
        for row in rows:
            row_id, player_name, action = int(row[0]), str(row[1]), str(row[2])
            bx, by, bz = int(row[3]), int(row[4]), int(row[5])
            dimension = str(row[7] or 'overworld')
            try:
                payload = json.loads(row[9]) if row[9] else {}
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            if action in {'Container Take', 'Container Change'}:
                item = payload.get('before_item')
                slot = payload.get('slot')
                if not isinstance(item, dict) or self.blockdata.is_empty_item(item):
                    continue
                if action == 'Container Take':
                    amount = max(1, int(payload.get('amount') or self.blockdata.item_count(item)))
                else:
                    after_item = payload.get('after_item')
                    if after_item and self._same_canonical_item(item, after_item):
                        amount = max(
                            0,
                            self.blockdata.item_count(item) - self.blockdata.item_count(after_item),
                        )
                    else:
                        amount = self.blockdata.item_count(item)
                if amount > 0:
                    recovery_candidates.append({
                        'row_id': row_id, 'player_name': player_name,
                        'owner_name': payload.get('owner_name'), 'world': dimension,
                        'x': bx, 'y': by, 'z': bz, 'slot': slot,
                        'item': item, 'amount': amount, 'reason': action,
                    })
            elif 'Break' in action:
                snapshot = self._saved_native_snapshot(payload)
                if snapshot is not None and self.blockdata.is_container(snapshot):
                    for slot, item in self.blockdata.inventory_map(snapshot).items():
                        recovery_candidates.append({
                            'row_id': row_id, 'player_name': player_name,
                            'owner_name': payload.get('container_owner'), 'world': dimension,
                            'x': bx, 'y': by, 'z': bz, 'slot': slot,
                            'item': item, 'amount': self.blockdata.item_count(item),
                            'reason': 'Container Break',
                        })

        queued_recoveries = 0
        affected_players = set()
        # Cap each origin slot/signature to the historical target count so an access
        # delta plus a later break snapshot cannot over-confiscate duplicate items.
        allocated = defaultdict(int)
        for candidate in recovery_candidates:
            position_key = (
                self._normalise_world_key(candidate['world']), candidate['x'],
                candidate['y'], candidate['z'],
            )
            target = targets_by_position.get(position_key)
            target_snapshot = (target or {}).get('saved_snapshot')
            target_item = self.blockdata.inventory_map(target_snapshot).get(
                int(candidate['slot']) if candidate['slot'] is not None else -1
            ) if target_snapshot else None
            if target_snapshot is not None:
                budget = (
                    self.blockdata.item_count(target_item)
                    if target_item and self._same_canonical_item(target_item, candidate['item'])
                    else 0
                )
            else:
                budget = int(candidate['amount'])
            signature_key = (
                *position_key, candidate['slot'], self._canonical_item_signature(candidate['item'])
            )
            remaining_budget = max(0, int(budget) - allocated[signature_key])
            amount = min(max(0, int(candidate['amount'])), remaining_budget)
            if amount <= 0:
                continue
            allocated[signature_key] += amount
            theft_key = (
                f"rollback:{rollback_id}:event:{candidate['row_id']}:"
                f"slot:{candidate['slot']}:player:{candidate['player_name'].casefold()}"
            )
            if self._queue_confiscation(
                candidate['player_name'], candidate.get('owner_name'), candidate['world'],
                candidate['x'], candidate['y'], candidate['z'], candidate['item'], amount,
                f"rollback_recovery:{candidate['reason']}", theft_key=theft_key,
                destination_slot=candidate['slot'], rollback_id=rollback_id,
            ):
                queued_recoveries += 1
                affected_players.add(candidate['player_name'])

        def run_recovery_batch():
            for player_name in sorted(affected_players, key=str.casefold):
                try:
                    self._apply_pending_confiscations(player_name, rollback_id)
                except Exception as error:
                    self.logger.warning(
                        f"[Rollback Recovery] Batch {rollback_id} failed for {player_name}: {error}"
                    )
        if affected_players:
            try:
                self.server.scheduler.run_task(self, run_recovery_batch, delay=40)
            except Exception:
                run_recovery_batch()

        report_execution = {
            'initial_blocks_verified': processed,
            'container_restores_queued': native_restores,
            'block_retries_queued': retry_queued,
            'item_recoveries_queued': queued_recoveries,
            'invalid_records_skipped': len(skipped_types),
        }
        self._update_grief_report_execution(report_id, report_execution)
        self._schedule_grief_report_finalize(report_id, rollback_id, targets)

        result_msg = f'{ColorFormat.GREEN}Rollback pass: {processed} blocks verified'
        if native_restores:
            result_msg += f', {native_restores} container restores queued'
        if retry_queued:
            result_msg += f', {retry_queued} block retries queued'
        if queued_recoveries:
            result_msg += f', {queued_recoveries} confirmed item recoveries queued'
        if skipped_types:
            result_msg += f' {ColorFormat.YELLOW}({len(skipped_types)} invalid records skipped)'
            self.logger.warning(f"Rollback skipped records/types: {list(skipped_types)[:5]}")
        sender.send_message(result_msg)
        sender.send_message(
            f'{ColorFormat.AQUA}Grief proof report {report_id} created. '
            f'It will finalize in the AntiGrief WebUI after rollback verification.'
        )

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

        container_owner = None
        container_authorized = True
        if self._is_container_block_type(block_type_str):
            container_owner, container_authorized = self._is_container_authorized(
                player, dimension, bx, by, bz
            )
            if container_owner and not container_authorized:
                self.logger.warning(
                    f"[Container] Unauthorized break: {player_name} broke {container_owner}'s "
                    f"{block_type_str} at {bx},{by},{bz} in {dimension}"
                )

        saved_data = {}
        try:
            saved_data = self._build_block_backup(
                block, dimension, player_name, 'block_break'
            )
            if isinstance(saved_data, dict) and self._is_container_block_type(block_type_str):
                saved_data['container_owner'] = container_owner
                saved_data['authorized'] = bool(container_authorized)
                saved_data['unauthorized'] = not bool(container_authorized)
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
        player_name = str(player.name)
        bx, by, bz = int(block.x), int(block.y), int(block.z)
        dimension = str(player.location.dimension.name)
        
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
        placement_key = f"{player_name}:{bx},{by},{bz}"
        self._recent_placements[placement_key] = tm.time()
        
        data_buffers['place'].append({
            'name': player_name,
            'action': lang["action_place"],
            'coordinates': {'x': bx, 'y': by, 'z': bz},
            'type': block_type_str,
            'world': dimension,
            'time': now_est().isoformat()
        })

        if CONTAINER_OWNERSHIP_ENABLED:
            def register_owner():
                actual_type = block_type_str
                if not self._is_container_block_type(actual_type) and self._blockdata_ready:
                    try:
                        captured = self.blockdata.capture(self.server, dimension, bx, by, bz)
                        actual_type = str((captured or {}).get('type') or actual_type)
                    except Exception:
                        pass
                if self._is_container_block_type(actual_type):
                    self._set_container_owner(
                        dimension, bx, by, bz, player_name, 'container_placement'
                    )
                    self.logger.info(
                        f"[Container] Owner registered: {player_name} @ {bx},{by},{bz} ({actual_type})"
                    )
            try:
                self.server.scheduler.run_task(self, register_owner, delay=1)
            except Exception:
                register_owner()
        
    
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
            owner_name = tracked.get('owner_name')
            authorized = bool(tracked.get('authorized', True))
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
                    'owner_name': owner_name,
                    'authorized': authorized,
                    'unauthorized': not authorized,
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
        
        # Resolve ownership before opening the native tracking session.
        container_owner = None
        container_authorized = True
        if self._is_container_block_type(block_type_str):
            container_owner, container_authorized = self._is_container_authorized(
                player, dim_name, bx, by, bz
            )
            if container_owner and not container_authorized:
                self.logger.warning(
                    f"[Container] Unauthorized access: {player_name} opened {container_owner}'s "
                    f"{block_type_str} at {bx},{by},{bz} in {dim_name}"
                )

        # Capture the actual container actor, every occupied slot, and canonical NBT.
        if self._is_container_block_type(block_type_str) and CAPTURE_CONTAINER_OPEN_CLOSE:
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
                    'owner_name': container_owner,
                    'authorized': container_authorized,
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
                        'owner_name': container_owner,
                        'authorized': container_authorized,
                        'time': tm.time(),
                    }

        # Link an ordinary chest interaction to the exact opening snapshot so the
        # WebUI can show its complete inventory even when no slot changed.
        interaction_blockdata = ""
        tracked = container_snapshots.get(player_name)
        if tracked and tracked.get('x') == bx and tracked.get('y') == by and tracked.get('z') == bz:
            opening_snapshot = tracked.get('blockdata_snapshot')
            interaction_blockdata = json.dumps({
                'schema_version': BlockDataAdapter.SCHEMA_VERSION,
                'provider': BlockDataAdapter.PROVIDER,
                'container_type': block_type_str,
                'snapshot_id': tracked.get('snapshot_id'),
                'revision': (opening_snapshot or {}).get('revision'),
                'reason': 'container_open',
                'owner_name': tracked.get('owner_name'),
                'authorized': bool(tracked.get('authorized', True)),
                'unauthorized': not bool(tracked.get('authorized', True)),
            }, ensure_ascii=False, separators=(',', ':'))

        data_buffers['chest'].append({
            'name': player_name,
            'action': lang["action_interact"],
            'coordinates': {'x': bx, 'y': by, 'z': bz},
            'type': block_type_str,
            'world': dim_name,
            'time': now_est().isoformat(),
            'blockdata': interaction_blockdata,
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
        
        # Retry only administrator-confirmed rollback recoveries after inventory is ready.
        if ROLLBACK_RECOVERY_ENABLED:
            player_name = str(player.name)
            try:
                self.server.scheduler.run_task(
                    self, lambda: self._apply_pending_confiscations(player_name), delay=20
                )
            except Exception:
                self._apply_pending_confiscations(player_name)

        if CAPTURE_PLAYER_INVENTORIES and self.blockdata.player_inventory_available:
            try:
                self.server.scheduler.run_task(
                    self, lambda: self._capture_player_inventory(player, force=True), delay=40
                )
            except Exception:
                self._capture_player_inventory(player, force=True)

        # Log join
        self.logger.info(f'{ColorFormat.GREEN}{player.name} ({lang["system_name"]}: {player.device_os if hasattr(player, "device_os") else "Unknown"}) {lang["joined_game"]}')
    
    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        """Keep the last exact snapshot but mark it as offline in the WebUI."""
        try:
            self._capture_player_inventory(event.player, force=True)
        except Exception:
            pass
        self._mark_player_inventory_offline(event.player)

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

"""
AntiGrief WebUI - Security Operations Dashboard
Self-contained FastAPI application for viewing behavior logs
"""

import os
import json
import sqlite3
import threading
import html
from datetime import datetime, timedelta, timezone

# Eastern timezone (same as main plugin)
try:
    from zoneinfo import ZoneInfo
    EASTERN_TZ = ZoneInfo("America/New_York")
except ImportError:
    EASTERN_TZ = timezone(timedelta(hours=-5))

def now_est():
    return datetime.now(EASTERN_TZ)
from typing import Optional

# Check for required dependencies
try:
    from fastapi import FastAPI, Query, Depends, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    WEBUI_AVAILABLE = True
except ImportError:
    WEBUI_AVAILABLE = False

# Data paths
DATA_DIR = "plugins/antigrief_data"
DB_FILE = os.path.join(DATA_DIR, "agdata.db")
WEB_CONFIG_FILE = os.path.join(DATA_DIR, "web_config.json")

# Global state
_server_thread = None
_app = None


def _escape(value):
    return html.escape(str(value if value is not None else ''))


def _contained_item_payload(value):
    """Normalize a direct item compound or an inventory-entry wrapper."""
    if not isinstance(value, dict):
        return None
    nested = value.get('item')
    return nested if isinstance(nested, dict) else value


def _is_empty_item_nbt(value):
    """Hide Bedrock empty-slot placeholders from human-readable item views."""
    item = _contained_item_payload(value)
    if not isinstance(item, dict) or not item:
        return True
    if item.get('empty') is True:
        return True
    identifier = item.get('id', item.get('name', item.get('Name')))
    if str(identifier or '').casefold() in {'', 'air', 'minecraft:air'}:
        return True
    count = item.get('count', item.get('Count', 1))
    try:
        return int(count) <= 0
    except (TypeError, ValueError):
        return False


def _occupied_nested_items(values):
    if not isinstance(values, list):
        return []
    return [
        _contained_item_payload(entry)
        for entry in values
        if isinstance(entry, dict) and not _is_empty_item_nbt(entry)
    ]


def _item_nested_groups(item):
    """Return occupied bundle/storage-item and shulker contents only."""
    if not isinstance(item, dict):
        return []
    tag = item.get('tag', item.get('user_data', {}))
    tag = tag if isinstance(tag, dict) else {}
    groups = []
    storage = tag.get('storage_item_component_content')
    if isinstance(storage, list):
        groups.append(('Bundle / storage contents', _occupied_nested_items(storage)))
    block_entity = tag.get('BlockEntityTag', tag.get('block_entity_tag', {}))
    block_entity = block_entity if isinstance(block_entity, dict) else {}
    shulker = tag.get('Items') or tag.get('items') or block_entity.get('Items') or block_entity.get('items')
    if isinstance(shulker, list):
        groups.append(('Contained block inventory', _occupied_nested_items(shulker)))
    return groups


def _report_item_details(item_summary, depth=0):
    if not isinstance(item_summary, dict):
        return '<span class="muted">No occupied item</span>'
    raw = item_summary.get('item') if isinstance(item_summary.get('item'), dict) else item_summary
    item_id = item_summary.get('item_id') or raw.get('id') or raw.get('Name') or 'unknown'
    count = item_summary.get('count', raw.get('count', raw.get('Count', 1)))
    tag = raw.get('tag', raw.get('user_data', {}))
    tag = tag if isinstance(tag, dict) else {}
    display = tag.get('display', tag.get('Display', {}))
    display = display if isinstance(display, dict) else {}
    custom_name = item_summary.get('custom_name') or raw.get('CustomName') or display.get('Name')
    raw_lore = display.get('Lore') or display.get('lore') or tag.get('Lore') or tag.get('lore') or []
    lore = item_summary.get('lore') if isinstance(item_summary.get('lore'), list) else (raw_lore if isinstance(raw_lore, list) else [raw_lore])
    raw_enchantments = tag.get('ench') or tag.get('Enchantments') or tag.get('enchantments') or []
    enchantments = item_summary.get('enchantments') if isinstance(item_summary.get('enchantments'), list) else (raw_enchantments if isinstance(raw_enchantments, list) else [])
    slot = item_summary.get('slot', raw.get('Slot', raw.get('slot')))
    title = custom_name or str(item_id).replace('minecraft:', '').replace('_', ' ')
    bits = [
        f'<div class="item-title">{_escape(title)} ×{_escape(count)}</div>',
        f'<div class="item-id">{_escape(item_id)}'
        + (f' · slot {_escape(slot)}' if slot is not None else '') + '</div>',
    ]
    if enchantments:
        rendered = []
        for enchantment in enchantments:
            if isinstance(enchantment, dict):
                name = enchantment.get('name', enchantment.get('id', enchantment.get('value', 'enchantment')))
                level = enchantment.get('level', enchantment.get('lvl', ''))
                rendered.append(f'{name} {level}'.strip())
            else:
                rendered.append(str(enchantment))
        bits.append('<div class="item-enchants">✦ ' + _escape(', '.join(rendered)) + '</div>')
    if lore:
        bits.append('<div class="item-lore">' + '<br>'.join(_escape(line) for line in lore) + '</div>')
    if depth < 8:
        for label, items in _item_nested_groups(raw):
            children = ''.join(
                '<div class="item-card nested-report-item">'
                + _report_item_details({'item': child, 'slot': child.get('Slot', child.get('slot'))}, depth + 1)
                + '</div>'
                for child in items
            ) or '<span class="muted">Empty</span>'
            bits.append(
                '<details class="nested-report"><summary>' + _escape(label)
                + f' ({len(items)} occupied)</summary>{children}</details>'
            )
    return ''.join(bits)


def _render_grief_report_page(record):
    report = record.get('report') if isinstance(record, dict) else {}
    report = report if isinstance(report, dict) else {}
    summary = report.get('summary') if isinstance(report.get('summary'), dict) else {}
    area = report.get('area') if isinstance(report.get('area'), dict) else {}
    query = area.get('query') if isinstance(area.get('query'), dict) else {}
    center = query.get('center') if isinstance(query.get('center'), dict) else {}
    players = report.get('players') if isinstance(report.get('players'), list) else []
    containers = report.get('containers') if isinstance(report.get('containers'), list) else []
    events = report.get('events') if isinstance(report.get('events'), list) else []
    rollback = report.get('rollback') if isinstance(report.get('rollback'), dict) else {}
    verification = rollback.get('verification') if isinstance(rollback.get('verification'), dict) else {}
    recovery = rollback.get('recovery') if isinstance(rollback.get('recovery'), dict) else {}
    execution = rollback.get('execution') if isinstance(rollback.get('execution'), dict) else {}

    player_rows = ''.join(
        '<tr><td>' + _escape(player.get('name')) + '</td><td>'
        + _escape(player.get('event_count', 0)) + '</td></tr>'
        for player in players if isinstance(player, dict)
    ) or '<tr><td colspan="2" class="muted">No player evidence recorded</td></tr>'

    action_rows = ''.join(
        '<tr><td>' + _escape(name) + '</td><td>' + _escape(count) + '</td></tr>'
        for name, count in sorted((summary.get('actions') or {}).items(), key=lambda entry: (-entry[1], entry[0]))
    ) or '<tr><td colspan="2" class="muted">No actions recorded</td></tr>'

    block_rows = ''.join(
        '<tr><td>' + _escape(name) + '</td><td>' + _escape(count) + '</td></tr>'
        for name, count in sorted((summary.get('block_types') or {}).items(), key=lambda entry: (-entry[1], entry[0]))
    ) or '<tr><td colspan="2" class="muted">No block types recorded</td></tr>'

    container_rows = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        items = container.get('items') if isinstance(container.get('items'), list) else []
        item_html = ''.join('<div class="item-card">' + _report_item_details(item) + '</div>' for item in items)
        if not item_html:
            item_html = '<span class="muted">No item delta stored</span>'
        position = f"{container.get('x')}, {container.get('y')}, {container.get('z')}"
        container_rows.append(
            '<tr><td>' + _escape(container.get('world')) + '</td><td>' + _escape(position) + '</td>'
            '<td>' + _escape(container.get('container_type')) + '</td><td>'
            + _escape(', '.join(container.get('players') or [])) + '</td><td>'
            + _escape(', '.join(container.get('actions') or [])) + '</td><td>' + item_html + '</td></tr>'
        )
    container_html = ''.join(container_rows) or '<tr><td colspan="6" class="muted">No container incidents recorded</td></tr>'

    event_rows = []
    for event in events:
        if not isinstance(event, dict):
            continue
        pos = event.get('position') if isinstance(event.get('position'), dict) else {}
        item_bits = ''.join(_report_item_details(item) for item in (event.get('items') or []) if isinstance(item, dict))
        event_rows.append(
            '<tr><td>' + _escape(event.get('time')) + '</td><td>' + _escape(event.get('player'))
            + '</td><td>' + _escape(event.get('action')) + '</td><td>' + _escape(event.get('target'))
            + '</td><td>' + _escape(f"{event.get('world')} {pos.get('x')}, {pos.get('y')}, {pos.get('z')}")
            + '</td><td>' + (item_bits or '<span class="muted">—</span>') + '</td></tr>'
        )
    event_html = ''.join(event_rows) or '<tr><td colspan="6" class="muted">No timeline entries recorded</td></tr>'

    verification_rows = []
    for position in verification.get('positions') or []:
        if not isinstance(position, dict):
            continue
        pos = f"{position.get('x')}, {position.get('y')}, {position.get('z')}"
        block_state = 'Verified' if position.get('block_restored') else 'Failed'
        inventory = position.get('container_inventory_restored')
        inventory_state = 'N/A' if inventory is None else ('Verified' if inventory else 'Failed')
        verification_rows.append(
            '<tr><td>' + _escape(position.get('world')) + '</td><td>' + _escape(pos) + '</td><td>'
            + _escape(position.get('expected_block')) + '</td><td>' + _escape(position.get('actual_block'))
            + '</td><td>' + _escape(block_state) + '</td><td>' + _escape(inventory_state) + '</td></tr>'
        )
    verification_html = ''.join(verification_rows) or '<tr><td colspan="6" class="muted">Rollback verification is still processing.</td></tr>'

    bounds_html = ''.join(
        '<li><strong>' + _escape(world) + ':</strong> '
        + _escape(f"({bounds.get('min_x')}, {bounds.get('min_y')}, {bounds.get('min_z')}) to ({bounds.get('max_x')}, {bounds.get('max_y')}, {bounds.get('max_z')})")
        + '</li>'
        for world, bounds in (area.get('bounds_by_world') or {}).items()
        if isinstance(bounds, dict)
    ) or '<li class="muted">No bounds available</li>'

    status = report.get('status') or record.get('status') or 'processing'
    return f'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AntiGrief Report {_escape(report.get('report_id'))}</title>
<style>
:root{{--ink:#111827;--muted:#64748b;--line:#cbd5e1;--panel:#f8fafc;--red:#991b1b;--green:#166534;--blue:#1d4ed8}}
*{{box-sizing:border-box}} body{{font-family:Arial,Helvetica,sans-serif;color:var(--ink);margin:0;background:#e2e8f0}}
.page{{max-width:1100px;margin:24px auto;background:white;padding:34px;box-shadow:0 12px 40px rgba(15,23,42,.18)}}
.toolbar{{display:flex;justify-content:flex-end;gap:8px;margin-bottom:18px}} button{{padding:10px 16px;border:0;border-radius:5px;background:#0f172a;color:white;font-weight:700;cursor:pointer}}
h1{{margin:0;font-size:28px;letter-spacing:.04em}} h2{{font-size:17px;border-bottom:2px solid #0f172a;padding-bottom:6px;margin-top:28px}} h3{{font-size:14px;margin:0 0 8px}}
.kicker{{font-size:12px;text-transform:uppercase;letter-spacing:.18em;color:var(--muted)}} .muted{{color:var(--muted)}}
.report-head{{display:flex;justify-content:space-between;gap:20px;border-bottom:4px solid #0f172a;padding-bottom:18px}}
.status{{display:inline-block;padding:5px 9px;border:1px solid var(--line);border-radius:999px;font-size:12px;font-weight:700;text-transform:uppercase}}
.meta-grid,.summary-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:18px}}
.meta,.summary-card{{border:1px solid var(--line);background:var(--panel);padding:12px;border-radius:6px}} .label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}} .value{{font-size:16px;font-weight:700;margin-top:4px;overflow-wrap:anywhere}}
table{{width:100%;border-collapse:collapse;font-size:12px}} th,td{{border:1px solid var(--line);padding:7px;vertical-align:top;text-align:left}} th{{background:#e2e8f0;text-transform:uppercase;font-size:10px;letter-spacing:.08em}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} .item-card{{padding:5px 0;border-bottom:1px dashed var(--line)}} .item-title{{font-weight:700}} .item-id{{font-family:monospace;color:var(--muted);font-size:10px}} .item-enchants{{color:#3730a3}} .item-lore{{color:#6b21a8;font-style:italic}}
.hash{{font-family:monospace;font-size:10px;word-break:break-all;background:#f1f5f9;padding:8px;border-radius:4px}} ul{{margin:6px 0;padding-left:20px}}
.nested-report{{margin-top:6px}} .nested-report summary{{cursor:pointer;color:#5b21b6;font-weight:700}} .nested-report-item{{margin:6px 0 0 12px;padding:6px;border-left:2px solid #c4b5fd}}
.disclaimer{{margin-top:28px;padding:12px;border-left:4px solid #0f172a;background:#f8fafc;font-size:11px;color:#334155}}
@media(max-width:800px){{.meta-grid,.summary-grid{{grid-template-columns:1fr 1fr}}.two-col{{grid-template-columns:1fr}}.page{{margin:0;padding:18px}}}}
@media print{{body{{background:white}}.page{{box-shadow:none;margin:0;max-width:none;padding:12mm}}.toolbar{{display:none}}@page{{size:auto;margin:8mm}}}}
</style></head><body><div class="page">
<div class="toolbar"><button onclick="window.print()">Print / Save PDF</button></div>
<div class="report-head"><div><div class="kicker">AntiGrief administrator-confirmed evidence</div><h1>Grief Incident Report</h1><div class="muted">Report {_escape(report.get('report_id'))}</div></div><div><span class="status">{_escape(status.replace('_',' '))}</span></div></div>
<div class="meta-grid">
<div class="meta"><div class="label">Primary griefer</div><div class="value">{_escape(report.get('primary_player'))}</div></div>
<div class="meta"><div class="label">Confirming admin</div><div class="value">{_escape(report.get('admin'))}</div></div>
<div class="meta"><div class="label">Created</div><div class="value">{_escape(report.get('created_at'))}</div></div>
<div class="meta"><div class="label">Rollback ID</div><div class="value">{_escape(report.get('rollback_id'))}</div></div>
</div>
<h2>Incident Scope</h2><div class="two-col"><div><p><strong>Search center:</strong> {_escape(center.get('x'))}, {_escape(center.get('y'))}, {_escape(center.get('z'))}</p><p><strong>Radius:</strong> {_escape(query.get('radius'))} blocks</p><p><strong>Evidence window:</strong> {_escape(query.get('hours'))} hours</p><p><strong>Player filter:</strong> {_escape(query.get('player_filter') or 'All involved players')}</p></div><div><h3>Affected bounds</h3><ul>{bounds_html}</ul></div></div>
<div class="summary-grid">
<div class="summary-card"><div class="label">Evidence events</div><div class="value">{_escape(summary.get('event_count',0))}</div></div>
<div class="summary-card"><div class="label">Affected positions</div><div class="value">{_escape(summary.get('affected_positions',0))}</div></div>
<div class="summary-card"><div class="label">Blocks broken</div><div class="value">{_escape(summary.get('blocks_broken',0))}</div></div>
<div class="summary-card"><div class="label">Blocks placed</div><div class="value">{_escape(summary.get('blocks_placed',0))}</div></div>
<div class="summary-card"><div class="label">Containers looted</div><div class="value">{_escape(summary.get('containers_looted',0))}</div></div>
<div class="summary-card"><div class="label">Containers broken</div><div class="value">{_escape(summary.get('containers_broken',0))}</div></div>
<div class="summary-card"><div class="label">Items reported</div><div class="value">{_escape(summary.get('items_reported',0))}</div></div>
<div class="summary-card"><div class="label">Items recovered</div><div class="value">{_escape(summary.get('items_recovered',0))}</div></div>
</div>
<h2>Players and Activity</h2><div class="two-col"><table><thead><tr><th>Player</th><th>Evidence events</th></tr></thead><tbody>{player_rows}</tbody></table><table><thead><tr><th>Action</th><th>Count</th></tr></thead><tbody>{action_rows}</tbody></table></div>
<h2>Blocks Involved</h2><table><thead><tr><th>Block or target</th><th>Recorded events</th></tr></thead><tbody>{block_rows}</tbody></table>
<h2>Containers Looted or Broken</h2><table><thead><tr><th>World</th><th>Position</th><th>Container</th><th>Player(s)</th><th>Action(s)</th><th>Items / NBT evidence</th></tr></thead><tbody>{container_html}</tbody></table>
<h2>Rollback Verification</h2><p><strong>Initial execution:</strong> {_escape(json.dumps(execution, ensure_ascii=False))}</p><p><strong>Recovery:</strong> {_escape(json.dumps(recovery, ensure_ascii=False))}</p><table><thead><tr><th>World</th><th>Position</th><th>Expected</th><th>Actual</th><th>Block</th><th>Inventory</th></tr></thead><tbody>{verification_html}</tbody></table>
<h2>Evidence Timeline</h2><table><thead><tr><th>Time</th><th>Player</th><th>Action</th><th>Target</th><th>Location</th><th>Item evidence</th></tr></thead><tbody>{event_html}</tbody></table>
<h2>Evidence Integrity</h2><p>The hash below covers the selected command scope, source event evidence, and rollback targets captured when the administrator ran <code>/agback</code>.</p><div class="hash">SHA-256: {_escape(report.get('evidence_hash'))}</div>
<div class="disclaimer">This report is generated from AntiGrief server logs after an administrator confirms the incident by running /agback. It records server evidence and rollback results; it is not an independent legal finding.</div>
</div></body></html>'''


def get_web_config():
    """Load or create WebUI configuration"""
    default_config = {
        "secret": "change_this_secret_key",
        "port": 8098,
        "max_results": 10000
    }
    
    if not os.path.exists(WEB_CONFIG_FILE):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(WEB_CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        return default_config
    
    with open(WEB_CONFIG_FILE, 'r') as f:
        return json.load(f)


def create_app():
    """Create and configure the FastAPI application"""
    app = FastAPI(
        title="AntiGrief WebUI",
        description="Player Behavior Logging Dashboard",
        version="1.5.13"
    )
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    web_config = get_web_config()
    SECRET_KEY = web_config.get("secret", "change_this_secret_key")
    MAX_RESULTS = web_config.get("max_results", 10000)
    
    def verify_secret(request: Request):
        """Verify API secret key"""
        secret = request.headers.get("X-Secret-Key") or request.query_params.get("secret")
        if secret != SECRET_KEY:
            raise HTTPException(status_code=401, detail="Invalid secret key")
        return True

    def _decode_json(value):
        if not value:
            return {}
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {"value": decoded}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"raw": str(value)}

    def _payload_snapshot_id(payload):
        if not isinstance(payload, dict):
            return None
        provider_data = payload.get("blockdata_api")
        return (
            payload.get("snapshot_id")
            or payload.get("after_snapshot_id")
            or payload.get("before_snapshot_id")
            or (provider_data.get("snapshot_id") if isinstance(provider_data, dict) else None)
        )

    def _revision_expression(db):
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(container_snapshots)")}
        except sqlite3.Error:
            columns = set()
        if "revision_text" in columns:
            return "COALESCE(revision_text, CAST(revision AS TEXT))"
        return "CAST(revision AS TEXT)"

    def _load_snapshot_record(db, snapshot_id):
        if not snapshot_id:
            return None
        revision_expr = _revision_expression(db)
        row = db.execute(
            f"""SELECT snapshot_id, player_name, reason, x, y, z, world,
                       block_type, {revision_expr}, captured_at, occupied_slots,
                       item_count, canonical_nbt, snapshot_json, raw_snbt
                FROM container_snapshots WHERE snapshot_id = ?""",
            (str(snapshot_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "metadata": {
                "snapshot_id": row[0], "player": row[1], "reason": row[2],
                "x": row[3], "y": row[4], "z": row[5], "dimension": row[6],
                "block_type": row[7], "revision": row[8], "captured_at": row[9],
                "occupied_slots": row[10], "item_count": row[11],
                "canonical_nbt": bool(row[12]),
            },
            "snapshot": _decode_json(row[13]),
            "raw_snbt": row[14] or "",
        }

    def _load_player_inventory_record(db, player_key):
        if not player_key or not _table_exists(db, 'player_inventory_snapshots'):
            return None
        row = db.execute(
            """SELECT player_key,snapshot_id,player_name,xuid,captured_at,online,
                      COALESCE(revision_text, CAST(revision AS TEXT)),selected_hotbar_slot,
                      main_size,armor_size,offhand_size,ender_chest_size,
                      occupied_main,occupied_armor,occupied_offhand,occupied_ender_chest,
                      item_count,storage_item_count,snapshot_json
               FROM player_inventory_snapshots WHERE player_key=?""",
            (str(player_key),),
        ).fetchone()
        if row is None:
            return None
        return {
            'metadata': {
                'player_key': row[0], 'snapshot_id': row[1], 'player_name': row[2],
                'xuid': row[3] or '', 'captured_at': row[4], 'online': bool(row[5]),
                'revision': row[6], 'selected_hotbar_slot': row[7],
                'main_size': row[8], 'armor_size': row[9], 'offhand_size': row[10],
                'ender_chest_size': row[11], 'occupied_main': row[12],
                'occupied_armor': row[13], 'occupied_offhand': row[14],
                'occupied_ender_chest': row[15], 'item_count': row[16],
                'storage_item_count': row[17],
            },
            'snapshot': _decode_json(row[18]),
        }

    def _nearest_snapshot_id(db, world, x, y, z, event_time, max_seconds=900):
        try:
            row = db.execute(
                """SELECT snapshot_id
                   FROM container_snapshots
                   WHERE world = ? AND x = ? AND y = ? AND z = ?
                     AND ABS((julianday(captured_at) - julianday(?)) * 86400.0) <= ?
                   ORDER BY ABS(julianday(captured_at) - julianday(?)) ASC
                   LIMIT 1""",
                (world, x, y, z, event_time, max_seconds, event_time),
            ).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def _table_exists(db, table_name):
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(table_name),),
        ).fetchone()
        return bool(row)

    def _load_grief_report_record(db, report_id):
        if not _table_exists(db, 'grief_reports'):
            return None
        row = db.execute(
            """SELECT report_id,rollback_id,created_at,completed_at,admin_name,status,
                      center_x,center_y,center_z,radius,hours,player_filter,
                      primary_player,event_count,affected_positions,blocks_broken,
                      blocks_placed,explosions,containers_looted,containers_broken,
                      items_reported,items_recovered,evidence_hash,players_json,
                      worlds_json,summary_json,report_json
               FROM grief_reports WHERE report_id=?""",
            (str(report_id),),
        ).fetchone()
        if row is None:
            return None
        try:
            report = json.loads(row[26])
        except Exception:
            report = {}
        try:
            players = json.loads(row[23])
        except Exception:
            players = []
        try:
            worlds = json.loads(row[24])
        except Exception:
            worlds = []
        try:
            summary = json.loads(row[25])
        except Exception:
            summary = {}
        return {
            'report_id': row[0], 'rollback_id': row[1], 'created_at': row[2],
            'completed_at': row[3], 'admin': row[4], 'status': row[5],
            'center': {'x': row[6], 'y': row[7], 'z': row[8]},
            'radius': row[9], 'hours': row[10], 'player_filter': row[11],
            'primary_player': row[12], 'event_count': row[13],
            'affected_positions': row[14], 'blocks_broken': row[15],
            'blocks_placed': row[16], 'explosions': row[17],
            'containers_looted': row[18], 'containers_broken': row[19],
            'items_reported': row[20], 'items_recovered': row[21],
            'evidence_hash': row[22], 'players': players, 'worlds': worlds,
            'summary': summary, 'report': report,
        }

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Main dashboard page"""
        return get_dashboard_html()
    
    @app.get("/api/logs")
    async def get_logs(
        hours: float = Query(24, description="Hours to look back"),
        x: Optional[float] = Query(None, description="X coordinate center"),
        y: Optional[float] = Query(None, description="Y coordinate center"),
        z: Optional[float] = Query(None, description="Z coordinate center"),
        radius: Optional[float] = Query(None, description="Search radius"),
        player: Optional[str] = Query(None, description="Player name filter"),
        action: Optional[str] = Query(None, description="Action type filter"),
        limit: int = Query(100, description="Results per page"),
        offset: int = Query(0, description="Result offset for pagination"),
        _: bool = Depends(verify_secret)
    ):
        """Retrieve logs with optional filters and pagination"""
        if not os.path.exists(DB_FILE):
            return {"logs": [], "total": 0, "total_count": 0, "offset": 0, "limit": limit}
        
        time_threshold = now_est() - timedelta(hours=hours)
        
        where_clause = "WHERE time >= ?"
        params = [time_threshold.isoformat()]
        
        if x is not None and y is not None and z is not None and radius:
            where_clause += " AND (x - ?)*(x - ?) + (y - ?)*(y - ?) + (z - ?)*(z - ?) <= ?"
            params.extend([x, x, y, y, z, z, radius ** 2])
        
        if player:
            where_clause += " AND name LIKE ?"
            params.append(f"%{player}%")
        
        if action:
            where_clause += " AND action LIKE ?"
            params.append(f"%{action}%")
        
        # Get total count for pagination
        total_count = 0
        effective_limit = min(limit, MAX_RESULTS)
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            cur.execute(f"SELECT COUNT(*) FROM interactions {where_clause}", params)
            total_count = cur.fetchone()[0]
        
        query = f"""
            SELECT i.name, i.action, i.x, i.y, i.z, i.type, i.world, i.time,
                   i.blockdata, i.id,
                   (SELECT cs.snapshot_id
                      FROM container_snapshots cs
                     WHERE cs.world = i.world AND cs.x = i.x AND cs.y = i.y AND cs.z = i.z
                       AND julianday(cs.captured_at) BETWEEN julianday(i.time, '-15 minutes')
                                                        AND julianday(i.time, '+15 minutes')
                     ORDER BY cs.captured_at DESC
                     LIMIT 1) AS nearby_snapshot_id
              FROM interactions i {where_clause}
             ORDER BY i.time DESC LIMIT ? OFFSET ?
        """
        params.append(effective_limit)
        params.append(max(0, offset))
        
        results = []
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            cur.execute(query, params)
            for row in cur.fetchall():
                log_entry = {
                    "player": row[0],
                    "action": row[1],
                    "x": row[2],
                    "y": row[3],
                    "z": row[4],
                    "target": row[5],
                    "dimension": row[6],
                    "time": row[7],
                    "blockdata": row[8] if len(row) > 8 else None,
                    "id": row[9] if len(row) > 9 else None,
                    "snapshot_id": None,
                    "has_nbt": False,
                    "container_detail": "",
                    "shulker_html": ""
                }

                parsed_blockdata = _decode_json(log_entry["blockdata"])
                log_entry["snapshot_id"] = (
                    _payload_snapshot_id(parsed_blockdata)
                    or (row[10] if len(row) > 10 else None)
                )
                log_entry["has_nbt"] = bool(
                    log_entry["snapshot_id"]
                    or parsed_blockdata.get("block_snapshot")
                    or parsed_blockdata.get("before_item")
                    or parsed_blockdata.get("after_item")
                    or parsed_blockdata.get("before_nbt")
                    or parsed_blockdata.get("after_nbt")
                )
                
                # Pre-render container detail and shulker HTML server-side
                action = row[1]
                target_text = row[5] or ''
                blockdata_str = row[8] if len(row) > 8 else None
                if blockdata_str and action in ('Container Take', 'Container Add', 'Container Change', 'Container NBT Change'):
                    try:
                        bd = json.loads(blockdata_str)
                        ct = (bd.get('container_type') or '').replace('minecraft:', '')
                        item = (bd.get('item') or '').replace('minecraft:', '')
                        amt = bd.get('amount', 1)
                        arrow = '\u2192' if action == 'Container Add' else '\u2190'
                        log_entry["container_detail"] = f"{ct} {arrow} {item} x{amt}"
                        
                        # Build item metadata HTML (custom name, enchantments, lore)
                        meta_html = ""
                        cust_name = bd.get('custom_name')
                        if cust_name:
                            meta_html += (
                                f'<div style="color:#fbbf24;font-style:italic;font-size:0.7rem;'
                                f'margin-top:2px">&quot;{cust_name}&quot;</div>'
                            )
                        enchants = bd.get('enchantments', [])
                        if enchants:
                            ench_parts = []
                            for e in enchants:
                                ename = (e.get('name') or '').replace('minecraft:', '')
                                elvl = e.get('level', 1)
                                ench_parts.append(f"{ename} {elvl}")
                            meta_html += (
                                f'<div style="color:#22d3ee;font-size:0.65rem;margin-top:1px">'
                                f'&#x2728; {", ".join(ench_parts)}</div>'
                            )
                        lore_lines = bd.get('lore', [])
                        if lore_lines:
                            lore_text = "<br>".join(str(l) for l in lore_lines)
                            meta_html += (
                                f'<div style="color:#9ca3af;font-style:italic;font-size:0.6rem;'
                                f'margin-top:1px">{lore_text}</div>'
                            )
                        
                        sc = bd.get('shulker_contents')
                        if sc and len(sc) > 0:
                            # Build clickable details/summary with ALL items
                            rows_html = ""
                            for c in sc:
                                cname = (c.get('name') or '').replace('minecraft:', '')
                                cnt = c.get('count', 1)
                                # Main item row: name + count
                                rows_html += (
                                    f'<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.05)">'
                                    f'<div style="display:flex;justify-content:space-between">'
                                    f'<span style="color:var(--text-primary);font-size:0.75rem">{cname}</span>'
                                    f'<span style="color:var(--accent-green);font-weight:600;font-size:0.75rem">x{cnt}</span>'
                                    f'</div>'
                                )
                                # Custom name
                                c_cname = c.get('custom_name')
                                if c_cname:
                                    rows_html += (
                                        f'<div style="color:#fbbf24;font-style:italic;font-size:0.65rem;'
                                        f'padding-left:8px">&quot;{c_cname}&quot;</div>'
                                    )
                                # Enchantments
                                c_enchs = c.get('enchantments', [])
                                if c_enchs:
                                    eparts = [f"{e.get('name','')} {e.get('level',1)}" for e in c_enchs]
                                    rows_html += (
                                        f'<div style="color:#22d3ee;font-size:0.6rem;'
                                        f'padding-left:8px">&#x2728; {", ".join(eparts)}</div>'
                                    )
                                # Lore
                                c_lore = c.get('lore', [])
                                if c_lore:
                                    lore_str = "<br>".join(str(l) for l in c_lore)
                                    rows_html += (
                                        f'<div style="color:#9ca3af;font-style:italic;font-size:0.55rem;'
                                        f'padding-left:8px">{lore_str}</div>'
                                    )
                                rows_html += '</div>'
                            log_entry["shulker_html"] = (
                                f'{item} x{amt}{meta_html}'
                                f'<details style="margin-top:4px">'
                                f'<summary style="cursor:pointer;color:#a78bfa;font-size:0.7rem;'
                                f'font-family:monospace;list-style:none;user-select:none">'
                                f'&#x1F4E6; {len(sc)} items inside (click to expand)</summary>'
                                f'<div style="padding:6px 8px;margin-top:4px;'
                                f'background:rgba(139,92,246,0.08);border:1px solid rgba(139,92,246,0.2);'
                                f'border-radius:4px;max-height:200px;overflow-y:auto">'
                                f'{rows_html}</div></details>'
                            )
                        elif meta_html:
                            # Non-shulker item with metadata — show item + metadata
                            log_entry["shulker_html"] = f'{item} x{amt}{meta_html}'
                    except Exception:
                        pass
                
                # Fallback: parse old-format display text with [...] shulker list
                if not log_entry["shulker_html"] and action in ('Container Take', 'Container Add', 'Container Change'):
                    bracket_start = target_text.find(' [')
                    if bracket_start > 0 and target_text.endswith(']'):
                        item_part = target_text[:bracket_start]
                        items_text = target_text[bracket_start+2:-1]  # content inside [ ]
                        # Split by comma and build full list
                        items_list = [i.strip() for i in items_text.split(',') if i.strip()]
                        if items_list:
                            rows_html = ""
                            for item_str in items_list:
                                if item_str.startswith('+'):
                                    continue  # skip "+9 more"
                                cname = item_str.replace('minecraft:', '').strip()
                                rows_html += (
                                    f'<div style="padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.05);'
                                    f'color:var(--text-primary);font-size:0.75rem">{cname}</div>'
                                )
                            log_entry["shulker_html"] = (
                                f'{item_part}'
                                f'<details style="margin-top:4px">'
                                f'<summary style="cursor:pointer;color:#a78bfa;font-size:0.7rem;'
                                f'font-family:monospace;list-style:none;user-select:none">'
                                f'&#x1F4E6; Click to see contents</summary>'
                                f'<div style="padding:6px 8px;margin-top:4px;'
                                f'background:rgba(139,92,246,0.08);border:1px solid rgba(139,92,246,0.2);'
                                f'border-radius:4px;max-height:200px;overflow-y:auto">'
                                f'{rows_html}</div></details>'
                            )
                
                results.append(log_entry)
        
        return {"logs": results, "total": len(results), "total_count": total_count, "offset": offset, "limit": effective_limit}
    
    @app.get("/api/logs/{log_id}/blockdata")
    async def get_log_blockdata(log_id: int, _: bool = Depends(verify_secret)):
        """Return the complete structured payload for one interaction row."""
        if not os.path.exists(DB_FILE):
            raise HTTPException(status_code=404, detail="Database not found")
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            cur.execute(
                "SELECT id, name, action, x, y, z, type, world, time, blockdata "
                "FROM interactions WHERE id = ?",
                (log_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Log entry not found")
            payload = _decode_json(row[9])
            snapshot_id = _payload_snapshot_id(payload)
            if not snapshot_id:
                snapshot_id = _nearest_snapshot_id(
                    db, row[7], row[3], row[4], row[5], row[8]
                )
            snapshot_record = _load_snapshot_record(db, snapshot_id) if snapshot_id else None

        inline_snapshot = payload.get("block_snapshot")
        provider_data = payload.get("blockdata_api")
        if not isinstance(inline_snapshot, dict) and isinstance(provider_data, dict):
            candidate = provider_data.get("snapshot")
            inline_snapshot = candidate if isinstance(candidate, dict) else None

        response = {
            "log": {
                "id": row[0], "player": row[1], "action": row[2],
                "x": row[3], "y": row[4], "z": row[5], "target": row[6],
                "dimension": row[7], "time": row[8],
            },
            "blockdata": payload,
            "resolved_snapshot_id": snapshot_id,
        }
        if snapshot_record:
            response.update(snapshot_record)
        elif isinstance(inline_snapshot, dict):
            response["snapshot"] = inline_snapshot
            response["raw_snbt"] = (inline_snapshot.get("block_entity") or {}).get("raw_snbt", "")
        return response

    @app.get("/api/container-snapshots")
    async def get_container_snapshots(
        hours: float = Query(24, description="Hours to look back"),
        player: Optional[str] = Query(None),
        reason: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        _: bool = Depends(verify_secret),
    ):
        """List canonical container snapshots without returning their large NBT blobs."""
        if not os.path.exists(DB_FILE):
            return {"snapshots": [], "total_count": 0}
        threshold = (now_est() - timedelta(hours=hours)).isoformat()
        clauses = ["captured_at >= ?"]
        params = [threshold]
        if player:
            clauses.append("player_name LIKE ?")
            params.append(f"%{player}%")
        if reason:
            clauses.append("reason LIKE ?")
            params.append(f"%{reason}%")
        where = "WHERE " + " AND ".join(clauses)
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            try:
                revision_expr = _revision_expression(db)
                cur.execute(f"SELECT COUNT(*) FROM container_snapshots {where}", params)
                total_count = cur.fetchone()[0]
                cur.execute(
                    f"""SELECT snapshot_id, player_name, reason, x, y, z, world,
                               block_type, {revision_expr}, captured_at, occupied_slots,
                               item_count, canonical_nbt
                        FROM container_snapshots {where}
                        ORDER BY captured_at DESC LIMIT ? OFFSET ?""",
                    [*params, limit, offset],
                )
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                return {"snapshots": [], "total_count": 0}
        return {
            "snapshots": [
                {
                    "snapshot_id": row[0], "player": row[1], "reason": row[2],
                    "x": row[3], "y": row[4], "z": row[5], "dimension": row[6],
                    "block_type": row[7], "revision": row[8], "captured_at": row[9],
                    "occupied_slots": row[10], "item_count": row[11],
                    "canonical_nbt": bool(row[12]),
                }
                for row in rows
            ],
            "total_count": total_count,
        }

    @app.get("/api/container-snapshots/{snapshot_id}")
    async def get_container_snapshot(snapshot_id: str, _: bool = Depends(verify_secret)):
        """Return a full canonical BlockData snapshot, including inventory and raw SNBT."""
        if not os.path.exists(DB_FILE):
            raise HTTPException(status_code=404, detail="Database not found")
        with sqlite3.connect(DB_FILE) as db:
            try:
                record = _load_snapshot_record(db, snapshot_id)
            except sqlite3.OperationalError:
                record = None
        if record is None:
            raise HTTPException(status_code=404, detail="Container snapshot not found")
        return record

    @app.get("/api/player-inventories")
    async def get_player_inventories(
        player: Optional[str] = Query(None, description="Player name filter"),
        online_only: bool = Query(False, description="Only currently online players"),
        limit: int = Query(100, ge=1, le=500),
        _: bool = Depends(verify_secret),
    ):
        """List the latest live BlockData inventory snapshot for each known player."""
        if not os.path.exists(DB_FILE):
            return {'players': [], 'total': 0}
        with sqlite3.connect(DB_FILE) as db:
            if not _table_exists(db, 'player_inventory_snapshots'):
                return {'players': [], 'total': 0}
            clauses = []
            params = []
            if player:
                clauses.append('player_name LIKE ?')
                params.append(f'%{player}%')
            if online_only:
                clauses.append('online = 1')
            where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
            total = db.execute(
                f'SELECT COUNT(*) FROM player_inventory_snapshots{where}', params
            ).fetchone()[0]
            rows = db.execute(
                f"""SELECT player_key,snapshot_id,player_name,xuid,captured_at,online,
                           COALESCE(revision_text, CAST(revision AS TEXT)),selected_hotbar_slot,
                           main_size,armor_size,offhand_size,ender_chest_size,
                           occupied_main,occupied_armor,occupied_offhand,occupied_ender_chest,
                           item_count,storage_item_count
                    FROM player_inventory_snapshots{where}
                    ORDER BY online DESC, player_name COLLATE NOCASE ASC LIMIT ?""",
                [*params, limit],
            ).fetchall()
        return {
            'total': total,
            'players': [
                {
                    'player_key': row[0], 'snapshot_id': row[1], 'player_name': row[2],
                    'xuid': row[3] or '', 'captured_at': row[4], 'online': bool(row[5]),
                    'revision': row[6], 'selected_hotbar_slot': row[7],
                    'main': {'capacity': row[8], 'occupied': row[12]},
                    'armor': {'capacity': row[9], 'occupied': row[13]},
                    'offhand': {'capacity': row[10], 'occupied': row[14]},
                    'ender_chest': {'capacity': row[11], 'occupied': row[15]},
                    'item_count': row[16], 'storage_item_count': row[17],
                }
                for row in rows
            ],
        }

    @app.get("/api/player-inventories/{player_key}")
    async def get_player_inventory(player_key: str, _: bool = Depends(verify_secret)):
        """Return exact main, armor, offhand, Ender Chest, and nested bundle NBT."""
        if not os.path.exists(DB_FILE):
            raise HTTPException(status_code=404, detail="Player inventory snapshot not found")
        with sqlite3.connect(DB_FILE) as db:
            record = _load_player_inventory_record(db, player_key)
        if record is None:
            raise HTTPException(status_code=404, detail="Player inventory snapshot not found")
        return record

    @app.get("/api/grief-reports")
    async def get_grief_reports(
        limit: int = Query(25, ge=1, le=200),
        offset: int = Query(0, ge=0),
        player: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        _: bool = Depends(verify_secret),
    ):
        if not os.path.exists(DB_FILE):
            return {'reports': [], 'total': 0}
        with sqlite3.connect(DB_FILE) as db:
            if not _table_exists(db, 'grief_reports'):
                return {'reports': [], 'total': 0}
            where = []
            params = []
            if player:
                where.append('(primary_player LIKE ? OR players_json LIKE ?)')
                params.extend([f'%{player}%', f'%{player}%'])
            if status:
                where.append('status=?')
                params.append(str(status))
            clause = (' WHERE ' + ' AND '.join(where)) if where else ''
            total = db.execute(
                f'SELECT COUNT(*) FROM grief_reports{clause}', params
            ).fetchone()[0]
            rows = db.execute(
                f"""SELECT report_id,rollback_id,created_at,completed_at,admin_name,status,
                           center_x,center_y,center_z,radius,hours,player_filter,
                           primary_player,event_count,affected_positions,blocks_broken,
                           blocks_placed,explosions,containers_looted,containers_broken,
                           items_reported,items_recovered,evidence_hash,players_json,worlds_json
                    FROM grief_reports{clause}
                    ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                [*params, int(limit), int(offset)],
            ).fetchall()
        reports = []
        for row in rows:
            try:
                players = json.loads(row[23])
            except Exception:
                players = []
            try:
                worlds = json.loads(row[24])
            except Exception:
                worlds = []
            reports.append({
                'report_id': row[0], 'rollback_id': row[1], 'created_at': row[2],
                'completed_at': row[3], 'admin': row[4], 'status': row[5],
                'center': {'x': row[6], 'y': row[7], 'z': row[8]},
                'radius': row[9], 'hours': row[10], 'player_filter': row[11],
                'primary_player': row[12], 'event_count': row[13],
                'affected_positions': row[14], 'blocks_broken': row[15],
                'blocks_placed': row[16], 'explosions': row[17],
                'containers_looted': row[18], 'containers_broken': row[19],
                'items_reported': row[20], 'items_recovered': row[21],
                'evidence_hash': row[22], 'players': players, 'worlds': worlds,
            })
        return {'reports': reports, 'total': total, 'offset': offset, 'limit': limit}

    @app.get("/api/grief-reports/{report_id}")
    async def get_grief_report(report_id: str, _: bool = Depends(verify_secret)):
        if not os.path.exists(DB_FILE):
            raise HTTPException(status_code=404, detail='Database not found')
        with sqlite3.connect(DB_FILE) as db:
            record = _load_grief_report_record(db, report_id)
            if record is None:
                raise HTTPException(status_code=404, detail='Grief report not found')
            if _table_exists(db, 'pending_confiscations'):
                recovery = db.execute(
                    """SELECT COALESCE(SUM(requested_amount),0),
                              COALESCE(SUM(removed_amount),0),
                              COALESCE(SUM(returned_amount),0),
                              SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END)
                       FROM pending_confiscations WHERE rollback_id=?""",
                    (record['rollback_id'],),
                ).fetchone()
                values = recovery or (0, 0, 0, 0)
                record['live_recovery'] = {
                    'requested': int(values[0] or 0),
                    'removed': int(values[1] or 0),
                    'returned': int(values[2] or 0),
                    'pending_rows': int(values[3] or 0),
                }
        return record

    @app.get("/reports/{report_id}", response_class=HTMLResponse)
    async def print_grief_report(report_id: str, _: bool = Depends(verify_secret)):
        if not os.path.exists(DB_FILE):
            raise HTTPException(status_code=404, detail='Database not found')
        with sqlite3.connect(DB_FILE) as db:
            record = _load_grief_report_record(db, report_id)
        if record is None:
            raise HTTPException(status_code=404, detail='Grief report not found')
        return HTMLResponse(_render_grief_report_page(record))

    @app.get("/api/debug")
    async def get_debug(_: bool = Depends(verify_secret)):
        """Debug endpoint — dump last 20 container events with all fields"""
        if not os.path.exists(DB_FILE):
            return {"events": [], "error": "DB not found"}
        
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            cur.execute("""
                SELECT name, action, x, y, z, type, world, time, blockdata 
                FROM interactions 
                WHERE action LIKE '%Container%'
                ORDER BY time DESC LIMIT 20
            """)
            events = []
            for row in cur.fetchall():
                events.append({
                    "player": row[0],
                    "action": row[1],
                    "x": row[2], "y": row[3], "z": row[4],
                    "target": row[5],
                    "world": row[6],
                    "time": row[7],
                    "blockdata": row[8] if len(row) > 8 else "N/A",
                    "blockdata_len": len(row[8]) if len(row) > 8 and row[8] else 0
                })
        return {"events": events, "count": len(events)}
    
    @app.get("/api/stats")
    async def get_stats(
        hours: float = Query(24, description="Hours to look back"),
        _: bool = Depends(verify_secret)
    ):
        """Get statistics for the dashboard"""
        if not os.path.exists(DB_FILE):
            return {
                "total_events": 0,
                "unique_players": 0,
                "actions": {},
                "top_players": []
            }
        
        time_threshold = now_est() - timedelta(hours=hours)
        
        with sqlite3.connect(DB_FILE) as db:
            cur = db.cursor()
            
            # Total events
            cur.execute("SELECT COUNT(*) FROM interactions WHERE time >= ?", (time_threshold.isoformat(),))
            total = cur.fetchone()[0]
            
            # Unique players
            cur.execute("SELECT COUNT(DISTINCT name) FROM interactions WHERE time >= ?", (time_threshold.isoformat(),))
            unique_players = cur.fetchone()[0]
            
            # Actions breakdown
            cur.execute("SELECT action, COUNT(*) FROM interactions WHERE time >= ? GROUP BY action", (time_threshold.isoformat(),))
            actions = dict(cur.fetchall())
            
            # Top players
            cur.execute("SELECT name, COUNT(*) as cnt FROM interactions WHERE time >= ? GROUP BY name ORDER BY cnt DESC LIMIT 10", (time_threshold.isoformat(),))
            top_players = [{"name": row[0], "count": row[1]} for row in cur.fetchall()]
        
        return {
            "total_events": total,
            "unique_players": unique_players,
            "actions": actions,
            "top_players": top_players
        }
    
    @app.get("/api/bans")
    async def get_bans(_: bool = Depends(verify_secret)):
        """Get banned players and devices"""
        banlist_file = os.path.join(DATA_DIR, "banlist.json")
        banidlist_file = os.path.join(DATA_DIR, "banidlist.json")
        
        players = {}
        devices = {}
        
        if os.path.exists(banlist_file):
            with open(banlist_file, 'r') as f:
                players = json.load(f)
        
        if os.path.exists(banidlist_file):
            with open(banidlist_file, 'r') as f:
                devices = json.load(f)
        
        return {
            "players": [{"name": k, **v} for k, v in players.items()],
            "devices": [{"id": k, **v} for k, v in devices.items()]
        }
    
    return app


def get_dashboard_html():
    """Generate the AntiGrief security-themed dashboard HTML"""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AntiGrief — Security Operations</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #060a10;
            --bg-secondary: #0b1120;
            --bg-card: rgba(11, 17, 32, 0.85);
            --bg-card-hover: rgba(16, 24, 44, 0.9);
            --border: rgba(0, 255, 170, 0.12);
            --border-active: rgba(0, 255, 170, 0.35);
            --text-primary: #e0e8f0;
            --text-secondary: #5a6a80;
            --text-muted: #3a4a5e;
            --accent-green: #00ffaa;
            --accent-cyan: #00d4ff;
            --accent-red: #ff3b5c;
            --accent-amber: #ffaa00;
            --accent-blue: #2563eb;
            --glow-green: rgba(0, 255, 170, 0.15);
            --glow-cyan: rgba(0, 212, 255, 0.12);
            --glow-red: rgba(255, 59, 92, 0.2);
            --font-mono: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
            --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: var(--font-sans);
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
        }

        /* Grid + scan-line background */
        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background:
                repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,255,170,0.015) 2px, rgba(0,255,170,0.015) 4px),
                radial-gradient(ellipse 70% 50% at 10% 20%, rgba(0,255,170,0.06), transparent),
                radial-gradient(ellipse 60% 40% at 90% 70%, rgba(0,212,255,0.05), transparent);
            pointer-events: none;
            z-index: -1;
        }

        /* Subtle animated scan-line */
        body::after {
            content: '';
            position: fixed;
            inset: 0;
            background: linear-gradient(to bottom, transparent 50%, rgba(0,255,170,0.01) 50%);
            background-size: 100% 4px;
            pointer-events: none;
            z-index: 9999;
            opacity: 0.4;
        }

        .container {
            max-width: 1480px;
            margin: 0 auto;
            padding: 1.5rem;
        }

        /* ─── HEADER ─── */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            padding: 1rem 1.5rem;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            backdrop-filter: blur(16px);
            position: relative;
            overflow: hidden;
        }

        header::after {
            content: '';
            position: absolute;
            bottom: 0; left: 0; right: 0;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--accent-green), var(--accent-cyan), transparent);
            opacity: 0.5;
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-icon {
            width: 38px;
            height: 38px;
            border: 2px solid var(--accent-green);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.1rem;
            background: rgba(0, 255, 170, 0.06);
            box-shadow: 0 0 12px rgba(0,255,170,0.1);
            position: relative;
        }

        .logo h1 {
            font-family: var(--font-mono);
            font-size: 1.35rem;
            font-weight: 700;
            color: var(--accent-green);
            letter-spacing: 0.05em;
            text-shadow: 0 0 20px rgba(0,255,170,0.3);
        }

        .logo h1 span {
            color: var(--text-secondary);
            font-weight: 400;
            font-size: 0.7rem;
            margin-left: 0.5rem;
            letter-spacing: 0.15em;
            text-transform: uppercase;
            text-shadow: none;
        }

        .header-right {
            display: flex;
            gap: 1rem;
            align-items: center;
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-family: var(--font-mono);
            font-size: 0.75rem;
            color: var(--accent-green);
            padding: 0.375rem 0.75rem;
            border: 1px solid rgba(0,255,170,0.2);
            border-radius: 4px;
            background: rgba(0,255,170,0.05);
        }

        .status-dot {
            width: 6px;
            height: 6px;
            background: var(--accent-green);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--accent-green);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; box-shadow: 0 0 8px var(--accent-green); }
            50% { opacity: 0.5; box-shadow: 0 0 4px var(--accent-green); }
        }

        /* ─── BUTTONS ─── */
        .btn {
            padding: 0.5rem 1rem;
            border: 1px solid var(--border);
            border-radius: 4px;
            font-family: var(--font-mono);
            font-size: 0.8rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }

        .btn-primary {
            background: rgba(0, 255, 170, 0.1);
            color: var(--accent-green);
            border-color: rgba(0, 255, 170, 0.3);
        }

        .btn-primary:hover {
            background: rgba(0, 255, 170, 0.18);
            border-color: var(--accent-green);
            box-shadow: 0 0 16px rgba(0,255,170,0.15);
        }

        .btn-danger {
            background: rgba(255, 59, 92, 0.08);
            color: var(--accent-red);
            border-color: rgba(255, 59, 92, 0.2);
        }

        .btn-danger:hover {
            background: rgba(255, 59, 92, 0.15);
            border-color: var(--accent-red);
        }

        /* ─── STAT CARDS ─── */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 0.75rem;
            margin-bottom: 1.5rem;
        }

        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 1rem 1.25rem;
            backdrop-filter: blur(12px);
            position: relative;
            overflow: hidden;
            transition: border-color 0.2s;
        }

        .stat-card:hover {
            border-color: var(--border-active);
        }

        .stat-card::before {
            content: '';
            position: absolute;
            top: 0; left: 0;
            width: 3px;
            height: 100%;
        }

        .stat-card:nth-child(1)::before { background: var(--accent-green); }
        .stat-card:nth-child(2)::before { background: var(--accent-cyan); }
        .stat-card:nth-child(3)::before { background: var(--accent-red); }
        .stat-card:nth-child(4)::before { background: var(--accent-amber); }

        .stat-card h3 {
            font-family: var(--font-mono);
            font-size: 0.65rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--text-secondary);
            margin-bottom: 0.5rem;
        }

        .stat-card .value {
            font-family: var(--font-mono);
            font-size: 1.75rem;
            font-weight: 700;
            color: var(--text-primary);
        }

        .stat-card:nth-child(1) .value { color: var(--accent-green); text-shadow: 0 0 16px rgba(0,255,170,0.2); }
        .stat-card:nth-child(2) .value { color: var(--accent-cyan); text-shadow: 0 0 16px rgba(0,212,255,0.2); }
        .stat-card:nth-child(3) .value { color: var(--accent-red); text-shadow: 0 0 16px rgba(255,59,92,0.2); }
        .stat-card:nth-child(4) .value { color: var(--accent-amber); text-shadow: 0 0 16px rgba(255,170,0,0.2); }

        /* ─── FILTERS ─── */
        .filters {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 1rem 1.25rem;
            margin-bottom: 1rem;
            backdrop-filter: blur(12px);
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            align-items: flex-end;
        }

        .filter-group {
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }

        .filter-group label {
            font-family: var(--font-mono);
            font-size: 0.65rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.12em;
        }

        .filter-group input, .filter-group select {
            padding: 0.5rem 0.75rem;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 4px;
            color: var(--text-primary);
            font-family: var(--font-mono);
            font-size: 0.8rem;
            min-width: 130px;
            transition: border-color 0.15s;
        }

        .filter-group input:focus, .filter-group select:focus {
            outline: none;
            border-color: var(--accent-green);
            box-shadow: 0 0 0 2px var(--glow-green);
        }

        .filter-group select { cursor: pointer; }
        .filter-group select option { background: var(--bg-secondary); }

        /* ─── GRIEF REPORTS ─── */
        .reports-section { background: var(--bg-card); border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden; margin-bottom: 1rem; backdrop-filter: blur(12px); }
        .reports-table { overflow-x: auto; }
        .reports-table table { width: 100%; border-collapse: collapse; }
        .reports-table th, .reports-table td { padding: 0.7rem 0.85rem; text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 0.76rem; }
        .player-status { display: inline-flex; align-items: center; gap: 0.35rem; padding: 0.2rem 0.48rem;
            border-radius: 999px; font-size: 0.64rem; font-family: var(--font-mono); border: 1px solid var(--border); }
        .player-status.online { color: #86efac; border-color: rgba(34,197,94,0.35); background: rgba(34,197,94,0.08); }
        .player-status.offline { color: var(--text-muted); background: rgba(148,163,184,0.06); }
        .inventory-cell { font-family: var(--font-mono); font-size: 0.68rem; white-space: nowrap; }
        .storage-count { color: #c4b5fd; font-weight: 700; }
        .nested-storage { margin-top: 0.55rem; padding-left: 0.65rem; border-left: 2px solid rgba(139,92,246,0.35); }
        .nested-storage > summary { cursor: pointer; color: #c4b5fd; font-size: 0.68rem; font-weight: 700; }
        .nested-storage .item-card { margin-top: 0.45rem; background: rgba(139,92,246,0.035); }
        .nested-depth-limit { color: var(--text-muted); font-size: 0.65rem; padding: 0.4rem; }
        .reports-table th { color: var(--text-muted); font-family: var(--font-mono);
            text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.62rem; }
        .report-id { font-family: var(--font-mono); color: var(--accent-cyan); }
        .report-status { display: inline-block; padding: 0.18rem 0.42rem; border-radius: 999px;
            border: 1px solid rgba(0,255,170,0.2); color: var(--accent-green);
            font-family: var(--font-mono); font-size: 0.62rem; text-transform: uppercase; }
        .report-print { padding: 0.35rem 0.55rem; font-size: 0.65rem; }

        /* ─── LOG TABLE ─── */
        .logs-section {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 6px;
            overflow: hidden;
            backdrop-filter: blur(12px);
        }

        .logs-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem 1.25rem;
            border-bottom: 1px solid var(--border);
            background: rgba(0,0,0,0.2);
        }

        .logs-header h2 {
            font-family: var(--font-mono);
            font-size: 0.75rem;
            font-weight: 500;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }

        .logs-count {
            font-family: var(--font-mono);
            font-size: 0.7rem;
            color: var(--accent-green);
            padding: 0.2rem 0.6rem;
            border: 1px solid rgba(0,255,170,0.2);
            border-radius: 3px;
            background: rgba(0,255,170,0.05);
        }

        .logs-table table {
            width: 100%;
            border-collapse: collapse;
        }

        .logs-table th {
            padding: 0.75rem 1rem;
            text-align: left;
            font-family: var(--font-mono);
            font-size: 0.65rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
            background: rgba(0,0,0,0.15);
        }

        .logs-table td {
            padding: 0.625rem 1rem;
            border-bottom: 1px solid rgba(255,255,255,0.03);
            font-size: 0.825rem;
        }

        .logs-table tr { transition: background 0.1s; }
        .logs-table tr:hover { background: rgba(0, 255, 170, 0.03); }

        .action-tag {
            display: inline-block;
            padding: 0.2rem 0.5rem;
            border-radius: 3px;
            font-family: var(--font-mono);
            font-size: 0.7rem;
            font-weight: 500;
            letter-spacing: 0.03em;
        }

        .action-break { background: rgba(255, 59, 92, 0.12); color: var(--accent-red); border: 1px solid rgba(255,59,92,0.2); }
        .action-place { background: rgba(0, 255, 170, 0.08); color: var(--accent-green); border: 1px solid rgba(0,255,170,0.15); }
        .action-interact { background: rgba(0, 212, 255, 0.08); color: var(--accent-cyan); border: 1px solid rgba(0,212,255,0.15); }
        .action-attack { background: rgba(255, 170, 0, 0.1); color: var(--accent-amber); border: 1px solid rgba(255,170,0,0.2); }
        .action-explode { background: rgba(255, 59, 92, 0.15); color: #ff6b8a; border: 1px solid rgba(255,59,92,0.25); }
        .action-container { background: rgba(37, 99, 235, 0.1); color: #60a5fa; border: 1px solid rgba(37,99,235,0.2); }

        .container-detail {
            font-family: var(--font-mono);
            font-size: 0.7rem;
            color: var(--text-secondary);
            margin-top: 4px;
            padding: 3px 6px;
            background: rgba(37, 99, 235, 0.06);
            border-radius: 3px;
            border-left: 2px solid rgba(37,99,235,0.4);
        }

        details summary::-webkit-details-marker { display: none; }
        details summary::marker { display: none; content: ''; }
        details summary:hover { color: #c4b5fd !important; text-decoration: underline; }

        .coords {
            font-family: var(--font-mono);
            font-size: 0.75rem;
            color: var(--accent-cyan);
        }

        .timestamp {
            font-family: var(--font-mono);
            color: var(--text-muted);
            font-size: 0.75rem;
        }

        .player-name {
            font-weight: 600;
            color: var(--text-primary);
        }

        .target-type {
            font-family: var(--font-mono);
            font-size: 0.8rem;
            color: var(--text-secondary);
        }

        /* ─── LOGIN OVERLAY ─── */
        #login-overlay {
            position: fixed;
            inset: 0;
            background: rgba(6, 10, 16, 0.97);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }

        .login-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 2.5rem 2rem;
            width: 100%;
            max-width: 380px;
            backdrop-filter: blur(20px);
            text-align: center;
            position: relative;
        }

        .login-card::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 2px;
            background: linear-gradient(90deg, transparent 10%, var(--accent-green), var(--accent-cyan), transparent 90%);
            opacity: 0.6;
            border-radius: 8px 8px 0 0;
        }

        .login-shield {
            width: 56px;
            height: 56px;
            margin: 0 auto 1.25rem;
            border: 2px solid var(--accent-green);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            background: rgba(0, 255, 170, 0.05);
            box-shadow: 0 0 20px rgba(0,255,170,0.1);
        }

        .login-card h2 {
            font-family: var(--font-mono);
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--accent-green);
            margin-bottom: 0.35rem;
            letter-spacing: 0.05em;
        }

        .login-subtitle {
            font-family: var(--font-mono);
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.15em;
            margin-bottom: 1.75rem;
        }

        .login-card input {
            width: 100%;
            padding: 0.75rem 1rem;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 4px;
            color: var(--text-primary);
            font-family: var(--font-mono);
            font-size: 0.875rem;
            margin-bottom: 1rem;
            letter-spacing: 0.1em;
        }

        .login-card input:focus {
            outline: none;
            border-color: var(--accent-green);
            box-shadow: 0 0 0 2px var(--glow-green);
        }

        .login-card input::placeholder {
            color: var(--text-muted);
            letter-spacing: 0.05em;
        }

        .login-card .btn {
            width: 100%;
            padding: 0.75rem;
        }

        .error-message {
            color: var(--accent-red);
            font-family: var(--font-mono);
            font-size: 0.75rem;
            margin-top: 0.75rem;
        }

        .hidden { display: none !important; }

        /* ─── PAGINATION ─── */
        .pagination {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            padding: 1rem;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-top: none;
            border-radius: 0 0 8px 8px;
        }
        .pagination button {
            background: rgba(0,255,170,0.08);
            color: var(--accent-green);
            border: 1px solid var(--border);
            padding: 0.4rem 0.9rem;
            border-radius: 4px;
            font-family: var(--font-mono);
            font-size: 0.75rem;
            cursor: pointer;
            transition: all 0.15s;
        }
        .pagination button:hover:not(:disabled) {
            background: rgba(0,255,170,0.18);
            border-color: var(--border-active);
        }
        .pagination button:disabled {
            opacity: 0.3;
            cursor: not-allowed;
        }
        .pagination .page-info {
            font-family: var(--font-mono);
            font-size: 0.75rem;
            color: var(--text-secondary);
            padding: 0 0.5rem;
        }
        .pagination select {
            background: var(--bg-secondary);
            color: var(--text-primary);
            border: 1px solid var(--border);
            padding: 0.35rem 0.5rem;
            border-radius: 4px;
            font-family: var(--font-mono);
            font-size: 0.7rem;
        }


        .nbt-button {
            background: rgba(139,92,246,0.12);
            color: #c4b5fd;
            border: 1px solid rgba(139,92,246,0.35);
            border-radius: 4px;
            padding: 0.3rem 0.55rem;
            font-family: var(--font-mono);
            font-size: 0.65rem;
            cursor: pointer;
            white-space: nowrap;
        }
        .nbt-button:hover { background: rgba(139,92,246,0.24); }
        .nbt-button:disabled { opacity: 0.28; cursor: default; }
        .nbt-modal {
            position: fixed; inset: 0; z-index: 2000;
            background: rgba(0,0,0,0.78);
            display: flex; align-items: center; justify-content: center;
            padding: 1rem;
        }
        .nbt-modal.hidden { display: none; }
        .nbt-panel {
            width: min(1100px, 96vw); max-height: 92vh;
            background: var(--bg-primary); border: 1px solid rgba(139,92,246,0.45);
            border-radius: 10px; box-shadow: 0 24px 80px rgba(0,0,0,0.6);
            display: flex; flex-direction: column; overflow: hidden;
        }
        .nbt-panel-header {
            display: flex; align-items: center; justify-content: space-between;
            padding: 0.85rem 1rem; background: rgba(139,92,246,0.1);
            border-bottom: 1px solid var(--border);
        }
        .nbt-panel-header h3 { margin: 0; font-family: var(--font-mono); color: #c4b5fd; }
        .nbt-close {
            border: 0; background: transparent; color: var(--text-secondary);
            font-size: 1.4rem; cursor: pointer;
        }
        .nbt-tabs { display: flex; gap: 0.4rem; padding: 0.65rem 1rem; border-bottom: 1px solid var(--border); }
        .nbt-tabs button {
            background: var(--bg-secondary); color: var(--text-secondary);
            border: 1px solid var(--border); border-radius: 4px; padding: 0.35rem 0.65rem;
            font-family: var(--font-mono); font-size: 0.68rem; cursor: pointer;
        }
        .nbt-tabs button.active { color: #c4b5fd; border-color: rgba(139,92,246,0.65); }
        .nbt-content { margin: 0; padding: 1rem; overflow: auto; word-break: break-word;
            font-family: var(--font-mono); font-size: 0.72rem; line-height: 1.5; color: #d1d5db; }
        .json-view { margin: 0; white-space: pre-wrap; word-break: break-word;
            font-family: var(--font-mono); font-size: 0.72rem; line-height: 1.5; color: #d1d5db; }
        .inventory-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 0.55rem; margin-bottom: 0.85rem; }
        .inventory-summary-card { background: rgba(139,92,246,0.07); border: 1px solid rgba(139,92,246,0.22);
            border-radius: 6px; padding: 0.65rem 0.75rem; }
        .inventory-summary-card .label { display: block; color: var(--text-muted); font-size: 0.58rem;
            letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 0.25rem; }
        .inventory-summary-card .value { color: var(--text-primary); font-size: 0.78rem; }
        .item-list { display: grid; gap: 0.65rem; }
        .item-card { background: rgba(255,255,255,0.025); border: 1px solid var(--border);
            border-radius: 7px; padding: 0.8rem; }
        .item-card-header { display: flex; align-items: flex-start; justify-content: space-between;
            gap: 0.8rem; margin-bottom: 0.5rem; }
        .item-name { color: #f5f3ff; font-size: 0.86rem; font-weight: 700; }
        .item-id { color: var(--text-muted); font-size: 0.65rem; margin-top: 0.15rem; }
        .slot-badge { color: #c4b5fd; border: 1px solid rgba(139,92,246,0.4);
            background: rgba(139,92,246,0.12); border-radius: 999px; padding: 0.2rem 0.48rem;
            font-size: 0.62rem; white-space: nowrap; }
        .item-meta { display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.45rem 0; }
        .item-chip { background: rgba(0,255,170,0.07); border: 1px solid rgba(0,255,170,0.2);
            border-radius: 4px; padding: 0.18rem 0.38rem; color: #a7f3d0; font-size: 0.62rem; }
        .item-section { margin-top: 0.55rem; }
        .item-section-title { color: var(--text-muted); font-size: 0.58rem; text-transform: uppercase;
            letter-spacing: 0.12em; margin-bottom: 0.25rem; }
        .item-lore { color: #d8b4fe; font-style: italic; white-space: pre-wrap; }
        .item-enchant { color: #a5b4fc; padding: 0.08rem 0; }
        .nested-item { color: var(--text-secondary); padding: 0.12rem 0 0.12rem 0.55rem;
            border-left: 2px solid rgba(139,92,246,0.28); }
        .raw-item-details { margin-top: 0.6rem; }
        .raw-item-details summary { cursor: pointer; color: var(--text-muted); font-size: 0.62rem; }
        .raw-item-details pre { margin: 0.45rem 0 0; padding: 0.55rem; border-radius: 5px;
            background: rgba(0,0,0,0.25); white-space: pre-wrap; overflow: auto; }
        .inventory-empty { padding: 2rem; text-align: center; color: var(--text-muted);
            border: 1px dashed var(--border); border-radius: 7px; }

        /* ─── RESPONSIVE ─── */
        @media (max-width: 768px) {
            .container { padding: 0.75rem; }
            header { flex-direction: column; gap: 0.75rem; }
            .header-right { width: 100%; justify-content: space-between; }
            .filters { flex-direction: column; }
            .filter-group { width: 100%; }
            .filter-group input, .filter-group select { width: 100%; }
            .stat-card .value { font-size: 1.4rem; }
        }
    </style>
</head>
<body>
    <div id="login-overlay">
        <div class="login-card">
            <div class="login-shield">🛡️</div>
            <h2>ANTIGRIEF</h2>
            <p class="login-subtitle">Security Operations Console</p>
            <input type="password" id="secret-input" placeholder="Enter access key..." onkeyup="if(event.key==='Enter') login()">
            <button class="btn btn-primary" onclick="login()">AUTHENTICATE</button>
            <p class="error-message hidden" id="login-error">⚠ Authentication failed — invalid key</p>
        </div>
    </div>

    <div class="container hidden" id="main-content">
        <header>
            <div class="logo">
                <div class="logo-icon">🛡️</div>
                <h1>ANTIGRIEF<span>Security Ops</span></h1>
            </div>
            <div class="header-right">
                <div class="status-badge">
                    <div class="status-dot"></div>
                    <span>MONITORING</span>
                </div>
                <button class="btn btn-primary" onclick="refreshData(true)">⟳ REFRESH</button>
            </div>
        </header>

        <div class="stats-grid" id="stats-grid">
            <div class="stat-card">
                <h3>Total Events</h3>
                <div class="value" id="stat-total">—</div>
            </div>
            <div class="stat-card">
                <h3>Active Players</h3>
                <div class="value" id="stat-players">—</div>
            </div>
            <div class="stat-card">
                <h3>Blocks Broken</h3>
                <div class="value" id="stat-breaks">—</div>
            </div>
            <div class="stat-card">
                <h3>Blocks Placed</h3>
                <div class="value" id="stat-places">—</div>
            </div>
        </div>

        <div class="filters">
            <div class="filter-group">
                <label>Time Range (hrs)</label>
                <input type="number" id="filter-hours" value="24" min="1" max="720">
            </div>
            <div class="filter-group">
                <label>Player Filter</label>
                <input type="text" id="filter-player" placeholder="gamertag...">
            </div>
            <div class="filter-group">
                <label>Event Type</label>
                <select id="filter-action">
                    <option value="">All Events</option>
                    <option value="Break">Break</option>
                    <option value="Place">Place</option>
                    <option value="Interact">Interact</option>
                    <option value="Attack">Attack</option>
                    <option value="Explode">Explode</option>
                    <option value="Container">Container</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Area (X Y Z Radius)</label>
                <input type="text" id="filter-coords" placeholder="0 64 0 50">
            </div>
            <div class="filter-group">
                <label>&nbsp;</label>
                <button class="btn btn-primary" onclick="refreshData(true)">APPLY</button>
            </div>
        </div>

        <div class="reports-section" id="player-inventory-section">
            <div class="logs-header">
                <h2>🎒 Player Inventories & Ender Chests</h2>
                <span class="logs-count" id="player-inventory-count">0 players</span>
            </div>
            <div class="reports-table">
                <table>
                    <thead><tr><th>Player</th><th>Status</th><th>Main + Hotbar</th><th>Armor</th><th>Offhand</th><th>Ender Chest</th><th>Bundles / Storage</th><th>Captured</th><th>Inspect</th></tr></thead>
                    <tbody id="player-inventory-body"><tr><td colspan="9" style="text-align:center;padding:1rem;color:var(--text-muted)">No player inventory snapshots loaded.</td></tr></tbody>
                </table>
            </div>
        </div>

        <div class="reports-section">
            <div class="logs-header">
                <h2>🧾 Grief Proof Reports</h2>
                <span class="logs-count" id="report-count">0 reports</span>
            </div>
            <div class="reports-table">
                <table>
                    <thead><tr><th>Report</th><th>Griefer</th><th>Admin</th><th>Area</th><th>Damage</th><th>Status</th><th>Created</th><th>Proof</th></tr></thead>
                    <tbody id="reports-body"><tr><td colspan="8" style="text-align:center;padding:1rem;color:var(--text-muted)">No reports loaded.</td></tr></tbody>
                </table>
            </div>
        </div>

        <div class="logs-section">
            <div class="logs-header">
                <h2>📋 Event Log</h2>
                <span class="logs-count" id="log-count">0 events</span>
            </div>
            <div class="logs-table">
                <table>
                    <thead>
                        <tr>
                            <th>Player</th>
                            <th>Event</th>
                            <th>Position</th>
                            <th>Target</th>
                            <th>Timestamp</th>
                            <th>NBT</th>
                        </tr>
                    </thead>
                    <tbody id="logs-body">
                        <tr><td colspan="6" style="text-align: center; padding: 2rem; color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem;">Awaiting authentication...</td></tr>
                    </tbody>
                </table>
            </div>
            <div class="pagination" id="pagination">
                <button onclick="goToPage(1)" id="btn-first">⟨⟨ First</button>
                <button onclick="goToPage(currentPage-1)" id="btn-prev">⟨ Prev</button>
                <span class="page-info" id="page-info">Page 1 of 1</span>
                <button onclick="goToPage(currentPage+1)" id="btn-next">Next ⟩</button>
                <button onclick="goToPage(totalPages)" id="btn-last">Last ⟩⟩</button>
                <select id="per-page" onchange="changePerPage()">
                    <option value="50">50/page</option>
                    <option value="100" selected>100/page</option>
                    <option value="200">200/page</option>
                    <option value="500">500/page</option>
                </select>
            </div>
        </div>
    </div>


    <div class="nbt-modal hidden" id="nbt-modal" onclick="if(event.target===this) closeNbt()">
        <div class="nbt-panel">
            <div class="nbt-panel-header">
                <h3 id="nbt-title">BlockData Snapshot</h3>
                <button class="nbt-close" onclick="closeNbt()">×</button>
            </div>
            <div class="nbt-tabs">
                <button id="tab-items" class="container-tab active" onclick="showNbtTab('items')">Item List</button>
                <button id="tab-nbt" class="container-tab" onclick="showNbtTab('nbt')">Canonical NBT</button>
                <button id="tab-snbt" class="container-tab" onclick="showNbtTab('snbt')">Raw SNBT</button>
                <button id="tab-main" class="player-tab hidden" onclick="showNbtTab('main')">Main + Hotbar</button>
                <button id="tab-armor" class="player-tab hidden" onclick="showNbtTab('armor')">Armor</button>
                <button id="tab-offhand" class="player-tab hidden" onclick="showNbtTab('offhand')">Offhand</button>
                <button id="tab-ender" class="player-tab hidden" onclick="showNbtTab('ender')">Ender Chest</button>
                <button id="tab-full" onclick="showNbtTab('full')">Full Snapshot</button>
            </div>
            <div class="nbt-content" id="nbt-content">Loading...</div>
        </div>
    </div>

    <script>
        let secretKey = '';
        let currentPage = 1;
        let totalPages = 1;
        let perPage = 100;
        let currentNbtPayload = null;
        let currentNbtMode = 'container';

        function login() {
            const input = document.getElementById('secret-input');
            secretKey = input.value;

            fetch(`/api/stats?hours=24&secret=${secretKey}`)
                .then(res => {
                    if (res.ok) {
                        document.getElementById('login-overlay').classList.add('hidden');
                        document.getElementById('main-content').classList.remove('hidden');
                        refreshData(true);
                    } else {
                        document.getElementById('login-error').classList.remove('hidden');
                    }
                })
                .catch(() => {
                    document.getElementById('login-error').classList.remove('hidden');
                });
        }

        async function refreshData(resetPage) {
            if (resetPage) currentPage = 1;
            const hours = document.getElementById('filter-hours').value || 24;
            const player = document.getElementById('filter-player').value;
            const action = document.getElementById('filter-action').value;
            const coords = document.getElementById('filter-coords').value.trim();
            const offset = (currentPage - 1) * perPage;

            let logsUrl = `/api/logs?hours=${hours}&limit=${perPage}&offset=${offset}&secret=${secretKey}`;
            if (player) logsUrl += `&player=${encodeURIComponent(player)}`;
            if (action) logsUrl += `&action=${encodeURIComponent(action)}`;
            if (coords) {
                const parts = coords.split(/\\s+/);
                if (parts.length >= 4) {
                    logsUrl += `&x=${parts[0]}&y=${parts[1]}&z=${parts[2]}&radius=${parts[3]}`;
                }
            }

            loadGriefReports();
            loadPlayerInventories();

            // Fetch stats
            try {
                const statsRes = await fetch(`/api/stats?hours=${hours}&secret=${secretKey}`);
                const stats = await statsRes.json();

                document.getElementById('stat-total').textContent = stats.total_events.toLocaleString();
                document.getElementById('stat-players').textContent = stats.unique_players.toLocaleString();
                document.getElementById('stat-breaks').textContent = (stats.actions['Break'] || 0).toLocaleString();
                document.getElementById('stat-places').textContent = (stats.actions['Place'] || 0).toLocaleString();
            } catch (e) {
                console.error('Stats fetch error:', e);
            }

            // Fetch logs
            try {
                const logsRes = await fetch(logsUrl);
                const data = await logsRes.json();

                // Update pagination state
                const tc = data.total_count || 0;
                totalPages = Math.max(1, Math.ceil(tc / perPage));
                if (currentPage > totalPages) currentPage = totalPages;
                const startIdx = (currentPage - 1) * perPage + 1;
                const endIdx = Math.min(currentPage * perPage, tc);
                document.getElementById('log-count').textContent = tc > 0 ? `Showing ${startIdx}-${endIdx} of ${tc.toLocaleString()} events` : '0 events';
                updatePagination();

                const tbody = document.getElementById('logs-body');
                if (data.logs.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 2rem; color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem;">No events match current filters</td></tr>';
                    return;
                }

                tbody.innerHTML = data.logs.map(log => {
                    let detailHtml = '';
                    let targetHtml = escapeHtml(log.target || '\u2014');
                    
                    // Use server-rendered container detail and shulker HTML
                    if (log.container_detail) {
                        detailHtml = '<div class="container-detail">' + escapeHtml(log.container_detail) + '</div>';
                    }
                    if (log.shulker_html) {
                        targetHtml = log.shulker_html;
                    }
                    const hasNbt = Boolean(log.has_nbt || log.snapshot_id || log.blockdata);
                    const nbtButton = hasNbt
                        ? '<button class="nbt-button" data-log-id="' + Number(log.id) + '" data-snapshot-id="' + encodeURIComponent(log.snapshot_id || '') + '">VIEW NBT</button>'
                        : '<button class="nbt-button" disabled>NONE</button>';
                    return '<tr>' +
                        '<td class="player-name">' + escapeHtml(log.player) + '</td>' +
                        '<td><span class="action-tag action-' + getActionClass(log.action) + '">' + escapeHtml(log.action) + '</span>' + detailHtml + '</td>' +
                        '<td class="coords">' + log.x + ', ' + log.y + ', ' + log.z + '</td>' +
                        '<td class="target-type">' + targetHtml + '</td>' +
                        '<td class="timestamp">' + formatTime(log.time) + '</td>' +
                        '<td>' + nbtButton + '</td>' +
                    '</tr>';
                }).join('');
            } catch (e) {
                console.error('Logs fetch error:', e);
            }
        }


        function setNbtMode(mode) {
            currentNbtMode = mode;
            document.querySelectorAll('.container-tab').forEach(button => {
                button.classList.toggle('hidden', mode !== 'container');
            });
            document.querySelectorAll('.player-tab').forEach(button => {
                button.classList.toggle('hidden', mode !== 'player');
            });
        }

        async function openNbt(logId, snapshotId) {
            const modal = document.getElementById('nbt-modal');
            const content = document.getElementById('nbt-content');
            modal.classList.remove('hidden');
            setNbtMode('container');
            content.textContent = 'Loading canonical BlockData payload...';
            try {
                // Resolve through the interaction first. That endpoint can use the
                // indexed snapshot, a nearby snapshot, or the inline destructive-event
                // backup when a snapshot-table row is unavailable.
                let response = await fetch(`/api/logs/${logId}/blockdata?secret=${encodeURIComponent(secretKey)}`);
                if (!response.ok && snapshotId) {
                    response = await fetch(`/api/container-snapshots/${encodeURIComponent(snapshotId)}?secret=${encodeURIComponent(secretKey)}`);
                }
                if (!response.ok) throw new Error(`BlockData request failed (${response.status})`);
                const payload = await response.json();
                currentNbtPayload = payload;
                const resolvedId = payload.resolved_snapshot_id || payload.metadata?.snapshot_id || snapshotId || '';
                document.getElementById('nbt-title').textContent = resolvedId
                    ? `Container Snapshot ${String(resolvedId).slice(0, 12)}`
                    : `Event #${logId} BlockData`;
                showNbtTab('items');
            } catch (error) {
                currentNbtPayload = { error: String(error) };
                content.innerHTML = '<div class="inventory-empty">' + escapeHtml(String(error)) + '</div>';
            }
        }

        function closeNbt() {
            document.getElementById('nbt-modal').classList.add('hidden');
            currentNbtPayload = null;
        }

        const ENCHANTMENT_NAMES = {
            0: 'Protection', 1: 'Fire Protection', 2: 'Feather Falling',
            3: 'Blast Protection', 4: 'Projectile Protection', 5: 'Thorns',
            6: 'Respiration', 7: 'Depth Strider', 8: 'Aqua Affinity',
            9: 'Sharpness', 10: 'Smite', 11: 'Bane of Arthropods',
            12: 'Knockback', 13: 'Fire Aspect', 14: 'Looting',
            15: 'Efficiency', 16: 'Silk Touch', 17: 'Unbreaking',
            18: 'Fortune', 19: 'Power', 20: 'Punch', 21: 'Flame',
            22: 'Infinity', 23: 'Luck of the Sea', 24: 'Lure',
            25: 'Frost Walker', 26: 'Mending', 27: 'Curse of Binding',
            28: 'Curse of Vanishing', 29: 'Impaling', 30: 'Riptide',
            31: 'Loyalty', 32: 'Channeling', 33: 'Multishot',
            34: 'Piercing', 35: 'Quick Charge', 36: 'Soul Speed',
            37: 'Swift Sneak', 38: 'Wind Burst'
        };

        function resolvedSnapshot(payload) {
            return payload?.snapshot
                || payload?.blockdata?.block_snapshot
                || payload?.blockdata?.blockdata_api?.snapshot
                || {};
        }

        function cleanMinecraftText(value) {
            if (value === null || value === undefined) return '';
            if (Array.isArray(value)) return value.map(cleanMinecraftText).filter(Boolean).join('');
            if (typeof value === 'object') {
                if (Array.isArray(value.rawtext)) return value.rawtext.map(cleanMinecraftText).join('');
                if (value.text !== undefined) return cleanMinecraftText(value.text);
                if (value.translate !== undefined) return cleanMinecraftText(value.translate);
                return JSON.stringify(value);
            }
            let text = String(value);
            const trimmed = text.trim();
            if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
                (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
                try { return cleanMinecraftText(JSON.parse(trimmed)); } catch (_) { /* plain text */ }
            }
            return text.replace(/§[0-9a-fk-or]/gi, '');
        }

        function romanNumeral(level) {
            const value = Number(level);
            const table = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];
            return Number.isInteger(value) && value > 0 && value < table.length ? table[value] : String(level ?? '?');
        }

        function itemTag(item) {
            return item?.tag || item?.Tag || {};
        }

        function containedItemPayload(value) {
            if (!value || typeof value !== 'object') return null;
            return value.item && typeof value.item === 'object' ? value.item : value;
        }

        function isEmptyItemNbt(value) {
            const item = containedItemPayload(value);
            if (!item || !Object.keys(item).length || item.empty === true) return true;
            const identifier = String(item.id || item.name || item.Name || '').toLowerCase();
            if (!identifier || identifier === 'air' || identifier === 'minecraft:air') return true;
            const count = Number(item.count ?? item.Count ?? 1);
            return Number.isFinite(count) && count <= 0;
        }

        function occupiedContainedItems(values) {
            if (!Array.isArray(values)) return [];
            return values
                .filter(value => value && typeof value === 'object' && !isEmptyItemNbt(value))
                .map(containedItemPayload)
                .filter(Boolean);
        }

        function itemIdentifier(item) {
            return String(item?.id || item?.name || item?.Name || 'minecraft:unknown');
        }

        function itemCount(item) {
            const count = Number(item?.count ?? item?.Count ?? 1);
            return Number.isFinite(count) ? count : 1;
        }

        function itemCustomName(item) {
            const tag = itemTag(item);
            const display = tag?.display || tag?.Display || {};
            return cleanMinecraftText(item?.CustomName ?? display?.Name ?? display?.name ?? tag?.CustomName ?? '');
        }

        function itemLore(item) {
            const tag = itemTag(item);
            const display = tag?.display || tag?.Display || {};
            const lore = display?.Lore || display?.lore || tag?.Lore || tag?.lore || [];
            const values = Array.isArray(lore) ? lore : [lore];
            return values.map(cleanMinecraftText).filter(Boolean);
        }

        function itemEnchantments(item) {
            const tag = itemTag(item);
            const source = tag?.ench || tag?.Enchantments || tag?.enchantments
                || item?.ench || item?.Enchantments || item?.enchantments || [];
            if (!Array.isArray(source)) return [];
            return source.map(enchantment => {
                const rawId = enchantment?.id ?? enchantment?.Id ?? enchantment?.name ?? 'unknown';
                const numericId = Number.parseInt(rawId, 10);
                const name = Number.isNaN(numericId)
                    ? cleanMinecraftText(String(rawId).replace('minecraft:', '').replaceAll('_', ' '))
                    : (ENCHANTMENT_NAMES[numericId] || `Enchantment ${numericId}`);
                const level = enchantment?.lvl ?? enchantment?.level ?? enchantment?.Level ?? '?';
                return `${name} ${romanNumeral(level)}`;
            });
        }

        function itemContainedGroups(item) {
            const tag = itemTag(item);
            const groups = [];
            const storage = tag?.storage_item_component_content;
            if (Array.isArray(storage)) {
                groups.push({
                    label: 'Bundle / Storage Contents',
                    kind: 'storage',
                    items: occupiedContainedItems(storage)
                });
            }
            const blockEntityTag = tag?.BlockEntityTag || tag?.block_entity_tag || item?.BlockEntityTag || {};
            const shulker = tag?.Items || tag?.items || blockEntityTag?.Items || blockEntityTag?.items || [];
            if (Array.isArray(shulker)) {
                groups.push({
                    label: 'Contained Block Inventory',
                    kind: 'container',
                    items: occupiedContainedItems(shulker)
                });
            }
            return groups;
        }

        function itemDamage(item) {
            const tag = itemTag(item);
            const value = item?.Damage ?? item?.damage ?? tag?.Damage ?? tag?.damage;
            return value === undefined || value === null ? null : value;
        }

        function countStorageItemsInItem(item, depth = 0) {
            if (!item || depth > 8) return 0;
            const id = itemIdentifier(item).replace('minecraft:', '');
            const groups = itemContainedGroups(item);
            const isStorage = id === 'bundle' || id.endsWith('_bundle')
                || groups.some(group => group.kind === 'storage');
            return (isStorage ? 1 : 0) + groups
                .filter(group => group.kind === 'storage')
                .flatMap(group => group.items)
                .reduce((total, child) => total + countStorageItemsInItem(child, depth + 1), 0);
        }

        function renderItemCard(entry, prefix, depth = 0) {
            const item = entry?.item || entry || {};
            const slot = entry?.slot ?? entry?.Slot ?? '?';
            const id = itemIdentifier(item);
            const customName = itemCustomName(item);
            const displayName = customName || id.replace('minecraft:', '').replaceAll('_', ' ');
            const lore = itemLore(item);
            const enchantments = itemEnchantments(item);
            const groups = itemContainedGroups(item);
            const damage = itemDamage(item);
            const count = itemCount(item);
            const aux = item?.aux ?? item?.Aux ?? item?.data ?? item?.Data;
            const containedCount = groups.reduce((total, group) => total + group.items.length, 0);

            let meta = `<span class="item-chip">Count: ${escapeHtml(String(count))}</span>`;
            if (damage !== null) meta += `<span class="item-chip">Damage: ${escapeHtml(String(damage))}</span>`;
            if (aux !== undefined && aux !== null) meta += `<span class="item-chip">Data: ${escapeHtml(String(aux))}</span>`;
            if (containedCount) meta += `<span class="item-chip">Contained stacks: ${containedCount}</span>`;
            if (groups.some(group => group.kind === 'storage')) {
                meta += '<span class="item-chip">Storage Item</span>';
            }

            let sections = '';
            if (enchantments.length) {
                sections += '<div class="item-section"><div class="item-section-title">Enchantments</div>'
                    + enchantments.map(value => `<div class="item-enchant">✦ ${escapeHtml(value)}</div>`).join('')
                    + '</div>';
            }
            if (lore.length) {
                sections += '<div class="item-section"><div class="item-section-title">Lore</div>'
                    + lore.map(value => `<div class="item-lore">${escapeHtml(value)}</div>`).join('')
                    + '</div>';
            }
            if (groups.length) {
                if (depth >= 8) {
                    sections += '<div class="nested-depth-limit">Nested storage exceeds the viewer depth limit.</div>';
                } else {
                    sections += groups.map(group => {
                        const children = group.items.length
                            ? group.items.map(child => renderItemCard(
                                { slot: child?.Slot ?? child?.slot ?? '?', item: child },
                                group.kind === 'storage' ? 'Bundle Slot' : 'Contained Slot',
                                depth + 1
                            )).join('')
                            : '<div class="inventory-empty">Empty</div>';
                        return `<details class="nested-storage" open><summary>${escapeHtml(group.label)} (${group.items.length} occupied)</summary>${children}</details>`;
                    }).join('');
                }
            }

            return '<div class="item-card">'
                + '<div class="item-card-header"><div>'
                + `<div class="item-name">${escapeHtml(displayName)}</div>`
                + `<div class="item-id">${escapeHtml(id)}</div></div>`
                + `<span class="slot-badge">${escapeHtml(prefix || 'Slot')} ${escapeHtml(String(slot))}</span></div>`
                + `<div class="item-meta">${meta}</div>`
                + sections
                + '<details class="raw-item-details"><summary>Show raw item NBT</summary>'
                + `<pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre></details></div>`;
        }

        async function loadPlayerInventories() {
            const body = document.getElementById('player-inventory-body');
            const playerFilter = document.getElementById('filter-player').value.trim();
            let url = `/api/player-inventories?limit=200&secret=${encodeURIComponent(secretKey)}`;
            if (playerFilter) url += `&player=${encodeURIComponent(playerFilter)}`;
            try {
                const response = await fetch(url);
                if (!response.ok) throw new Error('Player inventory request failed');
                const payload = await response.json();
                const players = Array.isArray(payload.players) ? payload.players : [];
                document.getElementById('player-inventory-count').textContent = `${payload.total || players.length} players`;
                if (!players.length) {
                    body.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:1rem;color:var(--text-muted)">No live player inventory snapshots are available yet.</td></tr>';
                    return;
                }
                const sectionCell = section => {
                    const value = section || {};
                    return `<span class="inventory-cell">${Number(value.occupied || 0)} / ${Number(value.capacity || 0)}</span>`;
                };
                body.innerHTML = players.map(player => {
                    const status = player.online ? 'ONLINE' : 'OFFLINE CACHE';
                    const statusClass = player.online ? 'online' : 'offline';
                    return '<tr>'
                        + `<td><span class="player-name">${escapeHtml(player.player_name || 'Unknown')}</span><div class="timestamp">${escapeHtml(player.xuid ? `XUID ${player.xuid}` : player.player_key)}</div></td>`
                        + `<td><span class="player-status ${statusClass}">${status}</span></td>`
                        + `<td>${sectionCell(player.main)}</td>`
                        + `<td>${sectionCell(player.armor)}</td>`
                        + `<td>${sectionCell(player.offhand)}</td>`
                        + `<td>${sectionCell(player.ender_chest)}</td>`
                        + `<td><span class="storage-count">${Number(player.storage_item_count || 0)}</span><div class="timestamp">${Number(player.item_count || 0)} total items</div></td>`
                        + `<td class="timestamp">${escapeHtml(formatTime(player.captured_at))}</td>`
                        + `<td><button class="btn btn-primary player-inventory-view" data-player-key="${encodeURIComponent(player.player_key)}">VIEW INVENTORY</button></td>`
                        + '</tr>';
                }).join('');
            } catch (error) {
                body.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:1rem;color:var(--accent-red)">Could not load player inventories.</td></tr>';
            }
        }

        async function openPlayerInventory(playerKey) {
            const modal = document.getElementById('nbt-modal');
            const content = document.getElementById('nbt-content');
            modal.classList.remove('hidden');
            setNbtMode('player');
            content.textContent = 'Loading live player inventory snapshot...';
            try {
                const response = await fetch(`/api/player-inventories/${encodeURIComponent(playerKey)}?secret=${encodeURIComponent(secretKey)}`);
                if (!response.ok) throw new Error(`Player inventory request failed (${response.status})`);
                const payload = await response.json();
                currentNbtPayload = payload;
                const metadata = payload.metadata || {};
                document.getElementById('nbt-title').textContent = `Player Inventory: ${metadata.player_name || playerKey}`;
                showNbtTab('main');
            } catch (error) {
                currentNbtPayload = { error: String(error) };
                content.innerHTML = '<div class="inventory-empty">' + escapeHtml(String(error)) + '</div>';
            }
        }

        document.getElementById('player-inventory-body').addEventListener('click', event => {
            const button = event.target.closest('button[data-player-key]');
            if (!button) return;
            openPlayerInventory(decodeURIComponent(button.dataset.playerKey || ''));
        });

        function renderPlayerInventorySection(payload, section) {
            const snapshot = payload?.snapshot || {};
            const metadata = payload?.metadata || {};
            const key = section === 'ender' ? 'ender_chest' : section;
            const labels = {
                main: 'Main Inventory + Hotbar', armor: 'Armor', offhand: 'Offhand',
                ender_chest: 'Ender Chest'
            };
            const entries = Array.isArray(snapshot?.[key]) ? [...snapshot[key]] : [];
            entries.sort((left, right) => Number(left?.slot ?? 0) - Number(right?.slot ?? 0));
            const capacity = Number(snapshot?.[`${key}_size`] ?? metadata?.[`${key}_size`] ?? 0);
            const totalItems = entries.reduce((total, entry) => total + itemCount(entry?.item || entry), 0);
            const storageItems = entries.reduce(
                (total, entry) => total + countStorageItemsInItem(entry?.item || entry), 0
            );
            let html = '<div class="inventory-summary">'
                + `<div class="inventory-summary-card"><span class="label">Player</span><span class="value">${escapeHtml(metadata.player_name || snapshot.player_name || 'Unknown')}</span></div>`
                + `<div class="inventory-summary-card"><span class="label">Section</span><span class="value">${escapeHtml(labels[key] || key)}</span></div>`
                + `<div class="inventory-summary-card"><span class="label">Occupied Slots</span><span class="value">${entries.length} / ${capacity}</span></div>`
                + `<div class="inventory-summary-card"><span class="label">Items / Storage</span><span class="value">${totalItems} / ${storageItems}</span></div>`
                + '</div>';
            if (snapshot.capture_mode === 'public_fallback') {
                html += '<div class="container-detail"><strong>Degraded readable capture:</strong> '
                    + escapeHtml(snapshot.capture_warning || 'One native NBT string could not be decoded. Readable item data was preserved, but exact raw NBT may be incomplete.')
                    + '</div>';
            }
            if (key === 'main') {
                html += `<div class="container-detail">Selected hotbar slot: ${escapeHtml(String(snapshot.selected_hotbar_slot ?? metadata.selected_hotbar_slot ?? 0))}</div>`;
            }
            if (!entries.length) {
                return html + `<div class="inventory-empty">No occupied slots in ${escapeHtml(labels[key] || key)}.</div>`;
            }
            const prefix = key === 'armor' ? 'Armor Slot' : key === 'offhand' ? 'Offhand Slot' : key === 'ender_chest' ? 'Ender Slot' : 'Slot';
            return html + '<div class="item-list">' + entries.map(entry => renderItemCard(entry, prefix)).join('') + '</div>';
        }

        async function loadGriefReports() {
            const body = document.getElementById('reports-body');
            try {
                const response = await fetch(`/api/grief-reports?limit=25&secret=${encodeURIComponent(secretKey)}`);
                if (!response.ok) throw new Error('Report request failed');
                const payload = await response.json();
                const reports = Array.isArray(payload.reports) ? payload.reports : [];
                document.getElementById('report-count').textContent = `${payload.total || reports.length} reports`;
                if (!reports.length) {
                    body.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:1rem;color:var(--text-muted)">No /agback grief reports have been generated yet.</td></tr>';
                    return;
                }
                body.innerHTML = reports.map(report => {
                    const center = report.center || {};
                    const damage = `${Number(report.blocks_broken || 0)} broken · ${Number(report.containers_looted || 0)} looted · ${Number(report.containers_broken || 0)} containers broken`;
                    return '<tr>'
                        + `<td><div class="report-id">${escapeHtml(report.report_id)}</div><div class="timestamp">${escapeHtml(String(report.event_count || 0))} evidence events</div></td>`
                        + `<td><span class="player-name">${escapeHtml(report.primary_player || 'Unknown')}</span></td>`
                        + `<td>${escapeHtml(report.admin || 'Console')}</td>`
                        + `<td><span class="coords">${escapeHtml(`${center.x}, ${center.y}, ${center.z}`)}</span><div class="timestamp">radius ${escapeHtml(String(report.radius))}</div></td>`
                        + `<td>${escapeHtml(damage)}<div class="timestamp">${Number(report.items_recovered || 0)} items recovered</div></td>`
                        + `<td><span class="report-status">${escapeHtml(String(report.status || 'processing').replaceAll('_', ' '))}</span></td>`
                        + `<td class="timestamp">${escapeHtml(formatTime(report.created_at))}</td>`
                        + `<td><button class="btn btn-primary report-print" data-report-id="${escapeHtml(report.report_id)}">VIEW / PRINT</button></td>`
                        + '</tr>';
                }).join('');
            } catch (error) {
                body.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:1rem;color:var(--accent-red)">Could not load grief reports.</td></tr>';
            }
        }

        function openGriefReport(reportId) {
            const url = `/reports/${encodeURIComponent(reportId)}?secret=${encodeURIComponent(secretKey)}`;
            window.open(url, '_blank', 'noopener');
        }

        document.getElementById('reports-body').addEventListener('click', event => {
            const button = event.target.closest('button[data-report-id]');
            if (!button) return;
            openGriefReport(button.dataset.reportId);
        });

        function renderInventory(payload) {
            const snapshot = resolvedSnapshot(payload);
            const entity = snapshot?.block_entity || {};
            const blockdata = payload?.blockdata || {};
            let entries = Array.isArray(entity?.inventory) ? [...entity.inventory] : [];
            let sourceNote = '';

            if (!entries.length) {
                if (blockdata?.after_item) {
                    entries.push({ slot: blockdata.slot ?? '?', item: blockdata.after_item });
                    sourceNote = 'Showing the item state recorded after this interaction.';
                } else if (blockdata?.before_item) {
                    entries.push({ slot: blockdata.slot ?? '?', item: blockdata.before_item });
                    sourceNote = 'Showing the item state recorded before this interaction.';
                }
            }
            entries.sort((left, right) => Number(left?.slot ?? left?.Slot ?? 0) - Number(right?.slot ?? right?.Slot ?? 0));

            const metadata = payload?.metadata || {};
            const capacity = entity?.container_size ?? metadata?.container_size ?? '?';
            const location = snapshot?.location || {};
            const coordinates = [metadata?.x ?? location?.x, metadata?.y ?? location?.y, metadata?.z ?? location?.z]
                .filter(value => value !== undefined && value !== null).join(', ');
            const totalItems = entries.reduce((total, entry) => total + itemCount(entry?.item || entry), 0);
            const blockType = snapshot?.type || metadata?.block_type || payload?.log?.target || 'unknown';

            let html = '<div class="inventory-summary">'
                + `<div class="inventory-summary-card"><span class="label">Container</span><span class="value">${escapeHtml(String(blockType))}</span></div>`
                + `<div class="inventory-summary-card"><span class="label">Occupied Slots</span><span class="value">${entries.length} / ${escapeHtml(String(capacity))}</span></div>`
                + `<div class="inventory-summary-card"><span class="label">Total Item Count</span><span class="value">${totalItems}</span></div>`
                + `<div class="inventory-summary-card"><span class="label">Location</span><span class="value">${escapeHtml(coordinates || 'Unknown')}</span></div>`
                + '</div>';
            if (sourceNote) html += `<div class="container-detail">${escapeHtml(sourceNote)}</div>`;
            if (!entries.length) {
                return html + '<div class="inventory-empty">No occupied slots were stored in this snapshot.</div>';
            }
            return html + '<div class="item-list">' + entries.map(entry => renderItemCard(entry, 'Slot')).join('') + '</div>';
        }

        function renderJson(value) {
            const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
            return `<pre class="json-view">${escapeHtml(text || '')}</pre>`;
        }

        function showNbtTab(tab) {
            ['full', 'nbt', 'items', 'snbt', 'main', 'armor', 'offhand', 'ender'].forEach(name => {
                const button = document.getElementById('tab-' + name);
                if (button) button.classList.toggle('active', name === tab);
            });
            const payload = currentNbtPayload || {};
            const snapshot = resolvedSnapshot(payload);
            const entity = snapshot?.block_entity || {};
            const content = document.getElementById('nbt-content');
            if (['main', 'armor', 'offhand', 'ender'].includes(tab)) {
                content.innerHTML = renderPlayerInventorySection(payload, tab);
                return;
            }
            if (tab === 'items') {
                content.innerHTML = renderInventory(payload);
                return;
            }
            if (tab === 'nbt') {
                content.innerHTML = renderJson(entity?.nbt || {});
                return;
            }
            if (tab === 'snbt') {
                const raw = payload?.raw_snbt || entity?.raw_snbt
                    || payload?.blockdata?.block_snapshot?.block_entity?.raw_snbt
                    || 'No raw SNBT stored for this record.';
                content.innerHTML = renderJson(raw);
                return;
            }
            content.innerHTML = renderJson(payload);
        }



        document.addEventListener('keydown', event => {
            if (event.key === 'Escape') closeNbt();
        });

        document.getElementById('logs-body').addEventListener('click', event => {
            const button = event.target.closest('button.nbt-button[data-log-id]');
            if (!button || button.disabled) return;
            const logId = Number(button.dataset.logId);
            const snapshotId = decodeURIComponent(button.dataset.snapshotId || '');
            openNbt(logId, snapshotId);
        });

        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        function toggleEl(btn) {
            var el = btn.nextElementSibling;
            if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
        }

        function formatTime(isoString) {
            try {
                const date = new Date(isoString);
                return date.toLocaleString('en-US', { timeZone: 'America/New_York', month: 'numeric', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true });
            } catch {
                return isoString;
            }
        }

        function getActionClass(action) {
            if (!action) return '';
            const lower = action.toLowerCase();
            if (lower.startsWith('container')) return 'container';
            return lower;
        }

        function updatePagination() {
            document.getElementById('page-info').textContent = `Page ${currentPage} of ${totalPages}`;
            document.getElementById('btn-first').disabled = (currentPage <= 1);
            document.getElementById('btn-prev').disabled = (currentPage <= 1);
            document.getElementById('btn-next').disabled = (currentPage >= totalPages);
            document.getElementById('btn-last').disabled = (currentPage >= totalPages);
        }

        function goToPage(page) {
            if (page < 1) page = 1;
            if (page > totalPages) page = totalPages;
            currentPage = page;
            refreshData(false);
        }

        function changePerPage() {
            perPage = parseInt(document.getElementById('per-page').value) || 100;
            currentPage = 1;
            refreshData(false);
        }
    </script>
</body>
</html>"""


def start_webui(logger, port=8098, secret=None):
    """Start the WebUI server in a background thread"""
    global _server_thread, _app
    
    if not WEBUI_AVAILABLE:
        logger.warning("FastAPI/uvicorn not installed. Run: pip install fastapi uvicorn")
        return False
    
    if _server_thread and _server_thread.is_alive():
        logger.info("WebUI already running")
        return True
    
    # Update config if secret provided
    if secret:
        config = get_web_config()
        config["secret"] = secret
        config["port"] = port
        with open(WEB_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    
    _app = create_app()
    
    def run_server():
        uvicorn.run(_app, host="0.0.0.0", port=port, log_level="warning")
    
    _server_thread = threading.Thread(target=run_server, daemon=True)
    _server_thread.start()
    
    return True

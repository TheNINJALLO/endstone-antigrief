"""
AntiGrief WebUI - Security Operations Dashboard
Self-contained FastAPI application for viewing behavior logs
"""

import os
import json
import sqlite3
import threading
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
DB_FILE = os.path.join(DATA_DIR, "tydata.db")
WEB_CONFIG_FILE = os.path.join(DATA_DIR, "web_config.json")

# Global state
_server_thread = None
_app = None


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
        version="1.3.0"
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
        
        query = f"SELECT name, action, x, y, z, type, world, time, blockdata FROM interactions {where_clause} ORDER BY time DESC LIMIT ? OFFSET ?"
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
                    "container_detail": "",
                    "shulker_html": ""
                }
                
                # Pre-render container detail and shulker HTML server-side
                action = row[1]
                target_text = row[5] or ''
                blockdata_str = row[8] if len(row) > 8 else None
                if blockdata_str and action in ('Container Take', 'Container Add'):
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
                if not log_entry["shulker_html"] and action in ('Container Take', 'Container Add'):
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
                        </tr>
                    </thead>
                    <tbody id="logs-body">
                        <tr><td colspan="5" style="text-align: center; padding: 2rem; color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem;">Awaiting authentication...</td></tr>
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

    <script>
        let secretKey = '';
        let currentPage = 1;
        let totalPages = 1;
        let perPage = 100;

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
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; padding: 2rem; color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem;">No events match current filters</td></tr>';
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
                    return '<tr>' +
                        '<td class="player-name">' + escapeHtml(log.player) + '</td>' +
                        '<td><span class="action-tag action-' + getActionClass(log.action) + '">' + escapeHtml(log.action) + '</span>' + detailHtml + '</td>' +
                        '<td class="coords">' + log.x + ', ' + log.y + ', ' + log.z + '</td>' +
                        '<td class="target-type">' + targetHtml + '</td>' +
                        '<td class="timestamp">' + formatTime(log.time) + '</td>' +
                    '</tr>';
                }).join('');
            } catch (e) {
                console.error('Logs fetch error:', e);
            }
        }

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

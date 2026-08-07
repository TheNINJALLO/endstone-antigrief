"""
AntiGrief Database Cleanup Utility
"""
import sqlite3
from datetime import datetime, timedelta, timezone

# Global state
is_running = False
msg1 = ""
msg2 = ""
vac_msg = ""

# Use same timezone as the main plugin (Eastern Time UTC-5)
try:
    from zoneinfo import ZoneInfo
    EASTERN_TZ = ZoneInfo("America/New_York")
except ImportError:
    EASTERN_TZ = timezone(timedelta(hours=-5))

def clean_old_interactions(db_path, hours_threshold):
    """
    Clean database records older than specified hours
    
    Args:
        db_path: Database file path
        hours_threshold: Delete records older than this many hours
    """
    global is_running, vac_msg, msg1, msg2
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    is_running = True

    def vacuum_db(db_path):
        global vac_msg
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("VACUUM")
            conn.commit()
            vac_msg = "Cleanup complete, database organized"

        except Exception as e:
            vac_msg = f"Error: {e}"

        finally:
            conn.close()
        return vac_msg

    try:
        # Use the same timezone as the main plugin to match stored timestamps
        cutoff_time = (datetime.now(EASTERN_TZ) - timedelta(hours=hours_threshold)).isoformat()

        cursor.execute("DELETE FROM interactions WHERE time < ?", (cutoff_time,))
        interaction_count = cursor.rowcount
        snapshot_count = 0
        try:
            cursor.execute(
                "DELETE FROM container_snapshots WHERE captured_at < ?",
                (cutoff_time,),
            )
            snapshot_count = cursor.rowcount
        except sqlite3.OperationalError:
            # Databases created before AntiGrief v1.5 do not have this table.
            pass
        conn.commit()

        msg1 = (
            f"Deleted {interaction_count} interaction records and {snapshot_count} "
            f"container snapshots older than {hours_threshold} hours"
        )
        vacuum_db(db_path)
        msg2 = "Database restructured to free space"

    except Exception as e:
        msg1 = f"Error: {e}"
        conn.rollback()

    finally:
        conn.close()
        is_running = False
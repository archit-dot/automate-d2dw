import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings
from .db import connect


def _json_ids(values):
    return json.dumps([int(v) for v in values])


def sources():
    with connect() as c:
        return c.execute("SELECT * FROM sources ORDER BY id").fetchall()


def source(source_id):
    with connect() as c:
        return c.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()


def source_by_chat_id(chat_id):
    with connect() as c:
        return c.execute("SELECT * FROM sources WHERE chat_id=?", (int(chat_id),)).fetchone()


def add_source(chat_id, title, username=None, peer_type=None, entity_id=None, access_hash=None):
    with connect() as c:
        c.execute(
            """INSERT INTO sources(chat_id,title,username,peer_type,entity_id,access_hash)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 title=excluded.title,
                 username=excluded.username,
                 peer_type=COALESCE(excluded.peer_type,sources.peer_type),
                 entity_id=COALESCE(excluded.entity_id,sources.entity_id),
                 access_hash=COALESCE(excluded.access_hash,sources.access_hash)""",
            (chat_id, title, username, peer_type, entity_id, access_hash),
        )
        return c.execute("SELECT * FROM sources WHERE chat_id=?", (int(chat_id),)).fetchone()


def set_source_enabled(source_id, enabled):
    with connect() as c:
        c.execute("UPDATE sources SET enabled=? WHERE id=?", (1 if enabled else 0, source_id))


def update_source_peer(source_id, resolved):
    with connect() as c:
        c.execute(
            """UPDATE sources
               SET chat_id=?,title=?,username=?,peer_type=?,entity_id=?,access_hash=?,enabled=1
               WHERE id=?""",
            (
                resolved.marked_id,
                resolved.title,
                resolved.username,
                resolved.peer_type,
                resolved.entity_id,
                resolved.access_hash,
                source_id,
            ),
        )
        return c.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()


def update_last_message(source_id, message_id):
    with connect() as c:
        c.execute(
            "UPDATE sources SET last_message_id=MAX(last_message_id,?) WHERE id=?",
            (int(message_id), source_id),
        )


def destinations():
    with connect() as c:
        return c.execute("SELECT * FROM destinations ORDER BY id").fetchall()


def destination(destination_id):
    with connect() as c:
        return c.execute("SELECT * FROM destinations WHERE id=?", (destination_id,)).fetchone()


def destination_by_chat_id(chat_id):
    with connect() as c:
        return c.execute("SELECT * FROM destinations WHERE chat_id=?", (int(chat_id),)).fetchone()


def add_destination(chat_id, title, username=None, peer_type=None, entity_id=None, access_hash=None):
    with connect() as c:
        c.execute(
            """INSERT INTO destinations(chat_id,title,username,peer_type,entity_id,access_hash)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 title=excluded.title,
                 username=excluded.username,
                 peer_type=COALESCE(excluded.peer_type,destinations.peer_type),
                 entity_id=COALESCE(excluded.entity_id,destinations.entity_id),
                 access_hash=COALESCE(excluded.access_hash,destinations.access_hash)""",
            (chat_id, title, username, peer_type, entity_id, access_hash),
        )
        row = c.execute("SELECT * FROM destinations WHERE chat_id=?", (int(chat_id),)).fetchone()
        c.execute("INSERT OR IGNORE INTO destination_settings(destination_id) VALUES(?)", (row["id"],))
        return row


def set_destination_enabled(destination_id, enabled):
    with connect() as c:
        c.execute("UPDATE destinations SET enabled=? WHERE id=?", (1 if enabled else 0, destination_id))


def update_destination_peer(destination_id, resolved):
    with connect() as c:
        c.execute(
            """UPDATE destinations
               SET chat_id=?,title=?,username=?,peer_type=?,entity_id=?,access_hash=?,enabled=1
               WHERE id=?""",
            (
                resolved.marked_id,
                resolved.title,
                resolved.username,
                resolved.peer_type,
                resolved.entity_id,
                resolved.access_hash,
                destination_id,
            ),
        )
        return c.execute("SELECT * FROM destinations WHERE id=?", (destination_id,)).fetchone()


def add_route(name, destination_id):
    with connect() as c:
        if not c.execute("SELECT 1 FROM destinations WHERE id=?", (destination_id,)).fetchone():
            raise ValueError("Destination not found.")
        cur = c.execute("INSERT INTO routes(name,destination_id) VALUES(?,?)", (name.strip(), destination_id))
        return cur.lastrowid


def route(route_id):
    with connect() as c:
        return c.execute(
            """SELECT r.*,d.title destination_title,d.chat_id destination_chat_id,
                      d.peer_type destination_peer_type,d.entity_id destination_entity_id,
                      d.access_hash destination_access_hash,d.enabled destination_enabled,
                      d.paused destination_paused
               FROM routes r JOIN destinations d ON d.id=r.destination_id
               WHERE r.id=?""",
            (route_id,),
        ).fetchone()


def routes():
    with connect() as c:
        return c.execute(
            """SELECT r.*,d.title destination_title,d.chat_id destination_chat_id,
                      d.peer_type destination_peer_type,d.entity_id destination_entity_id,
                      d.access_hash destination_access_hash,d.enabled destination_enabled,
                      d.paused destination_paused
               FROM routes r JOIN destinations d ON d.id=r.destination_id
               ORDER BY r.id"""
        ).fetchall()


def delete_route(route_id):
    with connect() as c:
        c.execute("DELETE FROM queues WHERE post_id IN (SELECT id FROM posts WHERE route_id=?)", (route_id,))
        c.execute("DELETE FROM posts WHERE route_id=?", (route_id,))
        c.execute("DELETE FROM route_sources WHERE route_id=?", (route_id,))
        c.execute("DELETE FROM schedules WHERE route_id=?", (route_id,))
        c.execute("DELETE FROM routes WHERE id=?", (route_id,))


def route_sources(route_id):
    with connect() as c:
        return c.execute(
            """SELECT s.*,rs.position,rs.enabled route_source_enabled,rs.last_message_id route_last_message_id,rs.pending_message_id route_pending_message_id
               FROM route_sources rs JOIN sources s ON s.id=rs.source_id
               WHERE rs.route_id=? AND rs.enabled=1 AND s.enabled=1
               ORDER BY rs.position,rs.source_id""",
            (route_id,),
        ).fetchall()


def add_route_source(route_id, source_id):
    if not route(route_id):
        raise ValueError("Route not found.")
    if not source(source_id):
        raise ValueError("Source not found.")
    with connect() as c:
        exists = c.execute(
            "SELECT 1 FROM route_sources WHERE route_id=? AND source_id=?",
            (route_id, source_id),
        ).fetchone()
        if exists:
            return
        pos = c.execute(
            "SELECT COALESCE(MAX(position),-1)+1 FROM route_sources WHERE route_id=?",
            (route_id,),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO route_sources(route_id,source_id,position,enabled,last_message_id) VALUES(?,?,?,1,0)",
            (route_id, source_id, pos),
        )


def remove_route_source(route_id, source_id):
    with connect() as c:
        c.execute("DELETE FROM route_sources WHERE route_id=? AND source_id=?", (route_id, source_id))
        rows = c.execute(
            "SELECT source_id FROM route_sources WHERE route_id=? ORDER BY position,source_id", (route_id,)
        ).fetchall()
        for position, row in enumerate(rows):
            c.execute(
                "UPDATE route_sources SET position=? WHERE route_id=? AND source_id=?",
                (position, route_id, row["source_id"]),
            )
        c.execute("UPDATE routes SET rotation_index=0 WHERE id=?", (route_id,))


def update_route_source_message(route_id, source_id, message_id):
    with connect() as c:
        c.execute(
            "UPDATE route_sources SET last_message_id=MAX(last_message_id,?), pending_message_id=0 WHERE route_id=? AND source_id=?",
            (int(message_id), route_id, source_id),
        )


def set_route_source_pending(route_id, source_id, message_id):
    with connect() as c:
        c.execute(
            "UPDATE route_sources SET pending_message_id=CASE WHEN pending_message_id=0 THEN ? ELSE MIN(pending_message_id,?) END WHERE route_id=? AND source_id=?",
            (int(message_id), int(message_id), route_id, source_id),
        )


def route_source_pending(route_id, source_id):
    with connect() as c:
        row = c.execute(
            "SELECT pending_message_id FROM route_sources WHERE route_id=? AND source_id=?",
            (route_id, source_id),
        ).fetchone()
        return int(row["pending_message_id"]) if row else 0


def routes_for_source(source_id):
    with connect() as c:
        return c.execute(
            """SELECT r.*,d.title destination_title,d.chat_id destination_chat_id,
                      d.peer_type destination_peer_type,d.entity_id destination_entity_id,d.access_hash destination_access_hash,
                      d.enabled destination_enabled,d.paused destination_paused
               FROM route_sources rs
               JOIN routes r ON r.id=rs.route_id
               JOIN destinations d ON d.id=r.destination_id
               WHERE rs.source_id=? AND rs.enabled=1 AND r.enabled=1 AND r.autopilot=1
                 AND d.enabled=1 AND d.paused=0
               ORDER BY r.id""",
            (source_id,),
        ).fetchall()


def route_source_position(route_id, source_id):
    with connect() as c:
        row = c.execute(
            "SELECT position FROM route_sources WHERE route_id=? AND source_id=? AND enabled=1",
            (route_id, source_id),
        ).fetchone()
        return None if row is None else int(row["position"])


def expected_source(route_id):
    with connect() as c:
        rows = c.execute(
            """SELECT rs.source_id,rs.position,rs.last_message_id AS route_last_message_id,rs.pending_message_id AS route_pending_message_id,s.*
               FROM route_sources rs JOIN sources s ON s.id=rs.source_id
               WHERE rs.route_id=? AND rs.enabled=1 AND s.enabled=1
               ORDER BY rs.position,rs.source_id""",
            (route_id,),
        ).fetchall()
        if not rows:
            return None
        r = c.execute("SELECT rotation_index FROM routes WHERE id=?", (route_id,)).fetchone()
        idx = (int(r["rotation_index"]) if r else 0) % len(rows)
        return rows[idx]


def advance_rotation(route_id):
    with connect() as c:
        rows = c.execute(
            "SELECT source_id FROM route_sources WHERE route_id=? AND enabled=1 ORDER BY position,source_id",
            (route_id,),
        ).fetchall()
        if not rows:
            c.execute("UPDATE routes SET rotation_index=0 WHERE id=?", (route_id,))
            return
        row = c.execute("SELECT rotation_index FROM routes WHERE id=?", (route_id,)).fetchone()
        current = int(row["rotation_index"]) if row else 0
        c.execute("UPDATE routes SET rotation_index=? WHERE id=?", ((current + 1) % len(rows), route_id))


def set_autopilot(route_id, on):
    with connect() as c:
        c.execute("UPDATE routes SET autopilot=? WHERE id=?", (1 if on else 0, route_id))


def set_route_max(route_id, value):
    with connect() as c:
        c.execute("UPDATE routes SET max_posts=? WHERE id=?", (max(1, min(40, int(value))), route_id))


def set_route_window(route_id, start, end):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end):
        raise ValueError("Invalid time. Use HH:MM HH:MM")
    with connect() as c:
        c.execute("UPDATE routes SET start_time=?,end_time=? WHERE id=?", (start, end, route_id))


def set_route_text(route_id, kind, text, enabled=True):
    if kind not in {"header", "footer"}:
        raise ValueError("Invalid text type.")
    col = "header_text" if kind == "header" else "footer_text"
    ecol = "header_enabled" if kind == "header" else "footer_enabled"
    with connect() as c:
        c.execute(f"UPDATE routes SET {col}=?,{ecol}=? WHERE id=?", (text, int(enabled), route_id))


def apply_header_footer(route_id, text):
    r = route(route_id)
    parts = []
    if r and r["header_enabled"] and r["header_text"]:
        parts.append(r["header_text"].strip())
    if text and text.strip():
        parts.append(text.strip())
    if r and r["footer_enabled"] and r["footer_text"]:
        parts.append(r["footer_text"].strip())
    return "\n\n".join(parts)


def clear_schedules(route_id):
    with connect() as c:
        c.execute("DELETE FROM schedules WHERE route_id=?", (route_id,))


def add_schedule(route_id, daily_time):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_time):
        return False
    r = route(route_id)
    if not r:
        return False
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO schedules(route_id,destination_id,daily_time,enabled) VALUES(?,?,?,1)",
            (route_id, r["destination_id"], daily_time),
        )
    return True


def schedules():
    with connect() as c:
        return c.execute(
            """SELECT s.*,r.name route_name FROM schedules s
               JOIN routes r ON r.id=s.route_id
               WHERE s.enabled=1 AND r.enabled=1
               ORDER BY s.daily_time,r.id"""
        ).fetchall()


def route_schedule(route_id):
    with connect() as c:
        return c.execute("SELECT * FROM schedules WHERE route_id=? AND enabled=1 ORDER BY daily_time", (route_id,)).fetchall()


def create_post(route_id, source_id, message_id, grouped_id=None, text="", source_message_ids=None, destination_id=None):
    with connect() as c:
        existing = c.execute(
            "SELECT * FROM posts WHERE route_id=? AND source_id=? AND source_message_id=?",
            (route_id, source_id, int(message_id)),
        ).fetchone()
        if existing:
            return existing
        r = c.execute("SELECT destination_id FROM routes WHERE id=?", (route_id,)).fetchone()
        if not r:
            raise ValueError("Route not found.")
        ids = source_message_ids or [message_id]
        c.execute(
            """INSERT INTO posts(route_id,source_id,destination_id,source_message_id,source_message_ids,grouped_id,original_text)
               VALUES(?,?,?,?,?,?,?)""",
            (route_id, source_id, destination_id or r["destination_id"], int(message_id), _json_ids(ids), grouped_id, text or ""),
        )
        return c.execute("SELECT * FROM posts WHERE id=last_insert_rowid()").fetchone()


def post_can_process(post_id):
    with connect() as c:
        row = c.execute("SELECT status FROM posts WHERE id=?", (post_id,)).fetchone()
        return bool(row and row["status"] in {"detected", "failed"})


def set_processed(post_id, converted_text, final_text, diskwala_chat_id, message_ids):
    with connect() as c:
        p = c.execute("SELECT route_id FROM posts WHERE id=?", (post_id,)).fetchone()
        if not p:
            return
        route_id = p["route_id"]
        ids = _json_ids(message_ids)
        c.execute(
            """UPDATE posts SET converted_text=?,final_text=?,diskwala_chat_id=?,diskwala_message_ids=?,
                      status='queued',diskwala_status='converted',processed_at=CURRENT_TIMESTAMP,error=NULL
               WHERE id=?""",
            (converted_text or "", final_text or "", int(diskwala_chat_id), ids, post_id),
        )
        pos = c.execute(
            """SELECT COALESCE(MAX(q.position),-1)+1
               FROM queues q JOIN posts p ON p.id=q.post_id
               WHERE q.status='queued' AND p.route_id=?""",
            (route_id,),
        ).fetchone()[0]
        c.execute(
            "INSERT OR REPLACE INTO queues(post_id,status,position,attempts,last_error) VALUES(?,'queued',?,0,NULL)",
            (post_id, pos),
        )


def pending_route(route_id):
    with connect() as c:
        return c.execute(
            """SELECT q.*,p.*,d.chat_id destination_chat_id,d.title destination_title,
                      d.peer_type destination_peer_type,d.entity_id destination_entity_id,d.access_hash destination_access_hash
               FROM queues q JOIN posts p ON p.id=q.post_id JOIN destinations d ON d.id=p.destination_id
               WHERE p.route_id=?
                 AND d.enabled=1 AND d.paused=0
                 AND (
                       (q.status='queued' AND p.status='queued')
                       OR (q.status='failed' AND p.status='failed' AND p.diskwala_status='converted' AND q.attempts < 3)
                 )
               ORDER BY CASE WHEN q.status='queued' THEN 0 ELSE 1 END, q.position, q.id LIMIT 1""",
            (route_id,),
        ).fetchone()


def route_queue_count(route_id):
    with connect() as c:
        return c.execute(
            """SELECT COUNT(*) FROM queues q JOIN posts p ON p.id=q.post_id
               WHERE q.status='queued' AND p.status='queued' AND p.route_id=?""",
            (route_id,),
        ).fetchone()[0]


def mark_publishing(post_id):
    with connect() as c:
        c.execute("UPDATE posts SET status='publishing',error=NULL WHERE id=?", (post_id,))
        c.execute("UPDATE queues SET status='publishing' WHERE post_id=?", (post_id,))


def mark_published(post_id):
    with connect() as c:
        c.execute("UPDATE posts SET status='published',published_at=CURRENT_TIMESTAMP,error=NULL WHERE id=?", (post_id,))
        c.execute("UPDATE queues SET status='published' WHERE post_id=?", (post_id,))


def mark_failed(post_id, error):
    msg = str(error)[:2000]
    with connect() as c:
        c.execute("UPDATE posts SET status='failed',error=? WHERE id=?", (msg, post_id))
        c.execute("UPDATE queues SET status='failed',last_error=?,attempts=attempts+1 WHERE post_id=?", (msg, post_id))


def count_queue():
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM queues WHERE status='queued'").fetchone()[0]


def count_processing():
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM posts WHERE status='publishing'").fetchone()[0]


def published_today():
    today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    with connect() as c:
        return c.execute(
            "SELECT COUNT(*) FROM posts WHERE status='published' AND date(published_at)=?",
            (today,),
        ).fetchone()[0]


def queue_posts(route_id, limit=20):
    with connect() as c:
        return c.execute(
            """SELECT q.*,p.source_message_id,p.final_text,p.status,s.title source_title
               FROM queues q JOIN posts p ON p.id=q.post_id JOIN sources s ON s.id=p.source_id
               WHERE q.status='queued' AND p.route_id=?
               ORDER BY q.position,q.id LIMIT ?""",
            (route_id, limit),
        ).fetchall()


def next_publish():
    now = datetime.now(ZoneInfo(settings.timezone))
    current = now.strftime("%H:%M")
    with connect() as c:
        rows = c.execute(
            """SELECT s.*,r.name route_name,r.start_time,r.end_time FROM schedules s JOIN routes r ON r.id=s.route_id
               WHERE s.enabled=1 AND r.enabled=1 AND r.autopilot=1
               ORDER BY s.daily_time,r.id"""
        ).fetchall()
    if not rows:
        return None
    eligible = [r for r in rows if r["start_time"] <= r["daily_time"] <= r["end_time"]] if all(r["start_time"] <= r["end_time"] for r in rows) else rows
    candidates = [r for r in eligible if r["daily_time"] >= current]
    if candidates:
        return candidates[0]
    return eligible[0] if eligible else rows[0]


def logs(limit=50):
    with connect() as c:
        return c.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

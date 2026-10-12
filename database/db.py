import sqlite3
from pathlib import Path
from config import settings

Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER UNIQUE NOT NULL,
    title TEXT NOT NULL,
    username TEXT,
    peer_type TEXT,
    entity_id INTEGER,
    access_hash INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS destinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER UNIQUE NOT NULL,
    title TEXT NOT NULL,
    username TEXT,
    peer_type TEXT,
    entity_id INTEGER,
    access_hash INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    paused INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS destination_settings (
    destination_id INTEGER PRIMARY KEY,
    header_text TEXT DEFAULT '',
    footer_text TEXT DEFAULT '',
    header_enabled INTEGER NOT NULL DEFAULT 1,
    footer_enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    destination_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    autopilot INTEGER NOT NULL DEFAULT 0,
    rotation_index INTEGER NOT NULL DEFAULT 0,
    max_posts INTEGER NOT NULL DEFAULT 40,
    start_time TEXT NOT NULL DEFAULT '08:00',
    end_time TEXT NOT NULL DEFAULT '23:00',
    header_text TEXT DEFAULT '',
    footer_text TEXT DEFAULT '',
    header_enabled INTEGER NOT NULL DEFAULT 1,
    footer_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS route_sources (
    route_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    pending_message_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(route_id, source_id)
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id INTEGER,
    source_id INTEGER NOT NULL,
    destination_id INTEGER,
    source_message_id INTEGER NOT NULL,
    source_message_ids TEXT DEFAULT '[]',
    grouped_id INTEGER,
    original_text TEXT DEFAULT '',
    converted_text TEXT DEFAULT '',
    final_text TEXT DEFAULT '',
    diskwala_chat_id INTEGER,
    diskwala_message_ids TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'detected',
    diskwala_status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT,
    published_at TEXT
);
CREATE TABLE IF NOT EXISTS queues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    position INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id INTEGER,
    destination_id INTEGER,
    daily_time TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect():
    con = sqlite3.connect(settings.db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def cols(con, table):
    if not table_exists(con, table):
        return set()
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def add_col(con, table, name, definition):
    if name not in cols(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db():
    with connect() as con:
        con.executescript(SCHEMA)

        # Existing databases from previous builds are upgraded in place.
        for name, definition in {
            "username": "TEXT",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "last_message_id": "INTEGER NOT NULL DEFAULT 0",
            "peer_type": "TEXT",
            "entity_id": "INTEGER",
            "access_hash": "INTEGER",
        }.items():
            add_col(con, "sources", name, definition)
        for name, definition in {
            "username": "TEXT",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "paused": "INTEGER NOT NULL DEFAULT 0",
            "peer_type": "TEXT",
            "entity_id": "INTEGER",
            "access_hash": "INTEGER",
        }.items():
            add_col(con, "destinations", name, definition)

        for name, definition in {
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "autopilot": "INTEGER NOT NULL DEFAULT 0",
            "rotation_index": "INTEGER NOT NULL DEFAULT 0",
            "max_posts": "INTEGER NOT NULL DEFAULT 40",
            "start_time": "TEXT NOT NULL DEFAULT '08:00'",
            "end_time": "TEXT NOT NULL DEFAULT '23:00'",
            "header_text": "TEXT DEFAULT ''",
            "footer_text": "TEXT DEFAULT ''",
            "header_enabled": "INTEGER NOT NULL DEFAULT 1",
            "footer_enabled": "INTEGER NOT NULL DEFAULT 1",
        }.items():
            add_col(con, "routes", name, definition)

        add_col(con, "route_sources", "enabled", "INTEGER NOT NULL DEFAULT 1")
        add_col(con, "route_sources", "position", "INTEGER NOT NULL DEFAULT 0")
        add_col(con, "route_sources", "last_message_id", "INTEGER NOT NULL DEFAULT 0")
        add_col(con, "route_sources", "pending_message_id", "INTEGER NOT NULL DEFAULT 0")
        for name, definition in {
            "route_id": "INTEGER",
            "destination_id": "INTEGER",
            "source_message_ids": "TEXT DEFAULT '[]'",
            "grouped_id": "INTEGER",
            "original_text": "TEXT DEFAULT ''",
            "converted_text": "TEXT DEFAULT ''",
            "final_text": "TEXT DEFAULT ''",
            "diskwala_chat_id": "INTEGER",
            "diskwala_message_ids": "TEXT DEFAULT '[]'",
            "status": "TEXT NOT NULL DEFAULT 'detected'",
            "diskwala_status": "TEXT NOT NULL DEFAULT 'pending'",
            "error": "TEXT",
            "created_at": "TEXT",
            "processed_at": "TEXT",
            "published_at": "TEXT",
        }.items():
            add_col(con, "posts", name, definition)
        add_col(con, "schedules", "route_id", "INTEGER")
        add_col(con, "schedules", "destination_id", "INTEGER")
        add_col(con, "schedules", "enabled", "INTEGER NOT NULL DEFAULT 1")

        try:
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_route_source_message ON posts(route_id,source_id,source_message_id)")
        except sqlite3.IntegrityError:
            # Legacy databases may contain duplicates. The application-level
            # lookup in repo.create_post still prevents new duplicates.
            pass

        # Old source->destination builds are migrated into one route per
        # destination. Existing route records are preserved.
        if table_exists(con, "source_destinations"):
            legacy_cols = cols(con, "source_destinations")
            if {"source_id", "destination_id"} <= legacy_cols:
                query = (
                    "SELECT source_id,destination_id FROM source_destinations WHERE enabled=1"
                    if "enabled" in legacy_cols
                    else "SELECT source_id,destination_id FROM source_destinations"
                )
                for pair in con.execute(query):
                    d = con.execute(
                        "SELECT id,title FROM destinations WHERE id=?", (pair["destination_id"],)
                    ).fetchone()
                    if not d:
                        continue
                    route = con.execute(
                        "SELECT id FROM routes WHERE destination_id=? ORDER BY id LIMIT 1",
                        (d["id"],),
                    ).fetchone()
                    if route:
                        route_id = route["id"]
                    else:
                        cur = con.execute(
                            "INSERT INTO routes(name,destination_id) VALUES(?,?)",
                            (d["title"], d["id"]),
                        )
                        route_id = cur.lastrowid
                    pos = con.execute(
                        "SELECT COALESCE(MAX(position),-1)+1 FROM route_sources WHERE route_id=?",
                        (route_id,),
                    ).fetchone()[0]
                    con.execute(
                        "INSERT OR IGNORE INTO route_sources(route_id,source_id,position,enabled,last_message_id) VALUES(?,?,?,1,0)",
                        (route_id, pair["source_id"], pos),
                    )

        # Convert legacy schedules to the route representation.
        if "destination_id" in cols(con, "schedules"):
            rows = con.execute(
                "SELECT * FROM schedules WHERE route_id IS NULL"
            ).fetchall()
            for row in rows:
                if row["destination_id"] is None:
                    continue
                route = con.execute(
                    "SELECT id FROM routes WHERE destination_id=? ORDER BY id LIMIT 1",
                    (row["destination_id"],),
                ).fetchone()
                if route:
                    exists = con.execute(
                        "SELECT 1 FROM schedules WHERE route_id=? AND daily_time=?",
                        (route["id"], row["daily_time"]),
                    ).fetchone()
                    if not exists:
                        con.execute(
                            "UPDATE schedules SET route_id=? WHERE id=?",
                            (route["id"], row["id"]),
                        )

        # Attach legacy posts to the best matching route.
        if "destination_id" in cols(con, "posts"):
            rows = con.execute(
                "SELECT id,destination_id,source_id,route_id FROM posts WHERE route_id IS NULL AND destination_id IS NOT NULL"
            ).fetchall()
            for row in rows:
                route = con.execute(
                    """SELECT r.id FROM routes r
                       JOIN route_sources rs ON rs.route_id=r.id
                       WHERE r.destination_id=? AND rs.source_id=?
                       ORDER BY r.id LIMIT 1""",
                    (row["destination_id"], row["source_id"]),
                ).fetchone()
                if route:
                    con.execute(
                        "UPDATE posts SET route_id=? WHERE id=?", (route["id"], row["id"])
                    )

        for d in con.execute("SELECT id FROM destinations"):
            con.execute(
                "INSERT OR IGNORE INTO destination_settings(destination_id) VALUES(?)",
                (d["id"],),
            )

        con.commit()


def log(action, status, message=""):
    with connect() as con:
        con.execute(
            "INSERT INTO logs(action,status,message) VALUES(?,?,?)",
            (action, status, message),
        )
        con.commit()

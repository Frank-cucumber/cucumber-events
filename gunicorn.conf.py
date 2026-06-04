import os
import sqlite3
from pathlib import Path

def post_fork(server, worker):
    """Called in each worker after forking — ensures DB tables exist."""
    root = Path(__file__).parent
    on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
    data_dir = Path("/tmp") if on_railway else root
    db_path = data_dir / "events.db"
    gfx_dir = data_dir / "graphics" / "web"
    photo_dir = data_dir / "photos" / "web"

    gfx_dir.mkdir(parents=True, exist_ok=True)
    photo_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                event_date  TEXT,
                source      TEXT    DEFAULT 'custom',
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS variants (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id     INTEGER REFERENCES events(id) ON DELETE CASCADE,
                variant_num  INTEGER,
                headline     TEXT    NOT NULL,
                subtext      TEXT    NOT NULL,
                image_path   TEXT,
                generated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS votes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                variant_id  INTEGER REFERENCES variants(id) ON DELETE CASCADE,
                voter_name  TEXT,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            );
        """)
    server.log.info(f"DB initialised at {db_path}")

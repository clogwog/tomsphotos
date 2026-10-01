"""Local record of what this app has uploaded to Google Photos.

The Photos Library API (post-April-2025) only exposes app-created content,
and only by filename inside our album. This SQLite log is the precise
source of truth for "did tomsphotos upload this exact file (path + mtime
+ size) already", and survives re-installs via filename fallback.
"""

import os
import sqlite3
import threading
import time

from config import UPLOAD_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    path TEXT,
    mtime REAL,
    file_size INTEGER,
    filename TEXT,
    media_item_id TEXT,
    uploaded_at REAL,
    PRIMARY KEY (path, mtime, file_size)
);
CREATE INDEX IF NOT EXISTS idx_uploads_filename ON uploads(filename);
"""


class UploadLog:
    def __init__(self, db_path=UPLOAD_DB_PATH):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            self._conn.close()

    def has_file(self, path, mtime, size):
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM uploads WHERE path=? AND mtime=? AND file_size=?",
                (path, mtime, size),
            ).fetchone()
        return row is not None

    def record(self, path, mtime, size, filename, media_item_id="", commit=True):
        with self._lock:
            self._conn.execute(
                """INSERT INTO uploads (path, mtime, file_size, filename, media_item_id, uploaded_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(path, mtime, file_size) DO UPDATE SET
                     filename=excluded.filename, media_item_id=excluded.media_item_id,
                     uploaded_at=excluded.uploaded_at""",
                (path, mtime, size, filename, media_item_id or "", time.time()),
            )
            if commit:
                self._conn.commit()

    def known_filenames(self):
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT filename FROM uploads").fetchall()
        return {row[0] for row in rows if row[0]}

    def count(self):
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM uploads").fetchone()
        return row[0]

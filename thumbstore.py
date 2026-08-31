"""Thumbnail cache in SQLite. Lives in app-support dir, never in the photo tree."""

import os
import sqlite3
import threading
import time

from config import THUMB_DB_PATH, THUMB_VERSION

_SCHEMA = """
CREATE TABLE IF NOT EXISTS thumbnails (
    path TEXT PRIMARY KEY,
    mtime REAL,
    file_size INTEGER,
    thumb_blob BLOB,
    thumb_width INTEGER,
    thumb_height INTEGER,
    created REAL,
    version INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS full_cache (
    path TEXT PRIMARY KEY,
    mtime REAL,
    file_size INTEGER,
    blob BLOB,
    width INTEGER,
    height INTEGER,
    created REAL,
    version INTEGER DEFAULT 0
);
"""


class ThumbStore:
    def __init__(self, db_path=THUMB_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-64000")  # 64MB page cache
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._lock = threading.Lock()
        self._pending = 0
        self._last_commit = time.time()

    def _migrate(self):
        """Add version column if missing; old rows become invalid."""
        for table in ("thumbnails", "full_cache"):
            cols = [r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")]
            if "version" not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN version INTEGER DEFAULT 0"
                )
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def invalidate(self, path):
        """Drop cached thumb + full-size rows for a path (file changed)."""
        with self._lock:
            self._conn.execute("DELETE FROM thumbnails WHERE path=?", (path,))
            self._conn.execute("DELETE FROM full_cache WHERE path=?", (path,))
            self._conn.commit()

    def get_thumbnail(self, path):
        """Return (blob, width, height) or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT thumb_blob, thumb_width, thumb_height FROM thumbnails WHERE path=?",
                (path,),
            ).fetchone()
        return row

    def get_thumbnail_valid(self, path, mtime, size):
        """Return (blob, width, height) if cached AND file unchanged AND current version, else None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT thumb_blob, thumb_width, thumb_height FROM thumbnails WHERE path=? AND mtime=? AND file_size=? AND version=?",
                (path, mtime, size, THUMB_VERSION),
            ).fetchone()
        return row

    def store_thumbnail(self, path, mtime, size, blob, width, height, commit=True):
        with self._lock:
            self._conn.execute(
                """INSERT INTO thumbnails (path, mtime, file_size, thumb_blob, thumb_width, thumb_height, created, version)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET
                     mtime=excluded.mtime, file_size=excluded.file_size,
                     thumb_blob=excluded.thumb_blob, thumb_width=excluded.thumb_width,
                     thumb_height=excluded.thumb_height, created=excluded.created,
                     version=excluded.version""",
                (path, mtime, size, blob, width, height, time.time(), THUMB_VERSION),
            )
            self._pending += 1
            if commit and (self._pending >= 50 or time.time() - self._last_commit > 2.0):
                self._conn.commit()
                self._pending = 0
                self._last_commit = time.time()

    def flush(self):
        with self._lock:
            if self._pending:
                self._conn.commit()
                self._pending = 0

    def get_full(self, path, mtime, size):
        with self._lock:
            row = self._conn.execute(
                "SELECT blob, width, height FROM full_cache WHERE path=? AND mtime=? AND file_size=? AND version=?",
                (path, mtime, size, THUMB_VERSION),
            ).fetchone()
        return row

    def store_full(self, path, mtime, size, blob, width, height):
        with self._lock:
            self._conn.execute(
                """INSERT INTO full_cache (path, mtime, file_size, blob, width, height, created, version)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET
                     mtime=excluded.mtime, file_size=excluded.file_size, blob=excluded.blob,
                     width=excluded.width, height=excluded.height, created=excluded.created,
                     version=excluded.version""",
                (path, mtime, size, blob, width, height, time.time(), THUMB_VERSION),
            )
            self._conn.commit()

    def stats(self):
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM thumbnails").fetchone()[0]
            f = self._conn.execute("SELECT COUNT(*) FROM full_cache").fetchone()[0]
            size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {"thumbs": n, "full": f, "db_bytes": size}
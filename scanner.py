"""Directory indexer. Scans the source tree, counts media files per directory.

Pure-python core (no Qt) so it is unit-testable; a QThread wrapper drives it
in the background from the GUI.
"""

import os
import sqlite3
import time
from collections import deque

from config import (
    IGNORE_DIRS,
    IGNORE_PATTERNS,
    INDEX_DB_PATH,
    MEDIA_EXTENSIONS,
    SOURCE_ROOT,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dirs (
    path TEXT PRIMARY KEY,
    parent_path TEXT,
    name TEXT,
    media_count INTEGER DEFAULT 0,
    dir_count INTEGER DEFAULT 0,
    last_scanned REAL DEFAULT 0,
    mtime REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dirs_parent ON dirs(parent_path);
"""


def is_media_file(name):
    """True if name is a media file we care about."""
    lower = name.lower()
    if lower in IGNORE_PATTERNS:
        return False
    return os.path.splitext(lower)[1] in MEDIA_EXTENSIONS


def is_ignored_dir(name):
    return name.lower() in IGNORE_DIRS


def iter_media_files(root):
    """Yield media file paths under root (recursive), skipping ignored dirs."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=True):
                            if not is_ignored_dir(entry.name):
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=True):
                            if is_media_file(entry.name):
                                yield entry.path
                    except OSError:
                        continue
        except OSError:
            continue


def scan_directory_tree(root, progress_cb=None, cancel_check=None):
    """Scan root recursively. Returns dict path -> (media_count, dir_count, mtime).

    progress_cb(dirs_done, dirs_total) called periodically.
    cancel_check() -> bool; if True, stop early (partial results returned).
    """
    result = {}
    children = {}  # path -> list of direct child dir paths
    dirs = []
    stack = [root]
    while stack:
        d = stack.pop()
        if cancel_check and cancel_check():
            break
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        subdirs = []
        media = 0
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=True):
                    if not is_ignored_dir(entry.name):
                        subdirs.append(entry.path)
                elif entry.is_file(follow_symlinks=True):
                    if is_media_file(entry.name):
                        media += 1
            except OSError:
                continue
        dirs.append(d)
        children[d] = subdirs
        result[d] = [media, len(subdirs), 0.0]
        stack.extend(subdirs)
        if progress_cb:
            progress_cb(len(dirs), -1)

    # Compute recursive counts bottom-up using only direct children
    for d in reversed(dirs):
        media, subdirs, _ = result[d]
        total_media = media
        total_dirs = subdirs
        try:
            mtime = os.stat(d).st_mtime
        except OSError:
            mtime = 0.0
        result[d][2] = mtime
        for child in children.get(d, []):
            if child in result:
                cm, cd, _ = result[child]
                total_media += cm
                total_dirs += cd
        result[d][0] = total_media
        result[d][1] = total_dirs

    if progress_cb:
        progress_cb(len(dirs), len(dirs))
    return result


class IndexDB:
    """SQLite index of directory counts. Thread-safe via a lock."""

    def __init__(self, db_path=INDEX_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._lock = __import__("threading").Lock()

    def close(self):
        with self._lock:
            self._conn.close()

    def get_dir(self, path):
        with self._lock:
            row = self._conn.execute(
                "SELECT path, parent_path, name, media_count, dir_count, last_scanned, mtime FROM dirs WHERE path=?",
                (path,),
            ).fetchone()
        return row

    def get_children(self, parent_path):
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, parent_path, name, media_count, dir_count, last_scanned, mtime FROM dirs WHERE parent_path=? ORDER BY name",
                (parent_path,),
            ).fetchall()
        return rows

    def upsert_dirs(self, rows):
        """rows: list of (path, parent_path, name, media_count, dir_count, last_scanned, mtime)."""
        with self._lock:
            self._conn.executemany(
                """INSERT INTO dirs (path, parent_path, name, media_count, dir_count, last_scanned, mtime)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET
                     parent_path=excluded.parent_path,
                     name=excluded.name,
                     media_count=excluded.media_count,
                     dir_count=excluded.dir_count,
                     last_scanned=excluded.last_scanned,
                     mtime=excluded.mtime""",
                rows,
            )
            self._conn.commit()

    def needs_rescan(self, path, mtime):
        """True if dir mtime changed since last scan (or never scanned)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT mtime, last_scanned FROM dirs WHERE path=?", (path,)
            ).fetchone()
        if row is None:
            return True
        db_mtime, last_scanned = row
        return abs(db_mtime - mtime) > 1e-3 or last_scanned == 0

    def is_scanned(self, path):
        with self._lock:
            row = self._conn.execute(
                "SELECT last_scanned FROM dirs WHERE path=?", (path,)
            ).fetchone()
        return row is not None and row[0] > 0

    def count_total_media(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(media_count),0) FROM dirs WHERE parent_path IS NULL"
            ).fetchone()
        return row[0] if row else 0


def build_rows_for_tree(result, root):
    """Convert scan result dict into (path, parent, name, count, dirs, scanned, mtime) rows."""
    now = time.time()
    rows = []
    rows.append(
        (root, None, os.path.basename(root) or root, result[root][0], result[root][1], now, result[root][2])
    )
    for path, (media, dirs, mtime) in result.items():
        if path == root:
            continue
        rows.append(
            (path, os.path.dirname(path), os.path.basename(path), media, dirs, now, mtime)
        )
    return rows


def sync_scan(root=SOURCE_ROOT, db=None, progress_cb=None, cancel_check=None):
    """One-shot incremental scan: rescan dirs whose mtime changed, update DB."""
    db = db or IndexDB()
    result = scan_directory_tree(root, progress_cb=progress_cb, cancel_check=cancel_check)
    rows = build_rows_for_tree(result, root)
    db.upsert_dirs(rows)
    return len(rows)


class ScannerThread:
    """Qt-free facade. Subclass and run scan() in a thread; callbacks fire
    from that thread. The GUI wraps this in a QThread."""

    def __init__(self, root=SOURCE_ROOT, db=None):
        self.root = root
        self.db = db or IndexDB()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def scan(self, progress_cb=None):
        def _cancel():
            return self.cancelled

        result = scan_directory_tree(self.root, progress_cb=progress_cb, cancel_check=_cancel)
        if self.cancelled:
            return None
        rows = build_rows_for_tree(result, self.root)
        self.db.upsert_dirs(rows)
        return len(rows)


def walk_media_files(root):
    """Yield (path, size, mtime) for every media file under root, sorted by path."""
    files = sorted(iter_media_files(root))
    out = []
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append((p, st.st_size, st.st_mtime))
    return out
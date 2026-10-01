import json
import os

APP_NAME = "tomsphotos"
DISPLAY_NAME = "tomPhoto"
MODE_THUMBNAIL = "thumbnail"
MODE_DETAIL = "detail"

DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/Library/Application Support")),
    APP_NAME,
)
os.makedirs(DATA_DIR, exist_ok=True)

SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

DEFAULT_SOURCE_ROOT = os.path.expanduser("~/Pictures")


def _load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


SETTINGS = _load_settings()


def _write_settings():
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(SETTINGS, f, indent=2)
    except OSError:
        pass


SOURCE_ROOT = os.path.expanduser(SETTINGS.get("source_root", DEFAULT_SOURCE_ROOT))


def set_source_root(path):
    """Persist the photo directory to the settings file."""
    global SOURCE_ROOT
    SOURCE_ROOT = os.path.expanduser(path)
    SETTINGS["source_root"] = SOURCE_ROOT
    _write_settings()


# Google Photos sync (Library API, app-created content only)
GP_ALBUM_TITLE = SETTINGS.get("gp_album_title", "tomsphotos")
GP_CLIENT_ID = SETTINGS.get("google_client_id", "")
GP_CLIENT_SECRET = SETTINGS.get("google_client_secret", "")
GP_TOKEN_PATH = os.path.join(DATA_DIR, "gphotos_token.json")
UPLOAD_DB_PATH = os.path.join(DATA_DIR, "uploads.db")


def set_google_credentials(client_id, client_secret):
    """Persist the user's OAuth Desktop client credentials."""
    global GP_CLIENT_ID, GP_CLIENT_SECRET
    GP_CLIENT_ID = client_id.strip()
    GP_CLIENT_SECRET = client_secret.strip()
    SETTINGS["google_client_id"] = GP_CLIENT_ID
    SETTINGS["google_client_secret"] = GP_CLIENT_SECRET
    _write_settings()

INDEX_DB_PATH = os.path.join(DATA_DIR, "index.db")
THUMB_DB_PATH = os.path.join(DATA_DIR, "thumbnails.db")

# Thumbnail sizes
THUMB_SIZE_GRID = 320
THUMB_SIZE_FULL = 1920
PLACEHOLDER_SIZE = 24
THUMB_VERSION = 2  # bump to invalidate all cached thumbnails

# Gallery layout
TARGET_ROW_HEIGHT = 200
MIN_ROW_HEIGHT = 100
MAX_ROW_HEIGHT = 300
ROW_HEIGHT_VARIANCE = 50
MAX_ROW_THUMBS = 100
GAP = 4
TREE_WIDTH = 280
PRELOAD_PAGES = 1
THREAD_POOL_SIZE = 4
QPIXMAP_CACHE_SIZE = 100 * 1024 * 1024  # 100 MB
BATCH_COMMIT = 50

# Scanning
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".mov", ".mp4", ".avi", ".m4v",
    ".webp", ".tiff", ".tif", ".bmp",
}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi", ".m4v", ".webm", ".mkv"}
IGNORE_PATTERNS = {".ds_store", ".tonfotos.ini", ".picasa.ini", ".ini", ".db"}
IGNORE_DIRS = {".dtrash", ".git", ".trash", ".trashes", "@eadir"}

QSS = """
  QMainWindow, QWidget { background: #000; color: #e0e0e0; font-family: 'SF Pro Text', -apple-system, 'Helvetica Neue'; font-size: 14px; }
  QTreeView { background: #0a0a0a; border: none; }
  QTreeView::item { padding: 4px 8px; }
  QTreeView::item:hover { background: #141414; }
  QTreeView::item:selected { background: #173a52; color: #a9dcff; border-left: 2px solid #62b8ff; }
  QTreeView::item:selected:active { background: #1d4965; color: #c4e8ff; border-left: 2px solid #76c8ff; }
  QHeaderView::section { background: #0a0a0a; color: #888; border: none; padding: 4px 8px; }
  QScrollBar:vertical { width: 8px; background: transparent; margin: 0; }
  QScrollBar::handle:vertical { background: #333; border-radius: 4px; min-height: 30px; }
  QScrollBar::handle:vertical:hover { background: #555; }
  QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
  QScrollBar:horizontal { height: 8px; background: transparent; margin: 0; }
  QScrollBar::handle:horizontal { background: #333; border-radius: 4px; min-width: 30px; }
  QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
  QProgressBar { background: #1a1a1a; border: none; height: 4px; text-align: center; max-height: 4px; }
  QProgressBar::chunk { background: #4a9eff; }
  QLabel { background: transparent; }
  QSlider::groove:horizontal { height: 3px; background: #444; border-radius: 1px; }
  QSlider::handle:horizontal { width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background: #fff; }
  QSlider::sub-page:horizontal { background: #4a9eff; }
  QToolButton { background: transparent; color: #fff; border: none; font-size: 16px; }
  QToolButton:hover { background: rgba(255,255,255,0.15); border-radius: 4px; }
  QMessageBox { background: #1c1c1e; }
  QMessageBox QLabel { background: transparent; color: #e0e0e0; font-size: 13px; }
  QMessageBox QPushButton { background: #2c2c2e; color: #e0e0e0; border: 1px solid #444; border-radius: 6px; padding: 6px 18px; font-size: 13px; }
  QMessageBox QPushButton:hover { background: #3a3a3c; }
  QMessageBox QPushButton:pressed { background: #48484a; }
"""
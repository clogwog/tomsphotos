"""Generate README screenshots headlessly using the bundled sample photos.

Usage:
    QT_QPA_PLATFORM=offscreen python scripts/make_screenshots.py

Writes PNGs to docs/screenshots/. Sample photos live in scripts/sample_photos/
and are copied into a temp library; they never touch the user's real library or
app support data.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

OUT_DIR = os.path.join(REPO, "docs", "screenshots")
SAMPLE_DIR = os.path.join(REPO, "scripts", "sample_photos")

# (year, month, day, photo count) — counts must sum to the number of bundled
# sample photos. The 2025-09-21 day is the one shown in gallery-grid.png.
DAYS = [
    ("2024", "07", "2024-07-14", 6),
    ("2025", "02", "2025-02-02", 6),
    ("2025", "09", "2025-09-21", 14),
    ("2026", "03", "2026-03-15", 6),
]


def build_sample_library(root):
    photos = sorted(f for f in os.listdir(SAMPLE_DIR) if f.lower().endswith(".jpg"))
    if not photos:
        raise SystemExit(f"no sample photos found in {SAMPLE_DIR}")
    expected = sum(count for *_, count in DAYS)
    if len(photos) < expected:
        raise SystemExit(f"need {expected} sample photos, found {len(photos)}")

    cursor = 0
    for year, month, day, count in DAYS:
        d = os.path.join(root, year, month, day)
        os.makedirs(d, exist_ok=True)
        for i in range(count):
            src = os.path.join(SAMPLE_DIR, photos[cursor % len(photos)])
            cursor += 1
            name = f"IMG_{year}{month}{day.replace('-', '')}_{i:04d}.jpg"
            shutil.copyfile(src, os.path.join(d, name))
    # a couple of short videos so the library looks real
    vid_dir = os.path.join(root, "2026", "03", "2026-03-15")
    for i in range(2):
        out = os.path.join(vid_dir, f"VID_20260315_{i:04d}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             f"testsrc=size={960 - i * 160}x540:rate=24:duration=2",
             "-pix_fmt", "yuv420p", out],
            check=False,
        )


def pump(app, until, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if until():
            return True
        time.sleep(0.02)
    return False


def _find_tree_index(model, text, parent=None):
    from PySide6.QtCore import QModelIndex

    parent = parent or QModelIndex()
    for row in range(model.rowCount(parent)):
        idx = model.index(row, 0, parent)
        if model.data(idx) == text:
            return idx
        found = _find_tree_index(model, text, idx)
        if found.isValid():
            return found
    return QModelIndex()


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tmp = tempfile.mkdtemp(prefix="tomsphotos-shots-")
    os.environ["XDG_DATA_HOME"] = tmp
    os.makedirs(OUT_DIR, exist_ok=True)

    samples = os.path.join(tmp, "Pictures")
    build_sample_library(samples)

    import config
    config.SOURCE_ROOT = samples

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    import main as app_main

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(config.DISPLAY_NAME)
    app.setApplicationDisplayName(config.DISPLAY_NAME)
    try:
        app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass

    win = app_main.MainWindow()
    win.resize(1440, 900)
    win.show()
    pump(app, lambda: False, timeout=0.3)
    pump(app, lambda: win._scan_worker is not None and not win._scan_worker.isRunning(), timeout=30)

    def grab(window, name):
        pix = window.grab()
        path = os.path.join(OUT_DIR, name)
        pix.save(path, "PNG")
        print("wrote", path, pix.width(), "x", pix.height())

    # 1) Gallery
    day_dir = os.path.join(samples, "2025", "09", "2025-09-21")
    win.load_directory(day_dir)
    mgr = win.thumb_manager
    pump(app, lambda: mgr.stats()["done"] > 0 and mgr.stats()["pending"] == 0, timeout=40)
    pump(app, lambda: False, timeout=0.8)
    win.tree.expandToDepth(2)
    idx = _find_tree_index(win.tree.model, "2025-09-21 (14)")
    if idx.isValid():
        win.tree.setCurrentIndex(idx)
        win.tree.scrollTo(idx)
    pump(app, lambda: False, timeout=0.8)
    grab(win, "gallery-grid-v2.png")

    # 1b) Two photos marked as deleted (red cross)
    photos = [p for p in win.gallery._items if p.lower().endswith(".jpg")]
    import gallerypanel
    gallerypanel.send2trash = lambda path: None  # simulate; don't touch the real Trash
    for path in (photos[1], photos[3]):
        win.gallery.delete_path(path, confirm=False)
    win.gallery.setFocus()
    pump(app, lambda: False, timeout=0.6)
    grab(win, "deleted-grid-v2.png")

    # 2) Detail view
    win._show_photo_detail(photos[2])
    pump(app, lambda: win.fullview._photo_pixmap is not None, timeout=20)
    win.fullview._title_label.setText("Pictures/" + os.path.relpath(photos[2], samples))
    win.fullview._date_label.setText(win.fullview._date_for(photos[2]))
    pump(app, lambda: False, timeout=0.8)
    grab(win, "photo-detail-v2.png")
    win.fullview.close_view()
    pump(app, lambda: False, timeout=0.3)

    # 3) Google Photos sync dialog (mid-upload, live thumbnail)
    from gpdialog import GoogleSyncDialog
    dlg = GoogleSyncDialog(win.thumb_store, parent=win)
    paths, meta = win.gallery._items, win.gallery._meta
    dlg.start(paths, meta)
    upload = photos[5]
    total = len(photos)
    size = meta.get(upload, (0, 0))[1] or 1
    dlg._on_queued(total, 3)
    dlg._on_file_started(upload, 4, total)
    dlg._on_file_progress(upload, int(size * 0.42), size)
    dlg.show()
    pump(app, lambda: dlg.thumb_label.pixmap() is not None and not dlg.thumb_label.pixmap().isNull(), timeout=20)
    pump(app, lambda: False, timeout=0.6)
    grab(dlg, "google-photos-sync-v2.png")
    dlg.close()

    win.close()
    print("done ->", OUT_DIR)


if __name__ == "__main__":
    main()

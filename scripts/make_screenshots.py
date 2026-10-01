"""Generate README screenshots headlessly with synthetic sample photos.

Usage:
    QT_QPA_PLATFORM=offscreen python scripts/make_screenshots.py

Writes PNGs to docs/screenshots/. Sample photos live in a temp dir and
never touch the user's real library or app support data.
"""

import math
import os
import random
import sys
import tempfile
import time

from PIL import Image, ImageDraw, ImageFilter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

OUT_DIR = os.path.join(REPO, "docs", "screenshots")


def _vgrad(size, stops):
    """Vertical gradient from a list of (pos, (r, g, b)) stops."""
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                f = (t - p0) / max(1e-6, p1 - p0)
                px[0, y] = tuple(int(c0[j] + (c1[j] - c0[j]) * f) for j in range(3))
                break
        else:
            px[0, y] = stops[-1][1]
    return img.resize((w, h), Image.BILINEAR)


def _blob(img, cx, cy, r, color, blur=18, alpha=255):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def photo_sunset(size):
    w, h = size
    img = _vgrad(size, [(0.0, (12, 24, 66)), (0.35, (206, 92, 66)), (0.62, (247, 176, 92)), (1.0, (26, 24, 48))])
    img = _blob(img, w * 0.5, h * 0.55, h * 0.12, (255, 236, 180), blur=30)
    d = ImageDraw.Draw(img)
    d.polygon([(0, h * 0.72), (w * 0.3, h * 0.5), (w * 0.62, h * 0.75), (w, h * 0.58), (w, h)], fill=(30, 26, 44))
    d.polygon([(0, h * 0.82), (w * 0.35, h * 0.68), (w * 0.7, h * 0.86), (w, h * 0.74), (w, h)], fill=(16, 14, 28))
    return img


def photo_ocean(size):
    w, h = size
    img = _vgrad(size, [(0.0, (36, 118, 176)), (0.42, (128, 196, 226)), (0.43, (22, 96, 150)), (0.75, (18, 74, 128)), (1.0, (254, 240, 214))])
    d = ImageDraw.Draw(img)
    d.ellipse([w * 0.66, h * 0.12, w * 0.78, h * 0.24], fill=(255, 250, 230))
    for i in range(30):
        y = h * (0.45 + i * 0.018)
        x = random.uniform(0, w)
        d.arc([x - 40, y - 10, x + 40, y + 10], 200, 340, fill=(226, 244, 252), width=2)
    return img


def photo_forest(size):
    w, h = size
    img = _vgrad(size, [(0.0, (198, 222, 188)), (0.5, (94, 146, 92)), (1.0, (26, 52, 34))])
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(70):
        x = random.uniform(0, w)
        base = random.uniform(h * 0.4, h)
        th = random.uniform(h * 0.25, h * 0.6)
        col = (int(30 + random.random() * 40), int(70 + random.random() * 60), int(40 + random.random() * 40), 220)
        d.polygon([(x, base - th), (x - th * 0.28, base), (x + th * 0.28, base)], fill=col)
    return img


def photo_city(size):
    w, h = size
    img = _vgrad(size, [(0.0, (10, 14, 40)), (0.6, (44, 34, 68)), (1.0, (120, 70, 80))])
    d = ImageDraw.Draw(img, "RGBA")
    x = 0
    random.seed(7)
    while x < w:
        bw = random.uniform(w * 0.05, w * 0.12)
        bh = random.uniform(h * 0.25, h * 0.72)
        top = h - bh
        d.rectangle([x, top, x + bw, h], fill=(12, 14, 26))
        for wy in range(int(top) + 8, int(h) - 6, 12):
            for wx in range(int(x) + 4, int(x + bw) - 4, 12):
                if random.random() < 0.5:
                    d.rectangle([wx, wy, wx + 5, wy + 7], fill=(255, 214, 130, 220))
        x += bw + 3
    return img


def photo_bloom(size):
    w, h = size
    img = Image.new("RGB", size, (26, 20, 30))
    cx, cy = w / 2, h / 2
    for i in range(14):
        ang = random.uniform(0, math.tau)
        r = random.uniform(h * 0.1, h * 0.42)
        col = random.choice([(246, 120, 168), (255, 186, 120), (196, 130, 240), (255, 230, 160)])
        img = _blob(img, cx + math.cos(ang) * r, cy + math.sin(ang) * r, random.uniform(h * 0.06, h * 0.16), col, blur=22, alpha=210)
    img = _blob(img, cx, cy, h * 0.09, (255, 250, 220), blur=16)
    return img


def photo_dunes(size):
    w, h = size
    img = _vgrad(size, [(0.0, (250, 214, 160)), (0.45, (232, 168, 104)), (1.0, (150, 92, 60))])
    d = ImageDraw.Draw(img)
    d.polygon([(0, h * 0.55), (w * 0.45, h * 0.42), (w, h * 0.6), (w, h), (0, h)], fill=(205, 138, 82))
    d.polygon([(0, h * 0.72), (w * 0.5, h * 0.6), (w, h * 0.78), (w, h), (0, h)], fill=(178, 112, 66))
    d.polygon([(0, h * 0.88), (w * 0.6, h * 0.78), (w, h * 0.9), (w, h), (0, h)], fill=(150, 92, 56))
    return img


GENERATORS = [photo_sunset, photo_ocean, photo_forest, photo_city, photo_bloom, photo_dunes]
RATIOS = [(3, 2), (4, 3), (16, 9), (2, 3), (3, 4), (1, 1)]


def build_sample_library(root):
    random.seed(11)
    days = [
        ("2023", "06", "2023-06-18", 9),
        ("2024", "04", "2024-04-07", 12),
        ("2024", "07", "2024-07-14", 15),
        ("2025", "02", "2025-02-02", 11),
        ("2025", "09", "2025-09-21", 14),
        ("2026", "01", "2026-01-01", 10),
        ("2026", "03", "2026-03-15", 13),
    ]
    for year, month, day, count in days:
        d = os.path.join(root, year, month, day)
        os.makedirs(d, exist_ok=True)
        for i in range(count):
            gen = GENERATORS[(i + count) % len(GENERATORS)]
            rw, rh = RATIOS[(i * 2 + 1) % len(RATIOS)]
            base = random.choice([900, 1100, 1300, 1500])
            size = (base, int(base * rh / rw))
            img = gen(size)
            img.save(os.path.join(d, f"IMG_{year}{month}{day.replace('-', '')}_{i:04d}.jpg"), "JPEG", quality=90)
    # a couple of short videos so the library looks real
    import subprocess
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
    day_dir = os.path.join(samples, "2024", "07", "2024-07-14")
    win.load_directory(day_dir)
    mgr = win.thumb_manager
    pump(app, lambda: mgr.stats()["done"] > 0 and mgr.stats()["pending"] == 0, timeout=40)
    pump(app, lambda: False, timeout=0.8)
    win.tree.expandToDepth(2)
    idx = _find_tree_index(win.tree.model, "2024-07-14 (15)")
    if idx.isValid():
        win.tree.setCurrentIndex(idx)
        win.tree.scrollTo(idx)
    pump(app, lambda: False, timeout=0.8)
    grab(win, "gallery.png")

    # 2) Detail view
    photos = [p for p in win.gallery._items if p.lower().endswith(".jpg")]
    win._show_photo_detail(photos[2])
    pump(app, lambda: win.fullview._photo_pixmap is not None, timeout=20)
    win.fullview._title_label.setText("Pictures/" + os.path.relpath(photos[2], samples))
    win.fullview._date_label.setText(win.fullview._date_for(photos[2]))
    pump(app, lambda: False, timeout=0.8)
    grab(win, "detail.png")
    win.fullview.close_view()
    pump(app, lambda: False, timeout=0.3)

    # 3) Google Photos sync dialog
    from gpdialog import GoogleSyncDialog
    dlg = GoogleSyncDialog(win.thumb_store, parent=win)
    dlg.start(win.gallery._items, win.gallery._meta)
    dlg.show()
    pump(app, lambda: False, timeout=1.2)
    grab(dlg, "google-photos.png")
    dlg.close()

    win.close()
    print("done ->", OUT_DIR)


if __name__ == "__main__":
    main()

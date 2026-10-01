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


def photo_aurora(size):
    w, h = size
    img = _vgrad(size, [(0.0, (4, 6, 24)), (0.6, (8, 14, 40)), (1.0, (10, 6, 26))])
    for band in [(60, 230, 140), (80, 170, 240), (150, 90, 230)]:
        y0 = random.uniform(h * 0.15, h * 0.45)
        d = ImageDraw.Draw(img)
        pts = [(x, y0 + math.sin(x / w * math.tau * 1.5) * h * 0.08) for x in range(0, w + 1, max(1, w // 40))]
        d.line(pts, fill=band, width=max(2, int(h * 0.05)))
    img = img.filter(ImageFilter.GaussianBlur(h * 0.012))
    d = ImageDraw.Draw(img)
    for _ in range(160):
        x, y = random.uniform(0, w), random.uniform(0, h * 0.8)
        d.point((x, y), fill=(255, 255, 255))
    d.polygon([(0, h * 0.82), (w * 0.4, h * 0.72), (w, h * 0.8), (w, h), (0, h)], fill=(6, 8, 18))
    return img


def photo_road(size):
    w, h = size
    img = _vgrad(size, [(0.0, (150, 196, 230)), (0.55, (238, 214, 176)), (1.0, (120, 130, 100))])
    d = ImageDraw.Draw(img)
    hz = h * 0.5
    d.polygon([(w * 0.42, hz), (w * 0.58, hz), (w * 0.95, h), (w * 0.05, h)], fill=(70, 68, 70))
    for i in range(7):
        f = i / 6
        y = hz + (h - hz) * f
        lw = 2 + 8 * f
        d.line([(w * 0.5, y), (w * 0.5, y + 6 + 12 * f)], fill=(240, 232, 190), width=int(lw))
    d.polygon([(0, h * 0.55), (w * 0.3, h * 0.5), (w * 0.32, h * 0.62), (0, h * 0.66)], fill=(90, 120, 70))
    d.polygon([(w, h * 0.55), (w * 0.7, h * 0.5), (w * 0.68, h * 0.62), (w, h * 0.66)], fill=(84, 112, 66))
    return img


def photo_autumn(size):
    w, h = size
    img = _vgrad(size, [(0.0, (92, 44, 24)), (0.5, (176, 84, 36)), (1.0, (60, 28, 16))])
    for _ in range(26):
        img = _blob(img, random.uniform(0, w), random.uniform(0, h), random.uniform(h * 0.04, h * 0.14),
                    random.choice([(255, 186, 60), (238, 120, 40), (210, 70, 30), (255, 220, 120)]),
                    blur=16, alpha=220)
    return img


def photo_snowpeaks(size):
    w, h = size
    img = _vgrad(size, [(0.0, (110, 150, 200)), (0.5, (196, 220, 240)), (1.0, (250, 252, 255))])
    d = ImageDraw.Draw(img)
    for cx, base, top, col in [(w * 0.3, h * 0.72, h * 0.28, (96, 118, 150)),
                               (w * 0.65, h * 0.78, h * 0.22, (120, 140, 170)),
                               (w * 0.85, h * 0.75, h * 0.34, (86, 108, 142))]:
        d.polygon([(cx, top), (cx - w * 0.18, base), (cx + w * 0.18, base)], fill=col)
        d.polygon([(cx, top), (cx - w * 0.06, top + h * 0.09), (cx + w * 0.06, top + h * 0.09)], fill=(245, 248, 252))
    d.rectangle([0, h * 0.78, w, h], fill=(238, 242, 246))
    return img


def photo_lavender(size):
    w, h = size
    img = _vgrad(size, [(0.0, (168, 196, 224)), (0.4, (214, 196, 224)), (1.0, (108, 78, 150))])
    d = ImageDraw.Draw(img)
    hz = h * 0.45
    for col, y in [((120, 80, 168), hz), ((138, 92, 186), h * 0.6), ((96, 62, 140), h * 0.75)]:
        d.rectangle([0, y, w, h], fill=col)
    for _ in range(120):
        x = random.uniform(0, w)
        y = random.uniform(hz, h)
        dell = (random.randint(120, 170), random.randint(80, 120), random.randint(180, 220))
        d.ellipse([x - 3, y - 6, x + 3, y + 6], fill=dell)
    return img


def photo_waves(size):
    w, h = size
    img = Image.new("RGB", size, (10, 12, 20))
    d = ImageDraw.Draw(img)
    for i in range(40):
        t = i / 40
        y = h * t
        col = (int(30 + 200 * (1 - t)), int(60 + 120 * math.sin(t * math.pi)), int(180 + 60 * t))
        d.line([(x, y + math.sin(x / w * math.tau * 3 + t * 6) * h * 0.05) for x in range(0, w + 1, 6)], fill=col, width=3)
    return img.filter(ImageFilter.GaussianBlur(1.2))


def photo_night(size):
    w, h = size
    img = _vgrad(size, [(0.0, (6, 10, 30)), (0.7, (16, 22, 54)), (1.0, (10, 12, 30))])
    d = ImageDraw.Draw(img)
    for _ in range(220):
        x, y = random.uniform(0, w), random.uniform(0, h * 0.7)
        b = random.randint(150, 255)
        d.point((x, y), fill=(b, b, 255))
    img = _blob(img, w * 0.78, h * 0.2, h * 0.06, (240, 240, 220), blur=10)
    d = ImageDraw.Draw(img)
    d.polygon([(0, h * 0.8), (w * 0.5, h * 0.62), (w, h * 0.82), (w, h), (0, h)], fill=(8, 10, 22))
    return img


def photo_canyon(size):
    w, h = size
    img = _vgrad(size, [(0.0, (240, 200, 150)), (1.0, (110, 60, 40))])
    d = ImageDraw.Draw(img)
    strata = [(0.3, (200, 120, 80)), (0.42, (170, 90, 60)), (0.54, (210, 140, 90)),
              (0.66, (150, 78, 52)), (0.78, (190, 110, 70)), (0.9, (120, 62, 44))]
    for y, col in strata:
        d.rectangle([0, h * y, w, h * (y + 0.12)], fill=col)
    for _ in range(40):
        x = random.uniform(0, w)
        d.ellipse([x, h * 0.2, x + 6, h * 0.24], fill=(255, 250, 240))
    return img


def photo_tropical(size):
    w, h = size
    img = _vgrad(size, [(0.0, (255, 200, 120)), (0.45, (250, 130, 90)), (0.7, (40, 60, 110)), (1.0, (12, 20, 40))])
    img = _blob(img, w * 0.5, h * 0.42, h * 0.1, (255, 236, 170), blur=26)
    d = ImageDraw.Draw(img)
    for _ in range(5):
        bx = random.uniform(0, w)
        d.line([(bx, h * 0.85), (bx + random.uniform(-w * 0.05, w * 0.05), h * 0.35)], fill=(10, 14, 20), width=6)
        for a in range(8):
            ang = math.pi + a / 7 * math.pi
            d.line([(bx, h * 0.4), (bx + math.cos(ang) * w * 0.1, h * 0.4 + math.sin(ang) * h * 0.16)],
                   fill=(10, 20, 16), width=5)
    d.rectangle([0, h * 0.82, w, h], fill=(10, 14, 20))
    return img


def photo_arches(size):
    w, h = size
    img = _vgrad(size, [(0.0, (246, 214, 196)), (1.0, (216, 176, 196))])
    d = ImageDraw.Draw(img)
    for i, col in enumerate([(238, 190, 172), (240, 160, 150), (216, 132, 140), (250, 224, 208)]):
        r = w * (0.2 + i * 0.14)
        d.pieslice([w * 0.5 - r, h * 0.55 - r, w * 0.5 + r, h * 0.55 + r], 180, 360, fill=col)
    d.rectangle([0, h * 0.78, w, h], fill=(96, 84, 96))
    return img


def photo_rain(size):
    w, h = size
    img = _vgrad(size, [(0.0, (18, 22, 40)), (1.0, (40, 30, 50))])
    for _ in range(18):
        img = _blob(img, random.uniform(0, w), random.uniform(0, h), random.uniform(h * 0.08, h * 0.2),
                    random.choice([(255, 200, 90), (120, 200, 255), (255, 120, 140)]), blur=26, alpha=170)
    img = img.filter(ImageFilter.GaussianBlur(6))
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = random.uniform(0, w), random.uniform(0, h)
        d.line([(x, y), (x - 4, y + 16)], fill=(220, 226, 240), width=1)
    return img


def photo_leaf(size):
    w, h = size
    img = _vgrad(size, [(0.0, (30, 70, 40)), (1.0, (18, 46, 30))])
    d = ImageDraw.Draw(img)
    d.line([(w * 0.5, h), (w * 0.5, h * 0.12)], fill=(180, 220, 150), width=6)
    for i in range(10):
        t = i / 10
        y = h * (0.16 + t * 0.7)
        ln = w * 0.32 * (1 - abs(t - 0.5) * 1.4)
        d.line([(w * 0.5, y), (w * 0.5 - ln, y - h * 0.08)], fill=(140, 200, 120), width=3)
        d.line([(w * 0.5, y), (w * 0.5 + ln, y - h * 0.08)], fill=(120, 186, 104), width=3)
    return img


SCENES = [
    photo_sunset, photo_ocean, photo_forest, photo_city, photo_bloom, photo_dunes,
    photo_aurora, photo_road, photo_autumn, photo_snowpeaks, photo_lavender,
    photo_waves, photo_night, photo_canyon, photo_tropical, photo_arches,
    photo_rain, photo_leaf,
]
RATIOS = [(3, 2), (4, 3), (16, 9), (2, 3), (3, 4), (1, 1), (5, 4), (21, 9), (4, 5), (7, 5)]


def _hue_shift(img, degrees):
    shift = int((degrees % 360) / 360 * 256)
    hsv = img.convert("HSV")
    h, s, v = hsv.split()
    h = h.point(lambda x: (x + shift) % 256)
    return Image.merge("HSV", (h, s, v)).convert("RGB")


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
    for day_index, (year, month, day, count) in enumerate(days):
        d = os.path.join(root, year, month, day)
        os.makedirs(d, exist_ok=True)
        for i in range(count):
            scene = SCENES[(day_index * 7 + i) % len(SCENES)]
            rw, rh = RATIOS[(day_index * 3 + i) % len(RATIOS)]
            base = random.choice([900, 1100, 1300, 1500])
            size = (base, int(base * rh / rw))
            img = scene(size)
            img = _hue_shift(img, random.uniform(-22, 22))
            name = f"IMG_{year}{month}{day.replace('-', '')}_{i:04d}.jpg"
            img.save(os.path.join(d, name), "JPEG", quality=90)
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
    grab(win, "gallery-grid.png")

    # 1b) Two photos marked as deleted (red cross)
    photos = [p for p in win.gallery._items if p.lower().endswith(".jpg")]
    import gallerypanel
    gallerypanel.send2trash = lambda path: None  # simulate; don't touch the real Trash
    for path in (photos[1], photos[3]):
        win.gallery.delete_path(path, confirm=False)
    win.gallery.setFocus()
    pump(app, lambda: False, timeout=0.6)
    grab(win, "deleted-grid.png")

    # 2) Detail view
    win._show_photo_detail(photos[2])
    pump(app, lambda: win.fullview._photo_pixmap is not None, timeout=20)
    win.fullview._title_label.setText("Pictures/" + os.path.relpath(photos[2], samples))
    win.fullview._date_label.setText(win.fullview._date_for(photos[2]))
    pump(app, lambda: False, timeout=0.8)
    grab(win, "photo-detail.png")
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
    grab(dlg, "google-photos-sync.png")
    dlg.close()

    win.close()
    print("done ->", OUT_DIR)


if __name__ == "__main__":
    main()

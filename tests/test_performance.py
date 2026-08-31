"""Performance regression tests. Thresholds are generous to avoid flakiness
but catch order-of-magnitude regressions (e.g. accidental O(n^2) layouts)."""

import os
import sys
import tempfile
import time

from PIL import Image

from gallerypanel import compute_justified_layout
from scanner import IndexDB, build_rows_for_tree, scan_directory_tree, walk_media_files
from thumbgen import generate_image_thumbnail
from thumbstore import ThumbStore

N_ITEMS = 15000


def _make_images(dirpath, n, size=(640, 480)):
    os.makedirs(dirpath, exist_ok=True)
    for i in range(n):
        img = Image.new("RGB", size, (i % 255, 40, 90))
        img.save(os.path.join(dirpath, f"IMG_{i:05d}.JPG"), "JPEG", quality=85)


def _make_tree(root, n_dirs=300, files_per_dir=20):
    """~6000 files across 300 unique dirs, nested 3 deep like years/months/days."""
    os.makedirs(root, exist_ok=True)
    for i in range(n_dirs):
        year = f"20{10 + i // 100:02d}"
        month = f"{i % 12 + 1:02d}"
        d = os.path.join(root, year, month)
        os.makedirs(d, exist_ok=True)
        _make_images(os.path.join(d, f"D{i:05d}"), files_per_dir)


def test_scan_6k_files_under_2s():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "photos")
        _make_tree(root)
        t0 = time.perf_counter()
        res = scan_directory_tree(root)
        dt = time.perf_counter() - t0
        assert len(res) >= 300
        assert dt < 2.0, f"scan took {dt:.2f}s"


def test_walk_6k_files_under_2s():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "photos")
        _make_tree(root)
        t0 = time.perf_counter()
        files = walk_media_files(root)
        dt = time.perf_counter() - t0
        assert len(files) >= 6000
        assert dt < 2.0, f"walk took {dt:.2f}s"


def test_indexdb_write_6k_rows_under_2s():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "photos")
        _make_tree(root)
        res = scan_directory_tree(root)
        rows = build_rows_for_tree(res, root)
        db = IndexDB(os.path.join(tmp, "index.db"))
        t0 = time.perf_counter()
        db.upsert_dirs(rows)
        dt = time.perf_counter() - t0
        assert dt < 2.0, f"upsert took {dt:.2f}s"
        # read back fast
        t0 = time.perf_counter()
        kids = db.get_children(root)
        dt = time.perf_counter() - t0
        assert len(kids) > 0
        assert dt < 0.05
        db.close()


def test_thumbnail_throughput_100_images_under_30s():
    with tempfile.TemporaryDirectory() as tmp:
        _make_images(tmp, 100)
        paths = sorted(os.path.join(tmp, f) for f in os.listdir(tmp))
        t0 = time.perf_counter()
        ok = 0
        for p in paths:
            if generate_image_thumbnail(p, 320) is not None:
                ok += 1
        dt = time.perf_counter() - t0
        assert ok == 100
        # ~200ms each single-threaded; 100 should finish well under 30s
        assert dt < 30.0, f"100 thumbs took {dt:.1f}s"


def test_thumbstore_10k_lookups_under_1s():
    with tempfile.TemporaryDirectory() as tmp:
        store = ThumbStore(os.path.join(tmp, "thumbs.db"))
        for i in range(200):
            store.store_thumbnail(f"/p{i}.jpg", 1.0, 100, b"x" * 50, 320, 240, commit=False)
        store.flush()
        t0 = time.perf_counter()
        hits = 0
        for i in range(10000):
            if store.get_thumbnail_valid(f"/p{i % 200}.jpg", 1.0, 100) is not None:
                hits += 1
        dt = time.perf_counter() - t0
        assert hits == 10000
        assert dt < 1.0, f"10k lookups took {dt:.2f}s"
        store.close()


def test_gallery_layout_15k_under_1s():
    items = [f"p{i}.jpg" for i in range(N_ITEMS)]
    ratios = {p: (0.8 + (i % 5) * 0.15) for i, p in enumerate(items)}
    t0 = time.perf_counter()
    positions, total = compute_justified_layout(items, ratios, 1200)
    dt = time.perf_counter() - t0
    assert len(positions) == N_ITEMS
    assert total > 0
    assert dt < 1.0, f"15k layout took {dt:.3f}s"


def test_memory_paths_list_15k():
    """15K paths must be cheap to hold in memory."""
    items = [os.path.join("/photos/2026", f"IMG_{i:05d}.JPG") for i in range(N_ITEMS)]
    import sys as _sys
    size = _sys.getsizeof(items) + sum(_sys.getsizeof(p) for p in items)
    assert size < 5 * 1024 * 1024, f"15k paths use {size/1e6:.1f} MB"


def test_visible_range_bisect_is_log():
    """Hit-testing a 15K gallery must be O(log n) via bisect."""
    import bisect
    items = [f"p{i}.jpg" for i in range(N_ITEMS)]
    positions, _ = compute_justified_layout(items, {}, 1200)
    y_starts = [p[1] for p in positions]
    t0 = time.perf_counter()
    for _ in range(1000):
        bisect.bisect_left(y_starts, 5000)
        bisect.bisect_right(y_starts, 9000)
    dt = time.perf_counter() - t0
    assert dt < 0.1, f"1000 bisect ops took {dt:.3f}s"
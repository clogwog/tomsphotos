"""GUI integration tests. Run headless via QT_QPA_PLATFORM=offscreen (set in conftest)."""

import os
import sys
import time

import pytest

from PIL import Image
from PySide6.QtCore import Qt

from gallerypanel import JustifiedGalleryView, hit_test_position
from scanner import IndexDB, build_rows_for_tree, scan_directory_tree, walk_media_files
from thumbgen import ThumbnailManager
from thumbstore import ThumbStore


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def media_tree(tmp_path):
    """Create a small years-style tree with real images."""
    root = tmp_path / "photos"
    for year, month, day, files in [
        ("2026", "01", "2026-01-01", ["a.JPG", "b.JPG", "c.MOV"]),
        ("2026", "01", "2026-01-02", ["d.JPG"]),
        ("2026", "02", "2026-02-01", ["e.JPG", "f.JPG"]),
    ]:
        d = root / year / month / day
        d.mkdir(parents=True)
        for fname in files:
            if fname.lower().endswith(".jpg"):
                Image.new("RGB", (800, 600), (30, 90, 200)).save(d / fname, "JPEG")
            else:
                (d / fname).write_bytes(b"\x00" * 100)
    return str(root)


@pytest.fixture
def manager(tmp_path):
    """Store + ThumbnailManager, always shut down cleanly."""
    store = ThumbStore(str(tmp_path / "thumbs.db"))
    mgr = ThumbnailManager(store, pool_size=2)
    mgr.start()
    try:
        yield mgr, store
    finally:
        mgr.shutdown()
        store.close()


@pytest.fixture
def gallery(qapp, manager, media_tree):
    mgr, store = manager
    g = JustifiedGalleryView(mgr)
    files = walk_media_files(media_tree)
    g.set_items_with_meta(
        [f[0] for f in files], {f[0]: (f[2], f[1]) for f in files}
    )
    return g


def _run_event_loop(app, until, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if until():
            return True
        time.sleep(0.02)
    return False


def test_tree_model_counts(qapp, media_tree, tmp_path):
    from treepanel import TreePanel
    db = IndexDB(str(tmp_path / "index.db"))
    res = scan_directory_tree(media_tree)
    db.upsert_dirs(build_rows_for_tree(res, media_tree))
    panel = TreePanel(db, root=media_tree)
    panel.model.fetchMore(panel.model.index(0, 0))
    assert panel.model.rowCount() == 1  # only 2026
    idx = panel.model.index(0, 0)
    assert panel.model.data(idx) == "2026 (6)"  # recursive count
    # expand year -> months
    panel.model.fetchMore(idx)
    assert panel.model.rowCount(idx) == 2
    m0 = panel.model.index(0, 0, idx)
    assert panel.model.data(m0) == "01 (4)"
    db.close()


def test_gallery_load_and_layout(qapp, gallery):
    g = gallery
    g.resize(800, 600)
    assert len(g._positions) == len(g._items)
    assert g._total_height > 0
    y_starts = [p[2] for p in g._positions]
    assert y_starts == sorted(y_starts)
    path, x, y, w, h = g._positions[0]
    assert hit_test_position(g._positions, y_starts, x + w / 2, y + h / 2) == path


def test_thumbnail_manager_end_to_end(qapp, manager, media_tree):
    mgr, store = manager
    files = walk_media_files(media_tree)
    thumbs = [f[0] for f in files if f[0].lower().endswith(".jpg")]
    for p in thumbs:
        mgr.request(p)
    done = _run_event_loop(qapp, lambda: mgr.stats()["done"] >= len(thumbs))
    assert done, f"timeout: {mgr.stats()}"
    s = mgr.stats()
    assert s["generated"] == len(thumbs)
    assert s["failed"] == 0
    for p in thumbs:
        st = os.stat(p)
        assert store.get_thumbnail_valid(p, st.st_mtime, st.st_size) is not None


def test_thumbnail_manager_priority_order(qapp, media_tree, tmp_path):
    """Higher-priority items must be generated first when pool is saturated."""
    store = ThumbStore(str(tmp_path / "prio.db"))
    mgr = ThumbnailManager(store, pool_size=1)  # single worker forces ordering
    mgr.start()
    try:
        files = walk_media_files(media_tree)
        thumbs = [f[0] for f in files if f[0].lower().endswith(".jpg")]
        generated = []
        mgr.thumbnail_ready.connect(lambda path, blob: generated.append(path))
        for i, p in enumerate(thumbs):
            mgr.request(p, priority=i)
        done = _run_event_loop(qapp, lambda: mgr.stats()["done"] >= len(thumbs))
        assert done
        assert generated[0] == thumbs[-1]  # highest priority first
    finally:
        mgr.shutdown()
        store.close()


def test_gallery_scroll_changes_content(qapp, manager, media_tree):
    """Scrolling must repaint different items (offset applied in paintEvent)."""
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QMainWindow
    mgr, store = manager
    g = JustifiedGalleryView(mgr)
    win = QMainWindow()
    win.resize(400, 300)
    win.setCentralWidget(g)
    win.show()
    files = walk_media_files(media_tree)
    paths = [f[0] for f in files]
    g.set_items_with_meta(paths, {f[0]: (f[2], f[1]) for f in files})
    sb = g.verticalScrollBar()
    assert sb.maximum() > 0, "gallery should be scrollable"

    def fp():
        img = g.viewport().grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        import hashlib
        return hashlib.md5(bytes(img.constBits())).hexdigest()

    top = fp()
    sb.setValue(sb.maximum() // 2)
    qapp.processEvents()
    mid = fp()
    sb.setValue(sb.maximum())
    qapp.processEvents()
    bot = fp()
    assert top != mid, "scrolling to middle should change content"
    assert mid != bot, "scrolling to bottom should change content"
    win.close()


def test_fullview_photo(qapp, manager, media_tree):
    from PySide6.QtWidgets import QMainWindow
    from fullview import FullView
    mgr, store = manager
    win = QMainWindow()
    win.resize(800, 600)
    gallery = JustifiedGalleryView(mgr)
    win.setCentralWidget(gallery)
    win.show()
    files = walk_media_files(media_tree)
    paths = [f[0] for f in files]
    gallery.set_items_with_meta(paths, {f[0]: (f[2], f[1]) for f in files})
    fv = FullView(gallery.viewport())
    fv.parent_store = store
    fv.resize(800, 600)
    photo = [p for p in paths if p.lower().endswith(".jpg")][0]
    fv.show_photo(photo)
    qapp.processEvents()
    assert fv._is_video is False
    assert fv._photo_pixmap is not None
    assert fv.isVisible()
    fv.close_view()
    assert not fv.isVisible()
    win.close()


def test_fullview_navigation_toolbar(qapp, manager, media_tree):
    from PySide6.QtWidgets import QMainWindow
    from fullview import FullView
    mgr, store = manager
    win = QMainWindow()
    win.resize(800, 600)
    gallery = JustifiedGalleryView(mgr)
    win.setCentralWidget(gallery)
    win.show()
    files = walk_media_files(media_tree)
    paths = [f[0] for f in files if f[0].lower().endswith(".jpg")]
    fv = FullView(gallery.viewport())
    fv.parent_store = store
    fv.set_items(paths)
    fv.resize(800, 600)
    fv.show_photo(paths[1])
    qapp.processEvents()
    assert fv._title_label.text() == paths[1]
    assert fv._previous_button.isVisible()
    assert fv._next_button.isVisible()
    assert fv._exit_button.isVisible()
    assert fv._title_label.alignment() == Qt.AlignCenter
    assert fv._date_label.alignment() == Qt.AlignCenter
    fv._show_next()
    qapp.processEvents()
    assert fv._title_label.text() == paths[2]
    assert fv._next_button.isVisible()
    fv._show_at(len(paths) - 1)
    qapp.processEvents()
    assert not fv._next_button.isVisible()
    fv._show_at(0)
    qapp.processEvents()
    assert not fv._previous_button.isVisible()
    fv.close_view()
    win.close()


def test_fullview_video_mode(qapp, manager, media_tree):
    from PySide6.QtWidgets import QMainWindow
    from fullview import FullView
    mgr, store = manager
    win = QMainWindow()
    win.resize(800, 600)
    gallery = JustifiedGalleryView(mgr)
    win.setCentralWidget(gallery)
    win.show()
    files = walk_media_files(media_tree)
    paths = [f[0] for f in files]
    gallery.set_items_with_meta(paths, {f[0]: (f[2], f[1]) for f in files})
    fv = FullView(gallery.viewport())
    fv.parent_store = store
    fv.resize(800, 600)
    video = [p for p in paths if p.lower().endswith(".mov")][0]
    fv.show_video(video)
    qapp.processEvents()
    assert fv._is_video is True
    assert fv.isVisible()
    fv.close_view()
    assert not fv.isVisible()
    win.close()
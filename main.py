"""tomsphotos — minimal photo browser for a configurable photo directory."""

import os
import sys

from PySide6.QtCore import QLockFile, QModelIndex, QThread, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmapCache
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QMainWindow, QWidget

import config
from fullview import FullView
from gallerypanel import JustifiedGalleryView
from config import MODE_DETAIL, MODE_THUMBNAIL
from scanner import IndexDB, ScannerThread, walk_media_files
from statusbar import StatusBar
from thumbgen import ThumbnailManager
from thumbstore import ThumbStore
from treepanel import TreePanel


class ScanWorker(QThread):
    finished_scan = Signal(int)
    progress = Signal(int, int)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.scanner = ScannerThread(root=root)

    def run(self):
        n = self.scanner.scan(progress_cb=lambda d, t: self.progress.emit(d, t))
        self.finished_scan.emit(n or 0)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.DISPLAY_NAME)
        self.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), "app_icon.svg")))
        self.resize(1440, 900)
        self.setStyleSheet(config.QSS)
        self.mode = MODE_THUMBNAIL

        self.index_db = IndexDB()
        self.thumb_store = ThumbStore()
        self.thumb_manager = ThumbnailManager(self.thumb_store, pool_size=config.THREAD_POOL_SIZE)
        self.thumb_manager.start()
        QPixmapCache.setCacheLimit(config.QPIXMAP_CACHE_SIZE // 1024)

        self._build_ui()
        self._build_menu()
        self._wire_signals()
        self._start_background_scan()

    def _build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        self._set_dir_action = QAction("Set Photo Directory…", self)
        self._set_dir_action.setShortcut(QKeySequence("Ctrl+O"))
        self._set_dir_action.triggered.connect(self._choose_source_root)
        file_menu.addAction(self._set_dir_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _build_ui(self):
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.tree = TreePanel(self.index_db)
        lay.addWidget(self.tree)

        self.gallery = JustifiedGalleryView(self.thumb_manager)
        lay.addWidget(self.gallery, 1)

        self.fullview = FullView(self.gallery.viewport())
        self.fullview.parent_store = self.thumb_store
        self.fullview.hide()

        self.status = StatusBar()
        self.setCentralWidget(central)
        self.statusBar().addWidget(self.status, 1)

    def _wire_signals(self):
        self.tree.directory_selected.connect(self.load_directory)
        self.gallery.photo_clicked.connect(self._show_photo_detail)
        self.gallery.video_clicked.connect(self._show_video_detail)
        self.gallery.item_deleted.connect(self._on_item_deleted)
        self.fullview.closed.connect(self._on_fullview_closed)
        self.fullview.delete_requested.connect(self._delete_from_detail)
        self.fullview.rotate_requested.connect(self._rotate_from_detail)
        self.gallery.rotate_requested.connect(self._rotate_from_detail)
        self.gallery.refresh_requested.connect(self._reload_current_directory)
        self.thumb_manager.progress.connect(self._on_thumb_progress)
        self.thumb_manager.all_done.connect(self.status.hide_thumb_progress)

    def _start_background_scan(self):
        # Show what's already indexed immediately (stale counts ok), then
        # re-scan incrementally in the background so the tree catches up.
        self.tree.model.fetchMore(QModelIndex())
        self.tree.expandToDepth(0)
        self.status.show_scan("Scanning directories…")
        self._scan_worker = ScanWorker(config.SOURCE_ROOT, self)
        self._scan_worker.progress.connect(
            lambda d, t: self.status.show_scan(f"Scanning directories… {d:,} dirs")
        )
        self._scan_worker.finished_scan.connect(self._on_scan_done)
        self._scan_worker.start()

    def _choose_source_root(self):
        start = config.SOURCE_ROOT
        if not os.path.isdir(start):
            start = config.DEFAULT_SOURCE_ROOT
        path = QFileDialog.getExistingDirectory(self, "Choose Photo Directory", start)
        if not path:
            return
        self._apply_source_root(path)

    def _apply_source_root(self, path):
        config.set_source_root(path)
        if hasattr(self, "_scan_worker") and self._scan_worker.isRunning():
            self._scan_worker.scanner.cancel()
            self._scan_worker.wait(2000)
        self.tree.set_root(path)
        self.status.set_info(f"Photo directory: {path}")
        self._start_background_scan()
        self.load_directory(path)

    def _on_scan_done(self, n):
        self.status.hide_scan()
        self.tree.model.refresh_root()

    def _show_photo_detail(self, path):
        self.mode = MODE_DETAIL
        self.gallery.set_toolbar_visible(False)
        self.fullview.show_photo(path)

    def _show_video_detail(self, path):
        self.mode = MODE_DETAIL
        self.gallery.set_toolbar_visible(False)
        self.fullview.show_video(path)

    def load_directory(self, path):
        self.mode = MODE_THUMBNAIL
        if not os.path.isdir(path):
            return
        self._current_dir = path
        self.gallery.set_toolbar_visible(True)
        self.fullview.close_view()
        self.status.set_info(path.replace(config.SOURCE_ROOT, "…", 1))
        files = walk_media_files(path)
        paths = [f[0] for f in files]
        meta = {f[0]: (f[2], f[1]) for f in files}  # path -> (mtime, size)
        self.gallery.set_items_with_meta(paths, meta)
        self.fullview.set_items(paths)
        self.gallery.setFocus()

    def _reload_current_directory(self):
        if not getattr(self, "_current_dir", None):
            return
        QPixmapCache.clear()
        selected = self.gallery._selected_path
        self.load_directory(self._current_dir)
        if selected:
            self.gallery.select_path(selected)
        self.gallery.setFocus()
        # Re-scan the tree so directory counts refresh too.
        self._start_background_scan()

    def _on_thumb_progress(self, done, total):
        self.status.show_thumb_progress(done, total)

    def _on_item_deleted(self, path):
        self.status.set_info(f"Moved to Trash: {os.path.basename(path)}")

    def _delete_from_detail(self, path, confirm):
        if self.gallery.delete_path(path, confirm):
            self.mode = MODE_THUMBNAIL
            self.gallery.set_toolbar_visible(True)
            self.fullview.close_view()
            self.gallery.setFocus()
            self.gallery.viewport().update()

    def _rotate_from_detail(self, path, clockwise):
        from rotate_media import rotate_media
        ok, message = rotate_media(path, clockwise)
        if not ok:
            self.status.set_info(message)
            return
        self.thumb_store.invalidate(path)
        self.gallery.refresh_item(path)
        self.fullview.reload_current()
        self.status.set_info(message)

    def _on_fullview_closed(self, path):
        self.mode = MODE_THUMBNAIL
        self.gallery.set_toolbar_visible(True)
        if path:
            self.gallery.select_path(path)
        self.gallery.setFocus()
        self.gallery.viewport().update()

    def closeEvent(self, event):
        self.thumb_manager.shutdown()
        self.index_db.close()
        self.thumb_store.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), "app_icon.svg")))
    app.setApplicationName(config.DISPLAY_NAME)
    app.setApplicationDisplayName(config.DISPLAY_NAME)
    app.setOrganizationName(config.APP_NAME)

    lock = QLockFile(os.path.join(config.DATA_DIR, "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        sys.exit("tomsphotos is already running")

    if not os.path.isdir(config.SOURCE_ROOT):
        sys.exit(f"Source directory not found: {config.SOURCE_ROOT}")

    win = MainWindow()
    win.show()
    rc = app.exec()
    lock.unlock()
    sys.exit(rc)


if __name__ == "__main__":
    main()
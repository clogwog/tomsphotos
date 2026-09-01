"""Right panel: Google-Photos-style justified gallery with virtualization.

Only visible thumbnails are painted and decoded. Layout is recomputed from
known aspect ratios; unknown ratios default to 1.0 until a dimension probe
fills them in (debounced reflow).
"""

import bisect
import os
import subprocess
import time

from send2trash import send2trash

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap, QPixmapCache
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QHBoxLayout, QMessageBox, QPushButton, QStyle, QWidget

from config import (
    GAP,
    MAX_ROW_HEIGHT,
    MAX_ROW_THUMBS,
    MIN_ROW_HEIGHT,
    PRELOAD_PAGES,
    TARGET_ROW_HEIGHT,
    VIDEO_EXTENSIONS,
)

PLACEHOLDER_COLOR = QColor("#1a1a1a")
DEFAULT_RATIO = 1.0
TOOLBAR_HEIGHT = 52


def hit_test_position(positions, y_starts, x, y):
    """Return the item path under (x, y) in viewport coords, or None."""
    if not positions:
        return None
    i = bisect.bisect_right(y_starts, y) - 1
    if i < 0:
        return None
    row_y = y_starts[i]
    # walk back to the first item of the row
    while i > 0 and y_starts[i - 1] == row_y:
        i -= 1
    # walk the row (contiguous items with same y_start) checking x
    while i < len(positions) and positions[i][2] == row_y:
        path, px, py, pw, ph = positions[i]
        if px <= x <= px + pw and py <= y <= py + ph:
            return path
        i += 1
    return None


def compute_justified_layout(items, ratios, width, target_height=TARGET_ROW_HEIGHT,
                             gap=GAP, min_h=MIN_ROW_HEIGHT, max_h=MAX_ROW_HEIGHT,
                             max_row_thumbs=MAX_ROW_THUMBS):
    """Pure layout math. Returns (positions, total_height).

    positions: list of (path, x, y, w, h). Rows are justified when they
    fill the width; the last (partial) row keeps target height.
    """
    positions = []
    y = 0
    row = []
    row_nat = 0.0
    width = max(1, width)
    for path in items:
        ratio = ratios.get(path, DEFAULT_RATIO)
        row_nat += target_height * ratio + gap
        row.append(path)
        if row_nat >= width and len(row) >= 2:
            y = _emit_row(positions, ratios, row, row_nat, width, y, target_height, gap, min_h, max_h)
            row, row_nat = [], 0.0
        if len(row) >= max_row_thumbs:
            y = _emit_row(positions, ratios, row, row_nat, width, y, target_height, gap, min_h, max_h)
            row, row_nat = [], 0.0
    if row:
        y = _emit_row(positions, ratios, row, row_nat, width, y, target_height, gap, min_h, max_h)
    return positions, y


def _emit_row(positions, ratios, row, row_nat, width, y, target_height, gap, min_h, max_h):
    n = len(row)
    if row_nat >= width and n >= 2:
        sum_ratios = sum(ratios.get(p, DEFAULT_RATIO) for p in row)
        avail = width - gap * (n - 1)
        h = avail / sum_ratios if sum_ratios > 0 else target_height
    else:
        h = target_height
    h = max(min_h, min(max_h, h))
    x = 0
    for path in row:
        ratio = ratios.get(path, DEFAULT_RATIO)
        w = h * ratio
        positions.append((path, x, y, w, h))
        x += w + gap
    return y + h + gap


class JustifiedGalleryView(QAbstractScrollArea):
    photo_clicked = Signal(str)
    video_clicked = Signal(str)
    item_deleted = Signal(str)
    rotate_requested = Signal(str, bool)  # path, clockwise
    refresh_requested = Signal()

    def __init__(self, thumb_manager, parent=None):
        super().__init__(parent)
        self.manager = thumb_manager
        self._items = []          # list of paths
        self._meta = {}           # path -> (mtime, size)
        self._ratios = {}         # path -> w/h
        self._positions = []      # list of (path, x, y, w, h)
        self._y_starts = []       # parallel array of y for bisect
        self._pos_index = {}      # path -> index in _positions
        self._selected_path = None
        self._deleted_paths = set()
        self._total_height = 0
        self._needs_layout = False
        self._pending_dims = set()
        self._scroll_active = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._reflow)
        # Throttle scroll-triggered work: probe/request at most every 30ms,
        # and only reflow once scrolling stops.
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(30)
        self._sync_timer.timeout.connect(self._sync_visible)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(200)
        self._scroll_timer.timeout.connect(self._on_scroll_idle)
        self._scroll_anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_anim.setDuration(110)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._build_toolbar()

        self.manager.thumbnail_ready.connect(self._on_thumb_ready)
        self.manager.dims_ready.connect(self._on_dims_ready)

        self.setViewportMargins(0, TOOLBAR_HEIGHT, 0, 0)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_OpaquePaintEvent, True)

    def _build_toolbar(self):
        self._toolbar = QWidget(self)
        self._toolbar.setStyleSheet("background:rgba(18,18,18,235); border-bottom:1px solid #333;")
        lay = QHBoxLayout(self._toolbar)
        lay.setContentsMargins(16, 6, 16, 6)
        lay.setSpacing(8)

        self.tb_refresh = QPushButton("⟳")
        self.tb_refresh.setToolTip("Reload thumbnails")
        self.tb_open = QPushButton()
        self.tb_open.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.tb_open.setToolTip("Open containing folder")
        self.tb_copy = QPushButton("⧉")
        self.tb_copy.setToolTip("Copy image to clipboard")
        self.tb_rotate_ccw = QPushButton("↺")
        self.tb_rotate_ccw.setToolTip("Rotate counter-clockwise 90°")
        self.tb_rotate_cw = QPushButton("↻")
        self.tb_rotate_cw.setToolTip("Rotate clockwise 90°")
        self.tb_exit = QPushButton("×")
        self.tb_exit.setToolTip("Quit")

        style = (
            "QPushButton { color:#fff; background:transparent; border:none; font-size:20px; }"
            "QPushButton:hover { background:#333; border-radius:19px; }"
        )
        for button in (self.tb_refresh, self.tb_open, self.tb_copy,
                       self.tb_rotate_ccw, self.tb_rotate_cw, self.tb_exit):
            button.setFixedSize(38, 38)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(style)
        self.tb_exit.setStyleSheet(self.tb_exit.styleSheet().replace("font-size:20px", "font-size:26px"))

        self.tb_refresh.clicked.connect(self.refresh_requested.emit)
        self.tb_open.clicked.connect(self._open_selected_folder)
        self.tb_copy.clicked.connect(self._copy_selected)
        self.tb_rotate_ccw.clicked.connect(lambda: self._rotate_selected(False))
        self.tb_rotate_cw.clicked.connect(lambda: self._rotate_selected(True))
        self.tb_exit.clicked.connect(self._confirm_exit)

        lay.addWidget(self.tb_refresh)
        lay.addWidget(self.tb_open)
        lay.addWidget(self.tb_copy)
        lay.addStretch(1)
        lay.addWidget(self.tb_rotate_ccw)
        lay.addWidget(self.tb_rotate_cw)
        lay.addWidget(self.tb_exit)

    def _rotate_selected(self, clockwise):
        if self._selected_path:
            self.rotate_requested.emit(self._selected_path, clockwise)

    def _open_selected_folder(self):
        path = self._selected_path
        if not path or not os.path.exists(path):
            return
        try:
            subprocess.Popen(["open", "-R", path])
        except OSError:
            pass

    def _copy_selected(self):
        path = self._selected_path
        if not path:
            return
        pm = QPixmapCache.find(path)
        if pm is not None and not pm.isNull():
            QGuiApplication.clipboard().setPixmap(pm)
        else:
            from thumbgen import get_image_dimensions
            if path and os.path.exists(path):
                QGuiApplication.clipboard().setText(path)

    def set_toolbar_visible(self, visible):
        self._toolbar.setVisible(visible)

    def _confirm_exit(self):
        answer = QMessageBox.question(
            self,
            "Quit",
            "Quit tomsphotos?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            QGuiApplication.quit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_items(self, paths):
        self.set_items_with_meta(paths, {})

    def set_items_with_meta(self, paths, meta):
        self._items = list(paths)
        self._selected_path = self._items[0] if self._items else None
        self._deleted_paths = set()
        self._meta = dict(meta)
        self._ratios = {}
        self._positions = []
        self._y_starts = []
        self._pos_index = {}
        self._total_height = 0
        self._pending_dims = set()
        self.verticalScrollBar().setValue(0)
        self._needs_layout = True
        self._reflow()
        self._seed_visible_ratios()
        self._reflow()
        self._probe_visible_dims()
        self._request_visible_thumbs()
        self.manager.request_background(self._items)
        self.viewport().update()

    def clear(self):
        self.set_items([])

    def select_path(self, path):
        if path not in self._pos_index:
            return
        self._set_selected(path)
        position = self._pos_index[path]
        item = self._positions[position]
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        if item[2] < top:
            self.verticalScrollBar().setValue(max(0, int(item[2])))
        elif item[2] + item[4] > bottom:
            self.verticalScrollBar().setValue(max(0, int(item[2] + item[4] - self.viewport().height())))
        self.viewport().update()

    def refresh_item(self, path):
        """Drop cached data for one item so its thumbnail, ratio and dims are
        re-derived (used after the file's metadata changed, e.g. rotation)."""
        QPixmapCache.remove(path)
        self._ratios.pop(path, None)
        self._pending_dims.discard(path)
        self.manager.request_dims(path, priority=10000)
        self.manager.request(path, priority=10000)
        self._needs_layout = True
        self.viewport().update()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _reflow(self):
        anchor = self._anchor()
        width = max(1, self.viewport().width())
        positions, total = compute_justified_layout(
            self._items, self._ratios, width,
            TARGET_ROW_HEIGHT, GAP, MIN_ROW_HEIGHT, MAX_ROW_HEIGHT, MAX_ROW_THUMBS,
        )
        self._positions = positions
        self._y_starts = [p[2] for p in positions]
        self._pos_index = {p[0]: i for i, p in enumerate(positions)}
        self._total_height = total
        self.verticalScrollBar().setRange(0, max(0, total - self.viewport().height()))
        self._restore_anchor(anchor)
        self._needs_layout = False

    def _anchor(self):
        """(path, viewport-relative offset) of the item at the viewport top."""
        if not self._positions:
            return None
        top = self.verticalScrollBar().value()
        i = bisect.bisect_left(self._y_starts, top)
        if i >= len(self._positions):
            i = len(self._positions) - 1
        path, y = self._positions[i][0], self._positions[i][2]
        return path, y - top

    def _restore_anchor(self, anchor):
        if anchor is None:
            return
        path, offset = anchor
        i = self._pos_index.get(path)
        if i is None:
            return
        self.verticalScrollBar().setValue(max(0, int(self._positions[i][2] - offset)))

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), QColor("#000000"))
        positions = self._positions
        if not positions:
            return
        offset = self.verticalScrollBar().value()
        top = event.rect().top() + offset
        bot = event.rect().bottom() + offset
        i0 = bisect.bisect_left(self._y_starts, top - MAX_ROW_HEIGHT)
        i1 = bisect.bisect_right(self._y_starts, bot)
        for i in range(i0, i1):
            path, x, y, w, h = positions[i]
            rect = QRect(int(x), int(y - offset), int(w), int(h))
            if not rect.intersects(event.rect()):
                continue
            if path in self._deleted_paths:
                self._draw_deleted_placeholder(painter, rect)
                continue
            pm = QPixmapCache.find(path)
            if pm is not None and not pm.isNull():
                # Fast transform when downscaling (the common case for grid
                # thumbs) — smooth scaling is the paint bottleneck.
                if rect.width() > pm.width() or rect.height() > pm.height():
                    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                else:
                    painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
                painter.drawPixmap(rect, pm, pm.rect())
            else:
                painter.fillRect(rect, PLACEHOLDER_COLOR)
                # Visible items always outrank preload (which is 0..100).
                self._request_thumb(path, priority=10000)
            if self._is_video(path):
                self._draw_play_icon(painter, rect)
            if path == self._selected_path:
                painter.setPen(QPen(QColor("#78c8ff"), 3))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.end()

    def _draw_deleted_placeholder(self, painter, rect):
        painter.fillRect(rect, QColor("#000000"))
        painter.setPen(QPen(QColor("#78c8ff"), 5, Qt.SolidLine, Qt.RoundCap))
        inset = max(8, min(rect.width(), rect.height()) // 8)
        painter.drawLine(rect.left() + inset, rect.top() + inset, rect.right() - inset, rect.bottom() - inset)
        painter.drawLine(rect.right() - inset, rect.top() + inset, rect.left() + inset, rect.bottom() - inset)

    def _draw_play_icon(self, painter, rect):
        cx, cy = rect.center().x(), rect.center().y()
        r = min(rect.width(), rect.height()) * 0.18
        r = max(10, min(28, r))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawEllipse(QPoint(cx, cy), int(r), int(r))
        tri = [QPoint(int(cx - r * 0.35), int(cy - r * 0.45)),
               QPoint(int(cx - r * 0.35), int(cy + r * 0.45)),
               QPoint(int(cx + r * 0.5), int(cy))]
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawPolygon(tri)

    # ------------------------------------------------------------------
    # Thumbnail pipeline
    # ------------------------------------------------------------------
    def _request_thumb(self, path, priority=0):
        if path in self._deleted_paths:
            return
        # The manager loads from disk cache in a worker thread; painting only
        # ever touches the RAM QPixmapCache, so scrolling never blocks on I/O.
        self.manager.request(path, priority=priority)

    def _is_video(self, path):
        return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS

    def _visible_range(self):
        vp = self.viewport()
        top = self.verticalScrollBar().value()
        bot = top + vp.height()
        return top, bot

    def _request_visible_thumbs(self):
        top, bot = self._visible_range()
        positions = self._positions
        if not positions:
            return
        visible_i0 = bisect.bisect_left(self._y_starts, top - MAX_ROW_HEIGHT)
        visible_i1 = bisect.bisect_right(self._y_starts, bot)
        preload_i1 = bisect.bisect_right(self._y_starts, bot + MAX_ROW_HEIGHT * PRELOAD_PAGES)
        mid = (top + bot) / 2
        for i in range(visible_i0, preload_i1):
            path, x, y, w, h = positions[i]
            if QPixmapCache.find(path) is not None:
                continue
            is_visible = y + h >= top and y <= bot
            if is_visible:
                priority = 10000
            else:
                dist = abs((y + h / 2) - mid)
                priority = max(0, 100 - int(dist / 100))
            self._request_thumb(path, priority=priority)

    def _seed_visible_ratios(self):
        from thumbgen import get_dimensions
        if not self._positions:
            return
        top, bot = self._visible_range()
        i0 = bisect.bisect_left(self._y_starts, top)
        i1 = min(len(self._positions), bisect.bisect_right(self._y_starts, bot + MAX_ROW_HEIGHT))
        for path, _, _, _, _ in self._positions[i0:i1]:
            if path in self._ratios:
                continue
            dimensions = get_dimensions(path)
            if dimensions and dimensions[1]:
                self._ratios[path] = dimensions[0] / dimensions[1]

    def _probe_visible_dims(self):
        top, bot = self._visible_range()
        positions = self._positions
        if not positions:
            return
        i0 = bisect.bisect_left(self._y_starts, top - MAX_ROW_HEIGHT)
        i1 = bisect.bisect_right(self._y_starts, bot + MAX_ROW_HEIGHT * PRELOAD_PAGES)
        for i in range(i0, i1):
            path = positions[i][0]
            if path in self._ratios or path in self._pending_dims:
                continue
            self._pending_dims.add(path)
            self.manager.request_dims(path, priority=1)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_thumb_ready(self, path, image):
        pm = QPixmap.fromImage(image)
        if not pm.isNull():
            QPixmapCache.insert(path, pm)
        old_ratio = self._ratios.get(path)
        new_ratio = image.width() / image.height() if image.height() else DEFAULT_RATIO
        self._ratios[path] = new_ratio
        if old_ratio is None or abs(old_ratio - new_ratio) > 0.02:
            self._needs_layout = True
            if not self._scroll_active:
                self._debounce.start()
        rect = self._item_rect(path)
        if rect is not None:
            self.viewport().update(rect)

    def _item_rect(self, path):
        """Viewport-space rect for an item if it's visible, else None."""
        index = self._pos_index.get(path)
        if index is None:
            return None
        p = self._positions[index]
        top = self.verticalScrollBar().value()
        bot = top + self.viewport().height()
        if p[2] + p[4] < top or p[2] > bot:
            return None
        return QRect(int(p[1]), int(p[2] - top), int(p[3]), int(p[4]))

    def _on_dims_ready(self, path, w, h):
        self._pending_dims.discard(path)
        old = self._ratios.get(path)
        new = (w / h) if h else DEFAULT_RATIO
        self._ratios[path] = new
        if old is None or abs(old - new) > 0.02:
            self._needs_layout = True
            # Never reflow mid-scroll — it shifts the scrollbar range and
            # makes scrolling feel jumpy. Settle once the user stops.
            if not self._scroll_active:
                self._debounce.start()
        rect = self._item_rect(path)
        if rect is not None:
            self.viewport().update(rect)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._toolbar.setGeometry(0, 0, self.width(), TOOLBAR_HEIGHT)
        self._toolbar.raise_()
        self._reflow()
        self._request_visible_thumbs()
        self.viewport().update()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if dx or dy:
            self._scroll_active = True
            self._scroll_timer.start()
            self.viewport().update()

    def _sync_visible(self):
        self._probe_visible_dims()
        if self._scroll_active:
            # During active scrolling only the paint path requests visible
            # thumbs (high priority, bounded). Skipping preload here stops
            # the worker queue from flooding and saturating CPU mid-scroll.
            return
        self._request_visible_thumbs()

    def _on_scroll_idle(self):
        self._scroll_active = False
        if self._needs_layout:
            self._reflow()
        self._sync_visible()

    def _animate_scroll(self, target):
        bar = self.verticalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), target))
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    def _set_selected(self, path):
        old = self._selected_path
        self._selected_path = path
        for item in (old, path):
            rect = self._item_rect(item) if item else None
            if rect is not None:
                self.viewport().update(rect)

    def _move_selection(self, delta):
        if not self._items:
            return
        current = self._items.index(self._selected_path) if self._selected_path in self._items else 0
        index = current + delta
        while 0 <= index < len(self._items) and self._items[index] in self._deleted_paths:
            index += delta
        if not 0 <= index < len(self._items):
            return
        self._set_selected(self._items[index])
        position = self._pos_index.get(self._selected_path)
        if position is not None:
            item = self._positions[position]
            top = self.verticalScrollBar().value()
            bottom = top + self.viewport().height()
            if item[2] < top:
                self._animate_scroll(item[2])
            elif item[2] + item[4] > bottom:
                self._animate_scroll(item[2] + item[4] - self.viewport().height())

    def _open_selected(self):
        if self._selected_path is None or self._selected_path in self._deleted_paths:
            return
        if self._is_video(self._selected_path):
            self.video_clicked.emit(self._selected_path)
        else:
            self.photo_clicked.emit(self._selected_path)

    def _delete_selected(self, confirm):
        self.delete_path(self._selected_path, confirm)

    def delete_path(self, path, confirm):
        if not path or path in self._deleted_paths:
            return False
        self._set_selected(path)
        if confirm:
            answer = QMessageBox.question(
                self,
                "Move to Trash",
                f"Move {os.path.basename(path)} to Trash?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return False
        try:
            send2trash(path)
        except OSError as error:
            QMessageBox.warning(self, "Move to Trash", str(error))
            return False
        index = self._items.index(path)
        self._deleted_paths.add(path)
        target = None
        for candidate_index in range(index - 1, -1, -1):
            candidate = self._items[candidate_index]
            if candidate not in self._deleted_paths:
                target = candidate
                break
        if target is None:
            for candidate_index in range(index + 1, len(self._items)):
                candidate = self._items[candidate_index]
                if candidate not in self._deleted_paths:
                    target = candidate
                    break
        if target is not None:
            self._set_selected(target)
        self.viewport().update()
        self.item_deleted.emit(path)
        return True

    def _select_row(self, row_index, row_ys, current_center_x):
        if not 0 <= row_index < len(row_ys):
            return
        target_y = row_ys[row_index]
        candidates = [
            position for position in self._positions
            if position[2] == target_y and position[0] not in self._deleted_paths
        ]
        if not candidates:
            return
        target = min(
            candidates,
            key=lambda position: abs(position[1] + position[3] / 2 - current_center_x),
        )
        self.select_path(target[0])

    def _move_selection_row(self, direction):
        if not self._selected_path or self._selected_path not in self._pos_index:
            return
        current = self._positions[self._pos_index[self._selected_path]]
        row_ys = sorted({position[2] for position in self._positions})
        row_index = row_ys.index(current[2])
        self._select_row(row_index + direction, row_ys, current[1] + current[3] / 2)

    def _move_selection_page(self, direction):
        if not self._selected_path or self._selected_path not in self._pos_index:
            return
        current = self._positions[self._pos_index[self._selected_path]]
        row_ys = sorted({position[2] for position in self._positions})
        row_index = row_ys.index(current[2])
        target_y = current[2] + direction * self.viewport().height()
        if direction > 0:
            target_index = min(
                len(row_ys) - 1,
                bisect.bisect_left(row_ys, target_y),
            )
        else:
            target_index = max(0, bisect.bisect_right(row_ys, target_y) - 1)
        self._select_row(target_index, row_ys, current[1] + current[3] / 2)

    def keyPressEvent(self, event):
        if event.text() == "X":
            self._delete_selected(False)
            event.accept()
            return
        if event.text() == "x":
            self._delete_selected(True)
            event.accept()
            return
        if event.key() == Qt.Key_Left:
            self._move_selection(-1)
            event.accept()
            return
        if event.key() == Qt.Key_Right:
            self._move_selection(1)
            event.accept()
            return
        if event.key() == Qt.Key_Space:
            self._open_selected()
            event.accept()
            return
        if event.key() == Qt.Key_Up:
            self._move_selection_row(-1)
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            self._move_selection_row(1)
            event.accept()
            return
        if event.key() == Qt.Key_PageUp:
            self._move_selection_page(-1)
            event.accept()
            return
        if event.key() == Qt.Key_PageDown:
            self._move_selection_page(1)
            event.accept()
            return
        if event.text() == "[":
            self.rotate_requested.emit(self._selected_path or "", False)
            self._move_selection(1)
            event.accept()
            return
        if event.text() == "]":
            self.rotate_requested.emit(self._selected_path or "", True)
            self._move_selection(1)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        x = pos.x()
        y = pos.y() + self.verticalScrollBar().value()
        path = hit_test_position(self._positions, self._y_starts, x, y)
        if path is None:
            return
        self._set_selected(path)
        if self._is_video(path):
            self.video_clicked.emit(path)
        else:
            self.photo_clicked.emit(path)
        super().mousePressEvent(event)
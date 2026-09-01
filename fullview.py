"""Full-size photo / video overlay. Covers the gallery viewport exactly;
scroll position is preserved because the gallery never scrolls underneath."""

import datetime
import os
import subprocess

from PySide6.QtCore import QEvent, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QStyle,
)

from config import THUMB_SIZE_FULL, VIDEO_EXTENSIONS
from thumbgen import generate_image_thumbnail


def _fmt_ms(ms):
    ms = max(0, int(ms))
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class _ControlBar(QWidget):
    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(2500)
        self._hide_timer.timeout.connect(self._auto_hide)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)

        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(32, 32)
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.clicked.connect(self._toggle_play)
        lay.addWidget(self.play_btn)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("color:#fff; font-size:12px;")
        lay.addWidget(self.time_label)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._seek)
        lay.addWidget(self.slider, 1)

        self.vol_btn = QPushButton("🔊")
        self.vol_btn.setFixedSize(28, 28)
        self.vol_btn.setCursor(Qt.PointingHandCursor)
        self.vol_btn.clicked.connect(self._toggle_mute)
        lay.addWidget(self.vol_btn)

        self.setStyleSheet(
            "QWidget { background: rgba(20,20,20,200); border-radius: 6px; }"
            "QLabel { background: transparent; }"
        )

        player.positionChanged.connect(self._on_pos)
        player.durationChanged.connect(self._on_duration)
        player.playbackStateChanged.connect(self._on_state)
        player.mediaStatusChanged.connect(self._on_media_status)

    def showEvent(self, event):
        super().showEvent(event)
        self._hide_timer.start()

    def mouseMoveEvent(self, event):
        self._hide_timer.start()
        super().mouseMoveEvent(event)

    def _auto_hide(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.hide()

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _toggle_mute(self):
        self.player.setMuted(not self.player.isMuted())

    def _seek(self, frac):
        dur = self.player.duration()
        if dur > 0:
            self.player.setPosition(int(dur * frac / 1000))

    def _on_pos(self, pos):
        dur = self.player.duration()
        if dur > 0:
            self.slider.setValue(int(pos * 1000 / dur))
        self.time_label.setText(f"{_fmt_ms(pos)} / {_fmt_ms(dur)}")

    def _on_duration(self, dur):
        self.time_label.setText(f"0:00 / {_fmt_ms(dur)}")

    def _on_state(self, state):
        if state == QMediaPlayer.PlayingState:
            self.play_btn.setText("⏸")
            self._hide_timer.start()
        else:
            self.play_btn.setText("▶")
            self._hide_timer.stop()
            self.show()

    def _on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0)
            self.player.pause()


class FullView(QWidget):
    closed = Signal(str)
    delete_requested = Signal(str, bool)
    rotate_requested = Signal(str, bool)  # path, clockwise

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:#000;")
        self.setFocusPolicy(Qt.StrongFocus)
        self._stack = QStackedWidget(self)
        self._stack.setGeometry(self.rect())
        self._items = []
        self._index = -1
        self.mode = "thumbnail"
        self._toolbar = QWidget(self)
        self._toolbar.setStyleSheet("background:rgba(18,18,18,235); border-bottom:1px solid #333;")
        toolbar_layout = QHBoxLayout(self._toolbar)
        toolbar_layout.setContentsMargins(16, 6, 16, 6)
        toolbar_layout.setSpacing(8)
        self._exit_button = QPushButton("×")
        self._open_folder_button = QPushButton()
        self._open_folder_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self._open_folder_button.setToolTip("Open containing folder")
        self._copy_button = QPushButton("⧉")
        self._copy_button.setToolTip("Copy image to clipboard")
        self._rotate_cw_button = QPushButton("↻")
        self._rotate_cw_button.setToolTip("Rotate clockwise 90°")
        self._rotate_ccw_button = QPushButton("↺")
        self._rotate_ccw_button.setToolTip("Rotate counter-clockwise 90°")
        self._previous_button = QPushButton("‹")
        self._next_button = QPushButton("›")
        self._exit_button.setFixedSize(38, 38)
        self._open_folder_button.setFixedSize(38, 38)
        self._copy_button.setFixedSize(38, 38)
        self._rotate_cw_button.setFixedSize(38, 38)
        self._rotate_ccw_button.setFixedSize(38, 38)
        self._previous_button.setFixedSize(38, 38)
        self._next_button.setFixedSize(38, 38)
        self._exit_button.setStyleSheet("QPushButton { color:#fff; background:transparent; border:none; font-size:28px; } QPushButton:hover { background:#333; border-radius:19px; }")
        self._open_folder_button.setStyleSheet("QPushButton { color:#fff; background:transparent; border:none; } QPushButton:hover { background:#333; border-radius:19px; }")
        self._copy_button.setStyleSheet("QPushButton { color:#fff; background:transparent; border:none; font-size:20px; } QPushButton:hover { background:#333; border-radius:19px; }")
        for button in (self._rotate_cw_button, self._rotate_ccw_button):
            button.setStyleSheet("QPushButton { color:#fff; background:transparent; border:none; font-size:20px; } QPushButton:hover { background:#333; border-radius:19px; }")
        for button in (self._previous_button, self._next_button):
            button.setStyleSheet("QPushButton { color:#fff; background:transparent; border:none; font-size:30px; } QPushButton:hover { background:#333; border-radius:19px; }")
        self._exit_button.clicked.connect(self._exit_detail)
        self._open_folder_button.clicked.connect(self._open_containing_folder)
        self._copy_button.clicked.connect(self._copy_image)
        self._rotate_cw_button.clicked.connect(lambda: self._rotate_current(True))
        self._rotate_ccw_button.clicked.connect(lambda: self._rotate_current(False))
        self._previous_button.clicked.connect(self._show_previous)
        self._next_button.clicked.connect(self._show_next)
        self._title_label = QLabel()
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.setStyleSheet("color:#fff; font-size:13px;")
        self._date_label = QLabel()
        self._date_label.setAlignment(Qt.AlignCenter)
        self._date_label.setStyleSheet("color:#999; font-size:12px;")
        metadata = QWidget()
        metadata.setFixedWidth(420)
        metadata_layout = QVBoxLayout(metadata)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(0)
        metadata_layout.addWidget(self._title_label)
        metadata_layout.addWidget(self._date_label)
        toolbar_layout.addWidget(self._exit_button)
        toolbar_layout.addWidget(self._open_folder_button)
        toolbar_layout.addWidget(self._copy_button)
        toolbar_layout.addWidget(self._rotate_ccw_button)
        toolbar_layout.addWidget(self._rotate_cw_button)
        toolbar_layout.addWidget(self._previous_button)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(metadata, 0, Qt.AlignCenter)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(self._next_button)
        self._toolbar.hide()

        # Photo mode
        self._photo_label = QLabel()
        self._photo_label.setAlignment(Qt.AlignCenter)
        self._photo_label.setStyleSheet("background:#000;")
        self._photo_label.installEventFilter(self)
        self._stack.installEventFilter(self)
        self._photo_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._stack.addWidget(self._photo_label)

        # Video mode
        self._video_page = QWidget()
        vlay = QVBoxLayout(self._video_page)
        vlay.setContentsMargins(0, 0, 0, 0)
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background:#000;")
        self.video_widget.installEventFilter(self)
        self._video_page.installEventFilter(self)
        vlay.addWidget(self.video_widget, 1)
        self._stack.addWidget(self._video_page)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)

        self.controls = _ControlBar(self.player, self)
        self.controls.hide()
        self._toolbar.installEventFilter(self)
        self.controls.installEventFilter(self)
        for child in self.controls.findChildren(QWidget):
            child.installEventFilter(self)

        self._current_path = None
        self._is_video = False
        self._photo_pixmap = None
        self._full_blob = None

    def set_items(self, paths):
        self._items = list(paths)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        toolbar_height = 52
        self._toolbar.setGeometry(0, 0, self.width(), toolbar_height)
        self._stack.setGeometry(0, toolbar_height, self.width(), max(0, self.height() - toolbar_height))
        self._place_controls()
        if self._photo_pixmap:
            self._photo_label.setPixmap(
                self._photo_pixmap.scaled(
                    self._stack.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

    def _place_controls(self):
        w = self.width()
        self.controls.setFixedWidth(min(560, w - 24))
        self.controls.move((w - self.controls.width()) // 2, max(0, self.height() - 122))
        self.controls.raise_()

    def _update_toolbar(self, path):
        self._index = self._items.index(path) if path in self._items else -1
        self._title_label.setText(os.path.basename(path))
        self._date_label.setText(self._date_for(path))
        self._previous_button.setVisible(self._index > 0)
        self._next_button.setVisible(0 <= self._index < len(self._items) - 1)
        self._copy_button.setVisible(not self._is_video)
        self._toolbar.show()
        self._toolbar.raise_()

    def _date_for(self, path):
        try:
            from PIL import Image
            with Image.open(path) as image:
                value = image.getexif().get(36867) or image.getexif().get(306)
            if value:
                return datetime.datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        try:
            return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return ""

    def _show_at(self, index):
        if not 0 <= index < len(self._items):
            return
        path = self._items[index]
        if os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS:
            self.show_video(path)
        else:
            self.show_photo(path)

    def _open_containing_folder(self):
        if not self._current_path or not os.path.exists(self._current_path):
            return
        try:
            subprocess.Popen(["open", "-R", self._current_path])
        except OSError:
            pass

    def _copy_image(self):
        if self._is_video:
            if self._current_path and os.path.exists(self._current_path):
                QGuiApplication.clipboard().setText(self._current_path)
            return
        if self._photo_pixmap is not None and not self._photo_pixmap.isNull():
            QGuiApplication.clipboard().setPixmap(self._photo_pixmap)

    def _rotate_current(self, clockwise):
        if self.mode == "detail" and self._current_path:
            self.rotate_requested.emit(self._current_path, clockwise)

    def _show_previous(self):
        self._show_at(self._index - 1)

    def _show_next(self):
        self._show_at(self._index + 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_photo(self, path):
        self.mode = "detail"
        self._current_path = path
        self._is_video = False
        self.player.stop()
        self._load_photo(path)
        self._stack.setCurrentIndex(0)
        self.controls.hide()
        self._update_toolbar(path)
        self._cover_parent()
        self.show()
        self.raise_()
        self.setFocus()

    def show_video(self, path):
        self.mode = "detail"
        self._current_path = path
        self._is_video = True
        self._stack.setCurrentIndex(1)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self._update_toolbar(path)
        self.controls.show()
        self.controls.raise_()
        self._cover_parent()
        self.show()
        self.raise_()
        self.setFocus()

    def _cover_parent(self):
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())

    def reload_current(self):
        """Re-render the current item after metadata changed (e.g. rotation).
        Re-reads the source file so new orientation/rotation is applied."""
        if self.mode != "detail" or not self._current_path:
            return
        path = self._current_path
        if self._is_video:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()
            self._update_toolbar(path)
        else:
            self.show_photo(path)

    def _exit_detail(self):
        path = self._current_path or ""
        self.mode = "thumbnail"
        self.close_view()
        self.closed.emit(path)

    def close_view(self):
        self.player.stop()
        self._toolbar.hide()
        self.hide()

    # ------------------------------------------------------------------
    def _load_photo(self, path):
        self._photo_label.clear()
        self._photo_pixmap = None
        st = None
        try:
            st = os.stat(path)
        except OSError:
            return
        # Try disk cache for the 1920px version
        row = self.parent_store.get_full(path, st.st_mtime, st.st_size) if hasattr(self, "parent_store") else None
        if row is not None:
            blob, w, h = row
            pm = QPixmap()
            if pm.loadFromData(blob, "WEBP"):
                self._photo_pixmap = pm
        if self._photo_pixmap is None:
            # Generate full-size thumbnail on demand (blocking, once per photo)
            res = generate_image_thumbnail(path, THUMB_SIZE_FULL)
            if res is not None:
                blob, w, h = res
                pm = QPixmap()
                if pm.loadFromData(blob, "WEBP"):
                    self._photo_pixmap = pm
                    if hasattr(self, "parent_store"):
                        self.parent_store.store_full(path, st.st_mtime, st.st_size, blob, w, h)
        if self._photo_pixmap:
            self._photo_label.setPixmap(
                self._photo_pixmap.scaled(
                    self._stack.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._exit_detail()
            return True
        if event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Escape, Qt.Key_Space):
                self._exit_detail()
                return True
            if event.text() in ("X", "x"):
                self.delete_requested.emit(self._current_path or "", event.text() == "x")
                return True
            if event.text() == "[":
                self._rotate_current(False)
                return True
            if event.text() == "]":
                self._rotate_current(True)
                return True
            if event.key() == Qt.Key_Left:
                self._show_previous()
                return True
            if event.key() == Qt.Key_Right:
                self._show_next()
                return True
            if event.key() in (Qt.Key_Up, Qt.Key_Down):
                return True
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._exit_detail()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Space):
            self._exit_detail()
        elif event.text() in ("X", "x"):
            self.delete_requested.emit(self._current_path or "", event.text() == "x")
        elif event.text() == "[":
            self._rotate_current(False)
        elif event.text() == "]":
            self._rotate_current(True)
        elif event.key() == Qt.Key_Left:
            self._show_previous()
        elif event.key() == Qt.Key_Right:
            self._show_next()
        elif event.key() in (Qt.Key_Up, Qt.Key_Down):
            event.accept()
            return
        event.accept()
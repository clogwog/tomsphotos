"""Bottom status bar: thumbnail progress + scan progress + selection info."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget


class StatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(12)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color:#888; font-size:12px;")
        lay.addWidget(self.info_label)

        lay.addStretch(1)

        self.thumb_progress = QProgressBar()
        self.thumb_progress.setRange(0, 100)
        self.thumb_progress.setFixedWidth(220)
        self.thumb_progress.setTextVisible(False)
        self.thumb_progress.hide()
        lay.addWidget(self.thumb_progress)

        self.thumb_label = QLabel("")
        self.thumb_label.setStyleSheet("color:#888; font-size:12px;")
        lay.addWidget(self.thumb_label)

        self.scan_label = QLabel("")
        self.scan_label.setStyleSheet("color:#888; font-size:12px;")
        lay.addWidget(self.scan_label)

    def set_info(self, text):
        self.info_label.setText(text)

    def show_thumb_progress(self, done, total):
        if total <= 0:
            self.thumb_progress.hide()
            self.thumb_label.setText("")
            return
        self.thumb_progress.show()
        self.thumb_progress.setMaximum(total)
        self.thumb_progress.setValue(done)
        self.thumb_label.setText(f"Thumbnailing {done:,} / {total:,}")

    def hide_thumb_progress(self):
        self.thumb_progress.hide()
        self.thumb_label.setText("")

    def show_scan(self, text):
        self.scan_label.setText(text)

    def hide_scan(self):
        self.scan_label.setText("")
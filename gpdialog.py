"""Google Photos sync dialog.

Google's post-2025 Library API cannot list a user's full library, so the app
cannot know up front which local files are already there. Instead this dialog
runs a *sync*: it skips files it has already synced (app-owned album by
filename + local upload log), uploads the rest at full resolution, and lets
Google deduplicate identical files that already exist in the account. The
dialog shows the file currently being uploaded as a live thumbnail.
"""

import os

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

import config
from gphotos import BATCH_LIMIT, GPhotosError, GooglePhotosClient, compute_missing
from synclog import UploadLog

THUMB_W, THUMB_H = 360, 270

QSS = """
  QDialog { background: #111; }
  QLabel#title { font-size: 17px; font-weight: 600; color: #fff; }
  QLabel#info { color: #9a9a9a; font-size: 12px; }
  QLabel#thumb { background: #000; border: 1px solid #2a2a2a; border-radius: 8px; }
  QLabel#current { color: #e0e0e0; font-size: 13px; }
  QLabel#status { color: #888; font-size: 12px; }
  QProgressBar#upload_progress { min-height: 24px; border: 1px solid #555; border-radius: 6px;
                                  background: #202124; color: #fff; font-size: 13px; font-weight: 600;
                                  text-align: center; }
  QProgressBar#upload_progress::chunk { background: #2f9e44; border-radius: 5px; }
  QPushButton#primary { background: #1a73e8; color: #fff; border: none; border-radius: 6px;
                        padding: 8px 18px; font-weight: 600; font-size: 13px; }
  QPushButton#primary:hover { background: #2b7de9; }
  QPushButton#primary:disabled { background: #333; color: #777; }
  QPushButton#plain { background: #2c2c2e; color: #e0e0e0; border: 1px solid #444; border-radius: 6px;
                      padding: 7px 16px; font-size: 13px; }
  QPushButton#plain:hover { background: #3a3a3c; }
  QPushButton#plain:disabled { color: #666; }
"""


class ThumbLoaderWorker(QThread):
    """Load/generate a thumbnail for one path (off the GUI thread)."""

    thumb_ready = Signal(str, object)  # path, QImage (or None)

    def __init__(self, path, meta, store, parent=None):
        super().__init__(parent)
        self.path = path
        self.meta = dict(meta or {})
        self.store = store

    def run(self):
        from thumbgen import generate_thumbnail

        blob = None
        if self.store is not None:
            entry = self.meta.get(self.path)
            if entry:
                mtime, size = entry
                row = self.store.get_thumbnail_valid(self.path, mtime, size)
                if row:
                    blob = row[0]
        if blob is None:
            result = generate_thumbnail(self.path)
            blob = result[0] if result else None
        image = QImage.fromData(blob, "WEBP") if blob else None
        try:
            self.thumb_ready.emit(self.path, image)
        except RuntimeError:
            pass


class SyncWorker(QThread):
    """One-way sync: skip known-synced files, upload the rest.

    Google deduplicates identical bytes, so files already in the account
    (even those uploaded by Google's own apps) are returned as the same
    media item instead of being copied again.
    """

    status = Signal(str)
    needs_setup = Signal()
    queued = Signal(int, int)              # to_upload, skipped
    file_started = Signal(str, int, int)   # path, index (1-based), total to upload
    file_progress = Signal(str, int, int)  # path, sent, size
    item_done = Signal(str, bool, str)
    finished_run = Signal(int, int, int)   # uploaded, failed, skipped
    failed = Signal(str)

    def __init__(self, paths, log=None, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.log = log
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            if not config.GP_CLIENT_ID or not config.GP_CLIENT_SECRET:
                self.needs_setup.emit()
                return
            client = GooglePhotosClient(config.GP_CLIENT_ID, config.GP_CLIENT_SECRET, config.GP_TOKEN_PATH)
            client.ensure_authorized(status_cb=self._status)
            self._status("Reading Google Photos album…")
            album_id = client.get_or_create_album(config.GP_ALBUM_TITLE)
            remote = client.list_album_filenames(album_id)
            if self.log is not None:
                remote = remote | self.log.known_filenames()
            to_upload, skipped = compute_missing(self.paths, remote, self.log)
            self.queued.emit(len(to_upload), skipped)
            if not to_upload:
                self.finished_run.emit(0, 0, skipped)
                return
        except GPhotosError as error:
            self._emit_failed(str(error))
            return

        uploaded = failed = 0
        total = len(to_upload)
        batch = []
        self._status(f"Uploading {total:,} file(s)…")
        for index, path in enumerate(to_upload, 1):
            if self._stop:
                break
            try:
                self.file_started.emit(path, index, total)
            except RuntimeError:
                pass
            name = os.path.basename(path)
            try:
                token = client.upload_file(
                    path,
                    progress_cb=lambda sent, size, p=path: self._progress(p, sent, size),
                )
            except GPhotosError as error:
                failed += 1
                self._item(path, False, str(error))
                continue
            batch.append((path, name, token))
            if len(batch) >= BATCH_LIMIT:
                add_ok, add_failed = self._flush(client, batch, album_id)
                uploaded += add_ok
                failed += add_failed
                batch = []
        if batch and not self._stop:
            add_ok, add_failed = self._flush(client, batch, album_id)
            uploaded += add_ok
            failed += add_failed
        try:
            self.finished_run.emit(uploaded, failed, skipped)
        except RuntimeError:
            pass

    def _flush(self, client, batch, album_id):
        uploaded = failed = 0
        results = client.create_media_items([(n, t) for _p, n, t in batch], album_id)
        for (path, name, _token), (good, message, media_id) in zip(batch, results):
            if good:
                uploaded += 1
                self._record(path, name, media_id)
            else:
                failed += 1
            self._item(path, good, message)
        return uploaded, failed

    def _record(self, path, name, media_id):
        if self.log is None:
            return
        try:
            st = os.stat(path)
            self.log.record(path, st.st_mtime, st.st_size, name, media_id)
        except OSError:
            pass

    def _status(self, text):
        try:
            self.status.emit(text)
        except RuntimeError:
            pass

    def _progress(self, path, sent, size):
        try:
            self.file_progress.emit(path, sent, size)
        except RuntimeError:
            pass

    def _item(self, path, ok, message):
        try:
            self.item_done.emit(path, ok, message)
        except RuntimeError:
            pass

    def _emit_failed(self, text):
        try:
            self.failed.emit(text)
        except RuntimeError:
            pass


class GoogleSyncDialog(QDialog):
    setup_requested = Signal()

    def __init__(self, thumb_store, log=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Google Photos sync")
        self.setWindowFlags(Qt.Window)
        self.setWindowModality(Qt.NonModal)
        self.setModal(False)
        self.setStyleSheet(QSS)
        self.setMinimumWidth(520)
        self._store = thumb_store
        self._log = log if log is not None else UploadLog()
        self._paths = []
        self._meta = {}
        self._worker = None
        self._thumb_worker = None
        self._thumb_workers = []
        self._current_path = None
        self._to_upload = 0
        self._skipped = 0
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(10)

        self.title_label = QLabel("Google Photos sync")
        self.title_label.setObjectName("title")
        self.info_label = QLabel("")
        self.info_label.setObjectName("info")
        self.info_label.setWordWrap(True)
        root.addWidget(self.title_label)
        root.addWidget(self.info_label)

        self.note_label = QLabel(
            "Any app can only see its own uploads in Google Photos, so this sync re-uploads "
            "files it hasn't synced before and relies on Google's content deduplication to "
            "skip photos it already has. Re-encoded or edited copies may still be duplicated."
        )
        self.note_label.setObjectName("info")
        self.note_label.setWordWrap(True)
        root.addWidget(self.note_label)

        self.thumb_label = QLabel("")
        self.thumb_label.setObjectName("thumb")
        self.thumb_label.setFixedSize(THUMB_W, THUMB_H)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.thumb_label, 0, Qt.AlignHCenter)

        self.current_label = QLabel("")
        self.current_label.setObjectName("current")
        self.current_label.setAlignment(Qt.AlignCenter)
        self.current_label.setWordWrap(True)
        root.addWidget(self.current_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("upload_progress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        root.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.setup_btn = QPushButton("Set up Google Photos…")
        self.setup_btn.setObjectName("primary")
        self.setup_btn.clicked.connect(self.setup_requested.emit)
        self.setup_btn.hide()
        self.sync_btn = QPushButton("Sync to Google Photos")
        self.sync_btn.setObjectName("primary")
        self.sync_btn.setEnabled(False)
        self.sync_btn.clicked.connect(self.start_sync)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("plain")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_sync)
        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("plain")
        self.close_btn.clicked.connect(self.close)
        bottom.addWidget(self.setup_btn)
        bottom.addWidget(self.sync_btn)
        bottom.addWidget(self.stop_btn)
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)

    # -- public ----------------------------------------------------------
    def start(self, paths, meta):
        """Load a new folder into the dialog (does not upload yet)."""
        self._cancel_workers()
        self._paths = list(paths)
        self._meta = dict(meta or {})
        self._current_path = None
        self._to_upload = 0
        self._skipped = 0
        self.thumb_label.clear()
        self.thumb_label.setText("—")
        self.current_label.setText("")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label.setText("")
        self.setup_btn.hide()
        self.stop_btn.setEnabled(False)
        self.sync_btn.setEnabled(bool(self._paths))
        self.sync_btn.setText("Sync to Google Photos")
        if self._paths:
            self.info_label.setText(
                f"{len(self._paths):,} files in the current view. "
                "Files already synced by this app are skipped automatically."
            )
        else:
            self.info_label.setText("No files in the current view")
            self._show_message("There are no files to sync.")

    def start_sync(self):
        if not self._paths or (self._worker is not None and self._worker.isRunning()):
            return
        self._cancel_workers()
        self.setup_btn.hide()
        self.sync_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label.setText("Checking Google Photos…")
        self._worker = SyncWorker(self._paths, log=self._log)
        self._worker.status.connect(self.status_label.setText)
        self._worker.needs_setup.connect(self._on_needs_setup)
        self._worker.queued.connect(self._on_queued)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.item_done.connect(self._on_item_done)
        self._worker.finished_run.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    # -- slots -----------------------------------------------------------
    def _on_needs_setup(self):
        self.sync_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.setup_btn.show()
        self._show_message(
            "Google Photos is not configured yet. Click \u201cSet up Google Photos…\u201d "
            "for step-by-step instructions."
        )

    def _on_queued(self, to_upload, skipped):
        self._to_upload = to_upload
        self._skipped = skipped
        if to_upload:
            self.info_label.setText(
                f"{to_upload:,} file(s) to sync — {skipped:,} already synced by this app."
            )
        # progress bar is re-ranged per file (bytes) once uploads start

    def _on_file_started(self, path, index, total):
        self._current_path = path
        self.current_label.setText(f"{index} of {total}: {os.path.basename(path)}")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.thumb_label.clear()
        self.thumb_label.setText("…")
        self.status_label.setText(f"Uploading {index} of {total}…")
        self._load_thumb(path)

    def _on_file_progress(self, path, sent, size):
        if path != self._current_path:
            return
        self.progress.setRange(0, max(1, size))
        self.progress.setValue(sent)

    def _on_item_done(self, path, ok, message):
        if not ok:
            self.status_label.setText(f"Failed: {os.path.basename(path)} — {message}")

    def _on_finished(self, uploaded, failed, skipped):
        self.stop_btn.setEnabled(False)
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("Sync again")
        self.progress.setValue(self.progress.maximum())
        if self._to_upload == 0:
            self._show_message(
                f"Everything in this view is already synced ({skipped:,} files)."
            )
            return
        parts = [f"Synced {uploaded:,} file(s) (new or already on Google Photos)"]
        if failed:
            parts.append(f"{failed:,} failed")
        if skipped:
            parts.append(f"{skipped:,} skipped (already synced by this app)")
        self.status_label.setText(". ".join(parts) + ".")

    def _on_failed(self, message):
        self.stop_btn.setEnabled(False)
        self.sync_btn.setEnabled(True)
        self.setup_btn.hide()
        self._show_message(f"Google Photos sync failed:\n{message}")

    def _stop_sync(self):
        worker = self._worker
        if self._is_running(worker):
            worker.stop()
            self.status_label.setText("Stopping after the current file…")
            self.stop_btn.setEnabled(False)

    # -- helpers -----------------------------------------------------------
    def _load_thumb(self, path):
        worker = ThumbLoaderWorker(path, self._meta, self._store)
        worker.thumb_ready.connect(self._on_thumb_ready)
        worker.finished.connect(lambda w=worker: self._drop_thumb_worker(w))
        self._thumb_workers.append(worker)
        self._thumb_worker = worker
        worker.start()

    def _on_thumb_ready(self, path, image):
        if path != self._current_path:
            return
        if image is None or image.isNull():
            self.thumb_label.setText("no preview")
            return
        pm = QPixmap.fromImage(image)
        self.thumb_label.setPixmap(pm.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _drop_thumb_worker(self, worker):
        if worker in self._thumb_workers:
            self._thumb_workers.remove(worker)
        if self._thumb_worker is worker:
            self._thumb_worker = None
        worker.deleteLater()

    @staticmethod
    def _is_running(worker):
        if worker is None or not isValid(worker):
            return False
        try:
            return worker.isRunning()
        except RuntimeError:
            return False

    def _show_message(self, text):
        self.info_label.setText(text)

    def _cancel_workers(self):
        workers = []
        for worker in (self._worker, self._thumb_worker, *self._thumb_workers):
            if worker is not None and worker not in workers:
                workers.append(worker)
        for worker in workers:
            if not self._is_running(worker):
                continue
            if hasattr(worker, "stop"):
                worker.stop()
            try:
                worker.wait(1500)
            except RuntimeError:
                pass
        self._worker = None
        self._thumb_worker = None
        self._thumb_workers.clear()

    def closeEvent(self, event):
        running = self._is_running(self._worker)
        if running:
            answer = QMessageBox.question(
                self, "Stop sync?",
                "A sync is in progress. Stop it and close?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self._cancel_workers()
        super().closeEvent(event)


class GPhotosSetupDialog(QDialog):
    """First-run setup: OAuth client credentials for the user's own project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Google Photos Setup")
        self.setStyleSheet(QSS)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)

        title = QLabel("Google Photos Setup")
        title.setObjectName("title")
        lay.addWidget(title)

        steps = QLabel(
            "<p>This app uses the Google Photos <i>Library API</i>, which only sees content "
            "it uploaded itself. It keeps an album called "
            f"\u201c{config.GP_ALBUM_TITLE}\u201d in your Google Photos and matches files "
            "against it by filename.</p>"
            "<p>One-time setup with your own Google account:</p>"
            "<ol>"
            "<li>Open <a href='https://console.cloud.google.com/'>console.cloud.google.com</a> and create a project.</li>"
            "<li>APIs &amp; Services \u2192 Library \u2192 enable <b>Photos Library API</b>.</li>"
            "<li>OAuth consent screen: type <b>External</b>, add your account as a <b>test user</b>.</li>"
            "<li>Credentials \u2192 <b>Create Credentials \u2192 OAuth client ID</b> \u2192 type <b>Desktop app</b>.</li>"
            "<li>Paste the client ID and secret here:</li>"
            "</ol>"
            "<p><a href='https://console.cloud.google.com/apis/credentials'>Open Google Cloud Credentials \u2192</a></p>"
        )
        steps.setWordWrap(True)
        steps.setOpenExternalLinks(True)
        steps.setTextFormat(Qt.RichText)
        lay.addWidget(steps)

        form = QHBoxLayout()
        id_edit = QLineEdit(config.GP_CLIENT_ID)
        id_edit.setPlaceholderText("Client ID (…apps.googleusercontent.com)")
        secret_edit = QLineEdit(config.GP_CLIENT_SECRET)
        secret_edit.setPlaceholderText("Client secret")
        form.addWidget(id_edit, 3)
        form.addWidget(secret_edit, 2)
        lay.addLayout(form)
        self._id_edit = id_edit
        self._secret_edit = secret_edit

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("plain")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)

    def _save(self):
        if not self._id_edit.text().strip() or not self._secret_edit.text().strip():
            QMessageBox.warning(self, "Google Photos Setup", "Both client ID and secret are required.")
            return
        config.set_google_credentials(self._id_edit.text(), self._secret_edit.text())
        self.accept()
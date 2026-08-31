"""Thumbnail generation: pure functions (testable) + Qt worker pool.

Pure core has no Qt dependency so performance/unit tests can run headless.
"""

import heapq
import io
import os
import sys
import threading
import time

from PIL import Image, ImageOps

from config import MEDIA_EXTENSIONS, THUMB_SIZE_GRID, VIDEO_EXTENSIONS

# ---------------------------------------------------------------------------
# Pure generation core (no Qt)
# ---------------------------------------------------------------------------

_Image_MAX_PIXELS = Image.MAX_IMAGE_PIXELS
Image.MAX_IMAGE_PIXELS = None  # allow huge images; we downscale anyway


def _resize_cover(img, size):
    """Resize so longest side <= size, preserving aspect. Returns RGB image."""
    try:
        # Decode only the resolution we need (big speedup for large JPEGs).
        img.draft("RGB", (size, size))
    except Exception:
        pass
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    img.thumbnail((size, size), Image.LANCZOS)
    return img


def encode_webp(img, quality=85):
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=4)
    return buf.getvalue()


def generate_image_thumbnail(path, size=THUMB_SIZE_GRID):
    """Return (webp_bytes, width, height) or None on failure.

    HEIC/HEIF fall back to macOS `sips` (Pillow cannot decode HEVC-in-HEIF).
    """
    try:
        with Image.open(path) as img:
            img = _resize_cover(img, size)
            w, h = img.size
            blob = encode_webp(img)
            return blob, w, h
    except Exception:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".heic", ".heif"):
            return _sips_thumbnail(path, size)
        return None


def _sips_thumbnail(path, size=THUMB_SIZE_GRID):
    """Decode HEIC/HEIF via macOS sips, then re-encode as WebP."""
    import subprocess
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        r = subprocess.run(
            ["sips", "-Z", str(size), "-s", "format", "jpeg", path, "--out", tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
        if r.returncode != 0:
            return None
        with Image.open(tmp) as img:
            img = _resize_cover(img, size)
            w, h = img.size
            blob = encode_webp(img)
            return blob, w, h
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _find_binary(name):
    import shutil
    for cand in (name, os.path.join(os.path.dirname(sys.executable), name)):
        p = shutil.which(cand)
        if p:
            return p
    return None


def generate_video_thumbnail(path, size=THUMB_SIZE_GRID, seek_fraction=0.25):
    """Extract a frame from a video via ffmpeg. Return (webp_bytes, w, h) or None."""
    ffmpeg = _find_binary("ffmpeg")
    if not ffmpeg:
        return None
    try:
        import subprocess
        # Probe duration to pick a sane seek point
        dur = _probe_duration(path)
        if dur:
            seek = max(0.0, min(dur * seek_fraction, dur - 0.1))
        else:
            seek = 1.0
        cmd = [
            ffmpeg, "-v", "error", "-ss", f"{seek:.3f}", "-i", path,
            "-frames:v", "1", "-vf", f"scale='min({size},iw)':-2",
            "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=20)
        if proc.returncode != 0 or not proc.stdout:
            return None
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(proc.stdout))
        img = _resize_cover(img, size)
        w, h = img.size
        blob = encode_webp(img)
        return blob, w, h
    except Exception:
        return None


def _probe_duration(path):
    ffprobe = _find_binary("ffprobe")
    if not ffprobe:
        return None
    try:
        import subprocess
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        )
        return float(proc.stdout.strip())
    except Exception:
        return None


def generate_thumbnail(path, size=THUMB_SIZE_GRID):
    """Dispatch by extension. Returns (blob, w, h) or None."""
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return generate_video_thumbnail(path, size)
    if ext in MEDIA_EXTENSIONS:
        return generate_image_thumbnail(path, size)
    return None


def get_image_dimensions(path):
    """Return display-oriented (width, height) of an image."""
    try:
        with Image.open(path) as img:
            return ImageOps.exif_transpose(img).size
    except Exception:
        return None


def get_video_dimensions(path):
    ffprobe = _find_binary("ffprobe")
    if not ffprobe:
        return None
    try:
        import subprocess
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_tags=rotate",
             "-of", "default=noprint_wrappers=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        )
        values = {}
        for line in proc.stdout.decode().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip()
        w, h = int(values["width"]), int(values["height"])
        rotation = abs(int(values.get("TAG:rotate", values.get("rotate", "0")))) % 180
        return (h, w) if rotation == 90 else (w, h)
    except Exception:
        return None


def get_dimensions(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return get_video_dimensions(path)
    return get_image_dimensions(path)


# ---------------------------------------------------------------------------
# Qt worker pool
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

    _HAS_QT = True
except ImportError:
    _HAS_QT = False


KIND_THUMB = 0
KIND_DIMS = 1


class _ThumbTask:
    __slots__ = ("priority", "seq", "path", "size", "kind")

    def __init__(self, priority, seq, path, size, kind=KIND_THUMB):
        self.priority = priority
        self.seq = seq
        self.path = path
        self.size = size
        self.kind = kind

    def __lt__(self, other):
        # heapq is a min-heap; invert priority so higher priority pops first
        return (-self.priority, self.seq) < (-other.priority, other.seq)


class _Worker(QRunnable):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def run(self):
        self.manager._worker_loop()


class ThumbnailManager(QObject):
    """Priority-ordered thumbnail generation with a bounded worker pool."""

    thumbnail_ready = Signal(str, object)  # path, QImage (decoded in worker thread)
    dims_ready = Signal(str, int, int)     # path, width, height
    progress = Signal(int, int)            # done, total
    all_done = Signal()

    MAX_PENDING = 50000  # safety cap on queued requests

    def __init__(self, store, pool_size=6, parent=None):
        super().__init__(parent)
        self.store = store
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(pool_size)
        self._heap = []
        self._pending = {}   # path -> (priority, seq)
        self._dims_pending = {}  # path -> (priority, seq)
        self._seq = 0
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._workers = []
        self._done_count = 0
        self._total = 0
        self._running = True
        self._generated = 0
        self._failed = 0
        self._started = False

    # -- public API ------------------------------------------------------
    def start(self):
        if self._started:
            return
        self._started = True
        with self._lock:
            n = self.pool.maxThreadCount()
            for _ in range(n):
                w = _Worker(self)
                self._workers.append(w)
                self.pool.start(w)

    def request(self, path, priority=0):
        """Enqueue a thumbnail request. priority: higher = sooner."""
        with self._lock:
            if path in self._pending:
                old = self._pending[path]
                if priority > old[0]:
                    # Push a fresh heap entry. Updating dict alone leaves
                    # stale priority in heap, so visible items stay buried.
                    self._seq += 1
                    self._pending[path] = (priority, self._seq)
                    heapq.heappush(self._heap, _ThumbTask(priority, self._seq, path, THUMB_SIZE_GRID, KIND_THUMB))
                return
            self._seq += 1
            self._pending[path] = (priority, self._seq)
            heapq.heappush(self._heap, _ThumbTask(priority, self._seq, path, THUMB_SIZE_GRID, KIND_THUMB))
            self._total += 1
            self._cv.notify()

    def request_background(self, paths):
        """Queue directory thumbnails at low priority for persistent caching."""
        for path in paths:
            self.request(path, priority=-100)

    def request_dims(self, path, priority=0):
        """Enqueue a dimension probe (header-only read)."""
        with self._lock:
            if path in self._dims_pending:
                old = self._dims_pending[path]
                if priority > old[0]:
                    self._seq += 1
                    self._dims_pending[path] = (priority, self._seq)
                    heapq.heappush(self._heap, _ThumbTask(priority, self._seq, path, 0, KIND_DIMS))
                return
            self._seq += 1
            self._dims_pending[path] = (priority, self._seq)
            heapq.heappush(self._heap, _ThumbTask(priority, self._seq, path, 0, KIND_DIMS))
            self._cv.notify()

    def set_total(self, n):
        with self._lock:
            self._total = n
            self._cv.notify()

    def cancel_all(self):
        """Drop all queued work. Completed items stay cached."""
        with self._lock:
            self._heap.clear()
            self._pending.clear()
            self._dims_pending.clear()
            self._total = 0
            self._done_count = 0
            self._generated = 0
            self._failed = 0
            self._cv.notify()

    def shutdown(self):
        with self._lock:
            self._running = False
            self._cv.notify_all()
        self.pool.waitForDone(5000)
        self.store.flush()

    def stats(self):
        with self._lock:
            return {
                "queued": len(self._heap),
                "pending": len(self._pending),
                "generated": self._generated,
                "failed": self._failed,
                "total": self._total,
                "done": self._done_count,
            }

    # -- internal --------------------------------------------------------
    def _worker_loop(self):
        while True:
            with self._cv:
                while self._running and not self._heap:
                    self._cv.wait(0.5)
                if not self._running:
                    return
                task = heapq.heappop(self._heap)
                if task.kind == KIND_DIMS:
                    cur = self._dims_pending.get(task.path)
                    if cur is None or cur[1] != task.seq:
                        continue
                    del self._dims_pending[task.path]
                    dims = get_dimensions(task.path)
                    if dims is not None:
                        try:
                            self.dims_ready.emit(task.path, dims[0], dims[1])
                        except RuntimeError:
                            pass
                    continue
                # dedupe stale entries
                cur = self._pending.get(task.path)
                if cur is None or cur[1] != task.seq:
                    continue
                del self._pending[task.path]
            try:
                # Load from store if already cached; otherwise generate.
                res = self._generate_or_load(task.path, task.size)
            except Exception:
                res = None
            if res is not None:
                blob, w, h, from_store = res
                if not from_store:
                    try:
                        st = os.stat(task.path)
                        self.store.store_thumbnail(task.path, st.st_mtime, st.st_size, blob, w, h)
                    except OSError:
                        pass
                self._emit_ready(task.path, blob)
            with self._lock:
                self._done_count += 1
                if res is not None:
                    self._generated += 1
                else:
                    self._failed += 1
                done, total = self._done_count, self._total
                all_done = total > 0 and done >= total and not self._pending
            try:
                self.progress.emit(done, total)
                if all_done:
                    self.all_done.emit()
            except RuntimeError:
                pass

    def _generate_or_load(self, path, size):
        """Return (blob, w, h, from_store) or None. Checks disk cache first."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        row = self.store.get_thumbnail_valid(path, st.st_mtime, st.st_size)
        if row is not None:
            return row[0], row[1], row[2], True
        r = generate_thumbnail(path, size)
        if r is None:
            return None
        return r[0], r[1], r[2], False

    def _generate(self, path, size):
        return generate_thumbnail(path, size)

    def _emit_ready(self, path, blob):
        # Decode in the worker thread; QImage is safe to hand across threads.
        from PySide6.QtGui import QImage
        img = QImage.fromData(blob, "WEBP")
        if img.isNull():
            return
        try:
            self.thumbnail_ready.emit(path, img)
        except RuntimeError:
            pass  # manager destroyed during shutdown
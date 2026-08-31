"""Metadata-only rotation for images and videos.

Writes the EXIF Orientation tag (JPEG/HEIC/WEBP/PNG/TIFF/GIF/AVIF) or the
video Rotation metadata (MOV/MP4) so the file displays rotated 90deg WITHOUT
re-encoding any pixel data. Requires `exiftool` on PATH.

EXIF Orientation values (0x0112):
    1 = normal, 2 = flip H, 3 = 180deg, 4 = flip V,
    5 = transpose, 6 = 90 CW, 7 = transverse, 8 = 90 CCW
Video Rotation is stored in degrees (0/90/180/270).

"Rotate CW" here means the on-screen image appears rotated 90deg clockwise.
"""

import os
import shutil
import subprocess

from config import VIDEO_EXTENSIONS

EXIFTOOL = shutil.which("exiftool")

# Orientation value -> orientation after rotating the DISPLAYED image 90deg CW/CCW.
CW_TABLE = {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1}
CCW_TABLE = {1: 8, 2: 5, 3: 6, 4: 7, 5: 4, 6: 1, 7: 2, 8: 3}


def exiftool_available():
    return EXIFTOOL is not None


def _run(args):
    proc = subprocess.run(
        [EXIFTOOL] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    if proc.returncode != 0 or (proc.stdout + proc.stderr).startswith(b"Error"):
        msg = (proc.stderr.decode(errors="replace") or proc.stdout.decode(errors="replace")).strip()
        raise RuntimeError(msg or "exiftool failed")
    return proc


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def get_orientation(path):
    """Current EXIF orientation int (1..8) or None when tag absent."""
    proc = _run(["-s3", "-n", "-Orientation", path])
    text = proc.stdout.decode().strip()
    return int(text) if text else None


def get_rotation(path):
    """Current video rotation degrees (0/90/180/270) or None when unset."""
    proc = _run(["-s3", "-n", "-Rotation", path])
    text = proc.stdout.decode().strip()
    return int(text) if text else None


def _write_orientation(path, value):
    _run(["-overwrite_original", f"-Orientation#={value}", path])


def _write_rotation(path, value):
    _run(["-overwrite_original", f"-Rotation={value}", path])


def rotate_image(path, clockwise=True):
    """Rotate the DISPLAYED image 90deg via the EXIF Orientation tag. Does not
    re-encode pixels. Supports JPEG/HEIC/WEBP/PNG/TIFF/GIF. Returns new value."""
    current = get_orientation(path) or 1
    table = CW_TABLE if clockwise else CCW_TABLE
    new = table[current]
    _write_orientation(path, new)
    return new


def rotate_video(path, clockwise=True):
    """Rotate a MOV/MP4 90deg via the rotation metadata (no pixel re-encode)."""
    current = get_rotation(path) or 0
    new = (current + 90 if clockwise else current - 90) % 360
    if new not in (0, 90, 180, 270):
        raise RuntimeError(f"unsupported rotation value {current}")
    _write_rotation(path, new)
    return new


def rotate_media(path, clockwise=True):
    """Rotate a media file 90deg via metadata only.

    Returns (ok: bool, message: str).
    """
    if not exiftool_available():
        return False, "exiftool required (brew install exiftool)"
    try:
        label = "right" if clockwise else "left"
        if is_video(path):
            rotate_video(path, clockwise)
        else:
            rotate_image(path, clockwise)
        return True, f"Rotated 90\u00b0 {label}"
    except (RuntimeError, OSError, ValueError) as error:
        return False, str(error)
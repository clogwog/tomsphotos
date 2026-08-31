"""Tests for metadata-only rotation. Skipped when exiftool is not installed."""

import os
import tempfile

import pytest
from PIL import Image

from rotate_media import (
    exiftool_available,
    get_orientation,
    get_rotation,
    rotate_image,
    rotate_media,
    rotate_video,
)
from thumbgen import get_image_dimensions, get_video_dimensions

pytestmark = pytest.mark.skipif(
    not exiftool_available(),
    reason="exiftool not installed",
)


def _make_jpeg(path, size=(2000, 1000)):
    Image.new("RGB", size, (200, 40, 90)).save(path, "JPEG")


def test_rotate_image_cycles_orientation():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "a.jpg")
        _make_jpeg(p)
        assert get_orientation(p) is None
        assert get_image_dimensions(p) == (2000, 1000)

        rotate_image(p, clockwise=True)
        assert get_orientation(p) == 6
        assert get_image_dimensions(p) == (1000, 2000)  # display swaps

        rotate_image(p, clockwise=True)
        assert get_orientation(p) == 3
        assert get_image_dimensions(p) == (2000, 1000)

        rotate_image(p, clockwise=False)
        assert get_orientation(p) == 6
        assert get_image_dimensions(p) == (1000, 2000)


def test_rotate_media_preserves_pixels():
    """Metadata-only rotation must not touch pixel data."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "a.jpg")
        _make_jpeg(p)
        with Image.open(p) as im:
            before = im.tobytes()
        ok, message = rotate_media(p, clockwise=True)
        assert ok
        with Image.open(p) as im:
            after = im.tobytes()
        assert before == after
        assert get_orientation(p) == 6


def test_cycle_returns_to_start():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "a.jpg")
        _make_jpeg(p)
        for _ in range(4):
            rotate_image(p, clockwise=True)
        assert get_orientation(p) in (None, 1) or get_orientation(p) == 1
        assert get_image_dimensions(p) == (2000, 1000)


def test_rotate_image_required_binary_missing(monkeypatch, tmp_path):
    p = str(tmp_path / "a.jpg")
    _make_jpeg(p)
    monkeypatch.setattr("rotate_media.EXIFTOOL", None)
    ok, message = rotate_media(p, clockwise=True)
    assert not ok
    assert "exiftool" in message.lower()


def test_rotate_video_metadata(tmp_path):
    if not exiftool_available():
        pytest.skip("exiftool required")
    # Build a tiny MP4 the same way other tests rely on ffmpeg; skip if absent.
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    path = str(tmp_path / "v.mp4")
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:size=320x180:rate=5:duration=0.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert get_rotation(path) in (None, 0)
    assert get_video_dimensions(path) == (320, 180)

    rotate_video(path, clockwise=True)
    assert get_rotation(path) == 90
    assert get_video_dimensions(path) == (180, 320)

    rotate_video(path, clockwise=False)
    assert get_rotation(path) == 0
    assert get_video_dimensions(path) == (320, 180)
import io
import os
import tempfile

from PIL import Image

from thumbgen import (
    encode_webp,
    generate_image_thumbnail,
    generate_thumbnail,
    get_dimensions,
    get_image_dimensions,
)


def _make_image(path, size=(2000, 1200), fmt="JPEG"):
    img = Image.new("RGB", size, (200, 40, 90))
    img.save(path, fmt)


def test_encode_webp_returns_webp():
    img = Image.new("RGB", (100, 100), (10, 20, 30))
    blob = encode_webp(img)
    assert blob[:4] == b"RIFF"
    assert blob[8:12] == b"WEBP"


def test_generate_image_thumbnail_resizes():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "a.jpg")
        _make_image(p, (2000, 1200))
        blob, w, h = generate_image_thumbnail(p, 320)
        assert max(w, h) <= 320
        assert w / h == 2000 / 1200
        # decodable back
        im = Image.open(io.BytesIO(blob))
        assert im.format == "WEBP"
        assert im.size == (w, h)


def test_generate_image_thumbnail_orientation():
    """EXIF orientation must be applied (portrait stored as landscape)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "rot.jpg")
        img = Image.new("RGB", (2000, 1000), (9, 9, 9))
        exif = Image.Exif()
        exif[274] = 6  # rotate 90 CW
        img.save(p, "JPEG", exif=exif)
        blob, w, h = generate_image_thumbnail(p, 320)
        assert w < h  # should come out portrait


def test_generate_thumbnail_unknown_ext_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "a.txt")
        with open(p, "w") as f:
            f.write("x")
        assert generate_thumbnail(p) is None


def test_get_image_dimensions():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "a.jpg")
        _make_image(p, (1234, 567))
        assert get_image_dimensions(p) == (1234, 567)
        assert get_dimensions(p) == (1234, 567)


def test_get_dimensions_missing_file():
    assert get_dimensions("/nonexistent/x.jpg") is None


def test_corrupt_image_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "bad.jpg")
        with open(p, "wb") as f:
            f.write(b"not an image at all")
        assert generate_image_thumbnail(p) is None
import os
import tempfile
import time

from thumbstore import ThumbStore


def _make_store(tmp):
    return ThumbStore(os.path.join(tmp, "thumbs.db"))


def test_store_and_get_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(tmp)
        blob = b"\x00\x01\x02\x03"
        store.store_thumbnail("/a.jpg", 100.0, 500, blob, 320, 240, commit=True)
        row = store.get_thumbnail("/a.jpg")
        assert row == (blob, 320, 240)
        store.close()


def test_validity_checks_mtime_and_size():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(tmp)
        store.store_thumbnail("/a.jpg", 100.0, 500, b"x", 10, 10, commit=True)
        assert store.get_thumbnail_valid("/a.jpg", 100.0, 500) is not None
        assert store.get_thumbnail_valid("/a.jpg", 101.0, 500) is None
        assert store.get_thumbnail_valid("/a.jpg", 100.0, 501) is None
        assert store.get_thumbnail_valid("/b.jpg", 100.0, 500) is None
        store.close()


def test_upsert_overwrites():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(tmp)
        store.store_thumbnail("/a.jpg", 1.0, 1, b"old", 1, 1, commit=True)
        store.store_thumbnail("/a.jpg", 2.0, 2, b"new", 2, 2, commit=True)
        row = store.get_thumbnail("/a.jpg")
        assert row == (b"new", 2, 2)
        store.close()


def test_full_cache_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(tmp)
        store.store_full("/a.jpg", 100.0, 500, b"full", 1920, 1080)
        assert store.get_full("/a.jpg", 100.0, 500) == (b"full", 1920, 1080)
        assert store.get_full("/a.jpg", 999.0, 500) is None
        store.close()


def test_batch_commit_flush():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(tmp)
        for i in range(10):
            store.store_thumbnail(f"/f{i}.jpg", 1.0, 1, b"x", 1, 1, commit=False)
        # unflushed data still visible via same connection
        assert store.get_thumbnail("/f9.jpg") == (b"x", 1, 1)
        store.flush()
        store.close()
        # reopen: persisted
        store2 = _make_store(tmp)
        assert store2.get_thumbnail("/f9.jpg") == (b"x", 1, 1)
        store2.close()


def test_stats():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(tmp)
        store.store_thumbnail("/a.jpg", 1.0, 1, b"x", 1, 1, commit=True)
        store.store_full("/b.jpg", 1.0, 1, b"y", 1, 1)
        s = store.stats()
        assert s["thumbs"] == 1
        assert s["full"] == 1
        assert s["db_bytes"] > 0
        store.close()
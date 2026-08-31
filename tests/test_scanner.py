import os
import tempfile

from PIL import Image

import scanner


def make_media_tree(root, structure):
    """Create dirs/files under root. structure: {rel_dir: [filenames]}."""
    for rel, files in structure.items():
        d = os.path.join(root, rel)
        os.makedirs(d, exist_ok=True)
        for fname in files:
            path = os.path.join(d, fname)
            if fname.lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".gif")):
                img = Image.new("RGB", (640, 480), (120, 40, 200))
                img.save(path, "JPEG")
            else:
                with open(path, "wb") as f:
                    f.write(b"\x00" * 64)


def test_is_media_file():
    assert scanner.is_media_file("IMG_1.JPG")
    assert scanner.is_media_file("a.heic")
    assert scanner.is_media_file("b.MOV")
    assert not scanner.is_media_file(".DS_Store")
    assert not scanner.is_media_file(".tonfotos.ini")
    assert not scanner.is_media_file("notes.txt")
    assert not scanner.is_media_file("digikam4.db")


def test_scan_recursive_counts():
    with tempfile.TemporaryDirectory() as tmp:
        make_media_tree(tmp, {
            "2026/01/2026-01-01": ["a.JPG", "b.JPG", "c.MOV"],
            "2026/01/2026-01-02": ["d.JPG"],
            "2026/02/2026-02-01": ["e.JPG", "f.HEIC"],
            "2026": ["top.JPG", "skip.ini", ".DS_Store"],
        })
        res = scanner.scan_directory_tree(tmp)
        day1 = os.path.join(tmp, "2026", "01", "2026-01-01")
        mon1 = os.path.join(tmp, "2026", "01")
        mon2 = os.path.join(tmp, "2026", "02")
        year = os.path.join(tmp, "2026")
        assert res[day1][0] == 3
        assert res[mon1][0] == 4
        assert res[mon2][0] == 2
        assert res[year][0] == 4 + 2 + 1  # months + top.JPG
        assert res[tmp][0] == 4 + 2 + 1
        # dir counts (recursive)
        assert res[year][1] == 5  # 01, 01/01, 01/02, 02, 02/01
        assert res[tmp][1] == 6


def test_scan_ignores_ignored_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        make_media_tree(tmp, {
            "2026/.dtrash": ["x.JPG"],
            "2026/01": ["a.JPG"],
        })
        res = scanner.scan_directory_tree(tmp)
        year = os.path.join(tmp, "2026")
        assert res[year][0] == 1  # only a.JPG, not x.JPG


def test_scan_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        make_media_tree(tmp, {"a": ["1.JPG"], "b": ["2.JPG"], "c": ["3.JPG"]})
        calls = {"n": 0}
        def cancel():
            calls["n"] += 1
            return calls["n"] > 1
        res = scanner.scan_directory_tree(tmp, cancel_check=cancel)
        assert len(res) < 4


def test_walk_media_files_sorted():
    with tempfile.TemporaryDirectory() as tmp:
        make_media_tree(tmp, {
            "z": ["b.JPG"],
            "a": ["a.JPG", "c.MOV"],
        })
        files = scanner.walk_media_files(tmp)
        paths = [f[0] for f in files]
        assert paths == sorted(paths)
        assert len(files) == 3
        # each entry has size and mtime
        for p, size, mtime in files:
            assert size > 0
            assert mtime > 0


def test_indexdb_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        db = scanner.IndexDB(os.path.join(tmp, "index.db"))
        make_media_tree(tmp + "/photos", {
            "2026/01": ["a.JPG"],
            "2026/02": ["b.JPG", "c.JPG"],
        })
        root = os.path.join(tmp, "photos")
        res = scanner.scan_directory_tree(root)
        db.upsert_dirs(scanner.build_rows_for_tree(res, root))
        # children of root
        kids = db.get_children(root)
        assert len(kids) == 1
        assert kids[0][2] == "2026"
        assert kids[0][3] == 3  # recursive count
        # children of year
        year = os.path.join(root, "2026")
        mkids = db.get_children(year)
        assert len(mkids) == 2
        assert sum(k[3] for k in mkids) == 3
        # needs_rescan
        assert not db.needs_rescan(root, res[root][2])
        assert db.needs_rescan(root, res[root][2] + 5)
        assert db.count_total_media() == 3
        db.close()


def test_incremental_scan_skips_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        db = scanner.IndexDB(os.path.join(tmp, "index.db"))
        root = os.path.join(tmp, "photos")
        make_media_tree(root, {"2026/01": ["a.JPG"]})
        res = scanner.scan_directory_tree(root)
        db.upsert_dirs(scanner.build_rows_for_tree(res, root))
        # second scan: same mtimes, should not need rescan
        res2 = scanner.scan_directory_tree(root)
        assert not db.needs_rescan(root, res2[root][2])
        db.close()
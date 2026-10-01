"""Tests for the Google Photos sync (pure client, upload log, sync worker, dialog)."""

import os
import sys

import pytest
from PIL import Image

import config
import gphotos
from gphotos import (
    AuthError,
    GooglePhotosClient,
    TokenStore,
    build_auth_url,
    compute_missing,
    find_missing_files,
    mime_type_for,
)
from synclog import UploadLog


# ---------------------------------------------------------------------------
# Pure client
# ---------------------------------------------------------------------------

def test_find_missing_files(tmp_path):
    a = str(tmp_path / "a.JPG")
    b = str(tmp_path / "b.png")
    c = str(tmp_path / "c.mov")
    for p in (a, b, c):
        open(p, "wb").write(b"\x00")
    missing = find_missing_files([a, b, c], {"a.jpg", "c.mov"})
    assert missing == [b]
    assert find_missing_files([], {"x"}) == []
    assert find_missing_files([a], set()) == [a]


def test_build_auth_url():
    url = build_auth_url("my-client", "http://127.0.0.1:8080")
    assert "my-client" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8080" in url
    assert "photoslibrary.appendonly" in url
    assert "photoslibrary.readonly.appcreateddata" in url
    assert "access_type=offline" in url
    assert "response_type=code" in url


def test_mime_type_for():
    assert mime_type_for("x.jpg") == "image/jpeg"
    assert mime_type_for("x.MOV") == "video/quicktime"
    assert mime_type_for("x.heic") == "image/heic"
    assert mime_type_for("x.unknownext") == "application/octet-stream"


def test_token_store_roundtrip(tmp_path):
    store = TokenStore(str(tmp_path / "tok.json"))
    assert store.load() is None
    store.save({"access_token": "a", "refresh_token": "r", "expires_at": 1})
    assert store.load()["refresh_token"] == "r"
    store.clear()
    assert store.load() is None


def test_upload_file_sends_file_body(tmp_path, monkeypatch):
    path = tmp_path / "photo.jpg"
    payload = b"photo-bytes" * 3
    path.write_bytes(payload)
    sent = bytearray()

    class Response:
        status = 200

        def read(self):
            return b"upload-token"

    class Connection:
        def __init__(self, host, timeout):
            self.host = host
            self.timeout = timeout

        def putrequest(self, method, path):
            assert method == "POST"
            assert path == "/v1/uploads"

        def putheader(self, name, value):
            pass

        def endheaders(self):
            pass

        def send(self, block):
            sent.extend(block)

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(gphotos.http.client, "HTTPSConnection", Connection)
    client = GooglePhotosClient("id", "secret", str(tmp_path / "tok.json"))
    client._token = {"access_token": "access", "refresh_token": "refresh", "expires_at": 10**12}
    progress = []
    assert client.upload_file(str(path), lambda done, size: progress.append((done, size))) == "upload-token"
    assert bytes(sent) == payload
    assert progress[-1] == (len(payload), len(payload))


def test_client_not_configured_or_signed_in(tmp_path):
    client = GooglePhotosClient("", "", str(tmp_path / "tok.json"))
    assert not client.is_configured()
    with pytest.raises(AuthError):
        client.ensure_authorized()
    client = GooglePhotosClient("id", "secret", str(tmp_path / "tok.json"))
    assert client.is_configured()
    with pytest.raises(AuthError):
        client._bearer()


def test_create_media_items_chunking(tmp_path):
    """50-item batches must be split, results stay aligned with input."""
    client = GooglePhotosClient("id", "secret", str(tmp_path / "tok.json"))
    calls = []

    def fake_api(method, url, payload=None, timeout=60):
        calls.append(payload)
        start = sum(len(p["newMediaItems"]) for p in calls[:-1])
        results = []
        for i, _item in enumerate(payload["newMediaItems"]):
            good = (start + i) % 2 == 0
            results.append({
                "status": {} if good else {"code": 13, "message": "boom"},
                **({"mediaItem": {"id": "m"}} if good else {}),
            })
        return {"newMediaItemResults": results}

    client._api = fake_api
    items = [(f"f{i}.jpg", f"tok{i}") for i in range(120)]
    results = client.create_media_items(items, "album1")
    assert len(calls) == 3  # 50 + 50 + 20
    assert all(len(p["newMediaItems"]) <= 50 for p in calls)
    assert calls[0]["albumId"] == "album1"
    assert len(results) == 120
    assert results[0] == (True, "", "m")
    assert results[1][0] is False and results[1][1] == "boom" and results[1][2] == ""
    assert results[118][0] is True
    assert results[119][0] is False and results[119][1] == "boom"


def test_album_or_create_listing(tmp_path):
    client = GooglePhotosClient("id", "secret", str(tmp_path / "tok.json"))
    client._token = {"access_token": "t", "refresh_token": "r", "expires_at": 1e18}

    def fake_api(method, url, payload=None, timeout=60):
        if method == "GET" and url.endswith("/albums?pageSize=50"):
            return {"albums": [{"id": "a9", "title": "tomsphotos"}]}
        raise AssertionError(f"unexpected call {method} {url}")

    client._api = fake_api
    assert client.get_or_create_album("tomsphotos") == "a9"


def test_list_album_filenames_paginates(tmp_path):
    client = GooglePhotosClient("id", "secret", str(tmp_path / "tok.json"))
    pages = [
        {"mediaItems": [{"filename": "a.jpg"}], "nextPageToken": "t2"},
        {"mediaItems": [{"filename": "B.MOV"}]},
    ]
    tokens = []

    def fake_api(method, url, payload=None, timeout=60):
        tokens.append(payload.get("pageToken", ""))
        return pages[len(tokens) - 1]

    client._api = fake_api
    assert client.list_album_filenames("album1") == {"a.jpg", "B.MOV"}
    assert tokens == ["", "t2"]


# ---------------------------------------------------------------------------
# Upload log + missing computation
# ---------------------------------------------------------------------------

def test_upload_log_roundtrip(tmp_path):
    log = UploadLog(str(tmp_path / "up.db"))
    assert log.count() == 0
    assert not log.has_file("/x/a.jpg", 1.0, 10)
    log.record("/x/a.jpg", 1.0, 10, "a.jpg", "m1")
    assert log.has_file("/x/a.jpg", 1.0, 10)
    # same path, changed mtime/size counts as not uploaded
    assert not log.has_file("/x/a.jpg", 2.0, 10)
    assert not log.has_file("/x/a.jpg", 1.0, 99)
    assert log.known_filenames() == {"a.jpg"}
    log.record("/x/b.jpg", 1.0, 5, "b.jpg", "")
    assert log.count() == 2
    log.close()


def test_upload_log_reopens(tmp_path):
    log = UploadLog(str(tmp_path / "up.db"))
    log.record("/x/a.jpg", 1.0, 10, "a.jpg")
    log.close()
    log2 = UploadLog(str(tmp_path / "up.db"))
    assert log2.has_file("/x/a.jpg", 1.0, 10)
    log2.close()


def test_compute_missing(tmp_path):
    a = str(tmp_path / "a.jpg"); open(a, "wb").write(b"x" * 10)
    b = str(tmp_path / "b.jpg"); open(b, "wb").write(b"y" * 20)
    c = str(tmp_path / "c.jpg"); open(c, "wb").write(b"z" * 30)
    st_a = os.stat(a)
    log = UploadLog(str(tmp_path / "up.db"))
    log.record(a, st_a.st_mtime, st_a.st_size, "a.jpg")
    # album lists b.jpg (uploaded by a previous app session), log knows a.jpg
    missing, tracked = compute_missing([a, b, c], {"b.jpg"}, log=log)
    assert missing == [c]
    assert tracked == 2
    # no log at all: only album matching
    missing, tracked = compute_missing([a, b, c], {"b.jpg"})
    assert missing == [a, c]
    assert tracked == 1
    # log lookup is precise: c is not in the log yet
    missing, tracked = compute_missing([c], set(), log=log)
    assert missing == [c]
    log.record(c, os.stat(c).st_mtime, 30, "c.jpg")
    missing, tracked = compute_missing([c], set(), log=log)
    assert missing == []
    assert tracked == 1
    log.close()


def test_compute_missing_unreadable_file(tmp_path):
    log = UploadLog(str(tmp_path / "up.db"))
    missing, tracked = compute_missing(["/nope/missing.jpg"], set(), log=log)
    assert missing == ["/nope/missing.jpg"]
    assert tracked == 0
    log.close()


# ---------------------------------------------------------------------------
# Qt fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _make_files(tmp_path, names):
    paths = []
    for name in names:
        p = str(tmp_path / name)
        open(p, "wb").write(b"x" * 16)
        paths.append(p)
    return paths


def _fake_client(**overrides):
    client = GooglePhotosClient("id", "secret", "/tmp/does-not-matter.json")
    client.ensure_authorized = overrides.get("ensure_authorized", lambda status_cb=None: False)
    client.get_or_create_album = overrides.get("get_or_create_album", lambda title: "album1")
    client.list_album_filenames = overrides.get("list_album_filenames", lambda album_id: set())
    client.upload_file = overrides.get("upload_file", lambda path, progress_cb=None: f"tok-{os.path.basename(path)}")
    client.create_media_items = overrides.get(
        "create_media_items",
        lambda named_tokens, album_id: [(True, "", f"m-{n}") for n, _t in named_tokens],
    )
    return client


@pytest.fixture
def gp_creds(monkeypatch):
    monkeypatch.setattr(config, "GP_CLIENT_ID", "id")
    monkeypatch.setattr(config, "GP_CLIENT_SECRET", "secret")


def _run_worker(worker):
    events = {"queued": [], "started": [], "done": [], "finished": [], "failed": [], "setup": []}
    worker.queued.connect(lambda a, b: events["queued"].append((a, b)))
    worker.file_started.connect(lambda p, i, t: events["started"].append((p, i, t)))
    worker.item_done.connect(lambda p, ok, m: events["done"].append((p, ok)))
    worker.finished_run.connect(lambda a, b, c: events["finished"].append((a, b, c)))
    worker.failed.connect(lambda m: events["failed"].append(m))
    worker.needs_setup.connect(lambda: events["setup"].append(True))
    worker.run()
    return events


# ---------------------------------------------------------------------------
# SyncWorker
# ---------------------------------------------------------------------------

def test_sync_worker_uploads_and_logs(qapp, tmp_path, gp_creds, monkeypatch):
    from gpdialog import SyncWorker

    paths = _make_files(tmp_path, [f"p{i}.jpg" for i in range(60)])
    uploaded = []
    created = []

    def fake_upload(path, progress_cb=None):
        uploaded.append(path)
        if progress_cb:
            progress_cb(8, 16)
            progress_cb(16, 16)
        return f"tok-{os.path.basename(path)}"

    def fake_create(named_tokens, album_id):
        created.append(list(named_tokens))
        return [(True, "", f"m-{n}") for n, _t in named_tokens]

    monkeypatch.setattr("gpdialog.GooglePhotosClient",
                        lambda *a, **k: _fake_client(upload_file=fake_upload, create_media_items=fake_create))
    log = UploadLog(str(tmp_path / "up.db"))
    events = _run_worker(SyncWorker(paths, log=log))

    assert events["queued"] == [(60, 0)]
    assert [len(c) for c in created] == [50, 10]
    assert events["finished"] == [(60, 0, 0)]
    assert len(uploaded) == 60
    assert log.count() == 60
    assert log.has_file(paths[0], os.stat(paths[0]).st_mtime, os.stat(paths[0]).st_size)
    assert events["done"] and all(ok for _p, ok in events["done"])
    # first and last file were signalled as current
    assert events["started"][0] == (paths[0], 1, 60)
    assert events["started"][-1] == (paths[-1], 60, 60)
    log.close()


def test_sync_worker_skips_album_and_log(qapp, tmp_path, gp_creds, monkeypatch):
    from gpdialog import SyncWorker

    paths = _make_files(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    log = UploadLog(str(tmp_path / "up.db"))
    log.record(paths[1], os.stat(paths[1]).st_mtime, os.stat(paths[1]).st_size, "b.jpg")

    uploaded = []
    monkeypatch.setattr("gpdialog.GooglePhotosClient", lambda *a, **k: _fake_client(
        list_album_filenames=lambda album_id: {"a.jpg"},
        upload_file=lambda path, progress_cb=None: uploaded.append(path) or "tok",
    ))
    events = _run_worker(SyncWorker(paths, log=log))

    assert events["queued"] == [(1, 2)]
    assert uploaded == [paths[2]]
    assert events["finished"] == [(1, 0, 2)]
    log.close()


def test_sync_worker_nothing_to_do(qapp, tmp_path, gp_creds, monkeypatch):
    from gpdialog import SyncWorker

    paths = _make_files(tmp_path, ["a.jpg", "b.jpg"])
    calls = {"upload": 0}
    monkeypatch.setattr("gpdialog.GooglePhotosClient", lambda *a, **k: _fake_client(
        list_album_filenames=lambda album_id: {"a.jpg", "b.jpg"},
        upload_file=lambda path, progress_cb=None: calls.__setitem__("upload", calls["upload"] + 1) or "tok",
    ))
    events = _run_worker(SyncWorker(paths))
    assert events["queued"] == [(0, 2)]
    assert events["finished"] == [(0, 0, 2)]
    assert calls["upload"] == 0
    assert events["started"] == []


def test_sync_worker_records_failures(qapp, tmp_path, gp_creds, monkeypatch):
    from gpdialog import SyncWorker

    paths = _make_files(tmp_path, ["v.jpg"])

    def fail_upload(path, progress_cb=None):
        raise gphotos.ApiError(503, "server busy")

    monkeypatch.setattr("gpdialog.GooglePhotosClient", lambda *a, **k: _fake_client(upload_file=fail_upload))
    events = _run_worker(SyncWorker(paths))
    assert events["finished"] == [(0, 1, 0)]
    assert events["done"] == [(paths[0], False)]


def test_sync_worker_needs_setup(qapp, tmp_path, monkeypatch):
    from gpdialog import SyncWorker

    monkeypatch.setattr(config, "GP_CLIENT_ID", "")
    monkeypatch.setattr(config, "GP_CLIENT_SECRET", "")
    paths = _make_files(tmp_path, ["a.jpg"])
    events = _run_worker(SyncWorker(paths))
    assert events["setup"] == [True]
    assert events["finished"] == []


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

def test_dialog_start_enables_sync(qapp, tmp_path):
    from gpdialog import GoogleSyncDialog

    log = UploadLog(str(tmp_path / "dlg.db"))
    dlg = GoogleSyncDialog(thumb_store=None, log=log, parent=None)
    paths = _make_files(tmp_path, ["a.jpg", "b.jpg"])
    dlg.start(paths, {})
    assert dlg.sync_btn.isEnabled()
    assert dlg.sync_btn.text() == "Sync to Google Photos"
    assert "2 files" in dlg.info_label.text()
    assert not dlg.stop_btn.isEnabled()
    dlg.deleteLater()


def test_dialog_start_empty(qapp, tmp_path):
    from gpdialog import GoogleSyncDialog

    dlg = GoogleSyncDialog(thumb_store=None, log=UploadLog(str(tmp_path / "dlg.db")), parent=None)
    dlg.start([], {})
    assert not dlg.sync_btn.isEnabled()
    assert "no files" in dlg.info_label.text().lower()
    dlg.deleteLater()


def test_dialog_reopen_after_thumbnail_worker(qapp, tmp_path):
    from gpdialog import GoogleSyncDialog
    import time

    path = str(tmp_path / "a.jpg")
    Image.new("RGB", (40, 30), (20, 80, 120)).save(path, "JPEG")
    dlg = GoogleSyncDialog(thumb_store=None, log=UploadLog(str(tmp_path / "dlg.db")), parent=None)
    dlg.start([path], {})
    dlg._load_thumb(path)
    deadline = time.time() + 5
    while dlg._thumb_workers and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    dlg.start([path], {})
    dlg.start([path], {})
    dlg.deleteLater()


def test_dialog_queued_and_current_upload(qapp, tmp_path):
    from gpdialog import GoogleSyncDialog

    dlg = GoogleSyncDialog(thumb_store=None, log=UploadLog(str(tmp_path / "dlg.db")), parent=None)
    paths = _make_files(tmp_path, ["a.jpg"])
    dlg.start(paths, {})
    dlg._load_thumb = lambda p: None  # no worker in tests
    dlg._on_queued(7, 3)
    assert "7" in dlg.info_label.text() and "3" in dlg.info_label.text()
    dlg._on_file_started(paths[0], 2, 7)
    assert "2 of 7" in dlg.current_label.text()
    assert "a.jpg" in dlg.current_label.text()
    dlg._on_file_progress(paths[0], 5, 10)
    assert dlg.progress.maximum() == 10 and dlg.progress.value() == 5
    dlg.deleteLater()


def test_dialog_finished_messages(qapp, tmp_path):
    from gpdialog import GoogleSyncDialog

    dlg = GoogleSyncDialog(thumb_store=None, log=UploadLog(str(tmp_path / "dlg.db")), parent=None)
    dlg.start(_make_files(tmp_path, ["a.jpg"]), {})
    dlg._to_upload = 5
    dlg._on_finished(5, 0, 2)
    assert "Synced 5" in dlg.status_label.text()
    assert dlg.sync_btn.text() == "Sync again"

    dlg._to_upload = 0
    dlg._on_finished(0, 0, 3)
    assert "already synced" in dlg.info_label.text()
    dlg.deleteLater()


def test_dialog_needs_setup(qapp, tmp_path):
    from gpdialog import GoogleSyncDialog

    dlg = GoogleSyncDialog(thumb_store=None, log=UploadLog(str(tmp_path / "dlg.db")), parent=None)
    dlg._on_needs_setup()
    assert not dlg.setup_btn.isHidden()
    assert "not configured" in dlg.info_label.text()
    dlg.deleteLater()


def test_setup_dialog_saves_credentials(qapp, monkeypatch):
    from gpdialog import GPhotosSetupDialog

    saved = {}
    monkeypatch.setattr(config, "set_google_credentials", lambda cid, sec: saved.update(cid=cid, sec=sec))
    dlg = GPhotosSetupDialog(parent=None)
    dlg._id_edit.setText("  cid123  ")
    dlg._secret_edit.setText("sec123")
    dlg._save()
    # dialog passes raw text; set_google_credentials strips/persists itself
    assert saved == {"cid": "  cid123  ", "sec": "sec123"}
    assert dlg.result() == 1
    dlg.deleteLater()
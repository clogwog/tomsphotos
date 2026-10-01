"""Google Photos Library API client (app-created content only).

Since the April 2025 API changes the Library API only exposes content
created by the calling app, so this client keeps an app-owned album
(default "tomsphotos") and matches local files against it by filename:

  albums.list / albums.create            -> photoslibrary.appendonly
  mediaItems:search (album contents)     -> photoslibrary.readonly.appcreateddata
  /v1/uploads + mediaItems:batchCreate   -> photoslibrary.appendonly

Auth is the installed-app OAuth loopback flow (RFC 8252) using only the
stdlib, so a personal Desktop OAuth client id/secret is required.
Files are matched by basename, so two local files that share a basename
are considered backed up if either copy was uploaded.
"""

import http.client
import json
import mimetypes
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
API_BASE = "https://photoslibrary.googleapis.com/v1"
UPLOAD_ENDPOINT = f"{API_BASE}/uploads"

SCOPE_APPENDONLY = "https://www.googleapis.com/auth/photoslibrary.appendonly"
SCOPE_READ_APP_CREATED = "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata"
SCOPES = [SCOPE_APPENDONLY, SCOPE_READ_APP_CREATED]

BATCH_LIMIT = 50
UPLOAD_CHUNK = 1024 * 1024
AUTH_TIMEOUT = 300
BACKOFFS = [1, 2, 5, 10]


class GPhotosError(Exception):
    pass


class AuthError(GPhotosError):
    pass


class ApiError(GPhotosError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {(body or '')[:300]}")
        self.status = status
        self.body = body or ""


def build_auth_url(client_id, redirect_uri):
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def find_missing_files(local_paths, remote_names):
    """Local paths whose basename is not present in remote_names."""
    have = {str(n).lower() for n in remote_names}
    return [p for p in local_paths if os.path.basename(p).lower() not in have]


def compute_missing(local_paths, remote_names, log=None, stat_fn=os.stat):
    """Split local_paths into (missing, tracked_count).

    A file counts as already on Google Photos ("tracked") when either its
    basename is in remote_names (the app-owned album) or the local upload
    log says this exact file (path + mtime + size) was uploaded before.
    remote_names may already include log.known_filenames().
    """
    have = {str(n).lower() for n in remote_names}
    missing = []
    tracked = 0
    for path in local_paths:
        if os.path.basename(path).lower() in have:
            tracked += 1
            continue
        if log is not None:
            try:
                st = stat_fn(path)
            except OSError:
                missing.append(path)
                continue
            if log.has_file(path, st.st_mtime, st.st_size):
                tracked += 1
                continue
        missing.append(path)
    return missing, tracked


def mime_type_for(path):
    guess, _ = mimetypes.guess_type(path)
    return guess or "application/octet-stream"


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class TokenStore:
    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def save(self, data):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def clear(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _http_send(req, timeout):
    """Return (status, body_bytes). Raises ApiError on HTTP errors."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode(errors="replace")
        except Exception:
            body = ""
        raise ApiError(error.code, body)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise GPhotosError(f"Network error: {error}")


def _post_form(fields, timeout=30):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(TOKEN_ENDPOINT, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    status, body = _http_send(req, timeout)
    try:
        payload = json.loads(body.decode() or "{}")
    except ValueError:
        raise AuthError(f"Bad token response: {body[:200]}")
    if status != 200 or "error" in payload:
        raise AuthError(payload.get("error_description") or payload.get("error") or f"HTTP {status}")
    return payload


class _LoopbackAuth:
    """One-shot localhost OAuth receiver (RFC 8252 loopback redirect)."""

    def __init__(self):
        self.result = queue.Queue()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = dict(urllib.parse.parse_qsl(parsed.query))
                if parsed.path == "/favicon.ico":
                    self.send_response(404)
                    self.end_headers()
                    return
                code = params.get("code")
                error = params.get("error")
                body = (
                    "<html><body style='font-family:sans-serif;background:#111;color:#eee;"
                    "display:grid;place-items:center;height:100%'>"
                    "<div><h2>&#10003; Signed in to Google Photos</h2>"
                    "<p>You can close this tab and return to the app.</p></div>"
                    "</body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                outer.result.put(code if code else ("error:" + (error or "no code")))
                # The connection is closed; only stop the server after the
                # main loop shuts it down.

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)

    @property
    def redirect_uri(self):
        host, port = self.server.server_address[:2]
        return f"http://127.0.0.1:{port}"

    def wait_for_code(self):
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        try:
            item = self.result.get(timeout=AUTH_TIMEOUT)
        except queue.Empty:
            raise AuthError("Timed out waiting for the Google sign-in")
        finally:
            self.server.shutdown()
            self.server.server_close()
        if isinstance(item, str) and item.startswith("error:"):
            raise AuthError(f"Google sign-in failed: {item[6:]}")
        return item

    def open_browser(self, url):
        webbrowser.open(url)


class GooglePhotosClient:
    def __init__(self, client_id, client_secret, token_path):
        self.client_id = client_id
        self.client_secret = client_secret
        self.store = TokenStore(token_path)
        self._token = None  # {"access_token", "refresh_token", "expires_at"}

    def is_configured(self):
        return bool(self.client_id and self.client_secret)

    # -- auth -------------------------------------------------------------
    def ensure_authorized(self, status_cb=None):
        """Make sure we hold a working access token. Opens the browser for
        the loopback sign-in flow when no valid token exists. Returns True
        if a fresh sign-in happened."""
        if not self.is_configured():
            raise AuthError("Google Photos client credentials are not configured")
        token = self.store.load()
        if token and token.get("refresh_token"):
            self._token = token
            try:
                self._refresh_if_needed()
                return False
            except AuthError:
                pass  # refresh token revoked -> sign in again
        if status_cb:
            status_cb("Waiting for Google sign-in in your browser…")
        auth = _LoopbackAuth()
        auth.open_browser(build_auth_url(self.client_id, auth.redirect_uri))
        code = auth.wait_for_code()
        payload = _post_form({
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": auth.redirect_uri,
            "grant_type": "authorization_code",
        })
        self._token = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or (token or {}).get("refresh_token", ""),
            "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        }
        if not self._token["refresh_token"]:
            raise AuthError("Google did not return a refresh token; sign in again")
        self.store.save(self._token)
        return True

    def _refresh(self):
        resp = _post_form({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self._token["refresh_token"],
            "grant_type": "refresh_token",
        })
        self._token["access_token"] = resp["access_token"]
        self._token["expires_at"] = time.time() + int(resp.get("expires_in", 3600))
        if resp.get("refresh_token"):
            self._token["refresh_token"] = resp["refresh_token"]
        self.store.save(self._token)

    def _refresh_if_needed(self):
        if time.time() > self._token.get("expires_at", 0) - 60:
            self._refresh()

    def _bearer(self):
        if not self._token:
            token = self.store.load()
            if not (token and token.get("refresh_token")):
                raise AuthError("Not signed in to Google Photos")
            self._token = token
        self._refresh_if_needed()
        return self._token["access_token"]

    # -- API --------------------------------------------------------------
    def _api(self, method, url, payload=None, timeout=60):
        for attempt in range(len(BACKOFFS) + 1):
            try:
                data = json.dumps(payload).encode() if payload is not None else None
                req = urllib.request.Request(url, data=data, method=method)
                req.add_header("Authorization", f"Bearer {self._bearer()}")
                if data:
                    req.add_header("Content-Type", "application/json")
                status, body = _http_send(req, timeout)
                try:
                    return json.loads(body.decode() or "{}")
                except ValueError:
                    return {}
            except ApiError as error:
                if error.status == 401 and attempt == 0:
                    self._refresh()
                    continue
                if error.status in (429, 500, 503, 504) and attempt < len(BACKOFFS):
                    time.sleep(BACKOFFS[attempt])
                    continue
                raise
        raise ApiError(500, "unreachable")

    def get_or_create_album(self, title):
        page_token = ""
        while True:
            url = f"{API_BASE}/albums?pageSize=50"
            if page_token:
                url += f"&pageToken={urllib.parse.quote(page_token)}"
            data = self._api("GET", url)
            for album in data.get("albums", []):
                if album.get("title") == title:
                    return album["id"]
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
        data = self._api("POST", f"{API_BASE}/albums", {"album": {"title": title}})
        album_id = data.get("id") or (data.get("album") or {}).get("id")
        if not album_id:
            raise ApiError(0, "albums.create returned no id")
        return album_id

    def list_album_filenames(self, album_id):
        names = set()
        page_token = ""
        while True:
            payload = {"albumId": album_id, "pageSize": 100}
            if page_token:
                payload["pageToken"] = page_token
            data = self._api("POST", f"{API_BASE}/mediaItems:search", payload)
            for item in data.get("mediaItems", []):
                if item.get("filename"):
                    names.add(item["filename"])
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
        return names

    def upload_file(self, path, progress_cb=None):
        """Upload full-resolution bytes, return the upload token."""
        size = os.path.getsize(path)
        mime = mime_type_for(path)
        endpoint = urllib.parse.urlsplit(UPLOAD_ENDPOINT)
        last_error = None
        for attempt in range(len(BACKOFFS) + 1):
            sent = 0
            connection = None
            try:
                connection = http.client.HTTPSConnection(endpoint.netloc, timeout=1800)
                connection.putrequest("POST", endpoint.path)
                connection.putheader("Authorization", f"Bearer {self._bearer()}")
                connection.putheader("Content-Type", "application/octet-stream")
                connection.putheader("X-Goog-Upload-Protocol", "raw")
                connection.putheader("X-Goog-Upload-Content-Type", mime)
                connection.putheader("Content-Length", str(size))
                connection.endheaders()
                with open(path, "rb") as file:
                    while True:
                        block = file.read(UPLOAD_CHUNK)
                        if not block:
                            break
                        connection.send(block)
                        sent += len(block)
                        if progress_cb:
                            try:
                                progress_cb(sent, size)
                            except Exception:
                                pass
                response = connection.getresponse()
                body = response.read()
                if response.status < 200 or response.status >= 300:
                    raise ApiError(response.status, body.decode(errors="replace"))
                token = body.decode().strip()
                if not token:
                    raise ApiError(500, "empty upload token")
                return token
            except ApiError as error:
                last_error = error
                if error.status in (429, 500, 503, 504) and attempt < len(BACKOFFS):
                    time.sleep(BACKOFFS[attempt])
                    continue
                raise
            except (OSError, TimeoutError) as error:
                last_error = GPhotosError(f"Network error: {error}")
                if attempt < len(BACKOFFS):
                    time.sleep(BACKOFFS[attempt])
                    continue
                raise last_error
            finally:
                if connection is not None:
                    connection.close()
        raise last_error

    def create_media_items(self, named_tokens, album_id):
        """Create media items from [(filename, upload_token)]. Returns a list
        of (ok, message, media_item_id) aligned with the input order."""
        results = []
        for chunk in _chunks(list(named_tokens), BATCH_LIMIT):
            payload = {
                "albumId": album_id,
                "newMediaItems": [
                    {"simpleMediaItem": {"fileName": name, "uploadToken": token}}
                    for name, token in chunk
                ],
            }
            data = self._api("POST", f"{API_BASE}/mediaItems:batchCreate", payload, timeout=120)
            per_item = data.get("newMediaItemResults", [])
            for i, (name, _token) in enumerate(chunk):
                entry = per_item[i] if i < len(per_item) else {}
                status = entry.get("status", {})
                media_id = (entry.get("mediaItem") or {}).get("id", "")
                ok = status.get("code", 0) == 0 and media_id
                results.append((bool(ok), "" if ok else status.get("message", "unknown error"), media_id))
        return results

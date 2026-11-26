r"""Checks the Google Drive storage backend (environment switch, Part B step 3)
against a fake Drive held in memory: save / read / delete, files placed in the
Shared Drive folder, retries with backoff on rate limits, outages and
timeouts, quota errors, an expired token, size limits, unusual-use logging,
and the 503 the API returns when storage fails.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_storage_gdrive.py
Nothing reaches Google: every Drive call goes to the fake below.
"""
import json
import logging
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["STORAGE_BACKEND"] = "gdrive"
os.environ["GDRIVE_CREDENTIALS_FILE"] = "no-such-key-file.json"
os.environ["GDRIVE_FOLDER_ID"] = "FOLDER123"

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
import httpx  # noqa: E402

from app.core import storage  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}")


# --- fake Drive ---------------------------------------------------------------
class FakeDrive:
    def __init__(self):
        self.files = {}        # id -> dict(name, parents, content, trashed)
        self.sessions = {}     # session id -> metadata
        self.calls = []        # (method, path, params)
        self.inject = []       # responses / exceptions to return before normal handling
        self.next = 0

    def handler(self, request: httpx.Request):
        url = urlparse(str(request.url))
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.calls.append((request.method, url.path, params, dict(request.headers)))
        if self.inject:
            item = self.inject.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if request.method == "POST" and url.path == "/upload/drive/v3/files":
            self.next += 1
            sid = f"s{self.next}"
            self.sessions[sid] = json.loads(request.content)
            return httpx.Response(200, headers={"Location": f"https://upload.fake/session/{sid}"})
        if request.method == "PUT" and url.path.startswith("/session/"):
            meta = self.sessions.pop(url.path.rsplit("/", 1)[1])
            self.next += 1
            fid = f"f{self.next}"
            self.files[fid] = {"name": meta["name"], "parents": meta["parents"],
                               "content": request.content, "trashed": False}
            return httpx.Response(200, json={"id": fid})
        if request.method == "GET" and url.path == "/drive/v3/files":
            name = re.search(r"name = '([^']+)'", params["q"]).group(1)
            folder = re.search(r"'([^']+)' in parents", params["q"]).group(1)
            hits = [{"id": i, "size": str(len(f["content"]))} for i, f in self.files.items()
                    if f["name"] == name and folder in f["parents"] and not f["trashed"]]
            return httpx.Response(200, json={"files": hits})
        m = re.fullmatch(r"/drive/v3/files/(\w+)", url.path)
        if m:
            f = self.files.get(m.group(1))
            if not f or (f["trashed"] and request.method == "GET"):
                return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
            if request.method == "GET" and params.get("alt") == "media":
                return httpx.Response(200, content=f["content"])
            if request.method == "PATCH":
                f.update(json.loads(request.content))
                return httpx.Response(200, json={"id": m.group(1)})
        return httpx.Response(400, json={"error": {"errors": [{"reason": "badRequest"}]}})


def err(status, reason, headers=None):
    return httpx.Response(status, headers=headers or {}, json={"error": {"errors": [{"reason": reason}]}})


drive = FakeDrive()
storage._client = httpx.Client(transport=httpx.MockTransport(drive.handler))

tokens = {"refreshes": 0}


def fake_token(force_refresh=False):
    if force_refresh:
        tokens["refreshes"] += 1
    return f"tok{tokens['refreshes']}"


real_token = storage._token
storage._token = fake_token

sleeps = []
storage.time = types.SimpleNamespace(time=__import__("time").time, sleep=lambda s: sleeps.append(s))


def reset():
    drive.calls.clear()
    drive.inject.clear()
    sleeps.clear()
    storage._ids.clear()


def key(ext=".pdf"):
    return storage.generate_storage_key("x" + ext)


# --- 1. save, read, delete ----------------------------------------------------
reset()
k1 = key()
storage.save_file(k1, b"%PDF hello")
up = [c for c in drive.calls if c[0] == "POST"][0]
f1 = next(f for f in drive.files.values() if f["name"] == k1)
check("upload goes into the Shared Drive folder", f1["parents"] == ["FOLDER123"])
check("upload is resumable and supports Shared Drives",
      up[2].get("uploadType") == "resumable" and up[2].get("supportsAllDrives") == "true")
check("upload sends the bearer token", up[3].get("authorization") == "Bearer tok0")
check("uploaded bytes match", f1["content"] == b"%PDF hello")

drive.calls.clear()
check("read returns the bytes", storage.read_file(k1) == b"%PDF hello")
check("read right after save needs no search (id remembered)",
      not any(c[1] == "/drive/v3/files" for c in drive.calls))

storage._ids.clear()
drive.calls.clear()
check("read after a restart finds the file by name", storage.read_file(k1) == b"%PDF hello")
search = next(c for c in drive.calls if c[1] == "/drive/v3/files")
check("search is limited to the folder and skips trash",
      "'FOLDER123' in parents" in search[2]["q"] and "trashed = false" in search[2]["q"])
check("download goes through the API (alt=media), never a share link",
      any(c[2].get("alt") == "media" for c in drive.calls)
      and not any("permissions" in c[1] for c in drive.calls))

storage.delete_file(k1)
check("delete moves the file to the trash", f1["trashed"] is True)
try:
    storage.read_file(k1)
    check("read after delete raises FileNotFoundError", False)
except FileNotFoundError:
    check("read after delete raises FileNotFoundError", True)

reset()
try:
    storage.read_file(key())
    check("read of a missing file raises FileNotFoundError", False)
except FileNotFoundError:
    check("read of a missing file raises FileNotFoundError", True)
storage.delete_file(key())
check("delete of a missing file is quietly ignored", True)

# --- 2. retries ---------------------------------------------------------------
reset()
k2 = key()
drive.inject = [err(503, "backendError"), err(500, "backendError")]
storage.save_file(k2, b"retry me")
check("upload succeeds after two server errors", any(f["name"] == k2 for f in drive.files.values()))
check("waits grow between tries (about 1 s then 2 s)",
      len(sleeps) == 2 and 1 <= sleeps[0] < 1.6 and 2 <= sleeps[1] < 2.6)

reset()
drive.inject = [err(503, "backendError")] * 4
try:
    storage.save_file(key(), b"x")
    check("gives up after 4 tries with a safe message", False)
except storage.StorageError as e:
    check("gives up after 4 tries with a safe message",
          str(e) == storage.MSG_UNAVAILABLE and len(drive.calls) == 4 and len(sleeps) == 3)

reset()
drive.inject = [err(403, "userRateLimitExceeded", {"Retry-After": "99"})]
storage.save_file(key(), b"rate")
check("403 rate limit is retried, Retry-After capped at 20 s", len(sleeps) == 1 and 20 <= sleeps[0] < 20.6)

reset()
drive.inject = [httpx.ReadTimeout("slow"), httpx.ConnectError("down")]
storage.save_file(key(), b"net")
check("timeouts and network errors are retried", len(sleeps) == 2)

# --- 3. errors that are not retried -------------------------------------------
reset()
drive.inject = [err(403, "storageQuotaExceeded")]
try:
    storage.save_file(key(), b"full")
    check("quota error stops at once with the quota message", False)
except storage.StorageError as e:
    check("quota error stops at once with the quota message",
          str(e) == storage.MSG_QUOTA and len(drive.calls) == 1 and not sleeps)

reset()
drive.inject = [err(403, "insufficientFilePermissions")]
try:
    storage.save_file(key(), b"perm")
    check("permission error stops at once with the setup message", False)
except storage.StorageError as e:
    check("permission error stops at once with the setup message",
          str(e) == storage.MSG_CONFIG and len(drive.calls) == 1)

reset()
before = tokens["refreshes"]
drive.inject = [err(401, "authError")]
k3 = key()
storage.save_file(k3, b"auth")
check("expired token: refreshed once and the call repeated",
      tokens["refreshes"] == before + 1 and any(f["name"] == k3 for f in drive.files.values())
      and drive.calls[1][3].get("authorization") == f"Bearer tok{before + 1}")

# --- 4. limits and bad input --------------------------------------------------
reset()
for bad in ("../etc/passwd", "abc' or name contains '", "x.pdf"):
    try:
        storage.read_file(bad)
        check(f"bad key refused: {bad!r}", False)
    except ValueError:
        check(f"bad key refused: {bad!r}", True)
check("bad keys never reach Drive", not drive.calls)

try:
    storage.save_file(key(), b"0" * (storage.MAX_FILE_SIZE_BYTES + 1))
    check("save over 20 MB refused", False)
except ValueError:
    check("save over 20 MB refused", not drive.calls)

reset()
kb = key()
drive.files["big"] = {"name": kb, "parents": ["FOLDER123"], "content": b"", "trashed": False}
storage._remember(kb, "big", storage.MAX_FILE_SIZE_BYTES + 1)
try:
    storage.read_file(kb)
    check("oversized Drive file is not downloaded", False)
except storage.StorageError:
    check("oversized Drive file is not downloaded", not any(c[2].get("alt") == "media" for c in drive.calls))

# --- 5. credentials -----------------------------------------------------------
storage._token = real_token
storage._creds = None
reset()
try:
    storage.save_file(key(), b"x")
    check("missing key file gives the setup message", False)
except storage.StorageError as e:
    check("missing key file gives the setup message", str(e) == storage.MSG_CONFIG and not drive.calls)
storage._token = fake_token

# --- 6. unusual-use logging ---------------------------------------------------
records = []
handler = logging.Handler()
handler.emit = lambda r: records.append(r.getMessage())
storage.logger.addHandler(handler)
storage._HOURLY_OPS_WARN["save"] = 2
storage._usage._hour = -1
reset()
for _ in range(5):
    storage.save_file(key(), b"u")
warns = [m for m in records if m.startswith("Unusual storage use")]
check("unusual volume logged once per hour", len(warns) == 1)
reset()
records.clear()
drive.inject = [err(503, "backendError")]
storage.save_file(key(), b"w")
check("each retry is logged as a warning", any("try 2 of 4" in m for m in records))
storage.logger.removeHandler(handler)

# --- 7. API answer when storage fails -----------------------------------------
from fastapi.testclient import TestClient  # noqa: E402
import app.main as main  # noqa: E402


def boom():
    raise storage.StorageError(storage.MSG_QUOTA)


main.app.add_api_route("/__storage_boom", boom)
resp = TestClient(main.app).get("/__storage_boom")
check("StorageError becomes 503 with the safe message",
      resp.status_code == 503 and resp.json() == {"detail": storage.MSG_QUOTA})

# --- 8. start-up checks -------------------------------------------------------
def start(env):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("STORAGE_", "GDRIVE_"))}
    e.update(env)
    return subprocess.run([sys.executable, "-c", "import app.core.storage"], cwd=BACKEND_DIR, env=e,
                          capture_output=True, text=True)


r = start({"STORAGE_BACKEND": "gdrive"})
check("gdrive without key file / folder refuses to start", r.returncode != 0 and "GDRIVE_CREDENTIALS_FILE" in r.stderr)
r = start({"STORAGE_BACKEND": "s3"})
check("unknown backend refuses to start", r.returncode != 0 and "STORAGE_BACKEND" in r.stderr)
r = start({"STORAGE_BACKEND": "local"})
check("local backend starts", r.returncode == 0)

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)

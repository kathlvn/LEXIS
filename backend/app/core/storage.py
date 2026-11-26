"""
Document storage adapter.

Two backends behind the same functions, chosen by STORAGE_BACKEND in .env:

- "local"  — files on this machine's disk under STORAGE_ROOT (development).
- "gdrive" — the Legal Office's Google Drive (ESSU Google Workspace): one
  folder in a Shared Drive owned by ESSU, reached with a service account.

The rest of LEXIS only calls generate_storage_key, validate_upload,
save_file, read_file and delete_file, and never touches the disk or Drive
itself. Drive only holds the bytes, each file named by its random storage
key. Versions, categories, who uploaded what and who may see it all stay in
the database. Files are never shared by Drive link: every download goes
through the LEXIS backend, which checks who is asking first.

Drive calls have timeouts and a capped number of retries with backoff.
Quota or permission problems become StorageError, which main.py turns into
a 503 with a message that is safe to show. Unusual volume (see the
_HOURLY_* limits) is logged as a warning.
"""
import logging
import mimetypes
import random
import re
import secrets
import threading
import time
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("lois.storage")

# Absolute path to backend/storage, no matter which folder uvicorn is started from.
_BACKEND_DIR = Path(__file__).resolve().parents[2]
STORAGE_ROOT = Path(settings.local_storage_dir or _BACKEND_DIR / "storage")

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB per file — adjust if needed

BACKEND = settings.storage_backend.strip().lower()
if BACKEND not in ("local", "gdrive"):
    raise RuntimeError(f"STORAGE_BACKEND must be 'local' or 'gdrive', not {settings.storage_backend!r}.")
if BACKEND == "gdrive" and not (settings.gdrive_credentials_file and settings.gdrive_folder_id):
    raise RuntimeError("STORAGE_BACKEND=gdrive needs GDRIVE_CREDENTIALS_FILE and GDRIVE_FOLDER_ID in .env.")


class StorageError(Exception):
    """Document storage could not do what was asked. The message is safe to
    show to the user; the details are in the log."""


MSG_UNAVAILABLE = "The document storage is not responding right now. Please try again in a few minutes."
MSG_QUOTA = ("The Legal Office's document storage has reached its limit, so files can't be saved or opened "
             "right now. Please let the Legal Office know.")
MSG_CONFIG = "The document storage is not set up correctly. Please let the Legal Office know."


def generate_storage_key(original_filename: str) -> str:
    """
    Random, non-guessable key — never derived from the original filename,
    tracking ID, or database ID, per the spec's requirement that documents
    are never reachable through a guessable URL. The extension is kept so
    downloads can set a sane Content-Disposition without a DB round-trip.
    """
    ext = Path(original_filename).suffix.lower()
    return f"{secrets.token_hex(24)}{ext}"


# What each allowed type starts with, so a program renamed ".pdf" or
# a web page renamed ".png" is refused. A PDF may have up to 1 KB of other
# bytes before "%PDF-"; .docx is a ZIP file.
_SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".docx": (b"PK\x03\x04",),
    ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}


def content_matches_type(original_filename: str, content: bytes) -> bool:
    ext = Path(original_filename).suffix.lower()
    signatures = _SIGNATURES.get(ext)
    if not signatures:
        return False
    if ext == ".pdf":
        return b"%PDF-" in content[:1024]
    return content.startswith(signatures)


def validate_upload(original_filename: str, file_size_bytes: int, content: bytes | None = None) -> str | None:
    """Returns a user-facing error message if the file fails validation,
    or None if it's fine to store. With the content, also checks that the
    file really is the type its name says."""
    if len(original_filename) > 255:
        return "The file name is too long (255 characters max). Please rename the file."
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"'{ext}' files are not allowed. Accepted types: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
    if file_size_bytes == 0:
        return "The uploaded file is empty."
    if file_size_bytes > MAX_FILE_SIZE_BYTES:
        return f"File exceeds the {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB limit."
    if content is not None and not content_matches_type(original_filename, content):
        return (f"This file isn't a real {ext[1:].upper()} file (it may have been renamed). "
                "Save it again in that format, then upload it.")
    return None


# Keys only ever come from generate_storage_key(); anything else is refused,
# so a key can't reach outside the folder or into a Drive search.
_KEY_RE = re.compile(r"^[0-9a-f]{48}(\.[a-z0-9_-]{1,16})?$")


def _check_key(storage_key: str) -> None:
    if not _KEY_RE.match(storage_key):
        raise ValueError("Invalid storage key.")


def save_file(storage_key: str, content: bytes) -> None:
    _check_key(storage_key)
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise ValueError("File is larger than the storage limit.")
    _usage.add("save", len(content))
    if BACKEND == "gdrive":
        _drive_save(storage_key, content)
        return
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    (STORAGE_ROOT / storage_key).write_bytes(content)


def read_file(storage_key: str) -> bytes:
    """Raises FileNotFoundError if the file isn't in storage."""
    _check_key(storage_key)
    content = _drive_read(storage_key) if BACKEND == "gdrive" else (STORAGE_ROOT / storage_key).read_bytes()
    _usage.add("read", len(content))
    return content


def delete_file(storage_key: str) -> None:
    """Removes the file; does nothing if it is already gone. On Drive the
    file goes to the Shared Drive's trash (kept 30 days, then removed)."""
    _check_key(storage_key)
    _usage.add("delete", 0)
    if BACKEND == "gdrive":
        _drive_delete(storage_key)
        return
    path = STORAGE_ROOT / storage_key
    if path.exists():
        path.unlink()


# --- Unusual-volume logging -------------------------------------------------
# Rough guides for one small office. Going past one only logs a warning (once
# per hour) so someone can look into it; nothing is blocked.
_HOURLY_OPS_WARN = {"save": 300, "read": 1500, "delete": 100}
_HOURLY_BYTES_WARN = 2 * 1024 * 1024 * 1024  # 2 GB moved in an hour


class _Usage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hour = -1
        self._ops: dict[str, int] = {}
        self._bytes = 0
        self._warned: set[str] = set()

    def add(self, op: str, nbytes: int) -> None:
        hour = int(time.time() // 3600)
        with self._lock:
            if hour != self._hour:
                self._hour, self._ops, self._bytes, self._warned = hour, {}, 0, set()
            self._ops[op] = self._ops.get(op, 0) + 1
            self._bytes += nbytes
            if self._ops[op] > _HOURLY_OPS_WARN[op] and op not in self._warned:
                self._warned.add(op)
                logger.warning("Unusual storage use: more than %d %s operations this hour.",
                               _HOURLY_OPS_WARN[op], op)
            if self._bytes > _HOURLY_BYTES_WARN and "bytes" not in self._warned:
                self._warned.add("bytes")
                logger.warning("Unusual storage use: more than %d MB moved this hour.",
                               _HOURLY_BYTES_WARN // (1024 * 1024))


_usage = _Usage()


# --- Google Drive backend ---------------------------------------------------
# Plain REST calls to Drive API v3 with httpx; google-auth handles the
# service-account token. supportsAllDrives is on every call because the
# folder lives in a Shared Drive.

_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_SCOPES = ["https://www.googleapis.com/auth/drive"]
_MAX_ATTEMPTS = 4          # first try + 3 retries
_BACKOFF_SECONDS = 1.0     # 1 s, 2 s, 4 s, plus a little jitter
_MAX_WAIT_SECONDS = 20     # never wait longer than this between tries
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
_QUOTA_REASONS = {"storageQuotaExceeded", "quotaExceeded", "dailyLimitExceeded",
                  "teamDriveFileLimitExceeded", "numChildrenInNonRootLimitExceeded"}

_lock = threading.Lock()
_creds = None
_client = None
# storage key -> (Drive file id, size). Saves a search on repeat downloads.
_ids: dict[str, tuple[str, int]] = {}
_IDS_MAX = 2000


class _Retry(Exception):
    def __init__(self, reason: str, wait: float | None = None) -> None:
        super().__init__(reason)
        self.reason, self.wait = reason, wait


def _http():
    global _client
    import httpx
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
        return _client


def _token(force_refresh: bool = False) -> str:
    global _creds
    from google.auth.exceptions import GoogleAuthError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    with _lock:
        try:
            if _creds is None:
                _creds = service_account.Credentials.from_service_account_file(
                    settings.gdrive_credentials_file, scopes=_SCOPES)
            if force_refresh or not _creds.valid:
                _creds.refresh(Request())
        except TransportError as e:
            raise _Retry(f"token refresh failed: {e}") from e
        except (OSError, ValueError, GoogleAuthError) as e:
            logger.error("Drive credentials problem (%s): %s", settings.gdrive_credentials_file, e)
            raise StorageError(MSG_CONFIG) from e
        return _creds.token


def _reason(resp) -> str:
    try:
        err = resp.json().get("error", {})
        errors = err.get("errors") or [{}]
        return errors[0].get("reason") or err.get("status") or str(resp.status_code)
    except Exception:
        return str(resp.status_code)


def _send(op: str, method: str, url: str, *, allow_404: bool = False, auth_retry: bool = True, **kw):
    """One HTTP call. Returns the response, raises _Retry for errors worth
    trying again, StorageError for the rest."""
    import httpx
    extra = kw.pop("headers", {})
    headers = {**extra, "Authorization": f"Bearer {_token()}"}
    try:
        resp = _http().request(method, url, headers=headers, **kw)
    except httpx.TimeoutException as e:
        raise _Retry(f"timeout ({type(e).__name__})") from e
    except httpx.TransportError as e:
        raise _Retry(f"network error ({type(e).__name__})") from e

    if resp.status_code < 400 or (allow_404 and resp.status_code == 404):
        return resp
    reason = _reason(resp)
    if resp.status_code == 401 and auth_retry:
        # Token expired early or was revoked: get a new one and try once more.
        _token(force_refresh=True)
        return _send(op, method, url, allow_404=allow_404, auth_retry=False, headers=extra, **kw)
    if resp.status_code in _RETRY_STATUSES or (resp.status_code == 403 and reason in _RATE_REASONS):
        wait = None
        try:
            wait = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            pass
        raise _Retry(f"HTTP {resp.status_code} {reason}", wait)
    if reason in _QUOTA_REASONS:
        logger.error("Drive %s refused: quota reached (%s).", op, reason)
        raise StorageError(MSG_QUOTA)
    logger.error("Drive %s failed: HTTP %d %s", op, resp.status_code, reason)
    raise StorageError(MSG_CONFIG if resp.status_code in (401, 403, 404) else MSG_UNAVAILABLE)


def _with_retries(op: str, fn):
    """Runs fn(), trying again on _Retry with growing waits, up to
    _MAX_ATTEMPTS times in all."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fn()
        except _Retry as r:
            if attempt == _MAX_ATTEMPTS:
                logger.error("Drive %s gave up after %d tries: %s", op, attempt, r.reason)
                raise StorageError(MSG_UNAVAILABLE) from r
            wait = r.wait if r.wait is not None else _BACKOFF_SECONDS * 2 ** (attempt - 1)
            wait = min(wait, _MAX_WAIT_SECONDS) + random.uniform(0, 0.5)
            logger.warning("Drive %s: %s — try %d of %d again in %.1f s.", op, r.reason, attempt + 1,
                           _MAX_ATTEMPTS, wait)
            time.sleep(wait)


def _remember(storage_key: str, file_id: str, size: int) -> None:
    with _lock:
        if len(_ids) >= _IDS_MAX:
            _ids.pop(next(iter(_ids)))
        _ids[storage_key] = (file_id, size)


def _find(storage_key: str) -> tuple[str, int] | None:
    with _lock:
        hit = _ids.get(storage_key)
    if hit:
        return hit
    # Safe to put in the query: _check_key allows no quotes or spaces.
    q = f"name = '{storage_key}' and '{settings.gdrive_folder_id}' in parents and trashed = false"
    resp = _with_retries("search", lambda: _send("search", "GET", _FILES_URL, params={
        "q": q, "fields": "files(id,size)", "pageSize": 2, "corpora": "allDrives",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
    }))
    files = resp.json().get("files", [])
    if not files:
        return None
    if len(files) > 1:
        logger.warning("Drive has more than one file named %s; using the first.", storage_key)
    found = (files[0]["id"], int(files[0].get("size") or 0))
    _remember(storage_key, *found)
    return found


def _drive_save(storage_key: str, content: bytes) -> None:
    mime = mimetypes.guess_type(storage_key)[0] or "application/octet-stream"

    def upload():
        # Resumable upload: start a session, then send the bytes. A retry
        # starts a fresh session, which is fine for files of 20 MB or less.
        start = _send("upload", "POST", _UPLOAD_URL,
                      params={"uploadType": "resumable", "supportsAllDrives": "true"},
                      headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(len(content))},
                      json={"name": storage_key, "parents": [settings.gdrive_folder_id]})
        session_url = start.headers.get("Location")
        if not session_url:
            raise _Retry("no upload session returned")
        done = _send("upload", "PUT", session_url, content=content, headers={"Content-Type": mime})
        return done.json()["id"]

    file_id = _with_retries("upload", upload)
    _remember(storage_key, file_id, len(content))
    logger.info("Drive upload: %s (%d bytes).", storage_key, len(content))


def _drive_read(storage_key: str) -> bytes:
    found = _find(storage_key)
    if not found:
        raise FileNotFoundError(storage_key)
    file_id, size = found
    if size > MAX_FILE_SIZE_BYTES:
        # LEXIS never saves a file this big, so someone put it there by hand.
        logger.warning("Drive file %s is %d bytes, over the limit; not downloading it.", storage_key, size)
        raise StorageError(MSG_CONFIG)
    resp = _with_retries("download", lambda: _send(
        "download", "GET", f"{_FILES_URL}/{file_id}",
        params={"alt": "media", "supportsAllDrives": "true"}, allow_404=True))
    if resp.status_code == 404:
        with _lock:
            _ids.pop(storage_key, None)
        raise FileNotFoundError(storage_key)
    return resp.content


def _drive_delete(storage_key: str) -> None:
    found = _find(storage_key)
    if not found:
        return
    # Trash rather than delete: a Content manager on the Shared Drive may
    # trash files, and a mistake can be undone for 30 days.
    _with_retries("delete", lambda: _send(
        "delete", "PATCH", f"{_FILES_URL}/{found[0]}",
        params={"supportsAllDrives": "true", "fields": "id"}, json={"trashed": True}, allow_404=True))
    with _lock:
        _ids.pop(storage_key, None)
    logger.info("Drive delete (to trash): %s.", storage_key)

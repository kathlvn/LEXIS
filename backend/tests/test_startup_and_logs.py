r"""Startup guard and what the server log may contain (qa-report SEC-09,
MAIL-04, SEC-12, SEC-08, PRIV-01, PRIV-02):
- LEXIS refuses to start without a valid ENVIRONMENT, with a weak SECRET_KEY
  outside development, or without a real https PUBLIC_SITE_URL in production;
- the API pages (/docs, /redoc, /openapi.json) open only in development;
- no other site is allowed by CORS unless CORS_ALLOWED_ORIGINS is set;
- SQL is not printed unless SQL_ECHO=true, and query values are never shown,
  not even in an error;
- the access log line keeps the path but drops the query string.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_startup_and_logs.py
Each case starts LEXIS in a separate Python process, in an empty temp folder
(so backend/.env is not there to read), with a throwaway SQLite file. Nothing
is sent anywhere and the real database is never touched.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
results = []


def check(name, good, detail=""):
    results.append(bool(good))
    print(("PASS " if good else "FAIL ") + name + (f"  -> {detail}" if not good else ""))


# Runs inside each child process. The guard refuses .env files and any
# connection outside this computer, like lexis_world.py does.
CHILD = r'''
import json, os, sys
def _guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.path.basename(os.fsdecode(args[0])).lower().startswith(".env"):
            raise PermissionError("blocked by the test guard")
    if event == "socket.connect" and isinstance(args[1], tuple) and args[1][0] not in ("127.0.0.1", "::1"):
        raise OSError("blocked by the test guard")
sys.addaudithook(_guard)
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import SQLModel
import app.models.models
from app.core.database import engine
SQLModel.metadata.create_all(engine)
from app.main import app
out = {"echo": engine.echo, "hide_parameters": engine.hide_parameters}
try:
    with TestClient(app) as c:
        out["started"] = True
        out["docs"] = [c.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")]
        r = c.options("/health", headers={"Origin": "http://localhost:5500", "Access-Control-Request-Method": "GET"})
        out["cors"] = r.headers.get("access-control-allow-origin")
except RuntimeError as e:
    out["started"] = False
    out["error"] = str(e)
try:
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO no_such_table VALUES (:a)"), {"a": "secret message text"})
except Exception as e:
    out["db_error"] = str(e)
print("RESULT" + json.dumps(out))
'''

SCRUB = ("ENVIRONMENT", "SECRET_KEY", "DATABASE_URL", "PUBLIC_SITE_URL", "CORS_ALLOWED_ORIGINS", "SQL_ECHO",
         "SMTP_", "MAIL_", "GDRIVE_", "STORAGE_", "LOCAL_STORAGE", "LOIS_", "ALLOWED_HOSTS")
GOOD_KEY = "test-only-secret-key-" + "x" * 32


def start(**extra):
    """Starts LEXIS in a child process with only these settings; returns what it saw."""
    tmp = tempfile.mkdtemp(prefix="lexis-test-start-")
    try:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(SCRUB)}
        env.update({"DATABASE_URL": "sqlite:///" + os.path.join(tmp, "t.db").replace("\\", "/"),
                    "STORAGE_BACKEND": "local", "LOCAL_STORAGE_DIR": os.path.join(tmp, "st")})
        env.update(extra)
        p = subprocess.run([sys.executable, "-c", CHILD, str(BACKEND)], cwd=tmp, env=env,
                           capture_output=True, text=True, timeout=120)
        line = [x for x in p.stdout.splitlines() if x.startswith("RESULT")]
        res = json.loads(line[0][6:]) if line else {"crash": p.stderr[-600:]}
        res["output"] = p.stdout + p.stderr
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def refused(res, words):
    return res.get("started") is False and words in res.get("error", "")


PROD = {"ENVIRONMENT": "production", "SECRET_KEY": GOOD_KEY, "PUBLIC_SITE_URL": "https://lexis.example.com"}

# ---- SEC-09: ENVIRONMENT must be set, and spelt right --------------------------
for name, extra in [("not set", {}), ("empty", {"ENVIRONMENT": ""}), ("misspelt 'Production'", {"ENVIRONMENT": "Production"})]:
    res = start(**extra)
    check(f"SEC-09 ENVIRONMENT {name}: LEXIS refuses to start", refused(res, "ENVIRONMENT must be set"), res.get("error") or res.get("crash"))

# ---- SEC-09: SECRET_KEY outside development ------------------------------------
for name, key in [("the public default", "dev-secret-key-change-me"), ("too short (31 characters)", "x" * 31)]:
    res = start(**{**PROD, "SECRET_KEY": key})
    check(f"SEC-09 production with SECRET_KEY {name}: refused", refused(res, "SECRET_KEY must be set"), res.get("error") or res.get("crash"))

# ---- MAIL-04: PUBLIC_SITE_URL in production ------------------------------------
for name, url in [("not set (localhost default)", None), ("http", "http://lexis.example.com"),
                  ("https localhost", "https://localhost:8443"), ("https 127.0.0.1", "https://127.0.0.1"),
                  ("no scheme", "lexis.example.com")]:
    extra = dict(PROD)
    if url is None:
        del extra["PUBLIC_SITE_URL"]
    else:
        extra["PUBLIC_SITE_URL"] = url
    res = start(**extra)
    check(f"MAIL-04 production with PUBLIC_SITE_URL {name}: refused", refused(res, "PUBLIC_SITE_URL must be set"),
          res.get("error") or res.get("crash"))

# ---- A good production setup, and development ---------------------------------
prod = start(**PROD)
check("production with a long key and an https address starts", prod.get("started") is True, prod.get("error") or prod.get("crash"))
check("SEC-12 production: /docs, /redoc and /openapi.json are not there (404)", prod.get("docs") == [404, 404, 404], prod.get("docs"))
check("CORS: no other site allowed by default (localhost:5500 gets no allow header)", prod.get("cors") is None, prod.get("cors"))

dev = start(ENVIRONMENT="development")
check("development starts with the default key and address", dev.get("started") is True, dev.get("error") or dev.get("crash"))
check("SEC-12 development: the API pages open", dev.get("docs") == [200, 200, 200], dev.get("docs"))

# ---- PRIV-02 and SEC-08: SQL printing and query values -----------------------------
for label, res in [("production", prod), ("development", dev)]:
    check(f"PRIV-02 {label}: SQL is not printed (SQL_ECHO off by default)",
          res.get("echo") is False and "SELECT" not in res.get("output", ""), res.get("echo"))
    check(f"SEC-08 {label}: a database error doesn't show the values sent",
          "secret message text" not in res.get("output", "") and "hidden" in res.get("db_error", ""), res.get("db_error"))

echo = start(ENVIRONMENT="development", SQL_ECHO="true")
check("PRIV-02 SQL_ECHO=true prints the SQL (for debugging)", echo.get("echo") is True and "SELECT" in echo.get("output", ""),
      echo.get("echo"))
check("SEC-08 ...but still never the values", echo.get("hide_parameters") is True and "secret message text" not in echo.get("output", ""),
      echo.get("db_error"))

# ---- PRIV-01: the access log drops the query string ------------------------------
# Same filter object uvicorn's access logger uses in app/main.py. Checked here
# on a log line shaped like uvicorn's own (client, method, path, version, status).
_here = tempfile.mkdtemp(prefix="lexis-test-cwd-")
os.chdir(_here)  # an empty folder: no .env to read
for k in list(os.environ):
    if k.upper().startswith(SCRUB):
        del os.environ[k]
os.environ.update({"ENVIRONMENT": "development", "DATABASE_URL": "sqlite://"})
sys.path.insert(0, str(BACKEND))
from app.main import _NoQueryInAccessLog  # noqa: E402

access = logging.getLogger("uvicorn.access")
check("PRIV-01 the query-string filter is on uvicorn's access log",
      any(isinstance(f, _NoQueryInAccessLog) for f in access.filters), access.filters)
fmt = '%s - "%s %s HTTP/%s" %d'
for path, kept in [("/clients/lookup?email=b%40example.com", "/clients/lookup"),
                   ("/public/articles?limit=3&q=Juana+Dela+Cruz", "/public/articles"),
                   ("/requests", "/requests")]:
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, fmt, ("127.0.0.1:5000", "GET", path, "1.1", 200), None)
    _NoQueryInAccessLog().filter(rec)
    line = rec.getMessage()
    check(f"PRIV-01 access line for {path.split('?')[0]} keeps the path, drops the query",
          f'"GET {kept} HTTP/1.1" 200' in line and "?" not in line, line)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

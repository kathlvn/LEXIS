import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.storage import StorageError
from app.routers import requests as requests_router
from app.routers import auth as auth_router
from app.routers import staff as staff_router
from app.routers import documents as documents_router
from app.routers import messages as messages_router
from app.routers import public_access as public_access_router
from app.routers import holidays as holidays_router
from app.routers import service_types as service_types_router
from app.routers import reports as reports_router
from app.routers import archive as archive_router
from app.routers import notifications as notifications_router
from app.routers import security_log as security_log_router
from app.routers import portal as portal_router
from app.routers import clients as clients_router
from app.routers import offices as offices_router
from app.routers import system_settings as system_settings_router
from app.routers import articles as articles_router
from app.seed import seed as seed_defaults

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlmodel import Session

from app.core.database import engine
from app.core.security_monitoring import limiter, log_security_event, get_client_ip

@asynccontextmanager
async def lifespan(app):
    # Runs once when the server starts (before the first request) and once when
    # it stops (after yield). Replaces @app.on_event("startup"), which newer
    # FastAPI marks deprecated. on_startup() is defined further down.
    on_startup()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="LEXIS API",
    description="Legal Executive Information System — Eastern Samar State University",
    version="0.1.0",
    # The API pages (/docs, /redoc, /openapi.json) list every endpoint; only
    # for development, never on the real site.
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url="/redoc" if settings.environment == "development" else None,
    openapi_url="/openapi.json" if settings.environment == "development" else None,
)

app.state.limiter = limiter


class _NoQueryInAccessLog(logging.Filter):
    """Uvicorn's access log line normally includes the query string, e.g.
    /clients/lookup?email=..., which would keep client emails and searched
    names in lexis.log. The Privacy Notice promises only the time and network
    address, so the line keeps the path and drops everything after "?"."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_NoQueryInAccessLog())

# CORS: Caddy serves the pages and /api from one address, so by default no
# other site is allowed (cors_allowed_origins is empty). Set
# CORS_ALLOWED_ORIGINS in .env only if the pages ever move to another address.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Let the console read the list total (paging) and download file names.
    expose_headers=["X-Total-Count", "Content-Disposition"],
)


# ---------------------------------------------------------------------------
# Changes from other sites are refused. Browsers count every
# *.essu.edu.ph site as "the same site", so the SameSite cookie setting alone
# would let a weak page anywhere on ESSU act as a signed-in user. Every
# POST/PUT/PATCH/DELETE must come from LEXIS's own pages: the browser's
# Sec-Fetch-Site must be "same-origin" (or "none", typed by the user), and an
# Origin header, when sent, must be this site. Scripts and tests send neither
# header and carry no browser cookies, so they are not affected.
# ---------------------------------------------------------------------------
_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Body size: an 80 MB login body used 317 MB of memory. Refused from
# its Content-Length before it is read: uploads (multipart) up to 75 MB (one
# submission is at most 50 MB of files plus a 20 MB endorsement letter),
# everything else up to 1 MB. Caddy also caps every body at 75 MB, which
# covers bodies sent without a Content-Length.
MAX_UPLOAD_BODY = 75 * 1024 * 1024
MAX_OTHER_BODY = 1024 * 1024


@app.middleware("http")
async def refuse_oversized_bodies(request, call_next):
    if request.method in _CHANGING_METHODS:
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
        is_upload = (request.headers.get("content-type") or "").lower().startswith("multipart/form-data")
        if length > (MAX_UPLOAD_BODY if is_upload else MAX_OTHER_BODY):
            return JSONResponse(status_code=413, content={
                "detail": "What you sent is too large. Files can be up to 20 MB each and 50 MB together."})
    return await call_next(request)


CROSS_SITE_MESSAGE = "This request came from another website and was refused. Open LEXIS directly and try again."


def _own_hosts(request) -> set[str]:
    hosts = {urlparse(settings.public_site_url).netloc.lower()}
    if request.headers.get("host"):
        hosts.add(request.headers["host"].lower())
    return hosts


@app.middleware("http")
async def refuse_cross_site_changes(request, call_next):
    if request.method in _CHANGING_METHODS:
        fetch_site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        cross = fetch_site is not None and fetch_site not in ("same-origin", "none")
        if not cross and origin is not None:
            cross = urlparse(origin).netloc.lower() not in _own_hosts(request)  # includes Origin: null
        if cross:
            from sqlmodel import Session as _Session
            from app.core.database import engine
            from app.core.security_monitoring import get_client_ip, log_security_event
            try:
                with _Session(engine) as session:
                    log_security_event(
                        session, "cross_site_refused",
                        f"{request.method} {request.url.path} from {(origin or fetch_site)[:100]}",
                        get_client_ip(request),
                    )
            except Exception:
                logging.getLogger("lois.security").exception("Could not record a cross-site refusal.")
            return JSONResponse(status_code=403, content={"detail": CROSS_SITE_MESSAGE})
    return await call_next(request)

# Host check: LEXIS answers only to its own names. Any other Host
# header gets "Invalid host header" (400). Added last, so it runs first.
# "testserver" is the name the test scripts' TestClient uses; it can't be
# reached over a network, so allowing it opens nothing.
ALLOWED_HOSTS = sorted({
    "localhost", "127.0.0.1", "testserver",
    (urlparse(settings.public_site_url).hostname or "localhost").lower(),
    *(h.strip().lower() for h in settings.allowed_hosts if h.strip()),
})
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

app.include_router(requests_router.router)
app.include_router(auth_router.router)
app.include_router(staff_router.router)
app.include_router(documents_router.router)
app.include_router(messages_router.router)
app.include_router(public_access_router.router)
app.include_router(holidays_router.router)
app.include_router(service_types_router.router)
app.include_router(reports_router.router)
app.include_router(archive_router.router)
app.include_router(notifications_router.router)
app.include_router(security_log_router.router)
app.include_router(portal_router.router)
app.include_router(clients_router.router)
app.include_router(offices_router.router)
app.include_router(system_settings_router.router)
app.include_router(articles_router.router)
app.include_router(articles_router.public_router)
# The development default for SECRET_KEY is public (it's on GitHub). Outside
# development LEXIS refuses to start with it, because session and code hashes
# and the bot check are all keyed with it.
DEV_SECRET_KEY = "dev-secret-key-change-me"


def on_startup():
    # Called by lifespan() at the top of this file when the server starts.
    # No guessing: a missing or misspelt ENVIRONMENT stops LEXIS here.
    if settings.environment not in ("development", "production"):
        raise RuntimeError(
            'ENVIRONMENT must be set in .env to "development" or "production".'
        )
    if settings.environment != "development" and (
        settings.secret_key == DEV_SECRET_KEY or len(settings.secret_key) < 32
    ):
        raise RuntimeError(
            "SECRET_KEY must be set in .env to a long random value (32+ characters) outside development."
        )
    # Every email links to this address, so in production it must be the real
    # https site, never the localhost default (MAIL-04).
    site = urlparse(settings.public_site_url)
    if settings.environment == "production" and (
        site.scheme != "https" or site.hostname in (None, "localhost", "127.0.0.1", "::1")
    ):
        raise RuntimeError(
            "PUBLIC_SITE_URL must be set in .env to the site's real https address in production."
        )
    # Dev convenience only — see the note in core/database.py about
    # switching to Alembic migrations before this goes to production.
    seed_defaults()


@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.exception_handler(StorageError)
def storage_error_handler(request, exc):
    # Document storage (Google Drive) is down, over quota or misconfigured.
    # The details are already in the log; the message is safe to show.
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request, exc):
    with Session(engine) as session:
        log_security_event(
            session,
            "rate_limit_exceeded",
            f"{request.method} {request.url.path}",
            get_client_ip(request),
        )
    return _rate_limit_exceeded_handler(request, exc)


# Refused calls count too. @limiter.limit runs inside the endpoint,
# so a call FastAPI refuses before reaching it (bad input 422, not signed in
# 401, wrong role 403) was never counted: 20 bad logins all got 422, never 429.
# After the answer, a call to a limited endpoint that the limiter didn't see
# is counted against that endpoint's own limit; past it, the caller gets the
# usual 429 (logged by rate_limit_handler). Calls already counted are skipped,
# and so is a 429 the decorator gave itself (it refuses before marking the
# call as counted, so it would otherwise be counted and logged twice).
@app.middleware("http")
async def count_refused_calls(request, call_next):
    response = await call_next(request)
    endpoint = request.scope.get("endpoint")
    if (
        limiter.enabled
        and response.status_code != 429
        and endpoint is not None
        and not getattr(request.state, "_rate_limiting_complete", False)
        and f"{endpoint.__module__}.{endpoint.__name__}" in limiter._route_limits
    ):
        try:
            limiter._check_request_limit(request, endpoint, False)
        except RateLimitExceeded as exc:
            return rate_limit_handler(request, exc)
    return response


# ---------------------------------------------------------------------------
# Odd input gets a short, friendly answer instead of "Internal Server Error"
# (NUL characters, half an emoji, numbers too big for the database,
# NaN, very deep JSON, dates past the year 9999), and a record added twice at
# the same moment gets "already listed". Nothing is saved in these
# cases: the database refused it, so the save is rolled back.
# ---------------------------------------------------------------------------
from fastapi.exceptions import RequestValidationError  # noqa: E402
from sqlalchemy.exc import DataError, IntegrityError, StatementError  # noqa: E402

BAD_VALUE_MESSAGE = "Some of what was sent can't be used (an unusual character or a number or date out of range). Check it and try again."
_error_log = logging.getLogger("lois.errors")


@app.exception_handler(RequestValidationError)
def validation_error_handler(request, exc):
    # Same shape as FastAPI's own answer, without echoing the input back:
    # an echoed NaN or half emoji could not be sent as JSON.
    errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(IntegrityError)
def integrity_error_handler(request, exc):
    return JSONResponse(status_code=409, content={
        "detail": "This is already listed, or was just added by someone else. Reload the page and check."})


@app.exception_handler(DataError)
def data_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": BAD_VALUE_MESSAGE})


@app.exception_handler(StatementError)
def statement_error_handler(request, exc):
    if isinstance(getattr(exc, "orig", None), (UnicodeError, ValueError, OverflowError)):
        return JSONResponse(status_code=400, content={"detail": BAD_VALUE_MESSAGE})
    _error_log.exception("Database error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


@app.exception_handler(UnicodeError)
@app.exception_handler(OverflowError)
@app.exception_handler(RecursionError)
def bad_value_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": BAD_VALUE_MESSAGE})

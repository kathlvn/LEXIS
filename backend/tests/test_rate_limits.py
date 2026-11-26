r"""Checks that every API route has a rate limit, and that the limits added on
2026-11-03 (request list, status changes, staff downloads, staff messages and
the other routes that had none) answer 429 once used up. Plain Python, no
pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_rate_limits.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
The calls use a Request ID that doesn't exist: the limit is counted before the
route looks the request up, so a 404 uses up the limit like a real call.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.routing import APIRoute, iter_route_contexts  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.main as main  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import StaffUser  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    head = StaffUser(full_name="Test Head", email="head@essu.edu.ph", hashed_password="x", role="head_attorney")
    s.add(head)
    s.commit()
    HEAD_ID = head.id


def override_session():
    with Session(engine) as s:
        yield s


def override_staff():
    with Session(engine) as s:
        return s.get(StaffUser, HEAD_ID)


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


# 1. Every route has a limit (only /health is left out: a server monitor calls it).
def limit_of(route):
    limits = limiter._route_limits.get(f"{route.endpoint.__module__}.{route.endpoint.__name__}", [])
    return ", ".join(str(lim.limit) for lim in limits)


# FastAPI 0.13x+ keeps included routers whole in app.routes, so the routes
# are listed with iter_route_contexts (path, methods and endpoint of each).
routes = [r for r in iter_route_contexts(main.app.routes)
          if isinstance(r.original_route, APIRoute) and r.path != "/health"]
missing = [f"{sorted(r.methods)[0]} {r.path}" for r in routes if not limit_of(r)]
check(f"all {len(routes)} API routes have a rate limit", routes and not missing, missing or "no routes found")

# 2. The limits added today, by kind: reads 120, downloads 60, changes 30 a minute.
EXPECTED = {
    "app.routers.requests.list_requests": "120 per 1 minute",
    "app.routers.requests.get_request": "120 per 1 minute",
    "app.routers.messages.list_messages_staff": "120 per 1 minute",
    "app.routers.auth.me": "120 per 1 minute",
    "app.routers.holidays.list_holidays": "120 per 1 minute",
    "app.routers.service_types.list_for_head_attorney": "120 per 1 minute",
    "app.routers.staff.list_staff": "120 per 1 minute",
    "app.routers.staff.staff_directory": "120 per 1 minute",
    "app.routers.staff.get_staff": "120 per 1 minute",
    "app.routers.documents.download_document_staff": "60 per 1 minute",
    "app.routers.requests.update_status": "30 per 1 minute",
    "app.routers.requests.return_for_revision": "30 per 1 minute",
    "app.routers.requests.reassign_request": "30 per 1 minute",
    "app.routers.messages.send_message_staff": "30 per 1 minute",
    "app.routers.documents.update_document_status": "30 per 1 minute",
    "app.routers.documents.set_document_release": "30 per 1 minute",
    "app.routers.holidays.add_holiday": "30 per 1 minute",
    "app.routers.holidays.remove_holiday": "30 per 1 minute",
    "app.routers.service_types.add_service_type": "30 per 1 minute",
    "app.routers.service_types.update_service_type": "30 per 1 minute",
    "app.routers.staff.update_staff": "30 per 1 minute",
    "app.routers.auth.logout": "30 per 1 minute",
    "app.routers.portal.logout": "30 per 1 minute",
}
by_name = {f"{r.endpoint.__module__}.{r.endpoint.__name__}": limit_of(r) for r in routes}
wrong = {k: by_name.get(k) for k, v in EXPECTED.items() if by_name.get(k) != v}
check(f"the {len(EXPECTED)} new limits are the agreed numbers", not wrong, wrong)


# 3. Each kind really answers 429 once used up (and not one call earlier).
def used_up(name, limit, call, ok_code):
    limiter.reset()
    codes = [call().status_code for _ in range(limit + 1)]
    check(f"{name}: {limit} work, call {limit + 1} -> 429",
          codes[:limit] == [ok_code] * limit and codes[limit] == 429, sorted(set(codes[:limit])) + codes[limit:])


NOPE = "/requests/LEO-2026-999999"
used_up("request list", 120, lambda: client.get("/requests?limit=1"), 200)
used_up("one request", 120, lambda: client.get(NOPE), 404)
used_up("status change", 30, lambda: client.patch(f"{NOPE}/status", json={"note": "x"}), 404)
used_up("staff download", 60, lambda: client.get(f"{NOPE}/documents/1/download"), 404)
used_up("staff messages list", 120, lambda: client.get(f"{NOPE}/messages"), 404)
used_up("staff message send", 30, lambda: client.post(f"{NOPE}/messages", json={"body": "hi"}), 404)
used_up("signed-in check (/auth/me)", 120, lambda: client.get("/auth/me"), 200)

# 4. The 429 is logged as a security event, like every other limit.
from app.models.models import SecurityEvent  # noqa: E402
from sqlmodel import select  # noqa: E402
with Session(engine) as s:
    events = s.exec(select(SecurityEvent).where(SecurityEvent.event_type == "rate_limit_exceeded")).all()
check("each of the 7 429s is logged in the Security Log", len(events) == 7, len(events))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

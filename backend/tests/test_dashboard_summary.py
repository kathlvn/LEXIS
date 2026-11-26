r"""Checks for GET /requests/summary, the dashboard counts (checklist #13),
and that GET /requests still filters the same after its filters moved into
_filtered_requests. Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_dashboard_summary.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
Due dates are weeks in the past or future, so the checks don't depend on today.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.main as main  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import (  # noqa: E402
    LegalServiceRequest, Requester, ServiceType, StaffUser, WorkflowStepTemplate,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

NOW = datetime.now(timezone.utc).replace(tzinfo=None)  # stored times are naive UTC
PAST, FUTURE = NOW - timedelta(days=30), NOW + timedelta(days=60)

with Session(engine) as s:
    staff = {
        "head": StaffUser(full_name="Test Head", email="head@essu.edu.ph", hashed_password="x", role="head_attorney"),
        "atty": StaffUser(full_name="Test Attorney", email="atty@essu.edu.ph", hashed_password="x", role="attorney"),
        "la1": StaffUser(full_name="Assistant One", email="la1@essu.edu.ph", hashed_password="x", role="legal_assistant"),
        "la2": StaffUser(full_name="Assistant Two", email="la2@essu.edu.ph", hashed_password="x", role="legal_assistant"),
    }
    clients = {
        "pending": Requester(full_name="Pending Letter", email="a@x.ph", verification_status="pending"),
        "verified": Requester(full_name="Verified Client", email="b@x.ph", verification_status="verified",
                              verification_expires_at=NOW + timedelta(days=200)),
        "details_done": Requester(full_name="Unverified Done", email="c@x.ph", client_category="essu_office"),
        "expired": Requester(full_name="Expired Client", email="d@x.ph", verification_status="verified",
                             verification_expires_at=NOW - timedelta(days=1)),
        "no_details": Requester(full_name="Unverified Blank", email="e@x.ph"),
    }
    advice = ServiceType(name="Legal Advice", code="legal_advice", default_processing_days=2)
    for x in (*staff.values(), *clients.values(), advice):
        s.add(x)
    s.commit()
    ID = {k: v.id for k, v in staff.items()}
    step = {}
    for i, name in enumerate(["Submitted", "Under Review", "For Attorney Review", "Completed"], 1):
        w = WorkflowStepTemplate(service_type_id=advice.id, sequence_order=i, status_name=name)
        s.add(w); s.commit(); step[name] = w.id

    def add(n, status, client, officer=None, due=FUTURE, **flags):
        s.add(LegalServiceRequest(
            tracking_id=f"LEO-2026-{n:06d}", requester_id=clients[client].id, service_type_id=advice.id,
            description="test", current_step_id=step[status], submitted_at=datetime(2026, 10, n, 2),
            due_date=due, assigned_officer_id=ID[officer] if officer else None, **flags))
        s.commit()

    add(1, "Submitted", "pending")
    add(2, "Submitted", "verified")
    add(3, "Under Review", "details_done", "la1", due=PAST, is_waiting_for_client=True)
    add(4, "For Attorney Review", "expired", "la1", reassignment_requested=True)
    add(5, "Under Review", "no_details", "la2", due=PAST, is_waiting_for_client=True)
    add(6, "Completed", "verified", "la1", due=PAST)                   # finished: never due soon
    add(7, "Submitted", "verified", "la1", due=PAST, is_cancelled=True)  # cancelled: in no tile
    add(8, "Under Review", "verified", "la1")


def override_session():
    with Session(engine) as s:
        yield s


current = {"id": ID["head"]}


def override_staff():
    with Session(engine) as s:
        return s.get(StaffUser, current["id"])


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def list_total(params):
    r = client.get("/requests?limit=1&" + params)
    return int(r.headers["X-Total-Count"]) if r.status_code == 200 else f"HTTP {r.status_code}"


# The list filters each tile's queue uses (the old dashboard sent exactly these).
def queue_params(key, who):
    me = ID[who]
    return {
        "new": "status_name=Submitted",
        "assigned_to_me": "assigned_to_me=true&open_only=true",
        "my_awaiting_client": f"waiting_for_client=true&assigned_officer_id={me}",
        "my_due_soon": f"deadline=due_soon&assigned_officer_id={me}",
        "for_approval": "status_name=For%20Attorney%20Review",
        "awaiting_client": "waiting_for_client=true",
        "due_soon": "deadline=due_soon",
        "reassignment": "reassignment_requested=true",
        "client_waiting": "client_waiting=true",
    }[key]


EXPECTED = {
    "head": {"new": 2, "for_approval": 1, "awaiting_client": 2, "due_soon": 2, "reassignment": 1, "client_waiting": 3},
    "atty": {"new": 2, "for_approval": 1, "awaiting_client": 2, "due_soon": 2, "client_waiting": 3},
    "la1": {"new": 2, "assigned_to_me": 3, "my_awaiting_client": 1, "my_due_soon": 1},
    "la2": {"new": 2, "assigned_to_me": 1, "my_awaiting_client": 1, "my_due_soon": 1},
}

PARTS = ("overdue", "due_soon", "on_track", "no_period", "closed")


def summary():
    r = client.get("/requests/summary")
    return r.json() if r.status_code == 200 else r.status_code


# 1. Each role: the right tiles, the right counts, and the same as its queue.
#    Every tile's deadline split adds up to its count.
for who, expected in EXPECTED.items():
    current["id"] = ID[who]
    full = summary()
    got = {k: v["count"] for k, v in full.items()} if isinstance(full, dict) else full
    check(f"{who}: tiles and counts", got == expected, got)
    if isinstance(got, dict):
        queues = {k: list_total(queue_params(k, who)) for k in got}
        check(f"{who}: every count equals its queue's total", queues == got, queues)
        bad = {k: v for k, v in full.items() if sum(v[p] for p in PARTS) != v["count"]}
        check(f"{who}: every split adds up to its count", not bad, bad)

# 1b. The split itself (Head Attorney). Overdue = past due and still open.
current["id"] = ID["head"]
full = summary()
split = {k: tuple(v[p] for p in PARTS) for k, v in full.items()}
check("split: Overdue or due soon = 2 overdue", split["due_soon"] == (2, 0, 0, 0, 0), split["due_soon"])
check("split: New = 2 on track", split["new"] == (0, 0, 2, 0, 0), split["new"])
check("split: client waiting = 1 overdue, 2 on track", split["client_waiting"] == (1, 0, 2, 0, 0),
      split["client_waiting"])

# 2. GET /requests after the move: filters, total header and rows as before.
current["id"] = ID["head"]
check("list: all 8, open only 6", (list_total(""), list_total("open_only=true")) == (8, 6))
check("list: status Cancelled 1, Completed 1", (list_total("status_name=Cancelled"),
                                                 list_total("status_name=Completed")) == (1, 1))
check("list: overdue 2 (finished and cancelled left out)", list_total("deadline=overdue") == 2)
check("list: unassigned 2, Assistant One's 5", (list_total("assigned_officer_id=0"),
                                                 list_total(f"assigned_officer_id={ID['la1']}")) == (2, 5))
check("list: search by client name", list_total("q=Expired") == 1)
check("list: service and dates", (list_total("service_type_code=legal_advice"),
                                  list_total("submitted_from=2026-10-03&submitted_to=2026-10-05")) == (8, 3))
r = client.get("/requests?sort=oldest&limit=3&offset=1")
check("list: sort and page still work", [x["tracking_id"][-1] for x in r.json()] == ["2", "3", "4"]
      and r.headers["X-Total-Count"] == "8", r.text[:200])
current["id"] = ID["la1"]
check("list: assigned_to_me follows the caller", list_total("assigned_to_me=true") == 5)

# 3. "summary" is not taken for a Request ID, and a real ID still opens.
r = client.get("/requests/LEO-2026-000002")
check("request detail still opens", r.status_code == 200 and r.json().get("tracking_id") == "LEO-2026-000002",
      r.status_code)

# 4. Staff only: without a staff session -> 401.
del main.app.dependency_overrides[get_current_staff_user]
r = client.get("/requests/summary")
check("no staff session -> 401", r.status_code == 401, r.status_code)
main.app.dependency_overrides[get_current_staff_user] = override_staff

# 5. Rate limit: 60 a minute.
limiter.reset()
codes = [client.get("/requests/summary").status_code for _ in range(61)]
check("61st call in a minute -> 429", codes[:60] == [200] * 60 and codes[60] == 429, codes[-3:])
limiter.reset()

# 6. Due soon, no set period and closed parts (rows added last, so the list
#    totals above are unchanged): Assistant Two gets one due in an hour and
#    one with no set period; a completed request of the pending client.
with Session(engine) as s:
    clients = {k: s.merge(v) for k, v in clients.items()}   # attach to this session
    advice = s.merge(advice)
    add(9,"Under Review", "verified", "la2", due=NOW + timedelta(hours=1))
    add(10, "Under Review", "verified", "la2", due=None)
    add(11, "Completed", "pending", "la1", due=PAST)
current["id"] = ID["la2"]
full = summary()
mine = tuple(full["assigned_to_me"][p] for p in PARTS)
check("split: due in an hour = due soon; no due date = no set period", mine == (1, 1, 0, 1, 0), mine)
soon = tuple(full["my_due_soon"][p] for p in PARTS)
check("split: My overdue or due soon = 1 overdue, 1 due soon", soon == (1, 1, 0, 0, 0), soon)
check("split: due soon tile equals its queue", full["my_due_soon"]["count"] == list_total(queue_params("my_due_soon", "la2")))
current["id"] = ID["head"]
cw = tuple(summary()["client_waiting"][p] for p in PARTS)
check("split: a completed request counts as closed, never overdue", cw == (1, 0, 2, 0, 1), cw)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

r"""Checks for "Requests by Requesting Party" on GET /reports (checklist #6a). Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_report_by_party.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.main as main  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import CLIENT_CATEGORIES, LegalServiceRequest, Requester, ServiceType, StaffUser  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    staff = StaffUser(full_name="Test Assistant", email="la@essu.edu.ph", hashed_password="x", role="legal_assistant")
    st = ServiceType(name="Legal Advice", code="legal_advice")
    student = Requester(full_name="Now A Student", client_category="essu_student")
    agency = Requester(full_name="Agency Client", client_category="ext_government")
    unknown = Requester(full_name="Old Record")   # no category at all
    for x in (staff, st, student, agency, unknown):
        s.add(x)
    s.commit()
    STAFF_ID = staff.id
    n = [0]

    def add(client, snapshot=None, when=datetime(2026, 11, 10, 2, 0)):
        n[0] += 1
        s.add(LegalServiceRequest(tracking_id=f"LEO-2026-{n[0]:06d}", requester_id=client.id, service_type_id=st.id,
                                  description="test", client_category_at_submission=snapshot, submitted_at=when))

    add(student, "essu_office")
    add(student, "essu_office")
    add(student, "essu_employee")     # snapshot wins over the client's category today (essu_student)
    add(agency)                       # filed before snapshots: the client's saved category
    add(unknown)                      # nothing known: "Not stated"
    add(agency, "old_code")           # an unknown code is shown as it is, never dropped
    add(agency, "ext_private", when=datetime(2026, 10, 10, 2, 0))   # October: not in November
    s.commit()


def override_session():
    with Session(engine) as s:
        yield s


def override_staff():
    with Session(engine) as s:
        return s.get(StaffUser, STAFF_ID)


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


r = client.get("/reports?period=month&year=2026&month=11")
check("report loads", r.status_code == 200, r.status_code)
rows = r.json()["by_category"]
got = {row["party"]: row["count"] for row in rows}
L = CLIENT_CATEGORIES
check("all five categories listed in order, zeros included",
      [row["party"] for row in rows[:5]] == list(L.values()), [row["party"] for row in rows])
check("snapshot counted (ESSU office: 2)", got[L["essu_office"]] == 2, got)
check("snapshot wins over today's category (employee 1, student 0)",
      got[L["essu_employee"]] == 1 and got[L["essu_student"]] == 0, got)
check("no snapshot: client's saved category (government 1)", got[L["ext_government"]] == 1, got)
check("October request not counted (private 0)", got[L["ext_private"]] == 0, got)
check("unknown code kept as is", got.get("old_code") == 1, got)
check("nothing known: 'Not stated' last", rows[-1] == {"party": "Not stated", "count": 1, "share": 16.7}, rows[-1])
check("counts add up to the requests received", sum(got.values()) == r.json()["summary"]["received"] == 6,
      (sum(got.values()), r.json()["summary"]["received"]))
check("share of received (ESSU office 2 of 6 = 33.3%)",
      next(row for row in rows if row["party"] == L["essu_office"])["share"] == 33.3, rows)

r = client.get("/reports?period=month&year=2026&month=9").json()["by_category"]
check("empty period: five zero rows, no share, no 'Not stated'",
      len(r) == 5 and all(row["count"] == 0 and row["share"] is None for row in r), r)

# Rate limit: 30 loads a minute.
limiter.reset()
codes = [client.get("/reports?period=month&year=2026&month=11").status_code for _ in range(31)]
check("31st load in a minute -> 429", codes[:30] == [200] * 30 and codes[30] == 429, codes[-3:])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

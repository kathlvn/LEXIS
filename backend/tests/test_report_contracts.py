r"""Checks for "Contract Reviews" on GET /reports (checklist #6c). Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_report_contracts.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
Due dates are far in the past or future, so the checks don't depend on today.
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
from app.models.models import (  # noqa: E402
    AuditLogEntry, LegalServiceRequest, Requester, ServiceType, StaffUser, WorkflowStepTemplate,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

PAST, FUTURE = datetime(2026, 9, 20, 9), datetime(2099, 1, 1, 9)

with Session(engine) as s:
    staff = StaffUser(full_name="Test Assistant", email="la@essu.edu.ph", hashed_password="x", role="legal_assistant")
    contract = ServiceType(name="Review of MOA/MOU, Contracts, and Similar Documents", code="contract_review",
                           default_processing_days=6)
    advice = ServiceType(name="Legal Advice", code="legal_advice", default_processing_days=2)
    cl = Requester(full_name="Test Client")
    for x in (staff, contract, advice, cl):
        s.add(x)
    s.commit()
    STAFF_ID = staff.id
    steps = {}
    for st in (contract, advice):
        for i, name in enumerate(["Submitted", "Under Review", "Processing", "Completed"], 1):
            w = WorkflowStepTemplate(service_type_id=st.id, sequence_order=i, status_name=name)
            s.add(w); s.commit(); steps[(st.id, name)] = w.id

    def add(n, filed, status, due=FUTURE, st=contract, completed=None, **extra):
        r = LegalServiceRequest(tracking_id=f"LEO-2026-{n:06d}", requester_id=cl.id, service_type_id=st.id,
                                description="test", current_step_id=steps[(st.id, status)], submitted_at=filed,
                                due_date=due, last_updated_at=filed, **extra)
        s.add(r); s.commit()
        if completed:
            s.add(AuditLogEntry(request_id=r.id, action="status_change", created_at=completed,
                                detail="Status changed from Processing to Completed."))
            s.commit()

    add(1, datetime(2026, 11, 2, 1), "Under Review", client_office_at_submission="Office of the President")
    add(2, datetime(2026, 11, 5, 1), "Completed", completed=datetime(2026, 11, 10, 2))
    add(3, datetime(2026, 9, 10, 1), "Processing", due=PAST)                  # filed earlier, still open, overdue
    add(4, datetime(2026, 9, 10, 1), "Completed", completed=datetime(2026, 9, 30, 2))   # filed earlier, done
    add(5, datetime(2026, 9, 12, 1), "Under Review", is_cancelled=True)       # filed earlier, cancelled
    add(6, datetime(2026, 11, 7, 1), "Under Review", due=PAST, is_rejected=True)        # closed: never overdue
    add(7, datetime(2026, 12, 2, 1), "Submitted")                             # filed after November
    add(8, datetime(2026, 11, 3, 1), "Submitted", st=advice)                  # another service


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
rows = r.json()["contracts"]
ids = [x["tracking_id"][-1] for x in rows]
row = {x["tracking_id"][-1]: x for x in rows}
check("November: filed in November + filed earlier and still open, by date filed", ids == ["3", "1", "2", "6"], ids)
check("filed earlier and done / cancelled: left out", "4" not in row and "5" not in row)
check("filed after the period: left out", "7" not in row)
check("other services: left out", "8" not in row)
check("carried-over request marked 'filed earlier'", row["3"]["filed_earlier"] is True and row["1"]["filed_earlier"] is False)
check("open and past due: overdue", row["3"]["overdue"] is True and row["3"]["status"] == "Processing", row.get("3"))
check("rejected: shown as Rejected, never overdue", row["6"]["status"] == "Rejected" and row["6"]["overdue"] is False,
      row.get("6"))
check("completed: date completed from the history", row["2"]["completed_on"] == "2026-11-10"
      and row["2"]["status"] == "Completed", row.get("2"))
check("open: no completion date", row["1"]["completed_on"] is None, row.get("1"))
check("office as filed", row["1"]["office"] == "Office of the President" and row["2"]["office"] == "Not stated")
check("due date as a Philippine date", row["1"]["due_on"] == "2099-01-01", row.get("1"))

r = client.get("/reports?period=month&year=2026&month=10").json()["contracts"]
check("October: only the September request still open", [x["tracking_id"][-1] for x in r] == ["3"], r)
r = client.get("/reports?period=month&year=2026&month=9").json()["contracts"]
check("September: everything filed in September, open or not",
      sorted(x["tracking_id"][-1] for x in r) == ["3", "4", "5"], r)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

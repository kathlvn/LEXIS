r"""Checks for "Accomplished Requests" on GET /reports (checklist #6b). Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_report_accomplished.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.main as main  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.models.models import (  # noqa: E402
    AuditLogEntry, Holiday, LegalServiceRequest, Requester, ServiceType, StaffUser, WorkflowStepTemplate,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    staff = StaffUser(full_name="Test Assistant", email="la@essu.edu.ph", hashed_password="x", role="legal_assistant")
    advice = ServiceType(name="Legal Advice", code="legal_advice", default_processing_days=2)
    research = ServiceType(name="Legal Research", code="legal_research", default_processing_days=None)
    cl = Requester(full_name="Test Client")
    for x in (staff, advice, research, cl):
        s.add(x)
    s.add(Holiday(holiday_date=date(2026, 10, 30), name="Test holiday", holiday_type="special"))
    s.commit()
    STAFF_ID = staff.id
    steps = {}
    for st in (advice, research):
        for i, name in enumerate(["Submitted", "Under Review", "Completed"], 1):
            w = WorkflowStepTemplate(service_type_id=st.id, sequence_order=i, status_name=name)
            s.add(w); s.commit(); steps[(st.id, name)] = w.id

    def add(tid, st, filed, status="Completed", completions=(), reopened_after=None, updated=None, **extra):
        r = LegalServiceRequest(tracking_id=tid, requester_id=cl.id, service_type_id=st.id, description="test",
                                current_step_id=steps[(st.id, status)], submitted_at=filed,
                                last_updated_at=updated or filed, **extra)
        s.add(r); s.commit()
        for when in completions:
            s.add(AuditLogEntry(request_id=r.id, action="status_change", created_at=when,
                                detail="Status changed from Under Review to Completed."))
        if reopened_after:
            s.add(AuditLogEntry(request_id=r.id, action="status_change", created_at=reopened_after,
                                detail="Status changed from Completed to Under Review."))
        s.commit()

    # Times are UTC, as stored; the Philippines is UTC+8.
    add("LEO-2026-000001", advice, datetime(2026, 10, 28, 1), completions=[datetime(2026, 11, 3, 2)],
        client_office_at_submission="College of Law")          # filed in October, completed in November
    add("LEO-2026-000002", advice, datetime(2026, 11, 2, 1), completions=[datetime(2026, 11, 3, 3)])
    add("LEO-2026-000003", research, datetime(2026, 11, 2, 1), completions=[datetime(2026, 11, 10, 2)])
    add("LEO-2026-000004", advice, datetime(2026, 10, 1, 1), completions=[datetime(2026, 10, 20, 2)])  # October
    add("LEO-2026-000005", advice, datetime(2026, 11, 2, 1), status="Under Review",
        completions=[datetime(2026, 11, 4, 2)], reopened_after=datetime(2026, 11, 5, 2))            # reopened
    add("LEO-2026-000006", advice, datetime(2026, 11, 2, 1), completions=[datetime(2026, 11, 4, 2)],
        is_cancelled=True)                                                                          # cancelled
    add("LEO-2026-000007", advice, datetime(2026, 10, 1, 1),
        completions=[datetime(2026, 10, 15, 2), datetime(2026, 11, 5, 2)])                          # completed twice
    add("LEO-2026-000008", advice, datetime(2026, 11, 2, 1), updated=datetime(2026, 11, 7, 2))      # no history entry
    add("LEO-2026-000009", advice, datetime(2026, 10, 29, 1), completions=[datetime(2026, 10, 31, 17)])  # Nov 1, 1 AM PH


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
rows = r.json()["accomplished"]
ids = [x["tracking_id"][-1] for x in rows]
row = {x["tracking_id"][-1]: x for x in rows}
check("completed in November, sorted by date completed",
      ids == ["9", "1", "2", "7", "8", "3"], ids)
check("filed in October, completed in November: included", "1" in row)
check("completed in October: left out", "4" not in row)
check("reopened since: left out", "5" not in row)
check("cancelled: left out", "6" not in row)
check("completed twice: the last completion counts (Nov 5)", row["7"]["completed_on"] == "2026-11-05", row.get("7"))
check("no history entry: last update used (Nov 7)", row["8"]["completed_on"] == "2026-11-07", row.get("8"))
check("1 AM Philippine time on Nov 1 counts in November", row["9"]["completed_on"] == "2026-11-01", row.get("9"))
check("working days skip weekends and the Oct 30 holiday (3)", row["1"]["working_days"] == 3, row.get("1"))
check("3 days for a 2-day service: over the period", row["1"]["within_period"] is False, row.get("1"))
check("1 day for a 2-day service: within", row["2"]["working_days"] == 1 and row["2"]["within_period"] is True,
      row.get("2"))
check("no set period: neither within nor over", row["3"]["within_period"] is None and row["3"]["processing_days"] is None,
      row.get("3"))
check("office as filed", row["1"]["office"] == "College of Law" and row["2"]["office"] == "Not stated",
      (row["1"]["office"], row["2"]["office"]))
check("dates are Philippine calendar dates", row["1"]["submitted_on"] == "2026-10-28", row.get("1"))

# The shared completion-date helper also feeds "Avg. working days to complete"
# (requests FILED in November): #2 took 1 day, #8 took 4 (Nov 2 -> Sat Nov 7).
advice_row = next(x for x in r.json()["by_service"] if x["service"] == "Legal Advice")
check("average processing time still uses the same completion dates (2.5)",
      advice_row["avg_working_days"] == 2.5, advice_row)

r = client.get("/reports?period=month&year=2026&month=10").json()["accomplished"]
check("October list: only the October completion", [x["tracking_id"][-1] for x in r] == ["4"], r)
r = client.get("/reports?period=month&year=2026&month=12").json()["accomplished"]
check("empty period: empty list", r == [], r)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

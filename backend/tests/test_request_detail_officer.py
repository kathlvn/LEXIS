r"""Checks the officer fields Request Detail reads (GET /requests/{id}):
assigned_officer_id, used for "(you)", the read-only notice for legal
assistants, and "Assigned now" in the reassign list. Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_request_detail_officer.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.models.models import (  # noqa: E402
    LegalServiceRequest, Requester, ServiceType, StaffUser, WorkflowStepTemplate,
)
from app.routers.requests import _to_out  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)

failures = 0


def check(label, actual, expected):
    global failures
    ok = actual == expected
    failures += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f": got {actual!r}, expected {expected!r}"))


NOW = datetime(2026, 11, 2, 1, 0)

with Session(engine) as s:
    advice = ServiceType(name="Legal Advice", code="legal_advice", default_processing_days=2)
    client = Requester(full_name="Test Client", email="client@example.com")
    officer = StaffUser(full_name="Assistant One", email="la1@essu.edu.ph", hashed_password="x", role="legal_assistant")
    s.add(advice); s.add(client); s.add(officer); s.commit()
    step = WorkflowStepTemplate(service_type_id=advice.id, sequence_order=1, status_name="Submitted")
    s.add(step); s.commit()

    def make(tracking_id, officer_id):
        req = LegalServiceRequest(
            tracking_id=tracking_id, service_type_id=advice.id, requester_id=client.id,
            request_type="new", description="Test.", submitted_at=NOW, last_updated_at=NOW,
            current_step_id=step.id, assigned_officer_id=officer_id,
        )
        s.add(req); s.commit(); s.refresh(req)
        return req

    assigned = _to_out(s, make("LEO-2026-000001", officer.id))
    check("assigned request: officer id", assigned.assigned_officer_id, officer.id)
    check("assigned request: officer name", assigned.assigned_officer_name, "Assistant One")

    unassigned = _to_out(s, make("LEO-2026-000002", None))
    check("unassigned request: no officer id", unassigned.assigned_officer_id, None)
    check("unassigned request: no officer name", unassigned.assigned_officer_name, None)

print("\nAll passed." if not failures else f"\n{failures} FAILED.")
sys.exit(1 if failures else 0)

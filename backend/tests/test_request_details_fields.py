r"""Checks the two fields Request Details reads: when the request reached its
current status, and the service's working days. Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_request_details_fields.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.models.models import (  # noqa: E402
    AuditLogEntry, LegalServiceRequest, Requester, ServiceType, WorkflowStepTemplate,
)
from app.routers.requests import _to_public_out  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)

failures = 0


def check(label, actual, expected):
    global failures
    ok = actual == expected
    failures += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f": got {actual!r}, expected {expected!r}"))


SUBMITTED = datetime(2026, 11, 1, 5, 37)
FIRST_CHANGE = datetime(2026, 11, 2, 1, 0)
LAST_CHANGE = datetime(2026, 11, 3, 1, 0)

with Session(engine) as s:
    advice = ServiceType(name="Legal Advice", code="legal_advice", default_processing_days=2)
    research = ServiceType(name="Legal Research", code="legal_research", default_processing_days=None)
    client = Requester(full_name="Test Client")
    s.add(advice); s.add(research); s.add(client); s.commit()
    step = WorkflowStepTemplate(service_type_id=advice.id, sequence_order=1, status_name="Submitted")
    s.add(step); s.commit()

    new_request = LegalServiceRequest(
        tracking_id="LEO-2026-000001", service_type_id=advice.id, requester_id=client.id,
        request_type="new", description="Test.", submitted_at=SUBMITTED,
        last_updated_at=SUBMITTED, current_step_id=step.id,
    )
    moved = LegalServiceRequest(
        tracking_id="LEO-2026-000002", service_type_id=research.id, requester_id=client.id,
        request_type="new", description="Test.", submitted_at=SUBMITTED,
        last_updated_at=LAST_CHANGE, current_step_id=step.id,
    )
    s.add(new_request); s.add(moved); s.commit()

    # Written out of order, with a newer non-status entry that must not count.
    for action, when, detail in [
        ("status_change", LAST_CHANGE, "Status changed from Received to Under Review."),
        ("status_change", FIRST_CHANGE, "Status changed from Submitted to Received."),
        ("reminder_sent", datetime(2026, 11, 4, 1, 0), "Reminder."),
    ]:
        s.add(AuditLogEntry(request_id=moved.id, action=action, detail=detail,
                            actor_type="staff", created_at=when))
    s.commit()

    out_new = _to_public_out(s, new_request)
    out_moved = _to_public_out(s, moved)

check("no status change yet: the date is the submission date", out_new.current_status_since, SUBMITTED)
check("the newest status change wins, other entries don't count", out_moved.current_status_since, LAST_CHANGE)
check("a service with a set period sends its days", out_new.processing_days, 2)
check("a service with no set period sends None", out_moved.processing_days, None)

print("\nAll passed." if not failures else f"\n{failures} FAILED.")
sys.exit(1 if failures else 0)

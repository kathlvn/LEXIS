r"""Checks what the client's "Handled By" line on request.html receives.
Decided 2026-11-06: the client sees the name of the staff member handling their
request. Only the name: no other staff details.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_public_request_handled_by.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.main as main_module  # noqa: E402
from app.models.models import LegalServiceRequest, Requester, ServiceType, StaffUser  # noqa: E402
from app.routers.requests import _to_public_out  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main_module.engine = engine   # So nothing here can reach the real database.

failures = 0


def check(label, actual, expected):
    global failures
    ok = actual == expected
    failures += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f": got {actual!r}, expected {expected!r}"))


with Session(engine) as s:
    first = StaffUser(full_name="First Assistant", email="first@example.com", hashed_password="x", role="legal_assistant")
    second = StaffUser(full_name="Second Assistant", email="second@example.com", hashed_password="x", role="legal_assistant")
    client = Requester(full_name="A Client", email="client@example.com")
    service = ServiceType(name="Legal Advice", code="legal_advice", default_processing_days=2)
    s.add_all([first, second, client, service])
    s.commit()

    req = LegalServiceRequest(tracking_id="LEO-TEST-000001", requester_id=client.id,
                              service_type_id=service.id, description="Test request")
    s.add(req)
    s.commit()

    check("no one assigned: no name (the page says Not yet assigned)",
          _to_public_out(s, req).assigned_officer_name, None)

    req.assigned_officer_id = first.id
    s.add(req)
    s.commit()
    answer = _to_public_out(s, req)
    check("assigned: the client gets the staff member's name", answer.assigned_officer_name, "First Assistant")
    check("the staff member's email is not sent", "first@example.com" in answer.model_dump_json(), False)

    req.assigned_officer_id = second.id
    s.add(req)
    s.commit()
    check("reassigned: the client gets the new name",
          _to_public_out(s, req).assigned_officer_name, "Second Assistant")

print("All passed." if not failures else f"{failures} check(s) failed.")
sys.exit(1 if failures else 0)

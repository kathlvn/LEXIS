r"""Checks the last lines of the update email: an open request points the client
to its Messages section; a closed one (cancelled, rejected or withdrawn) doesn't,
since it no longer accepts messages. Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_update_email_footer.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched,
and no email is sent (the sender is replaced by a recorder).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi import Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.main as main  # noqa: E402
from app.core import verification  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.requester_access import get_current_client, get_verified_request_for_path  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import (  # noqa: E402
    Document, LegalServiceRequest, Requester, ServiceType, StaffUser, WorkflowStepTemplate,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

IDS = {"cancel": "LEO-2026-000001", "reject": "LEO-2026-000002",
       "withdraw": "LEO-2026-000003", "open": "LEO-2026-000004"}
with Session(engine) as s:
    st = ServiceType(name="Legal Advice", code="legal_advice")
    c = Requester(full_name="Client", email="client@example.com")
    head = StaffUser(full_name="Head", email="head@essu.edu.ph", hashed_password="x", role="head_attorney")
    s.add_all([st, c, head])
    s.commit()
    step = WorkflowStepTemplate(service_type_id=st.id, sequence_order=1, status_name="Submitted")
    s.add(step)
    s.commit()
    for tid in IDS.values():
        r = LegalServiceRequest(tracking_id=tid, requester_id=c.id, service_type_id=st.id, description="x",
                                current_step_id=step.id, assigned_officer_id=head.id)
        s.add(r)
        s.commit()
        s.add(Document(request_id=r.id, category="legal_office", original_filename="Opinion.pdf",
                       storage_key=tid, status="submitted"))
    s.commit()


def override_session():
    with Session(engine) as s:
        yield s


def override_request(tracking_id: str, s: Session = Depends(get_session)):
    return s.exec(select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)).one()


main.app.dependency_overrides.update({
    get_session: override_session,
    get_current_staff_user: lambda s=Depends(get_session): s.exec(select(StaffUser)).one(),
    get_current_client: lambda s=Depends(get_session): s.exec(select(Requester)).one(),
    get_verified_request_for_path: override_request,
})
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run
limiter.enabled = False
bodies = []
verification.email_is_configured = lambda: True
verification.send_email = lambda to, subject, body, *a, **k: bodies.append(body)

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


OPEN = "use the Messages section of your request in My Requests."
CLOSED = "This request is closed, so it no longer accepts messages."


def expect(name, r, closed):
    """Exactly one email, ending with the right lines."""
    body = bodies[-1] if bodies else ""
    if closed:
        right = CLOSED in body and "Messages section" not in body
    else:
        right = OPEN in body and CLOSED not in body
    check(name, r.status_code == 200 and len(bodies) == 1 and right and body.endswith("ESSU Legal Office"),
          f"{r.status_code} {r.text[:200]} {bodies}")
    bodies.clear()
    return body


def release(tid):
    with Session(engine) as s:
        doc = s.exec(select(Document).where(Document.storage_key == tid)).one().id
    return client.patch(f"/requests/{tid}/documents/{doc}/release", json={"released": True})


def message(tid):
    return client.post(f"/requests/{tid}/messages", data={"body": "Hello"})


# --- Open request: unchanged ---------------------------------------------------------------
expect("open: document released", release(IDS["open"]), closed=False)
body = expect("open: staff message", message(IDS["open"]), closed=False)
check("open: the message notice invites a reply", "read it and reply." in body, body)

# --- Closed requests -----------------------------------------------------------------------
for action in ("cancel", "reject"):
    expect(f"{action}: the closing email",
           client.post(f"/requests/{IDS[action]}/close", json={"action": action, "reason": "Duplicate."}), closed=True)
expect("withdraw: the email",
       client.post(f"/portal/requests/{IDS['withdraw']}/withdraw", json={"reason": ""}), closed=True)
for name in ("cancel", "reject", "withdraw"):
    expect(f"{name}: document released later", release(IDS[name]), closed=True)
    body = expect(f"{name}: staff message later", message(IDS[name]), closed=True)
    check(f"{name}: the message notice doesn't invite a reply",
          "Open your request to read it." in body and "reply" not in body, body)

# --- Reopened: back to the normal lines ------------------------------------------------------
expect("reopened: the normal lines again",
       client.post(f"/requests/{IDS['cancel']}/reopen", json={"reason": "By mistake."}), closed=False)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

r"""Checks for "Request revision" with what to correct (manuscript Appendix D):
the text goes into the note to the client and the email, and the request is
marked as waiting for the client. Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_revision_note.py
Uses a throwaway in-memory SQLite database: the real Postgres is never
touched, and no email is sent.
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
from app.core.requester_access import get_verified_request_for_path  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import (  # noqa: E402
    AuditLogEntry, Document, LegalServiceRequest, Requester, ServiceType, StaffUser, WorkflowStepTemplate,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

T, CLOSED, PHONE = "LEO-2026-000001", "LEO-2026-000002", "LEO-2026-000003"
with Session(engine) as s:
    st = ServiceType(name="Review", code="contract_review")
    c, phone = Requester(full_name="Client", email="client@example.com"), Requester(full_name="Phone", phone="0900")
    la = StaffUser(full_name="Assistant One", email="la1@essu.edu.ph", hashed_password="x", role="legal_assistant")
    la2 = StaffUser(full_name="Assistant Two", email="la2@essu.edu.ph", hashed_password="x", role="legal_assistant")
    s.add_all([st, c, phone, la, la2])
    s.commit()
    step = WorkflowStepTemplate(service_type_id=st.id, sequence_order=1, status_name="Under Review")
    s.add(step)
    s.commit()
    for tid, owner, extra in [(T, c, {}), (CLOSED, c, {"is_cancelled": True}), (PHONE, phone, {})]:
        s.add(LegalServiceRequest(tracking_id=tid, requester_id=owner.id, service_type_id=st.id, description="x",
                                  current_step_id=step.id, assigned_officer_id=la.id, **extra))
    s.commit()
    LA, LA2 = la.id, la2.id
acting = {"id": LA}


def override_session():
    with Session(engine) as s:
        yield s


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = lambda s=Depends(get_session): s.get(StaffUser, acting["id"])
main.app.dependency_overrides[get_verified_request_for_path] = lambda tracking_id, s=Depends(get_session): s.exec(
    select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)).first()
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run
limiter.enabled = False
emails = []
verification._send_request_update_email = lambda to, tid, lines, is_closed: emails.append((to, tid, lines))

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def add_doc(tid, name="MOA.pdf", category="client_submitted"):
    with Session(engine) as s:
        doc = Document(request_id=get(tid).id, category=category, original_filename=name, storage_key=f"k-{name}")
        s.add(doc)
        s.commit()
        return doc.id


def get(tid):
    with Session(engine) as s:
        return s.exec(select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tid)).first()


def status_of(doc_id):
    with Session(engine) as s:
        return s.get(Document, doc_id).status


def log(tid):
    with Session(engine) as s:
        return [(e.action, e.detail) for e in s.exec(
            select(AuditLogEntry).where(AuditLogEntry.request_id == get(tid).id).order_by(AuditLogEntry.id)).all()]


def ask(tid, doc_id, note=None, status="revision_required"):
    body = {"status": status}
    if note is not None:
        body["note"] = note
    return client.patch(f"/requests/{tid}/documents/{doc_id}", json=body)


# --- What to correct is required ---------------------------------------------------
first = add_doc(T)
for label, note in [("no text", None), ("empty", ""), ("only spaces", "   ")]:
    r = ask(T, first, note)
    check(f"{label}: refused", r.status_code == 400 and "State what" in r.json()["detail"], r.text)
check("over 1,000 characters: refused", ask(T, first, "x" * 1001).status_code == 422)
check("nothing saved, logged or emailed", (status_of(first), get(T).is_waiting_for_client, log(T), emails)
      == ("submitted", False, [], []))
acting["id"] = LA2
check("another assistant's request: 403", ask(T, first, "Fix it").status_code == 403)
acting["id"] = LA

# --- Asking -------------------------------------------------------------------------
r = ask(T, first, "  Correct the date on page 2.  ")
check("accepted", r.status_code == 200 and r.json()["status"] == "revision_required", r.text)
req = get(T)
check("note to the client", req.staff_note_to_client == '"MOA.pdf": Correct the date on page 2.', req.staff_note_to_client)
check("marked as waiting for the client", req.is_waiting_for_client is True)
check("History: document, waiting, note", log(T) == [
    ("document_status_changed", '"MOA.pdf" marked as revision required.'),
    ("waiting_for_client_change", "Marked as waiting for client."),
    ("client_note_change", 'Updated the note to client: ""MOA.pdf": Correct the date on page 2."')], log(T))
check("one email, to the client, with what to correct", len(emails) == 1 and emails[0][:2] == ("client@example.com", T)
      and '"MOA.pdf" needs to be revised' in emails[0][2] and '"Correct the date on page 2."' in emails[0][2], emails)
check("no staff name in the email", "Assistant" not in emails[0][2])
out = client.get(f"/portal/requests/{T}").json()
check("the client's page gets the note", out["is_waiting_for_client"] is True
      and out["staff_note_to_client"] == '"MOA.pdf": Correct the date on page 2.', out)
check("in the Awaiting client list",
      [x["tracking_id"] for x in client.get("/requests?waiting_for_client=true").json()] == [T])

# --- A second document: added below ----------------------------------------------------
second = add_doc(T, "Letter.pdf")
r = ask(T, second, "Sign it.")
check("added below the earlier note", get(T).staff_note_to_client
      == '"MOA.pdf": Correct the date on page 2.\n"Letter.pdf": Sign it.', get(T).staff_note_to_client)
check("still waiting, not logged twice", [a for a, _ in log(T)].count("waiting_for_client_change") == 1)
check("the second email has only the new text", len(emails) == 2 and "Sign it." in emails[1][2]
      and "page 2" not in emails[1][2], emails)
third = add_doc(T, "Big.pdf")
before = (log(T), len(emails), get(T).staff_note_to_client)
r = ask(T, third, "x" * 990)
check("together over 1,000: refused with the reason", r.status_code == 400 and "1,000 characters" in r.json()["detail"], r.text)
check("and nothing saved", (log(T), len(emails), get(T).staff_note_to_client) == before
      and status_of(third) == "submitted")

# --- Clearing, and a later request ------------------------------------------------------
r = ask(T, second, status="submitted")
check("clear revision needs no text and sends no email", r.status_code == 200 and len(emails) == 2, r.text)
check("clearing leaves the note and the waiting mark", get(T).is_waiting_for_client is True
      and "Letter.pdf" in get(T).staff_note_to_client)
client.patch(f"/requests/{T}/status", json={"is_waiting_for_client": False})
r = ask(T, third, "Use the new template.")
check("after the client responded: a fresh note", get(T).staff_note_to_client == '"Big.pdf": Use the new template.'
      and get(T).is_waiting_for_client is True, get(T).staff_note_to_client)
asked = len(emails)
r = ask(T, third)
check("already asked: no second email or note", r.status_code == 200 and len(emails) == asked
      and get(T).staff_note_to_client == '"Big.pdf": Use the new template.', r.text)
r = client.patch(f"/requests/{T}/status", json={"note": '"Big.pdf": Edited.'})
check("the note can still be edited on the Overview tab", get(T).staff_note_to_client == '"Big.pdf": Edited.', r.text)
r = client.post(f"/requests/{T}/remind")
check("Send reminder repeats it", r.status_code == 200 and "Edited." in emails[-1][2] and "Reminder" in emails[-1][2], r.text)

# --- Closed requests, clients without email, office documents -------------------------------
closed = add_doc(CLOSED, "Old.pdf")
r = ask(CLOSED, closed, "Fix it")
check("closed request: 409, nothing saved", r.status_code == 409 and status_of(closed) == "submitted"
      and log(CLOSED) == [] and get(CLOSED).is_waiting_for_client is False, r.text)
sent = len(emails)
phone_doc = add_doc(PHONE, "Phone.pdf")
r = ask(PHONE, phone_doc, "Bring a signed copy.")
check("client without email: saved, waiting, no email", r.status_code == 200 and get(PHONE).is_waiting_for_client
      and get(PHONE).staff_note_to_client == '"Phone.pdf": Bring a signed copy.' and len(emails) == sent, r.text)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

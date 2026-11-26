r"""Checks the email a staff message triggers (decided 2026-11-11, replacing
"email the message in full" of 2026-11-10): only a short notice, never the
message text or a staff name; at most one per request in 24 hours (counting
staff messages only); none for a phone-only client; a mail problem never
undoes the message. Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_message_email.py
Uses a throwaway in-memory SQLite database: the real Postgres is never
touched, and no email is sent (the sender is replaced by a recorder).
"""
import sys
from datetime import datetime, timedelta
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
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import (  # noqa: E402
    AuditLogEntry, LegalServiceRequest, Message, Requester, ServiceType, StaffUser, WorkflowStepTemplate,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    st = ServiceType(name="Legal Advice", code="legal_advice")
    online = Requester(full_name="Online Client", email="client@example.com")
    walk_in = Requester(full_name="Walk-in Client", phone="0917 000 0000")
    mine = StaffUser(full_name="Assistant One", email="la1@essu.edu.ph", hashed_password="x", role="legal_assistant")
    other = StaffUser(full_name="Assistant Two", email="la2@essu.edu.ph", hashed_password="x", role="legal_assistant")
    s.add_all([st, online, walk_in, mine, other]); s.commit()
    step = WorkflowStepTemplate(service_type_id=st.id, sequence_order=1, status_name="Submitted")
    s.add(step); s.commit()
    for tid, who in [("LEO-2026-000001", online), ("LEO-2026-000002", walk_in),
                     ("LEO-2026-000003", online), ("LEO-2026-000004", online)]:
        s.add(LegalServiceRequest(tracking_id=tid, requester_id=who.id, service_type_id=st.id,
                                  description="Private description.", current_step_id=step.id,
                                  assigned_officer_id=mine.id))
    s.commit()
    MINE, OTHER = mine.id, other.id

current = {"id": MINE}


def override_session():
    with Session(engine) as s:
        yield s


def override_staff(s: Session = Depends(get_session)):
    return s.get(StaffUser, current["id"])


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run
limiter.enabled = False

sent = []        # (to, tracking_id, lines) for each email the endpoint asked for
fail_mail = {"on": False}


def fake_send(destination_email, tracking_id, lines, is_closed):
    if fail_mail["on"]:
        raise RuntimeError("SMTP is down")
    sent.append((destination_email, tracking_id, lines))


verification._send_request_update_email = fake_send

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def post(tid, body):
    return client.post(f"/requests/{tid}/messages", data={"body": body})  # a form since item 1 (files)


def request_id(tid):
    with Session(engine) as s:
        return s.exec(select(LegalServiceRequest.id).where(LegalServiceRequest.tracking_id == tid)).first()


def saved_messages(tid):
    with Session(engine) as s:
        return s.exec(select(Message).where(Message.request_id == request_id(tid))).all()


def age_messages(tid, hours):
    """Moves every message on a request back in time, as if sent earlier."""
    with Session(engine) as s:
        for m in s.exec(select(Message).where(Message.request_id == request_id(tid))).all():
            m.sent_at = datetime.utcnow() - timedelta(hours=hours)
            s.add(m)
        s.commit()


NOTICE = "  - The Legal Office sent you a message. Open your request to read it and reply."

# 1. First message to a client with email: one notice, no message text.
body = "Please bring the original copy of the contract on Friday."
r = post("LEO-2026-000001", "  " + body + "  ")
check("send to an online client: 200", r.status_code == 200, r.text)
check("one email queued", len(sent) == 1, sent)
if sent:
    to, tid, lines = sent[0]
    check("sent to the client's email", to == "client@example.com", to)
    check("email names the Request ID", tid == "LEO-2026-000001", tid)
    check("email is only the notice line", lines == NOTICE, lines)
    check("the message text is not in the email", "original copy" not in lines)
    check("no staff name in the email", "Assistant One" not in lines)
    check("never the client's description", "Private description." not in lines)
check("the message is saved trimmed", saved_messages("LEO-2026-000001")[0].body == body)

# 2. More staff messages on the same request within 24 hours: saved, no email.
sent.clear()
r = post("LEO-2026-000001", "One more thing: bring a valid ID.")
check("second message within 24 h: 200", r.status_code == 200, r.text)
check("second message within 24 h: no email", sent == [], sent)
current["id"] = OTHER  # another staff member's message counts too (the Head Attorney could write)
with Session(engine) as s:
    s.get(LegalServiceRequest, request_id("LEO-2026-000001")).assigned_officer_id = OTHER
    s.commit()
r = post("LEO-2026-000001", "Message from another staff member.")
current["id"] = MINE
check("another staff member within 24 h: no email", r.status_code == 200 and sent == [], sent)
check("all three messages saved", len(saved_messages("LEO-2026-000001")) == 3)

# 3. A client's reply doesn't count as a staff message.
with Session(engine) as s:
    s.add(Message(request_id=request_id("LEO-2026-000004"), sender_type="requester", body="Hello?"))
    s.commit()
r = post("LEO-2026-000004", "Hello, how can we help?")
check("first staff message after a client reply: email", r.status_code == 200 and len(sent) == 1, sent)

# 4. The last staff message is over 24 hours old: a new notice.
sent.clear()
age_messages("LEO-2026-000004", 25)
r = post("LEO-2026-000004", "Following up on your request.")
check("staff message after 24 h: one email", r.status_code == 200 and len(sent) == 1, sent)
sent.clear()
age_messages("LEO-2026-000004", 23)
r = post("LEO-2026-000004", "And one more.")
check("staff message 23 h after the last: no email", r.status_code == 200 and sent == [], sent)

# 5. Phone-only client: the message is saved, no email.
sent.clear()
r = post("LEO-2026-000002", "We tried to call you.")
check("send to a phone-only client: 200", r.status_code == 200, r.text)
check("phone-only client: no email", sent == [], sent)
check("phone-only client: message saved", len(saved_messages("LEO-2026-000002")) == 1)

# 6. Not allowed or empty: no email, nothing saved.
sent.clear()
with Session(engine) as s:
    s.get(LegalServiceRequest, request_id("LEO-2026-000003")).assigned_officer_id = OTHER
    s.commit()
r = post("LEO-2026-000003", "Not my request.")
check("assistant not assigned: 403", r.status_code == 403, r.status_code)
with Session(engine) as s:
    s.get(LegalServiceRequest, request_id("LEO-2026-000003")).assigned_officer_id = MINE
    s.commit()
r = post("LEO-2026-000003", "    ")
check("blank message: 400", r.status_code == 400, r.status_code)
check("refused messages: no email", sent == [], sent)
check("refused messages: nothing saved", saved_messages("LEO-2026-000003") == [])

# 7. The mail server fails: the message and its History entry stay.
fail_mail["on"] = True
r = post("LEO-2026-000003", "Mail will fail for this one.")
fail_mail["on"] = False
check("mail failure: staff still get 200", r.status_code == 200, r.text)
check("mail failure: message kept", len(saved_messages("LEO-2026-000003")) == 1)
with Session(engine) as s:
    logged = s.exec(select(AuditLogEntry).where(AuditLogEntry.request_id == request_id("LEO-2026-000003"),
                                                AuditLogEntry.action == "message_sent")).all()
check("mail failure: History entry kept", len(logged) == 1)

print(f"\n{sum(results)}/{len(results)} passed.")
sys.exit(0 if all(results) else 1)

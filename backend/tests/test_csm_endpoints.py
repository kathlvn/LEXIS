r"""Checks for the client's CSM survey endpoints (checklist #5). Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_csm_endpoints.py
Uses a throwaway in-memory SQLite database: the real Postgres is never
touched. The client sign-in is replaced by a stand-in that opens only the
test client's own requests (same 404 for anyone else's, like the real one).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi import Depends, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.main as main  # noqa: E402
from app.core import csm_form  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.requester_access import get_verified_request_for_path  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import (  # noqa: E402
    CsmResponse, LegalServiceRequest, Requester, ServiceType, WorkflowStepTemplate,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    st = ServiceType(name="Legal Advice", code="legal_advice")
    me, other = Requester(full_name="Test Client"), Requester(full_name="Someone Else")
    s.add(st); s.add(me); s.add(other); s.commit()
    steps = {}
    for i, name in enumerate(["Submitted", "Under Review", "Completed"], 1):
        w = WorkflowStepTemplate(service_type_id=st.id, sequence_order=i, status_name=name)
        s.add(w); s.commit(); steps[name] = w.id

    def make(tid, owner, step, **extra):
        s.add(LegalServiceRequest(tracking_id=tid, requester_id=owner.id, service_type_id=st.id,
                                  description="test", current_step_id=steps[step], **extra))

    make("LEO-2026-000001", me, "Completed")
    make("LEO-2026-000002", me, "Under Review")
    make("LEO-2026-000003", me, "Completed", is_cancelled=True)
    make("LEO-2026-000004", other, "Completed")
    make("LEO-2026-000005", me, "Completed")   # for the rate-limit check
    make("LEO-2026-000006", me, "Completed")   # for the broken-file check
    s.commit()
    OWNER_ID = me.id


def override_session():
    with Session(engine) as s:
        yield s


def override_request(tracking_id: str, s: Session = Depends(get_session)):
    # Same session as the endpoint, like the real get_verified_request_for_path.
    req = s.exec(select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)).first()
    if not req or req.requester_id != OWNER_ID:
        raise HTTPException(status_code=404, detail="Request not found.")
    return req


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_verified_request_for_path] = override_request
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run
limiter.enabled = False        # switched back on for the rate-limit check at the end

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def url(n):
    return f"/portal/requests/LEO-2026-{n:06d}/csm"


FORM = csm_form.load_csm_form()
IDS = [q["id"] for sec in FORM["sections"] for q in sec["questions"]]


def full_answers(**changes):
    answers = {qid: ("NA" if qid in ("CC2", "CC3", "SQD5") else ("4" if qid == "CC1" else "5")) for qid in IDS}
    answers.update(changes)
    return answers


def post(n, answers=None, version=None, comment=None):
    return client.post(url(n), json={"form_version": version or FORM["version"],
                                     "answers": full_answers() if answers is None else answers,
                                     "comment": comment})


# --- What the client sees ---------------------------------------------------
r = client.get(url(2))
check("not completed: survey not offered", r.status_code == 200 and r.json()["status"] == "not_offered", r.text)
r = client.get(url(3))
check("cancelled: survey not offered", r.status_code == 200 and r.json()["status"] == "not_offered", r.text)
r = client.get(url(4))
check("someone else's request: 404", r.status_code == 404, r.status_code)
r = client.get(url(1))
body = r.json()
sent_ids = [q["id"] for sec in body["form"]["sections"] for q in sec["questions"]] if body.get("form") else []
check("completed: survey open with the file's 12 questions", body["status"] == "open" and sent_ids == IDS, body)
check("scoring rules are not sent to the client", "satisfied_values" not in r.text, r.text[:200])

# --- Refused submissions ------------------------------------------------------
r = post(2)
check("answer before Completed: 409", r.status_code == 409 and "completed" in r.json()["detail"], r.text)
answers = full_answers(); del answers["SQD3"]; del answers["CC1"]
r = post(1, answers)
check("missing answers: 400 naming them", r.status_code == 400 and "CC1, SQD3" in r.json()["detail"], r.text)
r = post(1, full_answers(SQD9="5"))
check("unknown question: 400", r.status_code == 400 and "do not match" in r.json()["detail"], r.text)
r = post(1, full_answers(SQD0="7"))
check("value not a choice: 400", r.status_code == 400 and "do not match" in r.json()["detail"], r.text)
r = post(1, full_answers(CC1="NA"))
check("N/A where the question has none: 400", r.status_code == 400, r.text)
r = post(1, version="old-version")
check("form changed while answering: 409", r.status_code == 409 and "reload" in r.json()["detail"], r.text)
r = post(1, comment="x" * 1001)
check("suggestion over 1000 characters: 400", r.status_code == 400 and "too long" in r.json()["detail"], r.text)
r = post(1, {f"Q{i}": "5" for i in range(51)})
check("more than 50 answers: refused by the schema (422)", r.status_code == 422, r.status_code)
r = post(1, full_answers(SQD0="5" * 11))
check("answer value over 10 characters: 422", r.status_code == 422, r.status_code)
r = post(4)
check("someone else's request: 404 on submit", r.status_code == 404, r.status_code)
with Session(engine) as s:
    check("nothing saved by the refused attempts", not s.exec(select(CsmResponse)).all())

# --- A good submission ----------------------------------------------------------
r = post(1, comment="  <b>Faster replies</b> please.  ")
check("valid answers saved", r.status_code == 200 and r.json()["status"] == "answered", r.text)
with Session(engine) as s:
    row = s.exec(select(CsmResponse)).one()
    check("one row, for the right request", row.request_id == 1, row.request_id)
    check("answers stored by question id", json.loads(row.answers) == full_answers(), row.answers)
    check("form version stored", row.form_version == FORM["version"], row.form_version)
    check("suggestion trimmed, stored as typed (escaped when shown)",
          row.comment == "<b>Faster replies</b> please.", repr(row.comment))
r = post(1)
check("second answer: 409", r.status_code == 409 and "already answered" in r.json()["detail"], r.text)
r = client.get(url(1))
check("page then shows 'answered' without the form",
      r.json()["status"] == "answered" and r.json()["form"] is None, r.text)

# --- A broken form file ---------------------------------------------------------
saved_path = csm_form.CSM_FORM_PATH
try:
    bad = Path(tempfile.mkdtemp()) / "csm_form.json"
    bad.write_text('{"version": ', encoding="utf-8")
    csm_form.CSM_FORM_PATH = bad
    csm_form._cache = None
    r = client.get(url(6))
    check("broken file: page told 'unavailable' (no error page)",
          r.status_code == 200 and r.json()["status"] == "unavailable", r.text)
    r = post(6)
    check("broken file: submit gets a friendly 503", r.status_code == 503 and "not available" in r.json()["detail"],
          r.text)
finally:
    csm_form.CSM_FORM_PATH = saved_path
    csm_form._cache = None

# --- Rate limit: 5 submissions a minute -------------------------------------------
limiter.enabled = True
limiter.reset()
codes = [post(5, answers={}).status_code for _ in range(6)]
check("6th submission in a minute: 429", codes[:5] == [400] * 5 and codes[5] == 429, codes)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

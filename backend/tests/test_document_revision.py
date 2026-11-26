r"""Checks for revised documents saved as new versions (manuscript Table 9,
Figure 4.4). Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_document_revision.py
Uses a throwaway in-memory SQLite database and a temporary storage folder:
the real Postgres and backend/storage are never touched, and no email is sent.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP_STORAGE = tempfile.mkdtemp(prefix="lexis-revisions-")
os.environ["LOCAL_STORAGE_DIR"] = TMP_STORAGE  # read when app.core.storage is imported
os.environ["STORAGE_BACKEND"] = "local"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi import Depends, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.main as main  # noqa: E402
from app.core import storage, verification  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.requester_access import get_verified_request_for_path  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import (  # noqa: E402
    AuditLogEntry, Document, LegalServiceRequest, Requester, ServiceRequirement, ServiceType, StaffNotification,
    StaffUser, WorkflowStepTemplate,
)

assert str(storage.STORAGE_ROOT) == TMP_STORAGE, "storage must point at the temp folder"

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    st = ServiceType(name="Review", code="contract_review")
    me, someone = Requester(full_name="Client", email="client@example.com"), Requester(full_name="Other")
    la = StaffUser(full_name="Assistant One", email="la1@essu.edu.ph", hashed_password="x", role="legal_assistant")
    s.add_all([st, me, someone, la]); s.commit()
    step = WorkflowStepTemplate(service_type_id=st.id, sequence_order=1, status_name="Under Review")
    item = ServiceRequirement(service_type_id=st.id, label="Copy of the MOA")
    s.add_all([step, item]); s.commit()
    for tid, owner, extra in [("LEO-2026-000001", me, {}), ("LEO-2026-000002", me, {"is_cancelled": True}),
                              ("LEO-2026-000003", someone, {})]:
        s.add(LegalServiceRequest(tracking_id=tid, requester_id=owner.id, service_type_id=st.id, description="x",
                                  current_step_id=step.id, assigned_officer_id=la.id, **extra))
    s.commit()
    ME, LA, ITEM = me.id, la.id, item.id


def override_session():
    with Session(engine) as s:
        yield s


def override_request(tracking_id: str, s: Session = Depends(get_session)):
    """Stands in for the client's cookie: only their own requests, else 404."""
    req = s.exec(select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)).first()
    if not req or req.requester_id != ME:
        raise HTTPException(status_code=404, detail="Request not found.")
    return req


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = lambda s=Depends(get_session): s.get(StaffUser, LA)
main.app.dependency_overrides[get_verified_request_for_path] = override_request
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run
limiter.enabled = False
verification._send_request_update_email = lambda *a: None

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


PDF = b"%PDF-1.4 test file"


def add_doc(tid, **kw):
    """A stored document of this request, as if uploaded earlier."""
    key = storage.generate_storage_key("a.pdf")
    storage.save_file(key, PDF)
    with Session(engine) as s:
        req = s.exec(select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tid)).first()
        doc = Document(**{"request_id": req.id, "category": "client_submitted", "original_filename": "MOA.pdf",
                          "storage_key": key, "status": "revision_required", **kw})
        s.add(doc); s.commit()
        return doc.id


def revise(tid, doc_id, name="MOA-fixed.pdf", content=b"%PDF-1.4 new"):
    return client.post(f"/requests/{tid}/documents/{doc_id}/revision", files={"file": (name, content)})


def get_doc(doc_id):
    with Session(engine) as s:
        return s.get(Document, doc_id)


def count(model):
    with Session(engine) as s:
        return len(s.exec(select(model)).all())


def files_on_disk():
    return len(list(Path(TMP_STORAGE).iterdir()))


T = "LEO-2026-000001"
# --- A revised copy becomes version 2 ---------------------------------------
first = add_doc(T, requirement_id=ITEM)
r = revise(T, first)
check("revised copy accepted", r.status_code == 200, r.text)
second = r.json()["id"]
check("it is version 2", r.json()["version"] == 2)
check("it points to the file it replaces", r.json()["supersedes_document_id"] == first)
new = get_doc(second)
check("same checklist item", new.requirement_id == ITEM)
check("a client document, as submitted", (new.category, new.status) == ("client_submitted", "submitted"))
check("uploaded by the client", new.uploaded_by_requester_id == ME and new.uploaded_by_staff_id is None)
check("earlier row kept", get_doc(first).original_filename == "MOA.pdf")
check("earlier file unchanged in storage", storage.read_file(get_doc(first).storage_key) == PDF)
check("new file stored as received", storage.read_file(new.storage_key) == b"%PDF-1.4 new")
check("client can still open the earlier version",
      client.post(f"/requests/{T}/documents/{first}/download").content == PDF)
with Session(engine) as s:
    entry = s.exec(select(AuditLogEntry).order_by(AuditLogEntry.id.desc())).first()
    note = s.exec(select(StaffNotification).order_by(StaffNotification.id.desc())).first()
check("History entry by the client, naming both files", entry.actor_type == "requester" and entry.staff_id is None
      and entry.detail == 'Requester uploaded "MOA-fixed.pdf" as version 2 of "MOA.pdf".', entry.detail)
check("assigned assistant notified", note.staff_id == LA and "version 2" in note.message, note.message)
out = client.get(f"/portal/requests/{T}").json()
check("client's page gets the version and the link",
      any(d["version"] == 2 and d["supersedes_document_id"] == first for d in out["documents"]))
check("client's history shows it", any("version 2" in h["description"] for h in out["history"]))
check("staff page gets the link too",
      any(d["supersedes_document_id"] == first for d in client.get(f"/requests/{T}").json()["documents"]))

# --- Only once, and only when asked ------------------------------------------
before = (count(Document), files_on_disk())
r = revise(T, first)
check("a replaced file can't be replaced again", r.status_code == 409, r.text)
r = revise(T, second)
check("not asked to revise -> refused", r.status_code == 409, r.text)
r = client.patch(f"/requests/{T}/documents/{first}", json={"status": "submitted"})
check("staff can't change a replaced file", r.status_code == 409, r.text)
check("refusals saved nothing", (count(Document), files_on_disk()) == before)
r = client.patch(f"/requests/{T}/documents/{second}", json={"status": "revision_required", "note": "Fix the date."})
check("staff ask to revise version 2", r.status_code == 200, r.text)
r = revise(T, second, "MOA-final.docx", b"PK\x03\x04 word file")
check("then version 3 of it", r.status_code == 200 and r.json()["version"] == 3
      and r.json()["supersedes_document_id"] == second, r.text)

# --- Not allowed ---------------------------------------------------------------
office = add_doc(T, category="legal_office")
check("a Legal Office document: 404", revise(T, office).status_code == 404)
elsewhere = add_doc("LEO-2026-000003")
check("another request's document: 404", revise(T, elsewhere).status_code == 404)
check("another client's request: 404", revise("LEO-2026-000003", elsewhere).status_code == 404)
check("no such document: 404", revise(T, 99999).status_code == 404)
closed = add_doc("LEO-2026-000002")
check("closed request: 409", revise("LEO-2026-000002", closed).status_code == 409)
open_doc = add_doc(T)
before = (count(Document), files_on_disk())
check("wrong file type: 400", revise(T, open_doc, "virus.exe").status_code == 400)
check("empty file: 400", revise(T, open_doc, content=b"").status_code == 400)
check("over 20 MB: 400", revise(T, open_doc, content=b"x" * (20 * 1024 * 1024 + 1)).status_code == 400)
check("bad files saved nothing", (count(Document), files_on_disk()) == before)
main.app.dependency_overrides.pop(get_verified_request_for_path)
check("not signed in: 401", revise(T, open_doc).status_code == 401)
main.app.dependency_overrides[get_verified_request_for_path] = override_request

# --- The ordinary upload is unchanged ------------------------------------------
r = client.post(f"/requests/{T}/documents/client", files=[("files", ("extra.pdf", PDF))])
check("ordinary upload: version 1, replaces nothing",
      r.status_code == 200 and (r.json()[0]["version"], r.json()[0]["supersedes_document_id"]) == (1, None), r.text)

# --- Endorsement letter ----------------------------------------------------------
for status, moves in [("pending", True), ("verified", False)]:
    letter = add_doc(T, original_filename="Letter.pdf")
    with Session(engine) as s:
        c = s.get(Requester, ME); c.endorsement_document_id = letter; c.verification_status = status
        s.add(c); s.commit()
    r = revise(T, letter, "Letter2.pdf")
    with Session(engine) as s:
        now = s.get(Requester, ME).endorsement_document_id
    check(f"letter of a {status} client: " + ("staff check the new one" if moves else "the verified one stays on record"),
          r.status_code == 200 and now == (r.json()["id"] if moves else letter))

# --- A failed save leaves no stray file -------------------------------------------
import app.routers.documents as documents  # noqa: E402
broken = add_doc(T)
before = (count(Document), files_on_disk())
real_notify = documents.notify


def failing_notify(*a, **k):
    raise RuntimeError("test failure")


documents.notify = failing_notify
try:
    revise(T, broken)
except RuntimeError:
    pass
documents.notify = real_notify
check("a failed save leaves no row and no file", (count(Document), files_on_disk()) == before,
      (before, count(Document), files_on_disk()))

shutil.rmtree(TMP_STORAGE, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

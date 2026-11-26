r"""Checks files in the request chat (backlog item 1, step 1b): a message may
carry up to 5 files (PDF/Word/JPG/PNG, 20 MB each); each becomes a Document
linked by message_id. Staff files are Legal Office documents, released as they
are sent (History entry naming the file); client files are the client's own
documents. A bad batch saves nothing. The client never sees a withdrawn file.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_message_attachments.py
Uses a throwaway in-memory SQLite database and a temporary storage folder:
the real Postgres and backend/storage are never touched, and no email is sent.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP_STORAGE = tempfile.mkdtemp(prefix="lois-chat-files-")
os.environ["LOIS_STORAGE_ROOT"] = TMP_STORAGE  # read when app.core.storage is imported

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi import Depends  # noqa: E402
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
    AuditLogEntry, Document, LegalServiceRequest, Message, Requester, ServiceType, StaffNotification,
    StaffUser, WorkflowStepTemplate,
)

assert str(storage.STORAGE_ROOT) == TMP_STORAGE, "storage must point at the temp folder"

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    st = ServiceType(name="Legal Advice", code="legal_advice")
    client_a = Requester(full_name="Online Client", email="client@example.com")
    mine = StaffUser(full_name="Assistant One", email="la1@essu.edu.ph", hashed_password="x", role="legal_assistant")
    other = StaffUser(full_name="Assistant Two", email="la2@essu.edu.ph", hashed_password="x", role="legal_assistant")
    s.add_all([st, client_a, mine, other]); s.commit()
    step = WorkflowStepTemplate(service_type_id=st.id, sequence_order=1, status_name="Submitted")
    s.add(step); s.commit()
    for tid in ["LEO-2026-000001", "LEO-2026-000002", "LEO-2026-000003"]:
        s.add(LegalServiceRequest(tracking_id=tid, requester_id=client_a.id, service_type_id=st.id,
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


def override_client_request(tracking_id: str, s: Session = Depends(get_session)):
    """Stands in for the client's sign-in cookie: the client owns every test request."""
    return s.exec(select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)).first()


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff
main.app.dependency_overrides[get_verified_request_for_path] = override_client_request
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run
limiter.enabled = False

sent = []
verification._send_request_update_email = lambda to, tid, lines, is_closed: sent.append((to, tid, lines))

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


PDF = b"%PDF-1.4 test file"
PNG = b"\x89PNG\r\n\x1a\n test image"


def staff_send(tid, body="", files=()):
    return client.post(f"/requests/{tid}/messages", data={"body": body},
                       files=[("files", f) for f in files] or None)


def client_send(tid, body="", files=()):
    return client.post(f"/requests/{tid}/messages/client", data={"body": body},
                       files=[("files", f) for f in files] or None)


def rid(tid):
    with Session(engine) as s:
        return s.exec(select(LegalServiceRequest.id).where(LegalServiceRequest.tracking_id == tid)).first()


def rows(model, tid, *where):
    with Session(engine) as s:
        return s.exec(select(model).where(model.request_id == rid(tid), *where)).all()


def stored_files():
    return sorted(os.listdir(TMP_STORAGE)) if os.path.isdir(TMP_STORAGE) else []


try:
    # 1. Staff: text + two files.
    r = staff_send("LEO-2026-000001", "Here is the draft and a photo.",
                   [("draft.pdf", PDF, "application/pdf"), ("photo.png", PNG, "image/png")])
    check("staff text + 2 files: 200", r.status_code == 200, r.text)
    out = r.json() if r.status_code == 200 else {}
    names = [a["original_filename"] for a in out.get("attachments", [])]
    check("reply lists both files", names == ["draft.pdf", "photo.png"], names)
    check("reply gives type and size", out.get("attachments", [{}])[0].get("mime_type") == "application/pdf"
          and out["attachments"][0].get("file_size_bytes") == len(PDF), out.get("attachments"))
    docs = rows(Document, "LEO-2026-000001")
    check("two Document rows", len(docs) == 2, docs)
    check("linked to the message", all(d.message_id == out.get("id") for d in docs), [d.message_id for d in docs])
    check("filed as Legal Office documents", all(d.category == "legal_office" for d in docs))
    check("released to the client as sent", all(d.is_released_to_requester for d in docs))
    check("uploaded by the staff member", all(d.uploaded_by_staff_id == MINE for d in docs))
    check("both files written to storage", len(stored_files()) == 2, stored_files())
    released = rows(AuditLogEntry, "LEO-2026-000001", AuditLogEntry.action == "document_released")
    check("History names each released file", sorted(e.detail for e in released) == [
        'Document released with a message: "draft.pdf".',
        'Document released with a message: "photo.png".'], [e.detail for e in released])
    check("no staff name in the client-visible line", all("Assistant" not in e.detail for e in released))
    sent_entry = rows(AuditLogEntry, "LEO-2026-000001", AuditLogEntry.action == "message_sent")
    check("message History entry counts the files",
          [e.detail for e in sent_entry] == ["Assistant One sent a message with 2 file(s)."], [e.detail for e in sent_entry])
    check("one notice email, no extra email per file", len(sent) == 1, sent)

    # 2. Files only, no text.
    sent.clear()
    r = staff_send("LEO-2026-000001", "   ", [("reply.docx", b"PK\x03\x04 word file", "application/octet-stream")])
    check("staff file with no text: 200", r.status_code == 200, r.text)
    check("saved with empty text", r.status_code == 200 and r.json()["body"] == "", r.text)
    check("Word type from the file name, not the browser",
          rows(Document, "LEO-2026-000001", Document.original_filename == "reply.docx")[0].mime_type
          == "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    check("second message within 24 h: no email", sent == [], sent)

    # 3. Refused: nothing saved, nothing written.
    before_msgs = len(rows(Message, "LEO-2026-000002"))
    before_files = stored_files()
    r = staff_send("LEO-2026-000002", "  ")
    check("no text and no files: 400", r.status_code == 400, r.status_code)
    check("friendly empty message", r.json().get("detail") == "Write a message or attach a file.", r.text)
    r = staff_send("LEO-2026-000002", "Six files", [(f"f{i}.pdf", PDF, "application/pdf") for i in range(6)])
    check("six files: 400", r.status_code == 400, r.text)
    check("six files: says the limit", "up to 5 files" in r.text, r.text)
    r = staff_send("LEO-2026-000002", "Bad type", [("ok.pdf", PDF, "application/pdf"),
                                                  ("virus.exe", b"MZ", "application/pdf")])
    check("one bad file in the batch: 400", r.status_code == 400, r.text)
    check("bad file named in the error", "virus.exe" in r.text, r.text)
    r = staff_send("LEO-2026-000002", "Empty file", [("empty.pdf", b"", "application/pdf")])
    check("empty file: 400", r.status_code == 400, r.text)
    big = b"0" * (storage.MAX_FILE_SIZE_BYTES + 1)
    r = staff_send("LEO-2026-000002", "Too big", [("big.pdf", big, "application/pdf")])
    check("file over 20 MB: 400", r.status_code == 400, r.text)
    r = staff_send("LEO-2026-000002", "x" * 5001)
    check("text over 5,000 characters: refused", r.status_code == 422, r.status_code)
    check("refused sends: no message saved", len(rows(Message, "LEO-2026-000002")) == before_msgs)
    check("refused sends: no Document rows", rows(Document, "LEO-2026-000002") == [])
    check("refused sends: nothing written to storage", stored_files() == before_files)

    current["id"] = OTHER
    r = staff_send("LEO-2026-000002", "Not mine", [("a.pdf", PDF, "application/pdf")])
    current["id"] = MINE
    check("assistant not assigned: 403", r.status_code == 403, r.status_code)
    check("403: nothing written", stored_files() == before_files)

    # 4. Client: text + one file.
    with Session(engine) as s:
        before_notes = len(s.exec(select(StaffNotification)).all())
    r = client_send("LEO-2026-000001", "Here is my ID.", [("my-id.jpg", b"\xff\xd8\xff jpeg", "image/jpeg")])
    check("client text + file: 200", r.status_code == 200, r.text)
    cdoc = rows(Document, "LEO-2026-000001", Document.original_filename == "my-id.jpg")
    check("filed as the client's document", len(cdoc) == 1 and cdoc[0].category == "client_submitted", cdoc)
    check("uploaded by the client", cdoc and cdoc[0].uploaded_by_requester_id is not None
          and cdoc[0].uploaded_by_staff_id is None, cdoc)
    check("linked to the client's message", cdoc and cdoc[0].message_id == r.json()["id"], cdoc)
    up = rows(AuditLogEntry, "LEO-2026-000001", AuditLogEntry.action == "client_document_uploaded")
    check("History names the client's file", [e.detail for e in up] == ['Requester uploaded "my-id.jpg".'],
          [e.detail for e in up])
    with Session(engine) as s:
        notes = s.exec(select(StaffNotification)).all()
    check("assigned officer notified once", len(notes) == before_notes + 1
          and "with files" in notes[-1].message, [n.message for n in notes])
    r = client_send("LEO-2026-000001", "Seven", [(f"f{i}.png", PNG, "image/png") for i in range(7)])
    check("client: six or more files refused", r.status_code == 400, r.status_code)
    r = client_send("LEO-2026-000001", "")
    check("client: empty message refused", r.status_code == 400, r.status_code)

    # 5. Lists: staff see every file; the client sees released ones only.
    staff_list = client.get("/requests/LEO-2026-000001/messages").json()
    staff_files = [a["original_filename"] for m in staff_list for a in m["attachments"]]
    check("staff list shows all files", staff_files == ["draft.pdf", "photo.png", "reply.docx", "my-id.jpg"],
          staff_files)
    pub = client.post("/requests/LEO-2026-000001/messages/lookup").json()
    check("client list shows all files while released",
          [a["original_filename"] for m in pub for a in m["attachments"]] == staff_files)
    check("messages without files have an empty list",
          all(m["attachments"] == [] for m in client.get("/requests/LEO-2026-000003/messages").json()))

    draft_id = rows(Document, "LEO-2026-000001", Document.original_filename == "draft.pdf")[0].id
    r = client.post(f"/requests/LEO-2026-000001/documents/{draft_id}/download")
    check("client can download a released chat file", r.status_code == 200 and r.content == PDF, r.status_code)

    r = client.patch(f"/requests/LEO-2026-000001/documents/{draft_id}/release", json={"released": False})
    check("staff withdraw the file in the Documents tab", r.status_code == 200, r.text)
    pub = client.post("/requests/LEO-2026-000001/messages/lookup").json()
    pub_files = [a["original_filename"] for m in pub for a in m["attachments"]]
    check("withdrawn file hidden from the client's chat", "draft.pdf" not in pub_files and "photo.png" in pub_files,
          pub_files)
    r = client.post(f"/requests/LEO-2026-000001/documents/{draft_id}/download")
    check("withdrawn file: client download 404", r.status_code == 404, r.status_code)
    staff_atts = {a["original_filename"]: a for m in client.get("/requests/LEO-2026-000001/messages").json()
                  for a in m["attachments"]}
    check("staff still see the withdrawn file", "draft.pdf" in staff_atts, list(staff_atts))
    check("staff list marks it hidden from the client", staff_atts.get("draft.pdf", {}).get("hidden_from_client") is True,
          staff_atts.get("draft.pdf"))
    check("released and client files are not marked hidden",
          not any(a["hidden_from_client"] for n, a in staff_atts.items() if n != "draft.pdf"), staff_atts)
    check("the client's list never carries a hidden file",
          not any(a["hidden_from_client"] for m in pub for a in m["attachments"]), pub)

    # 6. Closed request: the client can't send.
    with Session(engine) as s:
        s.get(LegalServiceRequest, rid("LEO-2026-000003")).is_cancelled = True
        s.commit()
    before_files = stored_files()
    r = client_send("LEO-2026-000003", "Late", [("late.pdf", PDF, "application/pdf")])
    check("closed request: client send 409", r.status_code == 409, r.status_code)
    check("closed request: nothing saved", rows(Message, "LEO-2026-000003") == [] and stored_files() == before_files)

    # 7. The normal Documents upload is unchanged and has no message link.
    r = client.post("/requests/LEO-2026-000002/documents/staff", files=[("files", ("memo.pdf", PDF, "application/pdf"))])
    check("Documents tab upload still works", r.status_code == 200, r.text)
    memo = rows(Document, "LEO-2026-000002", Document.original_filename == "memo.pdf")
    check("Documents tab upload: no message, not released",
          memo and memo[0].message_id is None and not memo[0].is_released_to_requester, memo)
finally:
    shutil.rmtree(TMP_STORAGE, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed.")
sys.exit(0 if all(results) else 1)

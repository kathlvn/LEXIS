r"""Shared setup for the bigger tests (not a test itself): a throwaway copy of
LEXIS with made-up staff, clients and requests. Import it before anything
from app/.

Safety:
- the database is in-memory SQLite (the real Postgres is never touched);
- uploads go to a temporary folder, deleted at exit;
- emails are recorded in MAIL, never sent;
- backend/.env is not read (settings load from a temp folder), and any
  network connection outside this computer is refused;
- the robot check and rate limiter are off.
"""
import atexit
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
STORE = tempfile.mkdtemp(prefix="lexis-test-files-")
_WORK = tempfile.mkdtemp(prefix="lexis-test-cwd-")
atexit.register(lambda: shutil.rmtree(STORE, ignore_errors=True))
atexit.register(lambda: shutil.rmtree(_WORK, ignore_errors=True))
os.chdir(_WORK)  # settings look for ".env" in the current folder: there is none here
for k in list(os.environ):
    if k.upper().startswith(("SMTP_", "MAIL_", "DATABASE_URL", "SECRET_KEY", "GDRIVE_", "STORAGE_",
                             "LOIS_", "LOCAL_STORAGE", "ENVIRONMENT", "PUBLIC_SITE")):
        del os.environ[k]
os.environ.update({
    "DATABASE_URL": "sqlite://", "SECRET_KEY": "test-only-secret-key-" + "x" * 32,
    "ENVIRONMENT": "production", "LOCAL_STORAGE_DIR": STORE, "STORAGE_BACKEND": "local",
    "BOT_CHECK_ENABLED": "false",
})
REFUSED = []


def _guard(event, args):
    if event == "socket.connect" and isinstance(args[1], tuple):
        host, port = args[1][:2]
        if host not in ("127.0.0.1", "::1") or port in (25, 465, 587, 5432, 5433):
            REFUSED.append((host, port))
            raise OSError("blocked by the test guard")
    elif event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.path.basename(os.fsdecode(args[0])).lower().startswith(".env"):
            REFUSED.append(os.fsdecode(args[0]))
            raise PermissionError("blocked by the test guard")


sys.addaudithook(_guard)

from sqlalchemy import event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, create_engine, select  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.core.database as database  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


@event.listens_for(engine, "connect")
def _foreign_keys(conn, _):
    conn.execute("PRAGMA foreign_keys=ON")


database.engine = engine
import app.main as main  # noqa: E402
import app.seed as seed  # noqa: E402
import app.core.mailer as mailer  # noqa: E402
import app.core.verification as verification  # noqa: E402
from app.core import storage  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models import models as m  # noqa: E402

main.engine = engine  # the 429 handler logs with main.engine
seed.engine = engine
limiter.enabled = False
assert str(storage.STORAGE_ROOT) == STORE, "storage must point at the temp folder"
MAIL = []  # (to, subject, body, kind)


def _record(to, subject, body, kind="general"):
    MAIL.append((to, subject, body, kind))
    return True


for _mod in (verification, mailer):
    _mod.email_is_configured = lambda: True
    _mod.send_email = _record

PDF = b"%PDF-1.4 test file"
PW = "test-harbor-mango-01"


def new_client():
    return TestClient(main.app, raise_server_exceptions=False)


def one(model, *where):
    with Session(engine) as s:
        return s.exec(select(model).where(*where)).first()


def rows(model, *where):
    with Session(engine) as s:
        return s.exec(select(model).where(*where)).all()


def edit(model, id_, **kw):
    with Session(engine) as s:
        x = s.get(model, id_)
        for k, v in kw.items():
            setattr(x, k, v)
        s.add(x)
        s.commit()


def last_code(to):
    for t, _s, body, _k in reversed(MAIL):
        if t == to:
            found = re.search(r"\b(\d{6})\b", body)
            if found:
                return found.group(1)
    raise AssertionError(f"no code emailed to {to}")


def ok(r, what):
    assert r.status_code < 300, f"{what}: {r.status_code} {r.text[:300]}"
    return r.json() if r.content else None


def staff(name, email, role, **kw):
    with Session(engine) as s:
        x = m.StaffUser(full_name=name, email=email, role=role, hashed_password=hash_password(PW),
                        is_email_verified=True, **kw)
        s.add(x)
        s.commit()
        return x.id


def staff_login(email, pw=PW):
    c = new_client()
    ok(c.post("/auth/login", json={"email": email, "password": pw}), "login " + email)
    return c


def door(email, profile=None):
    """A client signs in with the emailed code, and registers if new."""
    c = new_client()
    for v in rows(m.ContactVerification, m.ContactVerification.contact_value == email):  # skip the 60 s wait
        edit(m.ContactVerification, v.id, created_at=v.created_at.replace(year=2020))
    ok(c.post("/access/contact/send-code", json={"contact": email}), "send code")
    r = ok(c.post("/portal/verify-code", json={"contact": email, "code": last_code(email)}), "verify code")
    if r["status"] == "new_client" and profile:
        ok(c.post("/portal/register", json={**profile, "privacy_consent": True}), "register")
    return c


def prof(last, first, cat, **kw):
    base = {"last_name": last, "first_name": first, "no_middle_name": True, "client_category": cat}
    base.update({"office_other": "Test Office"} if cat.startswith("essu") else {"organization": "Test Agency"})
    base.update(kw)
    return base


def submit(c, code, text, letter=False, files=(), **form):
    data = {"service_type_code": code, "description": text, "attestation": "true", **form}
    up = [("files", (n, b, "application/pdf")) for n, b, _ in files]
    if files:
        data["requirement_ids"] = [str(i or "") for _, _, i in files]
    if letter:
        up.append(("endorsement_letter", ("letter.pdf", PDF, "application/pdf")))
    return c.post("/portal/requests", data=data, files=up or None)


def req_items(code):
    with Session(engine) as s:
        st = s.exec(select(m.ServiceType).where(m.ServiceType.code == code)).one()
        return [(r.id, r.is_required) for r in s.exec(select(m.ServiceRequirement).where(
            m.ServiceRequirement.service_type_id == st.id)).all()]


def walkin(c, code, text, email=None, phone=None, letter=True, last="Walkin", cat="essu_employee", **kw):
    import json
    payload = {"requester": prof(last, "Wanda", cat, email=email, phone=phone, **kw), "service_type_code": code,
               "description": text, "consent_confirmed": True}
    return c.post("/requests/staff-encoded", data={"payload": json.dumps(payload)},
                  files={"endorsement_letter": ("wletter.pdf", PDF)} if letter else None)


class World:
    """Who is signed in (w.c), their ids (w.id), request IDs (w.t), document ids (w.d)."""


def build():
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        seed.seed()
    w = World()
    w.id = {}
    for key, name, role in [("la1", "Ana Assistant", "legal_assistant"), ("la2", "Ben Assistant", "legal_assistant"),
                            ("la3", "Cora Deactivated", "legal_assistant"), ("atty", "Dan Attorney", "attorney"),
                            ("head", "Ella Head", "head_attorney")]:
        w.id[key] = staff(name, f"{key}@essu.edu.ph", role)
    w.c = {k: staff_login(f"{k}@essu.edu.ph") for k in w.id}
    H, L = w.c["head"], w.c["la1"]
    ok(H.patch(f"/staff/{w.id['la3']}", json={"is_active": False}), "deactivate")
    n = ok(H.post("/staff", json={"full_name": "New Staff", "email": "new@essu.edu.ph", "password": PW,
                                  "role": "legal_assistant"}), "new staff")
    w.id["new"] = n["id"]
    w.c["new"] = new_client()  # verified email, but still has to change the temporary password
    ok(w.c["new"].post("/auth/verify-email", json={"email": "new@essu.edu.ph", "code": last_code("new@essu.edu.ph")}),
       "verify email")
    w.new_login = w.c["new"].post("/auth/login", json={"email": "new@essu.edu.ph", "password": PW})

    def mine(tid):
        ok(H.patch(f"/requests/{tid}/reassign", json={"new_officer_id": w.id["la1"]}), "reassign")
        return tid

    w.t = {}
    A = w.c["A"] = door("a@example.com", prof("Aquino", "Alma", "essu_employee"))
    w.t["A1"] = mine(ok(submit(A, "legal_advice", "A1 private concern", letter=True,
                               files=[("a1.pdf", PDF, None)]), "A1")["tracking_id"])
    ok(L.post(f"/requests/{w.t['A1']}/client/verify", json={"method": "endorsement_letter"}), "verify A")
    for k, text in [("Arev", "A for review"), ("Aclosed", "A to be cancelled"), ("Adone", "A completed")]:
        w.t[k] = mine(ok(submit(A, "legal_advice", text), k)["tracking_id"])
    # Statuses move one step at a time (API-05), so walk them.
    for step in ("Received", "Under Review", "Processing", "For Attorney Review"):
        ok(L.patch(f"/requests/{w.t['Arev']}/status", json={"new_status": step}), "Arev " + step)
    ok(H.post(f"/requests/{w.t['Aclosed']}/close", json={"action": "cancel", "reason": "test"}), "close")
    for step in ("Received", "Under Review", "Processing", "For Attorney Review", "Approved", "Completed"):
        ok(H.patch(f"/requests/{w.t['Adone']}/status", json={"new_status": step}), "Adone " + step)
    t = w.t["A1"]
    ok(L.patch(f"/requests/{t}/status", json={"is_waiting_for_client": True, "note": "Send the ID."}), "waiting")
    up = ok(L.post(f"/requests/{t}/documents/staff",
                   files=[("files", ("released.pdf", PDF)), ("files", ("secret.pdf", PDF))]), "staff docs")
    w.d = {"released": up[0]["id"], "secret": up[1]["id"]}
    ok(L.patch(f"/requests/{t}/documents/{w.d['released']}/release", json={"released": True}), "release")
    w.d["client"] = one(m.Document, m.Document.original_filename == "a1.pdf").id
    w.d["letter"] = one(m.Document, m.Document.original_filename == "letter.pdf").id
    rv = ok(A.post(f"/requests/{t}/documents/client", files=[("files", ("revise.pdf", PDF))]), "revise doc")
    w.d["revise"] = rv[0]["id"]
    ok(L.patch(f"/requests/{t}/documents/{w.d['revise']}", json={"status": "revision_required", "note": "Sign it."}),
       "ask revise")
    ok(L.post(f"/requests/{t}/messages", data={"body": "Staff message"}), "msg")
    ok(A.post(f"/requests/{t}/messages/client", data={"body": "Client message"}), "msg2")
    B = w.c["B"] = door("b@example.com", prof("Bautista", "Bea", "essu_employee"))
    w.t["B1"] = mine(ok(submit(B, "legal_advice", "B1 private concern", letter=True), "B1")["tracking_id"])
    C = w.c["C"] = door("c@example.com", prof("Cruz", "Carl", "essu_student"))
    w.t["C1"] = mine(ok(submit(C, "legal_research", "C1 research", letter=True), "C1")["tracking_id"])
    D = w.c["D"] = door("d@example.com", prof("Diaz", "Dina", "ext_government"))
    files = [("complaint.pdf", PDF, i) for i, need in req_items("complaint_referral") if need]
    w.t["D1"] = mine(ok(submit(D, "complaint_referral", "D1 complaint", files=files), "D1")["tracking_id"])
    for key, mail in (("E", "e@example.com"), ("F", "f@example.com")):
        x = w.c[key] = door(mail, prof("Estrada", key, "essu_employee"))
        w.t[key + "1"] = mine(ok(submit(x, "legal_advice", key + " request", letter=True), key)["tracking_id"])
    ok(L.post(f"/requests/{w.t['E1']}/client/verify", json={"method": "endorsement_letter"}), "verify E")
    ok(H.post(f"/requests/{w.t['E1']}/client/revoke", json={"reason": "test revoke"}), "revoke E")
    ok(L.post(f"/requests/{w.t['F1']}/client/decline", json={"reason": "test decline"}), "decline F")
    w.t["W1"] = mine(ok(walkin(L, "legal_advice", "Walk-in email", email="w1@example.com"), "W1")["tracking_id"])
    w.t["W2"] = mine(ok(walkin(L, "legal_advice", "Walk-in phone", phone="0917 000 1111"), "W2")["tracking_id"])
    w.art = ok(L.post("/articles", json={"title": "Draft one", "body": "Text"}), "article")["id"]
    w.pub = ok(L.post("/articles", json={"title": "Public one", "body": "Text"}), "article")["id"]
    ok(H.post(f"/articles/{w.pub}/publish"), "publish")
    w.office = ok(H.post("/offices", json={"name": "Test Listed Office"}), "office")["id"]
    w.hol = ok(H.post("/holidays", json={"holiday_date": "2027-01-01", "name": "New Year",
                                         "holiday_type": "regular"}), "holiday")["id"]
    w.note = one(m.StaffNotification, m.StaffNotification.staff_id == w.id["la1"]).id
    w.cid = {k: one(m.Requester, m.Requester.email == f"{k.lower()}@example.com").id for k in "ABCDEF"}
    w.c["visitor"] = new_client()
    f = w.c["forged"] = new_client()
    f.cookies.set("lois_session", "x" * 43)
    f.cookies.set("lois_track", "y" * 43)
    return w


class Snapshot:
    """A copy of the whole in-memory database, put back in a few milliseconds."""

    def __init__(self):
        self.copy = sqlite3.connect(":memory:", check_same_thread=False)
        engine.raw_connection().driver_connection.backup(self.copy)

    def restore(self):
        self.copy.backup(engine.raw_connection().driver_connection)
        MAIL.clear()

r"""Checks for staff account logging and "Set a temporary password" (routers/staff.py).
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_staff_accounts.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
No email is sent (the verification code sender is replaced).
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.main as main  # noqa: E402
import app.routers.staff as staff_router  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.security import verify_password  # noqa: E402
from app.models.models import SecurityEvent, StaffSession, StaffUser  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine
staff_router.issue_verification_code = lambda session, staff: "123456"   # no email

with Session(engine) as s:
    head = StaffUser(full_name="Test Head", email="head@essu.edu.ph", hashed_password="x", role="head_attorney")
    la = StaffUser(full_name="Test Assistant", email="la@essu.edu.ph", hashed_password="x", role="legal_assistant",
                   failed_login_attempts=4, locked_until=datetime.utcnow() + timedelta(minutes=10))
    s.add(head); s.add(la); s.commit()
    s.add(StaffSession(staff_id=la.id, token_hash="a" * 64, expires_at=datetime.utcnow() + timedelta(hours=2)))
    s.add(StaffSession(staff_id=head.id, token_hash="b" * 64, expires_at=datetime.utcnow() + timedelta(hours=2)))
    s.commit()
    HEAD_ID, LA_ID = head.id, la.id

acting = {"id": HEAD_ID}


def override_session():
    with Session(engine) as s:
        yield s


def override_staff():
    with Session(engine) as s:
        return s.get(StaffUser, acting["id"])


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def events(kind):
    with Session(engine) as s:
        return [e.detail for e in s.exec(select(SecurityEvent).where(SecurityEvent.event_type == kind)
                                         .order_by(SecurityEvent.id)).all()]


# ---- logging: create
PASSWORD = "a-long-temporary-pass"
r = client.post("/staff", json={"full_name": "New Person", "email": "new@essu.edu.ph",
                                "password": PASSWORD, "role": "legal_assistant"})
check("create works", r.status_code == 201, r.text)
NEW_ID = r.json()["id"]
log = events("staff_account_created")
check("create is logged with names and role", log == ["Test Head created a Legal Assistant account for New Person."], log)
check("the password never appears in the log", all(PASSWORD not in d for d in log))

# ---- logging: edit
r = client.patch(f"/staff/{NEW_ID}", json={"role": "attorney", "is_active": False, "full_name": "New Person"})
check("edit works", r.status_code == 200, r.text)
log = events("staff_account_changed")
check("role change and switch-off are logged (unchanged name is not)",
      log == ["Test Head changed the account of New Person: role changed from Legal Assistant to Attorney; "
              "account switched off."], log)
r = client.patch(f"/staff/{NEW_ID}", json={"role": "attorney"})
check("saving with nothing changed adds no log entry", len(events("staff_account_changed")) == 1, events("staff_account_changed"))
r = client.patch(f"/staff/{NEW_ID}", json={"full_name": "Renamed Person", "is_active": True})
check("rename and switch-on are logged", events("staff_account_changed")[-1] ==
      "Test Head changed the account of New Person: name changed from New Person to Renamed Person; account switched on.",
      events("staff_account_changed"))

# ---- temporary password
NEW_PW = "another-temp-harbor"
r = client.post(f"/staff/{LA_ID}/password", json={"password": "short"})
check("a password under 12 characters is refused", r.status_code == 422, r.status_code)
r = client.post(f"/staff/{HEAD_ID}/password", json={"password": NEW_PW})
check("not for your own account", r.status_code == 400 and "own account" in r.json()["detail"], r.text)
r = client.post("/staff/99999/password", json={"password": NEW_PW})
check("unknown account: 404", r.status_code == 404, r.status_code)
r = client.post(f"/staff/{LA_ID}/password", json={"password": NEW_PW})
check("set works", r.status_code == 200, r.text)
with Session(engine) as s:
    la = s.get(StaffUser, LA_ID)
    check("new password works", verify_password(NEW_PW, la.hashed_password))
    check("lockout cleared", la.failed_login_attempts == 0 and la.locked_until is None)
    la_sessions = s.exec(select(StaffSession).where(StaffSession.staff_id == LA_ID)).all()
    head_sessions = s.exec(select(StaffSession).where(StaffSession.staff_id == HEAD_ID)).all()
    check("their open sessions ended", all(x.revoked_at is not None for x in la_sessions))
    check("the Head Attorney's own session is untouched", all(x.revoked_at is None for x in head_sessions))
log = events("staff_password_set")
check("logged without the password", log == ["Test Head set a temporary password for Test Assistant."]
      and NEW_PW not in log[0], log)

# ---- role check (server side)
acting["id"] = LA_ID
r = client.post(f"/staff/{NEW_ID}/password", json={"password": NEW_PW})
check("a legal assistant is refused (403)", r.status_code == 403, r.status_code)
r = client.patch(f"/staff/{NEW_ID}", json={"role": "head_attorney"})
check("a legal assistant can't edit accounts (403)", r.status_code == 403, r.status_code)

# ---- Security Log labels
from app.routers.security_log import EVENT_LABELS  # noqa: E402
check("Security Log has labels for the three events",
      all(k in EVENT_LABELS for k in ("staff_account_created", "staff_account_changed", "staff_password_set")))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

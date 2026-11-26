r"""Checks for "choose a new password" (backlog item 2): a password the Head
Attorney chose (new account, temporary password) must be replaced by the
staff member at their next login (routers/auth.py, routers/staff.py).
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_force_password_change.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
No email is sent (the verification code sender is replaced).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.main as main  # noqa: E402
import app.routers.staff as staff_router  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.security import hash_password, verify_password  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import SecurityEvent, StaffSession, StaffUser  # noqa: E402
from app.routers.security_log import EVENT_LABELS  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine
staff_router.issue_verification_code = lambda session, staff: "123456"   # no email
limiter.enabled = False  # many logins in a row; the limits have their own test

OWN = "head-own-password-1"
with Session(engine) as s:
    head = StaffUser(full_name="Test Head", email="head@essu.edu.ph", hashed_password=hash_password(OWN),
                     role="head_attorney", is_email_verified=True)
    la = StaffUser(full_name="Test Assistant", email="la@essu.edu.ph", hashed_password=hash_password("la-old-password-1"),
                   role="legal_assistant", is_email_verified=True)
    s.add(head); s.add(la); s.commit()
    HEAD_ID, LA_ID = head.id, la.id


def override_session():
    with Session(engine) as s:
        yield s


def override_staff():
    with Session(engine) as s:
        return s.get(StaffUser, HEAD_ID)


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff  # only the /staff calls use it
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def account(staff_id):
    with Session(engine) as s:
        return s.get(StaffUser, staff_id)


def open_sessions(staff_id):
    with Session(engine) as s:
        return len(s.exec(select(StaffSession).where(StaffSession.staff_id == staff_id,
                                                     StaffSession.revoked_at.is_(None))).all())


def events(kind):
    with Session(engine) as s:
        return [e.detail for e in s.exec(select(SecurityEvent).where(SecurityEvent.event_type == kind)
                                         .order_by(SecurityEvent.id)).all()]


def sets_cookie(r):
    return settings.session_cookie_name in r.headers.get("set-cookie", "")


def login(email, password):
    client.cookies.clear()
    return client.post("/auth/login", json={"email": email, "password": password})


def new_password(email, password, new):
    client.cookies.clear()
    return client.post("/auth/new-password", json={"email": email, "password": password, "new_password": new})


# ---- a new account
GIVEN = "given-by-the-head-1"
r = client.post("/staff", json={"full_name": "New Person", "email": "new@essu.edu.ph",
                                "password": GIVEN, "role": "legal_assistant"})
check("create works", r.status_code == 201, r.text)
NEW_ID = r.json()["id"]
check("a new account must choose its own password", account(NEW_ID).must_change_password is True)

r = new_password("new@essu.edu.ph", GIVEN, "my-own-harbor-12")
check("email verification still comes first (403 verify)",
      r.status_code == 403 and "verify your email" in r.json()["detail"], r.text)
check("...and the password is unchanged", verify_password(GIVEN, account(NEW_ID).hashed_password))

with Session(engine) as s:
    a = s.get(StaffUser, NEW_ID); a.is_email_verified = True; s.add(a); s.commit()

r = login("new@essu.edu.ph", GIVEN)
check("login with the given password: 403 'choose a new password'",
      r.status_code == 403 and "choose a new password" in r.json()["detail"].lower(), r.text)
check("...no session is started", open_sessions(NEW_ID) == 0 and not sets_cookie(r), r.headers.get("set-cookie"))

r = login("new@essu.edu.ph", "wrong-password-xx")
check("a wrong password is still just 'incorrect' (401)", r.status_code == 401, r.text)

r = new_password("new@essu.edu.ph", "wrong-password-xx", "my-own-harbor-12")
check("new-password with a wrong given password: 401", r.status_code == 401, r.text)
check("...counted like a failed login (with the wrong login above: 2)",
      account(NEW_ID).failed_login_attempts == 2, account(NEW_ID).failed_login_attempts)

r = new_password("new@essu.edu.ph", GIVEN, "too-short")
check("a new password under 12 characters: 422", r.status_code == 422, r.status_code)

r = new_password("new@essu.edu.ph", GIVEN, GIVEN)
check("the same password as the given one: 400", r.status_code == 400 and "different" in r.json()["detail"], r.text)

r = client.post("/auth/new-password", json={"email": "new@essu.edu.ph", "password": GIVEN,
                                             "new_password": "x" * 257})
check("a new password over 256 characters: 422", r.status_code == 422, r.status_code)

MINE = "my-own-harbor-12"
r = new_password("new@essu.edu.ph", GIVEN, MINE)
check("new password saved (200)", r.status_code == 200 and r.json()["id"] == NEW_ID, r.text)
a = account(NEW_ID)
check("...the new password works, the given one doesn't",
      verify_password(MINE, a.hashed_password) and not verify_password(GIVEN, a.hashed_password))
check("...the flag is cleared", a.must_change_password is False)
check("...the session starts (cookie + session row)", sets_cookie(r) and open_sessions(NEW_ID) == 1,
      r.headers.get("set-cookie"))
check("...the failed-attempt count is reset", a.failed_login_attempts == 0, a.failed_login_attempts)
log = events("staff_password_changed")
check("...logged with the name only", log == ["New Person chose a new password."], log)
check("...neither password is in the log", all(GIVEN not in d and MINE not in d for d in log))

r = login("new@essu.edu.ph", MINE)
check("login with the new password works", r.status_code == 200 and sets_cookie(r), r.text)
r = new_password("new@essu.edu.ph", MINE, "another-harbor-12")
check("new-password when not needed: 400", r.status_code == 400 and "log in as usual" in r.json()["detail"].lower(), r.text)

# ---- a temporary password
TEMP = "temporary-pass-123"
r = client.post(f"/staff/{LA_ID}/password", json={"password": TEMP})
check("set temporary password works", r.status_code == 200, r.text)
check("...the account must choose a new password", account(LA_ID).must_change_password is True)
r = login("la@essu.edu.ph", TEMP)
check("login with the temporary password: 403 'choose a new password'",
      r.status_code == 403 and "choose a new password" in r.json()["detail"].lower(), r.text)

# lockout: the new-password step is not an easier way to guess
for _ in range(settings.max_failed_login_attempts):
    new_password("la@essu.edu.ph", "guess-guess-guess", "la-own-harbor-99")
r = new_password("la@essu.edu.ph", TEMP, "la-own-harbor-99")
check("repeated wrong guesses lock the account (423), even with the right one after",
      r.status_code == 423, r.text)
check("...and nothing changed", account(LA_ID).must_change_password is True
      and verify_password(TEMP, account(LA_ID).hashed_password))

# ---- accounts that were never flagged
r = login("head@essu.edu.ph", OWN)
check("an account without the flag logs in as before", r.status_code == 200 and sets_cookie(r), r.text)

r = client.post("/auth/new-password", json={"email": "nobody@essu.edu.ph", "password": "whatever-123",
                                             "new_password": "whatever-456789"})
check("unknown email on new-password: the usual 401", r.status_code == 401
      and r.json()["detail"] == "Incorrect email or password.", r.text)

check("Security Log has a label for the new event", "staff_password_changed" in EVENT_LABELS)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

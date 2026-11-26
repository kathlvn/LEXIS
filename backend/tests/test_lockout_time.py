r"""Checks that a locked staff account's message shows Philippine time (manuscript
Figure 6.3). Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_lockout_time.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.main as main  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import StaffUser  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

EMAIL, PASSWORD = "la@essu.edu.ph", "the-right-password"
with Session(engine) as s:
    s.add(StaffUser(full_name="Assistant", email=EMAIL, hashed_password=hash_password(PASSWORD),
                    role="legal_assistant", is_email_verified=True))
    s.commit()


def override_session():
    with Session(engine) as s:
        yield s


main.app.dependency_overrides[get_session] = override_session
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run
limiter.enabled = False

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def login(password):
    return client.post("/auth/login", json={"email": EMAIL, "password": password})


def set_lock(until):
    with Session(engine) as s:
        staff = s.exec(select(StaffUser)).one()
        staff.locked_until = until
        s.add(staff)
        s.commit()


# --- Five wrong passwords lock the account, and the message says until when -----------
for _ in range(5):
    r = login("wrong")
check("five wrong passwords: still the general message", r.status_code == 401, r.text)
with Session(engine) as s:
    locked_until = s.exec(select(StaffUser)).one().locked_until
minutes = (locked_until - datetime.utcnow()).total_seconds() / 60
check("locked for 15 minutes", 14 < minutes <= 15, minutes)
r = login(PASSWORD)
expected = f"Too many failed attempts. Try again after {(locked_until + timedelta(hours=8)).strftime('%I:%M %p')}."
check("the message shows Philippine time (UTC + 8 hours)", r.status_code == 423 and r.json()["detail"] == expected, r.text)
check("the same on the new-password step", client.post("/auth/new-password", json={
    "email": EMAIL, "password": PASSWORD, "new_password": "another-harbor-71"}).json()["detail"] == expected)

# --- Known times, including across midnight and noon ------------------------------------
tomorrow = (datetime.utcnow() + timedelta(days=1)).date()
for utc, ph in [((17, 30), "01:30 AM"), ((3, 5), "11:05 AM"), ((4, 0), "12:00 PM"), ((16, 0), "12:00 AM")]:
    set_lock(datetime(tomorrow.year, tomorrow.month, tomorrow.day, *utc))
    r = login(PASSWORD)
    check(f"{utc[0]:02}:{utc[1]:02} UTC is shown as {ph}", r.json()["detail"].endswith(f"after {ph}."), r.text)

# --- The lock itself is unchanged ---------------------------------------------------------
set_lock(datetime.utcnow() - timedelta(seconds=1))
r = login(PASSWORD)
check("once it has passed, the right password works", r.status_code == 200, r.text)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

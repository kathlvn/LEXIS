r"""Checks for GET /holidays/suggestions (task f). Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_holiday_suggestions.py
Uses a throwaway in-memory SQLite database: the real Postgres is never
touched. The last check is live (Official Gazette + Tesseract).
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.main as main  # noqa: E402
from app.core import holiday_import as hi  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import Holiday, SecurityEvent, StaffUser  # noqa: E402
from app.routers import holidays as holidays_router  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    head = StaffUser(full_name="Test Head", email="head@essu.edu.ph", hashed_password="x", role="head_attorney")
    assistant = StaffUser(full_name="Test Assistant", email="la@essu.edu.ph", hashed_password="x", role="legal_assistant")
    s.add(head); s.add(assistant); s.commit(); s.refresh(head); s.refresh(assistant)
    s.add(Holiday(holiday_date=date(2027, 1, 1), name="New Year (already entered)", holiday_type="regular"))
    s.commit()
    HEAD_ID, ASSISTANT_ID = head.id, assistant.id

current = {"id": HEAD_ID}


def override_session():
    with Session(engine) as s:
        yield s


def override_staff():
    with Session(engine) as s:
        return s.get(StaffUser, current["id"])


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def events(kind):
    with Session(engine) as s:
        return [e.detail for e in s.exec(select(SecurityEvent).where(SecurityEvent.event_type == kind)).all()]


FAKE = hi.ProclamationImport(
    2027, "https://www.officialgazette.gov.ph/p/", "https://www.officialgazette.gov.ph/downloads/p.pdf",
    "Proclamation No. 1427 s. 2026",
    [hi.SuggestedHoliday(date(2027, 1, 1), "New Year's Day", "regular"),
     hi.SuggestedHoliday(date(2027, 5, 1), "Labor Day", "regular", "The PDF says Sunday...")],
    ["Rizal Day 30 December"],
)


reads = []  # years the (fake) import was really run for


def fake_ok(year):
    reads.append(year)
    return FAKE


def fake_error(year):
    raise hi.HolidayImportError("No holiday proclamation for 2031 was found on the Official Gazette yet.")


def fake_crash(year):
    raise KeyError("secret internal detail")


# 1. Roles and input limits.
current["id"] = ASSISTANT_ID
r = client.get("/holidays/suggestions?year=2027")
check("legal assistant gets 403", r.status_code == 403, r.status_code)
current["id"] = HEAD_ID
holidays_router.import_from_gazette = fake_ok
check("year missing -> 422", client.get("/holidays/suggestions").status_code == 422)
check("year 1999 -> 422", client.get("/holidays/suggestions?year=1999").status_code == 422)
check("year 'abc' -> 422", client.get("/holidays/suggestions?year=abc").status_code == 422)

limiter.reset()  # the refused calls above (403, 422) count toward the 5/hour limit too (SEC-07)

# 2. Success shape.
r = client.get("/holidays/suggestions?year=2027")
body = r.json()
check("Head Attorney gets 200", r.status_code == 200, r.text)
check("title and both links returned", body.get("title") == FAKE.title and body.get("pdf_url") == FAKE.pdf_url)
by_date = {s["holiday_date"]: s for s in body.get("suggestions", [])}
check("already-entered date marked", by_date.get("2027-01-01", {}).get("already_listed_as") == "New Year (already entered)")
check("new date not marked", by_date.get("2027-05-01", {}).get("already_listed_as") is None)
check("check note passed through", by_date.get("2027-05-01", {}).get("check_note") == "The PDF says Sunday...")
check("unreadable lines passed through", body.get("unreadable_lines") == ["Rizal Day 30 December"])
check("nothing saved to Holiday", len(Session(engine).exec(select(Holiday)).all()) == 1)
check("success logged", any("2 dates read" in d and "Test Head" in d for d in events("holiday_import_checked")))
check("fresh read not logged as saved", not any("saved result" in d for d in events("holiday_import_checked")))

# 2b. Saved read (checklist #12): the same year again within the hour.
before = len(reads)
with Session(engine) as s:
    s.add(Holiday(holiday_date=date(2027, 5, 1), name="Labor Day (added after the check)", holiday_type="regular"))
    s.commit()
r = client.get("/holidays/suggestions?year=2027")
by_date = {s["holiday_date"]: s for s in r.json().get("suggestions", [])}
check("repeat check within the hour: no new read", r.status_code == 200 and len(reads) == before, (r.status_code, reads))
check("saved read: 'already listed' is still fresh",
      by_date.get("2027-05-01", {}).get("already_listed_as") == "Labor Day (added after the check)", by_date.get("2027-05-01"))
check("saved read logged as such", any("saved result" in d and "Test Head" in d for d in events("holiday_import_checked")))
saved_at, saved = holidays_router._saved_reads[2027]
holidays_router._saved_reads[2027] = (saved_at - holidays_router.SAVED_READ_SECONDS - 1, saved)  # an hour later
r = client.get("/holidays/suggestions?year=2027")
check("after an hour: read again", r.status_code == 200 and len(reads) == before + 1, reads)

# 3. Errors.
holidays_router.import_from_gazette = fake_error
r = client.get("/holidays/suggestions?year=2031")
check("friendly error -> 502 with the message", r.status_code == 502 and "No holiday proclamation for 2031" in r.json()["detail"], r.text)
holidays_router.import_from_gazette = fake_crash
r = client.get("/holidays/suggestions?year=2031")
check("unexpected error -> 502, raw error hidden", r.status_code == 502 and "secret" not in r.text, r.text)
check("failures logged", len(events("holiday_import_failed")) == 2 and any("KeyError" in d for d in events("holiday_import_failed")))
check("failures not kept", 2031 not in holidays_router._saved_reads)
holidays_router.import_from_gazette = fake_ok
limiter.reset()  # six calls so far: the 5/hour limit would answer 429
before = len(reads)
r = client.get("/holidays/suggestions?year=2031")
check("retry after a failure really reads again", r.status_code == 200 and reads[before:] == [2031], (r.status_code, reads))

# 4. Rate limit: 5 calls an hour (more made above for the Head Attorney... reset first).
limiter.reset()
r = [client.get("/holidays/suggestions?year=2027") for _ in range(6)]
codes = [x.status_code for x in r]
check("6th call in an hour -> 429", codes == [200] * 5 + [429], codes)
check("the limit is per hour", "1 hour" in r[-1].text, r[-1].text)
check("429 logged as rate_limit_exceeded", any("/holidays/suggestions" in d for d in events("rate_limit_exceeded")))

# 5. Live, through the endpoint (network + OCR).
limiter.reset()
holidays_router._saved_reads.clear()  # don't get the fake read back
holidays_router.import_from_gazette = hi.import_from_gazette
r = client.get("/holidays/suggestions?year=2027")
if r.status_code == 502 and "limiting requests" in r.text:
    print("SKIP live check: the Official Gazette is limiting requests right now; run again in a few minutes")
else:
    check("live 2027 through the endpoint: 18 dates", r.status_code == 200 and len(r.json()["suggestions"]) == 18, r.text[:300])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

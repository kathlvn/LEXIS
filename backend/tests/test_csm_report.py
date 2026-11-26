r"""Checks for the CSM section of GET /reports (checklist #5). Plain Python, no pytest.
From backend/:
    ..\.venv\Scripts\python.exe tests\test_csm_report.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.main as main  # noqa: E402
from app.core import csm_form  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.models.models import CsmResponse, StaffUser  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    staff = StaffUser(full_name="Test Assistant", email="la@essu.edu.ph", hashed_password="x", role="legal_assistant")
    s.add(staff); s.commit(); s.refresh(staff)
    STAFF_ID = staff.id


def override_session():
    with Session(engine) as s:
        yield s


def override_staff():
    with Session(engine) as s:
        return s.get(StaffUser, STAFF_ID)


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = override_staff
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


VERSION = csm_form.load_csm_form()["version"]
SQD = [f"SQD{i}" for i in range(9)]
next_request_id = [100]


def add(when_utc, sqd_value, cc, comment=None, version=VERSION):
    answers = {"CC1": cc[0], "CC2": cc[1], "CC3": cc[2]}
    answers.update({q: ("NA" if q == "SQD5" else sqd_value) for q in SQD})
    next_request_id[0] += 1
    with Session(engine) as s:
        s.add(CsmResponse(request_id=next_request_id[0], form_version=version, answers=json.dumps(answers),
                          comment=comment, submitted_at=when_utc))
        s.commit()


def november():
    r = client.get("/reports?period=month&year=2026&month=11")
    return r.status_code, r.json().get("csm")


# --- No surveys ---------------------------------------------------------------
code, c = november()
check("report loads with a csm section", code == 200 and c is not None, code)
check("no surveys: 0 responses, nothing shown", c["responses"] == 0 and not c["shown"] and c["questions"] == [], c)

# --- Fewer than 3: count only -----------------------------------------------------
# 2026-10-31 17:00 UTC is 1 November 01:00 in the Philippines: counts in November.
add(datetime(2026, 10, 31, 17, 0), "5", ("1", "1", "1"), comment="Very helpful")
add(datetime(2026, 11, 10, 2, 0), "4", ("4", "NA", "NA"))
add(datetime(2026, 10, 15, 2, 0), "1", ("1", "1", "1"), comment="October answer")       # October: not counted
add(datetime(2026, 11, 12, 2, 0), "1", ("1", "1", "1"), comment="Old form", version="old-1")  # older form
code, c = november()
check("2 responses: count shown", c["responses"] == 2, c.get("responses"))
check("2 responses: no breakdown, no rate, no suggestions sent",
      not c["shown"] and c["questions"] == [] and c["overall_rate"] is None and c["suggestions"] == [], c)
check("older form version counted apart", c["other_version_responses"] == 1, c.get("other_version_responses"))
check("survey is not a draft", c["placeholder"] is False)

# --- 3 responses: totals --------------------------------------------------------------
add(datetime(2026, 11, 20, 2, 0), "2", ("2", "3", "2"), comment="Slow <b>reply</b>")
code, c = november()
q = {x["id"]: x for x in c["questions"]}
check("3 responses: breakdown shown", c["shown"] and c["responses"] == 3, c.get("responses"))
check("12 questions in the file's order", list(q) == ["CC1", "CC2", "CC3"] + SQD, list(q))
check("CC1 choice counts", [o["count"] for o in q["CC1"]["options"]] == [1, 1, 0, 1], q["CC1"]["options"])
check("CC questions are not scored", q["CC1"]["scored"] is False and q["CC1"]["rate"] is None)
check("SQD0: 2 of 3 satisfied = 66.7%",
      (q["SQD0"]["satisfied"], q["SQD0"]["rated"], q["SQD0"]["rate"]) == (2, 3, 66.7), q["SQD0"])
check("SQD0 choice counts (1..5, N/A)", [o["count"] for o in q["SQD0"]["options"]] == [0, 1, 0, 1, 1, 0],
      q["SQD0"]["options"])
check("SQD5 all N/A: no rate", q["SQD5"]["rated"] == 0 and q["SQD5"]["rate"] is None, q["SQD5"])
check("overall: 16 of 24 rated = 66.7%", c["overall_rate"] == 66.7, c["overall_rate"])
check("suggestions newest first, as typed (escaped on the page)",
      c["suggestions"] == ["Slow <b>reply</b>", "Very helpful"], c["suggestions"])
text = json.dumps(c)
check("no request ids or dates in the CSM section",
      "request_id" not in text and "submitted_at" not in text and "101" not in text, text[:200])

# --- Quarter and year include the same November answers --------------------------------
r = client.get("/reports?period=quarter&year=2026&quarter=4").json()["csm"]
check("Q4 includes October + November (4 current-form responses)", r["responses"] == 4, r["responses"])

# --- Broken form file -------------------------------------------------------------------
saved_path = csm_form.CSM_FORM_PATH
try:
    bad = Path(tempfile.mkdtemp()) / "csm_form.json"
    bad.write_text('{"version": ', encoding="utf-8")
    csm_form.CSM_FORM_PATH = bad
    csm_form._cache = None
    code, c = november()
    check("broken file: report still loads, says what's wrong",
          code == 200 and c["available"] is False and "not valid JSON" in c["problem"], c)
finally:
    csm_form.CSM_FORM_PATH = saved_path
    csm_form._cache = None

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

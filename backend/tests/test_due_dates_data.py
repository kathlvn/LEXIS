r"""Dates and data rules: what the database itself refuses (unique Request ID,
staff email, holiday date; links to clients and requests that must exist),
the Request ID year and the due date at Philippine midnight, the
client_*_at_submission snapshot surviving later edits, a workflow step in use
that can't be switched off, and an integrity check over every record made.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_due_dates_data.py
In-memory SQLite, temp folder for files, emails recorded (see lexis_world.py).
SQL here only ever runs inside a transaction that is rolled back, on the
in-memory database.

Checks marked KNOWN are open findings from qa-report.md. They print KNOWN
while the problem is still there and don't fail the run; once fixed they print
PASS, and the mark can be removed so they guard the fix.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexis_world as q  # noqa: E402  (must come before anything from app/)
from lexis_world import ok, prof, submit  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlmodel import Session  # noqa: E402

import app.routers.requests as rq  # noqa: E402
from app.core.working_days import compute_due_date, next_working_day, to_ph_date  # noqa: E402

m, E = q.m, q.engine
w = q.build()
T = w.t
H, L = w.c["head"], w.c["la1"]
LR = m.LegalServiceRequest
q.edit(m.SystemSetting, "max_requests_per_day", value="500")
results, known_open = [], []


def check(name, good, detail="", known=None):
    if known and not good:
        known_open.append(known)
        print(f"KNOWN {name} ({known} in qa-report.md)" + (f"  -> {detail}" if detail != "" else ""))
        return
    results.append(bool(good))
    print(("PASS " if good else "FAIL ") + name + (f"  -> {detail}" if detail != "" and not good else ""))


def db_refuses(stmt, **params):
    """Runs SQL in a transaction that is always rolled back. True = the database refused it."""
    cx = E.connect()
    tr = cx.begin()
    try:
        cx.execute(text(stmt), params)
        return False
    except Exception:
        return True
    finally:
        tr.rollback()
        cx.close()


def count(stmt):
    with E.connect() as cx:
        return cx.execute(text(stmt)).scalar()


# --- 1. what the database itself refuses --------------------------------------
cases = [
    ("a duplicate Request ID", "UPDATE legalservicerequest SET tracking_id=:a WHERE tracking_id=:b",
     dict(a=T["A1"], b=T["B1"])),
    ("a request for a client that doesn't exist",
     "UPDATE legalservicerequest SET requester_id=999999 WHERE tracking_id=:a", dict(a=T["A1"])),
    ("a request with no description", "UPDATE legalservicerequest SET description=NULL WHERE tracking_id=:a",
     dict(a=T["A1"])),
    ("a duplicate staff email", "UPDATE staffuser SET email='la2@essu.edu.ph' WHERE email='la1@essu.edu.ph'", {}),
    ("a duplicate holiday date", "INSERT INTO holiday (holiday_date, name, holiday_type, created_at) "
                                 "VALUES ('2027-01-01', 'x', 'regular', CURRENT_TIMESTAMP)", {}),
    ("a document of a request that doesn't exist", "UPDATE document SET request_id=999999 WHERE id=:d",
     dict(d=w.d["client"])),
]
for name, stmt, params in cases:
    check(f"the database refuses {name}", db_refuses(stmt, **params))

# --- 2. Request ID year around New Year (Philippine time) ---------------------


class NewYear(datetime):
    @classmethod
    def utcnow(cls):
        return datetime(2026, 12, 31, 16, 30)  # = 1 Jan 2027, 00:30 in the Philippines


real_datetime = rq.datetime
rq.datetime = NewYear
try:
    with Session(E) as s:
        tid = rq.generate_tracking_id(s)
finally:
    rq.datetime = real_datetime
check("a Request ID made at 00:30 on 1 Jan 2027 (PH time) carries 2027", tid.startswith("LEO-2027-"), tid)

# --- 3. due date at Philippine midnight (Legal Advice, 2 working days) --------
before = to_ph_date(compute_due_date(datetime(2026, 11, 26, 15, 59), 2, set()))  # Thu 23:59 PH
after = to_ph_date(compute_due_date(datetime(2026, 11, 26, 16, 1), 2, set()))   # Fri 00:01 PH
check("filed Thu 26 Nov 23:59 PH: due Mon 30 Nov", before == datetime(2026, 11, 30).date(), before)
check("filed Fri 27 Nov 00:01 PH: due Tue 1 Dec", after == datetime(2026, 12, 1).date(), after)

# --- 4. the snapshot at submission survives later edits -----------------------
B = w.c["B"]
snap0 = {k: v for k, v in q.one(LR, LR.tracking_id == T["B1"]).__dict__.items() if k.endswith("_at_submission")}
B.patch("/portal/me", json={**prof("Bautista-Reyes", "Bea", "essu_employee", position="New Position"),
                            "privacy_consent": True})
ok(H.put(f"/requests/{T['B1']}/client", json=prof("Bautista-Head", "Bea", "essu_employee", position="Head edit")),
   "Head Attorney edits the client")
snap1 = {k: v for k, v in q.one(LR, LR.tracking_id == T["B1"]).__dict__.items() if k.endswith("_at_submission")}
check("the client's profile was really changed", q.one(m.Requester, m.Requester.email == "b@example.com").last_name
      == "Bautista-Head")
check("an old request keeps the client details it was filed with", snap0 == snap1,
      {k: (snap0[k], snap1[k]) for k in snap0 if snap0[k] != snap1[k]})

# --- 5. a work suspension added after filing (the office decides: DATA-01) ----
c5 = q.door("due@example.com", prof("Due", "Dora", "essu_employee"))
tid5 = ok(submit(c5, "legal_advice", "Due-date test", letter=True), "filed")["tracking_id"]
r5 = q.one(LR, LR.tracking_id == tid5)
due0 = to_ph_date(r5.due_date)
first_day = next_working_day(to_ph_date(r5.submitted_at), set())
susp = ok(H.post("/holidays", json={"holiday_date": first_day.isoformat(), "name": "Work suspension (test)",
                                    "holiday_type": "suspension"}), "suspension")
due1 = to_ph_date(q.one(LR, LR.tracking_id == tid5).due_date)
check("a suspension added after filing moves that request's due date", due1 != due0,
      f"due {due0} before and after", known="DATA-01")
ok(H.delete(f"/holidays/{susp['id']}"), "remove the suspension")

# --- 6. switching off an assistant who still holds open requests --------------
c6 = q.door("held@example.com", prof("Held", "Hana", "essu_employee"))
tid6 = ok(submit(c6, "legal_advice", "Held by Ben", letter=True), "filed")["tracking_id"]
ok(H.patch(f"/requests/{tid6}/reassign", json={"new_officer_id": w.id["la2"]}), "to Ben")
off = H.patch(f"/staff/{w.id['la2']}", json={"is_active": False})
still = q.one(LR, LR.tracking_id == tid6).assigned_officer_id == w.id["la2"]
check("switching off an assistant with open requests is refused, with the count (API-03)",
      off.status_code == 409 and "open request" in off.text and still, f"status {off.status_code} {off.text[:120]}")
open_with_inactive = count("SELECT count(*) FROM legalservicerequest r JOIN staffuser u ON u.id=r.assigned_officer_id "
                           "WHERE NOT u.is_active AND NOT r.is_cancelled AND NOT r.is_rejected")
ok(H.patch(f"/staff/{w.id['la2']}", json={"is_active": True}), "back on")

# --- 7. a workflow step in use can't be switched off --------------------------
c7 = q.door("step@example.com", prof("Step", "Sam", "essu_employee"))
tid7 = ok(submit(c7, "legal_advice", "At Under Review", letter=True), "filed")["tracking_id"]
ok(H.patch(f"/requests/{tid7}/reassign", json={"new_officer_id": w.id["la1"]}), "assign")
ok(L.patch(f"/requests/{tid7}/status", json={"new_status": "Received"}), "Received")
ok(L.patch(f"/requests/{tid7}/status", json={"new_status": "Under Review"}), "Under Review")
r7 = H.patch("/service-types/legal_advice", json={"optional_steps": ["Received", "For Attorney Review", "Approved"]})
check("a step a request is at can't be switched off", r7.status_code == 409, f"{r7.status_code} {r7.text[:120]}")

# --- 8. integrity check over every record made above --------------------------
audits = {
    "request at a step of a different service":
        "SELECT count(*) FROM legalservicerequest r JOIN workflowsteptemplate s ON s.id=r.current_step_id "
        "WHERE s.service_type_id<>r.service_type_id",
    "request both cancelled and rejected": "SELECT count(*) FROM legalservicerequest WHERE is_cancelled AND is_rejected",
    "closed request without a closing time":
        "SELECT count(*) FROM legalservicerequest WHERE (is_cancelled OR is_rejected) AND closed_at IS NULL",
    "open request with a closing time":
        "SELECT count(*) FROM legalservicerequest WHERE NOT is_cancelled AND NOT is_rejected AND closed_at IS NOT NULL",
    "survey on a request that isn't Completed":
        "SELECT count(*) FROM csm_response c JOIN legalservicerequest r ON r.id=c.request_id "
        "LEFT JOIN workflowsteptemplate s ON s.id=r.current_step_id WHERE coalesce(s.status_name,'')<>'Completed'",
    "new file version replacing a file of another request":
        "SELECT count(*) FROM document d JOIN document o ON o.id=d.supersedes_document_id WHERE o.request_id<>d.request_id",
    "endorsement letter belonging to another client":
        "SELECT count(*) FROM requester c JOIN document d ON d.id=c.endorsement_document_id "
        "JOIN legalservicerequest r ON r.id=d.request_id WHERE r.requester_id<>c.id",
    "follow-up of another client's request":
        "SELECT count(*) FROM legalservicerequest f JOIN legalservicerequest o ON o.id=f.followup_of_id "
        "WHERE o.requester_id<>f.requester_id",
    "request with no snapshot of the client":
        "SELECT count(*) FROM legalservicerequest WHERE client_name_at_submission IS NULL",
    "timed service with no due date":
        "SELECT count(*) FROM legalservicerequest r JOIN servicetype t ON t.id=r.service_type_id "
        "WHERE coalesce(t.default_processing_days,0)>0 AND r.due_date IS NULL",
    "clients sharing one email (any capitals)":
        "SELECT count(*) FROM (SELECT lower(email) FROM requester WHERE email IS NOT NULL GROUP BY 1 HAVING count(*)>1)",
}
for name, stmt in audits.items():
    n = count(stmt)
    check(f"integrity: no {name}", n == 0, f"{n} found")
check("integrity: no open request with a switched-off assistant (while Ben was off)", open_with_inactive == 0,
      f"{open_with_inactive} found")

check("no network connection or .env read was attempted", not q.REFUSED, q.REFUSED)
print(f"\n{sum(results)}/{len(results)} checks passed; {len(known_open)} known open findings still there.")
sys.exit(0 if all(results) else 1)

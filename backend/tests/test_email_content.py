r"""Emails: the real send_email (headers, retries, daily budgets) with only the
mail server faked, so nothing leaves this computer. Every email of one
client's requests is checked: no description, internal note, return comment,
message text or staff name; what the client should be told does reach them;
one recipient; From is the office. Also: the code email's expiry, the
"valid until" date, a staff action still saved when the mail server is down,
the daily budgets (lowered to 6, codes 3) and a corrected client email.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_email_content.py
In-memory SQLite, temp folder for files (see lexis_world.py).

Checks marked KNOWN are open findings from qa-report.md. They print KNOWN
while the problem is still there and don't fail the run; once fixed they print
PASS, and the mark can be removed so they guard the fix.
"""
import importlib
import re
import smtplib
import sys
from datetime import datetime
from email.utils import getaddresses
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexis_world as q  # noqa: E402  (must come before anything from app/)
from lexis_world import ok, prof, submit  # noqa: E402
import app.core.mailer as mailer  # noqa: E402
import app.core.verification as verification  # noqa: E402
from app.core.config import settings  # noqa: E402

results, known_open = [], []


def check(name, good, detail="", known=None):
    if known and not good:
        known_open.append(known)
        print(f"KNOWN {name} ({known} in qa-report.md)" + (f"  -> {detail}" if detail != "" else ""))
        return
    results.append(bool(good))
    print(("PASS " if good else "FAIL ") + name + (f"  -> {detail}" if detail != "" and not good else ""))


# lexis_world replaced send_email with a recorder; reload the mailer to test the real one.
mailer = importlib.reload(mailer)                 # undo the recorder: the real send_email again
verification.send_email = mailer.send_email
verification.email_is_configured = mailer.email_is_configured
import app.routers.public_access as public_access  # noqa: E402
public_access.code_email_available = mailer.code_email_available   # the door's budget check (MAIL-01)
settings.smtp_host, settings.smtp_username, settings.smtp_password = "smtp.fake.test", "qa", "qa"
settings.mail_from_address = "noreply@example.com"
settings.public_site_url = "https://lexis.example.com"
mailer.RETRY_DELAY_SECONDS = 0
SENT = []


class FakeSMTP:
    fail = 0            # how many next attempts raise

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        pass

    def send_message(self, msg):
        if FakeSMTP.fail:
            FakeSMTP.fail -= 1
            raise smtplib.SMTPServerDisconnected("qa: server went away")
        SENT.append(msg)
        q.MAIL.append((msg["To"], msg["Subject"], msg.get_content(), "smtp"))   # so door() finds codes


mailer._open_connection = lambda: FakeSMTP()

w = q.build()
m = q.m
H, L = w.c["head"], w.c["la1"]
LR = m.LegalServiceRequest
q.edit(m.SystemSetting, "max_requests_per_day", value="500")

# ---------- 1. one client's whole request, every email checked ----------
print("== 1. every email of one request")
SECRET = ["SECRET-DESC", "SECRET-RETURN", "SECRET-MSG", "SECRET-REASSIGN", "SECRET-VNOTE", "SECRET-DECLINE", "SECRET-REOPEN"]
STAFF_NAMES = ["Ana Assistant", "Ben Assistant", "Ella Head", "Dan Attorney"]
X = "mailx@example.com"
start = len(SENT)
cx = q.door(X, prof("Mail", "Xena", "essu_employee"))
tid = ok(submit(cx, "legal_advice", "SECRET-DESC private matter", letter=True), "x")["tracking_id"]
ok(H.patch(f"/requests/{tid}/reassign", json={"new_officer_id": w.id["la1"]}), "assign")
ok(L.post(f"/requests/{tid}/client/verify", json={"method": "endorsement_letter", "note": "SECRET-VNOTE"}), "verify")
ok(L.patch(f"/requests/{tid}/status", json={"new_status": "Received"}), "received")
n_before = len(SENT)
ok(L.patch(f"/requests/{tid}/status", json={"new_status": "Under Review", "is_waiting_for_client": True, "note": "OK-NOTE please send the contract"}), "two at once")
check("two changes saved together = one email", len(SENT) - n_before == 1, len(SENT) - n_before)
ok(L.post(f"/requests/{tid}/messages", data={"body": "SECRET-MSG staff text"}), "msg")
up = ok(L.post(f"/requests/{tid}/documents/staff", files=[("files", ("OK-RELEASED.pdf", q.PDF))]), "doc")
ok(L.patch(f"/requests/{tid}/documents/{up[0]['id']}/release", json={"released": True}), "release")
cu = ok(cx.post(f"/requests/{tid}/documents/client", files=[("files", ("mine.pdf", q.PDF))]), "client doc")
ok(L.patch(f"/requests/{tid}/documents/{cu[0]['id']}", json={"status": "revision_required", "note": "OK-REVISE sign page 2"}), "revise")
ok(L.patch(f"/requests/{tid}/status", json={"is_waiting_for_client": False}), "clear wait")
ok(L.patch(f"/requests/{tid}/status", json={"reassignment_reason": "SECRET-REASSIGN on leave"}), "ask reassign")
ok(L.patch(f"/requests/{tid}/status", json={"new_status": "Processing"}), "processing")
ok(L.patch(f"/requests/{tid}/status", json={"new_status": "For Attorney Review"}), "review")
ok(H.post(f"/requests/{tid}/return", json={"comment": "SECRET-RETURN add the legal basis"}), "return")
ok(L.patch(f"/requests/{tid}/status", json={"new_status": "For Attorney Review"}), "review again")
ok(H.patch(f"/requests/{tid}/status", json={"new_status": "Approved"}), "approve")
ok(H.patch(f"/requests/{tid}/status", json={"new_status": "Completed"}), "complete")
# second request: cancel, reopen, reminder, withdraw
tid2 = ok(submit(cx, "legal_research", "SECRET-DESC second", letter=False), "x2")["tracking_id"]
ok(H.patch(f"/requests/{tid2}/reassign", json={"new_officer_id": w.id["la1"]}), "assign2")
ok(H.post(f"/requests/{tid2}/close", json={"action": "cancel", "reason": "OK-CLOSE duplicate filing"}), "cancel")
ok(H.post(f"/requests/{tid2}/reopen", json={"reason": "SECRET-REOPEN by mistake"}), "reopen")
ok(L.patch(f"/requests/{tid2}/status", json={"is_waiting_for_client": True, "note": "OK-NOTE2 your ID"}), "wait2")
ok(L.post(f"/requests/{tid2}/remind"), "remind")
ok(cx.post(f"/portal/requests/{tid2}/withdraw", json={"reason": "OK-WITHDRAW no longer needed"}), "withdraw")
# decline (reason kept internal) and revoke (reason shared) on another client
Y = "mailY@example.com".lower()
cy = q.door(Y, prof("Mail", "Yuri", "essu_employee"))
ty = ok(submit(cy, "legal_advice", "SECRET-DESC y", letter=True), "y")["tracking_id"]
ok(H.patch(f"/requests/{ty}/reassign", json={"new_officer_id": w.id["la1"]}), "ay")
ok(L.post(f"/requests/{ty}/client/decline", json={"reason": "SECRET-DECLINE letter unsigned", "tell_client": False}), "decline")
ok(H.post(f"/requests/{ty}/client/verify", json={"method": "endorsement_letter"}), "verify y")
ok(H.post(f"/requests/{ty}/client/revoke", json={"reason": "OK-REVOKE left the office", "tell_client": True}), "revoke")

mails = SENT[start:]
links = [u for x in mails for u in re.findall(r"https?://\S+", x.get_content())]
check("every link in the emails points to the LEXIS site",
      all(u.startswith(settings.public_site_url.rstrip("/") + "/") for u in links), links[:3])
all_bodies = "\n".join(x.get_content() + x["Subject"] for x in mails)
check("no description, internal note, return comment, message text or staff name in any email",
      not any(s in all_bodies for s in SECRET + STAFF_NAMES), [s for s in SECRET + STAFF_NAMES if s in all_bodies])
want = ["OK-NOTE", "OK-RELEASED", "OK-REVISE", "OK-CLOSE", "OK-NOTE2", "OK-REVOKE", tid, tid2]
check("what the client should be told reaches them", all(s in all_bodies for s in want), [s for s in want if s not in all_bodies])
check("each email has exactly one recipient, the client", all(len(getaddresses(x.get_all("To", []))) == 1 and x["To"] in (X, Y) for x in mails))
check("From is the office", all(x["From"] == "ESSU Legal Office <noreply@example.com>" for x in mails), {x["From"] for x in mails})
check("withdraw email tells the client the request no longer accepts messages",
      any(tid2 in x["Subject"] and "no longer accepts messages" in x.get_content() for x in mails[-8:]))

# ---------- 2. code email text vs the real expiry ----------
print("== 2. sign-in code email vs the stored expiry")
Z = "codez@example.com"
q.new_client().post("/access/contact/send-code", json={"contact": Z})
v = q.one(m.ContactVerification, m.ContactVerification.contact_value == Z)
mins = round((v.expires_at - v.created_at).total_seconds() / 60)
body = [x for x in SENT if x["To"] == Z][-1].get_content()
said = int(re.search(r"expires in (\d+) minutes", body).group(1))
check("code email says the same expiry the database keeps", said == mins, f"email {said} min, stored {mins} min")
check("code email holds no link (nothing to click in a code email)", "http" not in body)

# ---------- 3. verified email date vs the Philippine date ----------
print("== 3. 'valid until' date in the verified email")
import app.routers.clients as clients_router


class EarlyMorning(datetime):
    @classmethod
    def utcnow(cls):
        return datetime(2026, 11, 26, 17, 0)   # = 27 Nov 2026, 01:00 in the Philippines


V = "early@example.com"
cv = q.door(V, prof("Early", "Eve", "essu_employee"))
tv = ok(submit(cv, "legal_advice", "verify early", letter=True), "v")["tracking_id"]
ok(H.patch(f"/requests/{tv}/reassign", json={"new_officer_id": w.id["la1"]}), "av")
clients_router.datetime = EarlyMorning
ok(L.post(f"/requests/{tv}/client/verify", json={"method": "endorsement_letter"}), "verify early")
clients_router.datetime = datetime
exp = q.one(m.Requester, m.Requester.email == V).verification_expires_at
from app.core.working_days import to_ph_date
said = re.search(r"valid until ([A-Z][a-z]+ \d\d, \d{4})", [x for x in SENT if x["To"] == V][-1].get_content()).group(1)
check("'valid until' in the email is the Philippine date", said == to_ph_date(exp).strftime("%B %d, %Y"),
      f"email says {said}; expires {exp} UTC = {to_ph_date(exp)} PH")

# ---------- 4. mail server down: the staff action must still be saved ----------
print("== 4. mail server down")
FakeSMTP.fail = 99
n0 = len(SENT)
r = L.patch(f"/requests/{tv}/status", json={"new_status": "Received"})
st = q.one(LR, LR.tracking_id == tv)
FakeSMTP.fail = 0
check("status change is saved and answered normally when the email cannot be sent", r.status_code == 200 and st.current_step_id is not None, r.status_code)

# ---------- 5. daily budgets ----------
print("== 5. daily budgets (limits lowered to 6 in all, 3 for codes)")
settings.mail_daily_limit, settings.mail_daily_code_limit = 6, 3
mailer._sent_today = mailer._codes_today = 0
mailer._limit_event_logged = mailer._code_limit_event_logged = False
n0 = len(SENT)
answers = []
for i in range(5):
    rr = q.new_client().post("/access/contact/send-code", json={"contact": f"budget{i}@example.com"})
    answers.append((rr.status_code, rr.json().get("detail") or rr.json().get("message") or str(rr.json())[:80]))
codes_sent = len(SENT) - n0
check("codes stop at the code budget (3)", codes_sent == 3, codes_sent)
check("a client asking after the code budget ran out is not told a code was sent",
      "has been sent" not in answers[4][1].lower(), answers[4][1])
ev = q.rows(m.SecurityEvent, m.SecurityEvent.event_type == "code_email_limit_reached")
check("reaching the code budget is logged once", len(ev) == 1, len(ev))
n1 = len(SENT)
for s_ in ("Under Review", "Processing", "For Attorney Review", "Approved"):  # one step at a time (API-05)
    H.patch(f"/requests/{tv}/status", json={"new_status": s_})
updates_sent = len(SENT) - n1
check("updates still go out after codes run out, until the overall limit", updates_sent == 3, updates_sent)
ev2 = q.rows(m.SecurityEvent, m.SecurityEvent.event_type == "email_daily_limit_reached")
check("reaching the overall limit is logged once", len(ev2) == 1, len(ev2))
# a failed send still uses up the budget?
settings.mail_daily_limit = 100
mailer._sent_today = mailer._codes_today = 0
FakeSMTP.fail = 99
r = H.patch(f"/requests/{tv}/status", json={"is_waiting_for_client": True, "note": "Send the ID."})  # emails the client
FakeSMTP.fail = 0
check("an email that was never delivered does not use up the day's budget",
      r.status_code == 200 and mailer._sent_today == 0, (r.status_code, mailer._sent_today))

# ---------- 6. editing a client's details keeps their sign-in email ----------
print("== 5b. the mailer sends to plain addresses only (SEC-05)")
mailer._sent_today = mailer._codes_today = 0
n0 = len(SENT)
refused = [mailer.send_email(a, "Test", "Body", "code") for a in
           ("x1<victim@example.com>", "a@example.com, b@example.com", "a@example.com\r\nBcc: z@example.com")]
check("the mailer refuses display names, lists and line breaks", refused == [False] * 3 and len(SENT) == n0, refused)
check("...without using the day's budget", (mailer._sent_today, mailer._codes_today) == (0, 0),
      (mailer._sent_today, mailer._codes_today))
check("a plain address is still sent", mailer.send_email("plain@example.com", "Test", "Body") and len(SENT) == n0 + 1)

print("== 6. the Head Attorney edits a client's details (open request)")
ok(H.put(f"/requests/{tv}/client", json={**prof("Early", "Eve", "essu_employee"), "email": "early.new@example.com"}),
   "edit details")
stored = q.one(m.Requester, m.Requester.id == q.one(LR, LR.tracking_id == tv).requester_id).email
check("editing a client's details doesn't change the email they sign in with", stored == V, stored)
n0 = len(SENT)
ok(H.patch(f"/requests/{tv}/status", json={"new_status": "Completed"}), "after the edit")
check("the next update still goes to the client's email", [x["To"] for x in SENT[n0:]] == [V],
      [x["To"] for x in SENT[n0:]])
check("no network connection or .env read was attempted", not q.REFUSED, q.REFUSED)
print(f"\n{sum(results)}/{len(results)} checks passed; {len(known_open)} known open findings still there.")
sys.exit(0 if all(results) else 1)

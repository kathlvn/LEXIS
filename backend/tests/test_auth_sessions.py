r"""Sign-in and sessions: clients can't reach each other's requests or files,
sign-in codes (one use, newest only, 5 tries, expiry, no hint whether an email
is known), session time limits (30 min idle; staff 12 h, clients 2 h), sign-out
ending the session for every copy of the cookie, staff passwords (temporary
password, lockout after 5 wrong tries) and deactivation.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_auth_sessions.py
In-memory SQLite, temp folder for files, emails recorded (see lexis_world.py).

Checks marked KNOWN are open findings from qa-report.md. They print KNOWN
while the problem is still there and don't fail the run; once fixed they print
PASS, and the mark can be removed so they guard the fix.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexis_world as q  # noqa: E402  (must come before anything from app/)
from lexis_world import PDF, PW, ok, prof  # noqa: E402
from app.core.csm_form import load_csm_form  # noqa: E402

m = q.m
w = q.build()
T, c = w.t, w.c
snap = q.Snapshot()
results, known_open = [], []


def check(name, good, detail="", known=None):
    if known and not good:
        known_open.append(known)
        print(f"KNOWN {name} ({known} in qa-report.md)" + (f"  -> {detail}" if detail != "" else ""))
        return
    results.append(bool(good))
    print(("PASS " if good else "FAIL ") + name + (f"  -> {detail}" if detail != "" and not good else ""))


def ago(**kw):
    return datetime.utcnow() - timedelta(**kw)


def last(model):
    return q.rows(model)[-1]


# --- A. a client on every other client's requests and files ----------------
F = load_csm_form()
csm = {"form_version": F["version"],
       "answers": {x["id"]: x["options"][0]["value"] for s in F["sections"] for x in s["questions"]}}
owner = {"A1": "A", "Arev": "A", "Aclosed": "A", "Adone": "A", "B1": "B", "C1": "C", "D1": "D",
         "E1": "E", "F1": "F", "W1": None, "W2": None}
docs = q.rows(m.Document)
with q.Session(q.engine) as s:
    rid = {r.tracking_id: r.id for r in s.exec(q.select(m.LegalServiceRequest)).all()}
n, wrong = 0, []
for who in "ABCDEF":
    X = c[who]
    for t, o in owner.items():
        if o == who:
            continue
        u = T[t]
        calls = [lambda: X.get(f"/portal/requests/{u}"), lambda: X.post(f"/portal/requests/{u}/withdraw", json={}),
                 lambda: X.get(f"/portal/requests/{u}/csm"), lambda: X.post(f"/portal/requests/{u}/csm", json=csm),
                 lambda: X.post(f"/requests/{u}/messages/lookup"),
                 lambda: X.post(f"/requests/{u}/messages/client", data={"body": "x"}),
                 lambda: X.post(f"/requests/{u}/documents/client", files=[("files", ("x.pdf", PDF))])]
        calls += [lambda d=d: X.post(f"/requests/{u}/documents/{d.id}/download") for d in docs]
        calls += [lambda d=d: X.post(f"/requests/{u}/documents/{d.id}/revision", files={"file": ("x.pdf", PDF)})
                  for d in docs]
        for f in calls:
            r = f()
            n += 1
            if r.status_code != 404:
                wrong.append((who, t, r.status_code))
check(f"another client's request answers 404 every time ({n} calls)", not wrong, wrong[:5])
n, wrong = 0, []
for who in "ABCDEF":
    for t, o in owner.items():
        if o != who:
            continue
        for d in docs:
            r = c[who].post(f"/requests/{T[t]}/documents/{d.id}/download")
            n += 1
            mine = d.request_id == rid[T[t]] and (d.category == "client_submitted" or d.is_released_to_requester)
            if (r.status_code == 200) != mine:
                wrong.append((who, t, d.id, r.status_code))
check(f"on their own request, a client gets only their files and released ones ({n} calls)", not wrong, wrong[:5])
B1 = f"/requests/{T['B1']}"
la2 = c["la2"]
codes = [la2.post(f"{B1}/client/verify", json={"method": "in_person"}).status_code,
         la2.post(f"{B1}/client/decline", json={"reason": "x"}).status_code,
         la2.post(f"{B1}/client/revoke", json={"reason": "x"}).status_code,
         la2.put(B1 + "/client", json=prof("X", "Y", "essu_employee")).status_code,
         la2.get(B1 + "/client/endorsement-letter").status_code]
check("an assistant not assigned can't verify, decline, revoke, edit or open the letter",
      all(x in (403, 404) for x in codes), codes)

# --- B. sign-in codes --------------------------------------------------------
snap.restore()


def age(e):  # skip the 60-second wait between codes
    for v in q.rows(m.ContactVerification, m.ContactVerification.contact_value == e):
        q.edit(m.ContactVerification, v.id, created_at=v.created_at.replace(year=2020))


def send(e):
    age(e)
    ok(q.new_client().post("/access/contact/send-code", json={"contact": e}), "send")
    return q.last_code(e)


def ver(e, k):
    return q.new_client().post("/portal/verify-code", json={"contact": e, "code": k})


a = "a@example.com"
k = send(a)
check("the right code works", ver(a, k).status_code == 200)
check("the same code can't be used twice", ver(a, k).status_code == 400)
k = send(a)
tries = [ver(a, f"{(int(k) + i) % 1000000:06d}").status_code for i in range(1, 6)]
check("after 5 wrong codes even the right one is refused", tries == [400] * 5 and ver(a, k).status_code == 400, tries)
k1 = send(a)
q.edit(m.ContactVerification, q.rows(m.ContactVerification, m.ContactVerification.contact_value == a)[-1].id,
       created_at=ago(seconds=61))
ok(q.new_client().post("/access/contact/send-code", json={"contact": a}), "second code")
k2 = q.last_code(a)
check("an older code stops working once a newer one is sent", ver(a, k1).status_code == 400)
check("the newer code works", ver(a, k2).status_code == 200)
k = send(a)
check("one client's code doesn't work for another email", ver("b@example.com", k).status_code == 400)
q.edit(m.ContactVerification, q.rows(m.ContactVerification, m.ContactVerification.contact_value == a)[-1].id,
       expires_at=ago(seconds=1))
check("an expired code is refused", ver(a, k).status_code == 400)
k = send(a)
odd = [ver(a, x).status_code for x in (" " + k[:5], k + "0", "", k[:5], "０" * 6, "-1", "0x1")]
check("malformed codes are refused", all(x in (400, 422) for x in odd), odd)
r1 = q.new_client().post("/access/contact/send-code", json={"contact": "nobody@example.com"})
age(a)
r2 = q.new_client().post("/access/contact/send-code", json={"contact": a})
check("sending a code answers the same for known and unknown emails",
      (r1.status_code, r1.text) == (r2.status_code, r2.text), (r1.text, r2.text))
xs = [ver(e, "000000") for e in ("nobody@example.com", a, "never@example.com")]
check("a wrong code answers the same for known and unknown emails", len({(x.status_code, x.text) for x in xs}) == 1)

# --- C. sessions -------------------------------------------------------------
snap.restore()


def cookie_ok(header):
    h = header.lower()
    return "httponly" in h and "samesite" in h and "path=/" in h


r = q.new_client().post("/auth/login", json={"email": "la2@essu.edu.ph", "password": PW})
check("staff cookie is HttpOnly with SameSite", cookie_ok(r.headers["set-cookie"]))
r = ver(a, send(a))
check("client cookie is HttpOnly with SameSite", cookie_ok(r.headers["set-cookie"]))
x = q.staff_login("la2@essu.edu.ph")
s = last(m.StaffSession)
q.edit(m.StaffSession, s.id, last_seen_at=ago(minutes=29))
check("staff: 29 minutes idle still signed in", x.get("/auth/me").status_code == 200)
q.edit(m.StaffSession, s.id, last_seen_at=ago(minutes=31))
check("staff: 31 minutes idle signed out", x.get("/auth/me").status_code == 401)
q.edit(m.StaffSession, s.id, last_seen_at=ago(minutes=0))
check("staff: an ended session stays ended", x.get("/auth/me").status_code == 401)
x = q.staff_login("la2@essu.edu.ph")
q.edit(m.StaffSession, last(m.StaffSession).id, expires_at=ago(seconds=1))
check("staff: the hard time limit signs out", x.get("/auth/me").status_code == 401)
x = q.door(a)
s = last(m.RequesterAccessSession)
check("client: session lasts at most 2 hours",
      round((s.expires_at - s.created_at).total_seconds() / 60) <= 120, s.expires_at - s.created_at)
q.edit(m.RequesterAccessSession, s.id, last_seen_at=ago(minutes=29))
check("client: 29 minutes idle still signed in", x.get("/portal/me").status_code == 200)
q.edit(m.RequesterAccessSession, s.id, last_seen_at=ago(minutes=31))
check("client: 31 minutes idle signed out", x.get("/portal/me").status_code == 401)
x = q.door(a)
q.edit(m.RequesterAccessSession, last(m.RequesterAccessSession).id, expires_at=ago(seconds=1))
check("client: the 2-hour limit signs out", x.get("/portal/me").status_code == 401)
x = q.door(a)
tok = x.cookies["lois_track"]
x.post("/portal/logout")
y = q.new_client()
y.cookies.set("lois_track", tok)
check("client sign-out also ends a copied cookie", y.get("/portal/me").status_code == 401)
x = q.staff_login("la2@essu.edu.ph")
tok = x.cookies["lois_session"]
x.post("/auth/logout")
y = q.new_client()
y.cookies.set("lois_session", tok)
check("staff sign-out also ends a copied cookie", y.get("/auth/me").status_code == 401)
tk = q.staff_login("la2@essu.edu.ph").cookies["lois_session"]
ta = q.door(a).cookies["lois_track"]
y, z, p = q.new_client(), q.new_client(), q.new_client()
y.cookies.set("lois_track", tk)
z.cookies.set("lois_session", ta)
p.cookies.set("lois_submit", ta)
got = (y.get("/portal/me").status_code, z.get("/auth/me").status_code,
       p.post("/portal/register", json={**prof("A", "B", "essu_employee"), "privacy_consent": True}).status_code)
check("a token doesn't work in a different cookie", got == (401, 401, 401), got)
y = q.new_client()
y.cookies.set("lois_session", "fixed-by-attacker")
r = y.post("/auth/login", json={"email": "la2@essu.edu.ph", "password": PW})
check("login always gives a new token", r.cookies["lois_session"] != "fixed-by-attacker")
y = q.new_client()
y.cookies.set("lois_session", last(m.StaffSession).token_hash)
check("the stored token hash can't be used as a token", y.get("/auth/me").status_code == 401)
b = q.door("new9@example.com")
sub = b.cookies["lois_submit"]
b.post("/portal/logout")
y = q.new_client()
y.cookies.set("lois_submit", sub)
check("signing out during sign-up ends it",
      y.post("/portal/register", json={**prof("A", "B", "essu_employee"), "privacy_consent": True}).status_code == 401)

# --- D. staff passwords, lockout, deactivation -------------------------------
snap.restore()
N = q.new_client()
H = c["head"]


def login(e, pw):
    return N.post("/auth/login", json={"email": e, "password": pw})


def newpw(e, pw, new):
    return N.post("/auth/new-password", json={"email": e, "password": pw, "new_password": new})


r = login("new@essu.edu.ph", PW)
check("a new staff member must change the temporary password (no cookie yet)",
      r.status_code == 403 and "set-cookie" not in r.headers, r.text)
check("...and no staff page opens", all(N.get(u).status_code == 401 for u in ("/auth/me", "/requests", "/staff")))
check("the new password can't be the temporary one", newpw("new@essu.edu.ph", PW, PW).status_code == 400)
check("a short new password is refused", newpw("new@essu.edu.ph", PW, "short").status_code == 422)
snap.restore()
r = newpw("la2@essu.edu.ph", PW, "other-harbor-99")
check("new-password can't be used when no change was asked for", r.status_code == 400, r.text)
x = q.staff_login("la2@essu.edu.ph")
ok(H.post(f"/staff/{w.id['la2']}/password", json={"password": "temporary-pass-77"}), "temp password")
check("a temporary password ends the open session", x.get("/auth/me").status_code == 401)
check("the old password stops working", login("la2@essu.edu.ph", PW).status_code == 401)
check("the temporary password asks for a new one", login("la2@essu.edu.ph", "temporary-pass-77").status_code == 403)
check("the Head Attorney can't reset their own password here",
      H.post(f"/staff/{w.id['head']}/password", json={"password": "temporary-pass-77"}).status_code == 400)
snap.restore()
tries = [login("head@essu.edu.ph", "wrong-pass").status_code for _ in range(5)]
r = login("head@essu.edu.ph", PW)
check("after 5 wrong passwords the account is locked, even for the right one", r.status_code == 423, (tries, r.text))
r2 = login("nobody@essu.edu.ph", PW)
r3 = login("head@essu.edu.ph", "wrong-pass")
check("a locked account with a wrong password answers like an unknown email (no hint the account exists)",
      (r3.status_code, r3.text) == (r2.status_code, r2.text), (r3.status_code, r2.status_code))
check("...and that wrong try doesn't make the lock longer",
      q.one(m.StaffUser, m.StaffUser.id == w.id["head"]).failed_login_attempts == 5)
q.edit(m.StaffUser, w.id["head"], locked_until=ago(seconds=1))
login("head@essu.edu.ph", "wrong-again")
s = q.one(m.StaffUser, m.StaffUser.id == w.id["head"])
check("after a lock ends, one wrong try doesn't lock the account again",
      s.locked_until is None or s.locked_until < datetime.utcnow(),
      f"attempts={s.failed_login_attempts}")
check("...and the count starts again from that one try", s.failed_login_attempts == 1, s.failed_login_attempts)
tries = [login("head@essu.edu.ph", "wrong-again").status_code for _ in range(4)]
check("five wrong tries after the lock ended lock it again", login("head@essu.edu.ph", PW).status_code == 423, tries)
check("the Head Attorney already signed in keeps working", H.get("/auth/me").status_code == 200)
snap.restore()
tries = [newpw("new@essu.edu.ph", "bad", "other-harbor-99").status_code for _ in range(5)]
check("wrong passwords on new-password count toward the same lock", login("new@essu.edu.ph", PW).status_code == 423, tries)
snap.restore()
x = q.staff_login("la2@essu.edu.ph")
ok(H.patch(f"/staff/{w.id['la2']}", json={"is_active": False}), "deactivate")
check("deactivating staff ends their open session", x.get("/auth/me").status_code == 401)
ok(H.patch(f"/staff/{w.id['la2']}", json={"is_active": True}), "reactivate")
check("after reactivation the old session stays ended", x.get("/auth/me").status_code == 401)
check("...and the staff member can sign in again", login("la2@essu.edu.ph", PW).status_code == 200)
snap.restore()
x = c["la2"]
ok(H.patch(f"/staff/{w.id['la2']}", json={"role": "head_attorney"}), "role up")
up = x.get("/settings").status_code
ok(H.patch(f"/staff/{w.id['la2']}", json={"role": "legal_assistant"}), "role down")
check("a role change takes effect at once", (up, x.get("/settings").status_code) == (200, 403))
snap.restore()
v = q.new_client()
r = v.post("/auth/verify-email", json={"email": "head@essu.edu.ph", "code": "000000"})
check("verify-email with a wrong code reveals nothing about the account", r.status_code >= 400 and "role" not in r.text,
      (r.status_code, r.text[:120]))
answers = {(x.status_code, x.text) for x in (r, v.post("/auth/verify-email", json={"email": "ghost@essu.edu.ph", "code": "000000"}))}
check("...and answers the same as for an email with no account", len(answers) == 1, answers)
ok(H.post("/staff", json={"full_name": "Zed New", "email": "zed@essu.edu.ph", "password": PW, "role": "attorney"}),
   "new staff")
k = q.last_code("zed@essu.edu.ph")
tries = [v.post("/auth/verify-email", json={"email": "zed@essu.edu.ph",
                                            "code": f"{(int(k) + i) % 10**6:06d}"}).status_code for i in range(1, 6)]
check("the staff email code locks after 5 wrong tries",
      v.post("/auth/verify-email", json={"email": "zed@essu.edu.ph", "code": k}).status_code == 429, tries)
q.MAIL.clear()
for _ in range(2):
    q.edit(m.StaffEmailVerificationCode, last(m.StaffEmailVerificationCode).id, created_at=ago(seconds=61))
    v.post("/auth/resend-verification", json={"email": "zed@essu.edu.ph"})
codes = [q.last_code("zed@essu.edu.ph")]
old = [b for t, _s, b, _k in q.MAIL if t == "zed@essu.edu.ph"]
if len(old) >= 2:
    import re
    kold = re.search(r"\b(\d{6})\b", old[-2]).group(1)
    check("an older staff code stops working once a newer one is sent",
          v.post("/auth/verify-email", json={"email": "zed@essu.edu.ph", "code": kold}).status_code == 400)
check("the newest staff code works",
      v.post("/auth/verify-email", json={"email": "zed@essu.edu.ph", "code": codes[0]}).status_code == 200)

# --- E. the email budget can't be drained (SEC-05) ----------------------------
snap.restore()
q.MAIL.clear()
odd = ["x1<victim@example.com>", "Victim <victim@example.com>", "victim@example.com,b@example.com",
       "victim@example.com;b@example.com", '"a"@example.com', "a(b)@example.com",
       "a@example.com\r\nBcc:z@example.com", "a@@example.com", "a@example"]
codes = [q.new_client().post("/access/contact/send-code", json={"contact": e}).status_code for e in odd]
check("the door refuses anything but a plain email address", codes == [400] * len(odd), codes)
check("...and sends nothing for them", not q.MAIL, [x[0] for x in q.MAIL])
plain = ["first.last@example.com", "first+tag@example.com", "o'brien@example.com", "a_b-c@sub.example.com"]
codes = [q.new_client().post("/access/contact/send-code", json={"contact": e}).status_code for e in plain]
check("plain addresses still work, including + and '", codes == [200] * len(plain), codes)
ok(H.post("/staff", json={"full_name": "Yan New", "email": "yan@essu.edu.ph", "password": PW, "role": "attorney"}),
   "new staff")
for _ in range(60):  # once a minute for an hour, by anyone, no robot check on this page
    q.edit(m.StaffEmailVerificationCode, last(m.StaffEmailVerificationCode).id, created_at=ago(seconds=61))
    q.new_client().post("/auth/resend-verification", json={"email": "yan@essu.edu.ph"})
sent = [x for x in q.MAIL if x[0] == "yan@essu.edu.ph"]
check("staff code emails stop at 5 an hour (the first one included)", len(sent) == 5, len(sent))
events = q.rows(m.SecurityEvent, m.SecurityEvent.event_type == "staff_code_hourly_cap")
check("reaching that limit is logged once", len(events) == 1, len(events))
r = q.new_client().post("/auth/resend-verification", json={"email": "yan@essu.edu.ph"})
check("the answer stays the same vague one", r.json()["detail"].startswith("If an account needs verification"), r.text)
ok_code = q.last_code("yan@essu.edu.ph")
check("the last code sent still works",
      q.new_client().post("/auth/verify-email", json={"email": "yan@essu.edu.ph", "code": ok_code}).status_code == 200)

check("no network connection or .env read was attempted", not q.REFUSED, q.REFUSED)
print(f"\n{sum(results)}/{len(results)} checks passed; {len(known_open)} known open findings still there.")
sys.exit(0 if all(results) else 1)

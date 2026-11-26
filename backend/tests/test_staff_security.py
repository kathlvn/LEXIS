r"""Staff password rules, sign-in logging and the forwarded address
(qa-report SEC-10, SEC-13, SEC-15; API-03 is in test_due_dates_data.py):
weak or personal passwords refused, temporary passwords expiring after
72 hours, staff sign-ins/sign-outs in the Security Log, staff opening a
client's file in the request's History (never shown to the client),
X-Forwarded-For believed only from Caddy, unknown Host headers refused.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_staff_security.py
In-memory SQLite, temp folder for files, emails recorded (see lexis_world.py).
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexis_world as q  # noqa: E402  (must come before anything from app/)
from lexis_world import ok  # noqa: E402

w = q.build()
m = q.m
H, L = w.c["head"], w.c["la1"]
results = []


def check(name, good, detail=""):
    results.append(bool(good))
    print(("PASS " if good else "FAIL ") + name + (f"  -> {detail}" if not good else ""))


def events(kind):
    return [e.detail for e in q.rows(m.SecurityEvent, m.SecurityEvent.event_type == kind)]


# ---- SEC-10: weak passwords --------------------------------------------------
la2 = w.id["la2"]
for pw, why in [("aaaaaaaaaaaa", "repeated"), ("password1234", "common word"), (" " * 12, "12 spaces"),
                ("P@ssw0rd-2026!", "swapped letters"), ("Lexis-Office-9", "lexis"), ("qwerty-Mango-7", "qwerty"),
                ("Assistant-Mango-7", "own name")]:
    r = H.post(f"/staff/{la2}/password", json={"password": pw})
    check(f"SEC-10 temporary password refused: {why}", r.status_code in (400, 422), (r.status_code, r.text[:100]))
r = H.post("/staff", json={"full_name": "Nina Newhire", "email": "nina.newhire@essu.edu.ph",
                           "password": "Newhire-Mango-7", "role": "legal_assistant"})
check("SEC-10 new account refused: their own name in the password", r.status_code == 422 and "name or email" in r.text,
      r.text[:120])
from app.core.security import password_problem  # noqa: E402
check("SEC-10 email name in the password is refused (juan.delacruz -> 'Delacruz-Mango-7')",
      password_problem("Delacruz-Mango-7", "juan.delacruz@essu.edu.ph") == "Don't use your name or email address in the password.")
r = H.post(f"/staff/{la2}/password", json={"password": "Harbor-Lantern-48"})
check("SEC-10 a good temporary password is accepted", r.status_code == 200, r.text[:100])
acct = q.one(m.StaffUser, m.StaffUser.id == la2)
left = acct.temp_password_expires_at - datetime.utcnow()
check("SEC-10 it is set to expire in 72 hours", timedelta(hours=71, minutes=59) < left <= timedelta(hours=72), left)

N = q.new_client()
r = N.post("/auth/new-password", json={"email": "la2@essu.edu.ph", "password": "Harbor-Lantern-48",
                                       "new_password": "password-12345"})
check("SEC-10 choosing your own: a common word is refused with the reason",
      r.status_code == 422 and "common words" in r.text, r.text[:120])

q.edit(m.StaffUser, la2, temp_password_expires_at=datetime.utcnow() - timedelta(minutes=1))
r = N.post("/auth/login", json={"email": "la2@essu.edu.ph", "password": "Harbor-Lantern-48"})
check("SEC-10 an expired temporary password: login says ask the Head Attorney (403)",
      r.status_code == 403 and "expired" in r.text, r.text[:120])
r = N.post("/auth/new-password", json={"email": "la2@essu.edu.ph", "password": "Harbor-Lantern-48",
                                       "new_password": "Quiet-River-Stone-5"})
check("SEC-10 ...and the new-password step refuses it too", r.status_code == 403 and "expired" in r.text, r.text[:120])
check("SEC-10 ...logged in the Security Log", any("expired temporary password" in d for d in events("temp_password_expired")))
r = N.post("/auth/login", json={"email": "la2@essu.edu.ph", "password": "wrong-guess-77"})
check("SEC-10 a wrong guess still gets only 'incorrect' (expiry tells guessers nothing)", r.status_code == 401, r.text)

r = H.post(f"/staff/{la2}/password", json={"password": "Harbor-Lantern-49"})
r = N.post("/auth/new-password", json={"email": "la2@essu.edu.ph", "password": "Harbor-Lantern-49",
                                       "new_password": "Quiet-River-Stone-5"})
check("SEC-10 a fresh temporary password works, and choosing your own clears the expiry",
      r.status_code == 200 and q.one(m.StaffUser, m.StaffUser.id == la2).temp_password_expires_at is None, r.text[:120])

# ---- SEC-15: sign-ins, sign-outs, document opens -------------------------------
before_in = len(events("staff_login"))
c = q.new_client()
ok(c.post("/auth/login", json={"email": "la1@essu.edu.ph", "password": q.PW}), "login")
check("SEC-15 a staff sign-in is in the Security Log", len(events("staff_login")) == before_in + 1
      and events("staff_login")[-1] == "Ana Assistant signed in.", events("staff_login")[-1:])
check("SEC-15 the new-password step's sign-in is logged too", "Ben Assistant signed in." in events("staff_login"))
ok(c.post("/auth/logout"), "logout")
c.post("/auth/logout")
check("SEC-15 a sign-out is logged once (a second click adds nothing)",
      events("staff_logout").count("Ana Assistant signed out.") == 1, events("staff_logout"))
labels = H.get("/security-events/summary").text
check("SEC-15 the Security Log has labels for the new events",
      all(x in labels for x in ("Staff signed in", "Staff signed out")), labels[:200])

t = w.t["A1"]
for _ in range(3):
    assert L.get(f"/requests/{t}/documents/{w.d['client']}/download").status_code == 200
opened = q.rows(m.AuditLogEntry, m.AuditLogEntry.action == "document_opened")
check("SEC-15 opening a document adds one History line (3 opens in 30 min = 1 line)",
      len(opened) == 1 and opened[0].detail == "Opened a1.pdf" and opened[0].staff_id == w.id["la1"],
      [(o.detail, o.staff_id) for o in opened])
q.edit(m.AuditLogEntry, opened[0].id, created_at=datetime.utcnow() - timedelta(minutes=31))
assert L.get(f"/requests/{t}/documents/{w.d['client']}/download").status_code == 200
check("SEC-15 ...a later open adds another line",
      len(q.rows(m.AuditLogEntry, m.AuditLogEntry.action == "document_opened")) == 2)
assert L.get(f"/requests/{t}/client/endorsement-letter").status_code == 200
check("SEC-15 opening the endorsement letter is in History",
      any("endorsement letter" in o.detail for o in q.rows(m.AuditLogEntry, m.AuditLogEntry.action == "document_opened")))
staff_hist = ok(L.get(f"/requests/{t}"), "detail")
check("SEC-15 staff see it in the request's History",
      any(e.get("action") == "document_opened" for e in staff_hist.get("audit_log", staff_hist.get("audit_entries", []))),
      [k for k in staff_hist.keys()])
client_view = ok(w.c["A"].get(f"/portal/requests/{t}"), "client view")
check("SEC-15 the client never sees it", "Opened" not in str(client_view.get("history")), client_view.get("history"))

# ---- SEC-13: forwarded address and host ------------------------------------------
from app.core.security_monitoring import get_client_ip  # noqa: E402


class FakeReq:
    def __init__(self, host, xff=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


check("SEC-13 X-Forwarded-For from Caddy (127.0.0.1) is used",
      get_client_ip(FakeReq("127.0.0.1", "203.0.113.9")) == "203.0.113.9")
check("SEC-13 ...the last entry (Caddy's) is taken, not a spoofed first one",
      get_client_ip(FakeReq("127.0.0.1", "1.2.3.4, 203.0.113.9")) == "203.0.113.9")
check("SEC-13 X-Forwarded-For from anyone else is ignored",
      get_client_ip(FakeReq("198.51.100.7", "1.2.3.4")) == "198.51.100.7")
r = q.new_client().get("/public/articles", headers={"Host": "evil.example.com"})
check("SEC-13 an unknown Host header is refused (400)", r.status_code == 400, r.status_code)
r = q.new_client().get("/public/articles", headers={"Host": "localhost:8080"})
check("SEC-13 localhost:8080 (Caddy) is answered", r.status_code == 200, r.status_code)

check("no network connection or .env read was attempted", not q.REFUSED, q.REFUSED)
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)

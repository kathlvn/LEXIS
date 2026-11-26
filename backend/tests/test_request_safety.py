r"""Requests that tried to slip past LEXIS's limits (qa-report SEC-01, SEC-02,
SEC-07):
- SEC-01: junk after the end of an upload no longer freezes the server;
- SEC-02: a change (POST/PUT/PATCH/DELETE) sent from another site is refused
  and logged, even with a signed-in user's cookie;
- SEC-07: rate limits count per endpoint, not per URL (other Request IDs,
  /staff/02 vs /staff/2); refused calls (bad input 422, not signed in 401,
  wrong role 403) count too; an oversized body is refused before it is read.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_request_safety.py
In-memory SQLite, temp folder for files, emails recorded (see lexis_world.py).
"""
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexis_world as q  # noqa: E402  (must come before anything from app/)
from app.core.security_monitoring import limiter  # noqa: E402

w = q.build()
m = q.m
H, L = w.c["head"], w.c["la1"]
results = []


def check(name, good, detail=""):
    results.append(bool(good))
    print(("PASS " if good else "FAIL ") + name + (f"  -> {detail}" if not good else ""))


def events(kind):
    return [e.detail for e in q.rows(m.SecurityEvent, m.SecurityEvent.event_type == kind)]


# ---- SEC-01: junk after the closing boundary of an upload ---------------------
# Before the upgrade (python-multipart 0.0.9) 100 KB of junk froze the server
# for about 42 s and wrote one log warning per byte.
B = "qaboundary"
for size in (100_000, 1_000_000):
    body = (f"--{B}\r\nContent-Disposition: form-data; name=\"description\"\r\n\r\nx\r\n--{B}--\r\n").encode() \
        + b"A\r\n" * (size // 3)
    t = time.time()
    r = q.new_client().post("/portal/requests", content=body,
                            headers={"Content-Type": f"multipart/form-data; boundary={B}"})
    took = time.time() - t
    check(f"SEC-01 {size // 1000} KB of junk after an upload is answered in under 3 s",
          took < 3 and r.status_code < 500, (r.status_code, f"{took:.1f} s"))

# ---- SEC-02: changes from another site ----------------------------------------
before = len(events("cross_site_refused"))
cases = [
    ("Sec-Fetch-Site: cross-site", {"Sec-Fetch-Site": "cross-site"}),
    ("Sec-Fetch-Site: same-site (another *.essu.edu.ph page)", {"Sec-Fetch-Site": "same-site"}),
    ("Origin of another site", {"Origin": "https://weak.essu.edu.ph"}),
    ("Origin: null (sandboxed page)", {"Origin": "null"}),
]
for name, headers in cases:
    r = H.post("/auth/logout", headers=headers)
    check(f"SEC-02 signed-in change with {name}: refused (403)",
          r.status_code == 403 and "another website" in r.text, (r.status_code, r.text[:100]))
check("SEC-02 ...and the session is still open (the change didn't happen)", H.get("/auth/me").status_code == 200)
check("SEC-02 each refusal is in the Security Log", len(events("cross_site_refused")) - before == len(cases),
      len(events("cross_site_refused")) - before)
r = H.patch(f"/staff/{w.id['la2']}", json={"is_active": False}, headers={"Sec-Fetch-Site": "cross-site"})
check("SEC-02 a cross-site PATCH (deactivate staff) is refused too", r.status_code == 403, r.status_code)
check("SEC-02 ...and the staff member is still active", q.one(m.StaffUser, m.StaffUser.id == w.id["la2"]).is_active)
r = H.get("/auth/me", headers={"Sec-Fetch-Site": "cross-site"})
check("SEC-02 reading (GET) from a link elsewhere still works", r.status_code == 200, r.status_code)
for name, headers in [("Sec-Fetch-Site: same-origin", {"Sec-Fetch-Site": "same-origin"}),
                      ("Sec-Fetch-Site: none (typed by the user)", {"Sec-Fetch-Site": "none"}),
                      ("LEXIS's own Origin", {"Origin": "http://localhost:8080", "Host": "localhost:8080"})]:
    r = q.new_client().post("/auth/login", json={"email": "la1@essu.edu.ph", "password": q.PW}, headers=headers)
    check(f"SEC-02 a change with {name} is allowed", r.status_code == 200, (r.status_code, r.text[:100]))

# ---- SEC-07: oversized bodies --------------------------------------------------
r = q.new_client().post("/auth/login", content=b"{" + b" " * (1024 * 1024) + b"}",
                        headers={"Content-Type": "application/json"})
check("SEC-07 a body over 1 MB (not an upload) is refused with 413", r.status_code == 413, (r.status_code, r.text[:100]))
r = q.new_client().post("/auth/login", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "abc"})
check("SEC-07 a Content-Length that isn't a number gets 400", r.status_code == 400, r.status_code)
r = q.new_client().post("/portal/requests", files={"files": ("a.pdf", q.PDF + b" " * (2 * 1024 * 1024), "application/pdf")},
                        data={"description": "x"})
check("SEC-07 an upload over 1 MB is not refused for its size (uploads may be up to 75 MB)",
      r.status_code != 413, r.status_code)

# ---- SEC-07: limits per endpoint, and refused calls count -------------------------
limiter.enabled = True
try:
    limiter.reset()
    codes = [H.get(f"/requests/LEO-2026-{i:06d}").status_code for i in range(121)]
    check("SEC-07 120 different Request IDs share one limit; call 121 -> 429",
          codes[:120] == [404] * 120 and codes[120] == 429, Counter(codes))

    limiter.reset()
    sid = w.id["la2"]
    # Setting a temporary password (limit 10 a minute). The password is too
    # short, so it is refused and nothing changes; refused calls count too.
    paths = [f"/staff/{sid}/password", f"/staff/0{sid}/password", f"/staff/00{sid}/password"] * 4
    codes = [H.post(p, json={"password": "short"}).status_code for p in paths[:11]]
    lim = [str(x.limit) for k, v in limiter._route_limits.items() if k.endswith("staff.set_temporary_password") for x in v]
    check("SEC-07 /staff/2, /staff/02 and /staff/002 count as one endpoint (temporary password: 10, then 429)",
          429 not in codes[:10] and codes[10] == 429, (codes, lim))

    limiter.reset()
    c = q.new_client()
    codes = [c.post("/auth/login", json={"email": "x"}).status_code for _ in range(6)]
    check("SEC-07 bad login bodies (422) count: 5 then 429", codes == [422] * 5 + [429], codes)

    limiter.reset()
    c = q.new_client()
    codes = [c.post("/auth/login", json={"email": "x"}).status_code for _ in range(3)] + \
            [c.post("/auth/login", json={"email": "la1@essu.edu.ph", "password": "wrong-guess-77"}).status_code for _ in range(3)]
    check("SEC-07 bad and wrong logins share one count of 5", codes == [422] * 3 + [401] * 2 + [429], codes)

    limiter.reset()
    c = q.new_client()
    codes = [c.get("/requests").status_code for _ in range(121)]
    check("SEC-07 signed out (401): 120 then 429", codes == [401] * 120 + [429], Counter(codes))

    limiter.reset()
    codes = [L.get("/requests").status_code for _ in range(121)]
    check("SEC-07 signed in: 120 good calls work, call 121 -> 429 (each call counted once)",
          codes == [200] * 120 + [429], Counter(codes))

    limiter.reset()
    codes = Counter(L.get("/security-events").status_code for _ in range(130))
    check("SEC-07 an assistant on a Head-Attorney page: the 403s count, then 429", codes[403] > 0 and codes[429] > 0, codes)

    before = len(events("rate_limit_exceeded"))
    limiter.reset()
    c = q.new_client()
    for _ in range(8):
        c.post("/auth/login", json={"email": "x"})
    check("SEC-07 each 429 for refused calls is logged once (3 of 8)", len(events("rate_limit_exceeded")) - before == 3,
          len(events("rate_limit_exceeded")) - before)

    limiter.reset()
    check("SEC-07 an unknown URL still answers 404 and is not counted",
          all(c.get("/nope").status_code == 404 for _ in range(200)))
finally:
    limiter.reset()
    limiter.enabled = False
check("with the limiter off (as in the other tests), refused calls never get 429",
      all(q.new_client().post("/auth/login", json={"email": "x"}).status_code == 422 for _ in range(20)))

check("no network connection or .env read was attempted", not q.REFUSED, q.REFUSED)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

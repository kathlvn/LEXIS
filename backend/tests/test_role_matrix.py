r"""Role matrix: every endpoint called as every kind of person (visitor, forged
cookie, six clients, each staff role, a new staff member who hasn't changed
the temporary password, a deactivated one). Checks each call is allowed only
for the people who should have it, and refused (401/403/404/409) for everyone
else. The database is put back before every call, so calls don't affect each
other. A new endpoint that isn't in the list makes this test fail, so the list
stays complete. Roles are enforced on the server (CLAUDE.md), not just by
hiding buttons.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_role_matrix.py
In-memory SQLite, temp folder for files, emails recorded (see lexis_world.py).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexis_world as q  # noqa: E402  (must come before anything from app/)
from lexis_world import PDF, PW, prof  # noqa: E402
from app.core import holiday_import as hi  # noqa: E402
from app.core.csm_form import load_csm_form  # noqa: E402
from app.routers import holidays as hr  # noqa: E402
from fastapi.routing import APIRoute, iter_route_contexts  # noqa: E402

# The Gazette check is replaced with an empty answer: no internet in this test.
hr.import_from_gazette = lambda year: hi.ProclamationImport(year, hi.GAZETTE_ORIGIN, hi.GAZETTE_ORIGIN, "Fake", [], [])
w = q.build()
snap = q.Snapshot()
T, D = w.t, w.d
PEOPLE = ["visitor", "forged", "A", "B", "C", "D", "E", "F", "la1", "la2", "atty", "head", "new", "la3"]
ANY, STAFF = set(PEOPLE), {"la1", "la2", "atty", "head"}
HANDLE, ATT, HEAD, OWN = {"la1", "atty", "head"}, {"atty", "head"}, {"head"}, {"A"}
CLIENTS = set("ABCDEF")
Y = datetime.utcnow().year
QTR = (datetime.utcnow().month - 1) // 3 + 1
f1 = lambda n="x.pdf": [("files", (n, PDF, "application/pdf"))]
enc = json.dumps({"requester": prof("Nuevo", "Nina", "essu_employee", email="nw@example.com"), "service_type_code": "legal_advice",
                  "description": "walk in", "consent_confirmed": True})
F = load_csm_form()
ans = {x["id"]: x["options"][0]["value"] for s in F["sections"] for x in s["questions"]}
csm = {"form_version": F["version"], "answers": ans}
r = lambda t: f"/requests/{T[t]}"
p = lambda t: f"/portal/requests/{T[t]}"
LAID, LA2 = w.id["la1"], w.id["la2"]
svc = q.one(q.m.ServiceType, q.m.ServiceType.code == "legal_advice").code
# (method, route template, url, kwargs, allowed people)
E = [
    ("GET", "/health", "/health", {}, ANY),
    ("GET", "/service-types", "/service-types", {}, ANY),
    ("GET", "/portal/offices", "/portal/offices", {}, ANY),
    ("GET", "/access/bot-check/challenge", "/access/bot-check/challenge", {}, ANY),
    ("GET", "/public/articles", "/public/articles", {}, ANY),
    ("GET", "/public/articles/search-text", "/public/articles/search-text", {}, ANY),
    ("GET", "/public/articles/{article_id}", f"/public/articles/{w.pub}", {}, ANY),
    ("POST", "/access/contact/send-code", "/access/contact/send-code", dict(json={"contact": "fresh@example.com"}), ANY),
    ("POST", "/portal/logout", "/portal/logout", {}, ANY),
    ("POST", "/auth/logout", "/auth/logout", {}, ANY),
    ("POST", "/auth/resend-verification", "/auth/resend-verification", dict(json={"email": "x@essu.edu.ph"}), ANY),
    ("GET", "/portal/me", "/portal/me", {}, CLIENTS),
    ("PATCH", "/portal/me", "/portal/me", dict(json=prof("X", "Y", "essu_employee")), {"D"}),   # locked (409) once staff check a client; D (complaint only) is still unverified
    ("GET", "/portal/services", "/portal/services", {}, CLIENTS),
    ("POST", "/portal/requests", "/portal/requests", dict(data={"service_type_code": "legal_advice", "description": "new one", "attestation": "true"}), {"A"}),
    ("GET", "/portal/requests/{tracking_id}", p("A1"), {}, OWN),
    ("POST", "/portal/requests/{tracking_id}/withdraw", p("A1") + "/withdraw", dict(json={}), OWN),
    ("GET", "/portal/requests/{tracking_id}/csm", p("Adone") + "/csm", {}, OWN),
    ("POST", "/portal/requests/{tracking_id}/csm", p("Adone") + "/csm", dict(json=csm), OWN),
    ("POST", "/requests/{tracking_id}/documents/client", r("A1") + "/documents/client", dict(files=f1()), OWN),
    ("POST", "/requests/{tracking_id}/documents/{document_id}/revision", r("A1") + f"/documents/{D['revise']}/revision", dict(files={"file": ("v2.pdf", PDF)}), OWN),
    ("POST", "/requests/{tracking_id}/documents/{document_id}/download", r("A1") + f"/documents/{D['released']}/download", {}, OWN),
    ("POST", "/requests/{tracking_id}/messages/lookup", r("A1") + "/messages/lookup", {}, OWN),
    ("POST", "/requests/{tracking_id}/messages/client", r("A1") + "/messages/client", dict(data={"body": "hi"}), OWN),
    ("GET", "/auth/me", "/auth/me", {}, STAFF),
    ("GET", "/requests", "/requests", {}, STAFF),
    ("GET", "/requests/summary", "/requests/summary", {}, STAFF),
    ("GET", "/requests/{tracking_id}", r("A1"), {}, STAFF),
    ("GET", "/requests/{tracking_id}/client", r("A1") + "/client", {}, STAFF),
    ("GET", "/clients/search", "/clients/search?q=aq", {}, STAFF),
    ("GET", "/clients/lookup", "/clients/lookup?email=a@example.com", {}, STAFF),
    ("GET", "/clients/{client_id}", f"/clients/{w.cid['A']}", {}, STAFF),
    ("GET", "/reports", f"/reports?period=year&year={Y}", {}, STAFF),
    ("GET", "/holidays", "/holidays", {}, STAFF),
    ("GET", "/staff/directory", "/staff/directory", {}, STAFF),
    ("POST", "/requests/staff-encoded", "/requests/staff-encoded", dict(data={"payload": enc}, files={"endorsement_letter": ("l.pdf", PDF)}), STAFF),
    ("GET", "/articles", "/articles", {}, STAFF),
    ("GET", "/articles/{article_id}", f"/articles/{w.art}", {}, STAFF),
    ("POST", "/articles", "/articles", dict(json={"title": "t", "body": "b"}), STAFF),
    ("GET", "/notifications", "/notifications", {}, STAFF),
    ("GET", "/notifications/unread-count", "/notifications/unread-count", {}, STAFF),
    ("POST", "/notifications/{notification_id}/read", f"/notifications/{w.note}/read", {}, {"la1"}),
    ("POST", "/notifications/read-all", "/notifications/read-all", {}, STAFF),
    ("PATCH", "/requests/{tracking_id}/status", r("A1") + "/status", dict(json={"new_status": "Received"}), HANDLE),
    ("POST", "/requests/{tracking_id}/remind", r("A1") + "/remind", {}, HANDLE),
    ("POST", "/requests/{tracking_id}/client-contacted", r("W2") + "/client-contacted", {}, HANDLE),
    ("GET", "/requests/{tracking_id}/client/endorsement-letter", r("A1") + "/client/endorsement-letter", {}, HANDLE),
    ("POST", "/requests/{tracking_id}/client/verify", r("B1") + "/client/verify", dict(json={"method": "in_person"}), HANDLE),
    ("POST", "/requests/{tracking_id}/client/verify #declined client", r("F1") + "/client/verify", dict(json={"method": "in_person"}), ATT),
    ("POST", "/requests/{tracking_id}/client/verify #revoked client", r("E1") + "/client/verify", dict(json={"method": "in_person"}), ATT),
    ("POST", "/requests/{tracking_id}/client/decline", r("B1") + "/client/decline", dict(json={"reason": "x"}), HANDLE),
    ("POST", "/requests/{tracking_id}/documents/staff", r("A1") + "/documents/staff", dict(files=f1()), HANDLE),
    ("POST", "/requests/{tracking_id}/documents/client-by-staff", r("A1") + "/documents/client-by-staff", dict(files=f1()), HANDLE),
    ("GET", "/requests/{tracking_id}/documents/{document_id}/download", r("A1") + f"/documents/{D['secret']}/download", {}, HANDLE),
    ("PATCH", "/requests/{tracking_id}/documents/{document_id}", r("A1") + f"/documents/{D['client']}", dict(json={"status": "revision_required", "note": "n"}), HANDLE),
    ("PATCH", "/requests/{tracking_id}/documents/{document_id}/release", r("A1") + f"/documents/{D['secret']}/release", dict(json={"released": True}), HANDLE),
    ("GET", "/requests/{tracking_id}/messages", r("A1") + "/messages", {}, HANDLE),
    ("POST", "/requests/{tracking_id}/messages", r("A1") + "/messages", dict(data={"body": "hi"}), HANDLE),
    ("PATCH", "/articles/{article_id} #own draft", f"/articles/{w.art}", dict(json={"title": "new"}), {"la1", "head"}),
    ("PATCH", "/articles/{article_id} #published", f"/articles/{w.pub}", dict(json={"title": "new"}), HEAD),
    ("POST", "/requests/{tracking_id}/return", r("Arev") + "/return", dict(json={"comment": "x"}), ATT),
    ("POST", "/requests/{tracking_id}/close", r("A1") + "/close", dict(json={"action": "cancel", "reason": "x"}), ATT),
    ("POST", "/requests/{tracking_id}/reopen", r("Aclosed") + "/reopen", dict(json={"reason": "x"}), ATT),
    ("PATCH", "/requests/{tracking_id}/status #approve", r("Arev") + "/status", dict(json={"new_status": "Approved"}), ATT),
    ("PATCH", "/requests/{tracking_id}/status #approve", r("Arev") + "/status", dict(json={"new_status": "Approved"}), ATT),
    # Statuses move one step at a time; going back is Return (API-05), so nobody may skip or pull back here.
    ("PATCH", "/requests/{tracking_id}/status #skip to completed", r("A1") + "/status", dict(json={"new_status": "Completed"}), set()),
    ("PATCH", "/requests/{tracking_id}/status #pull back from review", r("Arev") + "/status", dict(json={"new_status": "Processing"}), set()),
    ("PATCH", "/requests/{tracking_id}/reassign", r("A1") + "/reassign", dict(json={"new_officer_id": LA2}), HEAD),
    ("POST", "/requests/{tracking_id}/client/revoke", r("A1") + "/client/revoke", dict(json={"reason": "x"}), HEAD),
    ("PUT", "/requests/{tracking_id}/client", r("A1") + "/client", dict(json=prof("Aquino", "Alma", "essu_employee")), HEAD),
    ("POST", "/staff", "/staff", dict(json={"full_name": "N", "email": "n2@essu.edu.ph", "password": PW, "role": "attorney"}), HEAD),
    ("GET", "/staff", "/staff", {}, HEAD),
    ("GET", "/staff/{staff_id}", f"/staff/{LAID}", {}, HEAD),
    ("PATCH", "/staff/{staff_id}", f"/staff/{LA2}", dict(json={"full_name": "Changed"}), HEAD),
    ("PATCH", "/staff/{staff_id} #raise own role", f"/staff/{LAID}", dict(json={"role": "head_attorney"}), HEAD),
    ("POST", "/staff/{staff_id}/password", f"/staff/{LA2}/password", dict(json={"password": PW + "x"}), HEAD),
    ("GET", "/service-types/manage", "/service-types/manage", {}, HEAD),
    ("POST", "/service-types", "/service-types", dict(json={"name": "QA service"}), HEAD),
    ("PATCH", "/service-types/{code}", f"/service-types/{svc}", dict(json={"description": "x"}), HEAD),
    ("PUT", "/service-types/{code}/requirements", f"/service-types/{svc}/requirements", dict(json={"items": []}), HEAD),
    ("PUT", "/service-types/{code}/categories", f"/service-types/{svc}/categories", dict(json={"categories": ["essu_office"]}), HEAD),
    ("GET", "/offices/manage", "/offices/manage", {}, HEAD),
    ("GET", "/offices/typed", "/offices/typed", {}, HEAD),
    ("POST", "/offices", "/offices", dict(json={"name": "Other office"}), HEAD),
    ("PATCH", "/offices/{office_id}", f"/offices/{w.office}", dict(json={"is_active": False}), HEAD),
    ("POST", "/offices/from-typed", "/offices/from-typed", dict(json={"typed_name": "Test Office", "name": "Test Office"}), HEAD),
    ("GET", "/settings", "/settings", {}, HEAD),
    ("PUT", "/settings/{key}", "/settings/max_requests_per_day", dict(json={"value": "6"}), HEAD),
    ("GET", "/holidays/suggestions", "/holidays/suggestions?year=2027", {}, HEAD),
    ("POST", "/holidays", "/holidays", dict(json={"holiday_date": "2027-02-02", "name": "x", "holiday_type": "local"}), HEAD),
    ("DELETE", "/holidays/{holiday_id}", f"/holidays/{w.hol}", {}, HEAD),
    ("POST", "/articles/{article_id}/publish", f"/articles/{w.art}/publish", {}, HEAD),
    ("POST", "/articles/{article_id}/hide", f"/articles/{w.pub}/hide", {}, HEAD),
    ("GET", "/security-events", "/security-events", {}, HEAD),
    ("GET", "/security-events/summary", "/security-events/summary", {}, HEAD),
    ("GET", "/archive/preview", f"/archive/preview?year={Y}&quarter={QTR}", {}, HEAD),
    ("POST", "/archive/export", "/archive/export", dict(json={"year": Y, "quarter": QTR}), HEAD),
]
# These take no session; their own tests cover them.
OWN_TESTS = {"/auth/login", "/auth/new-password", "/auth/verify-email", "/portal/verify-code", "/portal/register"}

results = []


def check(name, good, detail=""):
    results.append(bool(good))
    print(("PASS " if good else "FAIL ") + name + (f"  -> {detail}" if detail and not good else ""))


have = {(a, b.split(" #")[0]) for a, b, *_ in E}
routes = {(sorted(x.methods)[0], x.path) for x in iter_route_contexts(q.main.app.routes)
          if isinstance(x.original_route, APIRoute)}
missing = sorted(x for x in routes - have if x[1] not in OWN_TESTS)
check("every endpoint is in the matrix", not missing, missing)
check("every matrix entry is a real endpoint", not (have - routes), sorted(have - routes))


def expected_denied(name, path):
    """Status codes that count as a correct refusal for this person."""
    if name in ("visitor", "forged", "new", "la3"):
        return {401}
    if name in CLIENTS:
        if (path.startswith("/portal/requests/") or "/revision" in path or "messages/lookup" in path
                or path.endswith("messages/client") or path.endswith("documents/client")):
            return {404}  # another client's request looks the same as one that doesn't exist
        return {401, 403, 404, 409}
    return {401, 403, 404}


# A status move that isn't the next step is refused with 409 ("reload the page"), for staff too (API-05).
ORDER_409 = lambda path: {409} if ("#skip" in path or "#pull back" in path) else set()

calls = 0
for method, path, url, kw, allowed in E:
    wrong = []
    for who in PEOPLE:
        snap.restore()
        c = w.c[who]
        jar = dict(c.cookies)
        res = c.request(method, url, **kw)
        c.cookies.clear()  # a call may sign someone out or rotate the cookie: put it back
        for k, v in jar.items():
            c.cookies.set(k, v)
        calls += 1
        good = res.status_code < 300 if who in allowed else res.status_code in expected_denied(who, path.split(" #")[0]) | ORDER_409(path)
        if not good:
            wrong.append(f"{who} got {res.status_code} ({'should be allowed' if who in allowed else 'should be refused'})")
    check(f"{method} {path}", not wrong, "; ".join(wrong))

check("no network connection or .env read was attempted", not q.REFUSED, q.REFUSED)
print(f"\n{calls} calls. {sum(results)}/{len(results)} checks passed.")
sys.exit(0 if all(results) else 1)

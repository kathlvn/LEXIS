r"""Request lifecycle rules: who may move a status, what a closed or completed
request still allows, withdraw, reassignment, the last Head Attorney, client
verification, walk-ins (no overwrite, letter, category, open-request cap) and
follow-ups.
Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_status_order.py
In-memory SQLite, temp folder for files, emails recorded (see lexis_world.py).

Checks marked KNOWN are open findings from qa-report.md. They print KNOWN
while the problem is still there and don't fail the run; once fixed they print
PASS, and the mark can be removed so they guard the fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lexis_world as q  # noqa: E402  (must come before anything from app/)
from lexis_world import PDF, ok, submit, walkin  # noqa: E402

m = q.m
w = q.build()
T, c = w.t, w.c
H, L, ATT, A = c["head"], c["la1"], c["atty"], c["A"]
snap = q.Snapshot()
results, known_open = [], []


def check(name, good, detail="", known=None):
    if known and not good:
        known_open.append(known)
        print(f"KNOWN {name} ({known} in qa-report.md)" + (f"  -> {detail}" if detail != "" else ""))
        return
    results.append(bool(good))
    print(("PASS " if good else "FAIL ") + name + (f"  -> {detail}" if detail != "" and not good else ""))


def refused(res):
    return 400 <= res.status_code < 500


def code(res):
    return f"{res.status_code} {res.text[:120]}"


def req(tid):
    return q.one(m.LegalServiceRequest, m.LegalServiceRequest.tracking_id == tid)


A1, AREV, ADONE, ACLOSED = (f"/requests/{T[k]}" for k in ("A1", "Arev", "Adone", "Aclosed"))

# --- who may move a status ---------------------------------------------------
for to in ("Approved", "Completed"):
    snap.restore()
    r = L.patch(A1 + "/status", json={"new_status": to})
    check(f"an assistant can't set {to}", r.status_code == 403, code(r))
snap.restore()
r = L.patch(A1 + "/status", json={"new_status": "For Attorney Review"})
check("an assistant can't skip steps (Submitted -> For Attorney Review)", refused(r), code(r))
snap.restore()
r = H.patch(A1 + "/status", json={"new_status": "Completed"})
check("Submitted can't jump to Completed (no review, no approval)", refused(r), code(r))
r = H.patch(ADONE + "/status", json={"new_status": "Submitted"})
check("Completed can't go back to Submitted", refused(r), code(r))
snap.restore()
r = ATT.patch(AREV + "/status", json={"new_status": "Processing"})
check("review -> Processing needs Return with a comment", refused(r), code(r))
snap.restore()
r = ATT.patch(AREV + "/status", json={"new_status": "Approved"})
stale = H.patch(AREV + "/status", json={"new_status": "Approved"})  # a second page, opened before
check("a page left open can't repeat or undo a move (409)", r.status_code < 300 and stale.status_code == 409, code(stale))
snap.restore()
for step in ("Received", "Under Review", "Processing", "For Attorney Review", "Approved", "Completed"):
    ok(H.patch(A1 + "/status", json={"new_status": step}), "move A1 to " + step)  # A1 was waiting for the client
check("completing a request clears Waiting for Client", not req(T["A1"]).is_waiting_for_client)

# --- completed and closed requests -------------------------------------------
snap.restore()
r = A.post(f"/portal/requests/{T['Adone']}/withdraw", json={})
check("a client can't withdraw a completed request", refused(r), code(r))
r = H.post(ADONE + "/return", json={"comment": "x"})
check("a completed request can't be returned", refused(r), code(r))
calls = [("status change", lambda: H.patch(ACLOSED + "/status", json={"new_status": "Received"})),
         ("waiting for client", lambda: L.patch(ACLOSED + "/status", json={"is_waiting_for_client": True})),
         ("return", lambda: H.post(ACLOSED + "/return", json={"comment": "x"})),
         ("close again", lambda: H.post(ACLOSED + "/close", json={"action": "reject", "reason": "x"})),
         ("reassign", lambda: H.patch(ACLOSED + "/reassign", json={"new_officer_id": w.id["la2"]})),
         ("reminder", lambda: L.post(ACLOSED + "/remind")),
         ("client upload", lambda: A.post(ACLOSED + "/documents/client", files=[("files", ("l.pdf", PDF))])),
         ("client message", lambda: A.post(ACLOSED + "/messages/client", data={"body": "x"})),
         ("client withdraw", lambda: A.post(f"/portal/requests/{T['Aclosed']}/withdraw", json={}))]
for name, f in calls:
    snap.restore()
    r = f()
    check(f"cancelled request: {name} is refused", refused(r), code(r))
snap.restore()
r = H.post(A1 + "/reopen", json={"reason": "x"})
check("an open request can't be reopened", refused(r), code(r))
r = A.post(f"/portal/requests/{T['A1']}/withdraw", json={"reason": "changed my mind"})
check("a client can withdraw an open request", r.status_code < 300, code(r))

# --- reassignment and the last Head Attorney ---------------------------------
for name, staff_id, known in (("a deactivated assistant", w.id["la3"], None), ("an id that doesn't exist", 99999, None),
                              ("staff who can't sign in yet", w.id["new"], None)):
    snap.restore()
    r = H.patch(A1 + "/reassign", json={"new_officer_id": staff_id})
    check(f"can't reassign to {name}", refused(r), code(r), known=known)
snap.restore()
r = H.patch(f"/staff/{w.id['head']}", json={"role": "attorney"})
check("the last Head Attorney can't demote themself", refused(r), code(r))
r = H.patch(f"/staff/{w.id['head']}", json={"is_active": False})
check("the last Head Attorney can't deactivate themself", refused(r), code(r))

# --- client verification -----------------------------------------------------
snap.restore()
r = L.post(f"/requests/{T['D1']}/client/verify", json={"method": "endorsement_letter"})
check("can't verify by letter when the client sent no letter", refused(r), code(r))

# --- walk-ins (CLAUDE.md "Walk-ins") ------------------------------------------
LA2 = c["la2"]
cases = [("existing verified client, any capitals, no letter", dict(email="A@EXAMPLE.com", letter=False), True),
         ("new ESSU client, legal service, no letter", dict(email="n1@example.com", letter=False), False),
         ("new ESSU client, complaint, no letter", dict(email="n2@example.com", letter=False, code="complaint_referral"), True),
         ("new student asking for Legal Advice", dict(email="n4@example.com", cat="essu_student"), False),
         ("revoked client without a new letter", dict(email="e@example.com", letter=False), False),
         ("unverified client with one open request (cap)", dict(email="b@example.com"), False),
         ("same phone typed with dashes (cap)", dict(phone="0917-0001111"), False)]
for name, kw, allowed in cases:
    snap.restore()
    r = walkin(LA2, kw.pop("code", "legal_advice"), "walk-in " + name, **kw)
    check(f"walk-in: {name} is {'accepted' if allowed else 'refused'}",
          (r.status_code < 300) == allowed, code(r))
snap.restore()
ok(walkin(LA2, "legal_advice", "x", email="a@example.com", letter=False), "walk-in for A")
a = q.one(m.Requester, m.Requester.email == "a@example.com")
check("walk-in never overwrites an existing client's saved name", a.last_name == "Aquino", a.full_name)
snap.restore()
# E's old request is closed first: while it is open the unverified cap blocks the walk-in
# (an open office rule, item 5 under "Office rules" in qa-report.md).
ok(H.post(f"/requests/{T['E1']}/close", json={"action": "cancel", "reason": "test"}), "close E1")
ok(walkin(LA2, "legal_advice", "x", email="e@example.com"), "revoked with letter")
e = q.one(m.Requester, m.Requester.email == "e@example.com")
check("a revoked client with a new letter stays revoked (only an attorney may verify again)",
      e.verification_status == "revoked", e.verification_status)
snap.restore()
r = walkin(LA2, "legal_advice", "x", phone="+639170001111")
check("+63 and 09 forms of one phone are the same client (cap applies)", refused(r), code(r))

# --- follow-ups and duplicates -------------------------------------------------
snap.restore()
ok(H.put("/settings/max_requests_per_day", json={"value": "20"}), "raise the daily limit")
base = q.Snapshot()
for label, tid, allowed in (("their own open request", T["A1"], True), ("someone else's request", T["B1"], False),
                            ("a request that doesn't exist", "LEO-0000-000000", False)):
    base.restore()
    r = submit(A, "legal_advice", "follow-up of " + label, request_type="followup", followup_tracking_id=tid)
    check(f"follow-up of {label} is {'accepted' if allowed else 'refused'}", (r.status_code < 300) == allowed, code(r))
base.restore()
r = submit(A, "legal_advice", "A1 private concern")
check("the same request text sent again is refused", r.status_code == 409, code(r))

check("no network connection or .env read was attempted", not q.REFUSED, q.REFUSED)
print(f"\n{sum(results)}/{len(results)} checks passed; {len(known_open)} known open findings still there.")
sys.exit(0 if all(results) else 1)

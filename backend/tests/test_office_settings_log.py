r"""Checks that changes to offices, service types and holidays are recorded in
the Security Log (manuscript Figure 4.1). Plain Python, no pytest. From backend/:
    ..\.venv\Scripts\python.exe tests\test_office_settings_log.py
Uses a throwaway in-memory SQLite database: the real Postgres is never touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from fastapi import Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.main as main  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.core.deps import get_current_staff_user  # noqa: E402
from app.core.security_monitoring import limiter  # noqa: E402
from app.models.models import (  # noqa: E402
    LegalServiceRequest, Requester, SecurityEvent, ServiceType, StaffUser, WorkflowStepTemplate,
)

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
main.engine = engine  # the 429 handler logs with main.engine

with Session(engine) as s:
    head = StaffUser(full_name="Head", email="head@essu.edu.ph", hashed_password="x", role="head_attorney")
    la = StaffUser(full_name="Assistant", email="la@essu.edu.ph", hashed_password="x", role="legal_assistant")
    s.add_all([head, la])
    s.add_all([Requester(full_name=f"C{i}", client_category="essu_employee", company_agency="Guidance Unit")
               for i in range(2)])
    s.commit()
    HEAD, LA = head.id, la.id
acting = {"id": HEAD}


def override_session():
    with Session(engine) as s:
        yield s


main.app.dependency_overrides[get_session] = override_session
main.app.dependency_overrides[get_current_staff_user] = lambda s=Depends(get_session): s.get(StaffUser, acting["id"])
client = TestClient(main.app)  # no "with": the startup seeding against Postgres doesn't run
limiter.enabled = False

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


seen = {"id": 0}


def new_events():
    """(type, detail) of the events logged since the last call."""
    with Session(engine) as s:
        rows = s.exec(select(SecurityEvent).where(SecurityEvent.id > seen["id"]).order_by(SecurityEvent.id)).all()
        if rows:
            seen["id"] = rows[-1].id
        return [(e.event_type, e.detail) for e in rows]


def expect(name, response, code, *events):
    got = new_events()
    check(name, response.status_code == code and got == list(events), (response.status_code, response.text[:200], got))


# --- Offices ---------------------------------------------------------------------
r = client.post("/offices", json={"name": "Registrar", "office_type": "office"})
expect("office added", r, 201, ("office_added", 'Head added the office "Registrar".'))
check("the answer still has the office", r.json()["name"] == "Registrar" and r.json()["is_active"] is True, r.text)
OFFICE = r.json()["id"]
expect("office already listed: nothing logged", client.post("/offices", json={"name": "registrar"}), 409)
r = client.patch(f"/offices/{OFFICE}", json={"name": "Office of the Registrar", "office_type": "college"})
expect("renamed and type changed: one entry", r, 200, (
    "office_changed", 'Head changed the office "Registrar": renamed to "Office of the Registrar"; '
                      'type changed from office to college.'))
check("the answer has the new name", r.json()["name"] == "Office of the Registrar", r.text)
expect("saved with no change: nothing logged",
       client.patch(f"/offices/{OFFICE}", json={"name": "Office of the Registrar", "office_type": "college",
                                                "is_active": True}), 200)
expect("switched off", client.patch(f"/offices/{OFFICE}", json={"is_active": False}), 200,
       ("office_changed", 'Head changed the office "Office of the Registrar": switched off.'))
expect("switched on", client.patch(f"/offices/{OFFICE}", json={"is_active": True}), 200,
       ("office_changed", 'Head changed the office "Office of the Registrar": switched on.'))
expect("no such office: nothing logged", client.patch("/offices/999", json={"is_active": False}), 404)
r = client.post("/offices/from-typed", json={"typed_name": "Guidance Unit", "name": "Guidance Office"})
expect("typed office added", r, 200, ("office_added", 'Head added the office "Guidance Office", typed by clients.'))
check("and both clients linked", r.json()["linked_clients"] == 2 and r.json()["created"] is True, r.text)
with Session(engine) as s:
    for c in s.exec(select(Requester)).all():
        c.office_id, c.company_agency = None, "guidance office"
        s.add(c)
    s.commit()
r = client.post("/offices/from-typed", json={"typed_name": "guidance office", "name": "Guidance Office"})
expect("clients linked to a listed office: the list didn't change, nothing logged", r, 200)
check("that office wasn't added again", r.json()["created"] is False, r.text)
with Session(engine) as s:
    for c in s.exec(select(Requester)).all():
        c.office_id, c.company_agency = None, "guidance office"
        s.add(c)
    s.commit()
client.patch(f"/offices/{r.json()['office']['id']}", json={"is_active": False})
new_events()
expect("typed office that was off: logged as switched on",
       client.post("/offices/from-typed", json={"typed_name": "guidance office", "name": "Guidance Office"}), 200,
       ("office_changed", 'Head changed the office "Guidance Office": switched on.'))

# --- Service types ------------------------------------------------------------------
r = client.post("/service-types", json={"name": "Notarial", "default_processing_days": 2})
expect("service added", r, 201, ("service_added", 'Head added the service "Notarial".'))
URL = f"/service-types/{r.json()['code']}"
check("the answer still has the service", r.json()["name"] == "Notarial" and len(r.json()["steps"]) > 3, r.text)
expect("name already used: nothing logged", client.post("/service-types", json={"name": "notarial"}), 409)
steps = [x for x in r.json()["steps"] if x not in ("Submitted", "Processing", "Completed")]
expect("saved with no change: nothing logged",
       client.patch(URL, json={"name": "Notarial", "description": "", "default_processing_days": 2,
                               "optional_steps": steps}), 200)
expect("name, description, days: one entry",
       client.patch(URL, json={"name": "Notary", "description": "Text", "default_processing_days": 3}), 200, (
           "service_changed", 'Head changed the service "Notarial": renamed to "Notary"; description edited; '
                              'processing days changed from 2 to 3.'))
expect("no set period", client.patch(URL, json={"clear_processing_days": True}), 200,
       ("service_changed", 'Head changed the service "Notary": processing days changed from 3 to no set period.'))
expect("step off", client.patch(URL, json={"optional_steps": [x for x in steps if x != "Received"]}), 200,
       ("service_changed", 'Head changed the service "Notary": step "Received" switched off.'))
expect("step on", client.patch(URL, json={"optional_steps": steps}), 200,
       ("service_changed", 'Head changed the service "Notary": step "Received" switched on.'))
expect("a step that can't be switched: nothing logged", client.patch(URL, json={"optional_steps": ["Submitted"]}), 400)
with Session(engine) as s:
    st = s.exec(select(ServiceType)).one()
    step = s.exec(select(WorkflowStepTemplate).where(WorkflowStepTemplate.status_name == "Received")).one()
    s.add(LegalServiceRequest(tracking_id="LEO-2026-000001", requester_id=1, service_type_id=st.id, description="x",
                              current_step_id=step.id))
    s.commit()
r = client.patch(URL, json={"name": "Other", "optional_steps": [x for x in steps if x != "Received"]})
expect("refused (a request is at that step): nothing logged", r, 409)
check("and nothing was changed", client.get("/service-types/manage").json()[0]["name"] == "Notary")

DOCS = URL + "/requirements"
r = client.put(DOCS, json={"items": [{"label": "ID"}, {"label": "Letter"}]})
expect("documents added", r, 200,
       ("service_changed", 'Head changed the required documents of "Notary": added "ID", "Letter".'))
a, b = [x["id"] for x in r.json()["requirements"]]
expect("same list again: nothing logged",
       client.put(DOCS, json={"items": [{"id": a, "label": "ID"}, {"id": b, "label": "Letter"}]}), 200)
expect("order changed", client.put(DOCS, json={"items": [{"id": b, "label": "Letter"}, {"id": a, "label": "ID"}]}), 200,
       ("service_changed", 'Head changed the required documents of "Notary": order changed.'))
expect("edited and switched off",
       client.put(DOCS, json={"items": [{"id": b, "label": "Letter", "is_required": False}]}), 200,
       ("service_changed", 'Head changed the required documents of "Notary": edited "Letter"; switched off "ID".'))
expect("switched back on",
       client.put(DOCS, json={"items": [{"id": b, "label": "Letter", "is_required": False},
                                        {"id": a, "label": "ID"}]}), 200,
       ("service_changed", 'Head changed the required documents of "Notary": added "ID".'))
expect("listed twice: nothing logged", client.put(DOCS, json={"items": [{"label": "x"}, {"label": "X"}]}), 400)

WHO = URL + "/categories"
expect("same categories: nothing logged",
       client.put(WHO, json={"categories": ["essu_office", "essu_employee"]}), 200)
expect("who may use it", client.put(WHO, json={"categories": ["essu_office", "essu_student"]}), 200,
       ("service_changed", 'Head changed who may use "Notary": added ESSU – Student; removed ESSU – Employee.'))

# --- Holidays -------------------------------------------------------------------------
r = client.post("/holidays", json={"holiday_date": "2027-01-01", "name": "New Year's Day", "holiday_type": "regular"})
expect("holiday added", r, 201, ("holiday_added", 'Head added 2027-01-01 "New Year\'s Day" (regular).'))
check("the answer still has the holiday", r.json()["name"] == "New Year's Day", r.text)
HOLIDAY = r.json()["id"]
expect("date already listed: nothing logged",
       client.post("/holidays", json={"holiday_date": "2027-01-01", "name": "x", "holiday_type": "local"}), 409)
expect("holiday removed", client.delete(f"/holidays/{HOLIDAY}"), 204,
       ("holiday_removed", 'Head removed 2027-01-01 "New Year\'s Day" (regular).'))
expect("no such holiday: nothing logged", client.delete(f"/holidays/{HOLIDAY}"), 404)

# --- Long details, roles, labels --------------------------------------------------------
client.post("/offices", json={"name": "A" * 200})
new_events()
office = next(o for o in client.get("/offices/manage").json() if o["name"].startswith("AAA"))
r = client.patch(f"/offices/{office['id']}", json={"name": "B" * 200})
long = new_events()
check("long names: saved, and who did it is kept", r.status_code == 200 and long[0][1].startswith("Head changed the office"))
r = client.put(DOCS, json={"items": [{"label": f"{i} " + "C" * 190} for i in range(20)]})
long = new_events()
check("a very long change: saved, cut to 500, who did it is kept", r.status_code == 200 and len(long) == 1
      and len(long[0][1]) == 500 and long[0][1].startswith("Head changed the required documents"), long)
acting["id"] = LA
for name, call in [("office", lambda: client.post("/offices", json={"name": "Z"})),
                   ("service", lambda: client.patch(URL, json={"name": "Z"})),
                   ("required documents", lambda: client.put(DOCS, json={"items": []})),
                   ("who may use it", lambda: client.put(WHO, json={"categories": []})),
                   ("holiday", lambda: client.post("/holidays", json={"holiday_date": "2027-02-02", "name": "Z",
                                                                      "holiday_type": "local"}))]:
    expect(f"a legal assistant still can't change: {name}", call(), 403)
acting["id"] = HEAD
listed = client.get("/security-events").json()
check("the page shows a plain label for each kind",
      {(e["event_type"], e["label"]) for e in listed} == {
          ("office_added", "Office added"), ("office_changed", "Office changed"),
          ("service_added", "Service added"), ("service_changed", "Service changed"),
          ("holiday_added", "Holiday added"), ("holiday_removed", "Holiday removed")},
      {(e["event_type"], e["label"]) for e in listed})
check("none of them raises an alert",
      not any(t["unusual"] or t["threshold"] for t in client.get("/security-events/summary").json()["types"]
              if t["event_type"].startswith(("office_", "service_", "holiday_"))))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

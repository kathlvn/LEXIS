"""
Runs on every backend startup (see main.py), and can also be run by hand:
`python -m app.seed`. Safe to run any number of times.

Keeps the service types in line with the six legal services in the thesis
scope, each with the default workflow
from Table 4. Processing days come from the Legal Office's Citizen's
Charter; None means the Charter sets no period, so no due date is computed.

Codes never change once created — existing requests point at the row, and
the code "stays the same even if the displayed name is later reworded".
Names and days are only filled in when a service is first
created, or when it still has its old seed name from an earlier version, so
edits the Head Attorney makes later (Service Types page) are never
overwritten by a restart.
"""
from sqlmodel import Session, select
from app.core.database import engine, create_db_and_tables
from app.core.working_days import compute_due_date, load_holiday_dates
from app.models.models import (
    CLIENT_CATEGORIES, LegalServiceRequest, ServiceCategoryAccess, ServiceRequirement, ServiceType,
    SystemSetting, WorkflowStepTemplate,
)

DEFAULT_STATUS_SEQUENCE = [
    "Submitted",
    "Received",
    "Under Review",
    "Processing",
    "For Attorney Review",
    "Approved",
    "Completed",
]


# File a Complaint / Referral. Follows the Legal
# Office's "Reception of Complaints or Referrals from Agencies" flowchart
# (OOM): validation through REMO stamping / Office of the President referral,
# then a clarificatory meeting, before evaluation. Processing, Submitted and
# Completed are kept because the approval rules and the Return button use them.
COMPLAINT_STATUS_SEQUENCE = [
    "Submitted",
    "Received",
    "For Validation",          # REMO stamp and/or Office of the President referral
    "Under Review",            # inspection of content, identification of issues
    "Clarificatory Meeting",   # notice to the parties; meeting or ADR
    "Processing",              # evaluation, legal research, drafting the recommendation
    "For Attorney Review",
    "Approved",
    "Completed",
]

# Which step list each door uses (ServiceType.door).
STATUS_SEQUENCES = {
    "service": DEFAULT_STATUS_SEQUENCE,
    "complaint": COMPLAINT_STATUS_SEQUENCE,
}

# (code, name, description, default_processing_days)
SERVICE_TYPES = [
    ("legal_advice", "Legal Advice",
     "Advice on legal concerns involving official ESSU functions or transactions.", 2),
    ("legal_research", "Legal Research", None, None),
    ("contract_review", "Review of MOA/MOU, Contracts, and Similar Documents", None, 6),
    ("investigation", "Conduct of Investigation", None, 23),
    ("drafting_document", "Drafting of Legal Documents", None, 5),
    ("other", "Other Legal Concerns", None, None),
    ("complaint_referral", "Complaint / Referral",
     "File a complaint or referral involving ESSU, its offices, or its personnel. "
     "Open to anyone, including persons and agencies outside ESSU.", None),
]

# Services that belong to the "File a Complaint / Referral" door; every
# other service is "service" (Request a Legal Service).
SERVICE_DOORS = {"complaint_referral": "complaint"}

# Starting checklist of documents per service, taken from
# the "Checklist of Requirements" in the Legal Office's Citizen's Charter
# where it names them, otherwise a general line. Filled in only while a
# service's list is still empty (NULL); the Head Attorney edits it on the
# Service Types page, and an emptied list is never refilled.
DEFAULT_REQUIRED_DOCUMENTS = {
    "legal_advice": "Documents related to your concern, if any",
    "legal_research": "Documents related to the matter to be researched, if any",
    "contract_review": "Copy of the MOA, MOU, contract, or draft ordinance to be reviewed\n"
                       "Supporting resolution or authority, if any",
    "investigation": "Written complaint or referral\n"
                     "Supporting evidence, such as affidavits, records, or photos\n"
                     "Board or University resolution or instruction, if any",
    "drafting_document": "All pertinent files needed to prepare the document",
    "other": "Documents related to your concern, if any",
    "complaint_referral": "Signed written complaint or letter of referral\n"
                          "Supporting evidence, such as affidavits, records, or photos, if any",
}

# Who may use each service. Follows the Citizen's
# Charter ("office/department heads/key officials"); students may request
# Legal Research only. Applied only when a service is
# first created; after that the Head Attorney decides on the Service Types page.
DEFAULT_CATEGORIES = {
    code: ["essu_office", "essu_employee"] for code, *_ in SERVICE_TYPES
}
DEFAULT_CATEGORIES["legal_research"] = ["essu_office", "essu_employee", "essu_student"]
DEFAULT_CATEGORIES["complaint_referral"] = list(CLIENT_CATEGORIES)   # anyone, incl. outsiders


DEFAULT_ENDORSEMENT_TEMPLATE = """[DRAFT \u2014 wording to be approved by the ESSU Legal Office]

ENDORSEMENT FOR LEGAL SERVICE REQUEST

Date: ____________________

The Attorney IV
Legal Office
Eastern Samar State University
Borongan City, Eastern Samar

Ma'am/Sir:

This is to endorse ______________________________ (name of requester),
______________________________ (position, or year and program for students),
of ______________________________ (office / college / campus),
to request legal services from the ESSU Legal Office through the
Legal Executive Information System (LEXIS).

Nature of the request (brief):
____________________________________________________________

I confirm that the person named above belongs to our office / college.

______________________________
Printed name and signature
Position (Office/Department Head, Dean, or Key Official)
Office / College / Campus
Contact number: ____________________"""

# Office-editable settings. Added only when the key is missing.
DEFAULT_SETTINGS = {
    "endorsement_template": DEFAULT_ENDORSEMENT_TEMPLATE,
    "verification_valid_days": "365",       # how long a verified client stays verified
    "unverified_max_open_requests": "1",    # open requests allowed while still unverified
    "max_requests_per_day": "5",            # any client: new requests per 24 hours
}


# Names the earlier seed used. A row that still has one of these is updated
# to the new name and days; anything else was edited on purpose and is kept.
OLD_SEED_NAMES = {
    "legal_advice": "Legal Advice (ESSU-related case only)",
    "contract_review": "Contract Review",
    "drafting_document": "Drafting of Legal Document",
    "other": "Other",
}

# Services that are no longer offered -> the service their requests move to.
# Development/test data only (no real records are in LEXIS yet).
RETIRED_SERVICES = {
    "notarization": "other",
    "dispute_complaint": "other",
}


def _add_default_steps(session: Session, service_type: ServiceType) -> None:
    sequence = STATUS_SEQUENCES.get(service_type.door, DEFAULT_STATUS_SEQUENCE)
    for i, status_name in enumerate(sequence, start=1):
        session.add(WorkflowStepTemplate(
            service_type_id=service_type.id,
            sequence_order=i,
            status_name=status_name,
        ))


def _add_default_requirements(session: Session, service_type: ServiceType) -> None:
    """Checklist items for a NEW service, from DEFAULT_REQUIRED_DOCUMENTS.
    A line ending in "if any" becomes optional (same rule as the migration that added checklists)."""
    lines = (DEFAULT_REQUIRED_DOCUMENTS.get(service_type.code) or "").splitlines()
    order = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        order += 1
        optional = line.lower().endswith("if any")
        label = line[: -len("if any")].rstrip(" ,") if optional else line
        session.add(ServiceRequirement(
            service_type_id=service_type.id, label=label[:200],
            is_required=not optional, sort_order=order,
        ))


def _add_default_categories(session: Session, service_type: ServiceType) -> None:
    for category in DEFAULT_CATEGORIES.get(service_type.code, []):
        session.add(ServiceCategoryAccess(service_type_id=service_type.id, client_category=category))


def _seed_settings(session: Session) -> None:
    added = 0
    for key, value in DEFAULT_SETTINGS.items():
        if not session.get(SystemSetting, key):
            session.add(SystemSetting(key=key, value=value))
            added += 1
    if added:
        session.commit()
        print(f"Added {added} default setting(s).")
        

def _sync_service_types(session: Session) -> None:
    for code, name, description, days in SERVICE_TYPES:
        existing = session.exec(select(ServiceType).where(ServiceType.code == code)).first()

        if existing and existing.required_documents is None and code in DEFAULT_REQUIRED_DOCUMENTS:
            existing.required_documents = DEFAULT_REQUIRED_DOCUMENTS[code]
            session.add(existing)
            session.commit()

        if not existing:
            service_type = ServiceType(
                code=code, name=name, description=description, default_processing_days=days,
                  required_documents=DEFAULT_REQUIRED_DOCUMENTS.get(code),
                  door=SERVICE_DOORS.get(code, "service"),
            )
            session.add(service_type)
            session.commit()
            session.refresh(service_type)
            _add_default_steps(session, service_type)
            _add_default_requirements(session, service_type)
            _add_default_categories(session, service_type)
            session.commit()
            print(f"Seeded service type '{name}'.")
            continue

        if existing.name == OLD_SEED_NAMES.get(code):
            existing.name = name
            existing.description = description
            existing.default_processing_days = days
            session.add(existing)
            session.commit()
            print(f"Updated service type '{code}' to '{name}'.")


def _retire_old_service_types(session: Session) -> None:
    for old_code, new_code in RETIRED_SERVICES.items():
        old = session.exec(select(ServiceType).where(ServiceType.code == old_code)).first()
        if not old:
            continue
        new = session.exec(select(ServiceType).where(ServiceType.code == new_code)).first()
        if not new:
            print(f"Could not retire '{old_code}': service '{new_code}' does not exist.")
            continue

        old_steps = session.exec(
            select(WorkflowStepTemplate).where(WorkflowStepTemplate.service_type_id == old.id)
        ).all()
        new_step_by_name = {
            s.status_name: s.id
            for s in session.exec(
                select(WorkflowStepTemplate).where(WorkflowStepTemplate.service_type_id == new.id)
            ).all()
        }
        old_step_name = {s.id: s.status_name for s in old_steps}

        # Move each request to the new service, keeping the same status by
        # name (both services use the same default steps).
        moved = 0
        for req in session.exec(
            select(LegalServiceRequest).where(LegalServiceRequest.service_type_id == old.id)
        ).all():
            status_name = old_step_name.get(req.current_step_id)
            req.service_type_id = new.id
            req.current_step_id = new_step_by_name.get(status_name) if status_name else None
            session.add(req)
            moved += 1
        session.commit()

        for step in old_steps:
            session.delete(step)
        session.commit()
        session.delete(old)
        session.commit()
        print(f"Retired service type '{old_code}'; moved {moved} request(s) to '{new_code}'.")


def _backfill_due_dates(session: Session) -> None:
    """Requests submitted before due dates existed get one,
    computed from their own submission time. Only fills empty due dates, so
    it runs once per request and never changes a due date already set."""
    holidays = load_holiday_dates(session)
    days_by_service = {
        s.id: s.default_processing_days for s in session.exec(select(ServiceType)).all()
    }
    filled = 0
    for req in session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.due_date.is_(None))
    ).all():
        due = compute_due_date(req.submitted_at, days_by_service.get(req.service_type_id), holidays)
        if due:
            req.due_date = due
            session.add(req)
            filled += 1
    if filled:
        session.commit()
        print(f"Filled in due dates for {filled} existing request(s).")


def seed():
    create_db_and_tables()
    with Session(engine) as session:
        _sync_service_types(session)
        _retire_old_service_types(session)
        _backfill_due_dates(session)
        _seed_settings(session)


if __name__ == "__main__":
    seed()

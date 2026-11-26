"""
Core data models for LEXIS (Legal Executive Information System).

These follow the architectural decisions already locked in during the
ERD/OOM phase:
- Requesters are linked across requests by matching contact info (email or
  phone), not persistent accounts. At least one contact method is required.
- Each service type defines its own configurable workflow sequence via
  WorkflowStepTemplate, rather than a single fixed status enum.
- Documents live in one unified table with a `version` column, instead of
  separate tables per document category.
- Only staff can trigger cancellation/rejection.
- "Waiting for Client" does not pause the due date — that's handled at the
  application layer (see routers/requests.py), not the schema.
"""
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import SQLModel, Field, Relationship


# Client categories. The code is stored; the label is shown.
CLIENT_CATEGORIES = {
    "essu_office": "ESSU \u2013 Office / Department",
    "essu_employee": "ESSU \u2013 Employee",
    "essu_student": "ESSU \u2013 Student",
    "ext_government": "External \u2013 Government agency",
    "ext_private": "External \u2013 Private individual or organization",
}
ESSU_CATEGORIES = {"essu_office", "essu_employee", "essu_student"}

# Requester.verification_status values.
#   unverified - contact confirmed by code only (or a record from before client verification existed)
#   pending    - waiting for staff to check the endorsement / referral
#   verified   - staff confirmed who the client is
#   declined   - staff could not confirm it
#   revoked    - staff withdrew an earlier verification
VERIFICATION_STATUSES = ("unverified", "pending", "verified", "declined", "revoked")

def build_full_name(first: str, middle: str | None, last: str, extension: str | None) -> str:
    """Natural order for screens and emails: "Juan Santos Dela Cruz Jr."."""
    return " ".join(p for p in (first, middle, last, extension) if p)[:150]


def formal_name(last: str | None, first: str | None, middle: str | None, extension: str | None,
                fallback: str = "") -> str:
    """Formal order for records and reports: "DELA CRUZ, Juan Santos Jr."
    Falls back to the full name for records without name parts."""
    if not (last and first):
        return fallback
    given = " ".join(p for p in (first, middle, extension) if p)
    return f"{last.upper()}, {given}"

def effective_verification_status(client: "Requester") -> str:
    """A client's verification status as staff should act on it: a
    verification past its end date counts as "expired"."""
    if client.verification_status == "verified" and client.verification_expires_at \
            and client.verification_expires_at <= datetime.utcnow():
        return "expired"
    return client.verification_status


class Office(SQLModel, table=True):
    """
    LEXIS's own repository of ESSU offices, colleges and campuses.
    ESSU has no central directory LEXIS can read from, so the Head
    Attorney keeps this list; clients pick their office from it instead of
    typing it, which keeps reports by office clean.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=200, unique=True)
    office_type: str = Field(default="office", max_length=20)  # "office" | "college" | "campus" | "other"
    campus: Optional[str] = Field(default=None, max_length=100)
    is_active: bool = Field(default=True)  # hidden from the client form when False; never deleted
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Requester(SQLModel, table=True):
    # One client per email, capitals ignored: two sign-ups or walk-ins
    # saved at the same moment can't make two records. Clients with no email
    # (phone-only walk-ins) are not affected. A migration adds it
    # to an existing database.
    __table_args__ = (Index("uq_requester_email_lower", text("lower(email)"), unique=True),)

    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str = Field(max_length=150)   # built from the parts below (natural order); older records: as typed
    # Name parts, as on government forms. Empty on
    # records from before the parts existed, until the client completes them.
    last_name: Optional[str] = Field(default=None, max_length=80)
    first_name: Optional[str] = Field(default=None, max_length=80)
    middle_name: Optional[str] = Field(default=None, max_length=80)
    name_extension: Optional[str] = Field(default=None, max_length=10)   # Jr., Sr., III
    email: Optional[str] = Field(default=None, index=True, max_length=254)
    phone: Optional[str] = Field(default=None, index=True, max_length=20)
    company_agency: Optional[str] = Field(default=None, max_length=200)  # external clients: their agency/organization
    client_category: Optional[str] = Field(default=None, max_length=30)   # a CLIENT_CATEGORIES code
    office_id: Optional[int] = Field(default=None, foreign_key="office.id")  # ESSU clients only
    position: Optional[str] = Field(default=None, max_length=150)
    verification_status: str = Field(default="unverified", max_length=20)  # see VERIFICATION_STATUSES
    verification_method: Optional[str] = Field(default=None, max_length=30)  # "endorsement_letter" | "in_person" | "referral"
    verified_by_staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    verified_at: Optional[datetime] = Field(default=None)
    verification_expires_at: Optional[datetime] = Field(default=None)
    verification_note: Optional[str] = Field(default=None, max_length=500)  # staff only; e.g. why it was declined
    endorsement_document_id: Optional[int] = Field(default=None, foreign_key="document.id")
    privacy_consent_given_at: Optional[datetime] = Field(default=None)  # Data Privacy consent at registration
    created_at: datetime = Field(default_factory=datetime.utcnow)

    requests: list["LegalServiceRequest"] = Relationship(back_populates="requester")


class ServiceType(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=150)  # e.g. "Legal Advice", "Review of MOA/MOU"
    code: str = Field(index=True, unique=True, max_length=50)  # e.g. "legal_advice"
    description: Optional[str] = Field(default=None, max_length=1000)
    default_processing_days: Optional[int] = None
    # Documents the requester should upload, one per line.
    # Shown as a checklist on the Submit Request page and the Home page.
    required_documents: Optional[str] = Field(default=None, max_length=1000)  # old text list; replaced by ServiceRequirement
    door: str = Field(default="service", max_length=10)  # "service" = Request a Legal Service | "complaint" = File a Complaint / Referral

    workflow_steps: list["WorkflowStepTemplate"] = Relationship(back_populates="service_type")


class WorkflowStepTemplate(SQLModel, table=True):
    """
    Defines the ordered sequence of statuses a request of a given service
    type moves through. This is what makes workflows configurable per
    service instead of a single hardcoded status enum.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    service_type_id: int = Field(foreign_key="servicetype.id")
    sequence_order: int
    status_name: str = Field(max_length=50)  # e.g. "Submitted", "Under Review", "For Attorney Review"

    service_type: ServiceType = Relationship(back_populates="workflow_steps")




class ServiceRequirement(SQLModel, table=True):
    """
    One document on a service's checklist. Required items
    must each have a file before the first submission goes through;
    optional ones are shown but not enforced. Must match the client steps
    in the Legal Office's Citizen's Charter (RA 11032). Items are switched
    off (is_active), never deleted, so old uploads keep their link.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    service_type_id: int = Field(foreign_key="servicetype.id", index=True)
    label: str = Field(max_length=200)
    help_text: Optional[str] = Field(default=None, max_length=500)
    is_required: bool = Field(default=True)
    sort_order: int = Field(default=0)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ServiceCategoryAccess(SQLModel, table=True):
    """Which client categories may use a service. One row
    per allowed category; set by the Head Attorney on the Service Types page."""
    __table_args__ = (UniqueConstraint("service_type_id", "client_category"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    service_type_id: int = Field(foreign_key="servicetype.id")
    client_category: str = Field(max_length=30)
    

class StaffUser(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str = Field(max_length=150)
    email: str = Field(index=True, unique=True, max_length=254)  # must be @essu.edu.ph — enforced in the router, not here
    hashed_password: str = Field(max_length=255)
    role: str = Field(max_length=20)  # "legal_assistant" | "attorney" | "head_attorney"
    is_active: bool = Field(default=True)
    is_email_verified: bool = Field(default=False)  # gates login until the new account confirms its inbox
    failed_login_attempts: int = Field(default=0)
    locked_until: Optional[datetime] = Field(default=None)  # basic lockout after repeated bad logins
    must_change_password: bool = Field(default=False)  # set when the Head Attorney chose the password (new account, temporary password); login asks for a new one first
    temp_password_expires_at: Optional[datetime] = Field(default=None)  # when that chosen password stops working (72 hours); cleared when they pick their own
    created_at: datetime = Field(default_factory=datetime.utcnow)

# server-side session for staff users, stored in the database (hashed) instead of a cookie
class StaffSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    staff_id: int = Field(foreign_key="staffuser.id")
    token_hash: str = Field(index=True, unique=True, max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    revoked_at: Optional[datetime] = Field(default=None)

# for gating newly-created accounts until owner confirms access
class StaffEmailVerificationCode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    staff_id: int = Field(foreign_key="staffuser.id")
    code_hash: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    attempts: int = Field(default=0)
    used_at: Optional[datetime] = Field(default=None)
    
class LegalServiceRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tracking_id: str = Field(index=True, unique=True, max_length=20)  # e.g. "LEO-2026-000104"

    requester_id: int = Field(foreign_key="requester.id")
    service_type_id: int = Field(foreign_key="servicetype.id")

    request_type: str = Field(default="new", max_length=10)  # "new" | "followup"
    followup_of_id: Optional[int] = Field(default=None, foreign_key="legalservicerequest.id")

    purpose: Optional[str] = Field(default=None, max_length=500)
    description: str = Field(max_length=5000)

    current_step_id: Optional[int] = Field(default=None, foreign_key="workflowsteptemplate.id")
    assigned_officer_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    prepared_by_officer_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    was_returned_for_revision: bool = Field(default=False)
    staff_note_to_client: Optional[str] = Field(default=None, max_length=1000)
    is_waiting_for_client: bool = Field(default=False)
    reassignment_requested: bool = Field(default=False)
    reassignment_reason: Optional[str] = Field(default=None, max_length=500)
    
    is_cancelled: bool = Field(default=False)
    is_rejected: bool = Field(default=False)
    # Who closed it (cancelled OR rejected); empty when the requester
    # withdrew it themselves.
    cancelled_by_staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    closed_at: Optional[datetime] = Field(default=None)
    closure_reason: Optional[str] = Field(default=None, max_length=1000)   # shown to the requester

    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    due_date: Optional[datetime] = None
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)
    privacy_consent_given_at: Optional[datetime] = Field(default=None)
    
    # The client's details AS SUBMITTED, so a later
    # change to the requester record never rewrites an older request.
    client_name_at_submission: Optional[str] = Field(default=None, max_length=150)
    client_last_name_at_submission: Optional[str] = Field(default=None, max_length=80)
    client_first_name_at_submission: Optional[str] = Field(default=None, max_length=80)
    client_middle_name_at_submission: Optional[str] = Field(default=None, max_length=80)
    client_name_extension_at_submission: Optional[str] = Field(default=None, max_length=10)
    client_category_at_submission: Optional[str] = Field(default=None, max_length=30)
    client_office_at_submission: Optional[str] = Field(default=None, max_length=200)
    client_position_at_submission: Optional[str] = Field(default=None, max_length=150)
    attestation_given_at: Optional[datetime] = Field(default=None)  # "the information I gave is true"
    
    requester: Requester = Relationship(back_populates="requests")
    documents: list["Document"] = Relationship(back_populates="request")
    messages: list["Message"] = Relationship(back_populates="request")


# versioned table
class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="legalservicerequest.id")

    category: str = Field(max_length=20)  # "client_submitted" | "legal_office"
    original_filename: str = Field(max_length=255)
    storage_key: str = Field(max_length=64)  # random object key in file storage — never a public URL directly
    mime_type: Optional[str] = Field(default=None, max_length=100)
    file_size_bytes: Optional[int] = None

    version: int = Field(default=1)
    supersedes_document_id: Optional[int] = Field(default=None, foreign_key="document.id")
    requirement_id: Optional[int] = Field(default=None, foreign_key="servicerequirement.id")  # checklist item this file satisfies; empty = additional file
    message_id: Optional[int] = Field(default=None, foreign_key="message.id", index=True)  # chat message this file was sent with; empty = uploaded as a document
    
    status: str = Field(default="submitted", max_length=20)  # "submitted" | "revision_required"
    # Legal Office documents stay hidden from the requester until staff
    # release them. Client-submitted documents are always visible to the
    # requester who sent them, so this flag only matters for "legal_office".
    is_released_to_requester: bool = Field(default=False)
    uploaded_by_requester_id: Optional[int] = Field(default=None, foreign_key="requester.id")
    uploaded_by_staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    request: LegalServiceRequest = Relationship(back_populates="documents")


# request specific chat
class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="legalservicerequest.id")

    sender_type: str = Field(max_length=10)  # "requester" | "staff"
    sender_staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")

    body: str = Field(max_length=5000)
    sent_at: datetime = Field(default_factory=datetime.utcnow)

    request: LegalServiceRequest = Relationship(back_populates="messages")
    

# Audit trail — one row per significant action, per section 30 of the brief.
class AuditLogEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="legalservicerequest.id")

    # Who did it: "staff", "requester",
    # or "system". staff_id is filled in only when a staff member acted, so no
    # staff member is credited with an action they did not take.
    actor_type: str = Field(default="staff", max_length=10)
    staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    action: str = Field(max_length=50)          # e.g. "status_change"
    detail: str = Field(max_length=1500)         # human-readable, e.g. "Status changed from Under Review to Processing"

    created_at: datetime = Field(default_factory=datetime.utcnow)
    
class StaffNotification(SQLModel, table=True):
    """In-LEXIS notifications for staff (the bell). Staff are not emailed.
    One row per recipient; read_at is set when they open or clear it."""
    id: Optional[int] = Field(default=None, primary_key=True)
    staff_id: int = Field(foreign_key="staffuser.id", index=True)
    request_id: Optional[int] = Field(default=None, foreign_key="legalservicerequest.id")
    kind: str = Field(max_length=40)        # e.g. "assigned", "requester_message", "security_alert"
    message: str = Field(max_length=300)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    read_at: Optional[datetime] = Field(default=None)


class SecurityEvent(SQLModel, table=True):
    """
    Abnormal-usage log: rate-limit trips and failed-login spikes. Distinct
    from AuditLogEntry, which tracks legitimate staff actions tied to a
    specific request — this tracks things that look like abuse and aren't
    tied to any one request.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(max_length=50)  # "rate_limit_exceeded" | "failed_login" | "failed_login_spike"
    detail: str = Field(max_length=500)
    source_ip: str = Field(max_length=45)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    

class RequesterAccessSession(SQLModel, table=True):
    """
      The "wristband" a public requester gets after entering a correct
      one-time code. Only the HMAC digest of the cookie value is stored, same
      as StaffSession. Two kinds:
      - client session (requester_id set): issued at the door; opens every
        request of THAT client (My Requests);
    - request session (request_id set): the retired Track Request flow.
    No longer issued or accepted; the column
    stays for older rows.

    Ends after 30 minutes of no activity (last_seen_at) or 2 hours total
    (expires_at), whichever comes first — or immediately on Exit.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: Optional[int] = Field(default=None, foreign_key="legalservicerequest.id", index=True)
    requester_id: Optional[int] = Field(default=None, foreign_key="requester.id", index=True)
    token_hash: str = Field(index=True, unique=True, max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    revoked_at: Optional[datetime] = Field(default=None)

class ContactVerification(SQLModel, table=True):
    """
    A sign-in code sent to an email address at the door (My Requests).
    For a known client the correct code signs them in; for a new client it
    gives a short-lived cookie that allows exactly ONE registration with
    that same email. Email only now: older rows may
    still say "phone".
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    channel: str = Field(max_length=10)  # "email" | "phone"
    contact_value: str = Field(index=True, max_length=254)  # normalized: email lowercased, both trimmed
    code_hash: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    attempts: int = Field(default=0)
    invalidated_at: Optional[datetime] = Field(default=None)  # replaced by a newer code, or locked
    verified_at: Optional[datetime] = Field(default=None)
    token_hash: Optional[str] = Field(default=None, index=True, max_length=64)  # the cookie, once verified
    consumed_at: Optional[datetime] = Field(default=None)  # used for a submission


class Holiday(SQLModel, table=True):
    """
    Non-working days. Due dates are counted in working days
    (RA 11032), so these dates are skipped along with weekends. The Head
    Attorney enters each year's dates after the presidential proclamation,
    plus local holidays and work suspensions; all staff can view them.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    holiday_date: date = Field(index=True, unique=True)
    name: str = Field(max_length=150)
    holiday_type: str = Field(max_length=20)  # "regular" | "special" | "local" | "suspension"
    created_by_staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SystemSetting(SQLModel, table=True):
    """
    Office-editable settings, one row per setting, edited by the
    Head Attorney on the Settings page so the office can change them after
    turnover without a programmer. Values are stored as text. Defaults are
    added by seed.py only when a key is missing, so edits are never
    overwritten. Keys in use: see DEFAULT_SETTINGS in seed.py.
    """
    key: str = Field(primary_key=True, max_length=50)
    value: str = Field(max_length=5000)
    updated_by_staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# Article.status values.
#   draft     - staff only; any staff member writes one, the author edits it
#   published - shown on the public Home page; only the Head Attorney publishes
#   hidden    - taken down by the Head Attorney; kept, can be published again
ARTICLE_STATUSES = ("draft", "published", "hidden")

class Article(SQLModel, table=True):
    """
    Articles the Legal Office posts on the public Home page.
    Text only, no images. The body is plain text with a few
    simple marks (## heading, **bold**, "- " list, web links); it is escaped
    before those marks are turned into HTML, so no raw HTML from staff is
    ever shown. Articles are hidden, never deleted.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=200)
    body: str = Field(max_length=20000)
    status: str = Field(default="draft", max_length=10, index=True)  # see ARTICLE_STATUSES
    author_staff_id: int = Field(foreign_key="staffuser.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by_staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # First time it went public; the date shown to readers. Kept if the
    # article is hidden and published again.
    published_at: Optional[datetime] = Field(default=None, index=True)
    published_by_staff_id: Optional[int] = Field(default=None, foreign_key="staffuser.id")


class CsmResponse(SQLModel, table=True):
    """
    A client's Client Satisfaction Measurement answers for one completed
    request. The questions come from the editable
    file app/csm_form.json; answers are stored by question id as JSON text,
    with the form's version, so editing the questions never needs a database
    change. One response per request (the unique request_id). Staff see
    totals only on Reports, never one client's answers.
    """
    __tablename__ = "csm_response"

    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="legalservicerequest.id", unique=True)
    form_version: str = Field(max_length=50)
    answers: str = Field(max_length=2000)                         # JSON, e.g. {"CC1": "1", "SQD0": "5"}
    comment: Optional[str] = Field(default=None, max_length=2000)  # the optional suggestions box
    submitted_at: datetime = Field(default_factory=datetime.utcnow, index=True)
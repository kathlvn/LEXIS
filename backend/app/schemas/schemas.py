from datetime import date, datetime
import re
from typing import Annotated, Optional, Literal
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlmodel import SQLModel

from app.core.security import password_problem


class DocumentOut(BaseModel):
    """Staff view of a document."""
    id: int
    category: str
    original_filename: str
    version: int
    supersedes_document_id: Optional[int] = None   # the earlier version this file replaces
    status: str
    is_released_to_requester: bool = False
    uploaded_by_staff_id: Optional[int] = None   # set on a requester's document = added by staff (walk-in)
    uploaded_at: datetime

    class Config:
        from_attributes = True


class DocumentReleaseUpdate(BaseModel):
    """Staff: release a Legal Office document to the requester (True) or
    withdraw it again (False)."""
    released: bool


class PublicDocumentOut(BaseModel):
    """Requester view of a document. Only documents the requester may see
    are ever put in this list (see _to_public_out in routers/requests.py)."""
    id: int
    category: str
    original_filename: str
    version: int
    supersedes_document_id: Optional[int] = None   # the earlier version this file replaces
    status: str
    uploaded_at: datetime

    class Config:
        from_attributes = True


class PublicHistoryEntry(BaseModel):
    """One requester-safe line of the request's history. No staff names."""
    description: str
    created_at: datetime

class AuditLogEntryOut(BaseModel):
    action: str
    detail: str
    staff_name: str          # the staff member, or "Requester" / "System"
    actor_type: str = "staff"
    created_at: datetime

    class Config:
        from_attributes = True
        
class LegalServiceRequestOut(BaseModel):
    tracking_id: str
    service_type_code: str
    service_type_name: Optional[str] = None
    workflow_steps: list[str] = []      # this service's steps, in order
    requester_full_name: str
    requester_company_agency: Optional[str]
    # These fields expose personal contact info — GET /requests/{tracking_id}
    # and GET /requests are now behind get_current_staff_user (see
    # routers/requests.py), so only an authenticated staff session can
    # retrieve them via those routes.
    requester_email: Optional[str] = None
    requester_phone: Optional[str] = None
    request_type: str
    purpose: Optional[str]
    description: str
    current_status: Optional[str]          # "Cancelled"/"Rejected" when closed
    workflow_status: Optional[str] = None  # the workflow step (where a closed request stopped)
    assigned_officer_name: Optional[str]
    assigned_officer_id: Optional[int] = None  # Request Detail: "(you)", the read-only notice, the reassign list
    submitted_at: datetime
    due_date: Optional[datetime]
    deadline_status: Optional[str] = None  # "overdue" | "approaching" | "on_track" | None
    last_updated_at: datetime
    is_waiting_for_client: bool = False
    staff_note_to_client: Optional[str] = None
    is_cancelled: bool
    is_rejected: bool
    closed_at: Optional[datetime] = None
    closure_reason: Optional[str] = None
    closed_by_name: Optional[str] = None     # staff name, or "Requester" when withdrawn
    reassignment_requested: bool = False
    reassignment_reason: Optional[str] = None
    documents: list[DocumentOut] = []
    audit_log: list[AuditLogEntryOut] = []


class PublicRequestOut(BaseModel):
    """What a verified requester (or the person who just submitted) gets
    back. Deliberately leaves out everything internal: unreleased Legal
    Office documents, reassignment fields, and the staff audit log."""
    tracking_id: str
    service_type_code: str
    service_type_name: Optional[str] = None
    workflow_steps: list[str] = []      # this service's steps, in order
    requester_full_name: str
    requester_company_agency: Optional[str]
    requester_email: Optional[str] = None
    requester_phone: Optional[str] = None
    request_type: str
    purpose: Optional[str]
    description: str
    current_status: Optional[str]
    current_status_since: Optional[datetime] = None   # when it reached this status (Request Details)
    processing_days: Optional[int] = None    # the service's working days; None = no set period
    assigned_officer_name: Optional[str]
    submitted_at: datetime
    due_date: Optional[datetime]
    last_updated_at: datetime
    is_waiting_for_client: bool = False
    staff_note_to_client: Optional[str] = None
    is_cancelled: bool
    is_rejected: bool
    is_withdrawn: bool = False               # cancelled by the requester themselves
    can_withdraw: bool = False
    closed_at: Optional[datetime] = None
    closure_reason: Optional[str] = None
    documents: list[PublicDocumentOut] = []
    history: list[PublicHistoryEntry] = []


class DashboardTileCounts(BaseModel):
    """One dashboard tile (GET /requests/summary): how many requests, and how
    they split by deadline (the tile's bar). The parts add up to count."""
    count: int
    overdue: int        # past the due date
    due_soon: int       # due today or the next working day
    on_track: int       # due later
    no_period: int      # no set period (Legal Research, Other Legal Concerns)
    closed: int         # completed, cancelled or rejected (only possible in some tiles)


class LegalServiceRequestListItem(BaseModel):
    tracking_id: str
    service_type_name: str
    requester_id: Optional[int] = None                     # for the client filter
    requester_full_name: str
    requester_company_agency: Optional[str]
    requester_has_email: bool = True
    requester_phone: Optional[str] = None                  # only when the client has no email (call instead of a reminder)
    client_verification_status: Optional[str] = None       # unverified | pending | verified | expired | declined | revoked
    current_status: Optional[str]
    submitted_at: datetime
    due_date: Optional[datetime]
    deadline_status: Optional[str] = None  # "overdue" | "approaching" | "on_track" | None
    assigned_officer_name: Optional[str]
    prepared_by_officer_name: Optional[str] = None
    is_assigned_to_you: bool = False
    is_prepared_by_you: bool = False
    was_returned_for_revision: bool = False
    staff_note_to_client: Optional[str] = None
    is_waiting_for_client: bool = False
    reassignment_requested: bool = False
    reassignment_reason: Optional[str] = None


class StatusUpdate(BaseModel):
    new_status: Optional[str] = Field(default=None, max_length=50)
    is_waiting_for_client: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=1000)
    reassignment_reason: Optional[str] = Field(default=None, max_length=500)
    
    
class ReturnForRevision(BaseModel):
    """Attorney: send forwarded work back to the legal assistant. The
    comment is internal — it goes to the staff History only."""
    comment: str = Field(min_length=1, max_length=1000)


class StaffEncodedRequestCreate(BaseModel):
    """Staff: encode a walk-in or paper request for a requester.
    consent_confirmed = the requester signed the Data Privacy consent on the
    paper form; it is required."""
    requester: "WalkInClientIn"
    service_type_code: str = Field(max_length=50)
    description: str = Field(min_length=1, max_length=5000)
    consent_confirmed: bool

    @model_validator(mode="after")
    def description_not_blank(self):
        # Spaces only would be saved as an empty description.
        self.description = self.description.strip()
        if not self.description:
            raise ValueError("Describe the client's legal concern.")
        return self


class CloseRequest(BaseModel):
    """Attorney: cancel or reject a request. The reason is emailed to the
    requester and shown on Track Request, so it is written for them."""
    action: Literal["cancel", "reject"]
    reason: str = Field(min_length=1, max_length=1000)


class ReopenRequest(BaseModel):
    """Attorney: undo a cancel/reject made by mistake. Internal reason."""
    reason: str = Field(min_length=1, max_length=500)


class WithdrawRequest(BaseModel):
    """Requester: withdraw their own request (optional reason)."""
    reason: Optional[str] = Field(default=None, max_length=500)


class CsmSubmission(BaseModel):
    """Requester: their Client Satisfaction Measurement answers.
    Which question ids and values are valid comes from app/csm_form.json and
    is checked in the router; these limits only cap the size."""
    form_version: str = Field(max_length=50)
    answers: dict[Annotated[str, StringConstraints(max_length=20)],
                  Annotated[str, StringConstraints(max_length=10)]] = Field(max_length=50)
    comment: Optional[str] = Field(default=None, max_length=2000)


class CsmOut(BaseModel):
    """The survey on the requester's page: not_offered (request not
    Completed), open (form included), answered, or unavailable (the form
    file has a problem)."""
    status: Literal["not_offered", "open", "answered", "unavailable"]
    form: Optional[dict] = None
    answered_at: Optional[datetime] = None


class ReassignRequest(BaseModel):
    new_officer_id: Optional[int] = None
    
    
class StaffLogin(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class StaffNewPassword(BaseModel):
    """POST /auth/new-password: the password the Head Attorney gave (new
    account or temporary password) and the staff member's own new one."""
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)

    @model_validator(mode="after")
    def validate_password_strength(self):
        problem = password_problem(self.new_password, self.email)
        if problem:
            raise ValueError(problem)
        return self


class StaffDirectoryEntry(BaseModel):
    """Staff names for list filters. No email or account details."""
    id: int
    full_name: str
    role: str
    is_active: bool


class StaffOut(BaseModel):
    id: int
    full_name: str
    email: str
    role: str
    is_active: bool
    is_email_verified: bool

    class Config:
        from_attributes = True


class VerifyEmailRequest(BaseModel):
    # Same limits as the client door (DoorVerify).
    email: str = Field(max_length=254)
    code: str = Field(min_length=6, max_length=6)


class ResendVerificationRequest(BaseModel):
    email: str = Field(max_length=254)
    
_ESSU_EMAIL_RE = re.compile(r"^[^\s@]+@essu\.edu\.ph$", re.IGNORECASE)


class StaffCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    role: Literal["legal_assistant", "attorney", "head_attorney"]

    @model_validator(mode="after")
    def name_not_blank(self):
        self.full_name = self.full_name.strip()  # spaces only = no name
        if not self.full_name:
            raise ValueError("Enter the staff member's full name.")
        return self

    @model_validator(mode="after")
    def normalize_and_validate_email(self):
        email = self.email.strip().lower()
        if not _ESSU_EMAIL_RE.match(email):
            raise ValueError("Staff accounts must use a valid @essu.edu.ph email address.")
        self.email = email
        return self

    @model_validator(mode="after")
    def validate_password_strength(self):
        problem = password_problem(self.password, self.email, self.full_name)
        if problem:
            raise ValueError(problem)
        return self


class StaffUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    role: Optional[Literal["legal_assistant", "attorney", "head_attorney"]] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def name_not_blank(self):
        if self.full_name is not None:
            self.full_name = self.full_name.strip()  # spaces only = no name
            if not self.full_name:
                raise ValueError("Enter the staff member's full name.")
        return self


class StaffPasswordSet(BaseModel):
    """Head Attorney sets a temporary password for a staff member who
    forgot theirs (POST /staff/{id}/password). Same rule as a new account."""
    password: str = Field(max_length=256)

    @model_validator(mode="after")
    def validate_password_strength(self):
        problem = password_problem(self.password)  # the email/name check is in routers/staff.py
        if problem:
            raise ValueError(problem)
        return self

class DocumentStatusUpdate(SQLModel):
    status: str
    # What the client must correct; required when asking for a revision.
    note: Optional[str] = Field(default=None, max_length=1000)

class StaffCreateOut(StaffOut):
    dev_verification_code: Optional[str] = None  # DEV ONLY — remove once real email sending exists

    class Config:
        from_attributes = True
        
class MessageAttachmentOut(BaseModel):
    """A file sent with a chat message (a Document row with message_id).
    Downloaded through the usual document download endpoints."""
    id: int
    original_filename: str
    mime_type: str | None = None
    file_size_bytes: int | None = None
    # Staff list only: true when the client can't see this file (staff
    # withdrew it in the Documents tab). Such files never reach the client.
    hidden_from_client: bool = False


class MessageOut(SQLModel):
    id: int
    sender_type: str
    sender_name: str | None = None
    body: str                     # "" when the message is only files
    sent_at: datetime
    attachments: list[MessageAttachmentOut] = []


class BootstrapHeadAttorneyCreate(BaseModel):
    """
    Used ONLY by bootstrap_head_attorney.py, for the very first account.
    Deliberately skips the @essu.edu.ph domain check. Every account
    created afterward, via POST /staff, still goes through StaffCreate,
    which DOES enforce the domain restriction.
    """
    full_name: str = Field(min_length=1, max_length=150)
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)

    @model_validator(mode="after")
    def validate_password_strength(self):
        problem = password_problem(self.password, self.email, self.full_name)
        if problem:
            raise ValueError(problem)
        return self


class ContactCodeSend(BaseModel):
    """The door: send a sign-in code to the email address the client typed
    (email only; checked in routers/public_access.py). altcha = the solved
    "I'm not a robot" puzzle (core/bot_check.py)."""
    contact: str = Field(min_length=3, max_length=254)
    altcha: Optional[str] = Field(default=None, max_length=4000)


HolidayType = Literal["regular", "special", "local", "suspension"]


class HolidayCreate(BaseModel):
    """Head Attorney: add one non-working day."""
    holiday_date: date
    name: str = Field(min_length=1, max_length=150)
    holiday_type: HolidayType

    @model_validator(mode="after")
    def check_values(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Enter the name of the holiday.")
        if not (2020 <= self.holiday_date.year <= 2100):
            raise ValueError("Enter a date between 2020 and 2100.")
        return self


class HolidayOut(BaseModel):
    id: int
    holiday_date: date
    name: str
    holiday_type: str

    class Config:
        from_attributes = True


class HolidaySuggestionOut(BaseModel):
    """One date read from the year's proclamation (core/holiday_import.py).
    Not saved: the Head Attorney ticks it and the page uses POST /holidays."""
    holiday_date: date
    name: str
    holiday_type: str                          # "regular" | "special"
    check_note: Optional[str] = None           # e.g. the PDF's weekday doesn't match the date
    already_listed_as: Optional[str] = None    # name of the Holiday already on that date


class HolidaySuggestionsOut(BaseModel):
    year: int
    title: str                                 # e.g. "Proclamation No. 1427 s. 2026"
    page_url: str                              # the Official Gazette page
    pdf_url: str                               # the signed PDF, to check each date against
    suggestions: list[HolidaySuggestionOut]
    unreadable_lines: list[str]                # looked like holidays but couldn't be read


# ---------- Service Types ----------
class ServiceTypeOut(BaseModel):
    """Public: the services offered (for the Submit Request dropdown) and
    each one's workflow steps, in order."""
    code: str
    name: str
    description: Optional[str] = None
    default_processing_days: Optional[int] = None
    required_documents: list[str] = []   # the active checklist as text lines (Home page, walk-in form)
    steps: list[str] = []
    request_count: Optional[int] = None  # staff view only
    door: str = "service"                 # "service" | "complaint"
    all_steps: list[str] = []             # every step this door can use, in order (Service Types page)
    categories: Optional[list[str]] = None                              # who may use the service
    # Service Types page only:
    requirements: Optional[list["ServiceRequirementAdminOut"]] = None   # checklist, switched-off items too

class ServiceTypeCreate(BaseModel):
    """Head Attorney: add a service. Gets the standard workflow steps."""
    name: str = Field(min_length=1, max_length=150)
    description: Optional[str] = Field(default=None, max_length=1000)
    default_processing_days: Optional[int] = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def tidy(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Enter the name of the service.")
        if self.description is not None:
            self.description = self.description.strip() or None
        return self


class ServiceTypeUpdate(BaseModel):
    """Head Attorney: change a service. Only the fields sent are changed.
    optional_steps lists which OPTIONAL steps are switched on; the required
    steps (Submitted, Processing, Completed) are always kept."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description: Optional[str] = Field(default=None, max_length=1000)
    default_processing_days: Optional[int] = Field(default=None, ge=1, le=365)
    clear_processing_days: bool = False   # True = "no set period"
    optional_steps: Optional[list[str]] = None

    @model_validator(mode="after")
    def tidy(self):
        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValueError("Enter the name of the service.")
        if self.description is not None:
            self.description = self.description.strip()
        return self



# ---------------------------------------------------------------------------
# The door and My Requests
# ---------------------------------------------------------------------------
ClientCategory = Literal["essu_office", "essu_employee", "essu_student", "ext_government", "ext_private"]
ESSU_CLIENT_CATEGORIES = {"essu_office", "essu_employee", "essu_student"}


class DoorVerify(BaseModel):
    """The door: the email address the code was sent to, and the code."""
    contact: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=6)


class ClientProfileIn(BaseModel):
    """A client's own details. ESSU clients pick their office from the list,
    or type it if it isn't listed; external clients give their agency or
    organization (required for government agencies). The name is taken in
    parts, as on government forms; the middle name may be left out only on
    purpose (no_middle_name)."""
    last_name: str = Field(min_length=1, max_length=80)
    first_name: str = Field(min_length=1, max_length=80)
    middle_name: Optional[str] = Field(default=None, max_length=80)
    no_middle_name: bool = False
    name_extension: Optional[str] = Field(default=None, max_length=10)
    client_category: ClientCategory
    office_id: Optional[int] = None
    office_other: Optional[str] = Field(default=None, max_length=200)   # "My office isn't listed"
    organization: Optional[str] = Field(default=None, max_length=200)   # external clients
    position: Optional[str] = Field(default=None, max_length=150)       # students: year and program

    @model_validator(mode="after")
    def tidy_and_check(self):
        clean = lambda v: " ".join((v or "").split()) or None   # trims and collapses inner spaces
        self.last_name, self.first_name = clean(self.last_name), clean(self.first_name)
        self.middle_name, self.name_extension = clean(self.middle_name), clean(self.name_extension)
        self.office_other = (self.office_other or "").strip() or None
        self.organization = (self.organization or "").strip() or None
        self.position = (self.position or "").strip() or None
        if not self.last_name or not self.first_name:
            raise ValueError("Enter your last name and first name.")
        if self.no_middle_name:
            self.middle_name = None
        elif not self.middle_name:
            raise ValueError("Enter your middle name, or tick \"No middle name\".")
        if self.client_category in ESSU_CLIENT_CATEGORIES:
            self.organization = None
            if not self.office_id and not self.office_other:
                raise ValueError("Choose your office or college, or type it if it isn't listed.")
        else:
            self.office_id = None
            self.office_other = None
            if self.client_category == "ext_government" and not self.organization:
                raise ValueError("Enter the name of your government agency.")
        return self


def normalize_ph_phone(number: str) -> str:
    """One way to write a Philippine mobile number: +639171234567,
    639171234567 and 09171234567 all become 09171234567, so one client
    isn't saved twice and the open-request limit sees both. Spaces and
    dashes are dropped; other numbers are kept as typed."""
    number = re.sub(r"[\s-]", "", number or "")
    m = re.fullmatch(r"\+?63(9\d{9})", number)
    return "0" + m.group(1) if m else number


class WalkInClientIn(ClientProfileIn):
    """Staff: the client's details from the paper form, the same fields as
    the client's own profile. Email is the main contact; a contact number
    only when the client has no email."""
    email: Optional[str] = Field(default=None, max_length=254)
    phone: Optional[str] = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def one_contact(self):
        self.email = (self.email or "").strip().lower() or None
        self.phone = normalize_ph_phone(self.phone) or None
        if self.email:
            self.phone = None   # the number is only the fallback
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.email):
                raise ValueError("Enter a valid email address.")
        elif not self.phone:
            raise ValueError("Enter the client's email address, or a contact number if they have no email.")
        elif not re.fullmatch(r"\+?\d{7,15}", self.phone):
            raise ValueError("Enter a valid contact number.")
        return self


class ClientRegistration(ClientProfileIn):
    """First visit through the door. The contact comes from the verified
    code, never from this body."""
    privacy_consent: bool


class OfficeOut(BaseModel):
    id: int
    name: str
    office_type: str
    campus: Optional[str] = None

    class Config:
        from_attributes = True


class ClientRequestSummary(BaseModel):
    tracking_id: str
    service_type_code: Optional[str] = None
    service_type_name: Optional[str] = None
    door: str = "service"
    current_status: Optional[str] = None
    submitted_at: datetime
    is_closed: bool = False


class ClientMeOut(BaseModel):
    """My Requests: who is signed in, whether they may submit, and their
    requests (newest first)."""
    full_name: str
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    name_extension: Optional[str] = None
    contact: str
    client_category: Optional[str] = None
    client_category_label: Optional[str] = None
    office_id: Optional[int] = None
    office_name: Optional[str] = None            # from the list, or what they typed
    organization: Optional[str] = None
    position: Optional[str] = None
    verification_status: str
    needs_profile: bool = False                  # older records with no category yet
    can_edit_profile: bool = True
    can_submit_new: bool = False
    submit_block_reason: Optional[str] = None
    requests: list[ClientRequestSummary] = []
    


# ---------------------------------------------------------------------------
# Submitting through My Requests
# ---------------------------------------------------------------------------
class ServiceRequirementOut(BaseModel):
    id: int
    label: str
    help_text: Optional[str] = None
    is_required: bool

    class Config:
        from_attributes = True


class PortalServiceOut(BaseModel):
    """A service this client may request, with its document checklist."""
    code: str
    name: str
    description: Optional[str] = None
    door: str
    default_processing_days: Optional[int] = None
    requirements: list[ServiceRequirementOut] = []


class PortalServicesOut(BaseModel):
    services: list[PortalServiceOut] = []
    endorsement_required: bool = False   # this client's next request must include the endorsement letter
    endorsement_template: Optional[str] = None   # sent only when the letter is needed
    

class ClientVerify(BaseModel):
    """Staff: verify the client of a request. note = staff only."""
    method: Literal["endorsement_letter", "in_person", "referral"]
    note: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def tidy(self):
        self.note = (self.note or "").strip() or None
        return self


class ClientDecision(BaseModel):
    """Staff: decline or revoke. The reason is kept for staff; it goes in
    the client's email only if tell_client is ticked."""
    reason: str = Field(min_length=1, max_length=500)
    tell_client: bool = False

    @model_validator(mode="after")
    def tidy(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("Please give a reason.")
        return self


class StaffClientOut(BaseModel):
    """The client box on Request Detail (staff only)."""
    id: int
    full_name: str
    formal_name: str
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    name_extension: Optional[str] = None
    client_category: Optional[str] = None
    client_category_label: Optional[str] = None
    office_id: Optional[int] = None
    office_name: Optional[str] = None
    organization: Optional[str] = None
    position: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    verification_status: str
    verification_method: Optional[str] = None
    verified_at: Optional[datetime] = None
    verification_expires_at: Optional[datetime] = None
    verified_by_name: Optional[str] = None
    verification_note: Optional[str] = None
    endorsement_filename: Optional[str] = None
    total_requests: int
    open_requests: int
    needs_profile: bool
    can_verify: bool
    can_decline: bool
    can_revoke: bool
    can_edit: bool


class ClientSearchItem(BaseModel):
    """One match in the All Requests client search."""
    id: int
    formal_name: str
    office_name: Optional[str] = None
    email: Optional[str] = None
    verification_status: str
    total_requests: int
    open_requests: int


class WalkInLookupOut(BaseModel):
    """Walk-in form: the client with exactly this email (or number), so
    staff see the saved details instead of typing them again. The form shows
    saved values read-only; the server never overwrites them anyway."""
    found: bool
    formal_name: Optional[str] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    name_extension: Optional[str] = None
    client_category: Optional[str] = None
    client_category_label: Optional[str] = None
    office_id: Optional[int] = None
    office_name: Optional[str] = None       # the listed office, or the office typed under "Others" (ESSU)
    organization: Optional[str] = None      # outside ESSU
    position: Optional[str] = None
    verification_status: Optional[str] = None   # as staff act on it ("expired" when past its end date)
    details_complete: bool = False          # name parts + category saved
    needs_letter: bool = False              # for a legal service request (never complaints/referrals)
    open_requests: int = 0
    cap_message: Optional[str] = None       # set when the unverified open-request cap is reached



# ---------------------------------------------------------------------------
# Offices page (Head Attorney; routers/offices.py)
# ---------------------------------------------------------------------------

OfficeType = Literal["office", "college", "other"]


def _clean_office_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        raise ValueError("Enter the name of the office or college.")
    return value


class OfficeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    office_type: OfficeType = "office"

    @model_validator(mode="after")
    def tidy(self):
        self.name = _clean_office_name(self.name)
        return self


class OfficeUpdate(BaseModel):
    """Any field left out stays as it is."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    office_type: Optional[OfficeType] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def tidy(self):
        self.name = _clean_office_name(self.name)
        return self


class OfficeAdminOut(BaseModel):
    id: int
    name: str
    office_type: str
    is_active: bool
    client_count: int


class TypedOfficeOut(BaseModel):
    """An office ESSU clients typed under "Others"."""
    name: str
    client_count: int
    matches_office_id: Optional[int] = None      # already on the list under this name
    matches_office_name: Optional[str] = None


class OfficeFromTyped(BaseModel):
    typed_name: str = Field(min_length=1, max_length=200)   # as shown in the "typed" list
    name: str = Field(min_length=1, max_length=200)         # the name to keep (spelling may be corrected)
    office_type: OfficeType = "office"

    @model_validator(mode="after")
    def tidy(self):
        self.name = _clean_office_name(self.name)
        return self


class OfficeFromTypedOut(BaseModel):
    office: OfficeAdminOut
    linked_clients: int
    created: bool
    
    


# ---------------------------------------------------------------------------
# Document checklist and who may use a service (Service Types page)
# ---------------------------------------------------------------------------

class ServiceRequirementAdminOut(BaseModel):
    id: int
    label: str
    help_text: Optional[str] = None
    is_required: bool
    is_active: bool

    class Config:
        from_attributes = True


class RequirementItemIn(BaseModel):
    """One checklist line. id = an existing item (kept, edited, or switched
    back on); no id = a new item."""
    id: Optional[int] = None
    label: str = Field(min_length=1, max_length=200)
    help_text: Optional[str] = Field(default=None, max_length=500)
    is_required: bool = True

    @model_validator(mode="after")
    def tidy(self):
        self.label = " ".join(self.label.split())
        if not self.label:
            raise ValueError("Every document needs a name.")
        self.help_text = " ".join((self.help_text or "").split()) or None
        return self


class RequirementListIn(BaseModel):
    """The whole checklist, in order. Items left out are switched off."""
    items: list[RequirementItemIn] = Field(default_factory=list, max_length=20)


class CategoryListIn(BaseModel):
    categories: list[ClientCategory] = Field(default_factory=list, max_length=10)


ServiceTypeOut.model_rebuild()
StaffEncodedRequestCreate.model_rebuild()



# ---------------------------------------------------------------------------
# Settings page (Head Attorney; routers/system_settings.py)
# ---------------------------------------------------------------------------

class SettingOut(BaseModel):
    key: str
    label: str
    help: str
    kind: str                       # "int" | "text"
    value: str
    default: str
    min: int
    max: int                        # for "text": the longest allowed
    updated_at: Optional[datetime] = None
    updated_by_name: Optional[str] = None


class SettingUpdate(BaseModel):
    value: str = Field(max_length=6000)   # checked against each setting's own limit in the router


# ---------------------------------------------------------------------------
# Task (c): articles on the public Home page (routers/articles.py)
# ---------------------------------------------------------------------------

def _clean_article_title(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        raise ValueError("Enter a title.")
    return value


def _clean_article_body(value: str | None) -> str | None:
    # Windows line endings unified, trailing spaces removed (same as the
    # Settings page's text). Blank lines stay: they separate paragraphs.
    if value is None:
        return None
    value = "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").split("\n")).strip()
    if not value:
        raise ValueError("Write the article text.")
    return value


class ArticleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def tidy(self):
        self.title = _clean_article_title(self.title)
        self.body = _clean_article_body(self.body)
        return self


class ArticleUpdate(BaseModel):
    """Any field left out stays as it is."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    body: Optional[str] = Field(default=None, min_length=1, max_length=20000)

    @model_validator(mode="after")
    def tidy(self):
        self.title = _clean_article_title(self.title)
        self.body = _clean_article_body(self.body)
        return self


class ArticleListItem(BaseModel):
    """One row of the staff Articles list (no body)."""
    id: int
    title: str
    status: str                         # draft | published | hidden
    author_name: str
    created_at: datetime
    updated_at: datetime
    updated_by_name: Optional[str] = None
    published_at: Optional[datetime] = None
    published_by_name: Optional[str] = None
    # What this staff member may do; the server checks again on every action.
    can_edit: bool
    can_publish: bool
    can_hide: bool


class ArticleOut(ArticleListItem):
    body: str


class PublicArticleListItem(BaseModel):
    """A published article on the public site. No staff names or ids."""
    id: int
    title: str
    published_at: datetime
    excerpt: str                        # plain text, marks removed


class PublicArticleSearchItem(BaseModel):
    """A published article's words, for the site search. No staff names or ids."""
    id: int
    title: str
    published_at: datetime
    text: str                           # plain text, marks removed


class PublicArticleOut(BaseModel):
    id: int
    title: str
    published_at: datetime
    body: str                           # turned into HTML by js/article-format.js (escaped first)
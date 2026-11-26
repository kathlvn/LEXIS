import mimetypes
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional
from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status,
)
from pydantic import ValidationError
from sqlalchemy import case, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session
from sqlmodel import Session, func, select
from app.core import storage
from app.core.database import get_session
from app.core.deps import get_current_staff_user, require_head_attorney
from app.core.notifications import attorney_ids, head_attorney_ids, notify, officers_of
from app.core.requester_access import find_requester_by_contact
from app.core.security_monitoring import limiter
from app.core.settings_store import get_int_setting
from app.core.verification import (
    closure_update_line, note_update_line, reminder_update_lines, send_request_update,
    send_submission_confirmation,
)
from app.core.working_days import (
    PH_TZ, compute_due_date, deadline_status, load_holiday_dates, next_working_day, to_ph_date,
)
from app.models.models import (
    Requester, ServiceType, WorkflowStepTemplate, LegalServiceRequest,
    Document, StaffUser, AuditLogEntry, effective_verification_status,
    CLIENT_CATEGORIES, Office, ServiceCategoryAccess,
)
from app.schemas.schemas import (
    LegalServiceRequestOut, LegalServiceRequestListItem, DashboardTileCounts,
    StatusUpdate, DocumentOut, AuditLogEntryOut, ReassignRequest, CloseRequest, ReopenRequest,
    StaffEncodedRequestCreate, ESSU_CLIENT_CATEGORIES, normalize_ph_phone,
    ReturnForRevision, PublicDocumentOut, PublicHistoryEntry, PublicRequestOut,
)

router = APIRouter(prefix="/requests", tags=["requests"])

# Review and approval rules:
# - only an attorney can approve or complete a request, and
# - once work is forwarded for attorney review, only an attorney can move it
#   (approve, complete, or return it), so a legal assistant can't undo an
#   approval or pull work back mid-review.
ATTORNEY_ROLES = ("attorney", "head_attorney")
ATTORNEY_ONLY_STATUSES = {"Approved", "Completed"}
ATTORNEY_LOCKED_STATUSES = {"For Attorney Review", "Approved", "Completed"}
RETURN_TARGET_STATUS = "Processing"


def generate_tracking_id(session: Session) -> str:
    """LEO-YYYY-###### — six digits (1,000,000 values), generated with
    secrets rather than random since this ID doubles as a lookup key on
    the public tracking endpoint. Regenerates on the rare collision
    instead of trusting luck."""
    # The Philippine year, so a request filed after midnight on 1 January
    # (still 31 December in UTC until 8 a.m.) gets the new year (DATA-03).
    year = to_ph_date(datetime.utcnow()).year
    for _ in range(20):
        candidate = f"LEO-{year}-{secrets.randbelow(1_000_000):06d}"
        exists = session.exec(
            select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == candidate)
        ).first()
        if not exists:
            return candidate
    raise HTTPException(status_code=500, detail="Could not generate a unique tracking ID.")


def find_requester_by_phone(session: Session, phone: str) -> Requester | None:
    """The client with this number, ignoring spaces and dashes in the saved
    number. The oldest record wins, as with email. A mobile number matches
    in any of its forms (09..., +639..., 639...), since older records may be
    saved either way."""
    saved = func.replace(func.replace(Requester.phone, " ", ""), "-", "")
    phone = normalize_ph_phone(phone)
    forms = [phone]
    if re.fullmatch(r"09\d{9}", phone):
        forms += ["+63" + phone[1:], "63" + phone[1:]]
    return session.exec(select(Requester).where(saved.in_(forms)).order_by(Requester.id)).first()


def find_or_create_requester(session: Session, data) -> Requester:
    """
    Walk-ins: the client with this email (or, without one, this number), or
    a new client. An existing client's saved details are NEVER overwritten
    with what staff typed; only blank fields on older records are filled.
    Corrections go through the client panel on
    Request Detail, which has its own rules on who may edit.
    """
    # Imported here: portal.py imports this module, so a top-level import would be circular.
    from app.routers.portal import _apply_profile

    existing = (find_requester_by_contact(session, "email", data.email) if data.email
                else find_requester_by_phone(session, data.phone))
    if not existing:
        requester = Requester(
            full_name="",            # filled in from the name parts by _apply_profile
            email=data.email,
            phone=data.phone,
            privacy_consent_given_at=datetime.utcnow(),   # the signed paper form
        )
        _apply_profile(session, requester, data)
        session.add(requester)
        try:
            session.commit()
            session.refresh(requester)
            return requester
        except IntegrityError:
            # Another staff member saved the same new client at the same
            # moment; the database allows one client per email, so
            # carry on with theirs, never overwriting it.
            session.rollback()
            existing = find_requester_by_contact(session, "email", data.email)

    if not (existing.last_name and existing.first_name) or not existing.client_category:
        # An older record (before name parts / categories): fill only what is missing.
        blank = Requester()
        _apply_profile(session, blank, data)
        if not (existing.last_name and existing.first_name):
            existing.last_name, existing.first_name = blank.last_name, blank.first_name
            existing.middle_name, existing.name_extension = blank.middle_name, blank.name_extension
            existing.full_name = blank.full_name
        if not existing.client_category:
            # The old free-text office/agency has no category, so the new one replaces it.
            existing.client_category = blank.client_category
            existing.office_id = blank.office_id
            existing.company_agency = blank.company_agency
        if not existing.position:
            existing.position = blank.position
    if not existing.privacy_consent_given_at:
        existing.privacy_consent_given_at = datetime.utcnow()
    session.add(existing)
    session.commit()
    session.refresh(existing)
    return existing


def is_closed_request(session: Session, req: LegalServiceRequest) -> bool:
    """Completed, cancelled, or rejected. "Approved" still counts as ongoing."""
    return req.is_cancelled or req.is_rejected or _current_status_name(session, req) == "Completed"


def _pick_next_legal_assistant(session: Session) -> StaffUser | None:
    """
    Round-robin: assigns each new request to whichever active legal
    assistant has gone the longest without receiving one (or has never
    received one at all). Recomputed from existing assignments every
    time rather than kept as a separate rotation-position counter, so
    there's nothing to drift out of sync — deactivating and later
    reactivating someone just puts them back at "never assigned."
    """
    # Only assistants who can sign in and work on it: active, email verified,
    # and not still waiting to choose their own password.
    assistants = session.exec(
        select(StaffUser).where(
            StaffUser.role == "legal_assistant",
            StaffUser.is_active == True,  # noqa: E712
            StaffUser.is_email_verified == True,  # noqa: E712
            StaffUser.must_change_password == False,  # noqa: E712
        )
    ).all()
    if not assistants:
        return None

    last_assigned_at: dict[int, datetime | None] = {}
    for a in assistants:
        most_recent = session.exec(
            select(LegalServiceRequest.submitted_at)
            .where(LegalServiceRequest.assigned_officer_id == a.id)
            .order_by(LegalServiceRequest.submitted_at.desc())
        ).first()
        last_assigned_at[a.id] = most_recent

    assistants.sort(key=lambda a: (last_assigned_at[a.id] is not None, last_assigned_at[a.id] or datetime.min))
    return assistants[0]


def _current_status_name(session: Session, req: LegalServiceRequest) -> str | None:
    if not req.current_step_id:
        return None
    step = session.get(WorkflowStepTemplate, req.current_step_id)
    return step.status_name if step else None


def _workflow_step_names(session: Session, service_type_id: int) -> list[str]:
    """The statuses this service's requests move through, in order. The
    Processing tab, Update Progress, and Track Request follow this list, so a
    step the Head Attorney switched off is skipped everywhere."""
    return [
        s.status_name for s in session.exec(
            select(WorkflowStepTemplate)
            .where(WorkflowStepTemplate.service_type_id == service_type_id)
            .order_by(WorkflowStepTemplate.sequence_order)
        ).all()
    ]


def _is_finished(req: LegalServiceRequest, status_name: str | None) -> bool:
    """Finished requests are never flagged as approaching or overdue."""
    return req.is_cancelled or req.is_rejected or status_name == "Completed"


# ---- Cancel / reject / withdraw ----------
# - Attorney and Head Attorney may cancel or reject; a reason is required and
#   is emailed to the requester and shown on Track Request.
# - The requester may withdraw their own request until it is Approved.
# - A closed (cancelled/rejected) request can't be moved or reassigned, and the
#   requester can't add documents or messages; an attorney can reopen it.
CLOSED_REQUEST_DETAIL = "This request has been closed (cancelled or rejected). An attorney must reopen it first."
WITHDRAW_BLOCKED_STATUSES = {"Approved", "Completed"}
CLIENT_CLOSED_DETAIL = ("This request has been closed, so it no longer accepts documents or messages. "
                        "If you still need help, please submit a new request.")


def ensure_open_for_requester(req: LegalServiceRequest) -> None:
    if req.is_cancelled or req.is_rejected:
        raise HTTPException(status_code=409, detail=CLIENT_CLOSED_DETAIL)


def is_withdrawn(req: LegalServiceRequest) -> bool:
    return req.is_cancelled and req.cancelled_by_staff_id is None


def lock_request(req: LegalServiceRequest) -> None:
    """Reloads the request with a row lock held until this save commits, so
    two opposite actions sent at once (cancel and a status change, withdraw
    and approve) run one after the other and the second sees the first
    (PostgreSQL; SQLite ignores the lock)."""
    session = object_session(req)
    if session is not None:
        session.refresh(req, with_for_update=True)


def ensure_not_closed(req: LegalServiceRequest) -> None:
    """Called by every change to an open request, before it changes
    anything: locks the request, then refuses a closed one."""
    lock_request(req)
    if req.is_cancelled or req.is_rejected:
        raise HTTPException(status_code=409, detail=CLOSED_REQUEST_DETAIL)


def _closed_by_name(session: Session, req: LegalServiceRequest) -> str | None:
    if not (req.is_cancelled or req.is_rejected):
        return None
    if is_withdrawn(req):
        return "Requester"
    closer = session.get(StaffUser, req.cancelled_by_staff_id)
    return closer.full_name if closer else None


def can_withdraw(session: Session, req: LegalServiceRequest) -> bool:
    if req.is_cancelled or req.is_rejected:
        return False
    return (_current_status_name(session, req) or "Submitted") not in WITHDRAW_BLOCKED_STATUSES


def _client_office(session: Session, req: LegalServiceRequest) -> str | None:
    """The client's office or agency as shown on a request: the snapshot
    taken at submission (a request keeps the client's details from when it
    was filed), else the client's listed office (requests filed before
    snapshots existed), else the office/agency they typed."""
    if req.client_office_at_submission:
        return req.client_office_at_submission
    client = req.requester
    office = session.get(Office, client.office_id) if client.office_id else None
    return office.name if office else client.company_agency


def _client_name(req: LegalServiceRequest) -> str:
    """The client's name as filed (snapshot), else their current name
    (requests filed before snapshots existed)."""
    return req.client_name_at_submission or req.requester.full_name


def _to_out(session: Session, req: LegalServiceRequest) -> LegalServiceRequestOut:
    service_type = session.get(ServiceType, req.service_type_id)
    status_name = _current_status_name(session, req)
    officer = session.get(StaffUser, req.assigned_officer_id) if req.assigned_officer_id else None
    docs = session.exec(select(Document).where(Document.request_id == req.id)).all()

    audit_entries = session.exec(
        select(AuditLogEntry)
        .where(AuditLogEntry.request_id == req.id)
        .order_by(AuditLogEntry.created_at.desc())
    ).all()
    audit_out = []
    for entry in audit_entries:
        actor = session.get(StaffUser, entry.staff_id) if entry.staff_id else None
        if actor:
            name = actor.full_name
        elif entry.actor_type == "requester":
            name = "Requester"
        elif entry.actor_type == "system":
            name = "System"
        else:
            name = "Unknown"
        audit_out.append(AuditLogEntryOut(
            action=entry.action,
            detail=entry.detail,
            staff_name=name,
            actor_type=entry.actor_type or "staff",
            created_at=entry.created_at,
        ))
        
    return LegalServiceRequestOut(
        tracking_id=req.tracking_id,
        service_type_code=service_type.code if service_type else "",
        service_type_name=service_type.name if service_type else None,
        workflow_steps=_workflow_step_names(session, req.service_type_id),
        requester_full_name=_client_name(req),
        requester_company_agency=_client_office(session, req),
        requester_email=req.requester.email,
        requester_phone=req.requester.phone,
        request_type=req.request_type,
        purpose=req.purpose,
        description=req.description,
        current_status=_list_status_name(req, status_name),
        workflow_status=status_name,
        assigned_officer_name=officer.full_name if officer else None,
        assigned_officer_id=req.assigned_officer_id,
        submitted_at=req.submitted_at,
        due_date=req.due_date,
        deadline_status=deadline_status(
            req.due_date, _is_finished(req, status_name), load_holiday_dates(session),
        ),
        last_updated_at=req.last_updated_at,
        is_cancelled=req.is_cancelled,
        is_rejected=req.is_rejected,
        closed_at=req.closed_at,
        closure_reason=req.closure_reason,
        closed_by_name=_closed_by_name(session, req),
        reassignment_requested=req.reassignment_requested,
        reassignment_reason=req.reassignment_reason,
        is_waiting_for_client=req.is_waiting_for_client,
        staff_note_to_client=req.staff_note_to_client,
        documents=[DocumentOut.model_validate(d) for d in docs],
        audit_log=audit_out,
    )


# ---------------------------------------------------------------------------
# Requester-facing output. Everything the public side receives goes through
# _to_public_out() — never _to_out(), which is for staff only.
# ---------------------------------------------------------------------------

def _is_visible_to_requester(doc: Document) -> bool:
    """The requester always sees what they uploaded themselves; a Legal
    Office document only once staff have released it."""
    return doc.category == "client_submitted" or doc.is_released_to_requester


def _public_history_text(entry: AuditLogEntry) -> str | None:
    """Turns one audit entry into a line the requester may see, or None to
    hide it. This is an allow-list: any action not named here (assignment,
    reassignment, internal uploads, notes, and anything added in the future)
    stays hidden by default. The texts for these actions never contain staff
    names — see where each one is logged."""
    if entry.action == "status_change":
        return entry.detail
    if entry.action == "client_document_uploaded":
        return entry.detail
    if entry.action in ("document_status_changed", "document_released", "document_withdrawn"):
        return entry.detail
    if entry.action in ("request_cancelled", "request_rejected", "request_withdrawn"):
        return entry.detail          # logged without staff names (see close_request)
    if entry.action == "reminder_sent":
        return "The Legal Office sent you a reminder."
    if entry.action == "request_reopened":
        return "The Legal Office reopened this request."    # the reopen reason stays internal
    if entry.action == "waiting_for_client_change":
        if entry.detail.startswith("Marked"):
            return "The Legal Office is waiting for a response or document from you."
        return "The Legal Office is no longer waiting for a response from you."
    return None


def _to_public_out(session: Session, req: LegalServiceRequest) -> PublicRequestOut:
    service_type = session.get(ServiceType, req.service_type_id)
    officer = session.get(StaffUser, req.assigned_officer_id) if req.assigned_officer_id else None

    docs = session.exec(select(Document).where(Document.request_id == req.id)).all()
    visible_docs = [d for d in docs if _is_visible_to_requester(d)]

    audit_entries = session.exec(
        select(AuditLogEntry)
        .where(AuditLogEntry.request_id == req.id)
        .order_by(AuditLogEntry.created_at.desc())
    ).all()
    history = []
    for entry in audit_entries:
        text = _public_history_text(entry)
        if text:
            history.append(PublicHistoryEntry(description=text, created_at=entry.created_at))
    # "Submitted" isn't an audit entry, so it's added as the oldest line.
    history.append(PublicHistoryEntry(description="Request submitted.", created_at=req.submitted_at))
    # Newest first, so this is the last status change; none yet = still as submitted.
    status_since = next(
        (e.created_at for e in audit_entries if e.action == "status_change"),
        req.submitted_at,
    )

    return PublicRequestOut(
        tracking_id=req.tracking_id,
        service_type_code=service_type.code if service_type else "",
        service_type_name=service_type.name if service_type else None,
        workflow_steps=_workflow_step_names(session, req.service_type_id),
        requester_full_name=_client_name(req),
        requester_company_agency=_client_office(session, req),
        requester_email=req.requester.email,
        requester_phone=req.requester.phone,
        request_type=req.request_type,
        purpose=req.purpose,
        description=req.description,
        current_status=_current_status_name(session, req),
        current_status_since=status_since,
        processing_days=service_type.default_processing_days if service_type else None,
        assigned_officer_name=officer.full_name if officer else None,
        submitted_at=req.submitted_at,
        due_date=req.due_date,
        last_updated_at=req.last_updated_at,
        is_waiting_for_client=req.is_waiting_for_client,
        staff_note_to_client=req.staff_note_to_client,
        is_cancelled=req.is_cancelled,
        is_rejected=req.is_rejected,
        is_withdrawn=is_withdrawn(req),
        can_withdraw=can_withdraw(session, req),
        closed_at=req.closed_at,
        closure_reason=req.closure_reason,
        documents=[PublicDocumentOut.model_validate(d) for d in visible_docs],
        history=history,
    )


# The public POST /requests (old Submit Request page) was retired.
# Clients submit through My Requests (POST /portal/requests),
# which checks the document checklist, the endorsement letter, the client's
# verification, and the submission limits. Staff encode walk-ins with
# POST /requests/staff-encoded below.


CLOSED_FLAG_STATUSES = {"Cancelled": "is_cancelled", "Rejected": "is_rejected"}
LIST_SORTS = {
    "newest": (LegalServiceRequest.submitted_at.desc(),),
    "oldest": (LegalServiceRequest.submitted_at.asc(),),
    # Earliest due first; requests with no set period go last.
    "due": (LegalServiceRequest.due_date.is_(None), LegalServiceRequest.due_date.asc(),
            LegalServiceRequest.submitted_at.asc()),
}


def _list_status_name(req: LegalServiceRequest, step_name: str | None) -> str | None:
    """A cancelled or rejected request shows that, not the step it stopped at."""
    if req.is_cancelled:
        return "Cancelled"
    if req.is_rejected:
        return "Rejected"
    return step_name


def _ph_day_start_utc(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=PH_TZ).astimezone(timezone.utc).replace(tzinfo=None)


@router.get("", response_model=list[LegalServiceRequestListItem])
@limiter.limit("120/minute")
def list_requests(
    request: Request,
    response: Response,
    q: str | None = Query(None, max_length=100),
    status_name: str | None = Query(None, max_length=50),
    service_type_code: str | None = Query(None, max_length=50),
    assigned_officer_id: int | None = Query(None, ge=0),   # 0 = unassigned
    submitted_from: date | None = None,                   # Philippine dates, inclusive
    submitted_to: date | None = None,
    deadline: Literal["overdue", "due_soon"] | None = None,
    open_only: bool = False,
    assigned_to_me: bool = False,
    waiting_for_client: bool = False,
    reassignment_requested: bool = False,
    requester_id: int | None = Query(None, ge=1),            # one client's requests (client filter)
    client_waiting: bool = False,                            # client waiting for verification
    sort: Literal["newest", "oldest", "due"] = "newest",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """
    Staff-only request listing: search, filters, sorting, and pages.

    - q searches the Request ID, the requester's name, email, and office.
    - status_name "Cancelled"/"Rejected" match those requests; any other
      status matches requests at that step that are NOT cancelled/rejected.
    - deadline "overdue" = past due; "due_soon" = overdue, due today, or due
      the next working day (same rule as the red flags in the lists).
    - open_only leaves out completed, cancelled, and rejected requests.
    The total number of matches is sent in the X-Total-Count header, so the
    console can show pages; limit is capped at 200 per page.
    """
    stmt = _filtered_requests(
        session, current_staff, q=q, status_name=status_name, service_type_code=service_type_code,
        assigned_officer_id=assigned_officer_id, submitted_from=submitted_from, submitted_to=submitted_to,
        deadline=deadline, open_only=open_only, assigned_to_me=assigned_to_me,
        waiting_for_client=waiting_for_client, reassignment_requested=reassignment_requested,
        requester_id=requester_id, client_waiting=client_waiting,
    )
    total = _count(session, stmt)
    response.headers["X-Total-Count"] = str(total)

    stmt = stmt.order_by(*LIST_SORTS[sort], LegalServiceRequest.id.desc()).offset(offset).limit(limit)

    holidays = load_holiday_dates(session)  # once for the whole list
    items = []
    for r in session.exec(stmt).all():
        service_type = session.get(ServiceType, r.service_type_id)
        step_name = _current_status_name(session, r)
        officer = session.get(StaffUser, r.assigned_officer_id) if r.assigned_officer_id else None
        prepared_by = session.get(StaffUser, r.prepared_by_officer_id) if r.prepared_by_officer_id else None
        items.append(LegalServiceRequestListItem(
            tracking_id=r.tracking_id,
            service_type_name=service_type.name if service_type else "",
            requester_id=r.requester_id,
            requester_full_name=_client_name(r),
            requester_company_agency=_client_office(session, r),
            requester_has_email=bool(r.requester.email),
            # The number only for clients with no email: the one case staff need it (a call instead of a reminder).
            requester_phone=None if r.requester.email else r.requester.phone,
            client_verification_status=effective_verification_status(r.requester),
            current_status=_list_status_name(r, step_name),
            submitted_at=r.submitted_at,
            due_date=r.due_date,
            deadline_status=deadline_status(r.due_date, _is_finished(r, step_name), holidays),
            assigned_officer_name=officer.full_name if officer else None,
            prepared_by_officer_name=prepared_by.full_name if prepared_by else None,
            is_assigned_to_you=(r.assigned_officer_id == current_staff.id),
            is_prepared_by_you=(r.prepared_by_officer_id == current_staff.id),
            was_returned_for_revision=r.was_returned_for_revision,
            staff_note_to_client=r.staff_note_to_client,
            is_waiting_for_client=r.is_waiting_for_client,
            reassignment_requested=r.reassignment_requested,
            reassignment_reason=r.reassignment_reason,
        ))
    return items


# Dashboard tiles: all of the caller's counts in one call
# instead of one GET /requests per tile. Each tile uses the same filters as
# the queue it opens (the links are in internal/js/dashboard.js);
# tests/test_dashboard_summary.py checks every count against the list.
# Each tile also says how its requests split by deadline (the bar on the
# redesigned tile), with the same rules as the list's deadline filter.
# Declared before /{tracking_id}, so "summary" is never read as a Request ID.
@router.get("/summary", response_model=dict[str, DashboardTileCounts])
@limiter.limit("60/minute")
def dashboard_summary(
    request: Request,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    me = current_staff.id
    tiles = {"new": dict(status_name="Submitted")}
    if current_staff.role == "legal_assistant":
        tiles["assigned_to_me"] = dict(assigned_to_me=True, open_only=True)
        tiles["my_awaiting_client"] = dict(waiting_for_client=True, assigned_officer_id=me)
        tiles["my_due_soon"] = dict(deadline="due_soon", assigned_officer_id=me)
    else:
        tiles["for_approval"] = dict(status_name="For Attorney Review")
        tiles["awaiting_client"] = dict(waiting_for_client=True)
        tiles["due_soon"] = dict(deadline="due_soon")
        if current_staff.role == "head_attorney":
            tiles["reassignment"] = dict(reassignment_requested=True)
        tiles["client_waiting"] = dict(client_waiting=True)
    now = datetime.utcnow()
    soon_limit = _due_soon_limit(session, now)
    return {key: _deadline_split(session, _filtered_requests(session, current_staff, **filters), now, soon_limit)
            for key, filters in tiles.items()}


def _count(session: Session, stmt) -> int:
    return session.exec(select(func.count()).select_from(stmt.subquery())).one()


def _due_soon_limit(session: Session, now: datetime) -> datetime:
    """Due before this (UTC) = due today or the next working day, or overdue:
    the start of the day after the next working day, Philippine time."""
    holidays = load_holiday_dates(session)
    return _ph_day_start_utc(next_working_day(to_ph_date(now), holidays) + timedelta(days=1))


def _deadline_split(session: Session, stmt, now: datetime, soon_limit: datetime) -> dict[str, int]:
    """Count the requests of a filtered SELECT, split by deadline in one query.
    Finished requests (completed, cancelled, rejected) are never overdue;
    they are counted as closed."""
    req, step = LegalServiceRequest, WorkflowStepTemplate
    ids = stmt.subquery()
    finished = or_(req.is_cancelled == True, req.is_rejected == True,  # noqa: E712
                   func.coalesce(step.status_name, "") == "Completed")
    open_ = ~finished
    due = req.due_date

    def n(condition):
        return func.count(case((condition, 1)))

    total, closed, no_period, overdue, due_soon = session.exec(
        select(
            func.count(),
            n(finished),
            n(open_ & due.is_(None)),
            n(open_ & (due < now)),
            n(open_ & (due >= now) & (due < soon_limit)),
        )
        .select_from(req)
        .outerjoin(step, req.current_step_id == step.id)
        .where(req.id.in_(select(ids.c.id)))
    ).one()
    return {
        "count": total, "overdue": overdue, "due_soon": due_soon,
        "on_track": total - closed - no_period - overdue - due_soon,
        "no_period": no_period, "closed": closed,
    }


def _filtered_requests(
    session: Session,
    current_staff: StaffUser,
    *,
    q: str | None = None,
    status_name: str | None = None,
    service_type_code: str | None = None,
    assigned_officer_id: int | None = None,
    submitted_from: date | None = None,
    submitted_to: date | None = None,
    deadline: str | None = None,
    open_only: bool = False,
    assigned_to_me: bool = False,
    waiting_for_client: bool = False,
    reassignment_requested: bool = False,
    requester_id: int | None = None,
    client_waiting: bool = False,
):
    """The SELECT behind GET /requests: search and filters, no order or page.
    The dashboard counts use it too, so a tile and its queue always match."""
    stmt = select(LegalServiceRequest)
    step = None
    needs_step = bool(status_name and status_name not in CLOSED_FLAG_STATUSES) or open_only or deadline
    if needs_step:
        stmt = stmt.outerjoin(WorkflowStepTemplate, LegalServiceRequest.current_step_id == WorkflowStepTemplate.id)
        step = WorkflowStepTemplate

    if q and q.strip():
        term = q.strip()
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.join(Requester, LegalServiceRequest.requester_id == Requester.id).where(
            LegalServiceRequest.tracking_id.ilike(like, escape="\\")
            | Requester.full_name.ilike(like, escape="\\")
            | Requester.email.ilike(like, escape="\\")
            | Requester.company_agency.ilike(like, escape="\\")
            | LegalServiceRequest.client_name_at_submission.ilike(like, escape="\\")     # what the list shows
            | LegalServiceRequest.client_office_at_submission.ilike(like, escape="\\")
        )

    if status_name:
        flag = CLOSED_FLAG_STATUSES.get(status_name)
        if flag:
            stmt = stmt.where(getattr(LegalServiceRequest, flag) == True)  # noqa: E712
        else:
            stmt = stmt.where(
                step.status_name == status_name,
                LegalServiceRequest.is_cancelled == False,  # noqa: E712
                LegalServiceRequest.is_rejected == False,  # noqa: E712
            )

    if open_only or deadline:
        stmt = stmt.where(
            LegalServiceRequest.is_cancelled == False,  # noqa: E712
            LegalServiceRequest.is_rejected == False,  # noqa: E712
            (step.status_name.is_(None)) | (step.status_name != "Completed"),
        )

    if deadline:
        now = datetime.utcnow()
        if deadline == "overdue":
            stmt = stmt.where(LegalServiceRequest.due_date < now)
        else:
            stmt = stmt.where(LegalServiceRequest.due_date < _due_soon_limit(session, now))

    if service_type_code:
        stmt = stmt.join(ServiceType, LegalServiceRequest.service_type_id == ServiceType.id).where(
            ServiceType.code == service_type_code)

    if assigned_officer_id is not None:
        stmt = stmt.where(
            LegalServiceRequest.assigned_officer_id.is_(None) if assigned_officer_id == 0
            else LegalServiceRequest.assigned_officer_id == assigned_officer_id
        )

    if submitted_from:
        stmt = stmt.where(LegalServiceRequest.submitted_at >= _ph_day_start_utc(submitted_from))
    if submitted_to:
        stmt = stmt.where(LegalServiceRequest.submitted_at < _ph_day_start_utc(submitted_to + timedelta(days=1)))

    if assigned_to_me:
        stmt = stmt.where(LegalServiceRequest.assigned_officer_id == current_staff.id)

    if waiting_for_client:
        stmt = stmt.where(LegalServiceRequest.is_waiting_for_client == True)  # noqa: E712

    if reassignment_requested:
        stmt = stmt.where(LegalServiceRequest.reassignment_requested == True)  # noqa: E712

    if requester_id is not None:
        stmt = stmt.where(LegalServiceRequest.requester_id == requester_id)

    if client_waiting:
        # Pending letter, not yet verified (details completed), or expired.
        waiting = select(Requester.id).where(
            (Requester.verification_status == "pending")
            | ((Requester.verification_status == "unverified") & Requester.client_category.is_not(None))
            | ((Requester.verification_status == "verified")
               & (Requester.verification_expires_at < datetime.utcnow()))
        )
        stmt = stmt.where(LegalServiceRequest.requester_id.in_(waiting))
    return stmt


@router.get("/{tracking_id}", response_model=LegalServiceRequestOut)
@limiter.limit("120/minute")
def get_request(
    request: Request,
    tracking_id: str,
    session: Session = Depends(get_session),
    staff: StaffUser = Depends(get_current_staff_user),
):
    """
    Used by the internal console's Request Detail page. Requires a valid
    staff session. Requesters open their own requests through My Requests
    (GET /portal/requests/{tracking_id}).
    """
    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    return _to_out(session, req)

@router.patch("/{tracking_id}/status", response_model=LegalServiceRequestOut)
@limiter.limit("30/minute")
def update_status(
    request: Request,
    tracking_id: str,
    payload: StatusUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):

    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")

    if current_staff.role == "legal_assistant" and req.assigned_officer_id != current_staff.id:
        raise HTTPException(
            status_code=403,
            detail="You can only update requests assigned to you.",
        )
    ensure_not_closed(req)

    if (payload.new_status is None and payload.is_waiting_for_client is None
            and payload.note is None and payload.reassignment_reason is None):
        raise HTTPException(status_code=400, detail="Provide new_status, is_waiting_for_client, note, or reassignment_reason.")

    # What the requester is told by email — generic lines only.
    requester_updates: list[str] = []
    
    if payload.new_status is not None:
        step = session.exec(
            select(WorkflowStepTemplate).where(
                WorkflowStepTemplate.service_type_id == req.service_type_id,
                WorkflowStepTemplate.status_name == payload.new_status,
            )
        ).first()
        if not step:
            raise HTTPException(
                status_code=400,
                detail=f"'{payload.new_status}' is not a defined status for this request's service type.",
            )

        old_status = _current_status_name(session, req)
        if current_staff.role not in ATTORNEY_ROLES:
            if payload.new_status in ATTORNEY_ONLY_STATUSES:
                raise HTTPException(
                    status_code=403,
                    detail="Only the Attorney can approve or complete a request.",
                )
            if old_status in ATTORNEY_LOCKED_STATUSES:
                raise HTTPException(
                    status_code=403,
                    detail=f"This request is at \"{old_status}\". Only the Attorney can change its status now.",
                )

        # Only the next step. Going back is done with Return (with a
        # comment) or Reopen. This also stops a page left open from moving a
        # request backwards after someone else moved it on (E2E-01): the step
        # it sends is no longer the next one.
        steps = session.exec(
            select(WorkflowStepTemplate)
            .where(WorkflowStepTemplate.service_type_id == req.service_type_id)
            .order_by(WorkflowStepTemplate.sequence_order)
        ).all()
        step_ids = [s.id for s in steps]
        here = step_ids.index(req.current_step_id) if req.current_step_id in step_ids else 0
        next_name = steps[here + 1].status_name if here + 1 < len(steps) else None
        if payload.new_status != next_name:
            current_name = old_status or steps[here].status_name
            raise HTTPException(
                status_code=409,
                detail=(f"This request is already at \"{current_name}\"." if payload.new_status == current_name
                        else f"This request is at \"{current_name}\", so it can't move to \"{payload.new_status}\".")
                + " Reload the page to see the latest.",
            )

        old_step =session.get(WorkflowStepTemplate, req.current_step_id) if req.current_step_id else None
        if old_step and step.sequence_order < old_step.sequence_order:
            req.was_returned_for_revision = True

        if payload.new_status == "For Attorney Review" and req.prepared_by_officer_id is None:
            req.prepared_by_officer_id = req.assigned_officer_id

        # Changing the status no longer changes who the request is assigned
        # to — that only happens through the Head Attorney's Reassign.
        req.current_step_id = step.id

        session.add(AuditLogEntry(
            request_id=req.id,
            staff_id=current_staff.id,
            action="status_change",
            detail=f"Status changed from {old_status or 'Submitted'} to {payload.new_status}.",
        ))
        if payload.new_status != (old_status or "Submitted"):
            requester_updates.append(f"Status: {payload.new_status}")
            if payload.new_status == "For Attorney Review":
                notify(session, attorney_ids(session), "for_review",
                       f"{req.tracking_id} was forwarded for attorney review by {current_staff.full_name}.",
                       req, current_staff.id)
            elif payload.new_status in ("Approved", "Completed"):
                notify(session, officers_of(req), payload.new_status.lower(),
                       f"{req.tracking_id} was marked {payload.new_status} by {current_staff.full_name}.",
                       req, current_staff.id)

    # A completed request isn't waiting for the client any more, so the flag
    # is cleared and no "still waiting" reminder can go out.
    if payload.new_status == "Completed":
        payload.is_waiting_for_client = False

    # Whether the requester should hear about "what we need from you" in
    # this save: the waiting flag was just turned on, or the note changed
    # while the request is waiting. Worded after the note is applied below.
    now_waiting = (payload.is_waiting_for_client
                   if payload.is_waiting_for_client is not None else req.is_waiting_for_client)
    tell_waiting = bool(now_waiting) and (
        (payload.is_waiting_for_client is True and not req.is_waiting_for_client)
        or (payload.note is not None and (payload.note or None) != req.staff_note_to_client
            and bool(payload.note))
    )

    if payload.is_waiting_for_client is not None and payload.is_waiting_for_client != req.is_waiting_for_client:
        req.is_waiting_for_client = payload.is_waiting_for_client
        session.add(AuditLogEntry(
            request_id=req.id,
            staff_id=current_staff.id,
            action="waiting_for_client_change",
            detail="Marked as waiting for client." if payload.is_waiting_for_client else "Cleared the waiting-for-client flag.",
        ))

    if payload.note is not None:
        req.staff_note_to_client = payload.note or None
        session.add(AuditLogEntry(
            request_id=req.id,
            staff_id=current_staff.id,
            action="client_note_change",
            detail=f'Updated the note to client: "{payload.note}"' if payload.note else "Cleared the note to client.",
        ))

    if payload.reassignment_reason is not None:
        reason = payload.reassignment_reason.strip()
        if not reason:
            raise HTTPException(status_code=400, detail="A reason is required to request reassignment.")
        req.reassignment_requested = True
        req.reassignment_reason = reason
        session.add(AuditLogEntry(
            request_id=req.id,
            staff_id=current_staff.id,
            action="reassignment_requested",
            detail=f'{current_staff.full_name} requested reassignment: "{reason}"',
        ))
        notify(session, head_attorney_ids(session), "reassignment_requested",
               f"{current_staff.full_name} asked for {req.tracking_id} to be reassigned.", req, current_staff.id)
        
    if tell_waiting:
        # The note is written for the requester (the box on Request Detail
        # says it is emailed); without one, a generic line is sent.
        requester_updates.append(
            note_update_line(req.staff_note_to_client) if req.staff_note_to_client
            else "The Legal Office is waiting for a response or document from you."
        )

    req.last_updated_at = datetime.utcnow()
    requester_email = req.requester.email
    session.add(req)
    session.commit()
    session.refresh(req)
    if requester_updates:
        # Sent after the reply goes out, so a slow mail server never slows staff down.
        background_tasks.add_task(send_request_update, requester_email, req.tracking_id, requester_updates)
    return _to_out(session, req)

@router.post("/{tracking_id}/return", response_model=LegalServiceRequestOut)
@limiter.limit("30/minute")
def return_for_revision(
    request: Request,
    tracking_id: str,
    payload: ReturnForRevision,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """
    Attorney-only: sends forwarded work back to
    Processing with a required comment. The comment is logged to the staff
    History only; the requester just sees the status change.
    """
    if current_staff.role not in ATTORNEY_ROLES:
        raise HTTPException(status_code=403, detail="Only the Attorney can return work for revision.")

    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    ensure_not_closed(req)

    old_status = _current_status_name(session, req)
    if old_status not in ("For Attorney Review", "Approved"):
        raise HTTPException(
            status_code=400,
            detail="Only work that is For Attorney Review or Approved can be returned for revision.",
        )

    step = session.exec(
        select(WorkflowStepTemplate).where(
            WorkflowStepTemplate.service_type_id == req.service_type_id,
            WorkflowStepTemplate.status_name == RETURN_TARGET_STATUS,
        )
    ).first()
    if not step:
        raise HTTPException(
            status_code=400,
            detail=f"This service type has no \"{RETURN_TARGET_STATUS}\" step to return the work to.",
        )

    comment = payload.comment.strip()
    if not comment:
        raise HTTPException(status_code=400, detail="A comment is required when returning work.")

    req.current_step_id = step.id
    req.was_returned_for_revision = True
    req.last_updated_at = datetime.utcnow()
    session.add(req)
    # Two entries on purpose: the status change is safe for the requester's
    # History; the comment entry is internal (not in the public allow-list).
    session.add(AuditLogEntry(
        request_id=req.id,
        staff_id=current_staff.id,
        action="status_change",
        detail=f"Status changed from {old_status} to {RETURN_TARGET_STATUS}.",
    ))
    session.add(AuditLogEntry(
        request_id=req.id,
        staff_id=current_staff.id,
        action="returned_for_revision",
        detail=f'Returned for revision: "{comment}"',
    ))
    notify(session, officers_of(req), "returned",
           f"{req.tracking_id} was returned for revision by {current_staff.full_name}.", req, current_staff.id)
    requester_email = req.requester.email
    session.commit()
    session.refresh(req)
    # The requester only learns the new status, never the attorney's comment.
    background_tasks.add_task(
        send_request_update, requester_email, req.tracking_id, [f"Status: {RETURN_TARGET_STATUS}"],
    )
    return _to_out(session, req)


@router.patch("/{tracking_id}/reassign", response_model=LegalServiceRequestOut)
@limiter.limit("30/minute")
def reassign_request(
    request: Request,
    tracking_id: str,
    payload: ReassignRequest,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(require_head_attorney),
):
    """
    Head-Attorney-only. new_officer_id set -> reassigns the request (and
    clears any pending reassignment request as a side effect). Omitted ->
    dismisses a pending reassignment request without reassigning.
    """
    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    ensure_not_closed(req)

    if payload.new_officer_id is None:
        if not req.reassignment_requested:
            raise HTTPException(status_code=400, detail="There is no pending reassignment request to dismiss.")
        req.reassignment_requested = False
        req.reassignment_reason = None
        session.add(AuditLogEntry(
            request_id=req.id,
            staff_id=current_staff.id,
            action="reassignment_dismissed",
            detail=f"{current_staff.full_name} reviewed the reassignment request and kept it as-is.",
        ))
        req.last_updated_at = datetime.utcnow()
        session.add(req)
        session.commit()
        session.refresh(req)
        return _to_out(session, req)

    new_officer = session.get(StaffUser, payload.new_officer_id)
    if not new_officer or not new_officer.is_active:
        raise HTTPException(status_code=400, detail="Selected staff member is not a valid, active account.")
    if not new_officer.is_email_verified or new_officer.must_change_password:
        # They can't sign in yet, so the request would sit untouched.
        raise HTTPException(
            status_code=400,
            detail="This staff member can't sign in yet (email not verified or new password not chosen).",
        )

    old_officer = session.get(StaffUser, req.assigned_officer_id) if req.assigned_officer_id else None
    req.assigned_officer_id = new_officer.id
    req.reassignment_requested = False
    req.reassignment_reason = None
    req.last_updated_at = datetime.utcnow()

    session.add(AuditLogEntry(
        request_id=req.id,
        staff_id=current_staff.id,
        action="reassigned",
        detail=f"Reassigned from {old_officer.full_name if old_officer else 'Unassigned'} to {new_officer.full_name} by {current_staff.full_name}.",
    ))
    notify(session, [new_officer.id], "assigned",
           f"{req.tracking_id} was reassigned to you by {current_staff.full_name}.", req, current_staff.id)
    if old_officer:
        notify(session, [old_officer.id], "unassigned_from",
               f"{req.tracking_id} was reassigned to {new_officer.full_name}.", req, current_staff.id)
    session.add(req)
    session.commit()
    session.refresh(req)
    return _to_out(session, req)


def _get_by_tracking_id(session: Session, tracking_id: str) -> LegalServiceRequest:
    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    return req


@router.post("/{tracking_id}/close", response_model=LegalServiceRequestOut)
@limiter.limit("30/minute")
def close_request(
    request: Request,
    tracking_id: str,
    payload: CloseRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Attorney / Head Attorney: cancel or reject a request, with a reason
    that is emailed to the requester and shown on Track Request."""
    if current_staff.role not in ATTORNEY_ROLES:
        raise HTTPException(status_code=403, detail="Only the Attorney can cancel or reject a request.")
    req = _get_by_tracking_id(session, tracking_id)
    ensure_not_closed(req)
    if _current_status_name(session, req) == "Completed":
        raise HTTPException(status_code=409, detail="A completed request can't be cancelled or rejected.")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Please give the reason. It is sent to the requester.")

    now = datetime.utcnow()
    label = "Rejected" if payload.action == "reject" else "Cancelled"
    req.is_rejected = payload.action == "reject"
    req.is_cancelled = payload.action == "cancel"
    req.cancelled_by_staff_id = current_staff.id
    req.closed_at = now
    req.closure_reason = reason
    req.is_waiting_for_client = False
    req.reassignment_requested = False
    req.reassignment_reason = None
    req.last_updated_at = now
    session.add(req)
    # No staff name in the text: this line is shown to the requester too.
    session.add(AuditLogEntry(
        request_id=req.id,
        staff_id=current_staff.id,
        action=f"request_{label.lower()}",
        detail=f'Request {label.lower()} by the Legal Office. Reason: "{reason}"',
    ))
    notify(session, officers_of(req), label.lower(),
           f"{req.tracking_id} was {label.lower()} by {current_staff.full_name}.", req, current_staff.id)
    requester_email = req.requester.email
    session.commit()
    session.refresh(req)
    background_tasks.add_task(
        send_request_update, requester_email, req.tracking_id,
        [f"Status: {label}", closure_update_line(reason)], is_closed=True,
    )
    return _to_out(session, req)


@router.post("/{tracking_id}/reopen", response_model=LegalServiceRequestOut)
@limiter.limit("30/minute")
def reopen_request(
    request: Request,
    tracking_id: str,
    payload: ReopenRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Attorney / Head Attorney: undo a cancel, reject, or withdrawal (for
    example one made by mistake). The request returns to the step it was
    at; the reason stays in the staff History."""
    if current_staff.role not in ATTORNEY_ROLES:
        raise HTTPException(status_code=403, detail="Only the Attorney can reopen a request.")
    req = _get_by_tracking_id(session, tracking_id)
    lock_request(req)   # one change at a time
    if not (req.is_cancelled or req.is_rejected):
        raise HTTPException(status_code=409, detail="This request is not closed.")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Please give the reason for reopening.")

    was = "withdrawn" if is_withdrawn(req) else ("rejected" if req.is_rejected else "cancelled")
    req.is_cancelled = False
    req.is_rejected = False
    req.cancelled_by_staff_id = None
    req.closed_at = None
    req.closure_reason = None
    req.last_updated_at = datetime.utcnow()
    session.add(req)
    session.add(AuditLogEntry(
        request_id=req.id,
        staff_id=current_staff.id,
        action="request_reopened",
        detail=f'Reopened (was {was}): "{reason}"',
    ))
    notify(session, officers_of(req), "reopened",
           f"{req.tracking_id} was reopened by {current_staff.full_name}.", req, current_staff.id)
    status_name = _current_status_name(session, req) or "Submitted"
    requester_email = req.requester.email
    session.commit()
    session.refresh(req)
    background_tasks.add_task(
        send_request_update, requester_email, req.tracking_id,
        [f"Your request has been reopened. Status: {status_name}"],
    )
    return _to_out(session, req)


def walk_in_needs_letter(category: str | None, verification_status: str) -> bool:
    """Whether a LEGAL SERVICE request (never a complaint/referral) from this
    client needs the endorsement letter: the rule of portal._endorsement_required,
    plus clients the office declined or revoked (who can't submit online at
    all): they bring a new letter."""
    return category in ESSU_CLIENT_CATEGORIES and verification_status in ("unverified", "declined", "revoked")


def walk_in_cap_message(session: Session, client: Requester | None) -> str | None:
    """The cap on open requests while a client isn't verified (the setting
    unverified_max_open_requests, as on My Requests). None = under the cap.
    A new client (None) has no requests yet."""
    # Imported here: portal.py imports this module, so a top-level import would be circular.
    from app.routers.portal import _is_verified
    if client and _is_verified(client):
        return None
    limit = get_int_setting(session, "unverified_max_open_requests", 1)
    mine = (session.exec(select(LegalServiceRequest).where(LegalServiceRequest.requester_id == client.id)).all()
            if client else [])
    open_count = sum(1 for r in mine if not is_closed_request(session, r))
    if open_count < limit:
        return None
    return (f"This client isn't verified yet and already has {open_count} open "
            f"request{'s' if open_count != 1 else ''} (the limit until they are verified is {limit}). "
            "Verify the client on one of their requests first, or wait until one is closed.")


# ---- Walk-in / paper requests -----------------
# The same rules as My Requests (routers/portal.py), however the request
# arrives: who may use the service, the cap on open
# requests while the client isn't verified, and the endorsement letter from
# the office head with a LEGAL SERVICE request from an unverified ESSU client
# (Citizen's Charter: "formal written request of office/department heads").
# Meeting the client in person doesn't replace the letter: it shows who the
# client is, the letter shows the office head authorized the request. The
# letter comes in the same submission, so a request is never created without it.
@router.post("/staff-encoded", response_model=LegalServiceRequestOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def create_staff_encoded_request(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: str = Form(..., max_length=20000),            # StaffEncodedRequestCreate, as JSON
    endorsement_letter: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """
    Any staff member encodes a request brought in person or on paper. There
    is no email code (the requester is not at a computer): instead the staff
    member confirms that the requester signed the Data Privacy consent on the
    paper form, and the request is logged as encoded by them. The requester
    is emailed the Request ID like any other submission.
    """
    # Imported here: portal.py imports this module, so a top-level import would be circular.
    from app.routers.portal import _read_checked

    try:
        payload = StaffEncodedRequestCreate.model_validate_json(payload)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=[{"loc": list(err["loc"]), "msg": err["msg"]} for err in e.errors()])
    if not payload.consent_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Confirm that the requester signed the Data Privacy consent on the paper form.",
        )
    service_type = session.exec(
        select(ServiceType).where(ServiceType.code == payload.service_type_code)
    ).first()
    if not service_type:
        raise HTTPException(status_code=400, detail="Choose a service.")
    # "Who may use it" (Service Types), as on the client side. For an existing
    # client, their saved category counts, since it is never overwritten.
    existing = (find_requester_by_contact(session, "email", payload.requester.email) if payload.requester.email
                else find_requester_by_phone(session, payload.requester.phone))
    if existing:
        # One walk-in at a time per client, so two staff encoding
        # for the same client can't both pass the open-request cap.
        session.exec(select(Requester).where(Requester.id == existing.id).with_for_update()).one()
    category = (existing.client_category if existing else None) or payload.requester.client_category
    allowed = session.exec(
        select(ServiceCategoryAccess.service_type_id).where(ServiceCategoryAccess.client_category == category)
    ).all()
    if service_type.id not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{service_type.name} isn't available for {CLIENT_CATEGORIES.get(category, 'this')} clients. "
                   "Choose another service.",
        )
    cap_message = walk_in_cap_message(session, existing)
    if cap_message:
        raise HTTPException(status_code=403, detail=cap_message)
    status_now = existing.verification_status if existing else "unverified"
    needs_letter = service_type.door != "complaint" and walk_in_needs_letter(category, status_now)
    letter_content = None
    if needs_letter:
        if endorsement_letter is None:
            raise HTTPException(
                status_code=400,
                detail=((f"The Legal Office {status_now} this client's verification before. Attach a NEW "
                         if status_now != "unverified" else "This client isn't verified yet. Attach the ")
                        + "signed endorsement letter from their office head (or dean, for students): "
                          "a legal service request needs it, however it arrives."),
            )
        letter_content = _read_checked(endorsement_letter, "Endorsement letter")
    requester = find_or_create_requester(session, payload.requester)
    office = session.get(Office, requester.office_id) if requester.office_id else None

    first_step = session.exec(
        select(WorkflowStepTemplate)
        .where(WorkflowStepTemplate.service_type_id == service_type.id)
        .order_by(WorkflowStepTemplate.sequence_order)
    ).first()
    assigned_assistant = _pick_next_legal_assistant(session)
    now = datetime.utcnow()
    new_request = LegalServiceRequest(
        tracking_id=generate_tracking_id(session),
        requester_id=requester.id,
        service_type_id=service_type.id,
        request_type="new",
        description=payload.description.strip(),
        privacy_consent_given_at=now,
        # The client's details as of this request; later edits never change it.
        client_name_at_submission=requester.full_name,
        client_last_name_at_submission=requester.last_name,
        client_first_name_at_submission=requester.first_name,
        client_middle_name_at_submission=requester.middle_name,
        client_name_extension_at_submission=requester.name_extension,
        client_category_at_submission=requester.client_category,
        client_office_at_submission=(office.name if office else requester.company_agency),
        client_position_at_submission=requester.position,
        submitted_at=now,
        due_date=compute_due_date(now, service_type.default_processing_days, load_holiday_dates(session)),
        current_step_id=first_step.id if first_step else None,
        assigned_officer_id=assigned_assistant.id if assigned_assistant else None,
    )
    session.add(new_request)
    session.flush()   # gives the request its id; nothing is committed yet

    # Save the letter and everything else together; undo it all if anything fails.
    letter_key = None
    try:
        if letter_content is not None:
            filename = endorsement_letter.filename or "file"
            letter_key = storage.generate_storage_key(filename)
            storage.save_file(letter_key, letter_content)
            letter = Document(
                request_id=new_request.id, category="client_submitted",
                original_filename=filename, storage_key=letter_key,
                mime_type=(mimetypes.guess_type(filename)[0] or "application/octet-stream")[:100],
                file_size_bytes=len(letter_content),
                uploaded_by_staff_id=current_staff.id,   # handed over by the client, added by staff
            )
            session.add(letter)
            session.flush()
            requester.endorsement_document_id = letter.id
            # A declined/revoked client keeps that status, so only an attorney may
            # verify them again (clients._allowed); the letter is there for them.
            again = requester.verification_status in ("declined", "revoked")
            if not again:
                requester.verification_status = "pending"
                requester.verification_method = "endorsement_letter"
            session.add(requester)
            # Staff-only History line (endorsement_uploaded isn't shown to the client).
            session.add(AuditLogEntry(
                request_id=new_request.id, staff_id=current_staff.id, action="endorsement_uploaded",
                detail=(f"{current_staff.full_name} added the endorsement letter \"{filename}\" "
                        "handed over by the client, for verification"
                        + (f" (the client was {requester.verification_status} before; an attorney decides)."
                           if again else ".")),
            ))
            notify(session, head_attorney_ids(session), "verification_pending",
                   (f"{requester.full_name} ({requester.verification_status} before) brought a new endorsement "
                    f"letter on {new_request.tracking_id}." if again else
                    f"{requester.full_name} is waiting for verification (endorsement letter on {new_request.tracking_id})."),
                   new_request, current_staff.id)

        session.add(AuditLogEntry(
            request_id=new_request.id,
            staff_id=current_staff.id,
            action="request_encoded",
            detail=(f"Encoded from a walk-in or paper request by {current_staff.full_name}. "
                    "The requester's Data Privacy consent is on the signed paper form."),
        ))
        if assigned_assistant:
            session.add(AuditLogEntry(
                request_id=new_request.id, actor_type="system", action="auto_assigned",
                detail=f"Automatically assigned to {assigned_assistant.full_name} (round-robin).",
            ))
            notify(session, [assigned_assistant.id], "assigned",
                   f"New request {new_request.tracking_id} ({service_type.name}) was assigned to you.",
                   new_request, current_staff.id)
        else:
            notify(session, head_attorney_ids(session), "unassigned",
                   f"New request {new_request.tracking_id} has no one assigned (no active legal assistant).",
                   new_request, current_staff.id)
        session.commit()
    except Exception:
        session.rollback()
        if letter_key:
            try:
                storage.delete_file(letter_key)
            except Exception:
                pass
        raise
    session.refresh(new_request)

    channel, destination = ("email", requester.email) if requester.email else ("phone", requester.phone)
    if destination:
        background_tasks.add_task(send_submission_confirmation, channel, destination, new_request.tracking_id)
    return _to_out(session, new_request)


def was_encoded_by(session: Session, req: LegalServiceRequest, staff_id: int) -> bool:
    return session.exec(
        select(AuditLogEntry.id).where(
            AuditLogEntry.request_id == req.id,
            AuditLogEntry.action == "request_encoded",
            AuditLogEntry.staff_id == staff_id,
        )
    ).first() is not None


# ---- Send Reminder -------------------------------------------------
REMINDER_INTERVAL = timedelta(hours=24)


@router.post("/{tracking_id}/remind")
@limiter.limit("20/minute")
def send_reminder(
    request: Request,
    tracking_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Emails the requester a reminder that the Legal Office is waiting for
    them. At most once every 24 hours per request; only while the request is
    marked as waiting for the client."""
    req = _get_by_tracking_id(session, tracking_id)
    # One reminder at a time per request: pressed three times at
    # once, the others wait here and then see the first one's History entry.
    lock_request(req)
    if current_staff.role == "legal_assistant" and req.assigned_officer_id != current_staff.id:
        raise HTTPException(status_code=403, detail="You can only send reminders on requests assigned to you.")
    ensure_not_closed(req)
    if not req.is_waiting_for_client:
        raise HTTPException(status_code=409, detail="This request is not marked as waiting for the client.")
    if not req.requester.email:
        raise HTTPException(status_code=409, detail="The requester has no email address on file, so a reminder can't be sent.")
    last = session.exec(
        select(AuditLogEntry.created_at).where(
            AuditLogEntry.request_id == req.id, AuditLogEntry.action == "reminder_sent",
        ).order_by(AuditLogEntry.created_at.desc())
    ).first()
    now = datetime.utcnow()
    if last and now - last < REMINDER_INTERVAL:
        hours = max(1, int((REMINDER_INTERVAL - (now - last)).total_seconds() // 3600) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"A reminder was already sent in the last 24 hours. You can send another in about {hours} hour(s).",
        )
    session.add(AuditLogEntry(
        request_id=req.id, staff_id=current_staff.id, action="reminder_sent",
        detail=f"{current_staff.full_name} sent the requester a reminder.",
    ))
    req.last_updated_at = now
    session.add(req)
    requester_email = req.requester.email
    note = req.staff_note_to_client
    session.commit()
    background_tasks.add_task(send_request_update, requester_email, req.tracking_id, reminder_update_lines(note))
    return {"sent": True, "sent_at": now}


# ---- Client contacted by phone (walk-ins) ------------------------------------
@router.post("/{tracking_id}/client-contacted")
@limiter.limit("20/minute")
def record_client_contacted(
    request: Request,
    tracking_id: str,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Records in History that staff phoned a client who has no email, as
    proof the client was told (LEXIS can't email them, and "Waiting for
    Client" doesn't pause the deadline). Allowed on closed requests too:
    staff still call after a cancel or reject. The entry is staff-only
    (not in _public_history_text's allow-list)."""
    req = _get_by_tracking_id(session, tracking_id)
    if current_staff.role == "legal_assistant" and req.assigned_officer_id != current_staff.id:
        raise HTTPException(status_code=403, detail="You can only record calls on requests assigned to you.")
    if req.requester.email:
        raise HTTPException(status_code=409, detail="This client has an email address on file, so LEXIS updates them by email.")
    if not req.requester.phone:
        raise HTTPException(status_code=409, detail="This client has no contact number on file.")
    now = datetime.utcnow()
    session.add(AuditLogEntry(
        request_id=req.id, staff_id=current_staff.id, action="client_contacted",
        detail=f"Client contacted by phone by {current_staff.full_name}.",
    ))
    req.last_updated_at = now
    session.add(req)
    session.commit()
    return {"recorded": True, "recorded_at": now}


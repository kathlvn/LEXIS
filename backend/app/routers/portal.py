"""
The door and My Requests.

Flow:
  1. Client enters their email address       -> POST /access/contact/send-code
     (email only, no SMS; limits per email and per IP)
  2. Client enters the 6-digit code           -> POST /portal/verify-code
     - known client: signed in (client session cookie, 30 min idle / 2 h max)
     - new client:   gets 30 minutes to register -> POST /portal/register
  3. My Requests                              -> GET /portal/me
     one request in detail                    -> GET /portal/requests/{id}
  4. Exit                                     -> POST /portal/logout

A client session opens only that client's own requests. The existing
document and message endpoints accept it too (see
core/requester_access.get_verified_request_for_path).

Who may submit:
- verified clients (staff checked the endorsement letter, referral, or met
  them in person) may submit, while the verification is still valid;
- clients not yet verified may have a limited number of open requests at a
  time (setting "unverified_max_open_requests", default 1) — their first
  request carries the endorsement letter that staff check;
- declined or revoked clients must contact the Legal Office.
"""
import json
import logging
import mimetypes
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi import status as http_status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core import storage
from app.core.csm_form import CsmFormError, load_csm_form
from app.core.database import get_session
from app.core.notifications import head_attorney_ids, notify
from app.core.requester_access import (
    consume_verified_contact,
    end_requester_session,
    find_requester_by_contact,
    get_current_client,
    get_verified_request_for_path,
    load_verified_contact,
    start_client_session,
    start_submit_session,
)
from app.core.security_monitoring import limiter
from app.core.settings_store import get_int_setting, get_setting
from app.core.verification import send_submission_confirmation
from app.core.working_days import compute_due_date, load_holiday_dates
from app.models.models import (
    CLIENT_CATEGORIES, build_full_name, AuditLogEntry, CsmResponse, Document, LegalServiceRequest, Office, Requester,
    ServiceCategoryAccess, ServiceRequirement, ServiceType, WorkflowStepTemplate,
)
from app.routers.public_access import check_contact_code, withdraw_request
from app.routers.requests import (
    _current_status_name, _pick_next_legal_assistant, _to_public_out, generate_tracking_id, is_closed_request,
)
from app.schemas.schemas import (
    ESSU_CLIENT_CATEGORIES,
    ClientMeOut,
    ClientProfileIn,
    ClientRegistration,
    ClientRequestSummary,
    CsmOut,
    CsmSubmission,
    DoorVerify,
    OfficeOut,
    PortalServiceOut,
    PortalServicesOut,
    PublicRequestOut,
    ServiceRequirementOut,
    WithdrawRequest,
)

router = APIRouter(prefix="/portal", tags=["portal"])
logger = logging.getLogger(__name__)

EDITABLE_STATUSES = {"unverified"}   # once staff are checking or have checked, details are locked


def _is_verified(client: Requester) -> bool:
    if client.verification_status != "verified":
        return False
    return client.verification_expires_at is None or client.verification_expires_at > datetime.utcnow()


def _needs_profile(client: Requester) -> bool:
    """Older records: no category yet, or the name not yet in parts."""
    return client.client_category is None or not (client.last_name and client.first_name)


def _submit_status(session: Session, client: Requester) -> tuple[bool, str | None]:
    if _needs_profile(client):
        return False, "Please complete your details first."
    if client.verification_status in ("declined", "revoked"):
        return False, ("The Legal Office could not verify your details. "
                       "Please contact the Legal Office before submitting a new request.")
    if _is_verified(client):
        return True, None
    limit = get_int_setting(session, "unverified_max_open_requests", 1)
    requests = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.requester_id == client.id)
    ).all()
    open_count = sum(1 for r in requests if not is_closed_request(session, r))
    if open_count >= limit:
        return False, ("Your details are still being verified by the Legal Office. Until then you can "
                       f"have {limit} open request{'s' if limit != 1 else ''} at a time.")
    return True, None


def _me(session: Session, client: Requester) -> ClientMeOut:
    office = session.get(Office, client.office_id) if client.office_id else None
    is_essu = client.client_category in ESSU_CLIENT_CATEGORIES
    rows = session.exec(
        select(LegalServiceRequest)
        .where(LegalServiceRequest.requester_id == client.id)
        .order_by(LegalServiceRequest.submitted_at.desc())
    ).all()
    services = {s.id: s for s in session.exec(select(ServiceType)).all()}
    summaries = []
    for r in rows:
        st = services.get(r.service_type_id)
        status = "Cancelled" if r.is_cancelled else "Rejected" if r.is_rejected else _current_status_name(session, r)
        summaries.append(ClientRequestSummary(
            tracking_id=r.tracking_id,
            service_type_code=st.code if st else None,
            service_type_name=st.name if st else None,
            door=st.door if st else "service",
            current_status=status,
            submitted_at=r.submitted_at,
            is_closed=is_closed_request(session, r),
        ))
    can_submit, reason = _submit_status(session, client)
    return ClientMeOut(
        full_name=client.full_name,
        last_name=client.last_name,
        first_name=client.first_name,
        middle_name=client.middle_name,
        name_extension=client.name_extension,
        contact=client.email or client.phone or "",
        client_category=client.client_category,
        client_category_label=CLIENT_CATEGORIES.get(client.client_category or ""),
        office_id=client.office_id,
        office_name=office.name if office else (client.company_agency if is_essu else None),
        organization=None if is_essu else client.company_agency,
        position=client.position,
        verification_status=client.verification_status,
        needs_profile=_needs_profile(client),
        can_edit_profile=client.verification_status in EDITABLE_STATUSES,
        can_submit_new=can_submit,
        submit_block_reason=reason,
        requests=summaries,
    )


def _apply_profile(session: Session, client: Requester, data: ClientProfileIn) -> None:
    if data.office_id is not None:
        office = session.get(Office, data.office_id)
        if not office or not office.is_active:
            raise HTTPException(status_code=400, detail="Choose an office from the list, or type it if it isn't listed.")
    client.last_name = data.last_name
    client.first_name = data.first_name
    client.middle_name = data.middle_name
    client.name_extension = data.name_extension
    client.full_name = build_full_name(data.first_name, data.middle_name, data.last_name, data.name_extension)
    client.client_category = data.client_category
    office_id, office_other = data.office_id, data.office_other
    if data.client_category in ESSU_CLIENT_CATEGORIES and office_id is None and office_other:
        # Typed under "Others" but already on the list (e.g. "college of
        # engineeriNg"): link to the listed office instead of keeping a
        # second spelling.
        listed = _listed_office_named(session, office_other)
        if listed:
            office_id, office_other = listed.id, None
    client.office_id = office_id
    client.position = data.position
    # One column holds what the client typed: their office when it isn't on
    # the list (ESSU), or their agency/organization (external).
    client.company_agency = office_other if data.client_category in ESSU_CLIENT_CATEGORIES else data.organization


def _listed_office_named(session: Session, name: str) -> Office | None:
    """The active office with this name, ignoring capitals and extra spaces."""
    key = " ".join(name.split()).lower()
    for office in session.exec(select(Office).where(Office.is_active == True)).all():  # noqa: E712
        if " ".join(office.name.split()).lower() == key:
            return office
    return None

@router.get("/offices", response_model=list[OfficeOut])
@limiter.limit("30/minute")
def list_offices(request: Request, session: Session = Depends(get_session)):
    """Public: the office list for the registration form (active only)."""
    return session.exec(
        select(Office).where(Office.is_active == True).order_by(Office.office_type, Office.name)  # noqa: E712
    ).all()


@router.post("/verify-code")
@limiter.limit("10/minute")
def verify_door_code(
    request: Request,
    response: Response,
    payload: DoorVerify,
    session: Session = Depends(get_session),
):
    """The door. A known client is signed in; a new one gets 30 minutes to
    register. Both answers come only after a correct code, so this can't be
    used to find out which contacts are on file."""
    record = check_contact_code(session, request, payload.contact, payload.code)
    client = find_requester_by_contact(session, record.channel, record.contact_value)
    if client:
        now = datetime.utcnow()
        record.verified_at = now
        record.consumed_at = now     # the code opened the door; it can't also be used to submit
        session.add(record)
        session.commit()
        start_client_session(session, client, response)
        return {"status": "signed_in", "needs_profile": _needs_profile(client)}
    start_submit_session(session, record, response)
    return {"status": "new_client"}


@router.post("/register", response_model=ClientMeOut)
@limiter.limit("5/minute")
def register(
    request: Request,
    response: Response,
    payload: ClientRegistration,
    session: Session = Depends(get_session),
):
    verification = load_verified_contact(request, session)
    if not payload.privacy_consent:
        raise HTTPException(status_code=400, detail="You must consent to the Data Privacy Notice to continue.")

    client = find_requester_by_contact(session, verification.channel, verification.contact_value)
    if not client:
        # Contact comes from the verified code, never from the form.
        client = Requester(
            email=verification.contact_value if verification.channel == "email" else None,
            phone=verification.contact_value if verification.channel == "phone" else None,
            full_name="",            # filled in from the name parts just below
            privacy_consent_given_at=datetime.utcnow(),
        )
        _apply_profile(session, client, payload)
        session.add(client)
        try:
            session.commit()
            session.refresh(client)
        except IntegrityError:
            # Registered at the same moment in another tab: the database
            # allows one client per email, so use that one.
            session.rollback()
            client = find_requester_by_contact(session, verification.channel, verification.contact_value)
    # else: registered in another tab meanwhile — sign in, don't duplicate.

    consume_verified_contact(session, verification, response)
    start_client_session(session, client, response)
    return _me(session, client)


@router.get("/me", response_model=ClientMeOut)
@limiter.limit("60/minute")
def get_me(request: Request, session: Session = Depends(get_session),
           client: Requester = Depends(get_current_client)):
    return _me(session, client)


@router.patch("/me", response_model=ClientMeOut)
@limiter.limit("10/minute")
def update_me(
    request: Request,
    payload: ClientProfileIn,
    session: Session = Depends(get_session),
    client: Requester = Depends(get_current_client),
):
    """Clients complete or correct their details until the Legal Office
    starts checking them; after that, changes go through the Legal Office."""
    if client.client_category is not None and client.verification_status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Your details are being or have been verified, so they can only be changed by the Legal Office. "
                   "Please message the Legal Office on one of your requests.",
        )
    _apply_profile(session, client, payload)
    session.add(client)
    session.commit()
    session.refresh(client)
    return _me(session, client)


@router.get("/requests/{tracking_id}", response_model=PublicRequestOut)
@limiter.limit("60/minute")
def get_my_request(
    request: Request,
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    return _to_public_out(session, req)


@router.post("/requests/{tracking_id}/withdraw", response_model=PublicRequestOut)
@limiter.limit("5/minute")
def withdraw_my_request(
    request: Request,
    payload: WithdrawRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    return withdraw_request(session, req, payload.reason, background_tasks)


@router.post("/logout")
@limiter.limit("30/minute")
def logout(request: Request, response: Response, session: Session = Depends(get_session)):
    end_requester_session(request, response, session)
    return {"detail": "Signed out."}



# ---------------------------------------------------------------------------
# Submitting a request through My Requests
#
# The request and ALL its files arrive in one submission and are checked
# together before anything is saved:
# - the service must be open to the client's category (Service Types page);
# - every REQUIRED checklist item of the service needs at least one file
#   (new requests; a follow-up may add documents freely);
# - an ESSU client who is not yet verified must include the signed
#   endorsement letter from their office head (Citizen's Charter: "formal
#   written request of office/department heads/key officials") with a LEGAL
#   SERVICE request. Complaints/referrals never ask for it:
#   the complaint workflow itself validates the complainant,
#   and the office head may be the one complained about;
# - the truthfulness statement must be ticked;
# - limits: open requests while unverified, requests per day, the same
#   request twice, files per submission, size per file and per submission.
# The client's name, category, office and position are copied onto the
# request, so later profile changes never rewrite older requests.
# ---------------------------------------------------------------------------
MAX_FILES_PER_SUBMISSION = 10
MAX_TOTAL_BYTES = 50 * 1024 * 1024   # 50 MB for one submission, all files together


def _endorsement_required(client: Requester) -> bool:
    """ESSU clients prove who they are, the first time, with a signed
    endorsement from their office head. Not asked again while staff are
    checking it (pending) or after they verified it. Applies to legal
    service requests only (see the note above)."""
    return client.client_category in ESSU_CLIENT_CATEGORIES and client.verification_status == "unverified"


def _allowed_service_ids(session: Session, client: Requester) -> set[int]:
    return set(session.exec(
        select(ServiceCategoryAccess.service_type_id)
        .where(ServiceCategoryAccess.client_category == (client.client_category or ""))
    ).all())


def _active_requirements(session: Session, service_type_id: int) -> list[ServiceRequirement]:
    return session.exec(
        select(ServiceRequirement)
        .where(ServiceRequirement.service_type_id == service_type_id, ServiceRequirement.is_active == True)  # noqa: E712
        .order_by(ServiceRequirement.sort_order, ServiceRequirement.id)
    ).all()


@router.get("/services", response_model=PortalServicesOut)
@limiter.limit("30/minute")
def list_my_services(request: Request, session: Session = Depends(get_session),
                     client: Requester = Depends(get_current_client)):
    """The services THIS client may request, each with its document
    checklist, and whether the endorsement letter is needed (for legal
    service requests only; never for complaints/referrals)."""
    allowed = _allowed_service_ids(session, client)
    services = session.exec(select(ServiceType).order_by(ServiceType.id)).all()
    needs_letter = _endorsement_required(client)
    return PortalServicesOut(
        services=[
            PortalServiceOut(
                code=st.code, name=st.name, description=st.description, door=st.door,
                default_processing_days=st.default_processing_days,
                requirements=[ServiceRequirementOut.model_validate(r) for r in _active_requirements(session, st.id)],
            )
            for st in services if st.id in allowed
        ],
        endorsement_required=needs_letter,
        endorsement_template=(get_setting(session, "endorsement_template", "") or None) if needs_letter else None,
    )


def _read_checked(f: UploadFile, label: str) -> bytes:
    """Reads one upload without ever holding more than the size limit in
    memory, and checks its name, type and size."""
    if f.size is not None and f.size > storage.MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail=f"{label}: File exceeds the "
                            f"{storage.MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB limit.")
    content = f.file.read(storage.MAX_FILE_SIZE_BYTES + 1)
    error = storage.validate_upload(f.filename or "", len(content), content)
    if error:
        raise HTTPException(status_code=400, detail=f"{label}: {error}")
    return content


@router.post("/requests", response_model=PublicRequestOut, status_code=http_status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def submit_request(
    request: Request,
    background_tasks: BackgroundTasks,
    service_type_code: str = Form(..., max_length=50),
    description: str = Form(..., min_length=1, max_length=5000),
    purpose: Optional[str] = Form(None, max_length=500),
    request_type: str = Form("new"),
    followup_tracking_id: Optional[str] = Form(None, max_length=20),
    attestation: bool = Form(...),
    files: Optional[list[UploadFile]] = File(None),
    requirement_ids: Optional[list[str]] = Form(None),   # one per file, same order; "" = additional file
    endorsement_letter: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
    client: Requester = Depends(get_current_client),
):
    files = files or []
    requirement_ids = requirement_ids or []
    description = description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Provide a description of your concern.")
    if not attestation:
        raise HTTPException(status_code=400, detail="Please confirm that the information you gave is true and correct.")
    if request_type not in ("new", "followup"):
        raise HTTPException(status_code=400, detail="Unknown request type.")

    # --- who may submit, and how often ---
    # One submission at a time per client: two sent together would
    # both pass the limits below. This row lock holds until the commit at the
    # end, so the second waits and then sees the first (PostgreSQL).
    session.exec(select(Requester).where(Requester.id == client.id).with_for_update()).one()
    can_submit, reason = _submit_status(session, client)
    if not can_submit:
        raise HTTPException(status_code=403, detail=reason)
    now = datetime.utcnow()
    mine = session.exec(select(LegalServiceRequest).where(LegalServiceRequest.requester_id == client.id)).all()
    per_day = get_int_setting(session, "max_requests_per_day", 5)
    if sum(1 for r in mine if r.submitted_at >= now - timedelta(hours=24)) >= per_day:
        raise HTTPException(status_code=429, detail=f"You can submit up to {per_day} requests a day. Please try again tomorrow.")

    # --- which service ---
    followup_of = None
    if request_type == "followup":
        tid = (followup_tracking_id or "").strip().upper()
        followup_of = next((r for r in mine if r.tracking_id == tid), None)
        if not followup_of or is_closed_request(session, followup_of):
            raise HTTPException(status_code=400, detail="Choose one of your ongoing requests to follow up.")
        service_type = session.get(ServiceType, followup_of.service_type_id)
    else:
        service_type = session.exec(select(ServiceType).where(ServiceType.code == service_type_code)).first()
        if not service_type or service_type.id not in _allowed_service_ids(session, client):
            raise HTTPException(status_code=403, detail="This service isn't available for your category. "
                                "Please choose another service or contact the Legal Office.")
        for r in mine:   # the same request twice ("kun mag-request hin sigida")
            if (r.service_type_id == service_type.id and r.description.strip() == description
                    and r.submitted_at >= now - timedelta(hours=24) and not is_closed_request(session, r)):
                raise HTTPException(status_code=409, detail=f"You already submitted this request as {r.tracking_id}.")

    # --- files: check EVERYTHING before saving anything ---
    if len(files) != len(requirement_ids) and requirement_ids:
        raise HTTPException(status_code=400, detail="Each file must be matched to a checklist item or marked as additional.")
    if len(files) > MAX_FILES_PER_SUBMISSION:
        raise HTTPException(status_code=400, detail=f"You can upload up to {MAX_FILES_PER_SUBMISSION} files at a time.")
    requirements = {r.id: r for r in _active_requirements(session, service_type.id)}
    checked: list[tuple[UploadFile, bytes, int | None]] = []
    for i, f in enumerate(files):
        raw = requirement_ids[i].strip() if i < len(requirement_ids) else ""
        req_id = None
        if raw:
            if not raw.isdigit() or int(raw) not in requirements:
                raise HTTPException(status_code=400, detail=f"{f.filename}: doesn't match this service's checklist.")
            req_id = int(raw)
        checked.append((f, _read_checked(f, f.filename or "File"), req_id))

    if request_type == "new":
        covered = {req_id for _f, _c, req_id in checked if req_id}
        missing = [r.label for r in requirements.values() if r.is_required and r.id not in covered]
        if missing:
            raise HTTPException(status_code=400, detail="Please upload the required documents: " + "; ".join(missing) + ".")

    needs_endorsement = _endorsement_required(client) and service_type.door != "complaint"
    endorsement: tuple[UploadFile, bytes] | None = None
    if endorsement_letter is not None and needs_endorsement:
        endorsement = (endorsement_letter, _read_checked(endorsement_letter, "Endorsement letter"))
    elif needs_endorsement:
        raise HTTPException(status_code=400, detail="Please upload the signed endorsement letter from your office head "
                            "(or dean, for students). You only need to do this once.")
    total = sum(len(c) for _f, c, _r in checked) + (len(endorsement[1]) if endorsement else 0)
    if total > MAX_TOTAL_BYTES:
        raise HTTPException(status_code=400, detail=f"The files together exceed {MAX_TOTAL_BYTES // (1024 * 1024)} MB. "
                            "Please upload fewer or smaller files.")

    # --- create the request ---
    first_step = session.exec(
        select(WorkflowStepTemplate)
        .where(WorkflowStepTemplate.service_type_id == service_type.id)
        .order_by(WorkflowStepTemplate.sequence_order)
    ).first()
    assignee = _pick_next_legal_assistant(session)
    office = session.get(Office, client.office_id) if client.office_id else None
    new_request = LegalServiceRequest(
        tracking_id=generate_tracking_id(session),
        requester_id=client.id,
        service_type_id=service_type.id,
        request_type=request_type,
        followup_of_id=followup_of.id if followup_of else None,
        purpose=(purpose or "").strip() or None,
        description=description,
        privacy_consent_given_at=client.privacy_consent_given_at or now,
        attestation_given_at=now,
        client_name_at_submission=client.full_name,
        client_last_name_at_submission=client.last_name,
        client_first_name_at_submission=client.first_name,
        client_middle_name_at_submission=client.middle_name,
        client_name_extension_at_submission=client.name_extension,
        client_category_at_submission=client.client_category,
        client_office_at_submission=(office.name if office else client.company_agency),
        client_position_at_submission=client.position,
        submitted_at=now,
        due_date=compute_due_date(now, service_type.default_processing_days, load_holiday_dates(session)),
        current_step_id=first_step.id if first_step else None,
        assigned_officer_id=assignee.id if assignee else None,
    )
    session.add(new_request)
    session.flush()   # gives the request its id; nothing is committed yet

    # --- save the files; undo them all if anything fails ---
    written: list[str] = []
    try:
        def save(f: UploadFile, content: bytes, requirement_id: int | None) -> Document:
            key = storage.generate_storage_key(f.filename or "file")
            storage.save_file(key, content)
            written.append(key)
            doc = Document(
                request_id=new_request.id, category="client_submitted",
                original_filename=f.filename or "file", storage_key=key,
                mime_type=(mimetypes.guess_type(f.filename or "")[0] or "application/octet-stream")[:100],
                file_size_bytes=len(content), requirement_id=requirement_id,
                uploaded_by_requester_id=client.id,
            )
            session.add(doc)
            return doc

        saved = [save(f, c, rid) for f, c, rid in checked]
        if endorsement:
            letter = save(endorsement[0], endorsement[1], None)
            session.flush()
            client.endorsement_document_id = letter.id
            client.verification_status = "pending"
            client.verification_method = "endorsement_letter"
            session.add(client)
            session.add(AuditLogEntry(request_id=new_request.id, actor_type="requester", action="endorsement_uploaded",
                                      detail=f"Requester uploaded the endorsement letter \"{letter.original_filename}\" for verification."))

        session.add(AuditLogEntry(request_id=new_request.id, actor_type="requester", action="request_submitted",
                                  detail="Request submitted through My Requests. Truthfulness statement confirmed."))
        for doc in saved:
            session.add(AuditLogEntry(request_id=new_request.id, actor_type="requester",
                                      action="client_document_uploaded",
                                      detail=f"Requester uploaded \"{doc.original_filename}\"."))
        if assignee:
            session.add(AuditLogEntry(request_id=new_request.id, actor_type="system", action="auto_assigned",
                                      detail=f"Automatically assigned to {assignee.full_name} (round-robin)."))
            notify(session, [assignee.id], "assigned",
                   f"New request {new_request.tracking_id} ({service_type.name}) was assigned to you.", new_request)
        else:
            notify(session, head_attorney_ids(session), "unassigned",
                   f"New request {new_request.tracking_id} has no one assigned (no active legal assistant).", new_request)
        if endorsement:
            notify(session, head_attorney_ids(session), "verification_pending",
                   f"{client.full_name} is waiting for verification (endorsement letter on {new_request.tracking_id}).",
                   new_request)
        session.commit()
    except Exception:
        session.rollback()
        for key in written:
            try:
                storage.delete_file(key)
            except Exception:
                pass
        raise

    session.refresh(new_request)
    channel, destination = ("email", client.email) if client.email else ("phone", client.phone)
    background_tasks.add_task(send_submission_confirmation, channel, destination, new_request.tracking_id)
    return _to_public_out(session, new_request)


# ---------------------------------------------------------------------------
# Client Satisfaction Measurement
#
# Offered on the client's request page once the request is Completed;
# optional, answered once (the unique request_id in csm_response). The
# questions come from app/csm_form.json (core/csm_form.py); every question
# must be answered (each has an N/A choice where it fits), the suggestion is
# optional. Staff see totals only on Reports, never one client's answers.
# ---------------------------------------------------------------------------
CSM_UNAVAILABLE = "The survey is not available right now. Please try again later."
CSM_MISMATCH = "Some answers do not match the survey. Please reload the page and try again."


def _public_csm_form(form: dict) -> dict:
    """What the client's page needs: the questions and choices, not the scoring."""
    return {
        "version": form["version"],
        "placeholder": form["placeholder"],
        "title": form["title"],
        "intro": form["intro"],
        "sections": [
            {"heading": s["heading"], "instructions": s["instructions"],
             "questions": [{"id": q["id"], "text": q["text"], "options": q["options"]} for q in s["questions"]]}
            for s in form["sections"]
        ],
        "comment": form["comment"],
    }


def _csm_state(session: Session, req: LegalServiceRequest) -> CsmOut:
    if req.is_cancelled or req.is_rejected or _current_status_name(session, req) != "Completed":
        return CsmOut(status="not_offered")
    done = session.exec(select(CsmResponse).where(CsmResponse.request_id == req.id)).first()
    if done:
        return CsmOut(status="answered", answered_at=done.submitted_at)
    try:
        form = load_csm_form()
    except CsmFormError as exc:
        logger.warning("CSM form not available: %s", exc)
        return CsmOut(status="unavailable")
    return CsmOut(status="open", form=_public_csm_form(form))


@router.get("/requests/{tracking_id}/csm", response_model=CsmOut)
@limiter.limit("60/minute")
def get_my_csm(
    request: Request,
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    return _csm_state(session, req)


@router.post("/requests/{tracking_id}/csm", response_model=CsmOut)
@limiter.limit("5/minute")
def submit_my_csm(
    request: Request,
    payload: CsmSubmission,
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    state = _csm_state(session, req)
    if state.status == "not_offered":
        raise HTTPException(status_code=409, detail="The survey opens once your request is completed.")
    if state.status == "answered":
        raise HTTPException(status_code=409, detail="You have already answered the survey for this request. Thank you!")
    if state.status == "unavailable":
        raise HTTPException(status_code=503, detail=CSM_UNAVAILABLE)
    try:
        form = load_csm_form()
    except CsmFormError:
        raise HTTPException(status_code=503, detail=CSM_UNAVAILABLE)
    if payload.form_version != form["version"]:
        raise HTTPException(status_code=409, detail="The survey was updated while you were answering. "
                                                    "Please reload the page and answer it again.")

    questions = [q for s in form["sections"] for q in s["questions"]]
    if set(payload.answers) - {q["id"] for q in questions}:
        raise HTTPException(status_code=400, detail=CSM_MISMATCH)
    missing = [q["id"] for q in questions if q["id"] not in payload.answers]
    if missing:
        raise HTTPException(status_code=400, detail=f"Please answer every question (missing: {', '.join(missing)}).")
    if any(payload.answers[q["id"]] not in {o["value"] for o in q["options"]} for q in questions):
        raise HTTPException(status_code=400, detail=CSM_MISMATCH)

    comment = (payload.comment or "").strip() if form["comment"] else ""
    if form["comment"] and len(comment) > form["comment"]["max_length"]:
        raise HTTPException(status_code=400, detail=f"Your suggestion is too long "
                                                    f"(up to {form['comment']['max_length']} characters).")

    session.add(CsmResponse(
        request_id=req.id,
        form_version=form["version"],
        answers=json.dumps({q["id"]: payload.answers[q["id"]] for q in questions}, separators=(",", ":")),
        comment=comment or None,
    ))
    try:
        session.commit()
    except IntegrityError:   # a second click that arrived at the same time
        session.rollback()
        raise HTTPException(status_code=409, detail="You have already answered the survey for this request. Thank you!")
    return _csm_state(session, req)
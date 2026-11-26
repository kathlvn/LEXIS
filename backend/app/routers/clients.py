"""
Client verification and client search for staff.

Verification belongs to the CLIENT (a person), but staff do it from one of
the client's requests, on Request Detail, where the endorsement letter or
the complaint/referral already is.

Who may do what:
- Verify / Decline: the attorney and Head Attorney on any request; a legal
  assistant only on requests assigned to them (same rule as status,
  documents and messages). Re-verifying a declined or revoked client is for
  the attorney and Head Attorney only, so a decision isn't quietly undone.
- Revoke, and correcting a client's details: Head Attorney only.
- Every action is logged on the request's staff History (never shown to
  the client) and the client is emailed. A decline/revoke reason goes in
  the email only if staff tick "tell the client".

Client search (All Requests filter): at least 2 letters, at most 10
matches, each with its number of requests, so staff screens never load
everyone's names at once.
"""
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlmodel import Session, func, or_, select

from app.core import storage
from app.core.database import get_session
from app.core.deps import get_current_staff_user
from app.core.security_monitoring import limiter
from app.core.settings_store import get_int_setting
from app.core.verification import send_client_verification_update
from app.models.models import (
    CLIENT_CATEGORIES, ESSU_CATEGORIES, AuditLogEntry, Document, LegalServiceRequest, Office, Requester,
    StaffUser, formal_name,
)
from app.models.models import effective_verification_status as effective_status   # "expired" when past its end date
from app.routers.portal import _apply_profile
from app.core.requester_access import find_requester_by_contact
from app.routers.requests import (
    find_requester_by_phone, is_closed_request, walk_in_cap_message, walk_in_needs_letter,
)
from app.schemas.schemas import (
    ClientDecision, ClientProfileIn, ClientSearchItem, ClientVerify, StaffClientOut, WalkInLookupOut,
)

router = APIRouter(tags=["clients"])

ATTORNEY_ROLES = ("attorney", "head_attorney")
METHOD_LABELS = {
    "endorsement_letter": "endorsement letter",
    "in_person": "in person",
    "referral": "referral (complaint/referral process)",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _get_request(session: Session, tracking_id: str) -> LegalServiceRequest:
    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    return req


def _handles(staff: StaffUser, req: LegalServiceRequest) -> bool:
    return staff.role in ATTORNEY_ROLES or req.assigned_officer_id == staff.id


def _allowed(staff: StaffUser, req: LegalServiceRequest, client: Requester) -> dict[str, bool]:
    status = effective_status(client)
    handles = _handles(staff, req)
    is_head = staff.role == "head_attorney"
    return {
        "can_verify": handles and (
            status in ("unverified", "pending", "expired")
            or (status in ("declined", "revoked") and staff.role in ATTORNEY_ROLES)
        ),
        "can_decline": handles and status in ("unverified", "pending", "expired"),
        "can_revoke": is_head and status == "verified",
        "can_edit": is_head,
    }


def _require(allowed: dict[str, bool], key: str, message: str) -> None:
    if not allowed[key]:
        raise HTTPException(status_code=403, detail=message)


def _request_counts(session: Session, client_id: int) -> tuple[int, int]:
    rows = session.exec(select(LegalServiceRequest).where(LegalServiceRequest.requester_id == client_id)).all()
    return len(rows), sum(1 for r in rows if not is_closed_request(session, r))


def _office_name(session: Session, client: Requester) -> str | None:
    if client.office_id:
        office = session.get(Office, client.office_id)
        if office:
            return office.name
    return client.company_agency if client.client_category in ESSU_CATEGORIES else None


def _client_out(session: Session, staff: StaffUser, req: LegalServiceRequest) -> StaffClientOut:
    client = req.requester
    letter = session.get(Document, client.endorsement_document_id) if client.endorsement_document_id else None
    verifier = session.get(StaffUser, client.verified_by_staff_id) if client.verified_by_staff_id else None
    total, open_count = _request_counts(session, client.id)
    return StaffClientOut(
        id=client.id,
        full_name=client.full_name,
        formal_name=formal_name(client.last_name, client.first_name, client.middle_name,
                                client.name_extension, client.full_name),
        last_name=client.last_name, first_name=client.first_name,
        middle_name=client.middle_name, name_extension=client.name_extension,
        client_category=client.client_category,
        client_category_label=CLIENT_CATEGORIES.get(client.client_category or ""),
        office_id=client.office_id,
        office_name=_office_name(session, client),
        organization=None if client.client_category in ESSU_CATEGORIES else client.company_agency,
        position=client.position,
        email=client.email,
        phone=client.phone,
        verification_status=effective_status(client),
        verification_method=client.verification_method,
        verified_at=client.verified_at,
        verification_expires_at=client.verification_expires_at,
        verified_by_name=verifier.full_name if verifier else None,
        verification_note=client.verification_note,
        endorsement_filename=letter.original_filename if letter else None,
        total_requests=total,
        open_requests=open_count,
        needs_profile=client.client_category is None or not (client.last_name and client.first_name),
        **_allowed(staff, req, client),
    )


def _log(session: Session, req: LegalServiceRequest, staff: StaffUser, action: str, detail: str) -> None:
    # Staff History only: these actions aren't on the requester's allow-list
    # (requests._public_history_text), so the client never sees them there.
    session.add(AuditLogEntry(request_id=req.id, staff_id=staff.id, actor_type="staff",
                              action=action, detail=detail[:1000]))


def _attachment_header(filename: str) -> str:
    """Download header that works for names with ñ, quotes or spaces:
    a plain ASCII fallback plus the exact UTF-8 name.
    The fallback keeps printable ASCII only, so a line break in a name
    can't break the header."""
    fallback = "".join(c for c in filename if " " <= c <= "~" and c not in '"\\').strip() or "document"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


# The same person opening the same file again within this time adds no new
# History line, so reading a file a few times doesn't bury the History.
OPENED_REPEAT_MINUTES = 30


def log_staff_opened(session: Session, staff: StaffUser, req: LegalServiceRequest, detail: str) -> None:
    """Request History (staff only): a staff member opened a client's file.
    "document_opened" is not on the client's History allow-list
    (requests._public_history_text), so clients never see it."""
    detail = detail[:1000]
    recent = session.exec(
        select(AuditLogEntry.id).where(
            AuditLogEntry.request_id == req.id,
            AuditLogEntry.staff_id == staff.id,
            AuditLogEntry.action == "document_opened",
            AuditLogEntry.detail == detail,
            AuditLogEntry.created_at >= datetime.utcnow() - timedelta(minutes=OPENED_REPEAT_MINUTES),
        )
    ).first()
    if recent:
        return
    session.add(AuditLogEntry(request_id=req.id, actor_type="staff", staff_id=staff.id,
                              action="document_opened", detail=detail))
    session.commit()


# --------------------------------------------------------------------------
# Request Detail: the client box
# --------------------------------------------------------------------------

@router.get("/requests/{tracking_id}/client", response_model=StaffClientOut)
@limiter.limit("60/minute")
def get_client(request: Request, tracking_id: str, session: Session = Depends(get_session),
               staff: StaffUser = Depends(get_current_staff_user)):
    """The client of this request: details, verification, request counts,
    and which actions this staff member may take."""
    return _client_out(session, staff, _get_request(session, tracking_id))


@router.get("/requests/{tracking_id}/client/endorsement-letter")
@limiter.limit("30/minute")
def download_endorsement_letter(request: Request, tracking_id: str, session: Session = Depends(get_session),
                                staff: StaffUser = Depends(get_current_staff_user)):
    """The client's endorsement letter, from whichever of their requests it
    came with. A legal assistant needs this request to be assigned to them,
    the same rule as other document downloads."""
    req = _get_request(session, tracking_id)
    if not _handles(staff, req):
        raise HTTPException(status_code=403, detail="You can only open documents on requests assigned to you.")
    client = req.requester
    letter = session.get(Document, client.endorsement_document_id) if client.endorsement_document_id else None
    if not letter or letter.uploaded_by_requester_id not in (None, client.id):
        raise HTTPException(status_code=404, detail="This client has no endorsement letter.")
    content = storage.read_file(letter.storage_key)
    log_staff_opened(session, staff, req, f"Opened the client's endorsement letter: {letter.original_filename}")
    return Response(
        content=content,
        media_type=letter.mime_type or "application/octet-stream",
        headers={"Content-Disposition": _attachment_header(letter.original_filename)},
    )


@router.post("/requests/{tracking_id}/client/verify", response_model=StaffClientOut)
@limiter.limit("20/minute")
def verify_client(request: Request, tracking_id: str, payload: ClientVerify, background_tasks: BackgroundTasks,
                  session: Session = Depends(get_session), staff: StaffUser = Depends(get_current_staff_user)):
    req = _get_request(session, tracking_id)
    client = req.requester
    _require(_allowed(staff, req, client), "can_verify",
             "You can't verify this client from this request." if effective_status(client) != "verified"
             else "This client is already verified.")
    if client.client_category is None:
        raise HTTPException(status_code=409, detail="The client must complete their details in My Requests first.")
    if payload.method == "endorsement_letter" and not client.endorsement_document_id:
        raise HTTPException(status_code=409, detail="This client has no endorsement letter. Choose another method.")

    now = datetime.utcnow()
    days = get_int_setting(session, "verification_valid_days", 365)
    client.verification_status = "verified"
    client.verification_method = payload.method
    client.verified_by_staff_id = staff.id
    client.verified_at = now
    client.verification_expires_at = now + timedelta(days=max(days, 1))
    client.verification_note = payload.note
    session.add(client)
    _log(session, req, staff, "client_verified",
         f"Client {client.full_name} verified ({METHOD_LABELS[payload.method]}), valid for {days} days."
         + (f' Note: "{payload.note}"' if payload.note else ""))
    session.commit()
    session.refresh(client)
    background_tasks.add_task(send_client_verification_update, client.email, "verified",
                              None, client.verification_expires_at)
    return _client_out(session, staff, req)


@router.post("/requests/{tracking_id}/client/decline", response_model=StaffClientOut)
@limiter.limit("20/minute")
def decline_client(request: Request, tracking_id: str, payload: ClientDecision, background_tasks: BackgroundTasks,
                   session: Session = Depends(get_session), staff: StaffUser = Depends(get_current_staff_user)):
    req = _get_request(session, tracking_id)
    client = req.requester
    _require(_allowed(staff, req, client), "can_decline", "You can't decline this client from this request.")
    client.verification_status = "declined"
    client.verification_note = payload.reason
    session.add(client)
    _log(session, req, staff, "client_declined",
         f'Client {client.full_name} declined. Reason (staff only): "{payload.reason}"'
         + (" The reason was included in the email." if payload.tell_client else ""))
    session.commit()
    background_tasks.add_task(send_client_verification_update, client.email, "declined",
                              payload.reason if payload.tell_client else None, None)
    return _client_out(session, staff, req)


@router.post("/requests/{tracking_id}/client/revoke", response_model=StaffClientOut)
@limiter.limit("20/minute")
def revoke_client(request: Request, tracking_id: str, payload: ClientDecision, background_tasks: BackgroundTasks,
                  session: Session = Depends(get_session), staff: StaffUser = Depends(get_current_staff_user)):
    req = _get_request(session, tracking_id)
    client = req.requester
    _require(_allowed(staff, req, client), "can_revoke", "Only the Head Attorney can revoke a verified client.")
    client.verification_status = "revoked"
    client.verification_note = payload.reason
    client.verification_expires_at = datetime.utcnow()
    session.add(client)
    _log(session, req, staff, "client_revoked",
         f'Verification of {client.full_name} revoked. Reason (staff only): "{payload.reason}"'
         + (" The reason was included in the email." if payload.tell_client else ""))
    session.commit()
    background_tasks.add_task(send_client_verification_update, client.email, "revoked",
                              payload.reason if payload.tell_client else None, None)
    return _client_out(session, staff, req)


@router.put("/requests/{tracking_id}/client", response_model=StaffClientOut)
@limiter.limit("20/minute")
def edit_client(request: Request, tracking_id: str, payload: ClientProfileIn,
                session: Session = Depends(get_session), staff: StaffUser = Depends(get_current_staff_user)):
    """Head Attorney: correct a client's details (e.g. after checking an ID
    in person). Older requests keep the details they were submitted with."""
    req = _get_request(session, tracking_id)
    client = req.requester
    _require(_allowed(staff, req, client), "can_edit", "Only the Head Attorney can change a client's details.")
    before = client.full_name
    _apply_profile(session, client, payload)
    session.add(client)
    _log(session, req, staff, "client_details_edited",
         f"Client details corrected by staff (was: {before}; now: {client.full_name}).")
    session.commit()
    return _client_out(session, staff, req)


# --------------------------------------------------------------------------
# All Requests: client search
# --------------------------------------------------------------------------

@router.get("/clients/search", response_model=list[ClientSearchItem])
@limiter.limit("60/minute")
def search_clients(request: Request, q: str = Query(..., min_length=2, max_length=100),
                   session: Session = Depends(get_session), staff: StaffUser = Depends(get_current_staff_user)):
    term = q.strip()
    if len(term) < 2:
        return []
    like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = session.exec(
        select(Requester)
        .outerjoin(Office, Requester.office_id == Office.id)
        .where(or_(
            Requester.full_name.ilike(like, escape="\\"),
            Requester.last_name.ilike(like, escape="\\"),
            Requester.email.ilike(like, escape="\\"),
            Requester.company_agency.ilike(like, escape="\\"),
            Office.name.ilike(like, escape="\\"),
        ))
        .order_by(func.lower(func.coalesce(Requester.last_name, Requester.full_name)), Requester.id)
        .limit(10)
    ).all()
    return [_search_item(session, c) for c in rows]


@router.get("/clients/lookup", response_model=WalkInLookupOut)
@limiter.limit("60/minute")
def lookup_client(request: Request, email: Optional[str] = Query(None, max_length=254),
                  phone: Optional[str] = Query(None, max_length=20),
                  session: Session = Depends(get_session), staff: StaffUser = Depends(get_current_staff_user)):
    """Walk-in form: the client with exactly this email (any capitals)
    or, for a client without email, this number (ignoring spaces and dashes),
    matched the same way the walk-in itself matches. Exact match only, so it
    can't be used to browse clients. Registered before /clients/{client_id}."""
    email = (email or "").strip().lower()
    phone = re.sub(r"[\s-]", "", phone or "")
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Enter an email address or a contact number.")
    client = find_requester_by_contact(session, "email", email) if email else find_requester_by_phone(session, phone)
    if not client:
        return WalkInLookupOut(found=False)
    office = session.get(Office, client.office_id) if client.office_id else None
    is_essu = client.client_category in ESSU_CATEGORIES
    _total, open_count = _request_counts(session, client.id)
    return WalkInLookupOut(
        found=True,
        formal_name=formal_name(client.last_name, client.first_name, client.middle_name, client.name_extension,
                                client.full_name),
        last_name=client.last_name,
        first_name=client.first_name,
        middle_name=client.middle_name,
        name_extension=client.name_extension,
        client_category=client.client_category,
        client_category_label=CLIENT_CATEGORIES.get(client.client_category or ""),
        office_id=client.office_id,
        office_name=office.name if office else (client.company_agency if is_essu else None),
        organization=None if is_essu else client.company_agency,
        position=client.position,
        verification_status=effective_status(client),
        details_complete=bool(client.last_name and client.first_name and client.client_category),
        needs_letter=walk_in_needs_letter(client.client_category, client.verification_status),
        open_requests=open_count,
        cap_message=walk_in_cap_message(session, client),
    )


@router.get("/clients/{client_id}", response_model=ClientSearchItem)
@limiter.limit("60/minute")
def get_client_summary(request: Request, client_id: int, session: Session = Depends(get_session),
                       staff: StaffUser = Depends(get_current_staff_user)):
    """One client's summary, for the strip above All Requests when the list
    is filtered to that client (e.g. from the request count on Request
    Detail). Registered after /clients/search, so "search" never lands here."""
    client = session.get(Requester, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    return _search_item(session, client)


def _search_item(session: Session, c: Requester) -> ClientSearchItem:
    total, open_count = _request_counts(session, c.id)
    return ClientSearchItem(
        id=c.id,
        formal_name=formal_name(c.last_name, c.first_name, c.middle_name, c.name_extension, c.full_name),
        office_name=_office_name(session, c) or (c.company_agency or None),
        email=c.email,
        verification_status=effective_status(c),
        total_requests=total,
        open_requests=open_count,
    )
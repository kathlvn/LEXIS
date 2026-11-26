"""
Public requester access sessions — the "wristband".

After a correct one-time code at the door (My Requests), the browser gets
an HttpOnly cookie that proves "this is client X". Public endpoints check
that cookie instead of trusting an email sent along with every call.

Rules:
- ends after `requester_session_idle_minutes` with no activity (default 30)
- ends after `requester_session_max_minutes` no matter what (default 120)
- ends immediately on Exit (POST /portal/logout)
- unlocks only that client's own requests

An earlier version had a "request session" (one request,
opened with Request ID + code on Track Request); it is retired. Old rows keep request_id
in the table, but such a session no longer opens anything.
"""
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlmodel import Session, func, select

from app.core.config import settings
from app.core.database import get_session
from app.core.security import generate_session_token, hash_session_token
from app.models.models import ContactVerification, LegalServiceRequest, Requester, RequesterAccessSession

SESSION_ENDED_MESSAGE = (
    "Your session has ended for security. Please request a new verification code to continue."
)


def start_client_session(session: Session, requester: Requester, response: Response) -> None:
    """Client session (the door): opens every request of this client."""
    now = datetime.utcnow()
    raw_token = generate_session_token()
    session.add(RequesterAccessSession(
        requester_id=requester.id,
        token_hash=hash_session_token(raw_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.requester_session_max_minutes),
    ))
    session.commit()

    response.set_cookie(
        key=settings.requester_cookie_name,
        value=raw_token,
        httponly=True,  # page JavaScript can't read it, so injected scripts can't steal it
        secure=settings.session_cookie_secure,
        samesite="strict",  # never sent along from other websites
        max_age=settings.requester_session_max_minutes * 60,
    )

def _load_active_session(request: Request, session: Session) -> RequesterAccessSession:
    raw_token = request.cookies.get(settings.requester_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESSION_ENDED_MESSAGE)

    access = session.exec(
        select(RequesterAccessSession).where(
            RequesterAccessSession.token_hash == hash_session_token(raw_token)
        )
    ).first()

    now = datetime.utcnow()
    idle_limit = timedelta(minutes=settings.requester_session_idle_minutes)
    if (
        not access
        or access.revoked_at is not None
        or access.expires_at <= now
        or now - access.last_seen_at > idle_limit
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESSION_ENDED_MESSAGE)

    # Activity resets the 30-minute idle timer (but never the 2-hour limit).
    access.last_seen_at = now
    session.add(access)
    session.commit()
    return access


def end_requester_session(request: Request, response: Response, session: Session) -> None:
    """Exit / Sign out: revoke server-side AND clear the cookies. Safe to
    call even if the session already expired."""
    raw_token = request.cookies.get(settings.requester_cookie_name)
    if raw_token:
        access = session.exec(
            select(RequesterAccessSession).where(
                RequesterAccessSession.token_hash == hash_session_token(raw_token)
            )
        ).first()
        if access and access.revoked_at is None:
            access.revoked_at = datetime.utcnow()
            session.add(access)
            session.commit()
    response.delete_cookie(settings.requester_cookie_name, samesite="strict")

    # A new client's unfinished sign-up (the lois_submit cookie) ends too, so
    # "Sign out" on "Tell us who you are" can't be undone with the Back button.
    raw_submit = request.cookies.get(settings.submit_cookie_name)
    if raw_submit:
        pending = session.exec(
            select(ContactVerification).where(ContactVerification.token_hash == hash_session_token(raw_submit))
        ).first()
        if pending and pending.consumed_at is None:
            pending.consumed_at = datetime.utcnow()
            session.add(pending)
            session.commit()
    response.delete_cookie(settings.submit_cookie_name, samesite="strict")

def get_verified_request_for_path(
    tracking_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> LegalServiceRequest:
    """Dependency for public endpoints that have {tracking_id} in the URL:
    the request must belong to the signed-in client. A request that doesn't
    exist and one that belongs to someone else get the same 404, so editing
    the URL can never open someone else's request or reveal that it exists."""
    access = _load_active_session(request, session)
    if access.requester_id is None:
        # A retired request session (old Track Request): sign in at the door.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESSION_ENDED_MESSAGE)
    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req or req.requester_id != access.requester_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return req

def get_current_client(
    request: Request,
    session: Session = Depends(get_session),
) -> Requester:
    """Dependency for the My Requests portal: the client who came in through
    the door, or 401."""
    access = _load_active_session(request, session)
    client = session.get(Requester, access.requester_id) if access.requester_id else None
    if not client:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESSION_ENDED_MESSAGE)
    return client


def find_requester_by_contact(session: Session, channel: str, value: str) -> Requester | None:
    """The client record for a verified contact (email compared lowercase).
    The oldest record wins if an older submission created duplicates."""
    if channel == "email":
        cond = func.lower(Requester.email) == value.lower()
    else:
        cond = Requester.phone == value
    return session.exec(select(Requester).where(cond).order_by(Requester.id)).first()

# ---------------------------------------------------------------------------
# The door, for a NEW client: proof that they control the email they typed,
# kept for 30 minutes while they register. One verification = one sign-up.
# ---------------------------------------------------------------------------

def normalize_contact(contact: str) -> tuple[str, str]:
    """Returns (channel, normalized value). Email is compared lowercase.
    Only "email" is accepted at the door (no SMS); see routers/public_access."""
    value = contact.strip()
    if "@" in value:
        return "email", value.lower()
    return "phone", value


def start_submit_session(session: Session, verification: ContactVerification, response: Response) -> None:
    raw_token = generate_session_token()
    verification.verified_at = datetime.utcnow()
    verification.token_hash = hash_session_token(raw_token)
    session.add(verification)
    session.commit()
    response.set_cookie(
        key=settings.submit_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=settings.submit_verification_minutes * 60,
    )


def load_verified_contact(request: Request, session: Session) -> ContactVerification:
    """The unused, unexpired contact verification this browser holds (the
          lois_submit cookie), or 401. Used by the door's registration step
      (routers/portal.py)."""

    not_verified = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
          detail="Please verify your email address first.",
    )
    raw_token = request.cookies.get(settings.submit_cookie_name)
    if not raw_token:
        raise not_verified
    verification = session.exec(
        select(ContactVerification).where(ContactVerification.token_hash == hash_session_token(raw_token))
    ).first()
    now = datetime.utcnow()
    if (
        not verification
        or verification.verified_at is None
        or verification.consumed_at is not None
        or now - verification.verified_at > timedelta(minutes=settings.submit_verification_minutes)
    ):
        raise not_verified
    return verification


def consume_verified_contact(session: Session, verification: ContactVerification, response: Response) -> None:
    verification.consumed_at = datetime.utcnow()
    session.add(verification)
    session.commit()
    response.delete_cookie(settings.submit_cookie_name, samesite="strict")
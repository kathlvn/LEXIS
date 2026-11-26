"""
The one-time sign-in code for the public site.

  POST /access/contact/send-code  -> emails a 6-digit code to the client
  (the code is checked by POST /portal/verify-code, the door, in
  routers/portal.py)

Email only: LEXIS has no SMS sender, so a contact
number can't receive a code. Clients without email are served in person
and encoded by staff as walk-ins.

Before a code is sent, the browser must solve an "I'm not a robot" puzzle
(ALTCHA, self-hosted; core/bot_check.py), so a script can't make LEXIS send
emails by the thousand:
  GET  /access/bot-check/challenge -> a fresh puzzle
  
The old Track Request and Submit Request flows (retired)
that also lived here (Request ID + contact lookup, follow-up check, and the
request-only session). Everything public now goes through the door.
"""
import hmac
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.core.config import settings
from app.core.bot_check import bot_check_passed, new_challenge
from app.core.database import get_session
from app.core.mailer import code_email_available, is_plain_address
from app.core.requester_access import normalize_contact
from app.core.security import generate_verification_code, hash_verification_code
from app.core.notifications import head_attorney_ids, notify, officers_of
from app.core.security_monitoring import get_client_ip, limiter, log_security_event
from app.core.verification import send_request_update, send_requester_access_code
from app.models.models import AuditLogEntry, ContactVerification, LegalServiceRequest
from app.routers.requests import _to_public_out, can_withdraw, lock_request
from app.schemas.schemas import ContactCodeSend, PublicRequestOut

router = APIRouter(prefix="/access", tags=["public-access"])

GENERIC_VERIFY_ERROR = "Invalid or expired code. Please request a new one."
EMAIL_ONLY_MESSAGE = (
    "Please enter an email address. If you don't have one, visit the Legal Office "
    "during office hours and staff will file your request for you."
)
CODE_BUDGET_MESSAGE = (
    "LEXIS can't send more sign-in codes today. Please try again tomorrow, or visit the Legal Office "
    "during office hours."
)
BOT_CHECK_MESSAGE = (
    "We couldn't confirm that you're not a robot. Please try again; if it keeps "
    "happening, reload the page."
)


@router.get("/bot-check/challenge")
@limiter.limit("20/minute")
def bot_check_challenge(request: Request):
    """A fresh "I'm not a robot" puzzle for the sign-in form."""
    return new_challenge()

# ---------------------------------------------------------------------------
# The door's code sender. There's no request behind it to protect, so it can
# give honest "wait a minute" errors.
# ---------------------------------------------------------------------------

@router.post("/contact/send-code")
@limiter.limit("5/minute")
def send_contact_code(
    request: Request,
    payload: ContactCodeSend,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    channel, value = normalize_contact(payload.contact)
    if channel != "email" or not is_plain_address(value):  # plain addresses only
        raise HTTPException(status_code=400, detail=EMAIL_ONLY_MESSAGE)
    # After the cheap email check (a typo doesn't use up the puzzle), before
    # anything is saved or sent.
    if not bot_check_passed(payload.altcha):
        log_security_event(session, "bot_check_failed", "sign-in code request", get_client_ip(request))
        raise HTTPException(status_code=400, detail=BOT_CHECK_MESSAGE)
    now = datetime.utcnow()
    recent = session.exec(
        select(ContactVerification)
        .where(
            ContactVerification.contact_value == value,
            ContactVerification.created_at >= now - timedelta(hours=1),
        )
        .order_by(ContactVerification.created_at.desc())
    ).all()

    if recent and (now - recent[0].created_at).total_seconds() < settings.requester_code_resend_cooldown_seconds:
        raise HTTPException(status_code=429, detail="Please wait a minute before requesting another code.")
    if len(recent) >= settings.requester_code_max_per_hour:
        log_security_event(session, "contact_code_hourly_cap", f"channel={channel}", get_client_ip(request))
        raise HTTPException(status_code=429, detail="Too many codes requested. Please try again later.")

    if not code_email_available():
        # Don't promise a code that won't be sent (MAIL-01).
        raise HTTPException(status_code=503, detail=CODE_BUDGET_MESSAGE)

    for old in recent:
        if old.verified_at is None and old.invalidated_at is None:
            old.invalidated_at = now
            session.add(old)

    code = generate_verification_code()
    session.add(ContactVerification(
        channel=channel,
        contact_value=value,
        code_hash=hash_verification_code(code),
        expires_at=now + timedelta(minutes=settings.requester_code_expire_minutes),
    ))
    session.commit()

    background_tasks.add_task(send_requester_access_code, channel, value, code)
    return {"message": "A verification code has been sent."}


def check_contact_code(session: Session, request: Request, contact: str, code: str) -> ContactVerification:
    """Checks the one-time code sent to a contact (used by the door in
    routers/portal.py). Returns the verification on success; raises
    the same generic error for every failure and locks the code after too
    many wrong tries."""
    _channel, value = normalize_contact(contact)
    now = datetime.utcnow()
    record = session.exec(
        select(ContactVerification)
        .where(
            ContactVerification.contact_value == value,
            ContactVerification.verified_at.is_(None),
            ContactVerification.invalidated_at.is_(None),
            ContactVerification.expires_at > now,
        )
        .order_by(ContactVerification.created_at.desc())
        # Locked until this check commits: guesses sent together are counted
        # one by one, and a right code works only once.
        .with_for_update()
    ).first()
    if not record:
        raise HTTPException(status_code=400, detail=GENERIC_VERIFY_ERROR)
    if record.verified_at is not None or record.invalidated_at is not None:
        # Used or locked by a check that ran while this one waited.
        raise HTTPException(status_code=400, detail=GENERIC_VERIFY_ERROR)

    record.attempts += 1
    if not hmac.compare_digest(record.code_hash, hash_verification_code(code)):
        if record.attempts >= settings.requester_code_max_attempts:
            record.invalidated_at = now
            log_security_event(session, "contact_code_locked", f"channel={record.channel}", get_client_ip(request))
        session.add(record)
        session.commit()
        raise HTTPException(status_code=400, detail=GENERIC_VERIFY_ERROR)

    return record


def withdraw_request(
    session: Session, req: LegalServiceRequest, reason: str | None, background_tasks: BackgroundTasks,
) -> PublicRequestOut:
    """The client withdraws their own request from My Requests
    (routers/portal.py). Allowed until it is Approved.
    It becomes Cancelled with no staff member as the one who closed it; an
    attorney can reopen it."""
    lock_request(req)   # withdraw and approve sent at once: one waits for the other
    if not can_withdraw(session, req):
        raise HTTPException(
            status_code=409,
            detail="This request can no longer be withdrawn. Please message the Legal Office instead.",
        )
    reason = (reason or "").strip()
    now = datetime.utcnow()
    req.is_cancelled = True
    req.cancelled_by_staff_id = None
    req.closed_at = now
    req.closure_reason = ("Withdrawn by the requester: " + reason) if reason else "Withdrawn by the requester."
    req.is_waiting_for_client = False
    req.last_updated_at = now
    session.add(req)
    session.add(AuditLogEntry(
        request_id=req.id,
        actor_type="requester",
        action="request_withdrawn",
        detail=(f'Request withdrawn by the requester. Reason: "{reason}"' if reason
                else "Request withdrawn by the requester."),
    ))
    notify(session, [*officers_of(req), *head_attorney_ids(session)], "withdrawn",
           f"The requester withdrew {req.tracking_id}.", req)
    requester_email = req.requester.email
    session.commit()
    session.refresh(req)
    background_tasks.add_task(
        send_request_update, requester_email, req.tracking_id,
        ["Status: Withdrawn. You withdrew this request. If this was a mistake, contact the Legal Office."],
        is_closed=True,
    )
    return _to_public_out(session, req)


"""
Staff authentication: login, logout, current-user check, and the
email-verification flow that gates a newly created account's first login.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import get_session
from app.core.deps import get_current_staff_user
from app.core.security import (
    verify_password,
    hash_password,
    generate_session_token,
    hash_session_token,
    new_session_expiry,
    hash_verification_code,
)
from app.core.security_monitoring import limiter, log_security_event, check_failed_login_spike, get_client_ip
from app.core.verification import issue_verification_code
from app.core.working_days import PH_TZ
from app.models.models import SecurityEvent, StaffUser, StaffSession, StaffEmailVerificationCode
from app.schemas.schemas import StaffLogin, StaffNewPassword, StaffOut, VerifyEmailRequest, ResendVerificationRequest

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_VERIFICATION_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
# This page has no robot check, so codes per account are capped per hour
# (the code sent when the account is created counts too).
RESEND_MAX_PER_HOUR = 5
# Used only to make a login with an unknown email do the same password work.
_TIMING_HASH = hash_password("lexis-unknown-email-timing-check")


# login.js looks for "choose a new password" in this text to open its
# new-password step, as it does with "verify your email".
NEW_PASSWORD_NEEDED = "Choose a new password to finish logging in."
TEMP_PASSWORD_EXPIRED = (
    "This password from the Head Attorney has expired. Ask the Head Attorney to set a new temporary password."
)


def _refuse_expired_temp_password(session: Session, request: Request, staff: StaffUser) -> None:
    """A password the Head Attorney chose works for 72 hours.
    Called only after the password was checked, so it tells nothing to
    someone guessing."""
    expires = staff.temp_password_expires_at
    if staff.must_change_password and expires and expires <= datetime.utcnow():
        log_security_event(
            session, "temp_password_expired",
            f"{staff.full_name} tried to sign in with an expired temporary password.",
            get_client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=TEMP_PASSWORD_EXPIRED)


def _check_password(session: Session, request: Request, email: str, password: str) -> StaffUser:
    """Email + password check shared by /login and /new-password: the
    lockout, the failed-attempt count and the Security Log entries are the
    same for both, so a guessed temporary password is never easier to try."""
    generic_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password."
    )
    email = email.strip().lower()

    staff = session.exec(select(StaffUser).where(StaffUser.email == email)).first()
    if not staff:
        # Check against a throwaway hash so an unknown email takes as long to
        # answer as a real one.
        verify_password(password, _TIMING_HASH)
        log_security_event(session, "failed_login", f"Unknown email attempted: {email}.", get_client_ip(request))
        check_failed_login_spike(session, get_client_ip(request))
        raise generic_error

    # Locked until this attempt is saved, so wrong passwords sent together
    # are all counted and the lock comes on at the limit.
    session.refresh(staff, with_for_update=True)
    password_ok = verify_password(password, staff.hashed_password)

    if staff.locked_until and staff.locked_until > datetime.utcnow():
        if not password_ok:
            # During a lock a wrong password gets the usual answer, so the lock
            # tells no one that this account exists. It is logged but
            # not counted, so the lock isn't made longer.
            log_security_event(session, "failed_login", f"Wrong password for {email} (locked).", get_client_ip(request))
            check_failed_login_spike(session, get_client_ip(request))
            raise generic_error
        # The right password: the staff member is told until when.
        # Stored in UTC; the message shows Philippine time.
        until = staff.locked_until.replace(tzinfo=timezone.utc).astimezone(PH_TZ)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Too many failed attempts. Try again after {until.strftime('%I:%M %p')}.",
        )

    if staff.locked_until:
        # The lock has ended: start the count again, so one wrong try doesn't
        # lock the account for another full period.
        staff.failed_login_attempts = 0
        staff.locked_until = None

    if not password_ok:
        staff.failed_login_attempts += 1
        if staff.failed_login_attempts >= settings.max_failed_login_attempts:
            staff.locked_until = datetime.utcnow() + timedelta(minutes=settings.lockout_minutes)
        session.add(staff)
        session.commit()
        log_security_event(session, "failed_login", f"Wrong password for {email}.", get_client_ip(request))
        check_failed_login_spike(session, get_client_ip(request))
        raise generic_error

    staff.failed_login_attempts = 0
    staff.locked_until = None
    session.add(staff)
    session.commit()

    if not staff.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in. Check your ESSU inbox for the code.",
        )
    if not staff.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated.")
    return staff


def _start_session(session: Session, request: Request, staff: StaffUser, response: Response) -> None:
    raw_token = generate_session_token()
    session.add(StaffSession(
        staff_id=staff.id,
        token_hash=hash_session_token(raw_token),
        expires_at=new_session_expiry(),
    ))
    session.commit()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_expire_minutes * 60,
    )
    # Security Log: every staff sign-in, not only the failed ones.
    log_security_event(session, "staff_login", f"{staff.full_name} signed in.", get_client_ip(request))


@router.post("/login", response_model=StaffOut)
@limiter.limit("5/minute")
def login(request: Request, payload: StaffLogin, response: Response, session: Session = Depends(get_session)):
    staff = _check_password(session, request, payload.email, payload.password)
    # The Head Attorney chose this password (new account or temporary
    # password), so no session starts until the staff member picks their own.
    if staff.must_change_password:
        _refuse_expired_temp_password(session, request, staff)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NEW_PASSWORD_NEEDED)
    _start_session(session, request, staff, response)
    return staff


@router.post("/new-password", response_model=StaffOut)
@limiter.limit("5/minute")
def choose_new_password(
    request: Request, payload: StaffNewPassword, response: Response, session: Session = Depends(get_session)
):
    """The step after a login with a password the Head Attorney chose. The
    given password is checked again here (same lockout as /login), then the
    new one replaces it and the session starts."""
    staff = _check_password(session, request, payload.email, payload.password)
    if not staff.must_change_password:
        raise HTTPException(status_code=400, detail="This account doesn't need a new password. Log in as usual.")
    _refuse_expired_temp_password(session, request, staff)
    if payload.new_password == payload.password:
        raise HTTPException(status_code=400, detail="Choose a password different from the one you were given.")

    staff.hashed_password = hash_password(payload.new_password)
    staff.must_change_password = False
    staff.temp_password_expires_at = None
    session.add(staff)
    session.commit()

    # Security Log (Head Attorney only). The name, never the password.
    log_security_event(
        session, "staff_password_changed",
        f"{staff.full_name} chose a new password.",
        get_client_ip(request),
    )
    _start_session(session, request, staff, response)
    session.refresh(staff)
    return staff


@router.post("/logout")
@limiter.limit("30/minute")
def logout(request: Request, response: Response, session: Session = Depends(get_session)):
    # Reads the cookie directly instead of via get_current_staff_user, so
    # logging out never fails just because the session already expired.
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        token_hash = hash_session_token(raw_token)
        staff_session = session.exec(
            select(StaffSession).where(StaffSession.token_hash == token_hash)
        ).first()
        if staff_session and staff_session.revoked_at is None:
            staff_session.revoked_at = datetime.utcnow()
            session.add(staff_session)
            session.commit()
            staff = session.get(StaffUser, staff_session.staff_id)
            if staff:
                # Security Log. Only when a live session ended, so a
                # repeated sign-out click isn't logged twice.
                log_security_event(session, "staff_logout", f"{staff.full_name} signed out.", get_client_ip(request))

    response.delete_cookie(settings.session_cookie_name)
    return {"detail": "Logged out."}


@router.get("/me", response_model=StaffOut)
@limiter.limit("120/minute")
def me(request: Request, staff: StaffUser = Depends(get_current_staff_user)):
    return staff


@router.post("/verify-email")
@limiter.limit("5/minute")
def verify_email(request: Request,payload: VerifyEmailRequest, session: Session = Depends(get_session)):
    # One answer for an unknown email, an account already verified, a wrong
    # code and an expired one, and no account details on success: this
    # endpoint must not tell anyone which staff accounts exist.
    not_right = HTTPException(status_code=400, detail="That code is not right or has expired. Request a new one.")
    staff = session.exec(
        select(StaffUser).where(StaffUser.email == payload.email.strip().lower())
    ).first()
    if not staff or staff.is_email_verified:
        raise not_right

    code_row = session.exec(
        select(StaffEmailVerificationCode)
        .where(
            StaffEmailVerificationCode.staff_id == staff.id,
            StaffEmailVerificationCode.used_at.is_(None),
        )
        .order_by(StaffEmailVerificationCode.created_at.desc())
        .with_for_update()   # guesses sent together all count
    ).first()

    if not code_row or code_row.expires_at < datetime.utcnow():
        raise not_right
    if code_row.attempts >= MAX_VERIFICATION_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many incorrect attempts. Request a new code.")

    if hash_verification_code(payload.code.strip()) != code_row.code_hash:
        code_row.attempts += 1
        session.add(code_row)
        session.commit()
        raise not_right

    code_row.used_at = datetime.utcnow()
    staff.is_email_verified = True
    session.add(code_row)
    session.add(staff)
    session.commit()
    return {"detail": "Email verified. You can now log in."}


@router.post("/resend-verification")
@limiter.limit("5/minute")
def resend_verification(request: Request, payload: ResendVerificationRequest, session: Session = Depends(get_session)):
    generic_response: dict = {"detail": "If an account needs verification, a new code has been sent."}

    staff = session.exec(
        select(StaffUser).where(StaffUser.email == payload.email.strip().lower())
    ).first()
    # Deliberately vague on purpose — don't reveal whether this email
    # exists or is already verified.
    if not staff or staff.is_email_verified:
        return generic_response

    last_code = session.exec(
        select(StaffEmailVerificationCode)
        .where(StaffEmailVerificationCode.staff_id == staff.id)
        .order_by(StaffEmailVerificationCode.created_at.desc())
    ).first()
    if last_code and (datetime.utcnow() - last_code.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
        return generic_response  # absorb rapid resend spam silently
    sent_last_hour = session.exec(
        select(StaffEmailVerificationCode).where(
            StaffEmailVerificationCode.staff_id == staff.id,
            StaffEmailVerificationCode.created_at >= datetime.utcnow() - timedelta(hours=1),
        )
    ).all()
    if len(sent_last_hour) >= RESEND_MAX_PER_HOUR:
        detail = f"Staff email codes for {staff.email}."
        already_logged = session.exec(
            select(SecurityEvent).where(
                SecurityEvent.event_type == "staff_code_hourly_cap",
                SecurityEvent.detail == detail,
                SecurityEvent.created_at >= datetime.utcnow() - timedelta(hours=1),
            )
        ).first()
        if not already_logged:  # once an hour, not on every refused try
            log_security_event(session, "staff_code_hourly_cap", detail, get_client_ip(request))
        return generic_response

    code = issue_verification_code(session, staff)
    if settings.environment == "development":
        generic_response["dev_code"] = code  # DEV ONLY — delete this line once real email is wired up
    return generic_response
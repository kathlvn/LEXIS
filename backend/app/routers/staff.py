"""
Head-Attorney-only staff account management: create, list, and modify
Legal Office personnel accounts. There is no self-service signup — every
account originates from a Head Attorney creating one (except the very
first Head Attorney account, which is bootstrapped directly via the seed
script — see Step 10 — since the API itself has nobody to authorize it).
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import get_session
from app.core.deps import get_current_staff_user, require_head_attorney
from app.core.security import TEMP_PASSWORD_HOURS, hash_password, password_problem
from app.core.verification import issue_verification_code
from app.models.models import LegalServiceRequest, StaffSession, StaffUser, WorkflowStepTemplate
from app.core.security_monitoring import get_client_ip, limiter, log_security_event
from app.schemas.schemas import (
    StaffCreate, StaffCreateOut, StaffDirectoryEntry, StaffOut, StaffPasswordSet, StaffUpdate,
)

router = APIRouter(prefix="/staff", tags=["staff"])

ROLE_LABELS = {"legal_assistant": "Legal Assistant", "attorney": "Attorney", "head_attorney": "Head Attorney"}


def _role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def _ensure_not_last_head_attorney(
    session: Session, staff: StaffUser, new_role: str | None, new_is_active: bool | None
):
    """Blocks a change that would leave zero active Head Attorney accounts
    — otherwise nobody could ever manage staff again."""
    was_active_head = staff.role == "head_attorney" and staff.is_active
    if not was_active_head:
        return

    will_stay_active_head = (
        (new_role if new_role is not None else staff.role) == "head_attorney"
        and (new_is_active if new_is_active is not None else staff.is_active)
    )
    if will_stay_active_head:
        return

    other_active_head_attorney = session.exec(
        select(StaffUser).where(
            StaffUser.role == "head_attorney",
            StaffUser.is_active.is_(True),
            StaffUser.id != staff.id,
        )
    ).first()
    if not other_active_head_attorney:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove or deactivate the last Head Attorney account.",
        )


def _open_request_count(session: Session, staff_id: int) -> int:
    """Requests assigned to this staff member that are still open (not
    completed, cancelled or rejected)."""
    req, step = LegalServiceRequest, WorkflowStepTemplate
    return session.exec(
        select(func.count()).select_from(req)
        .outerjoin(step, step.id == req.current_step_id)
        .where(
            req.assigned_officer_id == staff_id,
            req.is_cancelled == False,  # noqa: E712
            req.is_rejected == False,  # noqa: E712
            or_(step.status_name.is_(None), step.status_name != "Completed"),
        )
    ).one()


def _ensure_no_open_requests(session: Session, staff: StaffUser) -> None:
    """Switching off an account that still has open requests would leave
    them with nobody: they aren't in Unassigned, and a client's message
    would notify no one. The Head Attorney reassigns them first.
    user-management.js looks for "open request" in this text to add a link
    to the person's requests."""
    count = _open_request_count(session, staff.id)
    if count:
        noun = "open request" if count == 1 else "open requests"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"{staff.full_name} still has {count} {noun}. Reassign "
                    f"{'it' if count == 1 else 'them'} to someone else first, then deactivate the account."),
        )


@router.post("", response_model=StaffCreateOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def create_staff(
    request: Request,
    payload: StaffCreate,
    session: Session = Depends(get_session),
    current: StaffUser = Depends(require_head_attorney),
):
    existing = session.exec(select(StaffUser).where(StaffUser.email == payload.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    staff = StaffUser(
        full_name=payload.full_name.strip(),
        email=payload.email,  # already lowercased + domain-validated by the schema
        hashed_password=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        is_email_verified=False,
        must_change_password=True,  # the Head Attorney chose it; they pick their own at first login
        temp_password_expires_at=datetime.utcnow() + timedelta(hours=TEMP_PASSWORD_HOURS),
    )
    session.add(staff)
    session.commit()
    session.refresh(staff)

    code = issue_verification_code(session, staff)

    # Security Log (Head Attorney only). Names and role, never the password.
    log_security_event(
        session, "staff_account_created",
        f"{current.full_name} created a {_role_label(staff.role)} account for {staff.full_name}.",
        get_client_ip(request),
    )
    session.refresh(staff)

    result = StaffCreateOut.model_validate(staff)
    if settings.environment == "development":
        result.dev_verification_code = code
    return result


@router.get("", response_model=list[StaffOut])
@limiter.limit("120/minute")
def list_staff(
    request: Request,
    session: Session = Depends(get_session),
    _current: StaffUser = Depends(require_head_attorney),
):
    return session.exec(select(StaffUser).order_by(StaffUser.full_name)).all()


@router.get("/directory", response_model=list[StaffDirectoryEntry])
@limiter.limit("120/minute")
def staff_directory(
    request: Request,
    session: Session = Depends(get_session),
    _current: StaffUser = Depends(get_current_staff_user),
):
    """Names only, for the "Assigned officer" filter on the request lists.
    Any signed-in staff member; no emails or account details."""
    staff = session.exec(select(StaffUser).order_by(StaffUser.full_name)).all()
    return [StaffDirectoryEntry(id=s.id, full_name=s.full_name, role=s.role, is_active=s.is_active) for s in staff]


@router.get("/{staff_id}", response_model=StaffOut)
@limiter.limit("120/minute")
def get_staff(
    request: Request,
    staff_id: int,
    session: Session = Depends(get_session),
    _current: StaffUser = Depends(require_head_attorney),
):
    staff = session.get(StaffUser, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Staff account not found.")
    return staff


@router.patch("/{staff_id}", response_model=StaffOut)
@limiter.limit("30/minute")
def update_staff(
    request: Request,
    staff_id: int,
    payload: StaffUpdate,
    session: Session = Depends(get_session),
    current: StaffUser = Depends(require_head_attorney),
):
    staff = session.get(StaffUser, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Staff account not found.")

    _ensure_not_last_head_attorney(session, staff, payload.role, payload.is_active)
    if payload.is_active is False and staff.is_active:
        _ensure_no_open_requests(session, staff)

    old_name = staff.full_name
    changes = []
    if payload.full_name is not None and payload.full_name.strip() != staff.full_name:
        changes.append(f"name changed from {staff.full_name} to {payload.full_name.strip()}")
        staff.full_name = payload.full_name.strip()
    if payload.role is not None and payload.role != staff.role:
        changes.append(f"role changed from {_role_label(staff.role)} to {_role_label(payload.role)}")
        staff.role = payload.role
    if payload.is_active is not None and payload.is_active != staff.is_active:
        changes.append("account switched on" if payload.is_active else "account switched off")
        staff.is_active = payload.is_active
        if not payload.is_active:
            # End the open sessions now, so switching the account back on
            # doesn't bring an old session back to life.
            now = datetime.utcnow()
            for s in session.exec(
                select(StaffSession).where(StaffSession.staff_id == staff.id, StaffSession.revoked_at.is_(None))
            ).all():
                s.revoked_at = now
                session.add(s)

    session.add(staff)
    session.commit()

    if changes:
        log_security_event(
            session, "staff_account_changed",
            f"{current.full_name} changed the account of {old_name}: {'; '.join(changes)}.",
            get_client_ip(request),
        )
    session.refresh(staff)
    return staff


@router.post("/{staff_id}/password", response_model=StaffOut)
@limiter.limit("10/minute")
def set_temporary_password(
    request: Request,
    staff_id: int,
    payload: StaffPasswordSet,
    session: Session = Depends(get_session),
    current: StaffUser = Depends(require_head_attorney),
):
    """For a staff member who forgot their password: the Head Attorney sets
    a temporary one and tells them in person. Their open sessions end, a
    login lockout is cleared, and they must choose a new password at their
    next login (POST /auth/new-password)."""
    staff = session.get(StaffUser, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Staff account not found.")
    if staff.id == current.id:
        raise HTTPException(status_code=400, detail="You can't set a temporary password for your own account here.")
    # The schema checked length and common words; their name and email are
    # known only here.
    problem = password_problem(payload.password, staff.email, staff.full_name)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    staff.hashed_password = hash_password(payload.password)
    staff.must_change_password = True
    staff.temp_password_expires_at = datetime.utcnow() + timedelta(hours=TEMP_PASSWORD_HOURS)
    staff.failed_login_attempts = 0
    staff.locked_until = None
    now = datetime.utcnow()
    open_sessions = session.exec(
        select(StaffSession).where(StaffSession.staff_id == staff.id, StaffSession.revoked_at.is_(None))
    ).all()
    for s in open_sessions:
        s.revoked_at = now
        session.add(s)
    session.add(staff)
    session.commit()

    log_security_event(
        session, "staff_password_set",
        f"{current.full_name} set a temporary password for {staff.full_name}.",
        get_client_ip(request),
    )
    session.refresh(staff)
    return staff
"""
Auth dependencies: read the session cookie, resolve it to a live
StaffSession + StaffUser, and gate endpoints by role.

Usage in a router:
    @router.get("/something")
    def handler(staff: StaffUser = Depends(get_current_staff_user)):
        ...

    @router.post("/staff")
    def create_staff(staff: StaffUser = Depends(require_head_attorney)):
        ...
"""
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import get_session
from app.core.security import hash_session_token
from app.models.models import StaffSession, StaffUser


def get_current_staff_user(
    request: Request,
    session: Session = Depends(get_session),
) -> StaffUser:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in.")

    token_hash = hash_session_token(raw_token)
    staff_session = session.exec(
        select(StaffSession).where(StaffSession.token_hash == token_hash)
    ).first()

    if not staff_session or staff_session.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid.")

    now = datetime.utcnow()
    if staff_session.expires_at < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session has expired.")

    # Idle timeout: no activity for session_idle_minutes ends
    # the session for good — it is revoked, not just refused, so the same
    # cookie can't come back to life. Same rule as the requester side.
    if staff_session.last_seen_at < now - timedelta(minutes=settings.session_idle_minutes):
        staff_session.revoked_at = now
        session.add(staff_session)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signed out after inactivity.",
        )

    staff = session.get(StaffUser, staff_session.staff_id)
    if not staff or not staff.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive.")

    # Activity marker for the idle timeout (it does NOT extend the 12-hour
    # hard limit). Written at most once a minute, so a page that makes
    # several calls at once doesn't cause a database write for each one.
    if (now - staff_session.last_seen_at).total_seconds() >= 60:
        staff_session.last_seen_at = now
        session.add(staff_session)
        session.commit()

    return staff


def require_head_attorney(
    staff: StaffUser = Depends(get_current_staff_user),
) -> StaffUser:
    if staff.role != "head_attorney":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Head Attorney can perform this action.",
        )
    return staff
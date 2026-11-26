"""
The bell in the staff console. Each staff member only ever sees their own
notifications. The console asks for the unread count about once a minute
(only while the tab is open), so that call is kept small and rate-limited.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, delete, func, select

from app.core.database import get_session
from app.core.deps import get_current_staff_user
from app.core.security_monitoring import limiter
from app.models.models import LegalServiceRequest, StaffNotification, StaffUser

router = APIRouter(prefix="/notifications", tags=["notifications"])

KEEP_READ_DAYS = 60   # read notifications older than this are cleared away


def _unread_count(session: Session, staff_id: int) -> int:
    return session.exec(
        select(func.count()).select_from(StaffNotification).where(
            StaffNotification.staff_id == staff_id, StaffNotification.read_at.is_(None))
    ).one()


@router.get("/unread-count")
@limiter.limit("120/minute")
def unread_count(
    request: Request,
    session: Session = Depends(get_session),
    staff: StaffUser = Depends(get_current_staff_user),
):
    return {"count": _unread_count(session, staff.id)}


@router.get("")
@limiter.limit("60/minute")
def list_notifications(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    session: Session = Depends(get_session),
    staff: StaffUser = Depends(get_current_staff_user),
):
    # Housekeeping: old, already-read notifications are removed.
    session.exec(delete(StaffNotification).where(
        StaffNotification.staff_id == staff.id,
        StaffNotification.read_at.is_not(None),
        StaffNotification.created_at < datetime.utcnow() - timedelta(days=KEEP_READ_DAYS),
    ))
    session.commit()
    rows = session.exec(
        select(StaffNotification, LegalServiceRequest.tracking_id)
        .join(LegalServiceRequest, StaffNotification.request_id == LegalServiceRequest.id, isouter=True)
        .where(StaffNotification.staff_id == staff.id)
        .order_by(StaffNotification.created_at.desc(), StaffNotification.id.desc())
        .limit(limit)
    ).all()
    return {
        "unread_count": _unread_count(session, staff.id),
        "items": [
            {"id": n.id, "kind": n.kind, "message": n.message, "tracking_id": tracking_id,
             "created_at": n.created_at, "read": n.read_at is not None}
            for n, tracking_id in rows
        ],
    }


@router.post("/{notification_id}/read")
@limiter.limit("120/minute")
def mark_read(
    request: Request,
    notification_id: int,
    session: Session = Depends(get_session),
    staff: StaffUser = Depends(get_current_staff_user),
):
    n = session.get(StaffNotification, notification_id)
    if not n or n.staff_id != staff.id:          # someone else's: same answer as missing
        raise HTTPException(status_code=404, detail="Notification not found.")
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        session.add(n)
        session.commit()
    return {"ok": True, "unread_count": _unread_count(session, staff.id)}


@router.post("/read-all")
@limiter.limit("30/minute")
def mark_all_read(
    request: Request,
    session: Session = Depends(get_session),
    staff: StaffUser = Depends(get_current_staff_user),
):
    for n in session.exec(select(StaffNotification).where(
            StaffNotification.staff_id == staff.id, StaffNotification.read_at.is_(None))).all():
        n.read_at = datetime.utcnow()
        session.add(n)
    session.commit()
    return {"ok": True, "unread_count": 0}

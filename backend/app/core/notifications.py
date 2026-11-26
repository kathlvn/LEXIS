"""
Staff notifications (the bell in the staff console). In-LEXIS only: staff
are not emailed. Who hears what:
- a legal assistant hears when a request is assigned to them, when the
  requester replies or uploads, and when their work is returned, approved,
  completed, cancelled, rejected, reopened, or withdrawn;
- attorneys hear when work is forwarded for review;
- the Head Attorney also hears about reassignment requests, requests the
  requester withdrew, and unusual security activity.

notify() only adds rows; the caller's own commit saves them together with
the action, so a notification is never saved for an action that failed.
The person who did the action is never notified about it.
"""
from typing import Iterable

from sqlmodel import Session, select

from app.models.models import LegalServiceRequest, StaffNotification, StaffUser

ATTORNEY_ROLES = ("attorney", "head_attorney")


def notify(session: Session, staff_ids: Iterable[int | None], kind: str, message: str,
           request: LegalServiceRequest | None = None, actor_id: int | None = None) -> None:
    ids = {i for i in staff_ids if i and i != actor_id}
    if not ids:
        return
    active = session.exec(
        select(StaffUser.id).where(StaffUser.id.in_(ids), StaffUser.is_active == True)  # noqa: E712
    ).all()
    for staff_id in active:
        session.add(StaffNotification(
            staff_id=staff_id,
            request_id=request.id if request else None,
            kind=kind[:40],
            message=message[:300],
        ))


def attorney_ids(session: Session) -> list[int]:
    return list(session.exec(
        select(StaffUser.id).where(StaffUser.role.in_(ATTORNEY_ROLES), StaffUser.is_active == True)  # noqa: E712
    ).all())


def head_attorney_ids(session: Session) -> list[int]:
    return list(session.exec(
        select(StaffUser.id).where(StaffUser.role == "head_attorney", StaffUser.is_active == True)  # noqa: E712
    ).all())


def officers_of(req: LegalServiceRequest) -> list[int | None]:
    """The assistant handling the request, and the one who prepared the work
    (they differ after a reassignment)."""
    return [req.assigned_officer_id, req.prepared_by_officer_id]

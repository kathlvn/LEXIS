"""
Security Log — Head Attorney only, read-only.

Shows what LEXIS records in SecurityEvent: failed staff logins, rate-limit
blocks, locked verification codes, email limits, records exports. The
summary marks an event type "unusual" while its count is at or above the
alert threshold in ALERT_RULES (core/security_monitoring.py), the same rule
that sends the Head Attorney a bell notification.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlmodel import Session, func, select

from app.core.database import get_session
from app.core.deps import require_head_attorney
from app.core.security_monitoring import ALERT_RULES, limiter
from app.models.models import SecurityEvent, StaffUser

router = APIRouter(prefix="/security-events", tags=["security-log"])

EVENT_LABELS = {
    "failed_login": "Failed staff login",
    "failed_login_spike": "Repeated failed logins",
    "rate_limit_exceeded": "Blocked by rate limit",
    # Old Track Request events (retired); kept so older log rows still read well.
    "access_code_no_match": "Old Track Request: details didn't match",
    "access_code_locked": "Old Track Request: code locked",
    "access_code_hourly_cap": "Old Track Request: code limit reached",
    "bot_check_failed": "Sign-in: robot check failed",
    "code_email_limit_reached": "Daily sign-in code email limit reached",
    "contact_code_locked": "Sign-in: code locked",
    "contact_code_hourly_cap": "Sign-in: code limit reached",
    "staff_code_hourly_cap": "Staff email code: limit reached",
    "cross_site_refused": "Change from another website refused",
    "email_daily_limit_reached": "Daily email limit reached",
    "records_exported": "Records archive exported",
    "setting_changed": "Setting changed (Settings page)",
    "article_published": "Article published",
    "article_hidden": "Article hidden",
    "article_edited": "Published or hidden article edited",
    "office_added": "Office added",
    "office_changed": "Office changed",
    "service_added": "Service added",
    "service_changed": "Service changed",
    "holiday_added": "Holiday added",
    "holiday_removed": "Holiday removed",
    "holiday_import_checked": "Holidays read from the Official Gazette",
    "holiday_import_failed": "Holiday proclamation couldn't be read",
    "staff_account_created": "Staff account created",
    "staff_account_changed": "Staff account changed",
    "staff_password_set": "Temporary password set for a staff member",
    "staff_password_changed": "Staff member chose their own password",
    "temp_password_expired": "Expired temporary password used",
    "staff_login": "Staff signed in",
    "staff_logout": "Staff signed out",
}


@router.get("/summary")
@limiter.limit("30/minute")
def security_summary(
    request: Request,
    session: Session = Depends(get_session),
    _staff: StaffUser = Depends(require_head_attorney),
):
    now = datetime.utcnow()

    def counts_since(since):
        return dict(session.exec(
            select(SecurityEvent.event_type, func.count())
            .where(SecurityEvent.created_at >= since)
            .group_by(SecurityEvent.event_type)
        ).all())

    last_day = counts_since(now - timedelta(days=1))
    last_week = counts_since(now - timedelta(days=7))
    rows = []
    for event_type in sorted(set(last_week) | set(ALERT_RULES)):
        rule = ALERT_RULES.get(event_type)
        in_window = None
        if rule:
            in_window = session.exec(
                select(func.count()).select_from(SecurityEvent).where(
                    SecurityEvent.event_type == event_type,
                    SecurityEvent.created_at >= now - timedelta(minutes=rule[1]),
                )
            ).one()
        rows.append({
            "event_type": event_type,
            "label": EVENT_LABELS.get(event_type, event_type.replace("_", " ").capitalize()),
            "last_24h": last_day.get(event_type, 0),
            "last_7d": last_week.get(event_type, 0),
            "threshold": rule[0] if rule else None,
            "window_minutes": rule[1] if rule else None,
            "meaning": rule[2] if rule else None,
            "unusual": bool(rule and in_window >= rule[0]),
        })
    rows.sort(key=lambda r: (not r["unusual"], -r["last_7d"], r["label"]))
    return {"generated_at": now, "types": rows}


@router.get("")
@limiter.limit("30/minute")
def list_security_events(
    request: Request,
    response: Response,
    event_type: str | None = Query(None, max_length=50),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
    session: Session = Depends(get_session),
    _staff: StaffUser = Depends(require_head_attorney),
):
    stmt = select(SecurityEvent).where(SecurityEvent.created_at >= datetime.utcnow() - timedelta(days=days))
    if event_type:
        stmt = stmt.where(SecurityEvent.event_type == event_type)
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    response.headers["X-Total-Count"] = str(total)
    events = session.exec(
        stmt.order_by(SecurityEvent.created_at.desc(), SecurityEvent.id.desc()).offset(offset).limit(limit)
    ).all()
    return [
        {"id": e.id, "event_type": e.event_type,
         "label": EVENT_LABELS.get(e.event_type, e.event_type.replace("_", " ").capitalize()),
         "detail": e.detail, "source_ip": e.source_ip, "created_at": e.created_at}
        for e in events
    ]

"""
Settings page: the office-editable settings in the SystemSetting
table, so the Legal Office can change them after turnover without a
programmer. Head Attorney only.

Each setting has safe limits, so a typo can't lock clients out (for
example, "open requests while not yet verified" can't be set to 0).
Every change records who made it and when (on the setting, and as a
"setting changed" entry in the Security Log).

Defaults live in seed.DEFAULT_SETTINGS; "Reset to default" on the page
simply saves that value.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import require_head_attorney
from app.core.security_monitoring import get_client_ip, limiter, log_security_event
from app.models.models import StaffUser, SystemSetting
from app.schemas.schemas import SettingOut, SettingUpdate
from app.seed import DEFAULT_SETTINGS

router = APIRouter(prefix="/settings", tags=["settings"])

# key: (label, help, kind, min, max)   kind "int" = whole number, "text" = free text
SETTING_DEFS = {
    "verification_valid_days": (
        "How long a verification lasts",
        "After this many days a verified client must be verified again. Applies to verifications made from now on; "
        "clients verified earlier keep their end date.",
        "int", 30, 1095),
    "unverified_max_open_requests": (
        "Open requests while not yet verified",
        "How many open requests a client may have before the Legal Office verifies them. "
        "At least 1, so new clients can always send their first request with the endorsement letter.",
        "int", 1, 5),
    "max_requests_per_day": (
        "New requests per client per day",
        "Limits floods of requests from one client in 24 hours. Applies to every client, verified or not.",
        "int", 1, 20),
    "endorsement_template": (
        "Endorsement letter template",
        "The wording ESSU clients download and copy onto their office's letterhead. Plain text only. "
        "Leave it empty to hide the download link.",
        "text", 0, 5000),
}


def _out(session: Session, key: str) -> SettingOut:
    label, help_text, kind, low, high = SETTING_DEFS[key]
    row = session.get(SystemSetting, key)
    who = session.get(StaffUser, row.updated_by_staff_id) if row and row.updated_by_staff_id else None
    return SettingOut(
        key=key, label=label, help=help_text, kind=kind,
        value=row.value if row else DEFAULT_SETTINGS.get(key, ""),
        default=DEFAULT_SETTINGS.get(key, ""),
        min=low, max=high,
        updated_at=row.updated_at if row and row.updated_by_staff_id else None,
        updated_by_name=who.full_name if who else None,
    )


@router.get("", response_model=list[SettingOut])
@limiter.limit("60/minute")
def list_settings(request: Request, session: Session = Depends(get_session),
                  _staff: StaffUser = Depends(require_head_attorney)):
    return [_out(session, key) for key in SETTING_DEFS]


@router.put("/{key}", response_model=SettingOut)
@limiter.limit("30/minute")
def update_setting(request: Request, key: str, payload: SettingUpdate, session: Session = Depends(get_session),
                   staff: StaffUser = Depends(require_head_attorney)):
    if key not in SETTING_DEFS:
        raise HTTPException(status_code=404, detail="Unknown setting.")
    label, _help, kind, low, high = SETTING_DEFS[key]
    if kind == "int":
        raw = payload.value.strip()
        # Plain 0-9 only: isdigit() alone also accepts "²", which int() can't read.
        if not (raw.isascii() and raw.isdigit()) or not (low <= int(raw) <= high):
            raise HTTPException(status_code=400, detail=f"Enter a whole number from {low} to {high}.")
        value = str(int(raw))
    else:
        # Plain text: Windows line endings unified, trailing spaces removed.
        value = "\n".join(line.rstrip() for line in payload.value.replace("\r\n", "\n").split("\n")).strip()
        if len(value) > high:
            raise HTTPException(status_code=400, detail=f"Keep it under {high} characters.")

    row = session.get(SystemSetting, key)
    old = row.value if row else DEFAULT_SETTINGS.get(key, "")
    if row is None:
        row = SystemSetting(key=key, value=value)
    row.value = value
    row.updated_by_staff_id = staff.id
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    if old != value:
        change = f"{old} -> {value}" if kind == "int" else "wording edited"
        log_security_event(session, "setting_changed", f"{label}: {change} (by {staff.full_name})", get_client_ip(request))
    return _out(session, key)
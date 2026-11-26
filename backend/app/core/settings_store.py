"""
Office-editable settings: read from the SystemSetting table,
which the Head Attorney edits. A missing or unreadable value falls back to
the default given by the caller, so a bad edit can never break a page.
"""
from sqlmodel import Session

from app.models.models import SystemSetting


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(SystemSetting, key)
    return row.value if row else default


def get_int_setting(session: Session, key: str, default: int) -> int:
    try:
        return int(get_setting(session, key, str(default)).strip())
    except (TypeError, ValueError):
        return default
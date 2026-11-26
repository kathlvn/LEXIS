"""
Working-day arithmetic for due dates and deadline monitoring.

Rules (agreed with the Legal Office):
- Processing periods are counted in WORKING days (RA 11032): weekends and
  the dates in the Holiday table are skipped.
- Counting starts on the next working day after submission, so a request
  sent on a Saturday or a holiday effectively starts on the next working day.
- A request is due at the END of its last working day, Philippine time.
- "Approaching" = due today or on the next working day; "overdue" = past the
  due date. Finished requests (Completed, cancelled, rejected) are never
  flagged. "Waiting for Client" does NOT pause the clock.

Dates in the database are naive UTC (datetime.utcnow()), like everywhere
else in LEXIS. The Philippines has no daylight saving time, so a fixed +8
offset is exact and needs no time-zone database (which Windows lacks).
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlmodel import Session, select

from app.models.models import Holiday

PH_TZ = timezone(timedelta(hours=8))
# Safety cap so a bad value (e.g. a huge processing period) can never loop
# forever. 3,660 calendar days = about 10 years.
MAX_DAYS_TO_SCAN = 3660


def load_holiday_dates(session: Session) -> set[date]:
    """All non-working dates. The table holds a few dozen rows a year, so it
    is read once per API call rather than per request."""
    return set(session.exec(select(Holiday.holiday_date)).all())


def to_ph_date(utc_naive: datetime) -> date:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(PH_TZ).date()


def is_working_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def next_working_day(d: date, holidays: set[date]) -> date:
    for _ in range(MAX_DAYS_TO_SCAN):
        d += timedelta(days=1)
        if is_working_day(d, holidays):
            return d
    raise ValueError("No working day found within the scan limit.")


def add_working_days(start: date, days: int, holidays: set[date]) -> date:
    """The date `days` working days after `start` (start itself not counted)."""
    d = start
    for _ in range(days):
        d = next_working_day(d, holidays)
    return d


def working_days_between(start: date, end: date, holidays: set[date]) -> int:
    """Working days after `start` up to and including `end` — counted the
    same way as due dates, so "completed in N working days" and "due in N
    working days" mean the same thing. 0 if finished on the same day."""
    if end <= start:
        return 0
    count, d = 0, start
    for _ in range(MAX_DAYS_TO_SCAN):
        d += timedelta(days=1)
        if d > end:
            break
        if is_working_day(d, holidays):
            count += 1
    return count


def compute_due_date(submitted_at_utc: datetime, processing_days: int | None,
                     holidays: set[date]) -> datetime | None:
    """End of the last working day (23:59:59 Philippine time), as naive UTC.
    None when the service has no set processing period."""
    if not processing_days or processing_days <= 0:
        return None
    due_day = add_working_days(to_ph_date(submitted_at_utc), processing_days, holidays)
    end_of_day = datetime.combine(due_day, time(23, 59, 59), tzinfo=PH_TZ)
    return end_of_day.astimezone(timezone.utc).replace(tzinfo=None)


def deadline_status(due_date_utc: datetime | None, is_finished: bool,
                    holidays: set[date], now_utc: datetime | None = None) -> str | None:
    """'overdue' | 'approaching' | 'on_track', or None when there is no due
    date or the request is finished."""
    if is_finished or due_date_utc is None:
        return None
    now_utc = now_utc or datetime.utcnow()
    if now_utc > due_date_utc:
        return "overdue"
    today = to_ph_date(now_utc)
    due_day = to_ph_date(due_date_utc)
    if due_day == today or due_day == next_working_day(today, holidays):
        return "approaching"
    return "on_track"

"""
Holidays list: the non-working days that due-date counting
skips, together with weekends (working days under RA 11032).

- Every staff member can view the list, so anyone can see why a due date
  fell where it did.
- Only the Head Attorney can add or remove dates.
- The Head Attorney can also ask LEXIS to read the year's holiday
  proclamation from the Official Gazette (GET /holidays/suggestions). That
  only suggests dates; each one is still added with POST /holidays.
- Every date added or removed is recorded in the Security Log (manuscript
  Figure 4.1).
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_current_staff_user, require_head_attorney
from app.core.holiday_import import HolidayImportError, ProclamationImport, import_from_gazette
from app.core.security_monitoring import get_client_ip, limiter, log_security_event
from app.models.models import Holiday, StaffUser
from app.schemas.schemas import HolidayCreate, HolidayOut, HolidaySuggestionOut, HolidaySuggestionsOut

router = APIRouter(prefix="/holidays", tags=["holidays"])

# A successful proclamation read is kept for an hour, per year:
# one read takes about 15 seconds (minutes in the worst case) and the
# Gazette limits repeat downloads. Failures are not kept, so a retry after
# "wait a few minutes" really tries again. Kept in this server process's
# memory only: at most one entry per year (2020-2100), gone on restart.
SAVED_READ_SECONDS = 60 * 60
_saved_reads: dict[int, tuple[float, ProclamationImport]] = {}


def _read_proclamation(year: int) -> tuple[ProclamationImport, bool]:
    """(result, True if it is the saved read from the last hour)."""
    saved = _saved_reads.get(year)
    if saved and time.monotonic() - saved[0] < SAVED_READ_SECONDS:
        return saved[1], True
    result = import_from_gazette(year)
    _saved_reads[year] = (time.monotonic(), result)
    return result, False


@router.get("", response_model=list[HolidayOut])
@limiter.limit("120/minute")
def list_holidays(
    request: Request,
    year: int | None = Query(None, ge=2020, le=2100),
    session: Session = Depends(get_session),
    _staff: StaffUser = Depends(get_current_staff_user),
):
    stmt = select(Holiday).order_by(Holiday.holiday_date)
    rows = session.exec(stmt).all()
    if year is not None:
        rows = [h for h in rows if h.holiday_date.year == year]
    return rows


@router.get("/suggestions", response_model=HolidaySuggestionsOut)
@limiter.limit("5/hour")  # a fresh read downloads a PDF and runs OCR; a normal year needs 1-2 checks
def holiday_suggestions(
    request: Request,
    year: int = Query(..., ge=2020, le=2100),
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(require_head_attorney),
):
    """Read the year's proclamation from the Official Gazette. Nothing is saved."""
    ip = get_client_ip(request)
    try:
        result, reused = _read_proclamation(year)
    except HolidayImportError as exc:
        log_security_event(session, "holiday_import_failed",
                           f"{year}: {exc} (by {current_staff.full_name})", ip)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:  # an OCR or PDF library error: never show its raw text
        log_security_event(session, "holiday_import_failed",
                           f"{year}: unexpected {type(exc).__name__} (by {current_staff.full_name})", ip)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The proclamation couldn't be read this time. Try again later, or enter the dates by hand.",
        )

    # Always fresh, also for a saved read: dates may have been added since.
    listed = {h.holiday_date: h.name for h in session.exec(select(Holiday)).all()
              if h.holiday_date.year == year}
    log_security_event(session, "holiday_import_checked",
                       f"{year}: {len(result.holidays)} dates read from {result.title}"
                       f"{' (saved result from the last hour)' if reused else ''} "
                       f"(by {current_staff.full_name})", ip)
    return HolidaySuggestionsOut(
        year=year, title=result.title, page_url=result.page_url, pdf_url=result.pdf_url,
        suggestions=[
            HolidaySuggestionOut(
                holiday_date=h.holiday_date, name=h.name, holiday_type=h.holiday_type,
                check_note=h.check_note, already_listed_as=listed.get(h.holiday_date),
            )
            for h in result.holidays
        ],
        unreadable_lines=result.unreadable_lines,
    )


@router.post("", response_model=HolidayOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def add_holiday(
    request: Request,
    payload: HolidayCreate,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(require_head_attorney),
):
    existing = session.exec(
        select(Holiday).where(Holiday.holiday_date == payload.holiday_date)
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{payload.holiday_date.isoformat()} is already listed as \"{existing.name}\".",
        )
    holiday = Holiday(
        holiday_date=payload.holiday_date,
        name=payload.name,
        holiday_type=payload.holiday_type,
        created_by_staff_id=current_staff.id,
    )
    session.add(holiday)
    session.commit()
    log_security_event(
        session, "holiday_added",
        f"{current_staff.full_name} added {payload.holiday_date.isoformat()} \"{payload.name}\" ({payload.holiday_type}).",
        get_client_ip(request),
    )
    session.refresh(holiday)
    return holiday


@router.delete("/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
def remove_holiday(
    request: Request,
    holiday_id: int,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(require_head_attorney),
):
    holiday = session.get(Holiday, holiday_id)
    if not holiday:
        raise HTTPException(status_code=404, detail="Holiday not found.")
    removed = f"{holiday.holiday_date.isoformat()} \"{holiday.name}\" ({holiday.holiday_type})"
    session.delete(holiday)
    session.commit()
    log_security_event(session, "holiday_removed", f"{current_staff.full_name} removed {removed}.",
                       get_client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

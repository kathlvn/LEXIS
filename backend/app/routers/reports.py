"""
Reports page.
Read-only, computed from the requests and their history; no new tables.
Every legal staff member can view it.

How the figures are counted (also shown on the page):
- A request belongs to a period if it was SUBMITTED in that period
  (Philippine date).
- Completed / pending / overdue describe those requests AS OF TODAY:
  pending = not completed, cancelled, or rejected; overdue = pending and
  past its due date (same rule as the request lists).
- Average processing time = working days from submission to completion
  (weekends and holidays skipped), for the completed requests only.
  Completion = the last status change to "Completed" in the history.
- Staff workload is by the staff member the request is assigned to now.
- Accomplished requests are the exception to the first
  rule: requests COMPLETED in the period, whenever they were filed.
- Contract reviews: filed in the period, plus those filed
  earlier that are still open.
- Client Satisfaction Measurement: surveys ANSWERED in the
  period, totals only, and nothing but the count below 3 responses
  (see _csm_summary).
"""
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select

from app.core.csm_form import CsmFormError, load_csm_form
from app.core.database import get_session
from app.core.deps import get_current_staff_user
from app.core.security_monitoring import limiter
from app.core.working_days import (
    PH_TZ, deadline_status, load_holiday_dates, to_ph_date, working_days_between,
)
from app.models.models import (
    CLIENT_CATEGORIES, AuditLogEntry, CsmResponse, LegalServiceRequest, ServiceType, StaffUser,
    WorkflowStepTemplate,
)
from app.routers.requests import _client_office

router = APIRouter(prefix="/reports", tags=["reports"])

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
ROLE_LABELS = {"legal_assistant": "Legal Assistant", "attorney": "Attorney",
               "head_attorney": "Head Attorney"}


def _period_bounds(period: str, year: int, month: int | None, quarter: int | None):
    """(first_day, last_day, label) of the chosen period, as Philippine dates."""
    if period == "month":
        if not month:
            raise HTTPException(status_code=400, detail="Choose a month.")
        first = date(year, month, 1)
        last = date(year + (month == 12), (month % 12) + 1, 1)
        return first, last, f"{MONTH_NAMES[month - 1]} {year}"
    if period == "quarter":
        if not quarter:
            raise HTTPException(status_code=400, detail="Choose a quarter.")
        m = (quarter - 1) * 3 + 1
        first = date(year, m, 1)
        last = date(year + (quarter == 4), (m + 3 - 1) % 12 + 1, 1)
        return first, last, f"Quarter {quarter} ({MONTH_NAMES[m - 1][:3]}\u2013{MONTH_NAMES[m + 1][:3]}) {year}"
    return date(year, 1, 1), date(year + 1, 1, 1), f"Year {year}"


def _ph_midnight_utc(d: date) -> datetime:
    """Start of a Philippine calendar day, as naive UTC (how times are stored)."""
    return datetime.combine(d, time(0, 0), tzinfo=PH_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def _completion_times(session: Session, request_ids: list[int]) -> dict[int, datetime]:
    """When each request was completed: its LAST status change to
    "Completed" (a request reopened and completed again counts the last
    time). Requests with no such history entry are left out; callers fall
    back to the request's last update."""
    done_at: dict[int, datetime] = {}
    if not request_ids:
        return done_at
    for e in session.exec(
        select(AuditLogEntry).where(
            AuditLogEntry.request_id.in_(request_ids),
            AuditLogEntry.action == "status_change",
        ).order_by(AuditLogEntry.created_at)
    ).all():
        if e.detail.rstrip(".").endswith("to Completed"):
            done_at[e.request_id] = e.created_at
    return done_at


def _contracts(session: Session, first: date, after_last: date, services: dict, step_names: dict,
               holidays: set[date], now: datetime) -> list[dict]:
    """Contract reviews: requests of the
    "Review of MOA/MOU, Contracts..." service (code contract_review) FILED in
    the period, plus those filed earlier that are still open, so an
    unfinished review never drops off the list. Requests filed after the
    period are never shown. No expiry dates (LEXIS has no such field)."""
    service_ids = [sid for sid, st in services.items() if st.code == "contract_review"]
    if not service_ids:
        return []
    start, end = _ph_midnight_utc(first), _ph_midnight_utc(after_last)
    reqs = session.exec(
        select(LegalServiceRequest).where(
            LegalServiceRequest.service_type_id.in_(service_ids),
            LegalServiceRequest.submitted_at < end,
        )
    ).all()
    done_at = _completion_times(session, [r.id for r in reqs if step_names.get(r.current_step_id) == "Completed"])

    rows = []
    for r in reqs:
        status = step_names.get(r.current_step_id) or "Submitted"
        if r.is_cancelled:
            status = "Cancelled"
        elif r.is_rejected:
            status = "Rejected"
        is_closed = status in ("Completed", "Cancelled", "Rejected")
        filed_earlier = r.submitted_at < start
        if filed_earlier and is_closed:
            continue
        rows.append({
            "tracking_id": r.tracking_id,
            "office": (_client_office(session, r) or "").strip() or "Not stated",
            "submitted_on": to_ph_date(r.submitted_at).isoformat(),
            "due_on": to_ph_date(r.due_date).isoformat() if r.due_date else None,
            "completed_on": to_ph_date(done_at.get(r.id, r.last_updated_at)).isoformat()
                            if status == "Completed" else None,
            "status": status,
            "overdue": deadline_status(r.due_date, is_closed, holidays, now) == "overdue",
            "filed_earlier": filed_earlier,
        })
    rows.sort(key=lambda x: (x["submitted_on"], x["tracking_id"]))
    return rows


def _accomplished(session: Session, first: date, after_last: date, services: dict, step_names: dict,
                  holidays: set[date]) -> list[dict]:
    """Requests COMPLETED in the period, whenever they were filed:
    the printable accomplishment list. Completion = the last status
    change to "Completed" (as for the average processing time), else the
    last update. Requests reopened, cancelled or rejected since are not
    completed now and are left out."""
    completed_steps = [sid for sid, name in step_names.items() if name == "Completed"]
    if not completed_steps:
        return []
    reqs = session.exec(
        select(LegalServiceRequest).where(
            LegalServiceRequest.current_step_id.in_(completed_steps),
            LegalServiceRequest.is_cancelled == False,  # noqa: E712
            LegalServiceRequest.is_rejected == False,  # noqa: E712
        )
    ).all()
    if not reqs:
        return []
    done_at = _completion_times(session, [r.id for r in reqs])

    rows = []
    for r in reqs:
        done = to_ph_date(done_at.get(r.id, r.last_updated_at))
        if not first <= done < after_last:
            continue
        st = services.get(r.service_type_id)
        days = working_days_between(to_ph_date(r.submitted_at), done, holidays)
        period = st.default_processing_days if st else None
        rows.append({
            "tracking_id": r.tracking_id,
            "service": st.name if st else "",
            "office": (_client_office(session, r) or "").strip() or "Not stated",
            "submitted_on": to_ph_date(r.submitted_at).isoformat(),
            "completed_on": done.isoformat(),
            "working_days": days,
            "processing_days": period,
            "within_period": (days <= period) if period else None,   # None: no set period
        })
    rows.sort(key=lambda x: (x["completed_on"], x["tracking_id"]))
    return rows


def _client_category(req: LegalServiceRequest) -> str | None:
    """The requesting party (client category code) as filed: the snapshot
    taken at submission, else the client's saved category (requests filed
    before snapshots existed). Same rule as requests._client_office."""
    return req.client_category_at_submission or req.requester.client_category


# Fewer responses than this: only the count is sent, since staff know who
# completed a request and the totals could reveal one client's answers.
CSM_MIN_RESPONSES = 3


def _csm_summary(session: Session, first: date, after_last: date) -> dict:
    """Client Satisfaction Measurement results: surveys
    ANSWERED in the period that used the current form (app/csm_form.json).
    Totals only, never one client's answers. Satisfied = the scale's
    satisfied values (ARTA: Agree + Strongly agree) out of the answers that
    are not N/A; overall = the same across all rated statements."""
    rows = session.exec(
        select(CsmResponse).where(
            CsmResponse.submitted_at >= _ph_midnight_utc(first),
            CsmResponse.submitted_at < _ph_midnight_utc(after_last),
        ).order_by(CsmResponse.submitted_at.desc())
    ).all()
    try:
        form = load_csm_form()
    except CsmFormError as exc:
        # Staff see why, so the office knows the file needs fixing.
        return {"available": False, "problem": str(exc), "responses": len(rows)}

    current = []
    for r in rows:
        if r.form_version != form["version"]:
            continue
        try:
            answers = json.loads(r.answers)
        except ValueError:
            continue
        if isinstance(answers, dict):
            current.append((answers, r.comment))
    summary = {
        "available": True,
        "placeholder": form["placeholder"],
        "responses": len(current),
        "other_version_responses": len(rows) - len(current),
        "min_responses": CSM_MIN_RESPONSES,
        "shown": len(current) >= CSM_MIN_RESPONSES,
        "overall_rate": None,
        "questions": [],
        "suggestions": [],
    }
    if not summary["shown"]:
        return summary

    total_rated = total_satisfied = 0
    for section in form["sections"]:
        for q in section["questions"]:
            counts = Counter(a.get(q["id"]) for a, _ in current)
            options = [{"label": o["label"], "count": counts.get(o["value"], 0)} for o in q["options"]]
            answered = sum(o["count"] for o in options)
            rated = answered - sum(counts.get(v, 0) for v in q["not_applicable_values"])
            satisfied = sum(counts.get(v, 0) for v in q["satisfied_values"])
            scored = bool(q["satisfied_values"])
            if scored:
                total_rated += rated
                total_satisfied += satisfied
            summary["questions"].append({
                "id": q["id"],
                "section": section["heading"],
                "text": q["text"],
                "options": options,
                "scored": scored,
                "rated": rated if scored else None,
                "satisfied": satisfied if scored else None,
                "rate": round(100 * satisfied / rated, 1) if scored and rated else None,
            })
    if total_rated:
        summary["overall_rate"] = round(100 * total_satisfied / total_rated, 1)
    summary["suggestions"] = [c for _, c in current if c]
    return summary


@router.get("")
@limiter.limit("30/minute")  # each load reads every request in the period and its history
def get_reports(
    request: Request,
    period: str = Query("month", pattern="^(month|quarter|year)$"),
    year: int = Query(..., ge=2020, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    quarter: int | None = Query(None, ge=1, le=4),
    session: Session = Depends(get_session),
    _staff: StaffUser = Depends(get_current_staff_user),
):
    first, after_last, label = _period_bounds(period, year, month, quarter)
    holidays = load_holiday_dates(session)
    now = datetime.utcnow()

    services = {s.id: s for s in session.exec(select(ServiceType)).all()}
    step_names = {w.id: w.status_name for w in session.exec(select(WorkflowStepTemplate)).all()}
    staff = {u.id: u for u in session.exec(select(StaffUser)).all()}

    in_period = session.exec(
        select(LegalServiceRequest).where(
            LegalServiceRequest.submitted_at >= _ph_midnight_utc(first),
            LegalServiceRequest.submitted_at < _ph_midnight_utc(after_last),
        )
    ).all()

    completed_at = _completion_times(
        session, [r.id for r in in_period if step_names.get(r.current_step_id) == "Completed"])

    summary = Counter()
    by_service = defaultdict(lambda: {"received": 0, "completed": 0, "pending": 0, "overdue": 0, "_days": []})
    by_status = Counter()
    by_office = Counter()
    by_category = Counter()
    by_staff = defaultdict(lambda: {"handled": 0, "completed": 0, "pending": 0, "overdue": 0})

    for r in in_period:
        status = step_names.get(r.current_step_id) or "Submitted"
        if r.is_cancelled:
            status = "Cancelled"
        elif r.is_rejected:
            status = "Rejected"
        is_completed = status == "Completed"
        is_closed = is_completed or r.is_cancelled or r.is_rejected
        overdue = deadline_status(r.due_date, is_closed, holidays, now) == "overdue"

        svc = by_service[r.service_type_id]
        officer = by_staff[r.assigned_officer_id]
        summary["received"] += 1
        svc["received"] += 1
        officer["handled"] += 1
        by_status[status] += 1
        # Office as filed (snapshot), not the typed company_agency, which is
        # empty for clients who picked an ESSU office.
        by_office[(_client_office(session, r) or "").strip() or "Not stated"] += 1
        by_category[_client_category(r) or ""] += 1   # requesting party

        if is_completed:
            summary["completed"] += 1
            svc["completed"] += 1
            officer["completed"] += 1
            done = completed_at.get(r.id, r.last_updated_at)
            svc["_days"].append(working_days_between(to_ph_date(r.submitted_at), to_ph_date(done), holidays))
        elif not is_closed:
            summary["pending"] += 1
            svc["pending"] += 1
            officer["pending"] += 1
            if overdue:
                summary["overdue"] += 1
                svc["overdue"] += 1
                officer["overdue"] += 1
        else:
            summary["closed"] += 1

    # Requests received in each month of the selected year (for the chart).
    year_rows = session.exec(
        select(LegalServiceRequest.submitted_at).where(
            LegalServiceRequest.submitted_at >= _ph_midnight_utc(date(year, 1, 1)),
            LegalServiceRequest.submitted_at < _ph_midnight_utc(date(year + 1, 1, 1)),
        )
    ).all()
    per_month = Counter(to_ph_date(t).month for t in year_rows)

    service_rows = []
    for sid, st in sorted(services.items()):
        v = by_service.get(sid)
        days = v["_days"] if v else []
        service_rows.append({
            "service": st.name,
            "processing_days": st.default_processing_days,
            "received": v["received"] if v else 0,
            "completed": v["completed"] if v else 0,
            "pending": v["pending"] if v else 0,
            "overdue": v["overdue"] if v else 0,
            "avg_working_days": round(sum(days) / len(days), 1) if days else None,
        })

    staff_rows = []
    for uid, v in by_staff.items():
        u = staff.get(uid)
        staff_rows.append({
            "staff": u.full_name if u else "Unassigned",
            "role": ROLE_LABELS.get(u.role, u.role) if u else "",
            **v,
        })
    # Active legal assistants with no requests still appear, so the table
    # shows the whole team's share of the work.
    listed = {r["staff"] for r in staff_rows}
    for u in staff.values():
        if u.role == "legal_assistant" and u.is_active and u.full_name not in listed:
            staff_rows.append({"staff": u.full_name, "role": ROLE_LABELS[u.role],
                               "handled": 0, "completed": 0, "pending": 0, "overdue": 0})
    staff_rows.sort(key=lambda x: (-x["handled"], x["staff"]))

    # Every category in its usual order, zeros included, so the table always
    # shows the whole picture; then any old/unknown code, then "Not stated".
    received = summary["received"]
    category_rows = [{"party": label, "count": by_category.get(code, 0)} for code, label in CLIENT_CATEGORIES.items()]
    category_rows += [{"party": code, "count": n} for code, n in by_category.items()
                      if code and code not in CLIENT_CATEGORIES]
    if by_category.get(""):
        category_rows.append({"party": "Not stated", "count": by_category[""]})
    for row in category_rows:
        row["share"] = round(100 * row["count"] / received, 1) if received else None

    status_order = ["Submitted", "Received", "For Validation", "Under Review", "Clarificatory Meeting",
                    "Processing", "For Attorney Review", "Approved", "Completed", "Cancelled", "Rejected"]
    return {
        "period": {"type": period, "label": label, "first_day": first.isoformat(),
                   "last_day": date.fromordinal(after_last.toordinal() - 1).isoformat()},
        "generated_at": now.isoformat() + "Z",
        "summary": {"received": summary["received"], "completed": summary["completed"],
                    "pending": summary["pending"], "overdue": summary["overdue"],
                    "cancelled_or_rejected": summary["closed"]},
        "monthly": [{"month": m, "label": MONTH_NAMES[m - 1][:3], "received": per_month.get(m, 0)}
                    for m in range(1, 13)],
        "by_service": service_rows,
        "by_status": [{"status": s, "count": by_status[s]} for s in status_order if by_status.get(s)],
        "by_office": [{"office": o, "count": c} for o, c in by_office.most_common()],
        "by_category": category_rows,
        "accomplished": _accomplished(session, first, after_last, services, step_names, holidays),
        "contracts": _contracts(session, first, after_last, services, step_names, holidays, now),
        "by_staff": staff_rows,
        "csm": _csm_summary(session, first, after_last),
    }

"""
Records Archive — Head Attorney only.

Every quarter the Head Attorney downloads one ZIP of the requests that were
CLOSED in that quarter (completed, cancelled, or rejected), for upload to the
Legal Office's Google Drive (archive only; LEXIS keeps its own copy).

Agreed with the Legal Office:
- A request belongs to the quarter it was closed in (Philippine date), so
  each request is archived once, in its final state. Ongoing requests wait.
  Closed = the last status change to "Completed"; for a cancelled or
  rejected request, when it was closed (its last update for older ones).
- The archive is the office's full record: every document version (client
  and Legal Office, released or not), all messages, and the staff History
  with names.
- One ZIP:
      LEXIS-Archive-2026-Q3/
        README.txt
        index.csv                      (one row per request, opens in Excel)
        2026-07 July/
          LEO-2026-000154/
            Summary.pdf                (details, documents, messages, history)
            Client documents/v1 - MOA.pdf
            Legal Office documents/v2 - Review notes.pdf

Safety:
- Head Attorney only; every export is logged as a security event (who,
  which quarter, how many requests and files).
- POST for the download: the staff cookie is SameSite=Lax, so another
  website cannot start an export in the Head Attorney's browser.
- Rate-limited, one export at a time, a size cap, and the ZIP is built in a
  temporary file that is deleted as soon as it has been sent.
- File names inside the ZIP are cleaned (no folders, no characters Windows
  refuses), so a requester's file name can't write outside its folder.
"""
import csv
import io
import os
import re
import tempfile
import threading
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from starlette.background import BackgroundTask

from app.core import storage
from app.core.database import get_session
from app.core.deps import require_head_attorney
from app.core.security_monitoring import get_client_ip, limiter, log_security_event
from app.core.working_days import PH_TZ, to_ph_date
from app.models.models import (
    AuditLogEntry, Document, LegalServiceRequest, Message, Requester, ServiceType,
    StaffUser, WorkflowStepTemplate,
)
from app.routers.requests import _client_name, _client_office

router = APIRouter(prefix="/archive", tags=["archive"])

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
ROLE_LABELS = {"legal_assistant": "Legal Assistant", "attorney": "Attorney",
               "head_attorney": "Head Attorney"}

# One quarter is roughly 150 requests at the expected volume; 1 GB leaves a
# wide margin while keeping the browser download and the server disk safe.
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_export_lock = threading.Lock()   # one export at a time


class ArchiveExportIn(BaseModel):
    year: int = Field(ge=2020, le=2100)
    quarter: int = Field(ge=1, le=4)


# ---------------------------------------------------------------- helpers

def _quarter_bounds(year: int, quarter: int) -> tuple[date, date, str]:
    first_month = (quarter - 1) * 3 + 1
    first = date(year, first_month, 1)
    last = date(year + (quarter == 4), (first_month + 3 - 1) % 12 + 1, 1)  # exclusive
    label = (f"Quarter {quarter} ({MONTH_NAMES[first_month - 1][:3]}–"
             f"{MONTH_NAMES[first_month + 1][:3]}) {year}")
    return first, last, label


def _ph_text(utc_naive: datetime | None, with_time: bool = True) -> str:
    if not utc_naive:
        return "—"
    local = utc_naive.replace(tzinfo=timezone.utc).astimezone(PH_TZ)
    return local.strftime("%b %d, %Y %I:%M %p" if with_time else "%b %d, %Y")


def _closed_requests(session: Session, first: date, last: date):
    """[(request, final_status, closed_at)] for requests closed in [first, last)."""
    step_names = {s.id: s.status_name for s in session.exec(select(WorkflowStepTemplate)).all()}
    completed_step_ids = [sid for sid, name in step_names.items() if name == "Completed"]

    candidates = session.exec(
        select(LegalServiceRequest).where(
            (LegalServiceRequest.is_cancelled == True)  # noqa: E712
            | (LegalServiceRequest.is_rejected == True)  # noqa: E712
            | (LegalServiceRequest.current_step_id.in_(completed_step_ids or [-1]))
        )
    ).all()

    # When each completed request was completed: its last change to "Completed".
    completed_at: dict[int, datetime] = {}
    ids = [r.id for r in candidates]
    if ids:
        for e in session.exec(
            select(AuditLogEntry)
            .where(AuditLogEntry.request_id.in_(ids), AuditLogEntry.action == "status_change")
            .order_by(AuditLogEntry.created_at)
        ).all():
            if e.detail.rstrip(".").endswith("to Completed"):
                completed_at[e.request_id] = e.created_at

    rows = []
    for r in candidates:
        if r.is_cancelled:
            status, closed = "Cancelled", r.closed_at or r.last_updated_at
        elif r.is_rejected:
            status, closed = "Rejected", r.closed_at or r.last_updated_at
        else:
            status, closed = "Completed", completed_at.get(r.id, r.last_updated_at)
        if first <= to_ph_date(closed) < last:
            rows.append((r, status, closed))
    rows.sort(key=lambda row: (row[2], row[0].tracking_id))
    return rows


_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def _safe_name(name: str, limit: int = 120) -> str:
    """A file name that is safe inside the ZIP on Windows and Mac: no folder
    parts, no characters Windows refuses, not too long."""
    name = _BAD_NAME_CHARS.sub("_", (name or "").strip()).strip(" .") or "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED:
        stem = "_" + stem
    ext = ext[:10]
    stem = stem[: max(1, limit - len(ext) - 1)]
    return f"{stem}.{ext}" if ext else stem


def _unique(name: str, used: set[str]) -> str:
    candidate, n = name, 2
    stem, dot, ext = name.rpartition(".")
    while candidate.lower() in used:
        candidate = f"{stem} ({n}).{ext}" if dot else f"{name} ({n})"
        n += 1
    used.add(candidate.lower())
    return candidate


def _csv_cell(value) -> str:
    """Text typed by a requester (a name, an office) must never run as an
    Excel formula when index.csv is opened ("CSV injection")."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _file_size(doc: Document) -> int:
    return doc.file_size_bytes or 0


# ---------------------------------------------------------------- summary PDF

def _summary_pdf(req, status, closed_at, service_name, requester, client_name, client_office,
                 docs, doc_paths, messages, history, staff_names, exported_by, exported_at,
                 followup_of_tracking) -> bytes:
    # Imported here so the rest of LEXIS still starts if reportlab is missing.
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=styles["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10)
    label = ParagraphStyle("l", parent=small, textColor=colors.HexColor("#555555"))
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=15, leading=19, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, leading=14,
                        spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1f4d2c"),
                        keepWithNext=1)   # never leave a heading alone at the bottom of a page

    def p(text, style=body):
        # Everything shown comes from the database (some of it typed by the
        # requester), so it is escaped; line breaks are kept.
        return Paragraph(escape(str(text if text not in (None, "") else "—")).replace("\n", "<br/>"), style)

    grid = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3ef")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])

    story = [
        p("ESSU LEGAL OFFICE — REQUEST RECORD", label),
        Paragraph(escape(req.tracking_id), h1),
        p(f"{service_name} · {status} · closed {_ph_text(closed_at, False)}", small),
        Spacer(1, 6),
    ]

    officer = staff_names.get(req.assigned_officer_id)
    details = [
        ("Request type", "Follow-up to " + followup_of_tracking if followup_of_tracking else "New request"),
        ("Service", service_name),
        ("Final status", status),
        ("Submitted", _ph_text(req.submitted_at)),
        ("Due date", _ph_text(req.due_date, False) if req.due_date else "No set period"),
        ("Closed", _ph_text(closed_at)),
        ("Assigned to", officer or "—"),
        ("Prepared by", staff_names.get(req.prepared_by_officer_id) or "—"),
        ("Requester", client_name or "—"),          # as filed (snapshot)
        ("Office / Agency", client_office),
        ("Email", requester.email if requester else None),
        ("Phone", requester.phone if requester else None),
        ("Privacy consent", _ph_text(req.privacy_consent_given_at)),
    ]
    t = Table([[p(k, label), p(v)] for k, v in details], colWidths=[35 * mm, 135 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd"))]))
    story += [t, Paragraph("Description", h2), p(req.description)]
    if req.purpose:
        story += [Paragraph("Purpose", h2), p(req.purpose)]
    if req.staff_note_to_client:
        story += [Paragraph("Last note to the requester", h2), p(req.staff_note_to_client)]

    story.append(Paragraph(f"Documents ({len(docs)})", h2))
    if docs:
        rows = [[p(h, label) for h in ("File (in this folder)", "Ver.", "Status", "Released", "Uploaded", "By")]]
        for d in docs:
            if d.uploaded_by_staff_id:
                by = staff_names.get(d.uploaded_by_staff_id, "Staff")
            else:
                by = "Requester"
            released = "—" if d.category == "client_submitted" else ("Yes" if d.is_released_to_requester else "No")
            rows.append([p(doc_paths.get(d.id, f"MISSING from storage: {d.original_filename}"), small), p(d.version, small),
                         p(d.status.replace("_", " "), small), p(released, small),
                         p(_ph_text(d.uploaded_at), small), p(by, small)])
        dt = Table(rows, colWidths=[62 * mm, 10 * mm, 22 * mm, 16 * mm, 32 * mm, 28 * mm], repeatRows=1)
        dt.setStyle(grid)
        story.append(dt)
    else:
        story.append(p("No documents.", small))

    story.append(Paragraph(f"Messages ({len(messages)})", h2))
    if messages:
        for m in messages:
            who = "Requester" if m.sender_type == "requester" else staff_names.get(m.sender_staff_id, "Legal Office")
            story.append(KeepTogether([p(f"{who} · {_ph_text(m.sent_at)}", label), p(m.body), Spacer(1, 4)]))
    else:
        story.append(p("No messages.", small))

    story.append(Paragraph(f"History ({len(history)})", h2))
    if history:
        rows = [[p(h, label) for h in ("When", "By", "What happened")]]
        for e in history:
            if e.actor_type == "requester":
                who = "Requester"
            elif e.actor_type == "system":
                who = "System"
            else:
                who = staff_names.get(e.staff_id, "Unknown")
            rows.append([p(_ph_text(e.created_at), small), p(who, small), p(e.detail, small)])
        ht = Table(rows, colWidths=[32 * mm, 32 * mm, 106 * mm], repeatRows=1)
        ht.setStyle(grid)
        story.append(ht)
    else:
        story.append(p("No history entries.", small))

    footer_text = (f"LEXIS records archive · exported {_ph_text(exported_at)} by {exported_by} "
                   f"· CONFIDENTIAL — ESSU Legal Office")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(20 * mm, 10 * mm, footer_text)
        canvas.drawRightString(190 * mm, 10 * mm, f"{req.tracking_id} · page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
        title=f"{req.tracking_id} request record", author="ESSU Legal Office (LEXIS)",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ---------------------------------------------------------------- endpoints

@router.get("/preview")
@limiter.limit("30/minute")
def preview_archive(
    request: Request,
    year: int = Query(ge=2020, le=2100),
    quarter: int = Query(ge=1, le=4),
    session: Session = Depends(get_session),
    staff: StaffUser = Depends(require_head_attorney),
):
    """What an export of this quarter would contain, before downloading it."""
    first, last, label = _quarter_bounds(year, quarter)
    rows = _closed_requests(session, first, last)
    ids = [r.id for r, _, _ in rows]
    docs = session.exec(select(Document).where(Document.request_id.in_(ids or [-1]))).all()
    total = sum(_file_size(d) for d in docs)

    by_month = defaultdict(int)
    for _, _, closed in rows:
        d = to_ph_date(closed)
        by_month[(d.year, d.month)] += 1
    months = []
    m = first
    while m < last:
        months.append({"label": f"{MONTH_NAMES[m.month - 1]} {m.year}", "count": by_month.get((m.year, m.month), 0)})
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)

    today_ph = datetime.now(PH_TZ).date()
    return {
        "label": label,
        "request_count": len(rows),
        "document_count": len(docs),
        "total_bytes": total,
        "too_large": total > MAX_ARCHIVE_BYTES,
        "max_bytes": MAX_ARCHIVE_BYTES,
        "quarter_not_over": today_ph < last,
        "in_future": today_ph < first,
        "months": months,
        "requests": [
            {"tracking_id": r.tracking_id, "status": status, "closed_at": closed}
            for r, status, closed in rows
        ],
    }


@router.post("/export")
@limiter.limit("5/hour")
def export_archive(
    request: Request,
    payload: ArchiveExportIn,
    session: Session = Depends(get_session),
    staff: StaffUser = Depends(require_head_attorney),
):
    first, last, label = _quarter_bounds(payload.year, payload.quarter)
    rows = _closed_requests(session, first, last)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No requests were closed in {label}.")

    if not _export_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Another export is being prepared. Please try again in a minute.")
    tmp_path = None
    try:
        ids = [r.id for r, _, _ in rows]
        all_docs = session.exec(
            select(Document).where(Document.request_id.in_(ids)).order_by(Document.uploaded_at)
        ).all()
        if sum(_file_size(d) for d in all_docs) > MAX_ARCHIVE_BYTES:
            raise HTTPException(status_code=413, detail=(
                "This quarter's documents are larger than 1 GB, too large for one download. "
                "Please contact the system administrator."))

        docs_by_req = defaultdict(list)
        for d in all_docs:
            docs_by_req[d.request_id].append(d)
        msgs_by_req = defaultdict(list)
        for m in session.exec(select(Message).where(Message.request_id.in_(ids)).order_by(Message.sent_at)).all():
            msgs_by_req[m.request_id].append(m)
        hist_by_req = defaultdict(list)
        for e in session.exec(
            select(AuditLogEntry).where(AuditLogEntry.request_id.in_(ids)).order_by(AuditLogEntry.created_at)
        ).all():
            hist_by_req[e.request_id].append(e)

        staff_names = {s.id: s.full_name for s in session.exec(select(StaffUser)).all()}
        services = {s.id: s.name for s in session.exec(select(ServiceType)).all()}
        requesters = {q.id: q for q in session.exec(
            select(Requester).where(Requester.id.in_({r.requester_id for r, _, _ in rows}))).all()}
        followup_ids = {r.followup_of_id for r, _, _ in rows if r.followup_of_id}
        followup_tracking = {x.id: x.tracking_id for x in session.exec(
            select(LegalServiceRequest).where(LegalServiceRequest.id.in_(followup_ids or {-1}))).all()}

        root = f"LEXIS-Archive-{payload.year}-Q{payload.quarter}"
        exported_at = datetime.utcnow()
        missing: list[str] = []

        fd, tmp_path = tempfile.mkstemp(prefix="lois-archive-", suffix=".zip")
        os.close(fd)
        index_rows = []
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for req, status, closed in rows:
                d = to_ph_date(closed)
                folder = f"{root}/{d.year}-{d.month:02d} {MONTH_NAMES[d.month - 1]}/{req.tracking_id}"
                used: set[str] = {"summary.pdf"}
                doc_paths: dict[int, str] = {}
                for doc in docs_by_req[req.id]:
                    sub = "Client documents" if doc.category == "client_submitted" else "Legal Office documents"
                    name = _unique(_safe_name(f"v{doc.version} - {doc.original_filename}"), used)
                    try:
                        content = storage.read_file(doc.storage_key)
                    except Exception:
                        missing.append(f"{req.tracking_id}: {doc.original_filename}")
                        continue
                    # Documents are PDFs, Office files and photos: already
                    # compressed, so they are stored as they are.
                    zf.writestr(zipfile.ZipInfo(f"{folder}/{sub}/{name}", date_time=d.timetuple()[:6]),
                                content, compress_type=zipfile.ZIP_STORED)
                    doc_paths[doc.id] = f"{sub}/{name}"

                requester = requesters.get(req.requester_id)
                # Name and office as filed, the same as Request Detail shows
                client_name = _client_name(req) if requester else ""
                client_office = (_client_office(session, req) or "") if requester else ""
                pdf = _summary_pdf(
                    req, status, closed, services.get(req.service_type_id, "—"), requester,
                    client_name, client_office, docs_by_req[req.id], doc_paths, msgs_by_req[req.id], hist_by_req[req.id],
                    staff_names, staff.full_name, exported_at, followup_tracking.get(req.followup_of_id),
                )
                zf.writestr(f"{folder}/Summary.pdf", pdf)
                index_rows.append([
                    req.tracking_id, services.get(req.service_type_id, ""),
                    client_name, client_office,
                    _ph_text(req.submitted_at, False), _ph_text(closed, False), status,
                    len(doc_paths), len(msgs_by_req[req.id]), folder[len(root) + 1:],
                ])

            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(["Request ID", "Service", "Requester", "Office / Agency", "Submitted",
                             "Closed", "Final status", "Documents", "Messages", "Folder"])
            writer.writerows([[_csv_cell(v) for v in row] for row in index_rows])
            zf.writestr(f"{root}/index.csv", "﻿" + out.getvalue())   # BOM so Excel reads names correctly

            readme = [
                "ESSU LEGAL OFFICE — LEXIS RECORDS ARCHIVE",
                f"Period: {label}",
                f"Exported: {_ph_text(exported_at)} (Philippine time) by {staff.full_name}",
                f"Requests: {len(rows)} (closed in this quarter: completed, cancelled, or rejected)",
                "",
                "Each request has its own folder, filed under the month it was closed:",
                "  Summary.pdf              details, document list, messages, and full history",
                "  Client documents/        every version the requester uploaded",
                "  Legal Office documents/  every version the Legal Office uploaded (released or not)",
                "index.csv lists all requests and opens in Excel.",
                "",
                "CONFIDENTIAL. This archive contains personal information and legal records.",
                "Keep it only in the Legal Office's institutional Google Drive, shared with office staff only.",
                "Delete this downloaded copy from the computer after uploading it.",
            ]
            if missing:
                readme += ["", "Files that could not be read from storage (listed as missing in Summary.pdf):",
                           *[f"  {x}" for x in missing]]
            zf.writestr(f"{root}/README.txt", "\r\n".join(readme) + "\r\n")

        log_security_event(
            session, "records_exported",
            f"{staff.email} exported {label}: {len(rows)} request(s), {len(all_docs) - len(missing)} file(s)"
            + (f", {len(missing)} missing" if missing else ""),
            get_client_ip(request),
        )
        session.commit()

        path_to_send = tmp_path
        tmp_path = None   # FileResponse's background task deletes it now
        return FileResponse(
            path_to_send, media_type="application/zip", filename=f"{root}.zip",
            headers={"Cache-Control": "no-store"},
            background=BackgroundTask(os.remove, path_to_send),
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)   # something failed before the response
        _export_lock.release()

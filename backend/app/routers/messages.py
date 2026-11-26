"""
Request-specific messaging: a structured chat between a requester and Legal
Office staff, scoped to one request. The requester reads and replies on their
request page. A staff message emails the requester a short notice with no
message text, at most once a day per request (to spare the daily email
budget).

Files in the chat: a message may carry
up to MAX_CHAT_FILES files. Each is an ordinary Document row linked by
message_id, so it also shows in the Documents tab and on the client's
request page. A staff file is a Legal Office document, released when sent;
a client file is one of the client's documents. Downloads use the usual
document endpoints, which check access again.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_current_staff_user
from app.core.requester_access import get_verified_request_for_path
from app.core.security_monitoring import limiter
from app.models.models import AuditLogEntry, Document, LegalServiceRequest, Message, StaffUser
from app.core.notifications import notify
from app.core.verification import message_update_line, send_request_update
from app.routers.documents import _check_files, _store_files
from app.routers.requests import _is_visible_to_requester, ensure_open_for_requester
from app.schemas.schemas import MessageAttachmentOut, MessageOut

router = APIRouter(prefix="/requests", tags=["messages"])

# A staff message emails the client only if staff haven't written on the same
# request within this window, so a back-and-forth costs one email, not one each.
MESSAGE_NOTICE_WINDOW = timedelta(hours=24)

MESSAGE_MAX_LENGTH = 5000
MAX_CHAT_FILES = 5       # per message; each file has the usual type and 20 MB checks
EMPTY_MESSAGE = "Write a message or attach a file."


def _get_request_or_404(session: Session, tracking_id: str) -> LegalServiceRequest:
    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    return req


def _require_assigned_or_unrestricted(current_staff: StaffUser, req: LegalServiceRequest) -> None:
    if current_staff.role == "legal_assistant" and req.assigned_officer_id != current_staff.id:
        raise HTTPException(
            status_code=403,
            detail="You can only view or send messages on requests assigned to you.",
        )


def _attachment_out(doc: Document) -> MessageAttachmentOut:
    return MessageAttachmentOut(
        id=doc.id,
        original_filename=doc.original_filename,
        mime_type=doc.mime_type,
        file_size_bytes=doc.file_size_bytes,
        hidden_from_client=not _is_visible_to_requester(doc),
    )


def _attachments_by_message(
    session: Session, message_ids: list[int], for_requester: bool,
) -> dict[int, list[MessageAttachmentOut]]:
    """The files of these messages, in one query. The client sees a Legal
    Office file only while it is released (staff can withdraw it in the
    Documents tab), the same rule as the public download."""
    if not message_ids:
        return {}
    docs = session.exec(
        select(Document).where(Document.message_id.in_(message_ids)).order_by(Document.id)
    ).all()
    found: dict[int, list[MessageAttachmentOut]] = {}
    for doc in docs:
        if for_requester and not _is_visible_to_requester(doc):
            continue
        found.setdefault(doc.message_id, []).append(_attachment_out(doc))
    return found


def _to_message_out(
    session: Session, m: Message, attachments: list[MessageAttachmentOut] | None = None,
) -> MessageOut:
    sender_name = None
    if m.sender_type == "staff" and m.sender_staff_id:
        staff = session.get(StaffUser, m.sender_staff_id)
        sender_name = staff.full_name if staff else None
    return MessageOut(
        id=m.id,
        sender_type=m.sender_type,
        sender_name=sender_name,
        body=m.body,
        sent_at=m.sent_at,
        attachments=attachments or [],
    )


def _list_messages(session: Session, req: LegalServiceRequest, for_requester: bool) -> list[MessageOut]:
    rows = session.exec(
        select(Message).where(Message.request_id == req.id).order_by(Message.sent_at)
    ).all()
    files = _attachments_by_message(session, [m.id for m in rows], for_requester)
    return [_to_message_out(session, m, files.get(m.id)) for m in rows]


def _read_message_input(body: str, files: Optional[list[UploadFile]]) -> tuple[str, list]:
    """Trims the text and checks the files (nothing is written yet). A
    message needs text, at least one file, or both."""
    text = body.strip()
    checked = _check_files(files, MAX_CHAT_FILES) if files else []
    if not text and not checked:
        raise HTTPException(status_code=400, detail=EMPTY_MESSAGE)
    return text, checked


@router.get("/{tracking_id}/messages", response_model=list[MessageOut])
@limiter.limit("120/minute")
def list_messages_staff(
    request: Request,
    tracking_id: str,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    req = _get_request_or_404(session, tracking_id)
    _require_assigned_or_unrestricted(current_staff, req)
    return _list_messages(session, req, for_requester=False)


@router.post("/{tracking_id}/messages", response_model=MessageOut)
@limiter.limit("30/minute")
def send_message_staff(
    request: Request,
    tracking_id: str,
    background_tasks: BackgroundTasks,
    body: str = Form("", max_length=MESSAGE_MAX_LENGTH),
    files: Optional[list[UploadFile]] = File(None),
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Staff message, sent as a form: the text and up to 5 files. The files
    are Legal Office documents, released to the client as they are sent."""
    req = _get_request_or_404(session, tracking_id)
    _require_assigned_or_unrestricted(current_staff, req)

    body, checked = _read_message_input(body, files)

    # Checked before saving, so the new message doesn't count itself.
    noticed_recently = session.exec(
        select(Message.id).where(
            Message.request_id == req.id,
            Message.sender_type == "staff",
            Message.sent_at >= datetime.utcnow() - MESSAGE_NOTICE_WINDOW,
        )
    ).first() is not None

    msg = Message(request_id=req.id, sender_type="staff", sender_staff_id=current_staff.id, body=body)
    session.add(msg)
    session.flush()  # gives msg.id, so the files can point to it
    docs = _store_files(
        session, req, checked, category="legal_office",
        uploaded_by_staff_id=current_staff.id, message_id=msg.id, released=True,
    )
    session.add(AuditLogEntry(
        request_id=req.id,
        staff_id=current_staff.id,
        action="message_sent",
        detail=f"{current_staff.full_name} sent a message"
               + (f" with {len(docs)} file(s)." if docs else "."),
    ))
    for doc in docs:
        # Same action as releasing from the Documents tab, so the client's
        # History shows it too; no staff name, since the client reads it.
        session.add(AuditLogEntry(
            request_id=req.id,
            staff_id=current_staff.id,
            action="document_released",
            detail=f"Document released with a message: \"{doc.original_filename}\".",
        ))
    session.commit()
    session.refresh(msg)

    # Tell the client there is a message, after the reply is sent, like status
    # updates. Only a notice, never the text, and none if staff already wrote
    # on this request in the last 24 hours (that one's notice still stands).
    # Files sent with the message get no email of their own (Gmail budget):
    # the notice already sends the client to the request page.
    # No email on file (phone-only walk-in): nothing is sent, and Request
    # Detail reminds staff to call. A mail problem never undoes the message.
    if not noticed_recently:
        is_closed = req.is_cancelled or req.is_rejected
        background_tasks.add_task(
            send_request_update, req.requester.email, req.tracking_id, [message_update_line(is_closed)],
            is_closed=is_closed,
        )
    return _to_message_out(session, msg, [_attachment_out(d) for d in docs])


@router.post("/{tracking_id}/messages/lookup", response_model=list[MessageOut])
@limiter.limit("30/minute")
def list_messages_public(
    request: Request,
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    """Public: list a request's messages. Requires the requester's access
    cookie for this request."""
    return _list_messages(session, req, for_requester=True)


@router.post("/{tracking_id}/messages/client", response_model=MessageOut)
@limiter.limit("20/minute")
def send_message_public(
    request: Request,
    body: str = Form("", max_length=MESSAGE_MAX_LENGTH),
    files: Optional[list[UploadFile]] = File(None),
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    """Client message, sent as a form: the text and up to 5 files, filed as
    the client's own documents. Who they are comes from the access cookie,
    never from the form."""
    ensure_open_for_requester(req)

    body, checked = _read_message_input(body, files)

    msg = Message(request_id=req.id, sender_type="requester", body=body)
    session.add(msg)
    session.flush()  # gives msg.id, so the files can point to it
    docs = _store_files(
        session, req, checked, category="client_submitted",
        uploaded_by_requester_id=req.requester_id, message_id=msg.id,
    )

    # Logged as the requester's own action, with no staff member.
    session.add(AuditLogEntry(
        request_id=req.id,
        actor_type="requester",
        action="message_sent",
        detail="Requester sent a message" + (f" with {len(docs)} file(s)." if docs else "."),
    ))
    for doc in docs:
        session.add(AuditLogEntry(
            request_id=req.id,
            actor_type="requester",
            action="client_document_uploaded",
            detail=f"Requester uploaded \"{doc.original_filename}\".",
        ))
    notify(session, [req.assigned_officer_id], "requester_message",
           f"The requester sent a message{' with files' if docs else ''} on {req.tracking_id}.", req)

    session.commit()
    session.refresh(msg)
    return _to_message_out(session, msg, [_attachment_out(d) for d in docs])
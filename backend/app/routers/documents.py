"""
Document upload, download, and revision-status endpoints.

Handles both directions:
- Client-submitted documents: uploaded publicly (verified by tracking_id +
  contact, same pattern as POST /requests/lookup — no staff session
  required, since the public side has no persistent accounts).
- Legal Office documents: uploaded by staff (requires a staff session).

Files are never served from a guessable URL — every download goes through
one of the endpoints below, which re-verifies access before streaming
bytes back from storage.
"""
import mimetypes
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlmodel import Session, select

from app.core import storage
from app.core.database import get_session
from app.core.deps import get_current_staff_user
from app.core.requester_access import get_verified_request_for_path
from app.core.security_monitoring import limiter
from app.core.verification import released_update_line, revision_update_line, send_request_update
from app.core.notifications import notify
from app.routers.requests import ensure_not_closed, ensure_open_for_requester, was_encoded_by
from app.routers.portal import MAX_FILES_PER_SUBMISSION, MAX_TOTAL_BYTES, _read_checked
from app.routers.clients import _attachment_header, log_staff_opened
from app.models.models import AuditLogEntry, Document, LegalServiceRequest, Requester, StaffUser
from app.schemas.schemas import DocumentOut, DocumentReleaseUpdate, DocumentStatusUpdate

router = APIRouter(prefix="/requests", tags=["documents"])


NOTE_MAX = 1000   # size of LegalServiceRequest.staff_note_to_client


def _get_request_or_404(session: Session, tracking_id: str) -> LegalServiceRequest:
    req = session.exec(
        select(LegalServiceRequest).where(LegalServiceRequest.tracking_id == tracking_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    return req

def _require_assigned_or_unrestricted(current_staff: StaffUser, req: LegalServiceRequest) -> None:
    """Mirrors the restriction already used on PATCH /requests/{id}/status:
    a legal_assistant may only act on requests assigned to them."""
    if current_staff.role == "legal_assistant" and req.assigned_officer_id != current_staff.id:
        raise HTTPException(
            status_code=403,
            detail="You can only manage documents on requests assigned to you.",
        )


def _check_files(files: list[UploadFile], max_files: int = MAX_FILES_PER_SUBMISSION) -> list[tuple[UploadFile, bytes]]:
    """Reads and checks a batch of uploads; writes nothing. Same limits as
    client submissions in portal.py. Chat messages pass a
    smaller max_files."""
    if len(files) > max_files:
        raise HTTPException(status_code=400, detail=f"You can upload up to {max_files} files at a time.")

    # Check EVERY file before saving ANY, so one bad file in a batch can't
    # leave the earlier ones written to disk with no database record.
    # _read_checked never reads more than the per-file size limit into memory.
    checked: list[tuple[UploadFile, bytes]] = []
    total = 0
    for f in files:
        content = _read_checked(f, f.filename or "File")
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=400, detail=f"The files together exceed {MAX_TOTAL_BYTES // (1024 * 1024)} MB. "
                                "Please upload them in smaller batches.")
        checked.append((f, content))
    return checked


def _store_files(
    session: Session,
    req: LegalServiceRequest,
    checked: list[tuple[UploadFile, bytes]],
    category: str,
    uploaded_by_requester_id: int | None = None,
    uploaded_by_staff_id: int | None = None,
    message_id: int | None = None,
    released: bool = False,
    replaces: Document | None = None,
) -> list[Document]:
    """Writes checked files to storage and adds their Document rows. The
    caller commits. message_id links a file sent in the request chat;
    replaces is the earlier version a revised copy takes over from."""
    saved: list[Document] = []
    for f, content in checked:
        storage_key = storage.generate_storage_key(f.filename)
        try:
            storage.save_file(storage_key, content)
        except Exception:
            # Don't leave the earlier files of this batch in storage with
            # no Document row pointing at them.
            for done in saved:
                try:
                    storage.delete_file(done.storage_key)
                except Exception:
                    pass
            raise

        doc = Document(
            request_id=req.id,
            category=category,
            original_filename=f.filename,
            storage_key=storage_key,
            # Type from the file name (already checked against the allowed
            # extensions), never the type the browser claims.
            mime_type=(mimetypes.guess_type(f.filename or "")[0] or "application/octet-stream")[:100],
            file_size_bytes=len(content),
            uploaded_by_requester_id=uploaded_by_requester_id,
            uploaded_by_staff_id=uploaded_by_staff_id,
            message_id=message_id,
            is_released_to_requester=released,
            version=replaces.version + 1 if replaces else 1,
            supersedes_document_id=replaces.id if replaces else None,
            requirement_id=replaces.requirement_id if replaces else None,   # same checklist item
        )
        session.add(doc)
        saved.append(doc)
    return saved


def _save_uploaded_files(
    session: Session,
    req: LegalServiceRequest,
    files: list[UploadFile],
    category: str,
    uploaded_by_requester_id: int | None = None,
    uploaded_by_staff_id: int | None = None,
) -> list[Document]:
    if not files:
        raise HTTPException(status_code=400, detail="No files were provided.")
    saved = _store_files(
        session, req, _check_files(files), category,
        uploaded_by_requester_id=uploaded_by_requester_id,
        uploaded_by_staff_id=uploaded_by_staff_id,
    )
    session.commit()
    for doc in saved:
        session.refresh(doc)
    return saved


@router.post("/{tracking_id}/documents/client", response_model=list[DocumentOut])
@limiter.limit("10/minute")
def upload_client_documents(
    request: Request,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    """Public upload — used right after submission and for revision
    re-uploads. Requires the requester's access cookie for this request."""
    ensure_open_for_requester(req)

    saved = _save_uploaded_files(
        session, req, files, category="client_submitted",
        uploaded_by_requester_id=req.requester_id,
    )

    # Logged as the requester's own action, with no staff member.
    for doc in saved:
        session.add(AuditLogEntry(
            request_id=req.id,
            actor_type="requester",
            action="client_document_uploaded",
            detail=f"Requester uploaded \"{doc.original_filename}\".",
        ))
    if saved:
        notify(session, [req.assigned_officer_id], "requester_upload",
               f"The requester uploaded {len(saved)} document(s) on {req.tracking_id}.", req)
    session.commit()

    return saved


def _is_replaced(session: Session, doc: Document) -> bool:
    """Whether a newer version of this document has been uploaded."""
    return session.exec(
        select(Document.id).where(Document.supersedes_document_id == doc.id)
    ).first() is not None


@router.post("/{tracking_id}/documents/{document_id}/revision", response_model=DocumentOut)
@limiter.limit("10/minute")
def upload_revised_document(
    request: Request,
    document_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    """The client's revised copy of a document the Legal Office asked them to
    revise (manuscript Table 9, Figure 4.4). It is saved as a new version
    that points to the file it replaces; the earlier file is kept and can
    still be opened, so every version stays traceable."""
    ensure_open_for_requester(req)

    # Locked until the commit, so two uploads sent at the same time can't
    # both become the next version.
    old = session.exec(select(Document).where(Document.id == document_id).with_for_update()).first()
    if not old or old.request_id != req.id or old.category != "client_submitted":
        raise HTTPException(status_code=404, detail="Document not found.")
    if _is_replaced(session, old):
        raise HTTPException(status_code=409, detail="A revised copy of this document was already uploaded. "
                            "Please reload the page.")
    if old.status != "revision_required":
        raise HTTPException(status_code=409, detail="The Legal Office has not asked for a revision of this document.")

    new = _store_files(
        session, req, _check_files([file], 1), "client_submitted",
        uploaded_by_requester_id=req.requester_id, replaces=old,
    )[0]
    try:
        session.flush()   # gives the new version its id
        # A letter still waiting to be checked: staff check the corrected one.
        client = session.get(Requester, req.requester_id)
        if client.endorsement_document_id == old.id and client.verification_status == "pending":
            client.endorsement_document_id = new.id
            session.add(client)
        session.add(AuditLogEntry(
            request_id=req.id,
            actor_type="requester",
            action="client_document_uploaded",
            detail=f"Requester uploaded \"{new.original_filename}\" as version {new.version} of "
                   f"\"{old.original_filename}\".",
        ))
        notify(session, [req.assigned_officer_id], "requester_upload",
               f"The requester uploaded a revised document (version {new.version}) on {req.tracking_id}.", req)
        session.commit()
    except Exception:
        # Don't leave the file in storage with no Document row pointing at it.
        storage_key = new.storage_key
        session.rollback()
        try:
            storage.delete_file(storage_key)
        except Exception:
            pass
        raise
    session.refresh(new)
    return new


@router.post("/{tracking_id}/documents/staff", response_model=list[DocumentOut])
@limiter.limit("20/minute")
def upload_staff_documents(
    request: Request,
    tracking_id: str,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Staff-only upload for the 'Legal Office Documents' side of Request Detail."""
    req = _get_request_or_404(session, tracking_id)
    _require_assigned_or_unrestricted(current_staff, req)

    saved = _save_uploaded_files(
        session, req, files, category="legal_office",
        uploaded_by_staff_id=current_staff.id,
    )

    for doc in saved:
        session.add(AuditLogEntry(
            request_id=req.id,
            staff_id=current_staff.id,
            action="office_document_uploaded",
            detail=f"{current_staff.full_name} uploaded \"{doc.original_filename}\".",
        ))
    session.commit()

    return saved


@router.post("/{tracking_id}/documents/client-by-staff", response_model=list[DocumentOut])
@limiter.limit("20/minute")
def upload_client_documents_by_staff(
    request: Request,
    tracking_id: str,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Walk-in / paper requests: staff add the documents the
    requester handed over. They are filed as the requester's documents
    (visible on Track Request) and marked as uploaded by staff."""
    req = _get_request_or_404(session, tracking_id)
    if (current_staff.role == "legal_assistant" and req.assigned_officer_id != current_staff.id
            and not was_encoded_by(session, req, current_staff.id)):
        raise HTTPException(status_code=403, detail="You can only add documents to requests assigned to you or that you encoded.")
    ensure_not_closed(req)
    saved = _save_uploaded_files(
        session, req, files, category="client_submitted",
        uploaded_by_staff_id=current_staff.id,
    )
    for doc in saved:
        # No staff name in the text: this line is also shown to the requester.
        session.add(AuditLogEntry(
            request_id=req.id,
            staff_id=current_staff.id,
            action="client_document_uploaded",
            detail=f"The Legal Office added \"{doc.original_filename}\" for the requester.",
        ))
    session.commit()
    return saved


@router.post("/{tracking_id}/documents/{document_id}/download")
@limiter.limit("30/minute")
def download_document_public(
    request: Request,
    document_id: int,
    session: Session = Depends(get_session),
    req: LegalServiceRequest = Depends(get_verified_request_for_path),
):
    """Public download. Requires the requester's access cookie for this
    request. Staff use the GET version below instead."""

    doc = session.get(Document, document_id)
    # Same 404 whether the document doesn't exist, belongs to another
    # request, or is a Legal Office document that hasn't been released —
    # so a requester can't probe which IDs exist.
    if (
        not doc
        or doc.request_id != req.id
        or not (doc.category == "client_submitted" or doc.is_released_to_requester)
    ):
        raise HTTPException(status_code=404, detail="Document not found.")

    content = storage.read_file(doc.storage_key)
    return Response(
        content=content,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": _attachment_header(doc.original_filename)},
    )


@router.get("/{tracking_id}/documents/{document_id}/download")
@limiter.limit("60/minute")
def download_document_staff(
    request: Request,
    tracking_id: str,
    document_id: int,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Staff-only download — covers both client-submitted and office documents."""
    req = _get_request_or_404(session, tracking_id)
    _require_assigned_or_unrestricted(current_staff, req)

    doc = session.get(Document, document_id)
    if not doc or doc.request_id != req.id:
        raise HTTPException(status_code=404, detail="Document not found.")

    content = storage.read_file(doc.storage_key)
    log_staff_opened(session, current_staff, req, f"Opened {doc.original_filename}")
    return Response(
        content=content,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": _attachment_header(doc.original_filename)},
    )


@router.patch("/{tracking_id}/documents/{document_id}", response_model=DocumentOut)
@limiter.limit("30/minute")
def update_document_status(
    request: Request,
    tracking_id: str,
    document_id: int,
    payload: DocumentStatusUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Staff-only: marks a client-submitted document as needing revision (or
    reverts it to 'submitted'). Asking for a revision needs a note saying what
    to correct (manuscript Appendix D). The note is added to the request's
    note to the client, which also marks the request as waiting for the
    client, and is sent in the email; there is no per-document note column."""
    req = _get_request_or_404(session, tracking_id)
    _require_assigned_or_unrestricted(current_staff, req)

    doc = session.get(Document, document_id)
    if not doc or doc.request_id != req.id:
        raise HTTPException(status_code=404, detail="Document not found.")

    if payload.status not in ("submitted", "revision_required"):
        raise HTTPException(status_code=400, detail="status must be 'submitted' or 'revision_required'.")
    if _is_replaced(session, doc):
        raise HTTPException(status_code=409, detail="This document was replaced by a newer version. Use that one instead.")

    ask_client = (payload.status == "revision_required" and doc.status != "revision_required"
                  and doc.category == "client_submitted")
    what_to_correct = (payload.note or "").strip()
    note = None
    if ask_client:
        ensure_not_closed(req)   # a closed request can't take a corrected copy
        if not what_to_correct:
            raise HTTPException(status_code=400, detail="State what the client needs to correct.")
        note = f"\"{doc.original_filename}\": {what_to_correct}"
        if req.is_waiting_for_client and req.staff_note_to_client:
            # Something was already asked: keep it and add this below.
            note = f"{req.staff_note_to_client}\n{note}"
        if len(note) > NOTE_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"The note to the client can hold {NOTE_MAX:,} characters; with this it would have {len(note):,}. "
                       "Shorten this, or the earlier note on the Overview tab.",
            )

    doc.status = payload.status
    session.add(doc)
    session.add(AuditLogEntry(
        request_id=req.id,
        staff_id=current_staff.id,
        action="document_status_changed",
        # No staff name in the text: this line is also shown to the
        # requester (the staff name is stored separately in staff_id).
        detail=f"\"{doc.original_filename}\" marked as {payload.status.replace('_', ' ')}.",
    ))
    if ask_client:
        # The same entries as "Ask the client" (PATCH /requests/{id}/status).
        if not req.is_waiting_for_client:
            req.is_waiting_for_client = True
            session.add(AuditLogEntry(request_id=req.id, staff_id=current_staff.id,
                                      action="waiting_for_client_change", detail="Marked as waiting for client."))
        req.staff_note_to_client = note
        session.add(AuditLogEntry(request_id=req.id, staff_id=current_staff.id,
                                  action="client_note_change", detail=f'Updated the note to client: "{note}"'))
        req.last_updated_at = datetime.utcnow()
        session.add(req)
    requester_email = req.requester.email
    session.commit()
    session.refresh(doc)
    if ask_client:
        background_tasks.add_task(
            send_request_update, requester_email, req.tracking_id,
            [revision_update_line(doc.original_filename, what_to_correct)],
        )
    return doc


@router.patch("/{tracking_id}/documents/{document_id}/release", response_model=DocumentOut)
@limiter.limit("30/minute")
def set_document_release(
    request: Request,
    tracking_id: str,
    document_id: int,
    payload: DocumentReleaseUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_staff: StaffUser = Depends(get_current_staff_user),
):
    """Staff-only: release a Legal Office document to the requester, or
    withdraw it again. Withdrawing stops future downloads only — a copy the
    requester already downloaded can't be taken back."""
    req = _get_request_or_404(session, tracking_id)
    _require_assigned_or_unrestricted(current_staff, req)

    doc = session.get(Document, document_id)
    if not doc or doc.request_id != req.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    if doc.category != "legal_office":
        raise HTTPException(
            status_code=400,
            detail="Only Legal Office documents can be released. The requester already sees their own uploads.",
        )
    if doc.is_released_to_requester == payload.released:
        raise HTTPException(
            status_code=400,
            detail="This document is already released." if payload.released else "This document is not released.",
        )

    doc.is_released_to_requester = payload.released
    session.add(doc)
    session.add(AuditLogEntry(
        request_id=req.id,
        staff_id=current_staff.id,
        action="document_released" if payload.released else "document_withdrawn",
        # Shown to the requester too, so no staff name here.
        detail=(
            f"Document released: \"{doc.original_filename}\"."
            if payload.released
            else f"Document withdrawn: \"{doc.original_filename}\"."
        ),
    ))
    requester_email = req.requester.email
    session.commit()
    session.refresh(doc)
    if payload.released:
        background_tasks.add_task(
            send_request_update, requester_email, req.tracking_id,
            [released_update_line(doc.original_filename)],
            is_closed=req.is_cancelled or req.is_rejected,
        )
    return doc

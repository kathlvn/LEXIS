"""
Offices page: LEXIS's own list of ESSU offices and
colleges. ESSU has no official office list, so the
Head Attorney keeps this one; clients pick their office from it on My
Requests, which keeps "requests by office" reports clean.

- Head Attorney only (the list shapes every client form and report).
- Offices are renamed or switched off, never deleted, so clients and
  reports that already point to one keep working. A switched-off office
  is hidden from the client form only.
- Names are compared without regard to capitals or extra spaces, so
  "Registrar" and "registrar " can't both be added.
- "Typed by clients": what ESSU clients wrote under "Others (please
  specify)". Adding one to the list links those clients to it in one step.
  Requests already submitted keep the office they were submitted with.
- Every change to the list is recorded in the Security Log (manuscript
  Figure 4.1). Saving without changing anything records nothing.
"""
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, func, select

from app.core.database import get_session
from app.core.deps import require_head_attorney
from app.core.security_monitoring import get_client_ip, limiter, log_security_event
from app.models.models import ESSU_CATEGORIES, Office, Requester, StaffUser
from app.schemas.schemas import (
    OfficeAdminOut, OfficeCreate, OfficeFromTyped, OfficeFromTypedOut, OfficeUpdate, TypedOfficeOut,
)

router = APIRouter(prefix="/offices", tags=["offices"])


def _key(name: str | None) -> str:
    """Comparison form of a name: no extra spaces, no capitals."""
    return " ".join((name or "").split()).lower()


def _find_by_name(session: Session, name: str, except_id: int | None = None) -> Office | None:
    for office in session.exec(select(Office)).all():
        if office.id != except_id and _key(office.name) == _key(name):
            return office
    return None


def _client_counts(session: Session) -> dict[int, int]:
    rows = session.exec(
        select(Requester.office_id, func.count(Requester.id))
        .where(Requester.office_id.is_not(None))
        .group_by(Requester.office_id)
    ).all()
    return {office_id: n for office_id, n in rows}


def _out(office: Office, counts: dict[int, int]) -> OfficeAdminOut:
    return OfficeAdminOut(id=office.id, name=office.name, office_type=office.office_type,
                          is_active=office.is_active, client_count=counts.get(office.id, 0))


def _log(session: Session, request: Request, event_type: str, staff: StaffUser, what: str) -> None:
    # The name comes first: the log cuts long details at the end.
    log_security_event(session, event_type, f"{staff.full_name} {what}.", get_client_ip(request))


def _typed_clients(session: Session) -> dict[str, list[Requester]]:
    """ESSU clients who typed their office under "Others", grouped by name."""
    groups: dict[str, list[Requester]] = defaultdict(list)
    rows = session.exec(
        select(Requester).where(
            Requester.office_id.is_(None),
            Requester.client_category.in_(ESSU_CATEGORIES),
            Requester.company_agency.is_not(None),
        )
    ).all()
    for client in rows:
        key = _key(client.company_agency)
        if key:
            groups[key].append(client)
    return groups


@router.get("/manage", response_model=list[OfficeAdminOut])
@limiter.limit("60/minute")
def list_offices_for_head_attorney(request: Request, session: Session = Depends(get_session),
                                   _staff: StaffUser = Depends(require_head_attorney)):
    """Every office, switched off ones too, with how many clients use each."""
    counts = _client_counts(session)
    offices = session.exec(select(Office).order_by(Office.is_active.desc(), Office.office_type, Office.name)).all()
    return [_out(o, counts) for o in offices]


@router.post("", response_model=OfficeAdminOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def add_office(request: Request, payload: OfficeCreate, session: Session = Depends(get_session),
               staff: StaffUser = Depends(require_head_attorney)):
    existing = _find_by_name(session, payload.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"\"{existing.name}\" is already on the list"
                            + ("." if existing.is_active else " (switched off). Switch it back on instead."))
    office = Office(name=payload.name, office_type=payload.office_type)
    session.add(office)
    session.commit()
    _log(session, request, "office_added", staff, f"added the office \"{payload.name}\"")
    session.refresh(office)
    return _out(office, _client_counts(session))


@router.patch("/{office_id}", response_model=OfficeAdminOut)
@limiter.limit("30/minute")
def update_office(request: Request, office_id: int, payload: OfficeUpdate, session: Session = Depends(get_session),
                  staff: StaffUser = Depends(require_head_attorney)):
    """Rename, change the type, or switch an office off/on."""
    office = session.get(Office, office_id)
    if not office:
        raise HTTPException(status_code=404, detail="Office not found.")
    old_name, changes = office.name, []
    if payload.name is not None:
        clash = _find_by_name(session, payload.name, except_id=office.id)
        if clash:
            raise HTTPException(status_code=409, detail=f"\"{clash.name}\" is already on the list.")
        if payload.name != office.name:
            changes.append(f"renamed to \"{payload.name}\"")
        office.name = payload.name
    if payload.office_type is not None:
        if payload.office_type != office.office_type:
            changes.append(f"type changed from {office.office_type} to {payload.office_type}")
        office.office_type = payload.office_type
    if payload.is_active is not None:
        if payload.is_active != office.is_active:
            changes.append("switched on" if payload.is_active else "switched off")
        office.is_active = payload.is_active
    session.add(office)
    session.commit()
    if changes:
        _log(session, request, "office_changed", staff, f"changed the office \"{old_name}\": {'; '.join(changes)}")
    session.refresh(office)
    return _out(office, _client_counts(session))


@router.get("/typed", response_model=list[TypedOfficeOut])
@limiter.limit("60/minute")
def list_typed_offices(request: Request, session: Session = Depends(get_session),
                       _staff: StaffUser = Depends(require_head_attorney)):
    """Offices ESSU clients typed because theirs wasn't on the list, most
    used first. Each shows the spelling most clients used."""
    items = []
    for key, clients in _typed_clients(session).items():
        spelling = Counter(" ".join(c.company_agency.split()) for c in clients).most_common(1)[0][0]
        match = _find_by_name(session, spelling)
        items.append(TypedOfficeOut(name=spelling, client_count=len(clients),
                                    matches_office_id=match.id if match else None,
                                    matches_office_name=match.name if match else None))
    items.sort(key=lambda i: (-i.client_count, i.name.lower()))
    return items


@router.post("/from-typed", response_model=OfficeFromTypedOut)
@limiter.limit("30/minute")
def add_typed_office(request: Request, payload: OfficeFromTyped, session: Session = Depends(get_session),
                     staff: StaffUser = Depends(require_head_attorney)):
    """Adds a typed office to the list (or uses the one already there with
    the same name) and links every client who typed it. The Head Attorney
    may correct the spelling first."""
    clients = _typed_clients(session).get(_key(payload.typed_name), [])
    if not clients:
        raise HTTPException(status_code=404, detail="No client has typed that office anymore. Reload the page.")
    office = _find_by_name(session, payload.name)
    created = office is None
    switched_on = not created and not office.is_active
    if created:
        office = Office(name=payload.name, office_type=payload.office_type)
        session.add(office)
        session.flush()
    elif switched_on:
        office.is_active = True          # switching it back on: clients are using it
        session.add(office)
    for client in clients:
        client.office_id = office.id
        client.company_agency = None     # the typed text is replaced by the link
        session.add(client)
    session.commit()
    if created:
        _log(session, request, "office_added", staff, f"added the office \"{payload.name}\", typed by clients")
    elif switched_on:
        _log(session, request, "office_changed", staff, f"changed the office \"{office.name}\": switched on")
    session.refresh(office)
    return OfficeFromTypedOut(office=_out(office, _client_counts(session)),
                              linked_clients=len(clients), created=created)
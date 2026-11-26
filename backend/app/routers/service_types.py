"""
Service Types.

- GET /service-types: public. The Submit Request dropdown loads the list of
  services from here, so a service the Head Attorney adds or renames shows
  up without editing the website's files.
- GET /service-types/manage, POST, PATCH: Head Attorney only.

Rules:
- Services can be added and edited but not deleted, since requests point to
  them. The code never changes after creation.
- Every service uses its door's step list in that order: the
  standard steps, or for "File a Complaint / Referral" the complaint steps
  (seed.STATUS_SEQUENCES), which add For Validation and Clarificatory
  Meeting. Received,
  Under Review, For Attorney Review and Approved can be switched off;
  Submitted, Processing and Completed are always kept, because the approval
  rules and the Return button depend on them. A step cannot be switched off
  while any request is at it.
- Changing the processing days affects NEW requests only; due dates already
  set are not recomputed.

Documents and categories:
- The document checklist (ServiceRequirement) is edited here and is the
  only documents list: the old free-text "required documents" box is
  retired (its column stays in the database, unused). Items are switched
  off, never deleted, so documents already uploaded keep their link.
- "Who may use this service" (ServiceCategoryAccess) is edited here too.
  A new service starts open to ESSU offices and employees, the Citizen's
  Charter default.

Every change is recorded in the Security Log (manuscript Figure 4.1).
Saving without changing anything records nothing.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import require_head_attorney
from app.core.security_monitoring import get_client_ip, limiter, log_security_event
from app.models.models import (
    CLIENT_CATEGORIES, LegalServiceRequest, ServiceCategoryAccess, ServiceRequirement, ServiceType, StaffUser, WorkflowStepTemplate,
)
from app.schemas.schemas import (
    CategoryListIn, RequirementListIn, ServiceRequirementAdminOut, ServiceTypeCreate, ServiceTypeOut,
    ServiceTypeUpdate,
)
from app.seed import DEFAULT_STATUS_SEQUENCE, RETIRED_SERVICES, STATUS_SEQUENCES

router = APIRouter(prefix="/service-types", tags=["service-types"])

REQUIRED_STEPS = {"Submitted", "Processing", "Completed"}
NEW_SERVICE_CATEGORIES = ["essu_office", "essu_employee"]   # Citizen's Charter default for a new service


def _sequence(st: ServiceType) -> list[str]:
    """All the steps this service's door can use, in order."""
    return STATUS_SEQUENCES.get(st.door, DEFAULT_STATUS_SEQUENCE)


def _steps(session: Session, service_type_id: int) -> list[WorkflowStepTemplate]:
    return session.exec(
        select(WorkflowStepTemplate)
        .where(WorkflowStepTemplate.service_type_id == service_type_id)
        .order_by(WorkflowStepTemplate.sequence_order)
    ).all()


def _requirements(session: Session, service_type_id: int, active_only: bool = True) -> list[ServiceRequirement]:
    stmt = select(ServiceRequirement).where(ServiceRequirement.service_type_id == service_type_id)
    if active_only:
        stmt = stmt.where(ServiceRequirement.is_active == True)  # noqa: E712
    return session.exec(stmt.order_by(ServiceRequirement.is_active.desc(), ServiceRequirement.sort_order,
                                      ServiceRequirement.id)).all()


def _to_out(session: Session, st: ServiceType, with_count: bool = False) -> ServiceTypeOut:
    """with_count = the Head Attorney's view (counts, full checklist, who may use it)."""
    active = _requirements(session, st.id)
    out = ServiceTypeOut(
        code=st.code,
        name=st.name,
        description=st.description,
        default_processing_days=st.default_processing_days,
        # The checklist as plain lines, for the Home page and the walk-in form.
        required_documents=[r.label if r.is_required else f"{r.label} (optional)" for r in active],
        steps=[s.status_name for s in _steps(session, st.id)],
        door=st.door,
        all_steps=_sequence(st),
        # Who may use it: the walk-in form lists only the services the client's category may request.
        categories=sorted(session.exec(
            select(ServiceCategoryAccess.client_category).where(ServiceCategoryAccess.service_type_id == st.id)
        ).all()),
    )
    if with_count:
        out.request_count = session.exec(
            select(func.count()).select_from(LegalServiceRequest)
            .where(LegalServiceRequest.service_type_id == st.id)
        ).one()
        out.requirements = [ServiceRequirementAdminOut.model_validate(r)
                            for r in _requirements(session, st.id, active_only=False)]
    return out


def _log(session: Session, request: Request, event_type: str, staff: StaffUser, what: str) -> None:
    # The name comes first: the log cuts long details at the end.
    log_security_event(session, event_type, f"{staff.full_name} {what}.", get_client_ip(request))


def _days(number: int | None) -> str:
    return "no set period" if number is None else str(number)


def _quoted(names) -> str:
    return ", ".join(f"\"{name}\"" for name in names)


def _get_or_404(session: Session, code: str) -> ServiceType:
    st = session.exec(select(ServiceType).where(ServiceType.code == code)).first()
    if not st:
        raise HTTPException(status_code=404, detail="Service not found.")
    return st


def _ensure_unique_name(session: Session, name: str, except_id: int | None = None) -> None:
    for other in session.exec(select(ServiceType)).all():
        if other.id != except_id and other.name.strip().lower() == name.lower():
            raise HTTPException(status_code=409, detail=f"There is already a service named \"{other.name}\".")


def _new_code(session: Session, name: str) -> str:
    """A fixed internal code from the name, e.g. "Notarial Certification" ->
    "notarial_certification". Never reuses a retired code, which the startup
    seed would otherwise remove."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "service"
    taken = {c for c in session.exec(select(ServiceType.code)).all()} | set(RETIRED_SERVICES)
    code, n = base, 2
    while code in taken:
        code = f"{base}_{n}"
        n += 1
    return code


@router.get("", response_model=list[ServiceTypeOut])
@limiter.limit("30/minute")
def list_service_types(request: Request, session: Session = Depends(get_session)):
    return [_to_out(session, st) for st in session.exec(select(ServiceType).order_by(ServiceType.id)).all()]


@router.get("/manage", response_model=list[ServiceTypeOut])
@limiter.limit("120/minute")
def list_for_head_attorney(
    request: Request,
    session: Session = Depends(get_session),
    _current: StaffUser = Depends(require_head_attorney),
):
    return [_to_out(session, st, with_count=True)
            for st in session.exec(select(ServiceType).order_by(ServiceType.id)).all()]


@router.post("", response_model=ServiceTypeOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def add_service_type(
    request: Request,
    payload: ServiceTypeCreate,
    session: Session = Depends(get_session),
    current: StaffUser = Depends(require_head_attorney),
):
    _ensure_unique_name(session, payload.name)
    st = ServiceType(
        code=_new_code(session, payload.name),
        name=payload.name,
        description=payload.description,
        default_processing_days=payload.default_processing_days,
        required_documents="",   # retired; the checklist is edited with PUT .../requirements
    )
    session.add(st)
    session.commit()
    session.refresh(st)
    for i, status_name in enumerate(_sequence(st), start=1):
        session.add(WorkflowStepTemplate(service_type_id=st.id, sequence_order=i, status_name=status_name))
    # Without at least one category nobody could request it.
    for category in NEW_SERVICE_CATEGORIES:
        session.add(ServiceCategoryAccess(service_type_id=st.id, client_category=category))
    session.commit()
    _log(session, request, "service_added", current, f"added the service \"{payload.name}\"")
    return _to_out(session, st, with_count=True)


@router.patch("/{code}", response_model=ServiceTypeOut)
@limiter.limit("30/minute")
def update_service_type(
    request: Request,
    code: str,
    payload: ServiceTypeUpdate,
    session: Session = Depends(get_session),
    current: StaffUser = Depends(require_head_attorney),
):
    st = _get_or_404(session, code)
    old_name, old_description, old_days, changes = st.name, st.description, st.default_processing_days, []

    if payload.name is not None and payload.name != st.name:
        _ensure_unique_name(session, payload.name, except_id=st.id)
        st.name = payload.name
        changes.append(f"renamed to \"{payload.name}\"")
    if payload.description is not None:
        st.description = payload.description or None
        if st.description != (old_description or None):
            changes.append("description edited")
    if payload.clear_processing_days:
        st.default_processing_days = None
    elif payload.default_processing_days is not None:
        st.default_processing_days = payload.default_processing_days
    if st.default_processing_days != old_days:
        changes.append(f"processing days changed from {_days(old_days)} to {_days(st.default_processing_days)}")

    if payload.optional_steps is not None:
        optional_steps = [s for s in _sequence(st) if s not in REQUIRED_STEPS]
        unknown = [s for s in payload.optional_steps if s not in optional_steps]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Only these steps can be switched on or off: {', '.join(optional_steps)}.",
            )
        wanted = REQUIRED_STEPS | set(payload.optional_steps)
        existing = {s.status_name: s for s in _steps(session, st.id)}

        # Check everything first, so a refused change leaves nothing half-done.
        for name, step in existing.items():
            if name not in wanted:
                in_use = session.exec(
                    select(func.count()).select_from(LegalServiceRequest)
                    .where(LegalServiceRequest.current_step_id == step.id)
                ).one()
                if in_use:
                    raise HTTPException(
                        status_code=409,
                        detail=f"{in_use} request(s) of this service are at \"{name}\" right now. "
                               "Move them to another step before switching it off.",
                    )

        for name, step in existing.items():
            if name not in wanted:
                session.delete(step)
                changes.append(f"step \"{name}\" switched off")
        for i, name in enumerate(_sequence(st), start=1):
            if name in wanted and name not in existing:
                session.add(WorkflowStepTemplate(service_type_id=st.id, sequence_order=i, status_name=name))
                changes.append(f"step \"{name}\" switched on")

    session.add(st)
    session.commit()
    if changes:
        _log(session, request, "service_changed", current, f"changed the service \"{old_name}\": {'; '.join(changes)}")
    session.refresh(st)
    return _to_out(session, st, with_count=True)



@router.put("/{code}/requirements", response_model=ServiceTypeOut)
@limiter.limit("30/minute")
def set_requirements(
    request: Request,
    code: str,
    payload: RequirementListIn,
    session: Session = Depends(get_session),
    current: StaffUser = Depends(require_head_attorney),
):
    """The service's whole document checklist, in order. Items
    sent with an id are kept (edited, re-ordered, or switched back on);
    items without one are added; items left out are switched off, never
    deleted, so documents already uploaded keep their link. Applies to new
    requests: the submit page checks the active list."""
    st = _get_or_404(session, code)
    existing = {r.id: r for r in _requirements(session, st.id, active_only=False)}
    seen_ids, seen_labels = set(), set()
    for item in payload.items:          # check everything first
        if item.id is not None and item.id not in existing:
            raise HTTPException(status_code=400, detail="That document is not on this service's checklist. Reload the page.")
        if item.id in seen_ids:
            raise HTTPException(status_code=400, detail="The same document is listed twice.")
        if item.label.lower() in seen_labels:
            raise HTTPException(status_code=400, detail=f"\"{item.label}\" is listed twice.")
        if item.id is not None:
            seen_ids.add(item.id)
        seen_labels.add(item.label.lower())

    added, edited, removed = [], [], []      # for the Security Log
    was_active = [r.id for r in existing.values() if r.is_active]   # in the order shown before
    reordered = ([i for i in was_active if i in seen_ids]
                 != [item.id for item in payload.items if item.id in was_active])
    for order, item in enumerate(payload.items, start=1):
        req = existing.get(item.id) if item.id is not None else None
        if req is None or not req.is_active:
            added.append(item.label)
        elif ((req.label, req.help_text or None, req.is_required)
              != (item.label, item.help_text, item.is_required)):
            edited.append(item.label)
        if req is None:
            req = ServiceRequirement(service_type_id=st.id, label=item.label)
        req.label = item.label
        req.help_text = item.help_text
        req.is_required = item.is_required
        req.sort_order = order
        req.is_active = True
        session.add(req)
    for req_id, req in existing.items():
        if req_id not in seen_ids and req.is_active:
            req.is_active = False
            session.add(req)
            removed.append(req.label)
    session.commit()
    changes = [f"{word} {_quoted(labels)}" for word, labels in
               [("added", added), ("edited", edited), ("switched off", removed)] if labels]
    if reordered:
        changes.append("order changed")
    if changes:
        _log(session, request, "service_changed", current,
             f"changed the required documents of \"{st.name}\": {'; '.join(changes)}")
    return _to_out(session, st, with_count=True)


@router.put("/{code}/categories", response_model=ServiceTypeOut)
@limiter.limit("30/minute")
def set_categories(
    request: Request,
    code: str,
    payload: CategoryListIn,
    session: Session = Depends(get_session),
    current: StaffUser = Depends(require_head_attorney),
):
    """Who may request this service online. An empty list means
    nobody can request it online; staff can still encode walk-ins. Requests
    already submitted are not affected."""
    st = _get_or_404(session, code)
    wanted = set(payload.categories)
    rows = session.exec(select(ServiceCategoryAccess).where(ServiceCategoryAccess.service_type_id == st.id)).all()
    have = {r.client_category: r for r in rows}
    for category, row in have.items():
        if category not in wanted:
            session.delete(row)
    for category in wanted - set(have):
        session.add(ServiceCategoryAccess(service_type_id=st.id, client_category=category))
    session.commit()
    changes = [f"{word} {', '.join(label for code_, label in CLIENT_CATEGORIES.items() if code_ in codes)}"
               for word, codes in [("added", wanted - set(have)), ("removed", set(have) - wanted)] if codes]
    if changes:
        _log(session, request, "service_changed", current,
             f"changed who may use \"{st.name}\": {'; '.join(changes)}")
    return _to_out(session, st, with_count=True)
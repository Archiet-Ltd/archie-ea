"""Prioritisation service — MoSCoW, WSJF, RICE backlog scoring."""
from __future__ import annotations

import logging
from typing import Optional

from app import db
from app.models.models import Requirement

logger = logging.getLogger(__name__)

_VALID_MOSCOW = {"MUST", "SHOULD", "COULD", "WONT"}


def _req_to_dict(req: Requirement) -> dict:
    from app.services import result_footer_service

    return {
        "wsjf_missing": result_footer_service.missing_wsjf_inputs(req),
        "footer_html": result_footer_service.render_footer(result_footer_service.backlog_score_footer(req)),
        "id": req.id,
        "title": req.title,
        "description": req.description,
        "story_points": req.story_points,
        "moscow_priority": req.moscow_priority,
        "business_value": req.business_value,
        "time_criticality": req.time_criticality,
        "risk_reduction": req.risk_reduction,
        "job_size": req.job_size,
        "wsjf_score": req.wsjf_score,
        "reach": req.reach,
        "impact": req.impact,
        "confidence": req.confidence,
        "rice_score": req.rice_score,
        "requirement_type": req.requirement_type,
    }


def _architecture_id_for(organization_id: int, solution_id: Optional[int]) -> Optional[int]:
    """The architecture model that belongs to a backlog requirement's solution."""
    if solution_id is None:
        return None
    from app.models.models import ArchitectureModel

    model = (
        ArchitectureModel.query.filter(
            ArchitectureModel.organization_id == organization_id,
            ArchitectureModel.solution_id == solution_id,
        )
        .order_by(ArchitectureModel.id)
        .first()
    )
    return model.id if model else None


_SOURCE_TABLE = "solution_requirements"


def _projection_for(source) -> Requirement:
    return Requirement(
        organization_id=source.organization_id,
        title=source.name,
        description=source.description,
        type="functional",
        moscow_priority=source.moscow_priority,
        archimate_element_id=source.archimate_element_id,
        architecture_id=_architecture_id_for(source.organization_id, source.solution_id),
        source_table=_SOURCE_TABLE,
        source_id=source.id,
    )


def _own_projection(source) -> Optional[Requirement]:
    """The ranked requirement this backlog requirement made, if it still has one."""
    if source.id is None:
        return None
    # A copy made in the flush just before has its source id set but not yet written.
    for item in db.session.dirty:
        if (
            isinstance(item, Requirement)
            and item.source_table == _SOURCE_TABLE
            and item.source_id == source.id
            and item.organization_id == source.organization_id
        ):
            return item
    return Requirement.query.filter(
        Requirement.source_table == _SOURCE_TABLE,
        Requirement.source_id == source.id,
        Requirement.organization_id == source.organization_id,
    ).first()


def project_backlog_requirements(organization_id: Optional[int]) -> int:
    """Backfill: give every live backlog requirement of one organisation the
    enterprise requirement prioritisation ranks, and return how many were added.

    The backlog's requirements (SolutionRequirement) are the ones people write;
    prioritisation ranks the enterprise requirement they point at through
    ``archimate_requirement_id``. New and changed backlog requirements get theirs
    when they are written (``sync_backlog_requirements``); this covers the ones
    written before that. Flushes; the caller commits. Reading never calls it.
    """
    from app.models.solution_architect_models import SolutionRequirement

    if organization_id is None:
        return 0
    pending = SolutionRequirement.query.filter(
        SolutionRequirement.organization_id == organization_id,
        SolutionRequirement.deleted_at.is_(None),
        SolutionRequirement.archimate_requirement_id.is_(None),
    ).all()
    for source in pending:
        record = _own_projection(source)
        if record is None:
            record = _projection_for(source)
            db.session.add(record)
            db.session.flush()
        source.archimate_requirement_id = record.id
    return len(pending)


def _shared_with_other_items(session, source, own: Requirement) -> bool:
    """Whether a backlog requirement other than ``source`` is linked to this ranked copy."""
    from app.models.solution_architect_models import SolutionRequirement

    for item in list(session.new) + list(session.dirty):
        if (
            isinstance(item, SolutionRequirement)
            and item is not source
            and item.deleted_at is None
            and item.archimate_requirement_id == own.id
        ):
            return True
    return (
        db.session.query(SolutionRequirement.id)
        .filter(
            SolutionRequirement.organization_id == source.organization_id,
            SolutionRequirement.archimate_requirement_id == own.id,
            SolutionRequirement.id != source.id,
            SolutionRequirement.deleted_at.is_(None),
        )
        .first()
        is not None
    )


def _drop_own_projection(session, source, own: Requirement) -> None:
    """Remove the ranked copy a backlog requirement made, unless another item is linked to it."""
    if _shared_with_other_items(session, source, own):
        return
    if source.archimate_requirement_id == own.id:
        source.archimate_requirement_id = None
    session.delete(own)


def _release_linked_copy(session, source) -> None:
    """A backlog requirement leaving takes with it the copy it was linked to by hand,
    when that copy's own maker is already gone and nothing else is linked to it."""
    from app.models.solution_architect_models import SolutionRequirement

    if source.archimate_requirement_id is None:
        return
    copy = Requirement.query.filter(
        Requirement.id == source.archimate_requirement_id,
        Requirement.organization_id == source.organization_id,
        Requirement.source_table == _SOURCE_TABLE,
    ).first()
    if copy is None or copy.source_id == source.id:
        return
    owner = SolutionRequirement.query.filter(
        SolutionRequirement.id == copy.source_id,
        SolutionRequirement.organization_id == source.organization_id,
    ).first()
    if owner is not None and owner.deleted_at is None and owner not in session.deleted:
        return
    if _shared_with_other_items(session, source, copy):
        return
    if source not in session.deleted:
        source.archimate_requirement_id = None
    session.delete(copy)


def sync_backlog_requirements(session) -> None:
    """Keep the ranked requirement in step with the backlog requirement being written.

    Runs inside the flush that writes a SolutionRequirement. Only the requirement a
    backlog requirement made itself (named by ``source_table`` / ``source_id``) is ever
    changed or removed, and only while nothing else is linked to it: one a person linked
    by hand, from either side, is left exactly as it was.
    """
    from app.models.solution_architect_models import SolutionRequirement

    removed = [
        item for item in session.deleted
        if isinstance(item, SolutionRequirement) and item.organization_id is not None
    ]
    for source in removed:
        own = _own_projection(source)
        if own is not None:
            _drop_own_projection(session, source, own)
        else:
            _release_linked_copy(session, source)
    sources = [
        item for item in list(session.new) + list(session.dirty)
        if isinstance(item, SolutionRequirement) and item.organization_id is not None
        and item not in session.deleted
    ]
    if not sources:
        return
    pending = session.info.setdefault("backlog_projection_pending", [])
    for source in sources:
        own = _own_projection(source)
        if source.deleted_at is not None:
            if own is not None:
                _drop_own_projection(session, source, own)
            else:
                _release_linked_copy(session, source)
            continue
        linked_elsewhere = source.archimate_requirement_id is not None and (
            own is None or source.archimate_requirement_id != own.id
        )
        if linked_elsewhere:
            # A person pointed this at another requirement: that one is the ranked record.
            if own is not None:
                _drop_own_projection(session, source, own)
            continue
        if own is None:
            record = _projection_for(source)
            session.add(record)
            pending.append((source, record))
        elif _shared_with_other_items(session, source, own):
            # Another item is linked to this copy by hand: it keeps what it was.
            continue
        else:
            own.title = source.name
            own.description = source.description
            own.moscow_priority = source.moscow_priority
            own.architecture_id = (
                _architecture_id_for(source.organization_id, source.solution_id) or own.architecture_id
            )
            source.archimate_requirement_id = own.id


def link_backlog_projections(session) -> None:
    """After the flush: point each new backlog requirement at the record just made."""
    for source, record in session.info.pop("backlog_projection_pending", []):
        record.source_id = source.id
        source.archimate_requirement_id = record.id


def get_backlog_prioritised(solution_id: Optional[int] = None, method: str = "wsjf") -> list[dict]:
    """Return requirements sorted by the chosen method (wsjf/rice/moscow/manual)."""
    from app.models.solution_architect_models import SolutionRequirement

    query = Requirement.query
    if solution_id is not None:
        query = query.filter_by(architecture_id=solution_id)
    # A requirement the backlog has already delivered is not ranked again.
    delivered = db.select(SolutionRequirement.archimate_requirement_id).where(
        SolutionRequirement.archimate_requirement_id.isnot(None),
        SolutionRequirement.organization_id == Requirement.organization_id,
        SolutionRequirement.deleted_at.is_(None),
        db.func.lower(SolutionRequirement.status) == "implemented",
    )
    query = query.filter(Requirement.id.notin_(delivered))

    reqs = query.all()
    items = [_req_to_dict(r) for r in reqs]

    _MOSCOW_ORDER = {"MUST": 0, "SHOULD": 1, "COULD": 2, "WONT": 3, None: 4}

    # A requirement whose score cannot be worked out yet sorts after every one that can.
    if method == "wsjf":
        items.sort(key=lambda x: (x["wsjf_score"] is None, -(x["wsjf_score"] or 0)))
    elif method == "rice":
        items.sort(key=lambda x: (x["rice_score"] is None, -(x["rice_score"] or 0)))
    elif method == "moscow":
        items.sort(key=lambda x: _MOSCOW_ORDER.get(x["moscow_priority"], 4))
    # "manual" — preserve DB order

    return items


def set_moscow(requirement_id: int, priority: str) -> dict:
    """Set MoSCoW priority for a requirement. Returns updated dict."""
    priority = (priority or "").upper().strip()
    if priority not in _VALID_MOSCOW:
        raise ValueError(f"priority must be one of {_VALID_MOSCOW}, got {priority!r}")

    req = Requirement.query.get_or_404(requirement_id)
    req.moscow_priority = priority
    # The backlog shows the same priority: write it on the requirement(s) pointing here.
    from app.models.solution_architect_models import SolutionRequirement

    for source in SolutionRequirement.query.filter_by(archimate_requirement_id=req.id).all():
        source.moscow_priority = priority
    db.session.commit()
    logger.info("MoSCoW set req=%d priority=%s", requirement_id, priority)
    return _req_to_dict(req)


def set_wsjf_components(
    requirement_id: int,
    business_value: int,
    time_criticality: int,
    risk_reduction: int,
    job_size: int,
) -> dict:
    """Set WSJF scoring components and return updated dict with computed wsjf_score."""
    req = Requirement.query.filter_by(id=requirement_id).first_or_404()
    req.business_value = max(business_value, 1)
    req.time_criticality = max(time_criticality, 1)
    req.risk_reduction = max(risk_reduction, 1)
    req.job_size = max(job_size, 1)
    db.session.commit()
    logger.info("WSJF set req=%d score=%s", requirement_id, req.wsjf_score)
    return _req_to_dict(req)


def clear_wsjf_components(requirement_id: int) -> dict:
    """Empty the four WSJF inputs, so the requirement reads as not worked out."""
    req = Requirement.query.filter_by(id=requirement_id).first_or_404()
    req.business_value = req.time_criticality = req.risk_reduction = req.job_size = None
    db.session.commit()
    logger.info("WSJF cleared req=%d", requirement_id)
    return _req_to_dict(req)


def clear_rice_components(requirement_id: int) -> dict:
    """Empty reach, impact and confidence. Story points stay: they are the estimate, used beyond this score."""
    req = Requirement.query.filter_by(id=requirement_id).first_or_404()
    req.reach = req.impact = req.confidence = None
    db.session.commit()
    logger.info("RICE cleared req=%d", requirement_id)
    return _req_to_dict(req)


def set_rice_components(
    requirement_id: int,
    reach: int,
    impact: int,
    confidence: int,
    story_points: Optional[int] = None,
) -> dict:
    """Set RICE scoring components and return updated dict with computed rice_score.

    Story points are the effort the score divides by; they are written only when given."""
    req = Requirement.query.filter_by(id=requirement_id).first_or_404()
    if story_points is not None:
        req.story_points = max(story_points, 1)
    req.reach = max(reach, 0)
    req.impact = max(min(impact, 5), 1)
    req.confidence = max(min(confidence, 100), 0)
    db.session.commit()
    logger.info("RICE set req=%d score=%s", requirement_id, req.rice_score)
    return _req_to_dict(req)

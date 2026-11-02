"""Prioritisation service — MoSCoW, WSJF, RICE backlog scoring (TPM-006)."""
from __future__ import annotations

import logging
from typing import Optional

from app import db
from app.models.models import Requirement

logger = logging.getLogger(__name__)

_VALID_MOSCOW = {"MUST", "SHOULD", "COULD", "WONT"}


def _req_to_dict(req: Requirement) -> dict:
    return {
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


def _projection_for(source) -> Requirement:
    return Requirement(
        organization_id=source.organization_id,
        title=source.name,
        description=source.description,
        type="functional",
        moscow_priority=source.moscow_priority,
        archimate_element_id=source.archimate_element_id,
        architecture_id=_architecture_id_for(source.organization_id, source.solution_id),
    )


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
        record = _projection_for(source)
        db.session.add(record)
        db.session.flush()
        source.archimate_requirement_id = record.id
    return len(pending)


def sync_backlog_requirements(session) -> None:
    """Keep the ranked requirement in step with the backlog requirement being written.

    Runs inside the flush that writes a SolutionRequirement: a new or edited live
    one gets (or updates) its enterprise requirement, and a deleted one takes its
    enterprise requirement with it unless another live backlog requirement still
    points there.
    """
    from app.models.solution_architect_models import SolutionRequirement

    sources = [
        item for item in list(session.new) + list(session.dirty)
        if isinstance(item, SolutionRequirement) and item.organization_id is not None
    ]
    if not sources:
        return
    pending = session.info.setdefault("backlog_projection_pending", [])
    for source in sources:
        if source.deleted_at is not None:
            record_id = source.archimate_requirement_id
            if record_id is None:
                continue
            source.archimate_requirement_id = None
            still_used = SolutionRequirement.query.filter(
                SolutionRequirement.archimate_requirement_id == record_id,
                SolutionRequirement.deleted_at.is_(None),
                SolutionRequirement.id != source.id,
            ).first()
            record = Requirement.query.filter(
                Requirement.id == record_id, Requirement.organization_id == source.organization_id
            ).first()
            if record is not None and still_used is None:
                session.delete(record)
        elif source.archimate_requirement_id is None:
            record = _projection_for(source)
            session.add(record)
            pending.append((source, record))
        else:
            record = Requirement.query.filter(
                Requirement.id == source.archimate_requirement_id,
                Requirement.organization_id == source.organization_id,
            ).first()
            if record is not None:
                record.title = source.name
                record.description = source.description
                record.moscow_priority = source.moscow_priority
                record.architecture_id = (
                    _architecture_id_for(source.organization_id, source.solution_id)
                    or record.architecture_id
                )


def link_backlog_projections(session) -> None:
    """After the flush: point each new backlog requirement at the record just made."""
    for source, record in session.info.pop("backlog_projection_pending", []):
        source.archimate_requirement_id = record.id


def get_backlog_prioritised(solution_id: Optional[int] = None, method: str = "wsjf") -> list[dict]:
    """Return requirements sorted by the chosen method (wsjf/rice/moscow/manual)."""
    from app.models.solution_architect_models import SolutionRequirement

    query = Requirement.query
    # A requirement whose only backlog requirement was deleted is not ranked.
    deleted_only = (
        db.select(SolutionRequirement.id)
        .where(
            SolutionRequirement.archimate_requirement_id == Requirement.id,
            SolutionRequirement.deleted_at.is_not(None),
        )
        .exists()
    )
    live = (
        db.select(SolutionRequirement.id)
        .where(
            SolutionRequirement.archimate_requirement_id == Requirement.id,
            SolutionRequirement.deleted_at.is_(None),
        )
        .exists()
    )
    query = query.filter(db.or_(~deleted_only, live))
    if solution_id is not None:
        query = query.filter_by(architecture_id=solution_id)

    reqs = query.all()
    items = [_req_to_dict(r) for r in reqs]

    _MOSCOW_ORDER = {"MUST": 0, "SHOULD": 1, "COULD": 2, "WONT": 3, None: 4}

    if method == "wsjf":
        items.sort(key=lambda x: x["wsjf_score"], reverse=True)
    elif method == "rice":
        items.sort(key=lambda x: x["rice_score"], reverse=True)
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
    logger.info("TPM-006 MoSCoW set req=%d priority=%s", requirement_id, priority)
    return _req_to_dict(req)


def set_wsjf_components(
    requirement_id: int,
    business_value: int,
    time_criticality: int,
    risk_reduction: int,
    job_size: int,
) -> dict:
    """Set WSJF scoring components and return updated dict with computed wsjf_score."""
    req = Requirement.query.get_or_404(requirement_id)
    req.business_value = max(business_value, 1)
    req.time_criticality = max(time_criticality, 1)
    req.risk_reduction = max(risk_reduction, 1)
    req.job_size = max(job_size, 1)
    db.session.commit()
    logger.info("TPM-006 WSJF set req=%d score=%.2f", requirement_id, req.wsjf_score)
    return _req_to_dict(req)


def set_rice_components(
    requirement_id: int,
    reach: int,
    impact: int,
    confidence: int,
) -> dict:
    """Set RICE scoring components and return updated dict with computed rice_score."""
    req = Requirement.query.get_or_404(requirement_id)
    req.reach = max(reach, 0)
    req.impact = max(min(impact, 5), 1)
    req.confidence = max(min(confidence, 100), 0)
    db.session.commit()
    logger.info("TPM-006 RICE set req=%d score=%.2f", requirement_id, req.rice_score)
    return _req_to_dict(req)

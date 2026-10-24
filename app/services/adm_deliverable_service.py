"""ADM Deliverable Service — the TOGAF checklist and a solution's own deliverables against it."""

from datetime import date
from typing import Any, Dict, List

from app import db

# TOGAF standard deliverables per phase
_PHASE_DELIVERABLES: Dict[str, List[Dict[str, str]]] = {
    "A": [
        {
            "name": "Architecture Vision document",
            "description": "High-level aspirational view of target architecture",
        },
        {
            "name": "Statement of Architecture Work",
            "description": "Defines scope and approach for the architecture engagement",
        },
        {
            "name": "Architecture Principles",
            "description": "Governing principles for architecture decision-making",
        },
    ],
    "B": [
        {
            "name": "Business Architecture document",
            "description": "Detailed business architecture definition",
        },
        {
            "name": "Architecture Definition Document (business view)",
            "description": "Architecture Definition Document covering the Business Architecture",
        },
        {
            "name": "Gap Analysis",
            "description": "Analysis of gaps between baseline and target business architecture",
        },
    ],
    "C": [
        {
            "name": "Application Architecture",
            "description": "Application architecture definition and views",
        },
        {
            "name": "Data Architecture",
            "description": "Data architecture definition and views",
        },
        {
            "name": "Architecture Definition Document update",
            "description": "Updated Architecture Definition Document covering Information Systems",
        },
    ],
    "D": [
        {
            "name": "Technology Architecture",
            "description": "Technology architecture definition and views",
        },
        {
            "name": "Architecture Definition Document (tech)",
            "description": "Updated Architecture Definition Document covering Technology Architecture",
        },
        {
            "name": "Architecture Roadmap update",
            "description": "Updated Architecture Roadmap incorporating Technology Architecture",
        },
    ],
    "E": [
        {
            "name": "Implementation & Migration Plan",
            "description": "Plan for implementing and migrating to the target architecture",
        },
        {
            "name": "Architecture Roadmap",
            "description": "Prioritized list of work packages to realize the target architecture",
        },
        {
            "name": "Transition Architecture",
            "description": "Architectures describing transitional states",
        },
    ],
    "F": [
        {
            "name": "Finalized Implementation Plan",
            "description": "Completed and approved implementation and migration plan",
        },
        {
            "name": "Architecture Contract",
            "description": "Joint agreement on deliverables and quality of architecture work",
        },
    ],
    "G": [
        {
            "name": "Compliance Assessment",
            "description": "Assessment of implementation conformance against architecture contracts",
        },
        {
            "name": "Architecture Contract execution",
            "description": "Monitoring and reporting on implementation governance",
        },
    ],
    "H": [
        {
            "name": "Change Request",
            "description": "Formal request for architecture change",
        },
        {
            "name": "Architecture Update",
            "description": "Updated architecture documents reflecting approved changes",
        },
        {
            "name": "Lessons Learned",
            "description": "Documented learnings from the architecture engagement",
        },
    ],
    "Requirements": [
        {
            "name": "Requirements Impact Assessment",
            "description": "Ongoing assessment of requirements impact on architecture (all phases)",
        },
    ],
}


def seed_deliverables() -> int:
    """Insert template deliverables for all 9 phases if not already present.

    Returns the count of newly inserted rows (0 if already seeded).
    """
    from app.models.adm_deliverable import ADMDeliverable

    inserted = 0
    for phase, items in _PHASE_DELIVERABLES.items():
        for item in items:
            existing = ADMDeliverable.query.filter_by(  # model-safety-ok: seed loop, runs once on first boot
                phase=phase, name=item["name"], is_template=True
            ).first()  # model-safety-ok
            if not existing:
                db.session.add(
                    ADMDeliverable(
                        phase=phase,
                        name=item["name"],
                        description=item["description"],
                        is_template=True,
                    )
                )
                inserted += 1

    if inserted:
        db.session.commit()
    return inserted


def _own_deliverables(solution_id: int) -> list:
    """The deliverables linked to one solution (the canonical store)."""
    from app.models.implementation_migration import Deliverable
    from app.models.solution_models import solution_deliverables

    return (
        db.session.query(Deliverable)
        .join(
            solution_deliverables,
            solution_deliverables.c.deliverable_id == Deliverable.id,
        )
        .filter(solution_deliverables.c.solution_id == solution_id)
        .order_by(Deliverable.id)
        .all()
    )


def _item(deliverable) -> Dict[str, Any]:
    status = deliverable.delivery_status or "planned"
    return {
        "id": deliverable.id,
        "name": deliverable.name,
        "status": status,
        "complete": status == "completed",
    }


def solution_checklist(solution) -> Dict[str, Any]:
    """A solution's deliverables set against the TOGAF template of its phase.

    Each template item carries the solution's matching deliverable, or None
    (shown as "Not started"). Deliverables with no template item in this
    phase come back under ``also_produced``. Completion is read from the
    deliverable's own status.
    """
    from app.models.adm_deliverable import ADMDeliverable

    phase = (solution.adm_phase or "").upper() or None
    templates = (
        ADMDeliverable.query.filter_by(phase=phase, is_template=True)
        .order_by(ADMDeliverable.id)
        .all()
        if phase
        else []
    )
    own = _own_deliverables(solution.id)
    by_template: Dict[int, Any] = {}
    for d in own:
        if d.adm_template_id is not None:
            by_template.setdefault(d.adm_template_id, d)
    matched = set()
    checklist = []
    for t in templates:
        d = by_template.get(t.id)
        if d is not None:
            matched.add(d.id)
        checklist.append(
            {
                "template_id": t.id,
                "name": t.name,
                "description": t.description,
                "deliverable": _item(d) if d is not None else None,
            }
        )
    also = [_item(d) for d in own if d.id not in matched]
    return {
        "phase": phase,
        "checklist": checklist,
        "also_produced": also,
        "total": len(own),
        "complete": sum(1 for d in own if d.delivery_status == "completed"),
    }


def solution_deliverable_counts(solution_ids: List[int]) -> Dict[int, Dict[str, int]]:
    """Own and complete deliverable counts for several solutions, in one query."""
    from app.models.implementation_migration import Deliverable
    from app.models.solution_models import solution_deliverables

    if not solution_ids:
        return {}
    rows = (
        db.session.query(
            solution_deliverables.c.solution_id,
            Deliverable.delivery_status,
            db.func.count(Deliverable.id),
        )
        .join(Deliverable, Deliverable.id == solution_deliverables.c.deliverable_id)
        .filter(solution_deliverables.c.solution_id.in_(solution_ids))
        .group_by(solution_deliverables.c.solution_id, Deliverable.delivery_status)
        .all()
    )
    counts: Dict[int, Dict[str, int]] = {}
    for sid, status, n in rows:
        entry = counts.setdefault(sid, {"total": 0, "complete": 0})
        entry["total"] += n
        if status == "completed":
            entry["complete"] += n
    return counts


def set_complete(deliverable_id: int, complete: bool) -> Dict[str, Any]:
    """Mark a solution deliverable complete, or take it back to in progress.

    The tick is the deliverable's own status; nothing else records it.
    """
    from app.models.implementation_migration import Deliverable

    # A query, not Session.get(): a cached row would skip the tenant fence.
    deliverable = Deliverable.query.filter_by(id=deliverable_id).first()
    if deliverable is None:
        raise ValueError(f"Deliverable {deliverable_id} not found")
    if complete:
        deliverable.delivery_status = "completed"
        deliverable.delivered_date = date.today()
    else:
        deliverable.delivery_status = "in_progress"
        deliverable.delivered_date = None
    db.session.commit()
    return _item(deliverable)

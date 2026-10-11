"""ADM Deliverable Service — seed data and checklist state management."""

from datetime import datetime
from typing import Any, Dict, List, Optional

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


def get_phase_deliverables(
    phase: str, board_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Return template deliverables for *phase* with checked state for *board_id*."""
    from app.models.adm_deliverable import ADMDeliverable, ADMDeliverableCheck

    deliverables = ADMDeliverable.query.filter_by(phase=phase, is_template=True).all()
    result = []
    for d in deliverables:
        check = None
        if board_id is not None:
            check = ADMDeliverableCheck.query.filter_by(  # model-safety-ok: bounded by phase deliverables (<10 rows)
                deliverable_id=d.id, board_id=board_id
            ).first()  # model-safety-ok
        item = d.to_dict()
        item["checked"] = check.checked if check else False
        item["check_id"] = check.id if check else None
        result.append(item)
    return result


def toggle_deliverable(
    deliverable_id: int, board_id: Optional[int], checked: bool
) -> Dict[str, Any]:
    """Persist the checked state of a deliverable on a board.

    Creates the check row on first use. Returns the updated check dict.
    """
    from app.models.adm_deliverable import ADMDeliverable, ADMDeliverableCheck

    deliverable = ADMDeliverable.query.get(deliverable_id)
    if deliverable is None:
        raise ValueError(f"Deliverable {deliverable_id} not found")

    check = ADMDeliverableCheck.query.filter_by(
        deliverable_id=deliverable_id, board_id=board_id
    ).first()

    if check is None:
        check = ADMDeliverableCheck(
            deliverable_id=deliverable_id,
            board_id=board_id,
        )
        db.session.add(check)

    check.checked = checked
    check.checked_at = datetime.utcnow() if checked else None
    db.session.commit()
    return check.to_dict()

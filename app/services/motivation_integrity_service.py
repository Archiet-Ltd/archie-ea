"""Motivation-layer model-integrity checks (REQ 2763 / WP 2895).

Pattern reused from the two existing checks that already answer "is this
motivation element actually connected to the rest of the model", rather than
inventing a new validation framework:

- ``app/modules/architecture/services/archimate_validator.py::_validate_traceability``
  builds an outbound/inbound ``ArchiMateRelationship`` map keyed by element id
  and flags a ``Requirement`` with neither as "orphaned - no realizing
  relationships detected".
- ``app/modules/architecture/routes/archimate_crud/routes.py::api_health_scorecard``'s
  "Motivation Integrity" test finds Driver/Goal elements by
  ``ArchiMateElement.type`` membership and counts how many have a
  relationship to the other, the same shape used below.

This module does the same lookup for Stakeholder -> {Driver, Goal,
Capability} and is read by the motivation-page summary card and by
``GET /architecture/api/motivation/orphaned-stakeholders``.
"""

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
from app.models.motivation import Stakeholder

# Stakeholder (table `stakeholders`) carries no explicit confirmed/draft
# status column. A "draft" stakeholder proposed through the AI-assisted
# solution-design journey lives in a separate table (SolutionStakeholder,
# app/models/solution_stakeholder.py) until a person accepts the proposal and
# DomainPromotionService (app/modules/architecture_assistant/domain_promotion.py)
# promotes it into this table. So every row this function reads is already a
# person-confirmed stakeholder, and there is nothing to filter on here.
# TODO: if a `status`/`confirmed` column is ever added directly to
# Stakeholder, filter on it explicitly here instead of assuming every row
# qualifies.

_LINKING_TYPES = {"driver", "goal", "capability"}


def find_orphaned_stakeholders(organization_id):
    """Confirmed stakeholders in ``organization_id`` with no relationship to
    any Driver, Goal, or Capability element.

    Returns ``{"count": int, "orphaned": [{"id": int, "name": str}, ...]}``.
    """
    stakeholders = Stakeholder.query.filter(
        Stakeholder.organization_id == organization_id
    ).all()

    element_ids = {s.archimate_element_id for s in stakeholders if s.archimate_element_id}

    linked_element_ids = set()
    if element_ids:
        rels = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.organization_id == organization_id,
            db.or_(
                ArchiMateRelationship.source_id.in_(element_ids),
                ArchiMateRelationship.target_id.in_(element_ids),
            ),
        ).all()

        other_ids = set()
        for rel in rels:
            if rel.source_id in element_ids:
                other_ids.add(rel.target_id)
            if rel.target_id in element_ids:
                other_ids.add(rel.source_id)

        other_types = {}
        if other_ids:
            other_types = {
                e.id: (e.type or "").lower()
                for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(other_ids)).all()
            }

        for rel in rels:
            for this_id, other_id in ((rel.source_id, rel.target_id), (rel.target_id, rel.source_id)):
                if this_id in element_ids and other_types.get(other_id) in _LINKING_TYPES:
                    linked_element_ids.add(this_id)

    orphaned = [
        {"id": s.id, "name": s.name}
        for s in stakeholders
        if not s.archimate_element_id or s.archimate_element_id not in linked_element_ids
    ]
    return {"count": len(orphaned), "orphaned": orphaned}

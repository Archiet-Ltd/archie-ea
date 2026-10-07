"""R1-B38 PR 2: an initiative's capability links.

Mirrors app/modules/architecture/services/application_technology_links.py's
own pattern exactly: a link is the join-table row (strategic_initiative_
capabilities, for the strategy-answer page's own queries) AND a real
ArchiMate "serving" relationship (initiative -> capability), written through
ArchiMateRelationshipService -- so the strategy answer and anything else that
walks relationships see the same link, never a second, text-only record.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import db
from app.models import ArchiMateElement
from app.models.business_capabilities import BusinessCapability
from app.models.strategic import StrategicInitiative, strategic_initiative_capabilities
from app.modules.architecture.services.archimate_relationship_service import (
    ArchiMateRelationshipService,
)

SERVING = "serving"


class InitiativeCapabilityLinkError(Exception):
    """A refused link, with the HTTP status and the sentence the person sees."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _initiative_and_element(initiative_id: int):
    initiative = db.session.execute(
        db.select(StrategicInitiative).where(StrategicInitiative.id == initiative_id)
    ).scalar_one_or_none()
    if initiative is None:
        raise InitiativeCapabilityLinkError("Initiative not found.", 404)
    element = None
    if initiative.archimate_element_id:
        element = db.session.execute(
            db.select(ArchiMateElement).where(
                ArchiMateElement.id == initiative.archimate_element_id
            )
        ).scalar_one_or_none()
    if element is None:
        raise InitiativeCapabilityLinkError(
            "This initiative has no architecture element yet, so nothing can be linked to it.",
            409,
        )
    return initiative, element


def _contribution_level(initiative_id: int, capability_id: int) -> Optional[str]:
    row = db.session.execute(
        db.select(strategic_initiative_capabilities.c.contribution_level).where(
            strategic_initiative_capabilities.c.strategic_initiative_id == initiative_id,
            strategic_initiative_capabilities.c.capability_id == capability_id,
        )
    ).scalar_one_or_none()
    return row


def _link_dict(initiative_id: int, capability: BusinessCapability) -> Dict[str, Any]:
    return {
        "capability_id": capability.id,
        "name": capability.name,
        "contribution_level": _contribution_level(initiative_id, capability.id),
    }


def list_links(initiative_id: int) -> List[Dict[str, Any]]:
    """This initiative's linked capabilities, by name."""
    initiative, _ = _initiative_and_element(initiative_id)
    return [
        _link_dict(initiative_id, c) for c in sorted(initiative.capabilities, key=lambda c: c.name)
    ]


def add_link(
    initiative_id: int, capability_id: int, *, contribution_level: Optional[str] = None,
) -> Dict[str, Any]:
    """Record that this initiative serves the chosen capability: a join-table
    row for the strategy answer's own queries, plus a real "serving"
    ArchiMateRelationship so relationship-walking surfaces see it too.
    Flushes, never commits -- the caller owns the transaction."""
    initiative, initiative_element = _initiative_and_element(initiative_id)
    capability = db.session.execute(
        db.select(BusinessCapability).where(BusinessCapability.id == capability_id)
    ).scalar_one_or_none()
    if capability is None:
        raise InitiativeCapabilityLinkError("That capability was not found.", 404)
    if capability.organization_id != initiative.organization_id:
        raise InitiativeCapabilityLinkError("That capability belongs to another organisation.", 403)
    if capability in initiative.capabilities:
        raise InitiativeCapabilityLinkError(
            "%s is already linked to this initiative." % capability.name, 409,
        )
    if not capability.archimate_element_id:
        raise InitiativeCapabilityLinkError(
            "This capability has no architecture element yet, so it cannot be linked.", 409,
        )
    capability_element = db.session.execute(
        db.select(ArchiMateElement).where(ArchiMateElement.id == capability.archimate_element_id)
    ).scalar_one_or_none()
    if capability_element is None:
        raise InitiativeCapabilityLinkError("That capability's architecture element was not found.", 404)

    is_valid, reason = ArchiMateRelationshipService.validate_relationship(
        initiative_element, capability_element, SERVING
    )
    if not is_valid:
        raise InitiativeCapabilityLinkError(reason, 400)

    relationship = ArchiMateRelationshipService.create_relationship(
        initiative_element,
        capability_element,
        SERVING,
        initiative_element.architecture_id or capability_element.architecture_id,
    )
    if relationship is None:
        raise InitiativeCapabilityLinkError("The link could not be saved.", 500)

    initiative.capabilities.append(capability)
    # The relationship-append above only queues the join-table INSERT; a
    # Core UPDATE statement does not autoflush the session first (review
    # finding, SQLAlchemy 2.0), so without this explicit flush the UPDATE
    # below ran before the row existed, matched zero rows, and silently
    # dropped contribution_level with no error.
    db.session.flush()
    if contribution_level is not None:
        db.session.execute(
            strategic_initiative_capabilities.update()
            .where(
                strategic_initiative_capabilities.c.strategic_initiative_id == initiative.id,
                strategic_initiative_capabilities.c.capability_id == capability.id,
            )
            .values(contribution_level=contribution_level)
        )
        db.session.flush()
    return _link_dict(initiative.id, capability)


def remove_link(initiative_id: int, capability_id: int) -> None:
    """Remove this initiative's link to a capability (join-table row and the
    ArchiMate relationship both). Flushes, never commits."""
    initiative, initiative_element = _initiative_and_element(initiative_id)
    capability = db.session.execute(
        db.select(BusinessCapability).where(BusinessCapability.id == capability_id)
    ).scalar_one_or_none()
    if capability is None or capability not in initiative.capabilities:
        raise InitiativeCapabilityLinkError("That link was not found on this initiative.", 404)

    initiative.capabilities.remove(capability)

    if capability.archimate_element_id:
        from app.models import ArchiMateRelationship

        relationship = db.session.execute(
            db.select(ArchiMateRelationship).where(
                ArchiMateRelationship.source_id == initiative_element.id,
                ArchiMateRelationship.target_id == capability.archimate_element_id,
                db.func.lower(ArchiMateRelationship.type) == SERVING,
            )
        ).scalar_one_or_none()
        if relationship is not None:
            db.session.delete(relationship)
    db.session.flush()

"""What an application runs on: its links to nodes and system software.

A link is an ordinary ArchiMate ``realization`` relationship written through
``ArchiMateRelationshipService.create_relationship`` -- the node (or system
software) is the source and the application's own element is the target. It is
not a junction table and not a text field, so everything that already walks
relationships (the impact answer behind Ask and the Twin map) sees the
application downstream of the node with no change of its own.

Every read here goes through ``TenantMixin`` models inside the caller's tenant
context: an element, application or relationship of another organisation is
simply not found.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
from app.models.application_portfolio import ApplicationComponent
from app.modules.architecture.services.archimate_relationship_service import (
    ArchiMateRelationshipService,
)

REALIZATION = "realization"

# Element types an application can be mapped onto, keyed by their normalised
# spelling (imports write "Node", "node" and "system_software" alike).
TECHNOLOGY_TYPES = {"node": "Node", "systemsoftware": "System software"}


class TechnologyLinkError(Exception):
    """A refused link, with the HTTP status and the sentence the person sees."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _normalised_type(element_type: Optional[str]) -> str:
    return (element_type or "").replace("_", "").replace(" ", "").lower()


def is_technology_element(element: ArchiMateElement) -> bool:
    return _normalised_type(element.type) in TECHNOLOGY_TYPES


def _application_and_element(application_id: int) -> Tuple[ApplicationComponent, ArchiMateElement]:
    application = db.session.execute(
        db.select(ApplicationComponent).where(ApplicationComponent.id == application_id)
    ).scalar_one_or_none()
    if application is None:
        raise TechnologyLinkError("Application not found.", 404)
    element = None
    if application.archimate_element_id:
        element = db.session.execute(
            db.select(ArchiMateElement).where(
                ArchiMateElement.id == application.archimate_element_id
            )
        ).scalar_one_or_none()
    if element is None:
        raise TechnologyLinkError(
            "This application has no architecture element yet, so nothing can be mapped to it.",
            409,
        )
    return application, element


def _link_dict(relationship: ArchiMateRelationship, element: ArchiMateElement) -> Dict[str, Any]:
    return {
        "relationship_id": relationship.id,
        "element_id": element.id,
        "name": element.name,
        "type": element.type,
        "type_label": TECHNOLOGY_TYPES.get(_normalised_type(element.type), element.type),
    }


def list_links(application_id: int) -> List[Dict[str, Any]]:
    """The nodes and system software this application is realised by, by name."""
    _, app_element = _application_and_element(application_id)
    rows = db.session.execute(
        db.select(ArchiMateRelationship, ArchiMateElement)
        .join(ArchiMateElement, ArchiMateElement.id == ArchiMateRelationship.source_id)
        .where(
            ArchiMateRelationship.target_id == app_element.id,
            db.func.lower(ArchiMateRelationship.type) == REALIZATION,
        )
        .order_by(ArchiMateElement.name)
    ).all()
    return [_link_dict(rel, el) for rel, el in rows if is_technology_element(el)]


def add_link(application_id: int, element_id: int, *, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Record that the chosen node or system software realises the application.

    Flushes, never commits: the caller owns the transaction.
    """
    _, app_element = _application_and_element(application_id)
    technology = db.session.execute(
        db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
    ).scalar_one_or_none()
    if technology is None:
        raise TechnologyLinkError("That node or system software was not found.", 404)
    if not is_technology_element(technology):
        raise TechnologyLinkError("Choose a node or system software.", 400)

    already = db.session.execute(
        db.select(ArchiMateRelationship.id).where(
            ArchiMateRelationship.source_id == technology.id,
            ArchiMateRelationship.target_id == app_element.id,
            db.func.lower(ArchiMateRelationship.type) == REALIZATION,
        )
    ).first()
    if already is not None:
        raise TechnologyLinkError("%s is already mapped to this application." % technology.name, 409)

    is_valid, reason = ArchiMateRelationshipService.validate_relationship(
        technology, app_element, REALIZATION
    )
    if not is_valid:
        raise TechnologyLinkError(reason, 400)

    relationship = ArchiMateRelationshipService.create_relationship(
        technology,
        app_element,
        REALIZATION,
        app_element.architecture_id or technology.architecture_id,
        properties={"created_by_id": user_id} if user_id is not None else None,
    )
    if relationship is None:
        raise TechnologyLinkError("The link could not be saved.", 500)
    return _link_dict(relationship, technology)


def remove_link(application_id: int, relationship_id: int) -> None:
    """Delete one of this application's technology links. Flushes, never commits."""
    _, app_element = _application_and_element(application_id)
    relationship = db.session.execute(
        db.select(ArchiMateRelationship).where(
            ArchiMateRelationship.id == relationship_id,
            ArchiMateRelationship.target_id == app_element.id,
            db.func.lower(ArchiMateRelationship.type) == REALIZATION,
        )
    ).scalar_one_or_none()
    if relationship is None:
        raise TechnologyLinkError("That technology link was not found on this application.", 404)
    db.session.delete(relationship)
    db.session.flush()

"""Element ownership: the one writer of owner records for any element type.

Writes through the one ownership record (``ApplicationOwner``) using its
``element_type`` / ``element_id`` reference. ``set_capability_owner`` (the
capability owner service) and the AI system owner route both call
``set_element_owner``; there is no second implementation of the checks.
"""
from __future__ import annotations

from typing import Callable, Optional

from app import db
from app.models.application_owner import ApplicationOwner


class CrossOrganisationElementOwner(ValueError):
    """The chosen owner, or the element itself, is not in this organisation."""


class InvalidOwnershipType(ValueError):
    """The ownership type is not one the ownership record allows."""


def _tenant_capability(element_id: int, organization_id: int):
    from app.services.capability_ownership_service import get_tenant_capability

    return get_tenant_capability(element_id, organization_id)


def _tenant_ai_system(element_id: int, organization_id: int):
    from app.models.models import ArchiMateElement
    from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

    element = ArchiMateElement.query.filter_by(
        id=element_id, organization_id=organization_id,
    ).first()
    return element if element is not None and is_ai_system(element) else None


# element_type -> (lookup, message when the element is not in this organisation)
_ELEMENTS: dict[str, tuple[Callable, str]] = {
    "capability": (_tenant_capability, "That capability does not belong to this organisation."),
    "ai_system": (_tenant_ai_system, "That AI system does not belong to this organisation."),
}


def set_element_owner(
    *,
    element_type: str,
    element_id: int,
    user_id: int,
    organization_id: int,
    ownership_type: str = "primary",
    assigned_by: Optional[int] = None,
    replace_primary: bool = False,
) -> ApplicationOwner:
    """Record *user_id* as an owner of the element.

    Refuses (``CrossOrganisationElementOwner``) an element or an owner from
    another organisation, and (``InvalidOwnershipType``) a type the ownership
    record does not allow, rather than store it. With ``replace_primary`` a new
    primary owner replaces the existing primary.
    """
    from app.models.user import User

    if element_type not in _ELEMENTS:
        raise ValueError(f"Unsupported element type '{element_type}'")
    if ownership_type not in ApplicationOwner.OWNERSHIP_TYPES:
        raise InvalidOwnershipType(
            f"Invalid ownership type '{ownership_type}'. "
            f"Must be one of {', '.join(ApplicationOwner.OWNERSHIP_TYPES)}"
        )

    lookup, missing_message = _ELEMENTS[element_type]
    if lookup(element_id, organization_id) is None:
        raise CrossOrganisationElementOwner(missing_message)

    owner = User.query.filter_by(id=user_id, organization_id=organization_id).first()
    if owner is None:
        raise CrossOrganisationElementOwner("That user does not belong to this organisation.")

    existing = ApplicationOwner.query.filter_by(
        element_type=element_type,
        element_id=element_id,
        user_id=user_id,
        ownership_type=ownership_type,
        organization_id=organization_id,
    ).first()
    if existing is not None:
        return existing

    if replace_primary and ownership_type == "primary":
        for row in ApplicationOwner.query.filter_by(
            element_type=element_type,
            element_id=element_id,
            organization_id=organization_id,
            ownership_type="primary",
        ).all():
            db.session.delete(row)

    record = ApplicationOwner(
        element_type=element_type,
        element_id=element_id,
        user_id=user_id,
        ownership_type=ownership_type,
        assigned_by=assigned_by,
        organization_id=organization_id,
    )
    db.session.add(record)
    db.session.commit()
    return record

"""Capability ownership (R1-B03 PR 2).

Writes through the one ownership record (ApplicationOwner, R1-B03) using its
element_type/element_id reference rather than a second owner table or a new
owner column on UnifiedCapability -- both forbidden by the brief.
"""
from __future__ import annotations

from typing import Optional

from app import db
from app.models.application_owner import ApplicationOwner
from app.models.unified_capability import UnifiedCapability

ELEMENT_TYPE_CAPABILITY = "capability"


class CrossOrganisationCapabilityOwner(ValueError):
    """The chosen owner, or the capability itself, is not in this organisation."""


def get_tenant_capability(capability_id: int, organization_id: int) -> Optional[UnifiedCapability]:
    """The capability if it exists and belongs to this organisation, else
    None -- a route uses this to 404 a nonexistent or cross-org id rather
    than render a page for an entity that does not exist."""
    return UnifiedCapability.query.filter_by(
        id=capability_id, organization_id=organization_id,
    ).first()


def _get_tenant_capability(capability_id: int, organization_id: int) -> UnifiedCapability:
    capability = get_tenant_capability(capability_id, organization_id)
    if capability is None:
        raise CrossOrganisationCapabilityOwner(
            "That capability does not belong to this organisation."
        )
    return capability


def set_capability_owner(
    *,
    capability_id: int,
    user_id: int,
    organization_id: int,
    ownership_type: str = "primary",
    assigned_by: Optional[int] = None,
) -> ApplicationOwner:
    """Refuses (CrossOrganisationCapabilityOwner) a capability or an owner
    from another organisation, rather than store it -- same fencing as the
    application owner writer."""
    from app.models.user import User

    _get_tenant_capability(capability_id, organization_id)

    owner = User.query.filter_by(
        id=user_id, organization_id=organization_id,
    ).first()
    if owner is None:
        raise CrossOrganisationCapabilityOwner(
            "That user does not belong to this organisation."
        )

    existing = ApplicationOwner.query.filter_by(
        element_type=ELEMENT_TYPE_CAPABILITY,
        element_id=capability_id,
        user_id=user_id,
        ownership_type=ownership_type,
        organization_id=organization_id,
    ).first()
    if existing is not None:
        return existing

    record = ApplicationOwner(
        element_type=ELEMENT_TYPE_CAPABILITY,
        element_id=capability_id,
        user_id=user_id,
        ownership_type=ownership_type,
        assigned_by=assigned_by,
        organization_id=organization_id,
    )
    db.session.add(record)
    db.session.commit()
    return record


def remove_capability_owner(*, owner_record_id: int, organization_id: int) -> bool:
    """Removes one owner row for a capability. Returns False (no-op) rather
    than raise when the row is already gone or belongs to another
    organisation -- a second click on a stale row must not 500."""
    record = ApplicationOwner.query.filter_by(
        id=owner_record_id,
        element_type=ELEMENT_TYPE_CAPABILITY,
        organization_id=organization_id,
    ).first()
    if record is None:
        return False
    db.session.delete(record)
    db.session.commit()
    return True


def list_capabilities_with_no_owner(organization_id: int):
    """Capabilities in this organisation with zero rows in the one
    ownership record for them."""
    owned_capability_ids = {
        row.element_id
        for row in ApplicationOwner.query.filter_by(
            element_type=ELEMENT_TYPE_CAPABILITY,
            organization_id=organization_id,
        ).all()
    }
    capabilities = UnifiedCapability.query.filter_by(
        organization_id=organization_id,
    ).order_by(UnifiedCapability.name).all()
    return [c for c in capabilities if c.id not in owned_capability_ids]

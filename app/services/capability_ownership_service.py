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
from app.services.element_ownership_service import (
    CrossOrganisationElementOwner,
    set_element_owner,
)

ELEMENT_TYPE_CAPABILITY = "capability"


# Kept under its old name: the capability routes and tests catch this class.
CrossOrganisationCapabilityOwner = CrossOrganisationElementOwner


def get_tenant_capability(capability_id: int, organization_id: int) -> Optional[UnifiedCapability]:
    """The capability if it exists and belongs to this organisation, else
    None -- a route uses this to 404 a nonexistent or cross-org id rather
    than render a page for an entity that does not exist."""
    return UnifiedCapability.query.filter_by(
        id=capability_id, organization_id=organization_id,
    ).first()


def set_capability_owner(
    *,
    capability_id: int,
    user_id: int,
    organization_id: int,
    ownership_type: str = "primary",
    assigned_by: Optional[int] = None,
) -> ApplicationOwner:
    """Refuses (CrossOrganisationCapabilityOwner) a capability or an owner
    from another organisation, and an unknown ownership type, rather than
    store it -- same fencing as the application owner writer."""
    return set_element_owner(
        element_type=ELEMENT_TYPE_CAPABILITY,
        element_id=capability_id,
        user_id=user_id,
        organization_id=organization_id,
        ownership_type=ownership_type,
        assigned_by=assigned_by,
    )


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

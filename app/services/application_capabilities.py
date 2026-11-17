"""The capabilities an application is mapped to: the one reader.

The application page's "Mapped Capabilities" figure and linked-capabilities
table, and the AI chat's answer to "which capabilities does it support?", both
read this, so the two cannot disagree.

Support is one relationship per pair, read through ``capability_support``. A
mapping in the original table that has no projection yet is still listed until
the backfill has moved it; a capability held by both is listed once.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Tuple

from app import db


def application_capability_links(application_id: int, organization_id: int) -> List[Tuple[object, object]]:
    """[(mapping, capability)] for an application of *organization_id*.

    An application of another organisation reads as having none.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability
    from app.services.capability_support import capabilities_of_application

    owned = db.session.query(ApplicationComponent.id).filter(
        ApplicationComponent.id == application_id,
        ApplicationComponent.organization_id == organization_id,
    ).first()
    if owned is None:
        return []

    supported = capabilities_of_application(organization_id, application_id)
    records = {
        c.id: c
        for c in db.session.query(UnifiedCapability).filter(
            UnifiedCapability.organization_id == organization_id,
            UnifiedCapability.id.in_([r["capability_id"] for r in supported] or [0]),
        )
    }
    pairs = []
    for r in supported:
        if r["capability_id"] not in records:
            continue
        mapping = SimpleNamespace(
            id=r["row_id"], support_level=r["support_level"], coverage_percentage=r["coverage_percentage"],
            maturity_level=r["maturity_level"], is_strategic=r["is_strategic"], notes=r["notes"],
            kind=r["kind"], kind_word=r["kind_word"], relationship_id=r["relationship_id"],
        )
        pairs.append((mapping, records[r["capability_id"]]))
    held = {r["capability_element_id"] for r in supported}

    # Older mappings the projection has not reached yet.
    legacy = (
        db.session.query(ApplicationCapabilityMapping, BusinessCapability)
        .join(
            BusinessCapability,
            ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
        )
        .filter(
            ApplicationCapabilityMapping.application_component_id == application_id,
            ApplicationCapabilityMapping.organization_id == organization_id,
        )
        .all()
    )
    for mapping, capability in legacy:
        if capability.archimate_element_id in held:
            continue
        held.add(capability.archimate_element_id)
        pairs.append((mapping, capability))
    return pairs

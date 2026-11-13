"""The capabilities an application is mapped to: the one reader.

The application page's "Mapped Capabilities" figure and linked-capabilities
table, and the AI chat's answer to "which capabilities does it support?", both
read this, so the two cannot disagree.

Mappings are held in two stores: the original application-capability mapping and
the unified one the "Map Capability" dialog writes. A capability held by both is
listed once.
"""

from __future__ import annotations

from typing import List, Tuple

from app import db


def application_capability_links(application_id: int, organization_id: int) -> List[Tuple[object, object]]:
    """[(mapping, capability)] for an application of *organization_id*.

    An application of another organisation reads as having none.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability

    owned = db.session.query(ApplicationComponent.id).filter(
        ApplicationComponent.id == application_id,
        ApplicationComponent.organization_id == organization_id,
    ).first()
    if owned is None:
        return []

    # The mapping tables carry no organisation of their own: the application
    # was checked above, and a mapping is read only by that application's id.
    pairs = list(
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
    seen = {cap.id for _, cap in pairs}
    unified = (
        db.session.query(UnifiedApplicationCapabilityMapping, UnifiedCapability)
        .join(
            UnifiedCapability,
            UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
        )
        .filter(
            UnifiedApplicationCapabilityMapping.application_component_id == application_id,
            UnifiedCapability.source_table == "business_capability",
        )
        .all()
    )
    for mapping, capability in unified:
        try:
            source_id = int(capability.source_id)
        except (TypeError, ValueError):
            continue
        if source_id in seen:
            continue
        seen.add(source_id)
        pairs.append((mapping, capability))
    return pairs

"""Which applications support each capability.

One grouped read of the application-to-capability mapping, fenced on both
sides: a capability is the organisation's own (or shared) capability, and an
application counts only if it belongs to the organisation. A mapping to another
organisation's application therefore never counts as support. A failed read
raises; "no gaps" must mean the read ran and found none.

Two lists come out of it: capabilities nothing supports, and capabilities more
than two applications support (a candidate for consolidation). Nothing is
scored.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app import db

MANY_SUPPORTERS_ABOVE = 2


def capability_support() -> Dict[str, Any]:
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Mapping
    from app.models.unified_capability import UnifiedCapability

    capabilities = db.session.execute(
        db.select(UnifiedCapability).order_by(UnifiedCapability.name)
    ).scalars().all()
    pairs = db.session.execute(
        db.select(Mapping.unified_capability_id, ApplicationComponent.id, ApplicationComponent.name)
        .join(ApplicationComponent, ApplicationComponent.id == Mapping.application_component_id)
        .where(Mapping.is_active.is_(True))
    ).all()

    supporters: Dict[int, Dict[int, str]] = {}
    for capability_id, app_id, app_name in pairs:
        supporters.setdefault(capability_id, {})[app_id] = app_name

    rows: List[Dict[str, Any]] = []
    for cap in capabilities:
        names = sorted(supporters.get(cap.id, {}).values(), key=str.lower)
        rows.append({"id": cap.id, "name": cap.name, "level": cap.level, "supporters": names, "count": len(names)})

    return {
        "none": [r for r in rows if r["count"] == 0],
        "many": sorted((r for r in rows if r["count"] > MANY_SUPPORTERS_ABOVE), key=lambda r: (-r["count"], r["name"].lower())),
        "total": len(rows),
        "many_above": MANY_SUPPORTERS_ABOVE,
    }

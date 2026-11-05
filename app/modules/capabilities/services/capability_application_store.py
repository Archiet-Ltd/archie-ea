"""The one place that says which applications support which capability.

The store is ``unified_application_capability_mapping``: the capability map, its drawer, the mapping page, the
capability record and the hierarchy all read it through here. ``application_capability_coverage`` and
``application_capability_mapping`` (keyed on the business capability) are frozen for new readers; writes still
reach them so their older readers keep answering, but nothing new may read them. Reason: the map already read the
unified table and the unified capability is the system of record for a capability (ADR 0008).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from flask import current_app, url_for

from app import db


def _application_url(app_id: int) -> Optional[str]:
    if "unified_applications.application_detail" not in current_app.view_functions:
        return None
    return url_for("unified_applications.application_detail", id=app_id)


def applications_by_capability(org_id, capability_ids: Optional[Iterable[int]] = None) -> Dict[int, List[Dict[str, Any]]]:
    """``{unified capability id: [application, ...]}`` for the organisation.

    Only the organisation's own capabilities and the organisation's own applications are read: a mapping row that
    points at another organisation's application is neither counted nor listed. One list, one count.
    """
    from app.models.application_layer import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Mapping
    from app.models.unified_capability import UnifiedCapability

    if org_id is None:
        return {}
    query = (
        Mapping.query
        .join(UnifiedCapability, Mapping.unified_capability_id == UnifiedCapability.id)
        .filter(UnifiedCapability.organization_id == org_id)
    )
    if capability_ids is not None:
        ids = list(capability_ids)
        if not ids:
            return {}
        query = query.filter(Mapping.unified_capability_id.in_(ids))
    mappings = query.all()
    app_ids = {m.application_component_id for m in mappings}
    apps = {
        a.id: a for a in (
            ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all() if app_ids else []
        ) if a.organization_id == org_id
    }
    out: Dict[int, List[Dict[str, Any]]] = {}
    for m in mappings:
        app = apps.get(m.application_component_id)
        if app is None:
            continue
        out.setdefault(m.unified_capability_id, []).append({
            "id": app.id,
            "name": app.name,
            "lifecycleStatus": app.lifecycle_status,
            "supportLevel": m.support_level,
            "coveragePercentage": m.coverage_percentage,
            "mappingId": m.id,
            "url": _application_url(app.id),
        })
    for rows in out.values():
        rows.sort(key=lambda r: (r["name"] or "").lower())
    return out


def unified_for_business_capability(business_capability) -> Any:
    from app.models.unified_capability import UnifiedCapability

    return UnifiedCapability.query.filter_by(
        source_table="business_capability", source_id=str(business_capability.id),
        organization_id=business_capability.organization_id,
    ).first()


def business_for_unified_capability(unified) -> Any:
    """The business capability a unified row was projected from, or None."""
    from app.models.business_capabilities import BusinessCapability

    if unified is None or unified.source_table != "business_capability" or not str(unified.source_id).isdigit():
        return None
    return BusinessCapability.query.filter_by(id=int(unified.source_id), organization_id=unified.organization_id).first()


def own_application(org_id, app_id):
    """The organisation's own application, or None for a missing or foreign id."""
    from app.models.application_layer import ApplicationComponent

    try:
        app = ApplicationComponent.query.filter_by(id=int(app_id)).first()
    except (TypeError, ValueError):
        return None
    return app if app is not None and app.organization_id == org_id else None


def own_capability(org_id, unified_capability_id):
    """The organisation's own unified capability, or None for a missing, foreign or shared one."""
    from app.models.unified_capability import UnifiedCapability

    try:
        cap = UnifiedCapability.query.filter_by(id=int(unified_capability_id)).first()
    except (TypeError, ValueError):
        return None
    return cap if cap is not None and cap.organization_id == org_id else None


def set_mapping(unified, app_id: int, support_level: str = "partial", fields: Optional[dict] = None) -> bool:
    """Create or update one mapping in the store. Returns True when a row was created."""
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Mapping

    row = Mapping.query.filter_by(unified_capability_id=unified.id, application_component_id=app_id).first()
    created = row is None
    if created:
        row = Mapping(unified_capability_id=unified.id, application_component_id=app_id)
        db.session.add(row)
    row.support_level = support_level or row.support_level or "partial"
    for key in ("coverage_percentage", "notes", "is_strategic"):
        if fields and key in fields:
            setattr(row, key, fields[key])
    return created


def remove_mapping(unified, app_id: int) -> bool:
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Mapping

    rows = Mapping.query.filter_by(unified_capability_id=unified.id, application_component_id=app_id).all()
    for row in rows:
        db.session.delete(row)
    return bool(rows)


def invalidate_map_cache(org_id) -> None:
    try:
        from app.extensions.cache import invalidate_cache

        invalidate_cache(f"capability_map:*:{org_id}")
    except Exception:  # noqa: BLE001 - a cache that cannot be cleared must not fail the write
        current_app.logger.exception("Could not clear the capability map cache")

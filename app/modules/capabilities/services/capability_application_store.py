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
    from flask import has_request_context

    if not has_request_context() or "unified_applications.application_detail" not in current_app.view_functions:
        return None
    return url_for("unified_applications.application_detail", id=app_id)


def _completeness(row) -> int:
    """How much of a mapping row is filled in; the fuller of two copies of a pair is kept."""
    return sum(1 for v in (row.support_level, row.coverage_percentage, row.notes, row.maturity_level,
                           row.gap_description, row.assessor) if v not in (None, "", 0))


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
    # One entry per (capability, application). A pair held twice (no unique constraint existed before the
    # 20261105 revision) is returned once, keeping the most complete row, then the lowest id.
    best: Dict[tuple, Any] = {}
    for m in mappings:
        key = (m.unified_capability_id, m.application_component_id)
        kept = best.get(key)
        if kept is None or (_completeness(m), -m.id) > (_completeness(kept), -kept.id):
            best[key] = m
    out: Dict[int, List[Dict[str, Any]]] = {}
    for m in best.values():
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


def mapping_count(org_id) -> int:
    """How many (capability, application) pairs the organisation holds. The one number every screen shows."""
    return sum(len(rows) for rows in applications_by_capability(org_id).values())


def counts_by_business_capability(org_id) -> Dict[int, int]:
    """``{business capability id: application count}``, from the store, for the organisation only."""
    from app.models.unified_capability import UnifiedCapability

    by_unified = applications_by_capability(org_id)
    if not by_unified:
        return {}
    caps = UnifiedCapability.query.filter(
        UnifiedCapability.id.in_(list(by_unified)), UnifiedCapability.organization_id == org_id,
        UnifiedCapability.source_table == "business_capability",
    ).all()
    return {int(c.source_id): len(by_unified[c.id]) for c in caps if str(c.source_id).isdigit()}


def applications_for_business_capability(business_capability) -> List[Dict[str, Any]]:
    unified = unified_for_business_capability(business_capability)
    if unified is None:
        return []
    return applications_by_capability(business_capability.organization_id, [unified.id]).get(unified.id, [])


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


_MIRROR_SQL = """
INSERT INTO unified_application_capability_mapping
    (unified_capability_id, application_component_id, support_level, coverage_percentage,
     support_quality, relationship_type, relationship_strength, dependency_level,
     gap_status, is_active, is_strategic, notes, created_at, updated_at)
SELECT uc.id, :app_id, :support_level, :coverage, 3, :relationship_type, 3, 'medium',
       COALESCE(:gap_status, 'unknown'), COALESCE(:is_active, TRUE), COALESCE(:is_strategic, FALSE),
       :notes, NOW(), NOW()
  FROM unified_capabilities AS uc
  JOIN application_components AS ac ON ac.id = :app_id AND ac.organization_id = uc.organization_id
 WHERE uc.source_table = 'business_capability'
   AND uc.source_id = CAST(:bc_id AS VARCHAR)
ON CONFLICT (unified_capability_id, application_component_id) DO NOTHING
"""


def mirror_into_store(connection, business_capability_id, application_id, *, support_level=None, coverage=None,
                      relationship_type="supports", gap_status=None, is_active=None, is_strategic=None,
                      notes=None, label="") -> None:
    """Write a pair held against a business capability into the store, from a flush listener.

    This is how a writer that still creates a frozen-table row reaches the store: the row is mirrored in the
    same transaction. A pair already in the store is left as it is (unique constraint, ON CONFLICT). The
    application must belong to the capability's organisation, so a row can never cross organisations. A
    business capability with no unified record is skipped. The mirror never fails the write it follows.
    """
    import logging

    from sqlalchemy import text

    try:
        with connection.begin_nested():
            connection.execute(text(_MIRROR_SQL), {
                "app_id": application_id, "bc_id": business_capability_id,
                "support_level": support_level or "partial", "coverage": int(round(coverage or 0)),
                "relationship_type": relationship_type, "gap_status": gap_status, "is_active": is_active,
                "is_strategic": is_strategic, "notes": notes,
            })
    except Exception:  # noqa: BLE001 - the mirror must never fail the write it follows
        logging.getLogger(__name__).exception("could not mirror %s into the store", label)

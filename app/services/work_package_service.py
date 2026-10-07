"""The one work package writer (R1-B04 PR 2).

Every create, edit, delete and dependency change for a work package goes
through this module, onto the one canonical store ``UnifiedWorkPackage``
(``unified_work_packages``). Handlers in the roadmap, implementation planning,
ADM kanban and capability roadmap screens call these functions instead of
building their own rows.

Reuse check: no writer or service for work packages existed (each handler
constructed its own row from its own store's class). The ownership writers in
``capability_ownership_service`` set the pattern followed here: one module,
organisation-fenced, raising a typed error a route turns into a 404 or 400.

A "programme" is ``enterprise_initiative_id`` (the programme a work package
belongs to). A dependency is an id in ``work_dependencies``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app import db
from app.models.unified_work_package import UnifiedWorkPackage

# The screens that write work packages use slightly different status words
# (planned, not_started, in_progress, blocked, on_hold, completed, cancelled),
# so the writer checks shape, not a closed list, and leaves the vocabulary alone.
# A dependency that is finished or abandoned no longer blocks anything.
_RESOLVED = ("completed", "cancelled")

_EDITABLE = (
    "name", "description", "status", "priority", "assigned_to", "business_capability",
    "progress_percentage", "estimated_cost", "start_date", "end_date",
    "enterprise_initiative_id", "risk_level", "layer", "capability_ids", "capability_names",
)
_DATES = ("start_date", "end_date")


class WorkPackageError(ValueError):
    """The request cannot be applied (bad value, or a rule refused it)."""


class WorkPackageNotFound(WorkPackageError):
    """No such work package or programme in this organisation. Another
    organisation's id is reported exactly as a missing one."""


def _parse_date(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise WorkPackageError("Dates must be YYYY-MM-DD.") from exc


def _number(value: Any, field: str):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise WorkPackageError("%s must be a number." % field) from exc


def query_for(organization_id: int):
    """Every read of work packages starts here: the organisation's own rows."""
    return UnifiedWorkPackage.query.filter(
        UnifiedWorkPackage.organization_id == organization_id
    )


def get_work_package(work_package_id: int, organization_id: int) -> Optional[UnifiedWorkPackage]:
    return query_for(organization_id).filter(
        UnifiedWorkPackage.id == work_package_id
    ).first()


def require_work_package(work_package_id: int, organization_id: int) -> UnifiedWorkPackage:
    wp = get_work_package(work_package_id, organization_id)
    if wp is None:
        raise WorkPackageNotFound("Work package not found.")
    return wp


def _check_programme(programme_id: Any, organization_id: int) -> Optional[int]:
    if programme_id in (None, ""):
        return None
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    try:
        programme_id = int(programme_id)
    except (TypeError, ValueError) as exc:
        raise WorkPackageError("Programme must be an id.") from exc
    found = EnterpriseInitiative.query.filter_by(
        id=programme_id, organization_id=organization_id
    ).first()
    if found is None:
        raise WorkPackageNotFound("Programme not found.")
    return programme_id


def _apply(wp: UnifiedWorkPackage, fields: Dict[str, Any], organization_id: int) -> None:
    for key in fields:
        if key not in _EDITABLE:
            continue
        value = fields[key]
        if key in _DATES:
            value = _parse_date(value)
        elif key in ("progress_percentage", "estimated_cost"):
            value = _number(value, key)
            if value is None:
                continue
        elif key == "status":
            if not isinstance(value, str) or not value.strip() or len(value) > 30:
                raise WorkPackageError("Status must be a short word.")
        elif key == "priority":
            if not isinstance(value, str) or not value.strip() or len(value) > 20:
                raise WorkPackageError("Priority must be a short word.")
        elif key == "enterprise_initiative_id":
            value = _check_programme(value, organization_id)
        elif key == "name":
            value = (value or "").strip()
            if not value:
                raise WorkPackageError("Name is required.")
        setattr(wp, key, value)
    if wp.start_date and wp.end_date:
        wp.duration_days = max((wp.end_date - wp.start_date).days, 0)


def create_work_package(
    *, organization_id: int, user_id: Optional[int] = None, **fields: Any
) -> UnifiedWorkPackage:
    """Create a work package in this organisation. ``source_type`` and
    ``source_id`` may be passed for a row that came from another screen."""
    name = (fields.get("name") or "").strip()
    if not name:
        raise WorkPackageError("Name is required.")
    wp = UnifiedWorkPackage(
        name=name,
        organization_id=organization_id,
        created_by=user_id,
        updated_by=user_id,
        element_type="WorkPackage",
        auto_generated=False,
    )
    for extra in ("source_type", "source_id", "source_data", "confidence_score",
                  "generation_method"):
        if extra in fields and fields[extra] is not None:
            setattr(wp, extra, fields[extra])
    _apply(wp, fields, organization_id)
    db.session.add(wp)
    db.session.flush()
    return wp


def update_work_package(
    work_package_id: int, *, organization_id: int, user_id: Optional[int] = None, **fields: Any
) -> UnifiedWorkPackage:
    wp = require_work_package(work_package_id, organization_id)
    _apply(wp, fields, organization_id)
    wp.updated_by = user_id
    wp.updated_at = datetime.utcnow()
    db.session.flush()
    return wp


def dependents_of(work_package_id: int, organization_id: int) -> List[UnifiedWorkPackage]:
    """Work packages in this organisation that list this one as a dependency."""
    out = []
    for other in query_for(organization_id).filter(
        UnifiedWorkPackage.work_dependencies.isnot(None)
    ).all():
        if work_package_id in _dependency_ids(other):
            out.append(other)
    return out


def delete_work_package(work_package_id: int, *, organization_id: int) -> None:
    wp = require_work_package(work_package_id, organization_id)
    # Do not leave a dangling id on the work packages that depended on it.
    for other in dependents_of(work_package_id, organization_id):
        other.work_dependencies = [d for d in _dependency_ids(other) if d != work_package_id]
    db.session.delete(wp)
    db.session.flush()


def _dependency_ids(wp: UnifiedWorkPackage) -> List[int]:
    ids = []
    for raw in wp.work_dependencies or []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def dependency_ids(wp: UnifiedWorkPackage) -> List[int]:
    return _dependency_ids(wp)


def add_dependency(
    work_package_id: int, dependency_id: int, *, organization_id: int
) -> UnifiedWorkPackage:
    """Record that ``work_package_id`` depends on ``dependency_id``. Both must
    belong to this organisation; the dependency may lie in another programme."""
    wp = require_work_package(work_package_id, organization_id)
    try:
        dependency_id = int(dependency_id)
    except (TypeError, ValueError) as exc:
        raise WorkPackageError("Dependency must be a work package id.") from exc
    if dependency_id == wp.id:
        raise WorkPackageError("A work package cannot depend on itself.")
    require_work_package(dependency_id, organization_id)
    current = _dependency_ids(wp)
    if dependency_id not in current:
        wp.work_dependencies = current + [dependency_id]  # new list: JSON change tracking
        wp.updated_at = datetime.utcnow()
    db.session.flush()
    return wp


def remove_dependency(
    work_package_id: int, dependency_id: int, *, organization_id: int
) -> UnifiedWorkPackage:
    wp = require_work_package(work_package_id, organization_id)
    wp.work_dependencies = [d for d in _dependency_ids(wp) if d != int(dependency_id)]
    wp.updated_at = datetime.utcnow()
    db.session.flush()
    return wp


def blocked_by_another_programme(organization_id: int) -> List[Dict[str, Any]]:
    """Work packages whose unfinished dependency lies in a different programme
    of the same organisation (PB-0104).

    Both ends must belong to a programme, the two programmes must differ, and
    the dependency must not be completed or cancelled. Only this organisation's
    rows are read, so another organisation's work packages never appear.
    """
    rows = query_for(organization_id).all()
    by_id = {r.id: r for r in rows}
    result = []
    for wp in rows:
        if wp.enterprise_initiative_id is None or wp.status in _RESOLVED:
            continue
        blockers = []
        for dep_id in _dependency_ids(wp):
            dep = by_id.get(dep_id)
            if (
                dep is not None
                and dep.enterprise_initiative_id is not None
                and dep.enterprise_initiative_id != wp.enterprise_initiative_id
                and dep.status not in _RESOLVED
            ):
                blockers.append(dep)
        if blockers:
            result.append({"work_package": wp, "blocked_by": blockers})
    result.sort(key=lambda item: (item["work_package"].name or "").lower())
    return result


def programme_names(programme_ids: Iterable[int], organization_id: int) -> Dict[int, str]:
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    ids = {i for i in programme_ids if i is not None}
    if not ids:
        return {}
    rows = EnterpriseInitiative.query.filter(
        EnterpriseInitiative.id.in_(ids),
        EnterpriseInitiative.organization_id == organization_id,
    ).all()
    return {r.id: r.name for r in rows}


def list_programmes(organization_id: int):
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    return (
        EnterpriseInitiative.query.filter_by(organization_id=organization_id)
        .order_by(EnterpriseInitiative.name)
        .all()
    )


def to_dict(wp: UnifiedWorkPackage) -> Dict[str, Any]:
    return {
        "id": wp.id,
        "name": wp.name,
        "description": wp.description,
        "status": wp.status,
        "priority": wp.priority,
        "business_capability": wp.business_capability,
        "assigned_to": wp.assigned_to,
        "programme_id": wp.enterprise_initiative_id,
        "start_date": wp.start_date.isoformat() if wp.start_date else None,
        "end_date": wp.end_date.isoformat() if wp.end_date else None,
        "progress_percentage": wp.progress_percentage,
        "estimated_cost": float(wp.estimated_cost) if wp.estimated_cost else None,
        "dependencies": _dependency_ids(wp),
        "created_at": wp.created_at.isoformat() if wp.created_at else None,
        "updated_at": wp.updated_at.isoformat() if wp.updated_at else None,
        "auto_generated": bool(wp.auto_generated),
        "source_data": wp.source_data,
        "confidence_score": wp.confidence_score,
    }

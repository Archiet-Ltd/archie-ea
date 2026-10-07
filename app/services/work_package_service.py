"""The one work package writer (R1-B04 PR 2).

Every create, edit, delete and dependency change for a work package goes
through this module, onto the one canonical store ``UnifiedWorkPackage``
(``unified_work_packages``). Handlers in the roadmap, implementation planning,
ADM kanban and capability roadmap screens call these functions instead of
building their own rows.

Reuse check: this is the one writer of ``unified_work_packages``; a screen
that still writes one of the four retired stores is kept in step by the
transitional bridge (app/services/work_package_bridge.py) until R1-B04 PR 3
repoints it. The ownership writers in ``capability_ownership_service`` set the
pattern followed here: one module, organisation-fenced, raising a typed error a
route turns into a 404 or 400.

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
    "togaf_phase", "plateau_id", "owner_id", "capability_id", "parent_id",
    "archimate_element_id", "application_component_id", "goal_id", "actual_cost",
    "element_type", "documentation", "gap_id", "triggering_business_event_id",
    "risk_mitigation", "prerequisites", "required_resources",
)
_DATES = ("start_date", "end_date")
# Link columns -> the table they point at. A link to a row of another
# organisation is reported exactly as a missing one.
_LINKS = {
    "plateau_id": "plateaus",
    "owner_id": "users",
    "capability_id": "unified_capabilities",
    "archimate_element_id": "archimate_elements",
    "application_component_id": "application_components",
    "goal_id": "goals",
    "gap_id": "gaps",
    "triggering_business_event_id": "business_events",
}


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


def _check_link(column: str, value: Any, organization_id: int) -> Optional[int]:
    """An id of a linked row, valid only inside this organisation."""
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkPackageError("%s must be an id." % column) from exc
    table = db.metadata.tables.get(_LINKS[column])
    if table is None:
        return value
    columns = table.c
    query = db.select(columns.id).where(columns.id == value)
    if "organization_id" in columns:
        query = query.where(
            db.or_(columns.organization_id.is_(None), columns.organization_id == organization_id)
        )
    if db.session.execute(query).first() is None:
        raise WorkPackageNotFound("Linked record not found.")
    return value


def _apply(wp: UnifiedWorkPackage, fields: Dict[str, Any], organization_id: int) -> None:
    for key in fields:
        if key not in _EDITABLE:
            continue
        value = fields[key]
        if key in _DATES:
            value = _parse_date(value)
        elif key in ("progress_percentage", "estimated_cost", "actual_cost"):
            value = _number(value, key)
            if value is None:
                continue
        elif key in _LINKS:
            value = _check_link(key, value, organization_id)
        elif key == "parent_id":
            value = _check_parent(wp, value, organization_id)
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


def _check_parent(wp: UnifiedWorkPackage, value: Any, organization_id: int) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkPackageError("Parent must be a work package id.") from exc
    if wp.id is not None and value == wp.id:
        raise WorkPackageError("A work package cannot be its own parent.")
    require_work_package(value, organization_id)
    return value


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
        # Its dependencies are unified ids from the start; nothing to remap.
        dependencies_remapped_at=datetime.utcnow(),
    )
    for extra in ("source_type", "source_id", "source_data", "confidence_score",
                  "generation_method", "auto_generated"):
        if extra in fields and fields[extra] is not None:
            setattr(wp, extra, fields[extra])
    _apply(wp, fields, organization_id)
    db.session.add(wp)
    db.session.flush()
    # Every work package joins the ArchiMate model. Idempotent: a row that came
    # from another store already carries its element and is left alone.
    from app.services.archimate_backbone import sync_archimate_element

    try:
        sync_archimate_element(wp)
    except ValueError as exc:
        raise WorkPackageError("Could not add the work package to the ArchiMate model: %s" % exc) from exc
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


def from_form(data: Dict[str, Any]) -> Dict[str, Any]:
    """A screen's field names (summary, target_date, percent_complete, as the
    older forms send them) as the one store names them. Keys the store does not
    know are ignored by create and update."""
    mapped = dict(data)
    if "summary" in data and "description" not in data:
        mapped["description"] = data.get("summary")
    if "target_date" in data:
        mapped["end_date"] = data.get("target_date")
    if "percent_complete" in data:
        mapped["progress_percentage"] = data.get("percent_complete")
    return mapped


def children_of(work_package_id: int, organization_id: int) -> List[UnifiedWorkPackage]:
    return query_for(organization_id).filter(
        UnifiedWorkPackage.parent_id == work_package_id
    ).order_by(UnifiedWorkPackage.start_date, UnifiedWorkPackage.id).all()


def to_roadmap_dict(
    wp: UnifiedWorkPackage, organization_id: int, include_children: bool = False
) -> Dict[str, Any]:
    """A work package in the shape the capability-map roadmap screen reads."""
    from app.models.implementation_migration import Deliverable
    from app.models.user import User

    owner_name = None
    if wp.owner_id:
        owner = User.query.filter_by(id=wp.owner_id, organization_id=organization_id).first()
        if owner:
            owner_name = ("%s %s" % (owner.first_name or "", owner.last_name or "")).strip() or owner.email
    children = children_of(wp.id, organization_id)
    data = {
        "id": wp.id,
        "archimate_id": "wp-%s" % wp.id,
        "name": wp.name,
        "summary": wp.description,
        "description": wp.description,
        "level": 2 if wp.parent_id else 1,
        "parent_id": wp.parent_id,
        "color": None,
        "status": wp.status,
        "priority": wp.priority,
        "start_date": wp.start_date.date().isoformat() if wp.start_date else None,
        "end_date": wp.end_date.date().isoformat() if wp.end_date else None,
        "completed_date": None,
        "percent_complete": wp.progress_percentage or 0,
        "estimated_effort_hours": None,
        "actual_effort_hours": None,
        "estimated_cost": wp.estimated_cost,
        "actual_cost": wp.actual_cost,
        "owner_id": wp.owner_id,
        "owner_name": owner_name,
        "is_overdue": wp.is_overdue(),
        "gap_ids": [wp.gap_id] if wp.gap_id else [],
        "deliverable_count": Deliverable.query.filter_by(unified_work_package_id=wp.id).count(),
        "child_count": len(children),
        "dependencies": _dependency_ids(wp),
        "created_at": wp.created_at.isoformat() if wp.created_at else None,
        "updated_at": wp.updated_at.isoformat() if wp.updated_at else None,
    }
    if include_children and children:
        data["children"] = [to_roadmap_dict(c, organization_id, True) for c in children]
    return data


def delete_work_package(
    work_package_id: int, *, organization_id: int, flush: bool = True, cascade: bool = False
) -> None:
    """Delete a work package. Its source row in a retired store (if it has one)
    keeps ``retired_at`` and so stays deleted: the merge never copies it again.
    ``flush=False`` is for a caller already inside a flush (the bridge)."""
    wp = require_work_package(work_package_id, organization_id)
    if cascade:
        for child in children_of(work_package_id, organization_id):
            delete_work_package(child.id, organization_id=organization_id, flush=False, cascade=True)
    # Do not leave a dangling id on the work packages that depended on it.
    for other in dependents_of(work_package_id, organization_id):
        other.work_dependencies = [d for d in _dependency_ids(other) if d != work_package_id]
    # Its deliverables go with it, whether or not the database cascades.
    from app.models.implementation_migration import Deliverable

    Deliverable.query.filter_by(unified_work_package_id=wp.id).delete(synchronize_session=False)
    db.session.delete(wp)
    if flush:
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
    if _reaches(dependency_id, wp.id, organization_id):
        raise WorkPackageError("That dependency would make a work package wait on itself.")
    current = _dependency_ids(wp)
    if dependency_id not in current:
        wp.work_dependencies = current + [dependency_id]  # new list: JSON change tracking
        wp.updated_at = datetime.utcnow()
    db.session.flush()
    return wp


def _reaches(start_id: int, target_id: int, organization_id: int) -> bool:
    """True when ``target_id`` is ``start_id`` or one of its (transitive) dependencies."""
    graph = {
        row.id: _dependency_ids(row)
        for row in query_for(organization_id).filter(
            UnifiedWorkPackage.work_dependencies.isnot(None)
        ).all()
    }
    seen, stack = set(), [start_id]
    while stack:
        node = stack.pop()
        if node == target_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
    return False


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


def legacy_id(wp: UnifiedWorkPackage, table: str) -> Optional[int]:
    """The id this work package has in a retired store, when it came from it.
    Child tables that still hold a foreign key to a retired store's id resolve
    it here; the bridge keeps both rows in step."""
    if wp.source_table == table:
        return wp.source_id
    return None


def get_by_source(table: str, source_id: int, organization_id: int) -> Optional[UnifiedWorkPackage]:
    """The unified row copied from (table, source_id), inside this organisation."""
    return query_for(organization_id).filter(
        UnifiedWorkPackage.source_table == table,
        UnifiedWorkPackage.source_id == source_id,
    ).first()


# --- deliverables -----------------------------------------------------------
# A deliverable belongs to a work package of this organisation. It is keyed by
# unified_work_package_id; the older work_packages key is filled only when the
# work package has a row there.
_DELIVERABLE_FIELDS = ("description", "delivery_status", "deliverable_type", "start_date",
                       "target_date")


def deliverables_query(organization_id: int, work_package_id: Optional[int] = None):
    """Deliverables of this organisation's work packages (one work package when given)."""
    from app.models.implementation_migration import Deliverable

    if work_package_id is not None:
        wp = require_work_package(work_package_id, organization_id)
        return Deliverable.query.filter(Deliverable.unified_work_package_id == wp.id)
    owned = db.select(UnifiedWorkPackage.id).where(
        UnifiedWorkPackage.organization_id == organization_id
    )
    return Deliverable.query.filter(Deliverable.unified_work_package_id.in_(owned))


def get_deliverable(deliverable_id: int, organization_id: int):
    from app.models.implementation_migration import Deliverable

    return deliverables_query(organization_id).filter(Deliverable.id == deliverable_id).first()


def create_deliverable(work_package_id: int, *, organization_id: int, name: str, **fields: Any):
    """Add a deliverable to any work package of this organisation."""
    from app.models.implementation_migration import Deliverable

    name = (name or "").strip()
    if not name:
        raise WorkPackageError("Name is required.")
    wp = require_work_package(work_package_id, organization_id)
    deliverable = Deliverable(
        name=name,
        unified_work_package_id=wp.id,
        work_package_id=legacy_id(wp, "work_packages"),
    )
    for key in _DELIVERABLE_FIELDS:
        if key in fields and fields[key] is not None:
            setattr(deliverable, key, fields[key])
    db.session.add(deliverable)
    db.session.flush()
    return deliverable


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

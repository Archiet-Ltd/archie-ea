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
    "togaf_phase", "owner_id", "capability_id", "parent_id",
    "archimate_element_id", "application_component_id", "goal_id", "actual_cost",
    "element_type", "documentation", "triggering_business_event_id",
    "risk_mitigation", "prerequisites", "required_resources",
    # Fields of the enterprise create screen: its short summary, effort in hours,
    # level and colour (own columns) and its architecture (the context id).
    "summary", "estimated_effort_hours", "actual_effort_hours", "level", "color",
    "context_id",
)
# plateau_id and gap_id are link inputs, not columns: the writer records them as
# ArchiMate relationships from the work package's element (see _apply_links).
_LINK_INPUTS = ("plateau_id", "gap_id")
STATUS_MAX = 50
_DATES = ("start_date", "end_date")
# Link columns -> the table they point at. A link to a row of another
# organisation is reported exactly as a missing one.
_LINKS = {
    "owner_id": "users",
    "capability_id": "unified_capabilities",
    "application_component_id": "application_components",
    "goal_id": "goals",
    "triggering_business_event_id": "business_events",
    "context_id": "architecture_models",
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


def element_refusal_sql(element: str, org: str, copy: str = "NULL", earlier: bool = False) -> str:
    """The one element rule, as SQL: NULL when copy `copy` of organisation `org` may hold element
    `element`, else the first failed condition: 'organisation', 'type' (not a WorkPackage), or
    'shared' (another copy holds it; with `earlier`, one with a smaller id)."""
    other = f"ow.id < {copy}" if earlier else f"ow.id IS DISTINCT FROM {copy}"
    return (
        "(CASE "
        f"WHEN NOT EXISTS (SELECT 1 FROM archimate_elements ae WHERE ae.id = {element} "  # tenancy-ok: the organisation test is this expression
        f"AND ae.organization_id = {org}) THEN 'organisation' "
        f"WHEN NOT EXISTS (SELECT 1 FROM archimate_elements ae WHERE ae.id = {element} "  # tenancy-ok: same
        "AND ae.type = 'WorkPackage') THEN 'type' "
        f"WHEN EXISTS (SELECT 1 FROM unified_work_packages ow WHERE ow.archimate_element_id = {element} "  # tenancy-ok: same
        f"AND {other}) THEN 'shared' END)"
    )


def element_refusal(element_id, organization_id, copy_id=None, connection=None) -> Optional[str]:
    """None when the copy (None: a new one) may hold the element, else the failed condition."""
    from sqlalchemy import text

    sql = "SELECT " + element_refusal_sql(
        "CAST(:e AS integer)", "CAST(:o AS integer)", "CAST(:c AS bigint)")
    return (connection or db.session).execute(
        text(sql), {"e": element_id, "o": organization_id, "c": copy_id}).scalar()


def _check_element(wp: UnifiedWorkPackage, value: Any, organization_id: int) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkPackageError("archimate_element_id must be an id.") from exc
    refusal = element_refusal(value, organization_id, wp.id)
    if refusal == "organisation":
        raise WorkPackageNotFound("Linked record not found.")
    if refusal == "type":
        raise WorkPackageError("The element must be a work package element.")
    if refusal == "shared":
        raise WorkPackageError("Another work package already holds that element.")
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
        elif key in ("estimated_effort_hours", "actual_effort_hours"):
            value = _number(value, key)
        elif key == "level":
            try:
                value = None if value in (None, "") else int(value)
            except (TypeError, ValueError) as exc:
                raise WorkPackageError("Level must be a whole number.") from exc
        elif key == "color":
            value = None if value in (None, "") else str(value)[:20]
        elif key == "archimate_element_id":
            value = _check_element(wp, value, organization_id)
        elif key in _LINKS:
            value = _check_link(key, value, organization_id)
        elif key == "parent_id":
            value = _check_parent(wp, value, organization_id)
        elif key == "status":
            if not isinstance(value, str) or not value.strip() or len(value) > STATUS_MAX:
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
    links = _resolve_links(fields, organization_id)
    _apply(wp, fields, organization_id)
    db.session.add(wp)
    db.session.flush()
    # Every work package joins the ArchiMate model. Idempotent: a row that came
    # from another store already carries its element and is left alone.
    _ensure_element(wp)
    _apply_links(wp, links, organization_id)
    return wp


def _ensure_element(obj) -> None:
    from app.services.archimate_backbone import sync_archimate_element

    try:
        sync_archimate_element(obj)
    except ValueError as exc:
        raise WorkPackageError(
            "Could not add the %s to the ArchiMate model: %s" % (type(obj).__name__, exc)
        ) from exc


# --- plateau and gap links ----------------------------------------------------
# A work package realises a plateau and is associated with a gap. Both are
# relationships in the ArchiMate model, from the work package's element to the
# plateau's or the gap's element (reuse register entry work-package-record); the
# plateau_id and gap_id columns of the unified row are not written or read here.
_PLATEAU_REL = "realization"
_GAP_REL = "association"


def _link_models():
    from app.models.implementation_migration import Gap, Plateau

    return {"plateau_id": (Plateau, _PLATEAU_REL), "gap_id": (Gap, _GAP_REL)}


def _resolve_links(fields: Dict[str, Any], organization_id: int) -> Dict[str, Any]:
    """{'plateau_id': Plateau|None, 'gap_id': Gap|None} for each link input in
    ``fields``. A plateau or gap of another organisation is reported as missing.
    Nothing is written, so a bad id leaves the work package untouched."""
    out: Dict[str, Any] = {}
    for key, (model, _rel) in _link_models().items():
        if key not in fields:
            continue
        value = fields[key]
        if value in (None, ""):
            out[key] = None
            continue
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise WorkPackageError("%s must be an id." % key) from exc
        found = model.query.filter_by(id=value, organization_id=organization_id).first()
        if found is None:
            raise WorkPackageNotFound("Linked record not found.")
        out[key] = found
    return out


def _link_rows(
    organization_id: int, wp_ids=None, plateau_ids=None, gap_ids=None, connection=None
) -> List[tuple]:
    """The one reader of the plateau and gap link encoding.

    Rows of (work package id, relationship id, relationship type, plateau id, gap
    id) for the relationships from a work package's element to a plateau
    (realization) or a gap (association), ordered by relationship id. The one
    organisation predicate sits here: the relationship, the work package and the
    plateau or gap must all belong to ``organization_id``; another organisation's
    rows never appear. ``wp_ids``, ``plateau_ids`` and ``gap_ids`` narrow the read.
    ``connection`` runs the read on that connection instead of ``db.session`` (the bridge,
    inside a flush)."""
    from app.models.implementation_migration import Gap, Plateau
    from app.models.models import ArchiMateRelationship

    if organization_id is None:
        return []
    query = (
        db.select(
            UnifiedWorkPackage.id, ArchiMateRelationship.id, ArchiMateRelationship.type,
            Plateau.id, Gap.id,
        )
        .join(ArchiMateRelationship,
              ArchiMateRelationship.source_id == UnifiedWorkPackage.archimate_element_id)
        .outerjoin(Plateau, db.and_(
            Plateau.archimate_element_id == ArchiMateRelationship.target_id,
            Plateau.organization_id == organization_id))
        .outerjoin(Gap, db.and_(
            Gap.archimate_element_id == ArchiMateRelationship.target_id,
            Gap.organization_id == organization_id))
        .where(
            UnifiedWorkPackage.organization_id == organization_id,
            ArchiMateRelationship.organization_id == organization_id,
            ArchiMateRelationship.type.in_((_PLATEAU_REL, _GAP_REL)),
            db.or_(
                db.and_(ArchiMateRelationship.type == _PLATEAU_REL, Plateau.id.isnot(None)),
                db.and_(ArchiMateRelationship.type == _GAP_REL, Gap.id.isnot(None)),
            ),
        )
        .order_by(ArchiMateRelationship.id)
    )
    if wp_ids is not None:
        query = query.where(UnifiedWorkPackage.id.in_(list(wp_ids)))
    if plateau_ids is not None:
        query = query.where(Plateau.id.in_(list(plateau_ids)))
    if gap_ids is not None:
        query = query.where(Gap.id.in_(list(gap_ids)))
    return [tuple(row) for row in (connection or db.session).execute(query).all()]


def _link_targets(wp: UnifiedWorkPackage, key: str, organization_id: int):
    """(relationship id, plateau or gap id) for the links of the kind ``key`` names
    from this work package, through the one reader."""
    rel_type = _link_models()[key][1]
    out = []
    for _wp_id, rel_id, kind, plateau_id, gap_id in _link_rows(organization_id, wp_ids=[wp.id]):
        if kind == rel_type:
            out.append((rel_id, plateau_id if key == "plateau_id" else gap_id))
    return out


def lock_work_package(work_package_id: int) -> None:
    """SELECT ... FOR UPDATE on the unified row, held to the end of the transaction.
    Every path that changes a work package's links takes it before reading them."""
    db.session.execute(
        db.select(UnifiedWorkPackage.id).where(UnifiedWorkPackage.id == work_package_id)
        .with_for_update()
    ).first()


def remove_link_targets(wp: UnifiedWorkPackage, key: str, organization_id: int, target_ids) -> int:
    """Remove the relationships from ``wp`` to these plateaus or gaps (``key`` names
    the kind). Returns how many were removed."""
    from app.models.models import ArchiMateRelationship

    wanted = set(target_ids)
    removed = 0
    for rel_id, tid in _link_targets(wp, key, organization_id):
        if tid in wanted:
            rel = db.session.get(ArchiMateRelationship, rel_id)
            if rel is not None:
                db.session.delete(rel)
                removed += 1
    if removed:
        db.session.flush()
    return removed


def _apply_links(
    wp: UnifiedWorkPackage, links: Dict[str, Any], organization_id: int, replace: bool = True,
    rollback: bool = True, round_trip: bool = True, keep=None,
) -> None:
    """Record the links in ``links`` ({'plateau_id': Plateau|None, 'gap_id': Gap|None})
    as relationships. Only a kind that is a key of ``links`` is touched: a missing
    key leaves that kind alone, and None clears it. With ``replace`` the new
    target replaces the others of its kind. ``rollback=False`` is for a caller
    that holds a savepoint and rolls it back itself. With ``round_trip`` the id a
    screen sends back unchanged (the first linked id) leaves the other links of
    that kind alone; the bridge passes False, as an old screen holds one link.
    ``keep`` is {'plateau_id': {ids}, 'gap_id': {ids}}: targets a replace leaves
    linked (the ones the old store's association tables still hold). The unified
    row is locked before its links are read, so two edits of one work package's
    links run one after the other and the second reads what the first wrote."""
    if not links:
        return
    if organization_id is None:
        raise WorkPackageError("A work package link needs an organisation.")
    _ensure_element(wp)
    lock_work_package(wp.id)
    from app.models.archimate_core import ArchiMateElement
    from app.models.models import ArchiMateRelationship
    from app.modules.architecture.services.archimate_relationship_service import (
        ArchiMateRelationshipService,
    )

    for key, target in links.items():
        _model, rel_type = _link_models()[key]
        current = _link_targets(wp, key, organization_id)
        wanted = target.id if target is not None else None
        current_ids = [tid for _rid, tid in current]
        # The modal sends back the one id it shows (the first linked): that is
        # no change, whatever else the work package is linked to.
        unchanged = round_trip and bool(current_ids) and wanted == current_ids[0]
        if replace and not unchanged:
            kept = (keep or {}).get(key) or ()
            for rel_id, tid in current:
                if tid != wanted and tid not in kept:
                    rel = db.session.get(ArchiMateRelationship, rel_id)
                    if rel is not None:
                        db.session.delete(rel)
        if target is None or wanted in current_ids:
            continue
        _ensure_element(target)
        db.session.flush()
        source = db.session.get(ArchiMateElement, wp.archimate_element_id)
        end = db.session.get(ArchiMateElement, target.archimate_element_id)
        created = ArchiMateRelationshipService.create_relationship(
            source, end, rel_type,
            architecture_id=source.architecture_id or end.architecture_id,
            organization_id=organization_id, rollback=rollback,
        )
        if created is None:
            raise WorkPackageError("Could not link the work package to its %s." % key[:-3])
    db.session.flush()


def plateau_and_gap_links(wps, organization_id: int) -> Dict[int, Dict[str, List[int]]]:
    """{work package id: {"plateau_ids": [...], "gap_ids": [...]}} for these work
    packages, read from the relationships in one query. Another organisation's
    plateaus and gaps never appear."""
    wps = list(wps)
    out = {wp.id: {"plateau_ids": [], "gap_ids": []} for wp in wps}
    if not out:
        return out
    for wp_id, _rel_id, rel_type, plateau_id, gap_id in _link_rows(
            organization_id, wp_ids=list(out)):
        entry = out[wp_id]
        if rel_type == _PLATEAU_REL and plateau_id not in entry["plateau_ids"]:
            entry["plateau_ids"].append(plateau_id)
        if rel_type == _GAP_REL and gap_id not in entry["gap_ids"]:
            entry["gap_ids"].append(gap_id)
    return out


def work_package_ids_for_gap(gap_id: int, organization_id: int) -> List[int]:
    """Ids of this organisation's work packages linked to a gap."""
    rows = _link_rows(organization_id, gap_ids=[gap_id])
    return sorted({row[0] for row in rows if row[2] == _GAP_REL})


def plateau_work_package_ids(plateau_ids, organization_id: int) -> Dict[int, set]:
    """{plateau id: {ids of this organisation's work packages that realise it}}."""
    out: Dict[int, set] = {pid: set() for pid in plateau_ids}
    if not out:
        return out
    for wp_id, _rel_id, rel_type, plateau_id, _gap in _link_rows(
            organization_id, plateau_ids=list(out)):
        if rel_type == _PLATEAU_REL:
            out[plateau_id].add(wp_id)
    return out


def update_work_package(
    work_package_id: int, *, organization_id: int, user_id: Optional[int] = None, **fields: Any
) -> UnifiedWorkPackage:
    wp = require_work_package(work_package_id, organization_id)
    links = _resolve_links(fields, organization_id)
    _apply(wp, fields, organization_id)
    wp.updated_by = user_id
    wp.updated_at = datetime.utcnow()
    db.session.flush()
    _apply_links(wp, links, organization_id)
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
    # ``summary`` is a column of its own: it is not copied onto ``description``,
    # so a screen that sends the summary back unchanged cannot wipe the description.
    if "architecture_id" in data and "context_id" not in data:
        mapped["context_id"] = data.get("architecture_id")
    if "estimated_effort_hours" in data and data.get("estimated_effort_hours") == "":
        mapped["estimated_effort_hours"] = None
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
    wp: UnifiedWorkPackage, organization_id: int, include_children: bool = False,
    links: Optional[Dict[str, List[int]]] = None,
) -> Dict[str, Any]:
    """A work package in the shape the capability-map roadmap screen reads.
    ``links`` is this work package's entry of ``plateau_and_gap_links`` when the
    caller already read it for a batch."""
    from app.models.implementation_migration import Deliverable
    from app.models.user import User

    owner_name = None
    if wp.owner_id:
        owner = User.query.filter_by(id=wp.owner_id, organization_id=organization_id).first()
        if owner:
            owner_name = ("%s %s" % (owner.first_name or "", owner.last_name or "")).strip() or owner.email
    children = children_of(wp.id, organization_id)
    if links is None:
        links = plateau_and_gap_links([wp], organization_id)[wp.id]
    data = {
        "id": wp.id,
        "archimate_id": "wp-%s" % wp.id,
        "name": wp.name,
        "summary": wp.summary if wp.summary is not None else wp.description,
        "description": wp.description,
        "level": wp.level if wp.level is not None else (2 if wp.parent_id else 1),
        "parent_id": wp.parent_id,
        "color": wp.color,
        "status": wp.status,
        "priority": wp.priority,
        "start_date": wp.start_date.date().isoformat() if wp.start_date else None,
        "end_date": wp.end_date.date().isoformat() if wp.end_date else None,
        "completed_date": None,
        "percent_complete": wp.progress_percentage or 0,
        "estimated_effort_hours": wp.estimated_effort_hours,
        "actual_effort_hours": wp.actual_effort_hours,
        "estimated_cost": wp.estimated_cost,
        "actual_cost": wp.actual_cost,
        "owner_id": wp.owner_id,
        "owner_name": owner_name,
        "is_overdue": wp.is_overdue(),
        # The modal reads and sends back the singular keys (the first linked id).
        "gap_id": links["gap_ids"][0] if links["gap_ids"] else None,
        "plateau_id": links["plateau_ids"][0] if links["plateau_ids"] else None,
        "gap_ids": list(links["gap_ids"]),
        "plateau_ids": list(links["plateau_ids"]),
        "deliverable_count": Deliverable.query.filter_by(unified_work_package_id=wp.id).count(),
        "child_count": len(children),
        "dependencies": _dependency_ids(wp),
        "created_at": wp.created_at.isoformat() if wp.created_at else None,
        "updated_at": wp.updated_at.isoformat() if wp.updated_at else None,
    }
    if include_children and children:
        child_links = plateau_and_gap_links(children, organization_id)
        data["children"] = [
            to_roadmap_dict(c, organization_id, True, links=child_links[c.id]) for c in children
        ]
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
    else:
        # The children stay, at root level; the database rule (ON DELETE SET NULL)
        # says the same, this makes it true where that rule is missing.
        for child in children_of(work_package_id, organization_id):
            child.parent_id = None
    # Do not leave a dangling id on the work packages that depended on it.
    for other in dependents_of(work_package_id, organization_id):
        other.work_dependencies = [d for d in _dependency_ids(other) if d != work_package_id]
    # Its deliverables go with it (the one deliverable store), each deleted once
    # through the session so a row an older screen's cascade already removed in
    # this flush is not deleted a second time.
    from app.models.implementation_migration import Deliverable

    for deliverable in Deliverable.query.filter_by(unified_work_package_id=wp.id).all():
        db.session.delete(deliverable)
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
# The one deliverable store is ``deliverables``. A deliverable belongs to a work
# package of this organisation. It is keyed by unified_work_package_id; the older
# work_packages key is filled only when the work package has a row there.
_DELIVERABLE_FIELDS = ("description", "delivery_status", "deliverable_type", "start_date",
                       "target_date", "delivered_date", "review_date", "approval_criteria",
                       "approval_status", "quality_score", "related_task_ids")
_DELIVERABLE_DATES = ("start_date", "target_date", "delivered_date")


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


def _deliverable_value(key: str, value: Any):
    if key in _DELIVERABLE_DATES:
        parsed = _parse_date(value)
        return parsed.date() if isinstance(parsed, datetime) else parsed
    if key == "review_date":
        return _parse_date(value)
    if key == "quality_score":
        return _number(value, key)
    if key == "delivery_status":
        if not isinstance(value, str) or not value.strip() or len(value) > STATUS_MAX:
            raise WorkPackageError("Status must be a short word.")
    if key == "related_task_ids" and isinstance(value, (list, tuple)):
        import json

        return json.dumps(list(value))
    return value


def _apply_deliverable(deliverable, fields: Dict[str, Any]) -> None:
    for key in _DELIVERABLE_FIELDS:
        if key in fields:
            setattr(deliverable, key, _deliverable_value(key, fields[key]))


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
    _apply_deliverable(deliverable, {k: v for k, v in fields.items() if v is not None})
    db.session.add(deliverable)
    db.session.flush()
    return deliverable


def create_deliverables_for_roadmap_copy(roadmap_wp, specs: Iterable[Dict[str, Any]]) -> list:
    """Deliverables for a roadmap work package that was just flushed: they belong
    to its bridged copy in the one store. ``specs`` are dicts of deliverable fields
    plus ``name``. A row with no copy (the bridge could not place it in an
    organisation) gets none, and the gap is logged."""
    import logging

    unified_id = getattr(roadmap_wp, "retired_into_id", None)
    copy = UnifiedWorkPackage.query.filter_by(id=unified_id).first() if unified_id else None
    if copy is None or copy.organization_id is None:
        logging.getLogger(__name__).warning(
            "deliverables not created: roadmap work package %s has no copy in an organisation",
            getattr(roadmap_wp, "id", None))
        return []
    return [
        create_deliverable(copy.id, organization_id=copy.organization_id, **spec) for spec in specs
    ]


def update_deliverable(
    work_package_id: int, deliverable_id: int, *, organization_id: int, **fields: Any
):
    """Edit a deliverable of one of this organisation's work packages."""
    deliverable = deliverables_query(organization_id, work_package_id).filter_by(
        id=deliverable_id).first()
    if deliverable is None:
        raise WorkPackageNotFound("Deliverable not found.")
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise WorkPackageError("Name is required.")
        deliverable.name = name
    _apply_deliverable(deliverable, fields)
    db.session.flush()
    return deliverable


def delete_deliverable(work_package_id: int, deliverable_id: int, *, organization_id: int) -> None:
    deliverable = deliverables_query(organization_id, work_package_id).filter_by(
        id=deliverable_id).first()
    if deliverable is None:
        raise WorkPackageNotFound("Deliverable not found.")
    db.session.delete(deliverable)
    db.session.flush()


def roadmap_deliverable_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """The capability roadmap screen's deliverable fields (status, due_date) as the
    one store names them (delivery_status, target_date)."""
    mapped: Dict[str, Any] = {}
    if "name" in data:
        mapped["name"] = data["name"]
    for key in ("description", "deliverable_type", "approval_criteria", "approval_status",
                "quality_score", "related_task_ids", "delivered_date", "review_date"):
        if key in data:
            mapped[key] = data[key]
    if "status" in data:
        mapped["delivery_status"] = data["status"]
    if "due_date" in data:
        mapped["target_date"] = data["due_date"]
    return mapped


def deliverable_to_roadmap_dict(deliverable) -> Dict[str, Any]:
    """A deliverable in the shape the capability roadmap screen reads."""
    return {
        "id": str(deliverable.id) if deliverable.id else None,  # string: JavaScript BigInt safety
        "name": deliverable.name,
        "description": deliverable.description,
        "work_package_id": None,
        "unified_work_package_id": str(deliverable.unified_work_package_id)
        if deliverable.unified_work_package_id else None,
        "status": deliverable.delivery_status,
        "due_date": deliverable.target_date.isoformat() if deliverable.target_date else None,
        "delivered_date": deliverable.delivered_date.isoformat() if deliverable.delivered_date else None,
        "review_date": deliverable.review_date.isoformat() if deliverable.review_date else None,
        "approval_criteria": deliverable.approval_criteria,
        "quality_score": deliverable.quality_score,
        "approval_status": deliverable.approval_status,
        "archimate_element_type": "Deliverable",
        "deliverable_type": deliverable.deliverable_type,
        "related_task_ids": deliverable.related_task_ids,
        "auto_generated": bool(deliverable.auto_generated),
        "source_application_id": deliverable.application_component_id,
        "generation_method": deliverable.generation_method,
        "created_at": deliverable.created_at.isoformat() if deliverable.created_at else None,
        "updated_at": deliverable.updated_at.isoformat() if deliverable.updated_at else None,
    }


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


def to_dicts(wps) -> List[Dict[str, Any]]:
    """``to_dict`` for a list of work packages, reading their plateau and gap links
    in one query per organisation instead of one per row."""
    wps = list(wps)
    links: Dict[int, Dict[str, List[int]]] = {}
    for org_id in {wp.organization_id for wp in wps if wp.organization_id is not None}:
        links.update(plateau_and_gap_links([w for w in wps if w.organization_id == org_id], org_id))
    return [to_dict(wp, links=links.get(wp.id)) for wp in wps]


def to_dict(wp: UnifiedWorkPackage, links: Optional[Dict[str, List[int]]] = None) -> Dict[str, Any]:
    if links is None:
        links = (
            plateau_and_gap_links([wp], wp.organization_id)[wp.id]
            if wp.organization_id is not None else {"plateau_ids": [], "gap_ids": []}
        )
    return {
        "id": wp.id,
        "plateau_ids": list(links["plateau_ids"]),
        "gap_ids": list(links["gap_ids"]),
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

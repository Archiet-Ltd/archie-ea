"""Restore one organisation to how it stood before a model import.

Reads the snapshot that ``import_snapshot_service`` stored on the import's
session-log row (``ImportSessionLog``) and:

- ``build_preview`` names everything a restore would change, writes nothing:
  the elements and relationships the import created, anything added since
  that hangs off them, the stored elements the import changed, and the
  changes recorded in the audit trail since the import (with the ones that
  can be applied again marked);
- ``restore`` does it, in one transaction, and records the restore itself in
  the audit trail.

Every statement carries the organisation id. Bulk statements run in chunks so
an import of thousands of relationships restores in a handful of queries.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

import sqlalchemy as sa

from app import db
from app.services.import_snapshot_service import IMPORT_SOURCE

logger = logging.getLogger(__name__)

CHUNK = 5000
ELEMENT_TABLE = "archimate_elements"
RELATIONSHIP_TABLE = "archimate_relationships"
# Fields of a recorded element change that can be applied again.
REAPPLY_FIELDS = ("name", "description", "custom_properties")
RESTORE_ACTION = "restore"


class RestoreError(Exception):
    """A restore the service refuses; the message is safe to show a person."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _chunks(values: Sequence[int], size: int = CHUNK) -> Iterable[List[int]]:
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def _models():
    from app.models.archimate_core import (
        ArchiMateElement,
        ArchiMateRelationship,
        ArchitectureModel,
    )

    return ArchiMateElement, ArchiMateRelationship, ArchitectureModel


def _snapshot_of(log) -> Dict[str, Any]:
    snap = log.snapshot_data
    if not isinstance(snap, dict) or snap.get("version") != 1:
        raise RestoreError("This import has no snapshot to restore from.")
    return snap


def _ids(snap: Dict[str, Any], key: str) -> List[int]:
    return [int(i) for i in (snap.get(key) or [])]


# ---------------------------------------------------------------------------
# Reads (all organisation-scoped)
# ---------------------------------------------------------------------------

def _present_elements(org_id: int, ids: Sequence[int]) -> List[Any]:
    """Rows of ``ids`` this organisation still holds, as (id, name, type)."""
    ArchiMateElement, _, _ = _models()
    rows: List[Any] = []
    for chunk in _chunks(ids):
        rows.extend(
            db.session.execute(
                sa.select(ArchiMateElement.id, ArchiMateElement.name, ArchiMateElement.type)
                .where(ArchiMateElement.organization_id == org_id, ArchiMateElement.id.in_(chunk))
            ).all()
        )
    return rows


def _present_relationships(org_id: int, ids: Sequence[int]) -> List[Any]:
    _, ArchiMateRelationship, _ = _models()
    rows: List[Any] = []
    for chunk in _chunks(ids):
        rows.extend(
            db.session.execute(
                sa.select(
                    ArchiMateRelationship.id, ArchiMateRelationship.type,
                    ArchiMateRelationship.source_id, ArchiMateRelationship.target_id,
                ).where(
                    ArchiMateRelationship.organization_id == org_id,
                    ArchiMateRelationship.id.in_(chunk),
                )
            ).all()
        )
    return rows


def _dependent_relationships(org_id: int, element_ids: Sequence[int], own: set) -> List[Any]:
    """Relationships added since the import that touch an element it created."""
    _, ArchiMateRelationship, _ = _models()
    found: Dict[int, Any] = {}
    for chunk in _chunks(element_ids):
        for row in db.session.execute(
            sa.select(
                ArchiMateRelationship.id, ArchiMateRelationship.type,
                ArchiMateRelationship.source_id, ArchiMateRelationship.target_id,
            ).where(
                ArchiMateRelationship.organization_id == org_id,
                sa.or_(
                    ArchiMateRelationship.source_id.in_(chunk),
                    ArchiMateRelationship.target_id.in_(chunk),
                ),
            )
        ).all():
            if row.id not in own:
                found[row.id] = row
    return list(found.values())


def _foreign_key_columns(target_tables: Optional[Sequence[str]] = None):
    """Every column, in any table, that refers to one of ``target_tables``."""
    allowed = set(target_tables or ())
    out = []
    for table in db.metadata.tables.values():
        for fk in table.foreign_keys:
            if allowed and fk.column.table.name not in allowed:
                continue
            out.append((table, fk.parent, fk.column.table))
    return out


def _blocking_references(
    org_id: int, element_ids: Sequence[int], domain_ids: Dict[str, set], element_id_set: set
) -> List[Dict[str, Any]]:
    """Records elsewhere that still point at an element the restore would remove.

    Relationships are handled by the restore itself; the domain rows the
    import created are removed with it. Anything else (a view the element was
    added to, a record linked to it since) is named, and the restore is
    refused until it is dealt with, so a restore never silently breaks or
    deletes someone's later work.
    """
    blockers_by_table: Dict[str, Dict[str, Any]] = {}
    watched: Dict[str, List[int]] = {ELEMENT_TABLE: list(element_ids)}
    watched.update({table_name: list(ids) for table_name, ids in domain_ids.items() if ids})
    if not watched[ELEMENT_TABLE] and len(watched) == 1:
        return []
    for table, column, target_table in _foreign_key_columns(tuple(watched)):
        if table.name == RELATIONSHIP_TABLE and target_table.name == ELEMENT_TABLE:
            continue
        watched_ids = watched.get(target_table.name) or []
        if not watched_ids:
            continue
        pk = list(table.primary_key.columns)
        excluded = domain_ids.get(table.name, set())
        count = 0
        for chunk in _chunks(watched_ids):
            stmt = sa.select(*(pk or [column])).where(column.in_(chunk))
            if "organization_id" in table.c:
                stmt = stmt.where(table.c.organization_id == org_id)
            else:
                stmt = stmt.where(
                    column.in_(
                        sa.select(target_table.c.id).where(
                            target_table.c.id.in_(chunk),
                            _org_filter(target_table, org_id),
                        )
                    )
                )
            for row in db.session.execute(stmt).all():
                if table.name == ELEMENT_TABLE and target_table.name == ELEMENT_TABLE and row[0] in element_id_set:
                    continue
                if pk and len(pk) == 1 and row[0] in excluded:
                    continue
                count += 1
        if count:
            blocker = blockers_by_table.setdefault(
                table.name,
                {"table": table.name, "label": table.name.replace("_", " "), "count": 0},
            )
            blocker["count"] += count
    return [blockers_by_table[name] for name in sorted(blockers_by_table)]


def _changes_since(org_id: int, log, watched_ids: Sequence[int], updated_ids: set) -> List[Dict[str, Any]]:
    """Audit-trail entries for the touched elements recorded after the import."""
    from app.models.audit_log import AuditLog

    since = log.completed_at or log.started_at
    entries = []
    for chunk in _chunks(list(watched_ids)):
        entries.extend(
            AuditLog.query.filter(
                AuditLog.organization_id == org_id,
                AuditLog.table_name.in_((ELEMENT_TABLE, RELATIONSHIP_TABLE)),
                AuditLog.created_at >= since,
                AuditLog.record_id.in_(chunk),
            ).all()
        )
    entries.sort(key=lambda e: e.id)
    out = []
    for entry in entries:
        new_value = entry.new_value if isinstance(entry.new_value, dict) else {}
        old_value = entry.old_value if isinstance(entry.old_value, dict) else {}
        fields = {k: new_value[k] for k in REAPPLY_FIELDS if k in new_value}
        reappliable = (
            entry.table_name == ELEMENT_TABLE
            and str(entry.action).lower() == "update"
            and entry.record_id in updated_ids
            and bool(fields)
        )
        if reappliable:
            reason = ""
        elif entry.record_id not in updated_ids:
            reason = "The element was created by the import, so it goes with it."
        else:
            reason = "This kind of change cannot be applied again."
        out.append({
            "audit_id": entry.id,
            "when": entry.created_at.isoformat() if entry.created_at else None,
            "user_id": entry.user_id,
            "action": entry.action,
            "table": entry.table_name,
            "record_id": entry.record_id,
            "fields": fields,
            "before": {k: old_value[k] for k in fields if k in old_value},
            "reappliable": reappliable,
            "reason": reason,
        })
    return out


def build_preview(org_id: int, log) -> Dict[str, Any]:
    """What restoring to before ``log``'s import would change. Writes nothing."""
    ArchiMateElement, _, _ = _models()
    if log.organization_id != org_id or log.import_source != IMPORT_SOURCE:
        raise RestoreError("Restore point not found.", 404)
    snap = _snapshot_of(log)

    created_el = _ids(snap, "created_element_ids")
    created_rel = _ids(snap, "created_relationship_ids")
    updated = {int(k): v for k, v in (snap.get("updated_elements") or {}).items()}
    domain_created = snap.get("domain_created") or []
    domain_linked = snap.get("domain_linked") or []

    present_el = _present_elements(org_id, created_el)
    present_rel = _present_relationships(org_id, created_rel)
    dependent = _dependent_relationships(org_id, created_el, set(created_rel))

    reverts = []
    for chunk in _chunks(list(updated)):
        for row in db.session.execute(
            sa.select(
                ArchiMateElement.id, ArchiMateElement.name,
                ArchiMateElement.description, ArchiMateElement.custom_properties,
            ).where(ArchiMateElement.organization_id == org_id, ArchiMateElement.id.in_(chunk))
        ).all():
            before = updated[row.id]
            reverts.append({
                "id": row.id,
                "name": row.name,
                "description_now": row.description,
                "description_before": before.get("description"),
                "description_differs": (row.description or "") != (before.get("description") or ""),
                "properties_differ": (row.custom_properties or {}) != (before.get("custom_properties") or {}),
            })

    domain_ids: Dict[str, set] = {}
    for item in domain_created:
        domain_ids.setdefault(item["table"], set()).add(int(item["id"]))
    blockers = _blocking_references(org_id, created_el, domain_ids, set(created_el))

    changes = _changes_since(org_id, log, created_el + list(updated), set(updated))
    already = bool(log.is_rolled_back)
    return {
        "restore_point": {
            "id": log.id,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "filename": log.filename,
            "strategy": snap.get("strategy"),
            "model_name": snap.get("model_name"),
            "user_id": log.user_id,
        },
        "already_restored": already,
        "counts": {
            "elements_removed": len(present_el),
            "relationships_removed": len(present_rel),
            "later_relationships_removed": len(dependent),
            "elements_reverted": len(reverts),
            "domain_rows_removed": len(domain_created),
            "domain_rows_unlinked": len(domain_linked),
            "changes_since": len(changes),
            "changes_reappliable": sum(1 for c in changes if c["reappliable"]),
        },
        "elements_removed": [{"id": r.id, "name": r.name, "type": r.type} for r in present_el],
        "relationships_removed": [
            {"id": r.id, "type": r.type, "source_id": r.source_id, "target_id": r.target_id}
            for r in present_rel
        ],
        "later_relationships_removed": [
            {"id": r.id, "type": r.type, "source_id": r.source_id, "target_id": r.target_id}
            for r in dependent
        ],
        "elements_reverted": reverts,
        "changes_since": changes,
        "blockers": blockers,
        "can_restore": not already and not blockers,
    }


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def _org_filter(table, org_id: int):
    """The organisation predicate for a table that may lack its own column."""
    if "organization_id" in table.c:
        return table.c.organization_id == org_id
    if "archimate_element_id" in table.c:
        elements = db.metadata.tables[ELEMENT_TABLE]
        return table.c.archimate_element_id.in_(
            sa.select(elements.c.id).where(elements.c.organization_id == org_id)
        )
    raise RestoreError("Restore cannot verify the organisation of a stored record.", 500)


def _audit(org_id: int, user_id: int, action: str, table_name: str, record_id, old, new) -> None:
    from app.models.audit_log import AuditLog
    from app.services.audit_log_service import AuditLogService

    db.session.add(AuditLog(
        organization_id=org_id,
        user_id=user_id,
        action=action[:20],
        table_name=table_name,
        record_id=record_id,
        old_value=old,
        new_value=new,
        ip_address=AuditLogService._resolve_ip(),
        user_agent=(AuditLogService._resolve_ua() or None),
    ))


def restore(org_id: int, user_id: int, log_id: int, reapply_audit_ids: Iterable[int] = (),
            reason: Optional[str] = None) -> Dict[str, Any]:
    """Undo the import behind ``log_id`` in this organisation only.

    ``reapply_audit_ids`` names recorded later changes (from the preview) to
    apply again on top of the restored state. Raises ``RestoreError`` before
    writing anything when the restore is not possible.
    """
    from app.models.import_audit import ImportSessionLog
    ArchiMateElement, _, ArchitectureModel = _models()
    log = ImportSessionLog.query.filter(
        ImportSessionLog.id == log_id,
        ImportSessionLog.organization_id == org_id,
        ImportSessionLog.import_source == IMPORT_SOURCE,
    ).with_for_update().first()
    if log is None:
        raise RestoreError("Restore point not found.", 404)
    if log.is_rolled_back:
        raise RestoreError("This import has already been restored.", 409)

    preview = build_preview(org_id, log)
    if preview["blockers"]:
        names = ", ".join(f"{b['count']} in {b['label']}" for b in preview["blockers"])
        raise RestoreError(
            "Other records now refer to elements this import created (" + names + "). "
            "Remove or unlink them first, then restore.",
            409,
        )
    wanted = {int(i) for i in reapply_audit_ids}
    by_id = {c["audit_id"]: c for c in preview["changes_since"]}
    unknown = [i for i in wanted if i not in by_id or not by_id[i]["reappliable"]]
    if unknown:
        raise RestoreError("A chosen change cannot be applied again.", 400)

    snap = _snapshot_of(log)
    created_el = _ids(snap, "created_element_ids")
    created_rel = _ids(snap, "created_relationship_ids")
    updated = {int(k): v for k, v in (snap.get("updated_elements") or {}).items()}
    domain_created = snap.get("domain_created") or []
    domain_linked = snap.get("domain_linked") or []
    later_rel = [r["id"] for r in preview["later_relationships_removed"]]

    result = {
        "elements_removed": 0, "relationships_removed": 0, "elements_reverted": 0,
        "domain_rows_removed": 0, "changes_reapplied": 0,
    }
    tables = db.metadata.tables
    try:
        with db.session.begin_nested():
            # 1. Relationships: the import's, then any added since that hang off its elements.
            rel_t = tables[RELATIONSHIP_TABLE]
            for chunk in _chunks(created_rel + later_rel):
                res = db.session.execute(
                    sa.delete(rel_t).where(rel_t.c.organization_id == org_id, rel_t.c.id.in_(chunk))
                )
                result["relationships_removed"] += res.rowcount or 0

            # 2. Domain rows: unlink those the import only linked, delete those it created.
            by_previous: Dict[Any, Dict[str, List[int]]] = {}
            for item in domain_linked:
                by_previous.setdefault((item["table"], item.get("previous_element_id")), {"ids": []})[
                    "ids"].append(int(item["id"]))
            for (table_name, previous), group in by_previous.items():
                t = tables[table_name]
                for chunk in _chunks(group["ids"]):
                    db.session.execute(
                        sa.update(t)
                        .where(t.c.id.in_(chunk), _org_filter(t, org_id))
                        .values(archimate_element_id=previous)
                    )
            created_by_table: Dict[str, List[int]] = {}
            for item in domain_created:
                created_by_table.setdefault(item["table"], []).append(int(item["id"]))
            for table_name, ids in created_by_table.items():
                t = tables[table_name]
                for chunk in _chunks(ids):
                    res = db.session.execute(
                        sa.delete(t).where(t.c.id.in_(chunk), _org_filter(t, org_id))
                    )
                    result["domain_rows_removed"] += res.rowcount or 0

            # 3. Elements the import created.
            el_t = tables[ELEMENT_TABLE]
            for chunk in _chunks(created_el):
                res = db.session.execute(
                    sa.delete(el_t).where(el_t.c.organization_id == org_id, el_t.c.id.in_(chunk))
                )
                result["elements_removed"] += res.rowcount or 0

            # 4. Elements the import changed go back to how they were.
            for chunk in _chunks(list(updated)):
                for element in ArchiMateElement.query.filter(
                    ArchiMateElement.organization_id == org_id, ArchiMateElement.id.in_(chunk)
                ).all():
                    before = updated[element.id]
                    element.description = before.get("description")
                    element.custom_properties = before.get("custom_properties")
                    result["elements_reverted"] += 1
            db.session.flush()

            # 5. The model record of the import, when nothing else uses it.
            model_id = snap.get("model_id")
            if model_id:
                still_used = db.session.execute(
                    sa.select(sa.func.count()).select_from(el_t).where(
                        el_t.c.organization_id == org_id, el_t.c.architecture_id == model_id)
                ).scalar() or db.session.execute(
                    sa.select(sa.func.count()).select_from(rel_t).where(
                        rel_t.c.organization_id == org_id, rel_t.c.architecture_id == model_id)
                ).scalar()
                if not still_used:
                    model_t = tables[ArchitectureModel.__tablename__]
                    db.session.execute(
                        sa.delete(model_t).where(
                            model_t.c.organization_id == org_id, model_t.c.id == model_id)
                    )

            # 6. Chosen later changes, applied again in the order they were made.
            for audit_id in sorted(wanted):
                change = by_id[audit_id]
                element = ArchiMateElement.query.filter(
                    ArchiMateElement.organization_id == org_id,
                    ArchiMateElement.id == change["record_id"],
                ).first()
                if element is None:
                    raise RestoreError("A chosen change refers to an element that is gone.", 409)
                for field, value in change["fields"].items():
                    setattr(element, field, value)
                _audit(org_id, user_id, "update", ELEMENT_TABLE, element.id,
                       change["before"], change["fields"])
                result["changes_reapplied"] += 1

            log.rollback_import(user_id, reason or "Restored before import")
            log.status = "rolled_back"
            _audit(org_id, user_id, RESTORE_ACTION, "import_audit_log", log.id,
                   {"import_started_at": log.started_at.isoformat() if log.started_at else None,
                    "filename": log.filename},
                   dict(result, reapplied_audit_ids=sorted(wanted)))
            db.session.flush()
        db.session.commit()
    except RestoreError:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        logger.error("Restore of import %s failed: %s", log_id, exc, exc_info=True)
        raise RestoreError(
            "The restore could not be completed and nothing was changed.", 500
        ) from exc
    return result

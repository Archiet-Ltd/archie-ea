"""Transitional bridge: the four retired work package stores keep the one store in step.

R1-B04 PR 2 repoints the readers of work packages to ``unified_work_packages``
and the writers listed in the build notes to ``work_package_service``. A number
of other screens and services still create, edit and delete rows of the four
retired stores (``work_packages``, ``roadmap_work_packages``,
``implementation_work_packages``, ``technology_roadmap_initiatives``). Without
this module the repointed lists would miss every row such a screen creates until
the next deploy, and an edit to an already copied row would never arrive.

How it works: a session listener sees every flush.
  * new rows of a retired store are handed, in the same transaction, to
    ``sync_source_rows`` in app/commands/consolidate_work_packages.py -- the one
    per-row copy path the deploy merge also uses; a row edited later passes only
    the columns that changed (read from the session's attribute history), and a
    changed dependency list as the ids added and removed, so what was edited on
    the one store's own screens is never written over;
  * a deleted row of a retired store removes its copy through
    ``work_package_service.delete_work_package`` (dependency clean-up included);
  * a retired row whose copy was deleted carries ``retired_at`` and is never
    copied again.

The bridge is deleted by R1-B04 PR 3 ("retire the old-store writers"), together
with the last writer of the old stores. Bulk ``query.delete()`` and raw SQL
writes do not pass through a flush and are not bridged; the deploy merge and
its --verify step catch those.
"""
from __future__ import annotations

import contextlib
import logging

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

logger = logging.getLogger(__name__)

_suspended = 0


@contextlib.contextmanager
def suspended():
    """Stop the bridge for the duration (data migrations and the tests that
    exercise the deploy merge on rows the bridge would already have copied)."""
    global _suspended
    _suspended += 1
    try:
        yield
    finally:
        _suspended -= 1


_TABLES = None


def _tables():
    """{mapped class: table name} of the four retired stores."""
    global _TABLES
    if _TABLES is None:
        _TABLES = _load_tables()
    return _TABLES


def _load_tables():
    from app.models.implementation_migration import TechnologyRoadmapInitiative, WorkPackage
    from app.models.implementation_planning import ImplementationWorkPackage
    from app.models.roadmap_models import RoadmapWorkPackage

    return {
        WorkPackage: "work_packages",
        RoadmapWorkPackage: "roadmap_work_packages",
        ImplementationWorkPackage: "implementation_work_packages",
        TechnologyRoadmapInitiative: "technology_roadmap_initiatives",
    }


def _caller_org():
    """The organisation a row written with no attribution of its own belongs to:
    the request's, or the tenant a scheduled job runs for (tenant_scope in
    app/jobs/tenant_safe_job.py sets both g values)."""
    try:
        from flask import g, has_app_context

        if has_app_context():
            return getattr(g, "current_org_id", None) or getattr(
                g, "_tenant_scope_organization_id", None)
    except Exception:  # pragma: no cover - no Flask context at all
        pass
    return None


def _history_list(history, which):
    """The list value an attribute history holds (None when it holds none)."""
    values = getattr(history, which)
    for value in values or ():
        if isinstance(value, (list, tuple)):
            return list(value)
    return None


def _changes_of(obj, table):
    """({source column, ...}, (old dependency ids, new ids) or None) for a row of a
    retired store that was edited: its changed mapped columns, named as the
    source store names them, read from the attribute history."""
    from app.commands.consolidate_work_packages import _DEP_SOURCE_COLUMN, _LINK_RELATIONSHIPS

    state = inspect(obj)
    columns = set()
    dependency_change = None
    dep_column = _DEP_SOURCE_COLUMN.get(table)
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        name = attr.columns[0].name
        if name == dep_column:
            new = _history_list(history, "added")
            if new is None:
                new = list(getattr(obj, attr.key) or [])
            dependency_change = (_history_list(history, "deleted"), new)
        else:
            columns.add(name)
    for key in _LINK_RELATIONSHIPS:
        if key in state.mapper.relationships and state.attrs[key].history.has_changes():
            columns.add(key)
    return columns, dependency_change


def _table_of(tables, obj):
    for cls, name in tables.items():
        if isinstance(obj, cls):
            return name
    return None


def _before_flush(session, flush_context, instances):
    if _suspended or not session.deleted:
        return
    tables = _tables()
    from app.services import work_package_service

    for obj in list(session.deleted):
        table = _table_of(tables, obj)
        source_id = getattr(obj, "id", None)
        if table is None or source_id is None:
            continue
        row = session.execute(
            text("SELECT id, organization_id FROM unified_work_packages "
                 "WHERE source_table = :t AND source_id = :i"),
            {"t": table, "i": source_id},
        ).first()
        if row is None:
            continue
        copy_id, org_id = row
        try:
            if org_id is None:
                raise work_package_service.WorkPackageNotFound("unattributed copy")
            work_package_service.delete_work_package(copy_id, organization_id=org_id, flush=False)
        except work_package_service.WorkPackageError:
            # Not visible through the organisation filter (quarantined copy, or
            # a caller in another organisation): remove the copy directly so a
            # deleted row does not live on in the one store.
            session.execute(text("DELETE FROM unified_work_packages WHERE id = :i"), {"i": copy_id})


def _after_flush(session, flush_context):
    if _suspended or (not session.new and not session.dirty):
        return
    tables = _tables()
    pending = {}
    changed = {}
    dependency_changes = {}
    new_objects = set(session.new)
    for obj in list(session.new) + [o for o in session.dirty if session.is_modified(o)]:
        table = _table_of(tables, obj)
        if table is not None and getattr(obj, "id", None) is not None:
            pending.setdefault(table, {})[obj.id] = obj
            if obj not in new_objects:
                columns, dependency_change = _changes_of(obj, table)
                if columns:
                    changed.setdefault(table, {})[obj.id] = columns
                if dependency_change is not None:
                    dependency_changes.setdefault(table, {})[obj.id] = dependency_change
    if not pending:
        return

    from app.commands.consolidate_work_packages import sync_source_rows

    conn = session.connection()
    caller = _caller_org()
    for table, objs in pending.items():
        sync_source_rows(
            conn, table, list(objs), update_existing=True, fallback_org_id=caller,
            changed=changed.get(table), dependency_changes=dependency_changes.get(table),
            defer_links=lambda uids, _s=session: _s.info.setdefault(_PENDING_LINKS, set()).update(uids),
        )
        marks = {
            row[0]: row[1:] for row in conn.execute(
                text(f'SELECT id, retired_into_id, retired_at FROM "{table}" WHERE id = ANY(:ids)'),
                {"ids": list(objs)},
            )
        }
        for source_id, obj in objs.items():
            if source_id in marks:
                set_committed_value(obj, "retired_into_id", marks[source_id][0])
                set_committed_value(obj, "retired_at", marks[source_id][1])


_PENDING_LINKS = "work_package_bridge_pending_links"


def _run_pending_links(session):
    """Turn the plateau and gap values the copies carry into relationships. A
    session cannot flush inside its own flush, so the copy only notes the ids and
    this runs as soon as the outermost flush has returned, in the same transaction."""
    uids = session.info.pop(_PENDING_LINKS, None)
    if not uids:
        return
    from app.commands.consolidate_work_packages import _Stats, _link_columns_to_relationships

    _link_columns_to_relationships(_Stats(), unified_ids=sorted(uids))


def _flush_then_link(original):
    def flush(self, objects=None):
        outer = not self._flushing
        result = original(self, objects)
        if outer and self.info.get(_PENDING_LINKS):
            _run_pending_links(self)
        return result

    flush._wp_bridge_wrapped = True
    return flush


def register(app=None):
    """Install the session listeners once per process (create_app may run many times)."""
    for name, fn in (("before_flush", _before_flush), ("after_flush", _after_flush)):
        if not event.contains(Session, name, fn):
            event.listen(Session, name, fn)
    if not getattr(Session.flush, "_wp_bridge_wrapped", False):
        Session.flush = _flush_then_link(Session.flush)

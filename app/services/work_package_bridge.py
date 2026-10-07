"""Transitional bridge: the four retired work package stores keep the one store in step.

R1-B04 PR 2 repoints the readers of work packages to ``unified_work_packages``
and the writers listed in the build notes to ``work_package_service``. A number
of other screens and services still create, edit and delete rows of the four
retired stores (``work_packages``, ``roadmap_work_packages``,
``implementation_work_packages``, ``technology_roadmap_initiatives``). Without
this module the repointed lists would miss every row such a screen creates until
the next deploy, and an edit to an already copied row would never arrive.

How it works: a session listener sees every flush.
  * new or changed rows of a retired store are handed, in the same transaction,
    to ``sync_source_rows`` in app/commands/consolidate_work_packages.py -- the
    one per-row copy path the deploy merge also uses;
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

from sqlalchemy import event, text
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


def _tables():
    """{mapped class: table name} of the four retired stores."""
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
    try:
        from flask import g, has_app_context

        if has_app_context():
            return getattr(g, "current_org_id", None)
    except Exception:  # pragma: no cover - no Flask context at all
        pass
    return None


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
    for obj in list(session.new) + [o for o in session.dirty if session.is_modified(o)]:
        table = _table_of(tables, obj)
        if table is not None and getattr(obj, "id", None) is not None:
            pending.setdefault(table, {})[obj.id] = obj
    if not pending:
        return

    from app.commands.consolidate_work_packages import sync_source_rows

    conn = session.connection()
    caller = _caller_org()
    for table, objs in pending.items():
        sync_source_rows(
            conn, table, list(objs), update_existing=True, fallback_org_id=caller
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


def register(app=None):
    """Install the session listeners once per process (create_app may run many times)."""
    for name, fn in (("before_flush", _before_flush), ("after_flush", _after_flush)):
        if not event.contains(Session, name, fn):
            event.listen(Session, name, fn)

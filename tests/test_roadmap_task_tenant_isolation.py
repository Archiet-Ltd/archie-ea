"""Cross-tenant isolation for roadmap_tasks.

Before TenantMixin, this table (core roadmap/portfolio-planning data --
titles, dates, effort estimates, assignees) had no tenant boundary at all: any
org could read/edit every other org's roadmap tasks via
routes_capability_roadmap.py, migration_planner.py and
consolidation_list_routes.py.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_task(db_session, org_id, title):
    from app.models.roadmap import RoadmapTask

    row = RoadmapTask(title=title, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def test_roadmap_task_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """org A must not see org B's roadmap tasks."""
    from app.models.roadmap import RoadmapTask

    org_a, org_b = make_org("a"), make_org("b")
    _make_task(db_session, org_a.id, "Migrate CRM")
    b_task = _make_task(db_session, org_b.id, "Migrate ERP")

    with tenant_ctx(org_a.id):
        visible_ids = {t.id for t in RoadmapTask.query.all()}

    assert b_task.id not in visible_ids, (
        "TENANT LEAK: org A can see org B's roadmap task."
    )


def test_roadmap_task_insert_inherits_current_org(db_session, make_org, tenant_ctx):
    """A task created inside a request (the normal /roadmap create route path)
    must be stamped with the acting org automatically, with no explicit
    organization_id passed -- matching how routes_capability_roadmap.py's
    RoadmapTask(...) constructor call actually works."""
    from app.models.roadmap import RoadmapTask

    org = make_org("a")
    with tenant_ctx(org.id):
        task = RoadmapTask(title="New migration task")
        db_session.add(task)
        db_session.flush()

        assert task.organization_id == org.id, (
            "a RoadmapTask created in request context must inherit "
            "g.current_org_id automatically via TenantMixin's before_flush"
        )


# --------------------------------------------- background workflow thread read
#
# `_handle_roadmap_generation` runs from `_run_workflow_in_background` under a
# bare `self.app.app_context()` -- an APP context, not a REQUEST context, so
# `g.current_org_id` is never set and TenantMixin's before_flush/query listener
# is a no-op. The handler's read must therefore filter on the workflow
# instance's own organisation explicitly, the same way this engine's other
# background creation sites stamp `organization_id=instance.organization_id`.


def test_background_roadmap_generation_reads_only_instance_org(db_session, make_org, tenant_ctx):
    from app.models.roadmap import RoadmapTask
    from app.services.ea_workflow_engine import EAWorkflowEngine
    from test_arb_ea_tenant_isolation import _make_workflow_definition, _make_workflow_instance

    org_a, org_b = make_org("bg-rm-a"), make_org("bg-rm-b")

    a_task = RoadmapTask(title="A active task", organization_id=org_a.id, status="active")
    b_task = RoadmapTask(title="B active task", organization_id=org_b.id, status="active")
    db_session.add_all([a_task, b_task])
    db_session.flush()
    a_task_id = a_task.id

    with tenant_ctx(org_a.id):
        definition = _make_workflow_definition(db_session, "bg-roadmap")
        instance = _make_workflow_instance(db_session, org_a.id, definition, "A")

    # Simulate `_run_workflow_in_background`: no tenant_ctx, no
    # g.current_org_id -- only the bare app_context that db_session already
    # holds open. `tenant_ctx` above reuses that same AppContext (Flask does
    # not push a second one for the same app), so g.current_org_id would
    # otherwise leak from org_a's block even after the `with` exits; clear it
    # explicitly so this really has no tenant context, matching the
    # background thread's fresh, empty `g`.
    from flask import g
    g.pop("current_org_id", None)
    assert not hasattr(g, "current_org_id") or g.current_org_id is None

    engine = EAWorkflowEngine()
    result = engine._handle_roadmap_generation(instance, {}, {})

    output_ids = {row["id"] for row in result["output"]}
    assert output_ids == {a_task_id}, (
        "TENANT LEAK: background roadmap generation returned task ids outside "
        f"instance {instance.id}'s organisation ({org_a.id}) -- got {output_ids}"
    )

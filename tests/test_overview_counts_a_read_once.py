"""After a read from GitHub the Overview counts each record once, all of it from the connected source.

The sign-up step said how many items the read added; the Overview's "Records added", "From connected
sources" and "By hand" must say the same, and a record nobody typed is never "by hand". An application
is one record though it is stored as an inventory row and a model element.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.test_github_builds_the_model import (  # noqa: F401  (fixtures)
    acme,
    approved,
    connect,
    first_sync,
    github,
    org_a,
    org_b,
    people,
    sync,
)

pytestmark = pytest.mark.usefixtures("db_session")


def _added(org_id):
    from app.modules.dashboard.services import overview_figures as figures

    now = datetime.utcnow()
    changes = figures._history(org_id, now)
    assert changes is not None
    return figures._activity(changes, now, org_id)["ranges"]["90d"]


def test_a_read_is_counted_once_and_all_of_it_is_from_the_connected_source(
    db_session, org_a, org_b, approved, tenant_ctx
):
    from app.connectors import github as gh

    with tenant_ctx(org_a.id):
        expected = sum(gh.model_counts(org_a.id).values())
        added = _added(org_a.id)
    assert expected == 8 + 3 + 1 + 9
    assert (added["total"], added["connected"], added["manual"]) == (expected, expected, 0), added
    with tenant_ctx(org_b.id):
        other = _added(org_b.id)
    assert (other["total"], other["connected"], other["manual"]) == (0, 0, 0), other


def test_one_record_added_by_hand_after_a_read_is_the_only_one_by_hand(db_session, org_a, approved, tenant_ctx):
    from app.connectors import github as gh
    from app.services.archimate_backbone import create_backbone_element
    from app.services.change_source import change_source

    with tenant_ctx(org_a.id):
        expected = sum(gh.model_counts(org_a.id).values())
        with change_source("screen", user_id=approved.people.admin.id):
            create_backbone_element(
                element_type="BusinessActor", layer="Business", name="Treasury desk", organization_id=org_a.id
            )
        db_session.commit()
        added = _added(org_a.id)
    assert (added["total"], added["connected"], added["manual"]) == (expected + 1, expected, 1), added


def _activity_of(org_id):
    from app.modules.dashboard.services import overview_figures as figures

    now = datetime.utcnow()
    return figures._activity(figures._history(org_id, now), now, org_id)


def test_changes_this_week_adds_up_to_the_same_records_added_after_a_read(db_session, org_a, org_b, approved, tenant_ctx):
    from app.connectors import github as gh

    with tenant_ctx(org_a.id):
        expected = sum(gh.model_counts(org_a.id).values())
        act = _activity_of(org_a.id)
    assert act["week"]["added"] == act["ranges"]["7d"]["total"] == act["ranges"]["90d"]["total"] == expected, act["week"]
    with tenant_ctx(org_b.id):
        other = _activity_of(org_b.id)
    assert (other["week"]["added"], other["ranges"]["7d"]["total"]) == (0, 0), other["week"]


def test_one_application_typed_after_a_read_adds_one_to_both_figures(db_session, org_a, org_b, approved, tenant_ctx):
    from app.connectors import github as gh
    from app.services.change_source import change_source
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import create_backbone_element

    with tenant_ctx(org_a.id):
        expected = sum(gh.model_counts(org_a.id).values())
        with change_source("screen", user_id=approved.people.admin.id):
            element = create_backbone_element(
                element_type="ApplicationComponent", layer="Application", name="Treasury Desk", organization_id=org_a.id
            )
            db_session.add(ApplicationComponent(
                name="Treasury Desk", organization_id=org_a.id, archimate_element_id=element.id, lifecycle_status="active"
            ))
            db_session.flush()
        db_session.commit()
        act = _activity_of(org_a.id)
    assert act["ranges"]["7d"]["total"] == expected + 1, act["ranges"]["7d"]
    assert act["week"]["added"] == expected + 1, act["week"]
    with tenant_ctx(org_b.id):
        assert _activity_of(org_b.id)["week"]["added"] == 0

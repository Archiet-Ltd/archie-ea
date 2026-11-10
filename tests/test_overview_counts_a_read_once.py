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

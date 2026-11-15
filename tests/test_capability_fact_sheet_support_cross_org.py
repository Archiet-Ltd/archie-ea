"""A capability's fact sheet lists the supporting applications of the signed-in organisation only."""
from __future__ import annotations

import pytest

from tests.test_capability_gap_signals_cross_org import _user

pytestmark = pytest.mark.usefixtures("db_session")


def test_fact_sheet_lists_only_this_organisations_applications(db_session, make_org, client, login_as):
    from app.services.capability_support import record_capability_support
    from tests.test_capability_support_writer import seed

    org_a, org_b = make_org("fsheet-a"), make_org("fsheet-b")
    user_b = _user(db_session, org_b, "fb")
    app_a, cap_a = seed(db_session, org_a, "FA")
    app_b, cap_b = seed(db_session, org_b, "FB")
    record_capability_support(org_a.id, app_a.archimate_element_id, cap_a.archimate_element_id)
    record_capability_support(org_b.id, app_b.archimate_element_id, cap_b.archimate_element_id)
    db_session.flush()

    login_as(client, user_b)
    own = client.get("/enterprise/capabilities/%d/fact-sheet" % cap_b.id)
    assert own.status_code == 200
    body = own.get_data(as_text=True)
    assert app_b.name in body
    assert app_a.name not in body

    other = client.get("/enterprise/capabilities/%d/fact-sheet" % cap_a.id)
    assert other.status_code == 404
    assert app_a.name not in other.get_data(as_text=True)

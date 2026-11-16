"""The capability detail and the app counts read the one support store, for the signed-in organisation."""
from __future__ import annotations

import pytest

from tests.test_capability_gap_signals_cross_org import _user

pytestmark = pytest.mark.usefixtures("db_session")


def test_detail_json_lists_the_applications_the_fact_sheet_lists(db_session, make_org, client, login_as):
    from app.services.capability_support import record_capability_support
    from tests.test_capability_support_writer import seed

    org = make_org("capdetail-a")
    login_as(client, _user(db_session, org, "da"))
    app_row, cap = seed(db_session, org, "DA")
    other_app, _ = seed(db_session, org, "DA2")
    record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id)
    db_session.flush()

    body = client.get("/enterprise/capabilities/%d" % cap.id).get_json()["data"]
    assert [a["name"] for a in body["applications"]] == [app_row.name]
    assert body["application_count"] == 1
    sheet = client.get("/enterprise/capabilities/%d/fact-sheet" % cap.id).get_data(as_text=True)
    assert app_row.name in sheet and other_app.name not in sheet


def test_detail_json_of_another_organisations_capability_is_not_found(db_session, make_org, client, login_as):
    from tests.test_capability_support_writer import seed

    org_a, org_b = make_org("capdetail-b"), make_org("capdetail-c")
    login_as(client, _user(db_session, org_b, "db"))
    _, cap_a = seed(db_session, org_a, "DB")
    assert client.get("/enterprise/capabilities/%d" % cap_a.id).status_code == 404


def test_app_counts_never_count_another_organisations_support(db_session, make_org, client, login_as):
    from app.services.capability_support import record_capability_support
    from tests.test_capability_support_writer import seed

    org_a, org_b = make_org("appcount-a"), make_org("appcount-b")
    user_b = _user(db_session, org_b, "cb")
    app_a, cap_a = seed(db_session, org_a, "CA")
    app_b, cap_b = seed(db_session, org_b, "CB")
    record_capability_support(org_a.id, app_a.archimate_element_id, cap_a.archimate_element_id)
    record_capability_support(org_b.id, app_b.archimate_element_id, cap_b.archimate_element_id)
    db_session.flush()

    login_as(client, user_b)
    counts = client.get("/enterprise/capabilities/app-counts").get_json()["counts"]
    assert counts.get(str(cap_b.id)) == 1
    assert str(cap_a.id) not in counts

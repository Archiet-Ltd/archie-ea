"""The recommendations list is worked out from the organisation's own records, in a fixed order, with no model."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _application(db_session, org_id, name, **fields):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id, **fields)
    db_session.add(row)
    db_session.flush()
    return row


def test_an_application_with_no_owner_is_advised_an_owner_and_only_in_its_own_organisation(
    db_session, make_org, tenant_ctx
):
    from app.services import recommendation_service

    org_a, org_b = make_org("a"), make_org("b")
    _application(db_session, org_a.id, "Unowned ledger")
    _application(db_session, org_a.id, "Owned ledger", legacy_application_owner="Dana Reyes")
    _application(db_session, org_b.id, "Other organisation unowned")

    with tenant_ctx(org_a.id):
        result = recommendation_service.recommendations(org_a.id)

    titles = [row["title"] for row in result["rows"]]
    assert "Name an owner for Unowned ledger" in titles
    assert "Name an owner for Owned ledger" not in titles
    assert not any("Other organisation" in title for title in titles), "another organisation's record was listed"
    row = next(r for r in result["rows"] if r["title"].endswith("Unowned ledger"))
    assert row["category"] == "Ownership" and row["reason"] and row["footer"]["evidence"]
    assert result["total"] == len(result["rows"])


def test_the_list_is_cut_at_the_limit_and_says_how_many_there_were(db_session, make_org, tenant_ctx):
    from app.services import recommendation_service

    org = make_org("limit")
    for index in range(7):
        _application(db_session, org.id, "Unowned %d" % index)

    with tenant_ctx(org.id):
        result = recommendation_service.recommendations(org.id, limit=3)

    assert len(result["rows"]) == 3 and result["total"] == 7
    assert [row["position"] for row in result["rows"]] == [1, 2, 3]


def test_an_organisation_with_no_records_has_no_recommendations(db_session, make_org, tenant_ctx):
    from app.services import recommendation_service

    org = make_org("empty")
    with tenant_ctx(org.id):
        result = recommendation_service.recommendations(org.id)
    assert result["rows"] == [] and result["total"] == 0

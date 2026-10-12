"""The what-if comparison counts what the one impact walk finds, per organisation."""
from __future__ import annotations

import uuid

import pytest

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.modules.intelligence.services.what_if import MAX_SCENARIOS, compare


def _el(db_session, org, name, type_, layer):
    e = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org.id)
    db_session.add(e)
    db_session.flush()
    return e


def _rel(db_session, org, source, target, type_="Serving"):
    r = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org.id)
    db_session.add(r)
    db_session.flush()
    return r


@pytest.fixture
def model(db_session, make_org):
    """Org A: billing and crm both serve invoicing; billing also serves reporting."""
    org_a, org_b = make_org("whatif-a"), make_org("whatif-b")
    billing = _el(db_session, org_a, "Billing", "ApplicationComponent", "application")
    crm = _el(db_session, org_a, "CRM", "ApplicationComponent", "application")
    invoicing = _el(db_session, org_a, "Invoicing", "BusinessProcess", "business")
    reporting = _el(db_session, org_a, "Reporting", "BusinessProcess", "business")
    lonely = _el(db_session, org_a, "Lonely", "ApplicationComponent", "application")
    _rel(db_session, org_a, billing, invoicing)
    _rel(db_session, org_a, crm, invoicing)
    _rel(db_session, org_a, billing, reporting)
    foreign = _el(db_session, org_b, "Foreign", "ApplicationComponent", "application")
    foreign_dep = _el(db_session, org_b, "Foreign dependent", "BusinessProcess", "business")
    _rel(db_session, org_b, foreign, foreign_dep)
    return dict(org_a=org_a, org_b=org_b, billing=billing, crm=crm, invoicing=invoicing,
                reporting=reporting, lonely=lonely, foreign=foreign)


def _names(scenario):
    return [a["name"] for a in scenario["affected"]]


def test_counts_what_stops_for_each_element(model, tenant_ctx):
    with tenant_ctx(model["org_a"].id):
        result = compare([model["billing"].id, model["crm"].id, model["lonely"].id])
    billing, crm, lonely = result["scenarios"]
    assert _names(billing) == ["Invoicing", "Reporting"]
    assert billing["direct_count"] == 2
    assert _names(crm) == ["Invoicing"]
    assert lonely["found"] and lonely["affected"] == []


def test_shared_effects_are_listed_once(model, tenant_ctx):
    with tenant_ctx(model["org_a"].id):
        result = compare([model["billing"].id, model["crm"].id])
    assert [a["name"] for a in result["shared"]] == ["Invoicing"]


def test_duplicates_collapse_and_a_fourth_is_not_compared(model, db_session, tenant_ctx):
    extra = [_el(db_session, model["org_a"], "Extra %d" % i, "Node", "technology") for i in range(3)]
    ids = [model["billing"].id, model["billing"].id] + [e.id for e in extra]
    with tenant_ctx(model["org_a"].id):
        result = compare(ids)
    assert len(result["scenarios"]) == MAX_SCENARIOS
    assert result["truncated"] is True


def test_another_organisations_element_is_not_found(model, tenant_ctx):
    with tenant_ctx(model["org_a"].id):
        result = compare([model["foreign"].id])
    only = result["scenarios"][0]
    assert only["found"] is False and "affected" not in only
    with tenant_ctx(model["org_b"].id):
        assert _names(compare([model["foreign"].id])["scenarios"][0]) == ["Foreign dependent"]


def test_page_shows_the_comparison_and_not_found(model, db_session, client, login_as):
    from app.models.user import User

    user = User(email="whatif-%s@example.com" % uuid.uuid4().hex[:8], organization_id=model["org_a"].id, confirmed=True)
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    db_session.commit()
    login_as(client, user)
    body = client.get("/intelligence/what-if?element=%d&element=%d" % (model["billing"].id, model["crm"].id)).get_data(as_text=True)
    assert 'data-testid="whatif-scenario"' in body
    assert "Reporting" in body and "Affected by more than one" in body
    body = client.get("/intelligence/what-if?element=%d" % model["foreign"].id).get_data(as_text=True)
    assert "Foreign dependent" not in body and "does not exist in your organisation" in body
    body = client.get("/intelligence/what-if?element=abc").get_data(as_text=True)
    assert "Pick something to compare" in body

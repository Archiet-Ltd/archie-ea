"""Demand triage: what already covers the capability, and the route recorded.

Two organisations throughout: coverage and recommendations are read from the
signed-in organisation's applications only.
"""

import uuid

import pytest


def _clear_login_cache():
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _client_for(app, db_session, org, role="platform_admin"):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(
        email=f"triage-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Tri",
        last_name="Age",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid
    _clear_login_cache()
    return client, user


def _capability(db_session, org, name):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name=name, organization_id=org.id, level=1)
    db_session.add(cap)
    db_session.flush()
    return cap


def _application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _map(db_session, cap, app_row, support_level="full", coverage=90):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    db_session.add(
        UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap.id,
            application_component_id=app_row.id,
            support_level=support_level,
            coverage_percentage=coverage,
            is_active=True,
        )
    )
    db_session.flush()


@pytest.fixture
def world(app, db_session, make_org):
    org_a, org_b = make_org("triage-a"), make_org("triage-b")
    client_a, _ = _client_for(app, db_session, org_a)
    client_b, _ = _client_for(app, db_session, org_b)
    return {"a": (org_a, client_a), "b": (org_b, client_b)}


def _tenant(org_id):
    from flask import g

    g.current_org_id = org_id


def test_full_coverage_recommends_reuse(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Supplier Onboarding")
    _map(db_session, cap, _application(db_session, org, "Onboard Hub"))
    from app.modules.portfolio.services.demand_triage import recommend

    rec = recommend(cap.id)
    assert rec["route"] == "reuse"
    assert "Onboard Hub" in rec["reason"]
    assert [a["name"] for a in rec["applications"]] == ["Onboard Hub"]


def test_partial_coverage_recommends_extend(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Supplier Onboarding")
    _map(db_session, cap, _application(db_session, org, "Onboard Lite"), "partial", 30)
    from app.modules.portfolio.services.demand_triage import recommend

    assert recommend(cap.id)["route"] == "extend"


def test_no_coverage_recommends_buy_or_build(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Carbon Reporting")
    from app.modules.portfolio.services.demand_triage import recommend

    rec = recommend(cap.id)
    assert rec["route"] == "buy_or_build"
    assert rec["applications"] == []


def test_no_capability_gives_no_recommendation(world):
    org, _ = world["a"]
    _tenant(org.id)
    from app.modules.portfolio.services.demand_triage import recommend

    assert recommend(None)["route"] is None


def test_unrecorded_coverage_figure_is_not_zero(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Payroll")
    _map(db_session, cap, _application(db_session, org, "Pay One"), "partial", 0)
    from app.modules.portfolio.services.demand_triage import coverage_for_capability

    assert coverage_for_capability(cap.id)[0]["coverage_percentage"] is None


def test_other_organisations_applications_are_never_listed(world, db_session):
    org_a, _ = world["a"]
    org_b, _ = world["b"]
    _tenant(org_a.id)
    cap = _capability(db_session, org_a, "Billing")
    _map(db_session, cap, _application(db_session, org_a, "Org A Billing"))
    foreign = _application(db_session, org_b, "Org B Billing")
    _map(db_session, cap, foreign)
    from app.modules.portfolio.services.demand_triage import coverage_for_capability

    names = [a["name"] for a in coverage_for_capability(cap.id)]
    assert names == ["Org A Billing"]


def test_demand_submitted_with_capability_shows_coverage_and_records_route(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Supplier Onboarding")
    _map(db_session, cap, _application(db_session, org, "Onboard Hub"))
    _clear_login_cache()
    resp = client.post("/portfolio/demands/new", data={"title": "Faster onboarding", "capability_id": cap.id})
    assert resp.status_code == 302
    _clear_login_cache()
    page = client.get("/portfolio/demands").get_data(as_text=True)
    assert "Onboard Hub" in page
    assert "Recommended: reuse" in page

    from app.models.demand import Demand

    demand = db_session.query(Demand).filter_by(title="Faster onboarding").one()
    _clear_login_cache()
    resp = client.post(
        "/portfolio/demands/%d/decide" % demand.id,
        data={"status": "approved", "route": "reuse", "decision_rationale": "Use what we have"},
    )
    assert resp.status_code == 302
    db_session.refresh(demand)
    assert demand.triage_route == "reuse"
    assert "Onboard Hub" in demand.triage_route_reasons
    _clear_login_cache()
    page = client.get("/portfolio/demands").get_data(as_text=True)
    assert "Reuse" in page


def test_unknown_route_is_refused(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    from app.models.demand import Demand

    demand = Demand(title="X", organization_id=org.id, status="submitted")
    db_session.add(demand)
    db_session.flush()
    _clear_login_cache()
    client.post("/portfolio/demands/%d/decide" % demand.id, data={"status": "approved", "route": "outsource"})
    db_session.refresh(demand)
    assert demand.status == "submitted"
    assert demand.triage_route is None


def test_another_organisations_capability_cannot_be_chosen(world, db_session):
    org_a, client_a = world["a"]
    org_b, _ = world["b"]
    _tenant(org_b.id)
    foreign = _capability(db_session, org_b, "Org B Only")
    _tenant(org_a.id)
    _clear_login_cache()
    resp = client_a.post("/portfolio/demands/new", data={"title": "Sneaky", "capability_id": foreign.id})
    assert resp.status_code == 400
    from app.models.demand import Demand

    assert db_session.query(Demand).filter_by(title="Sneaky").count() == 0

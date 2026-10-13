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


def _demand(org, title, capability_id=None):
    from app.models.demand import Demand

    return Demand(title=title, organization_id=org.id, status="submitted", capability_id=capability_id)


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


def test_unrecorded_coverage_is_none_and_recorded_zero_is_zero(world, db_session):
    # The mapping column defaults to 0, so a row saved without a figure reads
    # as a recorded 0; only an explicit NULL is "not recorded".
    org, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Payroll")
    _map(db_session, cap, _application(db_session, org, "Pay Zero"), "partial", 0)
    null_app = _application(db_session, org, "Pay Null")
    _map(db_session, cap, null_app, "partial", 90)
    # Legacy rows can hold NULL; the ORM default would turn an explicit None into 0.
    from app import db
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as M

    db_session.execute(
        db.update(M).where(M.application_component_id == null_app.id).values(coverage_percentage=None)
    )
    from app.modules.portfolio.services.demand_triage import coverage_for_capability

    by_name = {a["name"]: a["coverage_percentage"] for a in coverage_for_capability(cap.id)}
    assert by_name["Pay Zero"] == 0
    assert by_name["Pay Null"] is None


def test_recorded_zero_coverage_renders_as_zero_percent(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Payroll")
    _map(db_session, cap, _application(db_session, org, "Pay Zero"), "partial", 0)
    db_session.add(_demand(org, "Payroll rework", cap.id))
    db_session.flush()
    _clear_login_cache()
    page = client.get("/portfolio/demands").get_data(as_text=True)
    assert "0% coverage" in page
    assert "coverage not recorded" not in page


def test_threshold_boundary_80_percent_is_reuse_79_is_extend(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    at = _capability(db_session, org, "At threshold")
    below = _capability(db_session, org, "Below threshold")
    _map(db_session, at, _application(db_session, org, "App 80"), "partial", 80)
    _map(db_session, below, _application(db_session, org, "App 79"), "partial", 79)
    from app.modules.portfolio.services.demand_triage import recommend

    assert recommend(at.id)["route"] == "reuse"
    assert recommend(below.id)["route"] == "extend"


def test_soft_deleted_application_is_not_coverage(world, db_session):
    from datetime import datetime

    org, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Archive")
    gone = _application(db_session, org, "Retired App")
    gone.deleted_at = datetime.utcnow()
    _map(db_session, cap, gone)
    from app.modules.portfolio.services.demand_triage import coverage_for_capability, recommend

    assert coverage_for_capability(cap.id) == []
    assert recommend(cap.id)["route"] == "buy_or_build"


def test_recommend_many_matches_recommend(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap1 = _capability(db_session, org, "One")
    cap2 = _capability(db_session, org, "Two")
    _map(db_session, cap1, _application(db_session, org, "App One"))
    from app.modules.portfolio.services.demand_triage import recommend, recommend_many

    many = recommend_many([cap1.id, cap2.id, None, cap1.id])
    assert many[cap1.id] == recommend(cap1.id)
    assert many[cap2.id] == recommend(cap2.id)
    assert many[None]["route"] is None


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
    assert 'class="px-4 py-3">Reuse</td>' in page


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


# -- Isolation: two organisations, and no resolved organisation ---------------

def test_unresolved_organisation_sees_nothing(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Billing")
    _map(db_session, cap, _application(db_session, org, "Org A Billing"))
    _tenant(None)
    from app.modules.portfolio.services.demand_triage import (
        coverage_for_capability,
        recommend,
        recommend_many,
        visible_capabilities,
        visible_capability,
    )

    assert visible_capabilities() == []
    assert visible_capability(cap.id) is None
    assert coverage_for_capability(cap.id) == []
    rec = recommend(cap.id)
    assert rec["route"] is None and rec["applications"] == []
    assert "Org A Billing" not in rec["reason"]
    assert recommend_many([cap.id])[cap.id]["applications"] == []


def test_other_organisations_capability_is_not_found_by_recommend(world, db_session):
    org_a, _ = world["a"]
    org_b, _ = world["b"]
    _tenant(org_b.id)
    foreign = _capability(db_session, org_b, "Org B Only")
    _map(db_session, foreign, _application(db_session, org_b, "Org B App"))
    _tenant(org_a.id)
    from app.modules.portfolio.services.demand_triage import recommend, visible_capabilities

    rec = recommend(foreign.id)
    assert rec["route"] is None
    assert "not found" in rec["reason"]
    assert "Org B App" not in str(rec)
    assert foreign.id not in {c.id for c in visible_capabilities()}


def test_shared_reference_capability_is_visible_to_every_organisation(world, db_session):
    from app.models.unified_capability import UnifiedCapability

    org_a, _ = world["a"]
    ref = UnifiedCapability(name="Shared Reference Cap", organization_id=None, scope="reference", level=1)
    db_session.add(ref)
    db_session.flush()
    _tenant(org_a.id)
    from app.modules.portfolio.services.demand_triage import visible_capability

    assert visible_capability(ref.id) is not None


def test_organisation_b_cannot_decide_organisation_a_demand(world, db_session):
    org_a, _ = world["a"]
    _, client_b = world["b"]
    demand = _demand(org_a, "A demand")
    db_session.add(demand)
    db_session.flush()
    _clear_login_cache()
    resp = client_b.post(
        "/portfolio/demands/%d/decide" % demand.id,
        data={"status": "approved", "route": "reuse"},
    )
    assert resp.status_code == 404
    db_session.refresh(demand)
    assert demand.status == "submitted"
    assert demand.triage_route is None
    assert demand.triage_route_reasons is None


def test_organisation_b_queue_shows_none_of_organisation_a_data(world, db_session):
    org_a, _ = world["a"]
    _, client_b = world["b"]
    _tenant(org_a.id)
    cap = _capability(db_session, org_a, "Org A Secret Capability")
    _map(db_session, cap, _application(db_session, org_a, "Org A Secret App"))
    db_session.add(_demand(org_a, "Org A Secret Demand", cap.id))
    db_session.flush()
    _clear_login_cache()
    page = client_b.get("/portfolio/demands").get_data(as_text=True)
    assert "Org A Secret" not in page
    _clear_login_cache()
    form = client_b.get("/portfolio/demands/new").get_data(as_text=True)
    assert "Org A Secret Capability" not in form


def test_picker_lists_only_own_and_shared_capabilities(world, db_session):
    org_a, client_a = world["a"]
    org_b, _ = world["b"]
    _tenant(org_b.id)
    _capability(db_session, org_b, "Org B Hidden Cap")
    _tenant(org_a.id)
    _capability(db_session, org_a, "Org A Visible Cap")
    _clear_login_cache()
    form = client_a.get("/portfolio/demands/new").get_data(as_text=True)
    assert "Org A Visible Cap" in form
    assert "Org B Hidden Cap" not in form


def test_capability_past_the_picker_limit_can_still_be_chosen(world, db_session):
    from app.models.unified_capability import UnifiedCapability

    org, client = world["a"]
    _tenant(org.id)
    db_session.add_all(
        UnifiedCapability(name="Filler %04d" % i, organization_id=org.id, level=1) for i in range(501)
    )
    last = _capability(db_session, org, "Zulu last alphabetically")
    _clear_login_cache()
    resp = client.post("/portfolio/demands/new", data={"title": "Deep pick", "capability_id": last.id})
    assert resp.status_code == 302
    from app.models.demand import Demand

    assert db_session.query(Demand).filter_by(title="Deep pick").one().capability_id == last.id


def test_blank_route_clears_an_earlier_route_and_its_reasons(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    demand = _demand(org, "Redecide")
    demand.triage_route = "buy"
    demand.triage_route_reasons = "earlier reasons"
    db_session.add(demand)
    db_session.flush()
    _clear_login_cache()
    client.post("/portfolio/demands/%d/decide" % demand.id, data={"status": "deferred", "route": ""})
    db_session.refresh(demand)
    assert demand.status == "deferred"
    assert demand.triage_route is None
    assert demand.triage_route_reasons is None


def test_request_with_no_resolved_organisation_is_refused(world, db_session, monkeypatch):
    org, client = world["a"]
    demand = _demand(org, "A demand")
    db_session.add(demand)
    db_session.flush()
    monkeypatch.setattr("app.middleware.tenant_context.current_org_id", lambda: None)
    _clear_login_cache()
    assert client.get("/portfolio/demands").status_code == 403
    _clear_login_cache()
    assert client.get("/portfolio/demands/new").status_code == 403
    _clear_login_cache()
    assert client.post("/portfolio/demands/new", data={"title": "T"}).status_code == 403
    _clear_login_cache()
    resp = client.post("/portfolio/demands/%d/decide" % demand.id, data={"status": "approved"})
    assert resp.status_code == 403
    db_session.refresh(demand)
    assert demand.status == "submitted"


def test_absent_route_field_leaves_the_recorded_route(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Keep")
    _map(db_session, cap, _application(db_session, org, "Keep App"))
    demand = _demand(org, "Defer then approve", cap.id)
    db_session.add(demand)
    db_session.flush()
    _clear_login_cache()
    client.post("/portfolio/demands/%d/decide" % demand.id, data={"status": "deferred", "route": "reuse"})
    db_session.refresh(demand)
    reasons = demand.triage_route_reasons
    assert demand.triage_route == "reuse" and "Keep App" in reasons
    _clear_login_cache()
    client.post("/portfolio/demands/%d/decide" % demand.id, data={"status": "approved"})
    db_session.refresh(demand)
    assert demand.status == "approved"
    assert demand.triage_route == "reuse"
    assert demand.triage_route_reasons == reasons


def test_non_numeric_capability_id_is_refused(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    _clear_login_cache()
    resp = client.post("/portfolio/demands/new", data={"title": "Junk id", "capability_id": "abc"})
    assert resp.status_code == 400
    from app.models.demand import Demand

    assert db_session.query(Demand).filter_by(title="Junk id").count() == 0


def test_queue_shows_capability_name_from_the_recommendation(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Named Capability")
    db_session.add(_demand(org, "Needs a name", cap.id))
    db_session.flush()
    _clear_login_cache()
    page = client.get("/portfolio/demands").get_data(as_text=True)
    assert "Named Capability" in page

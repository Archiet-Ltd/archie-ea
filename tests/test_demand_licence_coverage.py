"""The demand queue lists licences already held for the applications covering a capability."""

import datetime
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
        email=f"licence-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Lic",
        last_name="Ence",
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
    return client


def _tenant(org_id):
    from flask import g

    g.current_org_id = org_id


def _covered_capability(db_session, org, app_name="Onboard Hub", capability="Supplier Onboarding"):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name=capability, organization_id=org.id, level=1)
    app_row = ApplicationComponent(name=app_name, organization_id=org.id)
    db_session.add_all([cap, app_row])
    db_session.flush()
    db_session.add(UnifiedApplicationCapabilityMapping(
        unified_capability_id=cap.id, application_component_id=app_row.id,
        support_level="full", coverage_percentage=90, is_active=True))
    db_session.flush()
    return cap, app_row


def _licence(db_session, org, app_row, entitled=100, used=60, product="Hub Seats"):
    from app.models.application_portfolio import VendorContract
    from app.models.license_entitlement import LicenseEntitlement

    contract = VendorContract(
        contract_name="Hub contract %s" % uuid.uuid4().hex[:6], organization_id=org.id,
        application_id=app_row.id, start_date=datetime.date(2026, 1, 1),
    )
    db_session.add(contract)
    db_session.flush()
    row = LicenseEntitlement(
        contract_id=contract.id, application_id=app_row.id, organization_id=org.id,
        license_type="named_user", quantity_entitled=entitled, quantity_used=used,
        product_name=product,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def world(app, db_session, make_org):
    org_a, org_b = make_org("lic-a"), make_org("lic-b")
    return {
        "a": (org_a, _client_for(app, db_session, org_a)),
        "b": (org_b, _client_for(app, db_session, org_b)),
    }


def test_licence_with_spare_seats_is_listed(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap, app_row = _covered_capability(db_session, org)
    _licence(db_session, org, app_row, entitled=100, used=60)
    from app.modules.portfolio.services.demand_triage import recommend

    rec = recommend(cap.id)
    assert rec["route"] == "reuse"
    assert [(x["product"], x["entitled"], x["used"], x["spare"]) for x in rec["licences"]] == [
        ("Hub Seats", 100, 60, 40)
    ]


def test_unrecorded_usage_is_not_shown_as_spare(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap, app_row = _covered_capability(db_session, org)
    _licence(db_session, org, app_row, entitled=100, used=None)
    from app.modules.portfolio.services.demand_triage import recommend

    lic = recommend(cap.id)["licences"][0]
    assert lic["used"] is None and lic["spare"] is None


def test_no_licence_recorded_gives_an_empty_list(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    cap, _ = _covered_capability(db_session, org)
    from app.modules.portfolio.services.demand_triage import recommend

    assert recommend(cap.id)["licences"] == []


def test_uncovered_capability_has_no_licences(world, db_session):
    org, _ = world["a"]
    _tenant(org.id)
    from app.models.unified_capability import UnifiedCapability
    from app.modules.portfolio.services.demand_triage import recommend

    cap = UnifiedCapability(name="Carbon Reporting", organization_id=org.id, level=1)
    db_session.add(cap)
    db_session.flush()
    rec = recommend(cap.id)
    assert rec["route"] == "buy_or_build" and rec["licences"] == []


def test_other_organisations_licences_are_never_listed(world, db_session):
    org_a, _ = world["a"]
    org_b, _ = world["b"]
    _tenant(org_a.id)
    cap, app_a = _covered_capability(db_session, org_a)
    _licence(db_session, org_a, app_a, product="A seats")
    _tenant(org_b.id)
    _, app_b = _covered_capability(db_session, org_b, app_name="B App", capability="Other")
    _licence(db_session, org_b, app_b, product="B seats")
    _tenant(org_a.id)
    from app.modules.portfolio.services.demand_triage import recommend

    assert [x["product"] for x in recommend(cap.id)["licences"]] == ["A seats"]


def test_queue_page_shows_the_licence(world, db_session):
    org, client = world["a"]
    _tenant(org.id)
    cap, app_row = _covered_capability(db_session, org)
    _licence(db_session, org, app_row, entitled=100, used=60)
    _clear_login_cache()
    client.post("/portfolio/demands/new", data={"title": "Faster onboarding", "capability_id": cap.id})
    _clear_login_cache()
    page = client.get("/portfolio/demands").get_data(as_text=True)
    assert "Licences already held" in page
    assert "Hub Seats" in page and "40 spare" in page

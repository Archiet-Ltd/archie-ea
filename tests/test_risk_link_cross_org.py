"""Signed in as one organisation, a risk cannot be linked to another organisation's record, and the
pickers never show that organisation's names. Two real organisations; every refusal leaves no row."""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

KINDS = ("application", "solution", "programme", "capability", "archimate_element")


def _in_org(app, org, db_session, make):
    with app.test_request_context("/"):
        from flask import g
        g.current_org_id = org.id
        row = make()
        db_session.add(row)
        db_session.flush()
        return row.id


def _foreign_records(app, db_session, org_b):
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_models import Solution
    from app.models.strategic import StrategicInitiative

    return {
        "application": _in_org(app, org_b, db_session, lambda: ApplicationComponent(
            name="Zebra Ledger B", organization_id=org_b.id)),
        "solution": _in_org(app, org_b, db_session, lambda: Solution(
            name="Zebra Solution B", organization_id=org_b.id)),
        "programme": _in_org(app, org_b, db_session, lambda: StrategicInitiative(
            name="Zebra Programme B", organization_id=org_b.id)),
        "capability": _in_org(app, org_b, db_session, lambda: BusinessCapability(
            name="Zebra Capability B", organization_id=org_b.id)),
        "archimate_element": _in_org(app, org_b, db_session, lambda: ArchiMateElement(
            name="Zebra Element B", type="ApplicationComponent", layer="Application",
            organization_id=org_b.id)),
    }


def _signed_in_owner_of_a_risk(app, db_session, org_a, client, login_as):
    from app.models.risk import Risk
    from app.models.user import User

    with app.test_request_context("/"):
        from flask import g
        g.current_org_id = org_a.id
        risk = Risk(organization_id=org_a.id, title="Cross org risk A", likelihood=2, impact=2)
        user = User(email="risk-cross-org@example.com", first_name="Risk", last_name="Tester",
                    organization_id=org_a.id, confirmed=True)
        user.password = "not-used-in-tests-123"
        db_session.add_all([risk, user])
        db_session.flush()
        return risk.id, user


def test_linking_to_another_organisations_record_is_refused_and_writes_nothing(
        app, db_session, make_org, client, login_as):
    from app.models.risk_entity_link import RiskEntityLink

    org_a, org_b = make_org("xlink-a"), make_org("xlink-b")
    foreign = _foreign_records(app, db_session, org_b)
    risk_id, user = _signed_in_owner_of_a_risk(app, db_session, org_a, client, login_as)

    for kind in KINDS:
        login_as(client, user)
        resp = client.post(f"/api/risks/{risk_id}/links",
                           json={"entity_type": kind, "entity_id": foreign[kind]})
        assert resp.status_code == 400, (kind, resp.get_data(as_text=True))
        assert "not found in this organisation" in resp.get_json()["error"], kind
    assert db_session.execute(
        db_session.query(RiskEntityLink).filter_by(risk_id=risk_id).statement
    ).first() is None


def test_the_service_names_the_organisation_in_the_target_query(app, db_session, make_org):
    from app.services import risk_service

    org_a, org_b = make_org("xsvc-a"), make_org("xsvc-b")
    foreign = _foreign_records(app, db_session, org_b)
    for kind in KINDS:
        assert risk_service._load_linkable_entity(kind, foreign[kind], org_a.id) is None, kind
        assert risk_service._load_linkable_entity(kind, foreign[kind], org_b.id) is not None, kind


def test_pickers_never_return_another_organisations_names(
        app, db_session, make_org, client, login_as):
    org_a, org_b = make_org("xpick-a"), make_org("xpick-b")
    _foreign_records(app, db_session, org_b)
    _risk_id, user = _signed_in_owner_of_a_risk(app, db_session, org_a, client, login_as)

    for path in ("/api/programmes/search?q=Zebra", "/api/solutions/search?q=Zebra"):
        login_as(client, user)
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.get_json() == [], path

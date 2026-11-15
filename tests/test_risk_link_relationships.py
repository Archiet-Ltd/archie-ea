"""A risk mapped to something is drawn on the model: an association from the risk's Assessment element
to the target's element. Removing the mapping removes it; the backfill draws the ones made before."""

import pytest
from flask import g

pytestmark = pytest.mark.usefixtures("db_session")


def _seed(app, db_session, org, title="Cash runs out"):
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk import Risk
    from app.models.user import User

    with app.test_request_context("/"):
        g.current_org_id = org.id
        element = ArchiMateElement(name="Billing API", type="ApplicationComponent", layer="Application",
                                   organization_id=org.id)
        db_session.add(element)
        db_session.flush()
        billing = ApplicationComponent(name="Billing API", organization_id=org.id,
                                       archimate_element_id=element.id)
        product = ArchiMateElement(name="Startup plan", type="Product", layer="Business",
                                   organization_id=org.id)
        risk = Risk(organization_id=org.id, title=title, likelihood=3, impact=3)
        user = User(email="risk-rel-%s@example.com" % org.id, first_name="Risk", last_name="Tester",
                    organization_id=org.id, confirmed=True)
        user.password = "not-used-in-tests-123"
        db_session.add_all([billing, product, risk, user])
        db_session.flush()
        return {"risk": risk.id, "app": billing.id, "app_element": element.id,
                "product": product.id, "user": user}


def _associations(db_session, org, source_id):
    from app.models.archimate_core import ArchiMateRelationship

    return db_session.execute(
        db_session.query(ArchiMateRelationship).filter_by(
            organization_id=org.id, source_id=source_id, type="association").statement
    ).scalars().all()


def _mirror(db_session, risk_id):
    from app.models.risk import Risk

    return db_session.get(Risk, risk_id).archimate_element_id


def test_linking_from_the_register_draws_an_association_and_unlinking_removes_it(
        app, db_session, make_org, client, login_as):
    org = make_org("relmap-reg")
    ids = _seed(app, db_session, org)
    login_as(client, ids["user"])
    reply = client.post("/api/risks/%d/links" % ids["risk"],
                        json={"entity_type": "application", "entity_id": ids["app"]})
    assert reply.status_code == 201, reply.get_data(as_text=True)
    mirror = _mirror(db_session, ids["risk"])
    assert mirror is not None
    drawn = _associations(db_session, org, mirror)
    assert [r.target_id for r in drawn] == [ids["app_element"]]

    login_as(client, ids["user"])
    reply = client.delete("/api/risks/%d/links/%d" % (ids["risk"], reply.get_json()["id"]))
    assert reply.status_code in (200, 204), reply.get_data(as_text=True)
    assert _associations(db_session, org, mirror) == []


def test_an_element_link_draws_an_association_to_that_element(app, db_session, make_org, client, login_as):
    org = make_org("relmap-el")
    ids = _seed(app, db_session, org)
    login_as(client, ids["user"])
    reply = client.post("/api/risks/%d/links" % ids["risk"],
                        json={"entity_type": "archimate_element", "entity_id": ids["product"]})
    assert reply.status_code == 201, reply.get_data(as_text=True)
    mirror = _mirror(db_session, ids["risk"])
    assert [r.target_id for r in _associations(db_session, org, mirror)] == [ids["product"]]
    # and the chat's lens finds the risk from the product
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    with app.test_request_context("/"):
        g.current_org_id = org.id
        found = IntelligenceQueryService.risk_for_element(ids["product"])
    assert [r["risk_id"] for r in found["risks"]] == [ids["risk"]]


def test_the_backfill_draws_each_missing_association_once(app, db_session, make_org):
    from app.models.risk_entity_link import RiskEntityLink
    from app.services import risk_service

    org = make_org("relmap-fill")
    ids = _seed(app, db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        db_session.add_all([
            RiskEntityLink(organization_id=org.id, risk_id=ids["risk"], entity_type="application",
                           entity_id=ids["app"]),
            RiskEntityLink(organization_id=org.id, risk_id=ids["risk"], entity_type="archimate_element",
                           entity_id=ids["product"]),
        ])
        db_session.flush()
        first = risk_service.project_all_links(organization_id=org.id)
        second = risk_service.project_all_links(organization_id=org.id)
    mirror = _mirror(db_session, ids["risk"])
    assert first == (2, 2)
    assert second == (2, 0)
    assert sorted(r.target_id for r in _associations(db_session, org, mirror)) == sorted(
        [ids["app_element"], ids["product"]])


def test_unlinking_keeps_the_association_while_another_link_reaches_the_same_element(
        app, db_session, make_org):
    from app.models.risk_entity_link import RiskEntityLink
    from app.services import risk_service

    org = make_org("relmap-two")
    ids = _seed(app, db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        by_record = risk_service.add_risk_link(ids["risk"], "application", ids["app"], organization_id=org.id)
        risk_service.add_risk_link(ids["risk"], "archimate_element", ids["app_element"], organization_id=org.id)
        mirror = _mirror(db_session, ids["risk"])
        assert len(_associations(db_session, org, mirror)) == 1
        risk_service.remove_risk_link(ids["risk"], by_record.id)
        assert len(_associations(db_session, org, mirror)) == 1
        remaining = RiskEntityLink.query.filter_by(risk_id=ids["risk"]).one()
        risk_service.remove_risk_link(ids["risk"], remaining.id)
        assert _associations(db_session, org, mirror) == []


def test_unmapping_keeps_an_association_a_person_drew(app, db_session, make_org):
    from app.models.archimate_core import ArchiMateRelationship
    from app.services import risk_service

    org = make_org("relmap-hand")
    ids = _seed(app, db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        from app.services.archimate_backbone import ensure_archimate_element
        from app.models.risk import Risk
        mirror = ensure_archimate_element(db_session.get(Risk, ids["risk"])).id
        drawn = ArchiMateRelationship(type="association", source_id=mirror, target_id=ids["app_element"],
                                      organization_id=org.id, source_kind="manual")
        db_session.add(drawn)
        db_session.flush()
        link = risk_service.add_risk_link(ids["risk"], "application", ids["app"], organization_id=org.id)
        risk_service.remove_risk_link(ids["risk"], link.id)
        assert [r.id for r in _associations(db_session, org, mirror)] == [drawn.id]


def test_a_projected_association_is_marked_derived_and_removed_with_its_link(app, db_session, make_org):
    from app.services import risk_service

    org = make_org("relmap-derived")
    ids = _seed(app, db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        link = risk_service.add_risk_link(ids["risk"], "application", ids["app"], organization_id=org.id)
        mirror = _mirror(db_session, ids["risk"])
        assert [r.source_kind for r in _associations(db_session, org, mirror)] == ["derived"]
        risk_service.remove_risk_link(ids["risk"], link.id)
        assert _associations(db_session, org, mirror) == []


def test_the_dry_run_counts_what_the_real_run_draws_when_two_links_reach_one_element(
        app, db_session, make_org):
    from app.models.risk_entity_link import RiskEntityLink
    from app.services import risk_service

    org = make_org("relmap-dry")
    ids = _seed(app, db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        db_session.add_all([
            RiskEntityLink(organization_id=org.id, risk_id=ids["risk"], entity_type="application",
                           entity_id=ids["app"]),
            RiskEntityLink(organization_id=org.id, risk_id=ids["risk"], entity_type="archimate_element",
                           entity_id=ids["app_element"]),
            RiskEntityLink(organization_id=org.id, risk_id=ids["risk"], entity_type="archimate_element",
                           entity_id=ids["product"]),
        ])
        db_session.flush()
        dry = risk_service.project_all_links(organization_id=org.id, dry_run=True)
        real = risk_service.project_all_links(organization_id=org.id)
    assert dry == real == (3, 2)

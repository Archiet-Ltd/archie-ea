"""PA-0032: the risk register shows what a risk touches from the element field.

A risk linked only through ``Risk.archimate_element_id`` (as the demo seed does)
used to read "Not mapped" because the register looked at ``entity_links`` alone.
"""
from app.models.archimate_core import ArchiMateElement
from app.models.risk import Risk
from app.modules.architecture.routes.risk_routes import serialize_risk_row


def _element(db_session, org, name, type_="ApplicationComponent", layer="Application"):
    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


def _risk(db_session, org, title, element=None):
    risk = Risk(organization_id=org.id, title=title, likelihood=3, impact=4)
    db_session.add(risk)
    db_session.flush()
    if element is not None:
        risk.archimate_element_id = element.id
        db_session.flush()
    return risk


def test_risk_linked_only_through_element_field_shows_that_element(
        app, db_session, make_org, tenant_ctx):
    org = make_org("pa32-elem")
    with tenant_ctx(org.id):
        relay = _element(db_session, org, "Event Relay")
        risk = _risk(db_session, org, "Event Relay single point of failure", relay)
        links = serialize_risk_row(risk)["entity_links"]
        assert [(lk["entity_type"], lk["entity_label"]) for lk in links] == [
            ("ApplicationComponent", "Event Relay")
        ]


def test_risk_linked_through_both_shows_the_element_once(
        app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk_entity_link import RiskEntityLink

    org = make_org("pa32-both")
    with tenant_ctx(org.id):
        relay = _element(db_session, org, "Event Relay")
        component = ApplicationComponent(
            name="Event Relay", organization_id=org.id, archimate_element_id=relay.id)
        db_session.add(component)
        db_session.flush()
        risk = _risk(db_session, org, "Relay outage", relay)
        db_session.add(RiskEntityLink(
            risk_id=risk.id, entity_type="application", entity_id=component.id,
            organization_id=org.id))
        db_session.flush()
        links = serialize_risk_row(risk)["entity_links"]
        assert len(links) == 1
        assert links[0]["entity_label"] == "Event Relay"


def test_risk_with_no_link_has_no_entries_so_page_says_not_mapped(
        app, db_session, make_org, tenant_ctx):
    org = make_org("pa32-none")
    with tenant_ctx(org.id):
        risk = _risk(db_session, org, "Unlinked risk")
        # The risk's own ArchiMate mirror is not something it touches.
        assert risk.archimate_element_id is not None
        assert serialize_risk_row(risk)["entity_links"] == []


def test_other_organisations_element_never_appears(
        app, db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("pa32-a"), make_org("pa32-b")
    with tenant_ctx(org_b.id):
        foreign = _element(db_session, org_b, "Org B Secret System")
        foreign_id = foreign.id
    with tenant_ctx(org_a.id):
        risk = Risk(organization_id=org_a.id, title="A risk", likelihood=1, impact=1)
        db_session.add(risk)
        db_session.flush()
        risk.archimate_element_id = foreign_id
        db_session.flush()
        links = serialize_risk_row(risk)["entity_links"]
        assert links == []
        assert "Org B Secret System" not in repr(links)

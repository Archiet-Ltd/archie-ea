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
            ("application", "Event Relay")
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


def test_renamed_risk_is_still_not_mapped(app, db_session, make_org, tenant_ctx):
    """The risk's own backbone mirror is recognised by how it was made, not by
    its name, so renaming the risk must not make it appear to touch itself."""
    org = make_org("pa32-rename")
    with tenant_ctx(org.id):
        risk = _risk(db_session, org, "Original title")
        assert risk.archimate_element_id is not None
        risk.title = "Completely different title"
        db_session.flush()
        assert serialize_risk_row(risk)["entity_links"] == []


def test_genuine_assessment_named_like_the_risk_is_shown(
        app, db_session, make_org, tenant_ctx):
    org = make_org("pa32-assess")
    with tenant_ctx(org.id):
        genuine = _element(db_session, org, "Vendor lock-in", "Assessment", "Motivation")
        risk = _risk(db_session, org, "Vendor lock-in", genuine)
        links = serialize_risk_row(risk)["entity_links"]
        assert [lk["entity_label"] for lk in links] == ["Vendor lock-in"]


def test_register_rows_use_a_fixed_number_of_queries(
        app, db_session, make_org, tenant_ctx):
    from sqlalchemy import event

    from app.modules.architecture.routes.risk_routes import _prefetch_risk_context

    org = make_org("pa32-count")

    def count_for(n):
        with tenant_ctx(org.id):
            risks = [
                _risk(db_session, org, f"Risk {n}-{i}", _element(db_session, org, f"El {n}-{i}"))
                for i in range(n)
            ]
            seen = []
            engine = db_session.get_bind()

            def _count(conn, cursor, statement, *a):
                seen.append(statement)

            event.listen(engine, "before_cursor_execute", _count)
            try:
                ctx = _prefetch_risk_context(risks)
                rows = [serialize_risk_row(r, ctx) for r in risks]
            finally:
                event.remove(engine, "before_cursor_execute", _count)
            assert all(len(r["entity_links"]) == 1 for r in rows)
            return len(seen)

    assert count_for(2) == count_for(8)


def test_demo_event_relay_risk_lists_its_element(app, db_session):
    import os

    from app.commands.seed_demo_company import seed_demo_company
    from app.models.organization import Organization

    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    org_id = db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    from flask import g

    with app.test_request_context("/"):
        g.current_org_id = org_id
        risk = Risk.query.filter_by(
            organization_id=org_id, title="Event Relay single point of failure").one()
        links = serialize_risk_row(risk)["entity_links"]
        assert [lk["entity_label"] for lk in links] == ["Event Relay"]

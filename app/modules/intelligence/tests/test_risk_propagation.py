"""R1-B30 (TB-0038): ``IntelligenceQueryService.risk_propagation``.

An indirect risk recorded on a supplier shows on the business service that
depends on it, with the path and the controls on that path. It is built on the
one canonical walk (upstream) and the one risk register, read-only.

Fixtures (app, db_session, make_org) come from app/modules/conftest.py.
"""

from __future__ import annotations

import uuid

from app.modules.intelligence.services.query_service import IntelligenceQueryService


def _element(db_session, org_id, name, type_="BusinessService", layer="business"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _serves(db_session, org_id, supplier, service):
    """supplier --Serving--> service: the service depends on the supplier."""
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=supplier.id, target_id=service.id, type="Serving", organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _risk(db_session, org_id, element=None, *, title="Supplier outage", likelihood=4, impact=5, status=None):
    from app.models.risk import Risk, RiskStatus

    row = Risk(
        organization_id=org_id,
        archimate_element_id=element.id if element is not None else None,
        title=title,
        likelihood=likelihood,
        impact=impact,
        status=status or RiskStatus.OPEN,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _component(db_session, org_id, element):
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(name=element.name, organization_id=org_id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    return component


def _control(db_session, code="AC-2"):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    framework = RegulatoryFramework(code=f"FW{uuid.uuid4().hex[:6]}", name="Framework", category="security")
    db_session.add(framework)
    db_session.flush()
    control = ComplianceControl(framework_id=framework.id, control_code=code, title="Supplier assurance")
    db_session.add(control)
    db_session.flush()
    return control


def _map_control(db_session, org_id, component, control, status="implemented", **fields):
    from app.models.application_compliance import ApplicationComplianceControl

    row = ApplicationComplianceControl(
        organization_id=org_id, application_id=component.id, control_id=control.id,
        implementation_status=status, **fields,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _run(app, org_id, element_id, **kwargs):
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.risk_propagation(element_id, **kwargs)


def test_a_supplier_risk_appears_on_the_dependent_service_with_its_path(app, db_session, make_org):
    org = make_org("riskprop-indirect")
    service = _element(db_session, org.id, "Order handling")
    middle = _element(db_session, org.id, "Order platform", type_="ApplicationComponent", layer="application")
    supplier = _element(db_session, org.id, "Cloud supplier", type_="BusinessActor")
    first = _serves(db_session, org.id, middle, service)
    second = _serves(db_session, org.id, supplier, middle)
    risk = _risk(db_session, org.id, supplier, title="Supplier insolvency")
    db_session.commit()

    result = _run(app, org.id, service.id)

    assert [r["risk_id"] for r in result["rows"]] == [risk.id]
    row = result["rows"][0]
    assert row["indirect"] is True
    assert row["seeded_on_element_id"] == supplier.id
    assert row["depth"] == 2
    # The path runs from the service back along what it depends on to the supplier.
    assert row["path_elements"] == [service.id, middle.id, supplier.id]
    assert row["path_relationships"] == [first.id, second.id]
    assert row["path_kind"] == "explicit"
    assert result["summary"]["indirect_count"] == 1
    assert {service.id, middle.id, supplier.id} <= {int(k) for k in result["elements"]}


def test_a_risk_on_the_service_itself_is_direct(app, db_session, make_org):
    org = make_org("riskprop-direct")
    service = _element(db_session, org.id, "Payments")
    risk = _risk(db_session, org.id, service)
    db_session.commit()

    result = _run(app, org.id, service.id)

    assert [r["risk_id"] for r in result["rows"]] == [risk.id]
    assert result["rows"][0]["indirect"] is False
    assert result["rows"][0]["depth"] == 0
    assert result["summary"]["direct_count"] == 1


def test_controls_on_the_path_are_listed_with_their_status_and_evidence(app, db_session, make_org):
    org = make_org("riskprop-controls")
    service = _element(db_session, org.id, "Billing")
    supplier = _element(db_session, org.id, "Payment gateway", type_="ApplicationComponent", layer="application")
    _serves(db_session, org.id, supplier, service)
    _risk(db_session, org.id, supplier)
    component = _component(db_session, org.id, supplier)
    implemented = _control(db_session, "SUP-1")
    planned = _control(db_session, "SUP-2")
    _map_control(db_session, org.id, component, implemented, "implemented", evidence_url="https://evidence.example/1")
    _map_control(db_session, org.id, component, planned, "planned")
    db_session.commit()

    row = _run(app, org.id, service.id)["rows"][0]

    by_code = {c["code"]: c for c in row["controls"]}
    assert set(by_code) == {"SUP-1", "SUP-2"}
    assert by_code["SUP-1"]["implemented"] is True
    assert by_code["SUP-1"]["evidence_url_recorded"] is True
    assert by_code["SUP-2"]["implemented"] is False
    assert by_code["SUP-2"]["evidence_url_recorded"] is False
    assert by_code["SUP-1"]["applies_to_element_id"] == supplier.id
    assert row["implemented_control_count"] == 1
    assert "evidence.example" not in repr(row), "the evidence link itself is never returned"


def test_a_row_with_no_controls_says_so_with_an_empty_list(app, db_session, make_org):
    org = make_org("riskprop-no-controls")
    service = _element(db_session, org.id, "Billing")
    _risk(db_session, org.id, service)
    db_session.commit()

    assert _run(app, org.id, service.id)["rows"][0]["controls"] == []


def test_a_risk_linked_to_an_application_reaches_the_service_through_its_element(app, db_session, make_org):
    from app.models.risk_entity_link import RiskEntityLink

    from app.models.application_portfolio import ApplicationComponent

    org = make_org("riskprop-link")
    service = _element(db_session, org.id, "Claims")
    # Create the component first, then elements until the supplier element's id
    # differs from the component's id, so the two id spaces cannot coincide.
    component = ApplicationComponent(name="Claims engine", organization_id=org.id)
    db_session.add(component)
    db_session.flush()
    supplier = _element(db_session, org.id, "Claims engine", type_="ApplicationComponent", layer="application")
    while supplier.id == component.id:
        supplier = _element(db_session, org.id, "Claims engine", type_="ApplicationComponent", layer="application")
    component.archimate_element_id = supplier.id
    db_session.flush()
    assert supplier.id != component.id
    _serves(db_session, org.id, supplier, service)
    linked = _risk(db_session, org.id, None, title="Vendor lock-in")
    # A decoy risk linked by the ELEMENT's id number: link entity ids are
    # ApplicationComponent ids, a different id space, so this must not match.
    decoy = _risk(db_session, org.id, None, title="Decoy")
    db_session.add(RiskEntityLink(organization_id=org.id, risk_id=linked.id, entity_type="application",
                                  entity_id=component.id))
    db_session.add(RiskEntityLink(organization_id=org.id, risk_id=decoy.id, entity_type="application",
                                  entity_id=supplier.id))
    db_session.commit()

    result = _run(app, org.id, service.id)

    assert [r["risk_id"] for r in result["rows"]] == [linked.id]
    assert result["rows"][0]["seeded_on"] == "application_link"
    assert result["rows"][0]["seeded_on_element_id"] == supplier.id


def test_another_organisations_risk_never_appears_even_when_it_points_at_the_callers_element(
    app, db_session, make_org
):
    org_a, org_b = make_org("riskprop-iso-a"), make_org("riskprop-iso-b")
    service = _element(db_session, org_a.id, "Service A")
    supplier = _element(db_session, org_a.id, "Supplier A", type_="BusinessActor")
    _serves(db_session, org_a.id, supplier, service)
    own = _risk(db_session, org_a.id, supplier, title="Own supplier risk")
    # The foreign row exists and carries the CALLER's element id.
    foreign = _risk(db_session, org_b.id, supplier, title="Foreign risk on caller element")
    foreign_element = _element(db_session, org_b.id, "Foreign supplier", type_="BusinessActor")
    _risk(db_session, org_b.id, foreign_element, title="Foreign supplier risk")
    db_session.commit()

    result = _run(app, org_a.id, service.id)

    assert [r["risk_id"] for r in result["rows"]] == [own.id]
    assert foreign.id not in [r["risk_id"] for r in result["rows"]]
    assert "Foreign" not in repr(result)


def test_propagation_never_follows_an_edge_into_another_organisation(app, db_session, make_org):
    from app.models import ArchiMateRelationship

    org_a, org_b = make_org("riskprop-edge-a"), make_org("riskprop-edge-b")
    service = _element(db_session, org_a.id, "Service A")
    foreign_supplier = _element(db_session, org_b.id, "Foreign supplier", type_="BusinessActor")
    _risk(db_session, org_b.id, foreign_supplier, title="Foreign risk")
    # An edge written by the other organisation that targets the caller's service.
    db_session.add(ArchiMateRelationship(
        source_id=foreign_supplier.id, target_id=service.id, type="Serving", organization_id=org_b.id,
    ))
    db_session.commit()

    result = _run(app, org_a.id, service.id)

    assert result["rows"] == []
    assert result["reasons"] == ["no_risk_recorded"]


def test_a_closed_risk_does_not_propagate(app, db_session, make_org):
    from app.models.risk import RiskStatus

    org = make_org("riskprop-closed")
    service = _element(db_session, org.id, "Service")
    supplier = _element(db_session, org.id, "Supplier", type_="BusinessActor")
    _serves(db_session, org.id, supplier, service)
    _risk(db_session, org.id, supplier, status=RiskStatus.CLOSED)
    db_session.commit()

    result = _run(app, org.id, service.id)

    assert result["rows"] == []
    assert result["reasons"] == ["no_risk_recorded"]


def test_a_supplier_beyond_the_depth_bound_is_not_reached(app, db_session, make_org):
    org = make_org("riskprop-depth")
    chain = [_element(db_session, org.id, f"N{i}") for i in range(5)]
    for source, target in zip(chain[1:], chain[:-1]):
        _serves(db_session, org.id, source, target)
    _risk(db_session, org.id, chain[4])
    db_session.commit()

    assert _run(app, org.id, chain[0].id, max_depth=3)["rows"] == []
    assert len(_run(app, org.id, chain[0].id, max_depth=4)["rows"]) == 1


def test_risks_are_ranked_by_score_then_distance(app, db_session, make_org):
    org = make_org("riskprop-rank")
    service = _element(db_session, org.id, "Service")
    near = _element(db_session, org.id, "Near", type_="BusinessActor")
    far = _element(db_session, org.id, "Far", type_="BusinessActor")
    _serves(db_session, org.id, near, service)
    _serves(db_session, org.id, far, near)
    low_near = _risk(db_session, org.id, near, title="low near", likelihood=1, impact=1)
    high_far = _risk(db_session, org.id, far, title="high far", likelihood=5, impact=5)
    db_session.commit()

    result = _run(app, org.id, service.id)

    assert [r["risk_id"] for r in result["rows"]] == [high_far.id, low_near.id]
    assert result["summary"]["highest_risk_level"] == "critical"


def test_no_risk_anywhere_is_stated_not_zeroed(app, db_session, make_org):
    org = make_org("riskprop-none")
    service = _element(db_session, org.id, "Service")
    db_session.commit()

    result = _run(app, org.id, service.id)

    assert result["rows"] == []
    assert result["reasons"] == ["no_risk_recorded"]
    assert result["summary"]["risk_count"] == 0


def test_unknown_and_foreign_elements_answer_the_same_way(app, db_session, make_org):
    org_a, org_b = make_org("riskprop-nf-a"), make_org("riskprop-nf-b")
    foreign = _element(db_session, org_b.id, "Foreign")
    _risk(db_session, org_b.id, foreign)
    db_session.commit()

    assert _run(app, org_a.id, foreign.id)["reasons"] == ["element_not_found"]
    assert _run(app, org_a.id, 999_999_999)["reasons"] == ["element_not_found"]
    assert _run(app, org_a.id, foreign.id)["rows"] == []


def test_with_no_organisation_nothing_is_read(app, db_session, make_org):
    org = make_org("riskprop-no-org")
    service = _element(db_session, org.id, "Service")
    _risk(db_session, org.id, service)
    db_session.commit()

    result = _run(app, None, service.id)

    assert result["rows"] == []
    assert result["reasons"] == ["no_tenant_context"]

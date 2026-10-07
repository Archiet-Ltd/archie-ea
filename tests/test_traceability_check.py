"""Traceability check: a component's chains up to a capability and down to
technology, read from the canonical impact walk, with candidate fixes taken
only from what the organisation already recorded in a parallel table.

Every test seeds two real organisations and proves organisation B never sees,
counts or is offered anything from organisation A.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _element(db_session, org, name, element_type, layer):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(name=name, type=element_type, layer=layer, organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    return element


def _relate(db_session, source, target, rel_type):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=rel_type,
                                organization_id=source.organization_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _broken_estate(db_session, org, label):
    """Component -> serving -> process (stops); node -> serving -> component."""
    component = _element(db_session, org, "Order hub %s" % label, "ApplicationComponent", "application")
    process = _element(db_session, org, "Take orders %s" % label, "BusinessProcess", "business")
    node = _element(db_session, org, "Cluster %s" % label, "Node", "technology")
    other = _element(db_session, org, "Ledger %s" % label, "ApplicationComponent", "application")
    _relate(db_session, component, process, "serving")
    _relate(db_session, node, component, "serving")
    # A flow is not a trace: the ledger must not appear in any chain.
    _relate(db_session, component, other, "flow")
    db_session.commit()
    return component, process, node, other


def test_up_chain_stops_at_the_process_and_down_chain_reaches_technology(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.traceability_check_service import TraceabilityCheckService

    org_a = make_org("trace-a")
    with tenant_ctx(org_a.id):
        component, process, node, other = _broken_estate(db_session, org_a, "A")
        result = TraceabilityCheckService.check(component.id, org_a.id)

    assert result["state"] == "checked"
    assert result["up"]["complete"] is False
    stops = [c["stops_at"]["id"] for c in result["up"]["chains"] if not c["complete"]]
    assert stops == [process.id]
    assert result["down"]["complete"] is True
    down_ids = [[e["id"] for e in c["elements"]] for c in result["down"]["chains"] if c["complete"]]
    assert down_ids == [[component.id, node.id]]
    every_id = {e["id"] for block in ("up", "down") for c in result[block]["chains"] for e in c["elements"]}
    assert other.id not in every_id


def test_an_element_with_no_relationships_stops_at_itself(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.traceability_check_service import TraceabilityCheckService

    org = make_org("trace-lonely")
    with tenant_ctx(org.id):
        lonely = _element(db_session, org, "Lonely app", "ApplicationComponent", "application")
        db_session.commit()
        result = TraceabilityCheckService.check(lonely.id, org.id)

    for block in ("up", "down"):
        assert result[block]["complete"] is False
        assert result[block]["chains"] == [
            {"elements": [result["element"]], "complete": False, "stops_at": result["element"]}
        ]
    assert result["candidates"] == []


def test_capability_mapping_held_in_a_parallel_table_is_offered_then_closes(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability
    from app.modules.intelligence.services.traceability_check_service import TraceabilityCheckService

    org_a = make_org("trace-cand-a")
    org_b = make_org("trace-cand-b")
    with tenant_ctx(org_a.id):
        app_row = ApplicationComponent(name="Billing engine", organization_id=org_a.id,
                                       lifecycle_status="operational")
        db_session.add(app_row)
        db_session.commit()
        component_id = app_row.archimate_element_id
        assert component_id is not None
        capability = _element(db_session, org_a, "Bill customers", "Capability", "strategy")
        db_session.commit()
        unified = UnifiedCapability.query.filter_by(archimate_element_id=capability.id).first()
        if unified is None:
            unified = UnifiedCapability(name="Bill customers", organization_id=org_a.id,
                                        archimate_element_id=capability.id, level=1)
            db_session.add(unified)
            db_session.flush()
        db_session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=unified.id, application_component_id=app_row.id))
        db_session.commit()

        result = TraceabilityCheckService.check(component_id, org_a.id)
        assert result["up"]["complete"] is False
        ups = [c for c in result["candidates"] if c["direction"] == "up"]
        assert len(ups) == 1
        assert ups[0]["source"]["id"] == component_id
        assert ups[0]["target"]["id"] == capability.id
        assert ups[0]["relationship_type"] == "serving"
        assert ups[0]["held_in"] == "capability mapping"

    with tenant_ctx(org_b.id):
        # B can neither check A's component nor be offered A's capability.
        assert TraceabilityCheckService.check(component_id, org_b.id) == {"state": "not_found"}

    with tenant_ctx(org_a.id):
        db_session.add(ArchiMateRelationship(source_id=component_id, target_id=capability.id,
                                             type="serving", organization_id=org_a.id))
        db_session.commit()
        after = TraceabilityCheckService.check(component_id, org_a.id)
        assert after["up"]["complete"] is True
        assert [c for c in after["candidates"] if c["direction"] == "up"] == []


def test_technology_linked_by_column_is_offered_downward(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.technology_layer import SystemSoftware
    from app.modules.intelligence.services.traceability_check_service import TraceabilityCheckService

    org = make_org("trace-tech")
    with tenant_ctx(org.id):
        app_row = ApplicationComponent(name="Claims portal", organization_id=org.id,
                                       lifecycle_status="operational")
        db_session.add(app_row)
        db_session.commit()
        software_element = _element(db_session, org, "PostgreSQL 16", "SystemSoftware", "technology")
        software = SystemSoftware(name="PostgreSQL 16", organization_id=org.id,
                                  application_component_id=app_row.id,
                                  archimate_element_id=software_element.id)
        db_session.add(software)
        db_session.commit()

        result = TraceabilityCheckService.check(app_row.archimate_element_id, org.id)

    downs = [c for c in result["candidates"] if c["direction"] == "down"]
    assert result["down"]["complete"] is False
    assert [(c["source"]["id"], c["target"]["id"]) for c in downs] == [
        (software_element.id, app_row.archimate_element_id)
    ]
    assert downs[0]["held_in"] == "technology mapping"
    assert downs[0]["relationship_type"] == "serving"


def test_api_answers_for_own_organisation_and_404s_for_another(app, db_session, make_org, tenant_ctx, login_as):
    from app.models.user import User

    org_a = make_org("trace-api-a")
    org_b = make_org("trace-api-b")
    with tenant_ctx(org_a.id):
        component, process, node, other = _broken_estate(db_session, org_a, "api")
    with tenant_ctx(org_b.id):
        # B has a model of its own, so its page shows the check, not the
        # "nothing is modelled yet" state.
        _element(db_session, org_b, "B's own app", "ApplicationComponent", "application")
        db_session.commit()
    user_a = User(email="trace-a-%s@example.com" % org_a.id, organization_id=org_a.id,
                  enterprise_role="solution_architect", confirmed=True)
    user_b = User(email="trace-b-%s@example.com" % org_b.id, organization_id=org_b.id,
                  enterprise_role="solution_architect", confirmed=True)
    db_session.add_all([user_a, user_b])
    db_session.commit()

    client = app.test_client()
    login_as(client, user_a)
    resp = client.get("/api/v1/intelligence/traceability/%d" % component.id)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    data = body.get("data", body)
    assert data["element"]["id"] == component.id
    assert data["up"]["complete"] is False

    login_as(client, user_a)
    page = client.get("/intelligence/traceability?element=%d" % component.id)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Stops at Take orders api" in html
    assert 'data-testid="trace-up" data-complete="false"' in html
    assert 'data-testid="trace-down" data-complete="true"' in html

    login_as(client, user_b)
    denied = client.get("/api/v1/intelligence/traceability/%d" % component.id)
    assert denied.status_code == 404
    login_as(client, user_b)
    page_b = client.get("/intelligence/traceability?element=%d" % component.id)
    assert page_b.status_code == 200
    html_b = page_b.get_data(as_text=True)
    assert "Take orders api" not in html_b
    assert "That element was not found" in html_b

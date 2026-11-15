"""Two organisations: a risk cannot be mapped to the other organisation's element, no association is
written, and the chat's lens never finds the other organisation's risk."""

import pytest
from flask import g

pytestmark = pytest.mark.usefixtures("db_session")


def test_a_mapping_to_another_organisations_element_is_refused_and_draws_nothing(
        app, db_session, make_org, client, login_as):
    from app.models import ArchiMateElement
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.user import User
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a, org_b = make_org("relx-a"), make_org("relx-b")
    with app.test_request_context("/"):
        g.current_org_id = org_b.id
        foreign = ArchiMateElement(name="Startup plan", type="Product", layer="Business",
                                   organization_id=org_b.id)
        risk_b = Risk(organization_id=org_b.id, title="Price too low", likelihood=2, impact=2)
        db_session.add_all([foreign, risk_b])
        db_session.flush()
        db_session.add(RiskEntityLink(organization_id=org_b.id, risk_id=risk_b.id,
                                      entity_type="archimate_element", entity_id=foreign.id))
        db_session.flush()
        foreign_id, risk_b_id = foreign.id, risk_b.id
    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        risk_a = Risk(organization_id=org_a.id, title="Cash runs out", likelihood=2, impact=2)
        user = User(email="relx-a@example.com", first_name="A", last_name="Tester",
                    organization_id=org_a.id, confirmed=True)
        user.password = "not-used-in-tests-123"
        db_session.add_all([risk_a, user])
        db_session.flush()
        risk_a_id = risk_a.id

    login_as(client, user)
    reply = client.post("/api/risks/%d/links" % risk_a_id,
                        json={"entity_type": "archimate_element", "entity_id": foreign_id})
    assert reply.status_code == 400
    assert "archimate_element %d was not found in this organisation" % foreign_id in reply.get_json()["error"]
    assert RiskEntityLink.query.filter_by(risk_id=risk_a_id).count() == 0
    assert db_session.query(ArchiMateRelationship).filter_by(target_id=foreign_id).count() == 0

    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        found = IntelligenceQueryService.risk_for_element(foreign_id)
    assert risk_b_id not in [r["risk_id"] for r in found["risks"]]

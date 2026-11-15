"""A risk linked directly to an element lists that element under what it touches, so the chat card
never reads "no linked records yet" for a risk that the same answer lists against an element."""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def test_element_link_is_a_touch(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.services.risk_service import ranked_risks

    org = make_org("touch-a")
    element = ArchiMateElement(name="Payments gateway", type="ApplicationComponent",
                               layer="application", organization_id=org.id)
    risk = Risk(title="Gateway outage", likelihood=4, impact=4, organization_id=org.id)
    db_session.add_all([element, risk])
    db_session.flush()
    db_session.add(RiskEntityLink(risk_id=risk.id, entity_type="archimate_element",
                                  entity_id=element.id, organization_id=org.id))
    db_session.flush()
    with tenant_ctx(org.id):
        rows = ranked_risks()["rows"]
    row = next(r for r in rows if r["risk_id"] == risk.id)
    assert [t["name"] for t in row["touches"]] == ["Payments gateway"]

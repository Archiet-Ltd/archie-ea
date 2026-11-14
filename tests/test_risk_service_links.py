"""Risks and what they touch: the one writer takes the organisation and refuses a foreign target."""

import pytest
from flask import g

from app.models.application_portfolio import ApplicationComponent
from app.models.risk import Risk
from app.models.risk_entity_link import RiskEntityLink
from app.services import risk_service


def _application(db_session, org, name):
    row = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def test_create_risk_writes_the_risk_and_its_links_together(app, db_session, make_org):
    org = make_org("risk-links-own")
    billing = _application(db_session, org, "Billing API")
    with app.test_request_context("/"):
        g.current_org_id = org.id
        risk = risk_service.create_risk(None, "Lock-in", None, 4, 4, "Ops", None,
                                        links=[("application", billing.id)], organization_id=org.id)
        links = RiskEntityLink.query.filter_by(risk_id=risk.id).all()
    assert [(link.entity_type, link.entity_id, link.organization_id) for link in links] == [("application", billing.id, org.id)]
    assert risk.organization_id == org.id


def test_a_link_target_from_another_organisation_writes_no_risk(app, db_session, make_org):
    org_a, org_b = make_org("risk-links-a"), make_org("risk-links-b")
    foreign = _application(db_session, org_b, "Billing API")
    title = "Should not exist"
    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        with pytest.raises(ValueError):
            risk_service.create_risk(None, title, None, 4, 4, None, None,
                                     links=[("application", foreign.id)], organization_id=org_a.id)
        assert Risk.query.filter_by(title=title).count() == 0
        assert RiskEntityLink.query.filter_by(entity_id=foreign.id).count() == 0


def test_get_risk_is_fenced_by_the_organisation_given(app, db_session, make_org):
    org_a, org_b = make_org("risk-get-a"), make_org("risk-get-b")
    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        risk = risk_service.create_risk(None, "Mine", None, 1, 1, None, None, organization_id=org_a.id)
        assert risk_service.get_risk(org_a.id, risk.id) is risk
        assert risk_service.get_risk(org_b.id, risk.id) is None

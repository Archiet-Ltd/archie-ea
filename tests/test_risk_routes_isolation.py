"""Another organisation's person cannot read or change a risk, its links or its scores.

The routes and services look a risk up by id; these tests prove the tenant filter answers 404 for each
of them, using two real organisations and checking nothing changed."""

import pytest
from flask import g

pytestmark = pytest.mark.usefixtures("db_session")


def _person(db_session, org, tag):
    from app.models.user import User

    user = User(email="risk-iso-%s-%s@example.com" % (tag, org.id), first_name="Iso", last_name="Tester",
                organization_id=org.id, confirmed=True)
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    db_session.flush()
    return user


def _risk(app, db_session, org):
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    with app.test_request_context("/"):
        g.current_org_id = org.id
        risk = Risk(organization_id=org.id, title="Org A only", likelihood=2, impact=2)
        db_session.add(risk)
        db_session.flush()
        link = RiskEntityLink(organization_id=org.id, risk_id=risk.id, entity_type="archimate_element",
                              entity_id=1)
        db_session.add(link)
        db_session.flush()
        return risk.id


def test_org_b_cannot_reach_org_a_risk_through_any_route_or_service(app, db_session, make_org, client, login_as):
    from werkzeug.exceptions import NotFound

    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.services import risk_service

    org_a, org_b = make_org("iso-a"), make_org("iso-b")
    risk_id = _risk(app, db_session, org_a)
    person_a = _person(db_session, org_a, "a")
    person_b = _person(db_session, org_b, "b")

    login_as(client, person_b)
    base = "/api/risks/%d" % risk_id
    replies = {
        "status": client.patch(base, json={"status": "closed"}),
        "update": client.patch(base, json={"title": "Taken over", "likelihood": 5}),
        "delete": client.delete(base),
        "get": client.get(base),
        "links": client.get(base + "/links"),
        "scores": client.get(base + "/scores"),
    }
    assert {k: r.status_code for k, r in replies.items()} == {k: 404 for k in replies}

    # the services answer the same when called with org B's context
    with app.test_request_context("/"):
        g.current_org_id = org_b.id
        for call in (lambda: risk_service.update_risk_status(risk_id, "closed"),
                     lambda: risk_service.update_risk(risk_id, title="Taken over"),
                     lambda: risk_service.delete_risk(risk_id)):
            with pytest.raises(NotFound):
                call()

    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        db_session.expire_all()
        risk = db_session.get(Risk, risk_id)
        assert risk is not None and risk.title == "Org A only" and risk.status.value != "closed"
        assert db_session.query(RiskEntityLink).filter_by(risk_id=risk_id).count() == 1

    # org A's own person is allowed the same calls, so the 404s above are the fence and not a role refusal
    login_as(client, person_a)
    assert client.get(base).status_code == 200
    assert client.patch(base, json={"title": "Renamed"}).status_code == 200

"""The consolidation opportunities screen tells a failed savings forecast from an empty one.

The forecast service answers ``opportunities_included: None`` when it could not calculate. Reading that
as "no opportunity has been included" tells the reader the savings are nil when they are unknown.
"""
import uuid

FAILED = {"forecast_by_year": {}, "opportunities_included": None}
NOTHING_INCLUDED = {"forecast_by_year": {}, "opportunities_included": 0}


def _page(db_session, make_org, client, login_as, monkeypatch, forecast):
    from app.models.user import Role, User
    from app.routes import unified_low_priority_routes as routes

    org = make_org("forecast")
    Role.insert_roles()
    user = User(
        email="forecast.%s@example.com" % uuid.uuid4().hex[:8], first_name="F", last_name="C",
        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        role=Role.query.filter_by(name="Viewer").first(),
    )
    user.password = "Passw0rd!x"
    db_session.add(user)
    db_session.flush()
    monkeypatch.setattr(routes.consolidation_service, "get_savings_forecast", lambda *a, **k: forecast)
    login_as(client, user)
    response = client.get("/enterprise/consolidation/opportunities")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_a_failed_forecast_says_it_could_not_be_calculated(db_session, make_org, client, login_as, monkeypatch):
    body = _page(db_session, make_org, client, login_as, monkeypatch, FAILED)
    assert "savings forecast could not be calculated" in body
    assert "no opportunity has been included" not in body


def test_a_forecast_with_nothing_included_still_says_so(db_session, make_org, client, login_as, monkeypatch):
    body = _page(db_session, make_org, client, login_as, monkeypatch, NOTHING_INCLUDED)
    assert "no opportunity has been included" in body
    assert "could not be calculated" not in body

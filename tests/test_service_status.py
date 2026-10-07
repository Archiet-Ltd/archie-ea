"""The service-status page: current state, incident history, subscription.

State rules are tested against controlled inputs (the platform objectives are
process-wide counters, so they are replaced here rather than read). The page,
the subscription and the operator commands are exercised end to end with two
real organisations: a subscription is one user's own choice and never another
user's, in the same or another organisation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, role="security_architect"):
    from app.models.user import User

    user = User(
        email=f"status-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Status",
        last_name="Reader",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _objectives(**states):
    """A platform objective payload: name -> 'met' | 'missed' | 'unmeasured'."""
    objectives = {}
    for name, state in states.items():
        if state == "unmeasured":
            objectives[name] = {"measured": False, "meets_availability_target": None, "meets_latency_target": None}
        else:
            met = state == "met"
            objectives[name] = {"measured": True, "meets_availability_target": met, "meets_latency_target": met}
    return {"objectives": objectives}


@pytest.fixture
def quiet_objectives(monkeypatch):
    from app.services import platform_slo_service

    def _set(**states):
        monkeypatch.setattr(platform_slo_service, "get_platform_slo_status", lambda: _objectives(**states))

    _set(answers="met", api="met", approvals="unmeasured")
    return _set


def _incident(db_session, title, impact="degraded", started_days_ago=0, resolved=False):
    from app.models.service_incident import ServiceIncident

    started = datetime.utcnow() - timedelta(days=started_days_ago)
    row = ServiceIncident(
        title=title,
        impact=impact,
        started_at=started,
        resolved_at=started + timedelta(hours=1) if resolved else None,
    )
    db_session.add(row)
    db_session.flush()
    return row


# --------------------------------------------------------------- state rules


def test_operational_when_everything_measured_is_met(app, quiet_objectives):
    from app.modules.monitoring.services.service_status import current_status

    with app.test_request_context("/status"):
        status = current_status()
    assert status["state"] == "operational"
    assert status["label"] == "All systems operational"
    unmeasured = [c for c in status["checks"] if c.state is None]
    assert [c.name for c in unmeasured] == ["Approvals"]
    assert unmeasured[0].detail == "Not measured yet"


def test_missed_objective_is_degraded(app, quiet_objectives):
    from app.modules.monitoring.services.service_status import current_status

    quiet_objectives(answers="missed", api="met", approvals="met")
    with app.test_request_context("/status"):
        assert current_status()["state"] == "degraded"


def test_database_not_responding_is_an_outage(app, quiet_objectives, monkeypatch):
    from app.modules.monitoring.services.health_service import HealthService
    from app.modules.monitoring.services.service_status import current_status

    monkeypatch.setattr(HealthService, "check_database", staticmethod(lambda: {"status": "unhealthy"}))
    with app.test_request_context("/status"):
        status = current_status()
    assert status["state"] == "outage"
    assert status["checks"][0].detail == "Not responding"


def test_open_incidents_set_the_state_and_resolved_ones_do_not(app, db_session, quiet_objectives):
    from app.modules.monitoring.services.service_status import current_status

    _incident(db_session, "Old outage", impact="outage", started_days_ago=3, resolved=True)
    with app.test_request_context("/status"):
        assert current_status()["state"] == "operational"

    _incident(db_session, "Slow answers", impact="degraded")
    with app.test_request_context("/status"):
        status = current_status()
    assert status["state"] == "degraded"
    assert [i.title for i in status["open_incidents"]] == ["Slow answers"]

    _incident(db_session, "Sign-in down", impact="outage")
    with app.test_request_context("/status"):
        assert current_status()["state"] == "outage"


def test_history_is_the_last_ninety_days_newest_first(app, db_session, quiet_objectives):
    from app.modules.monitoring.services.service_status import incident_history

    _incident(db_session, "Too old", started_days_ago=120, resolved=True)
    _incident(db_session, "Last month", started_days_ago=30, resolved=True)
    _incident(db_session, "Yesterday", started_days_ago=1, resolved=True)
    with app.test_request_context("/status"):
        titles = [i.title for i in incident_history()]
    assert titles[:2] == ["Yesterday", "Last month"]
    assert "Too old" not in titles


# --------------------------------------------------------------- the page


def test_page_needs_a_signed_in_user(client):
    response = client.get("/status")
    assert response.status_code in (302, 401)


def test_page_shows_state_history_and_footer_link(
    app, client, db_session, make_org, login_as, quiet_objectives
):
    org = make_org("status-page")
    user = _user(db_session, org.id)
    incident = _incident(db_session, "Imports delayed", impact="degraded")
    db_session.commit()

    login_as(client, user)
    response = client.get("/status")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-state="degraded"' in body
    assert "Degraded performance" in body
    assert "Imports delayed" in body
    assert f'data-testid="service-incident-{incident.id}"' in body
    assert "Ongoing" in body
    assert 'data-testid="footer-service-status"' in body
    assert 'data-testid="service-status-subscribe"' in body


def test_empty_history_says_so(app, client, db_session, make_org, login_as, quiet_objectives):
    org = make_org("status-empty")
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)
    body = client.get("/status").get_data(as_text=True)
    assert "No incidents in the last 90 days" in body


# --------------------------------------------------------------- subscription


def test_subscribing_is_one_users_own_choice_across_two_organisations(
    app, client, db_session, make_org, login_as, quiet_objectives
):
    from app.modules.monitoring.services.service_status import is_subscribed

    org_a, org_b = make_org("sub-a"), make_org("sub-b")
    user_a = _user(db_session, org_a.id)
    colleague_a = _user(db_session, org_a.id, role="enterprise_architect")
    user_b = _user(db_session, org_b.id)
    db_session.commit()

    login_as(client, user_a)
    response = client.post("/status/subscription", data={"subscribe": "1"})
    assert response.status_code == 302

    login_as(client, user_a)
    assert 'data-testid="service-status-subscribed"' in client.get("/status").get_data(as_text=True)
    assert is_subscribed(user_a.id) is True
    assert is_subscribed(colleague_a.id) is False
    assert is_subscribed(user_b.id) is False

    login_as(client, user_b)
    body_b = client.get("/status").get_data(as_text=True)
    assert 'data-testid="service-status-subscribe"' in body_b
    assert 'data-testid="service-status-subscribed"' not in body_b

    login_as(client, user_a)
    client.post("/status/subscription", data={"subscribe": "0"})
    assert is_subscribed(user_a.id) is False


# --------------------------------------------------------------- operator commands


def test_operator_opens_lists_and_resolves_an_incident(app, db_session):
    from app.models.service_incident import ServiceIncident

    runner = app.test_cli_runner()
    title = f"Search slow {uuid.uuid4().hex[:6]}"

    opened = runner.invoke(args=["service-incident", "open", title, "--impact", "outage"])
    assert opened.exit_code == 0, opened.output
    incident = ServiceIncident.query.filter_by(title=title).one()
    assert incident.impact == "outage" and incident.resolved_at is None

    listed = runner.invoke(args=["service-incident", "list"])
    assert title in listed.output and "open" in listed.output

    resolved = runner.invoke(args=["service-incident", "resolve", str(incident.id)])
    assert resolved.exit_code == 0, resolved.output
    assert db_session.get(ServiceIncident, incident.id).resolved_at is not None


def test_operator_commands_refuse_bad_input(app):
    runner = app.test_cli_runner()
    bad_impact = runner.invoke(args=["service-incident", "open", "Thing", "--impact", "fine"])
    assert bad_impact.exit_code != 0
    missing = runner.invoke(args=["service-incident", "resolve", "2000000000"])
    assert missing.exit_code != 0 and "No incident" in missing.output

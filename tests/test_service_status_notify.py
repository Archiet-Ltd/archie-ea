"""Subscribers are emailed when a service incident opens and when it is resolved.

Two real organisations: an incident is platform-wide, so a subscriber in either
organisation is told, a non-subscriber and a disabled account are not, and
every message has exactly one recipient and carries no organisation's data.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

SENDER = "status@example.com"


@pytest.fixture
def outbox(app, monkeypatch):
    from app import mail

    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", SENDER)
    with mail.record_messages() as messages:
        yield messages


def _user(db_session, org, subscribed, disabled=False):
    from app.models.ai_suggestion import UserPreference
    from app.models.user import User

    user = User(
        email=f"notify-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Notify",
        last_name="Reader",
        organization_id=org.id,
        confirmed=True,
        disabled_at=datetime.utcnow() if disabled else None,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPreference(user_id=user.id, notify_on_service_status=subscribed))
    db_session.flush()
    return user


@pytest.fixture
def people(db_session, make_org):
    org_a, org_b = make_org("notify-org-a"), make_org("notify-org-b")
    db_session.flush()
    return {
        "org_a": org_a,
        "org_b": org_b,
        "sub_a": _user(db_session, org_a, True),
        "sub_b": _user(db_session, org_b, True),
        "plain": _user(db_session, org_a, False),
        "disabled": _user(db_session, org_b, True, disabled=True),
    }


def _mine(outbox, people):
    ours = {people[k].email for k in ("sub_a", "sub_b", "plain", "disabled")}
    return [m for m in outbox if set(m.recipients) & ours]


def test_opening_emails_each_subscriber_once_and_no_one_else(app, outbox, people):
    from app.modules.monitoring.services import service_status

    service_status.open_incident("Imports are slow", "degraded", "Imports take longer.")
    sent = _mine(outbox, people)
    assert sorted(r for m in sent for r in m.recipients) == sorted(
        [people["sub_a"].email, people["sub_b"].email]
    )
    body = sent[0].body
    assert "Imports are slow" in body and "Degraded" in body and "/status" in body
    assert "UTC" in body


def test_resolving_emails_them_once_more(app, outbox, people):
    from app.modules.monitoring.services import service_status

    incident = service_status.open_incident("Sign-in is down", "outage")
    service_status.resolve_incident(incident.id)
    sent = _mine(outbox, people)
    assert len(sent) == 4
    resolved = [m for m in sent if m.subject.endswith("Sign-in is down") and "Resolved" in m.subject]
    assert len(resolved) == 2
    assert "Down" in sent[0].body


def test_a_disabled_subscriber_gets_nothing(app, outbox, people):
    from app.modules.monitoring.services import service_status

    service_status.open_incident("Queue backlog", "degraded")
    assert all(people["disabled"].email not in m.recipients for m in outbox)


def test_a_mail_failure_still_saves_the_incident(app, people, monkeypatch):
    from app import flask_email
    from app.models.service_incident import ServiceIncident
    from app.modules.monitoring.services import service_status

    def refuse(self, msg):
        raise OSError("mail server unreachable")

    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", SENDER)
    monkeypatch.setattr(flask_email._BoundedConnection, "send", refuse)
    incident = service_status.open_incident("Mail trouble", "degraded")
    assert ServiceIncident.query.filter_by(id=incident.id).count() == 1


def test_each_message_has_one_recipient_and_no_organisation_data(app, outbox, people):
    from app.modules.monitoring.services import service_status

    service_status.open_incident("Search is slow", "degraded")
    sent = _mine(outbox, people)
    assert sent
    for message in sent:
        assert len(message.recipients) == 1
        assert not message.cc and not message.bcc
        assert people["org_a"].name not in message.body + message.html
        assert people["org_b"].name not in message.body + message.html

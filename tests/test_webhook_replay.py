"""Replay from a log offset or a time, and redelivery of one delivery (R1-B28)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.event_log import EventLogRecord
from app.models.webhook import WebhookDelivery
from app.services import webhook_service
from app.services.webhook_service import WebhookService, WebhookValidationError
from tests._webhook_helpers import emit_events, install_guards, install_transport, make_subscription

NOW = datetime(2026, 10, 7, 9, 0, 0)


@pytest.fixture(autouse=True)
def _guards(app, _schema):
    install_guards(app)


def _all(subscription_id):
    return (
        WebhookDelivery.query.filter_by(subscription_id=subscription_id)
        .order_by(WebhookDelivery.created_at, WebhookDelivery.id)
        .all()
    )


def _delivered_ten(service, org_id):
    subscription = make_subscription(service, org_id)
    emit_events(org_id, 10)
    service.fan_out(org_id)
    service.dispatch_due(org_id, now=NOW)
    return subscription


def test_replay_from_sequence_5_of_10_creates_six_ordered_replay_deliveries(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("replay")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = _delivered_ten(service, org.id)
        assert transport.sequences() == list(range(1, 11))
        cursor = subscription.last_ordinal
        assert cursor == 10
        result = service.replay(subscription.id, from_ordinal=5, actor="tester")
        assert result == {"count": 6, "capped": False, "cap": 10_000}
        replays = [d for d in _all(subscription.id) if d.is_replay]
        assert sorted(d.event_ordinal for d in replays) == [5, 6, 7, 8, 9, 10]
        assert all(d.status == "pending" and d.is_replay for d in replays)
        assert subscription.last_ordinal == cursor, "replay must not move the cursor"
        service.dispatch_due(org.id, now=NOW + timedelta(minutes=1))
    assert transport.sequences()[10:] == [5, 6, 7, 8, 9, 10]


def test_replay_since_a_time_selects_by_the_logs_created_at(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("replay-since")
    install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 6)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
        rows = (
            EventLogRecord.query.filter_by(organization_id=org.id)
            .order_by(EventLogRecord.ordinal)
            .all()
        )
        # Space the log's timestamps a minute apart so "since" has an unambiguous midpoint.
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for index, row in enumerate(rows):
            row.created_at = base + timedelta(minutes=index)
        db_session.flush()
        midpoint = base + timedelta(minutes=3)  # the time of ordinal 4
        result = service.replay(subscription.id, since=midpoint, actor="tester")
        assert result["count"] == 3
        replays = [d for d in _all(subscription.id) if d.is_replay]
        assert sorted(d.event_ordinal for d in replays) == [4, 5, 6]
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        assert service.replay(subscription.id, since=future, actor="tester")["count"] == 0
        # a bare time is read as UTC
        assert (
            service.replay(
                subscription.id, since=datetime.utcnow() - timedelta(hours=2), actor="tester"
            )["count"]
            == 6
        )


def test_replay_needs_exactly_one_starting_point(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("replay-arg")
    install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        with pytest.raises(WebhookValidationError):
            service.replay(subscription.id)
        with pytest.raises(WebhookValidationError):
            service.replay(subscription.id, from_ordinal=1, since=NOW)


def test_replay_only_includes_events_the_subscription_matches(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("replay-match")
    install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id, events=["archimate_relationship.*"])
        emit_events(org.id, 3)
        emit_events(org.id, 2, start=4, event_type="archimate_relationship.created")
        assert service.replay(subscription.id, from_ordinal=1)["count"] == 2


def test_replay_is_capped_and_says_so(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("replay-cap")
    install_transport(monkeypatch)
    monkeypatch.setattr(webhook_service, "REPLAY_CAP", 3)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 5)
        result = service.replay(subscription.id, from_ordinal=1)
        assert result == {"count": 3, "capped": True, "cap": 3}
        assert sorted(d.event_ordinal for d in _all(subscription.id) if d.is_replay) == [1, 2, 3]


def test_replay_does_not_overtake_an_event_that_is_still_being_retried(
    monkeypatch, tenant_ctx, make_org, db_session
):
    """Live deliveries stay ahead of replays in the queue, so order for live events is never disturbed."""
    org = make_org("replay-order")
    install_transport(monkeypatch, lambda call: 500)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 2)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
        service.replay(subscription.id, from_ordinal=2)
        head = service._head_of_line(subscription.id, org.id)
        assert head.event_ordinal == 1 and head.is_replay is False


def test_redeliver_copies_one_delivery_and_links_it_to_the_original(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("redeliver")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = _delivered_ten(service, org.id)
        original = WebhookDelivery.query.filter_by(
            subscription_id=subscription.id, event_ordinal=3
        ).one()
        copy = service.redeliver(original.id, actor="tester")
        assert copy.id != original.id
        assert copy.replay_of_id == original.id
        assert copy.is_replay is True
        assert copy.status == "pending"
        assert copy.request_body == original.request_body
        assert copy.event_ordinal == 3 and copy.log_event_id == original.log_event_id
        assert copy.attempt_count == 0
        service.dispatch_due(org.id, now=NOW + timedelta(minutes=1))
    assert transport.sequences()[-1] == 3


def test_redeliver_of_an_unknown_delivery_is_none(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("redeliver-none")
    install_transport(monkeypatch)
    with tenant_ctx(org.id):
        assert WebhookService().redeliver("00000000-0000-0000-0000-000000000000") is None


def test_retrying_an_event_redelivers_its_dead_or_retrying_deliveries(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("retry-event")
    install_transport(monkeypatch, lambda call: 500)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
        record = EventLogRecord.query.filter_by(organization_id=org.id, ordinal=1).one()
        assert service.retry_event(record.event_id) == 1
        assert service.retry_event("not-an-event") is None
        assert len(_all(subscription.id)) == 2

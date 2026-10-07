"""Ordered delivery from the event log: outage recovery, back-off, dead-letter (R1-B28)."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta

import pytest
import requests

from app.services import webhook_service
from app.services.webhook_service import WebhookService, display_status, next_backoff
from tests._webhook_helpers import (
    emit_events,
    install_guards,
    install_transport,
    make_subscription,
)

T0 = datetime(2026, 10, 7, 9, 0, 0)


@pytest.fixture(autouse=True)
def _guards(app, _schema):
    install_guards(app)


def _deliveries(subscription_id):
    from app.models.webhook import WebhookDelivery

    return (
        WebhookDelivery.query.filter_by(subscription_id=subscription_id)
        .order_by(WebhookDelivery.event_ordinal, WebhookDelivery.created_at)
        .all()
    )


# --------------------------------------------------------------------------- #
# The TB-0080 acceptance: a subscriber that is down for an hour
# --------------------------------------------------------------------------- #


def test_a_subscriber_down_for_an_hour_receives_every_event_in_order_afterwards(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("outage")
    clock = {"now": T0}
    recovery = T0 + timedelta(minutes=60)
    outcomes: list[tuple[int, int, datetime]] = []  # (sequence, status, when)

    def handler(call):
        status = 503 if clock["now"] < recovery else 200
        outcomes.append((int(json.loads(call["data"])["sequence"]), status, clock["now"]))
        return status

    install_transport(monkeypatch, handler)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 10)
        second_batch_sent = False
        step = timedelta(seconds=10)
        now = T0
        deadline = T0 + timedelta(hours=3)
        while now < deadline:
            clock["now"] = now
            if not second_batch_sent and now >= T0 + timedelta(minutes=30):
                emit_events(org.id, 10, start=11)  # logged while the subscriber is still down
                second_batch_sent = True
            service.fan_out(org.id)
            service.dispatch_due(org.id, now=now)
            if second_batch_sent and sum(1 for _s, status, _w in outcomes if status == 200) >= 20:
                break
            now += step
        rows = _deliveries(subscription.id)

    failures = [(s, w) for s, status, w in outcomes if status == 503]
    successes = [(s, w) for s, status, w in outcomes if status == 200]
    # While the subscriber was down only the head of the line was ever attempted.
    assert {s for s, _w in failures} == {1}
    # Every event arrived exactly once and in order.
    assert [s for s, _w in successes] == list(range(1, 21))
    # The first success came no more than fifteen minutes (plus 10% jitter and one step) after recovery.
    first_success_at = successes[0][1]
    assert recovery <= first_success_at <= recovery + timedelta(minutes=15) * 1.1 + step
    assert [r.status for r in rows] == ["delivered"] * 20
    assert [r.event_ordinal for r in rows] == list(range(1, 21))
    assert all(r.delivered_at is not None for r in rows)
    # None of the later events was attempted before the one ahead of it was delivered.
    delivered_at = {s: w for s, w in successes}
    for sequence in range(2, 21):
        assert delivered_at[sequence] >= delivered_at[sequence - 1]


# --------------------------------------------------------------------------- #
# Back-off and dead-letter
# --------------------------------------------------------------------------- #


def test_the_back_off_schedule():
    assert [next_backoff(n, jitter=False) for n in range(1, 9)] == [30, 60, 120, 300, 600, 900, 900, 900]
    for n, base in [(1, 30), (2, 60), (3, 120), (4, 300), (5, 600), (6, 900), (40, 900)]:
        for _ in range(50):
            assert base <= next_backoff(n) <= base * 1.10 + 1e-9


def test_after_24_hours_of_failure_a_delivery_is_dead_and_the_next_one_becomes_head_of_line(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("dead")
    state = {"fail_sequence": 1}

    def handler(call):
        sequence = int(json.loads(call["data"])["sequence"])
        return 500 if sequence == state["fail_sequence"] else 200

    transport = install_transport(monkeypatch, handler)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 2)
        service.fan_out(org.id)
        now = T0
        previous_now = None
        for _ in range(400):
            service.dispatch_due(org.id, now=now)
            first, second = _deliveries(subscription.id)
            if first.status == "dead":
                break
            assert first.status == "retrying"
            assert second.status == "pending", "a later event moved before the earlier one finished"
            previous_now, now = now, first.next_attempt_at
        else:
            pytest.fail("the delivery never reached the dead state")
        assert first.dead_lettered_at is not None
        assert first.dead_lettered_at - first.first_attempt_at >= timedelta(hours=24)
        assert previous_now - first.first_attempt_at < timedelta(hours=24)
        assert first.next_attempt_at is None
        # The next delivery is now the head of line and is delivered.
        service.dispatch_due(org.id, now=now + timedelta(seconds=1))
        first, second = _deliveries(subscription.id)
        assert first.status == "dead"
        assert second.status == "delivered"
    assert transport.sequences()[-1] == 2


def test_a_redirect_is_a_failed_attempt_and_its_target_is_never_requested(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("redir")
    transport = install_transport(monkeypatch, lambda call: 302)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id, url="https://hooks.example.com/in")
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=T0)
        (delivery,) = _deliveries(subscription.id)
    assert delivery.status == "retrying"
    assert delivery.error_message == "redirects are not followed"
    assert delivery.response_status == 302
    assert [c["url"] for c in transport.calls] == ["https://hooks.example.com/in"]
    assert all(c["allow_redirects"] is False for c in transport.calls)


def test_a_timeout_is_a_failed_attempt(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("timeout")
    install_transport(monkeypatch, lambda call: requests.exceptions.Timeout("slow"))
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=T0)
        (delivery,) = _deliveries(subscription.id)
    assert delivery.status == "retrying"
    assert delivery.attempt_count == 1
    assert "Timeout" in delivery.error_message
    assert delivery.next_attempt_at >= T0 + timedelta(seconds=30)


@pytest.mark.parametrize("status,delivered", [(200, True), (201, True), (204, True), (299, True), (400, False), (404, False), (500, False)])
def test_success_is_any_2xx(monkeypatch, tenant_ctx, make_org, db_session, status, delivered):
    org = make_org("status")
    install_transport(monkeypatch, lambda call: status)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=T0)
        (delivery,) = _deliveries(subscription.id)
    assert (delivery.status == "delivered") is delivered
    assert delivery.response_status == status


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #


def test_a_new_subscription_does_not_receive_history(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("history")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        emit_events(org.id, 3)
        subscription = make_subscription(service, org.id)
        assert subscription.last_ordinal == 3
        emit_events(org.id, 2, start=4)
        assert service.fan_out(org.id) == 2
        service.dispatch_due(org.id, now=T0)
    assert transport.sequences() == [4, 5]


def test_fan_out_is_idempotent_and_moves_the_cursor(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("cursor")
    install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 3)
        assert service.fan_out(org.id) == 3
        assert service.fan_out(org.id) == 0
        assert subscription.last_ordinal == 3
        assert len(_deliveries(subscription.id)) == 3


def test_subscriptions_receive_only_matching_events(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("filter")
    install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        elements = make_subscription(service, org.id, events=["archimate_element.*"])
        exact = make_subscription(service, org.id, events=["archimate_relationship.created"])
        everything = make_subscription(service, org.id, events=["*"])
        deleted_only = make_subscription(service, org.id, events=["*"], filters={"action": "deleted"})
        emit_events(org.id, 2)
        emit_events(org.id, 1, start=3, event_type="archimate_relationship.created")
        service.fan_out(org.id)
        assert [d.event_type for d in _deliveries(elements.id)] == ["archimate_element.created"] * 2
        assert [d.event_type for d in _deliveries(exact.id)] == ["archimate_relationship.created"]
        assert len(_deliveries(everything.id)) == 3
        assert _deliveries(deleted_only.id) == []


def test_an_inactive_subscription_is_not_delivered_to(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("inactive")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        emit_events(org.id, 1)
        assert service.delete_subscription(subscription.id)
        assert service.fan_out(org.id) == 0
        assert service.dispatch_due(org.id, now=T0) == 0
    assert transport.calls == []


def test_a_test_event_is_one_synchronous_attempt_with_no_retry(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("test-event")
    transport = install_transport(monkeypatch, lambda call: 500)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id)
        result = service.test_subscription(subscription.id)
        assert result["success"] is False
        (delivery,) = _deliveries(subscription.id)
        assert delivery.is_test is True
        assert delivery.status == "dead"
        assert delivery.attempt_count == 1
        assert delivery.next_attempt_at is None
        assert json.loads(transport.calls[0]["data"])["type"] == "webhook.test"
        # A failed test never blocks real deliveries
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=T0)
    assert len(transport.calls) == 2


def test_no_thread_or_sleep_is_left_in_the_delivery_path():
    source = inspect.getsource(webhook_service)
    assert "threading" not in source
    assert "Thread(" not in source
    assert "time.sleep" not in source
    assert "import time" not in source


def test_legacy_statuses_read_as_the_current_ones():
    assert display_status("success") == "delivered"
    assert display_status("failed") == "dead"
    assert display_status("retrying") == "retrying"

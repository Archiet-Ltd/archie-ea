"""Webhook signatures: the signed bytes are the sent bytes, and tampering is detectable (R1-B28)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.services import webhook_service
from app.services.webhook_service import (
    WebhookService,
    WebhookValidationError,
    sign_body,
    verify_signature,
)
from tests._webhook_helpers import (
    emit_events,
    install_guards,
    install_transport,
    make_org_user,
    make_subscription,
)

NOW = datetime(2026, 10, 7, 12, 0, 0)
SECRET = "unit-test-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def _guards(app, _schema):
    install_guards(app)


def _deliver_one(monkeypatch, tenant_ctx, make_org, *, webhook_type="generic", handler=None):
    """One subscription, one event, one attempt; returns (transport, subscription, delivery, org)."""
    from app.models.webhook import WebhookDelivery

    org = make_org("sign")
    transport = install_transport(monkeypatch, handler)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id, secret=SECRET, webhook_type=webhook_type)
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
        delivery = WebhookDelivery.query.filter_by(subscription_id=subscription.id).one()
    return transport, subscription, delivery, org


def test_a_delivery_signature_verifies_against_the_bytes_that_were_sent(
    monkeypatch, tenant_ctx, make_org, db_session
):
    transport, _sub, delivery, _org = _deliver_one(monkeypatch, tenant_ctx, make_org)
    assert len(transport.calls) == 1
    call = transport.calls[0]
    body = call["data"]
    assert isinstance(body, bytes)
    assert body == delivery.request_body.encode("utf-8")
    header = call["headers"]["Entelim-Signature"]
    stamp = int(call["headers"]["Entelim-Timestamp"])
    assert header == f"t={stamp},v1={delivery.signature}"
    assert delivery.signature_timestamp == stamp
    assert verify_signature(SECRET, header, body, now=stamp)
    # the documented recipe, written out by hand, gives the same answer
    import hashlib
    import hmac

    expected = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    assert delivery.signature == expected == sign_body(SECRET, stamp, body)


def test_the_required_headers_are_sent_and_the_old_header_is_not(
    monkeypatch, tenant_ctx, make_org, db_session
):
    transport, _sub, delivery, _org = _deliver_one(monkeypatch, tenant_ctx, make_org)
    headers = transport.calls[0]["headers"]
    assert headers["Entelim-Webhook-Id"] == delivery.id
    assert headers["Entelim-Event-Id"] == delivery.log_event_id
    assert headers["Entelim-Event-Type"] == "archimate_element.created"
    assert headers["Entelim-Timestamp"].isdigit()
    assert headers["Content-Type"] == "application/json"
    assert "X-Webhook-Signature" not in headers
    assert transport.calls[0]["allow_redirects"] is False
    assert transport.calls[0]["timeout"] == (5, 10)


def test_changing_one_byte_of_the_body_fails_verification(
    monkeypatch, tenant_ctx, make_org, db_session
):
    transport, *_ = _deliver_one(monkeypatch, tenant_ctx, make_org)
    call = transport.calls[0]
    header = call["headers"]["Entelim-Signature"]
    stamp = int(call["headers"]["Entelim-Timestamp"])
    body = call["data"]
    tampered = bytearray(body)
    tampered[10] ^= 0x01
    assert verify_signature(SECRET, header, body, now=stamp)
    assert not verify_signature(SECRET, header, bytes(tampered), now=stamp)
    assert not verify_signature(SECRET, header, body + b" ", now=stamp)


def test_changing_the_timestamp_or_the_secret_fails_verification(
    monkeypatch, tenant_ctx, make_org, db_session
):
    transport, *_ = _deliver_one(monkeypatch, tenant_ctx, make_org)
    call = transport.calls[0]
    header = call["headers"]["Entelim-Signature"]
    stamp = int(call["headers"]["Entelim-Timestamp"])
    body = call["data"]
    moved = header.replace(f"t={stamp}", f"t={stamp + 1}")
    assert not verify_signature(SECRET, moved, body, now=stamp)
    assert not verify_signature(SECRET + "x", header, body, now=stamp)
    assert not verify_signature("", header, body, now=stamp)


def test_a_timestamp_older_than_300_seconds_fails(monkeypatch, tenant_ctx, make_org, db_session):
    transport, *_ = _deliver_one(monkeypatch, tenant_ctx, make_org)
    call = transport.calls[0]
    header = call["headers"]["Entelim-Signature"]
    stamp = int(call["headers"]["Entelim-Timestamp"])
    body = call["data"]
    assert verify_signature(SECRET, header, body, now=stamp + 300)
    assert not verify_signature(SECRET, header, body, now=stamp + 301)
    assert not verify_signature(SECRET, header, body, now=stamp - 301)
    assert verify_signature(SECRET, header, body, now=stamp + 301, tolerance=600)


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=00", "v1=00", "t=1", "t=1,v1="])
def test_malformed_headers_never_verify(header):
    assert not verify_signature(SECRET, header, b"{}", now=1)


def test_verification_uses_a_constant_time_comparison():
    import inspect

    assert "compare_digest" in inspect.getsource(webhook_service.verify_signature)


def test_the_body_is_a_cloudevents_envelope(monkeypatch, tenant_ctx, make_org, db_session):
    transport, _sub, delivery, org = _deliver_one(monkeypatch, tenant_ctx, make_org)
    envelope = json.loads(transport.calls[0]["data"])
    assert envelope["specversion"] == "1.0"
    assert envelope["id"] == delivery.log_event_id
    assert envelope["type"] == "archimate_element.created"
    assert envelope["source"] == f"/organisations/{org.id}/entelim"
    assert envelope["datacontenttype"] == "application/json"
    assert envelope["organisationid"] == str(org.id)
    assert envelope["sequence"] == "1"
    assert envelope["data"] == {"action": "created", "id": 1}
    assert envelope["time"]
    assert "subject" in envelope


@pytest.mark.parametrize("webhook_type,marker", [("teams", "attachments"), ("slack", "blocks")])
def test_teams_and_slack_keep_their_formatted_bodies_and_are_signed_too(
    monkeypatch, tenant_ctx, make_org, db_session, webhook_type, marker
):
    transport, *_ = _deliver_one(monkeypatch, tenant_ctx, make_org, webhook_type=webhook_type)
    call = transport.calls[0]
    assert marker in json.loads(call["data"])
    stamp = int(call["headers"]["Entelim-Timestamp"])
    assert verify_signature(SECRET, call["headers"]["Entelim-Signature"], call["data"], now=stamp)


def test_the_body_is_serialised_once_and_the_stored_copy_is_what_is_sent(
    monkeypatch, tenant_ctx, make_org, db_session
):
    transport, _sub, delivery, _org = _deliver_one(monkeypatch, tenant_ctx, make_org)
    assert transport.calls[0]["data"].decode("utf-8") == delivery.request_body


@pytest.mark.parametrize(
    "name",
    ["Host", "host", "Content-Length", "Transfer-Encoding", "Entelim-Signature", "entelim-x"],
)
def test_reserved_headers_cannot_be_set_on_a_subscription(
    monkeypatch, tenant_ctx, make_org, db_session, name
):
    org = make_org("hdr")
    install_transport(monkeypatch)
    with tenant_ctx(org.id):
        with pytest.raises(WebhookValidationError):
            make_subscription(WebhookService(), org.id, headers={name: "x"})
        ok = make_subscription(WebhookService(), org.id, headers={"X-Team": "platform"})
        assert ok.headers == {"X-Team": "platform"}


def test_rotating_the_secret_stops_the_old_one_signing(
    monkeypatch, tenant_ctx, make_org, db_session
):
    from app.models.webhook import WebhookDelivery

    org = make_org("rot")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        sub = make_subscription(service, org.id, secret=SECRET)
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
        first = WebhookDelivery.query.filter_by(subscription_id=sub.id, event_ordinal=1).one()
        new_secret = service.rotate_secret(sub.id)
        assert new_secret and new_secret != SECRET
        emit_events(org.id, 1, start=2)
        service.fan_out(org.id)
        later = datetime(2026, 10, 7, 12, 5, 0)
        service.dispatch_due(org.id, now=later)
        second = WebhookDelivery.query.filter_by(subscription_id=sub.id, event_ordinal=2).one()
        call = transport.calls[-1]
        stamp = int(call["headers"]["Entelim-Timestamp"])
        assert verify_signature(
            new_secret, call["headers"]["Entelim-Signature"], call["data"], now=stamp
        )
        assert not verify_signature(
            SECRET, call["headers"]["Entelim-Signature"], call["data"], now=stamp
        )
        assert "verifies with the current secret" in service.signature_status(sub, second)
        assert "previous secret" in service.signature_status(sub, first)


def test_the_signing_secret_is_never_logged(monkeypatch, tenant_ctx, make_org, db_session, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    _deliver_one(monkeypatch, tenant_ctx, make_org)
    assert SECRET not in caplog.text


def test_a_subscription_with_no_usable_secret_is_never_sent_unsigned(
    monkeypatch, tenant_ctx, make_org, db_session
):
    from app.models.webhook import WebhookDelivery

    org = make_org("nosecret")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id, secret=SECRET)
        subscription.secret_encrypted = None
        subscription.secret = None
        db_session.commit()
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
        delivery = WebhookDelivery.query.filter_by(subscription_id=subscription.id).one()
    assert transport.calls == []
    assert delivery.status == "retrying"
    assert "signing secret" in delivery.error_message


def test_reserved_headers_on_a_stored_row_are_dropped_at_send_time(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("badheaders")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id, secret=SECRET)
        subscription.headers = {
            "Host": "evil.example",
            "Entelim-Signature": "forged",
            "X-Team": "platform",
        }
        db_session.commit()
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
    headers = transport.calls[0]["headers"]
    assert "Host" not in headers
    assert headers["X-Team"] == "platform"
    assert (
        headers["Entelim-Signature"].startswith("t=") and headers["Entelim-Signature"] != "forged"
    )


@pytest.mark.parametrize("events", [[1], [""], [None], ["x" * 101]])
def test_malformed_event_lists_are_refused(monkeypatch, tenant_ctx, make_org, db_session, events):
    org = make_org("badevents")
    install_transport(monkeypatch)
    with tenant_ctx(org.id):
        with pytest.raises(WebhookValidationError):
            make_subscription(WebhookService(), org.id, events=events)

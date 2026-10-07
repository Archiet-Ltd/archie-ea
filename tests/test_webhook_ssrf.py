"""Webhook target URLs: private, loopback and link-local targets are refused (R1-B28)."""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime

import pytest

from app.models.webhook import WebhookSubscription
from app.services import webhook_service
from app.services.webhook_service import WebhookService, WebhookValidationError
from app.utils.ssrf_guard import BlockedOutboundURL, validate_outbound_url
from tests._webhook_helpers import (
    emit_events,
    install_dns,
    install_guards,
    install_transport,
    make_org_user,
    make_subscription,
)

NOW = datetime(2026, 10, 7, 9, 0, 0)

BAD_URLS = [
    "http://127.0.0.1/x",
    "https://10.0.0.5/x",
    "https://169.254.169.254/latest",
    "https://[::1]/x",
    "https://localhost/x",
    "https://user:pw@example.com/x",
    "ftp://example.com/x",
    "https://rebinding.example.com/x",  # resolves (mocked) to 192.168.1.10
    "https://172.16.0.1/x",
    "https://0.0.0.0/x",
]

DNS = {"localhost": "127.0.0.1", "rebinding.example.com": "192.168.1.10"}


@pytest.fixture(autouse=True)
def _guards(app, _schema):
    install_guards(app)


def _count():
    return WebhookSubscription.query.count()


@pytest.mark.parametrize("url", BAD_URLS)
def test_creating_a_subscription_with_a_refused_url_is_a_400_and_stores_nothing(
    monkeypatch, app, db_session, make_org, client, login_as, url
):
    install_dns(monkeypatch, DNS)
    user = make_org_user(db_session, make_org("ssrf"))
    login_as(client, user)
    before = _count()
    response = client.post("/api/webhooks/subscriptions", json={"url": url, "events": ["*"]})
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False and body["error"]
    login_as(client, user)
    assert _count() == before


@pytest.mark.parametrize("url", BAD_URLS)
def test_updating_a_subscription_to_a_refused_url_is_a_400_and_changes_nothing(
    monkeypatch, app, db_session, make_org, tenant_ctx, client, login_as, url
):
    install_dns(monkeypatch, DNS)
    org = make_org("ssrf-up")
    user = make_org_user(db_session, org)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id, url="https://hooks.example.com/ok")
        subscription_id = subscription.id
    db_session.commit()
    login_as(client, user)
    response = client.put(f"/api/webhooks/subscriptions/{subscription_id}", json={"url": url})
    assert response.status_code == 400
    login_as(client, user)
    with tenant_ctx(org.id):
        assert WebhookSubscription.query.filter_by(id=subscription_id).one().url == "https://hooks.example.com/ok"


@pytest.mark.parametrize("url", BAD_URLS)
def test_the_service_raises_a_validation_error_for_a_refused_url(monkeypatch, tenant_ctx, make_org, db_session, url):
    install_dns(monkeypatch, DNS)
    org = make_org("ssrf-svc")
    with tenant_ctx(org.id):
        before = _count()
        with pytest.raises(WebhookValidationError):
            make_subscription(WebhookService(), org.id, url=url)
        assert _count() == before


def test_a_public_https_url_is_accepted(monkeypatch, tenant_ctx, make_org, db_session):
    install_dns(monkeypatch, DNS)
    org = make_org("ssrf-ok")
    with tenant_ctx(org.id):
        subscription = make_subscription(WebhookService(), org.id, url="https://hooks.example.com/ok")
        assert subscription.url == "https://hooks.example.com/ok"


def test_a_stored_hostname_that_later_resolves_to_a_private_address_is_refused_with_no_request(
    monkeypatch, tenant_ctx, make_org, db_session
):
    from app.models.webhook import WebhookDelivery

    org = make_org("ssrf-late")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id, url="https://later.example.com/in")
        emit_events(org.id, 1)
        service.fan_out(org.id)
        install_dns(monkeypatch, {"later.example.com": "10.1.2.3"})
        service.dispatch_due(org.id, now=NOW)
        (delivery,) = WebhookDelivery.query.filter_by(subscription_id=subscription.id).all()
    assert transport.calls == []
    assert delivery.status == "retrying"
    assert delivery.error_message == "target address not allowed"
    assert delivery.attempt_count == 1


def test_a_refused_test_event_makes_no_request(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("ssrf-test")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = make_subscription(service, org.id, url="https://later.example.com/in")
        install_dns(monkeypatch, {"later.example.com": "169.254.169.254"})
        result = service.test_subscription(subscription.id)
    assert result["success"] is False
    assert transport.calls == []


def _legacy_http_subscription(org_id, url):
    """A row from before the https rule; the one writer would now refuse to create it."""
    from app import db

    row = WebhookSubscription(
        id=str(uuid.uuid4()),
        user_id="1",
        url=url,
        events=["*"],
        organization_id=org_id,
        last_ordinal=0,
        secret="legacy-plaintext-secret",
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_a_legacy_plain_http_subscription_still_delivers_to_a_public_host(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("ssrf-http")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        subscription = _legacy_http_subscription(org.id, "http://plain.example.com/in")
        assert subscription.is_plain_http
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
    assert [c["url"] for c in transport.calls] == ["http://plain.example.com/in"]


def test_a_legacy_plain_http_subscription_is_still_refused_for_a_private_host(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("ssrf-http-private")
    transport = install_transport(monkeypatch)
    service = WebhookService()
    with tenant_ctx(org.id):
        _legacy_http_subscription(org.id, "http://127.0.0.1/in")
        emit_events(org.id, 1)
        service.fan_out(org.id)
        service.dispatch_due(org.id, now=NOW)
    assert transport.calls == []


def test_nothing_in_config_or_environment_turns_the_check_off(app, monkeypatch):
    assert app.config["TESTING"] is True
    install_dns(monkeypatch)
    for url in ("https://10.0.0.5/x", "https://169.254.169.254/x", "https://[::1]/x"):
        with pytest.raises(BlockedOutboundURL):
            validate_outbound_url(url, require_https=True)
    for name in ("_check_url", "attempt"):
        source = inspect.getsource(getattr(webhook_service, name, None) or getattr(WebhookService, name))
        assert "config" not in source.lower()
        assert "environ" not in source
    assert not [key for key in app.config if "SSRF" in key.upper()]

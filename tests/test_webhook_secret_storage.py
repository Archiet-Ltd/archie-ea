"""Webhook secrets are stored encrypted and shown once (R1-B28)."""

from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import text

from app.models.webhook import WebhookSubscription
from app.modules.codegen.services.credential_encryption import decrypt_credential
from app.services.webhook_service import WebhookSecretUnavailable, WebhookService
from tests._webhook_helpers import (
    install_guards,
    install_transport,
    make_org_user,
    make_subscription,
)


@pytest.fixture(autouse=True)
def _guards(app, _schema):
    install_guards(app)


def _raw_row(subscription_id):
    from app import db

    return db.session.execute(
        text("SELECT secret, secret_encrypted FROM webhook_subscriptions WHERE id = :id"),
        {"id": subscription_id},
    ).one()


def _legacy(org_id, secret):
    from app import db

    row = WebhookSubscription(
        id=str(uuid.uuid4()),
        user_id="1",
        url="https://legacy.example.com/in",
        events=["*"],
        organization_id=org_id,
        secret=secret,
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_a_generated_secret_is_stored_encrypted_and_returned_once(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("secret")
    install_transport(monkeypatch)
    with tenant_ctx(org.id):
        subscription = make_subscription(WebhookService(), org.id)
        shown = subscription.one_time_secret
        assert shown and len(shown) == 64
        legacy_column, encrypted = _raw_row(subscription.id)
        assert legacy_column is None
        assert encrypted is not None and shown.encode() not in bytes(encrypted)
        assert decrypt_credential(bytes(encrypted)) == shown
        assert subscription.get_secret() == shown


def test_a_supplied_secret_is_stored_encrypted_too(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("secret-supplied")
    install_transport(monkeypatch)
    with tenant_ctx(org.id):
        subscription = make_subscription(WebhookService(), org.id, secret="mine-123")
        assert subscription.one_time_secret is None
        legacy_column, encrypted = _raw_row(subscription.id)
        assert legacy_column is None
        assert decrypt_credential(bytes(encrypted)) == "mine-123"


def test_to_dict_never_carries_the_secret(monkeypatch, tenant_ctx, make_org, db_session):
    org = make_org("secret-dict")
    install_transport(monkeypatch)
    with tenant_ctx(org.id):
        subscription = make_subscription(WebhookService(), org.id, secret="mine-123")
        data = subscription.to_dict()
    assert "secret" not in data and "secret_encrypted" not in data
    assert data["has_secret"] is True
    assert "mine-123" not in repr(data)


def test_a_legacy_plaintext_secret_is_migrated_on_first_use(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("secret-legacy")
    install_transport(monkeypatch)
    with tenant_ctx(org.id):
        row = _legacy(org.id, "legacy-plain")
        assert _raw_row(row.id)[0] == "legacy-plain"
        assert row.get_secret() == "legacy-plain"
        db_session.commit()
        legacy_column, encrypted = _raw_row(row.id)
        assert legacy_column is None
        assert decrypt_credential(bytes(encrypted)) == "legacy-plain"


def test_the_command_migrates_every_legacy_row_and_never_prints_a_secret(app, make_org, db_session):
    org = make_org("secret-cli")
    first = _legacy(org.id, "cli-secret-one")
    second = _legacy(org.id, "cli-secret-two")
    runner = app.test_cli_runner()
    result = runner.invoke(args=["encrypt-webhook-secrets"])
    assert result.exit_code == 0, result.output
    assert "cli-secret" not in result.output
    count = int(result.output.strip().split()[1])
    assert count >= 2
    for row in (first, second):
        legacy_column, encrypted = _raw_row(row.id)
        assert legacy_column is None and encrypted is not None
    assert decrypt_credential(bytes(_raw_row(first.id)[1])) == "cli-secret-one"
    again = runner.invoke(args=["encrypt-webhook-secrets"])
    assert again.exit_code == 0
    assert again.output.strip() == "Encrypted 0 webhook secret(s)."


def test_nothing_is_stored_when_encryption_is_not_configured(
    monkeypatch, tenant_ctx, make_org, db_session
):
    org = make_org("secret-nokey")
    install_transport(monkeypatch)

    def no_key(_value):
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is not configured")

    service = WebhookService()
    with tenant_ctx(org.id):
        existing = make_subscription(service, org.id, secret="before")
        before = WebhookSubscription.query.count()
        monkeypatch.setattr(
            "app.modules.codegen.services.credential_encryption.encrypt_credential", no_key
        )
        with pytest.raises(WebhookSecretUnavailable):
            make_subscription(service, org.id)
        with pytest.raises(WebhookSecretUnavailable):
            service.rotate_secret(existing.id)
        assert WebhookSubscription.query.count() == before


def test_the_api_answers_503_when_encryption_is_not_configured(
    monkeypatch, app, db_session, make_org, client, login_as, tenant_ctx
):
    install_transport(monkeypatch)
    org = make_org("secret-503")
    user = make_org_user(db_session, org)
    with tenant_ctx(org.id):
        existing = make_subscription(WebhookService(), org.id, secret="before")
        existing_id = existing.id
    db_session.commit()

    def no_key(_value):
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is not configured")

    monkeypatch.setattr(
        "app.modules.codegen.services.credential_encryption.encrypt_credential", no_key
    )
    login_as(client, user)
    created = client.post(
        "/api/webhooks/subscriptions", json={"url": "https://hooks.example.com/in", "events": ["*"]}
    )
    assert created.status_code == 503
    assert created.get_json()["success"] is False
    login_as(client, user)
    rotated = client.post(f"/api/webhooks/subscriptions/{existing_id}/rotate-secret")
    assert rotated.status_code == 503
    login_as(client, user)
    assert WebhookSubscription.query.count() == 1


def test_the_secret_appears_once_in_the_api_and_nowhere_after(
    monkeypatch, app, db_session, make_org, client, login_as, caplog
):
    from app.models.audit_log import AuditLog

    install_transport(monkeypatch)
    caplog.set_level(logging.DEBUG)
    org = make_org("secret-api")
    user = make_org_user(db_session, org)
    login_as(client, user)
    created = client.post(
        "/api/webhooks/subscriptions", json={"url": "https://hooks.example.com/in", "events": ["*"]}
    )
    assert created.status_code == 201
    data = created.get_json()["data"]
    secret = data["secret"]
    assert len(secret) == 64
    subscription_id = data["id"]

    seen = []
    login_as(client, user)
    seen.append(client.get(f"/api/webhooks/subscriptions/{subscription_id}").get_data(as_text=True))
    login_as(client, user)
    seen.append(client.get("/api/webhooks/subscriptions").get_data(as_text=True))
    login_as(client, user)
    seen.append(
        client.put(
            f"/api/webhooks/subscriptions/{subscription_id}", json={"description": "renamed"}
        ).get_data(as_text=True)
    )
    login_as(client, user)
    seen.append(
        client.post(f"/api/webhooks/subscriptions/{subscription_id}/test").get_data(as_text=True)
    )
    login_as(client, user)
    seen.append(
        client.get(f"/api/webhooks/subscriptions/{subscription_id}/deliveries").get_data(
            as_text=True
        )
    )
    assert all(secret not in body for body in seen)

    login_as(client, user)
    rotated = client.post(f"/api/webhooks/subscriptions/{subscription_id}/rotate-secret")
    assert rotated.status_code == 200
    new_secret = rotated.get_json()["data"]["secret"]
    assert new_secret != secret and len(new_secret) == 64

    assert secret not in caplog.text and new_secret not in caplog.text
    audit_text = " ".join(
        str({column.name: getattr(row, column.name) for column in row.__table__.columns})
        for row in AuditLog.query.all()
    )
    assert secret not in audit_text and new_secret not in audit_text
    names = {row.action for row in AuditLog.query.all() if "webhook" in (row.action or "")}
    assert {"webhook_sub_create", "webhook_test", "webhook_sec_rotate"} <= names
    assert all(
        len(name) <= 20
        for name in names
        if name.startswith("webhook_sub") or name in {"webhook_test", "webhook_sec_rotate"}
    )

"""
Webhook models for event-driven notifications
"""

from datetime import datetime
from typing import Dict, Optional

from flask import current_app

from app.extensions import db
from app.models.mixins.core import TenantMixin


class WebhookSubscription(TenantMixin, db.Model):
    """Webhook subscription model.

    Pre-TenantMixin, `_find_matching_subscriptions` (webhook_service.py) queried
    every organisation's active subscriptions with no filter at all when publishing
    an event -- one org's event payload could be delivered to another org's
    registered webhook URL. TenantMixin's do_orm_execute filter closes that for
    every bare `.query` call in request context; see reconcile_schema.py's
    `_backfill_webhook_organizations` for the nullable-column backfill this needed
    (organization_id here predates the mixin and can't be NOT NULL immediately).
    """

    __tablename__ = "webhook_subscriptions"

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(36), nullable=False, index=True)
    url = db.Column(db.String(500), nullable=False)
    events = db.Column(db.JSON, nullable=False)  # List of event types to subscribe to
    # Legacy plaintext secret. New secrets are stored only in secret_encrypted;
    # get_secret() moves a legacy value across on first use.
    secret = db.Column(db.String(255), nullable=True)
    secret_encrypted = db.Column(db.LargeBinary, nullable=True)
    # Cursor: the highest event-log ordinal already fanned out to this subscription.
    last_ordinal = db.Column(db.BigInteger, nullable=False, default=0, server_default="0")
    description = db.Column(db.String(500), nullable=True)
    filters = db.Column(db.JSON, nullable=True)  # Additional filtering criteria
    headers = db.Column(db.JSON, nullable=True)  # Custom headers to send
    webhook_type = db.Column(
        db.String(20), nullable=False, default="generic"
    )  # generic | teams | slack
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    deliveries = db.relationship("WebhookDelivery", backref="subscription", lazy=True)

    def set_secret(self, raw: str) -> None:
        """Store *raw* encrypted and clear the legacy plaintext column."""
        from app.modules.codegen.services.credential_encryption import encrypt_credential

        self.secret_encrypted = encrypt_credential(raw)
        self.secret = None

    def get_secret(self) -> Optional[str]:
        """The signing secret, or None when there is none.

        A row that only has the legacy plaintext ``secret`` is encrypted into
        ``secret_encrypted`` here and the plaintext column cleared (the caller's
        commit persists it). If encryption is not configured the plaintext is
        returned for this call and left in place; nothing is logged with it.
        """
        from app.modules.codegen.services.credential_encryption import decrypt_credential

        if self.secret_encrypted:
            return decrypt_credential(bytes(self.secret_encrypted))
        legacy = self.secret
        if not legacy:
            return None
        try:
            self.set_secret(legacy)
        except RuntimeError:
            current_app.logger.warning(
                "webhook subscription %s still holds a plaintext secret: "
                "CREDENTIAL_ENCRYPTION_KEY is not configured",
                self.id,
            )
        return legacy

    @property
    def is_plain_http(self) -> bool:
        return (self.url or "").lower().startswith("http://")

    def to_dict(self) -> Dict:
        """Convert to dictionary representation. Never includes the secret."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "url": self.url,
            "has_secret": bool(self.secret_encrypted or self.secret),
            "last_ordinal": self.last_ordinal or 0,
            "events": self.events,
            "description": self.description,
            "webhook_type": getattr(self, "webhook_type", "generic") or "generic",  # model-safety-ok
            "filters": self.filters or {},
            "headers": self.headers or {},
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class WebhookEvent(TenantMixin, db.Model):
    """Webhook event model. See WebhookSubscription's docstring for why this
    carries TenantMixin -- `get_events`/`retry_event` (webhook_service.py) read
    this table with no org filter of their own, relying on TenantMixin's."""

    __tablename__ = "webhook_events"

    id = db.Column(db.String(36), primary_key=True)
    event_type = db.Column(db.String(100), nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=False)
    user_id = db.Column(db.String(36), nullable=True, index=True)  # Can be null for system events
    event_metadata = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    deliveries = db.relationship(
        "WebhookDelivery", backref="event", lazy=True, foreign_keys="[WebhookDelivery.event_id]"
    )

    def to_dict(self) -> Dict:
        """Convert to dictionary representation"""
        return {
            "id": self.id,
            "event_type": self.event_type,
            "payload": self.payload,
            "user_id": self.user_id,
            "metadata": self.event_metadata or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WebhookDelivery(TenantMixin, db.Model):
    """Webhook delivery attempt model.

    Carries TenantMixin directly (not just inherited via its subscription_id FK)
    because response_body/error_message can hold the delivered payload's content --
    defense in depth per CLAUDE.md's tenant-isolation guidance, not just belt-and-
    suspenders. `_deliver_to_subscriptions` writes these from a background thread
    with no request context (see app.middleware.tenant_isolation), so its INSERT
    is unfiltered by design there; the subscription list it iterates was already
    org-scoped by the time the thread started (WebhookSubscription's own filter,
    applied in-request before the thread is spawned)."""

    __tablename__ = "webhook_deliveries"

    id = db.Column(db.String(36), primary_key=True)
    event_id = db.Column(
        db.String(36), db.ForeignKey("webhook_events.id"), nullable=True, index=True
    )
    subscription_id = db.Column(
        db.String(36), db.ForeignKey("webhook_subscriptions.id"), nullable=False, index=True
    )
    event_type = db.Column(db.String(100), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    # pending, retrying, delivered, dead (legacy rows read success/failed)
    status = db.Column(db.String(20), nullable=False, default="pending")
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    response_status = db.Column(db.Integer, nullable=True)
    response_body = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Event-log delivery (the legacy event_id above points at webhook_events,
    # whose ids are not event-log ids, so the log event id has its own column).
    log_event_id = db.Column(db.String(36), nullable=True, index=True)
    event_ordinal = db.Column(db.BigInteger, nullable=True)
    next_attempt_at = db.Column(db.DateTime, nullable=True)
    first_attempt_at = db.Column(db.DateTime, nullable=True)
    dead_lettered_at = db.Column(db.DateTime, nullable=True)
    is_replay = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    replay_of_id = db.Column(db.String(36), nullable=True)
    is_test = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    request_body = db.Column(db.Text, nullable=True)
    signature_timestamp = db.Column(db.Integer, nullable=True)
    signature = db.Column(db.String(64), nullable=True)

    __table_args__ = (
        db.Index("ix_webhook_deliveries_sub_next_attempt", "subscription_id", "next_attempt_at"),
    )

    def to_dict(self) -> Dict:
        """Convert to dictionary representation"""
        return {
            "id": self.id,
            "subscription_id": self.subscription_id,
            "event_type": self.event_type,
            "event_id": self.log_event_id,
            "sequence": self.event_ordinal,
            "is_replay": bool(self.is_replay),
            "is_test": bool(self.is_test),
            "replay_of_id": self.replay_of_id,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "dead_lettered_at": self.dead_lettered_at.isoformat() if self.dead_lettered_at else None,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "response_status": self.response_status,
            "response_body": self.response_body,
            "error_message": self.error_message,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

"""Signed, ordered, replayable webhooks: columns on the existing webhook tables.

Adds the encrypted-secret column and the event-log cursor to
``webhook_subscriptions``, and the ordering, retry, dead-letter, replay and
signature-record columns to ``webhook_deliveries``. No new table. Every
statement is idempotent, so running the revision twice (or after ``flask
init-db`` already created the columns from the models) is a no-op.

Revision ID: 20261007_signed_webhooks
Revises: 20261007_public_visitor_events
Create Date: 2026-10-07
"""

from alembic import op

revision = "20261007_signed_webhooks"
down_revision = "20261007_public_visitor_events"
branch_labels = None
depends_on = None

_SUBSCRIPTION_COLUMNS = (
    "secret_encrypted BYTEA",
    "last_ordinal BIGINT NOT NULL DEFAULT 0",
)

_DELIVERY_COLUMNS = (
    "log_event_id VARCHAR(36)",
    "event_ordinal BIGINT",
    "next_attempt_at TIMESTAMP",
    "first_attempt_at TIMESTAMP",
    "dead_lettered_at TIMESTAMP",
    "is_replay BOOLEAN NOT NULL DEFAULT FALSE",
    "replay_of_id VARCHAR(36)",
    "is_test BOOLEAN NOT NULL DEFAULT FALSE",
    "request_body TEXT",
    "signature_timestamp INTEGER",
    "signature VARCHAR(64)",
)


def upgrade():
    for column in _SUBSCRIPTION_COLUMNS:
        op.execute(f"ALTER TABLE webhook_subscriptions ADD COLUMN IF NOT EXISTS {column}")
    for column in _DELIVERY_COLUMNS:
        op.execute(f"ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS {column}")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_log_event_id "
        "ON webhook_deliveries (log_event_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_sub_next_attempt "
        "ON webhook_deliveries (subscription_id, next_attempt_at)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_sub_next_attempt")
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_log_event_id")
    for column in _DELIVERY_COLUMNS:
        name = column.split()[0]
        op.execute(f"ALTER TABLE webhook_deliveries DROP COLUMN IF EXISTS {name}")
    for column in _SUBSCRIPTION_COLUMNS:
        name = column.split()[0]
        op.execute(f"ALTER TABLE webhook_subscriptions DROP COLUMN IF EXISTS {name}")

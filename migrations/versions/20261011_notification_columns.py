"""Add columns to notifications table for notification store.

Adds organization_id, event_type, entity_type, entity_id, email_sent_at
to the notifications table. Backfills organization_id from the user's
organization_id for existing rows.

Revision ID: 20261011_notification_columns
Revises: 20261010_oauth_token_grants
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261011_notification_columns"
down_revision = "20261010_oauth_token_grants"
branch_labels = None
depends_on = None


def _has_column(bind, table, column):
    result = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t "
            "AND column_name = :c)"
        ),
        {"t": table, "c": column},
    ).scalar()
    return bool(result)


def upgrade():
    bind = op.get_bind()

    # Add organization_id
    if not _has_column(bind, "notifications", "organization_id"):
        op.add_column(
            "notifications",
            sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True),
        )
        op.create_index("ix_notifications_organization_id", "notifications", ["organization_id"])

    # Add event_type
    if not _has_column(bind, "notifications", "event_type"):
        op.add_column(
            "notifications",
            sa.Column("event_type", sa.String(64), nullable=True),
        )

    # Add entity_type
    if not _has_column(bind, "notifications", "entity_type"):
        op.add_column(
            "notifications",
            sa.Column("entity_type", sa.String(64), nullable=True),
        )

    # Add entity_id
    if not _has_column(bind, "notifications", "entity_id"):
        op.add_column(
            "notifications",
            sa.Column("entity_id", sa.Integer, nullable=True),
        )

    # Add email_sent_at
    if not _has_column(bind, "notifications", "email_sent_at"):
        op.add_column(
            "notifications",
            sa.Column("email_sent_at", sa.DateTime, nullable=True),
        )

    # Backfill organization_id from users table for existing rows
    bind.execute(
        text(
            "SELECT COUNT(*) FROM notifications n "
            "LEFT JOIN users u ON n.user_id = u.id "
            "WHERE n.organization_id IS NULL"
        )
    ).scalar()

    bind.execute(
        text(
            "UPDATE notifications "
            "SET organization_id = u.organization_id "
            "FROM users u "
            "WHERE notifications.user_id = u.id "
            "AND notifications.organization_id IS NULL "
            "AND u.organization_id IS NOT NULL"
        )
    )

    remaining_nulls = bind.execute(
        text(
            "SELECT COUNT(*) FROM notifications "
            "WHERE organization_id IS NULL"
        )
    ).scalar()

    if remaining_nulls:
        print(
            f"WARNING: {remaining_nulls} notification(s) have no organization_id "
            f"after backfill (user has no organisation)."
        )


def downgrade():
    bind = op.get_bind()

    if _has_column(bind, "notifications", "email_sent_at"):
        op.drop_column("notifications", "email_sent_at")
    if _has_column(bind, "notifications", "entity_id"):
        op.drop_column("notifications", "entity_id")
    if _has_column(bind, "notifications", "entity_type"):
        op.drop_column("notifications", "entity_type")
    if _has_column(bind, "notifications", "event_type"):
        op.drop_column("notifications", "event_type")
    if _has_column(bind, "notifications", "organization_id"):
        op.drop_index("ix_notifications_organization_id", "notifications")
        op.drop_column("notifications", "organization_id")
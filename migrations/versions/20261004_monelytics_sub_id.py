"""Add monelytics_subscription_id to subscriptions.

ID: 20261004_monelytics_sub_id
Revises: 20261004_meaning_org_nullable
Create Date: 2026-10-04

The Monelytics provider (app/services/monelytics_provider.py) never writes a
plan onto the subscriptions row until a refresh call confirms Monelytics
itself has an active subscription, so this column is the only reliable way to
tell "this organisation has a live Monelytics-backed subscription" apart from
"this organisation has never checked out" without another round trip. Nothing
else reads or writes it: refreshing the row by organisation + product code
never needs the id itself.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261004_monelytics_sub_id"
down_revision = "20261004_meaning_org_nullable"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "subscriptions",
        sa.Column("monelytics_subscription_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_subscriptions_monelytics_subscription_id",
        "subscriptions",
        ["monelytics_subscription_id"],
    )


def downgrade():
    op.drop_index("ix_subscriptions_monelytics_subscription_id", table_name="subscriptions")
    op.drop_column("subscriptions", "monelytics_subscription_id")

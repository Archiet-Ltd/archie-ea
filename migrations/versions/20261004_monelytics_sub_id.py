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
from sqlalchemy import text

revision = "20261004_monelytics_sub_id"
down_revision = "20261004_meaning_org_nullable"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_subscriptions_monelytics_subscription_id"


def upgrade():
    bind = op.get_bind()
    # IF NOT EXISTS, not op.add_column: this column is also declared directly
    # on the Subscription model (app/models/subscription.py), so
    # reconcile-schema's own ADD COLUMN IF NOT EXISTS sweep can already have
    # added it by the time this revision runs in the same CI setup -- a bare
    # op.add_column() then fails with "column already exists" (seen in CI on
    # this exact migration, "Database gates (schema drift)"). Matches
    # 20261001_adr_canonical_cols.py's idiom.
    bind.execute(text(
        "ALTER TABLE subscriptions "
        "ADD COLUMN IF NOT EXISTS monelytics_subscription_id VARCHAR(255)"
    ))
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} "
        "ON subscriptions (monelytics_subscription_id)"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
    bind.execute(text(
        "ALTER TABLE subscriptions DROP COLUMN IF EXISTS monelytics_subscription_id"
    ))

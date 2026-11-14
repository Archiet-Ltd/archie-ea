"""Record a free offer on the subscription row.

Four nullable columns say which offer an organisation used, on which company domain, with
what declaration, and when. A partial unique index lets one startup credit exist per company
domain, so two organisations claiming in the same second cannot both be granted.

The downgrade drops the index and the columns; no other column is touched.

Revision ID: 20261114_subscription_offer
Revises: 20261112_competitive_advantage
Create Date: 2026-11-14
"""
from alembic import op

revision = "20261114_subscription_offer"
down_revision = "20261112_competitive_advantage"
branch_labels = None
depends_on = None

_INDEX = "uq_subscriptions_startup_credit_domain"


def upgrade():
    op.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS offer VARCHAR(32)")
    op.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS offer_domain VARCHAR(255)")
    op.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS offer_terms JSON")
    op.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS offer_granted_at TIMESTAMP")
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} ON subscriptions (offer_domain) "
        "WHERE offer = 'startup_credit'"
    )


def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    for column in ("offer_granted_at", "offer_terms", "offer_domain", "offer"):
        op.execute(f"ALTER TABLE subscriptions DROP COLUMN IF EXISTS {column}")

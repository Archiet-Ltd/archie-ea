"""Let a subscription be on the Education plan.

Adds the label ``education`` to the native ``subscriptionplan`` type. The label is added on its
own committed connection, so the new value is usable as soon as the revision ends.

The downgrade cannot remove a label from a PostgreSQL enum type, so it leaves the label and moves
any row on Education back to Community; the offer columns on that row are not touched.

Revision ID: 20261114_plan_education
Revises: 20261114_subscription_offer
Create Date: 2026-11-14
"""
from alembic import op

revision = "20261114_plan_education"
down_revision = "20261114_subscription_offer"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE subscriptionplan ADD VALUE IF NOT EXISTS 'education'")


def downgrade():
    op.execute("UPDATE subscriptions SET plan = 'free' WHERE plan = 'education'")

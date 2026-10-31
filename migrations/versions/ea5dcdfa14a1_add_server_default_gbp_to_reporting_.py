"""add_server_default_gbp_to_reporting_currency

Revision ID: ea5dcdfa14a1
Revises: 20261025_vendor_naming
Create Date: 2026-10-25 23:29:04.723020

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'ea5dcdfa14a1'
down_revision = "20261025_organization_signals"
branch_labels = None
depends_on = None


def upgrade():
    # Backfill NULL reporting_currency to 'GBP'
    op.execute("UPDATE organizations SET reporting_currency = 'GBP' WHERE reporting_currency IS NULL")
    # Add server_default='GBP' and make NOT NULL
    op.execute("ALTER TABLE organizations ALTER COLUMN reporting_currency SET DEFAULT 'GBP'")
    op.execute("ALTER TABLE organizations ALTER COLUMN reporting_currency SET NOT NULL")


def downgrade():
    # Remove NOT NULL and DEFAULT
    op.execute("ALTER TABLE organizations ALTER COLUMN reporting_currency DROP DEFAULT")
    op.execute("ALTER TABLE organizations ALTER COLUMN reporting_currency DROP NOT NULL")
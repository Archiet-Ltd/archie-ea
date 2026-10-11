"""Merge solution and vendor pricing heads

Revision ID: 20260123_merge_solution_vendor_pricing
Revises:
Create Date: 2026-01-23 13:34:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260123_merge_solution_vendor_pricing"
down_revision = ("20260123_add_solution_model", "20260123_add_vendor_product_pricing")
branch_labels = None
depends_on = None


def upgrade():
    # Merge migration: no schema changes, only unite heads
    pass


def downgrade():
    # Downgrade not implemented for merge migration
    pass

"""Add vendor_product_pricing table

Revision ID: 20260123_add_vendor_product_pricing
Revises: 20260123_merge_heads
Create Date: 2026-01-23 12:19:31.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260123_add_vendor_product_pricing"
down_revision = "20260123_merge_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vendor_product_pricing",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("vendor_products.id"), nullable=False),
        sa.Column("pricing_model", sa.String(length=50), nullable=False),
        sa.Column("tier_name", sa.String(length=100), nullable=False),
        sa.Column("list_price_annual", sa.Numeric(15, 2)),
        sa.Column("typical_discount_percent", sa.Integer()),
        sa.Column("min_users", sa.Integer()),
        sa.Column("max_users", sa.Integer()),
        sa.Column("includes_support", sa.Boolean()),
        sa.Column("currency", sa.String(length=3)),
        sa.Column("effective_date", sa.Date()),
        sa.Column("expiry_date", sa.Date()),
        sa.Column("source", sa.String(length=200)),
        sa.Column("billing_frequency", sa.String(length=30)),
        sa.Column("contract_term_months", sa.Integer()),
        sa.Column("setup_fee", sa.Numeric(15, 2)),
        sa.Column("implementation_fee", sa.Numeric(15, 2)),
        sa.Column("training_included", sa.Boolean()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
    )


def downgrade():
    op.drop_table("vendor_product_pricing")

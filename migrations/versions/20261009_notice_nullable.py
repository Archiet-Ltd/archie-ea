"""Make notice_period_days nullable so "not recorded" can be represented.

The column previously had default=90, which meant a new row always had a
notice period of 90 days and "no notice period recorded" was impossible.
This migration alters the column to be nullable and removes the default.

Revision ID: 20261009_notice_nullable
Revises: 20261008_uwp_element_unique
Create Date: 2026-10-09
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261009_notice_nullable"
down_revision = "20261008_uwp_element_unique"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("vendor_contracts"):
        return
    columns = {c["name"] for c in inspector.get_columns("vendor_contracts")}
    if "notice_period_days" not in columns:
        return
    op.execute(
        "ALTER TABLE vendor_contracts ALTER COLUMN notice_period_days DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE vendor_contracts ALTER COLUMN notice_period_days TYPE integer USING notice_period_days::integer"
    )
    op.execute(
        "ALTER TABLE vendor_contracts ALTER COLUMN notice_period_days DROP NOT NULL"
    )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("vendor_contracts"):
        return
    columns = {c["name"] for c in inspector.get_columns("vendor_contracts")}
    if "notice_period_days" not in columns:
        return
    op.execute(
        "ALTER TABLE vendor_contracts ALTER COLUMN notice_period_days SET DEFAULT 90"
    )
    op.execute(
        "UPDATE vendor_contracts SET notice_period_days = 90 WHERE notice_period_days IS NULL"
    )
    op.execute(
        "ALTER TABLE vendor_contracts ALTER COLUMN notice_period_days SET NOT NULL"
    )
"""The platform's answer spend: one row per provider per calendar month.

llm_spend_ledger (provider, month, usd_spent, requests, updated_at) is written by the LLM
service after every call made on the platform's own key and read before every such call, so
a provider whose month-to-date spend has reached its ceiling is skipped and, with none left,
answers are paused. Global (no organisation): the platform pays, not a tenant.

Idempotent: the table is created only when missing. The down step drops it.

Revision ID: 20261029_llm_spend_ledger
Revises: 20261027_admin_every_org
Create Date: 2026-10-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_llm_spend_ledger"
down_revision = "20261027_admin_every_org"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("llm_spend_ledger"):
        return
    op.create_table(
        "llm_spend_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("usd_spent", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "month", name="uq_llm_spend_ledger_provider_month"),
    )


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("llm_spend_ledger"):
        op.drop_table("llm_spend_ledger")

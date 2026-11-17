"""Give the sample company's records their own origin kind, 'sample', instead of 'connector'.

Rows the sample company loaded earlier were stamped 'connector' with the label 'Sample company',
so an answer could present them as evidence a connector read. This re-stamps exactly those rows
(kind 'connector' AND label 'Sample company'); a row a real connector wrote carries a repository
label and is untouched. Data-only; idempotent.

Revision ID: 20261030_sample_source_kind
Revises: 20261030_sample_company_origin
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261030_sample_source_kind"
down_revision = "20261030_sample_company_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("archimate_elements", "archimate_relationships"):
        bind.execute(sa.text(
            "UPDATE %s SET source_kind = 'sample' "
            "WHERE source_kind = 'connector' AND source_label = 'Sample company'" % table
        ))


def downgrade() -> None:
    pass

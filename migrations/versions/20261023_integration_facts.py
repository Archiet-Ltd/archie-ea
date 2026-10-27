"""Integration facts table.

Creates the integration_facts table with TenantMixin (organization_id FK); the
row-level security policy is the next revision. Records facts derived from
external systems before they are applied to the ArchiMate backbone.

Expand step only: adds one new table. Idempotent: the table and its indexes are
created only when absent, so a second run (or a database where ``init-db`` got
there first) is a no-op.

Revision ID: 20261023_integration_facts
Revises: 20261027_stakeholder_comm_org
Create Date: 2026-10-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261023_integration_facts"
down_revision = "20261027_stakeholder_comm_org"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    if not inspect(bind).has_table("integration_facts"):
        op.create_table(
            "integration_facts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("system", sa.String(50), nullable=False),
            sa.Column("external_id", sa.String(255), nullable=False),
            sa.Column("path", sa.String(100), nullable=False),
            sa.Column("fact_type", sa.String(20), nullable=False),
            sa.Column("fact_data", sa.Text(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("source_item_type", sa.String(50), nullable=True),
            sa.Column("source_read_at", sa.DateTime(), nullable=True),
            sa.Column(
                "status",
                sa.Enum("PENDING", "APPLIED", "REJECTED", name="integrationfactstatus"),
                nullable=False,
            ),
            sa.Column("applied_at", sa.DateTime(), nullable=True),
            sa.Column("applied_element_id", sa.Integer(), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "organization_id", "system", "external_id", "path",
                name="uq_integration_fact_system_id_path",
            ),
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_integration_facts_org_system_status "
        "ON integration_facts (organization_id, system, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_integration_facts_organization_id "
        "ON integration_facts (organization_id)"
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS integration_facts")
    op.execute("DROP TYPE IF EXISTS integrationfactstatus")

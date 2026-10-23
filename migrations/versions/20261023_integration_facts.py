"""Integration facts table.

Creates integration_facts table with TenantMixin (organization_id FK) and RLS policy.
Records facts derived from external systems before they are applied to the ArchiMate
backbone.

Revision ID: 20261023_integration_facts
Revises: 20261022_erole_no_default
Create Date: 2026-10-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261023_integration_facts"
down_revision = "20261022_erole_no_default"
branch_labels = None
depends_on = None


def upgrade():
    # Create integration_facts table
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
        sa.Column("status", sa.Enum("PENDING", "APPLIED", "REJECTED", name="integrationfactstatus"), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("applied_element_id", sa.Integer(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "system", "external_id", "path",
            name="uq_integration_fact_system_id_path"
        ),
    )
    op.create_index(
        "ix_integration_facts_org_system_status",
        "integration_facts",
        ["organization_id", "system", "status"],
    )
    op.create_index(
        op.f("ix_integration_facts_organization_id"),
        "integration_facts",
        ["organization_id"],
    )

    # Enable RLS
    op.execute("ALTER TABLE integration_facts ENABLE ROW LEVEL SECURITY")

    # Create RLS policy: users see only their organization's facts
    op.execute("""
        CREATE POLICY integration_facts_tenant_isolation ON integration_facts
        USING (organization_id = current_setting('app.current_organization_id')::integer)
    """)


def downgrade():
    op.execute("DROP POLICY IF EXISTS integration_facts_tenant_isolation ON integration_facts")
    op.execute("ALTER TABLE integration_facts DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_integration_facts_org_system_status", table_name="integration_facts")
    op.drop_index(op.f("ix_integration_facts_organization_id"), table_name="integration_facts")
    op.drop_table("integration_facts")
    op.execute("DROP TYPE IF EXISTS integrationfactstatus")

"""Relax gaps.organization_id to nullable for unattributable merged rows.

Gap already carries TenantMixin and organization_id NOT NULL. Merging
roadmap_gaps, implementation_gaps and compliance_gaps into gaps needs to
quarantine a row no source lets us attribute to an organisation, so the
column is widened to nullable rather than guessing an owner.

Revision ID: 20261003_gap_org_nullable
Revises: 20261004_evt_default
Create Date: 2026-10-03
"""
from alembic import op
from sqlalchemy import text

revision = "20261003_gap_org_nullable"
down_revision = "20261004_evt_default"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text("ALTER TABLE gaps ALTER COLUMN organization_id DROP NOT NULL"))


def downgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE gaps ALTER COLUMN organization_id SET NOT NULL"
    ))

"""Add organization_id to duplicate-detection tables for tenant isolation.

DuplicateDetectionRun, DuplicateGroup, UnifiedDetectionRun,
UnifiedDuplicateGroup, and ConsolidationListEntry all gained TenantMixin.
This migration adds the organization_id column (nullable for legacy rows)
to every table that does not already have it.

Revision ID: 20261002_dd_tenancy
Revises: 20261002_data_domain_org_unique
Create Date: 2026-10-03
"""
from alembic import op
from sqlalchemy import text

revision = "20261002_dd_tenancy"
down_revision = "20261002_data_domain_org_unique"
branch_labels = None
depends_on = None

_TABLES = (
    "duplicate_detection_runs",
    "duplicate_groups",
    "unified_detection_runs",
    "unified_duplicate_groups",
    "consolidation_list_entries",
)


def upgrade():
    bind = op.get_bind()
    for table in _TABLES:
        bind.execute(text(
            f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS organization_id INTEGER'
        ))


def downgrade():
    bind = op.get_bind()
    for table in _TABLES:
        bind.execute(text(
            f'ALTER TABLE {table} DROP COLUMN IF EXISTS organization_id'
        ))
"""Make meanings.organization_id nullable (R1-B81 PR #370 fix round).

Meaning gained TenantMixin before this brief, which declares
organization_id NOT NULL -- correct for a model that starts
tenant-scoped from creation, wrong for one with a real pre-existing
orphan population. A fresh create_all() built the column NOT NULL per
the mixin; production's column was added by reconcile-schema (ADD
COLUMN, always nullable) and genuinely carries legacy NULL rows that
backfill_meaning_tenancy (app/commands/backfill_meaning_tenancy.py) is
meant to tolerate, not reject. This migration makes the schema this
repo builds fresh match what production already has, idempotently.

Revision ID: 20261004_meaning_org_nullable
Revises: 20261004_product_inquiries
Create Date: 2026-10-04
"""
from alembic import op
from sqlalchemy import text

revision = "20261004_meaning_org_nullable"
down_revision = "20261004_product_inquiries"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text("ALTER TABLE meanings ALTER COLUMN organization_id DROP NOT NULL"))


def downgrade():
    # Not reversible without first re-deriving every orphan's organisation
    # (that's what backfill_meaning_tenancy is for) -- re-adding NOT NULL
    # blind would fail outright on any row the backfill couldn't resolve.
    pass

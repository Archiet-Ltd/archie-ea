"""Drop NOT NULL on unified_work_packages.business_capability.

``UnifiedWorkPackage.business_capability`` (app/models/unified_work_package.py)
was historically NOT NULL, but the consolidation merges rows from
technology_roadmap_initiatives, implementation_work_packages, and work_packages
that may carry no capability link at all. Forcing a value would be inventing
one, so the model was relaxed to nullable=True in the same change that added
TenantMixin. ``reconcile-schema`` adds missing columns but never relaxes a
constraint on one that already exists, so any database whose table predates
that model change keeps the original NOT NULL until this runs.

Idempotent: PostgreSQL's ``DROP NOT NULL`` is a no-op on an already-nullable
column.

Revision ID: 20261002_wp_cap_nullable
Revises: 20261001_approval_nullable
Create Date: 2026-10-02
"""
from alembic import op
from sqlalchemy import text

from app.commands.schema_migrations import ContractBlocked

revision = "20261002_wp_cap_nullable"
down_revision = "20261002_vendor_legal_reg_idx"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE unified_work_packages ALTER COLUMN business_capability DROP NOT NULL"
    ))


def downgrade():
    bind = op.get_bind()
    orphaned = bind.execute(
        text("SELECT count(*) FROM unified_work_packages WHERE business_capability IS NULL")
    ).scalar()
    if orphaned:
        raise ContractBlocked(
            f"unified_work_packages.business_capability: {orphaned} row(s) have no "
            "capability link; re-adding NOT NULL would break them. Forward-fix instead."
        )
    bind.execute(text(
        "ALTER TABLE unified_work_packages ALTER COLUMN business_capability SET NOT NULL"
    ))
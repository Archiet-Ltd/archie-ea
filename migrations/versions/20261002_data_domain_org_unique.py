"""Replace global unique index on data_domains.name with per-organisation constraint.

The global ``ix_data_domains_name`` unique index prevented two organisations
from each having a default "General" data domain.  Domain names must be unique
within an organisation, not across the whole platform.

Idempotent: the model's ``__table_args__`` already declares the
``uq_data_domains_org_name`` constraint, so on a fresh CI database the schema
build creates it before this migration runs.  The upgrade guards both the
index drop and the constraint create so a re-run or a fresh-database run is a
no-op rather than a duplicate-object error, matching the idiom in
20260930_capability_backlinks.py and 20261001_adr_canonical_cols.py.

Revision ID: 20261002_data_domain_org_unique
Revises: 20261001_approval_nullable
Create Date: 2026-10-02
"""
from alembic import op
from sqlalchemy import text

revision = "20261002_data_domain_org_unique"
down_revision = "20261001_approval_nullable"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Drop the old global unique index if it still exists (it may have been
    # replaced by the model's __table_args__ constraint on a fresh schema).
    bind.execute(text(
        "DROP INDEX IF EXISTS ix_data_domains_name"
    ))

    # Create the per-organisation constraint only if it does not already exist.
    # Postgres has no ADD CONSTRAINT IF NOT EXISTS; guard explicitly so a
    # second run of this revision (or a fresh CI schema build that already
    # created the constraint from the model) is a no-op rather than a
    # duplicate-object error, matching 20260930_capability_backlinks.py.
    bind.execute(text(
        """
        DO $add_uq$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = to_regclass('data_domains')
              AND conname = 'uq_data_domains_org_name'
          ) THEN
            ALTER TABLE data_domains
              ADD CONSTRAINT uq_data_domains_org_name
              UNIQUE (organization_id, name);
          END IF;
        END
        $add_uq$
        """
    ))


def downgrade():
    bind = op.get_bind()

    bind.execute(text(
        "ALTER TABLE data_domains DROP CONSTRAINT IF EXISTS uq_data_domains_org_name"
    ))

    # Recreate the global unique index only if it does not already exist.
    bind.execute(text(
        """
        DO $add_idx$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'data_domains'
              AND indexname = 'ix_data_domains_name'
          ) THEN
            CREATE UNIQUE INDEX ix_data_domains_name ON data_domains (name);
          END IF;
        END
        $add_idx$
        """
    ))
"""Add driver_id FK to assessments table.

An Assessment is conducted against a Driver; this column records that link.
Nullable: historical assessments predate this column and have no driver to
attribute.

Idempotent: uses IF NOT EXISTS / IF EXISTS so the migration is safe to run
against a database that already has the column (e.g. after reconcile-schema
has already added it from the model).

Revision ID: 20261002_assessment_driver_id
Revises: 20261002_agent_charters
Create Date: 2026-10-02
"""
from alembic import op
from sqlalchemy import text

# alembic_version.version_num is VARCHAR(32) -- keep this id at or under that
# length, the same constraint every revision after the baseline already satisfies.
revision = "20261002_assessment_driver_id"
down_revision = "20261002_agent_charters"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE assessments ADD COLUMN IF NOT EXISTS driver_id INTEGER"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_assessments_driver_id ON assessments (driver_id)"
    ))
    # Only add the FK constraint if the column was just created or if the
    # constraint does not already exist.  PostgreSQL does not support
    # ADD CONSTRAINT IF NOT EXISTS, so we inspect.
    result = bind.execute(text(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE constraint_name = 'fk_assessments_driver_id' "
        "AND table_name = 'assessments'"
    )).scalar()
    if not result:
        bind.execute(text(
            "ALTER TABLE assessments ADD CONSTRAINT fk_assessments_driver_id "
            "FOREIGN KEY (driver_id) REFERENCES drivers (id)"
        ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE assessments DROP CONSTRAINT IF EXISTS fk_assessments_driver_id"
    ))
    bind.execute(text(
        "DROP INDEX IF EXISTS ix_assessments_driver_id"
    ))
    bind.execute(text(
        "ALTER TABLE assessments DROP COLUMN IF EXISTS driver_id"
    ))
"""Add vendor_organizations legal-entity columns and a partial unique index.

The four columns (legal_name, legal_registration_number, legal_address,
parent_vendor_id) are declared on the VendorOrganization model but the model
is tagged migration-exempt, so reconcile-schema will not add them. This
migration adds them for existing databases, then creates the partial unique
index on legal_registration_number.

The columns must be added before the index: the original version of this
migration created the index first, which failed on production because the
column did not exist yet (schema-deploy runs migrations before
deploy-schema.sh).

Revision ID: 20261002_vendor_legal_reg_idx
Revises: 20261002_assessment_driver_id
Create Date: 2026-10-02
"""
from alembic import op
from sqlalchemy import text

revision = "20261002_vendor_legal_reg_idx"
down_revision = "20261002_assessment_driver_id"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("legal_name", "VARCHAR(300)"),
    ("legal_registration_number", "VARCHAR(100)"),
    ("legal_address", "TEXT"),
    ("parent_vendor_id", "INTEGER REFERENCES vendor_organizations(id)"),
)


def upgrade():
    bind = op.get_bind()

    # Add the four legal-entity columns first — the index below depends on
    # legal_registration_number existing.
    for column, coltype in _COLUMNS:
        bind.execute(text(
            f"ALTER TABLE vendor_organizations ADD COLUMN IF NOT EXISTS {column} {coltype}"
        ))

    # Partial unique index: NULLs are distinct under standard UNIQUE, so
    # only non-NULL values are constrained.
    bind.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vendor_legal_reg "
        "ON vendor_organizations(legal_registration_number) "
        "WHERE legal_registration_number IS NOT NULL"
    ))


def downgrade():
    bind = op.get_bind()

    bind.execute(text("DROP INDEX IF EXISTS uq_vendor_legal_reg"))

    for column, _coltype in reversed(_COLUMNS):
        bind.execute(text(
            f"ALTER TABLE vendor_organizations DROP COLUMN IF EXISTS {column}"
        ))
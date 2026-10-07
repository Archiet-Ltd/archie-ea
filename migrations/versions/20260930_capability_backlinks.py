"""Add a retired_into_id backlink to the four remaining legacy capability stores.

Expand step for the capability consolidation (ADR 0008): `capabilities`,
`enterprise_capabilities`, `archimate_capabilities` and `technical_capabilities`
each gain a nullable `retired_into_id` column pointing at the
`unified_capabilities` row that now represents the same capability, exactly
like `business_capability.deprecated_in_favor_of_id` already does for the
first superseded store. `flask backfill-capability-catalogs` (the accompanying
backfill command) sets it once per row and treats a row it has already
resolved as done, so this is also what makes that command idempotent.

A plain nullable column is ordinarily `reconcile-schema`'s job, but this
consolidation's design explicitly calls for every schema change to go through
this versioned revision system instead, never only through reconcile-schema
(see docs/adr/0008-one-system-of-record.md). The down step drops all four
columns; nothing depends on them existing, and no data is lost by removing a
pointer.

Revision ID: 20260930_capability_backlinks
Revises: 20260926_widen_element_name
Create Date: 2026-09-30
"""
from alembic import op
from sqlalchemy import text

revision = "20260930_capability_backlinks"
down_revision = "20260926_widen_element_name"
branch_labels = None
depends_on = None

# (table, constraint name) -- one nullable BigInteger column per table, all
# named identically for consistency with `unified_capabilities.retired_into_id`
# itself and with `business_capability.deprecated_in_favor_of_id`'s role.
_TABLES = (
    ("capabilities", "fk_capabilities_retired_into"),
    ("enterprise_capabilities", "fk_enterprise_capabilities_retired_into"),
    ("archimate_capabilities", "fk_archimate_capabilities_retired_into"),
    ("technical_capabilities", "fk_technical_capabilities_retired_into"),
)


def upgrade():
    bind = op.get_bind()
    for table, constraint_name in _TABLES:
        bind.execute(text(
            f'ALTER TABLE "{table}" '
            f'ADD COLUMN IF NOT EXISTS retired_into_id BIGINT'
        ))
        bind.execute(text(
            f'CREATE INDEX IF NOT EXISTS ix_{table}_retired_into_id '
            f'ON "{table}" (retired_into_id)'
        ))
        # Postgres has no ADD CONSTRAINT IF NOT EXISTS; guard explicitly so a
        # second run of this revision (or a database schema-upgrade re-applies
        # after a partial failure) is a no-op rather than a duplicate-object
        # error, matching install_cutover_constraints's own idiom
        # (app/commands/cutover_capability_tenancy.py).
        bind.execute(text(
            f"""
            DO $add_fk$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('{table}')
                  AND conname = '{constraint_name}'
              ) THEN
                ALTER TABLE "{table}"
                  ADD CONSTRAINT {constraint_name}
                  FOREIGN KEY (retired_into_id) REFERENCES unified_capabilities(id)
                  ON DELETE SET NULL;
              END IF;
            END
            $add_fk$
            """
        ))


def downgrade():
    bind = op.get_bind()
    for table, constraint_name in _TABLES:
        bind.execute(text(
            f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS {constraint_name}'
        ))
        bind.execute(text(
            f'DROP INDEX IF EXISTS ix_{table}_retired_into_id'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS retired_into_id'
        ))

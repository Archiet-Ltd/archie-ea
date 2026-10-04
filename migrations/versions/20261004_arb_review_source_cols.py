"""Add consolidation-provenance columns to the two ARB review tables.

``arb_review_cycles`` (``ARBReviewCycle``) and ``arb_review_items``
(``ARBReviewItem``) each declare five columns tracking where a row was
folded in from and whether it has since been retired into a newer row:
``source_table``, ``source_id``, ``source_org_id``, ``source_checksum``
and a self-referencing ``retired_into_id`` — the same shape already used
for the capability stores in ``20260930_capability_backlinks``. Neither
table has ever had a dedicated migration (both are created directly from
the models by ``ensure_baseline_schema``'s ``create_all``), so on a
database the baseline step has already brought up to the current model
shape these columns exist before this revision runs; on a database
migrated from an older schema snapshot they do not. ``ADD COLUMN IF NOT
EXISTS`` (with the foreign key inlined, so it is created in the same
statement as the column and never touched again once the column exists)
makes this revision a no-op either way, matching
``20261002_vendor_legal_reg_idx``'s and ``20260930_capability_backlinks``'s
own idiom rather than inventing a new one.

Revision ID: 20261004_arb_review_source_cols
Revises: 20261004_evt_default
Create Date: 2026-10-04
"""
from alembic import op
from sqlalchemy import text

revision = "20261004_arb_review_source_cols"
down_revision = "20261004_evt_default"
branch_labels = None
depends_on = None

# (table, self-referencing FK target) -- both tables get the identical
# five-column shape; only the table a row can "retire into" differs.
_TABLES = (
    ("arb_review_cycles", "arb_review_cycles"),
    ("arb_review_items", "arb_review_items"),
)


def upgrade():
    bind = op.get_bind()
    for table, retire_target in _TABLES:
        bind.execute(text(
            f'ALTER TABLE "{table}" '
            f'ADD COLUMN IF NOT EXISTS source_table VARCHAR(128)'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" '
            f'ADD COLUMN IF NOT EXISTS source_id VARCHAR(255)'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" '
            f'ADD COLUMN IF NOT EXISTS source_org_id INTEGER '
            f'REFERENCES organizations(id) ON DELETE SET NULL'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" '
            f'ADD COLUMN IF NOT EXISTS source_checksum VARCHAR(64)'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" '
            f'ADD COLUMN IF NOT EXISTS retired_into_id BIGINT '
            f'REFERENCES "{retire_target}"(id) ON DELETE SET NULL'
        ))

        # Matches the model's own index=True on source_table/source_id.
        # source_org_id and retired_into_id are plain FKs with no separate
        # index=True on the model, so none is created here either.
        bind.execute(text(
            f'CREATE INDEX IF NOT EXISTS ix_{table}_source_table '
            f'ON "{table}" (source_table)'
        ))
        bind.execute(text(
            f'CREATE INDEX IF NOT EXISTS ix_{table}_source_id '
            f'ON "{table}" (source_id)'
        ))


def downgrade():
    bind = op.get_bind()
    for table, _retire_target in _TABLES:
        bind.execute(text(f'DROP INDEX IF EXISTS ix_{table}_source_id'))
        bind.execute(text(f'DROP INDEX IF EXISTS ix_{table}_source_table'))

        bind.execute(text(
            f'ALTER TABLE "{table}" '
            f'DROP CONSTRAINT IF EXISTS {table}_retired_into_id_fkey'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS retired_into_id'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS source_checksum'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" '
            f'DROP CONSTRAINT IF EXISTS {table}_source_org_id_fkey'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS source_org_id'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS source_id'
        ))
        bind.execute(text(
            f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS source_table'
        ))

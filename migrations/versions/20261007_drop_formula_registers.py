"""Drop formula_registers (R1-B34/#407) -- superseded by #251's
ScoringConfiguration-based formula register (ADR 0008: one store per
concept, not two).

formula_registers (app/models/formula_register.py, removed in this same
change) duplicated the exact concept #251 solves by extending the existing
ScoringConfiguration table with owner/reviewer/effective_date/formula_version
columns. Both landed the same day; formula_registers was never given a
writer any real user reached (its own blueprint/UI is removed alongside
this migration), so it is refused loudly here rather than dropped blind:
a non-empty table means someone did write to it and the drop must be
reconsidered, not silently discarded.

Revision ID: 20261007_drop_formula_registers
Revises: 20261006_owner_element_ref
Create Date: 2026-10-07
"""
from alembic import op
from sqlalchemy import text

revision = "20261007_drop_formula_registers"
down_revision = "20261006_owner_element_ref"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    if not bind.execute(text("SELECT to_regclass('formula_registers')")).scalar():
        return  # already gone -- idempotent

    row_count = bind.execute(text("SELECT count(*) FROM formula_registers")).scalar()
    if row_count:
        raise RuntimeError(
            f"formula_registers has {row_count} row(s) -- refusing to drop a table "
            "someone actually wrote to. Resolve by hand (migrate the rows into "
            "ScoringConfiguration, or confirm they are safe to discard) before "
            "re-running this migration."
        )

    op.drop_index("ix_formula_registers_formula_key", table_name="formula_registers", if_exists=True)
    op.drop_index("ix_formula_registers_org_key_active", table_name="formula_registers", if_exists=True)
    op.drop_table("formula_registers", if_exists=True)


def downgrade():
    # The table's own creation migration (20261006_formula_registers) still
    # has the original schema; this is a one-way retirement, not reversible
    # by re-running this file. Re-add the table by hand if ever needed.
    pass

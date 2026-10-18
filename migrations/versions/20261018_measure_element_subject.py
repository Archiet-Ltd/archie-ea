"""Let a measure definition be bound to any model element (objectives and key results).

Expand step. A measure definition keeps its programme outcome binding but gains
an optional element subject, so a goal element can carry key results without a
new table. outcome_commitment_id becomes nullable; a check keeps every measure
bound to at least one of the two subjects. benefit_id points at the canonical
Benefit that carries the measure's measurements when it has no programme
outcome. Existing rows are untouched. The down step restores NOT NULL and
refuses, changing nothing, while any row has no outcome binding.

Revision ID: 20261018_measure_subject
Revises: 20261016_compliance_org_rls
Create Date: 2026-10-18
"""
from alembic import op
from sqlalchemy import text

from app.commands.schema_migrations import relax_not_null, tighten_not_null

revision = "20261018_measure_subject"
down_revision = "20261016_compliance_org_rls"
branch_labels = None
depends_on = None

TABLE, COLUMN = "measure_definitions", "outcome_commitment_id"


def upgrade():
    bind = op.get_bind()
    relax_not_null(bind, TABLE, COLUMN)
    bind.execute(text(
        "ALTER TABLE measure_definitions ADD COLUMN IF NOT EXISTS subject_element_id INTEGER "
        "REFERENCES archimate_elements(id) ON DELETE RESTRICT"
    ))
    bind.execute(text(
        "ALTER TABLE measure_definitions ADD COLUMN IF NOT EXISTS benefit_id INTEGER "
        "REFERENCES benefits(id) ON DELETE RESTRICT"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_measure_definitions_subject_element_id "
        "ON measure_definitions (subject_element_id)"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_measure_definitions_benefit_id "
        "ON measure_definitions (benefit_id)"
    ))
    bind.execute(text(
        "ALTER TABLE measure_definitions DROP CONSTRAINT IF EXISTS ck_measure_definition_subject"
    ))
    bind.execute(text(
        "ALTER TABLE measure_definitions ADD CONSTRAINT ck_measure_definition_subject "
        "CHECK (outcome_commitment_id IS NOT NULL OR subject_element_id IS NOT NULL)"
    ))


def downgrade():
    bind = op.get_bind()
    orphans = bind.execute(text(
        "SELECT count(*) FROM measure_definitions WHERE outcome_commitment_id IS NULL"
    )).scalar()
    if orphans:
        raise RuntimeError(
            f"{orphans} measure definition(s) have no programme outcome; nothing changed"
        )
    bind.execute(text(
        "ALTER TABLE measure_definitions DROP CONSTRAINT IF EXISTS ck_measure_definition_subject"
    ))
    bind.execute(text("DROP INDEX IF EXISTS ix_measure_definitions_benefit_id"))
    bind.execute(text("DROP INDEX IF EXISTS ix_measure_definitions_subject_element_id"))
    bind.execute(text("ALTER TABLE measure_definitions DROP COLUMN IF EXISTS benefit_id"))
    bind.execute(text("ALTER TABLE measure_definitions DROP COLUMN IF EXISTS subject_element_id"))
    tighten_not_null(bind, TABLE, COLUMN)

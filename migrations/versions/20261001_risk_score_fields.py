"""Give risks separate inherent/residual scores; point solution_risks at them.

Expand step, both changes additive and nullable:

* four new nullable columns on ``risks`` -- inherent_likelihood,
  inherent_impact, residual_likelihood, residual_impact. Existing rows are
  untouched (NULL until a score is recorded via risk_service.set_risk_score);
  every existing reader of the pre-existing likelihood/impact pair is
  unaffected.
* one new nullable column on ``solution_risks`` -- retired_into_risk_id,
  a pointer to the canonical Risk a row was merged into by
  ``flask backfill-solution-risk-merge``
  (app/commands/backfill_solution_risk_merge.py). NULL until that command has
  processed the row.

``reconcile-schema`` could add these as plain nullable columns on its own,
but schema changes for this consolidation go in as a versioned revision
rather than relying on the drift detector alone, per the lead's ruling on the
approval-queue/decision-register consolidations. The down step refuses while
any row holds a value, since dropping would discard a recorded score or an
already-completed merge pointer.

Revision ID: 20261001_risk_score_fields
Revises: 20260930_capability_backlinks
Create Date: 2026-10-01
"""
from alembic import op
from sqlalchemy import text

from app.commands.schema_migrations import ContractBlocked

# alembic_version.version_num is VARCHAR(32) (set by the baseline migration
# system in migrations/env.py, not by this revision) -- keep this id at or
# under that length, the same constraint every revision after the baseline
# already satisfies.
revision = "20261001_risk_score_fields"
down_revision = "20260930_capability_backlinks"
branch_labels = None
depends_on = None

_RISK_SCORE_COLUMNS = (
    "inherent_likelihood",
    "inherent_impact",
    "residual_likelihood",
    "residual_impact",
)
_SOLUTION_RISK_POINTER_COLUMN = "retired_into_risk_id"


def upgrade():
    bind = op.get_bind()
    for column in _RISK_SCORE_COLUMNS:
        bind.execute(text(
            f'ALTER TABLE risks ADD COLUMN IF NOT EXISTS "{column}" INTEGER'
        ))
    bind.execute(text(
        "ALTER TABLE solution_risks "
        f'ADD COLUMN IF NOT EXISTS "{_SOLUTION_RISK_POINTER_COLUMN}" INTEGER '
        "REFERENCES risks(id) ON DELETE SET NULL"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_solution_risks_retired_into_risk_id "
        f"ON solution_risks ({_SOLUTION_RISK_POINTER_COLUMN})"
    ))


def downgrade():
    bind = op.get_bind()
    for column in _RISK_SCORE_COLUMNS:
        populated = bind.execute(
            text(f'SELECT count(*) FROM risks WHERE "{column}" IS NOT NULL')
        ).scalar()
        if populated:
            raise ContractBlocked(
                f"risks.{column}: {populated} row(s) hold a recorded score; "
                "downgrading would discard them. Forward-fix instead."
            )
    pointed = bind.execute(
        text(
            "SELECT count(*) FROM solution_risks "
            f'WHERE "{_SOLUTION_RISK_POINTER_COLUMN}" IS NOT NULL'
        )
    ).scalar()
    if pointed:
        raise ContractBlocked(
            f"solution_risks.{_SOLUTION_RISK_POINTER_COLUMN}: {pointed} row(s) "
            "already point at a merged risk; downgrading would discard that "
            "record. Forward-fix instead."
        )
    bind.execute(text(
        "DROP INDEX IF EXISTS ix_solution_risks_retired_into_risk_id"
    ))
    bind.execute(text(
        f'ALTER TABLE solution_risks DROP COLUMN IF EXISTS "{_SOLUTION_RISK_POINTER_COLUMN}"'
    ))
    for column in _RISK_SCORE_COLUMNS:
        bind.execute(text(f'ALTER TABLE risks DROP COLUMN IF EXISTS "{column}"'))

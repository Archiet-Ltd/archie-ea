"""Add the review-board/AI-authoring columns to architecture_decisions, and
repoint solution_adr_links.adr_id at it instead of architecture_decision_records.

architecture_decisions is now the only writer for every decision creation
path (lead ruling): the AI chat, workbench and
solution-options-advisor paths that used to insert into
architecture_decision_records first and pair afterwards now write the
canonical row directly. Four fields those paths set had no column there
yet: affected_systems, assumptions, estimated_effort, business_value, and a
free-text decided_by_label for an AI actor that is not a users.id row.

solution_adr_links.adr_id's foreign key is repointed from
architecture_decision_records.id to architecture_decisions.id for the same
reason: the workbench paths that create a link alongside the decision need
it to point at the row that now actually gets created. This feature has
not shipped (this whole consolidation is still mid-review), so there is no
production data under the old relationship to migrate.

Revision ID: 20261001_adr_canonical_cols
Revises: 20260930_capability_backlinks
Create Date: 2026-10-01
"""
from alembic import op
from sqlalchemy import text

revision = "20261001_adr_canonical_cols"
down_revision = "20261001_risk_score_fields"
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    ("affected_systems", "JSON"),
    ("assumptions", "TEXT"),
    ("estimated_effort", "VARCHAR(50)"),
    ("business_value", "VARCHAR(50)"),
    ("decided_by_label", "TEXT"),
)

_FK_NAME = "solution_adr_links_adr_id_fkey"


def upgrade():
    bind = op.get_bind()

    # IF NOT EXISTS, not op.add_column: these five columns are also declared
    # directly on the ArchitectureDecision model (ADR-0002's migration-freeze
    # convention for this file predates this revision), so reconcile-schema's
    # own ADD COLUMN IF NOT EXISTS sweep can already have added them by the
    # time this revision runs in the same CI setup -- a bare op.add_column()
    # then fails with "column already exists" (seen in CI on this exact
    # migration). Matches 20260930_capability_backlinks.py's own idiom.
    for column, coltype in _NEW_COLUMNS:
        bind.execute(text(
            f'ALTER TABLE architecture_decisions ADD COLUMN IF NOT EXISTS {column} {coltype}'
        ))

    # Postgres has no "ADD CONSTRAINT IF NOT EXISTS" and no "DROP CONSTRAINT
    # ... ADD CONSTRAINT" in one statement, so drop whichever FK is currently
    # on adr_id (old or new -- a prior partial run of this revision may have
    # already repointed it) and recreate it, guarded by a DO block so a
    # second run is a no-op rather than a duplicate-object error, matching
    # 20260930_capability_backlinks.py's own guard. to_regclass makes this
    # tolerant of solution_adr_links not existing yet on a database that has
    # not run that table's own migration/reconcile-schema pass.
    bind.execute(text(
        f"""
        DO $repoint_adr_fk$
        BEGIN
          IF to_regclass('solution_adr_links') IS NOT NULL THEN
            IF EXISTS (
              SELECT 1 FROM pg_constraint
              WHERE conrelid = to_regclass('solution_adr_links')
                AND conname = '{_FK_NAME}'
                AND confrelid = to_regclass('architecture_decision_records')
            ) THEN
              ALTER TABLE solution_adr_links DROP CONSTRAINT {_FK_NAME};
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM pg_constraint
              WHERE conrelid = to_regclass('solution_adr_links')
                AND conname = '{_FK_NAME}'
                AND confrelid = to_regclass('architecture_decisions')
            ) THEN
              ALTER TABLE solution_adr_links
                ADD CONSTRAINT {_FK_NAME}
                FOREIGN KEY (adr_id) REFERENCES architecture_decisions(id);
            END IF;
          END IF;
        END
        $repoint_adr_fk$
        """
    ))


def downgrade():
    bind = op.get_bind()

    bind.execute(text(
        f"""
        DO $repoint_adr_fk_back$
        BEGIN
          IF to_regclass('solution_adr_links') IS NOT NULL THEN
            IF EXISTS (
              SELECT 1 FROM pg_constraint
              WHERE conrelid = to_regclass('solution_adr_links')
                AND conname = '{_FK_NAME}'
                AND confrelid = to_regclass('architecture_decisions')
            ) THEN
              ALTER TABLE solution_adr_links DROP CONSTRAINT {_FK_NAME};
            END IF;
            IF to_regclass('architecture_decision_records') IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM pg_constraint
              WHERE conrelid = to_regclass('solution_adr_links')
                AND conname = '{_FK_NAME}'
                AND confrelid = to_regclass('architecture_decision_records')
            ) THEN
              ALTER TABLE solution_adr_links
                ADD CONSTRAINT {_FK_NAME}
                FOREIGN KEY (adr_id) REFERENCES architecture_decision_records(id);
            END IF;
          END IF;
        END
        $repoint_adr_fk_back$
        """
    ))

    for column, _coltype in reversed(_NEW_COLUMNS):
        bind.execute(text(
            f'ALTER TABLE architecture_decisions DROP COLUMN IF EXISTS {column}'
        ))

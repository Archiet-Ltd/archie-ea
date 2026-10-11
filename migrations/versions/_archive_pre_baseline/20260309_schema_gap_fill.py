"""
Schema gap-fill: add 22 missing columns across 7 tables.

All columns use ADD COLUMN IF NOT EXISTS for idempotent safety.
NOT NULL columns use server_default to protect existing rows.

Revision ID: 20260309_schema_gap_fill
Revises: jira001_kanban_fields
Create Date: 2026-03-09
"""
from alembic import op
import sqlalchemy as sa

revision = '20260309_schema_gap_fill'
down_revision = 'jira001_kanban_fields'
branch_labels = None
depends_on = None


def _add_col_if_not_exists(table, column_sql):
    op.execute(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS {column_sql}')


def upgrade():
    # ----------------------------------------------------------------
    # adm_deliverables — 14 existing rows
    # ----------------------------------------------------------------
    _add_col_if_not_exists('adm_deliverables',
        "is_template BOOLEAN DEFAULT TRUE")
    # phase is NOT NULL in model; use server_default 'B' for existing rows
    _add_col_if_not_exists('adm_deliverables',
        "phase VARCHAR(32) NOT NULL DEFAULT 'B'")

    # ----------------------------------------------------------------
    # arb_review_items — 10 existing rows, all new cols nullable
    # ----------------------------------------------------------------
    _add_col_if_not_exists('arb_review_items',
        "conditions_response JSON")
    _add_col_if_not_exists('arb_review_items',
        "implementation_completed_at TIMESTAMP WITHOUT TIME ZONE")
    _add_col_if_not_exists('arb_review_items',
        "implementation_notes TEXT")
    _add_col_if_not_exists('arb_review_items',
        "implementation_started_at TIMESTAMP WITHOUT TIME ZONE")
    _add_col_if_not_exists('arb_review_items',
        "implementation_status VARCHAR(30) DEFAULT 'not_started'")

    # ----------------------------------------------------------------
    # architecture_decisions — 0 existing rows
    # ----------------------------------------------------------------
    _add_col_if_not_exists('architecture_decisions',
        "authority_level VARCHAR(30) DEFAULT 'enterprise_arb'")
    _add_col_if_not_exists('architecture_decisions',
        "decision_type VARCHAR(30)")
    # enterprise_level is NOT NULL; 0 rows so server_default safe to keep
    _add_col_if_not_exists('architecture_decisions',
        "enterprise_level BOOLEAN NOT NULL DEFAULT TRUE")
    _add_col_if_not_exists('architecture_decisions',
        "horizon VARCHAR(20) DEFAULT 'strategic'")
    _add_col_if_not_exists('architecture_decisions',
        "solution_id INTEGER REFERENCES solutions(id) ON DELETE SET NULL")
    _add_col_if_not_exists('architecture_decisions',
        "superseded_by_id INTEGER REFERENCES architecture_decisions(id) ON DELETE SET NULL")
    _add_col_if_not_exists('architecture_decisions',
        "valid_from TIMESTAMP WITHOUT TIME ZONE")
    _add_col_if_not_exists('architecture_decisions',
        "valid_until TIMESTAMP WITHOUT TIME ZONE")

    # ----------------------------------------------------------------
    # solution_assessments_sad — 2 existing rows
    # finding is NOT NULL; use server_default '' for existing rows
    # ----------------------------------------------------------------
    _add_col_if_not_exists('solution_assessments_sad',
        "finding TEXT NOT NULL DEFAULT ''")
    _add_col_if_not_exists('solution_assessments_sad',
        "recommendation TEXT")

    # ----------------------------------------------------------------
    # solution_principles_sad — 2 existing rows, nullable
    # ----------------------------------------------------------------
    _add_col_if_not_exists('solution_principles_sad',
        "statement TEXT")

    # ----------------------------------------------------------------
    # solution_templates — 0 existing rows
    # ----------------------------------------------------------------
    _add_col_if_not_exists('solution_templates',
        "source_solution_id INTEGER REFERENCES solutions(id) ON DELETE SET NULL")

    # ----------------------------------------------------------------
    # work_packages — 15 existing rows, all new cols nullable
    # ----------------------------------------------------------------
    _add_col_if_not_exists('work_packages',
        "capability_id INTEGER REFERENCES unified_capabilities(id) ON DELETE SET NULL")
    _add_col_if_not_exists('work_packages',
        "element_type VARCHAR(50)")
    _add_col_if_not_exists('work_packages',
        "plateau_id INTEGER REFERENCES plateaus(id) ON DELETE SET NULL")


def downgrade():
    # Remove all added columns (safe rollback)
    cols = [
        ('adm_deliverables', 'is_template'),
        ('adm_deliverables', 'phase'),
        ('arb_review_items', 'conditions_response'),
        ('arb_review_items', 'implementation_completed_at'),
        ('arb_review_items', 'implementation_notes'),
        ('arb_review_items', 'implementation_started_at'),
        ('arb_review_items', 'implementation_status'),
        ('architecture_decisions', 'authority_level'),
        ('architecture_decisions', 'decision_type'),
        ('architecture_decisions', 'enterprise_level'),
        ('architecture_decisions', 'horizon'),
        ('architecture_decisions', 'solution_id'),
        ('architecture_decisions', 'superseded_by_id'),
        ('architecture_decisions', 'valid_from'),
        ('architecture_decisions', 'valid_until'),
        ('solution_assessments_sad', 'finding'),
        ('solution_assessments_sad', 'recommendation'),
        ('solution_principles_sad', 'statement'),
        ('solution_templates', 'source_solution_id'),
        ('work_packages', 'capability_id'),
        ('work_packages', 'element_type'),
        ('work_packages', 'plateau_id'),
    ]
    for table, col in cols:
        op.execute(f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS "{col}"')

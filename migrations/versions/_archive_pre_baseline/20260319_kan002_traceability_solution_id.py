"""Add solution_id column to TraceabilityLink for journey scoping.

Revision ID: 20260319_kan002_traceability_solution_id
Revises: 20260312_api_settings_multi_key
Create Date: 2026-03-19

This migration adds a nullable solution_id column to traceability_links table
to scope traceability links within a specific solution context. This enables
journey-driven traceability where links are derived from solution workflows.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20260319_kan002_traceability_solution_id'
down_revision = '20260312_api_settings_multi_key'
branch_labels = None
depends_on = None


def _column_exists(table, column):
    """Check if a column exists in a table."""
    try:
        bind = op.get_bind()
        inspector = inspect(bind)
        cols = [c['name'] for c in inspector.get_columns(table)]
        return column in cols
    except Exception:
        return False


def upgrade():
    # Add solution_id column if it doesn't exist
    if not _column_exists('traceability_links', 'solution_id'):
        op.add_column(
            'traceability_links',
            sa.Column(
                'solution_id',
                sa.Integer(),
                nullable=True,
            )
        )

        # Add foreign key constraint
        op.create_foreign_key(
            'fk_traceability_links_solution_id',
            'traceability_links',
            'solutions',
            ['solution_id'],
            ['id'],
            ondelete='CASCADE'
        )

        # Add index for performance
        op.create_index(
            'ix_traceability_links_solution_id',
            'traceability_links',
            ['solution_id'],
            unique=False
        )


def downgrade():
    # Remove the index
    if _column_exists('traceability_links', 'solution_id'):
        try:
            op.drop_index(
                'ix_traceability_links_solution_id',
                table_name='traceability_links'
            )
        except Exception:
            pass

        # Remove the foreign key constraint
        try:
            op.drop_constraint(
                'fk_traceability_links_solution_id',
                'traceability_links'
            )
        except Exception:
            pass

        # Drop the column
        op.drop_column('traceability_links', 'solution_id')

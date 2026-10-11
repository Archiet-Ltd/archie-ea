"""add archimate audit log, arb implementation tracking columns

Revision ID: 20260311_enf007_model_changes
Revises: 20260303_add_workflow_orchestration, ia009_scenario, phase5_governance_v1, prod021_kanban_fk_indexes, wft069_workflow_instance_archimate_elements
Create Date: 2026-03-11 10:25:00

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260311_enf007_model_changes'
down_revision = (
    '20260303_add_workflow_orchestration',
    'ia009_scenario',
    'phase5_governance_v1',
    'prod021_kanban_fk_indexes',
    'wft069_workflow_instance_archimate_elements',
)
branch_labels = None
depends_on = None


def _column_exists(table, column):
    """Check if a column exists in a table."""
    from sqlalchemy import inspect
    from alembic import op as _op
    conn = _op.get_bind()
    insp = inspect(conn)
    cols = [c['name'] for c in insp.get_columns(table)]
    return column in cols


def _table_exists(table):
    """Check if a table exists."""
    from sqlalchemy import inspect
    from alembic import op as _op
    conn = _op.get_bind()
    insp = inspect(conn)
    return table in insp.get_table_names()


def upgrade():
    # Create archimate_audit_logs table if it doesn't already exist
    if not _table_exists('archimate_audit_logs'):
        op.create_table(
            'archimate_audit_logs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('viewpoint_id', sa.Integer(), nullable=True),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('action', sa.String(length=100), nullable=False),
            sa.Column('entity_type', sa.String(length=100), nullable=True),
            sa.Column('entity_id', sa.Integer(), nullable=True),
            sa.Column('entity_name', sa.String(length=255), nullable=True),
            sa.Column('old_value', sa.Text(), nullable=True),
            sa.Column('new_value', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.ForeignKeyConstraint(['viewpoint_id'], ['archimate_viewpoints.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            op.f('ix_archimate_audit_logs_created_at'),
            'archimate_audit_logs', ['created_at'], unique=False
        )

    # Add ARBReviewItem implementation tracking columns (idempotent)
    arb_new_cols = [
        ('implementation_status', sa.Column('implementation_status', sa.String(length=30),
                                            nullable=True, server_default='not_started')),
        ('implementation_notes', sa.Column('implementation_notes', sa.Text(), nullable=True)),
        ('implementation_started_at', sa.Column('implementation_started_at', sa.DateTime(), nullable=True)),
        ('implementation_completed_at', sa.Column('implementation_completed_at', sa.DateTime(), nullable=True)),
        ('conditions_response', sa.Column('conditions_response', sa.JSON(), nullable=True)),
    ]
    for col_name, col_def in arb_new_cols:
        if not _column_exists('arb_review_items', col_name):
            op.add_column('arb_review_items', col_def)


def downgrade():
    # Remove ARBReviewItem implementation tracking columns
    with op.batch_alter_table('arb_review_items') as batch_op:
        batch_op.drop_column('conditions_response')
        batch_op.drop_column('implementation_completed_at')
        batch_op.drop_column('implementation_started_at')
        batch_op.drop_column('implementation_notes')
        batch_op.drop_column('implementation_status')

    # Drop archimate_audit_logs table
    op.drop_index(
        op.f('ix_archimate_audit_logs_created_at'),
        table_name='archimate_audit_logs'
    )
    op.drop_table('archimate_audit_logs')

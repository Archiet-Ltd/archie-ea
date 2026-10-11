"""add_solution_ai_reasoning_state_model

Revision ID: 20260302_add_solution_ai_reasoning
Revises: 
Create Date: 2026-03-02 21:55:00.000000

Add SolutionAIReasoningState table for persistent AI reasoning audit trail.
"""

from alembic import op
import sqlalchemy as sa


revision = '20260302_add_solution_ai_reasoning'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create solution_ai_reasoning_states table."""
    op.create_table(
        'solution_ai_reasoning_states',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solution_id', sa.Integer(), nullable=False),
        sa.Column('adm_phase', sa.String(length=1), nullable=False, server_default='A'),
        sa.Column('context_snapshot', sa.JSON(), nullable=True),
        sa.Column('reasoning_trace', sa.JSON(), nullable=True),
        sa.Column('suggestions', sa.JSON(), nullable=True),
        sa.Column('selected_suggestion_id', sa.String(length=255), nullable=True),
        sa.Column('user_feedback', sa.String(length=20), nullable=True),
        sa.Column('feedback_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['solution_id'], ['solutions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create index for efficient lookups
    op.create_index(
        'ix_solution_ai_reasoning_states_solution_id',
        'solution_ai_reasoning_states',
        ['solution_id']
    )
    
    # Create index for finding unseen reasoning states
    op.create_index(
        'ix_solution_ai_reasoning_states_created_at',
        'solution_ai_reasoning_states',
        ['created_at']
    )


def downgrade():
    """Drop solution_ai_reasoning_states table."""
    op.drop_index('ix_solution_ai_reasoning_states_created_at', table_name='solution_ai_reasoning_states')
    op.drop_index('ix_solution_ai_reasoning_states_solution_id', table_name='solution_ai_reasoning_states')
    op.drop_table('solution_ai_reasoning_states')

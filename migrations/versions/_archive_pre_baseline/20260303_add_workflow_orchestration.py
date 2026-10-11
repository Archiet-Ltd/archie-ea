"""
Phase 4 Workflow Orchestration migration - Add solution_workflows and solution_workflow_tasks tables.

Revision ID: 20260303_add_workflow_orchestration
Revises: 20260302_add_solution_ai_reasoning
Create Date: 2026-03-03 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260303_add_workflow_orchestration'
down_revision = '20260302_add_solution_ai_reasoning'
branch_labels = None
depends_on = None


def upgrade():
    # Create solution_workflow_tasks table first (no foreign keys yet)
    op.create_table(
        'solution_workflow_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workflow_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.String(50), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        
        # Duration & timing
        sa.Column('duration_days', sa.Integer(), nullable=False),
        sa.Column('earliest_start', sa.Integer(), default=0),
        sa.Column('earliest_finish', sa.Integer(), default=0),
        sa.Column('latest_start', sa.Integer(), default=0),
        sa.Column('latest_finish', sa.Integer(), default=0),
        sa.Column('slack', sa.Integer(), default=0),
        
        # Relationships
        sa.Column('dependencies', sa.JSON(), default=list),
        sa.Column('parallelizable_with', sa.JSON(), default=list),
        
        # Resource & phase
        sa.Column('phase_name', sa.String(100), nullable=True),
        sa.Column('phase_sequence', sa.Integer(), nullable=True),
        sa.Column('assignee_role', sa.String(100), nullable=True),
        
        # Criticality
        sa.Column('is_critical', sa.Boolean(), default=False),
        sa.Column('buffer_recommendation', sa.Integer(), default=0),
        
        # Estimation confidence
        sa.Column('confidence_score', sa.Float(), default=0.8),
        sa.Column('estimation_method', sa.String(50), nullable=True),
        sa.Column('risk_adjustment', sa.Float(), default=1.0),
        
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_solution_workflow_tasks_workflow_id', 'workflow_id'),
        sa.Index('ix_solution_workflow_tasks_phase', 'phase_name'),
        sa.Index('ix_solution_workflow_tasks_critical', 'is_critical'),
    )
    
    # Create solution_workflows table
    op.create_table(
        'solution_workflows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solution_id', sa.Integer(), nullable=False),
        
        # Timeline metrics
        sa.Column('total_duration_days', sa.Integer(), nullable=False),
        sa.Column('critical_path_length', sa.Integer(), nullable=False),
        sa.Column('project_start_date', sa.DateTime(), nullable=True),
        sa.Column('project_end_date', sa.DateTime(), nullable=True),
        
        # Structure
        sa.Column('num_tasks', sa.Integer(), default=0),
        sa.Column('num_phases', sa.Integer(), default=0),
        sa.Column('num_critical_tasks', sa.Integer(), default=0),
        
        # Parallelization metrics
        sa.Column('avg_parallelization_factor', sa.Float(), default=1.0),
        sa.Column('min_team_size', sa.Integer(), nullable=True),
        sa.Column('max_team_size', sa.Integer(), nullable=True),
        
        # Risk assessment
        sa.Column('total_buffer_days', sa.Integer(), default=0),
        sa.Column('risk_confidence', sa.Float(), default=0.85),
        sa.Column('major_risks', sa.JSON(), default=list),
        
        # Phases captured
        sa.Column('phases', sa.JSON(), default=list),
        
        # Metadata
        sa.Column('generation_method', sa.String(50), nullable=True),
        sa.Column('version', sa.Integer(), default=1),
        sa.Column('is_locked', sa.Boolean(), default=False),
        
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['solution_id'], ['solutions.id'], ondelete='CASCADE'),
        sa.Index('ix_solution_workflows_solution_id', 'solution_id'),
        sa.Index('ix_solution_workflows_created_at', 'created_at'),
        sa.Index('ix_solution_workflows_critical', 'critical_path_length'),
    )
    
    # Add foreign key constraint for workflow_tasks
    op.create_foreign_key(
        'fk_solution_workflow_tasks_workflow_id',
        'solution_workflow_tasks',
        'solution_workflows',
        ['workflow_id'],
        ['id'],
        ondelete='CASCADE'
    )


def downgrade():
    # Drop tables in reverse order
    op.drop_constraint('fk_solution_workflow_tasks_workflow_id', 'solution_workflow_tasks')
    op.drop_table('solution_workflow_tasks')
    op.drop_table('solution_workflows')

"""
Migration: Add Phase 5A & 5B governance, execution, issue tracking, and learning tables.

Revision ID: phase5_governance_v1
Revises: None (orphan branch - tables created via db.create_all before alembic tracking)
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'phase5_governance_v1'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # SolutionVersion table
    op.create_table(
        'solution_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solution_id', sa.Integer(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('change_summary', sa.Text(), nullable=True),
        sa.Column('change_delta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('approval_status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('approval_notes', sa.Text(), nullable=True),
        sa.Column('approved_by_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('approval_conditions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('solution_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['solution_id'], ['solution.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['approved_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_solution_version_solution_id', 'solution_versions', ['solution_id'])
    op.create_index('idx_solution_version_created_at', 'solution_versions', ['created_at'])
    op.create_index('idx_solution_version_approval_status', 'solution_versions', ['approval_status'])
    
    # SolutionExecutionTracking table
    op.create_table(
        'solution_execution_tracking',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solution_id', sa.Integer(), nullable=False),
        sa.Column('workflow_task_id', sa.Integer(), nullable=True),
        sa.Column('percent_complete', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('actual_start_date', sa.Date(), nullable=True),
        sa.Column('actual_end_date', sa.Date(), nullable=True),
        sa.Column('planned_duration_days', sa.Integer(), nullable=True),
        sa.Column('actual_duration_days', sa.Integer(), nullable=True),
        sa.Column('planned_end_date', sa.Date(), nullable=True),
        sa.Column('variance_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('variance_percentage', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('status', sa.String(50), nullable=False, server_default='on_track'),
        sa.Column('status_reason', sa.Text(), nullable=True),
        sa.Column('realized_risks', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('last_updated_by_id', sa.Integer(), nullable=True),
        sa.Column('last_updated_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['solution_id'], ['solution.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workflow_task_id'], ['solution_workflow_task.id']),
        sa.ForeignKeyConstraint(['last_updated_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_exec_tracking_solution_id', 'solution_execution_tracking', ['solution_id'])
    op.create_index('idx_exec_tracking_status', 'solution_execution_tracking', ['status'])
    op.create_index('idx_exec_tracking_updated', 'solution_execution_tracking', ['last_updated_at'])
    
    # SolutionIssue table
    op.create_table(
        'solution_issues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solution_id', sa.Integer(), nullable=False),
        sa.Column('workflow_task_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('severity', sa.String(10), nullable=False, server_default='P3'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='999'),
        sa.Column('status', sa.String(50), nullable=False, server_default='open'),
        sa.Column('impact_area', sa.String(100), nullable=True),
        sa.Column('estimated_impact', sa.Text(), nullable=True),
        sa.Column('assigned_to_id', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('escalated_to_id', sa.Integer(), nullable=True),
        sa.Column('escalation_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('escalation_reason', sa.Text(), nullable=True),
        sa.Column('escalated_at', sa.DateTime(), nullable=True),
        sa.Column('resolution_plan', sa.Text(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('target_resolution_date', sa.Date(), nullable=True),
        sa.Column('auto_escalate_if_not_resolved_by', sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(['solution_id'], ['solution.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workflow_task_id'], ['solution_workflow_task.id']),
        sa.ForeignKeyConstraint(['assigned_to_id'], ['user.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['escalated_to_id'], ['user.id']),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_issue_solution_id', 'solution_issues', ['solution_id'])
    op.create_index('idx_issue_severity', 'solution_issues', ['severity'])
    op.create_index('idx_issue_status', 'solution_issues', ['status'])
    op.create_index('idx_issue_created_at', 'solution_issues', ['created_at'])
    
    # SolutionARBReview table
    op.create_table(
        'solution_arb_reviews',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solution_id', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.Integer(), nullable=True),
        sa.Column('submitted_by_id', sa.Integer(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('submission_version', sa.String(50), nullable=True),
        sa.Column('arb_decision', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('arb_decision_reason', sa.Text(), nullable=True),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('arb_attendees', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('conditions', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('compliance_areas_reviewed', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('compliance_notes', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('next_steps', sa.Text(), nullable=True),
        sa.Column('next_review_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['solution_id'], ['solution.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['version_id'], ['solution_versions.id']),
        sa.ForeignKeyConstraint(['submitted_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['decided_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_arb_solution_id', 'solution_arb_reviews', ['solution_id'])
    op.create_index('idx_arb_decision', 'solution_arb_reviews', ['arb_decision'])
    op.create_index('idx_arb_submitted_at', 'solution_arb_reviews', ['submitted_at'])
    
    # SolutionOutcomeTracking table
    op.create_table(
        'solution_outcome_tracking',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('solution_id', sa.Integer(), nullable=False),
        sa.Column('project_completed_at', sa.DateTime(), nullable=True),
        sa.Column('go_live_date', sa.Date(), nullable=True),
        sa.Column('predicted_duration_weeks', sa.Float(), nullable=True),
        sa.Column('actual_duration_weeks', sa.Float(), nullable=True),
        sa.Column('timeline_accuracy_percentage', sa.Float(), nullable=True),
        sa.Column('predicted_cost_usd', sa.Float(), nullable=True),
        sa.Column('actual_cost_usd', sa.Float(), nullable=True),
        sa.Column('cost_accuracy_percentage', sa.Float(), nullable=True),
        sa.Column('vendor_performance', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('predicted_risks', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('realized_risks', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('unforecast_risks', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('risk_accuracy_percentage', sa.Float(), nullable=True),
        sa.Column('lessons_learned', sa.Text(), nullable=True),
        sa.Column('what_went_well', sa.Text(), nullable=True),
        sa.Column('what_to_improve', sa.Text(), nullable=True),
        sa.Column('business_value_realized', sa.Text(), nullable=True),
        sa.Column('estimated_business_value_usd', sa.Float(), nullable=True),
        sa.Column('roi_percentage', sa.Float(), nullable=True),
        sa.Column('process_insights', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('recorded_by_id', sa.Integer(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.Column('used_for_retraining', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('retraining_version', sa.String(50), nullable=True),
        sa.ForeignKeyConstraint(['solution_id'], ['solution.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['recorded_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_outcome_solution_id', 'solution_outcome_tracking', ['solution_id'])
    op.create_index('idx_outcome_recorded_at', 'solution_outcome_tracking', ['recorded_at'])
    op.create_index('idx_outcome_used_for_retraining', 'solution_outcome_tracking', ['used_for_retraining'])


def downgrade():
    op.drop_index('idx_outcome_used_for_retraining', 'solution_outcome_tracking')
    op.drop_index('idx_outcome_recorded_at', 'solution_outcome_tracking')
    op.drop_index('idx_outcome_solution_id', 'solution_outcome_tracking')
    op.drop_table('solution_outcome_tracking')
    
    op.drop_index('idx_arb_submitted_at', 'solution_arb_reviews')
    op.drop_index('idx_arb_decision', 'solution_arb_reviews')
    op.drop_index('idx_arb_solution_id', 'solution_arb_reviews')
    op.drop_table('solution_arb_reviews')
    
    op.drop_index('idx_issue_created_at', 'solution_issues')
    op.drop_index('idx_issue_status', 'solution_issues')
    op.drop_index('idx_issue_severity', 'solution_issues')
    op.drop_index('idx_issue_solution_id', 'solution_issues')
    op.drop_table('solution_issues')
    
    op.drop_index('idx_exec_tracking_updated', 'solution_execution_tracking')
    op.drop_index('idx_exec_tracking_status', 'solution_execution_tracking')
    op.drop_index('idx_exec_tracking_solution_id', 'solution_execution_tracking')
    op.drop_table('solution_execution_tracking')
    
    op.drop_index('idx_solution_version_approval_status', 'solution_versions')
    op.drop_index('idx_solution_version_created_at', 'solution_versions')
    op.drop_index('idx_solution_version_solution_id', 'solution_versions')
    op.drop_table('solution_versions')

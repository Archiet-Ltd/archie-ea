"""Add ADM Audit Log tables

Migration for ADM-P1-4: Add comprehensive audit trail for all ADM activities

Revision ID: 20260206_adm_audit_log
Revises: 20260206_adm_kanban_junctions
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = '20260206_adm_audit_log'
down_revision = '20260206_adm_kanban_junctions'
branch_labels = None
depends_on = None


def upgrade():
    # Create adm_audit_logs table
    op.create_table('adm_audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('audit_id', sa.String(length=100), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('entity_reference', sa.String(length=255), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('action_description', sa.Text(), nullable=True),
        sa.Column('old_values', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('new_values', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('changed_fields', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('board_id', sa.Integer(), nullable=True),
        sa.Column('card_id', sa.Integer(), nullable=True),
        sa.Column('approval_id', sa.Integer(), nullable=True),
        sa.Column('phase_id', sa.Integer(), nullable=True),
        sa.Column('source_phase_id', sa.Integer(), nullable=True),
        sa.Column('target_phase_id', sa.Integer(), nullable=True),
        sa.Column('source_phase_code', sa.String(length=10), nullable=True),
        sa.Column('target_phase_code', sa.String(length=10), nullable=True),
        sa.Column('actor_id', sa.Integer(), nullable=False),
        sa.Column('actor_email', sa.String(length=255), nullable=True),
        sa.Column('actor_role', sa.String(length=100), nullable=True),
        sa.Column('ip_address', sa.String(length=100), nullable=True),
        sa.Column('user_agent', sa.String(length=500), nullable=True),
        sa.Column('request_id', sa.String(length=100), nullable=True),
        sa.Column('session_id', sa.String(length=100), nullable=True),
        sa.Column('justification', sa.Text(), nullable=True),
        sa.Column('approval_chain', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['approval_id'], ['adm_phase_approvals.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['board_id'], ['kanban_boards.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['card_id'], ['kanban_cards.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['phase_id'], ['adm_phases.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_phase_id'], ['adm_phases.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['target_phase_id'], ['adm_phases.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('audit_id')
    )

    # Create adm_audit_summaries table
    op.create_table('adm_audit_summaries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('summary_type', sa.String(length=50), nullable=False),
        sa.Column('scope_id', sa.Integer(), nullable=False),
        sa.Column('scope_name', sa.String(length=255), nullable=True),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('total_actions', sa.Integer(), nullable=True),
        sa.Column('cards_created', sa.Integer(), nullable=True),
        sa.Column('cards_moved', sa.Integer(), nullable=True),
        sa.Column('approvals_submitted', sa.Integer(), nullable=True),
        sa.Column('approvals_approved', sa.Integer(), nullable=True),
        sa.Column('approvals_rejected', sa.Integer(), nullable=True),
        sa.Column('phase_transitions', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('unique_actors', sa.Integer(), nullable=True),
        sa.Column('top_actors', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('checkpoints_completed', sa.Integer(), nullable=True),
        sa.Column('stakeholder_concurrences', sa.Integer(), nullable=True),
        sa.Column('computed_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create adm_data_retention_policies table
    op.create_table('adm_data_retention_policies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('policy_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('card_operations_retention_days', sa.Integer(), nullable=True),
        sa.Column('phase_transition_retention_days', sa.Integer(), nullable=True),
        sa.Column('approval_workflow_retention_days', sa.Integer(), nullable=True),
        sa.Column('compliance_audit_retention_days', sa.Integer(), nullable=True),
        sa.Column('system_generated_retention_days', sa.Integer(), nullable=True),
        sa.Column('auto_archive_enabled', sa.Boolean(), nullable=True),
        sa.Column('archive_after_days', sa.Integer(), nullable=True),
        sa.Column('archive_location', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('policy_name')
    )

    # Create indexes for performance
    op.create_index('idx_adm_audit_logs_timestamp', 'adm_audit_logs', ['timestamp'])
    op.create_index('idx_adm_audit_logs_action', 'adm_audit_logs', ['action'])
    op.create_index('idx_adm_audit_logs_entity', 'adm_audit_logs', ['entity_type', 'entity_id'])
    op.create_index('idx_adm_audit_logs_board', 'adm_audit_logs', ['board_id'])
    op.create_index('idx_adm_audit_logs_card', 'adm_audit_logs', ['card_id'])
    op.create_index('idx_adm_audit_logs_actor', 'adm_audit_logs', ['actor_id'])
    op.create_index('idx_adm_audit_logs_phase_transition', 'adm_audit_logs', ['source_phase_id', 'target_phase_id'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_adm_audit_logs_phase_transition')
    op.drop_index('idx_adm_audit_logs_actor')
    op.drop_index('idx_adm_audit_logs_card')
    op.drop_index('idx_adm_audit_logs_board')
    op.drop_index('idx_adm_audit_logs_entity')
    op.drop_index('idx_adm_audit_logs_action')
    op.drop_index('idx_adm_audit_logs_timestamp')

    # Drop tables
    op.drop_table('adm_data_retention_policies')
    op.drop_table('adm_audit_summaries')
    op.drop_table('adm_audit_logs')

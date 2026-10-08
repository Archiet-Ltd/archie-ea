"""Add ADM Phase Approval workflow tables

Migration for ADM-P0-2: Add phase gate criteria and Architecture Board approval workflow

Revision ID: 20260206_adm_phase_approval
Revises: 20260206_adm_togaf_fields
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = '20260206_adm_phase_approval'
down_revision = '20260206_adm_togaf_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Create adm_phase_approvals table
    op.create_table('adm_phase_approvals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('approval_number', sa.String(length=50), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('board_id', sa.Integer(), nullable=False),
        sa.Column('source_phase_id', sa.Integer(), nullable=False),
        sa.Column('target_phase_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('requested_by_id', sa.Integer(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=True),
        sa.Column('business_justification', sa.Text(), nullable=True),
        sa.Column('technical_justification', sa.Text(), nullable=True),
        sa.Column('risk_assessment', sa.Text(), nullable=True),
        sa.Column('deliverables_completed', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('compliance_checklist_status', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('stakeholder_concurrence', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('reviewer_id', sa.Integer(), nullable=True),
        sa.Column('review_started_at', sa.DateTime(), nullable=True),
        sa.Column('review_completed_at', sa.DateTime(), nullable=True),
        sa.Column('decision', sa.String(length=50), nullable=True),
        sa.Column('decision_rationale', sa.Text(), nullable=True),
        sa.Column('conditions', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.Column('decision_date', sa.DateTime(), nullable=True),
        sa.Column('arb_session_id', sa.Integer(), nullable=True),
        sa.Column('arb_review_item_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['board_id'], ['kanban_boards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['arb_review_item_id'], ['arb_review_items.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['arb_session_id'], ['architecture_review_boards.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['requested_by_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_phase_id'], ['adm_phases.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_phase_id'], ['adm_phases.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('approval_number')
    )

    # Create adm_compliance_checkpoints table
    op.create_table('adm_compliance_checkpoints',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('approval_id', sa.Integer(), nullable=False),
        sa.Column('checkpoint_name', sa.String(length=200), nullable=False),
        sa.Column('checkpoint_category', sa.String(length=50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_required', sa.Boolean(), nullable=True),
        sa.Column('is_completed', sa.Boolean(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('completed_by_id', sa.Integer(), nullable=True),
        sa.Column('evidence_required', sa.Boolean(), nullable=True),
        sa.Column('evidence_description', sa.Text(), nullable=True),
        sa.Column('evidence_url', sa.String(length=500), nullable=True),
        sa.Column('evidence_notes', sa.Text(), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=True),
        sa.Column('verified_by_id', sa.Integer(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('verification_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['approval_id'], ['adm_phase_approvals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['completed_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['verified_by_id'], ['users.id'], ondelete='SET NULL')
    )

    # Create adm_stakeholder_concurrences table
    op.create_table('adm_stakeholder_concurrences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('approval_id', sa.Integer(), nullable=False),
        sa.Column('stakeholder_role', sa.String(length=100), nullable=False),
        sa.Column('stakeholder_user_id', sa.Integer(), nullable=True),
        sa.Column('stakeholder_name', sa.String(length=200), nullable=True),
        sa.Column('stakeholder_email', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('concurrence_date', sa.DateTime(), nullable=True),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('concerns', sa.Text(), nullable=True),
        sa.Column('requested_at', sa.DateTime(), nullable=True),
        sa.Column('responded_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['approval_id'], ['adm_phase_approvals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['stakeholder_user_id'], ['users.id'], ondelete='SET NULL')
    )

    # Create adm_transition_history table
    op.create_table('adm_transition_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('board_id', sa.Integer(), nullable=False),
        sa.Column('source_phase_id', sa.Integer(), nullable=False),
        sa.Column('target_phase_id', sa.Integer(), nullable=False),
        sa.Column('approval_id', sa.Integer(), nullable=False),
        sa.Column('transitioned_by_id', sa.Integer(), nullable=False),
        sa.Column('transitioned_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['approval_id'], ['adm_phase_approvals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['board_id'], ['kanban_boards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_phase_id'], ['adm_phases.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_phase_id'], ['adm_phases.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['transitioned_by_id'], ['users.id'], ondelete='CASCADE')
    )

    # Create indexes for performance
    op.create_index('idx_adm_phase_approvals_card_id', 'adm_phase_approvals', ['card_id'])
    op.create_index('idx_adm_phase_approvals_board_id', 'adm_phase_approvals', ['board_id'])
    op.create_index('idx_adm_phase_approvals_status', 'adm_phase_approvals', ['status'])
    op.create_index('idx_adm_compliance_checkpoints_approval_id', 'adm_compliance_checkpoints', ['approval_id'])
    op.create_index('idx_adm_stakeholder_concurrences_approval_id', 'adm_stakeholder_concurrences', ['approval_id'])
    op.create_index('idx_adm_transition_history_card_id', 'adm_transition_history', ['card_id'])
    op.create_index('idx_adm_transition_history_board_id', 'adm_transition_history', ['board_id'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_adm_transition_history_board_id')
    op.drop_index('idx_adm_transition_history_card_id')
    op.drop_index('idx_adm_stakeholder_concurrences_approval_id')
    op.drop_index('idx_adm_compliance_checkpoints_approval_id')
    op.drop_index('idx_adm_phase_approvals_status')
    op.drop_index('idx_adm_phase_approvals_board_id')
    op.drop_index('idx_adm_phase_approvals_card_id')

    # Drop tables
    op.drop_table('adm_transition_history')
    op.drop_table('adm_stakeholder_concurrences')
    op.drop_table('adm_compliance_checkpoints')
    op.drop_table('adm_phase_approvals')

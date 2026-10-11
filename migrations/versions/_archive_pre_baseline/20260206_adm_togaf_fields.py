"""Add ADM Kanban TOGAF-compliant fields

Migration for ADM-P0-1: Fix ADMPhase model with TOGAF-compliant definitions

Revision ID: 20260206_adm_togaf_fields
Revises: 20260205_add_vendor_seed_tracking
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = '20260206_adm_togaf_fields'
down_revision = '20260205_add_vendor_seed_tracking'
branch_labels = None
depends_on = None


def upgrade():
    # Add TOGAF methodology metadata columns to adm_phases table
    op.add_column('adm_phases', sa.Column('togaf_objectives', sa.Text(), nullable=True))
    op.add_column('adm_phases', sa.Column('required_inputs', postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column('adm_phases', sa.Column('expected_outputs', postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column('adm_phases', sa.Column('architecture_board_gate_criteria', sa.Text(), nullable=True))
    op.add_column('adm_phases', sa.Column('governance_checkpoints', postgresql.JSON(astext_type=sa.Text()), nullable=True))

    # Create adm_phase_steps table for step-level tracking
    op.create_table('adm_phase_steps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('phase_id', sa.Integer(), nullable=False),
        sa.Column('step_number', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('activity_type', sa.String(length=50), nullable=True),
        sa.Column('inputs_required', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('outputs_produced', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('techniques_approaches', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('is_mandatory', sa.Boolean(), nullable=True, default=False),
        sa.Column('requires_approval', sa.Boolean(), nullable=True, default=False),
        sa.Column('approval_gate', sa.String(length=100), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=True, default=0),
        sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['phase_id'], ['adm_phases.id'], ondelete='CASCADE')
    )

    # Create indexes for performance
    op.create_index('idx_adm_phase_steps_phase_id', 'adm_phase_steps', ['phase_id'])
    op.create_index('idx_adm_phase_steps_step_number', 'adm_phase_steps', ['step_number'])
    op.create_index('idx_adm_phase_steps_sort_order', 'adm_phase_steps', ['sort_order'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_adm_phase_steps_sort_order')
    op.drop_index('idx_adm_phase_steps_step_number')
    op.drop_index('idx_adm_phase_steps_phase_id')

    # Drop adm_phase_steps table
    op.drop_table('adm_phase_steps')

    # Drop TOGAF columns from adm_phases
    op.drop_column('adm_phases', 'governance_checkpoints')
    op.drop_column('adm_phases', 'architecture_board_gate_criteria')
    op.drop_column('adm_phases', 'expected_outputs')
    op.drop_column('adm_phases', 'required_inputs')
    op.drop_column('adm_phases', 'togaf_objectives')

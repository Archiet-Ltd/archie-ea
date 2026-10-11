"""Add ADM Kanban Junction Tables for relational associations

Migration for ADM-P0-3: Replace JSON blobs with proper relational models

Revision ID: 20260206_adm_kanban_junctions
Revises: 20260206_adm_phase_approval
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = '20260206_adm_kanban_junctions'
down_revision = '20260206_adm_phase_approval'
branch_labels = None
depends_on = None


def upgrade():
    # Create card_applications table
    op.create_table('card_applications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('application_id', sa.Integer(), nullable=False),
        sa.Column('relationship_type', sa.String(length=50), nullable=True),
        sa.Column('impact_level', sa.String(length=20), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=True),
        sa.Column('verified_by_id', sa.Integer(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['application_id'], ['application_components.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['verified_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('card_id', 'application_id', name='uix_card_application')
    )

    # Create card_systems table
    op.create_table('card_systems',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('system_id', sa.Integer(), nullable=False),
        sa.Column('relationship_type', sa.String(length=50), nullable=True),
        sa.Column('impact_level', sa.String(length=20), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=True),
        sa.Column('verified_by_id', sa.Integer(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['system_id'], ['systems.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['verified_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('card_id', 'system_id', name='uix_card_system')
    )

    # Create card_archimate_elements table
    op.create_table('card_archimate_elements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('archimate_element_id', sa.Integer(), nullable=False),
        sa.Column('relationship_type', sa.String(length=50), nullable=True),
        sa.Column('relationship_direction', sa.String(length=20), nullable=True),
        sa.Column('archimate_layer', sa.String(length=50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('properties', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=True),
        sa.Column('verified_by_id', sa.Integer(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['archimate_element_id'], ['archimate_elements.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['verified_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('card_id', 'archimate_element_id', name='uix_card_archimate')
    )

    # Create card_capabilities table
    op.create_table('card_capabilities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('capability_id', sa.Integer(), nullable=False),
        sa.Column('relationship_type', sa.String(length=50), nullable=True),
        sa.Column('impact_level', sa.String(length=20), nullable=True),
        sa.Column('maturity_impact', sa.String(length=20), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=True),
        sa.Column('verified_by_id', sa.Integer(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['capability_id'], ['unified_capabilities.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['verified_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('card_id', 'capability_id', name='uix_card_capability')
    )

    # Create card_initiatives table
    op.create_table('card_initiatives',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('initiative_id', sa.Integer(), nullable=False),
        sa.Column('contribution_type', sa.String(length=50), nullable=True),
        sa.Column('contribution_level', sa.String(length=20), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['initiative_id'], ['business_initiatives.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('card_id', 'initiative_id', name='uix_card_initiative')
    )

    # Create card_dependencies table
    op.create_table('card_dependencies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_card_id', sa.Integer(), nullable=False),
        sa.Column('target_card_id', sa.Integer(), nullable=False),
        sa.Column('dependency_type', sa.String(length=50), nullable=True),
        sa.Column('is_blocking', sa.Boolean(), nullable=True),
        sa.Column('is_critical', sa.Boolean(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_card_id'], ['kanban_cards.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('source_card_id', 'target_card_id', name='uix_card_dependency')
    )

    # Create indexes
    op.create_index('idx_card_applications_card_id', 'card_applications', ['card_id'])
    op.create_index('idx_card_applications_app_id', 'card_applications', ['application_id'])
    op.create_index('idx_card_systems_card_id', 'card_systems', ['card_id'])
    op.create_index('idx_card_systems_system_id', 'card_systems', ['system_id'])
    op.create_index('idx_card_archimate_card_id', 'card_archimate_elements', ['card_id'])
    op.create_index('idx_card_archimate_element_id', 'card_archimate_elements', ['archimate_element_id'])
    op.create_index('idx_card_capabilities_card_id', 'card_capabilities', ['card_id'])
    op.create_index('idx_card_capabilities_cap_id', 'card_capabilities', ['capability_id'])
    op.create_index('idx_card_initiatives_card_id', 'card_initiatives', ['card_id'])
    op.create_index('idx_card_initiatives_init_id', 'card_initiatives', ['initiative_id'])
    op.create_index('idx_card_dependencies_source', 'card_dependencies', ['source_card_id'])
    op.create_index('idx_card_dependencies_target', 'card_dependencies', ['target_card_id'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_card_dependencies_target')
    op.drop_index('idx_card_dependencies_source')
    op.drop_index('idx_card_initiatives_init_id')
    op.drop_index('idx_card_initiatives_card_id')
    op.drop_index('idx_card_capabilities_cap_id')
    op.drop_index('idx_card_capabilities_card_id')
    op.drop_index('idx_card_archimate_element_id')
    op.drop_index('idx_card_archimate_card_id')
    op.drop_index('idx_card_systems_system_id')
    op.drop_index('idx_card_systems_card_id')
    op.drop_index('idx_card_applications_app_id')
    op.drop_index('idx_card_applications_card_id')

    # Drop tables
    op.drop_table('card_dependencies')
    op.drop_table('card_initiatives')
    op.drop_table('card_capabilities')
    op.drop_table('card_archimate_elements')
    op.drop_table('card_systems')
    op.drop_table('card_applications')

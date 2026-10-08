"""Add strategic_recommendations table for LLM-powered recommendations

Revision ID: 20260203_add_strategic_recommendations
Revises: 20260203_add_capability_health_overrides
Create Date: 2026-02-03 23:59:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260203_add_strategic_recommendations'
down_revision = '20260203_add_capability_health_overrides'
branch_labels = None
depends_on = None


def upgrade():
    """Create strategic_recommendations table."""
    op.create_table(
        'strategic_recommendations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dashboard', sa.String(length=50), nullable=False),
        sa.Column('capability_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=False),
        sa.Column('priority', sa.String(length=20), nullable=False),
        sa.Column('estimated_effort_weeks', sa.Integer(), nullable=True),
        sa.Column('expected_impact', sa.Text(), nullable=True),
        sa.Column('dependencies', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('confidence_score', sa.Float(), nullable=False),
        sa.Column('model_used', sa.String(length=100), nullable=False),
        sa.Column('provider_used', sa.String(length=50), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('user_rating', sa.Integer(), nullable=True),
        sa.Column('was_implemented', sa.Boolean(), nullable=True),
        sa.Column('feedback_notes', sa.Text(), nullable=True),
        sa.Column('rated_at', sa.DateTime(), nullable=True),
        sa.Column('rated_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['capability_id'], ['business_capabilities.id'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['rated_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for query performance
    op.create_index('ix_strategic_recommendations_dashboard', 'strategic_recommendations', ['dashboard'])
    op.create_index('ix_strategic_recommendations_capability_id', 'strategic_recommendations', ['capability_id'])
    op.create_index('ix_strategic_recommendations_created_at', 'strategic_recommendations', ['created_at'])
    op.create_index('ix_strategic_recommendations_user_rating', 'strategic_recommendations', ['user_rating'])
    op.create_index('ix_strategic_recommendations_is_active', 'strategic_recommendations', ['is_active'])


def downgrade():
    """Drop strategic_recommendations table."""
    op.drop_index('ix_strategic_recommendations_is_active', table_name='strategic_recommendations')
    op.drop_index('ix_strategic_recommendations_user_rating', table_name='strategic_recommendations')
    op.drop_index('ix_strategic_recommendations_created_at', table_name='strategic_recommendations')
    op.drop_index('ix_strategic_recommendations_capability_id', table_name='strategic_recommendations')
    op.drop_index('ix_strategic_recommendations_dashboard', table_name='strategic_recommendations')
    op.drop_table('strategic_recommendations')

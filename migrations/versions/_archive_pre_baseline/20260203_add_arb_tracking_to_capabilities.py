"""Add ARB tracking fields to business_capabilities

Revision ID: 20260203_arb_capability_tracking
Revises: 20260203_add_strategic_recommendations
Create Date: 2026-02-03 22:20:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260203_arb_capability_tracking'
down_revision = '20260203_add_strategic_recommendations'
branch_labels = None
depends_on = None


def upgrade():
    """Add ARB tracking columns to business_capabilities table."""
    # Add ARB tracking columns
    op.add_column('business_capabilities', sa.Column('arb_status', sa.String(50), nullable=True))
    op.add_column('business_capabilities', sa.Column('arb_review_id', sa.Integer(), nullable=True))
    op.add_column('business_capabilities', sa.Column('arb_decision_date', sa.DateTime(), nullable=True))
    op.add_column('business_capabilities', sa.Column('arb_submission_date', sa.DateTime(), nullable=True))
    
    # Create foreign key to arb_review_items
    op.create_foreign_key(
        'fk_business_capabilities_arb_review',
        'business_capabilities', 'arb_review_items',
        ['arb_review_id'], ['id'],
        ondelete='SET NULL'
    )
    
    # Create index for ARB status queries
    op.create_index('idx_capabilities_arb_status', 'business_capabilities', ['arb_status'])


def downgrade():
    """Remove ARB tracking columns from business_capabilities table."""
    op.drop_index('idx_capabilities_arb_status', table_name='business_capabilities')
    op.drop_constraint('fk_business_capabilities_arb_review', 'business_capabilities', type_='foreignkey')
    op.drop_column('business_capabilities', 'arb_submission_date')
    op.drop_column('business_capabilities', 'arb_decision_date')
    op.drop_column('business_capabilities', 'arb_review_id')
    op.drop_column('business_capabilities', 'arb_status')

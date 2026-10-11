"""COM-004: add organization_id to architecture_review_boards and work_packages

Revision ID: 001_com004
Revises:
Create Date: 2026-04-04
"""
from alembic import op
import sqlalchemy as sa

revision = '001_com004'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add organization_id to architecture_review_boards (nullable first for backfill)
    op.add_column('architecture_review_boards',
        sa.Column('organization_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_arb_org', 'architecture_review_boards', 'organizations',
        ['organization_id'], ['id'], ondelete='CASCADE')
    op.create_index('ix_arb_org_id', 'architecture_review_boards', ['organization_id'])

    # Add organization_id to work_packages (nullable first for backfill)
    op.add_column('work_packages',
        sa.Column('organization_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_wp_org', 'work_packages', 'organizations',
        ['organization_id'], ['id'], ondelete='CASCADE')
    op.create_index('ix_wp_org_id', 'work_packages', ['organization_id'])


def downgrade():
    op.drop_index('ix_wp_org_id', table_name='work_packages')
    op.drop_constraint('fk_wp_org', 'work_packages', type_='foreignkey')
    op.drop_column('work_packages', 'organization_id')
    op.drop_index('ix_arb_org_id', table_name='architecture_review_boards')
    op.drop_constraint('fk_arb_org', 'architecture_review_boards', type_='foreignkey')
    op.drop_column('architecture_review_boards', 'organization_id')

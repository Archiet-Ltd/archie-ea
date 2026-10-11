"""Bridge: satisfy broken alembic head reference then add ArchimatePattern / ArchimateViewpointTemplate

Revision ID: 20260311_work_packages_missing_columns
Revises: 20260311_ent111_rel_intent
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = '20260311_work_packages_missing_columns'
down_revision = '20260311_ent111_rel_intent'
branch_labels = None
depends_on = None


def upgrade():
    # ArchimatePattern — stores user-defined reusable element+relationship patterns
    op.create_table(
        'archimate_patterns',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('pattern_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # ArchimateViewpointTemplate — saves a full diagram layout as a reusable template
    op.create_table(
        'archimate_viewpoint_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('viewpoint_type', sa.String(100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('template_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('archimate_viewpoint_templates')
    op.drop_table('archimate_patterns')

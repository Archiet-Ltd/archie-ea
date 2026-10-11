"""ENT-111: add is_architectural_intent to saved_diagram_relationships

Revision ID: 20260311_ent111_rel_intent
Revises: 20260311_enf007_model_changes
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = '20260311_ent111_rel_intent'
down_revision = '20260311_enf007_model_changes'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'saved_diagram_relationships',
        sa.Column('is_architectural_intent', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column('saved_diagram_relationships', 'is_architectural_intent')

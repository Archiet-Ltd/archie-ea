"""Merge migration heads for QA-CAP-001

Revision ID: 8758cda215b7
Revises: add_archimate_element_ids_to_kanban_cards, add_workflow_artifact_tables, qa_cap_001_mapping_fields
Create Date: 2026-02-24 21:22:18.021606

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8758cda215b7'
down_revision = ('add_archimate_element_ids_to_kanban_cards', 'add_workflow_artifact_tables', 'qa_cap_001_mapping_fields')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

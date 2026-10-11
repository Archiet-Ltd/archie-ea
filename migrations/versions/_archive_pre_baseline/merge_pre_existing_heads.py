"""merge_pre_existing_heads

Revision ID: merge_pre_existing_heads
Revises: 20260222_restore_app_cols, add_adm_phase_to_workflow_definitions
Create Date: 2026-02-24

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "merge_pre_existing_heads"
down_revision = ("20260222_restore_app_cols", "add_adm_phase_to_workflow_definitions")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

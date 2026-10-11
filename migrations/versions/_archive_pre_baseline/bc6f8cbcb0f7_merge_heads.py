"""merge heads

Revision ID: bc6f8cbcb0f7
Revises: batch_import_001, dd9a0000a131, 20260130_sim_threshold, 20260131_add_capability_sets_table
Create Date: 2026-01-31 03:21:06.660785

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "bc6f8cbcb0f7"
down_revision = (
    "batch_import_001",
    "dd9a0000a131",
    "20260130_sim_threshold",
    "20260131_add_capability_sets_table",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

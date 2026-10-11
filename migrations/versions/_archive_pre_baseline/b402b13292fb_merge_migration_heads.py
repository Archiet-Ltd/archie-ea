"""Merge migration heads

Revision ID: b402b13292fb
Revises: 502ba51ab389, archimate_scope_001, add_capability_ids_001, data_obj_gov_001, support_quality_001, unified_wp_task_cols_001
Create Date: 2026-01-15 22:00:04.220550

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b402b13292fb"
down_revision = (
    "502ba51ab389",
    "archimate_scope_001",
    "add_capability_ids_001",
    "data_obj_gov_001",
    "support_quality_001",
    "unified_wp_task_cols_001",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

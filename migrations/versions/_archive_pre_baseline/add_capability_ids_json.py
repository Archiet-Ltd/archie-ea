"""Add capability_ids JSON field for multi-capability support

Revision ID: add_capability_ids_001
Revises: None
Create Date: 2026-01-15

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "add_capability_ids_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Add capability_ids JSON column for multi-capability selection"""
    # Add capability_ids JSON field to unified_work_packages table
    op.add_column("unified_work_packages", sa.Column("capability_ids", sa.JSON(), nullable=True))

    # Add capability_names JSON field to store names (for display without joins)
    op.add_column("unified_work_packages", sa.Column("capability_names", sa.JSON(), nullable=True))


def downgrade():
    """Remove capability_ids JSON columns"""
    op.drop_column("unified_work_packages", "capability_ids")
    op.drop_column("unified_work_packages", "capability_names")

"""Add user_id column to llm_interactions table

Revision ID: llm_user_id_001
Revises: app_component_cols_001
Create Date: 2026-01-14
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "llm_user_id_001"
down_revision = "app_component_cols_001"
branch_labels = None
depends_on = None


def upgrade():
    # Add user_id column to llm_interactions table
    try:
        op.add_column("llm_interactions", sa.Column("user_id", sa.Integer(), nullable=True))
        # Add foreign key constraint (optional - may fail if users table doesn't exist)
        try:
            op.create_foreign_key(
                "fk_llm_interactions_user_id", "llm_interactions", "users", ["user_id"], ["id"]
            )
        except Exception:
            pass
    except Exception:
        # Column may already exist
        pass


def downgrade():
    try:
        op.drop_constraint("fk_llm_interactions_user_id", "llm_interactions", type_="foreignkey")
    except Exception:
        pass
    try:
        op.drop_column("llm_interactions", "user_id")
    except Exception:
        pass

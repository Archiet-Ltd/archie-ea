"""Add missing governance columns to application_data_objects table

Revision ID: data_obj_gov_001
Revises: llm_user_id_001
Create Date: 2026-01-14
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "data_obj_gov_001"
down_revision = "llm_user_id_001"
branch_labels = None
depends_on = None


def upgrade():
    # Add missing governance columns to application_data_objects table
    try:
        op.add_column(
            "application_data_objects",
            sa.Column("access_level", sa.String(30), nullable=True, server_default="public"),
        )
    except Exception:
        pass  # Column may already exist

    try:
        op.add_column(
            "application_data_objects", sa.Column("access_roles", sa.JSON(), nullable=True)
        )
    except Exception:
        pass

    try:
        op.add_column(
            "application_data_objects", sa.Column("last_accessed", sa.DateTime(), nullable=True)
        )
    except Exception:
        pass

    try:
        op.add_column(
            "application_data_objects", sa.Column("auto_delete_date", sa.DateTime(), nullable=True)
        )
    except Exception:
        pass

    try:
        op.add_column(
            "application_data_objects", sa.Column("retention_reason", sa.String(255), nullable=True)
        )
    except Exception:
        pass


def downgrade():
    try:
        op.drop_column("application_data_objects", "retention_reason")
    except Exception:
        pass
    try:
        op.drop_column("application_data_objects", "auto_delete_date")
    except Exception:
        pass
    try:
        op.drop_column("application_data_objects", "last_accessed")
    except Exception:
        pass
    try:
        op.drop_column("application_data_objects", "access_roles")
    except Exception:
        pass
    try:
        op.drop_column("application_data_objects", "access_level")
    except Exception:
        pass

"""Add Hugging Face fields to API settings

Revision ID: 8b2c3d4e5f6
Revises: critical_fixes_001
Create Date: 2026-01-08 16:05:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8b2c3d4e5f6"
down_revision = "critical_fixes_001"
branch_labels = None
depends_on = None


def upgrade():
    """Add Hugging Face specific fields to api_settings table."""
    # Add Hugging Face specific columns
    op.add_column("api_settings", sa.Column("hf_model_id", sa.String(255), nullable=True))
    op.add_column("api_settings", sa.Column("hf_endpoint_url", sa.String(500), nullable=True))


def downgrade():
    """Remove Hugging Face specific fields from api_settings table."""
    # Remove Hugging Face specific columns
    op.drop_column("api_settings", "hf_endpoint_url")
    op.drop_column("api_settings", "hf_model_id")

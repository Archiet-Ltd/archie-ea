"""Add Abacus integration fields to ApplicationComponent

Revision ID: 20260202_add_abacus_fields
Revises: 9e4f2b1a8c3d
Create Date: 2026-02-02 22:20:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260202_add_abacus_fields"
down_revision = "9e4f2b1a8c3d"
branch_labels = None
depends_on = None


def upgrade():
    # Add Abacus integration fields to application_components table
    op.add_column(
        "application_components",
        sa.Column("external_id", sa.String(255), nullable=True, unique=True, index=True),
    )
    op.add_column(
        "application_components",
        sa.Column("abacus_source", sa.Boolean(), nullable=True, default=False),
    )
    op.add_column(
        "application_components", sa.Column("last_sync_from_abacus", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "application_components", sa.Column("abacus_properties", sa.JSON(), nullable=True)
    )
    op.add_column(
        "application_components", sa.Column("confidence_score", sa.Float(), nullable=True)
    )


def downgrade():
    # Remove Abacus integration fields from application_components table
    op.drop_column("application_components", "confidence_score")
    op.drop_column("application_components", "abacus_properties")
    op.drop_column("application_components", "last_sync_from_abacus")
    op.drop_column("application_components", "abacus_source")
    op.drop_column("application_components", "external_id")

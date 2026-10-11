"""Add missing columns to technical_capabilities table.

Revision ID: 20260125_tech_cap_cols
Revises: 20260123_merge_solution_vendor_pricing
Create Date: 2026-01-25

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260125_tech_cap_cols"
down_revision = "add_capability_vendor_app_mappings"
branch_labels = None
depends_on = None


def upgrade():
    """Add missing columns to technical_capabilities table."""
    # Add specialization_type column if it doesn't exist
    try:
        op.add_column(
            "technical_capabilities",
            sa.Column("specialization_type", sa.String(50), nullable=True, default="TECHNICAL"),
        )
        op.create_index(
            "ix_technical_capabilities_specialization_type",
            "technical_capabilities",
            ["specialization_type"],
        )
    except Exception as e:
        print(f"Column specialization_type may already exist: {e}")

    # Update existing rows to have default value
    op.execute(
        "UPDATE technical_capabilities SET specialization_type = 'TECHNICAL' WHERE specialization_type IS NULL"
    )


def downgrade():
    """Remove added columns."""
    try:
        op.drop_index(
            "ix_technical_capabilities_specialization_type", table_name="technical_capabilities"
        )
        op.drop_column("technical_capabilities", "specialization_type")
    except Exception as e:
        print(f"Error during downgrade: {e}")

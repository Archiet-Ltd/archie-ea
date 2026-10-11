"""Add roadmap enhancement fields to Gap and WorkPackage models.

This migration adds fields for:
- Gap: gap_type, color, source_capability_*, timeline dates, owner, costs
- WorkPackage: level, color, percent_complete, costs, dependencies

Revision ID: 006_roadmap_enhancements
Revises: 005_add_application_vendor_product_mapping
Create Date: 2026-01-22
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "006_roadmap_enhancements"
down_revision = None  # Set appropriately if chaining
branch_labels = None
depends_on = None


def upgrade():
    """Add roadmap enhancement fields."""

    # =========================================================================
    # Gap model enhancements
    # =========================================================================
    with op.batch_alter_table("gaps", schema=None) as batch_op:
        # Gap type classification
        batch_op.add_column(sa.Column("gap_type", sa.String(30), nullable=True, index=True))
        batch_op.add_column(sa.Column("gap_sub_types", sa.JSON, nullable=True))

        # UI customization
        batch_op.add_column(sa.Column("color", sa.String(7), nullable=True, default="#6B7280"))

        # Source capability reference
        batch_op.add_column(
            sa.Column("source_capability_type", sa.String(20), nullable=True, index=True)
        )
        batch_op.add_column(
            sa.Column("source_capability_id", sa.Integer, nullable=True, index=True)
        )

        # Timeline for roadmap
        batch_op.add_column(sa.Column("estimated_start_date", sa.Date, nullable=True))
        batch_op.add_column(sa.Column("target_resolution_date", sa.Date, nullable=True))

        # Additional roadmap fields
        batch_op.add_column(sa.Column("owner", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("estimated_effort_days", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("estimated_cost", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("business_value", sa.String(20), nullable=True))

    # Create composite index for source capability lookup
    op.create_index(
        "idx_gap_source_capability",
        "gaps",
        ["source_capability_type", "source_capability_id"],
        unique=False,
    )

    # =========================================================================
    # WorkPackage model enhancements
    # =========================================================================
    with op.batch_alter_table("work_packages", schema=None) as batch_op:
        # Hierarchy level
        batch_op.add_column(sa.Column("level", sa.Integer, nullable=True, default=1, index=True))

        # UI customization
        batch_op.add_column(sa.Column("color", sa.String(7), nullable=True))

        # Progress tracking
        batch_op.add_column(sa.Column("percent_complete", sa.Integer, nullable=True, default=0))

        # Cost tracking
        batch_op.add_column(sa.Column("estimated_cost", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("actual_cost", sa.Float, nullable=True))

        # Dependencies
        batch_op.add_column(sa.Column("dependencies", sa.JSON, nullable=True))


def downgrade():
    """Remove roadmap enhancement fields."""

    # WorkPackage
    with op.batch_alter_table("work_packages", schema=None) as batch_op:
        batch_op.drop_column("dependencies")
        batch_op.drop_column("actual_cost")
        batch_op.drop_column("estimated_cost")
        batch_op.drop_column("percent_complete")
        batch_op.drop_column("color")
        batch_op.drop_column("level")

    # Gap
    op.drop_index("idx_gap_source_capability", table_name="gaps")

    with op.batch_alter_table("gaps", schema=None) as batch_op:
        batch_op.drop_column("business_value")
        batch_op.drop_column("estimated_cost")
        batch_op.drop_column("estimated_effort_days")
        batch_op.drop_column("owner")
        batch_op.drop_column("target_resolution_date")
        batch_op.drop_column("estimated_start_date")
        batch_op.drop_column("source_capability_id")
        batch_op.drop_column("source_capability_type")
        batch_op.drop_column("color")
        batch_op.drop_column("gap_sub_types")
        batch_op.drop_column("gap_type")

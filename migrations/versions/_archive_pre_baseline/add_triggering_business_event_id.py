"""Add triggering_business_event_id to work_packages

Revision ID: add_triggering_business_event_id
Revises: 67f4a0d3c0f9
Create Date: 2026-01-10 18:37:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "add_triggering_business_event_id"
down_revision = "aa30eca8977b"
branch_labels = None
depends_on = None


def upgrade():
    # Add triggering_business_event_id column to work_packages table
    op.add_column(
        "work_packages", sa.Column("triggering_business_event_id", sa.Integer(), nullable=True)
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_work_packages_triggering_business_event",
        "work_packages",
        "business_events",
        ["triggering_business_event_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Add index for performance
    op.create_index(
        "ix_work_packages_triggering_business_event_id",
        "work_packages",
        ["triggering_business_event_id"],
    )


def downgrade():
    # Remove index
    op.drop_index("ix_work_packages_triggering_business_event_id", table_name="work_packages")

    # Remove foreign key constraint
    op.drop_constraint(
        "fk_work_packages_triggering_business_event", "work_packages", type_="foreignkey"
    )

    # Remove column
    op.drop_column("work_packages", "triggering_business_event_id")

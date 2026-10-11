"""create consolidation list table

Revision ID: create_consolidation_list
Revises:
Create Date: 2024-01-01 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "create_consolidation_list"
down_revision = "20260108_2155_simple_duplicate"  # Latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Create consolidation_action enum type
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE consolidationaction AS ENUM ('decommission', 'retire', 'merge', 'replace', 'modernize', 'add_to_roadmap', 'pending_review');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """
    )

    # Create consolidation_list_entries table
    op.create_table(
        "consolidation_list_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("source_group_id", sa.Integer(), nullable=True),
        sa.Column("source_group_name", sa.String(length=255), nullable=True),
        sa.Column(
            "source_type", sa.String(length=50), nullable=True, server_default="duplicate_detection"
        ),
        sa.Column(
            "recommended_action",
            postgresql.ENUM(
                "decommission",
                "retire",
                "merge",
                "replace",
                "modernize",
                "add_to_roadmap",
                "pending_review",
                name="consolidationaction",
                create_type=False,
            ),
            nullable=True,
            server_default="pending_review",
        ),
        sa.Column("priority", sa.String(length=20), nullable=True, server_default="medium"),
        sa.Column("estimated_savings", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("consolidation_complexity", sa.String(length=20), nullable=True),
        sa.Column("target_quarter", sa.String(length=20), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("roadmap_item_id", sa.Integer(), nullable=True),
        sa.Column("decommission_date", sa.Date(), nullable=True),
        sa.Column("retirement_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("business_rationale", sa.Text(), nullable=True),
        sa.Column("risk_assessment", sa.Text(), nullable=True),
        sa.Column("dependencies", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True, server_default="pending"),
        sa.Column("assigned_to", sa.String(length=200), nullable=True),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("added_by", sa.String(length=200), nullable=True),
        sa.Column(
            "added_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["application_components.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index(
        op.f("ix_consolidation_list_entries_application_id"),
        "consolidation_list_entries",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_consolidation_list_entries_status"),
        "consolidation_list_entries",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_consolidation_list_entries_added_at"),
        "consolidation_list_entries",
        ["added_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_consolidation_list_entries_added_at"), table_name="consolidation_list_entries"
    )
    op.drop_index(
        op.f("ix_consolidation_list_entries_status"), table_name="consolidation_list_entries"
    )
    op.drop_index(
        op.f("ix_consolidation_list_entries_application_id"),
        table_name="consolidation_list_entries",
    )
    op.drop_table("consolidation_list_entries")
    op.execute("DROP TYPE IF EXISTS consolidationaction")

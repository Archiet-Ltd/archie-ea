"""Create simple duplicate detection tables

Revision ID: 20260108_2155_simple_duplicate
Revises: add_duplicate_detection_models_2026
Create Date: 2026-01-08 21:55:00.000000

"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "20260108_2155_simple_duplicate"
down_revision = "add_duplicate_detection_models_2026"
branch_labels = None
depends_on = None


def upgrade():
    """Create simple duplicate detection tables"""

    # Create simple_duplicate_groups table
    op.create_table(
        "simple_duplicate_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duplicate_type", sa.String(length=50), nullable=False),
        sa.Column("overall_similarity", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
            default=datetime.utcnow,
            onupdate=datetime.utcnow,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_simple_duplicate_groups_id"), "simple_duplicate_groups", ["id"], unique=False
    )

    # Create simple_detection_runs table
    op.create_table(
        "simple_detection_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True, default="pending"),
        sa.Column("similarity_threshold", sa.Float(), nullable=True, default=0.7),
        sa.Column("applications_analyzed", sa.Integer(), nullable=True, default=0),
        sa.Column("groups_found", sa.Integer(), nullable=True, default=0),
        sa.Column("estimated_savings", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
            default=datetime.utcnow,
            onupdate=datetime.utcnow,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_simple_detection_runs_id"), "simple_detection_runs", ["id"], unique=False
    )

    # Create association table for groups and applications
    op.create_table(
        "simple_group_applications",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("role_in_group", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["application_components.id"],
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["simple_duplicate_groups.id"],
        ),
        sa.PrimaryKeyConstraint("group_id", "application_id"),
    )


def downgrade():
    """Remove simple duplicate detection tables"""

    # Drop association table first (due to foreign key constraints)
    op.drop_table("simple_group_applications")

    # Drop main tables
    op.drop_table("simple_detection_runs")
    op.drop_table("simple_duplicate_groups")

    # Drop indexes
    op.drop_index(op.f("ix_simple_detection_runs_id"), table_name="simple_detection_runs")
    op.drop_index(op.f("ix_simple_duplicate_groups_id"), table_name="simple_duplicate_groups")

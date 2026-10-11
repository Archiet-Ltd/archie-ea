"""Add task/deliverable columns for work package management.

Adds unified_work_package_id and related fields to roadmap_tasks and roadmap_deliverables tables.

Revision ID: unified_wp_task_cols_001
Revises: None
Create Date: 2026-01-15

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "unified_wp_task_cols_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # === Add columns to roadmap_tasks table ===
    # Check if columns exist before adding (safe for re-runs)
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Get existing columns for roadmap_tasks
    existing_task_columns = [col["name"] for col in inspector.get_columns("roadmap_tasks")]

    if "unified_work_package_id" not in existing_task_columns:
        op.add_column(
            "roadmap_tasks", sa.Column("unified_work_package_id", sa.BigInteger(), nullable=True)
        )
        op.create_index(
            "ix_roadmap_tasks_unified_work_package_id", "roadmap_tasks", ["unified_work_package_id"]
        )

    if "capability_level" not in existing_task_columns:
        op.add_column(
            "roadmap_tasks",
            sa.Column("capability_level", sa.String(10), nullable=True, server_default="L3"),
        )
        op.create_index("ix_roadmap_tasks_capability_level", "roadmap_tasks", ["capability_level"])

    if "archimate_element_type" not in existing_task_columns:
        op.add_column(
            "roadmap_tasks",
            sa.Column(
                "archimate_element_type",
                sa.String(50),
                nullable=True,
                server_default="ImplementationEvent",
            ),
        )

    if "priority" not in existing_task_columns:
        op.add_column(
            "roadmap_tasks",
            sa.Column("priority", sa.String(20), nullable=True, server_default="medium"),
        )

    if "estimated_hours" not in existing_task_columns:
        op.add_column(
            "roadmap_tasks",
            sa.Column("estimated_hours", sa.Float(), nullable=True, server_default="0.0"),
        )

    if "actual_hours" not in existing_task_columns:
        op.add_column(
            "roadmap_tasks",
            sa.Column("actual_hours", sa.Float(), nullable=True, server_default="0.0"),
        )

    if "assigned_to" not in existing_task_columns:
        op.add_column("roadmap_tasks", sa.Column("assigned_to", sa.String(255), nullable=True))
        op.create_index("ix_roadmap_tasks_assigned_to", "roadmap_tasks", ["assigned_to"])

    if "created_by" not in existing_task_columns:
        op.add_column("roadmap_tasks", sa.Column("created_by", sa.Integer(), nullable=True))

    if "updated_by" not in existing_task_columns:
        op.add_column("roadmap_tasks", sa.Column("updated_by", sa.Integer(), nullable=True))

    # === Add columns to roadmap_deliverables table ===
    existing_deliverable_columns = [
        col["name"] for col in inspector.get_columns("roadmap_deliverables")
    ]

    if "unified_work_package_id" not in existing_deliverable_columns:
        op.add_column(
            "roadmap_deliverables",
            sa.Column("unified_work_package_id", sa.BigInteger(), nullable=True),
        )
        op.create_index(
            "ix_roadmap_deliverables_unified_work_package_id",
            "roadmap_deliverables",
            ["unified_work_package_id"],
        )

    if "related_task_ids" not in existing_deliverable_columns:
        op.add_column(
            "roadmap_deliverables", sa.Column("related_task_ids", sa.Text(), nullable=True)
        )

    if "archimate_element_type" not in existing_deliverable_columns:
        op.add_column(
            "roadmap_deliverables",
            sa.Column(
                "archimate_element_type", sa.String(50), nullable=True, server_default="Deliverable"
            ),
        )

    if "deliverable_type" not in existing_deliverable_columns:
        op.add_column(
            "roadmap_deliverables", sa.Column("deliverable_type", sa.String(50), nullable=True)
        )


def downgrade():
    # Remove columns from roadmap_tasks
    op.drop_index("ix_roadmap_tasks_assigned_to", "roadmap_tasks")
    op.drop_index("ix_roadmap_tasks_capability_level", "roadmap_tasks")
    op.drop_index("ix_roadmap_tasks_unified_work_package_id", "roadmap_tasks")

    op.drop_column("roadmap_tasks", "updated_by")
    op.drop_column("roadmap_tasks", "created_by")
    op.drop_column("roadmap_tasks", "assigned_to")
    op.drop_column("roadmap_tasks", "actual_hours")
    op.drop_column("roadmap_tasks", "estimated_hours")
    op.drop_column("roadmap_tasks", "priority")
    op.drop_column("roadmap_tasks", "archimate_element_type")
    op.drop_column("roadmap_tasks", "capability_level")
    op.drop_column("roadmap_tasks", "unified_work_package_id")

    # Remove columns from roadmap_deliverables
    op.drop_index("ix_roadmap_deliverables_unified_work_package_id", "roadmap_deliverables")

    op.drop_column("roadmap_deliverables", "deliverable_type")
    op.drop_column("roadmap_deliverables", "archimate_element_type")
    op.drop_column("roadmap_deliverables", "related_task_ids")
    op.drop_column("roadmap_deliverables", "unified_work_package_id")

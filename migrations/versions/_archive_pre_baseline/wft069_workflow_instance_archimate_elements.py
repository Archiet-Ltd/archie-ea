"""WFT-069: create workflow_instance_archimate_elements junction table.

This migration adds the junction table that links ea_workflow_instances to
archimate_elements with ADM phase tagging and element role classification.
The ORM model WorkflowInstanceArchiMateElement already exists in app/models/models.py;
this migration aligns the DB schema with the ORM definition.

Revision ID: wft069_workflow_instance_archimate_elements
Revises: 20260309_schema_gap_fill
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "wft069_workflow_instance_archimate_elements"
down_revision = "20260309_schema_gap_fill"
branch_labels = None
depends_on = None


def _table_exists(table_name):
    try:
        bind = op.get_bind()
        inspector = inspect(bind)
        return table_name in inspector.get_table_names()
    except Exception:
        return False


def upgrade():
    if _table_exists("workflow_instance_archimate_elements"):
        return

    op.create_table(
        "workflow_instance_archimate_elements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("element_role", sa.String(length=50), nullable=False),
        sa.Column("adm_phase", sa.String(length=10), nullable=False),
        sa.Column("step_id", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["element_id"], ["archimate_elements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["ea_workflow_instances.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instance_id",
            "element_id",
            "element_role",
            name="uq_wiame_instance_element_role",
        ),
    )
    op.create_index(
        "ix_wiame_element_id",
        "workflow_instance_archimate_elements",
        ["element_id"],
        unique=False,
    )
    op.create_index(
        "ix_wiame_instance_id",
        "workflow_instance_archimate_elements",
        ["instance_id"],
        unique=False,
    )


def downgrade():
    if not _table_exists("workflow_instance_archimate_elements"):
        return

    op.drop_index(
        "ix_wiame_instance_id",
        table_name="workflow_instance_archimate_elements",
    )
    op.drop_index(
        "ix_wiame_element_id",
        table_name="workflow_instance_archimate_elements",
    )
    op.drop_table("workflow_instance_archimate_elements")

"""add_ea_workflow_engine_fields

Revision ID: add_ea_workflow_engine_fields
Revises:
Create Date: 2026-02-24

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_ea_workflow_engine_fields"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # EAWorkflowInstance new columns
    with op.batch_alter_table("ea_workflow_instances") as batch_op:
        batch_op.add_column(sa.Column("total_steps", sa.Integer(), nullable=True, server_default="0"))
        batch_op.add_column(sa.Column("completed_steps", sa.Integer(), nullable=True, server_default="0"))
        batch_op.add_column(sa.Column("failed_steps", sa.Integer(), nullable=True, server_default="0"))
        batch_op.add_column(sa.Column("current_step_id", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("pending_approval_step_id", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("error_step_id", sa.String(50), nullable=True))
        batch_op.add_column(
            sa.Column(
                "triggered_by_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("scheduled_at", sa.DateTime(), nullable=True))

    # EAWorkflowStepExecution new columns
    with op.batch_alter_table("ea_workflow_step_executions") as batch_op:
        batch_op.add_column(sa.Column("step_index", sa.Integer(), nullable=True, server_default="0"))
        batch_op.add_column(sa.Column("service_class", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("service_method", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("requires_approval", sa.Boolean(), nullable=True, server_default="false"))
        batch_op.add_column(sa.Column("approval_status", sa.String(30), nullable=True))
        batch_op.add_column(
            sa.Column(
                "approved_by_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("error_traceback", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("ea_workflow_step_executions") as batch_op:
        batch_op.drop_column("error_traceback")
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by_id")
        batch_op.drop_column("approval_status")
        batch_op.drop_column("requires_approval")
        batch_op.drop_column("service_method")
        batch_op.drop_column("service_class")
        batch_op.drop_column("step_index")

    with op.batch_alter_table("ea_workflow_instances") as batch_op:
        batch_op.drop_column("scheduled_at")
        batch_op.drop_column("triggered_by_user_id")
        batch_op.drop_column("error_step_id")
        batch_op.drop_column("pending_approval_step_id")
        batch_op.drop_column("current_step_id")
        batch_op.drop_column("failed_steps")
        batch_op.drop_column("completed_steps")
        batch_op.drop_column("total_steps")

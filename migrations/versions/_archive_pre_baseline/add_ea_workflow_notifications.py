"""add_ea_workflow_notifications

Revision ID: add_ea_workflow_notifications
Revises: add_ea_workflow_engine_fields
Create Date: 2026-02-24

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_ea_workflow_notifications"
down_revision = "add_ea_workflow_engine_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ea_workflow_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_instance_id", sa.Integer(), nullable=False),
        sa.Column("recipient_id", sa.Integer(), nullable=True),
        sa.Column("template", sa.String(100), nullable=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("channel", sa.String(30), server_default="in_app", nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default="false", nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("delivery_status", sa.String(30), server_default="sent", nullable=True),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workflow_instance_id"], ["ea_workflow_instances.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ea_workflow_notifications_workflow_instance_id",
        "ea_workflow_notifications",
        ["workflow_instance_id"],
    )
    op.create_index(
        "ix_ea_workflow_notifications_recipient_id",
        "ea_workflow_notifications",
        ["recipient_id"],
    )


def downgrade():
    op.drop_index("ix_ea_workflow_notifications_recipient_id", table_name="ea_workflow_notifications")
    op.drop_index("ix_ea_workflow_notifications_workflow_instance_id", table_name="ea_workflow_notifications")
    op.drop_table("ea_workflow_notifications")

"""Add production audit and import history tables

Migration for production readiness: Add audit logging and import rollback capability

Revision ID: 20260210_production_audit
Revises: 20260206_adm_audit_log
Create Date: 2026-02-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = "20260210_production_audit"
down_revision = "20260206_adm_audit_log"
branch_labels = None
depends_on = None


def upgrade():
    # Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_email", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("entity_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("old_values", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("new_values", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, default=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for audit_logs
    op.create_index(
        "ix_audit_logs_timestamp", "audit_logs", ["timestamp"], unique=False
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"], unique=False)
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index(
        "ix_audit_logs_entity_type", "audit_logs", ["entity_type"], unique=False
    )
    op.create_index(
        "ix_audit_logs_entity_id", "audit_logs", ["entity_id"], unique=False
    )
    op.create_index(
        "ix_audit_logs_request_id", "audit_logs", ["request_id"], unique=False
    )

    # Create import_history table
    op.create_table(
        "import_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("import_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, default="completed"),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.Column("records_imported", sa.Integer(), default=0),
        sa.Column("records_failed", sa.Integer(), default=0),
        sa.Column("errors", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("is_rolled_back", sa.Boolean(), default=False),
        sa.Column("rolled_back_by", sa.Integer(), nullable=True),
        sa.Column("rollback_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_entity_ids", postgresql.JSON(astext_type=sa.Text()), default=dict
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for import_history
    op.create_index(
        "ix_import_history_created_at", "import_history", ["created_at"], unique=False
    )
    op.create_index(
        "ix_import_history_user_id", "import_history", ["user_id"], unique=False
    )
    op.create_index(
        "ix_import_history_status", "import_history", ["status"], unique=False
    )
    op.create_index(
        "ix_import_history_is_rolled_back",
        "import_history",
        ["is_rolled_back"],
        unique=False,
    )


def downgrade():
    # Drop indexes first
    op.drop_index("ix_import_history_is_rolled_back", table_name="import_history")
    op.drop_index("ix_import_history_status", table_name="import_history")
    op.drop_index("ix_import_history_user_id", table_name="import_history")
    op.drop_index("ix_import_history_created_at", table_name="import_history")

    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_timestamp", table_name="audit_logs")

    # Drop tables
    op.drop_table("import_history")
    op.drop_table("audit_logs")

"""Add import session tables for transactional staging

Revision ID: add_import_session_001
Revises:
Create Date: 2026-01-22 16:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "add_import_session_001"
down_revision = None  # Update this to your latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Create import_sessions table
    op.create_table(
        "import_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_uuid", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("import_type", sa.String(length=50), nullable=True),
        sa.Column("enable_ai_import", sa.Boolean(), nullable=True),
        sa.Column("confidence_threshold", sa.Float(), nullable=True),
        sa.Column("archimate_mode", sa.String(length=20), nullable=True),
        sa.Column("custom_mappings", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_checkpoint", sa.String(length=50), nullable=True),
        sa.Column("checkpoint_data", sa.JSON(), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=True),
        sa.Column("processed_rows", sa.Integer(), nullable=True),
        sa.Column("successful_rows", sa.Integer(), nullable=True),
        sa.Column("failed_rows", sa.Integer(), nullable=True),
        sa.Column("skipped_rows", sa.Integer(), nullable=True),
        sa.Column("progress_percentage", sa.Float(), nullable=True),
        sa.Column("llm_calls_made", sa.Integer(), nullable=True),
        sa.Column("llm_tokens_used", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("llm_providers_used", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", sa.JSON(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=True),
        sa.Column("estimated_completion_at", sa.DateTime(), nullable=True),
        sa.Column("processing_time_seconds", sa.Integer(), nullable=True),
        sa.Column("can_resume", sa.Boolean(), nullable=True),
        sa.Column("resume_from_checkpoint", sa.String(length=50), nullable=True),
        sa.Column("recovery_attempts", sa.Integer(), nullable=True),
        sa.Column("results_summary", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for import_sessions
    op.create_index("idx_import_session_user_status", "import_sessions", ["user_id", "status"])
    op.create_index("idx_import_session_created", "import_sessions", ["created_at"])
    op.create_index(
        "idx_import_session_status_activity", "import_sessions", ["status", "last_activity_at"]
    )
    op.create_index(
        op.f("ix_import_sessions_session_uuid"), "import_sessions", ["session_uuid"], unique=True
    )
    op.create_index(op.f("ix_import_sessions_user_id"), "import_sessions", ["user_id"])
    op.create_index(op.f("ix_import_sessions_status"), "import_sessions", ["status"])

    # Create staging_elements table
    op.create_table(
        "staging_elements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_session_id", sa.Integer(), nullable=False),
        sa.Column("element_type", sa.String(length=50), nullable=False),
        sa.Column("element_uuid", sa.String(length=36), nullable=False),
        sa.Column("element_data", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("source_row_number", sa.Integer(), nullable=True),
        sa.Column("source_data", sa.JSON(), nullable=True),
        sa.Column("generated_by_llm", sa.Boolean(), nullable=True),
        sa.Column("llm_provider", sa.String(length=50), nullable=True),
        sa.Column("llm_model", sa.String(length=100), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("requires_review", sa.Boolean(), nullable=True),
        sa.Column("parent_element_uuid", sa.String(length=36), nullable=True),
        sa.Column("related_element_uuids", sa.JSON(), nullable=True),
        sa.Column("is_committed", sa.Boolean(), nullable=True),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.Column("committed_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["import_session_id"], ["import_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for staging_elements
    op.create_index(
        "idx_staging_session_type", "staging_elements", ["import_session_id", "element_type"]
    )
    op.create_index("idx_staging_committed", "staging_elements", ["is_committed"])
    op.create_index("idx_staging_parent", "staging_elements", ["parent_element_uuid"])
    op.create_index(
        op.f("ix_staging_elements_element_uuid"), "staging_elements", ["element_uuid"], unique=True
    )
    op.create_index(
        op.f("ix_staging_elements_import_session_id"), "staging_elements", ["import_session_id"]
    )
    op.create_index(op.f("ix_staging_elements_element_type"), "staging_elements", ["element_type"])
    op.create_index(op.f("ix_staging_elements_created_at"), "staging_elements", ["created_at"])

    # Create import_checkpoints table
    op.create_table(
        "import_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_session_id", sa.Integer(), nullable=False),
        sa.Column("checkpoint_type", sa.String(length=50), nullable=False),
        sa.Column("checkpoint_name", sa.String(length=100), nullable=False),
        sa.Column("checkpoint_data", sa.JSON(), nullable=True),
        sa.Column("rows_processed", sa.Integer(), nullable=True),
        sa.Column("elements_staged", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["import_session_id"], ["import_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for import_checkpoints
    op.create_index(
        op.f("ix_import_checkpoints_import_session_id"), "import_checkpoints", ["import_session_id"]
    )
    op.create_index(
        op.f("ix_import_checkpoints_checkpoint_type"), "import_checkpoints", ["checkpoint_type"]
    )
    op.create_index(op.f("ix_import_checkpoints_created_at"), "import_checkpoints", ["created_at"])


def downgrade():
    # Drop tables in reverse order
    op.drop_table("import_checkpoints")
    op.drop_table("staging_elements")
    op.drop_table("import_sessions")

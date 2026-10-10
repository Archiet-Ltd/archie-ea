"""Add missing tables for API health - batch processing, capability taxonomy audit, archimate relationships

Revision ID: 20260201_api_fix
Revises: bc6f8cbcb0f7
Create Date: 2026-02-01

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260201_api_fix"
down_revision = "bc6f8cbcb0f7"
branch_labels = None
depends_on = None


def table_exists(conn, table_name):
    """Check if a table exists in the database."""
    result = conn.execute(
        sa.text(
            f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table_name}')"
        )
    )
    return result.scalar()


def column_exists(conn, table_name, column_name):
    """Check if a column exists in a table."""
    result = conn.execute(
        sa.text(
            f"SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = '{table_name}' AND column_name = '{column_name}')"
        )
    )
    return result.scalar()


def upgrade():
    conn = op.get_bind()

    # Create enums if they don't exist
    conn.execute(
        sa.text(
            """
        DO $$ BEGIN
            CREATE TYPE batchjobtype AS ENUM (
                'ai_import', 'capability_mapping', 'apqc_classification',
                'archimate_generation', 'vendor_analysis', 'taxonomy_validation', 'bulk_update'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """
        )
    )
    conn.execute(
        sa.text(
            """
        DO $$ BEGIN
            CREATE TYPE batchjobstatus AS ENUM (
                'pending', 'running', 'paused', 'completed', 'failed', 'cancelled', 'recovering'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """
        )
    )

    # Create batch_jobs table if it doesn't exist
    if not table_exists(conn, "batch_jobs"):
        op.create_table(
            "batch_jobs",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("job_name", sa.String(200), nullable=False),
            sa.Column("job_type", sa.String(50), nullable=False),
            sa.Column("status", sa.String(50)),
            sa.Column("total_items", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("processed_items", sa.Integer(), server_default="0"),
            sa.Column("successful_items", sa.Integer(), server_default="0"),
            sa.Column("failed_items", sa.Integer(), server_default="0"),
            sa.Column("skipped_items", sa.Integer(), server_default="0"),
            sa.Column("progress_percentage", sa.Numeric(5, 2), server_default="0.0"),
            sa.Column("estimated_completion_time", sa.DateTime()),
            sa.Column("actual_completion_time", sa.DateTime()),
            sa.Column("items_per_second", sa.Numeric(10, 2)),
            sa.Column("average_processing_time", sa.Numeric(10, 3)),
            sa.Column("total_processing_time", sa.Numeric(10, 3)),
            sa.Column("error_count", sa.Integer(), server_default="0"),
            sa.Column("max_retries", sa.Integer(), server_default="3"),
            sa.Column("retry_count", sa.Integer(), server_default="0"),
            sa.Column("last_error_message", sa.Text()),
            sa.Column("last_error_time", sa.DateTime()),
            sa.Column("checkpoint_data", sa.Text()),
            sa.Column("last_checkpoint_time", sa.DateTime()),
            sa.Column("recovery_attempts", sa.Integer(), server_default="0"),
            sa.Column("job_parameters", sa.Text()),
            sa.Column("confidence_threshold", sa.Numeric(3, 2), server_default="0.6"),
            sa.Column("auto_retry", sa.Boolean(), server_default="true"),
            sa.Column("parallel_processing", sa.Boolean(), server_default="false"),
            sa.Column("batch_size", sa.Integer(), server_default="100"),
            sa.Column("priority", sa.Integer(), server_default="5"),
            sa.Column("created_by_id", sa.Integer()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime()),
            sa.Column("completed_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_batch_jobs_job_name", "batch_jobs", ["job_name"])
        op.create_index("ix_batch_jobs_job_type", "batch_jobs", ["job_type"])
        op.create_index("ix_batch_jobs_status", "batch_jobs", ["status"])
        op.create_index("ix_batch_jobs_created_at", "batch_jobs", ["created_at"])

    # Create batch_job_items table if it doesn't exist
    if not table_exists(conn, "batch_job_items"):
        op.create_table(
            "batch_job_items",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("batch_job_id", sa.BigInteger(), nullable=False),
            sa.Column("item_sequence", sa.Integer(), nullable=False),
            sa.Column("item_type", sa.String(50)),
            sa.Column("item_id", sa.BigInteger()),
            sa.Column("item_name", sa.String(500)),
            sa.Column("item_data", sa.Text()),
            sa.Column("status", sa.String(30), server_default="pending"),
            sa.Column("processing_attempts", sa.Integer(), server_default="0"),
            sa.Column("max_attempts", sa.Integer(), server_default="3"),
            sa.Column("processing_start_time", sa.DateTime()),
            sa.Column("processing_end_time", sa.DateTime()),
            sa.Column("processing_duration", sa.Numeric(10, 3)),
            sa.Column("result_data", sa.Text()),
            sa.Column("confidence_score", sa.Numeric(3, 2)),
            sa.Column("warnings", sa.Text()),
            sa.Column("recommendations", sa.Text()),
            sa.Column("error_message", sa.Text()),
            sa.Column("error_type", sa.String(100)),
            sa.Column("error_data", sa.Text()),
            sa.Column("retry_after", sa.DateTime()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["batch_job_id"], ["batch_jobs.id"]),
        )
        op.create_index("ix_batch_job_items_batch_job_id", "batch_job_items", ["batch_job_id"])
        op.create_index("ix_batch_job_items_created_at", "batch_job_items", ["created_at"])

    # Create batch_job_checkpoints table if it doesn't exist
    if not table_exists(conn, "batch_job_checkpoints"):
        op.create_table(
            "batch_job_checkpoints",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("batch_job_id", sa.BigInteger(), nullable=False),
            sa.Column("checkpoint_name", sa.String(200), nullable=False),
            sa.Column("checkpoint_type", sa.String(50)),
            sa.Column("processed_items_count", sa.Integer(), server_default="0"),
            sa.Column("successful_items_count", sa.Integer(), server_default="0"),
            sa.Column("failed_items_count", sa.Integer(), server_default="0"),
            sa.Column("checkpoint_data", sa.Text()),
            sa.Column("last_processed_item_id", sa.BigInteger()),
            sa.Column("last_successful_item_sequence", sa.Integer()),
            sa.Column("items_per_second_at_checkpoint", sa.Numeric(10, 2)),
            sa.Column("memory_usage_at_checkpoint", sa.Numeric(10, 2)),
            sa.Column("cpu_usage_at_checkpoint", sa.Numeric(5, 2)),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("created_by", sa.String(100)),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["batch_job_id"], ["batch_jobs.id"]),
        )
        op.create_index(
            "ix_batch_job_checkpoints_batch_job_id", "batch_job_checkpoints", ["batch_job_id"]
        )
        op.create_index(
            "ix_batch_job_checkpoints_created_at", "batch_job_checkpoints", ["created_at"]
        )

    # Create batch_job_errors table if it doesn't exist
    if not table_exists(conn, "batch_job_errors"):
        op.create_table(
            "batch_job_errors",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("batch_job_id", sa.BigInteger(), nullable=False),
            sa.Column("batch_job_item_id", sa.BigInteger()),
            sa.Column("error_type", sa.String(100), nullable=False),
            sa.Column("error_code", sa.String(50)),
            sa.Column("error_message", sa.Text(), nullable=False),
            sa.Column("error_stack_trace", sa.Text()),
            sa.Column("item_type", sa.String(50)),
            sa.Column("item_name", sa.String(500)),
            sa.Column("processing_step", sa.String(100)),
            sa.Column("can_retry", sa.Boolean(), server_default="true"),
            sa.Column("retry_delay_seconds", sa.Integer(), server_default="60"),
            sa.Column("max_retries", sa.Integer(), server_default="3"),
            sa.Column("retry_count", sa.Integer(), server_default="0"),
            sa.Column("recovery_action", sa.String(200)),
            sa.Column("recovery_suggestion", sa.Text()),
            sa.Column("auto_recovery_possible", sa.Boolean(), server_default="false"),
            sa.Column("severity", sa.String(20), server_default="'medium'"),
            sa.Column("category", sa.String(50)),
            sa.Column("occurred_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("resolved_at", sa.DateTime()),
            sa.Column("resolved_by_id", sa.Integer()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["batch_job_id"], ["batch_jobs.id"]),
        )
        op.create_index("ix_batch_job_errors_batch_job_id", "batch_job_errors", ["batch_job_id"])
        op.create_index("ix_batch_job_errors_occurred_at", "batch_job_errors", ["occurred_at"])

    # Create batch_job_statistics table if it doesn't exist
    if not table_exists(conn, "batch_job_statistics"):
        op.create_table(
            "batch_job_statistics",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("job_type", sa.String(50), nullable=False),
            sa.Column("date_bucket", sa.Date(), nullable=False),
            sa.Column("time_bucket", sa.String(20)),
            sa.Column("total_jobs", sa.Integer(), server_default="0"),
            sa.Column("completed_jobs", sa.Integer(), server_default="0"),
            sa.Column("failed_jobs", sa.Integer(), server_default="0"),
            sa.Column("cancelled_jobs", sa.Integer(), server_default="0"),
            sa.Column("total_items", sa.BigInteger(), server_default="0"),
            sa.Column("processed_items", sa.BigInteger(), server_default="0"),
            sa.Column("successful_items", sa.BigInteger(), server_default="0"),
            sa.Column("failed_items", sa.BigInteger(), server_default="0"),
            sa.Column("skipped_items", sa.BigInteger(), server_default="0"),
            sa.Column("average_items_per_second", sa.Numeric(10, 2)),
            sa.Column("average_processing_time", sa.Numeric(10, 3)),
            sa.Column("total_processing_time", sa.Numeric(15, 3)),
            sa.Column("total_errors", sa.Integer(), server_default="0"),
            sa.Column("average_errors_per_job", sa.Numeric(10, 2)),
            sa.Column("error_rate_percentage", sa.Numeric(5, 2)),
            sa.Column("average_memory_usage", sa.Numeric(10, 2)),
            sa.Column("peak_memory_usage", sa.Numeric(10, 2)),
            sa.Column("average_cpu_usage", sa.Numeric(5, 2)),
            sa.Column("peak_cpu_usage", sa.Numeric(5, 2)),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_batch_job_statistics_job_type", "batch_job_statistics", ["job_type"])
        op.create_index(
            "ix_batch_job_statistics_date_bucket", "batch_job_statistics", ["date_bucket"]
        )
        op.create_index(
            "ix_batch_job_statistics_time_bucket", "batch_job_statistics", ["time_bucket"]
        )
        op.create_index(
            "ix_batch_job_statistics_created_at", "batch_job_statistics", ["created_at"]
        )

    # Create capability_taxonomy_audit table if it doesn't exist
    if not table_exists(conn, "capability_taxonomy_audit"):
        op.create_table(
            "capability_taxonomy_audit",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("capability_id", sa.Integer()),
            sa.Column("audit_type", sa.String(50), nullable=False),
            sa.Column("change_type", sa.String(50)),
            sa.Column("entity_type", sa.String(50), server_default="'capability'"),
            sa.Column("entity_id", sa.Integer()),
            sa.Column("entity_name", sa.String(200)),
            sa.Column("old_value", sa.Text()),
            sa.Column("new_value", sa.Text()),
            sa.Column("change_reason", sa.Text()),
            sa.Column("changed_by", sa.String(100)),
            sa.Column("changed_by_id", sa.Integer()),
            sa.Column("approved_by", sa.String(100)),
            sa.Column("approval_status", sa.String(20), server_default="'pending'"),
            sa.Column("impact_assessment", sa.Text()),
            sa.Column("rollback_available", sa.Boolean(), server_default="true"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_capability_taxonomy_audit_capability_id",
            "capability_taxonomy_audit",
            ["capability_id"],
        )
        op.create_index(
            "ix_capability_taxonomy_audit_audit_type", "capability_taxonomy_audit", ["audit_type"]
        )
        op.create_index(
            "ix_capability_taxonomy_audit_created_at", "capability_taxonomy_audit", ["created_at"]
        )

    # Add missing columns to archimate_relationships table if it exists
    if table_exists(conn, "archimate_relationships"):
        if not column_exists(conn, "archimate_relationships", "source_element_id"):
            op.add_column("archimate_relationships", sa.Column("source_element_id", sa.Integer()))
        if not column_exists(conn, "archimate_relationships", "target_element_id"):
            op.add_column("archimate_relationships", sa.Column("target_element_id", sa.Integer()))
        if not column_exists(conn, "archimate_relationships", "relationship_strength"):
            op.add_column(
                "archimate_relationships", sa.Column("relationship_strength", sa.String(50))
            )
        if not column_exists(conn, "archimate_relationships", "impact_level"):
            op.add_column("archimate_relationships", sa.Column("impact_level", sa.String(50)))


def downgrade():
    conn = op.get_bind()

    # Drop archimate_relationships columns if they exist
    if table_exists(conn, "archimate_relationships"):
        if column_exists(conn, "archimate_relationships", "impact_level"):
            op.drop_column("archimate_relationships", "impact_level")
        if column_exists(conn, "archimate_relationships", "relationship_strength"):
            op.drop_column("archimate_relationships", "relationship_strength")
        if column_exists(conn, "archimate_relationships", "target_element_id"):
            op.drop_column("archimate_relationships", "target_element_id")
        if column_exists(conn, "archimate_relationships", "source_element_id"):
            op.drop_column("archimate_relationships", "source_element_id")

    # Drop tables if they exist
    if table_exists(conn, "capability_taxonomy_audit"):
        op.drop_table("capability_taxonomy_audit")

    if table_exists(conn, "batch_job_statistics"):
        op.drop_table("batch_job_statistics")

    if table_exists(conn, "batch_job_errors"):
        op.drop_table("batch_job_errors")

    if table_exists(conn, "batch_job_checkpoints"):
        op.drop_table("batch_job_checkpoints")

    if table_exists(conn, "batch_job_items"):
        op.drop_table("batch_job_items")

    if table_exists(conn, "batch_jobs"):
        op.drop_table("batch_jobs")

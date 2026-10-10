"""Batch import tables for approval workflow

Revision ID: batch_import_001
Revises:
Create Date: 2026-01-27

Creates tables for batch import system with approval workflow:
- batch_import_job: Master record for import operations
- batch_import_batch: Individual batches within a job
- batch_import_application: Applications being imported
- batch_import_element: AI-generated elements staged for approval
- batch_import_checkpoint: Recovery checkpoints
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "batch_import_001"
down_revision = None
branch_labels = ("batch_import",)
depends_on = None


def upgrade():
    # BatchImportJob table
    op.create_table(
        "batch_import_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_uuid", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("total_applications", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("batch_size", sa.Integer(), server_default="20"),
        sa.Column("total_batches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("batches_completed", sa.Integer(), server_default="0"),
        sa.Column("batches_approved", sa.Integer(), server_default="0"),
        sa.Column("batches_rejected", sa.Integer(), server_default="0"),
        sa.Column("batches_committed", sa.Integer(), server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), server_default="0"),
        sa.Column("actual_cost_usd", sa.Numeric(10, 4), server_default="0"),
        sa.Column("budget_limit_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("total_llm_calls", sa.Integer(), server_default="0"),
        sa.Column("total_tokens_used", sa.Integer(), server_default="0"),
        sa.Column("llm_providers_used", sa.JSON(), nullable=True),
        sa.Column("enable_ai_generation", sa.Boolean(), server_default="true"),
        sa.Column("archimate_mode", sa.String(20), server_default="standard"),
        sa.Column("auto_approve_high_confidence", sa.Boolean(), server_default="false"),
        sa.Column("confidence_threshold", sa.Float(), server_default="0.85"),
        sa.Column("custom_field_mappings", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_uuid"),
    )
    op.create_index("ix_batch_import_job_user_status", "batch_import_job", ["user_id", "status"])
    op.create_index("ix_batch_import_job_created", "batch_import_job", ["created_at"])

    # BatchImportBatch table
    op.create_table(
        "batch_import_batch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("batch_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("total_applications", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_applications", sa.Integer(), server_default="0"),
        sa.Column("successful_applications", sa.Integer(), server_default="0"),
        sa.Column("failed_applications", sa.Integer(), server_default="0"),
        sa.Column("current_application_name", sa.String(255), nullable=True),
        sa.Column("total_elements_generated", sa.Integer(), server_default="0"),
        sa.Column("elements_approved", sa.Integer(), server_default="0"),
        sa.Column("elements_rejected", sa.Integer(), server_default="0"),
        sa.Column("batch_cost_usd", sa.Numeric(10, 4), server_default="0"),
        sa.Column("batch_tokens_used", sa.Integer(), server_default="0"),
        sa.Column("batch_llm_calls", sa.Integer(), server_default="0"),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("max_retries", sa.Integer(), server_default="3"),
        sa.Column("priority", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["batch_import_job.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_batch_import_batch_job_status", "batch_import_batch", ["job_id", "status"])
    op.create_index(
        "ix_batch_import_batch_status_priority", "batch_import_batch", ["status", "priority"]
    )

    # BatchImportApplication table
    op.create_table(
        "batch_import_application",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("source_data", sa.JSON(), nullable=False),
        sa.Column("application_name", sa.String(255), nullable=False),
        sa.Column("application_description", sa.Text(), nullable=True),
        sa.Column("application_type", sa.String(100), nullable=True),
        sa.Column("vendor_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("committed_application_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("elements_generated", sa.Integer(), server_default="0"),
        sa.Column("average_confidence_score", sa.Float(), nullable=True),
        sa.Column("processing_time_seconds", sa.Float(), nullable=True),
        sa.Column("llm_calls", sa.Integer(), server_default="0"),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 4), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], ["batch_import_batch.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["committed_application_id"],
            ["application_components.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_batch_import_app_batch_status", "batch_import_application", ["batch_id", "status"]
    )
    op.create_index("ix_batch_import_app_name", "batch_import_application", ["application_name"])

    # BatchImportElement table
    op.create_table(
        "batch_import_element",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("element_uuid", sa.String(36), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("element_type", sa.String(50), nullable=False),
        sa.Column("element_subtype", sa.String(50), nullable=True),
        sa.Column("element_name", sa.String(255), nullable=False),
        sa.Column("element_description", sa.Text(), nullable=True),
        sa.Column("element_data", sa.JSON(), nullable=False),
        sa.Column("archimate_layer", sa.String(50), nullable=True),
        sa.Column("generated_by_model", sa.String(100), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("generation_prompt_hash", sa.String(64), nullable=True),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("approved_by_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("original_data", sa.JSON(), nullable=True),
        sa.Column("modified_data", sa.JSON(), nullable=True),
        sa.Column("is_modified", sa.Boolean(), server_default="false"),
        sa.Column("is_committed", sa.Boolean(), server_default="false"),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.Column("committed_record_id", sa.Integer(), nullable=True),
        sa.Column("committed_table", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["batch_id"], ["batch_import_batch.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["application_id"], ["batch_import_application.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("element_uuid"),
    )
    op.create_index("ix_batch_import_element_batch", "batch_import_element", ["batch_id"])
    op.create_index("ix_batch_import_element_app", "batch_import_element", ["application_id"])
    op.create_index("ix_batch_import_element_type", "batch_import_element", ["element_type"])
    op.create_index("ix_batch_import_element_approval", "batch_import_element", ["approval_status"])
    op.create_index("ix_batch_import_element_layer", "batch_import_element", ["archimate_layer"])

    # BatchImportCheckpoint table
    op.create_table(
        "batch_import_checkpoint",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("checkpoint_type", sa.String(50), nullable=False),
        sa.Column("checkpoint_name", sa.String(100), nullable=True),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("checkpoint_data", sa.JSON(), nullable=True),
        sa.Column("elements_staged", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["batch_id"], ["batch_import_batch.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["application_id"], ["batch_import_application.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_batch_import_checkpoint_batch", "batch_import_checkpoint", ["batch_id", "created_at"]
    )


def downgrade():
    op.drop_table("batch_import_checkpoint")
    op.drop_table("batch_import_element")
    op.drop_table("batch_import_application")
    op.drop_table("batch_import_batch")
    op.drop_table("batch_import_job")

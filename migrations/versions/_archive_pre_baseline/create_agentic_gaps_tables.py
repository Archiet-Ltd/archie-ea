"""Create agentic gaps tables

Revision ID: agentic_gaps_001
Revises: a1b2c3d4e5f6
Create Date: 2026-01-10 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "agentic_gaps_001"
down_revision = "4f32aa43ea3e"
branch_labels = None
depends_on = None


def upgrade():
    # Create agent_execution_history table
    op.create_table(
        "agent_execution_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("architecture_id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column("execution_type", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("result_data", sa.Text(), nullable=True),
        sa.Column("models_created", sa.Text(), nullable=True),
        sa.Column("services_created", sa.Text(), nullable=True),
        sa.Column("errors", sa.Text(), nullable=True),
        sa.Column("code_generated", sa.Text(), nullable=True),
        sa.Column("requires_review", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("reviewed", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("rollback_available", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("rolled_back", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.Column("rolled_back_by_id", sa.Integer(), nullable=True),
        sa.Column("executed_by_id", sa.Integer(), nullable=False),
        sa.Column("configuration", sa.Text(), nullable=True),
        sa.Column("metadata", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["executed_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["rolled_back_by_id"],
            ["users.id"],
        ),
    )

    # Create indexes
    op.create_index(
        "ix_agent_execution_history_architecture_id",
        "agent_execution_history",
        ["architecture_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_execution_history_agent_name",
        "agent_execution_history",
        ["agent_name"],
        unique=False,
    )
    op.create_index(
        "ix_agent_execution_history_started_at",
        "agent_execution_history",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_execution_history_status", "agent_execution_history", ["status"], unique=False
    )
    op.create_index(
        "ix_agent_execution_history_success", "agent_execution_history", ["success"], unique=False
    )
    op.create_index(
        "ix_agent_execution_history_executed_by_id",
        "agent_execution_history",
        ["executed_by_id"],
        unique=False,
    )

    # Create agent_configurations table
    op.create_table(
        "agent_configurations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column(
            "llm_provider", sa.String(length=50), nullable=True, server_default="huggingface"
        ),
        sa.Column("llm_model", sa.String(length=100), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True, server_default="0.7"),
        sa.Column("max_tokens", sa.Integer(), nullable=True, server_default="4000"),
        sa.Column("auto_generate", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("require_review", sa.Boolean(), nullable=True, server_default="true"),
        sa.Column("validate_models", sa.Boolean(), nullable=True, server_default="true"),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default="true"),
        sa.Column("priority", sa.Integer(), nullable=True, server_default="5"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True, server_default="300"),
        sa.Column("depends_on", sa.Text(), nullable=True),
        sa.Column("custom_settings", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_name"),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
        ),
    )

    # Create indexes for agent_configurations
    op.create_index(
        "ix_agent_configurations_agent_name", "agent_configurations", ["agent_name"], unique=True
    )

    # Create agent_schedules table
    op.create_table(
        "agent_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("agent_name", sa.String(length=100), nullable=True),
        sa.Column("architecture_id", sa.Integer(), nullable=False),
        sa.Column("schedule_type", sa.String(length=50), nullable=False),
        sa.Column("schedule_config", sa.Text(), nullable=True),
        sa.Column("trigger_event", sa.String(length=100), nullable=True),
        sa.Column("trigger_conditions", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("notify_on_completion", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("notify_emails", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
    )

    # Create indexes for agent_schedules
    op.create_index(
        "ix_agent_schedules_architecture_id", "agent_schedules", ["architecture_id"], unique=False
    )
    op.create_index("ix_agent_schedules_enabled", "agent_schedules", ["enabled"], unique=False)
    op.create_index(
        "ix_agent_schedules_next_run_at", "agent_schedules", ["next_run_at"], unique=False
    )


def downgrade():
    op.drop_index("ix_agent_schedules_next_run_at", table_name="agent_schedules")
    op.drop_index("ix_agent_schedules_enabled", table_name="agent_schedules")
    op.drop_index("ix_agent_schedules_architecture_id", table_name="agent_schedules")
    op.drop_table("agent_schedules")

    op.drop_index("ix_agent_configurations_agent_name", table_name="agent_configurations")
    op.drop_table("agent_configurations")

    op.drop_index("ix_agent_execution_history_executed_by_id", table_name="agent_execution_history")
    op.drop_index("ix_agent_execution_history_success", table_name="agent_execution_history")
    op.drop_index("ix_agent_execution_history_status", table_name="agent_execution_history")
    op.drop_index("ix_agent_execution_history_started_at", table_name="agent_execution_history")
    op.drop_index("ix_agent_execution_history_agent_name", table_name="agent_execution_history")
    op.drop_index(
        "ix_agent_execution_history_architecture_id", table_name="agent_execution_history"
    )
    op.drop_table("agent_execution_history")

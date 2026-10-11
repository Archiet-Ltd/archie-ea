"""
Database Migration for Roadmap Models
Create tables for enhanced roadmap functionality
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "001_create_roadmap_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create roadmap tables"""

    # Create work_package_dependencies table
    op.create_table(
        "work_package_dependencies",
        sa.Column("work_package_id", sa.Integer(), nullable=False),
        sa.Column("dependency_id", sa.Integer(), nullable=False),
        sa.Column("dependency_type", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["dependency_id"],
            ["implementation_work_packages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_package_id"],
            ["implementation_work_packages.id"],
        ),
        sa.PrimaryKeyConstraint("work_package_id", "dependency_id"),
    )

    # Create work_package_resources table
    op.create_table(
        "work_package_resources",
        sa.Column("work_package_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("allocation_percentage", sa.Float(), nullable=True),
        sa.Column("role", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resources.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_package_id"],
            ["implementation_work_packages.id"],
        ),
        sa.PrimaryKeyConstraint("work_package_id", "resource_id"),
    )

    # Create work_package_capabilities table
    op.create_table(
        "work_package_capabilities",
        sa.Column("work_package_id", sa.Integer(), nullable=False),
        sa.Column("capability_id", sa.Integer(), nullable=False),
        sa.Column("contribution_type", sa.String(length=50), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["capability_id"],
            ["unified_capabilities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_package_id"],
            ["implementation_work_packages.id"],
        ),
        sa.PrimaryKeyConstraint("work_package_id", "capability_id"),
    )

    # Add automation columns to implementation_work_packages
    op.add_column(
        "implementation_work_packages", sa.Column("auto_generated", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "implementation_work_packages", sa.Column("source_data", sa.Text(), nullable=True)
    )
    op.add_column(
        "implementation_work_packages",
        sa.Column("source_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "implementation_work_packages", sa.Column("source_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "implementation_work_packages", sa.Column("confidence_score", sa.Float(), nullable=True)
    )
    op.add_column(
        "implementation_work_packages",
        sa.Column("generation_method", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "implementation_work_packages", sa.Column("last_sync_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "implementation_work_packages",
        sa.Column("sync_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "implementation_work_packages", sa.Column("automation_metadata", sa.Text(), nullable=True)
    )

    # Add automation columns to planning_deliverables
    op.add_column("planning_deliverables", sa.Column("auto_generated", sa.Boolean(), nullable=True))
    op.add_column(
        "planning_deliverables", sa.Column("source_application_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "planning_deliverables",
        sa.Column("generation_method", sa.String(length=100), nullable=True),
    )

    # Add automation columns to implementation_gaps
    op.add_column("implementation_gaps", sa.Column("auto_detected", sa.Boolean(), nullable=True))
    op.add_column(
        "implementation_gaps", sa.Column("detection_method", sa.String(length=100), nullable=True)
    )
    op.add_column("implementation_gaps", sa.Column("confidence_score", sa.Float(), nullable=True))
    op.add_column(
        "implementation_gaps", sa.Column("source_capability_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "implementation_gaps", sa.Column("source_application_id", sa.Integer(), nullable=True)
    )
    op.add_column("implementation_gaps", sa.Column("resolution_strategy", sa.Text(), nullable=True))
    op.add_column(
        "implementation_gaps", sa.Column("estimated_resolution_cost", sa.Float(), nullable=True)
    )
    op.add_column(
        "implementation_gaps", sa.Column("estimated_resolution_time", sa.Integer(), nullable=True)
    )
    op.add_column("implementation_gaps", sa.Column("status", sa.String(length=20), nullable=True))
    op.add_column("implementation_gaps", sa.Column("resolution_date", sa.DateTime(), nullable=True))

    # Add automation columns to implementation_plateaus
    op.add_column(
        "implementation_plateaus", sa.Column("auto_generated", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "implementation_plateaus",
        sa.Column("generation_method", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "implementation_plateaus", sa.Column("source_scenario_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "implementation_plateaus",
        sa.Column("validation_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "implementation_plateaus", sa.Column("validation_criteria", sa.Text(), nullable=True)
    )

    # Create resources table
    op.create_table(
        "resources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=100), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("capacity_percentage", sa.Float(), nullable=True),
        sa.Column("skill_level", sa.String(length=20), nullable=True),
        sa.Column("hourly_rate", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("availability_start", sa.DateTime(), nullable=True),
        sa.Column("availability_end", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_resources_is_active"), "resources", ["is_active"], unique=False)
    op.create_index(op.f("ix_resources_name"), "resources", ["name"], unique=False)

    # Create roadmap_scenarios table
    op.create_table(
        "roadmap_scenarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scenario_type", sa.String(length=50), nullable=True),
        sa.Column("budget_constraint", sa.Float(), nullable=True),
        sa.Column("timeline_constraint", sa.Integer(), nullable=True),
        sa.Column("resource_constraint", sa.Text(), nullable=True),
        sa.Column("total_work_packages", sa.Integer(), nullable=True),
        sa.Column("total_cost", sa.Float(), nullable=True),
        sa.Column("total_duration", sa.Integer(), nullable=True),
        sa.Column("success_probability", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_roadmap_scenarios_created_at"), "roadmap_scenarios", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_roadmap_scenarios_name"), "roadmap_scenarios", ["name"], unique=False)
    op.create_index(
        op.f("ix_roadmap_scenarios_status"), "roadmap_scenarios", ["status"], unique=False
    )

    # Create scenario_work_packages table
    op.create_table(
        "scenario_work_packages",
        sa.Column("scenario_id", sa.Integer(), nullable=False),
        sa.Column("work_package_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["roadmap_scenarios.id"],
        ),
        sa.ForeignKeyConstraint(
            ["work_package_id"],
            ["implementation_work_packages.id"],
        ),
        sa.PrimaryKeyConstraint("scenario_id", "work_package_id"),
    )

    # Create roadmap_audit table
    op.create_table(
        "roadmap_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("old_values", sa.Text(), nullable=True),
        sa.Column("new_values", sa.Text(), nullable=True),
        sa.Column("changed_fields", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("batch_id", sa.String(length=100), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roadmap_audit_batch_id"), "roadmap_audit", ["batch_id"], unique=False)
    op.create_index(
        op.f("ix_roadmap_audit_entity_id"), "roadmap_audit", ["entity_id"], unique=False
    )
    op.create_index(
        op.f("ix_roadmap_audit_entity_type"), "roadmap_audit", ["entity_type"], unique=False
    )
    op.create_index(
        op.f("ix_roadmap_audit_timestamp"), "roadmap_audit", ["timestamp"], unique=False
    )
    op.create_index(op.f("ix_roadmap_audit_user_id"), "roadmap_audit", ["user_id"], unique=False)


def downgrade():
    """Remove roadmap tables and columns"""

    # Drop association tables
    op.drop_table("scenario_work_packages")
    op.drop_table("work_package_capabilities")
    op.drop_table("work_package_resources")
    op.drop_table("work_package_dependencies")

    # Drop main tables
    op.drop_table("roadmap_audit")
    op.drop_table("roadmap_scenarios")
    op.drop_table("resources")

    # Remove automation columns from implementation_work_packages
    op.drop_column("implementation_work_packages", "automation_metadata")
    op.drop_column("implementation_work_packages", "sync_status")
    op.drop_column("implementation_work_packages", "last_sync_at")
    op.drop_column("implementation_work_packages", "generation_method")
    op.drop_column("implementation_work_packages", "confidence_score")
    op.drop_column("implementation_work_packages", "source_id")
    op.drop_column("implementation_work_packages", "source_type")
    op.drop_column("implementation_work_packages", "source_data")
    op.drop_column("implementation_work_packages", "auto_generated")

    # Remove automation columns from planning_deliverables
    op.drop_column("planning_deliverables", "generation_method")
    op.drop_column("planning_deliverables", "source_application_id")
    op.drop_column("planning_deliverables", "auto_generated")

    # Remove automation columns from implementation_gaps
    op.drop_column("implementation_gaps", "resolution_date")
    op.drop_column("implementation_gaps", "status")
    op.drop_column("implementation_gaps", "estimated_resolution_time")
    op.drop_column("implementation_gaps", "estimated_resolution_cost")
    op.drop_column("implementation_gaps", "resolution_strategy")
    op.drop_column("implementation_gaps", "source_application_id")
    op.drop_column("implementation_gaps", "source_capability_id")
    op.drop_column("implementation_gaps", "confidence_score")
    op.drop_column("implementation_gaps", "detection_method")
    op.drop_column("implementation_gaps", "auto_detected")

    # Remove automation columns from implementation_plateaus
    op.drop_column("implementation_plateaus", "validation_criteria")
    op.drop_column("implementation_plateaus", "validation_status")
    op.drop_column("implementation_plateaus", "source_scenario_id")
    op.drop_column("implementation_plateaus", "generation_method")
    op.drop_column("implementation_plateaus", "auto_generated")

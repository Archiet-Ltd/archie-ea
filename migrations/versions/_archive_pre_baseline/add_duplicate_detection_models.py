"""
Add Duplicate Detection Models

Migration script for enhanced duplicate application detection system.
Adds tables for multi-dimensional duplicate analysis and consolidation recommendations.
"""

import json
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "add_duplicate_detection_models_2026"
down_revision = "8b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    """Create duplicate detection tables"""

    # Create enum types
    op.execute(
        "CREATE TYPE duplicate_type AS ENUM ('functional', 'technical', 'capability', 'partial', 'data')"
    )
    op.execute("CREATE TYPE process_level AS ENUM ('L0', 'L1', 'L2', 'L3')")

    # Note: business_processes table already exists, so we skip creating it

    # Create duplicate_app_process_mapping table
    op.create_table(
        "duplicate_app_process_mapping",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("business_process_id", sa.Integer(), nullable=False),
        sa.Column("support_type", sa.String(length=30), nullable=False),
        sa.Column("support_percentage", sa.Integer(), nullable=True, default=0),
        sa.Column("criticality", sa.String(length=20), nullable=True),
        sa.Column("integration_complexity", sa.String(length=20), nullable=True),
        sa.Column("integration_type", sa.String(length=30), nullable=True),
        sa.Column("data_flow_direction", sa.String(length=20), nullable=True),
        sa.Column("process_coverage", sa.Integer(), nullable=True),
        sa.Column("automation_contribution", sa.Integer(), nullable=True),
        sa.Column("efficiency_gain", sa.Integer(), nullable=True),
        sa.Column("business_value_score", sa.Integer(), nullable=True),
        sa.Column("user_adoption_rate", sa.Float(), nullable=True),
        sa.Column("error_reduction", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("last_assessed_date", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["application_components.id"],
        ),
        sa.ForeignKeyConstraint(
            ["business_process_id"],
            ["business_processes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create duplicate_detection_runs table
    op.create_table(
        "duplicate_detection_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("run_type", sa.String(length=30), nullable=True),
        sa.Column("similarity_threshold", sa.Float(), nullable=True, default=0.7),
        sa.Column("weighting_config", sa.JSON(), nullable=True),
        sa.Column("analysis_scope", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True, default="pending"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("applications_analyzed", sa.Integer(), nullable=True, default=0),
        sa.Column("duplicate_groups_found", sa.Integer(), nullable=True, default=0),
        sa.Column("total_duplicates_found", sa.Integer(), nullable=True, default=0),
        sa.Column("estimated_savings", sa.Float(), nullable=True),
        sa.Column("similarity_calculations_performed", sa.Integer(), nullable=True, default=0),
        sa.Column("average_similarity_score", sa.Float(), nullable=True),
        sa.Column("processing_rate", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=True, default=0),
        sa.Column("triggered_by", sa.String(length=50), nullable=True),
        sa.Column("ai_model_version", sa.String(length=30), nullable=True),
        sa.Column("confidence_threshold", sa.Float(), nullable=True, default=0.8),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create duplicate_groups table
    op.create_table(
        "duplicate_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "duplicate_type",
            sa.Enum(
                "functional", "technical", "capability", "partial", "data", name="duplicate_type"
            ),
            nullable=False,
        ),
        sa.Column("detection_run_id", sa.Integer(), nullable=False),
        sa.Column("overall_similarity_score", sa.Float(), nullable=False),
        sa.Column("functional_similarity", sa.Float(), nullable=True),
        sa.Column("capability_similarity", sa.Float(), nullable=True),
        sa.Column("technical_similarity", sa.Float(), nullable=True),
        sa.Column("data_similarity", sa.Float(), nullable=True),
        sa.Column("primary_business_process_id", sa.Integer(), nullable=True),
        sa.Column("primary_capability_id", sa.Integer(), nullable=True),
        sa.Column("common_technology_stack", sa.JSON(), nullable=True),
        sa.Column("consolidation_priority", sa.String(length=20), nullable=True),
        sa.Column("consolidation_complexity", sa.String(length=20), nullable=True),
        sa.Column("estimated_savings", sa.Float(), nullable=True),
        sa.Column("consolidation_timeline_months", sa.Integer(), nullable=True),
        sa.Column("business_risk", sa.String(length=20), nullable=True),
        sa.Column("technical_risk", sa.String(length=20), nullable=True),
        sa.Column("migration_risk", sa.String(length=20), nullable=True),
        sa.Column("similarity_factors", sa.JSON(), nullable=True),
        sa.Column("exclusion_reasons", sa.JSON(), nullable=True),
        sa.Column("recommendation_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True, default="identified"),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.ForeignKeyConstraint(
            ["detection_run_id"],
            ["duplicate_detection_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["primary_business_process_id"],
            ["business_processes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["primary_capability_id"],
            ["business_capability.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create duplicate_analyses table
    op.create_table(
        "duplicate_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_1_id", sa.Integer(), nullable=False),
        sa.Column("application_2_id", sa.Integer(), nullable=False),
        sa.Column("duplicate_group_id", sa.Integer(), nullable=True),
        sa.Column("overall_similarity_score", sa.Float(), nullable=False),
        sa.Column("confidence_level", sa.Float(), nullable=True),
        sa.Column("name_similarity", sa.Float(), nullable=True),
        sa.Column("functional_similarity", sa.Float(), nullable=True),
        sa.Column("capability_similarity", sa.Float(), nullable=True),
        sa.Column("technical_similarity", sa.Float(), nullable=True),
        sa.Column("data_similarity", sa.Float(), nullable=True),
        sa.Column("integration_similarity", sa.Float(), nullable=True),
        sa.Column("shared_processes", sa.JSON(), nullable=True),
        sa.Column("process_overlap_percentage", sa.Float(), nullable=True),
        sa.Column("process_similarity_details", sa.JSON(), nullable=True),
        sa.Column("shared_capabilities", sa.JSON(), nullable=True),
        sa.Column("capability_overlap_percentage", sa.Float(), nullable=True),
        sa.Column("capability_similarity_details", sa.JSON(), nullable=True),
        sa.Column("shared_technologies", sa.JSON(), nullable=True),
        sa.Column("technology_stack_similarity", sa.Float(), nullable=True),
        sa.Column("architecture_similarity", sa.Float(), nullable=True),
        sa.Column("integration_pattern_similarity", sa.Float(), nullable=True),
        sa.Column("shared_data_objects", sa.JSON(), nullable=True),
        sa.Column("data_model_similarity", sa.Float(), nullable=True),
        sa.Column("data_flow_similarity", sa.Float(), nullable=True),
        sa.Column("user_base_overlap", sa.Float(), nullable=True),
        sa.Column("usage_pattern_similarity", sa.Float(), nullable=True),
        sa.Column("business_unit_overlap", sa.Float(), nullable=True),
        sa.Column("combined_annual_cost", sa.Float(), nullable=True),
        sa.Column("potential_savings", sa.Float(), nullable=True),
        sa.Column("cost_benefit_ratio", sa.Float(), nullable=True),
        sa.Column("analysis_method", sa.String(length=50), nullable=True),
        sa.Column("analysis_version", sa.String(length=20), nullable=True),
        sa.Column("analysis_timestamp", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("exclusion_factors", sa.JSON(), nullable=True),
        sa.Column("business_constraints", sa.JSON(), nullable=True),
        sa.Column("technical_constraints", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.ForeignKeyConstraint(
            ["application_1_id"],
            ["application_components.id"],
        ),
        sa.ForeignKeyConstraint(
            ["application_2_id"],
            ["application_components.id"],
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_group_id"],
            ["duplicate_groups.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create consolidation_recommendations table
    op.create_table(
        "consolidation_recommendations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("duplicate_group_id", sa.Integer(), nullable=False),
        sa.Column("recommendation_type", sa.String(length=30), nullable=True),
        sa.Column("target_application_id", sa.Integer(), nullable=True),
        sa.Column("target_justification", sa.Text(), nullable=True),
        sa.Column("source_applications", sa.JSON(), nullable=True),
        sa.Column("implementation_approach", sa.String(length=50), nullable=True),
        sa.Column("estimated_timeline_months", sa.Integer(), nullable=True),
        sa.Column("implementation_phases", sa.JSON(), nullable=True),
        sa.Column("implementation_cost", sa.Float(), nullable=True),
        sa.Column("annual_savings", sa.Float(), nullable=True),
        sa.Column("payback_period_months", sa.Integer(), nullable=True),
        sa.Column("roi_percentage", sa.Float(), nullable=True),
        sa.Column("overall_risk_level", sa.String(length=20), nullable=True),
        sa.Column("business_risk_factors", sa.JSON(), nullable=True),
        sa.Column("technical_risk_factors", sa.JSON(), nullable=True),
        sa.Column("mitigation_strategies", sa.JSON(), nullable=True),
        sa.Column("affected_users", sa.Integer(), nullable=True),
        sa.Column("affected_business_processes", sa.JSON(), nullable=True),
        sa.Column("affected_integrations", sa.JSON(), nullable=True),
        sa.Column("data_migration_complexity", sa.String(length=20), nullable=True),
        sa.Column("prerequisite_tasks", sa.JSON(), nullable=True),
        sa.Column("required_capabilities", sa.JSON(), nullable=True),
        sa.Column("dependency_updates", sa.JSON(), nullable=True),
        sa.Column("expected_benefits", sa.JSON(), nullable=True),
        sa.Column("success_metrics", sa.JSON(), nullable=True),
        sa.Column("benefit_timeline_months", sa.Integer(), nullable=True),
        sa.Column("business_stakeholders", sa.JSON(), nullable=True),
        sa.Column("technical_stakeholders", sa.JSON(), nullable=True),
        sa.Column("change_management_requirements", sa.JSON(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("supporting_evidence", sa.JSON(), nullable=True),
        sa.Column("alternative_options", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True, default="proposed"),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("implementation_start_date", sa.DateTime(), nullable=True),
        sa.Column("completion_date", sa.DateTime(), nullable=True),
        sa.Column("ai_model_version", sa.String(length=30), nullable=True),
        sa.Column("analysis_timestamp", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("human_review_required", sa.Boolean(), nullable=True, default=True),
        sa.Column("recommendation_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.ForeignKeyConstraint(
            ["duplicate_group_id"],
            ["duplicate_groups.id"],
        ),
        sa.ForeignKeyConstraint(
            ["target_application_id"],
            ["application_components.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create duplicate_group_members association table
    op.create_table(
        "duplicate_group_members",
        sa.Column("duplicate_group_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("role_in_group", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), nullable=True, default=datetime.utcnow),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["application_components.id"],
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_group_id"],
            ["duplicate_groups.id"],
        ),
        sa.PrimaryKeyConstraint("duplicate_group_id", "application_id"),
    )


def downgrade():
    """Remove duplicate detection tables"""

    # Drop tables in reverse order of creation
    op.drop_table("duplicate_group_members")
    op.drop_table("consolidation_recommendations")
    op.drop_table("duplicate_analyses")
    op.drop_table("duplicate_groups")
    op.drop_table("duplicate_detection_runs")
    op.drop_table("duplicate_app_process_mapping")
    # Note: business_processes table already exists, so we don't drop it

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS duplicate_type")
    op.execute("DROP TYPE IF EXISTS process_level")

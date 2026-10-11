"""
Solution Architect Workspace - Enterprise Models

Revision ID: solution_architect_workspace_v1
Revises: (put previous migration ID here)
Create Date: 2026-01-26 18:10:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "solution_architect_workspace_v1"
down_revision = "20260125_tech_cap_cols"  # Latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Define enums
    solution_session_status = postgresql.ENUM(
        "draft",
        "in_progress",
        "completed",
        "archived",
        name="solutionsessionstatus",
        create_type=False,
    )
    support_level = postgresql.ENUM(
        "critical", "major", "minor", "nice_to_have", name="supportlevel", create_type=False
    )
    driver_type = postgresql.ENUM(
        "technology", "stakeholder", "external", "internal", name="drivertype", create_type=False
    )
    requirement_type = postgresql.ENUM(
        "functional", "quality", "constraint", name="requirementtype", create_type=False
    )
    constraint_type = postgresql.ENUM(
        "budget",
        "timeline",
        "resource",
        "compliance",
        "technical",
        "organizational",
        name="constrainttype",
        create_type=False,
    )
    recommendation_option_type = postgresql.ENUM(
        "buy",
        "build",
        "reuse",
        "partner",
        "hybrid",
        name="recommendationoptiontype",
        create_type=False,
    )

    # Create enums first
    solution_session_status.create(op.get_bind(), checkfirst=True)
    support_level.create(op.get_bind(), checkfirst=True)
    driver_type.create(op.get_bind(), checkfirst=True)
    requirement_type.create(op.get_bind(), checkfirst=True)
    constraint_type.create(op.get_bind(), checkfirst=True)
    recommendation_option_type.create(op.get_bind(), checkfirst=True)

    # Create solution_analysis_sessions table
    op.create_table(
        "solution_analysis_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", solution_session_status, nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("custom_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_solution_session_status", "solution_analysis_sessions", ["status"])
    op.create_index(
        "idx_solution_session_created_by", "solution_analysis_sessions", ["created_by_id"]
    )
    op.create_index("idx_solution_session_created_at", "solution_analysis_sessions", ["created_at"])

    # Create solution_problem_definitions table
    op.create_table(
        "solution_problem_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("problem_description", sa.Text(), nullable=False),
        sa.Column("business_context", sa.Text(), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("budget_min", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("budget_max", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("budget_currency", sa.String(length=3), nullable=True),
        sa.Column("timeline_months", sa.Integer(), nullable=True),
        sa.Column("target_start_date", sa.DateTime(), nullable=True),
        sa.Column("target_completion_date", sa.DateTime(), nullable=True),
        sa.Column("organization_size", sa.String(length=50), nullable=True),
        sa.Column("industry_vertical", sa.String(length=100), nullable=True),
        sa.Column("geographic_scope", sa.String(length=100), nullable=True),
        sa.Column("user_count", sa.Integer(), nullable=True),
        sa.Column("transaction_volume", sa.Integer(), nullable=True),
        sa.Column("data_volume_gb", sa.Integer(), nullable=True),
        sa.Column("compliance_requirements", sa.JSON(), nullable=True),
        sa.Column("standards_requirements", sa.JSON(), nullable=True),
        sa.Column("existing_technology_stack", sa.JSON(), nullable=True),
        sa.Column("integration_requirements", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["solution_analysis_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )

    # Create solution_capability_mappings table
    op.create_table(
        "solution_capability_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("capability_id", sa.Integer(), nullable=False),
        sa.Column("support_level", support_level, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("coverage_percentage", sa.Float(), nullable=True),
        sa.Column("maturity_current", sa.Integer(), nullable=True),
        sa.Column("maturity_target", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["capability_id"],
            ["business_capability.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["solution_problem_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("problem_id", "capability_id", name="uq_problem_capability"),
    )
    op.create_index(
        "idx_solution_capability_problem", "solution_capability_mappings", ["problem_id"]
    )
    op.create_index(
        "idx_solution_capability_support_level", "solution_capability_mappings", ["support_level"]
    )

    # Create solution_drivers table
    op.create_table(
        "solution_drivers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("driver_type", driver_type, nullable=False),
        sa.Column("impact_level", sa.Integer(), nullable=True),
        sa.Column("urgency", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["solution_problem_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_solution_driver_problem", "solution_drivers", ["problem_id"])
    op.create_index("idx_solution_driver_type", "solution_drivers", ["driver_type"])

    # Create solution_goals table
    op.create_table(
        "solution_goals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_date", sa.DateTime(), nullable=True),
        sa.Column("measurement_criteria", sa.Text(), nullable=True),
        sa.Column("kpis", sa.JSON(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["solution_problem_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_solution_goal_problem", "solution_goals", ["problem_id"])

    # Create solution_requirements table
    op.create_table(
        "solution_requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("requirement_type", requirement_type, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("is_mandatory", sa.Boolean(), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("acceptance_criteria", sa.Text(), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["solution_problem_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_solution_requirement_problem", "solution_requirements", ["problem_id"])
    op.create_index("idx_solution_requirement_type", "solution_requirements", ["requirement_type"])

    # Create solution_constraints table
    op.create_table(
        "solution_constraints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("constraint_type", constraint_type, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("severity", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["solution_problem_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_solution_constraint_problem", "solution_constraints", ["problem_id"])
    op.create_index("idx_solution_constraint_type", "solution_constraints", ["constraint_type"])

    # Create solution_principles table
    op.create_table(
        "solution_principles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("implications", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["solution_problem_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_solution_principle_problem", "solution_principles", ["problem_id"])

    # Create solution_assessments table
    op.create_table(
        "solution_assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("aspect", sa.String(length=200), nullable=False),
        sa.Column("current_state", sa.Text(), nullable=False),
        sa.Column("target_state", sa.Text(), nullable=False),
        sa.Column("gap_analysis", sa.Text(), nullable=True),
        sa.Column("gap_severity", sa.Integer(), nullable=True),
        sa.Column("assessed_by", sa.String(length=200), nullable=True),
        sa.Column("assessed_at", sa.DateTime(), nullable=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["problem_id"],
            ["solution_problem_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_solution_assessment_problem", "solution_assessments", ["problem_id"])

    # Create solution_recommendations table
    op.create_table(
        "solution_recommendations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("option_type", recommendation_option_type, nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("estimated_cost_min", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("estimated_cost_max", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.Column("timeline_months", sa.Integer(), nullable=True),
        sa.Column("pros", sa.JSON(), nullable=True),
        sa.Column("cons", sa.JSON(), nullable=True),
        sa.Column("risks", sa.JSON(), nullable=True),
        sa.Column("next_steps", sa.JSON(), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("data_sources", sa.JSON(), nullable=True),
        sa.Column("vendor_products", sa.JSON(), nullable=True),
        sa.Column("existing_apps", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("generated_by_model", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["solution_analysis_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_solution_rec_session", "solution_recommendations", ["session_id"])
    op.create_index("idx_solution_rec_type", "solution_recommendations", ["option_type"])
    op.create_index("idx_solution_rec_rank", "solution_recommendations", ["rank"])

    # Create solution_session_versions table
    op.create_table(
        "solution_session_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_name", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["solution_analysis_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "version_number", name="uq_session_version"),
    )
    op.create_index("idx_solution_version_session", "solution_session_versions", ["session_id"])
    op.create_index("idx_solution_version_created_at", "solution_session_versions", ["created_at"])

    # Create solution_adr_links table
    op.create_table(
        "solution_adr_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("adr_id", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("linked_at", sa.DateTime(), nullable=True),
        sa.Column("linked_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["adr_id"],
            ["architecture_decision_records.id"],
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["solution_analysis_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "adr_id", name="uq_session_adr"),
    )
    op.create_index("idx_solution_adr_session", "solution_adr_links", ["session_id"])
    op.create_index("idx_solution_adr_adr", "solution_adr_links", ["adr_id"])


def downgrade():
    # Drop tables in reverse order
    op.drop_index("idx_solution_adr_adr", table_name="solution_adr_links")
    op.drop_index("idx_solution_adr_session", table_name="solution_adr_links")
    op.drop_table("solution_adr_links")

    op.drop_index("idx_solution_version_created_at", table_name="solution_session_versions")
    op.drop_index("idx_solution_version_session", table_name="solution_session_versions")
    op.drop_table("solution_session_versions")

    op.drop_index("idx_solution_rec_rank", table_name="solution_recommendations")
    op.drop_index("idx_solution_rec_type", table_name="solution_recommendations")
    op.drop_index("idx_solution_rec_session", table_name="solution_recommendations")
    op.drop_table("solution_recommendations")

    op.drop_index("idx_solution_assessment_problem", table_name="solution_assessments")
    op.drop_table("solution_assessments")

    op.drop_index("idx_solution_principle_problem", table_name="solution_principles")
    op.drop_table("solution_principles")

    op.drop_index("idx_solution_constraint_type", table_name="solution_constraints")
    op.drop_index("idx_solution_constraint_problem", table_name="solution_constraints")
    op.drop_table("solution_constraints")

    op.drop_index("idx_solution_requirement_type", table_name="solution_requirements")
    op.drop_index("idx_solution_requirement_problem", table_name="solution_requirements")
    op.drop_table("solution_requirements")

    op.drop_index("idx_solution_goal_problem", table_name="solution_goals")
    op.drop_table("solution_goals")

    op.drop_index("idx_solution_driver_type", table_name="solution_drivers")
    op.drop_index("idx_solution_driver_problem", table_name="solution_drivers")
    op.drop_table("solution_drivers")

    op.drop_index(
        "idx_solution_capability_support_level", table_name="solution_capability_mappings"
    )
    op.drop_index("idx_solution_capability_problem", table_name="solution_capability_mappings")
    op.drop_table("solution_capability_mappings")

    op.drop_table("solution_problem_definitions")

    op.drop_index("idx_solution_session_created_at", table_name="solution_analysis_sessions")
    op.drop_index("idx_solution_session_created_by", table_name="solution_analysis_sessions")
    op.drop_index("idx_solution_session_status", table_name="solution_analysis_sessions")
    op.drop_table("solution_analysis_sessions")

    # Drop enums
    recommendation_option_type = postgresql.ENUM(
        "buy", "build", "reuse", "partner", "hybrid", name="recommendationoptiontype"
    )
    recommendation_option_type.drop(op.get_bind())

    constraint_type = postgresql.ENUM(
        "budget",
        "timeline",
        "resource",
        "compliance",
        "technical",
        "organizational",
        name="constrainttype",
    )
    constraint_type.drop(op.get_bind())

    requirement_type = postgresql.ENUM(
        "functional", "quality", "constraint", name="requirementtype"
    )
    requirement_type.drop(op.get_bind())

    driver_type = postgresql.ENUM(
        "technology", "stakeholder", "external", "internal", name="drivertype"
    )
    driver_type.drop(op.get_bind())

    support_level = postgresql.ENUM(
        "critical", "major", "minor", "nice_to_have", name="supportlevel"
    )
    support_level.drop(op.get_bind())

    solution_session_status = postgresql.ENUM(
        "draft", "in_progress", "completed", "archived", name="solutionsessionstatus"
    )
    solution_session_status.drop(op.get_bind())

"""Add missing ApplicationComponent columns

Revision ID: app_component_cols_001
Revises: agentic_gaps_001, perf_indexes_001
Create Date: 2026-01-14
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "app_component_cols_001"
down_revision = ("agentic_gaps_001", "perf_indexes_001")
branch_labels = None
depends_on = None


def upgrade():
    # Add missing columns to application_components table
    # Using batch mode for better compatibility

    columns_to_add = [
        ("application_code", sa.String(50), True),
        ("application_type", sa.String(100), True),
        ("application_category", sa.String(100), True),
        ("deployment_model", sa.String(100), True),
        ("criticality", sa.String(50), True),
        ("business_purpose", sa.Text(), True),
        ("business_functions", sa.Text(), True),
        ("user_base_size", sa.String(50), True),
        ("user_types", sa.Text(), True),
        ("strategic_importance", sa.String(50), True),
        ("business_value", sa.Text(), True),
        ("competitive_advantage", sa.Text(), True),
        ("differentiation_level", sa.String(50), True),
        ("programming_languages", sa.Text(), True),
        ("database_platforms", sa.Text(), True),
        ("integration_methods", sa.Text(), True),
        ("api_available", sa.Boolean(), True),
        ("api_documentation", sa.Text(), True),
        ("total_cost_of_ownership", sa.Numeric(15, 2), True),
        ("license_cost", sa.Numeric(15, 2), True),
        ("maintenance_cost", sa.Numeric(15, 2), True),
        ("infrastructure_cost", sa.Numeric(15, 2), True),
        ("support_cost", sa.Numeric(15, 2), True),
        ("implementation_cost", sa.Numeric(15, 2), True),
        ("roi_score", sa.Float(), True),
        ("vendor_name", sa.String(200), True),
        ("vendor_type", sa.String(100), True),
        ("contract_type", sa.String(100), True),
        ("contract_expiry_date", sa.Date(), True),
        ("support_level", sa.String(50), True),
        ("lifecycle_status", sa.String(50), True),
        ("implementation_date", sa.Date(), True),
        ("last_major_upgrade", sa.Date(), True),
        ("planned_retirement_date", sa.Date(), True),
        ("technology_age_years", sa.Integer(), True),
        ("availability_target", sa.Float(), True),
        ("availability_actual", sa.Float(), True),
        ("performance_rating", sa.String(50), True),
        ("user_satisfaction_score", sa.Float(), True),
        ("defect_density", sa.Float(), True),
        ("integration_complexity", sa.String(50), True),
        ("number_of_integrations", sa.Integer(), True),
        ("architecture_style", sa.String(100), True),
        ("data_architecture", sa.Text(), True),
        ("data_classification", sa.String(50), True),
        ("security_level", sa.String(50), True),
        ("compliance_requirements", sa.Text(), True),
        ("security_certifications", sa.Text(), True),
        ("application_owner", sa.String(200), True),
        ("business_owner", sa.String(200), True),
        ("technical_owner", sa.String(200), True),
        ("product_manager", sa.String(200), True),
        ("development_team", sa.String(200), True),
        ("support_team", sa.String(200), True),
        ("technical_risk", sa.String(50), True),
        ("business_risk", sa.String(50), True),
        ("vendor_risk", sa.String(50), True),
        ("obsolescence_risk", sa.String(50), True),
        ("manufacturing_critical", sa.Boolean(), True),
        ("manufacturing_processes_supported", sa.Text(), True),
        ("shop_floor_system", sa.Boolean(), True),
        ("real_time_requirements", sa.Boolean(), True),
        ("discovered_by_ai", sa.Boolean(), True),
        ("discovery_confidence", sa.Float(), True),
        ("last_assessed", sa.DateTime(), True),
        ("assessment_notes", sa.Text(), True),
    ]

    # Add columns one by one, ignoring if they already exist
    for col_name, col_type, nullable in columns_to_add:
        try:
            op.add_column(
                "application_components", sa.Column(col_name, col_type, nullable=nullable)
            )
        except Exception:
            # Column may already exist
            pass


def downgrade():
    # Remove added columns
    columns_to_remove = [
        "application_code",
        "application_type",
        "application_category",
        "deployment_model",
        "criticality",
        "business_purpose",
        "business_functions",
        "user_base_size",
        "user_types",
        "strategic_importance",
        "business_value",
        "competitive_advantage",
        "differentiation_level",
        "programming_languages",
        "database_platforms",
        "integration_methods",
        "api_available",
        "api_documentation",
        "total_cost_of_ownership",
        "license_cost",
        "maintenance_cost",
        "infrastructure_cost",
        "support_cost",
        "implementation_cost",
        "roi_score",
        "vendor_name",
        "vendor_type",
        "contract_type",
        "contract_expiry_date",
        "support_level",
        "lifecycle_status",
        "implementation_date",
        "last_major_upgrade",
        "planned_retirement_date",
        "technology_age_years",
        "availability_target",
        "availability_actual",
        "performance_rating",
        "user_satisfaction_score",
        "defect_density",
        "integration_complexity",
        "number_of_integrations",
        "architecture_style",
        "data_architecture",
        "data_classification",
        "security_level",
        "compliance_requirements",
        "security_certifications",
        "application_owner",
        "business_owner",
        "technical_owner",
        "product_manager",
        "development_team",
        "support_team",
        "technical_risk",
        "business_risk",
        "vendor_risk",
        "obsolescence_risk",
        "manufacturing_critical",
        "manufacturing_processes_supported",
        "shop_floor_system",
        "real_time_requirements",
        "discovered_by_ai",
        "discovery_confidence",
        "last_assessed",
        "assessment_notes",
    ]

    for col_name in columns_to_remove:
        try:
            op.drop_column("application_components", col_name)
        except Exception:
            pass

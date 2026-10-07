"""Add strategic analysis fields to vendor_product_apqc_mapping

Revision ID: 004_strategic_apqc
Revises: a1b2c3d4e5f6
Create Date: 2026-01-18 10:00:00.000000

Adds comprehensive strategic analysis capabilities to the VendorProductAPQCMapping table
including coverage assessment, gap analysis, competitive positioning, and implementation metrics.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "004_strategic_apqc"
down_revision = "003_strategic_consolidation_policy"
branch_labels = None
depends_on = None


def upgrade():
    # Add confidence level column
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("confidence_level", sa.String(length=20), nullable=True, server_default="medium"),
    )

    # ===== COVERAGE & CAPABILITY COLUMNS =====
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("coverage_level", sa.String(length=20), nullable=True, server_default="partial"),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("coverage_percentage", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("automation_capability", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("out_of_box_fit", sa.Integer(), nullable=True, server_default="0"),
    )

    # Process level support
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("supports_level_1", sa.Boolean(), nullable=True, server_default="true"),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("supports_level_2", sa.Boolean(), nullable=True, server_default="true"),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("supports_level_3", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("supports_level_4", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("supports_level_5", sa.Boolean(), nullable=True, server_default="false"),
    )

    # ===== CUSTOMIZATION & INTEGRATION COLUMNS =====
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("requires_customization", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("customization_effort", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("customization_scope", sa.Text(), nullable=True)
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column(
            "integration_complexity", sa.String(length=20), nullable=True, server_default="medium"
        ),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("integration_pattern", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("integration_prerequisites", sa.Text(), nullable=True),
    )  # JSON stored as text

    # ===== GAP ANALYSIS COLUMNS =====
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("gaps", sa.Text(), nullable=True)
    )  # JSON stored as text
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("missing_features", sa.Text(), nullable=True)
    )  # JSON stored as text
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("workarounds", sa.Text(), nullable=True)
    )  # JSON stored as text
    op.add_column("vendor_product_apqc_mapping", sa.Column("limitations", sa.Text(), nullable=True))

    # ===== STRATEGIC ANALYSIS COLUMNS =====
    # Industry fit
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("industry_fit", sa.Text(), nullable=True)
    )  # JSON stored as text
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("industry_specific_features", sa.Text(), nullable=True),
    )  # JSON stored as text

    # Market positioning
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("market_position", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("vendor_strength", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("competitive_advantage", sa.Text(), nullable=True)
    )
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("competitive_weaknesses", sa.Text(), nullable=True)
    )

    # Maturity
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("product_maturity", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("feature_roadmap_alignment", sa.Integer(), nullable=True),
    )

    # ===== IMPLEMENTATION METRICS COLUMNS =====
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("typical_implementation_weeks", sa.Integer(), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("implementation_risk", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("change_management_impact", sa.String(length=20), nullable=True),
    )

    # Expected benefits
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("expected_efficiency_gain", sa.Integer(), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("expected_cost_reduction", sa.Integer(), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("expected_quality_improvement", sa.Integer(), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("expected_cycle_time_reduction", sa.Integer(), nullable=True),
    )

    # ===== EVIDENCE & VALIDATION COLUMNS =====
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("reference_customers", sa.Text(), nullable=True)
    )  # JSON stored as text
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("case_study_urls", sa.Text(), nullable=True)
    )  # JSON stored as text
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("vendor_documentation_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("analyst_report_references", sa.Text(), nullable=True),
    )  # JSON stored as text

    # ===== ASSESSMENT METADATA COLUMNS =====
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("mapping_source", sa.String(length=50), nullable=True, server_default="auto"),
    )
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("mapping_rationale", sa.Text(), nullable=True)
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column("assessed_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("assessment_date", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("assessment_notes", sa.Text(), nullable=True)
    )
    op.add_column(
        "vendor_product_apqc_mapping", sa.Column("last_validated", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "vendor_product_apqc_mapping",
        sa.Column(
            "validation_status", sa.String(length=20), nullable=True, server_default="pending"
        ),
    )

    # ===== CREATE INDEXES FOR COMMON QUERIES =====
    op.create_index(
        "ix_vendor_apqc_coverage_level",
        "vendor_product_apqc_mapping",
        ["coverage_level"],
        unique=False,
    )
    op.create_index(
        "ix_vendor_apqc_market_position",
        "vendor_product_apqc_mapping",
        ["market_position"],
        unique=False,
    )
    op.create_index(
        "ix_vendor_apqc_validation_status",
        "vendor_product_apqc_mapping",
        ["validation_status"],
        unique=False,
    )
    op.create_index(
        "ix_vendor_apqc_relevance_coverage",
        "vendor_product_apqc_mapping",
        ["relevance_score", "coverage_percentage"],
        unique=False,
    )


def downgrade():
    # Drop indexes first
    op.drop_index("ix_vendor_apqc_relevance_coverage", table_name="vendor_product_apqc_mapping")
    op.drop_index("ix_vendor_apqc_validation_status", table_name="vendor_product_apqc_mapping")
    op.drop_index("ix_vendor_apqc_market_position", table_name="vendor_product_apqc_mapping")
    op.drop_index("ix_vendor_apqc_coverage_level", table_name="vendor_product_apqc_mapping")

    # Drop assessment metadata columns
    op.drop_column("vendor_product_apqc_mapping", "validation_status")
    op.drop_column("vendor_product_apqc_mapping", "last_validated")
    op.drop_column("vendor_product_apqc_mapping", "assessment_notes")
    op.drop_column("vendor_product_apqc_mapping", "assessment_date")
    op.drop_column("vendor_product_apqc_mapping", "assessed_by")
    op.drop_column("vendor_product_apqc_mapping", "mapping_rationale")
    op.drop_column("vendor_product_apqc_mapping", "mapping_source")

    # Drop evidence columns
    op.drop_column("vendor_product_apqc_mapping", "analyst_report_references")
    op.drop_column("vendor_product_apqc_mapping", "vendor_documentation_url")
    op.drop_column("vendor_product_apqc_mapping", "case_study_urls")
    op.drop_column("vendor_product_apqc_mapping", "reference_customers")

    # Drop implementation metrics columns
    op.drop_column("vendor_product_apqc_mapping", "expected_cycle_time_reduction")
    op.drop_column("vendor_product_apqc_mapping", "expected_quality_improvement")
    op.drop_column("vendor_product_apqc_mapping", "expected_cost_reduction")
    op.drop_column("vendor_product_apqc_mapping", "expected_efficiency_gain")
    op.drop_column("vendor_product_apqc_mapping", "change_management_impact")
    op.drop_column("vendor_product_apqc_mapping", "implementation_risk")
    op.drop_column("vendor_product_apqc_mapping", "typical_implementation_weeks")

    # Drop strategic analysis columns
    op.drop_column("vendor_product_apqc_mapping", "feature_roadmap_alignment")
    op.drop_column("vendor_product_apqc_mapping", "product_maturity")
    op.drop_column("vendor_product_apqc_mapping", "competitive_weaknesses")
    op.drop_column("vendor_product_apqc_mapping", "competitive_advantage")
    op.drop_column("vendor_product_apqc_mapping", "vendor_strength")
    op.drop_column("vendor_product_apqc_mapping", "market_position")
    op.drop_column("vendor_product_apqc_mapping", "industry_specific_features")
    op.drop_column("vendor_product_apqc_mapping", "industry_fit")

    # Drop gap analysis columns
    op.drop_column("vendor_product_apqc_mapping", "limitations")
    op.drop_column("vendor_product_apqc_mapping", "workarounds")
    op.drop_column("vendor_product_apqc_mapping", "missing_features")
    op.drop_column("vendor_product_apqc_mapping", "gaps")

    # Drop customization columns
    op.drop_column("vendor_product_apqc_mapping", "integration_prerequisites")
    op.drop_column("vendor_product_apqc_mapping", "integration_pattern")
    op.drop_column("vendor_product_apqc_mapping", "integration_complexity")
    op.drop_column("vendor_product_apqc_mapping", "customization_scope")
    op.drop_column("vendor_product_apqc_mapping", "customization_effort")
    op.drop_column("vendor_product_apqc_mapping", "requires_customization")

    # Drop coverage columns
    op.drop_column("vendor_product_apqc_mapping", "supports_level_5")
    op.drop_column("vendor_product_apqc_mapping", "supports_level_4")
    op.drop_column("vendor_product_apqc_mapping", "supports_level_3")
    op.drop_column("vendor_product_apqc_mapping", "supports_level_2")
    op.drop_column("vendor_product_apqc_mapping", "supports_level_1")
    op.drop_column("vendor_product_apqc_mapping", "out_of_box_fit")
    op.drop_column("vendor_product_apqc_mapping", "automation_capability")
    op.drop_column("vendor_product_apqc_mapping", "coverage_percentage")
    op.drop_column("vendor_product_apqc_mapping", "coverage_level")
    op.drop_column("vendor_product_apqc_mapping", "confidence_level")

"""Add Capability to Vendor/Application Mapping Models

Revision ID: add_capability_vendor_app_mappings
Revises: 7df707b627d1
Create Date: 2026-01-24

Creates 4 new mapping tables:

1. technical_capability_vendor_mappings
   - TechnicalCapability ↔ VendorProduct
   - Which vendors implement which technical capabilities?

2. unified_capability_application_mappings
   - UnifiedCapability ↔ ApplicationComponent
   - Which applications implement which business capabilities?

3. unified_capability_vendor_organization_mappings
   - UnifiedCapability ↔ VendorOrganization
   - Strategic vendor relationships and partnerships

4. application_vendor_product_mappings
   - ApplicationComponent ↔ VendorProduct
   - Application technology stack and product usage

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "add_capability_vendor_app_mappings"
down_revision = "7df707b627d1"
branch_labels = None
depends_on = None


def upgrade():
    """Create mapping tables"""
    # technical_capability_vendor_mappings
    op.create_table(
        "technical_capability_vendor_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("technical_capability_id", sa.Integer(), nullable=False),
        sa.Column("vendor_product_id", sa.Integer(), nullable=False),
        sa.Column("coverage_percentage", sa.Float(), nullable=True),
        sa.Column("maturity_level", sa.String(50), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("roi_percentage", sa.Float(), nullable=True),
        sa.Column("implementation_status", sa.String(50), nullable=True),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("complexity_level", sa.String(50), nullable=True),
        sa.Column("adoption_stage", sa.String(50), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("compliance_alignment", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["technical_capability_id"],
            ["technical_capabilities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["vendor_product_id"],
            ["vendor_products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "technical_capability_id", "vendor_product_id", name="uq_tech_cap_vendor"
        ),
    )

    # unified_capability_application_mappings
    op.create_table(
        "unified_capability_application_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unified_capability_id", sa.Integer(), nullable=False),
        sa.Column("application_component_id", sa.Integer(), nullable=False),
        sa.Column("support_level", sa.String(50), nullable=True),
        sa.Column("coverage_percentage", sa.Float(), nullable=True),
        sa.Column("health_status", sa.String(50), nullable=True),
        sa.Column("maturity_score", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(50), nullable=True),
        sa.Column("business_criticality", sa.String(50), nullable=True),
        sa.Column("technical_debt_score", sa.Float(), nullable=True),
        sa.Column("integration_complexity", sa.String(50), nullable=True),
        sa.Column("automation_level", sa.Float(), nullable=True),
        sa.Column("optimization_potential", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["unified_capability_id"],
            ["unified_capabilities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["application_component_id"],
            ["application_components.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "unified_capability_id", "application_component_id", name="uq_unified_cap_app"
        ),
    )

    # unified_capability_vendor_organization_mappings
    op.create_table(
        "unified_capability_vendor_organization_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unified_capability_id", sa.Integer(), nullable=False),
        sa.Column("vendor_organization_id", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=True),
        sa.Column("strategic_importance", sa.String(50), nullable=True),
        sa.Column("annual_spend", sa.Float(), nullable=True),
        sa.Column("contract_value", sa.Float(), nullable=True),
        sa.Column("renewal_date", sa.Date(), nullable=True),
        sa.Column("partner_tier", sa.String(50), nullable=True),
        sa.Column("sla_compliance_percentage", sa.Float(), nullable=True),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("business_alignment_score", sa.Float(), nullable=True),
        sa.Column("innovation_contribution", sa.Float(), nullable=True),
        sa.Column("cost_optimization_potential", sa.Float(), nullable=True),
        sa.Column("account_manager", sa.String(255), nullable=True),
        sa.Column("primary_contact", sa.String(255), nullable=True),
        sa.Column("communication_frequency", sa.String(50), nullable=True),
        sa.Column("satisfaction_score", sa.Float(), nullable=True),
        sa.Column("net_promoter_score", sa.Integer(), nullable=True),
        sa.Column("performance_rating", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["unified_capability_id"],
            ["unified_capabilities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["vendor_organization_id"],
            ["vendor_organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "unified_capability_id", "vendor_organization_id", name="uq_unified_cap_vendor_org"
        ),
    )

    # application_vendor_product_mappings
    op.create_table(
        "application_vendor_product_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_component_id", sa.Integer(), nullable=False),
        sa.Column("vendor_product_id", sa.Integer(), nullable=False),
        sa.Column("role_type", sa.String(100), nullable=True),
        sa.Column("criticality", sa.String(50), nullable=True),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("installation_date", sa.Date(), nullable=True),
        sa.Column("license_count", sa.Integer(), nullable=True),
        sa.Column("license_type", sa.String(100), nullable=True),
        sa.Column("annual_license_cost", sa.Float(), nullable=True),
        sa.Column("maintenance_cost", sa.Float(), nullable=True),
        sa.Column("support_level", sa.String(50), nullable=True),
        sa.Column("warranty_end_date", sa.Date(), nullable=True),
        sa.Column("health_status", sa.String(50), nullable=True),
        sa.Column("usage_percentage", sa.Float(), nullable=True),
        sa.Column("upgrade_available", sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column("security_compliance_status", sa.String(50), nullable=True),
        sa.Column("performance_rating", sa.String(50), nullable=True),
        sa.Column("technical_debt_level", sa.String(50), nullable=True),
        sa.Column("integration_complexity", sa.String(50), nullable=True),
        sa.Column("migration_effort_hours", sa.Integer(), nullable=True),
        sa.Column("replacement_risk_score", sa.Float(), nullable=True),
        sa.Column("vendor_relationship_score", sa.Float(), nullable=True),
        sa.Column("roi_score", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["application_component_id"],
            ["application_components.id"],
        ),
        sa.ForeignKeyConstraint(
            ["vendor_product_id"],
            ["vendor_products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_component_id", "vendor_product_id", name="uq_app_vendor_product"
        ),
    )


def downgrade():
    """Drop mapping tables"""
    op.drop_table("application_vendor_product_mappings")
    op.drop_table("unified_capability_vendor_organization_mappings")
    op.drop_table("unified_capability_application_mappings")
    op.drop_table("technical_capability_vendor_mappings")

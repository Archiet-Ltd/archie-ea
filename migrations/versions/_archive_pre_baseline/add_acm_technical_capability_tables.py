"""Add ACM Technical Capability tables

Revision ID: add_acm_tech_caps
Revises:
Create Date: 2026-01-21

Creates tables for Application Capability Model (ACM) with 7 domains:
- technical_capabilities: Main capability table with L0-L4 hierarchy
- technical_capability_business_mapping: Links to business capabilities
- application_technical_capability_mapping: Links to applications
- technical_capability_apqc_mapping: Links to APQC processes
- technical_capability_vendor_mapping: Links to vendor products

Also adds ACM fields to application_components table.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "add_acm_tech_caps"
down_revision = "a5c84df062c3"  # vendor_mapping
branch_labels = None
depends_on = None


def upgrade():
    # Create technical_capabilities table
    op.create_table(
        "technical_capabilities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("code", sa.String(50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("acm_domain", sa.String(50), nullable=False),
        sa.Column("level", sa.String(10), nullable=False, server_default="L1"),
        sa.Column("level_number", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("capability_type", sa.String(50), nullable=True),
        sa.Column("technology_patterns", sa.Text(), nullable=True),
        sa.Column("common_technologies", sa.Text(), nullable=True),
        sa.Column("industry_maturity", sa.String(20), nullable=True),
        sa.Column("complexity", sa.String(20), nullable=True),
        sa.Column("is_differentiating", sa.Boolean(), nullable=True, server_default="0"),
        sa.Column("is_foundational", sa.Boolean(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["parent_id"], ["technical_capabilities.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index("ix_technical_capabilities_name", "technical_capabilities", ["name"])
    op.create_index(
        "ix_technical_capabilities_code", "technical_capabilities", ["code"], unique=True
    )
    op.create_index(
        "ix_technical_capabilities_acm_domain", "technical_capabilities", ["acm_domain"]
    )

    # Create technical_capability_business_mapping table
    op.create_table(
        "technical_capability_business_mapping",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("technical_capability_id", sa.Integer(), nullable=False),
        sa.Column("business_capability_id", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=True, server_default="supports"),
        sa.Column("strength", sa.String(20), nullable=True, server_default="medium"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["technical_capability_id"], ["technical_capabilities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["business_capability_id"], ["business_capability.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "idx_tech_bus_cap_mapping",
        "technical_capability_business_mapping",
        ["technical_capability_id", "business_capability_id"],
        unique=True,
    )

    # Create application_technical_capability_mapping table
    op.create_table(
        "application_technical_capability_mapping",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("technical_capability_id", sa.Integer(), nullable=False),
        sa.Column("capability_coverage", sa.String(20), nullable=True, server_default="partial"),
        sa.Column("maturity_level", sa.String(20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["application_id"], ["application_components.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["technical_capability_id"], ["technical_capabilities.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "idx_app_tech_cap_mapping",
        "application_technical_capability_mapping",
        ["application_id", "technical_capability_id"],
        unique=True,
    )

    # Create technical_capability_apqc_mapping table
    op.create_table(
        "technical_capability_apqc_mapping",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("technical_capability_id", sa.Integer(), nullable=False),
        sa.Column("apqc_process_id", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=True, server_default="implements"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["technical_capability_id"], ["technical_capabilities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["apqc_process_id"], ["apqc_process.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_tech_cap_apqc_mapping",
        "technical_capability_apqc_mapping",
        ["technical_capability_id", "apqc_process_id"],
        unique=True,
    )

    # Create technical_capability_vendor_mapping table
    op.create_table(
        "technical_capability_vendor_mapping",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("technical_capability_id", sa.Integer(), nullable=False),
        sa.Column("vendor_product_id", sa.Integer(), nullable=False),
        sa.Column("capability_coverage", sa.String(20), nullable=True, server_default="partial"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["technical_capability_id"], ["technical_capabilities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["vendor_product_id"], ["vendor_products.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_tech_cap_vendor_mapping",
        "technical_capability_vendor_mapping",
        ["technical_capability_id", "vendor_product_id"],
        unique=True,
    )

    # Add ACM fields to application_components table
    op.add_column("application_components", sa.Column("acm_domains", sa.Text(), nullable=True))
    op.add_column(
        "application_components", sa.Column("acm_primary_domain", sa.String(50), nullable=True)
    )
    op.add_column(
        "application_components", sa.Column("acm_capability_level", sa.String(10), nullable=True)
    )


def downgrade():
    # Remove ACM fields from application_components
    op.drop_column("application_components", "acm_capability_level")
    op.drop_column("application_components", "acm_primary_domain")
    op.drop_column("application_components", "acm_domains")

    # Drop mapping tables
    op.drop_table("technical_capability_vendor_mapping")
    op.drop_table("technical_capability_apqc_mapping")
    op.drop_table("application_technical_capability_mapping")
    op.drop_table("technical_capability_business_mapping")
    op.drop_table("technical_capabilities")

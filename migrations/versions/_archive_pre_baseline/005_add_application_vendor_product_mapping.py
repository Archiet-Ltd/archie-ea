"""Add vendor product mapping to ApplicationComponent (Option B+)

Revision ID: 005_app_vendor_product
Revises: 004_strategic_apqc
Create Date: 2026-01-19 10:00:00.000000

Adds direct FK and M:M junction table for ApplicationComponent to VendorProduct relationships.
This enables proper tracking of which vendor products an application uses.

Changes:
1. Add vendor_product_id FK to application_components (primary vendor product)
2. Create application_component_vendor_products junction table (M:M for all vendor products)
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "005_app_vendor_product"
down_revision = (
    None  # Standalone migration - run manually with flask db upgrade 005_app_vendor_product
)
branch_labels = ("vendor_mapping",)
depends_on = None


def upgrade():
    # 1. Add vendor_product_id FK to application_components
    # This is the primary/main vendor product this application is based on
    op.add_column(
        "application_components", sa.Column("vendor_product_id", sa.Integer(), nullable=True)
    )

    # Create index for performance
    op.create_index(
        "idx_app_component_vendor_product", "application_components", ["vendor_product_id"]
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_app_component_vendor_product",
        "application_components",
        "vendor_products",
        ["vendor_product_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2. Create application_component_vendor_products junction table
    # For M:M relationships (applications can use multiple vendor products)
    op.create_table(
        "application_component_vendor_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_component_id", sa.Integer(), nullable=False, index=True),
        sa.Column("vendor_product_id", sa.Integer(), nullable=False, index=True),
        # Relationship metadata
        sa.Column(
            "relationship_type", sa.String(50), server_default="uses"
        ),  # 'primary', 'integration', 'data_source', 'reporting', 'uses'
        sa.Column(
            "deployment_type", sa.String(50), nullable=True
        ),  # 'production', 'staging', 'development', 'disaster_recovery'
        sa.Column(
            "criticality", sa.String(20), nullable=True
        ),  # 'mission_critical', 'business_critical', 'important', 'supporting'
        sa.Column(
            "usage_percentage", sa.Integer(), nullable=True
        ),  # 0-100: How much of the product's capabilities are used
        sa.Column("implementation_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True
        ),
        # Foreign keys
        sa.ForeignKeyConstraint(
            ["application_component_id"], ["application_components.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["vendor_product_id"], ["vendor_products.id"], ondelete="CASCADE"),
        # Unique constraint to prevent duplicate mappings
        sa.UniqueConstraint(
            "application_component_id", "vendor_product_id", name="uq_app_vendor_product"
        ),
    )

    # Create composite index for common queries
    op.create_index(
        "idx_app_vendor_product_lookup",
        "application_component_vendor_products",
        ["application_component_id", "vendor_product_id"],
    )


def downgrade():
    # Remove junction table
    op.drop_index(
        "idx_app_vendor_product_lookup", table_name="application_component_vendor_products"
    )
    op.drop_table("application_component_vendor_products")

    # Remove FK and column from application_components
    op.drop_constraint(
        "fk_app_component_vendor_product", "application_components", type_="foreignkey"
    )
    op.drop_index("idx_app_component_vendor_product", table_name="application_components")
    op.drop_column("application_components", "vendor_product_id")

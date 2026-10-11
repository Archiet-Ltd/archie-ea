"""Add Architecture CRUD models

Revision ID: arch_crud_001
Revises: create_consolidation_list
Create Date: 2026-01-09 18:00:00.000000

Creates tables for missing ArchiMate 3.2 elements:
- stakeholders (Motivation Layer)
- business_collaborations (Business Layer)
- business_interfaces (Business Layer)
- business_interactions (Business Layer)
- products (Business Layer)

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "arch_crud_001"
down_revision = "create_consolidation_list"  # Latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Create stakeholders table
    op.create_table(
        "stakeholders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archimate_element_id", sa.Integer(), nullable=True),
        sa.Column("stakeholder_type", sa.String(length=50), nullable=True),
        sa.Column("role", sa.String(length=100), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("power_level", sa.String(length=20), nullable=True),
        sa.Column("interest_level", sa.String(length=20), nullable=True),
        sa.Column("influence_score", sa.Integer(), nullable=True),
        sa.Column("engagement_strategy", sa.String(length=50), nullable=True),
        sa.Column("communication_frequency", sa.String(length=30), nullable=True),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("contact_phone", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["archimate_element_id"],
            ["archimate_elements.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
    )
    op.create_index(op.f("ix_stakeholders_name"), "stakeholders", ["name"], unique=False)

    # Create business_collaborations table
    op.create_table(
        "business_collaborations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archimate_element_id", sa.Integer(), nullable=True),
        sa.Column("collaboration_type", sa.String(length=50), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(length=100), nullable=True),
        sa.Column("coordinator_id", sa.Integer(), nullable=True),
        sa.Column("meeting_frequency", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["archimate_element_id"],
            ["archimate_elements.id"],
        ),
        sa.ForeignKeyConstraint(
            ["coordinator_id"],
            ["business_actors.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
    )
    op.create_index(
        op.f("ix_business_collaborations_name"), "business_collaborations", ["name"], unique=False
    )

    # Create business_interfaces table
    op.create_table(
        "business_interfaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archimate_element_id", sa.Integer(), nullable=True),
        sa.Column("interface_type", sa.String(length=50), nullable=True),
        sa.Column("access_method", sa.String(length=50), nullable=True),
        sa.Column("availability", sa.String(length=50), nullable=True),
        sa.Column("exposed_services", sa.Text(), nullable=True),
        sa.Column("technology_stack", sa.Text(), nullable=True),
        sa.Column("authentication_method", sa.String(length=50), nullable=True),
        sa.Column("user_count", sa.Integer(), nullable=True),
        sa.Column("transaction_volume", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["archimate_element_id"],
            ["archimate_elements.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
    )
    op.create_index(
        op.f("ix_business_interfaces_name"), "business_interfaces", ["name"], unique=False
    )

    # Create business_interactions table
    op.create_table(
        "business_interactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archimate_element_id", sa.Integer(), nullable=True),
        sa.Column("interaction_type", sa.String(length=50), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("supporting_process_id", sa.Integer(), nullable=True),
        sa.Column("frequency", sa.String(length=30), nullable=True),
        sa.Column("average_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["archimate_element_id"],
            ["archimate_elements.id"],
        ),
        sa.ForeignKeyConstraint(
            ["supporting_process_id"],
            ["business_processes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
    )
    op.create_index(
        op.f("ix_business_interactions_name"), "business_interactions", ["name"], unique=False
    )

    # Create products table
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archimate_element_id", sa.Integer(), nullable=True),
        sa.Column("product_type", sa.String(length=50), nullable=True),
        sa.Column("product_category", sa.String(length=100), nullable=True),
        sa.Column("target_market", sa.String(length=100), nullable=True),
        sa.Column("value_proposition", sa.Text(), nullable=True),
        sa.Column("pricing_model", sa.String(length=50), nullable=True),
        sa.Column("revenue_model", sa.String(length=50), nullable=True),
        sa.Column("included_services", sa.Text(), nullable=True),
        sa.Column("included_contracts", sa.Text(), nullable=True),
        sa.Column("product_status", sa.String(length=30), nullable=True, server_default="active"),
        sa.Column("launch_date", sa.Date(), nullable=True),
        sa.Column("retirement_date", sa.Date(), nullable=True),
        sa.Column("product_owner_id", sa.Integer(), nullable=True),
        sa.Column("product_manager", sa.String(length=200), nullable=True),
        sa.Column("customer_count", sa.Integer(), nullable=True),
        sa.Column("annual_revenue", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("market_share", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["archimate_element_id"],
            ["archimate_elements.id"],
        ),
        sa.ForeignKeyConstraint(
            ["product_owner_id"],
            ["business_actors.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
    )
    op.create_index(op.f("ix_products_name"), "products", ["name"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_products_name"), table_name="products")
    op.drop_table("products")

    op.drop_index(op.f("ix_business_interactions_name"), table_name="business_interactions")
    op.drop_table("business_interactions")

    op.drop_index(op.f("ix_business_interfaces_name"), table_name="business_interfaces")
    op.drop_table("business_interfaces")

    op.drop_index(op.f("ix_business_collaborations_name"), table_name="business_collaborations")
    op.drop_table("business_collaborations")

    op.drop_index(op.f("ix_stakeholders_name"), table_name="stakeholders")
    op.drop_table("stakeholders")

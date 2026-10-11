"""
Solution Cost Models - Multi-year TCO Analysis

Revision ID: solution_cost_models
Revises: merge_arb_and_workspace
Create Date: 2026-01-27 10:00:00.000000

Creates tables for comprehensive solution cost modeling:
- solution_cost_models: Main cost model entity
- solution_cost_line_items: Individual cost items
- solution_cost_yearly_projections: Year-by-year projections
- solution_cost_comparisons: Multi-option cost comparisons
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "solution_cost_models"
down_revision = "merge_arb_and_workspace"
branch_labels = None
depends_on = None


def upgrade():
    # Create solution_cost_models table
    op.create_table(
        "solution_cost_models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("solution_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True, server_default="USD"),
        sa.Column("projection_years", sa.Integer(), nullable=True, server_default="5"),
        sa.Column("discount_rate", sa.Float(), nullable=True, server_default="0.10"),
        sa.Column("inflation_rate", sa.Float(), nullable=True, server_default="0.03"),
        sa.Column(
            "total_capex", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "total_opex", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "total_tco", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column("npv", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True, server_default="draft"),
        sa.Column("approved_by_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["approved_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["solution_id"],
            ["solutions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cost_model_solution", "solution_cost_models", ["solution_id"])
    op.create_index("idx_cost_model_status", "solution_cost_models", ["status"])
    op.create_index("idx_cost_model_created_at", "solution_cost_models", ["created_at"])

    # Create solution_cost_line_items table
    op.create_table(
        "solution_cost_line_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cost_model_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("cost_type", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("vendor_product_id", sa.Integer(), nullable=True),
        sa.Column("unit_cost", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("frequency", sa.String(length=20), nullable=True, server_default="one_time"),
        sa.Column("start_year", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column("annual_growth_rate", sa.Float(), nullable=True, server_default="0"),
        sa.Column("total_cost", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["cost_model_id"],
            ["solution_cost_models.id"],
        ),
        sa.ForeignKeyConstraint(
            ["vendor_product_id"],
            ["vendor_products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_line_item_cost_model", "solution_cost_line_items", ["cost_model_id"])
    op.create_index("idx_line_item_category", "solution_cost_line_items", ["category"])
    op.create_index("idx_line_item_cost_type", "solution_cost_line_items", ["cost_type"])

    # Create solution_cost_yearly_projections table
    op.create_table(
        "solution_cost_yearly_projections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cost_model_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "capex_hardware", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "capex_software", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "capex_services", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "capex_other", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "capex_total", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "opex_licensing", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "opex_maintenance", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "opex_support", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "opex_infrastructure",
            sa.Numeric(precision=15, scale=2),
            nullable=True,
            server_default="0",
        ),
        sa.Column(
            "opex_personnel", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "opex_other", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "opex_total", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "year_total", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column(
            "cumulative_total", sa.Numeric(precision=15, scale=2), nullable=True, server_default="0"
        ),
        sa.Column("discounted_value", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("actual_total", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("variance", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("variance_explanation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["cost_model_id"],
            ["solution_cost_models.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cost_model_id", "year", name="uix_cost_model_year"),
    )
    op.create_index(
        "idx_yearly_projection_cost_model", "solution_cost_yearly_projections", ["cost_model_id"]
    )
    op.create_index("idx_yearly_projection_year", "solution_cost_yearly_projections", ["year"])

    # Create solution_cost_comparisons table
    op.create_table(
        "solution_cost_comparisons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("compared_models", sa.JSON(), nullable=True),
        sa.Column("lowest_tco_model_id", sa.Integer(), nullable=True),
        sa.Column("lowest_npv_model_id", sa.Integer(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("comparison_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["solution_analysis_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cost_comparison_session", "solution_cost_comparisons", ["session_id"])
    op.create_index("idx_cost_comparison_created_at", "solution_cost_comparisons", ["created_at"])


def downgrade():
    # Drop tables in reverse order
    op.drop_index("idx_cost_comparison_created_at", table_name="solution_cost_comparisons")
    op.drop_index("idx_cost_comparison_session", table_name="solution_cost_comparisons")
    op.drop_table("solution_cost_comparisons")

    op.drop_index("idx_yearly_projection_year", table_name="solution_cost_yearly_projections")
    op.drop_index("idx_yearly_projection_cost_model", table_name="solution_cost_yearly_projections")
    op.drop_table("solution_cost_yearly_projections")

    op.drop_index("idx_line_item_cost_type", table_name="solution_cost_line_items")
    op.drop_index("idx_line_item_category", table_name="solution_cost_line_items")
    op.drop_index("idx_line_item_cost_model", table_name="solution_cost_line_items")
    op.drop_table("solution_cost_line_items")

    op.drop_index("idx_cost_model_created_at", table_name="solution_cost_models")
    op.drop_index("idx_cost_model_status", table_name="solution_cost_models")
    op.drop_index("idx_cost_model_solution", table_name="solution_cost_models")
    op.drop_table("solution_cost_models")

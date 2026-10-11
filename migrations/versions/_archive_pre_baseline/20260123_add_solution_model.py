"""Add solutions table

Revision ID: 20260123_add_solution_model
Revises:
Create Date: 2026-01-23 13:06:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "20260123_add_solution_model"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "solutions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archimate_element_id", sa.Integer(), nullable=True),
        sa.Column("solution_type", sa.String(length=50), nullable=True),
        sa.Column("business_domain", sa.String(length=100), nullable=True),
        sa.Column("complexity_level", sa.String(length=20), nullable=True),
        sa.Column("business_value", sa.Text(), nullable=True),
        sa.Column("target_outcomes", sa.JSON(), nullable=True),
        sa.Column("success_metrics", sa.JSON(), nullable=True),
        sa.Column("scope_description", sa.Text(), nullable=True),
        sa.Column("in_scope_applications", sa.JSON(), nullable=True),
        sa.Column("out_of_scope_applications", sa.JSON(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(15, 2), nullable=True),
        sa.Column("actual_cost", sa.Numeric(15, 2), nullable=True),
        sa.Column("roi_percentage", sa.Float(), nullable=True),
        sa.Column("payback_period_months", sa.Integer(), nullable=True),
        sa.Column("planned_start_date", sa.Date(), nullable=True),
        sa.Column("planned_end_date", sa.Date(), nullable=True),
        sa.Column("actual_start_date", sa.Date(), nullable=True),
        sa.Column("actual_end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True, server_default="planned"),
        sa.Column("deployment_status", sa.String(length=30), nullable=True),
        sa.Column("solution_owner", sa.String(length=255), nullable=True),
        sa.Column("business_sponsor", sa.String(length=255), nullable=True),
        sa.Column("technical_lead", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_table("solutions")

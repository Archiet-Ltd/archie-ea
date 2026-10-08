"""Restore missing ApplicationComponent columns dropped by aa30eca8977b downgrade

Revision ID: 20260222_restore_app_cols
Revises: 20260210_production_audit
Create Date: 2026-02-22
"""

import sqlalchemy as sa
from alembic import op

revision = "20260222_restore_app_cols"
down_revision = "20260210_production_audit"
branch_labels = None
depends_on = None


def upgrade():
    """Add back columns that were dropped by the aa30eca8977b downgrade but are
    still defined on the ApplicationComponent SQLAlchemy model."""

    columns_to_restore = [
        ("development_cost_annual", sa.Float(), True),
        ("maintenance_cost_annual", sa.Float(), True),
        ("license_cost_annual", sa.Float(), True),
        ("infrastructure_cost_monthly", sa.Float(), True),
        ("license_type", sa.String(100), True),
        ("go_live_date", sa.Date(), True),
        ("end_of_life_date", sa.Date(), True),
        ("last_major_release_date", sa.Date(), True),
        ("cost_center", sa.String(50), True),
        ("exposes_api", sa.Boolean(), True),
        ("integration_pattern", sa.String(100), True),
        ("interfaces_count", sa.Integer(), True),
        ("dependencies_count", sa.Integer(), True),
        ("primary_data_store", sa.String(200), True),
        ("database_size_gb", sa.Float(), True),
        ("technical_lead", sa.String(100), True),
        ("architecture_domain", sa.String(100), True),
        ("average_daily_users", sa.Integer(), True),
        ("concurrent_users_max", sa.Integer(), True),
        ("user_count", sa.Integer(), True),
        ("user_type", sa.String(100), True),
        ("frameworks", sa.Text(), True),
        ("cache_technology", sa.String(200), True),
        ("message_queue", sa.String(200), True),
        ("primary_database", sa.String(200), True),
        ("master_data_source", sa.Boolean(), True),
    ]

    for col_name, col_type, nullable in columns_to_restore:
        try:
            op.add_column(
                "application_components",
                sa.Column(col_name, col_type, nullable=nullable),
            )
        except Exception:
            pass


def downgrade():
    columns = [
        "development_cost_annual",
        "maintenance_cost_annual",
        "license_cost_annual",
        "infrastructure_cost_monthly",
        "license_type",
        "go_live_date",
        "end_of_life_date",
        "last_major_release_date",
        "cost_center",
        "exposes_api",
        "integration_pattern",
        "interfaces_count",
        "dependencies_count",
        "primary_data_store",
        "database_size_gb",
        "technical_lead",
        "architecture_domain",
        "average_daily_users",
        "concurrent_users_max",
        "user_count",
        "user_type",
        "frameworks",
        "cache_technology",
        "message_queue",
        "primary_database",
        "master_data_source",
    ]

    for col_name in columns:
        try:
            op.drop_column("application_components", col_name)
        except Exception:
            pass

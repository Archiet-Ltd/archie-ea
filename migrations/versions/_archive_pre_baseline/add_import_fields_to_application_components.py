"""Add import fields to ApplicationComponent

Revision ID: add_import_fields_001
Revises: app_component_cols_001
Create Date: 2026-01-19
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "add_import_fields_001"
down_revision = "app_component_cols_001"
branch_labels = None
depends_on = None


def upgrade():
    # Add import-related columns to application_components table
    # These fields are needed for the auto-mapping functionality

    import_columns = [
        ("imported_capabilities", sa.Text(), True),
        ("application_services", sa.Text(), True),
        ("application_functions_text", sa.Text(), True),
        ("imported_apqc_codes", sa.Text(), True),
    ]

    # Add columns one by one, ignoring if they already exist
    for col_name, col_type, nullable in import_columns:
        try:
            op.add_column(
                "application_components", sa.Column(col_name, col_type, nullable=nullable)
            )
            print(f"Added column: {col_name}")
        except Exception as e:
            # Column may already exist
            print(f"Column {col_name} may already exist: {e}")
            pass


def downgrade():
    # Remove import-related columns
    import_columns = [
        "imported_capabilities",
        "application_services",
        "application_functions_text",
        "imported_apqc_codes",
    ]

    for col_name in import_columns:
        try:
            op.drop_column("application_components", col_name)
            print(f"Dropped column: {col_name}")
        except Exception as e:
            # Column may not exist
            print(f"Column {col_name} may not exist: {e}")
            pass

"""Add support_quality column to application_capability_mapping table

Revision ID: support_quality_001
Revises:
Create Date: 2026-01-15

This migration adds the missing 'support_quality' column to the
application_capability_mapping table to match the SQLAlchemy model definition.

Compatible with: SQLite, PostgreSQL, CockroachDB
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers
revision = "support_quality_001"
down_revision = None
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column already exists"""
    try:
        bind = op.get_bind()
        inspector = inspect(bind)
        columns = [col["name"] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception:
        return False


def table_exists(table_name):
    """Check if a table exists"""
    try:
        bind = op.get_bind()
        inspector = inspect(bind)
        return table_name in inspector.get_table_names()
    except Exception:
        return False


def upgrade():
    """Add support_quality column to application_capability_mapping table"""
    print("\n=== Adding support_quality Column ===\n")

    if not table_exists("application_capability_mapping"):
        print("  Table 'application_capability_mapping' does not exist, skipping")
        return

    # Add support_quality column if it doesn't exist
    if not column_exists("application_capability_mapping", "support_quality"):
        op.add_column(
            "application_capability_mapping",
            sa.Column("support_quality", sa.Integer, nullable=True, server_default="3"),
        )
        print("  Added 'support_quality' column to application_capability_mapping")

        # Update existing records to have default value
        bind = op.get_bind()
        try:
            bind.execute(
                sa.text(
                    """
                UPDATE application_capability_mapping
                SET support_quality = 3
                WHERE support_quality IS NULL
            """
                )
            )
            print("  Updated existing records with default support_quality = 3")
        except Exception as e:
            print(f"  Warning: Could not update existing records: {e}")
    else:
        print("  Column 'support_quality' already exists, skipping")

    print("\n=== support_quality Column Migration Complete ===\n")


def downgrade():
    """Remove support_quality column from application_capability_mapping table"""
    print("\n=== Removing support_quality Column ===\n")

    if not table_exists("application_capability_mapping"):
        print("  Table 'application_capability_mapping' does not exist, skipping")
        return

    # Remove column
    if column_exists("application_capability_mapping", "support_quality"):
        try:
            op.drop_column("application_capability_mapping", "support_quality")
            print("  Dropped 'support_quality' column from application_capability_mapping")
        except Exception as e:
            print(f"  Warning: Could not drop column: {e}")

    print("\n=== support_quality Column Removal Complete ===\n")

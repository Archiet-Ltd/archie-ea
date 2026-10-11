"""Add maturity_level, is_strategic, and notes columns to unified_application_capability_mapping

Revision ID: qa_cap_001_mapping_fields
Revises:
Create Date: 2026-02-24

This migration adds three missing columns to the unified_application_capability_mapping table:
1. maturity_level - Integer (1-5) capability maturity level
2. is_strategic - Boolean flag for strategic capabilities
3. notes - Text field for mapping notes

These columns are required by the capability mapping form and backend code
but were missing from the model schema, causing TypeError on mapping creation.

Compatible with: SQLite, PostgreSQL, CockroachDB
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers
revision = "qa_cap_001_mapping_fields"
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
    """Add maturity_level, is_strategic, and notes columns to unified_application_capability_mapping"""
    print("\n=== Adding Capability Mapping Fields (QA-CAP-001) ===\n")

    if not table_exists("unified_application_capability_mapping"):
        print("  Table 'unified_application_capability_mapping' does not exist, skipping")
        return

    # Add maturity_level column if it doesn't exist
    if not column_exists("unified_application_capability_mapping", "maturity_level"):
        op.add_column(
            "unified_application_capability_mapping",
            sa.Column("maturity_level", sa.Integer, nullable=True),
        )
        print("  Added 'maturity_level' column (Integer, nullable)")
    else:
        print("  Column 'maturity_level' already exists, skipping")

    # Add is_strategic column if it doesn't exist
    if not column_exists("unified_application_capability_mapping", "is_strategic"):
        op.add_column(
            "unified_application_capability_mapping",
            sa.Column("is_strategic", sa.Boolean, nullable=True, server_default="0"),
        )
        print("  Added 'is_strategic' column (Boolean, default=False)")

        # Update existing records to have default value
        bind = op.get_bind()
        try:
            bind.execute(
                sa.text(
                    """
                UPDATE unified_application_capability_mapping
                SET is_strategic = 0
                WHERE is_strategic IS NULL
            """
                )
            )
            print("  Updated existing records with default is_strategic = False")
        except Exception as e:
            print(f"  Warning: Could not update existing records: {e}")
    else:
        print("  Column 'is_strategic' already exists, skipping")

    # Add notes column if it doesn't exist
    if not column_exists("unified_application_capability_mapping", "notes"):
        op.add_column(
            "unified_application_capability_mapping",
            sa.Column("notes", sa.Text, nullable=True),
        )
        print("  Added 'notes' column (Text, nullable)")
    else:
        print("  Column 'notes' already exists, skipping")

    print("\n=== Capability Mapping Fields Migration Complete (QA-CAP-001) ===\n")


def downgrade():
    """Remove maturity_level, is_strategic, and notes columns from unified_application_capability_mapping"""
    print("\n=== Removing Capability Mapping Fields (QA-CAP-001) ===\n")

    if not table_exists("unified_application_capability_mapping"):
        print("  Table 'unified_application_capability_mapping' does not exist, skipping")
        return

    # Remove notes column
    if column_exists("unified_application_capability_mapping", "notes"):
        try:
            op.drop_column("unified_application_capability_mapping", "notes")
            print("  Dropped 'notes' column")
        except Exception as e:
            print(f"  Warning: Could not drop notes column: {e}")

    # Remove is_strategic column
    if column_exists("unified_application_capability_mapping", "is_strategic"):
        try:
            op.drop_column("unified_application_capability_mapping", "is_strategic")
            print("  Dropped 'is_strategic' column")
        except Exception as e:
            print(f"  Warning: Could not drop is_strategic column: {e}")

    # Remove maturity_level column
    if column_exists("unified_application_capability_mapping", "maturity_level"):
        try:
            op.drop_column("unified_application_capability_mapping", "maturity_level")
            print("  Dropped 'maturity_level' column")
        except Exception as e:
            print(f"  Warning: Could not drop maturity_level column: {e}")

    print("\n=== Capability Mapping Fields Removal Complete (QA-CAP-001) ===\n")

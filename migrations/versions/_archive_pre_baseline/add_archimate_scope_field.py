"""Add scope field to archimate_elements for Enterprise vs Application level filtering

Revision ID: archimate_scope_001
Revises:
Create Date: 2026-01-15

This migration adds a 'scope' column to the archimate_elements table to enable
filtering between Enterprise-level and Application-level elements.

Scope values:
- 'enterprise': Organization-wide elements (Strategy, Motivation layers)
- 'application': Application-specific elements (Application, Technology layers)
- 'cross-cutting': Elements that span both levels (Business layer elements)

Compatible with: SQLite, PostgreSQL, CockroachDB
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers
revision = "archimate_scope_001"
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


def index_exists(table_name, index_name):
    """Check if an index already exists"""
    try:
        bind = op.get_bind()
        inspector = inspect(bind)
        indexes = inspector.get_indexes(table_name)
        return any(idx["name"] == index_name for idx in indexes)
    except Exception:
        return False


def upgrade():
    """Add scope column to archimate_elements table"""
    print("\n=== Adding ArchiMate Scope Field ===\n")

    if not table_exists("archimate_elements"):
        print("  Table 'archimate_elements' does not exist, skipping")
        return

    # Add scope column if it doesn't exist
    if not column_exists("archimate_elements", "scope"):
        op.add_column(
            "archimate_elements",
            sa.Column("scope", sa.String(30), nullable=True, server_default="enterprise"),
        )
        print("  Added 'scope' column to archimate_elements")
    else:
        print("  Column 'scope' already exists, skipping")

    # Add index for scope column
    if not index_exists("archimate_elements", "idx_archimate_element_scope"):
        try:
            op.create_index(
                "idx_archimate_element_scope", "archimate_elements", ["scope"], unique=False
            )
            print("  Created index 'idx_archimate_element_scope'")
        except Exception as e:
            print(f"  Failed to create index: {e}")
    else:
        print("  Index 'idx_archimate_element_scope' already exists, skipping")

    # Update existing records based on layer
    # Strategy/Motivation layers -> enterprise
    # Application/Technology layers -> application
    # Business layer -> cross-cutting
    bind = op.get_bind()
    try:
        # Set enterprise scope for Strategy and Motivation layers
        bind.execute(
            sa.text(
                """
            UPDATE archimate_elements
            SET scope = 'enterprise'
            WHERE layer IN ('Strategy', 'Motivation') AND (scope IS NULL OR scope = 'enterprise')
        """
            )
        )

        # Set application scope for Application, Technology, and Physical layers
        bind.execute(
            sa.text(
                """
            UPDATE archimate_elements
            SET scope = 'application'
            WHERE layer IN ('Application', 'Technology', 'Physical') AND scope IS NULL
        """
            )
        )

        # Set cross-cutting scope for Business and Implementation layers
        bind.execute(
            sa.text(
                """
            UPDATE archimate_elements
            SET scope = 'cross-cutting'
            WHERE layer IN ('Business', 'Implementation & Migration') AND scope IS NULL
        """
            )
        )

        print("  Updated existing records with appropriate scope values")
    except Exception as e:
        print(f"  Warning: Could not update existing records: {e}")

    print("\n=== ArchiMate Scope Field Migration Complete ===\n")


def downgrade():
    """Remove scope column from archimate_elements table"""
    print("\n=== Removing ArchiMate Scope Field ===\n")

    if not table_exists("archimate_elements"):
        print("  Table 'archimate_elements' does not exist, skipping")
        return

    # Remove index first
    if index_exists("archimate_elements", "idx_archimate_element_scope"):
        try:
            op.drop_index("idx_archimate_element_scope", table_name="archimate_elements")
            print("  Dropped index 'idx_archimate_element_scope'")
        except Exception as e:
            print(f"  Warning: Could not drop index: {e}")

    # Remove column
    if column_exists("archimate_elements", "scope"):
        try:
            op.drop_column("archimate_elements", "scope")
            print("  Dropped 'scope' column from archimate_elements")
        except Exception as e:
            print(f"  Warning: Could not drop column: {e}")

    print("\n=== ArchiMate Scope Field Removal Complete ===\n")

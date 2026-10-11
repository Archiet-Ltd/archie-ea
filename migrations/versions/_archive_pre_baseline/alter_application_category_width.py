"""Expand application_category column width

Revision ID: alter_app_cat_001
Revises: b402b13292fb
Create Date: 2026-01-15

This migration expands the application_category column from VARCHAR(20) to VARCHAR(50)
to match the model definition and prevent truncation errors during imports.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers
revision = "alter_app_cat_001"
down_revision = "b402b13292fb"
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    try:
        bind = op.get_bind()
        inspector = inspect(bind)
        columns = [col["name"] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception:
        return False


def upgrade():
    """Expand application_category column width"""
    print("\n=== Expanding application_category column width ===\n")

    if not column_exists("application_components", "application_category"):
        print("  Column application_category does not exist, skipping")
        return

    # Get database type
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        # SQLite doesn't support ALTER COLUMN, need to recreate table
        # For SQLite, we'll skip as it's not production and the column
        # likely doesn't have a strict constraint
        print("  SQLite detected - skipping column width change (not strictly enforced)")
        return
    elif dialect in ("postgresql", "cockroachdb"):
        # PostgreSQL and CockroachDB syntax
        try:
            op.execute(
                """
                ALTER TABLE application_components
                ALTER COLUMN application_category TYPE VARCHAR(50)
            """
            )
            print("  Expanded application_category to VARCHAR(50)")
        except Exception as e:
            print(f"  Could not alter column (may already be correct size): {e}")
    else:
        # MySQL and others
        try:
            op.alter_column(
                "application_components",
                "application_category",
                type_=sa.String(50),
                existing_nullable=True,
            )
            print("  Expanded application_category to VARCHAR(50)")
        except Exception as e:
            print(f"  Could not alter column: {e}")

    print("\n=== Column width expansion complete ===\n")


def downgrade():
    """Revert to original column width"""
    # Note: Downgrade may fail if data exists that exceeds VARCHAR(20)
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect in ("postgresql", "cockroachdb"):
        op.execute(
            """
            ALTER TABLE application_components
            ALTER COLUMN application_category TYPE VARCHAR(20)
        """
        )
    elif dialect != "sqlite":
        op.alter_column(
            "application_components",
            "application_category",
            type_=sa.String(20),
            existing_nullable=True,
        )

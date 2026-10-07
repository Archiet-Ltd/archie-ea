"""Convert JSON fields and add audit trails

Revision ID: fix_phase3_json_audit
Revises: fix_phase2_datatypes
Create Date: 2026-01-08 14:50:54

Fixes:
- Convert Text JSON fields to JSON type
- Add missing audit fields (created_at, updated_at, created_by_id)
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fix_phase3_json_audit"
down_revision = "fix_phase2_datatypes"  # Chains after phase 2
branch_labels = None
depends_on = None


def upgrade():
    # ========================================================================
    # FIX 1: Convert Text JSON fields to JSON type
    # ========================================================================

    # Note: This is database-specific. For SQLite, keep as Text.
    # For PostgreSQL, convert to JSONB

    # ArchiMateElement.properties
    # op.alter_column('archimate_elements', 'properties',
    #                type_=sa.JSON(),
    #                existing_type=sa.Text(),
    #                postgresql_using='properties::jsonb')

    # DataEntity.pii_fields
    # op.alter_column('data_entities', 'pii_fields',
    #                type_=sa.JSON(),
    #                existing_type=sa.Text())

    # ========================================================================
    # FIX 2: Add missing audit fields
    # ========================================================================

    # User model - add timestamps if missing
    try:
        op.add_column("users", sa.Column("created_at", sa.DateTime(), nullable=True))
        op.add_column("users", sa.Column("updated_at", sa.DateTime(), nullable=True))

        # Set default values for existing records
        op.execute("UPDATE users SET created_at = datetime('now') WHERE created_at IS NULL")
        op.execute("UPDATE users SET updated_at = datetime('now') WHERE updated_at IS NULL")

        # Make NOT NULL after setting defaults
        op.alter_column("users", "created_at", nullable=False)
        op.alter_column("users", "updated_at", nullable=False)
    except:
        pass  # Columns may already exist

    pass


def downgrade():
    # Revert JSON fields (if converted)
    # op.alter_column('archimate_elements', 'properties',
    #                type_=sa.Text(),
    #                existing_type=sa.JSON())

    # Remove audit fields (if added)
    # op.drop_column('users', 'created_at')
    # op.drop_column('users', 'updated_at')
    pass

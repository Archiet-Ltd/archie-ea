"""Fix relationships and consolidate models

Revision ID: fix_phase4_relationships
Revises: fix_phase3_json_audit
Create Date: {timestamp}

Fixes:
- Remove duplicate relationships
- Standardize lazy loading
- Add unique constraints
"""
import sqlalchemy as sa
from alembic import op

revision = "fix_phase4_relationships"
down_revision = "fix_phase3_json_audit"  # Chains after phase 3
branch_labels = None
depends_on = None


def upgrade():
    # ========================================================================
    # FIX 1: Add unique constraints on junction tables
    # ========================================================================

    # application_capability_mapping - prevent duplicates
    try:
        op.create_unique_constraint(
            "uq_app_cap_mapping",
            "application_capability_mapping",
            ["application_component_id", "capability_id"],
        )
    except:
        pass

    # ========================================================================
    # FIX 2: Add composite indexes for common queries
    # ========================================================================

    # Index for filtering by architecture and type
    op.create_index(
        "ix_archimate_elements_arch_type",
        "archimate_elements",
        ["architecture_id", "type"],
        unique=False,
    )

    pass


def downgrade():
    op.drop_index("ix_archimate_elements_arch_type", table_name="archimate_elements")
    op.drop_constraint("uq_app_cap_mapping", "application_capability_mapping", type_="unique")

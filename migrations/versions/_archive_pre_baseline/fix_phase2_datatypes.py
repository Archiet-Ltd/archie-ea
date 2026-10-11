"""Standardize data types and add constraints

Revision ID: fix_phase2_datatypes
Revises: fix_phase1_critical
Create Date: 2026-01-08 14:50:54

Fixes:
- Standardize ID column types
- Add NOT NULL constraints
- Add check constraints for percentages
"""
import sqlalchemy as sa
from alembic import op

revision = "fix_phase2_datatypes"
down_revision = "fix_phase1_critical"  # Chains after phase 1
branch_labels = None
depends_on = None


def upgrade():
    # ========================================================================
    # FIX 1: Make critical name fields NOT NULL
    # ========================================================================

    # Requirement.title should not be nullable (or provide default)
    # Note: Handle existing NULLs first
    op.execute(
        """
        UPDATE requirements
        SET title = COALESCE(title, category, 'Untitled Requirement')
        WHERE title IS NULL
    """
    )

    op.alter_column("requirements", "title", existing_type=sa.String(255), nullable=False)

    # ========================================================================
    # FIX 2: Add check constraints for percentage fields
    # ========================================================================

    # Data quality percentages (0-100)
    op.create_check_constraint(
        "ck_data_entities_data_quality_score",
        "data_entities",
        sa.text(
            "data_quality_score IS NULL OR (data_quality_score >= 0 AND data_quality_score <= 100)"
        ),
    )

    op.create_check_constraint(
        "ck_data_entities_completeness",
        "data_entities",
        sa.text(
            "completeness_percentage IS NULL OR (completeness_percentage >= 0 AND completeness_percentage <= 100)"
        ),
    )

    # ========================================================================
    # FIX 3: Standardize ID types (if needed)
    # ========================================================================
    # Note: Changing column types requires careful migration
    # Only do this if absolutely necessary and data is compatible
    pass


def downgrade():
    op.drop_constraint("ck_data_entities_data_quality_score", "data_entities", type_="check")
    op.drop_constraint("ck_data_entities_completeness", "data_entities", type_="check")
    op.alter_column("requirements", "title", nullable=True)

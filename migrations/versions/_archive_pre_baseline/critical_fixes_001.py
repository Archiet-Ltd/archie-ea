"""Critical data model fixes

Revision ID: critical_fixes_001
Revises: fix_phase4_relationships
Create Date: 2026-01-08 15:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "critical_fixes_001"
down_revision = "fix_phase4_relationships"
branch_labels = None
depends_on = None


def upgrade():
    """Apply critical fixes"""

    # Add missing indexes on foreign keys
    op.create_index("idx_requirements_stakeholder_id", "requirements", ["stakeholder_id"])
    op.create_index("idx_requirements_driver_id", "requirements", ["driver_id"])
    op.create_index("idx_requirements_goal_id", "requirements", ["goal_id"])
    op.create_index(
        "idx_requirements_application_component_id", "requirements", ["application_component_id"]
    )
    op.create_index(
        "idx_application_capability_coverage_application_component_id",
        "application_capability_coverage",
        ["application_component_id"],
    )
    op.create_index(
        "idx_application_interfaces_provider_application_id",
        "application_interfaces",
        ["provider_application_id"],
    )
    op.create_index(
        "idx_business_capabilities_canonical_capability_id",
        "business_capabilities",
        ["canonical_capability_id"],
    )
    op.create_index(
        "idx_data_entities_owning_capability_id", "data_entities", ["owning_capability_id"]
    )

    # Add missing foreign key constraints
    op.create_foreign_key(
        "fk_app_cap_coverage_app_comp",
        "application_capability_coverage",
        "application_components",
        ["application_component_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Add NOT NULL constraints on critical fields
    op.alter_column(
        "business_capabilities", "name", existing_type=sa.String(length=200), nullable=False
    )
    op.alter_column("requirements", "title", existing_type=sa.String(length=500), nullable=False)

    # Add check constraints
    op.create_check_constraint(
        "chk_business_capabilities_maturity_level_range",
        "business_capabilities",
        sa.text("maturity_level >= 0 AND maturity_level <= 100"),
    )


def downgrade():
    """Remove critical fixes"""

    # Remove check constraints
    op.drop_constraint(
        "chk_business_capabilities_maturity_level_range", "business_capabilities", type_="check"
    )

    # Remove NOT NULL constraints
    op.alter_column("requirements", "title", existing_type=sa.String(length=500), nullable=True)
    op.alter_column(
        "business_capabilities", "name", existing_type=sa.String(length=200), nullable=True
    )

    # Remove foreign key constraints
    op.drop_constraint(
        "fk_app_cap_coverage_app_comp", "application_capability_coverage", type_="foreignkey"
    )

    # Remove indexes
    op.drop_index("idx_data_entities_owning_capability_id", table_name="data_entities")
    op.drop_index(
        "idx_business_capabilities_canonical_capability_id", table_name="business_capabilities"
    )
    op.drop_index(
        "idx_application_interfaces_provider_application_id", table_name="application_interfaces"
    )
    op.drop_index(
        "idx_application_capability_coverage_application_component_id",
        table_name="application_capability_coverage",
    )
    op.drop_index("idx_requirements_application_component_id", table_name="requirements")
    op.drop_index("idx_requirements_goal_id", table_name="requirements")
    op.drop_index("idx_requirements_driver_id", table_name="requirements")
    op.drop_index("idx_requirements_stakeholder_id", table_name="requirements")

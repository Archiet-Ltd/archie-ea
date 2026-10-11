"""Add performance indexes for critical foreign keys and query patterns

Revision ID: perf_indexes_001
Revises:
Create Date: 2026-01-14

This migration adds indexes identified in the database relationship audit:
- Foreign key indexes for high-traffic relationships
- Composite indexes for common query patterns

Compatible with: SQLite, PostgreSQL, CockroachDB
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers
revision = "perf_indexes_001"
down_revision = None
branch_labels = None
depends_on = None


def index_exists(table_name, index_name):
    """Check if an index already exists"""
    try:
        bind = op.get_bind()
        inspector = inspect(bind)
        indexes = inspector.get_indexes(table_name)
        return any(idx["name"] == index_name for idx in indexes)
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


def safe_create_index(index_name, table_name, columns, **kwargs):
    """Create index only if table exists and index doesn't"""
    if not table_exists(table_name):
        print(f"  Skipping {index_name}: table '{table_name}' does not exist")
        return False

    if index_exists(table_name, index_name):
        print(f"  Skipping {index_name}: already exists")
        return False

    try:
        op.create_index(index_name, table_name, columns, **kwargs)
        print(f"  Created {index_name} on {table_name}")
        return True
    except Exception as e:
        print(f"  Failed to create {index_name}: {e}")
        return False


def upgrade():
    """Add performance indexes"""
    print("\n=== Adding Performance Indexes ===\n")

    # =================================================================
    # PRIORITY 1: Critical Foreign Key Indexes
    # =================================================================
    print("Priority 1: Foreign Key Indexes")

    safe_create_index(
        "idx_app_cap_mapping_app_id",
        "application_capability_mapping",
        ["application_component_id"],
        unique=False,
    )
    # Note: This table may use 'business_capability_id' in older schema versions
    # The unified_capability_id column may need a migration first
    safe_create_index(
        "idx_app_cap_mapping_cap_id",
        "application_capability_mapping",
        ["unified_capability_id"],
        unique=False,
    )
    safe_create_index(
        "idx_unified_app_cap_mapping_app_id",
        "unified_application_capability_mapping",
        ["application_component_id"],
        unique=False,
    )
    safe_create_index(
        "idx_unified_app_cap_mapping_cap_id",
        "unified_application_capability_mapping",
        ["unified_capability_id"],
        unique=False,
    )
    safe_create_index(
        "idx_unified_capability_parent_id",
        "unified_capabilities",
        ["parent_capability_id"],
        unique=False,
    )
    safe_create_index(
        "idx_archimate_element_parent_id", "archimate_elements", ["parent_id"], unique=False
    )
    safe_create_index(
        "idx_archimate_element_arch_id", "archimate_elements", ["architecture_id"], unique=False
    )
    safe_create_index(
        "idx_business_capability_parent_id", "business_capabilities", ["parent_id"], unique=False
    )

    # ArchiMate element tables - fix N+1 queries for application detail pages
    archimate_tables = [
        "goals",
        "drivers",
        "requirements",
        "business_actors",
        "business_roles",
        "business_services",
        "business_objects",
        "business_events",
        "application_interfaces",
        "data_objects",
        "technology_services",
        "nodes",
        "artifacts",
        "communication_networks",
        "work_packages",
        "deliverables",
    ]
    for table in archimate_tables:
        safe_create_index(
            f"idx_{table}_app_component_id", table, ["application_component_id"], unique=False
        )

    # =================================================================
    # PRIORITY 2: Composite Indexes for Common Query Patterns
    # =================================================================
    print("\nPriority 2: Composite Indexes")

    # Note: Gap model uses 'resolution_status' not 'gap_status'
    safe_create_index(
        "idx_gap_status_priority", "gaps", ["resolution_status", "priority"], unique=False
    )
    safe_create_index(
        "idx_capability_maturity_status",
        "unified_capabilities",
        ["current_maturity_level", "status"],
        unique=False,
    )
    # Note: technical_risk and business_risk columns may need migration first
    safe_create_index(
        "idx_app_lifecycle_risk",
        "application_components",
        ["lifecycle_status", "technical_risk", "business_risk"],
        unique=False,
    )
    # Note: renewal_date column may need migration first
    safe_create_index(
        "idx_vendor_contract_status_renewal",
        "vendor_contracts",
        ["status", "renewal_date"],
        unique=False,
    )
    safe_create_index(
        "idx_work_package_status_priority", "work_packages", ["status", "priority"], unique=False
    )

    # =================================================================
    # PRIORITY 3: Domain-specific indexes
    # =================================================================
    print("\nPriority 3: Domain-Specific Indexes")

    safe_create_index(
        "idx_archimate_element_type_layer", "archimate_elements", ["type", "layer"], unique=False
    )
    safe_create_index(
        "idx_app_component_type_status",
        "application_components",
        ["component_type", "deployment_status"],
        unique=False,
    )
    # Application list page performance indexes
    safe_create_index(
        "idx_app_deployment_status", "application_components", ["deployment_status"], unique=False
    )
    safe_create_index(
        "idx_app_business_criticality",
        "application_components",
        ["business_criticality"],
        unique=False,
    )
    safe_create_index(
        "idx_app_list_filter",
        "application_components",
        ["deployment_status", "business_criticality", "business_domain"],
        unique=False,
    )
    safe_create_index(
        "idx_similarity_app_pair",
        "application_similarity_analysis",
        ["app_1_id", "app_2_id"],
        unique=False,
    )
    # Note: SimpleDuplicateGroup has no status column, index by duplicate_type instead
    safe_create_index(
        "idx_duplicate_group_type",
        "simple_duplicate_groups",
        ["duplicate_type", "created_at"],
        unique=False,
    )
    safe_create_index(
        "idx_process_mapping_app_id",
        "application_process_mappings",
        ["application_id"],
        unique=False,
    )
    safe_create_index(
        "idx_process_mapping_process_id",
        "application_process_mappings",
        ["business_process_id"],
        unique=False,
    )

    print("\n=== Index Migration Complete ===\n")


def downgrade():
    """Remove performance indexes"""

    # Priority 1 indexes
    op.drop_index(
        "idx_app_cap_mapping_app_id", table_name="application_capability_mapping", if_exists=True
    )
    op.drop_index(
        "idx_app_cap_mapping_cap_id", table_name="application_capability_mapping", if_exists=True
    )
    op.drop_index(
        "idx_unified_app_cap_mapping_app_id",
        table_name="unified_application_capability_mapping",
        if_exists=True,
    )
    op.drop_index(
        "idx_unified_app_cap_mapping_cap_id",
        table_name="unified_application_capability_mapping",
        if_exists=True,
    )
    op.drop_index(
        "idx_unified_capability_parent_id", table_name="unified_capabilities", if_exists=True
    )
    op.drop_index(
        "idx_archimate_element_parent_id", table_name="archimate_elements", if_exists=True
    )
    op.drop_index("idx_archimate_element_arch_id", table_name="archimate_elements", if_exists=True)
    op.drop_index(
        "idx_business_capability_parent_id", table_name="business_capabilities", if_exists=True
    )

    # Priority 2 indexes
    op.drop_index("idx_gap_status_priority_created", table_name="gaps", if_exists=True)
    op.drop_index(
        "idx_capability_maturity_status", table_name="unified_capabilities", if_exists=True
    )
    op.drop_index("idx_app_lifecycle_risk", table_name="application_components", if_exists=True)
    op.drop_index(
        "idx_vendor_contract_status_renewal", table_name="vendor_contracts", if_exists=True
    )
    op.drop_index("idx_work_package_status_priority", table_name="work_packages", if_exists=True)

    # Priority 3 indexes
    op.drop_index(
        "idx_archimate_element_type_layer", table_name="archimate_elements", if_exists=True
    )
    op.drop_index(
        "idx_app_component_type_status", table_name="application_components", if_exists=True
    )
    op.drop_index("idx_app_deployment_status", table_name="application_components", if_exists=True)
    op.drop_index(
        "idx_app_business_criticality", table_name="application_components", if_exists=True
    )
    op.drop_index("idx_app_list_filter", table_name="application_components", if_exists=True)
    op.drop_index(
        "idx_similarity_app_pair", table_name="application_similarity_analysis", if_exists=True
    )
    op.drop_index("idx_duplicate_group_type", table_name="simple_duplicate_groups", if_exists=True)
    op.drop_index(
        "idx_process_mapping_app_id", table_name="application_process_mappings", if_exists=True
    )
    op.drop_index(
        "idx_process_mapping_process_id", table_name="application_process_mappings", if_exists=True
    )

    print("Performance indexes removed")

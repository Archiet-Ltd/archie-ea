"""
Centralized SQLAlchemy Table Definitions

All association tables are defined once in this module to prevent conflicts.
This eliminates the need for extend_existing=True and provides a single source of truth.

Import this module in model files instead of defining tables locally.
"""

from datetime import datetime

from app import db

# ============================================================================
# ASSOCIATION TABLES - Defined Once Only
# ============================================================================

# Data Lineage Mappings
data_lineage_mapping = db.Table(
    "data_lineage_mapping",
    db.Column(
        "source_data_object_id",
        db.Integer,
        db.ForeignKey("application_data_objects.id"),
        primary_key=True,
    ),
    db.Column(
        "target_data_object_id",
        db.Integer,
        db.ForeignKey("application_data_objects.id"),
        primary_key=True,
    ),
    db.Column("data_lineage_id", db.Integer, db.ForeignKey("data_lineage.id"), primary_key=True),
    db.Column(
        "transformation_type", db.String(50), default="direct"
    ),  # direct, transform, aggregate
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

# Business Object <-> Data Lineage Mapping
business_object_data_lineage = db.Table(
    "business_object_data_lineage",
    db.Column(
        "business_object_id", db.Integer, db.ForeignKey("business_objects.id"), primary_key=True
    ),
    db.Column("data_lineage_id", db.Integer, db.ForeignKey("data_lineage.id"), primary_key=True),
    db.Column(
        "relationship_type", db.String(50), default="generates"
    ),  # generates, consumes, references
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

# Physical Model <-> Software System Mapping
physical_model_software_mapping = db.Table(
    "physical_model_software_mapping",
    db.Column(
        "physical_model_id",
        db.Integer,
        db.ForeignKey("physical_model_deployments.id"),
        primary_key=True,
    ),
    db.Column(
        "software_system_id", db.Integer, db.ForeignKey("software_systems.id"), primary_key=True
    ),
    db.Column(
        "deployment_type", db.String(50), default="hosted"
    ),  # hosted, containerized, serverless
    db.Column("environment", db.String(20), default="production"),  # dev, test, staging, production
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

# Node <-> Physical Model Mapping
node_physical_model_mapping = db.Table(
    "node_physical_model_mapping",
    db.Column("node_id", db.Integer, db.ForeignKey("infrastructure_nodes.id"), primary_key=True),
    db.Column(
        "physical_model_id",
        db.Integer,
        db.ForeignKey("physical_model_deployments.id"),
        primary_key=True,
    ),
    db.Column("hosting_type", db.String(50), default="dedicated"),  # dedicated, shared, cloud
    db.Column("resource_allocation", db.Text, nullable=True),  # JSON string of resource allocation
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

# Manufacturing Process <-> Capability Mapping
manufacturing_process_capability_mapping = db.Table(
    "manufacturing_process_capability_mapping",
    db.Column(
        "process_id", db.Integer, db.ForeignKey("manufacturing_processes.id"), primary_key=True
    ),
    db.Column(
        "capability_id", db.Integer, db.ForeignKey("unified_capabilities.id"), primary_key=True
    ),
    db.Column("process_step", db.String(100), nullable=True),
    db.Column(
        "automation_level", db.String(20), default="manual"
    ),  # manual, semi_automated, automated
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

# Framework Configuration <-> Extension Mapping
framework_extension_mapping = db.Table(
    "framework_extension_mapping",
    db.Column(
        "configuration_id",
        db.Integer,
        db.ForeignKey("capability_framework_configurations.id"),
        primary_key=True,
    ),
    db.Column(
        "extension_id", db.Integer, db.ForeignKey("framework_extensions.id"), primary_key=True
    ),
    db.Column("extension_version", db.String(20), default="1.0"),
    db.Column("enabled", db.Boolean, default=True),
    db.Column("configuration_data", db.Text, nullable=True),  # JSON string
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

# User <-> Role Mapping (for RBAC)
user_role_mapping = db.Table(
    "user_role_mapping",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("granted_by", db.Integer, db.ForeignKey("users.id"), nullable=True),
    db.Column("granted_at", db.DateTime, default=datetime.utcnow),
    db.Column("expires_at", db.DateTime, nullable=True),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

# Permission <-> Role Mapping
permission_role_mapping = db.Table(
    "permission_role_mapping",
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("granted_at", db.DateTime, default=datetime.utcnow),
    db.Column("granted_by", db.Integer, db.ForeignKey("users.id"), nullable=True),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

# ============================================================================
# TABLE REGISTRATION HELPER
# ============================================================================


def get_all_association_tables():
    """Return all association table definitions for reference"""
    return {
        "data_lineage_mapping": data_lineage_mapping,
        "business_object_data_lineage": business_object_data_lineage,
        "physical_model_software_mapping": physical_model_software_mapping,
        "node_physical_model_mapping": node_physical_model_mapping,
        "manufacturing_process_capability_mapping": manufacturing_process_capability_mapping,
        "framework_extension_mapping": framework_extension_mapping,
        "user_role_mapping": user_role_mapping,
        "permission_role_mapping": permission_role_mapping,
    }


def validate_table_definitions():
    """Validate that all tables are properly defined with required columns"""
    tables = get_all_association_tables()
    issues = []

    for table_name, table in tables.items():
        # Check for primary key columns
        pk_columns = [col for col in table.columns if col.primary_key]
        if len(pk_columns) == 0:
            issues.append(f"Table {table_name} has no primary key columns")

        # Check for timestamp columns
        timestamp_columns = [
            col for col in table.columns if "created_at" in str(col) or "updated_at" in str(col)
        ]
        if len(timestamp_columns) == 0:
            issues.append(f"Table {table_name} has no timestamp columns")

    return issues


# Auto-validate on import
validation_issues = validate_table_definitions()
if validation_issues:
    print("⚠️  Table definition issues found:")
    for issue in validation_issues:
        print(f"   - {issue}")
else:
    print("✅ All association tables validated successfully")

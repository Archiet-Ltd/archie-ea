"""
Abacus Field Mapping Configuration

Defines comprehensive field mappings for Abacus → A.R.C.I.E transformation.
Implements merge conflict resolution strategy using alias fields.

IMPORTANT - Minerva API Format (per official docs):
- Primary identifier is EEID (not Id)
- Properties returned as array: [{"Name": "...", "Value": "..."}, ...]
- Relationships via OutConnections embedded in Components (not separate endpoint)
- Response limited to 512 entries; must use $skip or @odata.nextLink for pagination

Strategy:
- Abacus imports create/update records with both original and alias fields
- Original field (e.g., 'name'): Current A.R.C.I.E value (may be enriched)
- Alias field (e.g., 'abacus_name'): Abacus source value
- Merge resolution: Keep both, UI can display source and allow override

Fields Priority:
- ABACUS_AUTHORITATIVE: Abacus always wins (name, description, lifecycle_status, owners)
- ARCIE_AUTHORITATIVE: A.R.C.I.E always wins (TCO, rationalization, vendor_analysis, performance_metrics)
- MERGE: Both values preserved (use alias fields)

Data Type Conversions:
- String → String (direct)
- Date strings → datetime.date
- JSON strings → dict/list
- Enum mappings (lifecycle status, etc.)
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional


class FieldMappingRule:
    """Defines a single field mapping rule with transformation and conflict resolution."""

    def __init__(
        self,
        abacus_field: str,
        arcie_field: str,
        abacus_alias_field: Optional[str] = None,
        data_type: str = "string",
        required: bool = False,
        transform: Optional[Callable] = None,
        conflict_strategy: str = "abacus_wins",  # abacus_wins, arcie_wins, merge
        validation: Optional[Callable] = None,
        description: str = "",
    ):
        self.abacus_field = abacus_field
        self.arcie_field = arcie_field
        self.abacus_alias_field = abacus_alias_field or f"abacus_{arcie_field}"
        self.data_type = data_type
        self.required = required
        self.transform = transform
        self.conflict_strategy = conflict_strategy
        self.validation = validation
        self.description = description


# =============================================================================
# TRANSFORMATION FUNCTIONS
# =============================================================================


def parse_date(value: Any) -> Optional[date]:
    """Parse date from various formats."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"]:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def parse_json(value: Any) -> Optional[Any]:
    """Parse JSON string to dict/list."""
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


# The one canonical, case-insensitive lifecycle mapping: standard A.R.C.I.E
# status names plus every raw ABACUS numbered lifecycle code (format like
# "2.1 STRATEGIC", "5. DECOMMISSIONED"), keyed lower-case. Every lifecycle
# consumer that needs to recognize or normalize a lifecycle_status value
# reads this one table rather than keeping its own copy.
LIFECYCLE_STATUS_MAP: Dict[str, str] = {
    # Standard A.R.C.I.E values
    "production": "operational",
    "prod": "operational",
    "live": "operational",
    "active": "operational",
    "operational": "operational",
    "development": "development",
    "dev": "development",
    "in development": "development",
    "testing": "testing",
    "test": "testing",
    "qa": "testing",
    "planning": "planning",
    "planned": "planning",
    "proposed": "planning",
    "deprecated": "deprecated",
    "retiring": "deprecated",
    "end of life": "deprecated",
    "retired": "retired",
    "decommissioned": "retired",
    "sunset": "retired",
    # ABACUS numbered lifecycle codes
    "2.1 strategic": "operational",
    "2.2 tactical": "operational",
    "1. undetermined": "planning",
    "3. sunset": "deprecated",
    "4.1 decom decided": "deprecated",
    "4.2 decom planned": "deprecated",
    "4.3 read-only": "deprecated",
    "4.4 stopped": "deprecated",
    "5. decommissioned": "retired",
}


def normalize_lifecycle_status(value: str) -> str:
    """Normalize lifecycle status to A.R.C.I.E format.

    Handles both standard status names AND raw ABACUS lifecycle codes
    (numbered format like "2.1 STRATEGIC", "5. DECOMMISSIONED", etc.), in
    any case, via the canonical LIFECYCLE_STATUS_MAP above.
    """
    if not value:
        return "operational"

    value_lower = str(value).lower().strip()
    return LIFECYCLE_STATUS_MAP.get(value_lower, "operational")


# Mapping from lifecycle_status to deployment_status for ABACUS-imported apps.
# Used when Properties.Status is absent (common in ABACUS data) and
# deployment_status falls back to the model default "development".
LIFECYCLE_TO_DEPLOYMENT = {
    "operational": "production",
    "production": "production",
    "active": "production",
    "live": "production",
    "development": "development",
    "testing": "testing",
    "planning": "development",
    "deprecated": "retired",
    "retired": "retired",
    "decommissioned": "retired",
    # Raw ABACUS lifecycle codes (lowercase)
    "2.1 strategic": "production",
    "2.2 tactical": "production",
    "1. undetermined": "development",
    "3. sunset": "retired",
    "4.1 decom decided": "retired",
    "4.2 decom planned": "retired",
    "4.3 read-only": "retired",
    "5. decommissioned": "retired",
}


def derive_deployment_from_lifecycle(lifecycle_status: str) -> str:
    """Derive deployment_status from lifecycle_status.

    Call this after field mapping when Properties.Status was absent and
    deployment_status is still the model default 'development'.
    """
    if not lifecycle_status:
        return "development"
    key = str(lifecycle_status).lower().strip()
    return LIFECYCLE_TO_DEPLOYMENT.get(key, "development")


def normalize_criticality(value: str) -> str:
    """Normalize business criticality."""
    if not value:
        return "Medium"

    value_lower = str(value).lower().strip()

    mapping = {
        "critical": "Critical",
        "mission critical": "Critical",
        "high": "High",
        "important": "High",
        "medium": "Medium",
        "moderate": "Medium",
        "low": "Low",
        "supporting": "Low",
    }

    return mapping.get(value_lower, "Medium")


def parse_capability_level(value: Any) -> int:
    """Parse capability level to integer (1, 2, 3)."""
    if not value:
        return 1

    value_str = str(value).upper().strip()

    if "L1" in value_str or "STRATEGIC" in value_str or value_str == "1":
        return 1
    elif "L2" in value_str or "TACTICAL" in value_str or value_str == "2":
        return 2
    elif "L3" in value_str or "OPERATIONAL" in value_str or value_str == "3":
        return 3

    return 1


def clean_string(value: Any) -> Optional[str]:
    """Clean and trim string value."""
    if not value:
        return None
    return str(value).strip() if str(value).strip() else None


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================


def validate_not_empty(value: Any) -> bool:
    """Validate field is not empty or None.
    
    AC-10: Relaxed to accept fallback values. Since we now have a robust fallback chain
    (root Name → Properties → application_code → EEID), we rarely get empty names.
    When we do, it means we have a valid fallback, so we should not reject it.
    
    Returns True if:
    - Value is a non-empty string (after strip)
    - Value is a valid fallback identifier (like EEID)
    """
    if value is None:
        return False
    value_str = str(value).strip()
    # Accept any non-empty string, including fallback values like EEIDs or APP IDs
    return len(value_str) > 0


def validate_url(value: Any) -> bool:
    """Validate URL format."""
    if not value:
        return True  # Optional field
    url_str = str(value).lower()
    return url_str.startswith(("http://", "https://"))


def validate_email(value: Any) -> bool:
    """Validate email format."""
    if not value:
        return True  # Optional field
    return "@" in str(value)


# =============================================================================
# APPLICATION FIELD MAPPINGS
# =============================================================================

APPLICATION_FIELD_MAPPINGS: List[FieldMappingRule] = [
    # Core identity fields - ABACUS AUTHORITATIVE
    # NOTE: Minerva API uses EEID as primary identifier, not Id
    # NOTE: Properties come as array of {Name, Value} objects, parsed by connector
    FieldMappingRule(
        abacus_field="EEID",
        arcie_field="external_id",
        data_type="string",
        required=True,
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Abacus EEID - primary identifier for sync (per Minerva API)",
    ),
    FieldMappingRule(
        abacus_field="Name",
        arcie_field="name",
        abacus_alias_field="abacus_name",
        data_type="string",
        required=True,
        transform=clean_string,
        conflict_strategy="merge",
        validation=validate_not_empty,
        description="Application name - merge strategy allows A.R.C.I.E enrichments",
    ),
    FieldMappingRule(
        abacus_field="Description",
        arcie_field="description",
        abacus_alias_field="abacus_description",
        data_type="text",
        transform=clean_string,
        conflict_strategy="merge",
        description="Application description - merge to preserve both versions",
    ),
    # Classification fields - ABACUS AUTHORITATIVE
    FieldMappingRule(
        abacus_field="Type",
        arcie_field="application_type",
        abacus_alias_field="abacus_application_type",
        data_type="string",
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Application type/category from Abacus taxonomy",
    ),
    FieldMappingRule(
        abacus_field="Properties.ApplicationType",
        arcie_field="application_category",
        data_type="string",
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Detailed application category",
    ),
    FieldMappingRule(
        abacus_field="Properties.LifecycleStatus",
        arcie_field="lifecycle_status",
        abacus_alias_field="abacus_lifecycle_status",
        data_type="string",
        transform=normalize_lifecycle_status,
        conflict_strategy="abacus_wins",
        description="Lifecycle status - normalized to A.R.C.I.E values",
    ),
    FieldMappingRule(
        abacus_field="Properties.Status",
        arcie_field="deployment_status",
        data_type="string",
        transform=normalize_lifecycle_status,
        conflict_strategy="abacus_wins",
        description="Alternative status field in Abacus",
    ),
    # Ownership fields - ABACUS AUTHORITATIVE
    FieldMappingRule(
        abacus_field="Properties.BusinessOwner",
        arcie_field="business_owner",
        abacus_alias_field="abacus_business_owner",
        data_type="string",
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Business owner from Abacus (authoritative)",
    ),
    FieldMappingRule(
        abacus_field="Properties.Owner",
        arcie_field="business_owner",  # Fallback to generic Owner field
        data_type="string",
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Generic owner field (fallback)",
    ),
    FieldMappingRule(
        abacus_field="Properties.TechnicalOwner",
        arcie_field="technical_owner",
        abacus_alias_field="abacus_technical_owner",
        data_type="string",
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Technical owner from Abacus",
    ),
    FieldMappingRule(
        abacus_field="Properties.ApplicationOwner",
        arcie_field="application_owner",
        data_type="string",
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Application owner/manager",
    ),
    # Business context - MERGE STRATEGY
    FieldMappingRule(
        abacus_field="Properties.BusinessDomain",
        arcie_field="business_domain",
        data_type="string",
        transform=clean_string,
        conflict_strategy="merge",
        description="Business domain classification",
    ),
    FieldMappingRule(
        abacus_field="Properties.Domain",
        arcie_field="business_domain",
        data_type="string",
        transform=clean_string,
        conflict_strategy="merge",
        description="Alternative domain field",
    ),
    FieldMappingRule(
        abacus_field="Properties.BusinessCriticality",
        arcie_field="business_criticality",
        data_type="string",
        transform=normalize_criticality,
        conflict_strategy="merge",
        description="Business criticality level",
    ),
    # Technical details - A.R.C.I.E AUTHORITATIVE (more detailed in A.R.C.I.E)
    FieldMappingRule(
        abacus_field="Properties.TechnologyStack",
        arcie_field="technology_stack",
        data_type="json",
        transform=parse_json,
        conflict_strategy="arcie_wins",
        description="Technology stack - A.R.C.I.E has more detail",
    ),
    FieldMappingRule(
        abacus_field="Properties.Vendor",
        arcie_field="vendor_name",
        data_type="string",
        transform=clean_string,
        conflict_strategy="merge",
        description="Vendor name - may link to VendorOrganization",
    ),
    # Lifecycle dates - ABACUS AUTHORITATIVE
    FieldMappingRule(
        abacus_field="Properties.GoLiveDate",
        arcie_field="go_live_date",
        data_type="date",
        transform=parse_date,
        conflict_strategy="abacus_wins",
        description="Go-live date",
    ),
    FieldMappingRule(
        abacus_field="Properties.RetirementDate",
        arcie_field="planned_retirement_date",
        data_type="date",
        transform=parse_date,
        conflict_strategy="abacus_wins",
        description="Planned retirement date",
    ),
    # Metadata - SYSTEM GENERATED
    FieldMappingRule(
        abacus_field="_SYSTEM",
        arcie_field="discovered_by_ai",
        data_type="boolean",
        transform=lambda x: False,  # Always False for Abacus imports
        conflict_strategy="abacus_wins",
        description="Mark as Abacus import, not AI discovery",
    ),
    FieldMappingRule(
        abacus_field="_SYSTEM",
        arcie_field="abacus_source",
        data_type="boolean",
        transform=lambda x: True,  # Always True for Abacus imports
        conflict_strategy="abacus_wins",
        description="Flag indicating Abacus source",
    ),
]


# =============================================================================
# CAPABILITY FIELD MAPPINGS
# =============================================================================

CAPABILITY_FIELD_MAPPINGS: List[FieldMappingRule] = [
    # Core identity - ABACUS AUTHORITATIVE
    # NOTE: Minerva API uses EEID as primary identifier, not Id
    # NOTE: Properties come as array of {Name, Value} objects, parsed by connector
    FieldMappingRule(
        abacus_field="EEID",
        arcie_field="external_id",
        data_type="string",
        required=True,
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Abacus EEID - primary identifier for sync (per Minerva API)",
    ),
    FieldMappingRule(
        abacus_field="Name",
        arcie_field="name",
        abacus_alias_field="abacus_name",
        data_type="string",
        required=True,
        transform=clean_string,
        conflict_strategy="merge",
        validation=validate_not_empty,
        description="Capability name - merge to preserve enrichments",
    ),
    FieldMappingRule(
        abacus_field="Description",
        arcie_field="description",
        abacus_alias_field="abacus_description",
        data_type="text",
        transform=clean_string,
        conflict_strategy="merge",
        description="Capability description - merge both versions",
    ),
    # Hierarchy - ABACUS AUTHORITATIVE
    FieldMappingRule(
        abacus_field="Properties.Level",
        arcie_field="level",
        data_type="integer",
        required=True,
        transform=parse_capability_level,
        conflict_strategy="abacus_wins",
        description="Capability level (1=L1/Strategic, 2=L2/Tactical, 3=L3/Operational)",
    ),
    FieldMappingRule(
        abacus_field="Properties.CapabilityLevel",
        arcie_field="level",
        data_type="integer",
        transform=parse_capability_level,
        conflict_strategy="abacus_wins",
        description="Alternative capability level field",
    ),
    FieldMappingRule(
        abacus_field="Relationships.CompositionRelationship.TargetId",
        arcie_field="parent_capability_id",
        data_type="string",
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Parent capability ID (from CompositionRelationship)",
    ),
    # Classification - MERGE STRATEGY
    FieldMappingRule(
        abacus_field="Properties.Domain",
        arcie_field="business_domain",
        data_type="string",
        transform=clean_string,
        conflict_strategy="merge",
        description="Business domain",
    ),
    FieldMappingRule(
        abacus_field="Properties.BusinessDomain",
        arcie_field="business_domain",
        data_type="string",
        transform=clean_string,
        conflict_strategy="merge",
        description="Alternative business domain field",
    ),
    FieldMappingRule(
        abacus_field="Properties.Category",
        arcie_field="category",
        data_type="string",
        transform=clean_string,
        conflict_strategy="merge",
        description="Capability category",
    ),
    # Strategic attributes - A.R.C.I.E AUTHORITATIVE
    FieldMappingRule(
        abacus_field="Properties.StrategicImportance",
        arcie_field="strategic_importance",
        data_type="string",
        transform=clean_string,
        conflict_strategy="arcie_wins",
        description="Strategic importance - A.R.C.I.E has maturity model",
    ),
    FieldMappingRule(
        abacus_field="Properties.MaturityLevel",
        arcie_field="current_maturity_level",
        data_type="integer",
        conflict_strategy="arcie_wins",
        description="Maturity level - A.R.C.I.E has assessment framework",
    ),
    # Metadata - SYSTEM GENERATED
    FieldMappingRule(
        abacus_field="_SYSTEM",
        arcie_field="discovered_by_ai",
        data_type="boolean",
        transform=lambda x: False,
        conflict_strategy="abacus_wins",
        description="Mark as Abacus import",
    ),
    FieldMappingRule(
        abacus_field="_SYSTEM",
        arcie_field="abacus_source",
        data_type="boolean",
        transform=lambda x: True,
        conflict_strategy="abacus_wins",
        description="Flag indicating Abacus source",
    ),
]


# =============================================================================
# RELATIONSHIP FIELD MAPPINGS
# =============================================================================

RELATIONSHIP_FIELD_MAPPINGS: List[FieldMappingRule] = [
    # NOTE: Minerva API embeds relationships in OutConnections, not separate endpoint
    # Fields come from OutConnections: ConnectionTypeName, SinkComponentName
    FieldMappingRule(
        abacus_field="source_eeid",
        arcie_field="application_id",
        data_type="string",
        required=True,
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Source EEID (from parent application)",
    ),
    FieldMappingRule(
        abacus_field="target_name",
        arcie_field="target_name",
        data_type="string",
        required=True,
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Target name (SinkComponentName from OutConnections)",
    ),
    FieldMappingRule(
        abacus_field="connection_type",
        arcie_field="relationship_type",
        data_type="string",
        transform=clean_string,
        conflict_strategy="abacus_wins",
        description="Connection type (ConnectionTypeName from OutConnections)",
    ),
]


# =============================================================================
# CONFLICT RESOLUTION CONFIGURATION
# =============================================================================

CONFLICT_RESOLUTION_CONFIG = {
    "applications": {
        "abacus_authoritative_fields": [
            "external_id",  # EEID from Minerva API
            "eeid",
            "lifecycle_status",
            "deployment_status",
            "business_owner",
            "technical_owner",
            "application_owner",
            "go_live_date",
            "planned_retirement_date",
        ],
        "arcie_authoritative_fields": [
            "total_cost_of_ownership",
            "license_cost",
            "maintenance_cost",
            "infrastructure_cost",
            "roi_score",
            "rationalization_score",
            "vendor_risk",
            "technical_risk",
            "performance_rating",
            "user_satisfaction_score",
        ],
        "merge_fields": [
            "name",
            "description",
            "business_domain",
            "business_criticality",
            "vendor_name",
        ],
    },
    "capabilities": {
        "abacus_authoritative_fields": [
            "external_id",  # EEID from Minerva API
            "eeid",
            "level",
            "parent_capability_id",
            "business_domain",
            "category",
        ],
        "arcie_authoritative_fields": [
            "current_maturity_level",
            "target_maturity_level",
            "maturity_gap",
            "strategic_importance",
            "business_value",
            "roi_score",
            "performance_score",
        ],
        "merge_fields": ["name", "description"],
    },
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_application_mappings() -> List[FieldMappingRule]:
    """Get all application field mappings."""
    return APPLICATION_FIELD_MAPPINGS


def get_capability_mappings() -> List[FieldMappingRule]:
    """Get all capability field mappings."""
    return CAPABILITY_FIELD_MAPPINGS


def get_relationship_mappings() -> List[FieldMappingRule]:
    """Get all relationship field mappings."""
    return RELATIONSHIP_FIELD_MAPPINGS


def get_conflict_resolution_strategy(entity_type: str, field_name: str) -> str:
    """
    Get conflict resolution strategy for a specific field.

    Args:
        entity_type: "applications" or "capabilities"
        field_name: Name of the field

    Returns:
        Strategy: "abacus_wins", "arcie_wins", or "merge"
    """
    config = CONFLICT_RESOLUTION_CONFIG.get(entity_type, {})

    if field_name in config.get("abacus_authoritative_fields", []):
        return "abacus_wins"
    elif field_name in config.get("arcie_authoritative_fields", []):
        return "arcie_wins"
    elif field_name in config.get("merge_fields", []):
        return "merge"
    else:
        # Default to merge for unknown fields
        return "merge"


def apply_field_mapping(
    abacus_data: Dict[str, Any], mapping_rule: FieldMappingRule
) -> Optional[Any]:
    """
    Apply a single field mapping rule to Abacus data.

    Args:
        abacus_data: Raw data from Abacus API
        mapping_rule: Mapping rule to apply

    Returns:
        Transformed value or None if not found
    """
    # Handle nested properties (e.g., "Properties.BusinessOwner")
    if "." in mapping_rule.abacus_field:
        parts = mapping_rule.abacus_field.split(".")
        value = abacus_data
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
    else:
        value = abacus_data.get(mapping_rule.abacus_field)

    # Apply transformation if defined
    if value is not None and mapping_rule.transform:
        try:
            value = mapping_rule.transform(value)
        except Exception as e:
            # Log transformation error but don't fail
            logging.getLogger(__name__).warning(
                "Transformation error for %s: %s", mapping_rule.abacus_field, e
            )
            return None

    # Validate if validation function defined
    if value is not None and mapping_rule.validation:
        if not mapping_rule.validation(value):
            logging.getLogger(__name__).warning(
                "Validation failed for %s: %s", mapping_rule.abacus_field, value
            )
            return None

    return value


# ---------------------------------------------------------------------------
# OutConnection → ArchiMate Relationship Mappings
# ---------------------------------------------------------------------------

DEFAULT_OUTCONNECTION_MAPPINGS: Dict[str, Dict[str, str]] = {
    # Technology / hosting connections
    "is deployed on": {
        "rel_type": "assignment",
        "source_type": "ApplicationComponent",
        "target_type": "Node",
    },
    "runs on": {
        "rel_type": "assignment",
        "source_type": "ApplicationComponent",
        "target_type": "Node",
    },
    "is hosted on": {
        "rel_type": "assignment",
        "source_type": "ApplicationComponent",
        "target_type": "Node",
    },
    "app is hosted by": {
        "rel_type": "assignment",
        "source_type": "ApplicationComponent",
        "target_type": "Node",
    },
    # Integration / data flow connections
    "integrates with": {
        "rel_type": "flow",
        "source_type": "ApplicationComponent",
        "target_type": "ApplicationComponent",
    },
    "sends data to": {
        "rel_type": "flow",
        "source_type": "ApplicationComponent",
        "target_type": "ApplicationComponent",
    },
    "receives data from": {
        "rel_type": "flow",
        "source_type": "ApplicationComponent",
        "target_type": "ApplicationComponent",
    },
    # Ownership / people connections
    "has business owner": {
        "rel_type": "association",
        "source_type": "BusinessActor",
        "target_type": "ApplicationComponent",
    },
    "has owner": {
        "rel_type": "association",
        "source_type": "BusinessActor",
        "target_type": "ApplicationComponent",
    },
    "has technical owner": {
        "rel_type": "association",
        "source_type": "BusinessActor",
        "target_type": "ApplicationComponent",
    },
    "has it owner": {
        "rel_type": "association",
        "source_type": "BusinessActor",
        "target_type": "ApplicationComponent",
    },
    "app is managed by": {
        "rel_type": "association",
        "source_type": "BusinessActor",
        "target_type": "ApplicationComponent",
    },
    "is managed by": {
        "rel_type": "association",
        "source_type": "BusinessActor",
        "target_type": "ApplicationComponent",
    },
    "is owned by": {
        "rel_type": "association",
        "source_type": "BusinessActor",
        "target_type": "ApplicationComponent",
    },
    # Location / usage connections
    "app is used in country": {
        "rel_type": "association",
        "source_type": "ApplicationComponent",
        "target_type": "Facility",
    },
    "is used in country": {
        "rel_type": "association",
        "source_type": "ApplicationComponent",
        "target_type": "Facility",
    },
    # Access connections
    "is used by": {
        "rel_type": "serving",
        "source_type": "ApplicationComponent",
        "target_type": "BusinessActor",
    },
    "is accessed via": {
        "rel_type": "access",
        "source_type": "ApplicationComponent",
        "target_type": "ApplicationInterface",
    },
    "has access method": {
        "rel_type": "access",
        "source_type": "ApplicationComponent",
        "target_type": "ApplicationInterface",
    },
}


def get_outconnection_mappings(
    external_system=None,
) -> Dict[str, Dict[str, str]]:
    """Get OutConnection type → ArchiMate relationship mappings.

    If an ExternalSystem record has custom mappings in config_json, those
    override the defaults.  Otherwise returns DEFAULT_OUTCONNECTION_MAPPINGS.
    """
    if external_system is not None:
        config = external_system.config_json or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (json.JSONDecodeError, TypeError):
                config = {}
        custom = config.get("outconnection_mappings")
        if custom and isinstance(custom, dict):
            merged = dict(DEFAULT_OUTCONNECTION_MAPPINGS)
            merged.update(custom)
            return merged
    return dict(DEFAULT_OUTCONNECTION_MAPPINGS)


def save_outconnection_mappings(
    external_system, mappings: Dict[str, Dict[str, str]]
) -> None:
    """Persist custom OutConnection mappings to ExternalSystem.config_json."""
    config = external_system.config_json or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (json.JSONDecodeError, TypeError):
            config = {}
    config["outconnection_mappings"] = mappings
    external_system.config_json = config

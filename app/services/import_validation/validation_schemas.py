"""
Validation schemas for ApplicationComponent import fields.
Defines valid enum values, field constraints, and normalization mappings.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.services.application_status import APPLICATION_STATUS_VALUES, OLD_STATUS_WORDS


class FieldType(Enum):
    """Supported field data types"""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    ENUM = "enum"
    EMAIL = "email"
    URL = "url"


@dataclass
class FieldSchema:
    """Schema definition for a single field"""

    name: str
    field_type: FieldType
    required: bool = False
    max_length: Optional[int] = None
    min_length: Optional[int] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: Optional[List[str]] = None
    default_value: Any = None
    description: str = ""


# =============================================================================
# ENUM DEFINITIONS - Valid values extracted from forms.py and constants.py
# =============================================================================

LIFECYCLE_STATUS_VALUES = [
    "planning",
    "development",
    "testing",
    "pilot",
    "production",
    "maintenance",
    "sunset",
    "retired",
]

# The one list of deployment statuses: app/services/application_status.py.
DEPLOYMENT_STATUS_VALUES = list(APPLICATION_STATUS_VALUES)

COMPONENT_TYPE_VALUES = [
    "Web Application",
    "Mobile App",
    "Desktop Application",
    "Microservice",
    "Backend Service",
    "Integration Service",
    "Batch Job",
    "API",
    "Database",
    "Data Warehouse",
    "ETL Pipeline",
    "Reporting Tool",
]

BUSINESS_CRITICALITY_VALUES = ["Critical", "High", "Medium", "Low"]

APPLICATION_CATEGORY_VALUES = [
    "Enterprise Application",
    "Business Application",
    "System Application",
    "Custom",
    "COTS",
    "SaaS",
    "PaaS",
    "IaaS",
]

DATA_CLASSIFICATION_VALUES = ["Public", "Internal", "Confidential", "Restricted"]

ARCHITECTURE_STYLE_VALUES = [
    "Monolithic",
    "Microservices",
    "SOA",
    "Serverless",
    "Event-Driven",
    "Layered",
]

DEPLOYMENT_MODEL_VALUES = ["On-Premise", "Cloud", "Hybrid", "SaaS", "PaaS", "IaaS"]

USER_TYPE_VALUES = ["Internal", "External", "B2B", "B2C", "Mixed"]


# =============================================================================
# NORMALIZATION MAPPINGS - Common variations -> canonical values
# =============================================================================

LIFECYCLE_STATUS_ALIASES: Dict[str, str] = {
    # Production variations
    "prod": "production",
    "live": "production",
    "operational": "production",
    "active": "production",
    "in production": "production",
    "in-production": "production",
    "running": "production",
    "deployed": "production",
    # Development variations
    "dev": "development",
    "developing": "development",
    "in development": "development",
    "in-development": "development",
    "under development": "development",
    # Testing variations
    "test": "testing",
    "qa": "testing",
    "uat": "testing",
    "in testing": "testing",
    "quality assurance": "testing",
    # Retired variations
    "decommissioned": "retired",
    "end-of-life": "retired",
    "eol": "retired",
    "archived": "retired",
    "removed": "retired",
    "inactive": "retired",
    "terminated": "retired",
    # Maintenance variations
    "support": "maintenance",
    "sustaining": "maintenance",
    "maintained": "maintenance",
    "in maintenance": "maintenance",
    # Planning variations
    "planned": "planning",
    "proposal": "planning",
    "concept": "planning",
    "proposed": "planning",
    "in planning": "planning",
    # Sunset variations
    "sunsetting": "sunset",
    "deprecating": "sunset",
    "phasing out": "sunset",
    "phase-out": "sunset",
    "end of support": "sunset",
    # Pilot variations
    "piloting": "pilot",
    "beta": "pilot",
    "trial": "pilot",
}

DEPLOYMENT_STATUS_ALIASES: Dict[str, str] = {
    "prod": "production",
    "live": "production",
    "operational": "production",
    "running": "production",
    "active": "production",
    "done": "production",
    "dev": "development",
    "developing": "development",
    "in dev": "development",
    "under development": "development",
    "test": "testing",
    "qa": "testing",
    "uat": "testing",
    "in test": "testing",
    "in testing": "testing",
    "stage": "staging",
    "preprod": "staging",
    "pre-prod": "staging",
    "pre-production": "staging",
    # Older words (planned, deprecated, retired ...) mean a value on the one list.
    **OLD_STATUS_WORDS,
}

COMPONENT_TYPE_ALIASES: Dict[str, str] = {
    "web app": "Web Application",
    "webapp": "Web Application",
    "web": "Web Application",
    "website": "Web Application",
    "portal": "Web Application",
    "web application": "Web Application",
    "web-application": "Web Application",
    "mobile": "Mobile App",
    "mobile application": "Mobile App",
    "mobile app": "Mobile App",
    "ios": "Mobile App",
    "android": "Mobile App",
    "ios app": "Mobile App",
    "android app": "Mobile App",
    "desktop": "Desktop Application",
    "desktop app": "Desktop Application",
    "desktop application": "Desktop Application",
    "windows app": "Desktop Application",
    "mac app": "Desktop Application",
    "service": "Backend Service",
    "backend": "Backend Service",
    "backend service": "Backend Service",
    "rest api": "Backend Service",
    "api service": "Backend Service",
    "microservice": "Microservice",
    "micro-service": "Microservice",
    "micro service": "Microservice",
    "integration": "Integration Service",
    "etl": "Integration Service",
    "middleware": "Integration Service",
    "integration service": "Integration Service",
    "data integration": "Integration Service",
    "batch": "Batch Job",
    "scheduled job": "Batch Job",
    "cron": "Batch Job",
    "batch job": "Batch Job",
    "scheduled task": "Batch Job",
    "api": "API",
    "rest": "API",
    "graphql": "API",
    "db": "Database",
    "database": "Database",
    "data store": "Database",
    "dw": "Data Warehouse",
    "data warehouse": "Data Warehouse",
    "warehouse": "Data Warehouse",
    "etl pipeline": "ETL Pipeline",
    "pipeline": "ETL Pipeline",
    "data pipeline": "ETL Pipeline",
    "reporting": "Reporting Tool",
    "report": "Reporting Tool",
    "bi": "Reporting Tool",
    "business intelligence": "Reporting Tool",
}

BUSINESS_CRITICALITY_ALIASES: Dict[str, str] = {
    "mission critical": "Critical",
    "mission-critical": "Critical",
    "tier 1": "Critical",
    "tier1": "Critical",
    "p1": "Critical",
    "essential": "Critical",
    "vital": "Critical",
    "tier 2": "High",
    "tier2": "High",
    "p2": "High",
    "important": "High",
    "significant": "High",
    "tier 3": "Medium",
    "tier3": "Medium",
    "p3": "Medium",
    "normal": "Medium",
    "standard": "Medium",
    "moderate": "Medium",
    "tier 4": "Low",
    "tier4": "Low",
    "p4": "Low",
    "minor": "Low",
    "non-critical": "Low",
    "optional": "Low",
}

DATA_CLASSIFICATION_ALIASES: Dict[str, str] = {
    "public": "Public",
    "open": "Public",
    "external": "Public",
    "internal": "Internal",
    "private": "Internal",
    "internal use": "Internal",
    "confidential": "Confidential",
    "sensitive": "Confidential",
    "proprietary": "Confidential",
    "restricted": "Restricted",
    "highly confidential": "Restricted",
    "secret": "Restricted",
    "top secret": "Restricted",
    "pii": "Restricted",
    "phi": "Restricted",
}

# Values that indicate "no data" and should be converted to None
NULL_VALUE_PATTERNS = [
    "n/a",
    "na",
    "none",
    "null",
    "-",
    "--",
    "not applicable",
    "not available",
    "unknown",
    "tbd",
    "tbc",
    "",
    ".",
    "...",
    "undefined",
    "unspecified",
    "not specified",
    "not set",
    "empty",
    "blank",
    "missing",
    "no data",
    "no value",
]


# =============================================================================
# ALIAS MAPPING GETTER
# =============================================================================


def get_alias_mapping(field_name: str) -> Dict[str, str]:
    """Get normalization aliases for a given field"""
    ALIAS_MAPPINGS = {
        "lifecycle_status": LIFECYCLE_STATUS_ALIASES,
        "deployment_status": DEPLOYMENT_STATUS_ALIASES,
        "component_type": COMPONENT_TYPE_ALIASES,
        "business_criticality": BUSINESS_CRITICALITY_ALIASES,
        "data_classification": DATA_CLASSIFICATION_ALIASES,
    }
    return ALIAS_MAPPINGS.get(field_name, {})


def get_allowed_values(field_name: str) -> Optional[List[str]]:
    """Get allowed values for an enum field"""
    ALLOWED_VALUES = {
        "lifecycle_status": LIFECYCLE_STATUS_VALUES,
        "deployment_status": DEPLOYMENT_STATUS_VALUES,
        "component_type": COMPONENT_TYPE_VALUES,
        "business_criticality": BUSINESS_CRITICALITY_VALUES,
        "application_category": APPLICATION_CATEGORY_VALUES,
        "data_classification": DATA_CLASSIFICATION_VALUES,
        "architecture_style": ARCHITECTURE_STYLE_VALUES,
        "deployment_model": DEPLOYMENT_MODEL_VALUES,
        "user_type": USER_TYPE_VALUES,
    }
    return ALLOWED_VALUES.get(field_name)


# =============================================================================
# FIELD SCHEMA DEFINITIONS
# =============================================================================

APPLICATION_COMPONENT_SCHEMA: Dict[str, FieldSchema] = {
    # Required fields
    "name": FieldSchema(
        name="name",
        field_type=FieldType.STRING,
        required=True,
        max_length=256,
        min_length=1,
        description="Application name (required)",
    ),
    # Enum fields with normalization
    "lifecycle_status": FieldSchema(
        name="lifecycle_status",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=LIFECYCLE_STATUS_VALUES,
        default_value="production",
        max_length=100,
        description="Application lifecycle status",
    ),
    "deployment_status": FieldSchema(
        name="deployment_status",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=DEPLOYMENT_STATUS_VALUES,
        default_value="development",
        max_length=100,
        description="Current deployment status",
    ),
    "component_type": FieldSchema(
        name="component_type",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=COMPONENT_TYPE_VALUES,
        max_length=100,
        description="Type of application component",
    ),
    "business_criticality": FieldSchema(
        name="business_criticality",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=BUSINESS_CRITICALITY_VALUES,
        max_length=100,
        description="Business criticality level",
    ),
    "application_category": FieldSchema(
        name="application_category",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=APPLICATION_CATEGORY_VALUES,
        max_length=100,
        description="Application category",
    ),
    "data_classification": FieldSchema(
        name="data_classification",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=DATA_CLASSIFICATION_VALUES,
        description="Data classification level",
    ),
    "architecture_style": FieldSchema(
        name="architecture_style",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=ARCHITECTURE_STYLE_VALUES,
        description="Architecture style",
    ),
    "deployment_model": FieldSchema(
        name="deployment_model",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=DEPLOYMENT_MODEL_VALUES,
        description="Deployment model",
    ),
    "user_type": FieldSchema(
        name="user_type",
        field_type=FieldType.ENUM,
        required=False,
        allowed_values=USER_TYPE_VALUES,
        max_length=100,
        description="Primary user type",
    ),
    # String fields with length constraints
    "description": FieldSchema(
        name="description",
        field_type=FieldType.STRING,
        required=False,
        max_length=5000,
        description="Application description",
    ),
    "application_code": FieldSchema(
        name="application_code",
        field_type=FieldType.STRING,
        required=False,
        max_length=50,
        description="Unique application identifier code",
    ),
    "app_id": FieldSchema(
        name="app_id",
        field_type=FieldType.STRING,
        required=False,
        max_length=50,
        description="Application ID",
    ),
    "business_domain": FieldSchema(
        name="business_domain",
        field_type=FieldType.STRING,
        required=False,
        max_length=100,
        description="Business domain/area",
    ),
    "business_owner": FieldSchema(
        name="business_owner",
        field_type=FieldType.STRING,
        required=False,
        max_length=200,
        description="Business owner name",
    ),
    "technical_owner": FieldSchema(
        name="technical_owner",
        field_type=FieldType.STRING,
        required=False,
        max_length=100,
        description="Technical owner name",
    ),
    "development_team": FieldSchema(
        name="development_team",
        field_type=FieldType.STRING,
        required=False,
        max_length=200,
        description="Development team name",
    ),
    "technology_stack": FieldSchema(
        name="technology_stack",
        field_type=FieldType.STRING,
        required=False,
        max_length=500,
        description="Technology stack (comma-separated)",
    ),
    "version": FieldSchema(
        name="version",
        field_type=FieldType.STRING,
        required=False,
        max_length=50,
        description="Application version",
    ),
    "vendor_name": FieldSchema(
        name="vendor_name",
        field_type=FieldType.STRING,
        required=False,
        max_length=200,
        description="Vendor name",
    ),
    "support_team": FieldSchema(
        name="support_team",
        field_type=FieldType.STRING,
        required=False,
        max_length=200,
        description="Support team name",
    ),
    "technical_lead": FieldSchema(
        name="technical_lead",
        field_type=FieldType.STRING,
        required=False,
        max_length=100,
        description="Technical lead name",
    ),
    "product_manager": FieldSchema(
        name="product_manager",
        field_type=FieldType.STRING,
        required=False,
        max_length=100,
        description="Product manager name",
    ),
    # Numeric fields
    "user_base_size": FieldSchema(
        name="user_base_size",
        field_type=FieldType.INTEGER,
        required=False,
        min_value=0,
        description="Number of users",
    ),
    "user_count": FieldSchema(
        name="user_count",
        field_type=FieldType.INTEGER,
        required=False,
        min_value=0,
        description="Number of users",
    ),
    "interfaces_count": FieldSchema(
        name="interfaces_count",
        field_type=FieldType.INTEGER,
        required=False,
        min_value=0,
        description="Number of interfaces",
    ),
    "dependencies_count": FieldSchema(
        name="dependencies_count",
        field_type=FieldType.INTEGER,
        required=False,
        min_value=0,
        description="Number of dependencies",
    ),
    "rpo_hours": FieldSchema(
        name="rpo_hours",
        field_type=FieldType.INTEGER,
        required=False,
        min_value=0,
        description="Recovery Point Objective in hours",
    ),
    "rto_hours": FieldSchema(
        name="rto_hours",
        field_type=FieldType.INTEGER,
        required=False,
        min_value=0,
        description="Recovery Time Objective in hours",
    ),
    "total_cost_of_ownership": FieldSchema(
        name="total_cost_of_ownership",
        field_type=FieldType.FLOAT,
        required=False,
        min_value=0,
        description="Total cost of ownership",
    ),
    "license_cost_annual": FieldSchema(
        name="license_cost_annual",
        field_type=FieldType.FLOAT,
        required=False,
        min_value=0,
        description="Annual license cost",
    ),
    "infrastructure_cost_monthly": FieldSchema(
        name="infrastructure_cost_monthly",
        field_type=FieldType.FLOAT,
        required=False,
        min_value=0,
        description="Monthly infrastructure cost",
    ),
    # Date fields
    "go_live_date": FieldSchema(
        name="go_live_date", field_type=FieldType.DATE, required=False, description="Go-live date"
    ),
    "retirement_date": FieldSchema(
        name="retirement_date",
        field_type=FieldType.DATE,
        required=False,
        description="Planned retirement date",
    ),
    "end_of_life_date": FieldSchema(
        name="end_of_life_date",
        field_type=FieldType.DATE,
        required=False,
        description="End of life date",
    ),
    # Boolean fields
    "disaster_recovery_enabled": FieldSchema(
        name="disaster_recovery_enabled",
        field_type=FieldType.BOOLEAN,
        required=False,
        default_value=False,
        description="Whether DR is enabled",
    ),
    "exposes_api": FieldSchema(
        name="exposes_api",
        field_type=FieldType.BOOLEAN,
        required=False,
        default_value=False,
        description="Whether application exposes an API",
    ),
    "pii_data_processed": FieldSchema(
        name="pii_data_processed",
        field_type=FieldType.BOOLEAN,
        required=False,
        default_value=False,
        description="Whether PII data is processed",
    ),
    "gdpr_compliant": FieldSchema(
        name="gdpr_compliant",
        field_type=FieldType.BOOLEAN,
        required=False,
        default_value=False,
        description="Whether GDPR compliant",
    ),
    # URL fields
    "documentation_url": FieldSchema(
        name="documentation_url",
        field_type=FieldType.URL,
        required=False,
        max_length=500,
        description="Documentation URL",
    ),
    "version_control_url": FieldSchema(
        name="version_control_url",
        field_type=FieldType.URL,
        required=False,
        max_length=500,
        description="Version control repository URL",
    ),
}

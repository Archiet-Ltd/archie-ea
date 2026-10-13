"""
Model Constants and Standard Definitions

This file defines constants for consistent model field definitions across
the application. Using these constants ensures:
1. Consistent string lengths
2. Standardized enum values
3. Documented field constraints

Usage:
    from app.models.constants import FieldLength, CascadePolicy

    class MyModel(db.Model):
        name = db.Column(db.String(FieldLength.NAME), nullable=False)
        description = db.Column(db.String(FieldLength.DESCRIPTION))
"""

import re


# =============================================================================
# String Field Lengths
# =============================================================================


class FieldLength:
    """
    Standard string field lengths.

    Use these constants instead of magic numbers for consistency.
    """

    # Short identifiers and codes
    CODE = 50  # Short codes like 'CAP - 001', 'APP - 123'
    SHORT_CODE = 20  # Very short codes like 'US', 'EU'
    STATUS = 30  # Status values like 'active', 'deprecated'
    TYPE = 50  # Type classifications

    # Names and titles
    NAME = 255  # Standard name fields
    SHORT_NAME = 100  # Abbreviated names
    TITLE = 255  # Titles (same as names)

    # Contact and identity
    EMAIL = 255  # Email addresses
    PHONE = 50  # Phone numbers
    URL = 2048  # URLs (generous for complex query strings)
    PATH = 1000  # File paths

    # Descriptive text (use Text for longer)
    DESCRIPTION = 2000  # Short descriptions
    NOTES = 4000  # Longer notes
    SUMMARY = 500  # Brief summaries

    # Technical fields
    VERSION = 50  # Version strings like '1.0.0', 'v2.3.1 - beta'
    HASH = 128  # Hash values (SHA - 512 = 128 hex chars)
    UUID = 36  # UUID strings
    IP_ADDRESS = 45  # IPv6 addresses (max 45 chars)
    HOSTNAME = 255  # DNS hostnames

    # Security
    PASSWORD_HASH = 255  # Hashed passwords (bcrypt = ~60, argon2 = ~97)
    TOKEN = 500  # Auth tokens, API keys

    # Metadata
    CREATED_BY = 100  # Username/email of creator
    MIME_TYPE = 100  # MIME types like 'application/json'
    LOCALE = 10  # Locale codes like 'en-US'

    # ArchiMate specific
    LAYER = 30  # ArchiMate layers: 'business', 'application', 'technology'
    ELEMENT_TYPE = 100  # ArchiMate element types
    RELATIONSHIP_TYPE = 100  # ArchiMate relationship types

    # Business specific
    CURRENCY_CODE = 3  # ISO currency codes: 'USD', 'EUR'
    COUNTRY_CODE = 3  # ISO country codes: 'USA', 'GBR'


# =============================================================================
# Cascade Policies
# =============================================================================


class CascadePolicy:
    """
    Standard cascade deletion policies.

    Use these for consistent foreign key behavior:
    - COMPOSITION: Child cannot exist without parent (e.g., order items)
    - AGGREGATION: Child can exist independently (e.g., tags on posts)
    - ASSOCIATION: Loose coupling (e.g., user preferences)
    """

    # Parent-child composition - delete children when parent deleted
    COMPOSITION = {"ondelete": "CASCADE", "nullable": False}

    # Aggregation - set to NULL when parent deleted
    AGGREGATION = {"ondelete": "SET NULL", "nullable": True}

    # Strict reference - prevent deletion if referenced
    REFERENCE = {"ondelete": "RESTRICT", "nullable": True}

    # Historical/audit - keep reference even if parent deleted
    HISTORICAL = {"ondelete": "SET NULL", "nullable": True}

    # Self-referential (hierarchy) - set NULL to avoid orphans
    HIERARCHY = {"ondelete": "SET NULL", "nullable": True}


# =============================================================================
# Status Enums
# =============================================================================


class LifecycleStatus:
    """Standard lifecycle status values for applications and capabilities"""

    PLANNING = "planning"
    DEVELOPMENT = "development"
    TESTING = "testing"
    PILOT = "pilot"
    PRODUCTION = "production"
    MAINTENANCE = "maintenance"
    SUNSET = "sunset"
    RETIRED = "retired"

    ALL = [PLANNING, DEVELOPMENT, TESTING, PILOT, PRODUCTION, MAINTENANCE, SUNSET, RETIRED]

    ACTIVE = [PLANNING, DEVELOPMENT, TESTING, PILOT, PRODUCTION, MAINTENANCE]
    INACTIVE = [SUNSET, RETIRED]


class GapStatus:
    """Standard gap status values"""

    IDENTIFIED = "identified"
    ANALYZING = "analyzing"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    CLOSED = "closed"
    DEFERRED = "deferred"
    REJECTED = "rejected"

    ALL = [
        IDENTIFIED,
        ANALYZING,
        PLANNED,
        IN_PROGRESS,
        IMPLEMENTED,
        VERIFIED,
        CLOSED,
        DEFERRED,
        REJECTED,
    ]

    OPEN = [IDENTIFIED, ANALYZING, PLANNED, IN_PROGRESS]
    RESOLVED = [IMPLEMENTED, VERIFIED, CLOSED]


class Priority:
    """Standard priority values"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    ALL = [CRITICAL, HIGH, MEDIUM, LOW]


class Criticality:
    """Business criticality levels"""

    MISSION_CRITICAL = "mission_critical"
    BUSINESS_CRITICAL = "business_critical"
    BUSINESS_OPERATIONAL = "business_operational"
    ADMINISTRATIVE = "administrative"
    EXPERIMENTAL = "experimental"

    ALL = [MISSION_CRITICAL, BUSINESS_CRITICAL, BUSINESS_OPERATIONAL, ADMINISTRATIVE, EXPERIMENTAL]


class MaturityLevel:
    """Capability maturity levels"""

    INITIAL = 1
    MANAGED = 2
    DEFINED = 3
    QUANTITATIVELY_MANAGED = 4
    OPTIMIZING = 5

    ALL = [INITIAL, MANAGED, DEFINED, QUANTITATIVELY_MANAGED, OPTIMIZING]


# =============================================================================
# ArchiMate Constants
# =============================================================================


class ArchiMateLayer:
    """ArchiMate 3.2 layers"""

    STRATEGY = "strategy"
    BUSINESS = "business"
    APPLICATION = "application"
    TECHNOLOGY = "technology"
    PHYSICAL = "physical"
    IMPLEMENTATION = "implementation_migration"
    MOTIVATION = "motivation"

    ALL = [STRATEGY, BUSINESS, APPLICATION, TECHNOLOGY, PHYSICAL, IMPLEMENTATION, MOTIVATION]

    ALIASES = {
        "implementation & migration": IMPLEMENTATION,
        "implementation&migration": IMPLEMENTATION,
        "implementation/migration": IMPLEMENTATION,
        "implementation migration": IMPLEMENTATION,
        "implementation and migration": IMPLEMENTATION,
        "implementation_migration": IMPLEMENTATION,
        "implementation-migration": IMPLEMENTATION,
        "implementationmigration": IMPLEMENTATION,
    }

    DISPLAY_NAMES = {
        STRATEGY: "Strategy",
        BUSINESS: "Business",
        APPLICATION: "Application",
        TECHNOLOGY: "Technology",
        PHYSICAL: "Physical",
        IMPLEMENTATION: "Implementation",
        MOTIVATION: "Motivation",
    }

    @classmethod
    def normalize(cls, layer: str | None) -> str | None:
        """Return the canonical lower-case layer key for *layer*."""

        if not isinstance(layer, str):
            return layer
        key = layer.strip().lower()
        key = re.sub(r"[\s\-]+", "_", key)
        return cls.ALIASES.get(key.replace("_", " "), cls.ALIASES.get(key, key))

    @classmethod
    def canonical(cls, value):
        """Resolve a stored ``archimate_elements.layer`` value onto this
        class's vocabulary.

        Casing on newly written rows is already handled by the column type
        in ``app/models/models.py``; this only folds the one legacy word
        ``"implementation"`` onto ``IMPLEMENTATION``
        (``"implementation_migration"``). Every other string is returned
        stripped and lower-cased, unchanged otherwise, and a non-string is
        returned untouched. This is deliberately not a general-purpose
        layer normaliser.

        Two other normalisers elsewhere in the product touch the same word,
        and only one of them actually opposes this one. The ArchiMate CRUD
        route's layer aliases fold this exact pair the other way, listing
        ``"implementation_migration"`` as one more spelling of
        ``"implementation"``. The architecture validation service's own
        layer aliases map the long-form, punctuated spellings (``"implementation
        & migration"`` and its ``"and"``/``"&"``/``"/"`` variants) onto
        ``"implementation"`` too, but never list ``"implementation_migration"``
        itself, so it passes that spelling through unchanged rather than
        folding this pair either direction. Reconciling all of these onto one
        shared spelling is a separate, already-recorded piece of work this
        function does not attempt.
        """
        if not isinstance(value, str):
            return value
        stripped = value.strip().lower()
        if stripped == "implementation":
            return cls.IMPLEMENTATION
        return stripped


class ArchiMateRelationshipType:
    """ArchiMate 3.2 relationship types"""

    # Structural relationships
    COMPOSITION = "composition"
    AGGREGATION = "aggregation"
    ASSIGNMENT = "assignment"
    REALIZATION = "realization"

    # Dependency relationships
    SERVING = "serving"
    ACCESS = "access"
    INFLUENCE = "influence"

    # Dynamic relationships
    TRIGGERING = "triggering"
    FLOW = "flow"

    # Other relationships
    SPECIALIZATION = "specialization"
    ASSOCIATION = "association"

    ALL = [
        COMPOSITION,
        AGGREGATION,
        ASSIGNMENT,
        REALIZATION,
        SERVING,
        ACCESS,
        INFLUENCE,
        TRIGGERING,
        FLOW,
        SPECIALIZATION,
        ASSOCIATION,
    ]

    ALIASES = {
        "compositionrelationship": COMPOSITION,
        "composes": COMPOSITION,
        "aggregationrelationship": AGGREGATION,
        "aggregates": AGGREGATION,
        "assignmentrelationship": ASSIGNMENT,
        "assigns": ASSIGNMENT,
        "realizationrelationship": REALIZATION,
        "realisationrelationship": REALIZATION,
        "realisation": REALIZATION,
        "realizes": REALIZATION,
        "realises": REALIZATION,
        "servingrelationship": SERVING,
        "serves": SERVING,
        "uses": SERVING,
        "accessrelationship": ACCESS,
        "accesses": ACCESS,
        "influencerelationship": INFLUENCE,
        "influences": INFLUENCE,
        "triggeringrelationship": TRIGGERING,
        "triggers": TRIGGERING,
        "flowrelationship": FLOW,
        "flows": FLOW,
        "specializationrelationship": SPECIALIZATION,
        "specialisationrelationship": SPECIALIZATION,
        "specialisation": SPECIALIZATION,
        "specialises": SPECIALIZATION,
        "specializes": SPECIALIZATION,
        "associationrelationship": ASSOCIATION,
        "associates": ASSOCIATION,
    }

    PASCAL_CASE = {
        COMPOSITION: "Composition",
        AGGREGATION: "Aggregation",
        ASSIGNMENT: "Assignment",
        REALIZATION: "Realization",
        SERVING: "Serving",
        ACCESS: "Access",
        INFLUENCE: "Influence",
        TRIGGERING: "Triggering",
        FLOW: "Flow",
        SPECIALIZATION: "Specialization",
        ASSOCIATION: "Association",
    }

    @classmethod
    def normalize(cls, relationship_type: str | None, *, pascal_case: bool = False) -> str | None:
        """Return the canonical ArchiMate relationship spelling."""

        if not isinstance(relationship_type, str):
            return relationship_type
        key = relationship_type.strip()
        if not key:
            return ""
        key = re.sub(r"(?i)relationship$", "", key).strip().lower()
        key = cls.ALIASES.get(key, key)
        if key not in cls.ALL:
            return relationship_type.strip()
        if pascal_case:
            return cls.PASCAL_CASE[key]
        return key


# =============================================================================
# Capability Type Classification
# =============================================================================


class CapabilityType:
    """
    Extended capability types aligned with ArchiMate 3.2.

    Provides a comprehensive taxonomy for classifying capabilities across
    different architectural layers and business domains.

    Categories:
    - Strategic: High-level capabilities from Strategy Layer
    - Business: Core business capabilities from Business Layer
    - Application: Technical/software capabilities from Application Layer
    - Manufacturing: Industry-specific manufacturing capabilities
    """

    # ==========================================================================
    # Strategic Layer Capabilities (ArchiMate Strategy Layer)
    # ==========================================================================
    STRATEGIC = "strategic"  # High-level strategic capability
    RESOURCE = "resource"  # Resource-based capability (people, assets)
    COURSE_OF_ACTION = "course_of_action"  # Strategic initiative capability
    VALUE_STREAM = "value_stream"  # End-to-end value delivery capability

    # ==========================================================================
    # Business Layer Capabilities (ArchiMate Business Layer)
    # ==========================================================================
    BUSINESS = "business"  # Core business capability
    OPERATIONAL = "operational"  # Day-to-day operational capability
    SUPPORTING = "supporting"  # Supporting/enabling capability
    MANAGEMENT = "management"  # Management and governance capability
    CUSTOMER_FACING = "customer_facing"  # Customer-facing capability
    PARTNER_FACING = "partner_facing"  # Partner/supplier-facing capability

    # ==========================================================================
    # Application Layer Capabilities (ArchiMate Application Layer)
    # ==========================================================================
    APPLICATION = "application"  # Application/software capability
    INTEGRATION = "integration"  # System integration capability
    DATA = "data"  # Data management capability
    ANALYTICS = "analytics"  # Analytics and BI capability
    AUTOMATION = "automation"  # Process automation capability

    # ==========================================================================
    # Technology Layer Capabilities (ArchiMate Technology Layer)
    # ==========================================================================
    INFRASTRUCTURE = "infrastructure"  # Infrastructure capability
    PLATFORM = "platform"  # Platform capability
    SECURITY = "security"  # Security capability

    # ==========================================================================
    # Manufacturing/Industry-Specific Capabilities
    # ==========================================================================
    MANUFACTURING = "manufacturing"  # General manufacturing capability
    PRODUCTION = "production"  # Production/assembly capability
    SUPPLY_CHAIN = "supply_chain"  # Supply chain capability
    QUALITY = "quality"  # Quality management capability
    MAINTENANCE = "maintenance"  # Maintenance/reliability capability
    ENGINEERING = "engineering"  # Engineering capability
    LOGISTICS = "logistics"  # Logistics capability

    # ==========================================================================
    # Groupings
    # ==========================================================================
    ALL = [
        # Strategic
        STRATEGIC,
        RESOURCE,
        COURSE_OF_ACTION,
        VALUE_STREAM,
        # Business
        BUSINESS,
        OPERATIONAL,
        SUPPORTING,
        MANAGEMENT,
        CUSTOMER_FACING,
        PARTNER_FACING,
        # Application
        APPLICATION,
        INTEGRATION,
        DATA,
        ANALYTICS,
        AUTOMATION,
        # Technology
        INFRASTRUCTURE,
        PLATFORM,
        SECURITY,
        # Manufacturing
        MANUFACTURING,
        PRODUCTION,
        SUPPLY_CHAIN,
        QUALITY,
        MAINTENANCE,
        ENGINEERING,
        LOGISTICS,
    ]

    STRATEGIC_TYPES = [STRATEGIC, RESOURCE, COURSE_OF_ACTION, VALUE_STREAM]
    BUSINESS_TYPES = [
        BUSINESS,
        OPERATIONAL,
        SUPPORTING,
        MANAGEMENT,
        CUSTOMER_FACING,
        PARTNER_FACING,
    ]
    APPLICATION_TYPES = [APPLICATION, INTEGRATION, DATA, ANALYTICS, AUTOMATION]
    TECHNOLOGY_TYPES = [INFRASTRUCTURE, PLATFORM, SECURITY]
    MANUFACTURING_TYPES = [
        MANUFACTURING,
        PRODUCTION,
        SUPPLY_CHAIN,
        QUALITY,
        MAINTENANCE,
        ENGINEERING,
        LOGISTICS,
    ]

    @classmethod
    def get_archimate_layer(cls, capability_type: str) -> str:
        """Map capability type to ArchiMate layer."""
        if capability_type in cls.STRATEGIC_TYPES:
            return ArchiMateLayer.STRATEGY
        elif capability_type in cls.BUSINESS_TYPES:
            return ArchiMateLayer.BUSINESS
        elif capability_type in cls.APPLICATION_TYPES:
            return ArchiMateLayer.APPLICATION
        elif capability_type in cls.TECHNOLOGY_TYPES:
            return ArchiMateLayer.TECHNOLOGY
        elif capability_type in cls.MANUFACTURING_TYPES:
            return ArchiMateLayer.BUSINESS  # Manufacturing maps to Business Layer
        else:
            return ArchiMateLayer.BUSINESS  # Default


class CapabilityDomain:
    """
    Business domain classification for capabilities.

    Aligned with APQC PCF categories for cross-reference.
    """

    # Core business domains (APQC 1.x - 5.x)
    STRATEGY_PLANNING = "strategy_planning"  # 1.x
    PRODUCT_DEVELOPMENT = "product_development"  # 2.x
    MARKETING_SALES = "marketing_sales"  # 3.x
    SUPPLY_CHAIN = "supply_chain"  # 4.x
    OPERATIONS = "operations"  # 5.x

    # Support domains (APQC 6.x - 13.x)
    HUMAN_RESOURCES = "human_resources"  # 6.x
    INFORMATION_TECHNOLOGY = "information_technology"  # 7.x
    FINANCE = "finance"  # 8.x
    FACILITIES_ASSETS = "facilities_assets"  # 9.x
    RISK_COMPLIANCE = "risk_compliance"  # 10.x
    EXTERNAL_RELATIONS = "external_relations"  # 11.x
    KNOWLEDGE_MANAGEMENT = "knowledge_management"  # 12.x
    EMERGENCY_MANAGEMENT = "emergency_management"  # 13.x

    ALL = [
        STRATEGY_PLANNING,
        PRODUCT_DEVELOPMENT,
        MARKETING_SALES,
        SUPPLY_CHAIN,
        OPERATIONS,
        HUMAN_RESOURCES,
        INFORMATION_TECHNOLOGY,
        FINANCE,
        FACILITIES_ASSETS,
        RISK_COMPLIANCE,
        EXTERNAL_RELATIONS,
        KNOWLEDGE_MANAGEMENT,
        EMERGENCY_MANAGEMENT,
    ]

    # Map to APQC top-level categories
    APQC_MAPPING = {
        STRATEGY_PLANNING: "1.0",
        PRODUCT_DEVELOPMENT: "2.0",
        MARKETING_SALES: "3.0",
        SUPPLY_CHAIN: "4.0",
        OPERATIONS: "5.0",
        HUMAN_RESOURCES: "6.0",
        INFORMATION_TECHNOLOGY: "7.0",
        FINANCE: "8.0",
        FACILITIES_ASSETS: "9.0",
        RISK_COMPLIANCE: "10.0",
        EXTERNAL_RELATIONS: "11.0",
        KNOWLEDGE_MANAGEMENT: "12.0",
        EMERGENCY_MANAGEMENT: "13.0",
    }


# =============================================================================
# Validation Helpers
# =============================================================================


def validate_status(value, allowed_values, field_name="status"):
    """
    Validate that a status value is in the allowed list.

    Usage:
        @validates('lifecycle_status')
        def validate_lifecycle_status(self, key, value):
            return validate_status(value, LifecycleStatus.ALL, 'lifecycle_status')
    """
    if value is not None and value not in allowed_values:
        raise ValueError(
            f"Invalid {field_name}: '{value}'. " f"Must be one of: {', '.join(allowed_values)}"
        )
    return value


def validate_percentage(value, field_name="percentage"):
    """Validate that a value is between 0 and 100"""
    if value is not None and (value < 0 or value > 100):
        raise ValueError(f"{field_name} must be between 0 and 100")
    return value


def validate_positive(value, field_name="value"):
    """Validate that a value is positive"""
    if value is not None and value < 0:
        raise ValueError(f"{field_name} must be positive")
    return value

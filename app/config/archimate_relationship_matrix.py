"""
ArchiMate 3.2 Relationship Validation Matrix
PRD - 009.1: Complete relationship rules per ArchiMate specification

Defines valid relationships between element types and their cardinality constraints.
This module serves as the authoritative source for validating ArchiMate relationships
according to the official ArchiMate 3.2 Specification (The Open Group).

Reference: ArchiMate 3.2 Specification, Appendix B: Derivation Rules
"""
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

# =============================================================================
# Data Classes and Enumerations
# =============================================================================


class RelationshipCategory(Enum):
    """Categories of ArchiMate relationship types."""

    STRUCTURAL = "structural"  # Composition, Aggregation, Assignment, Realization
    DEPENDENCY = "dependency"  # Serving, Access, Influence
    DYNAMIC = "dynamic"  # Triggering, Flow
    OTHER = "other"  # Specialization, Association


class AccessMode(Enum):
    """Access relationship modes (read, write, read/write)."""

    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"
    UNSPECIFIED = "unspecified"


@dataclass
class RelationshipRule:
    """Defines a valid relationship between element types."""

    source_type: str
    target_type: str
    relationship_types: List[str]
    cardinality: Tuple[int, Optional[int]]  # (min, max) - None means unlimited
    description: str


@dataclass
class RelationshipTypeDefinition:
    """Complete definition of an ArchiMate relationship type."""

    name: str
    category: RelationshipCategory
    description: str
    notation: str
    is_directed: bool
    can_be_derived: bool
    derivation_strength: int  # Higher = stronger in derivation chains


# =============================================================================
# ArchiMate 3.2 Relationship Types
# =============================================================================

RELATIONSHIP_TYPES = [
    "composition",  # Source contains target (whole-part)
    "aggregation",  # Source groups target (collection)
    "assignment",  # Active element assigned to behavior
    "realization",  # Element realizes another
    "serving",  # Element provides functionality to another
    "access",  # Behavior accesses data (passive element)
    "influence",  # Motivation element influences another
    "triggering",  # Behavior triggers another behavior
    "flow",  # Flow of information/material/value
    "specialization",  # Element is specialization of another
    "association",  # Generic/unspecified relationship
]

RELATIONSHIP_TYPE_DEFINITIONS: Dict[str, RelationshipTypeDefinition] = {
    "composition": RelationshipTypeDefinition(
        name="composition",
        category=RelationshipCategory.STRUCTURAL,
        description="Indicates that an element consists of one or more other elements",
        notation="Filled diamond at source",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=10,
    ),
    "aggregation": RelationshipTypeDefinition(
        name="aggregation",
        category=RelationshipCategory.STRUCTURAL,
        description="Indicates that an element groups a number of other elements",
        notation="Open diamond at source",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=9,
    ),
    "assignment": RelationshipTypeDefinition(
        name="assignment",
        category=RelationshipCategory.STRUCTURAL,
        description="Links active structure elements with behavior elements",
        notation="Filled circle at source with line",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=8,
    ),
    "realization": RelationshipTypeDefinition(
        name="realization",
        category=RelationshipCategory.STRUCTURAL,
        description="Links a logical entity with a more concrete entity that realizes it",
        notation="Dashed line with open arrowhead",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=7,
    ),
    "serving": RelationshipTypeDefinition(
        name="serving",
        category=RelationshipCategory.DEPENDENCY,
        description="Models that an element provides functionality to another element",
        notation="Line with open arrowhead",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=6,
    ),
    "access": RelationshipTypeDefinition(
        name="access",
        category=RelationshipCategory.DEPENDENCY,
        description="Models the access of behavioral elements to passive structure elements",
        notation="Dashed line with optional arrowhead",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=5,
    ),
    "influence": RelationshipTypeDefinition(
        name="influence",
        category=RelationshipCategory.DEPENDENCY,
        description="Models how motivation elements influence each other",
        notation="Dashed line with open arrowhead and optional +/-",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=4,
    ),
    "triggering": RelationshipTypeDefinition(
        name="triggering",
        category=RelationshipCategory.DYNAMIC,
        description="Describes temporal/causal relationship between behaviors",
        notation="Line with filled arrowhead",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=3,
    ),
    "flow": RelationshipTypeDefinition(
        name="flow",
        category=RelationshipCategory.DYNAMIC,
        description="Describes transfer of something from one element to another",
        notation="Dashed line with filled arrowhead",
        is_directed=True,
        can_be_derived=True,
        derivation_strength=2,
    ),
    "specialization": RelationshipTypeDefinition(
        name="specialization",
        category=RelationshipCategory.OTHER,
        description="Indicates that an element is a more specific form of another",
        notation="Line with open triangle arrowhead",
        is_directed=True,
        can_be_derived=False,
        derivation_strength=0,
    ),
    "association": RelationshipTypeDefinition(
        name="association",
        category=RelationshipCategory.OTHER,
        description="Used when no other relationship applies",
        notation="Simple line",
        is_directed=False,
        can_be_derived=True,
        derivation_strength=1,
    ),
}


# =============================================================================
# Element Type Categories for Relationship Rules
# =============================================================================

# Strategy Layer Elements
STRATEGY_ELEMENTS = ["Resource", "Capability", "CourseOfAction", "ValueStream"]

# Business Layer Elements
BUSINESS_ACTIVE_ELEMENTS = [
    "BusinessActor",
    "BusinessRole",
    "BusinessCollaboration",
    "BusinessInterface",
]
BUSINESS_BEHAVIOR_ELEMENTS = [
    "BusinessProcess",
    "BusinessFunction",
    "BusinessInteraction",
    "BusinessEvent",
    "BusinessService",
]
BUSINESS_PASSIVE_ELEMENTS = ["BusinessObject", "Contract", "Representation", "Product"]
BUSINESS_ELEMENTS = (
    BUSINESS_ACTIVE_ELEMENTS + BUSINESS_BEHAVIOR_ELEMENTS + BUSINESS_PASSIVE_ELEMENTS
)

# Application Layer Elements
APPLICATION_ACTIVE_ELEMENTS = [
    "ApplicationComponent",
    "ApplicationCollaboration",
    "ApplicationInterface",
]
APPLICATION_BEHAVIOR_ELEMENTS = [
    "ApplicationFunction",
    "ApplicationProcess",
    "ApplicationInteraction",
    "ApplicationEvent",
    "ApplicationService",
]
APPLICATION_PASSIVE_ELEMENTS = ["DataObject"]
APPLICATION_ELEMENTS = (
    APPLICATION_ACTIVE_ELEMENTS + APPLICATION_BEHAVIOR_ELEMENTS + APPLICATION_PASSIVE_ELEMENTS
)

# Technology Layer Elements
TECHNOLOGY_ACTIVE_ELEMENTS = [
    "Node",
    "Device",
    "SystemSoftware",
    "TechnologyCollaboration",
    "TechnologyInterface",
    "Path",
    "CommunicationNetwork",
]
TECHNOLOGY_BEHAVIOR_ELEMENTS = [
    "TechnologyFunction",
    "TechnologyProcess",
    "TechnologyInteraction",
    "TechnologyEvent",
    "TechnologyService",
]
TECHNOLOGY_PASSIVE_ELEMENTS = ["Artifact"]
TECHNOLOGY_ELEMENTS = (
    TECHNOLOGY_ACTIVE_ELEMENTS + TECHNOLOGY_BEHAVIOR_ELEMENTS + TECHNOLOGY_PASSIVE_ELEMENTS
)

# Physical Layer Elements
PHYSICAL_ACTIVE_ELEMENTS = ["Equipment", "Facility", "DistributionNetwork"]
PHYSICAL_PASSIVE_ELEMENTS = ["Material"]
PHYSICAL_ELEMENTS = PHYSICAL_ACTIVE_ELEMENTS + PHYSICAL_PASSIVE_ELEMENTS

# Motivation Layer Elements
MOTIVATION_ACTIVE_ELEMENTS = ["Stakeholder"]
MOTIVATION_PASSIVE_ELEMENTS = [
    "Driver",
    "Assessment",
    "Goal",
    "Outcome",
    "Principle",
    "Requirement",
    "Constraint",
    "Meaning",
    "Value",
]
MOTIVATION_ELEMENTS = MOTIVATION_ACTIVE_ELEMENTS + MOTIVATION_PASSIVE_ELEMENTS

# Implementation & Migration Layer Elements
IMPLEMENTATION_BEHAVIOR_ELEMENTS = ["WorkPackage", "ImplementationEvent"]
IMPLEMENTATION_PASSIVE_ELEMENTS = ["Deliverable", "Plateau", "Gap"]
IMPLEMENTATION_ELEMENTS = IMPLEMENTATION_BEHAVIOR_ELEMENTS + IMPLEMENTATION_PASSIVE_ELEMENTS

# Other Elements (ArchiMate 3.2 §4.5)
# These belong to no layer: they may be combined with elements from any of them.
OTHER_ELEMENTS = ["Grouping", "Location"]

# Junction (ArchiMate 3.2 §5.4) is a relationship CONNECTOR, not an element -
# it joins several relationships of the same type. It is listed separately for
# that reason, and folded into ALL_ELEMENTS only so that a model containing
# junctions can be validated at all. Before this it was unknown to the matrix,
# which meant every relationship touching a junction validated as "no valid
# relationship exists" rather than being checked.
CONNECTOR_ELEMENTS = ["Junction"]

# All Elements
ALL_ACTIVE_ELEMENTS = (
    BUSINESS_ACTIVE_ELEMENTS
    + APPLICATION_ACTIVE_ELEMENTS
    + TECHNOLOGY_ACTIVE_ELEMENTS
    + PHYSICAL_ACTIVE_ELEMENTS
    + MOTIVATION_ACTIVE_ELEMENTS
    + ["Resource"]
)

ALL_BEHAVIOR_ELEMENTS = (
    BUSINESS_BEHAVIOR_ELEMENTS
    + APPLICATION_BEHAVIOR_ELEMENTS
    + TECHNOLOGY_BEHAVIOR_ELEMENTS
    + IMPLEMENTATION_BEHAVIOR_ELEMENTS
    + ["Capability", "CourseOfAction", "ValueStream"]
)

ALL_PASSIVE_ELEMENTS = (
    BUSINESS_PASSIVE_ELEMENTS
    + APPLICATION_PASSIVE_ELEMENTS
    + TECHNOLOGY_PASSIVE_ELEMENTS
    + PHYSICAL_PASSIVE_ELEMENTS
    + MOTIVATION_PASSIVE_ELEMENTS
    + IMPLEMENTATION_PASSIVE_ELEMENTS
)

ALL_ELEMENTS = (
    STRATEGY_ELEMENTS
    + BUSINESS_ELEMENTS
    + APPLICATION_ELEMENTS
    + TECHNOLOGY_ELEMENTS
    + PHYSICAL_ELEMENTS
    + MOTIVATION_ELEMENTS
    + IMPLEMENTATION_ELEMENTS
    + OTHER_ELEMENTS
    + CONNECTOR_ELEMENTS
)

# The layered elements only - useful when generating rules that should apply to
# "every real element" without Grouping/Location/Junction recursing into
# themselves.
LAYERED_ELEMENTS = (
    STRATEGY_ELEMENTS
    + BUSINESS_ELEMENTS
    + APPLICATION_ELEMENTS
    + TECHNOLOGY_ELEMENTS
    + PHYSICAL_ELEMENTS
    + MOTIVATION_ELEMENTS
    + IMPLEMENTATION_ELEMENTS
)


# =============================================================================
# Complete ArchiMate 3.2 Relationship Matrix
# =============================================================================

# Format: (source_type, target_type): [allowed_relationship_types]
# This matrix is based on ArchiMate 3.2 Specification Appendix B

VALID_RELATIONSHIPS: Dict[Tuple[str, str], List[str]] = {
    # =========================================================================
    # STRATEGY LAYER RELATIONSHIPS
    # =========================================================================
    # Resource relationships
    ("Resource", "Resource"): ["composition", "aggregation", "specialization", "association"],
    ("Resource", "Capability"): ["assignment", "association"],
    ("Resource", "CourseOfAction"): ["association"],
    ("Resource", "ValueStream"): ["association"],
    # Capability relationships
    ("Capability", "Resource"): ["association"],
    ("Capability", "Capability"): ["composition", "aggregation", "specialization", "association"],
    ("Capability", "CourseOfAction"): ["realization", "association"],
    ("Capability", "ValueStream"): ["realization", "serving", "association"],
    ("Capability", "BusinessProcess"): ["realization", "association"],
    ("Capability", "BusinessFunction"): ["realization", "association"],
    ("Capability", "BusinessService"): ["realization", "association"],
    # Course of Action relationships
    ("CourseOfAction", "Resource"): ["association"],
    ("CourseOfAction", "Capability"): ["association"],
    ("CourseOfAction", "CourseOfAction"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("CourseOfAction", "ValueStream"): ["association"],
    ("CourseOfAction", "Goal"): ["realization", "association"],
    ("CourseOfAction", "Requirement"): ["realization", "association"],
    # Value Stream relationships
    ("ValueStream", "Resource"): ["association"],
    ("ValueStream", "Capability"): ["triggering", "association"],
    ("ValueStream", "CourseOfAction"): ["association"],
    ("ValueStream", "ValueStream"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "specialization",
        "association",
    ],
    ("ValueStream", "Outcome"): ["realization", "association"],
    ("ValueStream", "Value"): ["realization", "association"],
    # =========================================================================
    # BUSINESS LAYER RELATIONSHIPS - Active Structure
    # =========================================================================
    # BusinessActor relationships
    ("BusinessActor", "BusinessActor"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("BusinessActor", "BusinessRole"): ["assignment", "association"],
    ("BusinessActor", "BusinessCollaboration"): ["aggregation", "assignment", "association"],
    ("BusinessActor", "BusinessInterface"): ["composition", "association"],
    ("BusinessActor", "BusinessProcess"): ["assignment", "association"],
    ("BusinessActor", "BusinessFunction"): ["assignment", "association"],
    ("BusinessActor", "BusinessInteraction"): ["assignment", "association"],
    ("BusinessActor", "BusinessService"): ["assignment", "serving", "association"],
    ("BusinessActor", "BusinessEvent"): ["triggering", "association"],
    ("BusinessActor", "BusinessObject"): ["access", "association"],
    ("BusinessActor", "ApplicationComponent"): ["assignment", "association"],
    # BusinessRole relationships
    ("BusinessRole", "BusinessActor"): ["association"],
    ("BusinessRole", "BusinessRole"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("BusinessRole", "BusinessCollaboration"): ["aggregation", "assignment", "association"],
    ("BusinessRole", "BusinessInterface"): ["composition", "assignment", "association"],
    ("BusinessRole", "BusinessProcess"): ["assignment", "association"],
    ("BusinessRole", "BusinessFunction"): ["assignment", "association"],
    ("BusinessRole", "BusinessInteraction"): ["assignment", "association"],
    ("BusinessRole", "BusinessService"): ["assignment", "serving", "association"],
    ("BusinessRole", "BusinessEvent"): ["triggering", "association"],
    ("BusinessRole", "BusinessObject"): ["access", "association"],
    # BusinessCollaboration relationships
    ("BusinessCollaboration", "BusinessActor"): ["aggregation", "association"],
    ("BusinessCollaboration", "BusinessRole"): ["aggregation", "association"],
    ("BusinessCollaboration", "BusinessCollaboration"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("BusinessCollaboration", "BusinessInterface"): ["composition", "association"],
    ("BusinessCollaboration", "BusinessProcess"): ["assignment", "association"],
    ("BusinessCollaboration", "BusinessFunction"): ["assignment", "association"],
    ("BusinessCollaboration", "BusinessInteraction"): ["assignment", "association"],
    ("BusinessCollaboration", "BusinessService"): ["serving", "association"],
    # BusinessInterface relationships
    ("BusinessInterface", "BusinessActor"): ["serving", "association"],
    ("BusinessInterface", "BusinessRole"): ["serving", "association"],
    ("BusinessInterface", "BusinessCollaboration"): ["serving", "association"],
    ("BusinessInterface", "BusinessInterface"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("BusinessInterface", "BusinessService"): ["assignment", "serving", "association"],
    ("BusinessInterface", "BusinessProcess"): ["triggering", "flow", "association"],
    # =========================================================================
    # BUSINESS LAYER RELATIONSHIPS - Behavior
    # =========================================================================
    # BusinessProcess relationships
    ("BusinessProcess", "BusinessActor"): ["serving", "association"],
    ("BusinessProcess", "BusinessRole"): ["serving", "association"],
    ("BusinessProcess", "BusinessCollaboration"): ["serving", "association"],
    ("BusinessProcess", "BusinessInterface"): ["serving", "association"],
    ("BusinessProcess", "BusinessProcess"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "specialization",
        "association",
    ],
    ("BusinessProcess", "BusinessFunction"): ["composition", "aggregation", "association"],
    ("BusinessProcess", "BusinessInteraction"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "association",
    ],
    ("BusinessProcess", "BusinessEvent"): ["triggering", "flow", "association"],
    ("BusinessProcess", "BusinessService"): ["realization", "association"],
    ("BusinessProcess", "BusinessObject"): ["access", "association"],
    ("BusinessProcess", "Contract"): ["access", "association"],
    ("BusinessProcess", "Representation"): ["access", "association"],
    ("BusinessProcess", "Product"): ["realization", "association"],
    ("BusinessProcess", "ApplicationService"): ["serving", "association"],
    ("BusinessProcess", "DataObject"): ["access", "association"],
    # BusinessFunction relationships
    ("BusinessFunction", "BusinessActor"): ["serving", "association"],
    ("BusinessFunction", "BusinessRole"): ["serving", "association"],
    ("BusinessFunction", "BusinessCollaboration"): ["serving", "association"],
    ("BusinessFunction", "BusinessInterface"): ["serving", "association"],
    ("BusinessFunction", "BusinessProcess"): [
        "composition",
        "aggregation",
        "triggering",
        "association",
    ],
    ("BusinessFunction", "BusinessFunction"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("BusinessFunction", "BusinessInteraction"): ["composition", "aggregation", "association"],
    ("BusinessFunction", "BusinessEvent"): ["triggering", "association"],
    ("BusinessFunction", "BusinessService"): ["realization", "association"],
    ("BusinessFunction", "BusinessObject"): ["access", "association"],
    ("BusinessFunction", "Contract"): ["access", "association"],
    # BusinessInteraction relationships
    ("BusinessInteraction", "BusinessActor"): ["serving", "association"],
    ("BusinessInteraction", "BusinessRole"): ["serving", "association"],
    ("BusinessInteraction", "BusinessCollaboration"): ["serving", "association"],
    ("BusinessInteraction", "BusinessProcess"): ["triggering", "flow", "association"],
    ("BusinessInteraction", "BusinessFunction"): ["triggering", "association"],
    ("BusinessInteraction", "BusinessInteraction"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "specialization",
        "association",
    ],
    ("BusinessInteraction", "BusinessEvent"): ["triggering", "flow", "association"],
    ("BusinessInteraction", "BusinessService"): ["realization", "association"],
    ("BusinessInteraction", "BusinessObject"): ["access", "association"],
    # BusinessEvent relationships
    ("BusinessEvent", "BusinessProcess"): ["triggering", "association"],
    ("BusinessEvent", "BusinessFunction"): ["triggering", "association"],
    ("BusinessEvent", "BusinessInteraction"): ["triggering", "association"],
    ("BusinessEvent", "BusinessEvent"): [
        "composition",
        "aggregation",
        "triggering",
        "specialization",
        "association",
    ],
    ("BusinessEvent", "BusinessService"): ["triggering", "association"],
    ("BusinessEvent", "BusinessObject"): ["access", "association"],
    # BusinessService relationships
    ("BusinessService", "BusinessActor"): ["serving", "association"],
    ("BusinessService", "BusinessRole"): ["serving", "association"],
    ("BusinessService", "BusinessCollaboration"): ["serving", "association"],
    ("BusinessService", "BusinessInterface"): ["serving", "association"],
    ("BusinessService", "BusinessProcess"): ["serving", "triggering", "association"],
    ("BusinessService", "BusinessFunction"): ["serving", "association"],
    ("BusinessService", "BusinessInteraction"): ["serving", "association"],
    ("BusinessService", "BusinessEvent"): ["triggering", "association"],
    ("BusinessService", "BusinessService"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("BusinessService", "BusinessObject"): ["access", "association"],
    ("BusinessService", "Product"): ["composition", "association"],
    # =========================================================================
    # BUSINESS LAYER RELATIONSHIPS - Passive Structure
    # =========================================================================
    # BusinessObject relationships
    ("BusinessObject", "BusinessObject"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("BusinessObject", "Contract"): ["composition", "aggregation", "association"],
    ("BusinessObject", "Representation"): ["realization", "association"],
    ("BusinessObject", "Product"): ["composition", "association"],
    ("BusinessObject", "DataObject"): ["realization", "association"],
    # Contract relationships
    ("Contract", "BusinessObject"): ["aggregation", "association"],
    ("Contract", "Contract"): ["composition", "aggregation", "specialization", "association"],
    ("Contract", "Representation"): ["realization", "association"],
    ("Contract", "Product"): ["composition", "association"],
    ("Contract", "BusinessService"): ["association"],
    # Representation relationships
    ("Representation", "BusinessObject"): ["association"],
    ("Representation", "Contract"): ["association"],
    ("Representation", "Representation"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("Representation", "Product"): ["composition", "association"],
    # Product relationships
    ("Product", "BusinessActor"): ["serving", "association"],
    ("Product", "BusinessRole"): ["serving", "association"],
    ("Product", "BusinessCollaboration"): ["serving", "association"],
    ("Product", "BusinessInterface"): ["composition", "aggregation", "serving", "association"],
    ("Product", "BusinessService"): ["composition", "aggregation", "association"],
    ("Product", "BusinessObject"): ["composition", "aggregation", "association"],
    ("Product", "Contract"): ["composition", "aggregation", "association"],
    ("Product", "Product"): ["composition", "aggregation", "specialization", "association"],
    ("Product", "Value"): ["realization", "association"],
    # =========================================================================
    # APPLICATION LAYER RELATIONSHIPS - Active Structure
    # =========================================================================
    # ApplicationComponent relationships
    ("ApplicationComponent", "ApplicationComponent"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("ApplicationComponent", "ApplicationCollaboration"): ["aggregation", "association"],
    ("ApplicationComponent", "ApplicationInterface"): ["composition", "assignment", "association"],
    ("ApplicationComponent", "ApplicationFunction"): ["assignment", "association"],
    ("ApplicationComponent", "ApplicationProcess"): ["assignment", "association"],
    ("ApplicationComponent", "ApplicationInteraction"): ["assignment", "association"],
    ("ApplicationComponent", "ApplicationEvent"): ["triggering", "association"],
    ("ApplicationComponent", "ApplicationService"): ["realization", "serving", "association"],
    ("ApplicationComponent", "DataObject"): ["access", "association"],
    ("ApplicationComponent", "BusinessProcess"): ["serving", "association"],
    ("ApplicationComponent", "BusinessFunction"): ["serving", "association"],
    ("ApplicationComponent", "BusinessService"): ["serving", "association"],
    ("ApplicationComponent", "Node"): ["association"],
    # ApplicationCollaboration relationships
    ("ApplicationCollaboration", "ApplicationComponent"): ["aggregation", "association"],
    ("ApplicationCollaboration", "ApplicationCollaboration"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("ApplicationCollaboration", "ApplicationInterface"): ["composition", "association"],
    ("ApplicationCollaboration", "ApplicationFunction"): ["assignment", "association"],
    ("ApplicationCollaboration", "ApplicationProcess"): ["assignment", "association"],
    ("ApplicationCollaboration", "ApplicationInteraction"): ["assignment", "association"],
    ("ApplicationCollaboration", "ApplicationService"): ["realization", "serving", "association"],
    ("ApplicationCollaboration", "DataObject"): ["access", "association"],
    # ApplicationInterface relationships
    ("ApplicationInterface", "ApplicationComponent"): ["serving", "association"],
    ("ApplicationInterface", "ApplicationCollaboration"): ["serving", "association"],
    ("ApplicationInterface", "ApplicationInterface"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("ApplicationInterface", "ApplicationService"): ["assignment", "serving", "association"],
    ("ApplicationInterface", "ApplicationProcess"): ["triggering", "flow", "association"],
    ("ApplicationInterface", "ApplicationEvent"): ["triggering", "flow", "association"],
    ("ApplicationInterface", "DataObject"): ["access", "flow", "association"],
    ("ApplicationInterface", "BusinessActor"): ["serving", "association"],
    ("ApplicationInterface", "BusinessRole"): ["serving", "association"],
    ("ApplicationInterface", "BusinessProcess"): ["serving", "triggering", "flow", "association"],
    # =========================================================================
    # APPLICATION LAYER RELATIONSHIPS - Behavior
    # =========================================================================
    # ApplicationFunction relationships
    ("ApplicationFunction", "ApplicationComponent"): ["serving", "association"],
    ("ApplicationFunction", "ApplicationCollaboration"): ["serving", "association"],
    ("ApplicationFunction", "ApplicationInterface"): ["serving", "association"],
    ("ApplicationFunction", "ApplicationFunction"): [
        "composition",
        "aggregation",
        "triggering",
        "specialization",
        "association",
    ],
    ("ApplicationFunction", "ApplicationProcess"): [
        "composition",
        "aggregation",
        "triggering",
        "association",
    ],
    ("ApplicationFunction", "ApplicationInteraction"): [
        "composition",
        "aggregation",
        "association",
    ],
    ("ApplicationFunction", "ApplicationEvent"): ["triggering", "association"],
    ("ApplicationFunction", "ApplicationService"): ["realization", "association"],
    ("ApplicationFunction", "DataObject"): ["access", "association"],
    ("ApplicationFunction", "BusinessProcess"): ["serving", "association"],
    ("ApplicationFunction", "BusinessFunction"): ["serving", "association"],
    # ApplicationProcess relationships
    ("ApplicationProcess", "ApplicationComponent"): ["serving", "association"],
    ("ApplicationProcess", "ApplicationCollaboration"): ["serving", "association"],
    ("ApplicationProcess", "ApplicationInterface"): ["serving", "association"],
    ("ApplicationProcess", "ApplicationFunction"): ["composition", "aggregation", "association"],
    ("ApplicationProcess", "ApplicationProcess"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "specialization",
        "association",
    ],
    ("ApplicationProcess", "ApplicationInteraction"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "association",
    ],
    ("ApplicationProcess", "ApplicationEvent"): ["triggering", "flow", "association"],
    ("ApplicationProcess", "ApplicationService"): ["realization", "association"],
    ("ApplicationProcess", "DataObject"): ["access", "association"],
    ("ApplicationProcess", "BusinessProcess"): ["serving", "association"],
    # ApplicationInteraction relationships
    ("ApplicationInteraction", "ApplicationComponent"): ["serving", "association"],
    ("ApplicationInteraction", "ApplicationCollaboration"): ["serving", "association"],
    ("ApplicationInteraction", "ApplicationFunction"): ["triggering", "association"],
    ("ApplicationInteraction", "ApplicationProcess"): ["triggering", "flow", "association"],
    ("ApplicationInteraction", "ApplicationInteraction"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "specialization",
        "association",
    ],
    ("ApplicationInteraction", "ApplicationEvent"): ["triggering", "flow", "association"],
    ("ApplicationInteraction", "ApplicationService"): ["realization", "association"],
    ("ApplicationInteraction", "DataObject"): ["access", "association"],
    # ApplicationEvent relationships
    ("ApplicationEvent", "ApplicationFunction"): ["triggering", "association"],
    ("ApplicationEvent", "ApplicationProcess"): ["triggering", "association"],
    ("ApplicationEvent", "ApplicationInteraction"): ["triggering", "association"],
    ("ApplicationEvent", "ApplicationEvent"): [
        "composition",
        "aggregation",
        "triggering",
        "specialization",
        "association",
    ],
    ("ApplicationEvent", "ApplicationService"): ["triggering", "association"],
    ("ApplicationEvent", "DataObject"): ["access", "association"],
    ("ApplicationEvent", "BusinessProcess"): ["triggering", "association"],
    ("ApplicationEvent", "BusinessEvent"): ["triggering", "association"],
    # ApplicationService relationships
    ("ApplicationService", "ApplicationComponent"): ["serving", "association"],
    ("ApplicationService", "ApplicationCollaboration"): ["serving", "association"],
    ("ApplicationService", "ApplicationInterface"): ["serving", "association"],
    ("ApplicationService", "ApplicationFunction"): ["triggering", "association"],
    ("ApplicationService", "ApplicationProcess"): ["triggering", "association"],
    ("ApplicationService", "ApplicationInteraction"): ["triggering", "association"],
    ("ApplicationService", "ApplicationEvent"): ["triggering", "association"],
    ("ApplicationService", "ApplicationService"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("ApplicationService", "DataObject"): ["access", "association"],
    ("ApplicationService", "BusinessActor"): ["serving", "association"],
    ("ApplicationService", "BusinessRole"): ["serving", "association"],
    ("ApplicationService", "BusinessCollaboration"): ["serving", "association"],
    ("ApplicationService", "BusinessProcess"): ["serving", "triggering", "association"],
    ("ApplicationService", "BusinessFunction"): ["serving", "association"],
    ("ApplicationService", "BusinessInteraction"): ["serving", "association"],
    ("ApplicationService", "BusinessService"): ["serving", "association"],
    # =========================================================================
    # APPLICATION LAYER RELATIONSHIPS - Passive Structure
    # =========================================================================
    # DataObject relationships
    ("DataObject", "DataObject"): ["composition", "aggregation", "specialization", "association"],
    ("DataObject", "BusinessObject"): ["realization", "association"],
    ("DataObject", "Artifact"): ["realization", "association"],
    # =========================================================================
    # TECHNOLOGY LAYER RELATIONSHIPS - Active Structure
    # =========================================================================
    # Node relationships
    ("Node", "Node"): ["composition", "aggregation", "serving", "specialization", "association"],
    ("Node", "Device"): ["composition", "aggregation", "association"],
    ("Node", "SystemSoftware"): ["composition", "assignment", "association"],
    ("Node", "TechnologyCollaboration"): ["aggregation", "association"],
    ("Node", "TechnologyInterface"): ["composition", "assignment", "association"],
    ("Node", "Path"): ["assignment", "association"],
    ("Node", "CommunicationNetwork"): ["assignment", "serving", "association"],
    ("Node", "TechnologyFunction"): ["assignment", "association"],
    ("Node", "TechnologyProcess"): ["assignment", "association"],
    ("Node", "TechnologyInteraction"): ["assignment", "association"],
    ("Node", "TechnologyEvent"): ["triggering", "association"],
    ("Node", "TechnologyService"): ["realization", "serving", "association"],
    ("Node", "Artifact"): ["assignment", "association"],
    ("Node", "ApplicationComponent"): ["assignment", "association"],
    # Device relationships
    ("Device", "Node"): ["composition", "aggregation", "association"],
    ("Device", "Device"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("Device", "SystemSoftware"): ["composition", "assignment", "association"],
    ("Device", "TechnologyCollaboration"): ["aggregation", "association"],
    ("Device", "TechnologyInterface"): ["composition", "assignment", "association"],
    ("Device", "Path"): ["assignment", "association"],
    ("Device", "CommunicationNetwork"): ["assignment", "serving", "association"],
    ("Device", "TechnologyFunction"): ["assignment", "association"],
    ("Device", "TechnologyProcess"): ["assignment", "association"],
    ("Device", "TechnologyEvent"): ["triggering", "association"],
    ("Device", "TechnologyService"): ["realization", "serving", "association"],
    ("Device", "Artifact"): ["assignment", "association"],
    ("Device", "ApplicationComponent"): ["assignment", "association"],
    ("Device", "Equipment"): ["realization", "association"],
    # SystemSoftware relationships
    ("SystemSoftware", "Node"): ["serving", "association"],
    ("SystemSoftware", "Device"): ["serving", "association"],
    ("SystemSoftware", "SystemSoftware"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("SystemSoftware", "TechnologyCollaboration"): ["aggregation", "association"],
    ("SystemSoftware", "TechnologyInterface"): ["composition", "assignment", "association"],
    ("SystemSoftware", "TechnologyFunction"): ["assignment", "association"],
    ("SystemSoftware", "TechnologyProcess"): ["assignment", "association"],
    ("SystemSoftware", "TechnologyEvent"): ["triggering", "association"],
    ("SystemSoftware", "TechnologyService"): ["realization", "serving", "association"],
    ("SystemSoftware", "Artifact"): ["assignment", "association"],
    ("SystemSoftware", "ApplicationComponent"): ["assignment", "serving", "association"],
    # TechnologyCollaboration relationships
    ("TechnologyCollaboration", "Node"): ["aggregation", "association"],
    ("TechnologyCollaboration", "Device"): ["aggregation", "association"],
    ("TechnologyCollaboration", "SystemSoftware"): ["aggregation", "association"],
    ("TechnologyCollaboration", "TechnologyCollaboration"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("TechnologyCollaboration", "TechnologyInterface"): ["composition", "association"],
    ("TechnologyCollaboration", "TechnologyInteraction"): ["assignment", "association"],
    ("TechnologyCollaboration", "TechnologyService"): ["realization", "serving", "association"],
    # TechnologyInterface relationships
    ("TechnologyInterface", "Node"): ["serving", "association"],
    ("TechnologyInterface", "Device"): ["serving", "association"],
    ("TechnologyInterface", "SystemSoftware"): ["serving", "association"],
    ("TechnologyInterface", "TechnologyCollaboration"): ["serving", "association"],
    ("TechnologyInterface", "TechnologyInterface"): [
        "composition",
        "aggregation",
        "specialization",
        "association",
    ],
    ("TechnologyInterface", "TechnologyService"): ["assignment", "serving", "association"],
    ("TechnologyInterface", "TechnologyProcess"): ["triggering", "flow", "association"],
    ("TechnologyInterface", "TechnologyEvent"): ["triggering", "flow", "association"],
    ("TechnologyInterface", "Artifact"): ["access", "flow", "association"],
    ("TechnologyInterface", "ApplicationInterface"): ["serving", "association"],
    # Path relationships
    ("Path", "Node"): ["serving", "association"],
    ("Path", "Device"): ["serving", "association"],
    ("Path", "Path"): ["composition", "aggregation", "specialization", "association"],
    ("Path", "CommunicationNetwork"): ["realization", "association"],
    ("Path", "TechnologyService"): ["realization", "association"],
    # CommunicationNetwork relationships
    ("CommunicationNetwork", "Node"): ["serving", "association"],
    ("CommunicationNetwork", "Device"): ["serving", "association"],
    ("CommunicationNetwork", "Path"): ["aggregation", "association"],
    ("CommunicationNetwork", "CommunicationNetwork"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("CommunicationNetwork", "TechnologyService"): ["realization", "serving", "association"],
    ("CommunicationNetwork", "ApplicationComponent"): ["serving", "association"],
    # =========================================================================
    # TECHNOLOGY LAYER RELATIONSHIPS - Behavior
    # =========================================================================
    # TechnologyFunction relationships
    ("TechnologyFunction", "Node"): ["serving", "association"],
    ("TechnologyFunction", "Device"): ["serving", "association"],
    ("TechnologyFunction", "SystemSoftware"): ["serving", "association"],
    ("TechnologyFunction", "TechnologyCollaboration"): ["serving", "association"],
    ("TechnologyFunction", "TechnologyInterface"): ["serving", "association"],
    ("TechnologyFunction", "TechnologyFunction"): [
        "composition",
        "aggregation",
        "triggering",
        "specialization",
        "association",
    ],
    ("TechnologyFunction", "TechnologyProcess"): [
        "composition",
        "aggregation",
        "triggering",
        "association",
    ],
    ("TechnologyFunction", "TechnologyInteraction"): ["composition", "aggregation", "association"],
    ("TechnologyFunction", "TechnologyEvent"): ["triggering", "association"],
    ("TechnologyFunction", "TechnologyService"): ["realization", "association"],
    ("TechnologyFunction", "Artifact"): ["access", "association"],
    ("TechnologyFunction", "ApplicationComponent"): ["serving", "association"],
    ("TechnologyFunction", "ApplicationFunction"): ["serving", "association"],
    # TechnologyProcess relationships
    ("TechnologyProcess", "Node"): ["serving", "association"],
    ("TechnologyProcess", "Device"): ["serving", "association"],
    ("TechnologyProcess", "SystemSoftware"): ["serving", "association"],
    ("TechnologyProcess", "TechnologyCollaboration"): ["serving", "association"],
    ("TechnologyProcess", "TechnologyInterface"): ["serving", "association"],
    ("TechnologyProcess", "TechnologyFunction"): ["composition", "aggregation", "association"],
    ("TechnologyProcess", "TechnologyProcess"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "specialization",
        "association",
    ],
    ("TechnologyProcess", "TechnologyInteraction"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "association",
    ],
    ("TechnologyProcess", "TechnologyEvent"): ["triggering", "flow", "association"],
    ("TechnologyProcess", "TechnologyService"): ["realization", "association"],
    ("TechnologyProcess", "Artifact"): ["access", "association"],
    # TechnologyInteraction relationships
    ("TechnologyInteraction", "Node"): ["serving", "association"],
    ("TechnologyInteraction", "Device"): ["serving", "association"],
    ("TechnologyInteraction", "SystemSoftware"): ["serving", "association"],
    ("TechnologyInteraction", "TechnologyCollaboration"): ["serving", "association"],
    ("TechnologyInteraction", "TechnologyFunction"): ["triggering", "association"],
    ("TechnologyInteraction", "TechnologyProcess"): ["triggering", "flow", "association"],
    ("TechnologyInteraction", "TechnologyInteraction"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "specialization",
        "association",
    ],
    ("TechnologyInteraction", "TechnologyEvent"): ["triggering", "flow", "association"],
    ("TechnologyInteraction", "TechnologyService"): ["realization", "association"],
    ("TechnologyInteraction", "Artifact"): ["access", "association"],
    # TechnologyEvent relationships
    ("TechnologyEvent", "TechnologyFunction"): ["triggering", "association"],
    ("TechnologyEvent", "TechnologyProcess"): ["triggering", "association"],
    ("TechnologyEvent", "TechnologyInteraction"): ["triggering", "association"],
    ("TechnologyEvent", "TechnologyEvent"): [
        "composition",
        "aggregation",
        "triggering",
        "specialization",
        "association",
    ],
    ("TechnologyEvent", "TechnologyService"): ["triggering", "association"],
    ("TechnologyEvent", "Artifact"): ["access", "association"],
    ("TechnologyEvent", "ApplicationEvent"): ["triggering", "association"],
    # TechnologyService relationships
    ("TechnologyService", "Node"): ["serving", "association"],
    ("TechnologyService", "Device"): ["serving", "association"],
    ("TechnologyService", "SystemSoftware"): ["serving", "association"],
    ("TechnologyService", "TechnologyCollaboration"): ["serving", "association"],
    ("TechnologyService", "TechnologyInterface"): ["serving", "association"],
    ("TechnologyService", "TechnologyFunction"): ["triggering", "association"],
    ("TechnologyService", "TechnologyProcess"): ["triggering", "association"],
    ("TechnologyService", "TechnologyInteraction"): ["triggering", "association"],
    ("TechnologyService", "TechnologyEvent"): ["triggering", "association"],
    ("TechnologyService", "TechnologyService"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("TechnologyService", "Artifact"): ["access", "association"],
    ("TechnologyService", "ApplicationComponent"): ["serving", "association"],
    ("TechnologyService", "ApplicationService"): ["serving", "association"],
    # =========================================================================
    # TECHNOLOGY LAYER RELATIONSHIPS - Passive Structure
    # =========================================================================
    # Artifact relationships
    ("Artifact", "Artifact"): ["composition", "aggregation", "specialization", "association"],
    ("Artifact", "DataObject"): ["realization", "association"],
    # =========================================================================
    # PHYSICAL LAYER RELATIONSHIPS
    # =========================================================================
    # Equipment relationships
    ("Equipment", "Equipment"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("Equipment", "Facility"): ["composition", "association"],
    ("Equipment", "DistributionNetwork"): ["assignment", "association"],
    ("Equipment", "Node"): ["assignment", "realization", "association"],
    ("Equipment", "Device"): ["realization", "association"],
    ("Equipment", "Material"): ["access", "association"],
    ("Equipment", "TechnologyService"): ["realization", "association"],
    # Facility relationships
    ("Facility", "Equipment"): ["aggregation", "composition", "association"],
    ("Facility", "Facility"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("Facility", "DistributionNetwork"): ["assignment", "association"],
    ("Facility", "Node"): ["assignment", "composition", "association"],
    ("Facility", "Material"): ["access", "association"],
    ("Facility", "BusinessProcess"): ["assignment", "association"],
    # DistributionNetwork relationships
    ("DistributionNetwork", "Equipment"): ["aggregation", "association"],
    ("DistributionNetwork", "Facility"): ["serving", "association"],
    ("DistributionNetwork", "DistributionNetwork"): [
        "composition",
        "aggregation",
        "serving",
        "specialization",
        "association",
    ],
    ("DistributionNetwork", "Material"): ["flow", "association"],
    ("DistributionNetwork", "CommunicationNetwork"): ["realization", "association"],
    ("DistributionNetwork", "BusinessService"): ["realization", "association"],
    # Material relationships
    ("Material", "Material"): ["composition", "aggregation", "specialization", "association"],
    ("Material", "BusinessObject"): ["realization", "association"],
    # =========================================================================
    # MOTIVATION LAYER RELATIONSHIPS
    # =========================================================================
    # Stakeholder relationships
    ("Stakeholder", "Stakeholder"): ["composition", "aggregation", "specialization", "association"],
    ("Stakeholder", "Driver"): ["association"],
    ("Stakeholder", "Assessment"): ["association"],
    ("Stakeholder", "Goal"): ["association"],
    ("Stakeholder", "Outcome"): ["association"],
    ("Stakeholder", "Principle"): ["association"],
    ("Stakeholder", "Requirement"): ["association"],
    ("Stakeholder", "Constraint"): ["association"],
    ("Stakeholder", "Value"): ["association"],
    # Driver relationships
    ("Driver", "Stakeholder"): ["association"],
    ("Driver", "Driver"): [
        "composition",
        "aggregation",
        "influence",
        "specialization",
        "association",
    ],
    ("Driver", "Assessment"): ["association"],
    ("Driver", "Goal"): ["influence", "association"],
    ("Driver", "Outcome"): ["influence", "association"],
    ("Driver", "Principle"): ["influence", "association"],
    ("Driver", "Requirement"): ["influence", "association"],
    ("Driver", "Constraint"): ["influence", "association"],
    # Assessment relationships
    ("Assessment", "Stakeholder"): ["association"],
    ("Assessment", "Driver"): ["association"],
    ("Assessment", "Assessment"): [
        "composition",
        "aggregation",
        "influence",
        "specialization",
        "association",
    ],
    ("Assessment", "Goal"): ["influence", "association"],
    ("Assessment", "Outcome"): ["influence", "association"],
    ("Assessment", "Principle"): ["influence", "association"],
    ("Assessment", "Requirement"): ["influence", "association"],
    ("Assessment", "Constraint"): ["influence", "association"],
    # Goal relationships
    ("Goal", "Stakeholder"): ["association"],
    ("Goal", "Driver"): ["association"],
    ("Goal", "Assessment"): ["association"],
    ("Goal", "Goal"): ["composition", "aggregation", "influence", "specialization", "association"],
    ("Goal", "Outcome"): ["realization", "influence", "association"],
    ("Goal", "Principle"): ["realization", "influence", "association"],
    ("Goal", "Requirement"): ["realization", "influence", "association"],
    ("Goal", "Constraint"): ["realization", "influence", "association"],
    ("Goal", "Value"): ["association"],
    ("Goal", "Meaning"): ["association"],
    ("Goal", "CourseOfAction"): ["association"],
    ("Goal", "Capability"): ["influence", "association"],
    ("Goal", "Resource"): ["influence", "association"],
    # Outcome relationships
    ("Outcome", "Stakeholder"): ["association"],
    ("Outcome", "Goal"): ["realization", "association"],  # ArchiMate 3.2 §6.3: an outcome realizes a goal
    ("Outcome", "Outcome"): [
        "composition",
        "aggregation",
        "influence",
        "specialization",
        "association",
    ],
    ("Outcome", "Principle"): ["influence", "association"],
    ("Outcome", "Requirement"): ["influence", "association"],
    ("Outcome", "Constraint"): ["influence", "association"],
    ("Outcome", "Value"): ["realization", "association"],
    # Principle relationships
    ("Principle", "Stakeholder"): ["association"],
    ("Principle", "Goal"): ["association"],
    ("Principle", "Outcome"): ["influence", "association"],
    ("Principle", "Principle"): [
        "composition",
        "aggregation",
        "influence",
        "specialization",
        "association",
    ],
    ("Principle", "Requirement"): ["realization", "influence", "association"],
    ("Principle", "Constraint"): ["realization", "influence", "association"],
    # Requirement relationships
    ("Requirement", "Stakeholder"): ["association"],
    ("Requirement", "Goal"): ["association"],
    ("Requirement", "Outcome"): ["influence", "association"],
    ("Requirement", "Principle"): ["association"],
    ("Requirement", "Requirement"): [
        "composition",
        "aggregation",
        "influence",
        "specialization",
        "association",
    ],
    ("Requirement", "Constraint"): ["realization", "influence", "association"],
    ("Requirement", "Meaning"): ["association"],
    # Core element realizations
    ("Requirement", "BusinessProcess"): ["realization", "association"],
    ("Requirement", "BusinessFunction"): ["realization", "association"],
    ("Requirement", "BusinessService"): ["realization", "association"],
    ("Requirement", "ApplicationComponent"): ["realization", "association"],
    ("Requirement", "ApplicationService"): ["realization", "association"],
    ("Requirement", "Node"): ["realization", "association"],
    ("Requirement", "TechnologyService"): ["realization", "association"],
    # Constraint relationships
    ("Constraint", "Stakeholder"): ["association"],
    ("Constraint", "Goal"): ["association"],
    ("Constraint", "Outcome"): ["influence", "association"],
    ("Constraint", "Principle"): ["association"],
    ("Constraint", "Requirement"): ["influence", "association"],
    ("Constraint", "Constraint"): [
        "composition",
        "aggregation",
        "influence",
        "specialization",
        "association",
    ],
    # Core element constraints
    ("Constraint", "BusinessProcess"): ["realization", "association"],
    ("Constraint", "BusinessFunction"): ["realization", "association"],
    ("Constraint", "BusinessService"): ["realization", "association"],
    ("Constraint", "ApplicationComponent"): ["realization", "association"],
    ("Constraint", "ApplicationService"): ["realization", "association"],
    # Meaning relationships
    ("Meaning", "Meaning"): ["composition", "aggregation", "specialization", "association"],
    ("Meaning", "BusinessObject"): ["association"],
    ("Meaning", "Representation"): ["association"],
    ("Meaning", "Requirement"): ["association"],
    ("Meaning", "Value"): ["association"],
    # Value relationships
    ("Value", "Stakeholder"): ["association"],
    ("Value", "Goal"): ["association"],
    ("Value", "Outcome"): ["association"],
    ("Value", "Value"): ["composition", "aggregation", "specialization", "association"],
    ("Value", "Meaning"): ["association"],
    ("Value", "Product"): ["association"],
    ("Value", "BusinessService"): ["association"],
    # =========================================================================
    # IMPLEMENTATION & MIGRATION LAYER RELATIONSHIPS
    # =========================================================================
    # WorkPackage relationships
    ("WorkPackage", "WorkPackage"): [
        "composition",
        "aggregation",
        "triggering",
        "flow",
        "specialization",
        "association",
    ],
    ("WorkPackage", "ImplementationEvent"): ["triggering", "flow", "association"],
    ("WorkPackage", "Deliverable"): ["realization", "access", "association"],
    ("WorkPackage", "Plateau"): ["realization", "association"],
    ("WorkPackage", "Gap"): ["association"],
    # WorkPackage realizes core elements
    ("WorkPackage", "BusinessProcess"): ["realization", "association"],
    ("WorkPackage", "BusinessFunction"): ["realization", "association"],
    ("WorkPackage", "BusinessService"): ["realization", "association"],
    ("WorkPackage", "ApplicationComponent"): ["realization", "association"],
    ("WorkPackage", "ApplicationService"): ["realization", "association"],
    ("WorkPackage", "Node"): ["realization", "association"],
    ("WorkPackage", "TechnologyService"): ["realization", "association"],
    ("WorkPackage", "Capability"): ["realization", "association"],
    ("WorkPackage", "CourseOfAction"): ["realization", "association"],
    # ArchiMate 3.2 §12.4 with Appendix B: a work package realizes a
    # deliverable, a deliverable realizes an outcome, and the §5.7
    # derivation of a realization chain is realization.
    ("WorkPackage", "Outcome"): ["realization", "association"],
    # ImplementationEvent relationships
    ("ImplementationEvent", "WorkPackage"): ["triggering", "association"],
    ("ImplementationEvent", "ImplementationEvent"): [
        "composition",
        "aggregation",
        "triggering",
        "specialization",
        "association",
    ],
    ("ImplementationEvent", "Deliverable"): ["access", "association"],
    ("ImplementationEvent", "Plateau"): ["triggering", "association"],
    # Deliverable relationships
    ("Deliverable", "Deliverable"): ["composition", "aggregation", "specialization", "association"],
    ("Deliverable", "Plateau"): ["association"],
    # Deliverable realizes core elements
    ("Deliverable", "BusinessObject"): ["realization", "association"],
    ("Deliverable", "DataObject"): ["realization", "association"],
    ("Deliverable", "Artifact"): ["realization", "association"],
    # Plateau relationships
    ("Plateau", "Plateau"): [
        "composition",
        "aggregation",
        "triggering",
        "specialization",
        "association",
    ],
    ("Plateau", "Gap"): ["association"],
    # Plateau aggregates core elements (represents architecture state)
    ("Plateau", "BusinessProcess"): ["composition", "aggregation", "association"],
    ("Plateau", "BusinessFunction"): ["composition", "aggregation", "association"],
    ("Plateau", "BusinessService"): ["composition", "aggregation", "association"],
    ("Plateau", "BusinessObject"): ["composition", "aggregation", "association"],
    ("Plateau", "ApplicationComponent"): ["composition", "aggregation", "association"],
    ("Plateau", "ApplicationService"): ["composition", "aggregation", "association"],
    ("Plateau", "DataObject"): ["composition", "aggregation", "association"],
    ("Plateau", "Node"): ["composition", "aggregation", "association"],
    ("Plateau", "TechnologyService"): ["composition", "aggregation", "association"],
    ("Plateau", "Capability"): ["composition", "aggregation", "association"],
    # Gap relationships
    ("Gap", "Plateau"): ["association"],
    ("Gap", "Gap"): ["composition", "aggregation", "specialization", "association"],
    # Gap associated with core elements (what's missing/changing)
    ("Gap", "BusinessProcess"): ["association"],
    ("Gap", "BusinessService"): ["association"],
    ("Gap", "ApplicationComponent"): ["association"],
    ("Gap", "ApplicationService"): ["association"],
    ("Gap", "Node"): ["association"],
    ("Gap", "TechnologyService"): ["association"],
    ("Gap", "Capability"): ["association"],
    # =========================================================================
    # CROSS-LAYER RELATIONSHIPS (Additional)
    # =========================================================================
    # Strategy to Business
    ("Capability", "BusinessActor"): ["association"],
    ("Capability", "BusinessRole"): ["association"],
    ("Resource", "BusinessActor"): ["assignment", "association"],
    ("Resource", "BusinessRole"): ["assignment", "association"],
    ("ValueStream", "BusinessProcess"): ["triggering", "association"],
    # Strategy to Application
    ("Capability", "ApplicationComponent"): ["realization", "association"],
    ("Capability", "ApplicationService"): ["realization", "association"],
    # Strategy to Technology
    ("Capability", "Node"): ["realization", "association"],
    ("Capability", "TechnologyService"): ["realization", "association"],
    ("Resource", "Node"): ["assignment", "association"],
    # Business to Application (additional cross-layer)
    ("BusinessInterface", "ApplicationInterface"): ["serving", "association"],
    ("BusinessEvent", "ApplicationEvent"): ["triggering", "association"],
    ("BusinessObject", "ApplicationComponent"): ["access", "association"],
    # Application to Technology (additional cross-layer)
    ("ApplicationInterface", "TechnologyInterface"): ["serving", "association"],
    ("ApplicationEvent", "TechnologyEvent"): ["triggering", "association"],
    ("ApplicationProcess", "TechnologyService"): ["serving", "association"],
    # Technology to Physical
    ("Node", "Facility"): ["association"],
    ("Node", "Equipment"): ["assignment", "association"],
    ("CommunicationNetwork", "DistributionNetwork"): ["association"],
    ("Artifact", "Material"): ["realization", "association"],
    # Motivation to Core Layers (influence relationships)
    ("Goal", "BusinessProcess"): ["influence", "association"],
    ("Goal", "BusinessService"): ["influence", "association"],
    ("Goal", "ApplicationComponent"): ["influence", "association"],
    ("Goal", "ApplicationService"): ["influence", "association"],
    ("Principle", "BusinessProcess"): ["influence", "association"],
    ("Principle", "BusinessService"): ["influence", "association"],
    ("Principle", "ApplicationComponent"): ["influence", "association"],
    ("Driver", "BusinessProcess"): ["influence", "association"],
    ("Driver", "Capability"): ["influence", "association"],
    ("Assessment", "BusinessProcess"): ["influence", "association"],
    ("Assessment", "Capability"): ["influence", "association"],
}


# =============================================================================
# Cardinality Constraints
# =============================================================================

RELATIONSHIP_CARDINALITY: Dict[str, Tuple[int, Optional[int]]] = {
    "composition": (1, 1),  # Exactly one parent (composite)
    "aggregation": (0, None),  # Zero or more
    "assignment": (0, None),  # Zero or more
    "realization": (1, None),  # At least one (something must realize)
    "serving": (0, None),  # Zero or more
    "access": (0, None),  # Zero or more
    "influence": (0, None),  # Zero or more
    "triggering": (0, None),  # Zero or more
    "flow": (0, None),  # Zero or more
    "specialization": (0, 1),  # At most one parent
    "association": (0, None),  # Zero or more
}


# =============================================================================
# Derivation Rules (ArchiMate 3.2 Appendix B)
# =============================================================================

# Derivation strength order (strongest to weakest)
DERIVATION_STRENGTH_ORDER = [
    "composition",
    "aggregation",
    "assignment",
    "realization",
    "serving",
    "access",
    "influence",
    "triggering",
    "flow",
    "association",
]

# =============================================================================
# Other Elements and Junction — generated rules
# =============================================================================
# Generated rather than enumerated: Grouping and Location relate to every one of
# the ~58 layered element types, and hand-writing 350+ pairs would be unreadable
# and would drift the moment an element is added. Written with setdefault so an
# explicit rule above always wins.


def _add_rule(source: str, target: str, types: List[str]) -> None:
    """Register a pair without overwriting an explicitly declared rule."""
    VALID_RELATIONSHIPS.setdefault((source, target), types)


# Grouping (§4.5.1) and Location (§4.5.2) belong to no layer and are meant to be
# combined freely with elements from all of them. They are encoded permissively
# on purpose.
#
# The temptation is to allow Grouping only composition/aggregation/association,
# which is what it is FOR. That would be tighter than the specification and
# would turn a previously-tolerated model into a conformance error - exactly the
# trade this codebase already rejected once, in the rule that an absent matrix
# entry warns rather than rejects: "absence of a rule is not evidence of a
# violation". Over-restricting these two produces false positives on real
# models, and a validator that cries wolf gets switched off.
#
# Specialization is the exception. "This grouping is a kind of business process"
# is not a statement anyone means, so it is permitted only between two elements
# of the same other-type.
_OTHER_ELEMENT_RELATIONSHIPS = [r for r in RELATIONSHIP_TYPES if r != "specialization"]

for _element in LAYERED_ELEMENTS:
    for _other in OTHER_ELEMENTS:
        _add_rule(_other, _element, list(_OTHER_ELEMENT_RELATIONSHIPS))
        _add_rule(_element, _other, list(_OTHER_ELEMENT_RELATIONSHIPS))

# Between two of the same other-type, specialization does mean something.
for _other in OTHER_ELEMENTS:
    _add_rule(_other, _other, list(RELATIONSHIP_TYPES))
_add_rule("Grouping", "Location", list(_OTHER_ELEMENT_RELATIONSHIPS))
_add_rule("Location", "Grouping", list(_OTHER_ELEMENT_RELATIONSHIPS))

# Junction (§5.4) joins relationships of the SAME type, so it inherits whichever
# type it is joining. Specialization is excluded - a junction cannot express an
# is-a. Encoded permissively on purpose: the junction itself carries no
# semantics, and the relationships either side are validated on their own terms.
_JUNCTION_RELATIONSHIPS = [r for r in RELATIONSHIP_TYPES if r != "specialization"]
for _element in LAYERED_ELEMENTS + OTHER_ELEMENTS + CONNECTOR_ELEMENTS:
    _add_rule("Junction", _element, list(_JUNCTION_RELATIONSHIPS))
    _add_rule(_element, "Junction", list(_JUNCTION_RELATIONSHIPS))

# Association between any two concepts (ArchiMate 3.2 §5.2.4: "an association
# relationship... may be used whenever no other relationship in the
# specification applies" between any two elements). Encoded as one derived
# pass over every ordered pair of ALL_ELEMENTS, appending "association" where
# a row lacks it (and creating the row where it is missing entirely), for the
# same reason the Grouping/Location/Junction passes above are generated
# rather than hand-authored: an element added to ALL_ELEMENTS gets its
# association rows automatically, with nothing to fall out of sync.
for _assoc_source in ALL_ELEMENTS:
    for _assoc_target in ALL_ELEMENTS:
        _assoc_entry = VALID_RELATIONSHIPS.setdefault((_assoc_source, _assoc_target), [])
        if "association" not in _assoc_entry:
            _assoc_entry.append("association")

del _assoc_source, _assoc_target, _assoc_entry

# Dynamic relationships between ACTIVE STRUCTURE elements.
#
# The matrix allowed triggering and flow between behaviour elements
# (BusinessProcess → BusinessProcess had both) but not between active structure
# elements: ApplicationComponent → ApplicationComponent offered composition,
# aggregation, serving, specialization and association only. ArchiMate 3.2 §5.3
# places no such restriction - a component sending a file to another component
# is a flow, and it is how almost every real application landscape is drawn.
#
# Found by conformance-checking a production solution diagram on import: 33 of
# its 103 relationships were reported as violations, every one of them an
# ApplicationComponent --flow--> ApplicationComponent. A conformance report that
# is wrong a third of the time is a report nobody reads.
for _source in ALL_ACTIVE_ELEMENTS:
    for _target in ALL_ACTIVE_ELEMENTS:
        _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
        for _dynamic in ("triggering", "flow"):
            if _dynamic not in _allowed:
                _allowed.append(_dynamic)

del _element, _other, _source, _target, _allowed, _dynamic

# Realization may only point at a MORE ABSTRACT entity (ArchiMate 3.2 §5.1.3), so an
# active structure element is never a valid TARGET. Enforced here as a derived pass over
# the finished table rather than by editing the offending entries, because the entries
# are the symptom: the table is authored per-direction, so a pair could be written
# correctly one way round and wrongly the other. Twelve hand-authored pairs were wrong
# this way, and every one of them was the reverse of a legitimate rule —
# ApplicationComponent --realization--> Requirement is valid, Requirement -->
# ApplicationComponent is not; likewise Capability, WorkPackage, Node and Equipment.
#
# Grouping, Location and Junction are exempt. They belong to no layer and are encoded
# permissively on purpose (see the rationale above); narrowing them here would
# re-litigate that decision as a side effect of a different fix.
_CORE_ACTIVE_ELEMENTS = frozenset(
    BUSINESS_ACTIVE_ELEMENTS
    + APPLICATION_ACTIVE_ELEMENTS
    + TECHNOLOGY_ACTIVE_ELEMENTS
    + PHYSICAL_ACTIVE_ELEMENTS
)

for _pair, _types in VALID_RELATIONSHIPS.items():
    if "realization" not in _types:
        continue
    _tgt = _pair[1]
    if _tgt in OTHER_ELEMENTS or _tgt in CONNECTOR_ELEMENTS:
        continue
    # Only the four core layers. Strategy's Resource is a legitimate realization
    # target (a Node realises a Resource) and motivation's Stakeholder is governed by
    # the motivation rules, so neither is swept up by ALL_ACTIVE_ELEMENTS here.
    if _tgt in _CORE_ACTIVE_ELEMENTS:
        _types.remove("realization")

del _pair, _types, _tgt


# =============================================================================
# The rule-based service this matrix replaced used to grant several of the
# rows below through its own per-layer rank rules; the matrix regressed them
# when it became the one relationship-validity authority. Landed here on
# their own ArchiMate 3.2 citations instead of carried over from the old
# rules. Each pass follows the style above: setdefault the row, then append
# the type only if the row lacks it, so nothing already present is touched.
# =============================================================================

# -- Influence ----------------------------------------------------------------
# ArchiMate 3.2 §5.2.3: influence represents that an element affects the
# achievement of a motivation element; §5.7.2: an influence on a requirement
# realized by a core element is an influence on that element -- the rule the
# matrix's existing Goal->BusinessProcess, Driver->Capability and
# Assessment->Capability rows already rely on; Appendix B.3 motivation
# tables. Any ordered pair with at least one motivation-layer endpoint gains
# influence. A strategy element with no motivation endpoint of its own (a
# course of action shaping a capability) is realization (§7.3), not
# influence, and this pass does not touch a strategy-only pair.
for _source in LAYERED_ELEMENTS:
    for _target in LAYERED_ELEMENTS:
        if _source in MOTIVATION_ELEMENTS or _target in MOTIVATION_ELEMENTS:
            _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
            if "influence" not in _allowed:
                _allowed.append("influence")

del _source, _target, _allowed

# -- Serving --------------------------------------------------------------
# §5.7.1: a process realizes a service that serves a process, so the derived
# relationship within one core layer is serving; Appendix B.2 core tables
# carry serving between processes, functions, interactions and services.
_NON_EVENT_BUSINESS_BEHAVIOUR = [e for e in BUSINESS_BEHAVIOR_ELEMENTS if e != "BusinessEvent"]
_NON_EVENT_APPLICATION_BEHAVIOUR = [
    e for e in APPLICATION_BEHAVIOR_ELEMENTS if e != "ApplicationEvent"
]
_NON_EVENT_TECHNOLOGY_BEHAVIOUR = [
    e for e in TECHNOLOGY_BEHAVIOR_ELEMENTS if e != "TechnologyEvent"
]

for _layer_non_event_behaviour in (
    _NON_EVENT_BUSINESS_BEHAVIOUR,
    _NON_EVENT_APPLICATION_BEHAVIOUR,
    _NON_EVENT_TECHNOLOGY_BEHAVIOUR,
):
    for _source in _layer_non_event_behaviour:
        for _target in _layer_non_event_behaviour:
            _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
            if "serving" not in _allowed:
                _allowed.append("serving")

del _layer_non_event_behaviour, _source, _target, _allowed

# §12.1 and §12.2: serving crosses layers in both directions; §11: physical
# active structure participates on the same basis as technology-layer active
# structure. Core elements of active-structure or behaviour aspect,
# excluding events and passive structure, gain serving across a different
# core layer (business, application, technology, physical are each their
# own group for this pass).
_SERVING_CORE_ELEMENTS = (
    BUSINESS_ACTIVE_ELEMENTS
    + _NON_EVENT_BUSINESS_BEHAVIOUR
    + APPLICATION_ACTIVE_ELEMENTS
    + _NON_EVENT_APPLICATION_BEHAVIOUR
    + TECHNOLOGY_ACTIVE_ELEMENTS
    + _NON_EVENT_TECHNOLOGY_BEHAVIOUR
    + PHYSICAL_ACTIVE_ELEMENTS
)


def _serving_core_group(element_type: str) -> Optional[str]:
    if element_type in BUSINESS_ACTIVE_ELEMENTS or element_type in _NON_EVENT_BUSINESS_BEHAVIOUR:
        return "Business"
    if (
        element_type in APPLICATION_ACTIVE_ELEMENTS
        or element_type in _NON_EVENT_APPLICATION_BEHAVIOUR
    ):
        return "Application"
    if (
        element_type in TECHNOLOGY_ACTIVE_ELEMENTS
        or element_type in _NON_EVENT_TECHNOLOGY_BEHAVIOUR
    ):
        return "Technology"
    if element_type in PHYSICAL_ACTIVE_ELEMENTS:
        return "Physical"
    return None


for _source in _SERVING_CORE_ELEMENTS:
    for _target in _SERVING_CORE_ELEMENTS:
        if _serving_core_group(_source) == _serving_core_group(_target):
            continue
        _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
        if "serving" not in _allowed:
            _allowed.append("serving")

del _source, _target, _allowed

# -- Realization ------------------------------------------------------------
_MOTIVATION_REALIZATION_TARGETS = ["Goal", "Outcome", "Principle", "Requirement", "Constraint"]
_CORE_LAYER_ELEMENTS = (
    BUSINESS_ELEMENTS + APPLICATION_ELEMENTS + TECHNOLOGY_ELEMENTS + PHYSICAL_ELEMENTS
)
_CORE_STRUCTURE_ELEMENTS = (
    BUSINESS_ACTIVE_ELEMENTS + BUSINESS_PASSIVE_ELEMENTS
    + APPLICATION_ACTIVE_ELEMENTS + APPLICATION_PASSIVE_ELEMENTS
    + TECHNOLOGY_ACTIVE_ELEMENTS + TECHNOLOGY_PASSIVE_ELEMENTS
    + PHYSICAL_ACTIVE_ELEMENTS + PHYSICAL_PASSIVE_ELEMENTS
)
_CORE_BEHAVIOUR_NON_EVENT = (
    _NON_EVENT_BUSINESS_BEHAVIOUR + _NON_EVENT_APPLICATION_BEHAVIOUR + _NON_EVENT_TECHNOLOGY_BEHAVIOUR
)
_NON_ACTIVE_CORE_ELEMENTS = [e for e in _CORE_LAYER_ELEMENTS if e not in _CORE_ACTIVE_ELEMENTS]


def _add_realization(source: str, target: str) -> None:
    _allowed = VALID_RELATIONSHIPS.setdefault((source, target), [])
    if "realization" not in _allowed:
        _allowed.append("realization")


# §5.7.1: assignment to a process that realizes the service derives
# realization; the matrix already has ApplicationComponent -> ApplicationService
# on the same basis.
for _source in ("BusinessActor", "BusinessRole", "BusinessCollaboration"):
    _add_realization(_source, "BusinessService")
del _source

# §12.1: application behaviour realizes business behaviour, a data object
# realizes a business object and Contract is a specialization of BusinessObject
# (§8.4.2).
for _source in _NON_EVENT_APPLICATION_BEHAVIOUR:
    for _target in _NON_EVENT_BUSINESS_BEHAVIOUR:
        _add_realization(_source, _target)
del _source, _target
_add_realization("DataObject", "Contract")

# §6.3.4 and §6.3.6: a requirement or constraint is realized by any core
# element; §5.7.1 chains that through Requirement -> Outcome -> Goal and
# Requirement -> Principle.
for _source in _CORE_LAYER_ELEMENTS:
    for _target in _MOTIVATION_REALIZATION_TARGETS:
        _add_realization(_source, _target)
del _source, _target

# §7.6: capabilities are realized by structure and behaviour elements,
# resources by structure elements, value streams by behaviour.
for _source in _CORE_LAYER_ELEMENTS:
    _add_realization(_source, "Capability")
for _source in _CORE_STRUCTURE_ELEMENTS:
    _add_realization(_source, "Resource")
for _source in _CORE_BEHAVIOUR_NON_EVENT:
    _add_realization(_source, "ValueStream")
del _source

# §7.5: strategy elements realize motivation elements; Appendix B.3. Lands
# Capability -> Requirement and CourseOfAction -> Outcome, beside the
# CourseOfAction -> Goal and -> Requirement rows already pinned, so the
# CourseOfAction inconsistency (Goal and Requirement present, Outcome
# absent) is closed.
for _source in STRATEGY_ELEMENTS:
    for _target in _MOTIVATION_REALIZATION_TARGETS:
        _add_realization(_source, _target)
del _source, _target

# §13.2.2: a deliverable realizes core elements; §5.7.1: a work package
# realizes a deliverable that realizes them, so a work package realizes them
# too. Neither realizes an active-structure element (§5.1.3, enforced by the
# realization-target sweep above, which this pass runs after).
for _target in _NON_ACTIVE_CORE_ELEMENTS:
    _add_realization("WorkPackage", _target)
    _add_realization("Deliverable", _target)
del _target

# §6.3.3 and §6.3.5.
_add_realization("Requirement", "Goal")
_add_realization("Requirement", "Outcome")
_add_realization("Constraint", "Principle")

# -- Triggering and flow ------------------------------------------------------
# §5.4.1: triggering joins behaviour elements and events; §5.7.2: dynamic
# relationships derive through the realization landed above; Appendix B.2.
# §5.4.2 for flow. Business<->technology stays refused: no realization joins
# those layers directly, so nothing derives it.
for _layer_behaviour in (
    BUSINESS_BEHAVIOR_ELEMENTS, APPLICATION_BEHAVIOR_ELEMENTS, TECHNOLOGY_BEHAVIOR_ELEMENTS,
):
    for _source in _layer_behaviour:
        for _target in _layer_behaviour:
            _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
            for _dynamic in ("triggering", "flow"):
                if _dynamic not in _allowed:
                    _allowed.append(_dynamic)

_ADJACENT_BEHAVIOUR_LAYERS = [
    (BUSINESS_BEHAVIOR_ELEMENTS, APPLICATION_BEHAVIOR_ELEMENTS),
    (APPLICATION_BEHAVIOR_ELEMENTS, TECHNOLOGY_BEHAVIOR_ELEMENTS),
]
for _layer_a, _layer_b in _ADJACENT_BEHAVIOUR_LAYERS:
    for _source in _layer_a:
        for _target in _layer_b:
            _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
            for _dynamic in ("triggering", "flow"):
                if _dynamic not in _allowed:
                    _allowed.append(_dynamic)
    for _source in _layer_b:
        for _target in _layer_a:
            _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
            for _dynamic in ("triggering", "flow"):
                if _dynamic not in _allowed:
                    _allowed.append(_dynamic)

del _layer_behaviour, _layer_a, _layer_b, _source, _target, _allowed, _dynamic

# -- Composition and aggregation ----------------------------------------------
# §8.3, §9.3, §10.3: an interaction of one layer composes and aggregates the
# process and function of the same layer; the matrix already has the other
# four directions per layer. §5.1.1 and §5.1.2 define composition and
# aggregation per element type in each layer metamodel, not per aspect, so
# this pass is these six explicit pairs, not a generated sweep.
_INTERACTION_STRUCTURE_PAIRS = [
    ("BusinessInteraction", "BusinessProcess"), ("BusinessInteraction", "BusinessFunction"),
    ("ApplicationInteraction", "ApplicationFunction"), ("ApplicationInteraction", "ApplicationProcess"),
    ("TechnologyInteraction", "TechnologyFunction"), ("TechnologyInteraction", "TechnologyProcess"),
]
for _source, _target in _INTERACTION_STRUCTURE_PAIRS:
    _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
    for _structural in ("composition", "aggregation"):
        if _structural not in _allowed:
            _allowed.append(_structural)

del _source, _target, _allowed, _structural

# -- Assignment ---------------------------------------------------------------
# §10.2: node, device and system software are assigned to technology
# behaviour; the matrix has them for function and process already.
for _source in ("Device", "SystemSoftware"):
    _allowed = VALID_RELATIONSHIPS.setdefault((_source, "TechnologyInteraction"), [])
    if "assignment" not in _allowed:
        _allowed.append("assignment")

del _source, _allowed

# -- Access ---------------------------------------------------------------
# §5.7.1: assignment followed by access derives access; the matrix already
# holds BusinessActor -> BusinessObject and ApplicationComponent -> DataObject
# on that basis. §8.4 for the business passive-structure targets.
for _source in BUSINESS_BEHAVIOR_ELEMENTS:
    for _target in ("Contract", "Representation"):
        _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
        if "access" not in _allowed:
            _allowed.append("access")

for _source in ("BusinessActor", "BusinessRole", "BusinessCollaboration", "BusinessInterface"):
    for _target in ("BusinessObject", "Contract", "Representation"):
        _allowed = VALID_RELATIONSHIPS.setdefault((_source, _target), [])
        if "access" not in _allowed:
            _allowed.append("access")

for _source in ("Node", "Device", "SystemSoftware", "TechnologyCollaboration"):
    _allowed = VALID_RELATIONSHIPS.setdefault((_source, "Artifact"), [])
    if "access" not in _allowed:
        _allowed.append("access")

del _source, _target, _allowed


# Relationships that can participate in derivation chains
DERIVABLE_RELATIONSHIPS = {
    "composition",
    "aggregation",
    "assignment",
    "realization",
    "serving",
    "access",
    "influence",
    "triggering",
    "flow",
    "association",
}

# Specialization always yields the most specific relationship
# Association is always a valid derived relationship (fallback)


# =============================================================================
# Utility Functions
# =============================================================================


def normalize_element_type(value: str) -> str:
    """Convert a stored element type to the PascalCase keys this matrix uses.

    Handles snake_case ("application_component") and kebab-case
    ("application-component") from the database, and leaves an already
    PascalCase value ("ApplicationComponent") alone.

    ``str.capitalize()`` lowercases the remainder of the string, so a value
    already stored as "ApplicationComponent" became "Applicationcomponent"
    and matched nothing in this matrix. That refused a legal relationship
    between two elements whose types were already PascalCase -- a validator
    that blocks correct modelling is worse than no validator. The same fix
    existed twice under different names (one call site's ``_pascal``,
    another's ``_normalize_type``); it lives here once now, and both call
    sites use this.
    """
    text = str(value or "").replace("-", "_")
    if "_" not in text and text[:1].isupper():
        return text  # already PascalCase
    return "".join(part[:1].upper() + part[1:] for part in text.split("_") if part)


def get_valid_relationships(source_type: str, target_type: str) -> List[str]:
    """
    Get valid relationship types between two element types.

    Args:
        source_type: The source ArchiMate element type
        target_type: The target ArchiMate element type

    Returns:
        List of valid relationship type names, or empty list if none valid
    """
    return VALID_RELATIONSHIPS.get((source_type, target_type), [])


def is_valid_relationship(source_type: str, target_type: str, relationship_type: str) -> bool:
    """
    Check if a relationship is valid per ArchiMate 3.2 specification.

    Args:
        source_type: The source ArchiMate element type
        target_type: The target ArchiMate element type
        relationship_type: The relationship type to validate

    Returns:
        True if the relationship is valid, False otherwise
    """
    valid = get_valid_relationships(source_type, target_type)
    return relationship_type.lower() in [r.lower() for r in valid]


def get_cardinality(relationship_type: str) -> Tuple[int, Optional[int]]:
    """
    Get cardinality constraints for a relationship type.

    Args:
        relationship_type: The relationship type

    Returns:
        Tuple of (min, max) where max=None means unlimited
    """
    return RELATIONSHIP_CARDINALITY.get(relationship_type.lower(), (0, None))


def get_relationship_category(relationship_type: str) -> Optional[RelationshipCategory]:
    """
    Get the category of a relationship type.

    Args:
        relationship_type: The relationship type

    Returns:
        RelationshipCategory enum value, or None if not found
    """
    definition = RELATIONSHIP_TYPE_DEFINITIONS.get(relationship_type.lower())
    return definition.category if definition else None


def get_all_valid_sources_for_target(target_type: str) -> Dict[str, List[str]]:
    """
    Get all valid source types and their allowed relationships for a target.

    Args:
        target_type: The target ArchiMate element type

    Returns:
        Dict mapping source types to lists of valid relationship types
    """
    result = {}
    for (src, tgt), relationships in VALID_RELATIONSHIPS.items():
        if tgt == target_type:
            result[src] = relationships
    return result


def get_all_valid_targets_for_source(source_type: str) -> Dict[str, List[str]]:
    """
    Get all valid target types and their allowed relationships for a source.

    Args:
        source_type: The source ArchiMate element type

    Returns:
        Dict mapping target types to lists of valid relationship types
    """
    result = {}
    for (src, tgt), relationships in VALID_RELATIONSHIPS.items():
        if src == source_type:
            result[tgt] = relationships
    return result


def get_derivation_strength(relationship_type: str) -> int:
    """
    Get the derivation strength of a relationship type.
    Higher values mean stronger relationships in derivation chains.

    Args:
        relationship_type: The relationship type

    Returns:
        Integer strength value (0 - 10, higher is stronger)
    """
    definition = RELATIONSHIP_TYPE_DEFINITIONS.get(relationship_type.lower())
    return definition.derivation_strength if definition else 0


def derive_relationship(chain: List[str]) -> str:
    """
    Derive the resulting relationship type from a chain of relationships.
    Per ArchiMate 3.2, the derived relationship is typically the weakest in the chain.

    Args:
        chain: List of relationship type names in the chain

    Returns:
        The derived relationship type name
    """
    if not chain:
        return "association"

    # Find the weakest relationship (lowest derivation strength)
    min_strength = float("inf")
    weakest = "association"

    for rel_type in chain:
        strength = get_derivation_strength(rel_type)
        if strength < min_strength:
            min_strength = strength
            weakest = rel_type

    return weakest


def can_derive_relationship(relationship_type: str) -> bool:
    """
    Check if a relationship type can participate in derivation.

    Args:
        relationship_type: The relationship type

    Returns:
        True if the relationship can be derived
    """
    definition = RELATIONSHIP_TYPE_DEFINITIONS.get(relationship_type.lower())
    return definition.can_be_derived if definition else False


# The one definition of "Title Case layer name" -> "lowercase single-word
# key". app.models.archimate_core's coarse layer-triple projection and
# app.services.archimate_validity_service's practitioner-warning helpers each
# used to carry their own copy of this same seven-entry map; both now import
# it from here.
LAYER_NAME_TO_KEY: Dict[str, str] = {
    "Strategy": "strategy",
    "Business": "business",
    "Application": "application",
    "Technology": "technology",
    "Physical": "physical",
    "Motivation": "motivation",
    "Implementation & Migration": "implementation",
}


def get_element_layer(element_type: str) -> Optional[str]:
    """
    Get the ArchiMate layer for an element type.

    Args:
        element_type: The ArchiMate element type

    Returns:
        Layer name string, or None if not found
    """
    if element_type in STRATEGY_ELEMENTS:
        return "Strategy"
    elif element_type in BUSINESS_ELEMENTS:
        return "Business"
    elif element_type in APPLICATION_ELEMENTS:
        return "Application"
    elif element_type in TECHNOLOGY_ELEMENTS:
        return "Technology"
    elif element_type in PHYSICAL_ELEMENTS:
        return "Physical"
    elif element_type in MOTIVATION_ELEMENTS:
        return "Motivation"
    elif element_type in IMPLEMENTATION_ELEMENTS:
        return "Implementation & Migration"
    return None


def get_element_aspect(element_type: str) -> Optional[str]:
    """
    Get the ArchiMate aspect (active/behavior/passive) for an element type.

    Args:
        element_type: The ArchiMate element type

    Returns:
        Aspect name string, or None if not found
    """
    if element_type in ALL_ACTIVE_ELEMENTS:
        return "Active Structure"
    elif element_type in ALL_BEHAVIOR_ELEMENTS:
        return "Behavior"
    elif element_type in ALL_PASSIVE_ELEMENTS:
        return "Passive Structure"
    return None


def validate_cross_layer_relationship(
    source_type: str, target_type: str, relationship_type: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate a relationship considering cross-layer rules.

    Args:
        source_type: The source ArchiMate element type
        target_type: The target ArchiMate element type
        relationship_type: The relationship type

    Returns:
        Tuple of (is_valid, warning_message)
        warning_message is None if valid, contains guidance if potentially problematic
    """
    # First check if relationship is valid at all
    if not is_valid_relationship(source_type, target_type, relationship_type):
        return (
            False,
            f"Relationship '{relationship_type}' is not valid between {source_type} and {target_type}",
        )

    source_layer = get_element_layer(source_type)
    target_layer = get_element_layer(target_type)

    # Same layer is always fine
    if source_layer == target_layer:
        return True, None

    # Cross-layer relationships have specific rules
    # Generally, serving/realization flow "downward" (Business -> Application -> Technology)
    layer_order = [
        "Motivation",
        "Strategy",
        "Business",
        "Application",
        "Technology",
        "Physical",
        "Implementation & Migration",
    ]

    source_idx = layer_order.index(source_layer) if source_layer in layer_order else -1
    target_idx = layer_order.index(target_layer) if target_layer in layer_order else -1

    # Cross-layer serving should flow upward (lower serves higher)
    if relationship_type == "serving" and source_idx < target_idx:
        return (
            True,
            f"Cross-layer serving from {source_layer} to {target_layer} - verify direction is intentional",
        )

    # Cross-layer realization should flow upward (lower realizes higher)
    if relationship_type == "realization" and source_idx > target_idx:
        return (
            True,
            f"Cross-layer realization from {source_layer} to {target_layer} - verify direction is intentional",
        )

    return True, None


def get_relationship_summary() -> Dict[str, int]:
    """
    Get a summary of the relationship matrix.

    Returns:
        Dict with counts of relationships by type
    """
    summary = {rel_type: 0 for rel_type in RELATIONSHIP_TYPES}

    for relationships in VALID_RELATIONSHIPS.values():
        for rel_type in relationships:
            if rel_type in summary:
                summary[rel_type] += 1

    return summary


def get_matrix_statistics() -> Dict[str, any]:
    """
    Get statistics about the relationship matrix.

    Returns:
        Dict with various statistics
    """
    total_rules = len(VALID_RELATIONSHIPS)
    total_relationships = sum(len(rels) for rels in VALID_RELATIONSHIPS.values())

    source_types = set(src for src, _ in VALID_RELATIONSHIPS.keys())
    target_types = set(tgt for _, tgt in VALID_RELATIONSHIPS.keys())

    return {
        "total_rules": total_rules,
        "total_relationships": total_relationships,
        "unique_source_types": len(source_types),
        "unique_target_types": len(target_types),
        "all_element_types": len(ALL_ELEMENTS),
        "relationship_types": len(RELATIONSHIP_TYPES),
        "relationship_summary": get_relationship_summary(),
    }


# =============================================================================
# Validation Classes
# =============================================================================


class RelationshipValidator:
    """
    Comprehensive ArchiMate relationship validator.
    Provides detailed validation with error messages and suggestions.
    """

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate(
        self,
        source_type: str,
        target_type: str,
        relationship_type: str,
        check_cross_layer: bool = True,
    ) -> bool:
        """
        Validate a relationship and collect errors/warnings.

        Args:
            source_type: The source ArchiMate element type
            target_type: The target ArchiMate element type
            relationship_type: The relationship type
            check_cross_layer: Whether to check cross-layer rules

        Returns:
            True if valid, False otherwise
        """
        self.errors = []
        self.warnings = []

        # Check if element types are valid
        if source_type not in ALL_ELEMENTS:
            self.errors.append(f"Unknown source element type: {source_type}")
            return False

        if target_type not in ALL_ELEMENTS:
            self.errors.append(f"Unknown target element type: {target_type}")
            return False

        # Check if relationship type is valid
        if relationship_type.lower() not in RELATIONSHIP_TYPES:
            self.errors.append(f"Unknown relationship type: {relationship_type}")
            return False

        # Check if relationship is valid between these types
        if not is_valid_relationship(source_type, target_type, relationship_type):
            valid_rels = get_valid_relationships(source_type, target_type)
            if valid_rels:
                self.errors.append(
                    f"Relationship '{relationship_type}' is not valid between {source_type} and {target_type}. "
                    f"Valid relationships are: {', '.join(valid_rels)}"
                )
            else:
                self.errors.append(
                    f"No direct relationship is defined between {source_type} and {target_type} "
                    f"in the ArchiMate 3.2 specification"
                )
            return False

        # Check cross-layer rules
        if check_cross_layer:
            is_valid, warning = validate_cross_layer_relationship(
                source_type, target_type, relationship_type
            )
            if warning:
                self.warnings.append(warning)

        return True

    def get_suggestions(self, source_type: str, target_type: str) -> List[str]:
        """
        Get suggested valid relationships between two element types.

        Args:
            source_type: The source ArchiMate element type
            target_type: The target ArchiMate element type

        Returns:
            List of valid relationship type suggestions
        """
        return get_valid_relationships(source_type, target_type)

    def get_errors(self) -> List[str]:
        """Get the list of validation errors."""
        return self.errors

    def get_warnings(self) -> List[str]:
        """Get the list of validation warnings."""
        return self.warnings


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    # Constants
    "RELATIONSHIP_TYPES",
    "RELATIONSHIP_TYPE_DEFINITIONS",
    "VALID_RELATIONSHIPS",
    "RELATIONSHIP_CARDINALITY",
    "DERIVATION_STRENGTH_ORDER",
    "DERIVABLE_RELATIONSHIPS",
    # Element type lists
    "STRATEGY_ELEMENTS",
    "BUSINESS_ELEMENTS",
    "BUSINESS_ACTIVE_ELEMENTS",
    "BUSINESS_BEHAVIOR_ELEMENTS",
    "BUSINESS_PASSIVE_ELEMENTS",
    "APPLICATION_ELEMENTS",
    "APPLICATION_ACTIVE_ELEMENTS",
    "APPLICATION_BEHAVIOR_ELEMENTS",
    "APPLICATION_PASSIVE_ELEMENTS",
    "TECHNOLOGY_ELEMENTS",
    "TECHNOLOGY_ACTIVE_ELEMENTS",
    "TECHNOLOGY_BEHAVIOR_ELEMENTS",
    "TECHNOLOGY_PASSIVE_ELEMENTS",
    "PHYSICAL_ELEMENTS",
    "PHYSICAL_ACTIVE_ELEMENTS",
    "PHYSICAL_PASSIVE_ELEMENTS",
    "MOTIVATION_ELEMENTS",
    "MOTIVATION_ACTIVE_ELEMENTS",
    "MOTIVATION_PASSIVE_ELEMENTS",
    "IMPLEMENTATION_ELEMENTS",
    "IMPLEMENTATION_BEHAVIOR_ELEMENTS",
    "IMPLEMENTATION_PASSIVE_ELEMENTS",
    "OTHER_ELEMENTS",
    "CONNECTOR_ELEMENTS",
    "LAYERED_ELEMENTS",
    "ALL_ELEMENTS",
    "LAYER_NAME_TO_KEY",
    "ALL_ACTIVE_ELEMENTS",
    "ALL_BEHAVIOR_ELEMENTS",
    "ALL_PASSIVE_ELEMENTS",
    # Data classes and enums
    "RelationshipRule",
    "RelationshipTypeDefinition",
    "RelationshipCategory",
    "AccessMode",
    # Functions
    "normalize_element_type",
    "get_valid_relationships",
    "is_valid_relationship",
    "get_cardinality",
    "get_relationship_category",
    "get_all_valid_sources_for_target",
    "get_all_valid_targets_for_source",
    "get_derivation_strength",
    "derive_relationship",
    "can_derive_relationship",
    "get_element_layer",
    "get_element_aspect",
    "validate_cross_layer_relationship",
    "get_relationship_summary",
    "get_matrix_statistics",
    # Classes
    "RelationshipValidator",
]

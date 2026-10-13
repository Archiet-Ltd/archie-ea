"""
ArchiMate 3.2 Element Type Taxonomy

Complete definition of all ArchiMate 3.2 element types organized by layer.
Provides comprehensive metadata for element classification, validation,
and intelligent mapping.

Reference: The Open Group ArchiMate 3.2 Specification
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set

from app.models.constants import ArchiMateLayer

# =============================================================================
# Element Type Data Classes
# =============================================================================


@dataclass
class ElementTypeDefinition:
    """Definition of an ArchiMate element type."""

    name: str  # Element type name (e.g., 'BusinessProcess')
    layer: str  # ArchiMate layer
    aspect: str  # Active/Passive/Behavior
    description: str  # Human-readable description
    notation_symbol: str  # ArchiMate notation symbol description
    example_keywords: List[str]  # Keywords for AI matching
    related_apqc: List[str] = None  # Related APQC categories (for Business layer)
    parent_type: str = None  # For specializations


@dataclass
class RelationshipPattern:
    """
    Definition of an ArchiMate relationship pattern.

    Represents a typical relationship between element types that can be
    derived from APQC process categories. Used for intelligent relationship
    suggestion and validation.

    Attributes:
        source_type: The ArchiMate element type that is the source of the relationship
        relationship_type: The ArchiMate 3.2 relationship type
        target_type: The ArchiMate element type that is the target of the relationship
        description: Human-readable description of when this pattern applies
        bidirectional: Whether the relationship can be read in both directions
    """

    source_type: str
    relationship_type: str  # ArchiMate 3.2: composition, aggregation, assignment,
    # realization, serving, access, influence, triggering,
    # flow, specialization, association
    target_type: str
    description: str
    bidirectional: bool = False


# =============================================================================
# ArchiMate 3.2 Element Type Taxonomy
# =============================================================================


class ArchiMateElementTypes:
    """
    Complete ArchiMate 3.2 element type taxonomy by layer.

    Organized according to the ArchiMate metamodel with:
    - Strategy Layer elements
    - Business Layer elements
    - Application Layer elements
    - Technology Layer elements
    - Physical Layer elements
    - Motivation Layer elements
    - Implementation & Migration Layer elements
    """

    # =========================================================================
    # STRATEGY LAYER ELEMENTS
    # =========================================================================
    STRATEGY_ELEMENTS = {
        "Resource": ElementTypeDefinition(
            name="Resource",
            layer="strategy",
            aspect="passive",
            description="An asset owned or controlled by an individual or organization",
            notation_symbol="Rectangle with folded corner",
            example_keywords=[
                "resource",
                "asset",
                "capital",
                "investment",
                "fund",
                "budget",
                "workforce",
                "talent",
                "expertise",
                "skill",
            ],
        ),
        "Capability": ElementTypeDefinition(
            name="Capability",
            layer="strategy",
            aspect="behavior",
            description="An ability that an active structure element possesses",
            notation_symbol="Rounded rectangle with horizontal line",
            example_keywords=[
                "capability",
                "ability",
                "competency",
                "capacity",
                "skill set",
                "core competency",
                "organizational capability",
            ],
        ),
        "CourseOfAction": ElementTypeDefinition(
            name="CourseOfAction",
            layer="strategy",
            aspect="behavior",
            description="An approach or plan for configuring capabilities and resources",
            notation_symbol="Rounded rectangle with chevron",
            example_keywords=[
                "course of action",
                "strategy",
                "initiative",
                "program",
                "roadmap",
                "plan",
                "approach",
                "strategic plan",
            ],
        ),
        "ValueStream": ElementTypeDefinition(
            name="ValueStream",
            layer="strategy",
            aspect="behavior",
            description="A sequence of activities that creates an overall result for a customer",
            notation_symbol="Arrow-shaped rectangle",
            example_keywords=[
                "value stream",
                "value chain",
                "end-to-end process",
                "customer journey",
                "value delivery",
                "value creation",
            ],
        ),
    }

    # =========================================================================
    # BUSINESS LAYER ELEMENTS
    # =========================================================================
    BUSINESS_ELEMENTS = {
        # Active Structure Elements
        "BusinessActor": ElementTypeDefinition(
            name="BusinessActor",
            layer="business",
            aspect="active",
            description="A business entity that is capable of performing behavior",
            notation_symbol="Stick figure",
            example_keywords=[
                "actor",
                "person",
                "employee",
                "customer",
                "partner",
                "stakeholder",
                "user",
                "staff",
                "team member",
            ],
            related_apqc=["6.0"],
        ),
        "BusinessRole": ElementTypeDefinition(
            name="BusinessRole",
            layer="business",
            aspect="active",
            description="The responsibility for performing specific behavior",
            notation_symbol="Yellow rectangle with vertical lines",
            example_keywords=[
                "role",
                "responsibility",
                "position",
                "job",
                "function",
                "manager",
                "analyst",
                "specialist",
                "coordinator",
            ],
            related_apqc=["6.0"],
        ),
        "BusinessCollaboration": ElementTypeDefinition(
            name="BusinessCollaboration",
            layer="business",
            aspect="active",
            description="An aggregate of two or more business internal active structure elements",
            notation_symbol="Yellow rectangle with two interlocking gears",
            example_keywords=[
                "collaboration",
                "team",
                "committee",
                "council",
                "working group",
                "task force",
                "partnership",
            ],
        ),
        "BusinessInterface": ElementTypeDefinition(
            name="BusinessInterface",
            layer="business",
            aspect="active",
            description="A point of access where a business service is made available",
            notation_symbol="Yellow rectangle with socket",
            example_keywords=[
                "interface",
                "channel",
                "touchpoint",
                "portal",
                "counter",
                "helpdesk",
                "service desk",
                "contact point",
            ],
            related_apqc=["5.0"],
        ),
        # Behavior Elements
        "BusinessProcess": ElementTypeDefinition(
            name="BusinessProcess",
            layer="business",
            aspect="behavior",
            description="A sequence of business behaviors that achieves a specific result",
            notation_symbol="Yellow rounded rectangle with arrow",
            example_keywords=[
                "process",
                "workflow",
                "procedure",
                "operation",
                "activity",
                "task",
                "step",
                "routine",
            ],
            related_apqc=["1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0"],
        ),
        "BusinessFunction": ElementTypeDefinition(
            name="BusinessFunction",
            layer="business",
            aspect="behavior",
            description="A collection of business behavior based on required business resources",
            notation_symbol="Yellow rounded rectangle",
            example_keywords=[
                "function",
                "department",
                "unit",
                "division",
                "business area",
                "functional area",
                "capability area",
            ],
            related_apqc=["1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0"],
        ),
        "BusinessInteraction": ElementTypeDefinition(
            name="BusinessInteraction",
            layer="business",
            aspect="behavior",
            description="A unit of collective business behavior performed by collaboration",
            notation_symbol="Yellow rounded rectangle with interlocking gears",
            example_keywords=[
                "interaction",
                "meeting",
                "negotiation",
                "transaction",
                "exchange",
                "conversation",
                "communication",
            ],
        ),
        "BusinessEvent": ElementTypeDefinition(
            name="BusinessEvent",
            layer="business",
            aspect="behavior",
            description="An organizational state change that triggers or is triggered by behavior",
            notation_symbol="Yellow rounded rectangle with lightning bolt",
            example_keywords=[
                "event",
                "trigger",
                "milestone",
                "occurrence",
                "notification",
                "alert",
                "deadline",
                "incident",
            ],
        ),
        "BusinessService": ElementTypeDefinition(
            name="BusinessService",
            layer="business",
            aspect="behavior",
            description="An explicitly defined exposed business behavior",
            notation_symbol="Yellow rounded rectangle with small rectangle",
            example_keywords=[
                "service",
                "offering",
                "solution",
                "business service",
                "customer service",
                "delivery",
                "support service",
            ],
            related_apqc=["3.0", "5.0"],
        ),
        # Passive Structure Elements
        "BusinessObject": ElementTypeDefinition(
            name="BusinessObject",
            layer="business",
            aspect="passive",
            description="A concept used within a particular business domain",
            notation_symbol="Yellow rectangle",
            example_keywords=[
                "object",
                "entity",
                "document",
                "record",
                "artifact",
                "item",
                "asset",
                "information",
            ],
        ),
        "Contract": ElementTypeDefinition(
            name="Contract",
            layer="business",
            aspect="passive",
            description="A formal or informal specification of an agreement",
            notation_symbol="Yellow rectangle with scroll",
            example_keywords=[
                "contract",
                "agreement",
                "sla",
                "mou",
                "terms",
                "policy",
                "regulation",
                "standard",
            ],
            related_apqc=["10.0"],
        ),
        "Representation": ElementTypeDefinition(
            name="Representation",
            layer="business",
            aspect="passive",
            description="A perceptible form of the information carried by a business object",
            notation_symbol="Yellow rectangle with folded corner",
            example_keywords=[
                "representation",
                "form",
                "document",
                "report",
                "template",
                "format",
                "view",
                "visualization",
            ],
        ),
        "Product": ElementTypeDefinition(
            name="Product",
            layer="business",
            aspect="passive",
            description="A coherent collection of services and/or passive structure elements",
            notation_symbol="Yellow rectangle with box",
            example_keywords=[
                "product",
                "offering",
                "package",
                "bundle",
                "solution",
                "service package",
                "portfolio item",
            ],
            related_apqc=["2.0", "3.0"],
        ),
    }

    # =========================================================================
    # APPLICATION LAYER ELEMENTS
    # =========================================================================
    APPLICATION_ELEMENTS = {
        # Active Structure Elements
        "ApplicationComponent": ElementTypeDefinition(
            name="ApplicationComponent",
            layer="application",
            aspect="active",
            description="An encapsulation of application functionality",
            notation_symbol="Blue rectangle with component symbol",
            example_keywords=[
                "application",
                "system",
                "software",
                "module",
                "component",
                "app",
                "tool",
                "platform",
            ],
            related_apqc=["7.0"],
        ),
        "ApplicationCollaboration": ElementTypeDefinition(
            name="ApplicationCollaboration",
            layer="application",
            aspect="active",
            description="An aggregate of two or more application components",
            notation_symbol="Blue rectangle with gears",
            example_keywords=[
                "integration",
                "collaboration",
                "ecosystem",
                "system landscape",
                "application suite",
            ],
        ),
        "ApplicationInterface": ElementTypeDefinition(
            name="ApplicationInterface",
            layer="application",
            aspect="active",
            description="A point of access where application services are made available",
            notation_symbol="Blue rectangle with socket",
            example_keywords=[
                "api",
                "interface",
                "endpoint",
                "web service",
                "rest api",
                "soap",
                "graphql",
                "connector",
            ],
            related_apqc=["7.0"],
        ),
        # Behavior Elements
        "ApplicationFunction": ElementTypeDefinition(
            name="ApplicationFunction",
            layer="application",
            aspect="behavior",
            description="Automated behavior that can be performed by an application component",
            notation_symbol="Blue rounded rectangle",
            example_keywords=[
                "function",
                "feature",
                "capability",
                "operation",
                "automation",
                "processing",
                "calculation",
                "algorithm",
            ],
        ),
        "ApplicationProcess": ElementTypeDefinition(
            name="ApplicationProcess",
            layer="application",
            aspect="behavior",
            description="A sequence of application behaviors that achieves a specific result",
            notation_symbol="Blue rounded rectangle with arrow",
            example_keywords=[
                "process",
                "workflow",
                "job",
                "batch",
                "pipeline",
                "etl",
                "automation process",
            ],
        ),
        "ApplicationInteraction": ElementTypeDefinition(
            name="ApplicationInteraction",
            layer="application",
            aspect="behavior",
            description="A unit of collective application behavior",
            notation_symbol="Blue rounded rectangle with gears",
            example_keywords=[
                "interaction",
                "transaction",
                "message exchange",
                "request-response",
                "synchronization",
            ],
        ),
        "ApplicationEvent": ElementTypeDefinition(
            name="ApplicationEvent",
            layer="application",
            aspect="behavior",
            description="An application state change that triggers behavior",
            notation_symbol="Blue rounded rectangle with lightning",
            example_keywords=[
                "event",
                "trigger",
                "webhook",
                "notification",
                "message",
                "signal",
                "callback",
                "alert",
            ],
        ),
        "ApplicationService": ElementTypeDefinition(
            name="ApplicationService",
            layer="application",
            aspect="behavior",
            description="An explicitly defined exposed application behavior",
            notation_symbol="Blue rounded rectangle with small rectangle",
            example_keywords=[
                "service",
                "api",
                "web service",
                "microservice",
                "saas",
                "cloud service",
                "endpoint",
                "operation",
            ],
            related_apqc=["7.0"],
        ),
        # Passive Structure Elements
        "DataObject": ElementTypeDefinition(
            name="DataObject",
            layer="application",
            aspect="passive",
            description="Data structured for automated processing",
            notation_symbol="Blue rectangle",
            example_keywords=[
                "data",
                "entity",
                "table",
                "record",
                "object",
                "model",
                "schema",
                "dataset",
            ],
        ),
    }

    # =========================================================================
    # TECHNOLOGY LAYER ELEMENTS
    # =========================================================================
    TECHNOLOGY_ELEMENTS = {
        # Active Structure Elements
        "Node": ElementTypeDefinition(
            name="Node",
            layer="technology",
            aspect="active",
            description="A computational or physical resource that hosts or manipulates artifacts",
            notation_symbol="Green 3D box",
            example_keywords=[
                "node",
                "server",
                "host",
                "machine",
                "instance",
                "container",
                "vm",
                "cluster",
            ],
            related_apqc=["7.0"],
        ),
        "Device": ElementTypeDefinition(
            name="Device",
            layer="technology",
            aspect="active",
            description="A physical IT resource upon which artifacts may be deployed",
            notation_symbol="Green 3D box with device icon",
            example_keywords=[
                "device",
                "hardware",
                "server",
                "workstation",
                "laptop",
                "mobile",
                "iot",
                "sensor",
                "appliance",
            ],
        ),
        "SystemSoftware": ElementTypeDefinition(
            name="SystemSoftware",
            layer="technology",
            aspect="active",
            description="Software that provides or contributes to an environment",
            notation_symbol="Green rectangle with system icon",
            example_keywords=[
                "os",
                "operating system",
                "middleware",
                "runtime",
                "database",
                "web server",
                "application server",
                "container runtime",
            ],
        ),
        "TechnologyCollaboration": ElementTypeDefinition(
            name="TechnologyCollaboration",
            layer="technology",
            aspect="active",
            description="An aggregate of two or more technology nodes",
            notation_symbol="Green rectangle with gears",
            example_keywords=[
                "cluster",
                "farm",
                "grid",
                "distributed system",
                "technology stack",
                "infrastructure",
            ],
        ),
        "TechnologyInterface": ElementTypeDefinition(
            name="TechnologyInterface",
            layer="technology",
            aspect="active",
            description="A point of access where technology services are made available",
            notation_symbol="Green rectangle with socket",
            example_keywords=[
                "port",
                "protocol",
                "endpoint",
                "connection",
                "tcp",
                "http",
                "ssh",
                "network interface",
            ],
        ),
        "Path": ElementTypeDefinition(
            name="Path",
            layer="technology",
            aspect="active",
            description="A link between two or more nodes through which information is exchanged",
            notation_symbol="Green line",
            example_keywords=[
                "path",
                "link",
                "connection",
                "channel",
                "network link",
                "fiber",
                "cable",
                "tunnel",
            ],
        ),
        "CommunicationNetwork": ElementTypeDefinition(
            name="CommunicationNetwork",
            layer="technology",
            aspect="active",
            description="A set of structures that connects nodes for transmission and routing",
            notation_symbol="Green rectangle with network icon",
            example_keywords=[
                "network",
                "lan",
                "wan",
                "vpn",
                "internet",
                "intranet",
                "cloud network",
                "vnet",
            ],
        ),
        # Behavior Elements
        "TechnologyFunction": ElementTypeDefinition(
            name="TechnologyFunction",
            layer="technology",
            aspect="behavior",
            description="A collection of technology behavior that can be performed by a node",
            notation_symbol="Green rounded rectangle",
            example_keywords=[
                "function",
                "service",
                "daemon",
                "process",
                "routine",
                "job",
                "scheduled task",
            ],
        ),
        "TechnologyProcess": ElementTypeDefinition(
            name="TechnologyProcess",
            layer="technology",
            aspect="behavior",
            description="A sequence of technology behaviors that achieves a result",
            notation_symbol="Green rounded rectangle with arrow",
            example_keywords=[
                "process",
                "batch",
                "script",
                "automation",
                "pipeline",
                "deployment",
                "provisioning",
            ],
        ),
        "TechnologyInteraction": ElementTypeDefinition(
            name="TechnologyInteraction",
            layer="technology",
            aspect="behavior",
            description="A unit of collective technology behavior",
            notation_symbol="Green rounded rectangle with gears",
            example_keywords=[
                "interaction",
                "handshake",
                "protocol exchange",
                "synchronization",
                "replication",
            ],
        ),
        "TechnologyEvent": ElementTypeDefinition(
            name="TechnologyEvent",
            layer="technology",
            aspect="behavior",
            description="A technology state change that triggers behavior",
            notation_symbol="Green rounded rectangle with lightning",
            example_keywords=[
                "event",
                "alert",
                "trigger",
                "interrupt",
                "signal",
                "log event",
                "monitoring alert",
            ],
        ),
        "TechnologyService": ElementTypeDefinition(
            name="TechnologyService",
            layer="technology",
            aspect="behavior",
            description="An explicitly defined exposed technology behavior",
            notation_symbol="Green rounded rectangle with small rectangle",
            example_keywords=[
                "service",
                "infrastructure service",
                "cloud service",
                "iaas",
                "paas",
                "storage service",
                "compute service",
            ],
            related_apqc=["7.0"],
        ),
        # Passive Structure Elements
        "Artifact": ElementTypeDefinition(
            name="Artifact",
            layer="technology",
            aspect="passive",
            description="A piece of data that is used or produced in a software development process",
            notation_symbol="Green rectangle",
            example_keywords=[
                "artifact",
                "file",
                "binary",
                "package",
                "image",
                "config",
                "script",
                "deployment package",
            ],
        ),
    }

    # =========================================================================
    # PHYSICAL LAYER ELEMENTS
    # =========================================================================
    PHYSICAL_ELEMENTS = {
        "Equipment": ElementTypeDefinition(
            name="Equipment",
            layer="physical",
            aspect="active",
            description="Physical machines, tools, or instruments",
            notation_symbol="Yellow-green rectangle with equipment icon",
            example_keywords=[
                "equipment",
                "machine",
                "tool",
                "instrument",
                "device",
                "robot",
                "sensor",
                "plc",
            ],
            related_apqc=["5.0", "9.0"],
        ),
        "Facility": ElementTypeDefinition(
            name="Facility",
            layer="physical",
            aspect="active",
            description="A physical structure or environment",
            notation_symbol="Yellow-green rectangle with building",
            example_keywords=[
                "facility",
                "building",
                "data center",
                "warehouse",
                "office",
                "plant",
                "factory",
                "site",
            ],
            related_apqc=["9.0"],
        ),
        "DistributionNetwork": ElementTypeDefinition(
            name="DistributionNetwork",
            layer="physical",
            aspect="active",
            description="A physical network for transport of materials or energy",
            notation_symbol="Yellow-green line with distribution icon",
            example_keywords=[
                "distribution",
                "logistics network",
                "supply chain",
                "pipeline",
                "grid",
                "shipping network",
            ],
            related_apqc=["4.0"],
        ),
        "Material": ElementTypeDefinition(
            name="Material",
            layer="physical",
            aspect="passive",
            description="Tangible physical matter or energy",
            notation_symbol="Yellow-green rectangle",
            example_keywords=[
                "material",
                "raw material",
                "component",
                "product",
                "inventory",
                "goods",
                "parts",
                "supplies",
            ],
            related_apqc=["4.0", "5.0"],
        ),
    }

    # =========================================================================
    # MOTIVATION LAYER ELEMENTS
    # =========================================================================
    MOTIVATION_ELEMENTS = {
        "Stakeholder": ElementTypeDefinition(
            name="Stakeholder",
            layer="motivation",
            aspect="active",
            description="The role of an individual, team, or organization with interest",
            notation_symbol="Purple stick figure",
            example_keywords=[
                "stakeholder",
                "sponsor",
                "owner",
                "executive",
                "decision maker",
                "influencer",
                "board",
            ],
            related_apqc=["11.0"],
        ),
        "Driver": ElementTypeDefinition(
            name="Driver",
            layer="motivation",
            aspect="passive",
            description="An external or internal condition that motivates the organization",
            notation_symbol="Purple oval",
            example_keywords=[
                "driver",
                "trend",
                "pressure",
                "force",
                "regulation",
                "market condition",
                "competitive pressure",
            ],
        ),
        "Assessment": ElementTypeDefinition(
            name="Assessment",
            layer="motivation",
            aspect="passive",
            description="The result of an analysis of the state of affairs",
            notation_symbol="Purple oval with assessment icon",
            example_keywords=[
                "assessment",
                "analysis",
                "evaluation",
                "review",
                "audit finding",
                "gap",
                "opportunity",
                "risk assessment",
            ],
            related_apqc=["10.0"],
        ),
        "Goal": ElementTypeDefinition(
            name="Goal",
            layer="motivation",
            aspect="passive",
            description="A high-level statement of intent, direction, or desired end state",
            notation_symbol="Purple oval with filled top",
            example_keywords=[
                "goal",
                "objective",
                "target",
                "aim",
                "mission",
                "vision",
                "strategic objective",
            ],
            related_apqc=["1.0"],
        ),
        "Outcome": ElementTypeDefinition(
            name="Outcome",
            layer="motivation",
            aspect="passive",
            description="An end result that has been achieved",
            notation_symbol="Purple oval with bottom filled",
            example_keywords=[
                "outcome",
                "result",
                "achievement",
                "benefit",
                "kpi",
                "metric",
                "success measure",
            ],
        ),
        "Principle": ElementTypeDefinition(
            name="Principle",
            layer="motivation",
            aspect="passive",
            description="A qualitative statement of intent that should be met",
            notation_symbol="Purple rectangle with triangle",
            example_keywords=[
                "principle",
                "guideline",
                "standard",
                "best practice",
                "policy",
                "rule",
                "norm",
            ],
            related_apqc=["9.0"],
        ),
        "Requirement": ElementTypeDefinition(
            name="Requirement",
            layer="motivation",
            aspect="passive",
            description="A statement of need that must be met by the architecture",
            notation_symbol="Purple rectangle",
            example_keywords=[
                "requirement",
                "need",
                "demand",
                "specification",
                "constraint",
                "criteria",
                "condition",
            ],
        ),
        "Constraint": ElementTypeDefinition(
            name="Constraint",
            layer="motivation",
            aspect="passive",
            description="A factor that limits the realization of goals",
            notation_symbol="Purple rectangle with vertical line",
            example_keywords=[
                "constraint",
                "limitation",
                "restriction",
                "boundary",
                "regulation",
                "compliance requirement",
                "budget",
            ],
            related_apqc=["10.0"],
        ),
        "Meaning": ElementTypeDefinition(
            name="Meaning",
            layer="motivation",
            aspect="passive",
            description="The knowledge or expertise present in a business object",
            notation_symbol="Purple cloud",
            example_keywords=[
                "meaning",
                "definition",
                "semantics",
                "interpretation",
                "context",
                "understanding",
            ],
            related_apqc=["12.0"],
        ),
        "Value": ElementTypeDefinition(
            name="Value",
            layer="motivation",
            aspect="passive",
            description="The relative worth, utility, or importance",
            notation_symbol="Purple oval with dollar sign",
            example_keywords=[
                "value",
                "benefit",
                "worth",
                "roi",
                "business value",
                "customer value",
                "stakeholder value",
            ],
        ),
    }

    # =========================================================================
    # IMPLEMENTATION & MIGRATION LAYER ELEMENTS
    # =========================================================================
    IMPLEMENTATION_ELEMENTS = {
        "WorkPackage": ElementTypeDefinition(
            name="WorkPackage",
            layer=ArchiMateLayer.IMPLEMENTATION,
            aspect="behavior",
            description="A series of actions designed to achieve specific results",
            notation_symbol="Pink rounded rectangle with arrow",
            example_keywords=[
                "work package",
                "project",
                "initiative",
                "task",
                "sprint",
                "release",
                "deployment",
                "implementation",
            ],
        ),
        "Deliverable": ElementTypeDefinition(
            name="Deliverable",
            layer=ArchiMateLayer.IMPLEMENTATION,
            aspect="passive",
            description="A precisely-defined outcome of a work package",
            notation_symbol="Pink rectangle",
            example_keywords=[
                "deliverable",
                "artifact",
                "output",
                "milestone",
                "documentation",
                "release",
                "version",
            ],
        ),
        "ImplementationEvent": ElementTypeDefinition(
            name="ImplementationEvent",
            layer=ArchiMateLayer.IMPLEMENTATION,
            aspect="behavior",
            description="A state change that marks a transition or a release",
            notation_symbol="Pink rounded rectangle with lightning",
            example_keywords=[
                "event",
                "milestone",
                "go-live",
                "cutover",
                "release date",
                "deadline",
                "phase completion",
            ],
        ),
        "Plateau": ElementTypeDefinition(
            name="Plateau",
            layer=ArchiMateLayer.IMPLEMENTATION,
            aspect="passive",
            description="A relatively stable state of the architecture that exists during a period",
            notation_symbol="Pink rectangle with horizontal lines",
            example_keywords=[
                "plateau",
                "state",
                "baseline",
                "target state",
                "current state",
                "future state",
                "architecture version",
            ],
        ),
        "Gap": ElementTypeDefinition(
            name="Gap",
            layer=ArchiMateLayer.IMPLEMENTATION,
            aspect="passive",
            description="A statement of difference between two plateaus",
            notation_symbol="Pink rectangle with gap indicator",
            example_keywords=[
                "gap",
                "difference",
                "delta",
                "variance",
                "discrepancy",
                "shortfall",
                "deficiency",
            ],
        ),
    }

    # =========================================================================
    # APQC TO ARCHIMATE RELATIONSHIP PATTERN TEMPLATES
    # =========================================================================

    # ArchiMate 3.2 Valid Relationship Types
    VALID_RELATIONSHIP_TYPES = {
        "composition",  # Whole-part structural relationship
        "aggregation",  # Grouping structural relationship
        "assignment",  # Allocation of responsibility
        "realization",  # Implementation relationship
        "serving",  # Provides functionality to
        "access",  # Read/Write to passive element
        "influence",  # Effect on motivation element
        "triggering",  # Temporal/causal dependency
        "flow",  # Transfer of content
        "specialization",  # More specific form of
        "association",  # Unspecified relationship
    }

    # Relationship patterns organized by APQC category
    # Each category has 3 - 5 typical relationship patterns for ArchiMate derivation
    APQC_RELATIONSHIP_TEMPLATES: Dict[str, List["RelationshipPattern"]] = {}

    @classmethod
    def _initialize_relationship_templates(cls):
        """Initialize the APQC relationship templates. Called once at module load."""
        cls.APQC_RELATIONSHIP_TEMPLATES = {
            # -----------------------------------------------------------------
            # 1.0 - Develop Vision and Strategy
            # -----------------------------------------------------------------
            "1.0": [
                RelationshipPattern(
                    source_type="Goal",
                    relationship_type="influence",
                    target_type="Capability",
                    description="Strategic goals influence which capabilities are prioritized",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Capability",
                    relationship_type="realization",
                    target_type="CourseOfAction",
                    description="Capabilities realize strategic courses of action",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Driver",
                    relationship_type="influence",
                    target_type="Goal",
                    description="Business drivers influence strategic goals",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Stakeholder",
                    relationship_type="association",
                    target_type="Goal",
                    description="Stakeholders are associated with strategic goals",
                    bidirectional=True,
                ),
                RelationshipPattern(
                    source_type="ValueStream",
                    relationship_type="realization",
                    target_type="Outcome",
                    description="Value streams realize business outcomes",
                    bidirectional=False,
                ),
            ],
            # -----------------------------------------------------------------
            # 2.0 - Develop and Manage Products and Services
            # -----------------------------------------------------------------
            "2.0": [
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="realization",
                    target_type="Product",
                    description="Business processes realize product development",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessService",
                    relationship_type="composition",
                    target_type="Product",
                    description="Products are composed of business services",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessRole",
                    relationship_type="assignment",
                    target_type="BusinessProcess",
                    description="Roles are assigned to product development processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Requirement",
                    relationship_type="realization",
                    target_type="Product",
                    description="Products realize customer requirements",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="ApplicationComponent",
                    relationship_type="serving",
                    target_type="BusinessProcess",
                    description="Applications serve product management processes",
                    bidirectional=False,
                ),
            ],
            # -----------------------------------------------------------------
            # 3.0 - Market and Sell Products and Services
            # -----------------------------------------------------------------
            "3.0": [
                RelationshipPattern(
                    source_type="BusinessService",
                    relationship_type="serving",
                    target_type="BusinessActor",
                    description="Sales services serve customer actors",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="triggering",
                    target_type="BusinessEvent",
                    description="Sales processes trigger customer events",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessInterface",
                    relationship_type="serving",
                    target_type="BusinessActor",
                    description="Sales channels serve customers",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="ApplicationComponent",
                    relationship_type="serving",
                    target_type="BusinessProcess",
                    description="CRM applications serve sales processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Contract",
                    relationship_type="association",
                    target_type="Product",
                    description="Contracts are associated with products sold",
                    bidirectional=True,
                ),
            ],
            # -----------------------------------------------------------------
            # 4.0 - Deliver Physical Products (Supply Chain)
            # -----------------------------------------------------------------
            "4.0": [
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="triggering",
                    target_type="BusinessProcess",
                    description="Supply chain processes trigger subsequent processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="access",
                    target_type="Material",
                    description="Logistics processes access physical materials",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="DistributionNetwork",
                    relationship_type="realization",
                    target_type="BusinessService",
                    description="Distribution networks realize delivery services",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Facility",
                    relationship_type="assignment",
                    target_type="BusinessProcess",
                    description="Facilities are assigned to warehousing processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="flow",
                    target_type="Material",
                    description="Supply chain processes flow materials through stages",
                    bidirectional=False,
                ),
            ],
            # -----------------------------------------------------------------
            # 5.0 - Deliver Services
            # -----------------------------------------------------------------
            "5.0": [
                RelationshipPattern(
                    source_type="BusinessService",
                    relationship_type="serving",
                    target_type="BusinessActor",
                    description="Services are delivered to customer actors",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessRole",
                    relationship_type="assignment",
                    target_type="BusinessService",
                    description="Service roles are assigned to deliver services",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessInterface",
                    relationship_type="realization",
                    target_type="BusinessService",
                    description="Service interfaces realize service delivery",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="realization",
                    target_type="BusinessService",
                    description="Service processes realize business services",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Contract",
                    relationship_type="association",
                    target_type="BusinessService",
                    description="SLAs are associated with services delivered",
                    bidirectional=True,
                ),
            ],
            # -----------------------------------------------------------------
            # 6.0 - Manage Customer Service
            # -----------------------------------------------------------------
            "6.0": [
                RelationshipPattern(
                    source_type="BusinessRole",
                    relationship_type="assignment",
                    target_type="BusinessProcess",
                    description="Customer service roles are assigned to support processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessActor",
                    relationship_type="triggering",
                    target_type="BusinessEvent",
                    description="Customer actors trigger service requests",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessInterface",
                    relationship_type="serving",
                    target_type="BusinessActor",
                    description="Service channels serve customer interactions",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="ApplicationComponent",
                    relationship_type="serving",
                    target_type="BusinessProcess",
                    description="Helpdesk applications serve customer service processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="access",
                    target_type="BusinessObject",
                    description="Service processes access customer records",
                    bidirectional=False,
                ),
            ],
            # -----------------------------------------------------------------
            # 7.0 - Develop and Manage IT
            # -----------------------------------------------------------------
            "7.0": [
                RelationshipPattern(
                    source_type="ApplicationComponent",
                    relationship_type="serving",
                    target_type="BusinessProcess",
                    description="Applications serve business processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Node",
                    relationship_type="realization",
                    target_type="ApplicationComponent",
                    description="Infrastructure nodes realize application components",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="ApplicationService",
                    relationship_type="serving",
                    target_type="ApplicationComponent",
                    description="Application services serve other components",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="ApplicationInterface",
                    relationship_type="composition",
                    target_type="ApplicationComponent",
                    description="APIs are part of application components",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="TechnologyService",
                    relationship_type="serving",
                    target_type="ApplicationComponent",
                    description="Technology services serve application components",
                    bidirectional=False,
                ),
            ],
            # -----------------------------------------------------------------
            # 8.0 - Manage Enterprise Information
            # -----------------------------------------------------------------
            "8.0": [
                RelationshipPattern(
                    source_type="DataObject",
                    relationship_type="realization",
                    target_type="BusinessObject",
                    description="Data objects realize business objects",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="ApplicationComponent",
                    relationship_type="access",
                    target_type="DataObject",
                    description="Applications access data objects",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="access",
                    target_type="BusinessObject",
                    description="Information processes access business objects",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Artifact",
                    relationship_type="realization",
                    target_type="DataObject",
                    description="Physical artifacts realize data objects",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Meaning",
                    relationship_type="association",
                    target_type="BusinessObject",
                    description="Semantic meaning is associated with business objects",
                    bidirectional=True,
                ),
            ],
            # -----------------------------------------------------------------
            # 9.0 - Manage Financial Resources
            # -----------------------------------------------------------------
            "9.0": [
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="access",
                    target_type="BusinessObject",
                    description="Financial processes access financial records",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessRole",
                    relationship_type="assignment",
                    target_type="BusinessProcess",
                    description="Financial roles are assigned to accounting processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Principle",
                    relationship_type="influence",
                    target_type="BusinessProcess",
                    description="Financial principles influence financial processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="ApplicationComponent",
                    relationship_type="serving",
                    target_type="BusinessProcess",
                    description="ERP/Finance applications serve financial processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Contract",
                    relationship_type="association",
                    target_type="BusinessObject",
                    description="Financial contracts are associated with financial records",
                    bidirectional=True,
                ),
            ],
            # -----------------------------------------------------------------
            # 10.0 - Acquire, Construct, and Manage Assets
            # -----------------------------------------------------------------
            "10.0": [
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="access",
                    target_type="Resource",
                    description="Asset processes access organizational resources",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Facility",
                    relationship_type="aggregation",
                    target_type="Equipment",
                    description="Facilities aggregate equipment assets",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Constraint",
                    relationship_type="influence",
                    target_type="BusinessProcess",
                    description="Regulatory constraints influence asset management",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Contract",
                    relationship_type="association",
                    target_type="Resource",
                    description="Asset contracts are associated with resources",
                    bidirectional=True,
                ),
                RelationshipPattern(
                    source_type="Assessment",
                    relationship_type="influence",
                    target_type="BusinessProcess",
                    description="Asset assessments influence maintenance processes",
                    bidirectional=False,
                ),
            ],
            # -----------------------------------------------------------------
            # 11.0 - Manage Enterprise Risk, Compliance, Remediation, Resiliency
            # -----------------------------------------------------------------
            "11.0": [
                RelationshipPattern(
                    source_type="Assessment",
                    relationship_type="influence",
                    target_type="Requirement",
                    description="Risk assessments influence compliance requirements",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Constraint",
                    relationship_type="influence",
                    target_type="BusinessProcess",
                    description="Compliance constraints influence business processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Stakeholder",
                    relationship_type="association",
                    target_type="Principle",
                    description="Stakeholders are associated with governance principles",
                    bidirectional=True,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="realization",
                    target_type="Requirement",
                    description="Compliance processes realize regulatory requirements",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Driver",
                    relationship_type="influence",
                    target_type="Constraint",
                    description="External drivers influence compliance constraints",
                    bidirectional=False,
                ),
            ],
            # -----------------------------------------------------------------
            # 12.0 - Manage External Relationships
            # -----------------------------------------------------------------
            "12.0": [
                RelationshipPattern(
                    source_type="BusinessActor",
                    relationship_type="association",
                    target_type="BusinessCollaboration",
                    description="External partners participate in business collaborations",
                    bidirectional=True,
                ),
                RelationshipPattern(
                    source_type="Contract",
                    relationship_type="association",
                    target_type="BusinessActor",
                    description="Contracts govern relationships with external parties",
                    bidirectional=True,
                ),
                RelationshipPattern(
                    source_type="BusinessInterface",
                    relationship_type="serving",
                    target_type="BusinessActor",
                    description="Partner interfaces serve external actors",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="flow",
                    target_type="BusinessObject",
                    description="Partner processes flow information to/from partners",
                    bidirectional=True,
                ),
                RelationshipPattern(
                    source_type="Meaning",
                    relationship_type="association",
                    target_type="Representation",
                    description="Shared meaning is associated with partner communications",
                    bidirectional=True,
                ),
            ],
            # -----------------------------------------------------------------
            # 13.0 - Develop and Manage Human Capital
            # -----------------------------------------------------------------
            "13.0": [
                RelationshipPattern(
                    source_type="BusinessRole",
                    relationship_type="assignment",
                    target_type="BusinessActor",
                    description="HR roles are assigned to employee actors",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Resource",
                    relationship_type="realization",
                    target_type="Capability",
                    description="Human resources realize organizational capabilities",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="BusinessProcess",
                    relationship_type="access",
                    target_type="BusinessObject",
                    description="HR processes access employee records",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="ApplicationComponent",
                    relationship_type="serving",
                    target_type="BusinessProcess",
                    description="HRIS applications serve HR processes",
                    bidirectional=False,
                ),
                RelationshipPattern(
                    source_type="Goal",
                    relationship_type="influence",
                    target_type="BusinessProcess",
                    description="Workforce goals influence talent management processes",
                    bidirectional=False,
                ),
            ],
        }

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    @classmethod
    def get_all_elements(cls) -> Dict[str, ElementTypeDefinition]:
        """Get all element type definitions as a single dictionary."""
        all_elements = {}
        all_elements.update(cls.STRATEGY_ELEMENTS)
        all_elements.update(cls.BUSINESS_ELEMENTS)
        all_elements.update(cls.APPLICATION_ELEMENTS)
        all_elements.update(cls.TECHNOLOGY_ELEMENTS)
        all_elements.update(cls.PHYSICAL_ELEMENTS)
        all_elements.update(cls.MOTIVATION_ELEMENTS)
        all_elements.update(cls.IMPLEMENTATION_ELEMENTS)
        return all_elements

    @classmethod
    def get_elements_by_layer(cls, layer: str) -> Dict[str, ElementTypeDefinition]:
        """Get element type definitions for a specific layer."""
        layer_map = {
            "strategy": cls.STRATEGY_ELEMENTS,
            "business": cls.BUSINESS_ELEMENTS,
            "application": cls.APPLICATION_ELEMENTS,
            "technology": cls.TECHNOLOGY_ELEMENTS,
            "physical": cls.PHYSICAL_ELEMENTS,
            "motivation": cls.MOTIVATION_ELEMENTS,
            ArchiMateLayer.IMPLEMENTATION: cls.IMPLEMENTATION_ELEMENTS,
        }
        return layer_map.get(layer, {})

    @classmethod
    def get_element_type(cls, element_type: str) -> Optional[ElementTypeDefinition]:
        """Get a specific element type definition."""
        all_elements = cls.get_all_elements()
        return all_elements.get(element_type)

    @classmethod
    def get_keywords_for_element(cls, element_type: str) -> List[str]:
        """Get example keywords for matching an element type."""
        element = cls.get_element_type(element_type)
        return element.example_keywords if element else []

    @classmethod
    def find_element_type_by_keyword(cls, keyword: str) -> List[str]:
        """Find element types that match a given keyword."""
        keyword_lower = keyword.lower()
        matches = []
        for element_name, element_def in cls.get_all_elements().items():
            if keyword_lower in [kw.lower() for kw in element_def.example_keywords]:
                matches.append(element_name)
        return matches

    @classmethod
    def get_element_types_for_apqc(cls, apqc_category: str) -> List[str]:
        """Get element types related to an APQC category."""
        matches = []
        for element_name, element_def in cls.get_all_elements().items():
            if element_def.related_apqc and apqc_category in element_def.related_apqc:
                matches.append(element_name)
        return matches

    @classmethod
    def get_valid_element_types(cls) -> Set[str]:
        """Get set of all valid element type names."""
        return set(cls.get_all_elements().keys())

    @classmethod
    def is_valid_element_type(cls, element_type: str) -> bool:
        """Check if an element type is valid."""
        return element_type in cls.get_valid_element_types()

    @classmethod
    def get_relationships_for_apqc(cls, apqc_category: str) -> List["RelationshipPattern"]:
        """
        Get relationship patterns associated with an APQC category.

        Returns typical ArchiMate relationship patterns that are commonly
        derived from processes in the specified APQC category. This enables
        intelligent relationship suggestion when mapping APQC processes
        to ArchiMate elements.

        Args:
            apqc_category: The APQC category code (e.g., '1.0', '7.0', '13.0')

        Returns:
            List of RelationshipPattern objects for the category.
            Returns empty list if category not found.

        Example:
            >>> patterns = ArchiMateElementTypes.get_relationships_for_apqc('7.0')
            >>> for p in patterns:
            ...     print(f"{p.source_type} --{p.relationship_type}--> {p.target_type}")
            ApplicationComponent --serving--> BusinessProcess
            Node --realization--> ApplicationComponent
            ...
        """
        # Ensure templates are initialized
        if not cls.APQC_RELATIONSHIP_TEMPLATES:
            cls._initialize_relationship_templates()

        # Normalize category code (handle both '1.0' and '1' formats)
        normalized_category = apqc_category
        if "." not in apqc_category:
            normalized_category = f"{apqc_category}.0"

        return cls.APQC_RELATIONSHIP_TEMPLATES.get(normalized_category, [])

    @classmethod
    def get_valid_relationships_for_element(cls, element_type: str) -> List[str]:
        """
        Get valid relationship types for a given element type.

        Returns the ArchiMate 3.2 relationship types that are valid for
        the specified element type, based on the ArchiMate metamodel rules.
        This considers both source and target roles for the element.

        Args:
            element_type: The ArchiMate element type name (e.g., 'BusinessProcess')

        Returns:
            List of valid relationship type names that the element can participate in.
            Returns empty list if element type is not valid.

        Example:
            >>> rels = ArchiMateElementTypes.get_valid_relationships_for_element('BusinessProcess')
            >>> print(rels)
            ['composition', 'aggregation', 'assignment', 'realization', 'serving',
             'access', 'triggering', 'flow', 'specialization', 'association']
        """
        if not cls.is_valid_element_type(element_type):
            return []

        element_def = cls.get_element_type(element_type)
        if not element_def:
            return []

        # Base relationships available to all elements
        valid_relationships = ["specialization", "association"]

        # Aspect-based relationship rules (ArchiMate 3.2 metamodel)
        aspect = element_def.aspect
        layer = element_def.layer

        if aspect == "active":
            # Active structure elements can participate in:
            valid_relationships.extend(
                [
                    "composition",  # Can be composed of other active elements
                    "aggregation",  # Can aggregate other active elements
                    "assignment",  # Can be assigned to behavior elements
                    "serving",  # Can serve other elements
                    "flow",  # Can have flow to other elements
                    "triggering",  # Can trigger behavior (in some cases)
                ]
            )

        elif aspect == "behavior":
            # Behavior elements can participate in:
            valid_relationships.extend(
                [
                    "composition",  # Can be composed of other behaviors
                    "aggregation",  # Can aggregate other behaviors
                    "realization",  # Can realize services/passive elements
                    "access",  # Can access passive elements
                    "triggering",  # Can trigger other behaviors
                    "flow",  # Can flow to other elements
                    "serving",  # Services serve other elements
                ]
            )

        elif aspect == "passive":
            # Passive structure elements can participate in:
            valid_relationships.extend(
                [
                    "composition",  # Can be composed of other passive elements
                    "aggregation",  # Can aggregate other passive elements
                    "realization",  # Can be realized by lower-layer elements
                    "access",  # Can be accessed by behavior elements
                ]
            )

        # Layer-specific additions
        if layer == "motivation":
            valid_relationships.append("influence")  # Motivation elements use influence

        if layer == ArchiMateLayer.IMPLEMENTATION:
            valid_relationships.extend(["realization", "triggering"])

        # Remove duplicates and return sorted list
        return sorted(list(set(valid_relationships)))


# =============================================================================
# Plain-language display names for element types
# =============================================================================
# Maps PascalCase ArchiMate type names to plain-language labels so a user who
# has never heard of ArchiMate can read every screen. The "Show ArchiMate names"
# user setting (default off) swaps every display back to the standard names.
# Read by the |plain_name Jinja filter (app/template_helpers.py).

PLAIN_LANGUAGE_NAMES: dict[str, str] = {
    # Strategy layer
    "Resource": "Resource",
    "Capability": "Capability",
    "CourseOfAction": "Option",
    "ValueStream": "Value stream",
    # Business layer
    "BusinessActor": "Person or team",
    "BusinessRole": "Role",
    "BusinessCollaboration": "Collaboration",
    "BusinessInterface": "Business interface",
    "BusinessProcess": "Business process",
    "BusinessFunction": "Business function",
    "BusinessInteraction": "Business interaction",
    "BusinessEvent": "Business event",
    "BusinessService": "Business service",
    "BusinessObject": "Business information",
    "Contract": "Contract",
    "Representation": "Representation",
    "Product": "Product",
    # Application layer
    "ApplicationComponent": "Application",
    "ApplicationCollaboration": "Application collaboration",
    "ApplicationInterface": "Application interface",
    "ApplicationFunction": "Application function",
    "ApplicationProcess": "Application process",
    "ApplicationInteraction": "Application interaction",
    "ApplicationEvent": "Application event",
    "ApplicationService": "Application service",
    "DataObject": "Data",
    # Technology layer
    "Node": "Platform",
    "Device": "Device",
    "SystemSoftware": "Software platform",
    "TechnologyCollaboration": "Technology collaboration",
    "TechnologyInterface": "Technology interface",
    "Path": "Network path",
    "CommunicationNetwork": "Communication network",
    "TechnologyFunction": "Technology function",
    "TechnologyProcess": "Technology process",
    "TechnologyInteraction": "Technology interaction",
    "TechnologyEvent": "Technology event",
    "TechnologyService": "Infrastructure service",
    "Artifact": "Artifact",
    # Physical layer
    "Equipment": "Equipment",
    "Facility": "Facility",
    "DistributionNetwork": "Distribution network",
    "Material": "Material",
    # Motivation layer
    "Stakeholder": "Stakeholder",
    "Driver": "Driver",
    "Assessment": "Finding",
    "Goal": "Goal",
    "Outcome": "Outcome",
    "Principle": "Principle",
    "Requirement": "Requirement",
    "Constraint": "Constraint",
    "Meaning": "Meaning",
    "Value": "Value",
    # Implementation & Migration layer
    "WorkPackage": "Project",
    "Deliverable": "Deliverable",
    "ImplementationEvent": "Milestone",
    "Plateau": "Stage",
    "Gap": "Gap",
}

# Layer display names
PLAIN_LAYER_NAMES: dict[str, str] = {
    "strategy": "Strategy",
    "business": "Business",
    "application": "Applications",
    "technology": "Technology",
    "physical": "Physical",
    "motivation": "Motivation",
    "implementation": "Projects and change",
    "implementation_migration": "Projects and change",
    "other": "Other",
}


def plain_name_for(element_type: str | None) -> str:
    """Return the plain-language display name for an ArchiMate element type.

    Returns the original value unchanged when no plain name is
    registered (e.g. for Location, Grouping, Junction, or any future type).
    Handles snake_case input by converting to PascalCase before lookup.
    """
    if not element_type:
        return "\u2014"
    if element_type in PLAIN_LANGUAGE_NAMES:
        return PLAIN_LANGUAGE_NAMES[element_type]
    # Try snake_case -> PascalCase conversion
    pascal = "".join(word.capitalize() for word in element_type.split("_"))
    if pascal in PLAIN_LANGUAGE_NAMES:
        return PLAIN_LANGUAGE_NAMES[pascal]
    return element_type


def plain_layer_name(layer: str | None) -> str:
    """Return the plain-language display name for an ArchiMate layer."""
    if not layer:
        return "\u2014"
    if layer in PLAIN_LAYER_NAMES:
        return PLAIN_LAYER_NAMES[layer]
    # Try snake_case -> PascalCase conversion
    pascal = "".join(word.capitalize() for word in layer.split("_"))
    if pascal.lower() in PLAIN_LAYER_NAMES:
        return PLAIN_LAYER_NAMES[pascal.lower()]
    return layer


# =============================================================================
# Convenience Constants for Import
# =============================================================================

# All element type names by layer
STRATEGY_ELEMENT_TYPES = list(ArchiMateElementTypes.STRATEGY_ELEMENTS.keys())
BUSINESS_ELEMENT_TYPES = list(ArchiMateElementTypes.BUSINESS_ELEMENTS.keys())
APPLICATION_ELEMENT_TYPES = list(ArchiMateElementTypes.APPLICATION_ELEMENTS.keys())
TECHNOLOGY_ELEMENT_TYPES = list(ArchiMateElementTypes.TECHNOLOGY_ELEMENTS.keys())
PHYSICAL_ELEMENT_TYPES = list(ArchiMateElementTypes.PHYSICAL_ELEMENTS.keys())
MOTIVATION_ELEMENT_TYPES = list(ArchiMateElementTypes.MOTIVATION_ELEMENTS.keys())
IMPLEMENTATION_ELEMENT_TYPES = list(ArchiMateElementTypes.IMPLEMENTATION_ELEMENTS.keys())

ALL_ELEMENT_TYPES = (
    STRATEGY_ELEMENT_TYPES
    + BUSINESS_ELEMENT_TYPES
    + APPLICATION_ELEMENT_TYPES
    + TECHNOLOGY_ELEMENT_TYPES
    + PHYSICAL_ELEMENT_TYPES
    + MOTIVATION_ELEMENT_TYPES
    + IMPLEMENTATION_ELEMENT_TYPES
)

# ArchiMate 3.2 Valid Relationship Types (convenience constant)
VALID_RELATIONSHIP_TYPES = ArchiMateElementTypes.VALID_RELATIONSHIP_TYPES

# =============================================================================
# Module Initialization
# =============================================================================

# Initialize APQC relationship templates at module load time
ArchiMateElementTypes._initialize_relationship_templates()

# Convenience access to APQC relationship templates
APQC_RELATIONSHIP_TEMPLATES = ArchiMateElementTypes.APQC_RELATIONSHIP_TEMPLATES

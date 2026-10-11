"""
ArchiMate 3.2 Standard Viewpoints
PRD - 010.1: All 25 standard viewpoints per ArchiMate specification

Each viewpoint defines:
- Purpose and stakeholders
- Allowed element types
- Allowed relationship types
- Typical concerns addressed

Reference: The Open Group ArchiMate 3.2 Specification, Chapter 14 (Viewpoints)
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ViewpointDefinition:
    """
    Definition of an ArchiMate 3.2 standard viewpoint.

    Viewpoints are a means to focus on particular aspects and layers of the
    architecture. Each viewpoint defines a perspective for a particular type
    of stakeholder and their concerns.

    Attributes:
        name: Full name of the viewpoint
        code: Short code for reference (e.g., 'ORG', 'BPC')
        purpose: Description of what the viewpoint is designed to show
        typical_stakeholders: List of stakeholder roles who typically use this viewpoint
        allowed_elements: List of ArchiMate element types allowed in this viewpoint
        allowed_relationships: List of ArchiMate relationship types allowed
        concerns: List of typical concerns this viewpoint addresses
        layer: Primary layer(s) this viewpoint covers
    """

    name: str
    code: str
    purpose: str
    typical_stakeholders: List[str]
    allowed_elements: List[str]
    allowed_relationships: List[str]
    concerns: List[str]
    layer: str  # motivation, strategy, business, application, technology, physical, implementation, composite


# =============================================================================
# ArchiMate 3.2 Standard Viewpoints (25 Total)
# =============================================================================

VIEWPOINTS: Dict[str, ViewpointDefinition] = {
    # =========================================================================
    # BASIC VIEWPOINTS - Business Layer Focus
    # =========================================================================
    "organization": ViewpointDefinition(
        name="Organization Viewpoint",
        code="ORG",
        purpose="Show the structure of the organization in terms of its constituent "
        "departments, units, roles, and their relationships, optionally linked "
        "to business functions, processes, and locations",
        typical_stakeholders=[
            "Enterprise Architect",
            "Process Architect",
            "HR Manager",
            "Organizational Designer",
        ],
        allowed_elements=["BusinessActor", "BusinessRole", "BusinessCollaboration", "Location"],
        allowed_relationships=["composition", "aggregation", "assignment", "association"],
        concerns=[
            "Organizational structure",
            "Roles and responsibilities",
            "Reporting lines",
            "Departmental boundaries",
        ],
        layer="business",
    ),
    "business_process_cooperation": ViewpointDefinition(
        name="Business Process Cooperation Viewpoint",
        code="BPC",
        purpose="Show the relationships between various business processes, including "
        "how they exchange information and how business roles and actors are "
        "involved in their execution",
        typical_stakeholders=[
            "Process Architect",
            "Business Analyst",
            "Process Owner",
            "Operations Manager",
        ],
        allowed_elements=[
            "BusinessProcess",
            "BusinessFunction",
            "BusinessService",
            "BusinessEvent",
            "BusinessObject",
            "BusinessRole",
            "BusinessActor",
            "BusinessCollaboration",
            "Representation",
        ],
        allowed_relationships=[
            "triggering",
            "flow",
            "access",
            "serving",
            "realization",
            "assignment",
            "composition",
            "aggregation",
        ],
        concerns=[
            "Process interaction",
            "Information flow",
            "Process dependencies",
            "Business choreography",
        ],
        layer="business",
    ),
    "product": ViewpointDefinition(
        name="Product Viewpoint",
        code="PRD",
        purpose="Show the value that products offer to customers or other stakeholders, "
        "and how products are composed of services (business, application, or "
        "technology) and passive structure elements",
        typical_stakeholders=[
            "Product Manager",
            "Marketing Manager",
            "Business Development",
            "Customer Experience Designer",
        ],
        allowed_elements=[
            "Product",
            "BusinessService",
            "ApplicationService",
            "TechnologyService",
            "Contract",
            "Value",
            "BusinessInterface",
            "BusinessProcess",
        ],
        allowed_relationships=[
            "composition",
            "aggregation",
            "realization",
            "association",
            "serving",
        ],
        concerns=["Product composition", "Service bundling", "Value proposition", "Contract terms"],
        layer="business",
    ),
    # =========================================================================
    # APPLICATION LAYER VIEWPOINTS
    # =========================================================================
    "application_cooperation": ViewpointDefinition(
        name="Application Cooperation Viewpoint",
        code="APC",
        purpose="Show application components and their mutual relationships, including "
        "the application services they expose and the data they share",
        typical_stakeholders=[
            "Application Architect",
            "Software Developer",
            "Integration Architect",
            "Solution Architect",
        ],
        allowed_elements=[
            "ApplicationComponent",
            "ApplicationCollaboration",
            "ApplicationInterface",
            "ApplicationService",
            "ApplicationFunction",
            "ApplicationEvent",
            "DataObject",
        ],
        allowed_relationships=[
            "serving",
            "flow",
            "realization",
            "composition",
            "aggregation",
            "triggering",
            "access",
        ],
        concerns=[
            "Application integration",
            "Data exchange",
            "API dependencies",
            "System interfaces",
        ],
        layer="application",
    ),
    "application_usage": ViewpointDefinition(
        name="Application Usage Viewpoint",
        code="APU",
        purpose="Show how applications support business processes by providing "
        "application services to them, and how these services access and "
        "manipulate business data",
        typical_stakeholders=[
            "Application Architect",
            "Business Analyst",
            "Enterprise Architect",
            "Solution Architect",
        ],
        allowed_elements=[
            "BusinessProcess",
            "BusinessFunction",
            "BusinessEvent",
            "BusinessObject",
            "ApplicationComponent",
            "ApplicationService",
            "ApplicationInterface",
            "DataObject",
        ],
        allowed_relationships=["serving", "access", "realization", "flow", "triggering"],
        concerns=[
            "Application support for business",
            "Business-IT alignment",
            "Data access patterns",
            "Service consumption",
        ],
        layer="composite",
    ),
    "application_structure": ViewpointDefinition(
        name="Application Structure Viewpoint",
        code="APS",
        purpose="Show the internal structure of applications in terms of their "
        "sub-components, interfaces, and the data they manage",
        typical_stakeholders=[
            "Application Architect",
            "Software Developer",
            "Technical Lead",
            "Solution Architect",
        ],
        allowed_elements=[
            "ApplicationComponent",
            "ApplicationFunction",
            "ApplicationInterface",
            "ApplicationEvent",
            "ApplicationProcess",
            "ApplicationInteraction",
            "DataObject",
        ],
        allowed_relationships=[
            "composition",
            "aggregation",
            "realization",
            "access",
            "flow",
            "serving",
        ],
        concerns=[
            "Application design",
            "Component structure",
            "Internal interfaces",
            "Data management",
        ],
        layer="application",
    ),
    # =========================================================================
    # TECHNOLOGY LAYER VIEWPOINTS
    # =========================================================================
    "technology": ViewpointDefinition(
        name="Technology Viewpoint",
        code="TEC",
        purpose="Show the technology infrastructure of an organization, including "
        "hardware, system software, networks, and the technology services "
        "they provide",
        typical_stakeholders=[
            "Infrastructure Architect",
            "Operations Manager",
            "Network Administrator",
            "Platform Engineer",
        ],
        allowed_elements=[
            "Node",
            "Device",
            "SystemSoftware",
            "TechnologyInterface",
            "Path",
            "CommunicationNetwork",
            "TechnologyFunction",
            "TechnologyService",
            "Artifact",
            "TechnologyCollaboration",
        ],
        allowed_relationships=[
            "composition",
            "aggregation",
            "assignment",
            "serving",
            "realization",
            "flow",
        ],
        concerns=[
            "Infrastructure topology",
            "Platform components",
            "Network architecture",
            "Technology services",
        ],
        layer="technology",
    ),
    "technology_usage": ViewpointDefinition(
        name="Technology Usage Viewpoint",
        code="TEU",
        purpose="Show how applications are realized on technology infrastructure, "
        "including the mapping of application components to technology nodes",
        typical_stakeholders=[
            "Infrastructure Architect",
            "Application Architect",
            "Platform Engineer",
            "DevOps Engineer",
        ],
        allowed_elements=[
            "ApplicationComponent",
            "ApplicationService",
            "DataObject",
            "Node",
            "Device",
            "SystemSoftware",
            "TechnologyService",
            "TechnologyInterface",
            "Artifact",
        ],
        allowed_relationships=["serving", "assignment", "realization", "access"],
        concerns=[
            "Platform support",
            "Deployment architecture",
            "Application-infrastructure mapping",
            "Resource allocation",
        ],
        layer="composite",
    ),
    # =========================================================================
    # PHYSICAL LAYER VIEWPOINT
    # =========================================================================
    "physical": ViewpointDefinition(
        name="Physical Viewpoint",
        code="PHY",
        purpose="Show the physical environment and equipment of an organization, "
        "including facilities, physical distribution networks, and materials",
        typical_stakeholders=[
            "Facility Manager",
            "Operations Manager",
            "Logistics Manager",
            "Physical Plant Engineer",
        ],
        allowed_elements=[
            "Equipment",
            "Facility",
            "DistributionNetwork",
            "Material",
            "Node",
            "Device",
            "Path",
        ],
        allowed_relationships=[
            "composition",
            "aggregation",
            "serving",
            "assignment",
            "flow",
            "realization",
        ],
        concerns=[
            "Physical infrastructure",
            "Equipment deployment",
            "Facility layout",
            "Distribution networks",
        ],
        layer="physical",
    ),
    # =========================================================================
    # INFORMATION VIEWPOINTS
    # =========================================================================
    "information_structure": ViewpointDefinition(
        name="Information Structure Viewpoint",
        code="INF",
        purpose="Show the structure of the information used in the enterprise, "
        "including data entities, their relationships, and the business "
        "objects they represent",
        typical_stakeholders=[
            "Information Architect",
            "Data Architect",
            "Data Analyst",
            "Business Analyst",
        ],
        allowed_elements=["BusinessObject", "DataObject", "Representation", "Meaning"],
        allowed_relationships=[
            "composition",
            "aggregation",
            "association",
            "realization",
            "access",
        ],
        concerns=["Data structure", "Information model", "Entity relationships", "Data semantics"],
        layer="composite",
    ),
    # =========================================================================
    # SERVICE VIEWPOINTS
    # =========================================================================
    "service_realization": ViewpointDefinition(
        name="Service Realization Viewpoint",
        code="SRV",
        purpose="Show how services are realized by the behavior performed by "
        "internal active structure elements, including the data they access",
        typical_stakeholders=[
            "Application Architect",
            "Service Owner",
            "Solution Architect",
            "Business Analyst",
        ],
        allowed_elements=[
            "BusinessService",
            "BusinessProcess",
            "BusinessFunction",
            "ApplicationService",
            "ApplicationComponent",
            "ApplicationFunction",
            "TechnologyService",
            "DataObject",
            "BusinessObject",
        ],
        allowed_relationships=["realization", "serving", "composition", "aggregation", "access"],
        concerns=[
            "Service implementation",
            "Service dependencies",
            "Service composition",
            "Behavior-service mapping",
        ],
        layer="composite",
    ),
    # =========================================================================
    # MOTIVATION LAYER VIEWPOINTS
    # =========================================================================
    "stakeholder": ViewpointDefinition(
        name="Stakeholder Viewpoint",
        code="STK",
        purpose="Show stakeholders, their concerns, and how these relate to "
        "drivers, assessments, goals, and outcomes",
        typical_stakeholders=[
            "Enterprise Architect",
            "Business Analyst",
            "Strategy Manager",
            "Project Manager",
        ],
        allowed_elements=["Stakeholder", "Driver", "Assessment", "Goal", "Outcome", "Value"],
        allowed_relationships=[
            "association",
            "influence",
            "realization",
            "aggregation",
            "composition",
        ],
        concerns=[
            "Stakeholder concerns",
            "Business drivers",
            "Strategic goals",
            "Expected outcomes",
        ],
        layer="motivation",
    ),
    "goal_realization": ViewpointDefinition(
        name="Goal Realization Viewpoint",
        code="GRL",
        purpose="Show how goals are realized through principles, requirements, "
        "and constraints, and how these relate to each other",
        typical_stakeholders=[
            "Enterprise Architect",
            "Business Analyst",
            "Requirements Engineer",
            "Strategy Manager",
        ],
        allowed_elements=["Goal", "Outcome", "Principle", "Requirement", "Constraint", "Value"],
        allowed_relationships=[
            "realization",
            "influence",
            "association",
            "aggregation",
            "composition",
        ],
        concerns=[
            "Goal decomposition",
            "Requirements traceability",
            "Principle definition",
            "Constraint management",
        ],
        layer="motivation",
    ),
    "requirements_realization": ViewpointDefinition(
        name="Requirements Realization Viewpoint",
        code="RRZ",
        purpose="Show how requirements are realized by core elements across "
        "business, application, and technology layers",
        typical_stakeholders=[
            "Requirements Engineer",
            "Solution Architect",
            "Enterprise Architect",
            "Business Analyst",
        ],
        allowed_elements=[
            "Requirement",
            "Constraint",
            "Goal",
            "BusinessProcess",
            "BusinessFunction",
            "ApplicationComponent",
            "ApplicationService",
            "Node",
            "TechnologyService",
        ],
        allowed_relationships=["realization", "influence", "association"],
        concerns=[
            "Requirements coverage",
            "Solution fit",
            "Traceability",
            "Compliance verification",
        ],
        layer="composite",
    ),
    "motivation": ViewpointDefinition(
        name="Motivation Viewpoint",
        code="MOT",
        purpose="Provide a comprehensive view of all motivation elements and "
        "their relationships, showing the complete motivation architecture",
        typical_stakeholders=[
            "Enterprise Architect",
            "Strategy Manager",
            "Business Analyst",
            "Chief Architect",
        ],
        allowed_elements=[
            "Stakeholder",
            "Driver",
            "Assessment",
            "Goal",
            "Outcome",
            "Principle",
            "Requirement",
            "Constraint",
            "Meaning",
            "Value",
        ],
        allowed_relationships=[
            "association",
            "influence",
            "realization",
            "aggregation",
            "composition",
        ],
        concerns=[
            "Strategic alignment",
            "Business motivation",
            "Value proposition",
            "Governance framework",
        ],
        layer="motivation",
    ),
    # =========================================================================
    # STRATEGY LAYER VIEWPOINTS
    # =========================================================================
    "strategy": ViewpointDefinition(
        name="Strategy Viewpoint",
        code="STR",
        purpose="Show strategic elements such as resources, capabilities, and "
        "courses of action, and how they support the achievement of goals",
        typical_stakeholders=[
            "Strategy Manager",
            "Enterprise Architect",
            "Business Executive",
            "Chief Architect",
        ],
        allowed_elements=[
            "Resource",
            "Capability",
            "CourseOfAction",
            "ValueStream",
            "Goal",
            "Outcome",
        ],
        allowed_relationships=[
            "realization",
            "association",
            "assignment",
            "triggering",
            "flow",
            "composition",
            "aggregation",
            "influence",
        ],
        concerns=[
            "Strategic planning",
            "Capability development",
            "Resource allocation",
            "Strategic initiatives",
        ],
        layer="strategy",
    ),
    "capability_map": ViewpointDefinition(
        name="Capability Map Viewpoint",
        code="CAP",
        purpose="Show business capabilities, their structure, and how they are "
        "realized by resources, processes, and applications",
        typical_stakeholders=[
            "Enterprise Architect",
            "Business Analyst",
            "Strategy Manager",
            "Capability Owner",
        ],
        allowed_elements=[
            "Capability",
            "Resource",
            "BusinessProcess",
            "BusinessFunction",
            "ApplicationComponent",
            "ApplicationService",
            "CourseOfAction",
        ],
        allowed_relationships=[
            "composition",
            "aggregation",
            "assignment",
            "realization",
            "serving",
        ],
        concerns=[
            "Capability assessment",
            "Capability gaps",
            "Capability hierarchy",
            "Capability realization",
        ],
        layer="composite",
    ),
    "outcome_realization": ViewpointDefinition(
        name="Outcome Realization Viewpoint",
        code="OUT",
        purpose="Show how business outcomes are achieved through capabilities, "
        "courses of action, and the value they deliver",
        typical_stakeholders=[
            "Strategy Manager",
            "Program Manager",
            "Enterprise Architect",
            "Portfolio Manager",
        ],
        allowed_elements=["Outcome", "Value", "Capability", "CourseOfAction", "Resource", "Goal"],
        allowed_relationships=[
            "realization",
            "influence",
            "association",
            "composition",
            "aggregation",
        ],
        concerns=[
            "Value delivery",
            "Outcome tracking",
            "Benefit realization",
            "Performance measurement",
        ],
        layer="composite",
    ),
    "resource_map": ViewpointDefinition(
        name="Resource Map Viewpoint",
        code="RSC",
        purpose="Show resources, their allocation to capabilities, and their "
        "relationships with organizational actors and roles",
        typical_stakeholders=[
            "Resource Manager",
            "Enterprise Architect",
            "HR Manager",
            "Finance Manager",
        ],
        allowed_elements=[
            "Resource",
            "Capability",
            "BusinessActor",
            "BusinessRole",
            "BusinessCollaboration",
            "CourseOfAction",
        ],
        allowed_relationships=[
            "assignment",
            "association",
            "composition",
            "aggregation",
            "realization",
        ],
        concerns=[
            "Resource allocation",
            "Resource dependencies",
            "Resource optimization",
            "Capability support",
        ],
        layer="strategy",
    ),
    "value_stream": ViewpointDefinition(
        name="Value Stream Viewpoint",
        code="VAL",
        purpose="Show value streams and their stages, including the capabilities "
        "required and the value delivered at each stage",
        typical_stakeholders=[
            "Business Analyst",
            "Process Owner",
            "Strategy Manager",
            "Enterprise Architect",
        ],
        allowed_elements=[
            "ValueStream",
            "Capability",
            "Resource",
            "BusinessProcess",
            "BusinessFunction",
            "Value",
            "Outcome",
            "Stakeholder",
        ],
        allowed_relationships=[
            "triggering",
            "flow",
            "realization",
            "association",
            "composition",
            "aggregation",
            "serving",
        ],
        concerns=[
            "Value creation",
            "Process flow",
            "Stage dependencies",
            "End-to-end value delivery",
        ],
        layer="composite",
    ),
    # =========================================================================
    # IMPLEMENTATION & MIGRATION VIEWPOINTS
    # =========================================================================
    "project": ViewpointDefinition(
        name="Project Viewpoint",
        code="PRJ",
        purpose="Show work packages, their deliverables, and the actors and "
        "roles responsible for their execution, linked to goals",
        typical_stakeholders=["Project Manager", "PMO", "Program Manager", "Portfolio Manager"],
        allowed_elements=[
            "WorkPackage",
            "Deliverable",
            "ImplementationEvent",
            "BusinessActor",
            "BusinessRole",
            "Goal",
            "Plateau",
        ],
        allowed_relationships=[
            "realization",
            "assignment",
            "association",
            "triggering",
            "composition",
            "aggregation",
        ],
        concerns=[
            "Project planning",
            "Deliverable tracking",
            "Resource assignment",
            "Milestone management",
        ],
        layer="implementation",
    ),
    "migration": ViewpointDefinition(
        name="Migration Viewpoint",
        code="MIG",
        purpose="Show the transition from a baseline architecture to a target "
        "architecture, including plateaus and the gaps that need to be addressed",
        typical_stakeholders=[
            "Enterprise Architect",
            "Program Manager",
            "Transformation Lead",
            "Solution Architect",
        ],
        allowed_elements=["Plateau", "Gap", "WorkPackage", "Deliverable", "ImplementationEvent"],
        allowed_relationships=[
            "association",
            "triggering",
            "realization",
            "composition",
            "aggregation",
        ],
        concerns=[
            "Migration planning",
            "Gap closure",
            "Transition states",
            "Architecture evolution",
        ],
        layer="implementation",
    ),
    "implementation_and_migration": ViewpointDefinition(
        name="Implementation and Migration Viewpoint",
        code="IMP",
        purpose="Provide a comprehensive view of implementation and migration "
        "planning, including work packages, plateaus, gaps, and how they "
        "relate to architecture elements",
        typical_stakeholders=[
            "Enterprise Architect",
            "Solution Architect",
            "Program Manager",
            "Transformation Lead",
        ],
        allowed_elements=[
            "Plateau",
            "Gap",
            "WorkPackage",
            "Deliverable",
            "ImplementationEvent",
            "BusinessProcess",
            "BusinessFunction",
            "ApplicationComponent",
            "ApplicationService",
            "Node",
            "TechnologyService",
            "Goal",
            "Requirement",
        ],
        allowed_relationships=[
            "association",
            "realization",
            "triggering",
            "composition",
            "aggregation",
            "assignment",
        ],
        concerns=[
            "Roadmap planning",
            "Architecture evolution",
            "Change management",
            "Implementation tracking",
        ],
        layer="implementation",
    ),
    # =========================================================================
    # COMPOSITE/LAYERED VIEWPOINTS
    # =========================================================================
    "layered": ViewpointDefinition(
        name="Layered Viewpoint",
        code="LAY",
        purpose="Show all architecture layers and their dependencies in a single "
        "overview, providing a complete picture of the enterprise architecture",
        typical_stakeholders=[
            "Enterprise Architect",
            "Chief Architect",
            "CIO",
            "Solution Architect",
        ],
        allowed_elements=["*"],  # All elements allowed
        allowed_relationships=["*"],  # All relationships allowed
        concerns=[
            "Cross-layer dependencies",
            "Architecture overview",
            "Layer alignment",
            "End-to-end traceability",
        ],
        layer="composite",
    ),
    "landscape_map": ViewpointDefinition(
        name="Landscape Map Viewpoint",
        code="LND",
        purpose="Provide a high-level overview map of the enterprise architecture, "
        "typically used for communication with senior stakeholders",
        typical_stakeholders=[
            "Enterprise Architect",
            "CIO",
            "Business Executive",
            "Chief Architect",
        ],
        allowed_elements=[
            "ApplicationComponent",
            "Capability",
            "BusinessFunction",
            "BusinessProcess",
            "Node",
            "BusinessService",
            "ApplicationService",
            "TechnologyService",
        ],
        allowed_relationships=[
            "composition",
            "aggregation",
            "serving",
            "realization",
            "assignment",
        ],
        concerns=[
            "Portfolio overview",
            "Application landscape",
            "Capability coverage",
            "Strategic positioning",
        ],
        layer="composite",
    ),
}


# =============================================================================
# Utility Functions
# =============================================================================


def get_viewpoint(code: str) -> Optional[ViewpointDefinition]:
    """
    Get a viewpoint definition by its code.

    Args:
        code: The viewpoint code (e.g., 'ORG', 'BPC', 'LAY')

    Returns:
        ViewpointDefinition if found, None otherwise

    Example:
        >>> vp = get_viewpoint('ORG')
        >>> print(vp.name)
        'Organization Viewpoint'
    """
    code_upper = code.upper()
    for vp in VIEWPOINTS.values():
        if vp.code == code_upper:
            return vp
    return None


def get_viewpoint_by_name(name: str) -> Optional[ViewpointDefinition]:
    """
    Get a viewpoint definition by its dictionary key name.

    Args:
        name: The dictionary key (e.g., 'organization', 'business_process_cooperation')

    Returns:
        ViewpointDefinition if found, None otherwise
    """
    return VIEWPOINTS.get(name)


def get_viewpoints_for_layer(layer: str) -> List[ViewpointDefinition]:
    """
    Get all viewpoints that primarily cover a specific layer.

    Args:
        layer: The ArchiMate layer (motivation, strategy, business, application,
               technology, physical, implementation, composite)

    Returns:
        List of ViewpointDefinition objects for the specified layer

    Example:
        >>> vps = get_viewpoints_for_layer('business')
        >>> print([vp.code for vp in vps])
        ['ORG', 'BPC', 'PRD']
    """
    return [vp for vp in VIEWPOINTS.values() if vp.layer == layer]


def get_all_viewpoint_codes() -> List[str]:
    """
    Get all viewpoint codes.

    Returns:
        List of all viewpoint codes (e.g., ['ORG', 'BPC', 'PRD', ...])
    """
    return [vp.code for vp in VIEWPOINTS.values()]


def get_all_viewpoint_names() -> List[str]:
    """
    Get all viewpoint dictionary keys.

    Returns:
        List of all viewpoint keys (e.g., ['organization', 'business_process_cooperation', ...])
    """
    return list(VIEWPOINTS.keys())


def get_viewpoints_for_stakeholder(stakeholder: str) -> List[ViewpointDefinition]:
    """
    Get viewpoints that are relevant for a specific stakeholder type.

    Args:
        stakeholder: The stakeholder role (e.g., 'Enterprise Architect', 'Developer')

    Returns:
        List of ViewpointDefinition objects relevant for the stakeholder

    Example:
        >>> vps = get_viewpoints_for_stakeholder('Enterprise Architect')
        >>> print(len(vps))  # Many viewpoints are relevant for EA
    """
    stakeholder_lower = stakeholder.lower()
    return [
        vp
        for vp in VIEWPOINTS.values()
        if any(stakeholder_lower in s.lower() for s in vp.typical_stakeholders)
    ]


def get_viewpoints_allowing_element(element_type: str) -> List[ViewpointDefinition]:
    """
    Get viewpoints that allow a specific element type.

    Args:
        element_type: The ArchiMate element type (e.g., 'BusinessProcess', 'ApplicationComponent')

    Returns:
        List of ViewpointDefinition objects that allow the element type

    Example:
        >>> vps = get_viewpoints_allowing_element('ApplicationComponent')
        >>> print([vp.code for vp in vps])
    """
    return [
        vp
        for vp in VIEWPOINTS.values()
        if element_type in vp.allowed_elements or "*" in vp.allowed_elements
    ]


def get_viewpoints_addressing_concern(concern: str) -> List[ViewpointDefinition]:
    """
    Get viewpoints that address a specific concern.

    Args:
        concern: The concern keyword (e.g., 'integration', 'deployment', 'strategy')

    Returns:
        List of ViewpointDefinition objects that address the concern

    Example:
        >>> vps = get_viewpoints_addressing_concern('integration')
        >>> print([vp.code for vp in vps])
    """
    concern_lower = concern.lower()
    return [
        vp for vp in VIEWPOINTS.values() if any(concern_lower in c.lower() for c in vp.concerns)
    ]


def is_element_allowed_in_viewpoint(element_type: str, viewpoint_code: str) -> bool:
    """
    Check if an element type is allowed in a specific viewpoint.

    Args:
        element_type: The ArchiMate element type
        viewpoint_code: The viewpoint code

    Returns:
        True if the element is allowed, False otherwise
    """
    vp = get_viewpoint(viewpoint_code)
    if not vp:
        return False
    return element_type in vp.allowed_elements or "*" in vp.allowed_elements


def is_relationship_allowed_in_viewpoint(relationship_type: str, viewpoint_code: str) -> bool:
    """
    Check if a relationship type is allowed in a specific viewpoint.

    Args:
        relationship_type: The ArchiMate relationship type
        viewpoint_code: The viewpoint code

    Returns:
        True if the relationship is allowed, False otherwise
    """
    vp = get_viewpoint(viewpoint_code)
    if not vp:
        return False
    return relationship_type in vp.allowed_relationships or "*" in vp.allowed_relationships


def validate_view_against_viewpoint(
    elements: List[str], relationships: List[str], viewpoint_code: str
) -> Dict[str, List[str]]:
    """
    Validate elements and relationships against a viewpoint definition.

    Args:
        elements: List of element types in the view
        relationships: List of relationship types in the view
        viewpoint_code: The viewpoint code to validate against

    Returns:
        Dictionary with 'invalid_elements' and 'invalid_relationships' lists

    Example:
        >>> result = validate_view_against_viewpoint(
        ...     ['BusinessProcess', 'Node'],  # Node not allowed in BPC
        ...     ['serving', 'composition'],
        ...     'BPC'
        ... )
        >>> print(result['invalid_elements'])
        ['Node']
    """
    vp = get_viewpoint(viewpoint_code)
    if not vp:
        return {
            "invalid_elements": [],
            "invalid_relationships": [],
            "error": f"Unknown viewpoint code: {viewpoint_code}",
        }

    invalid_elements = []
    invalid_relationships = []

    # Check elements (skip if '*' allows all)
    if "*" not in vp.allowed_elements:
        for element in elements:
            if element not in vp.allowed_elements:
                invalid_elements.append(element)

    # Check relationships (skip if '*' allows all)
    if "*" not in vp.allowed_relationships:
        for relationship in relationships:
            if relationship not in vp.allowed_relationships:
                invalid_relationships.append(relationship)

    return {"invalid_elements": invalid_elements, "invalid_relationships": invalid_relationships}


def get_viewpoint_summary() -> Dict[str, Dict]:
    """
    Get a summary of all viewpoints for documentation or UI purposes.

    Returns:
        Dictionary mapping viewpoint codes to summary information

    Example:
        >>> summary = get_viewpoint_summary()
        >>> print(summary['ORG']['name'])
        'Organization Viewpoint'
    """
    return {
        vp.code: {
            "name": vp.name,
            "purpose": vp.purpose,
            "layer": vp.layer,
            "element_count": len(vp.allowed_elements) if "*" not in vp.allowed_elements else "all",
            "relationship_count": len(vp.allowed_relationships)
            if "*" not in vp.allowed_relationships
            else "all",
            "stakeholder_count": len(vp.typical_stakeholders),
            "concern_count": len(vp.concerns),
        }
        for vp in VIEWPOINTS.values()
    }


# =============================================================================
# Layer Constants
# =============================================================================

VIEWPOINT_LAYERS = [
    "motivation",
    "strategy",
    "business",
    "application",
    "technology",
    "physical",
    "implementation",
    "composite",
]

# =============================================================================
# Viewpoint Categories (for grouping in UI)
# =============================================================================

VIEWPOINT_CATEGORIES = {
    "basic": ["organization", "business_process_cooperation", "product"],
    "application": ["application_cooperation", "application_usage", "application_structure"],
    "technology": ["technology", "technology_usage"],
    "physical": ["physical"],
    "information": ["information_structure"],
    "service": ["service_realization"],
    "motivation": ["stakeholder", "goal_realization", "requirements_realization", "motivation"],
    "strategy": [
        "strategy",
        "capability_map",
        "outcome_realization",
        "resource_map",
        "value_stream",
    ],
    "implementation": ["project", "migration", "implementation_and_migration"],
    "composite": ["layered", "landscape_map"],
}


# =============================================================================
# Canvas templates — Lean Canvas, Business Model Canvas, business case (wave 1)
# =============================================================================
# A canvas is a viewpoint template, not a second drawing engine: it projects
# the standard viewpoints above through named boxes instead of drawing
# anything new. CANVAS_TEMPLATES is data beside VIEWPOINTS; `flask
# seed-viewpoints` (app/commands/seed_viewpoints.py) upserts one
# ArchiMateViewpoint catalogue row per template and the `profile`
# property-template row every zone's element types need.
#
# `colour` is always a layer name (motivation/strategy/business/
# implementation/composite) — never a literal colour. The client resolves it
# through ComposerRenderer.layerColor: no new drawing library, no literal
# colour anywhere in this module (see test_no_literal_colour_anywhere_in_the_
# config_module in tests/test_canvas_templates.py).
#
# Only two templates share a record: `lean_canvas` and `business_model_canvas`
# both read/write BusinessModelCanvas — which canvas is which is the saved
# diagram's viewpoint_type, not a second table.

CANVAS_REASON_CODES = frozenset({"canvas_box_empty", "canvas_box_not_derived"})

CANVAS_MEMBERSHIP_KINDS = frozenset({
    "type_profile",         # entries = elements of the box's type(s) carrying its profile
    "type_profile_anchor",  # as above, plus a relationship to another box's entry (anchor)
    "attribute",            # a typed-property view over another box's entries — never an element
    "composed",             # read from other boxes' recorded facts side by side, never derived
    "register",             # read from an existing register (e.g. risks), never duplicated
})

# Membership kinds whose entries are real elements and so carry all three flags.
CANVAS_ELEMENT_MEMBERSHIP = frozenset({"type_profile", "type_profile_anchor", "register"})

CANVAS_FLAG_NAMES = ("high_risk", "nothing_realises", "stale")

# Words that would make a zone's label or box_key read as example content
# rather than a real box name — the fabrication rule's static placeholder
# test.
_CANVAS_PLACEHOLDER_WORDS = (
    "lorem ipsum", "example text", "placeholder", "todo", "tbd", "xxx", "sample data",
)


def _canvas_zone(
    box_key, label, order, element_types, profile, membership, colour,
    anchor_box=None, fill_relationship=None, attribute_keys=None,
    empty_reason="canvas_box_empty",
):
    """One canvas box.

    `phone_order` always equals `order`: the design stacks every canvas in
    the same sequence at 360px as at desktop width — there is no separate
    phone reordering for canvases, only a different layout of the same
    order.
    """
    return {
        "box_key": box_key,
        "label": label,
        "order": order,
        "phone_order": order,
        "element_types": list(element_types),
        "profile": profile,
        "membership": membership,
        "anchor_box": anchor_box,
        "fill_relationship": fill_relationship,
        "attribute_keys": list(attribute_keys or []),
        "empty_reason": empty_reason,
        "colour": colour,
        "flags": list(CANVAS_FLAG_NAMES) if membership in CANVAS_ELEMENT_MEMBERSHIP else [],
    }


# ── Lean Canvas — one row per box the standard's own mapping gives, in the
# design's stated screen order ──────────────────────────────────────────
_LEAN_ZONES = [
    _canvas_zone(
        "problem", "Problem", 1, ["Driver", "Assessment"], "problem",
        "type_profile_anchor", "motivation",
        anchor_box="customer_segments",
        fill_relationship={"type": "association", "direction": "out"},
    ),
    _canvas_zone(
        "customer_segments", "Customer Segments", 2, ["Stakeholder"], "customer_segment",
        "type_profile", "motivation",
    ),
    _canvas_zone(
        "value_propositions", "Unique Value Proposition", 3, ["Value"], "value_proposition",
        "type_profile_anchor", "motivation",
        anchor_box="customer_segments",
        fill_relationship={"type": "association", "direction": "out"},
    ),
    _canvas_zone(
        "solution", "Solution", 4, ["Requirement"], "solution_feature",
        "type_profile_anchor", "motivation",
        fill_relationship={"type": "realization", "direction": "in"},
    ),
    _canvas_zone(
        "channels", "Channels", 5, ["BusinessInterface"], "channel",
        "type_profile_anchor", "business",
        anchor_box="customer_segments",
        fill_relationship={"type": "association", "direction": "out"},
    ),
    _canvas_zone(
        "revenue_streams", "Revenue Streams", 6, ["Value"], None,
        "attribute", "motivation",
        anchor_box="value_propositions",
        attribute_keys=["revenue_model", "revenue_amount", "currency"],
    ),
    _canvas_zone(
        "cost_structure", "Cost Structure", 7, ["Resource", "Capability"], None,
        "attribute", "strategy",
        attribute_keys=["cost_type", "cost_amount", "currency"],
    ),
    _canvas_zone(
        "key_metrics", "Key Metrics", 8, ["Outcome"], "key_metric",
        "type_profile_anchor", "motivation",
        anchor_box="value_propositions",
        fill_relationship={"type": "association", "direction": "out"},
    ),
    _canvas_zone(
        "unfair_advantage", "Unfair Advantage", 9, ["Resource"], "unfair_advantage",
        "type_profile_anchor", "strategy",
        fill_relationship={"type": "assignment", "direction": "out"},
    ),
]

# ── Business Model Canvas — one row per box the standard's own mapping
# gives, in the design's stated screen order. Every box_key equals its
# BusinessModelCanvas column (app/models/business_model.py CANVAS_BLOCKS) —
# a box_key equals the record column where one exists.
_BMC_ZONES = [
    _canvas_zone(
        "customer_segments", "Customer Segments", 1, ["Stakeholder"], "customer_segment",
        "type_profile", "motivation",
    ),
    _canvas_zone(
        "value_propositions", "Value Propositions", 2, ["Value"], "value_proposition",
        "type_profile_anchor", "motivation",
        anchor_box="customer_segments",
        fill_relationship={"type": "association", "direction": "out"},
    ),
    _canvas_zone(
        "channels", "Channels", 3, ["BusinessInterface"], "channel",
        "type_profile_anchor", "business",
        anchor_box="customer_segments",
        fill_relationship={"type": "association", "direction": "out"},
    ),
    _canvas_zone(
        "customer_relationships", "Customer Relationships", 4, ["BusinessService"],
        "customer_relationship", "type_profile_anchor", "business",
        anchor_box="customer_segments",
        fill_relationship={"type": "association", "direction": "out"},
    ),
    _canvas_zone(
        "revenue_streams", "Revenue Streams", 5, ["Value"], None,
        "attribute", "motivation",
        anchor_box="value_propositions",
        attribute_keys=["revenue_model", "revenue_amount", "currency"],
    ),
    _canvas_zone(
        "key_activities", "Key Activities", 6, ["Capability"], "key_activity",
        "type_profile_anchor", "strategy",
        fill_relationship={"type": "realization", "direction": "out"},
    ),
    _canvas_zone(
        "key_resources", "Key Resources", 7, ["Resource"], "key_resource",
        "type_profile_anchor", "strategy",
        anchor_box="key_activities",
        fill_relationship={"type": "assignment", "direction": "out"},
    ),
    _canvas_zone(
        "key_partners", "Key Partnerships", 8, ["BusinessActor"], "key_partner",
        "type_profile_anchor", "business",
        anchor_box="key_resources",
        fill_relationship={"type": "association", "direction": "out"},
    ),
    _canvas_zone(
        "cost_structure", "Cost Structure", 9, ["Resource", "Capability"], None,
        "attribute", "strategy",
        attribute_keys=["cost_type", "cost_amount", "currency"],
    ),
]

# ── Business case — one row per box the standard's own mapping gives, in
# record order. box_key equals the BusinessCase column where one exists
# (app/models/business_case.py); the five with none (executive_summary,
# expected_disbenefits, timescale, costs, investment_appraisal) carry no
# existing note to render yet.
_CASE_ZONES = [
    _canvas_zone(
        "executive_summary", "Executive summary", 1, [], None,
        "composed", "composite",
        empty_reason="canvas_box_not_derived",
    ),
    _canvas_zone(
        "problem_statement", "Reasons (strategic context)", 2, ["Driver", "Assessment"], "reason",
        "type_profile", "motivation",
    ),
    _canvas_zone(
        "options_considered", "Business options", 3, ["CourseOfAction"], "option",
        "type_profile", "strategy",
    ),
    _canvas_zone(
        "expected_benefits", "Expected benefits", 4, ["Outcome"], "benefit",
        "type_profile_anchor", "motivation",
        anchor_box="options_considered",
        fill_relationship={"type": "realization", "direction": "in"},
    ),
    _canvas_zone(
        "expected_disbenefits", "Expected dis-benefits", 5, ["Outcome"], "disbenefit",
        "type_profile_anchor", "motivation",
        anchor_box="options_considered",
        fill_relationship={"type": "realization", "direction": "in"},
    ),
    _canvas_zone(
        "timescale", "Timescale", 6, ["WorkPackage", "ImplementationEvent", "Plateau"], "plan_item",
        "type_profile_anchor", "implementation",
        anchor_box="expected_benefits",
        fill_relationship={"type": "realization", "direction": "out"},
    ),
    _canvas_zone(
        "costs", "Costs", 7, ["WorkPackage", "CourseOfAction"], None,
        "attribute", "implementation",
        attribute_keys=["cost_amount", "currency", "cost_type"],
    ),
    _canvas_zone(
        "investment_appraisal", "Investment appraisal", 8, [], None,
        "composed", "composite",
        empty_reason="canvas_box_not_derived",
    ),
    _canvas_zone(
        "key_risks", "Major risks", 9, ["Assessment"], "risk",
        "register", "motivation",
        anchor_box="options_considered",
        fill_relationship={"type": "association", "direction": "out"},
    ),
]

CANVAS_TEMPLATES: Dict[str, dict] = {
    "lean_canvas": {
        "key": "lean_canvas",
        "name": "Lean Canvas",
        "record": "business_model_canvas",
        "projects": ["motivation", "strategy"],
        "zones": _LEAN_ZONES,
    },
    "business_model_canvas": {
        "key": "business_model_canvas",
        "name": "Business Model Canvas",
        "record": "business_model_canvas",
        "projects": ["motivation", "strategy", "product"],
        "zones": _BMC_ZONES,
    },
    "business_case": {
        "key": "business_case",
        "name": "Business case",
        "record": "business_case",
        "projects": ["motivation", "strategy", "implementation_and_migration", "project"],
        "zones": _CASE_ZONES,
    },
}


def validate_canvas_templates(templates: Optional[Dict[str, dict]] = None) -> None:
    """Assert a CANVAS_TEMPLATES-shaped dict's schema: every required zone
    field present, box_keys unique within a template, membership and
    empty_reason from their closed vocabularies, colour a layer name and
    never a literal colour, flags present on every element zone and absent
    from attribute/composed zones, order a dense 1..N sequence, and no
    placeholder text in any label or box_key. Raises AssertionError on the
    first problem found.

    Validates the real CANVAS_TEMPLATES by default; a caller passes a copy
    with one field deliberately broken to prove the check actually catches
    it (a static test importing this module is otherwise unable to tell a
    validator that always passes from one that works).
    """
    templates = CANVAS_TEMPLATES if templates is None else templates
    for tpl_key, tpl in templates.items():
        assert tpl["key"] == tpl_key, f"{tpl_key}: key field does not match its dict key"
        assert tpl["record"] in ("business_model_canvas", "business_case"), (
            f"{tpl_key}: unknown record {tpl['record']!r}"
        )
        assert isinstance(tpl["projects"], list) and tpl["projects"], f"{tpl_key}: no projects"
        for vp_key in tpl["projects"]:
            assert vp_key in VIEWPOINTS, f"{tpl_key}: {vp_key!r} is not a standard viewpoint key"

        zones = tpl["zones"]
        assert zones, f"{tpl_key}: no zones"
        seen_keys = set()
        for zone in zones:
            for field in (
                "box_key", "label", "order", "phone_order", "element_types",
                "membership", "anchor_box", "fill_relationship", "attribute_keys",
                "empty_reason", "colour", "flags",
            ):
                assert field in zone, f"{tpl_key}: zone missing {field!r}"

            box_key = zone["box_key"]
            assert box_key and box_key not in seen_keys, (
                f"{tpl_key}: missing or duplicate box_key {box_key!r}"
            )
            seen_keys.add(box_key)

            assert zone["membership"] in CANVAS_MEMBERSHIP_KINDS, (
                f"{tpl_key}.{box_key}: unknown membership {zone['membership']!r}"
            )
            assert zone["empty_reason"] in CANVAS_REASON_CODES, (
                f"{tpl_key}.{box_key}: unknown empty_reason {zone['empty_reason']!r}"
            )
            assert zone["colour"] in VIEWPOINT_LAYERS, (
                f"{tpl_key}.{box_key}: colour {zone['colour']!r} is not a layer name"
            )
            assert not re.match(r"^#[0-9a-fA-F]{3,8}$", str(zone["colour"])), (
                f"{tpl_key}.{box_key}: colour must never be a literal colour"
            )
            if zone["membership"] in CANVAS_ELEMENT_MEMBERSHIP:
                assert zone["flags"] == list(CANVAS_FLAG_NAMES), (
                    f"{tpl_key}.{box_key}: an element zone must carry all three flags"
                )
            else:
                assert zone["flags"] == [], (
                    f"{tpl_key}.{box_key}: an attribute/composed zone must carry no flags"
                )

            for text in (zone["label"], box_key):
                low = text.lower()
                for word in _CANVAS_PLACEHOLDER_WORDS:
                    assert word not in low, (
                        f"{tpl_key}.{box_key}: placeholder text {word!r} found in {text!r}"
                    )

        orders = sorted(z["order"] for z in zones)
        assert orders == list(range(1, len(zones) + 1)), (
            f"{tpl_key}: order is not a dense 1..{len(zones)} sequence: {orders}"
        )
        phone_orders = sorted(z["phone_order"] for z in zones)
        assert phone_orders == orders, f"{tpl_key}: phone_order must match order"


# Every distinct ArchiMate type CANVAS_TEMPLATES' zones name, plus the
# element types the mapping names as a secondary concept within a box (e.g.
# Problem's "existing alternatives" Assessment), mapped to every profile
# value that type takes anywhere across the three templates. One
# AcmPropertyTemplate `profile` row is seeded per key
# (app/commands/seed_viewpoints.py) — this is the source of that seeding, not
# derived from the zones above, because a type's full profile vocabulary
# spans more than any one zone's own `profile` field.
CANVAS_PROFILE_OPTIONS_BY_TYPE: Dict[str, List[str]] = {
    "Driver": ["problem", "reason"],
    "Assessment": ["existing_alternative", "current_state", "risk"],
    "Stakeholder": ["customer_segment"],
    "Value": ["value_proposition"],
    "Requirement": ["solution_feature"],
    "Outcome": ["key_metric", "benefit", "disbenefit"],
    "BusinessInterface": ["channel"],
    "Resource": ["unfair_advantage", "key_resource"],
    "Capability": ["key_activity"],
    "BusinessService": ["customer_relationship"],
    "BusinessActor": ["key_partner"],
    "CourseOfAction": ["option"],
    "WorkPackage": ["plan_item"],
    "ImplementationEvent": ["milestone"],
    "Plateau": ["target_state"],
}

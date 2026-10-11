"""QA-CMP-004: Seed the 25 standard ArchiMate 3.2 viewpoints.

Run via Flask CLI:
    flask seed-viewpoints

Idempotent: uses upsert by name. Safe to run multiple times.
"""

import logging

import click
from flask.cli import with_appcontext

from app import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 25 standard ArchiMate 3.2 viewpoints
# Reference: The Open Group ArchiMate 3.2 Specification, Chapter 14
# ---------------------------------------------------------------------------
_STANDARD_VIEWPOINTS = [
    {
        "standard_number": 1,
        "name": "Organization",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows the structure of an organization in terms of its business "
            "actors, roles, collaborations, and their relationships."
        ),
        "purpose": (
            "Designing, deciding, and informing about the organization's "
            "structure, responsibilities, and authority relationships."
        ),
        "concerns": [
            "Organizational structure",
            "Responsibilities",
            "Authority",
            "Roles",
        ],
        "typical_stakeholders": [
            "CIO",
            "Business Owner",
            "HR Manager",
            "Enterprise Architect",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": False,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessActor",
            "BusinessRole",
            "BusinessCollaboration",
            "BusinessInterface",
            "Location",
        ],
        "allowed_relationship_types": [
            "composition",
            "aggregation",
            "assignment",
            "association",
            "specialization",
        ],
        "typical_usage_scenario": (
            "Use when designing or documenting the organizational hierarchy, "
            "role assignments, and inter-departmental collaborations."
        ),
        "example_questions": [
            "Which roles are responsible for a given business function?",
            "How are departments structured?",
            "What collaboration structures exist?",
        ],
        "related_viewpoints": [
            "Actor Co-operation",
            "Business Function",
        ],
    },
    {
        "standard_number": 2,
        "name": "Actor Co-operation",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows the co-operation between business actors, the roles they "
            "play, and the services they use and provide."
        ),
        "purpose": (
            "Designing and deciding on actor co-operation, identifying "
            "external partners, and understanding service provision chains."
        ),
        "concerns": [
            "Actor co-operation",
            "External collaboration",
            "Service provision",
        ],
        "typical_stakeholders": [
            "Business Owner",
            "Enterprise Architect",
            "Business Analyst",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": True,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessActor",
            "BusinessRole",
            "BusinessCollaboration",
            "BusinessInterface",
            "BusinessService",
            "ApplicationComponent",
            "ApplicationInterface",
            "ApplicationService",
        ],
        "allowed_relationship_types": [
            "serving",
            "composition",
            "aggregation",
            "assignment",
            "association",
            "flow",
        ],
        "typical_usage_scenario": (
            "Use when analysing collaboration between internal and external "
            "actors and the services exchanged between them."
        ),
        "example_questions": [
            "Which external partners interact with which internal roles?",
            "What services are exchanged between actors?",
        ],
        "related_viewpoints": [
            "Organization",
            "Business Process",
        ],
    },
    {
        "standard_number": 3,
        "name": "Business Function",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows the main business functions of an organization and their "
            "relationships, often derived from business reference models."
        ),
        "purpose": (
            "Designing, deciding, and informing about the functional "
            "decomposition of the business and its service provision."
        ),
        "concerns": [
            "Functional decomposition",
            "Business capabilities",
            "Service delivery",
        ],
        "typical_stakeholders": [
            "Enterprise Architect",
            "Business Owner",
            "Business Analyst",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": False,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessFunction",
            "BusinessService",
            "BusinessRole",
            "BusinessObject",
        ],
        "allowed_relationship_types": [
            "composition",
            "aggregation",
            "assignment",
            "serving",
            "triggering",
            "flow",
            "access",
            "association",
        ],
        "typical_usage_scenario": (
            "Use when modelling the functional landscape of the enterprise, "
            "mapping business functions to responsible roles."
        ),
        "example_questions": [
            "What are the main business functions?",
            "Which roles are assigned to which functions?",
            "How do functions relate to services?",
        ],
        "related_viewpoints": [
            "Organization",
            "Business Process",
            "Application Usage",
        ],
    },
    {
        "standard_number": 4,
        "name": "Business Process",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows the high-level structure and composition of one or more "
            "business processes, the services they provide, and the data "
            "objects they access."
        ),
        "purpose": (
            "Designing, deciding, and informing about the orchestration of "
            "business processes and their dependencies."
        ),
        "concerns": [
            "Process orchestration",
            "Service dependencies",
            "Data access",
            "Process flows",
        ],
        "typical_stakeholders": [
            "Business Analyst",
            "Process Owner",
            "Enterprise Architect",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": False,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessProcess",
            "BusinessFunction",
            "BusinessInteraction",
            "BusinessEvent",
            "BusinessService",
            "BusinessRole",
            "BusinessActor",
            "BusinessObject",
            "Representation",
        ],
        "allowed_relationship_types": [
            "composition",
            "aggregation",
            "assignment",
            "triggering",
            "flow",
            "access",
            "serving",
            "association",
        ],
        "typical_usage_scenario": (
            "Use when designing or documenting end-to-end business processes "
            "and their event-driven triggers."
        ),
        "example_questions": [
            "What is the sequence of activities in this process?",
            "Which roles participate in each step?",
            "What data objects are accessed?",
        ],
        "related_viewpoints": [
            "Business Function",
            "Application Usage",
            "Service Realization",
        ],
    },
    {
        "standard_number": 5,
        "name": "Application Co-operation",
        "viewpoint_type": "layered",
        "description": (
            "Shows the co-operation between application components in terms "
            "of the services they provide and use, and the data flows between them."
        ),
        "purpose": (
            "Designing, deciding, and informing about application "
            "interactions, integration patterns, and data exchange."
        ),
        "concerns": [
            "Application integration",
            "Data exchange",
            "Service dependencies",
            "Interface contracts",
        ],
        "typical_stakeholders": [
            "Solution Architect",
            "Application Manager",
            "Enterprise Architect",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": False,
        "includes_application_layer": True,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "ApplicationComponent",
            "ApplicationInterface",
            "ApplicationService",
            "ApplicationFunction",
            "ApplicationInteraction",
            "ApplicationCollaboration",
            "DataObject",
        ],
        "allowed_relationship_types": [
            "serving",
            "composition",
            "aggregation",
            "flow",
            "triggering",
            "access",
            "association",
            "realization",
        ],
        "typical_usage_scenario": (
            "Use when analysing how applications interact, exchange data, "
            "and depend on each other's services."
        ),
        "example_questions": [
            "Which applications exchange data?",
            "What integration patterns are used?",
            "Which interfaces are exposed?",
        ],
        "related_viewpoints": [
            "Application Usage",
            "Technology Usage",
            "Information Structure",
        ],
    },
    {
        "standard_number": 6,
        "name": "Application Usage",
        "viewpoint_type": "layered",
        "description": (
            "Shows how applications are used by business processes and how "
            "they provide services that support those processes."
        ),
        "purpose": (
            "Designing, deciding, and informing about the relationship "
            "between business processes and the applications that support them."
        ),
        "concerns": [
            "Application use by business",
            "Service dependencies",
            "Business-IT alignment",
        ],
        "typical_stakeholders": [
            "Enterprise Architect",
            "Application Manager",
            "Business Analyst",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": True,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessProcess",
            "BusinessFunction",
            "BusinessInteraction",
            "BusinessEvent",
            "BusinessService",
            "BusinessRole",
            "ApplicationComponent",
            "ApplicationService",
            "ApplicationInterface",
            "DataObject",
        ],
        "allowed_relationship_types": [
            "serving",
            "realization",
            "assignment",
            "association",
            "access",
            "triggering",
            "flow",
        ],
        "typical_usage_scenario": (
            "Use when determining which applications support which business "
            "processes and assessing business-IT alignment."
        ),
        "example_questions": [
            "Which applications support the order-to-cash process?",
            "What business processes are impacted if this application fails?",
        ],
        "related_viewpoints": [
            "Business Process",
            "Application Co-operation",
        ],
    },
    {
        "standard_number": 7,
        "name": "Implementation and Deployment",
        "viewpoint_type": "layered",
        "description": (
            "Shows the realization of applications on infrastructure and "
            "the deployment of artifacts on technology nodes."
        ),
        "purpose": (
            "Designing, deciding, and informing about the physical "
            "deployment of applications onto infrastructure."
        ),
        "concerns": [
            "Deployment",
            "Infrastructure mapping",
            "Physical realization",
            "Run-time environment",
        ],
        "typical_stakeholders": [
            "IT Manager",
            "Infrastructure Architect",
            "Operations Manager",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": False,
        "includes_application_layer": True,
        "includes_technology_layer": True,
        "includes_physical_layer": True,
        "includes_motivation_layer": False,
        "includes_implementation_layer": True,
        "allowed_element_types": [
            "ApplicationComponent",
            "Artifact",
            "Node",
            "Device",
            "SystemSoftware",
            "TechnologyService",
            "CommunicationNetwork",
            "Path",
            "WorkPackage",
            "Deliverable",
        ],
        "allowed_relationship_types": [
            "realization",
            "assignment",
            "serving",
            "composition",
            "aggregation",
            "association",
        ],
        "typical_usage_scenario": (
            "Use when planning or documenting where applications are deployed "
            "and how infrastructure supports them."
        ),
        "example_questions": [
            "On which servers is this application deployed?",
            "Which artifacts run on which nodes?",
            "What is the network topology?",
        ],
        "related_viewpoints": [
            "Technology",
            "Technology Usage",
            "Application Co-operation",
        ],
    },
    {
        "standard_number": 8,
        "name": "Technology",
        "viewpoint_type": "layered",
        "description": (
            "Shows the structure and interconnection of technology "
            "infrastructure elements such as nodes, devices, and networks."
        ),
        "purpose": (
            "Designing, deciding, and informing about the technology "
            "infrastructure and its interconnections."
        ),
        "concerns": [
            "Infrastructure components",
            "Network topology",
            "Technology standards",
        ],
        "typical_stakeholders": [
            "Infrastructure Architect",
            "IT Manager",
            "Operations Manager",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": False,
        "includes_application_layer": False,
        "includes_technology_layer": True,
        "includes_physical_layer": True,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "Node",
            "Device",
            "SystemSoftware",
            "TechnologyInterface",
            "TechnologyService",
            "TechnologyFunction",
            "TechnologyInteraction",
            "TechnologyCollaboration",
            "CommunicationNetwork",
            "Path",
            "Artifact",
        ],
        "allowed_relationship_types": [
            "composition",
            "aggregation",
            "assignment",
            "serving",
            "realization",
            "access",
            "association",
            "flow",
        ],
        "typical_usage_scenario": (
            "Use when documenting the technology landscape, platform "
            "standards, or infrastructure capacity."
        ),
        "example_questions": [
            "What technology nodes exist in the environment?",
            "How are networks interconnected?",
            "Which system software runs on which devices?",
        ],
        "related_viewpoints": [
            "Implementation and Deployment",
            "Technology Usage",
        ],
    },
    {
        "standard_number": 9,
        "name": "Technology Usage",
        "viewpoint_type": "layered",
        "description": (
            "Shows how applications are supported by technology "
            "infrastructure: software and hardware that realise them."
        ),
        "purpose": (
            "Designing, deciding, and informing about the dependencies "
            "between applications and the technology that supports them."
        ),
        "concerns": [
            "Technology use by applications",
            "Infrastructure dependencies",
            "Platform selection",
        ],
        "typical_stakeholders": [
            "Enterprise Architect",
            "IT Manager",
            "Infrastructure Architect",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": False,
        "includes_application_layer": True,
        "includes_technology_layer": True,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "ApplicationComponent",
            "ApplicationFunction",
            "ApplicationService",
            "DataObject",
            "Artifact",
            "Node",
            "Device",
            "SystemSoftware",
            "TechnologyService",
            "CommunicationNetwork",
            "Path",
        ],
        "allowed_relationship_types": [
            "serving",
            "realization",
            "assignment",
            "association",
            "access",
            "composition",
            "aggregation",
        ],
        "typical_usage_scenario": (
            "Use when assessing which technology platforms underpin which "
            "applications and identifying infrastructure risks."
        ),
        "example_questions": [
            "Which technology supports this application?",
            "What happens to applications if this infrastructure fails?",
        ],
        "related_viewpoints": [
            "Technology",
            "Implementation and Deployment",
            "Application Co-operation",
        ],
    },
    {
        "standard_number": 10,
        "name": "Information Structure",
        "viewpoint_type": "relation",
        "description": (
            "Shows the structure of information used in the enterprise, "
            "in terms of data types, data objects, and their relationships."
        ),
        "purpose": (
            "Designing, deciding, and informing about the structure and "
            "dependencies of the enterprise's information assets."
        ),
        "concerns": [
            "Data entities",
            "Information structure",
            "Data relationships",
            "Data ownership",
        ],
        "typical_stakeholders": [
            "Data Architect",
            "Enterprise Architect",
            "Business Analyst",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": True,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessObject",
            "Representation",
            "DataObject",
            "Artifact",
        ],
        "allowed_relationship_types": [
            "composition",
            "aggregation",
            "association",
            "specialization",
            "realization",
            "access",
        ],
        "typical_usage_scenario": (
            "Use when defining or reviewing the enterprise data model and "
            "information ownership."
        ),
        "example_questions": [
            "What are the key data entities?",
            "How are business objects related to data objects?",
            "Which data objects are shared across applications?",
        ],
        "related_viewpoints": [
            "Application Co-operation",
            "Application Usage",
        ],
    },
    {
        "standard_number": 11,
        "name": "Service Realization",
        "viewpoint_type": "relation",
        "description": (
            "Shows how services are realised by the underlying behaviour "
            "and active structure elements across layers."
        ),
        "purpose": (
            "Designing, deciding, and informing about the realization chain "
            "from services down to processes and components."
        ),
        "concerns": [
            "Service delivery",
            "Service realization",
            "Cross-layer traceability",
        ],
        "typical_stakeholders": [
            "Enterprise Architect",
            "Service Manager",
            "Solution Architect",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": True,
        "includes_technology_layer": True,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessService",
            "BusinessProcess",
            "BusinessFunction",
            "BusinessRole",
            "ApplicationService",
            "ApplicationComponent",
            "ApplicationFunction",
            "TechnologyService",
            "Node",
            "DataObject",
        ],
        "allowed_relationship_types": [
            "serving",
            "realization",
            "assignment",
            "triggering",
            "flow",
            "association",
        ],
        "typical_usage_scenario": (
            "Use when tracing how end-user services are realised through "
            "the full technology stack."
        ),
        "example_questions": [
            "Which processes and components realize this service?",
            "What is the full realization chain for a customer-facing service?",
        ],
        "related_viewpoints": [
            "Business Process",
            "Application Usage",
            "Technology Usage",
        ],
    },
    {
        "standard_number": 12,
        "name": "Layered",
        "viewpoint_type": "layered",
        "description": (
            "Provides a comprehensive overview across all architecture "
            "layers: business, application, and technology."
        ),
        "purpose": (
            "Providing a holistic, cross-layer overview of the enterprise "
            "architecture and its dependencies."
        ),
        "concerns": [
            "Enterprise overview",
            "Cross-layer dependencies",
            "Full architecture landscape",
        ],
        "typical_stakeholders": [
            "CIO",
            "Enterprise Architect",
            "Board Member",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": True,
        "includes_technology_layer": True,
        "includes_physical_layer": False,
        "includes_motivation_layer": False,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessActor",
            "BusinessRole",
            "BusinessProcess",
            "BusinessFunction",
            "BusinessService",
            "BusinessObject",
            "ApplicationComponent",
            "ApplicationService",
            "ApplicationFunction",
            "ApplicationInterface",
            "DataObject",
            "Node",
            "Device",
            "SystemSoftware",
            "TechnologyService",
            "Artifact",
            "CommunicationNetwork",
            "Path",
        ],
        "allowed_relationship_types": [
            "serving",
            "realization",
            "assignment",
            "association",
            "composition",
            "aggregation",
            "access",
            "triggering",
            "flow",
        ],
        "typical_usage_scenario": (
            "Use for executive-level overviews and cross-layer impact "
            "analysis of proposed changes."
        ),
        "example_questions": [
            "What does the full architecture look like end to end?",
            "Which technology changes impact business processes?",
        ],
        "related_viewpoints": [
            "Service Realization",
            "Application Usage",
            "Technology Usage",
        ],
    },
    {
        "standard_number": 13,
        "name": "Motivation",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows the motivations behind the architecture: stakeholders, "
            "drivers, goals, principles, requirements, and constraints."
        ),
        "purpose": (
            "Designing, deciding, and informing about the motivations that "
            "drive architecture decisions and change."
        ),
        "concerns": [
            "Stakeholder concerns",
            "Drivers",
            "Goals",
            "Principles",
            "Requirements",
        ],
        "typical_stakeholders": [
            "CIO",
            "Business Owner",
            "Enterprise Architect",
            "Strategy Officer",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": False,
        "includes_application_layer": False,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": True,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "Stakeholder",
            "Driver",
            "Assessment",
            "Goal",
            "Outcome",
            "Principle",
            "Requirement",
            "Constraint",
            "Value",
            "Meaning",
        ],
        "allowed_relationship_types": [
            "association",
            "influence",
            "realization",
            "aggregation",
            "composition",
            "specialization",
            "assignment",
        ],
        "typical_usage_scenario": (
            "Use when documenting WHY the architecture is shaped the way "
            "it is, linking goals to requirements and constraints."
        ),
        "example_questions": [
            "What are the key drivers for change?",
            "Which goals does this requirement support?",
            "What constraints apply to this initiative?",
        ],
        "related_viewpoints": [
            "Strategy",
            "Layered",
        ],
    },
    {
        "standard_number": 14,
        "name": "Strategy",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows the strategic direction of the enterprise: capabilities, "
            "resources, value streams, and courses of action."
        ),
        "purpose": (
            "Designing, deciding, and informing about the strategic "
            "capabilities and how they will be achieved."
        ),
        "concerns": [
            "Strategic goals",
            "Capabilities",
            "Value streams",
            "Courses of action",
            "Resource allocation",
        ],
        "typical_stakeholders": [
            "CIO",
            "CEO",
            "Strategy Officer",
            "Enterprise Architect",
        ],
        "includes_strategy_layer": True,
        "includes_business_layer": False,
        "includes_application_layer": False,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": True,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "Resource",
            "Capability",
            "ValueStream",
            "CourseOfAction",
            "Goal",
            "Outcome",
            "Driver",
        ],
        "allowed_relationship_types": [
            "realization",
            "assignment",
            "aggregation",
            "composition",
            "association",
            "influence",
            "serving",
        ],
        "typical_usage_scenario": (
            "Use when defining or reviewing strategic capabilities and "
            "mapping courses of action to goals."
        ),
        "example_questions": [
            "What capabilities does the organization need?",
            "Which value streams deliver on strategic goals?",
            "What resources are required for this course of action?",
        ],
        "related_viewpoints": [
            "Motivation",
            "Business Function",
        ],
    },
    {
        "standard_number": 15,
        "name": "Security",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows trust boundaries, authentication flows, data "
            "classification, and security zones across architecture layers."
        ),
        "purpose": (
            "Designing and deciding on security architecture, reviewing "
            "compliance with security policies."
        ),
        "concerns": [
            "Trust boundaries",
            "Authentication",
            "Authorization",
            "Data classification",
            "Security zones",
        ],
        "typical_stakeholders": [
            "Security Architect",
            "CISO",
            "Compliance Officer",
            "Enterprise Architect",
        ],
        "includes_strategy_layer": False,
        "includes_business_layer": True,
        "includes_application_layer": True,
        "includes_technology_layer": True,
        "includes_physical_layer": False,
        "includes_motivation_layer": True,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessActor",
            "BusinessRole",
            "BusinessProcess",
            "ApplicationComponent",
            "ApplicationService",
            "ApplicationInterface",
            "Node",
            "Device",
            "SystemSoftware",
            "CommunicationNetwork",
            "Constraint",
            "Requirement",
            "Goal",
        ],
        "allowed_relationship_types": [
            "serving",
            "realization",
            "assignment",
            "association",
            "access",
            "flow",
            "influence",
            "composition",
            "aggregation",
        ],
        "typical_usage_scenario": (
            "Use when designing or reviewing security architecture, "
            "mapping trust boundaries, and assessing compliance posture."
        ),
        "example_questions": [
            "Where are the trust boundaries in this architecture?",
            "Which components handle authentication and authorization?",
            "How is sensitive data classified and protected across layers?",
        ],
        "related_viewpoints": [
            "Layered",
            "Motivation",
            "Technology",
        ],
    },
    {
        "standard_number": 16,
        "name": "Requirements Realization",
        "viewpoint_type": "relation",
        "description": (
            "Shows how requirements are realized by architecture elements "
            "across all layers, and how work packages implement those elements."
        ),
        "purpose": (
            "Tracing requirements to implementation, validating "
            "completeness of architecture."
        ),
        "concerns": [
            "Requirements traceability",
            "Implementation completeness",
            "Gap identification",
        ],
        "typical_stakeholders": [
            "Solution Architect",
            "Enterprise Architect",
            "Governance Board",
            "Project Manager",
        ],
        "includes_strategy_layer": True,
        "includes_business_layer": True,
        "includes_application_layer": True,
        "includes_technology_layer": True,
        "includes_physical_layer": False,
        "includes_motivation_layer": True,
        "includes_implementation_layer": True,
        "allowed_element_types": [
            "Requirement",
            "Constraint",
            "Goal",
            "Outcome",
            "BusinessProcess",
            "BusinessFunction",
            "BusinessService",
            "ApplicationComponent",
            "ApplicationService",
            "ApplicationFunction",
            "Node",
            "SystemSoftware",
            "TechnologyService",
            "WorkPackage",
            "Deliverable",
            "Plateau",
            "Gap",
        ],
        "allowed_relationship_types": [
            "realization",
            "assignment",
            "association",
            "aggregation",
            "composition",
            "influence",
            "serving",
            "triggering",
        ],
        "typical_usage_scenario": (
            "Use when tracing requirements through architecture layers to "
            "verify completeness and identify gaps in implementation."
        ),
        "example_questions": [
            "Which architecture elements realize this requirement?",
            "Are there requirements not yet covered by any element?",
            "Which work packages deliver on which requirements?",
        ],
        "related_viewpoints": [
            "Motivation",
            "Service Realization",
            "Implementation and Deployment",
        ],
    },
    # Additional standard viewpoints from ArchiMate 3.2 specification
    {
        "standard_number": 17,
        "name": "Introductory",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows a general, high-level overview of the enterprise architecture, "
            "suitable for introducing the architecture to new stakeholders."
        ),
        "purpose": (
            "Providing an accessible introduction to the enterprise architecture "
            "for stakeholders unfamiliar with ArchiMate modeling."
        ),
        "concerns": [
            "Architecture overview",
            "Stakeholder communication",
            "High-level understanding",
        ],
        "typical_stakeholders": [
            "Business Executive",
            "Board Member",
            "New Team Member",
            "External Stakeholder",
        ],
        "includes_strategy_layer": True,
        "includes_business_layer": True,
        "includes_application_layer": True,
        "includes_technology_layer": True,
        "includes_physical_layer": False,
        "includes_motivation_layer": True,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "BusinessActor",
            "BusinessProcess",
            "BusinessService",
            "ApplicationComponent",
            "ApplicationService",
            "Node",
            "Goal",
            "Driver",
            "Capability",
        ],
        "allowed_relationship_types": [
            "serving",
            "realization",
            "assignment",
            "association",
            "composition",
            "aggregation",
        ],
        "typical_usage_scenario": (
            "Use when presenting architecture to executives or new stakeholders "
            "who need a simplified, high-level view."
        ),
        "example_questions": [
            "What does our architecture look like at a high level?",
            "How do our main systems support business goals?",
        ],
        "related_viewpoints": [
            "Layered",
            "Motivation",
        ],
    },
    {
        "standard_number": 18,
        "name": "Goal Realization",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows how goals are realized by outcomes, and how outcomes "
            "are achieved through principles, requirements, and constraints."
        ),
        "purpose": (
            "Designing, deciding, and informing about goal achievement "
            "and the means to realize strategic objectives."
        ),
        "concerns": [
            "Goal achievement",
            "Strategic alignment",
            "Outcome delivery",
            "Success measurement",
        ],
        "typical_stakeholders": [
            "Strategy Manager",
            "Enterprise Architect",
            "Program Manager",
            "Business Executive",
        ],
        "includes_strategy_layer": True,
        "includes_business_layer": False,
        "includes_application_layer": False,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": True,
        "includes_implementation_layer": False,
        "allowed_element_types": [
            "Goal",
            "Outcome",
            "Principle",
            "Requirement",
            "Constraint",
            "Driver",
            "Assessment",
            "Value",
        ],
        "allowed_relationship_types": [
            "realization",
            "influence",
            "association",
            "aggregation",
            "composition",
        ],
        "typical_usage_scenario": (
            "Use when planning how to achieve strategic goals and "
            "measuring progress towards desired outcomes."
        ),
        "example_questions": [
            "How will we achieve this strategic goal?",
            "What outcomes indicate goal success?",
            "Which principles guide goal realization?",
        ],
        "related_viewpoints": [
            "Motivation",
            "Strategy",
        ],
    },
    {
        "standard_number": 19,
        "name": "Course of Action",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows courses of action and how they contribute to the "
            "realization of goals through work packages and deliverables."
        ),
        "purpose": (
            "Planning and tracking strategic initiatives and their "
            "contribution to organizational goals."
        ),
        "concerns": [
            "Strategic initiatives",
            "Action planning",
            "Goal contribution",
            "Initiative coordination",
        ],
        "typical_stakeholders": [
            "Strategy Manager",
            "Program Manager",
            "Enterprise Architect",
            "Portfolio Manager",
        ],
        "includes_strategy_layer": True,
        "includes_business_layer": False,
        "includes_application_layer": False,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": True,
        "includes_implementation_layer": True,
        "allowed_element_types": [
            "CourseOfAction",
            "Goal",
            "Outcome",
            "WorkPackage",
            "Deliverable",
            "Capability",
            "Resource",
        ],
        "allowed_relationship_types": [
            "realization",
            "association",
            "assignment",
            "triggering",
            "composition",
            "aggregation",
        ],
        "typical_usage_scenario": (
            "Use when planning strategic initiatives and tracking "
            "their progress towards goal achievement."
        ),
        "example_questions": [
            "Which initiatives support this goal?",
            "What work packages deliver this course of action?",
            "How do initiatives coordinate with each other?",
        ],
        "related_viewpoints": [
            "Strategy",
            "Project",
        ],
    },
    {
        "standard_number": 20,
        "name": "Program & Portfolio Management",
        "viewpoint_type": "stakeholder",
        "description": (
            "Shows programs, projects, and their relationships to "
            "strategic goals and business capabilities."
        ),
        "purpose": (
            "Managing portfolios of projects and programs to ensure "
            "strategic alignment and resource optimization."
        ),
        "concerns": [
            "Portfolio alignment",
            "Program coordination",
            "Resource allocation",
            "Strategic delivery",
        ],
        "typical_stakeholders": [
            "Portfolio Manager",
            "Program Manager",
            "PMO",
            "Enterprise Architect",
        ],
        "includes_strategy_layer": True,
        "includes_business_layer": True,
        "includes_application_layer": False,
        "includes_technology_layer": False,
        "includes_physical_layer": False,
        "includes_motivation_layer": True,
        "includes_implementation_layer": True,
        "allowed_element_types": [
            "WorkPackage",
            "Deliverable",
            "Goal",
            "Outcome",
            "Capability",
            "BusinessFunction",
            "CourseOfAction",
        ],
        "allowed_relationship_types": [
            "realization",
            "assignment",
            "association",
            "composition",
            "aggregation",
            "triggering",
        ],
        "typical_usage_scenario": (
            "Use when managing project portfolios and ensuring "
            "programs deliver strategic value."
        ),
        "example_questions": [
            "Which projects support which capabilities?",
            "How do programs align with strategic goals?",
            "What is the portfolio's strategic impact?",
        ],
        "related_viewpoints": [
            "Project",
            "Strategy",
        ],
    },
]


def _apply_viewpoint_fields(target, vp_data):
    """Apply all viewpoint data fields to a model instance (create or update)."""
    target.viewpoint_type = vp_data["viewpoint_type"]
    target.description = vp_data.get("description", "")
    target.purpose = vp_data["purpose"]
    target.concerns = vp_data["concerns"]
    target.typical_stakeholders = vp_data["typical_stakeholders"]
    target.allowed_element_types = vp_data["allowed_element_types"]
    target.allowed_relationship_types = vp_data["allowed_relationship_types"]
    target.includes_strategy_layer = vp_data.get("includes_strategy_layer", False)
    target.includes_business_layer = vp_data.get("includes_business_layer", False)
    target.includes_application_layer = vp_data.get("includes_application_layer", False)
    target.includes_technology_layer = vp_data.get("includes_technology_layer", False)
    target.includes_physical_layer = vp_data.get("includes_physical_layer", False)
    target.includes_motivation_layer = vp_data.get("includes_motivation_layer", False)
    target.includes_implementation_layer = vp_data.get("includes_implementation_layer", False)
    target.typical_usage_scenario = vp_data.get("typical_usage_scenario")
    target.example_questions = vp_data.get("example_questions")
    target.related_viewpoints = vp_data.get("related_viewpoints")
    target.standard_number = vp_data.get("standard_number")
    target.archimate_version = "3.2"
    target.is_standard = True


def seed_viewpoints(org_id=None):
    """Upsert the 20 ArchiMate viewpoints (18 standard + 2 custom). Returns (created, updated) counts.

    ``org_id`` names the organization new catalogue rows belong to. Leave it
    unset only when the tenant column's own single-organization fallback can
    resolve one (see ``app.models.mixins.core._default_org_id``); with
    several organizations already in the database, pass the id explicitly,
    the same way ``backfill-layer-tenancy`` requires ``--org-id`` rather
    than guessing.
    """
    try:
        from app.models.archimate_viewpoint import ArchiMateViewpoint
    except ImportError:
        logger.error("Cannot import ArchiMateViewpoint - skipping seed.")
        return 0, 0

    created = 0
    updated = 0

    for vp_data in _STANDARD_VIEWPOINTS:
        existing = ArchiMateViewpoint.query.filter_by(name=vp_data["name"]).first()
        if existing:
            _apply_viewpoint_fields(existing, vp_data)
            updated += 1
        else:
            vp = ArchiMateViewpoint(name=vp_data["name"])
            if org_id is not None:
                vp.organization_id = org_id
            _apply_viewpoint_fields(vp, vp_data)
            db.session.add(vp)
            created += 1

    db.session.commit()
    logger.info("QA-CMP-004: Seeded viewpoints - created=%d, updated=%d (total: %d)", created, updated, len(_STANDARD_VIEWPOINTS))

    seed_canvas_templates(org_id=org_id)
    seed_property_templates()

    return created, updated


def _canvas_viewpoint_row_data(tpl):
    """Shape one CANVAS_TEMPLATES entry into the fields _apply_viewpoint_fields
    expects."""
    from app.config.archimate_viewpoints import VIEWPOINTS

    element_types = sorted({t for zone in tpl["zones"] for t in zone["element_types"]})
    layers_included = {
        VIEWPOINTS[vp_key].layer for vp_key in tpl["projects"] if vp_key in VIEWPOINTS
    }
    return {
        "standard_number": None,
        "viewpoint_type": "canvas",
        "description": (
            f"Business-language viewpoint over the one model: {tpl['name']}, "
            "pre-populated from the tenant's existing elements."
        ),
        "purpose": f"Show the tenant's model as a {tpl['name']} without a notation lesson.",
        "concerns": [zone["label"] for zone in tpl["zones"]],
        "typical_stakeholders": ["Founder", "Sponsor"],
        "allowed_element_types": element_types,
        "allowed_relationship_types": [],
        "includes_strategy_layer": "strategy" in layers_included,
        "includes_business_layer": "business" in layers_included,
        "includes_application_layer": "application" in layers_included,
        "includes_technology_layer": "technology" in layers_included,
        "includes_physical_layer": "physical" in layers_included,
        "includes_motivation_layer": "motivation" in layers_included,
        "includes_implementation_layer": "implementation" in layers_included,
        "typical_usage_scenario": None,
        "example_questions": None,
        "related_viewpoints": None,
    }


def seed_canvas_templates(org_id=None):
    """Upsert (a) one ArchiMateViewpoint catalogue row per CANVAS_TEMPLATES
    entry — ``viewpoint_type="canvas"``, ``is_standard=False`` — so the
    Composer catalogue lists Lean Canvas, Business Model Canvas and business
    case without a fourth catalogue, and (b) one ``profile`` AcmPropertyTemplate
    row per element type the templates use, with ``enum_options`` set to every
    profile value that type takes across the three templates
    (CANVAS_PROFILE_OPTIONS_BY_TYPE). Idempotent — upsert by name for (a), by
    ``(archimate_type, property_key)`` for (b); running twice changes neither.

    ``org_id`` names the organization new viewpoint rows belong to; see
    ``seed_viewpoints`` for when it can be left unset.
    Returns
    ``(viewpoints_created, viewpoints_updated, profiles_created, profiles_updated)``.
    """
    from app.config.archimate_viewpoints import CANVAS_PROFILE_OPTIONS_BY_TYPE, CANVAS_TEMPLATES
    from app.models.acm_property_template import AcmPropertyTemplate
    from app.models.archimate_viewpoint import ArchiMateViewpoint

    vp_created = 0
    vp_updated = 0
    for tpl in CANVAS_TEMPLATES.values():
        data = _canvas_viewpoint_row_data(tpl)
        existing = ArchiMateViewpoint.query.filter_by(name=tpl["name"]).first()
        if existing:
            _apply_viewpoint_fields(existing, data)
            existing.is_standard = False  # canvas rows are not standard viewpoints
            vp_updated += 1
        else:
            vp = ArchiMateViewpoint(name=tpl["name"])
            if org_id is not None:
                vp.organization_id = org_id
            _apply_viewpoint_fields(vp, data)
            vp.is_standard = False
            db.session.add(vp)
            vp_created += 1

    profile_created = 0
    profile_updated = 0
    for archimate_type, options in sorted(CANVAS_PROFILE_OPTIONS_BY_TYPE.items()):
        # Only the shared platform row: an organisation's own definition with
        # the same key is that organisation's, never updated by the seed.
        existing = AcmPropertyTemplate.query.filter_by(
            archimate_type=archimate_type, property_key="profile",
        ).filter(AcmPropertyTemplate.organization_id.is_(None)).first()
        if existing:
            existing.display_name = "Profile"
            existing.property_type = "enum"
            existing.enum_options = options
            existing.required_for_tier = "standard"
            profile_updated += 1
        else:
            db.session.add(AcmPropertyTemplate(
                archimate_type=archimate_type, property_key="profile",
                display_name="Profile", property_type="enum",
                enum_options=options, required_for_tier="standard",
            ))
            profile_created += 1

    db.session.commit()
    logger.info(
        "Canvas templates seeded - viewpoints created=%d updated=%d; "
        "profile templates created=%d updated=%d",
        vp_created, vp_updated, profile_created, profile_updated,
    )
    return vp_created, vp_updated, profile_created, profile_updated


def seed_property_templates():
    """Upsert typed property templates registered for the journey property writer."""
    from app.config.property_templates import PROPERTY_TEMPLATES
    from app.models.acm_property_template import AcmPropertyTemplate

    created = 0
    updated = 0
    for row in PROPERTY_TEMPLATES:
        stored = {k: v for k, v in row.items() if k != "unit"}
        existing = AcmPropertyTemplate.query.filter_by(
            archimate_type=stored["archimate_type"],
            property_key=stored["property_key"],
        ).first()
        if existing:
            for field, value in stored.items():
                setattr(existing, field, value)
            updated += 1
            continue

        db.session.add(AcmPropertyTemplate(**stored))
        created += 1

    db.session.commit()
    logger.info("Property templates seeded - created=%d updated=%d", created, updated)
    return created, updated


def _resolve_seed_org_id():
    """With exactly one organization, seed into it; with several, refuse
    because standard viewpoints must not become the property of one organisation.
    """
    from app.models.organization import Organization

    orgs = Organization.query.order_by(Organization.id).all()
    if len(orgs) == 1:
        return orgs[0].id
    if not orgs:
        return None
    listing = ", ".join(f"{o.id}={o.name}" for o in orgs)
    raise click.ClickException(
        f"{len(orgs)} organisations exist ({listing}). This command seeds "
        "only a single-organisation database."
    )


@click.command("seed-viewpoints")
@with_appcontext
def seed_viewpoints_command():
    """Seed the 25 ArchiMate viewpoints (all standard, idempotent), the
    business-language canvas templates and their `profile` property rows."""
    resolved_org_id = _resolve_seed_org_id()
    created, updated = seed_viewpoints(org_id=resolved_org_id)
    click.echo(f"Viewpoints seeded: {created} created, {updated} updated.")


def init_app(app):
    """Register CLI command with app."""
    app.cli.add_command(seed_viewpoints_command)

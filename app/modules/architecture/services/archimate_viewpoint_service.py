"""
ArchiMate 3.2 Viewpoint Service

Implements the 23 standard ArchiMate viewpoints and provides filtering/generation capabilities.
Viewpoints are perspectives on the architecture tailored to specific stakeholders and concerns.
"""

from typing import Dict, List, Optional, Set

from app.models import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
from app.models.archimate_element_types import base_type


class ArchiMateViewpointService:
    """
    Service for generating and managing ArchiMate viewpoints.

    ArchiMate 3.2 defines 23 standard viewpoints organized by aspect:
    - Organization viewpoints
    - Information viewpoints
    - Process & Behavior viewpoints
    - Product viewpoints
    - Application viewpoints
    - Technology viewpoints
    - Motivation viewpoints
    - Strategy viewpoints
    - Implementation & Migration viewpoints
    """

    # Standard ArchiMate 3.2 Viewpoints
    STANDARD_VIEWPOINTS = {
        # Organization viewpoints
        "organization": {
            "name": "Organization Viewpoint",
            "aspect": "organization",
            "purpose": "Modeling the (internal) organization of a company, department, network of partners, or other organizational entity",
            "layers": ["business"],
            "element_types": [
                "BusinessActor",
                "BusinessRole",
                "BusinessCollaboration",
                "BusinessInterface",
                "Location",
                "BusinessObject",
            ],
            "relationship_types": ["Composition", "Aggregation", "Assignment", "Association"],
            "stakeholders": [
                "Enterprise architect",
                "Process architect",
                "Organizational architect",
                "Manager",
                "CIO",
            ],
        },
        # Business Process viewpoints
        "business_process_cooperation": {
            "name": "Business Process Cooperation Viewpoint",
            "aspect": "process",
            "purpose": "Showing the relationships of one or more business processes with each other and/or with their environment",
            "layers": ["business"],
            "element_types": [
                "BusinessProcess",
                "BusinessInteraction",
                "BusinessEvent",
                "BusinessService",
                "BusinessActor",
                "BusinessRole",
                "BusinessCollaboration",
                "BusinessInterface",
                "BusinessObject",
                "Representation",
                "Product",
                "Contract",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Triggering",
                "Flow",
                "Access",
            ],
            "stakeholders": [
                "Process architect",
                "Business process manager",
                "Operational manager",
            ],
        },
        "business_function": {
            "name": "Business Function Viewpoint",
            "aspect": "process",
            "purpose": "Showing the main business functions and their relationships",
            "layers": ["business"],
            "element_types": [
                "BusinessFunction",
                "BusinessService",
                "BusinessActor",
                "BusinessRole",
                "BusinessCollaboration",
                "Location",
                "BusinessObject",
                "BusinessProcess",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Flow",
                "Triggering",
            ],
            "stakeholders": [
                "Process architect",
                "Enterprise architect",
                "Business process manager",
            ],
        },
        # Product viewpoints
        "product": {
            "name": "Product Viewpoint",
            "aspect": "product",
            "purpose": "Showing the value that products offer to customers or other external parties",
            "layers": ["business"],
            "element_types": [
                "Product",
                "BusinessService",
                "Value",
                "Stakeholder",
                "BusinessActor",
                "BusinessRole",
                "Contract",
                "BusinessObject",
                "Representation",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Association",
                "Realization",
                "Access",
                "Influence",
            ],
            "stakeholders": ["Product manager", "Marketing manager", "Business manager", "CIO"],
        },
        # Application viewpoints
        "application_cooperation": {
            "name": "Application Cooperation Viewpoint",
            "aspect": "application",
            "purpose": "Describing the relationships between application components in terms of information flows",
            "layers": ["application"],
            "element_types": [
                "ApplicationComponent",
                "ApplicationCollaboration",
                "ApplicationInterface",
                "ApplicationFunction",
                "ApplicationInteraction",
                "ApplicationService",
                "DataObject",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Serving",
                "Flow",
                "Access",
            ],
            "stakeholders": [
                "Application architect",
                "Enterprise architect",
                "Operational manager",
            ],
        },
        "application_structure": {
            "name": "Application Structure Viewpoint",
            "aspect": "application",
            "purpose": "Showing the structure of one or more applications or components",
            "layers": ["application"],
            "element_types": [
                "ApplicationComponent",
                "ApplicationCollaboration",
                "ApplicationInterface",
                "DataObject",
                "ApplicationFunction",
                "ApplicationInteraction",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Access",
            ],
            "stakeholders": ["Application architect", "Application developer"],
        },
        "application_usage": {
            "name": "Application Usage Viewpoint",
            "aspect": "application",
            "purpose": "Showing how applications are used to support business processes",
            "layers": ["business", "application"],
            "element_types": [
                "BusinessProcess",
                "BusinessFunction",
                "BusinessInteraction",
                "BusinessEvent",
                "BusinessService",
                "BusinessRole",
                "BusinessActor",
                "ApplicationService",
                "ApplicationComponent",
                "ApplicationInterface",
                "DataObject",
                "BusinessObject",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Serving",
                "Triggering",
                "Flow",
                "Access",
            ],
            "stakeholders": [
                "Application architect",
                "Enterprise architect",
                "Process architect",
                "Business manager",
            ],
        },
        # Information viewpoints
        "information_structure": {
            "name": "Information Structure Viewpoint",
            "aspect": "information",
            "purpose": "Showing the structure of the information used in the enterprise",
            "layers": ["business", "application"],
            "element_types": [
                "BusinessObject",
                "Representation",
                "Meaning",
                "DataObject",
                "ApplicationService",
                "ApplicationInterface",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Specialization",
                "Association",
                "Access",
                "Realization",
            ],
            "stakeholders": ["Information architect", "Data architect", "Application architect"],
        },
        # Technology viewpoints
        "technology": {
            "name": "Technology Viewpoint",
            "aspect": "technology",
            "purpose": "Showing the technology infrastructure that supports application components",
            "layers": ["technology"],
            "element_types": [
                "Node",
                "Device",
                "SystemSoftware",
                "TechnologyCollaboration",
                "TechnologyInterface",
                "Path",
                "CommunicationNetwork",
                "TechnologyFunction",
                "TechnologyProcess",
                "TechnologyInteraction",
                "TechnologyService",
                "Artifact",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Serving",
                "Access",
            ],
            "stakeholders": [
                "Infrastructure architect",
                "Technical architect",
                "Operational manager",
            ],
        },
        "technology_usage": {
            "name": "Technology Usage Viewpoint",
            "aspect": "technology",
            "purpose": "Showing how applications are supported by the technology infrastructure",
            "layers": ["application", "technology"],
            "element_types": [
                "ApplicationComponent",
                "ApplicationCollaboration",
                "ApplicationInterface",
                "DataObject",
                "Artifact",
                "Node",
                "Device",
                "SystemSoftware",
                "TechnologyService",
                "TechnologyInterface",
                "Path",
                "CommunicationNetwork",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Serving",
                "Access",
            ],
            "stakeholders": [
                "Application architect",
                "Infrastructure architect",
                "Operational manager",
            ],
        },
        # Layered viewpoint (cross-layer)
        "layered": {
            "name": "Layered Viewpoint",
            "aspect": "layered",
            "purpose": "Showing multiple layers and their relationships in a holistic way",
            "layers": [
                "motivation",
                "strategy",
                "business",
                "application",
                "technology",
                "physical",
                "implementation",
            ],
            "element_types": None,  # All element types
            "relationship_types": None,  # All relationship types
            "stakeholders": ["Enterprise architect", "CIO", "CTO", "Senior management"],
        },
        # Motivation viewpoints
        "stakeholder": {
            "name": "Stakeholder Viewpoint",
            "aspect": "motivation",
            "purpose": "Modeling stakeholders, their interests, and the way these are addressed by goals and requirements",
            "layers": ["motivation"],
            "element_types": [
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
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Realization",
                "Influence",
                "Association",
            ],
            "stakeholders": [
                "Enterprise architect",
                "Business manager",
                "Business analyst",
                "Policy maker",
            ],
        },
        "goal_realization": {
            "name": "Goal Realization Viewpoint",
            "aspect": "motivation",
            "purpose": "Showing how goals are realized by core elements",
            "layers": ["motivation", "business", "application", "technology"],
            "element_types": [
                "Goal",
                "Outcome",
                "Principle",
                "Requirement",
                "BusinessProcess",
                "BusinessFunction",
                "BusinessService",
                "ApplicationService",
                "ApplicationComponent",
                "TechnologyService",
            ],
            "relationship_types": ["Realization", "Influence", "Association"],
            "stakeholders": ["Enterprise architect", "Business manager", "Senior management"],
        },
        "requirements_realization": {
            "name": "Requirements Realization Viewpoint",
            "aspect": "motivation",
            "purpose": "Showing how requirements are realized by core elements",
            "layers": ["motivation", "business", "application", "technology"],
            "element_types": [
                "Requirement",
                "Constraint",
                "BusinessProcess",
                "BusinessFunction",
                "BusinessService",
                "ApplicationService",
                "ApplicationComponent",
                "ApplicationInterface",
                "TechnologyService",
                "Node",
                "Device",
            ],
            "relationship_types": ["Realization", "Influence", "Association", "Serving"],
            "stakeholders": ["Enterprise architect", "Business analyst", "Requirements engineer"],
        },
        "motivation": {
            "name": "Motivation Viewpoint",
            "aspect": "motivation",
            "purpose": "Designing or deciding on strategy, showing motivation behind core elements",
            "layers": ["motivation"],
            "element_types": [
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
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Realization",
                "Influence",
                "Association",
            ],
            "stakeholders": ["Enterprise architect", "Strategic management", "Policy maker"],
        },
        # Strategy viewpoints
        "strategy": {
            "name": "Strategy Viewpoint",
            "aspect": "strategy",
            "purpose": "Designing strategy, showing strategic elements and their relationships",
            "layers": ["strategy", "business"],
            "element_types": [
                "Resource",
                "Capability",
                "ValueStream",
                "CourseOfAction",
                "BusinessActor",
                "BusinessRole",
                "BusinessCollaboration",
                "Product",
                "Contract",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Realization",
                "Assignment",
                "Association",
                "Serving",
            ],
            "stakeholders": ["Enterprise architect", "Strategic management", "CIO"],
        },
        "capability_map": {
            "name": "Capability Map Viewpoint",
            "aspect": "strategy",
            "purpose": "Planning transformation showing capabilities and their relationships",
            "layers": ["strategy", "business"],
            "element_types": [
                "Capability",
                "Resource",
                "BusinessService",
                "BusinessFunction",
                "BusinessProcess",
                "BusinessActor",
                "BusinessRole",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Realization",
                "Association",
                "Serving",
            ],
            "stakeholders": ["Enterprise architect", "Business manager", "Strategic planner"],
        },
        # Implementation & Migration viewpoints
        "implementation_migration": {
            "name": "Implementation and Migration Viewpoint",
            "aspect": "implementation",
            "purpose": "Designing a migration plan, showing work packages and deliverables",
            "layers": ["implementation", "business", "application", "technology"],
            "element_types": [
                "WorkPackage",
                "Deliverable",
                "ImplementationEvent",
                "Plateau",
                "Gap",
                "BusinessActor",
                "BusinessRole",
                "ApplicationComponent",
                "Node",
                "Device",
                "SystemSoftware",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Realization",
                "Assignment",
                "Association",
                "Triggering",
                "Flow",
            ],
            "stakeholders": ["Enterprise architect", "Project manager", "Program manager"],
        },
        "project": {
            "name": "Project Viewpoint",
            "aspect": "implementation",
            "purpose": "Managing large programs and projects and its associated work packages",
            "layers": ["implementation"],
            "element_types": [
                "WorkPackage",
                "Deliverable",
                "ImplementationEvent",
                "BusinessActor",
                "BusinessRole",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Triggering",
                "Flow",
            ],
            "stakeholders": ["Project manager", "Program manager", "PMO"],
        },
        # Physical viewpoints
        "physical": {
            "name": "Physical Viewpoint",
            "aspect": "physical",
            "purpose": "Showing physical elements and their relationships",
            "layers": ["physical", "technology"],
            "element_types": [
                "Equipment",
                "Facility",
                "DistributionNetwork",
                "Material",
                "Device",
                "Node",
                "SystemSoftware",
                "Artifact",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Association",
            ],
            "stakeholders": ["Infrastructure architect", "Facility manager", "Operations manager"],
        },
        # Service viewpoints
        "service_realization": {
            "name": "Service Realization Viewpoint",
            "aspect": "service",
            "purpose": "Showing how services are realized by underlying components",
            "layers": ["business", "application", "technology"],
            "element_types": [
                "BusinessService",
                "BusinessProcess",
                "BusinessFunction",
                "BusinessRole",
                "ApplicationService",
                "ApplicationComponent",
                "ApplicationInterface",
                "TechnologyService",
                "Node",
                "Device",
            ],
            "relationship_types": [
                "Realization",
                "Composition",
                "Aggregation",
                "Assignment",
                "Serving",
            ],
            "stakeholders": ["Service architect", "Application architect", "Business manager"],
        },
        # Data Architecture viewpoints
        "data_architecture": {
            "name": "Data Architecture Viewpoint",
            "aspect": "information",
            "purpose": "Showing the structure and flow of data across the enterprise",
            "layers": ["business", "application", "technology"],
            "element_types": [
                "ConceptualDataModel",
                "LogicalDataModel",
                "PhysicalDataModel",
                "DataLineage",
                "DataTransformation",
                "BusinessObject",
                "DataObject",
                "Representation",
                "Meaning",
                "ApplicationComponent",
                "Node",
                "SystemSoftware",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Realization",
                "Assignment",
                "Association",
                "Flow",
                "Access",
            ],
            "stakeholders": ["Data architect", "Enterprise architect", "Data steward", "DBA"],
        },
        "data_governance": {
            "name": "Data Governance Viewpoint",
            "aspect": "information",
            "purpose": "Modeling data governance, quality, and compliance aspects",
            "layers": ["motivation", "business", "application"],
            "element_types": [
                "DataLineage",
                "DataTransformation",
                "Stakeholder",
                "Driver",
                "Assessment",
                "Goal",
                "Outcome",
                "Principle",
                "BusinessObject",
                "DataObject",
                "Representation",
                "Meaning",
            ],
            "relationship_types": [
                "Association",
                "Assignment",
                "Realization",
                "Influence",
                "Access",
            ],
            "stakeholders": [
                "Data governance officer",
                "Data steward",
                "Compliance officer",
                "Enterprise architect",
            ],
        },
        # Solutions Architecture viewpoints
        "solution_architecture": {
            "name": "Solution Architecture Viewpoint",
            "aspect": "solution",
            "purpose": "Showing end-to-end solutions and their components",
            "layers": ["business", "application", "technology", "implementation"],
            "element_types": [
                "Solution",
                "SolutionPattern",
                "Contract",
                "BusinessProcess",
                "BusinessService",
                "BusinessFunction",
                "ApplicationComponent",
                "ApplicationCollaboration",
                "ApplicationInterface",
                "Node",
                "Device",
                "SystemSoftware",
                "TechnologyService",
                "WorkPackage",
                "Deliverable",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Realization",
                "Assignment",
                "Serving",
                "Association",
            ],
            "stakeholders": [
                "Solutions architect",
                "Enterprise architect",
                "Project manager",
                "Business sponsor",
            ],
        },
        "solution_pattern_catalog": {
            "name": "Solution Pattern Catalog Viewpoint",
            "aspect": "solution",
            "purpose": "Cataloging reusable solution patterns and their applications",
            "layers": ["solution"],
            "element_types": [
                "SolutionPattern",
                "Solution",
                "ApplicationComponent",
                "TechnologyService",
                "BusinessProcess",
                "BusinessService",
            ],
            "relationship_types": ["Association", "Specialization", "Realization"],
            "stakeholders": ["Solutions architect", "Enterprise architect", "Technical architect"],
        },
        # Software Architecture viewpoints
        "software_architecture": {
            "name": "Software Architecture Viewpoint",
            "aspect": "application",
            "purpose": "Showing detailed software structure below the application component level",
            "layers": ["application"],
            "element_types": [
                "SoftwareModule",
                "DesignPattern",
                "SoftwareDependency",
                "ApplicationComponent",
                "ApplicationInterface",
                "ApplicationFunction",
                "DataObject",
            ],
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Dependency",
                "Realization",
                "Assignment",
            ],
            "stakeholders": [
                "Software architect",
                "Application architect",
                "Development lead",
                "Technical architect",
            ],
        },
        "design_pattern_catalog": {
            "name": "Design Pattern Catalog Viewpoint",
            "aspect": "application",
            "purpose": "Cataloging software design patterns and their usage",
            "layers": ["application"],
            "element_types": [
                "DesignPattern",
                "SoftwareModule",
                "ApplicationComponent",
                "ApplicationFunction",
                "ApplicationInterface",
            ],
            "relationship_types": ["Association", "Specialization", "Application"],
            "stakeholders": ["Software architect", "Development team", "Technical architect"],
        },
        "software_dependency": {
            "name": "Software Dependency Viewpoint",
            "aspect": "application",
            "purpose": "Showing software dependencies and their impact",
            "layers": ["application"],
            "element_types": [
                "SoftwareDependency",
                "SoftwareModule",
                "ApplicationComponent",
                "ApplicationInterface",
                "Node",
                "SystemSoftware",
            ],
            "relationship_types": ["Dependency", "Association", "Assignment"],
            "stakeholders": [
                "Software architect",
                "DevOps engineer",
                "Security officer",
                "Development lead",
            ],
        },
    }

    def __init__(self):
        """Initialize the viewpoint service."""
        pass

    def get_available_viewpoints(self) -> List[Dict]:
        """
        Get list of all standard ArchiMate viewpoints.

        Returns:
            List of viewpoint dictionaries with metadata
        """
        return [
            {
                "id": key,
                "name": vp["name"],
                "aspect": vp["aspect"],
                "purpose": vp["purpose"],
                "layers": vp["layers"],
                "stakeholders": vp["stakeholders"],
            }
            for key, vp in self.STANDARD_VIEWPOINTS.items()
        ]

    def get_viewpoint_definition(self, viewpoint_id: str) -> Optional[Dict]:
        """
        Get full definition of a specific viewpoint.

        Args:
            viewpoint_id: ID of the viewpoint

        Returns:
            Viewpoint definition dictionary or None
        """
        return self.STANDARD_VIEWPOINTS.get(viewpoint_id)

    def generate_viewpoint(
        self, model: ArchitectureModel, viewpoint_id: str, additional_filters: Optional[Dict] = None
    ) -> Dict:
        """
        Generate a viewpoint from an architecture model.

        Args:
            model: ArchitectureModel to filter
            viewpoint_id: ID of the viewpoint to generate
            additional_filters: Optional additional filtering criteria

        Returns:
            Dictionary with filtered elements and relationships:
            {
                'viewpoint': viewpoint_definition,
                'elements': List[ArchiMateElement],
                'relationships': List[ArchiMateRelationship],
                'summary': Dict with counts
            }
        """
        viewpoint_def = self.get_viewpoint_definition(viewpoint_id)
        if not viewpoint_def:
            return {
                "error": f"Viewpoint {viewpoint_id} not found",
                "elements": [],
                "relationships": [],
            }

        # Get all elements and relationships from model
        all_elements = model.archimate_elements.all()
        all_relationships = model.archimate_relationships.all()

        # Filter elements based on viewpoint definition
        filtered_elements = self._filter_elements(all_elements, viewpoint_def, additional_filters)

        # Get element IDs for relationship filtering
        element_ids = {e.id for e in filtered_elements}

        # Filter relationships (only include if both source and target are in filtered elements)
        filtered_relationships = self._filter_relationships(
            all_relationships, element_ids, viewpoint_def
        )

        return {
            "viewpoint": viewpoint_def,
            "elements": filtered_elements,
            "relationships": filtered_relationships,
            "summary": {
                "viewpoint_name": viewpoint_def["name"],
                "element_count": len(filtered_elements),
                "relationship_count": len(filtered_relationships),
                "layers_included": list(set(e.layer for e in filtered_elements)),
                "element_types": list(set(e.type for e in filtered_elements)),
            },
        }

    def _filter_elements(
        self,
        elements: List[ArchiMateElement],
        viewpoint_def: Dict,
        additional_filters: Optional[Dict],
    ) -> List[ArchiMateElement]:
        """Filter elements based on viewpoint definition."""
        filtered = []

        allowed_layers = viewpoint_def.get("layers")
        allowed_types = viewpoint_def.get("element_types")

        for element in elements:
            # Check layer
            if allowed_layers and element.layer not in allowed_layers:
                continue

            # Check element type (if specified)
            if allowed_types and base_type(element.type) not in allowed_types:
                continue

            # Apply additional filters if provided
            if additional_filters:
                if "element_names" in additional_filters:
                    if element.name not in additional_filters["element_names"]:
                        continue

                if "exclude_types" in additional_filters:
                    if element.type in additional_filters["exclude_types"]:
                        continue

            filtered.append(element)

        return filtered

    def _filter_relationships(
        self, relationships: List[ArchiMateRelationship], element_ids: Set[int], viewpoint_def: Dict
    ) -> List[ArchiMateRelationship]:
        """Filter relationships based on viewpoint definition."""
        filtered = []
        allowed_rel_types = viewpoint_def.get("relationship_types")

        for rel in relationships:
            # Both source and target must be in the filtered element set
            if rel.source_element_id not in element_ids:
                continue
            if rel.target_element_id not in element_ids:
                continue

            # Check relationship type (if specified)
            if allowed_rel_types and rel.type not in allowed_rel_types:
                continue

            filtered.append(rel)

        return filtered

    def suggest_viewpoints_for_stakeholder(
        self, stakeholder_role: str, concerns: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        Suggest relevant viewpoints for a stakeholder role.

        Args:
            stakeholder_role: Role of the stakeholder (e.g., "CIO", "Business Manager")
            concerns: Optional list of specific concerns

        Returns:
            List of recommended viewpoints with rationale
        """
        recommendations = []

        for vp_id, vp_def in self.STANDARD_VIEWPOINTS.items():
            # Check if stakeholder role is listed for this viewpoint
            stakeholders = [s.lower() for s in vp_def.get("stakeholders", [])]
            role_lower = stakeholder_role.lower()

            # Simple matching (can be enhanced with NLP)
            relevance_score = 0
            if any(role_lower in sh or sh in role_lower for sh in stakeholders):
                relevance_score = 10

            # Check concerns against viewpoint purpose
            if concerns:
                purpose_lower = vp_def["purpose"].lower()
                for concern in concerns:
                    if concern.lower() in purpose_lower:
                        relevance_score += 5

            if relevance_score > 0:
                recommendations.append(
                    {
                        "viewpoint_id": vp_id,
                        "viewpoint_name": vp_def["name"],
                        "relevance_score": relevance_score,
                        "purpose": vp_def["purpose"],
                        "rationale": f"This viewpoint is relevant for {stakeholder_role} stakeholders",
                    }
                )

        # Sort by relevance
        recommendations.sort(key=lambda x: x["relevance_score"], reverse=True)

        return recommendations

"""
-> app.modules.architecture.services.modeling_service

ArchiMate Open Exchange Format 3.2 Import/Export Service

Provides import and export capabilities for ArchiMate models in the Open Exchange Format 3.2 specification.

Capabilities:
- Export solutions to ArchiMate XML
- Export analysis sessions with motivational elements
- Import ArchiMate XML and create corresponding models
- Validate ArchiMate XML against schema

Complies with:
- ArchiMate 3.2 Specification
- Open Exchange Format 3.2 (http://www.opengroup.org/xsd/archimate/3.0/)

Reference:
- https://pubs.opengroup.org/architecture/archimate3 - doc/
"""

from app.utils import safe_xml  # untrusted XML: entity-expansion safe
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, Optional, Type

from app.models.constants import ArchiMateRelationshipType


logger = logging.getLogger(__name__)


class ArchiMateExchangeService:
    """
    Import/Export ArchiMate models in Open Exchange Format 3.2.

    Provides comprehensive support for ArchiMate model exchange including
    elements, relationships, views, and properties.
    """

    # ArchiMate 3.2 XML namespace
    ARCHIMATE_NS = "http://www.opengroup.org/xsd/archimate/3.0/"
    XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

    # Namespace map for XML creation
    NAMESPACES = {"": ARCHIMATE_NS, "xsi": XSI_NS}

    # ArchiMate 3.2 Element Types by Layer
    MOTIVATION_ELEMENTS = [
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
    ]

    STRATEGY_ELEMENTS = ["Resource", "Capability", "CourseOfAction", "ValueStream"]

    BUSINESS_ELEMENTS = [
        "BusinessActor",
        "BusinessRole",
        "BusinessCollaboration",
        "BusinessInterface",
        "BusinessProcess",
        "BusinessFunction",
        "BusinessInteraction",
        "BusinessEvent",
        "BusinessService",
        "BusinessObject",
        "Contract",
        "Representation",
        "Product",
    ]

    APPLICATION_ELEMENTS = [
        "ApplicationComponent",
        "ApplicationCollaboration",
        "ApplicationInterface",
        "ApplicationFunction",
        "ApplicationInteraction",
        "ApplicationProcess",
        "ApplicationEvent",
        "ApplicationService",
        "DataObject",
    ]

    TECHNOLOGY_ELEMENTS = [
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
        "TechnologyEvent",
        "TechnologyService",
        "Artifact",
    ]

    PHYSICAL_ELEMENTS = ["Equipment", "Facility", "DistributionNetwork", "Material"]

    IMPLEMENTATION_ELEMENTS = [
        "WorkPackage",
        "Deliverable",
        "ImplementationEvent",
        "Plateau",
        "Gap",
    ]

    COMPOSITE_ELEMENTS = ["Grouping", "Location"]

    # All valid element types
    ALL_ELEMENT_TYPES = (
        MOTIVATION_ELEMENTS
        + STRATEGY_ELEMENTS
        + BUSINESS_ELEMENTS
        + APPLICATION_ELEMENTS
        + TECHNOLOGY_ELEMENTS
        + PHYSICAL_ELEMENTS
        + IMPLEMENTATION_ELEMENTS
        + COMPOSITE_ELEMENTS
    )

    # ArchiMate 3.2 Relationship Types
    RELATIONSHIP_TYPES = [
        "Composition",
        "Aggregation",
        "Assignment",
        "Realization",
        "Serving",
        "Access",
        "Influence",
        "Triggering",
        "Flow",
        "Specialization",
        "Association",
    ]

    # Layer mapping for elements
    ELEMENT_LAYER_MAP = {
        **{e: "Motivation" for e in MOTIVATION_ELEMENTS},
        **{e: "Strategy" for e in STRATEGY_ELEMENTS},
        **{e: "Business" for e in BUSINESS_ELEMENTS},
        **{e: "Application" for e in APPLICATION_ELEMENTS},
        **{e: "Technology" for e in TECHNOLOGY_ELEMENTS},
        **{e: "Physical" for e in PHYSICAL_ELEMENTS},
        **{e: "Implementation" for e in IMPLEMENTATION_ELEMENTS},
        **{e: "Composite" for e in COMPOSITE_ELEMENTS},
    }

    def __init__(self):
        """Initialize the ArchiMate Exchange Service."""
        self.logger = logger
        # Register namespaces for XML output
        for prefix, uri in self.NAMESPACES.items():
            if prefix:
                ET.register_namespace(prefix, uri)
            else:
                ET.register_namespace("", uri)

    # =========================================================================
    # EXPORT METHODS
    # =========================================================================

    def export_solution_to_archimate_xml(self, solution_id: int) -> str:
        """
        Export a solution and all its elements to ArchiMate XML.

        Args:
            solution_id: ID of the Solution to export

        Returns:
            ArchiMate XML string in Open Exchange Format 3.2
        """
        from app.models.archimate_core import ArchiMateElement
        from app.models.truly_missing_models import Solution

        try:
            # Query solution with relationships
            solution = Solution.query.get(solution_id)
            if not solution:
                raise ValueError(f"Solution with ID {solution_id} not found")

            self.logger.info(
                f"Exporting solution {solution.name} (ID: {solution_id}) to ArchiMate XML"
            )

            # Create root model element
            root = self._create_model_root(
                name=solution.name,
                identifier=f"id-solution-{solution_id}",
                documentation=solution.description,
            )

            # Create elements container
            elements_container = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}elements")

            # Track exported element IDs for relationship building
            exported_elements = {}

            # Export solution as a Grouping element
            solution_elem = self._create_element_xml(
                element_type="Grouping",
                identifier=f"id-solution-{solution_id}",
                name=solution.name,
                documentation=solution.description,
            )
            elements_container.append(solution_elem)
            exported_elements[f"solution-{solution_id}"] = f"id-solution-{solution_id}"

            # Export associated applications
            if hasattr(solution, "applications"):
                for app in solution.applications:
                    app_id = f"id-app-{app.id}"
                    app_elem = self._create_element_xml(
                        element_type="ApplicationComponent",
                        identifier=app_id,
                        name=app.name,
                        documentation=getattr(app, "description", None),
                    )
                    elements_container.append(app_elem)
                    exported_elements[f"app-{app.id}"] = app_id

            # Export ArchiMate elements linked to solution
            if solution.archimate_element_id:
                archimate_elem = ArchiMateElement.query.get(solution.archimate_element_id)
                if archimate_elem:
                    elem_id = f"id-archimate-{archimate_elem.id}"
                    elem = self._create_element_xml(
                        element_type=archimate_elem.type or "Grouping",
                        identifier=elem_id,
                        name=archimate_elem.name,
                        documentation=archimate_elem.description,
                    )
                    elements_container.append(elem)
                    exported_elements[f"archimate-{archimate_elem.id}"] = elem_id

            # Create relationships container
            relationships_container = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}relationships")

            # Add composition relationships from solution to applications
            for app in getattr(solution, "applications", []):
                app_id = exported_elements.get(f"app-{app.id}")
                if app_id:
                    rel_elem = self._build_relationship_xml(
                        relationship_type="Composition",
                        identifier=f"id-rel-solution-app-{app.id}",
                        source=f"id-solution-{solution_id}",
                        target=app_id,
                    )
                    relationships_container.append(rel_elem)

            # Add properties
            self._add_properties_to_element(
                root,
                {
                    "exportDate": datetime.utcnow().isoformat(),
                    "exportSource": "ArchiMate Exchange Service",
                    "solutionStatus": solution.status,
                    "solutionType": solution.solution_type,
                    "businessDomain": solution.business_domain,
                },
            )

            # Generate XML string
            return self._element_to_string(root)

        except Exception as e:
            self.logger.error(f"Error exporting solution {solution_id} to ArchiMate XML: {e}")
            raise

    def export_session_to_archimate_xml(self, session_id: int) -> str:
        """
        Export analysis session with motivational elements.

        Args:
            session_id: ID of the SolutionAnalysisSession to export

        Returns:
            ArchiMate XML string with motivation layer elements
        """
        from app.models.solution_architect_models import (
            SolutionAnalysisSession,
        )

        try:
            # Query session with relationships
            session = SolutionAnalysisSession.query.get(session_id)
            if not session:
                raise ValueError(f"Session with ID {session_id} not found")

            self.logger.info(
                f"Exporting session {session.name} (ID: {session_id}) to ArchiMate XML"
            )

            # Create root model element
            root = self._create_model_root(
                name=f"Analysis Session: {session.name}",
                identifier=f"id-session-{session_id}",
                documentation=session.description,
            )

            # Create elements container
            elements_container = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}elements")

            # Track exported element IDs
            exported_elements = {}

            # Get problem definition
            problem = session.problem_definition

            if problem:
                # Export Drivers
                for driver in problem.drivers:
                    driver_id = f"id-driver-{driver.id}"
                    driver_elem = self._create_element_xml(
                        element_type="Driver",
                        identifier=driver_id,
                        name=driver.name,
                        documentation=driver.description,
                    )
                    # Add driver-specific properties
                    self._add_properties_to_element(
                        driver_elem,
                        {
                            "driverType": driver.driver_type.value if driver.driver_type else None,
                            "impactLevel": str(driver.impact_level)
                            if driver.impact_level
                            else None,
                            "urgency": str(driver.urgency) if driver.urgency else None,
                            "source": driver.source,
                        },
                    )
                    elements_container.append(driver_elem)
                    exported_elements[f"driver-{driver.id}"] = driver_id

                # Export Goals
                for goal in problem.goals:
                    goal_id = f"id-goal-{goal.id}"
                    goal_elem = self._create_element_xml(
                        element_type="Goal",
                        identifier=goal_id,
                        name=goal.name,
                        documentation=goal.description,
                    )
                    self._add_properties_to_element(
                        goal_elem,
                        {
                            "targetDate": goal.target_date.isoformat()
                            if goal.target_date
                            else None,
                            "measurementCriteria": goal.measurement_criteria,
                            "priority": str(goal.priority) if goal.priority else None,
                        },
                    )
                    elements_container.append(goal_elem)
                    exported_elements[f"goal-{goal.id}"] = goal_id

                # Export Requirements
                for req in problem.requirements:
                    req_id = f"id-requirement-{req.id}"
                    req_elem = self._create_element_xml(
                        element_type="Requirement",
                        identifier=req_id,
                        name=req.name,
                        documentation=req.description,
                    )
                    self._add_properties_to_element(
                        req_elem,
                        {
                            "requirementType": req.requirement_type.value
                            if req.requirement_type
                            else None,
                            "priority": str(req.priority) if req.priority else None,
                            "isMandatory": str(req.is_mandatory),
                            "acceptanceCriteria": req.acceptance_criteria,
                        },
                    )
                    elements_container.append(req_elem)
                    exported_elements[f"requirement-{req.id}"] = req_id

                # Export Constraints
                for constraint in problem.constraints:
                    constraint_id = f"id-constraint-{constraint.id}"
                    constraint_elem = self._create_element_xml(
                        element_type="Constraint",
                        identifier=constraint_id,
                        name=constraint.name,
                        documentation=constraint.description,
                    )
                    self._add_properties_to_element(
                        constraint_elem,
                        {
                            "constraintType": constraint.constraint_type.value
                            if constraint.constraint_type
                            else None,
                            "value": constraint.value,
                            "unit": constraint.unit,
                            "severity": str(constraint.severity) if constraint.severity else None,
                        },
                    )
                    elements_container.append(constraint_elem)
                    exported_elements[f"constraint-{constraint.id}"] = constraint_id

                # Export Principles
                for principle in problem.principles:
                    principle_id = f"id-principle-{principle.id}"
                    principle_elem = self._create_element_xml(
                        element_type="Principle",
                        identifier=principle_id,
                        name=principle.name,
                        documentation=principle.statement,
                    )
                    self._add_properties_to_element(
                        principle_elem,
                        {
                            "rationale": principle.rationale,
                            "implications": principle.implications,
                            "priority": str(principle.priority) if principle.priority else None,
                        },
                    )
                    elements_container.append(principle_elem)
                    exported_elements[f"principle-{principle.id}"] = principle_id

                # Export Assessments
                for assessment in problem.assessments:
                    assessment_id = f"id-assessment-{assessment.id}"
                    assessment_elem = self._create_element_xml(
                        element_type="Assessment",
                        identifier=assessment_id,
                        name=assessment.aspect,
                        documentation=f"Current: {assessment.current_state}\nTarget: {assessment.target_state}",
                    )
                    self._add_properties_to_element(
                        assessment_elem,
                        {
                            "currentState": assessment.current_state,
                            "targetState": assessment.target_state,
                            "gapAnalysis": assessment.gap_analysis,
                            "gapSeverity": str(assessment.gap_severity)
                            if assessment.gap_severity
                            else None,
                        },
                    )
                    elements_container.append(assessment_elem)
                    exported_elements[f"assessment-{assessment.id}"] = assessment_id

            # Create relationships container
            relationships_container = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}relationships")

            # Build relationships: Driver -> Goal (Association)
            if problem:
                for goal in problem.goals:
                    for driver in problem.drivers:
                        rel_elem = self._build_relationship_xml(
                            relationship_type="Association",
                            identifier=f"id-rel-driver-goal-{driver.id}-{goal.id}",
                            source=exported_elements.get(f"driver-{driver.id}"),
                            target=exported_elements.get(f"goal-{goal.id}"),
                        )
                        if rel_elem is not None:
                            relationships_container.append(rel_elem)

                # Goal -> Requirement (Realization)
                for req in problem.requirements:
                    for goal in problem.goals:
                        rel_elem = self._build_relationship_xml(
                            relationship_type="Realization",
                            identifier=f"id-rel-goal-req-{goal.id}-{req.id}",
                            source=exported_elements.get(f"requirement-{req.id}"),
                            target=exported_elements.get(f"goal-{goal.id}"),
                        )
                        if rel_elem is not None:
                            relationships_container.append(rel_elem)

                # Constraint -> Goal (Influence)
                for constraint in problem.constraints:
                    for goal in problem.goals:
                        rel_elem = self._build_relationship_xml(
                            relationship_type="Influence",
                            identifier=f"id-rel-constraint-goal-{constraint.id}-{goal.id}",
                            source=exported_elements.get(f"constraint-{constraint.id}"),
                            target=exported_elements.get(f"goal-{goal.id}"),
                        )
                        if rel_elem is not None:
                            relationships_container.append(rel_elem)

            # Add export metadata
            self._add_properties_to_element(
                root,
                {
                    "exportDate": datetime.utcnow().isoformat(),
                    "exportSource": "ArchiMate Exchange Service",
                    "sessionStatus": session.status.value if session.status else None,
                    "sessionVersion": str(session.current_version),
                },
            )

            return self._element_to_string(root)

        except Exception as e:
            self.logger.error(f"Error exporting session {session_id} to ArchiMate XML: {e}")
            raise

    # =========================================================================
    # IMPORT
    # =========================================================================
    # This service no longer imports. OEF import has one engine,
    # app.services.archimate_import_service.ArchiMateImportService (ADR 0008),
    # which previews, applies an import strategy and refuses relationships
    # the ArchiMate matrix does not allow; the Driver / Goal /
    # ApplicationComponent domain rows this class used to create on import
    # are created there.

    # =========================================================================
    # VALIDATION METHODS
    # =========================================================================

    def validate_archimate_xml(self, xml_content: str) -> Dict[str, Any]:
        """
        Validate XML without importing.

        Args:
            xml_content: ArchiMate XML content string

        Returns:
            Validation result with errors and warnings
        """
        result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "element_count": 0,
            "relationship_count": 0,
            "element_types": [],
            "relationship_types": [],
        }

        try:
            # Parse XML
            root = safe_xml.fromstring(xml_content)

            # Check for model element
            if "model" not in root.tag.lower():
                result["warnings"].append("Root element is not 'model'")

            # Validate elements
            elements_container = root.find(f".//{{{self.ARCHIMATE_NS}}}elements")
            if elements_container is None:
                elements_container = root.find(".//elements")

            element_types_found = set()
            if elements_container is not None:
                for elem in elements_container:
                    result["element_count"] += 1

                    # Get element type
                    elem_type = elem.get(f"{{{self.XSI_NS}}}type")
                    if not elem_type:
                        tag = elem.tag
                        if "{" in tag:
                            elem_type = tag.split("}")[1]
                        else:
                            elem_type = tag

                    element_types_found.add(elem_type)

                    # Validate element type
                    if elem_type and elem_type not in self.ALL_ELEMENT_TYPES:
                        # Check if it's a known variant
                        normalized = self._map_archimate_type_to_internal(elem_type)
                        if not normalized:
                            result["warnings"].append(f"Unknown element type: {elem_type}")

                    # Check for required name
                    name_elem = elem.find(f".//{{{self.ARCHIMATE_NS}}}name")
                    if name_elem is None:
                        name_elem = elem.find(".//name")
                    if name_elem is None and not elem.get("name"):
                        result["warnings"].append(
                            f"Element {elem.get('identifier', 'unknown')} has no name"
                        )

            result["element_types"] = list(element_types_found)

            # Validate relationships
            relationships_container = root.find(f".//{{{self.ARCHIMATE_NS}}}relationships")
            if relationships_container is None:
                relationships_container = root.find(".//relationships")

            relationship_types_found = set()
            if relationships_container is not None:
                for rel in relationships_container:
                    result["relationship_count"] += 1

                    # Get relationship type
                    rel_type = rel.get(f"{{{self.XSI_NS}}}type")
                    if not rel_type:
                        tag = rel.tag
                        if "{" in tag:
                            rel_type = tag.split("}")[1]
                        else:
                            rel_type = tag

                    relationship_types_found.add(rel_type)

                    # Validate relationship type
                    if rel_type and rel_type not in self.RELATIONSHIP_TYPES:
                        normalized = self._normalize_relationship_type(rel_type)
                        if normalized not in self.RELATIONSHIP_TYPES:
                            result["warnings"].append(f"Unknown relationship type: {rel_type}")

                    # Check for source and target
                    if not rel.get("source"):
                        result["errors"].append(
                            f"Relationship {rel.get('identifier', 'unknown')} has no source"
                        )
                        result["valid"] = False
                    if not rel.get("target"):
                        result["errors"].append(
                            f"Relationship {rel.get('identifier', 'unknown')} has no target"
                        )
                        result["valid"] = False

            result["relationship_types"] = list(relationship_types_found)

        except ET.ParseError as e:
            result["valid"] = False
            result["errors"].append(f"XML parsing error: {e}")

        except Exception as e:
            result["valid"] = False
            result["errors"].append(f"Validation error: {e}")

        return result

    # =========================================================================
    # MAPPING HELPERS
    # =========================================================================

    def _map_element_to_archimate_type(self, element) -> str:
        """
        Map internal element to ArchiMate type.

        Args:
            element: Database model instance

        Returns:
            ArchiMate element type string
        """
        # Get class name and map to ArchiMate type
        class_name = element.__class__.__name__

        # Direct mappings
        type_map = {
            "Driver": "Driver",
            "Goal": "Goal",
            "Requirement": "Requirement",
            "Constraint": "Constraint",
            "Principle": "Principle",
            "Assessment": "Assessment",
            "ApplicationComponent": "ApplicationComponent",
            "ApplicationInterface": "ApplicationInterface",
            "ApplicationFunction": "ApplicationFunction",
            "ApplicationProcess": "ApplicationProcess",
            "ApplicationEvent": "ApplicationEvent",
            "ApplicationService": "ApplicationService",
            "DataObject": "DataObject",
            "BusinessActor": "BusinessActor",
            "BusinessRole": "BusinessRole",
            "BusinessProcess": "BusinessProcess",
            "BusinessFunction": "BusinessFunction",
            "BusinessService": "BusinessService",
            "BusinessObject": "BusinessObject",
            "Node": "Node",
            "Device": "Device",
            "SystemSoftware": "SystemSoftware",
            "Artifact": "Artifact",
            "TechnologyService": "TechnologyService",
            "Solution": "Grouping",
            "Capability": "Capability",
            "Resource": "Resource",
        }

        return type_map.get(class_name, "Grouping")

    def _map_archimate_type_to_internal(self, archimate_type: str) -> Optional[str]:
        """
        Map ArchiMate type to valid internal type.

        Handles common variations and aliases.
        """
        if archimate_type in self.ALL_ELEMENT_TYPES:
            return archimate_type

        # Handle common variations
        type_aliases = {
            "Business Actor": "BusinessActor",
            "Business Role": "BusinessRole",
            "Business Process": "BusinessProcess",
            "Business Function": "BusinessFunction",
            "Business Service": "BusinessService",
            "Business Object": "BusinessObject",
            "Business Event": "BusinessEvent",
            "Business Interface": "BusinessInterface",
            "Business Collaboration": "BusinessCollaboration",
            "Business Interaction": "BusinessInteraction",
            "Application Component": "ApplicationComponent",
            "Application Interface": "ApplicationInterface",
            "Application Function": "ApplicationFunction",
            "Application Process": "ApplicationProcess",
            "Application Event": "ApplicationEvent",
            "Application Service": "ApplicationService",
            "Application Collaboration": "ApplicationCollaboration",
            "Application Interaction": "ApplicationInteraction",
            "Data Object": "DataObject",
            "Technology Service": "TechnologyService",
            "Technology Function": "TechnologyFunction",
            "Technology Process": "TechnologyProcess",
            "Technology Event": "TechnologyEvent",
            "Technology Interface": "TechnologyInterface",
            "Technology Collaboration": "TechnologyCollaboration",
            "Technology Interaction": "TechnologyInteraction",
            "System Software": "SystemSoftware",
            "Communication Network": "CommunicationNetwork",
            "Distribution Network": "DistributionNetwork",
            "Course of Action": "CourseOfAction",
            "Value Stream": "ValueStream",
            "Work Package": "WorkPackage",
            "Implementation Event": "ImplementationEvent",
        }

        return type_aliases.get(archimate_type)

    def _map_archimate_type_to_model(self, archimate_type: str) -> Optional[Type]:
        """
        Map ArchiMate type to internal model class.

        Args:
            archimate_type: ArchiMate element type string

        Returns:
            Model class or None
        """
        # Import models lazily
        from app.models.application_layer import (
            ApplicationCollaboration,
            ApplicationEvent,
            ApplicationFunction,
            ApplicationInteraction,
            ApplicationInterface,
            ApplicationProcess,
            ApplicationService,
            DataObject,
        )
        from app.models.motivation import Assessment, Driver, Goal, Meaning, Value

        type_model_map = {
            "Driver": Driver,
            "Goal": Goal,
            "Assessment": Assessment,
            "Value": Value,
            "Meaning": Meaning,
            "ApplicationInterface": ApplicationInterface,
            "ApplicationEvent": ApplicationEvent,
            "ApplicationCollaboration": ApplicationCollaboration,
            "ApplicationFunction": ApplicationFunction,
            "ApplicationProcess": ApplicationProcess,
            "ApplicationInteraction": ApplicationInteraction,
            "DataObject": DataObject,
            "ApplicationService": ApplicationService,
        }

        return type_model_map.get(archimate_type)

    def _normalize_relationship_type(self, rel_type: str) -> str:
        """
        Normalize relationship type to standard ArchiMate type.
        """
        return ArchiMateRelationshipType.normalize(rel_type, pascal_case=True) or rel_type

    def _build_relationship_xml(
        self,
        relationship_type: str,
        identifier: str,
        source: str,
        target: str,
        name: Optional[str] = None,
        documentation: Optional[str] = None,
    ) -> Optional[ET.Element]:
        """
        Build XML element for relationship.

        Args:
            relationship_type: ArchiMate relationship type
            identifier: Unique identifier for the relationship
            source: Source element identifier
            target: Target element identifier
            name: Optional relationship name
            documentation: Optional documentation

        Returns:
            XML Element for the relationship
        """
        if not source or not target:
            return None

        rel_elem = ET.Element(f"{{{self.ARCHIMATE_NS}}}relationship")
        rel_elem.set("identifier", identifier)
        rel_elem.set(f"{{{self.XSI_NS}}}type", relationship_type)
        rel_elem.set("source", source)
        rel_elem.set("target", target)

        if name:
            name_elem = ET.SubElement(rel_elem, f"{{{self.ARCHIMATE_NS}}}name")
            name_elem.text = name

        if documentation:
            doc_elem = ET.SubElement(rel_elem, f"{{{self.ARCHIMATE_NS}}}documentation")
            doc_elem.text = documentation

        return rel_elem

    # =========================================================================
    # XML BUILDING HELPERS
    # =========================================================================

    def _create_model_root(
        self, name: str, identifier: str, documentation: Optional[str] = None
    ) -> ET.Element:
        """
        Create the root model element for ArchiMate XML.

        Args:
            name: Model name
            identifier: Model identifier
            documentation: Optional model documentation

        Returns:
            Root XML Element
        """
        root = ET.Element(f"{{{self.ARCHIMATE_NS}}}model")
        root.set("identifier", identifier)
        root.set("name", name)
        root.set(
            f"{{{self.XSI_NS}}}schemaLocation",
            f"{self.ARCHIMATE_NS} http://www.opengroup.org/xsd/archimate/3.0/archimate3_Model.xsd",
        )

        # Add name element
        name_elem = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}name")
        name_elem.text = name

        # Add documentation if provided
        if documentation:
            doc_elem = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}documentation")
            doc_elem.text = documentation

        return root

    def _create_element_xml(
        self, element_type: str, identifier: str, name: str, documentation: Optional[str] = None
    ) -> ET.Element:
        """
        Create an ArchiMate element XML node.

        Args:
            element_type: ArchiMate element type
            identifier: Unique identifier
            name: Element name
            documentation: Optional documentation

        Returns:
            XML Element
        """
        elem = ET.Element(f"{{{self.ARCHIMATE_NS}}}element")
        elem.set("identifier", identifier)
        elem.set(f"{{{self.XSI_NS}}}type", element_type)

        # Add name
        name_elem = ET.SubElement(elem, f"{{{self.ARCHIMATE_NS}}}name")
        name_elem.text = name

        # Add documentation if provided
        if documentation:
            doc_elem = ET.SubElement(elem, f"{{{self.ARCHIMATE_NS}}}documentation")
            doc_elem.text = documentation

        return elem

    def _add_properties_to_element(
        self, element: ET.Element, properties: Dict[str, Optional[str]]
    ) -> None:
        """
        Add property elements to an ArchiMate element.

        Args:
            element: XML Element to add properties to
            properties: Dictionary of property key-value pairs
        """
        if not properties:
            return

        # Find or create properties container
        props_container = element.find(f".//{{{self.ARCHIMATE_NS}}}properties")
        if props_container is None:
            props_container = ET.SubElement(element, f"{{{self.ARCHIMATE_NS}}}properties")

        for key, value in properties.items():
            if value is not None:
                prop_elem = ET.SubElement(props_container, f"{{{self.ARCHIMATE_NS}}}property")
                prop_elem.set("propertyDefinitionRef", key)

                value_elem = ET.SubElement(prop_elem, f"{{{self.ARCHIMATE_NS}}}value")
                value_elem.text = str(value)

    def _element_to_string(self, element: ET.Element) -> str:
        """
        Convert XML Element to formatted string.

        Args:
            element: XML Element

        Returns:
            Formatted XML string
        """
        # Add XML declaration
        xml_declaration = '<?xml version="1.0" encoding="UTF - 8"?>\n'

        # Convert to string with indentation
        self._indent_xml(element)
        xml_string = ET.tostring(element, encoding="unicode")

        return xml_declaration + xml_string

    def _indent_xml(self, elem: ET.Element, level: int = 0) -> None:
        """
        Add indentation to XML element for pretty printing.

        Args:
            elem: XML Element to indent
            level: Current indentation level
        """
        indent = "\n" + "  " * level
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = indent + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = indent
            for child in elem:
                self._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent


# Singleton instance
_archimate_exchange_service = None


def get_archimate_exchange_service() -> ArchiMateExchangeService:
    """
    Get singleton instance of ArchiMateExchangeService.

    Returns:
        ArchiMateExchangeService instance
    """
    global _archimate_exchange_service
    if _archimate_exchange_service is None:
        _archimate_exchange_service = ArchiMateExchangeService()
    return _archimate_exchange_service

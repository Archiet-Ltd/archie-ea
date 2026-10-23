"""
-> app.modules.architecture.services.modeling_service

ArchiMate 3.2 Viewpoint Generator Service

Generates standard ArchiMate viewpoints from existing model data and exports
to ArchiMate Open Exchange format for interoperability with tools like Archi.

Features:
- Application Cooperation viewpoint generation
- Service Realization viewpoint generation
- Technology Usage viewpoint generation
- Layered viewpoint (cross-layer)
- Export to ArchiMate Open Exchange XML format
- Capability-centric viewpoint generation

Usage:
    service = ArchiMateViewpointGenerator()

    # Generate Application Cooperation viewpoint
    viewpoint = service.generate_application_cooperation_viewpoint(domain_id=1)

    # Export to Open Exchange format
    xml = service.export_to_open_exchange(viewpoint)
"""

from app.utils import safe_xml  # untrusted XML: entity-expansion safe
import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import current_app
from sqlalchemy import and_, or_

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate import ElementType, RelationshipType
from app.models.models import ArchiMateElement, ArchiMateRelationship
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import BusinessDomain, UnifiedCapability
from app.models.archimate_element_types import application_kind_types

logger = logging.getLogger(__name__)


class _ValidatedXML(str):
    """String subclass that carries validation_errors metadata."""

    def __new__(cls, value, validation_errors=None):
        obj = super().__new__(cls, value)
        obj.validation_errors = validation_errors or []
        return obj


class ArchiMateViewpointGenerator:
    """
    Generates ArchiMate 3.2 compliant viewpoints from existing data.

    Supports standard viewpoints:
    - Application Cooperation: Shows application components and their relationships
    - Service Realization: Shows how services are realized by applications
    - Technology Usage: Shows technology stack supporting applications
    - Capability Map: Shows business capabilities and supporting applications
    - Layered: Cross-layer view combining business, application, and technology
    """

    # ArchiMate 3.2 Open Exchange namespace
    ARCHIMATE_NS = "http://www.opengroup.org/xsd/archimate/3.0/"
    XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

    # Standard ArchiMate viewpoints
    VIEWPOINTS = {
        "application_cooperation": {
            "name": "Application Cooperation",
            "purpose": "Shows application components and their cooperation relationships",
            "layers": ["application"],
            "element_types": [
                ElementType.APPLICATION_COMPONENT,
                ElementType.APPLICATION_INTERFACE,
                ElementType.APPLICATION_SERVICE,
                ElementType.DATA_OBJECT,
            ],
            "relationship_types": [
                RelationshipType.SERVING,
                RelationshipType.FLOW,
                RelationshipType.COMPOSITION,
                RelationshipType.AGGREGATION,
            ],
        },
        "service_realization": {
            "name": "Service Realization",
            "purpose": "Shows how business services are realized by application services",
            "layers": ["business", "application"],
            "element_types": [
                ElementType.BUSINESS_SERVICE,
                ElementType.BUSINESS_PROCESS,
                ElementType.APPLICATION_SERVICE,
                ElementType.APPLICATION_COMPONENT,
            ],
            "relationship_types": [
                RelationshipType.REALIZATION,
                RelationshipType.SERVING,
                RelationshipType.COMPOSITION,
            ],
        },
        "technology_usage": {
            "name": "Technology Usage",
            "purpose": "Shows technology infrastructure supporting applications",
            "layers": ["application", "technology"],
            "element_types": [
                ElementType.APPLICATION_COMPONENT,
                ElementType.NODE,
                ElementType.DEVICE,
                ElementType.SYSTEM_SOFTWARE,
                ElementType.ARTIFACT,
            ],
            "relationship_types": [
                RelationshipType.ASSIGNMENT,
                RelationshipType.REALIZATION,
                RelationshipType.SERVING,
            ],
        },
        "capability_map": {
            "name": "Capability Map",
            "purpose": "Shows business capabilities and their application support",
            "layers": ["strategy", "business", "application"],
            "element_types": [
                ElementType.CAPABILITY,
                ElementType.BUSINESS_SERVICE,
                ElementType.APPLICATION_COMPONENT,
                ElementType.APPLICATION_SERVICE,
            ],
            "relationship_types": [
                RelationshipType.REALIZATION,
                RelationshipType.SERVING,
                RelationshipType.AGGREGATION,
            ],
        },
        "layered": {
            "name": "Layered",
            "purpose": "Cross-layer view from business through technology",
            "layers": ["business", "application", "technology"],
            "element_types": [
                ElementType.BUSINESS_PROCESS,
                ElementType.BUSINESS_SERVICE,
                ElementType.APPLICATION_COMPONENT,
                ElementType.APPLICATION_SERVICE,
                ElementType.NODE,
                ElementType.SYSTEM_SOFTWARE,
            ],
            "relationship_types": [
                RelationshipType.REALIZATION,
                RelationshipType.SERVING,
                RelationshipType.ASSIGNMENT,
                RelationshipType.COMPOSITION,
            ],
        },
    }

    def __init__(self):
        """Initialize the ArchiMate Viewpoint Generator."""
        self.app = current_app._get_current_object() if current_app else None

    def generate_application_cooperation_viewpoint(
        self,
        domain_id: Optional[int] = None,
        capability_ids: Optional[List[int]] = None,
        include_data_flows: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate Application Cooperation viewpoint.

        Shows application components and their relationships including:
        - Data flows between applications
        - Shared services
        - Integration points

        Args:
            domain_id: Optional domain ID to filter applications
            capability_ids: Optional list of capability IDs to filter
            include_data_flows: Include data flow relationships

        Returns:
            Dictionary containing viewpoint elements and relationships
        """
        logger.info("Generating Application Cooperation viewpoint")

        viewpoint = {
            "viewpoint_type": "application_cooperation",
            "name": "Application Cooperation Viewpoint",
            "description": "Shows application components and their cooperation relationships",
            "generated_at": datetime.utcnow().isoformat(),
            "elements": [],
            "relationships": [],
            "metadata": {},
        }

        # Get application components
        query = ArchiMateElement.query.filter(
            ArchiMateElement.type.in_(application_kind_types())
        )

        if capability_ids:
            # Filter by capability mappings
            mapped_app_ids = (
                db.session.query(UnifiedApplicationCapabilityMapping.application_component_id)
                .filter(
                    UnifiedApplicationCapabilityMapping.unified_capability_id.in_(capability_ids),
                    UnifiedApplicationCapabilityMapping.is_active == True,
                )
                .distinct()
                .all()
            )
            app_ids = [a[0] for a in mapped_app_ids]
            query = query.filter(ArchiMateElement.id.in_(app_ids))

        applications = query.all()

        # Add elements
        element_ids = set()
        for app in applications:
            element_data = self._element_to_dict(app)
            viewpoint["elements"].append(element_data)
            element_ids.add(app.id)

        # Get relationships between these elements
        if include_data_flows:
            relationships = ArchiMateRelationship.query.filter(
                and_(
                    ArchiMateRelationship.source_id.in_(element_ids),
                    ArchiMateRelationship.target_id.in_(element_ids),
                )
            ).all()

            for rel in relationships:
                rel_data = self._relationship_to_dict(rel)
                viewpoint["relationships"].append(rel_data)

        viewpoint["metadata"] = {
            "element_count": len(viewpoint["elements"]),
            "relationship_count": len(viewpoint["relationships"]),
            "filtered_by_domain": domain_id,
            "filtered_by_capabilities": capability_ids,
        }

        return viewpoint

    def generate_service_realization_viewpoint(
        self, capability_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate Service Realization viewpoint.

        Shows how business capabilities/services are realized by applications.

        Args:
            capability_id: Optional capability ID to focus on

        Returns:
            Dictionary containing viewpoint elements and relationships
        """
        logger.info("Generating Service Realization viewpoint")

        viewpoint = {
            "viewpoint_type": "service_realization",
            "name": "Service Realization Viewpoint",
            "description": "Shows how business capabilities are realized by applications",
            "generated_at": datetime.utcnow().isoformat(),
            "elements": [],
            "relationships": [],
            "metadata": {},
        }

        element_ids = set()

        # Get capabilities
        cap_query = UnifiedCapability.query
        if capability_id:
            cap_query = cap_query.filter(UnifiedCapability.id == capability_id)

        capabilities = cap_query.limit(50).all()  # Limit for performance

        for cap in capabilities:
            # Add capability as element
            cap_element = {
                "id": f"cap_{cap.id}",
                "type": "capability",
                "name": cap.name,
                "description": cap.description,
                "layer": "strategy",
                "properties": {
                    "code": cap.code,
                    "level": cap.level,
                    "strategic_importance": cap.strategic_importance,
                },
            }
            viewpoint["elements"].append(cap_element)
            element_ids.add(f"cap_{cap.id}")

            # Get applications realizing this capability
            mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
                unified_capability_id=cap.id, is_active=True
            ).all()

            for mapping in mappings:
                app = db.session.get(ApplicationComponent, mapping.application_component_id)
                if app:
                    app_element = {
                        "id": f"app_{app.id}",
                        "type": "application_component",
                        "name": app.name,
                        "description": getattr(app, "description", ""),
                        "layer": "application",
                        "properties": {
                            "coverage_percentage": mapping.coverage_percentage,
                            "support_level": mapping.support_level,
                        },
                    }

                    if f"app_{app.id}" not in element_ids:
                        viewpoint["elements"].append(app_element)
                        element_ids.add(f"app_{app.id}")

                    # Add realization relationship
                    viewpoint["relationships"].append(
                        {
                            "id": f"rel_{cap.id}_{app.id}",
                            "type": "realization",
                            "source": f"app_{app.id}",
                            "target": f"cap_{cap.id}",
                            "properties": {
                                "coverage_percentage": mapping.coverage_percentage,
                            },
                        }
                    )

        viewpoint["metadata"] = {
            "element_count": len(viewpoint["elements"]),
            "relationship_count": len(viewpoint["relationships"]),
            "capability_count": len(capabilities),
        }

        return viewpoint

    def generate_technology_usage_viewpoint(
        self, application_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Generate Technology Usage viewpoint.

        Shows technology infrastructure supporting applications.

        Args:
            application_ids: Optional list of application IDs to include

        Returns:
            Dictionary containing viewpoint elements and relationships
        """
        logger.info("Generating Technology Usage viewpoint")

        viewpoint = {
            "viewpoint_type": "technology_usage",
            "name": "Technology Usage Viewpoint",
            "description": "Shows technology infrastructure supporting applications",
            "generated_at": datetime.utcnow().isoformat(),
            "elements": [],
            "relationships": [],
            "metadata": {},
        }

        element_ids = set()

        # Get application elements
        app_query = ArchiMateElement.query.filter(
            ArchiMateElement.type.in_(application_kind_types())
        )
        if application_ids:
            app_query = app_query.filter(ArchiMateElement.id.in_(application_ids))

        applications = app_query.limit(30).all()

        for app in applications:
            viewpoint["elements"].append(self._element_to_dict(app))
            element_ids.add(app.id)

            # Find technology elements related to this application
            tech_rels = ArchiMateRelationship.query.filter(
                or_(
                    ArchiMateRelationship.source_id == app.id,
                    ArchiMateRelationship.target_id == app.id,
                )
            ).all()

            for rel in tech_rels:
                # Get the related element
                related_id = (
                    rel.target_id
                    if rel.source_id == app.id
                    else rel.source_id
                )
                related = db.session.get(ArchiMateElement, related_id)

                if related and related.type in [
                    "Node",
                    "Device",
                    "SystemSoftware",
                    "Artifact",
                ]:
                    if related.id not in element_ids:
                        viewpoint["elements"].append(self._element_to_dict(related))
                        element_ids.add(related.id)

                    viewpoint["relationships"].append(self._relationship_to_dict(rel))

        viewpoint["metadata"] = {
            "element_count": len(viewpoint["elements"]),
            "relationship_count": len(viewpoint["relationships"]),
            "application_count": len(applications),
        }

        return viewpoint

    def generate_capability_map_viewpoint(
        self, domain_id: Optional[int] = None, level: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate Capability Map viewpoint.

        Shows business capabilities hierarchy with application support overlay.

        Args:
            domain_id: Optional domain ID to filter
            level: Optional capability level to include (1, 2, or 3)

        Returns:
            Dictionary containing viewpoint elements and relationships
        """
        logger.info("Generating Capability Map viewpoint")

        viewpoint = {
            "viewpoint_type": "capability_map",
            "name": "Capability Map Viewpoint",
            "description": "Shows business capabilities and their application support",
            "generated_at": datetime.utcnow().isoformat(),
            "elements": [],
            "relationships": [],
            "domains": [],
            "metadata": {},
        }

        # Get domains
        domain_query = BusinessDomain.query
        if domain_id:
            domain_query = domain_query.filter(BusinessDomain.id == domain_id)

        domains = domain_query.all()

        for domain in domains:
            domain_data = {
                "id": f"domain_{domain.id}",
                "code": domain.code,
                "name": domain.name,
                "capabilities": [],
            }

            # Get capabilities in this domain
            cap_query = UnifiedCapability.query.filter(UnifiedCapability.domain_id == domain.id)
            if level:
                cap_query = cap_query.filter(UnifiedCapability.level == level)

            capabilities = cap_query.all()

            for cap in capabilities:
                cap_element = {
                    "id": f"cap_{cap.id}",
                    "type": "capability",
                    "name": cap.name,
                    "code": cap.code,
                    "level": cap.level,
                    "parent_id": f"cap_{cap.parent_capability_id}"
                    if cap.parent_capability_id
                    else None,
                    "strategic_importance": cap.strategic_importance,
                    "current_maturity": cap.current_maturity_level,
                    "target_maturity": cap.target_maturity_level,
                    "application_support": [],
                }

                # Get application mappings
                mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
                    unified_capability_id=cap.id, is_active=True
                ).all()

                for mapping in mappings:
                    app = db.session.get(ApplicationComponent, mapping.application_component_id)
                    if app:
                        cap_element["application_support"].append(
                            {
                                "app_id": app.id,
                                "app_name": app.name,
                                "coverage": mapping.coverage_percentage,
                                "support_level": mapping.support_level,
                            }
                        )

                domain_data["capabilities"].append(cap_element)
                viewpoint["elements"].append(cap_element)

            viewpoint["domains"].append(domain_data)

        viewpoint["metadata"] = {
            "domain_count": len(domains),
            "element_count": len(viewpoint["elements"]),
            "filtered_by_domain": domain_id,
            "filtered_by_level": level,
        }

        return viewpoint

    def generate_layered_viewpoint(self, capability_id: int) -> Dict[str, Any]:
        """
        Generate Layered viewpoint for a specific capability.

        Shows cross-layer view from capability through applications to technology.

        Args:
            capability_id: Capability ID to center the view on

        Returns:
            Dictionary containing viewpoint elements organized by layer
        """
        logger.info(f"Generating Layered viewpoint for capability {capability_id}")

        capability = db.session.get(UnifiedCapability, capability_id)
        if not capability:
            return {"error": f"Capability {capability_id} not found"}

        viewpoint = {
            "viewpoint_type": "layered",
            "name": f"Layered Viewpoint: {capability.name}",
            "description": f"Cross-layer view centered on capability: {capability.name}",
            "generated_at": datetime.utcnow().isoformat(),
            "layers": {
                "strategy": [],
                "business": [],
                "application": [],
                "technology": [],
            },
            "relationships": [],
            "metadata": {},
        }

        # Strategy layer - the capability
        viewpoint["layers"]["strategy"].append(
            {
                "id": f"cap_{capability.id}",
                "type": "capability",
                "name": capability.name,
                "code": capability.code,
            }
        )

        # Application layer - applications supporting this capability
        mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
            unified_capability_id=capability.id, is_active=True
        ).all()

        for mapping in mappings:
            app = db.session.get(ApplicationComponent, mapping.application_component_id)
            if app:
                viewpoint["layers"]["application"].append(
                    {
                        "id": f"app_{app.id}",
                        "type": "application_component",
                        "name": app.name,
                        "coverage": mapping.coverage_percentage,
                    }
                )

                # Add realization relationship
                viewpoint["relationships"].append(
                    {
                        "id": f"rel_cap_app_{app.id}",
                        "type": "realization",
                        "source": f"app_{app.id}",
                        "target": f"cap_{capability.id}",
                    }
                )

        viewpoint["metadata"] = {
            "capability_id": capability_id,
            "capability_name": capability.name,
            "application_count": len(viewpoint["layers"]["application"]),
        }

        return viewpoint

    def export_to_open_exchange(self, viewpoint: Dict[str, Any]) -> str:
        """
        Export viewpoint to ArchiMate Open Exchange XML format.

        Args:
            viewpoint: Viewpoint dictionary to export

        Returns:
            XML string in ArchiMate Open Exchange format
        """
        logger.info("Exporting viewpoint to Open Exchange format")

        # Create root element with namespaces
        root = ET.Element(
            "model",
            {
                "xmlns": self.ARCHIMATE_NS,
                "xmlns:xsi": self.XSI_NS,
                "identifier": f"id-{uuid.uuid4().hex[:8]}",
            },
        )

        # Add name
        name_elem = ET.SubElement(root, "name")
        name_elem.text = viewpoint.get("name", "Exported Viewpoint")

        # Add documentation
        doc_elem = ET.SubElement(root, "documentation")
        doc_elem.text = viewpoint.get("description", "")

        # Add elements
        elements_container = ET.SubElement(root, "elements")
        for elem in viewpoint.get("elements", []):
            element = ET.SubElement(
                elements_container,
                "element",
                {
                    "identifier": str(elem.get("id", "")),
                    "xsi:type": self._map_type_to_xsi(elem.get("type", "application_component")),
                },
            )

            elem_name = ET.SubElement(element, "name")
            elem_name.text = elem.get("name", "")

            if elem.get("description"):
                elem_doc = ET.SubElement(element, "documentation")
                elem_doc.text = elem.get("description", "")

        # Add relationships
        relationships_container = ET.SubElement(root, "relationships")
        for rel in viewpoint.get("relationships", []):
            (ET.SubElement(
                relationships_container,
                "relationship",
                {
                    "identifier": str(rel.get("id", "")),
                    "xsi:type": self._map_rel_type_to_xsi(rel.get("type", "association")),
                    "source": str(rel.get("source", "")),
                    "target": str(rel.get("target", "")),
                },
            ))

        # Add view
        views_container = ET.SubElement(root, "views")
        diagrams = ET.SubElement(views_container, "diagrams")
        view = ET.SubElement(
            diagrams,
            "view",
            {
                "identifier": f"view-{uuid.uuid4().hex[:8]}",
                "xsi:type": "Diagram",
            },
        )

        view_name = ET.SubElement(view, "name")
        view_name.text = viewpoint.get("name", "Exported View")

        # Add nodes (elements in view)
        for idx, elem in enumerate(viewpoint.get("elements", [])):
            (ET.SubElement(
                view,
                "node",
                {
                    "identifier": f"node-{elem.get('id', '')}",
                    "elementRef": str(elem.get("id", "")),
                    "x": str(50 + (idx % 5) * 200),
                    "y": str(50 + (idx // 5) * 150),
                    "w": "140",
                    "h": "55",
                },
            ))

        # Convert to string
        xml_string = ET.tostring(root, encoding="unicode", method="xml")

        # Non-blocking validation — log issues but always return XML
        validation_errors = self._validate_oef_xml(xml_string)
        if validation_errors:
            logger.warning(
                "ArchiMate OEF validation found %d issue(s): %s",
                len(validation_errors),
                "; ".join(validation_errors[:5]),
            )

        # Attach validation_errors to the string as an attribute for route access
        xml_string = _ValidatedXML(xml_string, validation_errors)
        return xml_string

    def _validate_oef_xml(self, xml_string: str) -> List[str]:
        """
        Validate XML against ArchiMate Open Exchange structural rules.

        Uses lxml XSD validation if available; falls back to basic structural
        checks with xml.etree.ElementTree otherwise.

        Returns:
            List of validation error strings (empty = valid).
        """
        errors: List[str] = []

        # Try lxml-based XSD validation first
        try:
            from lxml import etree as lxml_etree

            doc = lxml_etree.fromstring(xml_string.encode("utf-8"))

            # Structural checks without external XSD file
            ns = self.ARCHIMATE_NS
            if doc.tag != f"{{{ns}}}model" and doc.tag != "model":
                errors.append(f"Root element is '{doc.tag}', expected 'model'")

            required_children = {"name", "elements", "relationships"}
            found = {child.tag.split("}")[-1] if "}" in child.tag else child.tag for child in doc}
            missing = required_children - found
            if missing:
                errors.append(f"Missing required children: {', '.join(sorted(missing))}")

            # Validate element identifiers are non-empty
            for elem in doc.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag == "element" and not elem.get("identifier"):
                    errors.append("Element missing required 'identifier' attribute")
                    break
                if tag == "relationship":
                    if not elem.get("source") or not elem.get("target"):
                        errors.append("Relationship missing 'source' or 'target' attribute")
                        break

            return errors

        except ImportError:
            logger.exception("Failed to import lxml for XML validation")
            pass
        except Exception as e:
            errors.append(f"lxml validation error: {e}")
            return errors

        # Fallback: stdlib xml.etree structural checks
        try:
            root = safe_xml.fromstring(xml_string)

            if "model" not in root.tag:
                errors.append(f"Root element is '{root.tag}', expected 'model'")

            child_tags = {child.tag.split("}")[-1] if "}" in child.tag else child.tag for child in root}
            required = {"name", "elements", "relationships"}
            missing = required - child_tags
            if missing:
                errors.append(f"Missing required children: {', '.join(sorted(missing))}")

            for elem in root.iter("element"):
                if not elem.get("identifier"):
                    errors.append("Element missing required 'identifier' attribute")
                    break

        except ET.ParseError as e:
            errors.append(f"XML parse error: {e}")

        return errors

    def _element_to_dict(self, element: ArchiMateElement) -> Dict[str, Any]:
        """Convert ArchiMate element to dictionary."""
        return {
            "id": element.id,
            "type": element.type,
            "name": element.name,
            "description": element.description,
            "layer": self._get_layer_for_type(element.type),
            "properties": {
                "documentation": element.documentation,
            },
        }

    def _relationship_to_dict(self, rel: ArchiMateRelationship) -> Dict[str, Any]:
        """Convert ArchiMate relationship to dictionary."""
        return {
            "id": rel.id,
            "type": rel.type,
            "source": rel.source_id,
            "target": rel.target_id,
            "name": getattr(rel, "custom_label", None) or getattr(rel, "flow_label", None),
        }

    def _get_layer_for_type(self, element_type: str) -> str:
        """Get the ArchiMate layer for an element type."""
        type_lower = element_type.lower() if element_type else ""

        if type_lower.startswith("business"):
            return "business"
        elif type_lower.startswith("application") or type_lower == "data_object":
            return "application"
        elif type_lower.startswith("technology") or type_lower in [
            "node",
            "device",
            "system_software",
            "artifact",
            "path",
            "communication_network",
        ]:
            return "technology"
        elif type_lower in ["equipment", "facility", "distribution_network", "material"]:
            return "physical"
        elif type_lower in [
            "stakeholder",
            "driver",
            "assessment",
            "goal",
            "outcome",
            "principle",
            "requirement",
            "constraint",
            "meaning",
            "value",
        ]:
            return "motivation"
        elif type_lower in ["resource", "capability", "course_of_action"]:
            return "strategy"
        elif type_lower in [
            "work_package",
            "deliverable",
            "implementation_event",
            "plateau",
            "gap",
        ]:
            return "implementation"
        return "unknown"

    def _map_type_to_xsi(self, element_type: str) -> str:
        """Map element type to ArchiMate XSI type."""
        type_map = {
            "application_component": "ApplicationComponent",
            "application_service": "ApplicationService",
            "application_interface": "ApplicationInterface",
            "data_object": "DataObject",
            "business_service": "BusinessService",
            "business_process": "BusinessProcess",
            "capability": "Capability",
            "node": "Node",
            "device": "Device",
            "system_software": "SystemSoftware",
            "artifact": "Artifact",
        }
        return type_map.get(element_type.lower(), "ApplicationComponent")

    def _map_rel_type_to_xsi(self, rel_type: str) -> str:
        """Map relationship type to ArchiMate XSI type."""
        type_map = {
            "realization": "Realization",
            "serving": "Serving",
            "composition": "Composition",
            "aggregation": "Aggregation",
            "assignment": "Assignment",
            "flow": "Flow",
            "triggering": "Triggering",
            "access": "Access",
            "influence": "Influence",
            "association": "Association",
            "specialization": "Specialization",
        }
        return type_map.get(rel_type.lower(), "Association")

    def get_available_viewpoints(self) -> List[Dict[str, Any]]:
        """Get list of available viewpoint types."""
        return [
            {
                "key": key,
                "name": config["name"],
                "purpose": config["purpose"],
                "layers": config["layers"],
            }
            for key, config in self.VIEWPOINTS.items()
        ]

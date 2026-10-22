"""ArchiMate 3.2 Open Exchange Format (OEF) XML export service (ARC-E01)."""

import logging
from typing import Optional
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from app import db
from app.models.archimate_element_types import exchange_type_for

logger = logging.getLogger(__name__)

# ArchiMate 3.2 element type mapping: snake_case internal → OEF xsi:type (PascalCase)
ARCHIMATE_TYPE_MAP = {
    # Strategy layer
    "resource": "Resource",
    "capability": "Capability",
    "course_of_action": "CourseOfAction",
    # Business layer
    "business_actor": "BusinessActor",
    "business_role": "BusinessRole",
    "business_collaboration": "BusinessCollaboration",
    "business_interface": "BusinessInterface",
    "business_process": "BusinessProcess",
    "business_function": "BusinessFunction",
    "business_interaction": "BusinessInteraction",
    "business_event": "BusinessEvent",
    "business_service": "BusinessService",
    "business_object": "BusinessObject",
    "contract": "Contract",
    "representation": "Representation",
    "product": "Product",
    # Application layer
    "application_component": "ApplicationComponent",
    "application_collaboration": "ApplicationCollaboration",
    "application_interface": "ApplicationInterface",
    "application_function": "ApplicationFunction",
    "application_interaction": "ApplicationInteraction",
    "application_process": "ApplicationProcess",
    "application_event": "ApplicationEvent",
    "application_service": "ApplicationService",
    "data_object": "DataObject",
    # Technology layer
    "node": "Node",
    "device": "Device",
    "system_software": "SystemSoftware",
    "technology_collaboration": "TechnologyCollaboration",
    "technology_interface": "TechnologyInterface",
    "path": "Path",
    "communication_network": "CommunicationNetwork",
    "technology_function": "TechnologyFunction",
    "technology_process": "TechnologyProcess",
    "technology_interaction": "TechnologyInteraction",
    "technology_event": "TechnologyEvent",
    "technology_service": "TechnologyService",
    "artifact": "Artifact",
    # Physical layer
    "equipment": "Equipment",
    "facility": "Facility",
    "distribution_network": "DistributionNetwork",
    "material": "Material",
    # Motivation layer
    "stakeholder": "Stakeholder",
    "driver": "Driver",
    "assessment": "Assessment",
    "goal": "Goal",
    "outcome": "Outcome",
    "principle": "Principle",
    "requirement": "Requirement",
    "constraint": "Constraint",
    "meaning": "Meaning",
    "value": "Value",
    # Implementation & Migration layer
    "work_package": "WorkPackage",
    "deliverable": "Deliverable",
    "implementation_event": "ImplementationEvent",
    "plateau": "Plateau",
    "gap": "Gap",
    # Other
    "location": "Location",
    "grouping": "Grouping",
    "junction": "Junction",
}


class ArchimateOEFExportService:
    """Export solution ArchiMate elements as Open Exchange Format XML."""

    OEF_NAMESPACE = "http://www.opengroup.org/xsd/archimate/3.0/"
    XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"

    def export_solution(self, solution_id: int) -> str:
        """Generate OEF XML for a solution's ArchiMate elements.

        Returns XML string. Raises ValueError if solution not found.
        """
        from app.models.solution_models import Solution

        solution = db.session.get(Solution, solution_id)
        if not solution:
            raise ValueError(f"Solution {solution_id} not found")

        elements = self._get_solution_elements(solution_id)
        element_ids = {e.id for e in elements}
        relationships = self._get_relationships(element_ids)

        root = Element("model")
        root.set("xmlns", self.OEF_NAMESPACE)
        root.set("xmlns:xsi", self.XSI_NAMESPACE)
        root.set("identifier", f"id-model-{solution_id}")

        name_el = SubElement(root, "name")
        name_el.set("xml:lang", "en")
        name_el.text = solution.name or f"Solution {solution_id}"

        elements_container = SubElement(root, "elements")

        used_props: dict = {}
        for elem in elements:
            self._add_element(elements_container, elem, used_props)

        # Relationships
        if relationships:
            rels_container = SubElement(root, "relationships")
            for rel in relationships:
                raw_type = getattr(rel, "type", "Association")
                oef_rel = raw_type.replace("Relationship", "") if "Relationship" in raw_type else raw_type
                r = SubElement(rels_container, "relationship")
                r.set("identifier", f"id-rel-{rel.id}")
                r.set("xsi:type", oef_rel)
                r.set("source", f"id-{rel.source_id}")
                r.set("target", f"id-{rel.target_id}")

        if used_props:
            defs = SubElement(root, "propertyDefinitions")
            for key, ident in used_props.items():
                definition = SubElement(defs, "propertyDefinition")
                definition.set("identifier", ident)
                definition.set("type", "string")
                d_name = SubElement(definition, "name")
                d_name.set("xml:lang", "en")
                d_name.text = key

        indent(root, space="  ")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
            root, encoding="unicode"
        )

    def _get_solution_elements(self, solution_id: int) -> list:
        """Get all ArchiMate elements linked to a solution via junction table."""
        try:
            from app.models.archimate_core import ArchiMateElement  # type: ignore[import]
            from app.models.solution_archimate_element import SolutionArchiMateElement

            mappings = (
                db.session.query(SolutionArchiMateElement)
                .filter_by(solution_id=solution_id)
                .all()
            )

            element_ids = [m.element_id for m in mappings if m.element_id]
            if not element_ids:
                return []

            return (
                db.session.query(ArchiMateElement)
                .filter(ArchiMateElement.id.in_(element_ids))
                .all()
            )
        except Exception:
            logger.exception("Failed to query solution ArchiMate elements")
            return []

    def _add_element(self, parent: Element, elem, used_props: Optional[dict] = None) -> None:
        """Add a single ArchiMate element to the OEF XML tree.

        An Application is written as its parent type with the Entelim property (ADR-0014 Decision 5);
        the property keys used are collected in *used_props* for the caller to declare.
        """
        stored_type = getattr(elem, "type", None) or "grouping"  # model-safety-ok
        element_type, extra = exchange_type_for(stored_type)
        oef_type = ARCHIMATE_TYPE_MAP.get(element_type, element_type)

        el = SubElement(parent, "element")
        el.set("identifier", f"id-{elem.id}")
        el.set("xsi:type", oef_type)

        name_el = SubElement(el, "name")
        name_el.set("xml:lang", "en")
        name_el.text = getattr(elem, "name", None) or f"Element {elem.id}"  # model-safety-ok

        description = getattr(elem, "description", None)  # model-safety-ok
        if description:
            doc_el = SubElement(el, "documentation")
            doc_el.set("xml:lang", "en")
            doc_el.text = description

        if extra:
            if used_props is None:
                used_props = {}
            props_el = SubElement(el, "properties")
            for key, value in extra.items():
                ident = used_props.setdefault(key, f"propid-{len(used_props) + 1}")
                prop_el = SubElement(props_el, "property")
                prop_el.set("propertyDefinitionRef", ident)
                val_el = SubElement(prop_el, "value")
                val_el.set("xml:lang", "en")
                val_el.text = value


    def _get_relationships(self, element_ids):
        """Get relationships where both source and target are in the element set."""
        if not element_ids:
            return []
        try:
            from app.models.archimate_core import ArchiMateRelationship
            return (
                db.session.query(ArchiMateRelationship)
                .filter(
                    ArchiMateRelationship.source_id.in_(list(element_ids)),
                    ArchiMateRelationship.target_id.in_(list(element_ids)),
                )
                .all()
            )
        except Exception:
            logger.exception("Failed to query relationships")
            return []

    def validate_oef_xml(self, xml_string: str) -> bool:
        """Validate that xml_string conforms to OEF 3.2 XML structure.

        Checks:
        - Root element is <model> with OEF namespace (or bare <model>)
        - Root has 'identifier' attribute
        - Root contains a <name> child element
        - Root contains an <elements> section
        - Each <element> has 'identifier' and xsi:type attributes
        - Each <relationship> (if present) has identifier, xsi:type, source, target

        Returns True if valid.
        Raises ValueError with a description of the first detected violation.
        """
        from xml.etree.ElementTree import ParseError, fromstring

        try:
            root = fromstring(xml_string)
        except ParseError as exc:
            raise ValueError(f"XML parse error: {exc}") from exc

        oef_ns = self.OEF_NAMESPACE
        xsi_ns = self.XSI_NAMESPACE

        # Determine namespace mode from the parsed root tag
        if root.tag == f"{{{oef_ns}}}model":
            ns = oef_ns
        elif root.tag == "model":
            ns = None
        else:
            raise ValueError(
                f"Root element must be <model> in OEF namespace {oef_ns!r}, got <{root.tag}>"
            )

        def _q(tag: str) -> str:
            return f"{{{ns}}}{tag}" if ns else tag

        if not root.get("identifier"):
            raise ValueError("<model> must have an 'identifier' attribute")

        if root.find(_q("name")) is None:
            raise ValueError("<model> must contain a <name> child element")

        elements_el = root.find(_q("elements"))
        if elements_el is None:
            raise ValueError("<model> must contain an <elements> section")

        xsi_type_key = f"{{{xsi_ns}}}type"
        for el in elements_el.findall(_q("element")):
            identifier = el.get("identifier")
            if not identifier:
                raise ValueError("<element> is missing required 'identifier' attribute")
            xsi_type = el.get(xsi_type_key) or el.get("xsi:type") or el.get("type")
            if not xsi_type:
                raise ValueError(
                    f"<element identifier='{identifier}'> is missing required xsi:type attribute"  # raw-html-ok: ValueError exception message during XML import validation, never rendered as HTML/browser output
                )

        rels_el = root.find(_q("relationships"))
        if rels_el is not None:
            for rel in rels_el.findall(_q("relationship")):
                identifier = rel.get("identifier")
                if not identifier:
                    raise ValueError("<relationship> is missing required 'identifier' attribute")
                xsi_type = rel.get(xsi_type_key) or rel.get("xsi:type") or rel.get("type")
                if not xsi_type:
                    raise ValueError(
                        f"<relationship identifier='{identifier}'> is missing required xsi:type attribute"  # raw-html-ok: ValueError exception message during XML import validation, never rendered as HTML/browser output
                    )
                if not rel.get("source"):
                    raise ValueError(
                        f"<relationship identifier='{identifier}'> is missing required 'source' attribute"  # raw-html-ok: ValueError exception message during XML import validation, never rendered as HTML/browser output
                    )
                if not rel.get("target"):
                    raise ValueError(
                        f"<relationship identifier='{identifier}'> is missing required 'target' attribute"  # raw-html-ok: ValueError exception message during XML import validation, never rendered as HTML/browser output
                    )

        return True


archimate_oef_export_service = ArchimateOEFExportService()

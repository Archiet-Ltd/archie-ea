"""
ArchiMate XML Export Service

Exports ArchiMate viewpoints and models to ArchiMate 3.2 XML format.
Follows the ArchiMate 3.2 XML schema specification.

SA-005: export_to_xml() produces valid Open Exchange Format (OEF) XML
        importable by Archi and Sparx EA.
"""

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime  # dead-code-ok: kept for class methods below
from typing import Dict, Optional
from xml.dom import minidom

from app.models import (
    ArchiMateElement,
    ArchiMateRelationship,
    ArchiMateViewpoint,
    ArchitectureModel,
)
from app.models.archimate_element_types import SPECIALIZATION_PROPERTY, exchange_type_for

# ---------------------------------------------------------------------------
# OEF namespace constants
# ---------------------------------------------------------------------------

_OEF_NS = "http://www.opengroup.org/xsd/archimate/3.0/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# Folder tree emitted in <organizations>, in ArchiMate layer order.
_LAYER_ORDER = [
    "Strategy", "Business", "Application", "Technology", "Physical",
    "Motivation", "Implementation & Migration", "Other",
]

_SCHEMA_LOC = (
    "http://www.opengroup.org/xsd/archimate/3.0/ "
    "http://www.opengroup.org/xsd/archimate/3.0/archimate3_Diagram.xsd"
)

# ---------------------------------------------------------------------------
# Mapping: internal element_type → OEF xsi:type
# ---------------------------------------------------------------------------

# OEF xsi:type -> ArchiMate layer, used to build the <organizations> folder tree.
_OEF_TYPE_LAYER = {
    **{t: "Strategy" for t in ("Resource", "Capability", "CourseOfAction", "ValueStream")},
    **{t: "Business" for t in (
        "BusinessActor", "BusinessRole", "BusinessCollaboration", "BusinessInterface",
        "BusinessProcess", "BusinessFunction", "BusinessInteraction", "BusinessEvent",
        "BusinessService", "BusinessObject", "Contract", "Representation", "Product")},
    **{t: "Application" for t in (
        "ApplicationComponent", "ApplicationCollaboration", "ApplicationInterface",
        "ApplicationFunction", "ApplicationInteraction", "ApplicationProcess",
        "ApplicationEvent", "ApplicationService", "DataObject")},
    **{t: "Technology" for t in (
        "Node", "Device", "SystemSoftware", "TechnologyCollaboration",
        "TechnologyInterface", "Path", "CommunicationNetwork", "TechnologyFunction",
        "TechnologyProcess", "TechnologyInteraction", "TechnologyEvent",
        "TechnologyService", "Artifact")},
    **{t: "Physical" for t in ("Equipment", "Facility", "DistributionNetwork", "Material")},
    **{t: "Motivation" for t in (
        "Stakeholder", "Driver", "Assessment", "Goal", "Outcome", "Principle",
        "Requirement", "Constraint", "Meaning", "Value")},
    **{t: "Implementation & Migration" for t in (
        "WorkPackage", "Deliverable", "ImplementationEvent", "Plateau", "Gap")},
}

OEF_ELEMENT_TYPE_MAP: Dict[str, str] = {
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
    # Strategy layer
    "resource": "Resource",
    "capability": "Capability",
    "course_of_action": "CourseOfAction",
    # Implementation & Migration layer
    "work_package": "WorkPackage",
    "deliverable": "Deliverable",
    "implementation_event": "ImplementationEvent",
    "plateau": "Plateau",
    "gap": "Gap",
}

# ---------------------------------------------------------------------------
# Mapping: internal relationship_type → OEF xsi:type
# ---------------------------------------------------------------------------

OEF_RELATIONSHIP_TYPE_MAP: Dict[str, str] = {
    "association": "Association",
    "composition": "Composition",
    "aggregation": "Aggregation",
    "realization": "Realization",
    "serving": "Serving",
    "access": "Access",
    "influence": "Influence",
    "triggering": "Triggering",
    "flow": "Flow",
    "specialization": "Specialization",
    "assignment": "Assignment",
}


def _elem_oef_type(raw_type: Optional[str]) -> str:
    """Resolve an element's OEF xsi:type from its stored type string.

    A specialisation (an Application) is written as its standard parent; the property that
    names it is added by ``_exchange_properties`` (ADR-0014 Decision 5).
    """
    exchange_type, _ = exchange_type_for(raw_type)
    if not exchange_type:
        return "ApplicationComponent"
    key = exchange_type.lower().replace("-", "_").replace(" ", "_")
    return OEF_ELEMENT_TYPE_MAP.get(key, exchange_type)


def _exchange_properties(elem, *sources) -> Dict[str, str]:
    """An element's properties as written to a file: stored ones, then the exchange mapping's.

    The specialisation property is never copied from storage, because the stored type already
    records it; it is written only when the type is a specialisation.
    """
    merged: Dict[str, str] = {}
    for source in sources:
        merged.update(_coerce_properties(source))
    for key in [k for k in merged if k.strip().lower() == SPECIALIZATION_PROPERTY]:
        del merged[key]
    merged.update(exchange_type_for(getattr(elem, "type", None))[1])
    return merged


def _rel_oef_type(raw_type: Optional[str]) -> str:
    """Resolve a relationship's OEF xsi:type from its stored type string."""
    if not raw_type:
        return "Association"
    key = raw_type.lower().replace("-", "_").replace(" ", "_")
    return OEF_RELATIONSHIP_TYPE_MAP.get(key, raw_type)


def export_to_xml(model_id: Optional[int] = None) -> str:
    """Export ArchiMate elements and relationships to OEF XML.

    Produces a valid ArchiMate 3.0 Open Exchange Format XML string
    importable by Archi and Sparx EA.

    Args:
        model_id: Optional ArchitectureModel ID to scope the export.
                  When None, all elements/relationships are exported.

    Returns:
        UTF-8 XML string (without XML declaration prefix; use as-is).
    """
    ET.register_namespace("", _OEF_NS)
    ET.register_namespace("xsi", _XSI_NS)

    # Resolve model metadata
    model_name = "Architecture Model"
    model_identifier = f"id-{uuid.uuid4()}"
    if model_id is not None:
        try:
            model = ArchitectureModel.query.get(model_id)
            if model:
                model_name = model.name or model_name
                model_identifier = f"id-model-{model_id}"
        except Exception:  # noqa: BLE001 — tolerate missing DB in tests
            model_identifier = f"id-model-{model_id}"

    root = ET.Element(
        f"{{{_OEF_NS}}}model",
        attrib={
            f"{{{_XSI_NS}}}schemaLocation": _SCHEMA_LOC,
            "identifier": model_identifier,
        },
    )

    name_el = ET.SubElement(root, f"{{{_OEF_NS}}}name")
    name_el.set(_XML_LANG, "en")
    name_el.text = model_name

    # --- Query elements ---
    elem_query = ArchiMateElement.query
    if model_id is not None:
        elem_query = elem_query.filter_by(architecture_id=model_id)
    elements = elem_query.all()

    # --- Query relationships ---
    rel_query = ArchiMateRelationship.query
    if model_id is not None:
        rel_query = rel_query.filter_by(architecture_id=model_id)
    relationships = rel_query.all()

    # Property keys are declared once in <propertyDefinitions> and referenced by
    # identifier from each element, so they must be collected up front.
    prop_defs: Dict[str, str] = {}   # key -> propertyDefinition identifier
    elem_props: Dict[int, Dict[str, str]] = {}
    for elem in elements:
        merged = _exchange_properties(
            elem, getattr(elem, "properties", None), getattr(elem, "custom_properties", None)
        )
        if merged:
            elem_props[elem.id] = merged
            for key in merged:
                if key not in prop_defs:
                    prop_defs[key] = f"propid-{len(prop_defs) + 1}"

    # <elements>
    elements_el = ET.SubElement(root, f"{{{_OEF_NS}}}elements")
    for elem in elements:
        oef_type = _elem_oef_type(getattr(elem, "type", None))
        el_node = ET.SubElement(
            elements_el,
            f"{{{_OEF_NS}}}element",
            attrib={
                "identifier": f"id-{elem.id}",
                f"{{{_XSI_NS}}}type": oef_type,
            },
        )
        el_name = ET.SubElement(el_node, f"{{{_OEF_NS}}}name")
        el_name.set(_XML_LANG, "en")
        el_name.text = getattr(elem, "name", "") or ""
        desc = getattr(elem, "description", None)
        if desc:
            doc_el = ET.SubElement(el_node, f"{{{_OEF_NS}}}documentation")
            doc_el.set(_XML_LANG, "en")
            doc_el.text = desc
        # Order inside <element> is fixed by the schema: name, documentation,
        # then properties.
        _emit_properties(el_node, elem_props.get(elem.id), prop_defs)

    # <relationships>
    relationships_el = ET.SubElement(root, f"{{{_OEF_NS}}}relationships")
    exported_rel_ids = set()
    for rel in relationships:
        src = getattr(rel, "source_id", None)
        tgt = getattr(rel, "target_id", None)
        if src is None or tgt is None:
            continue
        oef_rel_type = _rel_oef_type(getattr(rel, "type", None))
        ET.SubElement(
            relationships_el,
            f"{{{_OEF_NS}}}relationship",
            attrib={
                "identifier": f"id-rel-{rel.id}",
                f"{{{_XSI_NS}}}type": oef_rel_type,
                "source": f"id-{src}",
                "target": f"id-{tgt}",
            },
        )
        exported_rel_ids.add(rel.id)

    # <organizations> — the folder tree an importing tool shows in its model
    # browser. Without it Archi drops everything into one flat default folder,
    # so a large model arrives technically complete but unusable.
    _emit_organizations(root, elements)

    # <propertyDefinitions> — must follow <organizations> and precede <views>.
    if prop_defs:
        defs_el = ET.SubElement(root, f"{{{_OEF_NS}}}propertyDefinitions")
        for key, ident in prop_defs.items():
            definition = ET.SubElement(
                defs_el,
                f"{{{_OEF_NS}}}propertyDefinition",
                attrib={"identifier": ident, "type": "string"},
            )
            d_name = ET.SubElement(definition, f"{{{_OEF_NS}}}name")
            d_name.set(_XML_LANG, "en")
            d_name.text = key

    # <views> — diagram geometry. This is what makes the export a model rather
    # than a data dump: without it every saved layout is lost on import.
    _emit_views(root, {e.id for e in elements}, exported_rel_ids)

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _coerce_properties(raw) -> Dict[str, str]:
    """Normalise a JSON / text / dict property bag into flat {str: str}."""
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): ("" if v is None else str(v))
        for k, v in raw.items()
        if k is not None and not isinstance(v, (dict, list))
    }


def _emit_properties(parent, props, prop_defs) -> None:
    if not props:
        return
    props_el = ET.SubElement(parent, f"{{{_OEF_NS}}}properties")
    for key, value in props.items():
        prop = ET.SubElement(
            props_el,
            f"{{{_OEF_NS}}}property",
            attrib={"propertyDefinitionRef": prop_defs[key]},
        )
        val = ET.SubElement(prop, f"{{{_OEF_NS}}}value")
        val.set(_XML_LANG, "en")
        val.text = value


def _emit_organizations(root, elements) -> None:
    """Group elements into one <item> folder per ArchiMate layer."""
    by_layer: Dict[str, list] = {}
    for elem in elements:
        oef_type = _elem_oef_type(getattr(elem, "type", None))
        by_layer.setdefault(_OEF_TYPE_LAYER.get(oef_type, "Other"), []).append(elem.id)
    if not by_layer:
        return
    orgs = ET.SubElement(root, f"{{{_OEF_NS}}}organizations")
    for layer in _LAYER_ORDER:
        ids = by_layer.get(layer)
        if not ids:
            continue
        item = ET.SubElement(orgs, f"{{{_OEF_NS}}}item")
        label = ET.SubElement(item, f"{{{_OEF_NS}}}label")
        label.set(_XML_LANG, "en")
        label.text = layer
        for eid in ids:
            ET.SubElement(item, f"{{{_OEF_NS}}}item", attrib={"identifierRef": f"id-{eid}"})


def _emit_views(root, element_ids, relationship_ids) -> None:
    """Emit <views><diagrams><view> with node geometry and connections.

    Scoped to the exported model by way of the elements each diagram places,
    because SavedDiagram itself carries no architecture_id.
    """
    try:
        from app.models.archimate_core import (
            SavedDiagram,
            SavedDiagramElement,
            SavedDiagramRelationship,
        )
    except Exception:  # noqa: BLE001 — views are optional; never fail the export
        return

    try:
        placements = (
            SavedDiagramElement.query.filter(
                SavedDiagramElement.element_id.in_(element_ids)
            ).all()
            if element_ids
            else []
        )
        if not placements:
            return
        diagram_ids = {p.diagram_id for p in placements}
        diagrams = SavedDiagram.query.filter(SavedDiagram.id.in_(diagram_ids)).all()
        connections = SavedDiagramRelationship.query.filter(
            SavedDiagramRelationship.diagram_id.in_(diagram_ids)
        ).all()
    except Exception:  # noqa: BLE001 — a missing table must not break the export
        return

    if not diagrams:
        return

    views = ET.SubElement(root, f"{{{_OEF_NS}}}views")
    diagrams_el = ET.SubElement(views, f"{{{_OEF_NS}}}diagrams")
    for diagram in diagrams:
        view = ET.SubElement(
            diagrams_el,
            f"{{{_OEF_NS}}}view",
            attrib={
                "identifier": f"id-view-{diagram.id}",
                f"{{{_XSI_NS}}}type": "Diagram",
            },
        )
        v_name = ET.SubElement(view, f"{{{_OEF_NS}}}name")
        v_name.set(_XML_LANG, "en")
        v_name.text = getattr(diagram, "name", "") or f"View {diagram.id}"

        # A <connection>'s source/target reference NODE identifiers, not element
        # identifiers, so the element -> node mapping for this view is needed.
        node_for_element = {}
        for placement in placements:
            if placement.diagram_id != diagram.id:
                continue
            node_id = f"id-node-{placement.id}"
            node_for_element[placement.element_id] = node_id
            ET.SubElement(
                view,
                f"{{{_OEF_NS}}}node",
                attrib={
                    "identifier": node_id,
                    "elementRef": f"id-{placement.element_id}",
                    f"{{{_XSI_NS}}}type": "Element",
                    "x": str(int(placement.position_x or 0)),
                    "y": str(int(placement.position_y or 0)),
                    "w": str(int(placement.width or 180)),
                    "h": str(int(placement.height or 64)),
                },
            )

        for conn in connections:
            if conn.diagram_id != diagram.id or conn.relationship_id not in relationship_ids:
                continue
            rel = ArchiMateRelationship.query.get(conn.relationship_id)
            if rel is None:
                continue
            src_node = node_for_element.get(rel.source_id)
            tgt_node = node_for_element.get(rel.target_id)
            # Both endpoints must be placed on THIS view or the reference dangles.
            if not src_node or not tgt_node:
                continue
            ET.SubElement(
                view,
                f"{{{_OEF_NS}}}connection",
                attrib={
                    "identifier": f"id-conn-{conn.id}",
                    "relationshipRef": f"id-rel-{conn.relationship_id}",
                    f"{{{_XSI_NS}}}type": "Relationship",
                    "source": src_node,
                    "target": tgt_node,
                },
            )


class ArchiMateXMLExportService:
    """
    Service for exporting ArchiMate models to XML format.

    Supports:
    - Exporting complete architecture models
    - Exporting viewpoints
    - ArchiMate 3.2 XML schema compliance
    """

    def __init__(self):
        pass

    def export_model_to_xml(self, architecture_id: int) -> str:
        """
        Export an architecture model to ArchiMate XML format.

        Args:
            architecture_id: ID of the ArchitectureModel to export

        Returns:
            XML string in ArchiMate 3.2 format
        """
        model = ArchitectureModel.query.get(architecture_id)
        if not model:
            raise ValueError(f"Architecture model {architecture_id} not found")

        # Create root element
        root = ET.Element(
            "archimate:model",
            {
                "xmlns:archimate": "http://www.archimatetool.com/archimate",
                "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "xsi:schemaLocation": "http://www.archimatetool.com/archimate http://www.archimatetool.com/archimate",
                "name": model.name or "Architecture Model",
                "id": str(model.id),
                "version": "3.2",
            },
        )

        # Add metadata
        metadata = ET.SubElement(root, "metadata")
        ET.SubElement(
            metadata, "property", {"key": "created", "value": datetime.utcnow().isoformat()}
        )
        ET.SubElement(
            metadata, "property", {"key": "description", "value": model.description or ""}
        )

        # Add elements
        elements = ET.SubElement(root, "elements")
        for element in model.archimate_elements.all():
            self._add_element_to_xml(elements, element)

        # Add relationships
        relationships = ET.SubElement(root, "relationships")
        for relationship in model.archimate_relationships.all():
            self._add_relationship_to_xml(relationships, relationship)

        # Add views (viewpoints)
        ET.SubElement(root, "views")
        # Viewpoints would be added here if needed

        # Convert to pretty XML string
        xml_str = ET.tostring(root, encoding="unicode")
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent="  ")

    def export_viewpoint_to_xml(self, viewpoint_id: int, architecture_id: int) -> str:
        """
        Export a viewpoint to ArchiMate XML format.

        Args:
            viewpoint_id: ID of the ArchiMateViewpoint
            architecture_id: ID of the ArchitectureModel

        Returns:
            XML string in ArchiMate 3.2 format
        """
        viewpoint = ArchiMateViewpoint.query.get(viewpoint_id)
        if not viewpoint:
            raise ValueError(f"Viewpoint {viewpoint_id} not found")

        model = ArchitectureModel.query.get(architecture_id)
        if not model:
            raise ValueError(f"Architecture model {architecture_id} not found")

        # Create root element
        root = ET.Element(
            "archimate:model",
            {
                "xmlns:archimate": "http://www.archimatetool.com/archimate",
                "name": viewpoint.name or "Viewpoint",
                "id": str(viewpoint.id),
                "version": "3.2",
            },
        )

        # Add metadata
        metadata = ET.SubElement(root, "metadata")
        ET.SubElement(metadata, "property", {"key": "viewpoint", "value": viewpoint.name})
        ET.SubElement(metadata, "property", {"key": "purpose", "value": viewpoint.purpose or ""})

        # Filter elements based on viewpoint
        from app.services.archimate.archimate_viewpoint_service import ArchiMateViewpointService

        viewpoint_service = ArchiMateViewpointService()
        viewpoint_data = viewpoint_service.generate_viewpoint(model, viewpoint.name)

        # Add filtered elements
        elements = ET.SubElement(root, "elements")
        for element in viewpoint_data.get("elements", []):
            self._add_element_to_xml(elements, element)

        # Add filtered relationships
        relationships = ET.SubElement(root, "relationships")
        for relationship in viewpoint_data.get("relationships", []):
            self._add_relationship_to_xml(relationships, relationship)

        # Convert to pretty XML string
        xml_str = ET.tostring(root, encoding="unicode")
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent="  ")

    def _add_element_to_xml(self, parent: ET.Element, element: ArchiMateElement):
        """Add an ArchiMate element to XML."""
        exchange_type, extra = exchange_type_for(element.type)
        elem = ET.SubElement(
            parent,
            "element",
            {
                "id": str(element.id),
                "name": element.name or "",
                "type": exchange_type or "",
                "layer": element.layer or "",
            },
        )

        if element.description:
            ET.SubElement(elem, "documentation").text = element.description

        # Add properties if they exist
        props_el = None
        if hasattr(element, "properties") and element.properties:
            props_el = ET.SubElement(elem, "properties")
            # Parse and add properties (would need to handle JSON properties)
        if extra:
            # The specialisation travels as a property (ADR-0014 Decision 5).
            if props_el is None:
                props_el = ET.SubElement(elem, "properties")
            for key, value in extra.items():
                ET.SubElement(props_el, "property", {"key": key, "value": value})

    def _add_relationship_to_xml(self, parent: ET.Element, relationship: ArchiMateRelationship):
        """Add an ArchiMate relationship to XML."""
        rel = ET.SubElement(
            parent,
            "relationship",
            {
                "id": str(relationship.id),
                "source": str(relationship.source_id),
                "target": str(relationship.target_id),
                "type": relationship.type or "",
            },
        )

        if relationship.properties:
            ET.SubElement(rel, "properties")
            # Parse and add properties

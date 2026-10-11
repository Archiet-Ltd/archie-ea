"""
ArchiMate Open Exchange Format export service (AV-012).

Converts a viewpoint dictionary (as returned by PhaseViewpointBindingService)
into a standards-compliant ArchiMate Open Exchange XML document.

Spec ref: The Open Group ArchiMate 3.2 Exchange File Format (October 2022)
"""
import xml.etree.ElementTree as ET
from typing import Any, Dict, List
from xml.dom import minidom


# QA 3.5: The Open Group ArchiMate Model Exchange File Format keeps the
# `.../archimate/3.0/` namespace URI across ArchiMate 3.0/3.1/3.2 — there is no
# `3.2/` namespace or XSD at that host. Archi (the reference tool) and the
# official validator both use the 3.0 namespace with `archimate3_Model.xsd`.
# The bug was a 3.0 namespace declaration paired with a 3.1 *diagram-only* XSD
# in schemaLocation; fix by pointing schemaLocation at the model XSD under the
# same 3.0 namespace so strict validators accept the document.
_OEF_NS = "http://www.opengroup.org/xsd/archimate/3.0/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_XSI_SCHEMA_LOC = (
    "http://www.opengroup.org/xsd/archimate/3.0/ "
    "http://www.opengroup.org/xsd/archimate/3.0/archimate3_Model.xsd"
)


def to_open_exchange_xml(viewpoint_dict: Dict[str, Any]) -> str:
    """
    Converts a viewpoint dictionary into ArchiMate 3.2 Open Exchange Format XML.

    viewpoint_dict structure (subset used):
        {
          "viewpoint_name": str,
          "phase_name": str,
          "elements": [
            {"id": int, "name": str, "type": str, "layer": str, "description": str|None},
            ...
          ],
          "relationships": [
            {"id": int, "source_id": int, "target_id": int, "type": str},
            ...
          ]
        }

    Returns a UTF-8 XML string.
    """
    ET.register_namespace("", _OEF_NS)
    ET.register_namespace("xsi", _XSI_NS)

    root = ET.Element(
        f"{{{_OEF_NS}}}model",
        attrib={
            f"{{{_XSI_NS}}}schemaLocation": _XSI_SCHEMA_LOC,
            "identifier": _safe_id(viewpoint_dict.get("viewpoint_name", "export")),
        },
    )

    # <name>
    name_el = ET.SubElement(root, f"{{{_OEF_NS}}}name")
    name_el.text = viewpoint_dict.get("viewpoint_name", "ArchiMate Export")

    # <documentation>
    doc_el = ET.SubElement(root, f"{{{_OEF_NS}}}documentation")
    doc_el.text = (
        f"Generated from TOGAF ADM phase: {viewpoint_dict.get('phase_name', 'unknown')}. "
        "Exported by Entelim platform."
    )

    # <elements>
    elements: List[Dict] = viewpoint_dict.get("elements", [])
    if elements:
        elements_el = ET.SubElement(root, f"{{{_OEF_NS}}}elements")
        for el in elements:
            elem_node = ET.SubElement(
                elements_el,
                f"{{{_OEF_NS}}}element",
                attrib={
                    "identifier": f"id-{el.get('id', '')}",
                    "xsi:type": el.get("type", "ApplicationComponent"),
                },
            )
            label = ET.SubElement(elem_node, f"{{{_OEF_NS}}}name")
            label.text = el.get("name", "")
            desc = el.get("description") or el.get("description_text") or ""
            if desc:
                doc_node = ET.SubElement(elem_node, f"{{{_OEF_NS}}}documentation")
                doc_node.text = desc

    # <relationships>
    relationships: List[Dict] = viewpoint_dict.get("relationships", [])
    if relationships:
        rels_el = ET.SubElement(root, f"{{{_OEF_NS}}}relationships")
        for rel in relationships:
            rel_node = ET.SubElement(
                rels_el,
                f"{{{_OEF_NS}}}relationship",
                attrib={
                    "identifier": f"rel-{rel.get('id', '')}",
                    "xsi:type": rel.get("type", "Association"),
                    "source": f"id-{rel.get('source_id', '')}",
                    "target": f"id-{rel.get('target_id', '')}",
                },
            )
            _ = rel_node  # node created; no child elements required for relationships

    # <views> — minimal single diagram view for the viewpoint
    views_el = ET.SubElement(root, f"{{{_OEF_NS}}}views")
    diagrams_el = ET.SubElement(views_el, f"{{{_OEF_NS}}}diagrams")
    view_el = ET.SubElement(
        diagrams_el,
        f"{{{_OEF_NS}}}view",
        attrib={
            "identifier": _safe_id(viewpoint_dict.get("viewpoint_name", "view") + "-diagram"),
            "viewpoint": viewpoint_dict.get("viewpoint_name", ""),
        },
    )
    view_name = ET.SubElement(view_el, f"{{{_OEF_NS}}}name")
    view_name.text = viewpoint_dict.get("viewpoint_name", "View")

    # Add node references for each element (no layout coordinates — valid minimal OEF)
    for el in elements:
        ET.SubElement(
            view_el,
            f"{{{_OEF_NS}}}node",
            attrib={
                "identifier": f"node-{el.get('id', '')}",
                "elementRef": f"id-{el.get('id', '')}",
                "xsi:type": "Element",
            },
        )

    return _pretty_print(root)


def load_viewpoint_dict(viewpoint_id: int) -> Dict[str, Any]:
    """Load a SavedDiagram by ID into the canonical viewpoint dict used by every
    composer exporter (OEF, Mermaid, Lucid, Archi).

    Returns {viewpoint_name, phase_name, elements[], relationships[]} with x/y/w/h
    on elements. Raises ValueError if the viewpoint is not found.
    """
    from app import db
    from app.models.archimate_core import (
        ArchiMateElement,
        ArchiMateRelationship,
        SavedDiagram,
    )

    vp = db.session.get(SavedDiagram, viewpoint_id)
    if not vp:
        raise ValueError(f"SavedDiagram {viewpoint_id} not found")

    positions = vp.positions.all()
    element_ids = [p.element_id for p in positions]
    pos_map = {p.element_id: p for p in positions}

    elements: List[Dict[str, Any]] = []
    if element_ids:
        els = ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
        for el in els:
            p = pos_map.get(el.id)
            elements.append({
                "id": el.id,
                "name": el.name,
                "type": el.type or "ApplicationComponent",
                "layer": el.layer or "",
                "description": el.description or "",
                "x": p.position_x if p else 0,
                "y": p.position_y if p else 0,
                "w": p.width if p else 180,
                "h": p.height if p else 64,
            })

    relationships: List[Dict[str, Any]] = []
    if element_ids:
        rels = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(element_ids),
            ArchiMateRelationship.target_id.in_(element_ids),
        ).all()
        for r in rels:
            relationships.append({
                "id": r.id,
                "source_id": r.source_id,
                "target_id": r.target_id,
                "type": r.type or "Association",
                "label": getattr(r, "name", None) or "",
            })

    return {
        "viewpoint_name": vp.name or f"Viewpoint {viewpoint_id}",
        "phase_name": vp.viewpoint_type or "Architecture",
        "elements": elements,
        "relationships": relationships,
    }


def export_saved_viewpoint(viewpoint_id: int) -> str:
    """Load a SavedDiagram by ID and export it as ArchiMate Exchange Format XML.

    Returns a UTF-8 XML string. Raises ValueError if the viewpoint is not found.
    """
    return _export_with_layout(load_viewpoint_dict(viewpoint_id))


def _export_with_layout(viewpoint_dict: Dict[str, Any]) -> str:
    """Like to_open_exchange_xml but includes x/y/w/h on diagram nodes."""
    ET.register_namespace("", _OEF_NS)
    ET.register_namespace("xsi", _XSI_NS)

    root = ET.Element(
        f"{{{_OEF_NS}}}model",
        attrib={
            f"{{{_XSI_NS}}}schemaLocation": _XSI_SCHEMA_LOC,
            "identifier": _safe_id(viewpoint_dict.get("viewpoint_name", "export")),
        },
    )

    # <name>
    name_el = ET.SubElement(root, f"{{{_OEF_NS}}}name")
    name_el.text = viewpoint_dict.get("viewpoint_name", "ArchiMate Export")

    # <documentation>
    doc_el = ET.SubElement(root, f"{{{_OEF_NS}}}documentation")
    doc_el.text = (
        f"Viewpoint type: {viewpoint_dict.get('phase_name', 'unknown')}. "
        "Exported by Entelim platform."
    )

    # <elements>
    elements: List[Dict] = viewpoint_dict.get("elements", [])
    if elements:
        elements_el = ET.SubElement(root, f"{{{_OEF_NS}}}elements")
        for el in elements:
            elem_node = ET.SubElement(
                elements_el,
                f"{{{_OEF_NS}}}element",
                attrib={
                    "identifier": f"id-{el.get('id', '')}",
                    "xsi:type": el.get("type", "ApplicationComponent"),
                },
            )
            label = ET.SubElement(elem_node, f"{{{_OEF_NS}}}name")
            label.text = el.get("name", "")
            desc = el.get("description") or ""
            if desc:
                doc_node = ET.SubElement(elem_node, f"{{{_OEF_NS}}}documentation")
                doc_node.text = desc

    # <relationships>
    relationships: List[Dict] = viewpoint_dict.get("relationships", [])
    if relationships:
        rels_el = ET.SubElement(root, f"{{{_OEF_NS}}}relationships")
        for rel in relationships:
            ET.SubElement(
                rels_el,
                f"{{{_OEF_NS}}}relationship",
                attrib={
                    "identifier": f"rel-{rel.get('id', '')}",
                    "xsi:type": rel.get("type", "Association"),
                    "source": f"id-{rel.get('source_id', '')}",
                    "target": f"id-{rel.get('target_id', '')}",
                },
            )

    # <views> with layout coordinates
    views_el = ET.SubElement(root, f"{{{_OEF_NS}}}views")
    diagrams_el = ET.SubElement(views_el, f"{{{_OEF_NS}}}diagrams")
    view_el = ET.SubElement(
        diagrams_el,
        f"{{{_OEF_NS}}}view",
        attrib={
            "identifier": _safe_id(viewpoint_dict.get("viewpoint_name", "view") + "-diagram"),
            "viewpoint": viewpoint_dict.get("viewpoint_name", ""),
        },
    )
    view_name = ET.SubElement(view_el, f"{{{_OEF_NS}}}name")
    view_name.text = viewpoint_dict.get("viewpoint_name", "View")

    # Nodes with position data
    node_id_map = {}
    for el in elements:
        node_id = f"node-{el.get('id', '')}"
        node_id_map[el.get("id")] = node_id
        attribs = {
            "identifier": node_id,
            "elementRef": f"id-{el.get('id', '')}",
            "xsi:type": "Element",
            "x": str(int(el.get("x", 0))),
            "y": str(int(el.get("y", 0))),
            "w": str(int(el.get("w", 180))),
            "h": str(int(el.get("h", 64))),
        }
        ET.SubElement(view_el, f"{{{_OEF_NS}}}node", attrib=attribs)

    # Connections
    for rel in relationships:
        src_node = node_id_map.get(rel.get("source_id"), "")
        tgt_node = node_id_map.get(rel.get("target_id"), "")
        if src_node and tgt_node:
            ET.SubElement(
                view_el,
                f"{{{_OEF_NS}}}connection",
                attrib={
                    "identifier": f"conn-{rel.get('id', '')}",
                    "relationshipRef": f"rel-{rel.get('id', '')}",
                    "source": src_node,
                    "target": tgt_node,
                },
            )

    return _pretty_print(root)


def _safe_id(name: str) -> str:
    """Convert a display name to a safe XML identifier string."""
    return "id-" + "".join(c if c.isalnum() or c == "-" else "_" for c in name.lower())[:64]


def _pretty_print(root: ET.Element) -> str:
    """Return a formatted, indented XML string."""
    raw = ET.tostring(root, encoding="unicode", xml_declaration=False)
    reparsed = minidom.parseString(f'<?xml version="1.0" encoding="UTF-8"?>{raw}')
    return reparsed.toprettyxml(indent="  ", encoding=None)

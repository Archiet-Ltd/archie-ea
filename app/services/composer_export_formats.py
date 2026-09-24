"""Composer diagram exporters: Mermaid, Lucidchart, and Archi (.archimate).

Each renders the canonical viewpoint dict produced by
``archimate_export_service.load_viewpoint_dict`` — the same source the ArchiMate
Open Exchange (OEF) export uses — into one more interchange format:

* Mermaid     — a ``flowchart`` for markdown / wikis / PRs.
* Lucidchart  — a native ``.lucid`` ZIP (document.json in Lucid Standard Import
                format) that re-opens in Lucid AND round-trips back into Entelim.
* Archi       — the Archi tool's native ``.archimate`` model XML.

All three preserve element names, ArchiMate types, relationships, and (where the
format supports it) the diagram layout.
"""

import io
import json
import zipfile
from typing import Any, Dict, List

# Native ArchiMate layer fills (match the composer's LAYER_COLORS).
_LAYER_FILL = {
    "business": "#FFFFB5",
    "application": "#B5FFFF",
    "technology": "#C9E7B7",
    "motivation": "#CCCCFF",
    "strategy": "#F5DEAA",
    "implementation": "#FFE0E0",
    "physical": "#C9E7B7",
    "other": "#EEEEEE",
}

# Canonical ArchiMate relationship names (PascalCase) keyed by the lower-case
# value stored on relationships.
_REL_CANONICAL = {
    "composition": "Composition",
    "aggregation": "Aggregation",
    "assignment": "Assignment",
    "realization": "Realization",
    "realisation": "Realization",
    "serving": "Serving",
    "access": "Access",
    "influence": "Influence",
    "triggering": "Triggering",
    "flow": "Flow",
    "association": "Association",
    "specialization": "Specialization",
    "specialisation": "Specialization",
}

# ArchiMate element type → Lucidchart shape class (inverse of the importer's map).
_TYPE_TO_LUCID_CLASS = {
    "ApplicationService": "ArchiMate3ServiceBoxBlock",
    "ApplicationComponent": "ArchiMate3ComponentBoxBlock",
    "ApplicationInterface": "ArchiMate3InterfaceBoxBlock",
    "ApplicationFunction": "ArchiMate3FunctionBoxBlock",
    "DataObject": "ArchiMate3ObjectBoxBlock",
    "Location": "ArchiMate3LocationBoxBlock",
    "CommunicationNetwork": "ArchiMate3CommunicationNetworkBoxBlock",
    "Grouping": "ArchiMate3GroupingBoxBlock",
    "BusinessActor": "ArchiMate3ActorBoxBlock",
    "BusinessRole": "ArchiMate3RoleBoxBlock",
    "BusinessProcess": "ArchiMate3ProcessBoxBlock",
    "BusinessService": "ArchiMate3ServiceBoxBlock",
    "BusinessObject": "ArchiMate3ObjectBoxBlock",
    "Node": "ArchiMate3NodeBoxBlock",
    "Artifact": "ArchiMate3ArtifactBoxBlock",
}


def _canonical_rel(rel_type: str) -> str:
    return _REL_CANONICAL.get((rel_type or "").strip().lower(), "Association")


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


# --------------------------------------------------------------------------- #
# Mermaid                                                                      #
# --------------------------------------------------------------------------- #

# ArchiMate relationship → Mermaid edge operator. Restricted to operators that
# reliably accept a |label| (the relationship name is carried in the label, so the
# arrow style only conveys solid/dotted/thick). Avoids invalid operators that would
# break the Mermaid parser.
_REL_MERMAID = {
    "Composition": "==>",
    "Aggregation": "==>",
    "Realization": "-.->",
    "Serving": "-->",
    "Triggering": "-->",
    "Flow": "-.->",
    "Access": "-.->",
    "Assignment": "-->",
    "Influence": "-.->",
    "Specialization": "-->",
    "Association": "-->",
}


def to_mermaid(vp: Dict[str, Any]) -> str:
    """Render the viewpoint as a Mermaid flowchart."""
    lines: List[str] = ["flowchart LR"]
    layers_used = set()

    for el in vp.get("elements", []):
        nid = f"n{el['id']}"
        label = _clean(el.get("name")) or el.get("type", "Element")
        label = label.replace('"', "'")
        type_tag = el.get("type", "")
        # node text shows the ArchiMate type as a stereotype line, like the tool does
        text = f"«{type_tag}»<br/>{label}" if type_tag else label
        lines.append(f'    {nid}["{text}"]')
        layer = (el.get("layer") or "other").lower()
        if layer in _LAYER_FILL:
            lines.append(f"    class {nid} {layer}")
            layers_used.add(layer)

    for rel in vp.get("relationships", []):
        rtype = _canonical_rel(rel.get("type"))
        op = _REL_MERMAID.get(rtype, "-->")
        label = _clean(rel.get("label")) or rtype
        label = label.replace('"', "'")
        lines.append(f'    n{rel["source_id"]} {op}|"{label}"| n{rel["target_id"]}')

    for layer in sorted(layers_used):
        fill = _LAYER_FILL[layer]
        lines.append(f"    classDef {layer} fill:{fill},stroke:#1a1a1a,color:#1a1a1a;")

    reasons = vp.get("reasons") or []
    if reasons:
        lines.append("")
        for r in reasons:
            lines.append(f"    %% {r['box_key']}: {r['reason']}")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Lucidchart (.lucid ZIP, Standard Import document.json)                       #
# --------------------------------------------------------------------------- #

def to_lucid_document(vp: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Lucid Standard Import document.json for this viewpoint."""
    shapes = []
    for el in vp.get("elements", []):
        shapes.append({
            "id": f"e{el['id']}",
            "class": _TYPE_TO_LUCID_CLASS.get(el.get("type"), "ArchiMate3ObjectBoxBlock"),
            "boundingBox": {
                "x": int(el.get("x", 0)), "y": int(el.get("y", 0)),
                "w": int(el.get("w", 180)) or 180, "h": int(el.get("h", 64)) or 64,
            },
            "textAreas": [{"label": "Text", "text": _clean(el.get("name"))}],
            "customData": [{"key": "ArchiMateType", "value": el.get("type", "")}],
            "linkedData": [],
        })

    lines = []
    for rel in vp.get("relationships", []):
        rtype = _canonical_rel(rel.get("type"))
        endpoint2_style = "Generalization" if rtype == "Specialization" else "Arrow"
        label = _clean(rel.get("label")) or rtype
        lines.append({
            "id": f"r{rel['id']}",
            "endpoint1": {"style": "None", "connectedTo": f"e{rel['source_id']}"},
            "endpoint2": {"style": endpoint2_style, "connectedTo": f"e{rel['target_id']}"},
            "textAreas": [{"label": "t0", "text": label}],
            "customData": [],
            "linkedData": [],
        })

    document = {
        "version": 1,
        "title": vp.get("viewpoint_name", "Entelim Export"),
        "product": "lucidchart",
        "pages": [{
            "id": "page-1",
            "title": vp.get("viewpoint_name", "Page 1"),
            "shapes": shapes,
            "lines": lines,
        }],
    }
    reasons = vp.get("reasons") or []
    if reasons:
        # Metadata only — no shape is added for a reason, so re-importing this
        # file back into the composer never turns an empty box into a
        # fabricated element. A key of its own so it can never collide with
        # a Lucid-native key.
        document["notes"] = [f"{r['box_key']}: {r['reason']}" for r in reasons]
    return document


def to_lucid_zip(vp: Dict[str, Any]) -> bytes:
    """Render a native ``.lucid`` ZIP (document.json) for this viewpoint."""
    document = to_lucid_document(vp)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("document.json", json.dumps(document))
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Archi native .archimate model XML                                           #
# --------------------------------------------------------------------------- #

import xml.etree.ElementTree as ET  # noqa: E402
from xml.dom import minidom  # noqa: E402

_ARCHI_NS = "http://www.archimatetool.com/archimate"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# ArchiMate layer → Archi folder name + type.
_LAYER_FOLDER = {
    "business": ("Business", "business"),
    "application": ("Application", "application"),
    "technology": ("Technology", "technology"),
    "physical": ("Technology", "technology"),
    "motivation": ("Motivation", "motivation"),
    "strategy": ("Strategy", "strategy"),
    "implementation": ("Implementation & Migration", "implementation_migration"),
    "other": ("Other", "other"),
}


def to_archi(vp: Dict[str, Any]) -> str:
    """Render the viewpoint as Archi's native ``.archimate`` model XML."""
    ET.register_namespace("archimate", _ARCHI_NS)
    ET.register_namespace("xsi", _XSI_NS)

    root = ET.Element(
        f"{{{_ARCHI_NS}}}model",
        attrib={
            f"{{{_XSI_NS}}}schemaLocation": (
                f"{_ARCHI_NS} http://www.archimatetool.com/archimate/archimate.xsd"
            ),
            "name": vp.get("viewpoint_name", "Entelim Model"),
            "id": "model-1",
            "version": "5.0.0",
        },
    )

    # Group elements into folders by layer.
    by_folder: Dict[str, List[Dict]] = {}
    for el in vp.get("elements", []):
        layer = (el.get("layer") or "other").lower()
        fname, ftype = _LAYER_FOLDER.get(layer, _LAYER_FOLDER["other"])
        by_folder.setdefault(ftype, {"name": fname, "elements": []})["elements"].append(el)

    for i, (ftype, data) in enumerate(by_folder.items()):
        folder = ET.SubElement(root, "folder", attrib={
            "name": data["name"], "id": f"folder-{ftype}", "type": ftype,
        })
        for el in data["elements"]:
            ET.SubElement(folder, "element", attrib={
                f"{{{_XSI_NS}}}type": f"archimate:{el.get('type', 'ApplicationComponent')}",
                "name": _clean(el.get("name")),
                "id": f"e{el['id']}",
            })

    # Relations folder.
    rels = vp.get("relationships", [])
    if rels:
        rel_folder = ET.SubElement(root, "folder", attrib={
            "name": "Relations", "id": "folder-relations", "type": "relations",
        })
        for rel in rels:
            ET.SubElement(rel_folder, "element", attrib={
                f"{{{_XSI_NS}}}type": f"archimate:{_canonical_rel(rel.get('type'))}Relationship",
                "id": f"r{rel['id']}",
                "source": f"e{rel['source_id']}",
                "target": f"e{rel['target_id']}",
            })

    # Views folder with one diagram carrying layout + connections.
    views_folder = ET.SubElement(root, "folder", attrib={
        "name": "Views", "id": "folder-views", "type": "diagrams",
    })
    diagram = ET.SubElement(views_folder, "element", attrib={
        f"{{{_XSI_NS}}}type": "archimate:ArchimateDiagramModel",
        "name": vp.get("viewpoint_name", "View"),
        "id": "view-1",
    })
    el_ids = {el["id"] for el in vp.get("elements", [])}
    for el in vp.get("elements", []):
        child = ET.SubElement(diagram, "child", attrib={
            f"{{{_XSI_NS}}}type": "archimate:DiagramObject",
            "id": f"do{el['id']}",
            "archimateElement": f"e{el['id']}",
        })
        ET.SubElement(child, "bounds", attrib={
            "x": str(int(el.get("x", 0))), "y": str(int(el.get("y", 0))),
            "width": str(int(el.get("w", 180)) or 180),
            "height": str(int(el.get("h", 64)) or 64),
        })

    # Connections are emitted as sourceConnection children on the source object.
    # Build a lookup of the source diagram-object element to append to.
    do_by_el = {child.get("archimateElement"): child for child in diagram.findall("child")}
    for rel in rels:
        if rel["source_id"] not in el_ids or rel["target_id"] not in el_ids:
            continue
        src_do = do_by_el.get(f"e{rel['source_id']}")
        if src_do is None:
            continue
        ET.SubElement(src_do, "sourceConnection", attrib={
            f"{{{_XSI_NS}}}type": "archimate:Connection",
            "id": f"c{rel['id']}",
            "source": f"do{rel['source_id']}",
            "target": f"do{rel['target_id']}",
            "archimateRelationship": f"r{rel['id']}",
        })

    # One model-level property per empty box, named box_key and reason — the
    # model's own notes field, not a folder or element, so an empty box is
    # never re-imported as a fabricated one.
    for r in vp.get("reasons") or []:
        ET.SubElement(root, "property", attrib={
            "key": f"reason:{r['box_key']}", "value": r["reason"],
        })

    raw = ET.tostring(root, encoding="unicode")
    reparsed = minidom.parseString(f'<?xml version="1.0" encoding="UTF-8"?>{raw}')
    return reparsed.toprettyxml(indent="  ", encoding=None)


# --------------------------------------------------------------------------- #
# Saved-viewpoint convenience wrappers                                         #
# --------------------------------------------------------------------------- #

def render_viewpoint(vp: Dict[str, Any], fmt: str):
    """Render a viewpoint dict to ``(body, mimetype, extension)`` for ``fmt``.

    Shared by the saved-viewpoint (GET) and live-canvas (POST) export routes so
    every format is produced identically from either source. Raises ValueError on
    an unsupported format.
    """
    if fmt == "archimate_exchange":
        from app.services.archimate_export_service import _export_with_layout
        return _export_with_layout(vp), "application/xml", "xml"
    if fmt == "mermaid":
        return to_mermaid(vp), "text/plain; charset=utf-8", "mmd"
    if fmt == "lucid":
        return to_lucid_zip(vp), "application/octet-stream", "lucid"
    if fmt == "archi":
        return to_archi(vp), "application/xml", "archimate"
    raise ValueError(f"Unsupported format: {fmt}")


def export_saved_viewpoint_mermaid(viewpoint_id: int) -> str:
    from app.services.archimate_export_service import load_viewpoint_dict
    return to_mermaid(load_viewpoint_dict(viewpoint_id))


def export_saved_viewpoint_lucid(viewpoint_id: int) -> bytes:
    from app.services.archimate_export_service import load_viewpoint_dict
    return to_lucid_zip(load_viewpoint_dict(viewpoint_id))


def export_saved_viewpoint_archi(viewpoint_id: int) -> str:
    from app.services.archimate_export_service import load_viewpoint_dict
    return to_archi(load_viewpoint_dict(viewpoint_id))


# --------------------------------------------------------------------------- #
# Canvas export (business-model canvas / business-case pages)                 #
# --------------------------------------------------------------------------- #
#
# A canvas record's export runs over its saved diagram through the same three
# renderers above — no fourth format, no second exporter — with one addition:
# a `reasons` entry naming every box with no entry, so the file never leaves
# an empty box unexplained.
#
# The entry-vs-empty check below covers only a template's `type_profile`
# zones (plain "does an entry with this profile exist on the diagram"), the
# one membership rule that needs no anchor relationship, attribute value, or
# risk-register read to answer honestly. A `type_profile_anchor`, `attribute`,
# `composed` or `register` zone always carries its configured `empty_reason`
# here; resolving those needs the fuller projection, not the exporter.

def resolve_canvas_saved_diagram_id(record, template_key: str):
    """The saved diagram id an export or a share link for this canvas record
    uses.

    The record's own ``saved_diagram_id`` once one exists (``getattr`` with a
    default so this reads safely before that column exists); otherwise the
    tenant's own saved diagram of this template's kind — one canvas store per
    tenant per template in this wave — otherwise ``None`` (nothing saved yet,
    so export runs over an empty diagram and share has nothing to link to).
    """
    saved_diagram_id = getattr(record, "saved_diagram_id", None)
    if saved_diagram_id:
        return saved_diagram_id

    from app.models.archimate_core import SavedDiagram

    diagram = (
        SavedDiagram.query.filter(SavedDiagram.viewpoint_type == template_key)
        .order_by(SavedDiagram.updated_at.desc())
        .first()
    )
    return diagram.id if diagram else None


def canvas_export_reasons(template_key: str, saved_diagram_id=None) -> List[Dict[str, str]]:
    """``[{box_key, reason}, ...]`` for every zone of ``template_key`` with no
    matching entry on the given saved diagram (every zone, when there is
    none).

    A tenant-scoped read (``ArchiMateElement`` and ``SavedDiagram`` are both
    tenant-scoped models; a query against either carries the organisation
    predicate automatically) — the same explicit membership check the design
    uses for a plain ``type_profile`` zone: an entry counts once an element on
    the diagram carries the zone's ``profile`` in ``acm_properties``.
    """
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    tpl = CANVAS_TEMPLATES.get(template_key)
    if not tpl:
        return []

    profiles_present = set()
    if saved_diagram_id:
        from app.models.archimate_core import ArchiMateElement, SavedDiagram

        diagram = SavedDiagram.query.filter(SavedDiagram.id == saved_diagram_id).first()
        if diagram is not None:
            element_ids = [p.element_id for p in diagram.positions.all()]
            if element_ids:
                els = ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
                for el in els:
                    profile = (el.acm_properties or {}).get("profile")
                    if profile:
                        profiles_present.add(profile)

    reasons = []
    for zone in tpl.get("zones", []):
        if zone.get("membership") == "type_profile" and zone.get("profile") in profiles_present:
            continue
        reasons.append({
            "box_key": zone["box_key"],
            "reason": zone.get("empty_reason") or "canvas_box_empty",
        })
    return reasons


def export_canvas_viewpoint(template_key: str, saved_diagram_id, fmt: str, name: str = "Canvas"):
    """Render a canvas record through the same ``render_viewpoint`` dispatch
    every saved viewpoint uses, with a ``reasons`` section added.

    ``fmt`` is one of ``mermaid``, ``lucid``, ``archi`` — the OEF/XML export
    stays the generic saved-viewpoint route's, not a canvas one. Works from
    an empty diagram-free payload when ``saved_diagram_id`` is ``None``, so a
    canvas with nothing saved yet still exports a file naming every box's
    reason rather than failing.

    Returns ``(body, mimetype, extension)`` — the same shape ``render_viewpoint``
    returns.
    """
    if fmt not in ("mermaid", "lucid", "archi"):
        raise ValueError(f"Unsupported canvas export format: {fmt}")

    if saved_diagram_id:
        from app.services.archimate_export_service import load_viewpoint_dict
        vp = load_viewpoint_dict(saved_diagram_id)
    else:
        vp = {"viewpoint_name": name, "elements": [], "relationships": []}

    vp["reasons"] = canvas_export_reasons(template_key, saved_diagram_id)
    return render_viewpoint(vp, fmt)

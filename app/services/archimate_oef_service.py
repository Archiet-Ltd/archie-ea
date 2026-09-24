"""ArchiMate Open Exchange Format (OEF) import/export service.

Implements XML-based interchange compatible with ArchiMate 3.0 OEF specification.
"""

from app.utils import safe_xml  # untrusted XML: entity-expansion safe
import json
import xml.etree.ElementTree as ET

from app import db
from app.config.archimate_relationship_matrix import RELATIONSHIP_TYPES
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship, ArchitectureModel

# Import (and _normalize_rel_type in archimate_routes.py) store/compare
# relationship types in lowercase per RELATIONSHIP_TYPES (the canonical
# authority, app/config/archimate_relationship_matrix.py). The OEF/XSD spec
# requires PascalCase (e.g. "Serving", "Realization") for xsi:type — every
# name in RELATIONSHIP_TYPES is a single lowercase word, so .capitalize()
# round-trips correctly without a second hardcoded name list. A tool that
# actually validates the XSD (Archi) rejects the lowercase form even though
# Entelim's own importer tolerates it by lowercasing again on the way back in.
_PASCAL_CASE_REL_TYPES: dict[str, str] = {t: t.capitalize() for t in RELATIONSHIP_TYPES}


class ArchiMateOEFService:
    """Service for ArchiMate Open Exchange Format XML import/export."""

    ARCHIMATE_NS = "http://www.opengroup.org/xsd/archimate/3.0/"
    XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
    DC_NS = "http://purl.org/dc/elements/1.1/"

    # Maps OEF xsi:type to (layer, element_type)
    _TYPE_MAP: dict[str, tuple[str, str]] = {
        # Business layer
        "BusinessActor": ("Business", "BusinessActor"),
        "BusinessRole": ("Business", "BusinessRole"),
        "BusinessProcess": ("Business", "BusinessProcess"),
        "BusinessFunction": ("Business", "BusinessFunction"),
        "BusinessService": ("Business", "BusinessService"),
        "BusinessObject": ("Business", "BusinessObject"),
        "BusinessInterface": ("Business", "BusinessInterface"),
        "BusinessEvent": ("Business", "BusinessEvent"),
        "BusinessInteraction": ("Business", "BusinessInteraction"),
        "BusinessCollaboration": ("Business", "BusinessCollaboration"),
        "Contract": ("Business", "Contract"),
        "Representation": ("Business", "Representation"),
        # Application layer
        "ApplicationComponent": ("Application", "ApplicationComponent"),
        "ApplicationInterface": ("Application", "ApplicationInterface"),
        "ApplicationService": ("Application", "ApplicationService"),
        "ApplicationFunction": ("Application", "ApplicationFunction"),
        "ApplicationProcess": ("Application", "ApplicationProcess"),
        "DataObject": ("Application", "DataObject"),
        # Technology layer
        "Node": ("Technology", "Node"),
        "Device": ("Technology", "Device"),
        "SystemSoftware": ("Technology", "SystemSoftware"),
        "TechnologyService": ("Technology", "TechnologyService"),
        "TechnologyInterface": ("Technology", "TechnologyInterface"),
        "Path": ("Technology", "Path"),
        "CommunicationNetwork": ("Technology", "CommunicationNetwork"),
        "Artifact": ("Technology", "Artifact"),
        # Strategy layer
        "Resource": ("Strategy", "Resource"),
        "Capability": ("Strategy", "Capability"),
        "CourseOfAction": ("Strategy", "CourseOfAction"),
        "ValueStream": ("Strategy", "ValueStream"),
        # Motivation layer
        "Driver": ("Motivation", "Driver"),
        "Assessment": ("Motivation", "Assessment"),
        "Goal": ("Motivation", "Goal"),
        "Outcome": ("Motivation", "Outcome"),
        "Principle": ("Motivation", "Principle"),
        "Requirement": ("Motivation", "Requirement"),
        "Constraint": ("Motivation", "Constraint"),
        "Stakeholder": ("Motivation", "Stakeholder"),
        "Value": ("Motivation", "Value"),
        "Meaning": ("Motivation", "Meaning"),
        # Implementation & Migration layer
        "WorkPackage": ("Implementation", "WorkPackage"),
        "Deliverable": ("Implementation", "Deliverable"),
        "ImplementationEvent": ("Implementation", "ImplementationEvent"),
        "Plateau": ("Implementation", "Plateau"),
        "Gap": ("Implementation", "Gap"),
    }

    def export_model(self, model_id: int | None = None) -> str:
        """Export elements (and optionally a specific model) to OEF XML string.

        Thin wrapper over :meth:`export_model_validated` for callers that only
        want the XML. Use ``export_model_validated`` to also see which
        relationships were direction-corrected or dropped.
        """
        xml_str, _errors = self.export_model_validated(model_id)
        return xml_str

    def export_model_validated(self, model_id: int | None = None) -> tuple[str, list[str]]:
        """Export elements/relationships to OEF XML, validating every relationship
        against the corrected ArchiMate 3.2 matrix (``ArchimateValidityService``) on
        the way out.

        A relationship whose stored direction is invalid but whose reverse is
        valid is emitted reversed (source/target swapped) and reported. A
        relationship that is invalid in both directions is dropped from the
        export and reported — OEF-invalid data is never silently emitted.

        Returns ``(xml_string, validation_errors)`` where each entry in
        ``validation_errors`` describes one corrected or dropped relationship.
        """
        from app.services.archimate_validity_service import ArchimateValidityService

        validity = ArchimateValidityService()
        ET.register_namespace("", self.ARCHIMATE_NS)
        ET.register_namespace("xsi", self.XSI_NS)
        ET.register_namespace("dc", self.DC_NS)

        # Determine model metadata
        model = None
        if model_id is not None:
            model = db.session.get(ArchitectureModel, model_id)

        model_name = model.name if model else "ArchiMate Model"
        model_identifier = f"id-model-{model_id}" if model_id else "id-model-export"

        root = ET.Element(
            f"{{{self.ARCHIMATE_NS}}}model",
            attrib={
                f"{{{self.XSI_NS}}}schemaLocation": (
                    f"{self.ARCHIMATE_NS} "
                    "http://www.opengroup.org/xsd/archimate/3.1/archimate3_Diagram.xsd"
                ),
                "identifier": model_identifier,
                "version": "3.0",
            },
        )

        name_el = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}name")
        name_el.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
        name_el.text = model_name

        # Query elements
        query = ArchiMateElement.query
        if model_id is not None:
            query = query.filter_by(architecture_id=model_id)
        elements = query.all()

        # Query relationships
        rel_query = ArchiMateRelationship.query
        if model_id is not None:
            rel_query = rel_query.filter_by(architecture_id=model_id)
        relationships = rel_query.all()

        # <propertyDefinitions> — O-02: custom element attributes were
        # previously dropped entirely on export. Collect every distinct key
        # across the exported elements first so each can get a stable
        # propertyDefinition identifier that <properties> entries below
        # reference.
        #
        # DOGFOOD-004 correction: this used to read ONLY
        # ``ArchiMateElement.properties`` (a legacy JSON-text column — see
        # app/models/models.py:274 — used by older financial/tagging
        # features such as annual_cost/owner). The OEF importer
        # (app/services/archimate_import_service.py) writes to a different,
        # newer column, ``custom_properties`` (db.JSON — app/models/
        # archimate_core.py:80 / models.py:377), which this export never
        # read at all, so nothing imported via OEF ever survived a
        # round-trip. Both columns are real and both are exported here,
        # merged with ``custom_properties`` taking precedence on a key
        # collision (it is the column DOGFOOD-004's import convention
        # writes to). This is two systems of record for "element
        # properties" and is flagged, not fixed, by this task — see the
        # ADR 0009 addendum. The ``archie:`` namespace is reserved
        # provenance (``archie:imported_at``, written by the importer) and
        # is excluded here per the round-trip rule in
        # docs/adr/0009-continuous-model-maintenance.md (Addendum) — only
        # the customer's own keys are written back, so re-importing an
        # exported file reproduces byte-identical custom_properties for
        # those keys.
        prop_key_to_def_id: dict[str, str] = {}
        elem_props: dict[int, dict] = {}

        def _parsed_json_dict(raw):
            if not raw:
                return {}
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}

        for elem in elements:
            merged: dict = {}
            merged.update(_parsed_json_dict(getattr(elem, "properties", None)))
            merged.update(_parsed_json_dict(getattr(elem, "custom_properties", None)))
            merged = {k: v for k, v in merged.items() if not str(k).startswith("archie:")}
            if not merged:
                continue
            elem_props[elem.id] = merged
            for key in merged:
                if key not in prop_key_to_def_id:
                    prop_key_to_def_id[key] = f"id-propdef-{len(prop_key_to_def_id) + 1}"

        if prop_key_to_def_id:
            propdefs_el = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}propertyDefinitions")
            for key, def_id in prop_key_to_def_id.items():
                pd = ET.SubElement(
                    propdefs_el,
                    f"{{{self.ARCHIMATE_NS}}}propertyDefinition",
                    {"identifier": def_id, "type": "string"},
                )
                pd_name = ET.SubElement(pd, f"{{{self.ARCHIMATE_NS}}}name")
                pd_name.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
                pd_name.text = key

        # <elements>
        elements_el = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}elements")
        for elem in elements:
            el_type = elem.type or "ApplicationComponent"
            el_attrib = {
                "identifier": f"id-{elem.id}",
                f"{{{self.XSI_NS}}}type": el_type,
            }
            el_node = ET.SubElement(elements_el, f"{{{self.ARCHIMATE_NS}}}element", el_attrib)
            el_name = ET.SubElement(el_node, f"{{{self.ARCHIMATE_NS}}}name")
            el_name.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
            el_name.text = elem.name or ""
            if elem.description:
                doc_el = ET.SubElement(el_node, f"{{{self.ARCHIMATE_NS}}}documentation")
                doc_el.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
                doc_el.text = elem.description
            props = elem_props.get(elem.id)
            if props:
                props_el = ET.SubElement(el_node, f"{{{self.ARCHIMATE_NS}}}properties")
                for key, value in props.items():
                    def_id = prop_key_to_def_id.get(key)
                    if not def_id:
                        continue
                    prop_el = ET.SubElement(
                        props_el, f"{{{self.ARCHIMATE_NS}}}property", {"propertyDefinitionRef": def_id}
                    )
                    val_el = ET.SubElement(prop_el, f"{{{self.ARCHIMATE_NS}}}value")
                    val_el.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
                    val_el.text = str(value)

        # <relationships> — validated against the ArchiMate 3.2 matrix on the way out.
        elem_type_by_id: dict[int, str] = {elem.id: (elem.type or "") for elem in elements}
        validation_errors: list[str] = []

        relationships_el = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}relationships")
        for rel in relationships:
            if rel.source_id is None or rel.target_id is None:
                continue
            rel_type = rel.type or "Association"
            rel_type_key = rel_type.lower()
            source_id, target_id = rel.source_id, rel.target_id
            source_type = elem_type_by_id.get(source_id)
            target_type = elem_type_by_id.get(target_id)

            if source_type is None or target_type is None:
                # Endpoint outside this export's element set (e.g. cross-model
                # export) — cannot validate direction, emit as-is.
                pass
            elif validity.is_valid(source_type, target_type, rel_type_key):
                pass
            elif validity.is_valid(target_type, source_type, rel_type_key):
                validation_errors.append(
                    f"relationship id-rel-{rel.id} ({rel_type}) {source_type}(id-{source_id}) "
                    f"-> {target_type}(id-{target_id}) is invalid per the ArchiMate 3.2 matrix; "
                    f"the reverse direction is valid — emitted reversed."
                )
                source_id, target_id = target_id, source_id
            else:
                validation_errors.append(
                    f"relationship id-rel-{rel.id} ({rel_type}) {source_type}(id-{source_id}) "
                    f"-> {target_type}(id-{target_id}) is not a permitted pairing in either "
                    f"direction per the ArchiMate 3.2 matrix — dropped from export."
                )
                continue

            # Export xsi:type in OEF/XSD-required PascalCase, not the
            # lowercase form the importer stores internally (B4).
            rel_type_export = _PASCAL_CASE_REL_TYPES.get(rel_type_key, rel_type_key.capitalize() if rel_type_key else "Association")
            rel_attrib = {
                "identifier": f"id-rel-{rel.id}",
                f"{{{self.XSI_NS}}}type": rel_type_export,
                "source": f"id-{source_id}",
                "target": f"id-{target_id}",
            }
            ET.SubElement(relationships_el, f"{{{self.ARCHIMATE_NS}}}relationship", rel_attrib)

        # <organizations> — O-02: folder structure was previously dropped entirely on
        # export, so a re-imported model lost all layer/folder grouping. Group exported
        # elements by ArchiMate layer, which is the only folder structure Entelim itself
        # tracks server-side (there is no separate user-defined folder tree to preserve).
        layer_groups: dict[str, list] = {}
        for elem in elements:
            layer_groups.setdefault(elem.layer or "Other", []).append(elem)

        if layer_groups:
            organizations_el = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}organizations")
            for layer_name, layer_elements in layer_groups.items():
                item_el = ET.SubElement(organizations_el, f"{{{self.ARCHIMATE_NS}}}item")
                label_el = ET.SubElement(item_el, f"{{{self.ARCHIMATE_NS}}}label")
                label_el.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
                label_el.text = layer_name
                for elem in layer_elements:
                    ref_el = ET.SubElement(item_el, f"{{{self.ARCHIMATE_NS}}}item")
                    ref_el.set("identifierRef", f"id-{elem.id}")

        # <views> — O-02: the Composer's saved diagrams (SavedDiagram /
        # SavedDiagramElement / SavedDiagramRelationship — see
        # app/models/archimate_core.py) carry the real node positions and
        # connection routing; the export used to omit <views> entirely, so
        # every diagram authored in the Composer was lost on export even
        # though the geometry exists server-side. Only diagrams whose
        # elements are actually in this export's element set are included.
        from app.models.archimate_core import SavedDiagram

        exported_element_ids = {elem.id for elem in elements}
        exported_rel_ids = {rel.id for rel in relationships}

        diagrams = SavedDiagram.query.all()
        views_to_export = []
        for diagram in diagrams:
            diagram_element_ids = {
                pos.element_id for pos in diagram.positions if pos.element_id in exported_element_ids
            }
            if diagram_element_ids:
                views_to_export.append((diagram, diagram_element_ids))

        if views_to_export:
            views_el = ET.SubElement(root, f"{{{self.ARCHIMATE_NS}}}views")
            diagrams_el = ET.SubElement(views_el, f"{{{self.ARCHIMATE_NS}}}diagrams")
            for diagram, diagram_element_ids in views_to_export:
                view_el = ET.SubElement(
                    diagrams_el,
                    f"{{{self.ARCHIMATE_NS}}}view",
                    {
                        "identifier": f"id-view-{diagram.id}",
                        f"{{{self.XSI_NS}}}type": "archimate:ArchimateDiagramModel",
                    },
                )
                view_name_el = ET.SubElement(view_el, f"{{{self.ARCHIMATE_NS}}}name")
                view_name_el.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
                view_name_el.text = diagram.name or f"View {diagram.id}"

                for pos in diagram.positions:
                    if pos.element_id not in diagram_element_ids:
                        continue
                    ET.SubElement(
                        view_el,
                        f"{{{self.ARCHIMATE_NS}}}node",
                        {
                            "identifier": f"id-view-{diagram.id}-node-{pos.element_id}",
                            "elementRef": f"id-{pos.element_id}",
                            "x": str(pos.position_x or 0),
                            "y": str(pos.position_y or 0),
                            "w": str(pos.width or 180),
                            "h": str(pos.height or 64),
                        },
                    )

                for rel_pos in diagram.rel_positions:
                    if rel_pos.relationship_id not in exported_rel_ids:
                        continue
                    ET.SubElement(
                        view_el,
                        f"{{{self.ARCHIMATE_NS}}}connection",
                        {
                            "identifier": f"id-view-{diagram.id}-conn-{rel_pos.relationship_id}",
                            "relationshipRef": f"id-rel-{rel_pos.relationship_id}",
                        },
                    )

        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=False)
        return xml_str, validation_errors

    def import_model(self, xml_string: str) -> dict:
        """Parse OEF XML and upsert elements/relationships.

        Returns a summary dict with counts of created/updated items and errors.
        """
        result = {
            "elements_created": 0,
            "elements_updated": 0,
            "relationships_created": 0,
            "relationships_updated": 0,
            "errors": [],
        }

        try:
            root = safe_xml.fromstring(xml_string)
        except ET.ParseError as exc:
            result["errors"].append(f"XML parse error: {exc}")
            return result


        # Resolve default namespace from root tag if present
        def _ns_tag(tag: str) -> str:
            """Return tag with archimate namespace prefix."""
            return f"{{{self.ARCHIMATE_NS}}}{tag}"

        # Map OEF identifiers → DB ids for relationship wiring
        identifier_to_db_id: dict[str, int] = {}

        # --- Elements ---
        elements_container = root.find(_ns_tag("elements"))
        if elements_container is not None:
            for el_node in elements_container.findall(_ns_tag("element")):
                try:
                    identifier = el_node.get("identifier", "")
                    xsi_type = el_node.get(f"{{{self.XSI_NS}}}type") or el_node.get("type") or ""
                    layer, elem_type = self._element_type_to_archimate(xsi_type)

                    name_node = el_node.find(_ns_tag("name"))
                    name = (name_node.text or "").strip() if name_node is not None else xsi_type

                    doc_node = el_node.find(_ns_tag("documentation"))
                    description = (doc_node.text or "").strip() if doc_node is not None else None

                    # Try to match existing element by identifier suffix (e.g. "id-42" → id=42)
                    existing = None
                    if identifier.startswith("id-") and not identifier.startswith("id-rel-"):
                        id_part = identifier[3:]
                        if id_part.isdigit():
                            existing = db.session.get(ArchiMateElement, int(id_part))

                    if existing:
                        existing.name = name
                        existing.type = elem_type
                        existing.layer = layer
                        if description:
                            existing.description = description
                        identifier_to_db_id[identifier] = existing.id
                        result["elements_updated"] += 1
                    else:
                        new_el = ArchiMateElement(
                            name=name,
                            type=elem_type,
                            layer=layer,
                            description=description,
                        )
                        db.session.add(new_el)
                        db.session.flush()  # get id
                        identifier_to_db_id[identifier] = new_el.id
                        result["elements_created"] += 1

                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"Element '{el_node.get('identifier', '?')}': {exc}")

        # --- Relationships ---
        rels_container = root.find(_ns_tag("relationships"))
        if rels_container is not None:
            for rel_node in rels_container.findall(_ns_tag("relationship")):
                try:
                    rel_identifier = rel_node.get("identifier", "")
                    xsi_type = (
                        rel_node.get(f"{{{self.XSI_NS}}}type") or rel_node.get("type") or "Association"
                    )
                    source_ref = rel_node.get("source", "")
                    target_ref = rel_node.get("target", "")

                    source_db_id = identifier_to_db_id.get(source_ref)
                    target_db_id = identifier_to_db_id.get(target_ref)

                    # Fallback: parse numeric id from source/target ref
                    if source_db_id is None and source_ref.startswith("id-"):
                        part = source_ref[3:]
                        if part.isdigit():
                            source_db_id = int(part)
                    if target_db_id is None and target_ref.startswith("id-"):
                        part = target_ref[3:]
                        if part.isdigit():
                            target_db_id = int(part)

                    if source_db_id is None or target_db_id is None:
                        result["errors"].append(
                            f"Relationship '{rel_identifier}': could not resolve "
                            f"source='{source_ref}' or target='{target_ref}'"  # raw-html-ok: internal import error message appended to a JSON errors list, never rendered as HTML/browser output
                        )
                        continue

                    # Try to match existing
                    existing_rel = None
                    if rel_identifier.startswith("id-rel-"):
                        id_part = rel_identifier[7:]
                        if id_part.isdigit():
                            existing_rel = db.session.get(ArchiMateRelationship, int(id_part))

                    if existing_rel:
                        existing_rel.type = xsi_type
                        existing_rel.source_id = source_db_id
                        existing_rel.target_id = target_db_id
                        result["relationships_updated"] += 1
                    else:
                        new_rel = ArchiMateRelationship(
                            type=xsi_type,
                            source_id=source_db_id,
                            target_id=target_db_id,
                        )
                        db.session.add(new_rel)
                        result["relationships_created"] += 1

                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(
                        f"Relationship '{rel_node.get('identifier', '?')}': {exc}"
                    )

        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            result["errors"].append(f"Database commit error: {exc}")

        return result

    def _element_type_to_archimate(self, xsi_type: str) -> tuple[str, str]:
        """Convert OEF xsi:type like 'BusinessProcess' to (layer, element_type)."""
        # Strip namespace prefix if present (e.g. "archimate3:BusinessProcess")
        if ":" in xsi_type:
            xsi_type = xsi_type.split(":")[-1]
        return self._TYPE_MAP.get(xsi_type, ("Application", xsi_type or "ApplicationComponent"))

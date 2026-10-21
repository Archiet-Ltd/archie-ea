"""ArchiMate Open Exchange Format (OEF) XML import engine (ENT-067).

This is the one engine that imports an OEF model into the platform's
ArchiMate element store (ADR 0008: one accessor per concept). Every entry
point calls it: the model-import screen (``architect_ui.import_oef`` and its
preview endpoint), the composer's JSON endpoint (``/archimate/api/import/oef``)
and brownfield programme setup.

What it does, in order:

- ``load`` refuses anything over ``MAX_UPLOAD_BYTES``, decodes bytes as UTF-8
  with a Latin-1 fallback, and parses the XML (entity-expansion safe).
- ``preview_import`` classifies each element against the store (new / exists
  / conflict / invalid) and each relationship against the ArchiMate
  relationship matrix, writing nothing.
- ``execute_import`` writes under one of three strategies --
  ``skip_duplicates``, ``update_existing`` or ``create_all`` -- refusing
  (never storing) a relationship the matrix does not allow.

Duplicate detection is by (name, element_type) case-insensitive match
against the ``archimate_elements`` table.
"""

from app.utils import safe_xml  # untrusted XML: entity-expansion safe
import re
import logging
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Union

from app import db
from app.models.archimate_element_types import (
    SPECIALIZATION_PROPERTY,
    application_kind_types,
    is_application_kind,
)

logger = logging.getLogger(__name__)

# OEF namespaces — must match export service
_OEF_NS = "http://www.opengroup.org/xsd/archimate/3.0/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB, for every entry point
STRATEGIES = ("skip_duplicates", "update_existing", "create_all")


class ImportRequestError(ValueError):
    """An import the engine refuses before writing anything.

    ``status_code`` is the HTTP status a route should answer with: 413 for
    an oversized file, 400 for everything else (empty, malformed, no
    elements, unknown strategy).
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def read_xml_from_request(req, file_fields=("oef_file", "file")) -> Optional[Union[bytes, str]]:
    """Return the OEF document carried by a Flask request, or ``None``.

    Accepted, in order: a multipart upload in any of ``file_fields``; a JSON
    body with an ``xml_content`` string; a raw ``application/xml`` /
    ``text/xml`` body. Bytes are returned undecoded so ``load`` applies the
    one size limit and the one decoding rule.
    """
    for field in file_fields:
        uploaded = req.files.get(field) if req.files else None
        if uploaded is not None and uploaded.filename:
            return uploaded.read()

    if req.is_json:
        payload = req.get_json(silent=True) or {}
        content = payload.get("xml_content") if isinstance(payload, dict) else None
        if isinstance(content, str) and content:
            return content

    if "xml" in (req.content_type or ""):
        raw = req.get_data(as_text=False)
        if raw:
            return raw

    return None


_MODELLED_NOTE = re.compile(r"As modelled: ([A-Za-z]+)\.")


def _modelled_note_type(description):
    """The relationship type recorded in an "As modelled: <Type>." note, lower
    case, or None when the description carries no such note."""
    match = _MODELLED_NOTE.search(description or "")
    return match.group(1).lower() if match else None


# A property key that names a specialisation in a file written by another tool.
_PLAIN_SPECIALIZATION_KEY = "specialization"
_PARENT_RELATIONSHIPS = ("composition", "aggregation")


def _specialization_marks(props) -> Dict[str, bool]:
    """How an element's properties speak about its specialisation (key match ignores case).

    ``application``: the element is stated to be an Application (rule 1).
    ``entelim``: the element carries Entelim's own property, with any value. A file with
    such an element was written by Entelim, so promotion does not apply to it.
    """
    application = entelim = False
    for key, value in (props or {}).items():
        norm = str(key).strip().lower()
        if norm == SPECIALIZATION_PROPERTY:
            entelim = True
        if norm in (SPECIALIZATION_PROPERTY, _PLAIN_SPECIALIZATION_KEY) and str(value).strip() == "Application":
            application = True
    return {"application": application, "entelim": entelim}


def decision_key(index: int, element: Dict[str, Any]) -> str:
    """The key under which ``decide_application_kinds`` reports an element."""
    return element.get("identifier") or f"#{index}"


def decide_application_kinds(
    parsed: Dict[str, Any], *, promote_top_level: bool = True
) -> Dict[str, Dict[str, Any]]:
    """Decide, for each application-kind element of a parsed file, Application or component.

    Pure: reads ``parsed["elements"]`` and ``parsed["relationships"]`` and writes nothing.
    Returns ``{key: {"name", "declared_type", "type", "reason", "stated"}}`` where ``type`` is
    ``Application`` or ``ApplicationComponent``, ``reason`` is plain English for the preview and
    ``stated`` is true when the file itself marks the element as an Application (ADR-0014
    Decision 4, rules 1 to 4).
    """
    elements = parsed.get("elements") or []
    relationships = parsed.get("relationships") or []
    usable = {
        e["identifier"]: e
        for e in elements
        if e.get("identifier") and not e.get("invalid") and is_application_kind(e.get("type"))
    }

    # Rule 3 counts only an application-kind element as a parent, by Composition or Aggregation.
    composed_by: Dict[str, str] = {}
    aggregated_by: Dict[str, str] = {}
    for rel in relationships:
        kind = (rel.get("type") or "").strip().lower()
        source, target = rel.get("source"), rel.get("target")
        if kind in _PARENT_RELATIONSHIPS and source in usable and target in usable and source != target:
            (composed_by if kind == "composition" else aggregated_by).setdefault(target, source)

    marks = {
        decision_key(i, e): _specialization_marks(e.get("properties")) for i, e in enumerate(elements)
    }
    entelim_file = any(m["application"] or m["entelim"] for m in marks.values())

    decisions: Dict[str, Dict[str, Any]] = {}
    for index, elem in enumerate(elements):
        if elem.get("invalid") or not is_application_kind(elem.get("type")):
            continue
        key = decision_key(index, elem)
        ident = elem.get("identifier")
        name = elem.get("name") or ""

        def _name_of(parent_id: str) -> str:
            return usable[parent_id].get("name") or parent_id

        if marks[key]["application"]:
            # Rule 1, then rule 4: validity wins over what the file states.
            if ident in composed_by:
                type_, stated = "ApplicationComponent", False
                reason = (
                    f"The file marks it as an Application, but it is composed by "
                    f"{_name_of(composed_by[ident])} and an Application cannot be composed by an "
                    f"application, so it is imported as an application component."
                )
            else:
                type_, stated = "Application", True
                reason = "The file marks it as an Application."
        elif entelim_file:
            type_, stated = "ApplicationComponent", False
            reason = "Imported as an application component: the file marks its Applications and does not mark this one."
        elif not promote_top_level:
            type_, stated = "ApplicationComponent", False
            reason = "Imported as an application component: treating top-level components as applications is switched off."
        elif ident in composed_by:
            type_, stated = "ApplicationComponent", False
            reason = f"Imported as an application component: it is composed by {_name_of(composed_by[ident])}."
        elif ident in aggregated_by:
            type_, stated = "ApplicationComponent", False
            reason = f"Imported as an application component: it is aggregated by {_name_of(aggregated_by[ident])}."
        else:
            type_, stated = "Application", False
            reason = "Imported as an Application: no application component composes or aggregates it, so it is a top-level system."

        decisions[key] = {
            "identifier": ident or "",
            "name": name,
            "declared_type": elem.get("type"),
            "type": type_,
            "reason": reason,
            "stated": stated,
        }
    return decisions


class ArchiMateImportService:
    """Service for importing ArchiMate OEF XML into the platform."""

    # Map ArchiMate xsi:type values to platform layer names.
    # Covers all 57 ArchiMate 3.2 element types across 7 layers.
    TYPE_TO_LAYER: Dict[str, str] = {
        # Strategy layer
        "Resource": "Strategy",
        "Capability": "Strategy",
        "CourseOfAction": "Strategy",
        "ValueStream": "Strategy",
        # Business layer
        "BusinessActor": "Business",
        "BusinessRole": "Business",
        "BusinessCollaboration": "Business",
        "BusinessInterface": "Business",
        "BusinessProcess": "Business",
        "BusinessFunction": "Business",
        "BusinessInteraction": "Business",
        "BusinessEvent": "Business",
        "BusinessService": "Business",
        "BusinessObject": "Business",
        "Contract": "Business",
        "Representation": "Business",
        "Product": "Business",
        # Application layer
        "ApplicationComponent": "Application",
        "ApplicationCollaboration": "Application",
        "ApplicationInterface": "Application",
        "ApplicationFunction": "Application",
        "ApplicationProcess": "Application",
        "ApplicationInteraction": "Application",
        "ApplicationEvent": "Application",
        "ApplicationService": "Application",
        "DataObject": "Application",
        # Technology layer
        "Node": "Technology",
        "Device": "Technology",
        "SystemSoftware": "Technology",
        "TechnologyCollaboration": "Technology",
        "TechnologyInterface": "Technology",
        "Path": "Technology",
        "CommunicationNetwork": "Technology",
        "TechnologyFunction": "Technology",
        "TechnologyProcess": "Technology",
        "TechnologyInteraction": "Technology",
        "TechnologyEvent": "Technology",
        "TechnologyService": "Technology",
        "Artifact": "Technology",
        # Physical layer
        "Equipment": "Physical",
        "Facility": "Physical",
        "DistributionNetwork": "Physical",
        "Material": "Physical",
        # Motivation layer
        "Stakeholder": "Motivation",
        "Driver": "Motivation",
        "Assessment": "Motivation",
        "Goal": "Motivation",
        "Outcome": "Motivation",
        "Principle": "Motivation",
        "Requirement": "Motivation",
        "Constraint": "Motivation",
        "Meaning": "Motivation",
        "Value": "Motivation",
        # Implementation & Migration layer. DOGFOOD-002: the stored key must
        # be the one the Element Catalog counts and filters on
        # (``layer_order`` in app/modules/architecture/routes/archimate_routes.py
        # uses "Implementation"). Writing "Implementation & Migration"
        # here made 35 of customer zero's 168 elements land and vanish.
        "WorkPackage": "Implementation",
        "Deliverable": "Implementation",
        "ImplementationEvent": "Implementation",
        "Plateau": "Implementation",
        "Gap": "Implementation",
        # Concepts that belong to no layer. They are known types; the layer
        # "Other" is their stored layer, so no unknown-type warning is logged.
        "Grouping": "Other",
        "Location": "Other",
        "Junction": "Other",
    }

    # Column widths on ``archimate_elements`` (app/models/archimate_core.py).
    # DOGFOOD-001: the parser checks these so preview reports what execute
    # will refuse, instead of one over-long name aborting the whole import
    # with a raw ``StringDataRightTruncation`` from the database.
    # The element name column holds 500 characters; 255 is the import limit so a
    # long Open Exchange name no longer blocks its element.
    MAX_NAME_LENGTH = 255
    MAX_TYPE_LENGTH = 50

    # DOGFOOD-003 (relationship-validity authority audit): this class used to
    # carry its own `VALID_RELATIONSHIP_TYPES` flat set — a second opinion on
    # relationship validity with no element-type awareness, and unread by
    # anything (grepped: no external reference). Deleted. The authoritative
    # check is `RelationshipValidator.validate_relationship()`
    # (`app/modules/architecture/services/relationship_validator.py`), backed
    # by the element-type-keyed matrix in
    # `app/config/archimate_relationship_matrix.py` — see `_classify_relationships`
    # below, which is the only place this service classifies relationships.

    # ------------------------------------------------------------------
    # Entry points -- every route and service calls these, never the
    # parser on its own, so the size limit and decoding rule apply
    # everywhere.
    # ------------------------------------------------------------------

    @staticmethod
    def decode_xml(content: Union[bytes, str]) -> str:
        """Apply the size limit and decode to text.

        Raises ``ImportRequestError`` (413) above ``MAX_UPLOAD_BYTES``. Bytes
        that are not UTF-8 are read as Latin-1, which is what OEF files
        exported by older Windows tools usually are.
        """
        if content is None:
            raise ImportRequestError(
                "No XML content provided. Upload a .xml file or send XML in the request body."
            )
        size = len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
        if size > MAX_UPLOAD_BYTES:
            raise ImportRequestError(
                f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                status_code=413,
            )
        if isinstance(content, str):
            return content
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1")

    def load(self, content: Union[bytes, str], *, promote_top_level: bool = True) -> Dict[str, Any]:
        """Size-check, decode and parse an OEF document."""
        xml_content = self.decode_xml(content)
        try:
            return self.parse_oef_xml(xml_content, promote_top_level=promote_top_level)
        except ValueError as exc:
            raise ImportRequestError(str(exc)) from exc

    def preview_xml(
        self,
        content: Union[bytes, str],
        organization_id: Optional[int] = None,
        *,
        promote_top_level: bool = True,
    ) -> Dict[str, Any]:
        """What ``import_xml`` would do with this document, writing nothing."""
        parsed = self.load(content, promote_top_level=promote_top_level)
        preview = self.preview_import(parsed, organization_id=organization_id)
        return {"model_name": parsed.get("model_name", ""), **preview}

    def import_xml(
        self,
        content: Union[bytes, str],
        strategy: str = "skip_duplicates",
        organization_id: Optional[int] = None,
        *,
        source_ref: Optional[str] = None,
        source_label: Optional[str] = None,
        promote_top_level: bool = True,
    ) -> Dict[str, Any]:
        """Import an OEF document under ``strategy``.

        Raises ``ImportRequestError`` for an unknown strategy, an oversized,
        empty or malformed document, or one with no importable elements --
        all before anything is written.

        Provenance parameters:
        - source_ref: import batch or run id (e.g., ArchitectureModel id)
        - source_label: uploaded file name
        - promote_top_level: ADR-0014 rule 3, on by default; False imports every
          incoming application component as a component
        """
        if strategy not in STRATEGIES:
            raise ImportRequestError(
                f"Invalid strategy: {strategy}. Must be one of: {', '.join(STRATEGIES)}"
            )
        parsed = self.load(content, promote_top_level=promote_top_level)
        if not parsed["elements"]:
            raise ImportRequestError("No valid elements found in the XML file.")
        result = self.execute_import(
            parsed,
            strategy=strategy,
            organization_id=organization_id,
            source_ref=source_ref or uuid.uuid4().hex,
            source_label=source_label,
        )
        return {"model_name": parsed.get("model_name", ""), **result}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse_oef_xml(self, xml_content: str, *, promote_top_level: bool = True) -> Dict[str, Any]:
        """Parse an OEF XML string into structured element/relationship lists.

        Returns::

            {
                "model_name": str,
                "elements": [
                    {
                        "identifier": "id-elem-1",
                        "name": "Order Processing",
                        "type": "BusinessProcess",
                        "layer": "Business",
                        "description": "..." | None,
                        "properties": {"status": "RULED", ...},
                        "invalid": "name_too_long (269 chars > 255)",  # only when present
                    }, ...
                ],
                "relationships": [
                    {
                        "identifier": "id-rel-1",
                        "type": "Serving",
                        "source": "id-elem-2",
                        "target": "id-elem-1",
                        "description": "..." | None,
                    }, ...
                ],
                "errors": [],
            }

        An element with ``invalid`` set is kept in the list so preview can
        show it, but ``execute_import`` refuses it (counted in ``failed``)
        rather than letting the database reject the whole batch.

        Raises ``ValueError`` on malformed XML.
        """
        if not xml_content or not xml_content.strip():
            raise ValueError("Empty XML content")

        try:
            root = safe_xml.fromstring(xml_content)
        except ET.ParseError as exc:
            raise ValueError(f"Malformed XML: {exc}") from exc

        # Detect namespace — accept both namespaced and bare tags
        ns = ""
        tag = root.tag
        if tag.startswith("{"):
            ns = tag[1 : tag.index("}")]

        def _tag(local: str) -> str:
            return f"{{{ns}}}{local}" if ns else local

        model_name = ""
        name_el = root.find(_tag("name"))
        if name_el is not None and name_el.text:
            model_name = name_el.text.strip()

        elements: List[Dict[str, Any]] = []
        relationships: List[Dict[str, Any]] = []
        errors: List[str] = []

        # --- Property definitions (id -> name) ---
        # DOGFOOD-004: OEF stores properties indirectly — each <property> on
        # an element references a <propertyDefinition> by id, and the
        # human-readable key lives on the definition, not the property.
        property_defs: Dict[str, str] = {}
        propdefs_container = root.find(_tag("propertyDefinitions"))
        if propdefs_container is not None:
            for pd in propdefs_container.findall(_tag("propertyDefinition")):
                pd_id = pd.get("identifier", "")
                pd_name_el = pd.find(_tag("name"))
                pd_name = (
                    pd_name_el.text.strip()
                    if pd_name_el is not None and pd_name_el.text
                    else pd_id
                )
                if pd_id:
                    property_defs[pd_id] = pd_name

        # --- Elements ---
        elements_container = root.find(_tag("elements"))
        if elements_container is not None:
            for elem in elements_container.findall(_tag("element")):
                identifier = elem.get("identifier", "")
                # xsi:type may appear as {ns}type or plain attribute
                elem_type = (
                    elem.get(f"{{{_XSI_NS}}}type")
                    or elem.get("xsi:type")
                    or elem.get("type")
                    or ""
                )
                elem_name_el = elem.find(_tag("name"))
                elem_name = (
                    elem_name_el.text.strip()
                    if elem_name_el is not None and elem_name_el.text
                    else ""
                )
                doc_el = elem.find(_tag("documentation"))
                description = (
                    doc_el.text.strip()
                    if doc_el is not None and doc_el.text
                    else None
                )

                layer = self.TYPE_TO_LAYER.get(elem_type, "")
                if not layer:
                    errors.append(
                        f"Unknown element type '{elem_type}' for '{elem_name}' "
                        f"(identifier={identifier}). Element will be imported "
                        f"with layer='Other'."
                    )
                    layer = "Other"

                if not elem_name:
                    errors.append(
                        f"Element {identifier} has no name — skipping."
                    )
                    continue

                # DOGFOOD-001: flag what the database would reject, per
                # element, so preview reports it and execute skips it.
                invalid = None
                if len(elem_name) > self.MAX_NAME_LENGTH:
                    invalid = (
                        f"name_too_long ({len(elem_name)} chars > {self.MAX_NAME_LENGTH})"
                    )
                    errors.append(
                        f"Element {identifier} '{elem_name[:40]}…' ({elem_type}) has a "
                        f"name of {len(elem_name)} characters; the limit is "
                        f"{self.MAX_NAME_LENGTH}. It will not be imported — shorten the "
                        f"name in the source model (the full text can go in its documentation)."
                    )
                elif len(elem_type) > self.MAX_TYPE_LENGTH:
                    invalid = (
                        f"type_too_long ({len(elem_type)} chars > {self.MAX_TYPE_LENGTH})"
                    )
                    errors.append(
                        f"Element {identifier} '{elem_name}' has a type of "
                        f"{len(elem_type)} characters; the limit is {self.MAX_TYPE_LENGTH}. "
                        f"It will not be imported."
                    )

                # --- Properties (DOGFOOD-004 minimal convention — see
                # docs/adr/0009-continuous-model-maintenance.md (Addendum)) ---
                properties: Dict[str, str] = {}
                props_container = elem.find(_tag("properties"))
                if props_container is not None:
                    for prop in props_container.findall(_tag("property")):
                        pd_ref = prop.get("propertyDefinitionRef", "")
                        value_el = prop.find(_tag("value"))
                        value = (
                            value_el.text.strip()
                            if value_el is not None and value_el.text
                            else ""
                        )
                        key = property_defs.get(pd_ref, pd_ref)
                        if key:
                            properties[key] = value

                entry: Dict[str, Any] = {
                    "identifier": identifier,
                    "name": elem_name,
                    "type": elem_type,
                    "layer": layer,
                    "description": description,
                    "properties": properties,
                }
                if invalid:
                    entry["invalid"] = invalid
                elements.append(entry)

        # --- Relationships ---
        rels_container = root.find(_tag("relationships"))
        if rels_container is not None:
            for rel in rels_container.findall(_tag("relationship")):
                identifier = rel.get("identifier", "")
                rel_type = (
                    rel.get(f"{{{_XSI_NS}}}type")
                    or rel.get("xsi:type")
                    or rel.get("type")
                    or ""
                )
                source = rel.get("source", "")
                target = rel.get("target", "")

                if not source or not target:
                    errors.append(
                        f"Relationship {identifier} missing source/target — skipping."
                    )
                    continue

                rel_doc_el = rel.find(_tag("documentation"))
                rel_description = (
                    rel_doc_el.text.strip()
                    if rel_doc_el is not None and rel_doc_el.text
                    else None
                )

                rel_props: Dict[str, str] = {}
                rel_props_container = rel.find(_tag("properties"))
                if rel_props_container is not None:
                    for prop in rel_props_container.findall(_tag("property")):
                        pd_ref = prop.get("propertyDefinitionRef", "")
                        value_el = prop.find(_tag("value"))
                        value = (
                            value_el.text.strip()
                            if value_el is not None and value_el.text
                            else ""
                        )
                        key = property_defs.get(pd_ref, pd_ref)
                        if key:
                            rel_props[key] = value

                relationships.append({
                    "identifier": identifier,
                    "type": rel_type,
                    "source": source,
                    "target": target,
                    "description": rel_description,
                    "properties": rel_props,
                })

        # ADR-0014 Decision 4: which application-kind elements are Applications, decided
        # once, before anything is written, with a plain reason for the preview.
        decisions = decide_application_kinds(
            {"elements": elements, "relationships": relationships},
            promote_top_level=promote_top_level,
        )
        for index, elem in enumerate(elements):
            decision = decisions.get(decision_key(index, elem))
            if decision is None:
                continue
            elem["declared_type"] = elem["type"]
            elem["type"] = decision["type"]
            elem["application_kind_decision"] = decision["reason"]
            elem["stated_application"] = decision["stated"]
            elem["layer"] = "Application"

        return {
            "model_name": model_name,
            "elements": elements,
            "relationships": relationships,
            "errors": errors,
            "source_xml": xml_content,
            "application_kind_decisions": decisions,
        }

    # ------------------------------------------------------------------
    # Relationship classification — shared by preview and execute so the
    # two agree (DOGFOOD-003 acceptance criterion). The only validity
    # authority consulted is RelationshipValidator, backed by the
    # element-type-keyed matrix in app/config/archimate_relationship_matrix.py.
    # ------------------------------------------------------------------

    @staticmethod
    def _can_keep_as_association(validator, source_type, target_type, rel_type):
        """True when only the pairing is refused: both element types and the
        relationship type are known, and Association is allowed for the pair."""
        from app.config.archimate_relationship_matrix import RELATIONSHIP_TYPES

        # Composition, aggregation and specialization assert that one thing is
        # part of, or a kind of, another. Softening a wrong one to a plain
        # association would hide a modelling error, so those stay refused.
        if (
            not rel_type
            or rel_type == "association"
            or rel_type not in RELATIONSHIP_TYPES
            or rel_type in ("composition", "aggregation", "specialization")
        ):
            return False
        return validator.validate_relationship(
            source_type, target_type, "association"
        ).is_valid

    def _classify_relationships(
        self,
        relationships: List[Dict[str, Any]],
        type_by_identifier: Dict[str, str],
        id_map: Dict[str, int] = None,
    ) -> List[Dict[str, Any]]:
        """Validate each parsed relationship and annotate it with a status.

        ``type_by_identifier`` maps an OEF element identifier to its
        ArchiMate element type — used to resolve source/target types for
        the validator (identifiers, not names, because names may collide).

        ``id_map``, when given, additionally maps identifier to the
        already-persisted DB row id, so a valid entry also carries
        ``source_id``/``target_id`` ready for insert. Pass ``None`` for a
        type-only preview pass.

        Returns a list of dicts, each with ``identifier``, ``type``
        (normalized), ``source``, ``target``, ``status`` ("valid" |
        "invalid"), and — only when invalid — ``reason`` (human-readable,
        never a raw exception) and ``suggestions``.
        """
        from app.modules.architecture.routes.archimate_routes import _normalize_rel_type
        from app.modules.architecture.services.relationship_validator import (
            RelationshipValidator,
        )

        validator = RelationshipValidator()
        out: List[Dict[str, Any]] = []

        for rel in relationships:
            identifier = rel.get("identifier", "")
            rel_type_raw = rel.get("type", "")
            source_ref = rel.get("source", "")
            target_ref = rel.get("target", "")

            source_type = type_by_identifier.get(source_ref)
            target_type = type_by_identifier.get(target_ref)

            entry: Dict[str, Any] = {
                "identifier": identifier,
                "type": rel_type_raw,
                "source": source_ref,
                "target": target_ref,
                "description": rel.get("description"),
                "properties": rel.get("properties") or {},
            }

            if source_type is None or target_type is None:
                entry["status"] = "invalid"
                entry["reason"] = (
                    "Source or target element was not imported "
                    "(missing, unnamed, or failed to parse)."
                )
                entry["suggestions"] = []
                out.append(entry)
                continue

            normalized_type = _normalize_rel_type(rel_type_raw)
            entry["type"] = normalized_type
            entry["modelled_type"] = normalized_type
            result = validator.validate_relationship(
                source_type, target_type, normalized_type
            )

            if not result.is_valid and self._can_keep_as_association(
                validator, source_type, target_type, normalized_type
            ):
                # The pair is known and the type is a real ArchiMate type, but
                # the matrix has no entry for it. Association is permitted
                # between any two elements (ArchiMate 3.2 section 5.4.3), so
                # keep the relationship and record what was modelled.
                modelled = normalized_type.capitalize()
                note = f"As modelled: {modelled}."
                prior = (entry.get("description") or "").strip()
                entry["description"] = f"{prior} {note}".strip()
                entry["type"] = "association"
                entry["softened"] = True
                entry["notice"] = (
                    f"{modelled} between {source_type} and {target_type} is not "
                    f"in the ArchiMate 3.2 matrix; stored as Association "
                    f"(identifier={identifier or 'n/a'})."
                )
                result = validator.validate_relationship(
                    source_type, target_type, "association"
                )

            if not result.is_valid:
                entry["status"] = "invalid"
                entry["reason"] = (
                    "; ".join(result.errors)
                    if result.errors
                    else (
                        f"'{rel_type_raw}' is not a valid ArchiMate relationship "
                        f"between {source_type} and {target_type}."
                    )
                )
                entry["suggestions"] = result.suggestions
                out.append(entry)
                continue

            entry["status"] = "valid"
            if id_map is not None:
                entry["source_id"] = id_map.get(source_ref)
                entry["target_id"] = id_map.get(target_ref)
            out.append(entry)

        return out

    # ------------------------------------------------------------------
    # Preview (diff against existing DB)
    # ------------------------------------------------------------------

    @staticmethod
    def _importing_organization_id(explicit: Optional[int]) -> Optional[int]:
        """The organisation a run reads and writes.

        An explicit id wins (CLI and programme setup have no ambient tenant);
        then the request's tenant; then the single organisation of a
        single-tenant install. ``None`` only when none of these exist.
        """
        if explicit is not None:
            return int(explicit)
        try:
            from app.utils.tenant import current_organization_id

            ambient = current_organization_id()
            if ambient is not None:
                return ambient
        except Exception:
            pass
        from app.models.mixins.core import _default_org_id

        return _default_org_id()

    @classmethod
    def _require_organization_id(cls, explicit: Optional[int]) -> int:
        org_id = cls._importing_organization_id(explicit)
        if org_id is None:
            raise ImportRequestError(
                "Name the organisation to import into: several exist and none was given."
            )
        return org_id

    @staticmethod
    def _in_organization(model_cls, org_id: Optional[int]) -> list:
        """Explicit organisation predicate; the ORM fence is a no-op with no
        ambient tenant (CLI), so every read here states its own."""
        assert org_id is not None, "an import always names its organisation"
        return [model_cls.organization_id == org_id]

    def preview_import(
        self, parsed_data: Dict[str, Any], organization_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Compare parsed elements against the DB and classify each element.

        Returns::

            {
                "elements": [
                    {
                        ...element fields...,
                        "status": "new" | "exists" | "conflict" | "invalid",
                        "existing_id": int | None,
                        "diff": str | None,
                    }, ...
                ],
                "summary": {"new": N, "exists": N, "conflict": N, "invalid": N, "total": N, ...},
                "relationships": [...],
                "errors": [...],
            }

        ``invalid`` (DOGFOOD-001) counts the elements ``execute_import`` will
        refuse, so the number shown before the import matches what happens
        during it.
        """
        from app.models.archimate_core import ArchiMateElement

        org_id = self._require_organization_id(organization_id)
        elements = parsed_data.get("elements", [])
        preview_elements: List[Dict[str, Any]] = []
        counts = {"new": 0, "exists": 0, "conflict": 0, "invalid": 0}

        for elem in elements:
            if elem.get("invalid"):
                preview_elements.append({
                    **elem,
                    "status": "invalid",
                    "existing_id": None,
                    "diff": elem["invalid"],
                })
                counts["invalid"] += 1
                continue

            name_lower = elem["name"].strip().lower()
            elem_type = elem["type"]

            # Duplicate detection: case-insensitive name + exact type match; an
            # application-kind element matches either kind (ADR-0014 rule 5).
            existing = ArchiMateElement.query.filter(  # model-safety-ok: bounded by XML element count
                db.func.lower(ArchiMateElement.name) == name_lower,
                ArchiMateElement.type.in_(self._match_types(elem_type)),
                *self._in_organization(ArchiMateElement, org_id),
            ).first()

            entry = {**elem}

            if existing is None:
                entry["status"] = "new"
                entry["existing_id"] = None
                entry["diff"] = None
                counts["new"] += 1
            else:
                existing_desc = (existing.description or "").strip()
                import_desc = (elem.get("description") or "").strip()
                entry["existing_id"] = existing.id
                if existing_desc == import_desc:
                    entry["status"] = "exists"
                    entry["diff"] = None
                    counts["exists"] += 1
                else:
                    entry["status"] = "conflict"
                    entry["diff"] = (
                        f"Description differs: existing='{existing_desc[:120]}'"  # raw-html-ok: internal diff-conflict message field, never rendered as HTML/browser output
                    )
                    counts["conflict"] += 1

            preview_elements.append(entry)

        # Invalid elements will not be written, so relationships touching
        # them are unresolved in preview exactly as they will be in execute.
        type_by_identifier = {
            elem["identifier"]: elem["type"]
            for elem in elements
            if elem.get("identifier") and not elem.get("invalid")
        }
        relationships_preview = self._classify_relationships(
            parsed_data.get("relationships", []), type_by_identifier, id_map=None
        )
        relationships_valid = sum(1 for r in relationships_preview if r["status"] == "valid")
        relationships_invalid = len(relationships_preview) - relationships_valid

        return {
            "elements": preview_elements,
            "summary": {
                **counts,
                "total": len(elements),
                "relationships_valid": relationships_valid,
                "relationships_invalid": relationships_invalid,
                "relationships_total": len(relationships_preview),
            },
            "relationships": relationships_preview,
            "errors": parsed_data.get("errors", []),
            "application_kind_decisions": parsed_data.get("application_kind_decisions", {}),
        }

    @staticmethod
    def _match_types(elem_type: str) -> tuple:
        """The stored types a same-named element may have to count as the same one."""
        return application_kind_types() if is_application_kind(elem_type) else (elem_type,)

    # ------------------------------------------------------------------
    # Execute import
    # ------------------------------------------------------------------

    def execute_import(
        self,
        parsed_data: Dict[str, Any],
        strategy: str = "skip_duplicates",
        organization_id: Optional[int] = None,
        *,
        source_ref: Optional[str] = None,
        source_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create/update ArchiMate elements from parsed OEF data.

        Strategies:
        - ``skip_duplicates``: create only new elements, skip existing
        - ``update_existing``: create new + update description of existing
        - ``create_all``: create all elements regardless of duplicates

        Also writes relationships in a second pass (DOGFOOD-003) and
        ``<properties>`` into ``custom_properties`` (DOGFOOD-004, per the
        convention in ``docs/adr/0009-continuous-model-maintenance.md (Addendum)``).

        Returns::

            {
                "created": int,
                "updated": int,
                "skipped": int,
                "failed": int,
                "errors": [str, ...],
                "relationships_created": int,
                "relationships_skipped": int,
                "relationships_failed": [
                    {"identifier", "type", "source", "target", "reason"}, ...
                ],
                "created_ids": [int, ...],
                "model_id": int | None,
                "element_results": [
                    {"identifier", "id", "name", "type", "layer",
                     "custom_properties", "status"}, ...
                ],
                "relationship_results": [
                    {"identifier", "id", "source_id", "target_id", "type",
                     "status"}, ...
                ],
            }

        ``status`` on an element result is ``created`` / ``updated`` /
        ``skipped``; on a relationship result ``created`` / ``skipped``
        (already stored). Refusals are in ``errors`` and
        ``relationships_failed``, never in these lists.

        Each run is recorded as an ``ArchitectureModel`` (the imported XML in
        ``model_data``) and every element or relationship it creates carries
        that ``architecture_id``. A created Driver, Goal or
        ApplicationComponent element also gets its domain row, linked by
        ``archimate_element_id``, each inside its own savepoint so a domain
        row the database refuses never costs the element.

        DOGFOOD-001: every element is written inside its own savepoint
        (``db.session.begin_nested()``). An element the parser flagged as
        ``invalid``, or one the database refuses, is counted in ``failed``
        and named in ``errors`` in plain English; the rest of the model
        still lands. Nothing here ever aborts the whole batch.
        """
        from datetime import datetime, timezone

        from app.models.archimate_core import (
            ArchiMateElement,
            ArchiMateRelationship,
            ArchitectureModel,
        )

        if strategy not in STRATEGIES:
            raise ValueError(f"Invalid strategy: {strategy}")

        org_id = self._require_organization_id(organization_id)
        org_kw = {} if org_id is None else {"organization_id": org_id}
        elements = parsed_data.get("elements", [])
        created = 0
        updated = 0
        skipped = 0
        failed = 0
        errors: List[str] = list(parsed_data.get("errors", []))

        # OEF identifier -> {"db_id": int, "type": str}. Populated for every
        # element touched this run, including pre-existing ones matched by
        # name+type under skip_duplicates/update_existing — otherwise every
        # relationship touching a pre-existing element would fail to resolve.
        id_map: Dict[str, Dict[str, Any]] = {}
        created_ids: List[int] = []
        element_results: List[Dict[str, Any]] = []
        relationship_results: List[Dict[str, Any]] = []

        # Snapshot for restore: opened before anything is written, on the
        # import's own session-log row; None outside a signed-in request.
        from app.services import import_snapshot_service

        snapshot = import_snapshot_service.begin(
            strategy=strategy, model_name=(parsed_data.get("model_name") or "")[:100]
        )
        model_id = self._record_model(ArchitectureModel, parsed_data, org_id)
        if snapshot is not None:
            snapshot.note_model(model_id)
        created_relationship_ids: List[int] = []

        def _element_result(row, identifier: str, status: str) -> None:
            element_results.append({
                "identifier": identifier,
                "id": row.id,
                "name": row.name,
                "type": row.type,
                "layer": row.layer,
                "custom_properties": dict(row.custom_properties or {}),
                "status": status,
            })

        def _write_properties(target_row, props: Dict[str, str], merge: bool) -> None:
            """Persist parsed <properties> onto custom_properties (DOGFOOD-004).

            Every property is a literal key, value as parsed — no renaming,
            no coercion. ``archie:imported_at`` is the one namespaced,
            reserved key, recording provenance for a future model-age
            measurement (ADR 0009 names no field of its own).
            """
            if not props:
                return
            base = dict(target_row.custom_properties or {}) if merge else {}
            base.update(props)
            base["archie:imported_at"] = datetime.now(timezone.utc).isoformat()
            target_row.custom_properties = base

        for elem in elements:
            name = elem["name"].strip()
            name_lower = name.lower()
            elem_type = elem["type"]
            layer = elem.get("layer", self.TYPE_TO_LAYER.get(elem_type, "Other"))
            description = elem.get("description")
            identifier = elem.get("identifier", "")
            # The type string records the specialisation (ADR-0014 Decision 2), so the
            # property that carried it in the file is not stored beside it.
            props = {
                k: v
                for k, v in (elem.get("properties") or {}).items()
                if str(k).strip().lower() != SPECIALIZATION_PROPERTY
            }

            # DOGFOOD-001: the parser already flagged this element (name or
            # type wider than its column). Refuse it here, by name, and carry
            # on — the database never sees it, so nothing else is lost.
            if elem.get("invalid"):
                failed += 1
                errors.append(
                    f"Refused '{name[:40]}…' ({elem_type}, identifier={identifier or 'n/a'}): "
                    f"{elem['invalid']}."
                )
                continue

            try:
                # One savepoint per element. A failed INSERT/UPDATE rolls
                # back this element only; the outer transaction stays live
                # for every element after it, and the counters/id_map are
                # touched only after the flush succeeds, so they never
                # describe a row that does not exist.
                retyped_row = None
                with db.session.begin_nested():
                    existing = None
                    if strategy != "create_all":
                        # ADR-0014 rule 5: an application-kind element matches by name across
                        # both kinds in this organisation. The stored type is kept unless the
                        # file itself states the element is an Application.
                        existing = ArchiMateElement.query.filter(  # model-safety-ok: bounded by XML element count
                            db.func.lower(ArchiMateElement.name) == name_lower,
                            ArchiMateElement.type.in_(self._match_types(elem_type)),
                            *self._in_organization(ArchiMateElement, org_id),
                        ).first()
                        if (
                            existing is not None
                            and elem.get("stated_application")
                            and existing.type != elem_type
                            and is_application_kind(existing.type)
                        ):
                            if snapshot is not None:
                                snapshot.note_updated_element(existing)
                            existing.type = elem_type
                            existing.layer = layer
                            db.session.flush()
                            retyped_row = existing

                    if existing is None:
                        new_elem = ArchiMateElement(
                            name=name,
                            type=elem_type,
                            layer=layer,
                            description=description,
                            architecture_id=model_id,
                            **org_kw,
                            source_kind="import",
                            source_ref=source_ref,
                            source_label=source_label,
                            source_recorded_at=datetime.now(timezone.utc),
                        )
                        db.session.add(new_elem)
                        _write_properties(new_elem, props, merge=False)
                        db.session.flush()
                        if identifier:
                            id_map[identifier] = {"db_id": new_elem.id, "type": elem_type}
                        created += 1
                        created_ids.append(new_elem.id)
                        if snapshot is not None:
                            snapshot.note_created_element(new_elem.id)
                        _element_result(new_elem, identifier, "created")
                        created_row = new_elem
                    elif strategy == "update_existing":
                        if snapshot is not None:
                            snapshot.note_updated_element(existing)
                        if description is not None:
                            existing.description = description
                        _write_properties(existing, props, merge=True)
                        existing.source_kind = "import"
                        existing.source_ref = source_ref
                        existing.source_label = source_label
                        existing.source_recorded_at = datetime.now(timezone.utc)
                        db.session.flush()
                        if identifier:
                            id_map[identifier] = {"db_id": existing.id, "type": existing.type}
                        updated += 1
                        _element_result(existing, identifier, "updated")
                        created_row = None
                    else:
                        # skip_duplicates — element itself is untouched, but it
                        # still needs an id-map entry so relationships that
                        # target it can resolve.
                        if identifier:
                            id_map[identifier] = {"db_id": existing.id, "type": existing.type}
                        skipped += 1
                        _element_result(existing, identifier, "skipped")
                        created_row = None

            except Exception as exc:
                logger.warning(
                    "Failed to import element '%s' (%s): %s",
                    name, elem_type, exc,
                )
                # M6: a raw exception string (e.g. a DB constraint message)
                # must never reach the client. The savepoint has already
                # been rolled back by the context manager, so the session
                # is usable for the next element.
                failed += 1
                errors.append(
                    f"Failed to import '{name[:40]}' ({elem_type}, identifier={identifier or 'n/a'}): "
                    "the database refused this element. It was skipped; the rest of the model "
                    "was still imported."
                )
                continue

            if created_row is None and retyped_row is not None:
                created_row = retyped_row  # an upgraded component needs its inventory row
            if created_row is not None:
                domain = self._create_domain_row(created_row)
                if domain is not None and snapshot is not None:
                    if domain["created"]:
                        snapshot.note_domain_created(domain["table"], domain["id"])
                    else:
                        snapshot.note_domain_linked(
                            domain["table"], domain["id"], domain["previous_element_id"]
                        )

        # --- Relationships (second pass, after every element has an id) ---
        type_by_identifier = {ident: info["type"] for ident, info in id_map.items()}
        classified = self._classify_relationships(
            parsed_data.get("relationships", []), type_by_identifier, id_map=None
        )
        # id_map carries db_id per identifier; attach it to the "valid" entries.
        db_id_by_identifier = {ident: info["db_id"] for ident, info in id_map.items()}

        relationships_created = 0
        relationships_skipped = 0
        relationships_softened = 0
        relationships_failed: List[Dict[str, Any]] = []
        relationship_notices: List[str] = []

        for rel in classified:
            if rel["status"] == "invalid":
                relationships_failed.append({
                    "identifier": rel["identifier"],
                    "type": rel["type"],
                    "source": rel["source"],
                    "target": rel["target"],
                    "reason": rel["reason"],
                    "suggestions": rel.get("suggestions", []),
                })
                continue

            source_db_id = db_id_by_identifier.get(rel["source"])
            target_db_id = db_id_by_identifier.get(rel["target"])
            if not source_db_id or not target_db_id:
                relationships_failed.append({
                    "identifier": rel["identifier"],
                    "type": rel["type"],
                    "source": rel["source"],
                    "target": rel["target"],
                    "reason": "Source or target element was not imported this run.",
                })
                continue

            try:
                # Same savepoint discipline as elements: one bad row never
                # takes the rest of the relationships (or the elements
                # already flushed) down with it.
                with db.session.begin_nested():
                    # Identity is (source, target, modelled type), checked
                    # before softening: a softened row is told apart from a
                    # modelled Association by its "As modelled: <Type>." note.
                    existing_rel = None
                    for cand in ArchiMateRelationship.query.filter(
                        ArchiMateRelationship.source_id == source_db_id,
                        ArchiMateRelationship.target_id == target_db_id,
                        ArchiMateRelationship.type == rel["type"],
                        *self._in_organization(ArchiMateRelationship, org_id),
                    ).all():
                        cand_note = _modelled_note_type(cand.description)
                        if rel.get("softened"):
                            if cand_note == rel["modelled_type"]:
                                existing_rel = cand
                                break
                        elif cand_note is None:
                            existing_rel = cand
                            break
                    if existing_rel is not None:
                        relationships_skipped += 1
                        if strategy == "update_existing":
                            existing_rel.source_kind = "import"
                            existing_rel.source_ref = source_ref
                            existing_rel.source_label = source_label
                            existing_rel.source_recorded_at = datetime.now(timezone.utc)
                            _write_properties(
                                existing_rel, rel.get("properties") or {}, merge=True
                            )
                        stored_rel, rel_status = existing_rel, "skipped"
                    else:
                        new_rel = ArchiMateRelationship(
                            type=rel["type"],
                            source_id=source_db_id,
                            target_id=target_db_id,
                            # <documentation> on the relationship (DOGFOOD-003):
                            # "ruling constrains offer" is the reason the edge
                            # exists, and it must survive the import.
                            description=rel.get("description"),
                            architecture_id=model_id,
                            **org_kw,
                            source_kind="import",
                            source_ref=source_ref,
                            source_label=source_label,
                            source_recorded_at=datetime.now(timezone.utc),
                        )
                        _write_properties(
                            new_rel, rel.get("properties") or {}, merge=False
                        )
                        db.session.add(new_rel)
                        db.session.flush()
                        relationships_created += 1
                        created_relationship_ids.append(new_rel.id)
                        stored_rel, rel_status = new_rel, "created"
                        if rel.get("notice"):
                            relationships_softened += 1
                            relationship_notices.append(rel["notice"])
                relationship_results.append({
                    "identifier": rel["identifier"],
                    "id": stored_rel.id,
                    "source_id": stored_rel.source_id,
                    "target_id": stored_rel.target_id,
                    "type": stored_rel.type,
                    "status": rel_status,
                })
            except Exception as exc:
                logger.warning(
                    "Failed to import relationship %s (%s -> %s): %s",
                    rel["identifier"], rel["source"], rel["target"], exc,
                )
                relationships_failed.append({
                    "identifier": rel["identifier"],
                    "type": rel["type"],
                    "source": rel["source"],
                    "target": rel["target"],
                    "reason": "Could not save this relationship due to an internal error.",
                })

        try:
            if snapshot is not None:
                snapshot.note_created_relationships(created_relationship_ids)
                snapshot.finish({
                    "created": created, "updated": updated,
                    "skipped": skipped, "failed": failed,
                })
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("Import commit failed: %s", exc)
            return {
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "failed": len(elements),
                "errors": ["Database commit failed. No changes were saved."],
                "relationships_created": 0,
                "relationships_skipped": 0,
                "relationships_failed": [],
                "created_ids": [],
                "model_id": None,
                "element_results": [],
                "relationship_results": [],
            }

        logger.info(
            "ArchiMate OEF import complete: %d created, %d updated, %d skipped, %d failed elements; "
            "%d created, %d skipped, %d failed relationships",
            created, updated, skipped, failed,
            relationships_created, relationships_skipped, len(relationships_failed),
        )

        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
            "relationships_created": relationships_created,
            "relationships_skipped": relationships_skipped,
            "relationships_softened": relationships_softened,
            "relationships_failed": relationships_failed,
            "relationship_notices": relationship_notices,
            "created_ids": created_ids,
            "model_id": model_id,
            "element_results": element_results,
            "relationship_results": relationship_results,
            "application_kind_decisions": parsed_data.get("application_kind_decisions", {}),
        }

    # ------------------------------------------------------------------
    # Provenance and domain rows
    # ------------------------------------------------------------------

    @staticmethod
    def _record_model(
        model_cls, parsed_data: Dict[str, Any], org_id: Optional[int] = None
    ) -> Optional[int]:
        """Record this import run as an ArchitectureModel; return its id.

        A refused row costs the provenance record only, never the import.
        """
        from datetime import datetime, timezone

        name = (parsed_data.get("model_name") or "").strip() or (
            "Imported Model " + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        )
        try:
            with db.session.begin_nested():
                record = model_cls(
                    name=name[:100],
                    version="1.0",
                    model_data=parsed_data.get("source_xml"),
                    **({} if org_id is None else {"organization_id": org_id}),
                )
                db.session.add(record)
                db.session.flush()
                return record.id
        except Exception as exc:
            logger.warning("Could not record the imported model: %s", exc)
            return None

    @staticmethod
    def _create_domain_row(element) -> Optional[Dict[str, Any]]:
        """Give a newly created Driver / Goal / Application element
        its domain row, linked back by ``archimate_element_id``.

        Idempotent per organisation, whatever the strategy: a domain row the
        organisation already holds for this element, or under the same name
        (case-insensitive), is linked rather than duplicated. ``create_all``
        creates a fresh element on every run; without this, every repeat of
        the same file added another portfolio row for the same application.

        Only an Application gets an inventory row (ADR-0014 rule 6); a component
        has none.
        """
        from app.models.archimate_core import ArchiMateElement

        if element.type in ("Driver", "Goal"):
            from app.models.motivation import Driver, Goal

            cls = Driver if element.type == "Driver" else Goal
        elif element.type == "Application":
            from app.models.application_portfolio import ApplicationComponent

            cls = ApplicationComponent
        else:
            return
        is_portfolio = element.type == "Application"

        org_id = element.organization_id
        name_key = (element.name or "").strip().lower()
        try:
            with db.session.begin_nested():
                if is_portfolio:
                    # Explicit organisation predicate: imports also run
                    # outside a request (programme setup, CLI), where the
                    # TenantMixin filter is absent.
                    existing = cls.query.filter(  # model-safety-ok: one row per imported element
                        cls.organization_id == org_id,
                        db.or_(
                            cls.archimate_element_id == element.id,
                            db.func.lower(cls.name) == name_key,
                        ),
                    ).order_by(
                        (cls.archimate_element_id == element.id).desc(), cls.id
                    ).first()
                else:
                    # Driver and Goal carry no organization_id of their own;
                    # their organisation is that of the element they realise,
                    # so a row with no element is never matched across tenants.
                    existing = (
                        cls.query.join(  # model-safety-ok: one row per imported element
                            ArchiMateElement, ArchiMateElement.id == cls.archimate_element_id
                        )
                        .filter(
                            ArchiMateElement.organization_id == org_id,
                            ArchiMateElement.type == element.type,
                            db.or_(
                                cls.archimate_element_id == element.id,
                                db.func.lower(cls.name) == name_key,
                            ),
                        )
                        .order_by((cls.archimate_element_id == element.id).desc(), cls.id)
                        .first()
                    )

                if existing is not None:
                    linked = existing.archimate_element_id
                    if linked is None or db.session.get(ArchiMateElement, linked) is None:
                        existing.archimate_element_id = element.id
                        db.session.flush()
                        return {
                            "table": cls.__table__.name, "id": existing.id,
                            "created": False, "previous_element_id": linked,
                        }
                    return None

                if is_portfolio:
                    row = cls(
                        name=element.name,
                        description=element.description,
                        archimate_element_id=element.id,
                        organization_id=org_id,
                    )
                else:
                    row = cls(
                        name=element.name,
                        description=element.description,
                        archimate_element_id=element.id,
                        architecture_id=element.architecture_id,
                        status="active",
                    )
                db.session.add(row)
                db.session.flush()
                return {
                    "table": cls.__table__.name, "id": row.id,
                    "created": True, "previous_element_id": None,
                }
        except Exception as exc:
            logger.warning(
                "Could not create the %s domain row for element %s: %s",
                element.type, element.id, exc,
            )
        return None

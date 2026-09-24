"""Lucidchart payload transformer for ArchiMate composer imports."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class LucidArchiMateTransformer:
    """Transform Lucidchart document contents into canonical ArchiMate payloads."""

    LUCID_CLASS_TO_ELEMENT_TYPE: Dict[str, str] = {
        "ArchiMate3ServiceBoxBlock": "ApplicationService",
        "ArchiMate3ComponentBoxBlock": "ApplicationComponent",
        "ArchiMate3InterfaceBoxBlock": "ApplicationInterface",
        "ArchiMate3LocationBoxBlock": "Location",
        "ArchiMate3CommunicationNetworkBoxBlock": "CommunicationNetwork",
        "ArchiMate3ObjectBoxBlock": "DataObject",
        "ArchiMate3GroupingBoxBlock": "Grouping",
    }

    LUCID_CLASS_TO_RENDERING_MODE: Dict[str, str] = {
        "ArchiMate3ServiceBoxBlock": "lucid_white_box",
        "ArchiMate3ComponentBoxBlock": "lucid_black_box",
        "ArchiMate3InterfaceBoxBlock": "lucid_white_box",
        "ArchiMate3EventBoxBlock": "lucid_white_box",
        "ArchiMate3LocationBoxBlock": "lucid_white_box",
        "ArchiMate3CommunicationNetworkBoxBlock": "lucid_white_box",
        "ArchiMate3ObjectBoxBlock": "lucid_white_box",
        "ArchiMate3GroupingBoxBlock": "lucid_white_box",
    }

    APPLICATION_CONTEXT_CLASSES = {
        "ArchiMate3ServiceBoxBlock",
        "ArchiMate3ComponentBoxBlock",
        "ArchiMate3InterfaceBoxBlock",
        "ArchiMate3ObjectBoxBlock",
    }

    KNOWN_STEREOTYPES = {
        "SERVICE",
        "INTERFACE",
        "EVENT",
        "DATA OBJECT",
    }

    ELEMENT_TYPE_TO_LAYER: Dict[str, str] = {
        "ApplicationService": "application",
        "ApplicationComponent": "application",
        "ApplicationInterface": "application",
        "ApplicationEvent": "application",
        "BusinessEvent": "business",
        "Location": "physical",
        "CommunicationNetwork": "technology",
        "DataObject": "application",
        "Grouping": "other",
    }

    # Lucid line-endpoint arrowhead styles → canonical ArchiMate relationship type.
    ENDPOINT_STYLE_TO_RELATIONSHIP: Dict[str, str] = {
        "Generalization": "specialization",
    }

    # Where a line's stroke pattern hides, by export flavour. ArchiMate encodes
    # meaning in the LINE as much as the arrowhead - solid, dashed and dotted
    # are three different relationships with the same head - so a reader that
    # only looks at arrowheads cannot tell serving from flow from access.
    STROKE_STYLE_PATHS = (
        ("stroke", "style"),
        ("style", "stroke", "style"),
        ("style", "strokeStyle"),
        ("strokeStyle",),
        ("lineStyle",),
    )

    # Arrowhead vocabulary. Matched on tokens rather than exact names because
    # Lucid spells these differently across stencils and export paths, and an
    # exact-match table silently degrades every unrecognised head to
    # "association" - which is precisely the bug this replaces.
    HEAD_DIAMOND = "diamond"
    HEAD_TRIANGLE = "triangle"
    HEAD_BALL = "ball"
    HEAD_ARROW = "arrow"

    # ArchiMate 3.2 notation, Appendix C. Read as (head, stroke) → relationship.
    NOTATION_TO_RELATIONSHIP: Dict[Tuple[str, str], str] = {
        # Structural: the diamond sits at the WHOLE end, so it also fixes direction.
        (HEAD_DIAMOND + "_filled", "solid"): "composition",
        (HEAD_DIAMOND + "_open", "solid"): "aggregation",
        # A hollow triangle is specialization when solid, realization when dotted.
        (HEAD_TRIANGLE + "_open", "solid"): "specialization",
        (HEAD_TRIANGLE + "_open", "dotted"): "realization",
        (HEAD_TRIANGLE + "_open", "dashed"): "realization",
        # Assignment carries a ball at the active-structure end.
        (HEAD_BALL + "_filled", "solid"): "assignment",
        # Dependency and dynamic relationships share an arrowhead and differ
        # only by stroke.
        (HEAD_ARROW + "_open", "solid"): "serving",
        (HEAD_ARROW + "_filled", "solid"): "serving",
        (HEAD_ARROW + "_open", "dashed"): "flow",
        (HEAD_ARROW + "_filled", "dashed"): "flow",
        (HEAD_ARROW + "_open", "dotted"): "access",
        (HEAD_ARROW + "_filled", "dotted"): "access",
    }

    CONNECTION_SPEC_KEY_MAP: Dict[str, str] = {
        "data": "data_name",
        "transfer strategy": "transfer_strategy",
        "interface type": "interface_type",
        "iam": "iam_method",
        "file format": "file_format",
        "file name": "file_name_pattern",
        "protocol": "protocol",
        "direction": "direction",
    }

    RELATIONSHIP_LABEL_MAP: Dict[str, str] = {
        "triggers": "triggering",
        "flow": "flow",
        "assigned": "assignment",
        "accesses": "access",
    }

    GEOMETRY_KEYS = {
        "x",
        "y",
        "width",
        "height",
        "position",
        "geometry",
        "bounds",
        "boundingBox",
        "vertices",
        "waypoints",
    }

    # Keys a Lucid export may use to name a shape's container. Which one appears
    # depends on the export path, so all are tried before falling back to
    # geometry.
    PARENT_ID_KEYS = (
        "parent",
        "parentId",
        "containedBy",
        "containerId",
        "groupId",
    )

    # Where a fill colour hides, by export flavour. Colour frequently carries
    # real meaning in a hand-drawn diagram - a RAG status, a readiness key, an
    # ownership legend - and it is meaning the author cannot get back once the
    # import drops it. It is preserved as a property rather than interpreted,
    # because only the author knows what their palette meant.
    FILL_COLOR_PATHS = (
        ("style", "fill", "color"),
        ("style", "fillColor"),
        ("style", "backgroundColor"),
        ("fillColor",),
    )

    # Nesting in ArchiMate denotes a structural relationship, and the spec allows
    # several. Composition is the usual reading and is what Archi emits when you
    # drop one element inside another; a Grouping aggregates its members rather
    # than owning them (ArchiMate 3.2 §4.5), so it gets aggregation. Association
    # is the last resort - weaker, but it keeps the containment visible instead
    # of discarding it.
    NESTING_FALLBACK_ORDER = ("composition", "aggregation", "association")

    # Text longer than this is prose, not a name. Chosen to match the widest
    # element name the repository stores (varchar(100)) with a little room:
    # anything past it could not be persisted as a name anyway, and in practice
    # a shape carrying that much text is a note, a legend or a risk register.
    ANNOTATION_TEXT_LENGTH = 100

    # Titles a legend block carries. A shape holding one of these, or holding
    # nothing but an ArchiMate type name, is a key explaining the notation - not
    # a system. See _is_legend_swatch.
    LEGEND_TITLES = {"LEGEND", "KEY", "LEGEND:", "KEY:"}

    # Shorthand a legend uses for a concept when it does not spell out the full
    # ArchiMate name - "Application" for Application Component is the common one.
    # These are only ever treated as swatches when the shape is also connected to
    # nothing, which is what keeps a genuinely-named element safe.
    LEGEND_CONCEPT_ALIASES = {
        "APPLICATION", "APPLICATIONS", "BUSINESS", "TECHNOLOGY",
        "MOTIVATION", "STRATEGY", "IMPLEMENTATION", "DATA",
    }

    # Qualifiers architects put in element names. They are real attributes -
    # delivery phase, country scope, deployment model - written where the tool
    # gave them nowhere else to go, and they stay trapped in the name unless
    # something lifts them out. "(Phase 2)" should be filterable, not grep-able.
    QUALIFIER_PATTERNS = (
        (r"\bphase\s*([0-9]+)\b", "phase"),
        (r"\b(DE|UK|US|FR|EU)\s+only\b", "scope"),
        (r"\b(DE|UK|US|FR|EU)\s+payments\s+only\b", "scope"),
        (r"\b(SaaS|PaaS|IaaS|On-?Prem(?:ise)?|Server\s+Image)\b", "deployment"),
    )

    def __init__(
        self,
        event_element_type: str = "BusinessEvent",
        fallback_element_type: Optional[str] = None,
    ):
        """
        Args:
            event_element_type: which layer Lucid's layer-agnostic Event shape
                maps to when context does not settle it.
            fallback_element_type: what to do with shapes drawn with ordinary
                rectangles instead of Lucid's ArchiMate stencils. Default None
                keeps the strict behaviour - unrecognised shapes are skipped and
                reported. Set it (e.g. "ApplicationComponent") and those shapes
                are imported as that type, carrying their name, colour and
                nesting, with the guess recorded on every element so it can be
                reviewed and corrected in bulk afterwards.

                Opt-in rather than default because inventing a type for every
                box silently produces a model that looks authoritative and is
                largely fiction. Retyping 40 correctly-named, correctly-nested
                elements is minutes of work; discovering later that a diagram
                was imported as confident nonsense is not.
        """
        if event_element_type not in {"BusinessEvent", "ApplicationEvent"}:
            raise ValueError(
                "event_element_type must be 'BusinessEvent' or 'ApplicationEvent'"
            )
        if fallback_element_type is not None:
            known = self._canonical_type_index()
            if known and fallback_element_type not in known.values():
                raise ValueError(
                    f"fallback_element_type '{fallback_element_type}' is not an "
                    f"ArchiMate 3.2 element type"
                )
        self.event_element_type = event_element_type
        self.fallback_element_type = fallback_element_type

    def transform_document(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return canonical elements, relationships, and import warnings."""
        if not isinstance(payload, dict):
            raise ValueError("Lucidchart payload must be a dictionary")

        payload = self._normalize_payload(payload)
        pages = payload.get("pages") or []
        if not isinstance(pages, list) or not pages:
            raise ValueError("Lucidchart payload must include at least one page")

        warnings: List[str] = []
        elements: List[Dict[str, Any]] = []
        relationships: List[Dict[str, Any]] = []
        imported_shape_ids: Dict[str, Dict[str, Any]] = {}
        skipped_connector_count = 0
        skipped_relationship_count = 0
        total_shapes_seen = 0
        fallback_used = 0
        annotations = 0
        legend_swatches = 0
        unmapped_classes: set = set()
        # Structured record of what the deterministic pass could not type/place,
        # so an opt-in AI-assist step can propose types and relationships for
        # exactly these gaps (and only these) for human review. Counts alone,
        # which is all the warnings carry, give an LLM nothing to reason over.
        skipped_shapes: List[Dict[str, Any]] = []
        skipped_relationships: List[Dict[str, Any]] = []
        inferred_event_type = self._infer_event_element_type(pages)
        # Only needed to decide container-vs-leaf for fallback typing.
        container_ids = (
            self._container_shape_ids(pages) if self.fallback_element_type else set()
        )

        connected_ids = self._connected_shape_ids(pages)
        stroke_data_available = self._payload_has_stroke_data(pages)
        geometry_present = self._payload_has_geometry(payload)
        if not geometry_present:
            warnings.append(
                "Lucidchart payload does not include geometry data; layout_hints are omitted to avoid fabricated coordinates."
            )

        for page_index, page in enumerate(pages):
            items = page.get("items") or {}
            page_id = page.get("id") or f"page-{page_index}"
            page_name = page.get("title") or page_id

            for shape in items.get("shapes") or []:
                total_shapes_seen += 1
                lucid_class = (shape.get("class") or "").strip()
                if lucid_class == "ConnectorBlock":
                    skipped_connector_count += 1
                    continue

                identifier = shape.get("id")
                if not identifier:
                    warnings.append("Encountered Lucidchart shape without an id; skipped.")
                    continue

                name = self._extract_shape_name(shape)
                if not name:
                    warnings.append(
                        f"Lucidchart shape '{identifier}' has no importable name; skipped."
                    )
                    continue

                # A shape holding a paragraph is a note, not an element. Real
                # diagrams carry commentary - risk registers, provisioning
                # checklists, legends - in ordinary boxes, and with a fallback
                # type configured those would otherwise import as applications
                # named after their entire contents.
                if len(name) > self.ANNOTATION_TEXT_LENGTH:
                    annotations += 1
                    continue

                if self._is_legend_swatch(name, str(identifier), connected_ids):
                    legend_swatches += 1
                    continue

                # Resolve the type from the strongest available signal, and
                # record which one was used so a guess can be reviewed later.
                type_source = "class"
                element_type = self._element_type_for_class(
                    lucid_class,
                    inferred_event_type=inferred_event_type,
                )
                if not element_type:
                    element_type = self._element_type_from_stereotype(shape)
                    type_source = "stereotype"
                if not element_type and self.fallback_element_type:
                    # A box enclosing other boxes is a grouping whatever it was
                    # drawn with; only leaves take the caller's default.
                    element_type = (
                        "Grouping" if identifier in container_ids
                        else self.fallback_element_type
                    )
                    type_source = "fallback"
                    fallback_used += 1
                if not element_type:
                    unmapped_classes.add(lucid_class or "(no class)")
                    skipped_shapes.append({
                        "id": identifier,
                        "name": name,
                        "lucid_class": lucid_class or None,
                        "reason": "no ArchiMate type could be resolved from the "
                                  "shape's class or stereotype",
                    })
                    continue

                lucid_stereotype = self._extract_shape_stereotype(shape)
                geometry = self._shape_geometry(shape)

                element = {
                    "id": identifier,
                    "identifier": identifier,
                    "name": name,
                    "type": element_type,
                    "layer": self.ELEMENT_TYPE_TO_LAYER.get(element_type, "other"),
                    # No rendering_mode: imported elements adopt the composer's
                    # native ArchiMate styling (layer colours, icons, shape per
                    # type) — they should look like natively-created elements, not
                    # carry a Lucid-specific box style. The original Lucid class is
                    # kept below purely as provenance metadata.
                    "rendering_mode": None,
                    "description": None,
                    "custom_properties": {
                        "lucid_class": lucid_class,
                        "lucid_page_id": page_id,
                        "lucid_page_name": page_name,
                        # How the type was decided: "class" (the shape said so),
                        # "stereotype" (the author labelled it), or "fallback"
                        # (nobody said, and a default was applied). Filter on
                        # this to find everything worth re-checking.
                        "lucid_type_source": type_source,
                    },
                }
                if lucid_stereotype:
                    element["custom_properties"]["lucid_stereotype"] = lucid_stereotype

                element["custom_properties"].update(self._extract_qualifiers(name))

                fill_color = self._extract_fill_color(shape)
                if fill_color:
                    element["custom_properties"]["lucid_fill_color"] = fill_color
                declared_parent = self._extract_parent_id(shape)
                if declared_parent:
                    # Consumed by _derive_nesting_relationships, which removes it
                    # once it has been turned into a relationship.
                    element["custom_properties"]["lucid_parent_id"] = declared_parent
                if geometry:
                    # Source layout available (e.g. Standard Import boundingBox or
                    # an Entelim round-trip export) — preserve it so the composer
                    # places elements exactly where they were, no auto-arrange.
                    element.update(geometry)
                elements.append(element)
                imported_shape_ids[identifier] = element

            for line in items.get("lines") or []:
                relationship = self._transform_line(
                    line, imported_shape_ids,
                    stroke_data_available=stroke_data_available)
                if relationship is None:
                    skipped_relationship_count += 1
                    src, tgt = self._resolve_line_endpoints(line)
                    skipped_relationships.append({
                        "id": line.get("id"),
                        "label": self._extract_line_label(line),
                        "source_id": src,
                        "target_id": tgt,
                    })
                    continue
                relationships.append(relationship)

        # Visual containment carries structure that no connector states. Derived
        # after every page is read, so a declared parent is resolvable wherever
        # it sits, and only where an explicit connector has not already spoken.
        existing_pairs = {
            (rel.get("source_id"), rel.get("target_id")) for rel in relationships
        }
        relationships.extend(
            self._derive_nesting_relationships(elements, existing_pairs, warnings)
        )
        # Any parent id that survived (target not imported) is provenance noise.
        for element in elements:
            element.get("custom_properties", {}).pop("lucid_parent_id", None)

        if annotations:
            warnings.append(
                f"Skipped {annotations} shape(s) holding more than {self.ANNOTATION_TEXT_LENGTH} "
                f"characters of text. A paragraph is a note, not an element name - these "
                f"are commentary (risks, checklists, legends) rather than architecture."
            )

        if legend_swatches:
            warnings.append(
                f"Skipped {legend_swatches} legend swatch(es) - shapes labelled with an "
                f"ArchiMate concept name and connected to nothing, which illustrate the "
                f"notation rather than use it."
            )

        if unmapped_classes:
            shown = ", ".join(sorted(unmapped_classes))[:300]
            warnings.append(
                f"Skipped {len(unmapped_classes)} unrecognised shape type(s): "
                f"{shown}. These are not from Lucid's ArchiMate stencil set and "
                f"carry no element type. Re-run with a fallback element type to "
                f"import them as a starting point instead, keeping their names, "
                f"colours and nesting."
            )

        if fallback_used:
            warnings.append(
                f"{fallback_used} shape(s) had no ArchiMate type of their own and "
                f"were imported as '{self.fallback_element_type}' (containers as "
                f"Grouping). Their names, colours and nesting are real; the TYPE "
                f"is a guess. Filter on the lucid_type_source property to review "
                f"and retype them."
            )

        if not elements and total_shapes_seen:
            distinct = sorted({
                (shape.get("class") or "").strip()
                for page in pages
                for shape in (page.get("items") or {}).get("shapes") or []
                if (shape.get("class") or "").strip()
            })
            shown = ", ".join(distinct)[:300] or "(none)"
            warnings.append(
                f"No ArchiMate shapes were recognized in this export "
                f"({total_shapes_seen} shape(s) found). The importer reads Lucid's "
                f"ArchiMate stencils and «stereotype» labels. Shape types in "
                f"this export: {shown}."
            )

        if skipped_connector_count:
            warnings.append(
                f"Skipped {skipped_connector_count} Lucid ConnectorBlock scaffolding shapes because they do not represent canonical ArchiMate elements."
            )
            warnings.append(
                "This Lucid export uses anonymous connector anchors for some relationships. Without source geometry or owner metadata, those edges cannot be attached to the correct ArchiMate element automatically."
            )
        if skipped_relationship_count:
            warnings.append(
                f"Skipped {skipped_relationship_count} Lucid relationships whose endpoints were unsupported or non-importable."
            )

        return {
            "model_name": payload.get("title") or "Lucidchart Import",
            "source_product": payload.get("product"),
            "elements": elements,
            "relationships": relationships,
            "layout_hints": {},
            "warnings": self._unique(warnings),
            "errors": [],
            "skipped": {
                "shapes": skipped_shapes,
                "relationships": skipped_relationships,
            },
        }

    @classmethod
    def _payload_has_stroke_data(cls, pages: List[Dict[str, Any]]) -> bool:
        """Whether ANY line in the export records a stroke pattern.

        Lucid's JSON export drops stroke entirely: a line carries its endpoints,
        its label and nothing about how it was drawn. That erases the difference
        between serving, flow and access, which in ArchiMate is carried by solid
        vs dashed vs dotted and by nothing else.

        Knowing the information was stripped - rather than assuming every line
        was solid - is what licenses the label fallback below.
        """
        for page in pages:
            for line in (page.get("items") or {}).get("lines") or []:
                for path in cls.STROKE_STYLE_PATHS:
                    value: Any = line
                    for key in path:
                        if not isinstance(value, dict):
                            value = None
                            break
                        value = value.get(key)
                    if isinstance(value, str) and value.strip():
                        return True
        return False

    def _transform_line(
        self,
        line: Dict[str, Any],
        imported_shape_ids: Dict[str, Dict[str, Any]],
        stroke_data_available: bool = True,
    ) -> Optional[Dict[str, Any]]:
        line_id = line.get("id")
        source_id, target_id = self._resolve_line_endpoints(line)
        if not source_id or not target_id:
            return None
        if source_id not in imported_shape_ids or target_id not in imported_shape_ids:
            return None

        label = self._extract_line_label(line)
        connection_spec = self._parse_connection_spec(line)
        relationship_type = self._infer_relationship_type(label, connection_spec)
        endpoint_style = self._endpoint_style(line)
        notation_used = False
        if relationship_type == "association":
            # An explicit label wins - the author wrote it deliberately. Failing
            # that, read the notation, which is how an ArchiMate diagram states
            # the relationship when nothing is written on the line. Most real
            # diagrams label almost nothing: this one carries five distinct
            # relationship types across ~200 edges and labels none of them.
            from_notation, swap = self._relationship_from_notation(line)
            if from_notation:
                relationship_type = from_notation
                notation_used = True
                if swap:
                    source_id, target_id = target_id, source_id
            else:
                relationship_type = (
                    self.ENDPOINT_STYLE_TO_RELATIONSHIP.get(endpoint_style)
                    or relationship_type
                )
        # A solid arrow carrying a data label, in an export that recorded no
        # stroke at all, is a data flow whose stroke was stripped on the way
        # out. ArchiMate says so directly: a flow relationship transfers
        # something between elements, and the something is written on the line.
        # "eSign File", "Compliance CSV", "CAMT Bank Statements" are payloads,
        # not descriptions of a service being offered.
        #
        # Guarded on the export genuinely lacking stroke data, so a complete
        # export is never second-guessed - there, a solid arrow means serving
        # and is left alone.
        if (
            relationship_type == "serving"
            and notation_used
            and not stroke_data_available
            and label
            and not connection_spec
        ):
            relationship_type = "flow"
            notation_used = "label"

        access_mode = self._infer_access_mode(relationship_type, label)
        flow_label = connection_spec.get("data_name") if relationship_type == "flow" else None
        if relationship_type == "flow" and not flow_label and label:
            flow_label = label
        # Preserve a meaningful edge label: an explicit line label, else the
        # arrowhead style (keeps ERD cardinality like "One Or More" visible).
        custom_label = None
        if label and not connection_spec:
            custom_label = label
        elif relationship_type == "association" and endpoint_style:
            custom_label = self._pretty_endpoint_style(endpoint_style)

        return {
            "id": line_id,
            "identifier": line_id,
            "type": relationship_type,
            "source_id": source_id,
            "target_id": target_id,
            "source": source_id,
            "target": target_id,
            "access_mode": access_mode,
            "flow_label": flow_label,
            "custom_label": custom_label,
            "description": None,
            "connection_spec": connection_spec or None,
            # How the type was decided. "notation" - read from how the line was
            # drawn. "stroke-stripped-label" - the export dropped the stroke, so
            # a labelled arrow was read as a flow; the weakest inference here
            # and the one to review first.
            "derived_from": (
                "stroke-stripped-label" if notation_used == "label"
                else "notation" if notation_used
                else None
            ),
        }

    @classmethod
    def _normalize_payload(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Accept both Lucid payload conventions.

        - REST ``documents/{id}/contents``: ``pages[].items.shapes`` where a shape
          carries ``class`` + ``textAreas`` (what the OAuth path delivers).
        - Standard Import / native ``.lucid`` ``document.json``: ``pages[].shapes``
          where a shape carries ``type`` + ``text``.

        Returns a payload in the ``items``/``class``/``textAreas`` convention the rest
        of the transformer expects. Non-destructive for payloads already in that
        convention.
        """
        if not isinstance(payload, dict):
            return payload
        pages = payload.get("pages")
        if not isinstance(pages, list):
            return payload

        for page in pages:
            if not isinstance(page, dict):
                continue
            items = page.get("items")
            items = dict(items) if isinstance(items, dict) else {}
            shapes = items.get("shapes")
            if shapes is None:
                shapes = page.get("shapes") or []
            lines = items.get("lines")
            if lines is None:
                lines = page.get("lines") or []
            items["shapes"] = [cls._normalize_shape(s) for s in shapes if isinstance(s, dict)]
            items["lines"] = [cls._normalize_line(line) for line in lines if isinstance(line, dict)]
            page["items"] = items
        return payload

    @staticmethod
    def _normalize_shape(shape: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(shape)
        if not normalized.get("class") and normalized.get("type"):
            normalized["class"] = normalized.get("type")
        if not normalized.get("textAreas"):
            text = normalized.get("text")
            if isinstance(text, dict):
                text = text.get("text")
            if isinstance(text, str) and text.strip():
                normalized["textAreas"] = [{"label": "Text", "text": text}]
        return normalized

    @staticmethod
    def _normalize_line(line: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(line)
        if not normalized.get("textAreas"):
            text = normalized.get("text")
            if isinstance(text, dict):
                text = text.get("text")
            if isinstance(text, str) and text.strip():
                normalized["textAreas"] = [{"label": "t0", "text": text}]
        for key in ("endpoint1", "endpoint2"):
            endpoint = normalized.get(key)
            if isinstance(endpoint, dict) and not endpoint.get("connectedTo"):
                ref = (
                    endpoint.get("id")
                    or endpoint.get("shapeId")
                    or endpoint.get("shape")
                    or endpoint.get("endpoint")
                )
                if ref:
                    endpoint = dict(endpoint)
                    endpoint["connectedTo"] = ref
                    normalized[key] = endpoint
        return normalized

    _CANONICAL_TYPE_INDEX: Optional[Dict[str, str]] = None

    @classmethod
    def _canonical_type_index(cls) -> Dict[str, str]:
        """{"businessprocess": "BusinessProcess", ...} for every ArchiMate type.

        Built from the relationship matrix so there is one list of element types
        in the codebase rather than a second one here that drifts from it.
        Returns {} if the matrix cannot be imported, which degrades this to the
        curated map alone rather than failing the import.
        """
        if cls._CANONICAL_TYPE_INDEX is None:
            try:
                from app.config.archimate_relationship_matrix import (  # noqa: PLC0415
                    ALL_ELEMENTS,
                )
                cls._CANONICAL_TYPE_INDEX = {
                    name.lower(): name for name in ALL_ELEMENTS
                }
            except Exception:  # noqa: BLE001 - transformer works standalone
                cls._CANONICAL_TYPE_INDEX = {}
        return cls._CANONICAL_TYPE_INDEX

    @classmethod
    def _type_from_token(cls, token: str) -> Optional[str]:
        """Match free text to a canonical type: "BUSINESS PROCESS" → BusinessProcess."""
        if not token:
            return None
        squashed = "".join(ch for ch in token if ch.isalnum()).lower()
        return cls._canonical_type_index().get(squashed)

    @classmethod
    def _element_type_from_class_name(cls, lucid_class: str) -> Optional[str]:
        """Derive the type from Lucid's own class name.

        Lucid names its ArchiMate stencils after the concept -
        ``ArchiMate3BusinessProcessBoxBlock``. Deriving the type from the name
        covers the whole stencil set instead of only the handful anyone has got
        round to listing, and it is safe because the result must match a real
        ArchiMate type before it is used.
        """
        if not lucid_class.startswith("ArchiMate3"):
            return None
        token = lucid_class[len("ArchiMate3"):]
        for suffix in ("BoxBlock", "Block", "Box"):
            if token.endswith(suffix):
                token = token[: -len(suffix)]
                break
        return cls._type_from_token(token)

    def _element_type_from_stereotype(self, shape: Dict[str, Any]) -> Optional[str]:
        """A «Capability» label above the name is the author stating the type."""
        text = self._extract_text(shape.get("textAreas") or [])
        if not text:
            return None
        first = text.splitlines()[0].strip().strip("«»<>").strip()
        return self._type_from_token(first)

    def _element_type_for_class(
        self,
        lucid_class: str,
        inferred_event_type: Optional[str] = None,
    ) -> Optional[str]:
        # Lucid's Event shape is layer-agnostic; context decides which it is.
        if lucid_class == "ArchiMate3EventBoxBlock":
            return inferred_event_type or self.event_element_type
        # The curated map first: it resolves Lucid's deliberately layer-agnostic
        # names (Object → DataObject, Component → ApplicationComponent), which a
        # literal reading of the class name would get wrong.
        explicit = self.LUCID_CLASS_TO_ELEMENT_TYPE.get(lucid_class)
        if explicit:
            return explicit
        return self._element_type_from_class_name(lucid_class)

    @classmethod
    def _container_shape_ids(cls, pages: List[Dict[str, Any]]) -> set:
        """Ids of shapes that visually enclose at least one other shape.

        A box drawn around other boxes is a grouping, whatever stencil it was
        drawn with. Used only when a fallback type is configured, to avoid
        typing a container as a leaf.
        """
        containers = set()
        for page in pages:
            shapes = (page.get("items") or {}).get("shapes") or []
            boxed = [(s.get("id"), cls._shape_geometry(s)) for s in shapes]
            boxed = [(i, g) for i, g in boxed if i and g]
            for outer_id, outer in boxed:
                for inner_id, inner in boxed:
                    if inner_id == outer_id:
                        continue
                    if cls._strictly_contains(outer, inner):
                        containers.add(outer_id)
                        break
        return containers

    def _infer_event_element_type(self, pages: List[Dict[str, Any]]) -> str:
        if self.event_element_type != "BusinessEvent":
            return self.event_element_type

        shape_classes = {
            (shape.get("class") or "").strip()
            for page in pages
            for shape in (page.get("items") or {}).get("shapes") or []
        }
        if shape_classes & self.APPLICATION_CONTEXT_CLASSES:
            return "ApplicationEvent"
        return self.event_element_type

    def _rendering_mode_for_class(self, lucid_class: str) -> str:
        return self.LUCID_CLASS_TO_RENDERING_MODE.get(lucid_class, "black_box")

    def _is_stereotype_line(self, line: str) -> bool:
        """A first line that states the type rather than the name.

        Covers Lucid's own layer-agnostic labels (SERVICE, DATA OBJECT) and any
        canonical ArchiMate type the author typed, with or without guillemets.
        """
        bare = line.strip().strip("«»<>").strip()
        return bare.upper() in self.KNOWN_STEREOTYPES or self._type_from_token(bare) is not None

    def _extract_shape_name(self, shape: Dict[str, Any]) -> str:
        text = self._extract_text(shape.get("textAreas") or [])
        if not text:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= 2 and self._is_stereotype_line(lines[0]):
            return " ".join(lines[1:]).strip()
        return " ".join(lines).strip()

    def _extract_shape_stereotype(self, shape: Dict[str, Any]) -> Optional[str]:
        text = self._extract_text(shape.get("textAreas") or [])
        if not text:
            return None
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        stereotype = lines[0].upper()
        if stereotype in self.KNOWN_STEREOTYPES:
            return stereotype
        return None

    def _extract_line_label(self, line: Dict[str, Any]) -> str:
        return self._extract_text(line.get("textAreas") or [])

    def _extract_text(self, text_areas: List[Dict[str, Any]]) -> str:
        texts = [str(area.get("text") or "").strip() for area in text_areas]
        texts = [text for text in texts if text]
        return "\n".join(texts).strip()

    def _parse_connection_spec(self, line: Dict[str, Any]) -> Dict[str, str]:
        spec: Dict[str, str] = {}

        for item in line.get("customData") or []:
            raw_key = str(item.get("key") or "").strip().lower()
            raw_value = str(item.get("value") or "").strip()
            canonical_key = self.CONNECTION_SPEC_KEY_MAP.get(raw_key)
            if canonical_key and raw_value:
                spec[canonical_key] = raw_value

        if spec:
            return spec

        label = self._extract_line_label(line)
        for raw_line in label.splitlines():
            if " - " not in raw_line:
                continue
            key, value = raw_line.split(" - ", 1)
            canonical_key = self.CONNECTION_SPEC_KEY_MAP.get(key.strip().lower())
            if canonical_key and value.strip():
                spec[canonical_key] = value.strip()

        return spec

    def _infer_relationship_type(
        self,
        label: str,
        connection_spec: Dict[str, str],
    ) -> str:
        lowered = label.strip().lower()
        if connection_spec:
            return "flow"
        if lowered == "creates":
            return "access"
        return self.RELATIONSHIP_LABEL_MAP.get(lowered, "association")

    def _infer_access_mode(self, relationship_type: str, label: str) -> Optional[str]:
        if relationship_type != "access":
            return None
        lowered = label.strip().lower()
        if lowered == "creates":
            return "write"
        return "read"

    @staticmethod
    def _shape_geometry(shape: Dict[str, Any]) -> Dict[str, int]:
        """Extract {x, y, w, h} from a shape when the export carries layout.

        Sources, in order: ``boundingBox`` (Lucid Standard Import / Entelim
        round-trip exports, keys x/y/w/h or x/y/width/height), then top-level
        x/y(+width/height). Returns {} when no usable position exists — never
        fabricates coordinates.
        """
        def _num(value: Any) -> Optional[float]:
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

        box = shape.get("boundingBox")
        if isinstance(box, dict):
            x, y = _num(box.get("x")), _num(box.get("y"))
            if x is not None and y is not None:
                w = _num(box.get("w")) or _num(box.get("width"))
                h = _num(box.get("h")) or _num(box.get("height"))
                geom = {"x": int(x), "y": int(y)}
                if w:
                    geom["w"] = int(w)
                if h:
                    geom["h"] = int(h)
                return geom

        x, y = _num(shape.get("x")), _num(shape.get("y"))
        if x is not None and y is not None:
            geom = {"x": int(x), "y": int(y)}
            w, h = _num(shape.get("width")), _num(shape.get("height"))
            if w:
                geom["w"] = int(w)
            if h:
                geom["h"] = int(h)
            return geom
        return {}

    @classmethod
    def _extract_qualifiers(cls, name: str) -> Dict[str, Any]:
        """Lift phase / scope / deployment out of an element name.

        The name is left exactly as drawn - it is what the architect sees on the
        diagram and what everything else matches on. These are additions, so
        "Formula NAV UK/DE (Server Image)" keeps its name AND gains
        deployment="Server Image", which a filter can reach.

        Every parenthetical is also kept verbatim under `qualifiers`, because the
        interesting one is always the next one nobody anticipated.
        """
        import re

        found: Dict[str, Any] = {}
        parentheticals = [p.strip() for p in re.findall(r"\(([^)]*)\)", name or "")]
        if parentheticals:
            found["qualifiers"] = parentheticals

        haystack = " ".join(parentheticals) or (name or "")
        for pattern, key in cls.QUALIFIER_PATTERNS:
            match = re.search(pattern, haystack, re.IGNORECASE)
            if match and key not in found:
                found[key] = match.group(1).strip()
        return found

    @classmethod
    def _is_legend_swatch(cls, name: str, identifier: str, connected: set) -> bool:
        """True for a shape that illustrates the notation rather than using it.

        A legend draws one box per concept and labels it with the concept's own
        name. Those import as elements called "Application", "Business Role",
        "Location" - six of them from the diagram that prompted this, deleted by
        hand afterwards.

        Two conditions together, because either alone is too eager: the text is
        exactly an ArchiMate concept name (or a legend title), AND the shape is
        connected to nothing. A real Location genuinely named "Location" would
        still be related to something; a swatch never is.
        """
        stripped = (name or "").strip()
        if not stripped or identifier in connected:
            return False
        upper = stripped.upper()
        if upper in cls.LEGEND_TITLES or upper in cls.LEGEND_CONCEPT_ALIASES:
            return True
        return cls._type_from_token(stripped) is not None

    @classmethod
    def _connected_shape_ids(cls, pages: List[Dict[str, Any]]) -> set:
        """Every shape id that some line attaches to."""
        connected = set()
        for page in pages:
            for line in (page.get("items") or {}).get("lines") or []:
                for key in ("endpoint1", "endpoint2"):
                    ref = (line.get(key) or {}).get("connectedTo")
                    if ref:
                        connected.add(str(ref))
        return connected

    @classmethod
    def _extract_fill_color(cls, shape: Dict[str, Any]) -> Optional[str]:
        """The shape's fill colour as a string, or None.

        Returned verbatim rather than normalised: Lucid emits '#FF9900',
        'rgb(255,153,0)' and named colours depending on the export, and the
        author's legend is easier to match against the original spelling.
        """
        for path in cls.FILL_COLOR_PATHS:
            value: Any = shape
            for key in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _extract_parent_id(cls, shape: Dict[str, Any]) -> Optional[str]:
        """An explicitly declared container id, if the export names one."""
        for key in cls.PARENT_ID_KEYS:
            value = shape.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
            # Some exports nest it: {"parent": {"id": "..."}}
            if isinstance(value, dict):
                inner = value.get("id") or value.get("shapeId")
                if isinstance(inner, (str, int)) and str(inner).strip():
                    return str(inner).strip()
        return None

    @staticmethod
    def _box(element: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
        """(x, y, w, h) when the element has a full rectangle, else None."""
        keys = ("x", "y", "w", "h")
        if not all(isinstance(element.get(k), int) for k in keys):
            return None
        x, y, w, h = (element[k] for k in keys)
        if w <= 0 or h <= 0:
            return None
        return x, y, w, h

    @classmethod
    def _strictly_contains(
        cls, outer: Dict[str, Any], inner: Dict[str, Any], tolerance: int = 2
    ) -> bool:
        """True when `outer`'s rectangle encloses `inner`'s and is larger.

        The tolerance absorbs a border width or a shape sitting flush against
        its container's edge, which is common once a diagram has been tidied.
        """
        outer_box, inner_box = cls._box(outer), cls._box(inner)
        if not outer_box or not inner_box:
            return False
        ox, oy, ow, oh = outer_box
        ix, iy, iw, ih = inner_box
        if ow * oh <= iw * ih:
            return False
        return (
            ix >= ox - tolerance
            and iy >= oy - tolerance
            and ix + iw <= ox + ow + tolerance
            and iy + ih <= oy + oh + tolerance
        )

    def _nesting_relationship_type(
        self, parent_type: str, child_type: str
    ) -> Optional[str]:
        """Pick a structural type for a nesting, refusing to invent an invalid one.

        Validated against the ArchiMate 3.2 matrix when it knows both element
        types. It does not know Grouping, Location or Junction - the three
        "Other" elements are absent from it - and for those the spec default is
        used directly rather than treating "unknown to the matrix" as "not
        allowed", which would silently drop the most common nesting of all.
        """
        try:
            from app.config.archimate_relationship_matrix import (  # noqa: PLC0415
                ALL_ELEMENTS,
                is_valid_relationship,
            )
        except Exception:  # noqa: BLE001 - transformer must work without app config
            ALL_ELEMENTS, is_valid_relationship = [], None

        # A Grouping collects its members and a Location holds what sits in it;
        # neither owns the way a composition claims. Everything else nests as
        # composition, which is what Archi emits for a dropped-in element.
        preferred = (
            "aggregation" if parent_type in ("Grouping", "Location") else "composition"
        )
        order = [preferred] + [t for t in self.NESTING_FALLBACK_ORDER if t != preferred]

        both_known = (
            is_valid_relationship is not None
            and parent_type in ALL_ELEMENTS
            and child_type in ALL_ELEMENTS
        )
        if not both_known:
            return preferred

        for candidate in order:
            if is_valid_relationship(parent_type, child_type, candidate):
                return candidate
        return None

    def _derive_nesting_relationships(
        self,
        elements: List[Dict[str, Any]],
        existing_pairs: set,
        warnings: List[str],
    ) -> List[Dict[str, Any]]:
        """Turn visual containment into structural ArchiMate relationships.

        A nested box is the most common way an architect writes "part of", and
        dropping it loses the diagram's whole structure: every element arrives
        as a flat, unrelated list. Two sources, in order of trust:

        1. An explicit container id, when the export names one.
        2. Geometry - the smallest shape that encloses this one, which is its
           nearest ancestor rather than the outermost box it happens to sit in.

        Geometric inference is confined to a single page: two shapes on
        different pages can share coordinates and are not nested.
        """
        by_id = {e["id"]: e for e in elements if e.get("id")}
        parents: Dict[str, str] = {}

        for element in elements:
            declared = element.get("custom_properties", {}).pop("lucid_parent_id", None)
            if declared and declared in by_id and declared != element["id"]:
                parents[element["id"]] = declared

        # Geometry fills in only where nothing was declared.
        page_of = lambda e: (e.get("custom_properties") or {}).get("lucid_page_id")  # noqa: E731
        for element in elements:
            if element["id"] in parents or not self._box(element):
                continue
            best: Optional[Dict[str, Any]] = None
            for candidate in elements:
                if candidate["id"] == element["id"]:
                    continue
                if page_of(candidate) != page_of(element):
                    continue
                if not self._strictly_contains(candidate, element):
                    continue
                if best is None or self._strictly_contains(best, candidate):
                    best = candidate
            if best is not None:
                parents[element["id"]] = best["id"]

        derived: List[Dict[str, Any]] = []
        unrepresentable = 0
        for child_id, parent_id in sorted(parents.items()):
            # An explicit connector already says how these two relate; a derived
            # one would duplicate or contradict it.
            if (parent_id, child_id) in existing_pairs or (child_id, parent_id) in existing_pairs:
                continue
            parent, child = by_id[parent_id], by_id[child_id]
            rel_type = self._nesting_relationship_type(parent["type"], child["type"])
            if rel_type is None:
                unrepresentable += 1
                continue
            derived.append({
                "id": f"nesting-{parent_id}-{child_id}",
                "identifier": f"nesting-{parent_id}-{child_id}",
                "type": rel_type,
                "source_id": parent_id,
                "target_id": child_id,
                "source": parent_id,
                "target": child_id,
                "access_mode": None,
                "flow_label": None,
                "custom_label": None,
                "description": "Derived from visual nesting in the Lucidchart source.",
                "connection_spec": None,
                "derived_from": "nesting",
            })

        if derived:
            warnings.append(
                f"Derived {len(derived)} structural relationship(s) from nested "
                f"shapes. Nesting is ambiguous in ArchiMate - composition was "
                f"assumed (aggregation under a Grouping). Review before publishing."
            )
        if unrepresentable:
            warnings.append(
                f"{unrepresentable} nested shape pair(s) have no valid ArchiMate "
                f"relationship between their element types, so the containment "
                f"was not imported."
            )
        return derived

    @staticmethod
    def _pretty_endpoint_style(style: str) -> str:
        """Readable edge label from a Lucid arrowhead style — e.g.
        'CFN ERD Zero Or More Arrow' → 'Zero Or More'."""
        s = style.strip()
        for prefix in ("CFN ERD ", "ERD "):
            if s.startswith(prefix):
                s = s[len(prefix):]
        if s.endswith(" Arrow"):
            s = s[: -len(" Arrow")]
        return s.strip() or style.strip()

    @classmethod
    def _stroke_pattern(cls, line: Dict[str, Any]) -> str:
        """"solid", "dashed" or "dotted" for a line, defaulting to solid.

        ArchiMate puts as much meaning in the stroke as the arrowhead: serving,
        flow and access can share a head and differ only here.
        """
        raw = ""
        for path in cls.STROKE_STYLE_PATHS:
            value: Any = line
            for key in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(key)
            if isinstance(value, str) and value.strip():
                raw = value.strip().lower()
                break
        if not raw:
            return "solid"
        if "dot" in raw:
            return "dotted"
        if "dash" in raw:
            return "dashed"
        return "solid"

    @classmethod
    def _classify_head(cls, style: str) -> str:
        """Normalise an arrowhead name to "<shape>_<fill>", or "" for none.

        Token matching rather than an exact-name table: Lucid spells these
        differently per stencil and export path ("Filled Diamond", "Composition
        Diamond", "ArchiMate Composition"), and an exact table degrades every
        name it has not seen to association without saying so.
        """
        s = (style or "").strip().lower()
        if not s or s == "none":
            return ""

        if "diamond" in s or "composit" in s or "aggregat" in s:
            shape = cls.HEAD_DIAMOND
        elif "triangle" in s or "generaliz" in s or "realiz" in s or "inherit" in s:
            shape = cls.HEAD_TRIANGLE
        elif "ball" in s or "circle" in s or "assign" in s:
            shape = cls.HEAD_BALL
        elif "arrow" in s:
            shape = cls.HEAD_ARROW
        else:
            return ""

        # "Open"/"hollow"/"empty" is the unfilled form; aggregation and
        # specialization are the unfilled twins of composition and realization.
        if any(t in s for t in ("open", "hollow", "empty", "line", "aggregat", "generaliz")):
            fill = "open"
        elif any(t in s for t in ("fill", "solid", "closed", "composit")):
            fill = "filled"
        else:
            # An unqualified head: Lucid's plain "Arrow" is a filled arrowhead,
            # and an unqualified triangle is the hollow specialization head.
            fill = "open" if shape == cls.HEAD_TRIANGLE else "filled"
        return f"{shape}_{fill}"

    @classmethod
    def _relationship_from_notation(
        cls, line: Dict[str, Any]
    ) -> Tuple[Optional[str], bool]:
        """(relationship type, swap_endpoints) read from the drawn notation.

        Returns (None, False) when the notation says nothing, leaving the
        caller's label-based inference and association fallback in charge.

        The swap exists because two ArchiMate heads sit at the SOURCE end: a
        composition/aggregation diamond marks the whole, and an assignment ball
        marks the active element. When Lucid put that head on endpoint2, the
        line was drawn from part to whole and the relationship runs the other
        way.
        """
        stroke = cls._stroke_pattern(line)
        head1 = cls._classify_head(str((line.get("endpoint1") or {}).get("style") or ""))
        head2 = cls._classify_head(str((line.get("endpoint2") or {}).get("style") or ""))

        # Heads that denote the source end rather than the target.
        source_headed = (cls.HEAD_DIAMOND, cls.HEAD_BALL)

        for head, on_target in ((head2, True), (head1, False)):
            if not head:
                continue
            rel = cls.NOTATION_TO_RELATIONSHIP.get((head, stroke))
            if rel is None:
                continue
            marks_source = head.split("_")[0] in source_headed
            # A source-marking head found on endpoint2 means the line runs
            # backwards; a target-marking head on endpoint1 likewise.
            swap = marks_source if on_target else not marks_source
            return rel, swap
        return None, False

    @staticmethod
    def _endpoint_style(line: Dict[str, Any]) -> str:
        """The meaningful arrowhead style on a line (prefer the target end)."""
        for key in ("endpoint2", "endpoint1"):
            style = str((line.get(key) or {}).get("style") or "").strip()
            if style and style.lower() != "none":
                return style
        return ""

    def _resolve_line_endpoints(self, line: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        endpoint1 = line.get("endpoint1") or {}
        endpoint2 = line.get("endpoint2") or {}
        source_id = endpoint1.get("connectedTo")
        target_id = endpoint2.get("connectedTo")

        style1 = str(endpoint1.get("style") or "").strip()
        style2 = str(endpoint2.get("style") or "").strip()
        if style1 == "Arrow" and style2 != "Arrow":
            return target_id, source_id

        return source_id, target_id

    def _payload_has_geometry(self, payload: Dict[str, Any]) -> bool:
        pages = payload.get("pages") or []
        for page in pages:
            items = page.get("items") or {}
            for collection_name in ("shapes", "lines", "groups", "layers"):
                for item in items.get(collection_name) or []:
                    if self._contains_geometry(item):
                        return True
        return False

    def _contains_geometry(self, value: Any) -> bool:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in self.GEOMETRY_KEYS:
                    return True
                if self._contains_geometry(nested):
                    return True
            return False
        if isinstance(value, list):
            return any(self._contains_geometry(item) for item in value)
        return False

    def _unique(self, items: List[str]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

"""
Spec Generator — Transform Solution Blueprints into API Contracts & Schemas.

Phase 1.5 of the Blueprint-to-Code pipeline. Generates:
- OpenAPI 3.1 (with versioned paths, governance policy, error schemas)
- JSON Schema (standalone validation schemas)
- AsyncAPI 2.6 (event channels from integration flows with protocol inference)
- Contract tests (request/response validation)
- Developer onboarding bundle (examples, mock server script)

Every generated artifact includes x-archimate-source traceability linking
back to the governed architecture element in Entelim
"""
import hashlib
import logging
import re
from collections import OrderedDict
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Naming Utilities ─────────────────────────────────────────────────────

_GENERIC_NAMES = re.compile(
    r"^(service|component|module|system|app|element|resource)\s*\d*$", re.IGNORECASE
)


def _slugify(name):
    """Convert a display name to a URL-safe kebab-case slug."""
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", (name or "unknown").strip())
    return re.sub(r"[\s_]+", "-", s).lower().strip("-")


def _snake(name):
    """Convert a display name to snake_case for code identifiers."""
    s = re.sub(r"[^a-zA-Z0-9]", "_", (name or "unknown").strip())
    return re.sub(r"_+", "_", s).strip("_").lower()


def _pascal(name):
    """Convert a display name to PascalCase for schema names."""
    words = re.split(r"[\s_\-]+", (name or "Unknown").strip())
    return "".join(w.capitalize() for w in words if w)


def _unique_schema_name(name, element_id, seen):
    """Resolve PascalCase schema name collisions by appending element ID."""
    pascal = _pascal(name)
    if pascal not in seen:
        seen.add(pascal)
        return pascal
    suffixed = f"{pascal}_{element_id}"
    seen.add(suffixed)
    return suffixed


def _is_generic_name(name):
    """Check if a name is too generic for meaningful API paths."""
    return bool(_GENERIC_NAMES.match((name or "").strip()))


# ── Protocol Inference ───────────────────────────────────────────────────

def _infer_protocol(flow):
    """Infer integration protocol from flow attributes when not explicitly set."""
    if flow.protocol:
        return flow.protocol, False  # (protocol, inferred?)

    ft = (flow.flow_type or "").lower()
    df = (flow.data_format or "").lower()
    freq = (flow.frequency or "").lower()

    if df in ("protobuf", "proto"):
        return "gRPC", True
    if df in ("avro",):
        return "Kafka", True
    if df in ("xml", "soap"):
        return "SOAP", True
    if "real-time" in freq or "realtime" in freq:
        return "WebSocket", True
    if ft == "async" or ft == "event":
        return "Kafka", True
    if ft == "batch":
        return "SFTP", True
    return "REST", True  # safe default


# ── Spec Maturity Scoring ────────────────────────────────────────────────

def _compute_spec_maturity(app_elements, integration_flows, slas, requirements):
    """Compute spec maturity score (0.0 - 1.0) from data quality factors."""
    factors = {}

    # 25%: element descriptions
    if app_elements:
        with_desc = sum(1 for e in app_elements if e.description and len(e.description) >= 20)
        factors["descriptions"] = (with_desc / len(app_elements), 0.25)
    else:
        factors["descriptions"] = (0.0, 0.25)

    # 20%: flow protocols
    if integration_flows:
        with_proto = sum(1 for f in integration_flows if f.protocol)
        factors["protocols"] = (with_proto / len(integration_flows), 0.20)
    else:
        factors["protocols"] = (1.0, 0.20)  # no flows = no penalty

    # 20%: schema field richness (proxy: has technology specified)
    if app_elements:
        with_tech = sum(1 for e in app_elements if e.technology)
        factors["field_richness"] = (with_tech / len(app_elements), 0.20)
    else:
        factors["field_richness"] = (0.0, 0.20)

    # 15%: SLA coverage
    if app_elements:
        sla_ratio = min(len(slas) / max(len(app_elements), 1), 1.0)
        factors["sla_coverage"] = (sla_ratio, 0.15)
    else:
        factors["sla_coverage"] = (0.0, 0.15)

    # 10%: requirement linkage
    if app_elements:
        req_ratio = min(len(requirements) / max(len(app_elements), 1), 1.0)
        factors["requirements"] = (req_ratio, 0.10)
    else:
        factors["requirements"] = (0.0, 0.10)

    # 10%: naming quality
    if app_elements:
        good_names = sum(1 for e in app_elements if not _is_generic_name(e.name))
        factors["naming"] = (good_names / len(app_elements), 0.10)
    else:
        factors["naming"] = (0.0, 0.10)

    score = sum(ratio * weight for ratio, weight in factors.values())
    return round(score, 3), {k: round(v[0], 2) for k, v in factors.items()}


# ── Validation ───────────────────────────────────────────────────────────

def _validate_for_generation(solution, app_elements, integration_flows):
    """Return (errors, warnings) for generation readiness."""
    errors = []
    warnings = []

    # Blocking errors
    if not app_elements:
        errors.append({"code": "VAL-001", "message": "No application elements defined. Add elements in Phase C."})
    if solution.governance_status == "rejected":
        errors.append({"code": "VAL-002", "message": "Solution rejected by ARB. Resolve feedback first."})

    # Warnings
    if 0 < len(app_elements) < 3:
        warnings.append({"code": "WARN-001", "message": f"Only {len(app_elements)} application elements — generated contracts will be minimal."})
    if not integration_flows:
        warnings.append({"code": "WARN-002", "message": "No integration flows — AsyncAPI spec will be empty."})

    no_desc = [e for e in app_elements if not e.description or len(e.description) < 20]
    if no_desc:
        warnings.append({"code": "WARN-003", "message": f"{len(no_desc)} elements lack descriptions — schemas will have empty documentation."})

    no_proto = [f for f in integration_flows if not f.protocol]
    if no_proto:
        names = ", ".join(f.flow_name for f in no_proto[:3])
        warnings.append({"code": "WARN-004", "message": f"Flows without protocol (defaulting to REST): {names}"})

    # Check for duplicate names
    name_counts = {}
    for e in app_elements:
        p = _pascal(e.name)
        name_counts[p] = name_counts.get(p, 0) + 1
    dupes = {k: v for k, v in name_counts.items() if v > 1}
    if dupes:
        for name, count in dupes.items():
            warnings.append({"code": "WARN-007", "message": f"'{name}' appears {count} times — schema name collision, suffix _{{id}} will be applied."})

    # Check for generic names
    generic = [e for e in app_elements if _is_generic_name(e.name)]
    if generic:
        names = ", ".join(e.name for e in generic[:3])
        warnings.append({"code": "WARN-008", "message": f"Generic element names produce unclear API paths: {names}"})

    return errors, warnings


# ═════════════════════════════════════════════════════════════════════════
# MAIN GENERATOR
# ═════════════════════════════════════════════════════════════════════════

class SolutionSpecGenerator:
    """Generate API specs from a solution blueprint.

    Usage:
        gen = SolutionSpecGenerator(solution_id=42)
        bundle = gen.generate()
    """

    def __init__(self, solution_id):
        self.solution_id = solution_id
        self._solution = None
        self._app_elements = []
        self._business_elements = []
        self._tech_elements = []
        self._integration_flows = []
        self._requirements = []
        self._quality_attrs = []
        self._slas = []
        self._compositions = []
        self._archimate_elements = []
        self._schema_names_seen = set()

    def _load(self):
        """Load all solution data from the database."""
        from app import db
        from app.models.solution_models import Solution
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.solution_sad_models import (
            SolutionAppElement, SolutionBusinessElement, SolutionTechElement,
            SolutionIntegrationFlow, SolutionQualityAttribute, SolutionSLA,
            SolutionComposition,
        )

        self._solution = Solution.query.get(self.solution_id)
        if not self._solution:
            raise ValueError(f"Solution {self.solution_id} not found")

        sid = self.solution_id
        self._app_elements = SolutionAppElement.query.filter_by(solution_id=sid).order_by(SolutionAppElement.name).all()
        self._business_elements = SolutionBusinessElement.query.filter_by(solution_id=sid).order_by(SolutionBusinessElement.name).all()
        self._tech_elements = SolutionTechElement.query.filter_by(solution_id=sid).order_by(SolutionTechElement.name).all()
        try:
            self._integration_flows = SolutionIntegrationFlow.query.filter_by(solution_id=sid).order_by(SolutionIntegrationFlow.flow_name).all()
        except Exception as e:
            logger.warning("Failed to load integration flows (missing columns?): %s", e)
            db.session.rollback()
            self._integration_flows = []
        try:
            self._quality_attrs = SolutionQualityAttribute.query.filter_by(solution_id=sid).all()
        except Exception:
            self._quality_attrs = []
        try:
            self._slas = SolutionSLA.query.filter_by(solution_id=sid).all()
        except Exception:
            self._slas = []
        try:
            self._compositions = SolutionComposition.query.filter_by(solution_id=sid).order_by(SolutionComposition.component_name).all()
        except Exception:
            self._compositions = []
        self._archimate_elements = SolutionArchiMateElement.query.filter_by(solution_id=sid).order_by(
            SolutionArchiMateElement.layer_type,
            SolutionArchiMateElement.element_name,
        ).all()

        # ── Enrichment: pull real data from ArchiMate catalog + application portfolio ──
        self._enrich_from_catalog(sid)
        self._derive_integration_flows(sid)
        self._synthesize_slas()

        try:
            from app.models.solution_architect_models import SolutionRequirement
            self._requirements = SolutionRequirement.query.filter_by(
                solution_id=sid
            ).filter(
                SolutionRequirement.deleted_at.is_(None)
            ).order_by(SolutionRequirement.priority).all()
        except Exception:
            self._requirements = []

    # ── Data Enrichment (pull from existing catalog) ───────────────────

    def _enrich_from_catalog(self, sid):
        """Enrich app/business elements from ArchiMate catalog + ApplicationComponent portfolio.

        Data model context (CG-009):
        - SolutionArchiMateElement (junction) links solutions to ArchiMate catalog elements.
          This is the CANONICAL source — populated by the solution design workflow.
        - SolutionAppElement (SAD table) is a legacy parallel table that is typically EMPTY.
          The spec generator falls back to SolutionArchiMateElement when SolutionAppElement is empty.
        - The codegen workbench (uml_enrichment_service.py) reads SolutionArchiMateElement directly.
        - Both pipelines should converge on SolutionArchiMateElement as the single source of truth.

        Layer detection: junction records may have layer_type=NULL or inconsistent casing.
        We first try layer_type match, then check the linked ArchiMateElement.type for
        application-layer types, then fall back to all elements to avoid VAL-001 on data
        that exists but has incomplete layer metadata.
        """
        if not self._app_elements:
            try:
                from app.models.archimate_core import ArchiMateElement
                from app.models.application_portfolio import ApplicationComponent

                # ArchiMate 3.2 application-layer element types
                _APP_LAYER_TYPES = {
                    "applicationcomponent", "applicationinterface", "applicationservice",
                    "applicationfunction", "applicationprocess", "applicationevent",
                    "applicationinteraction", "applicationcollaboration", "dataobject",
                }

                # Strategy 1: filter by layer_type (case-insensitive)
                app_junctions = [
                    e for e in self._archimate_elements
                    if (e.layer_type or "").lower() == "application"
                ]

                # Strategy 2: check linked ArchiMateElement.type for app-layer types
                if not app_junctions:
                    for ae in self._archimate_elements:
                        if ae.element_id:
                            real = ArchiMateElement.query.get(ae.element_id)
                            if real and (real.type or "").lower().replace(" ", "").replace("_", "") in _APP_LAYER_TYPES:
                                app_junctions.append(ae)

                # Strategy 3: use ALL elements rather than returning VAL-001
                if not app_junctions and self._archimate_elements:
                    logger.warning(
                        "No application-layer elements found for solution %s — "
                        "using all %d ArchiMate elements as fallback",
                        sid, len(self._archimate_elements),
                    )
                    app_junctions = list(self._archimate_elements)

                for ae in app_junctions:
                    desc = ae.notes
                    tech = None
                    criticality = None
                    resolved_name = ae.element_name  # may be NULL on older junction rows

                    # Load the real ArchiMate element for name + description
                    if ae.element_id:
                        real = ArchiMateElement.query.get(ae.element_id)
                        if real:
                            if not resolved_name:
                                resolved_name = real.name
                            if real.description:
                                desc = real.description

                    # Try to match to an ApplicationComponent for richer data
                    if resolved_name:
                        app = ApplicationComponent.query.filter(
                            ApplicationComponent.name.ilike(f"%{resolved_name[:50]}%")
                        ).first()
                        if app:
                            desc = desc or app.description
                            tech = app.technology_stack
                            criticality = app.business_criticality

                    # Read confirmed spec_data from junction if available
                    # "confirmed" = human-approved; "ai_inferred" = LLM-inferred via UML enrichment (auto-accepted)
                    code_spec = None
                    if ae.spec_data and ae.spec_data.get("fields") and ae.spec_data.get("fields_status") in ("confirmed", "ai_inferred"):
                        code_spec = {
                            "fields": ae.spec_data["fields"],
                            "confirmed": True,
                            "version": ae.spec_data.get("fields_version", 0),
                            "hash": ae.spec_data.get("fields_hash"),
                        }

                    # Backward compatibility: fall back to SolutionAppElement.code_spec
                    if not code_spec:
                        try:
                            from app.models.solution_sad_models import SolutionAppElement
                            app_elem = SolutionAppElement.query.filter_by(
                                solution_id=sid, name=ae.element_name
                            ).first()
                            if app_elem and app_elem.code_spec:
                                code_spec = app_elem.code_spec
                        except Exception as e:
                            logger.warning("Failed to load code_spec fallback: %s", e)

                    self._app_elements.append(type("EnrichedAppElement", (), {
                        "id": ae.element_id or ae.id,
                        "name": resolved_name or "Unknown",
                        "element_type": "component",
                        "description": desc,
                        "technology": tech,
                        "code_spec": code_spec,
                        "_criticality": criticality,
                        "_spec_data": ae.spec_data,
                    })())

                if self._app_elements:
                    logger.info("Enriched %d app elements from catalog for solution %s", len(self._app_elements), sid)
            except Exception as e:
                logger.warning("Catalog enrichment failed: %s", e)

        if not self._business_elements:
            biz_junctions = [e for e in self._archimate_elements if e.layer_type == "business"]
            for ae in biz_junctions:
                self._business_elements.append(type("EnrichedBizElement", (), {
                    "id": ae.element_id or ae.id,
                    "name": ae.element_name or "Unknown",
                    "element_type": "process",
                    "description": ae.notes,
                    "owner": None,
                })())

    def _derive_integration_flows(self, sid):
        """Derive integration flows from ArchiMate relationships between solution elements."""
        if self._integration_flows:
            return  # Already have explicit flows

        try:
            from app.models.archimate_core import ArchiMateRelationship

            element_ids = {ae.element_id for ae in self._archimate_elements if ae.element_id}
            if len(element_ids) < 2:
                return

            # Find relationships where BOTH ends are in this solution
            rels = ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_id.in_(element_ids),
                ArchiMateRelationship.target_id.in_(element_ids),
            ).all()

            if not rels:
                return

            # Build element name lookup
            elem_map = {}
            for ae in self._archimate_elements:
                if ae.element_id:
                    elem_map[ae.element_id] = ae.element_name

            # Flow-like relationship types → integration flows
            flow_types = {"flow", "serving", "triggering", "access", "realization"}
            flow_count = 0

            for rel in rels:
                rel_type = (rel.type or "").lower().replace("relationship", "").strip()
                if rel_type not in flow_types:
                    continue

                src_name = elem_map.get(rel.source_id, f"Element-{rel.source_id}")
                tgt_name = elem_map.get(rel.target_id, f"Element-{rel.target_id}")
                flow_name = f"{src_name} → {tgt_name}"

                self._integration_flows.append(type("DerivedFlow", (), {
                    "id": rel.id,
                    "flow_name": flow_name,
                    "flow_type": "sync" if rel_type in ("serving", "access") else "async",
                    "flow_direction": "outbound",
                    "protocol": None,
                    "data_format": None,
                    "frequency": None,
                    "contains_pii": False,
                    "encryption_required": True,
                    "notes": f"Derived from ArchiMate {rel_type} relationship",
                })())
                flow_count += 1

            if flow_count:
                logger.info("Derived %d integration flows from ArchiMate relationships for solution %s", flow_count, sid)
        except Exception as e:
            logger.warning("Flow derivation failed: %s", e)

    def _synthesize_slas(self):
        """Synthesize SLAs from application criticality when no explicit SLAs exist."""
        if self._slas:
            return  # Already have explicit SLAs

        # Map criticality → availability targets
        criticality_sla = {
            "mission_critical": (99.99, 100, "24x7"),
            "critical": (99.9, 200, "24x7"),
            "business_critical": (99.9, 200, "24x7"),
            "high": (99.5, 500, "extended"),
            "important": (99.5, 500, "business_hours"),
            "medium": (99.0, 1000, "business_hours"),
            "supporting": (99.0, 2000, "business_hours"),
            "low": (95.0, 5000, "best_effort"),
        }

        seen_criticalities = set()
        for elem in self._app_elements:
            crit = getattr(elem, "_criticality", None)
            if not crit or crit.lower() in seen_criticalities:
                continue
            seen_criticalities.add(crit.lower())

            sla_vals = criticality_sla.get(crit.lower())
            if not sla_vals:
                continue

            availability, response_ms, support = sla_vals
            self._slas.append(type("DerivedSLA", (), {
                "sla_name": f"{crit.title()} Service SLA",
                "availability_target": availability,
                "response_time_ms": response_ms,
                "throughput_tps": None,
                "rto_hours": 4 if availability >= 99.9 else 24,
                "rpo_hours": 1 if availability >= 99.9 else 8,
                "support_hours": support,
            })())

        if self._slas:
            logger.info("Synthesized %d SLAs from application criticality", len(self._slas))

    def generate(self):
        """Generate the full spec bundle with validation and maturity scoring."""
        self._load()
        self._schema_names_seen = set()

        # Validate (app_elements now includes ArchiMate fallback if SAD table is empty)
        errors, warnings = _validate_for_generation(
            self._solution, self._app_elements, self._integration_flows
        )
        if errors:
            return {
                "success": False,
                "errors": errors,
                "warnings": warnings,
            }

        # Compute maturity
        maturity_score, maturity_factors = _compute_spec_maturity(
            self._app_elements, self._integration_flows, self._slas, self._requirements
        )

        # Build architecture snapshot for diffing
        snapshot = self._build_snapshot()

        # Generate specs
        openapi = self._build_openapi()
        schemas = self._build_json_schemas()
        asyncapi = self._build_asyncapi()
        contract_tests = self._build_contract_tests(openapi)
        summary = self._build_summary(maturity_score, maturity_factors)

        return {
            "success": True,
            "openapi": openapi,
            "schemas": schemas,
            "asyncapi": asyncapi,
            "contract_tests": contract_tests,
            "summary": summary,
            "warnings": warnings,
            "maturity_score": maturity_score,
            "maturity_factors": maturity_factors,
            "architecture_snapshot": snapshot,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "solution_id": self.solution_id,
            "solution_name": self._solution.name,
            "version": "1.0.0",
        }

    # ── Architecture Snapshot (for diff on regeneration) ─────────────────

    def _build_snapshot(self):
        """Capture current architecture state for future diff computation."""
        def _hash(text):
            return hashlib.md5((text or "").encode(), usedforsecurity=False).hexdigest()[:8]

        return {
            "app_elements": [
                {"id": e.id, "name": e.name, "type": e.element_type, "desc_hash": _hash(e.description)}
                for e in self._app_elements
            ],
            "business_elements": [
                {"id": e.id, "name": e.name, "type": e.element_type}
                for e in self._business_elements
            ],
            "integration_flows": [
                {"id": f.id, "name": f.flow_name, "protocol": f.protocol, "direction": f.flow_direction}
                for f in self._integration_flows
            ],
            "requirements_count": len(self._requirements),
            "sla_count": len(self._slas),
            "composition_count": len(self._compositions),
            "archimate_count": len(self._archimate_elements),
        }

    # ── OpenAPI 3.1 ──────────────────────────────────────────────────────

    def _build_openapi(self):
        """Build OpenAPI 3.1 spec with governance policy and deterministic ordering."""
        sol = self._solution
        spec = OrderedDict([
            ("openapi", "3.1.0"),
            ("info", OrderedDict([
                ("title", f"{sol.name} API"),
                ("description", (
                    f"Auto-generated API contract from Entelim solution blueprint.\n\n"
                    f"**Solution:** {sol.name}\n"
                    f"**ADM Phase:** {sol.adm_phase or 'A'}\n"
                    f"**Governance Status:** {sol.governance_status or 'draft'}\n"
                    f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                )),
                ("version", "1.0.0"),
                ("contact", {"name": sol.solution_owner or "Architecture Team"}),
            ])),
            ("servers", [
                {"url": "https://api.example.com/v1", "description": "Production"},
                {"url": "https://api-staging.example.com/v1", "description": "Staging"},
            ]),
            ("paths", OrderedDict()),
            ("components", OrderedDict([
                ("schemas", OrderedDict()),
                ("securitySchemes", {
                    "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
                }),
            ])),
            ("security", [{"bearerAuth": []}]),
        ])

        # Add standard ErrorResponse schema (API governance policy)
        spec["components"]["schemas"]["ErrorResponse"] = OrderedDict([
            ("type", "object"),
            ("description", "Standard error response envelope"),
            ("properties", OrderedDict([
                ("error", {"type": "string", "description": "Error message"}),
                ("code", {"type": "string", "description": "Machine-readable error code"}),
                ("details", {"type": "array", "items": {"type": "object"}, "description": "Validation error details"}),
            ])),
            ("required", ["error"]),
        ])

        # Generate paths + schemas for each application element (alphabetical order)
        for app_elem in self._app_elements:
            schema_name = _unique_schema_name(app_elem.name, app_elem.id, self._schema_names_seen)
            self._add_app_element_paths(spec, app_elem, schema_name)
            self._add_app_element_schema(spec, app_elem, schema_name)

        # Generate schemas for business elements (alphabetical)
        for biz_elem in self._business_elements:
            schema_name = _unique_schema_name(biz_elem.name, biz_elem.id, self._schema_names_seen)
            props = OrderedDict([
                ("id", {"type": "string", "format": "uuid"}),
                ("name", {"type": "string", "description": biz_elem.name}),
                ("type", {"type": "string", "enum": [biz_elem.element_type]}),
            ])
            if biz_elem.owner:
                props["owner"] = {"type": "string", "description": f"Owned by: {biz_elem.owner}"}

            spec["components"]["schemas"][schema_name] = OrderedDict([
                ("type", "object"),
                ("description", biz_elem.description or f"Business element: {biz_elem.name}"),
                ("x-archimate-layer", "business"),
                ("x-archimate-type", biz_elem.element_type),
                ("properties", props),
            ])

        # Add requirements + SLA as x-extensions
        if self._requirements:
            spec["info"]["x-requirements-count"] = len(self._requirements)
            functional_reqs = [
                r for r in self._requirements
                if r.requirement_type and r.requirement_type.value == "functional"
            ]
            if functional_reqs:
                spec["info"]["x-functional-requirements"] = [
                    {"id": f"REQ-{r.id}", "name": r.name, "priority": r.priority}
                    for r in functional_reqs[:20]
                ]

        if self._slas:
            spec["info"]["x-sla"] = [
                {
                    "name": sla.sla_name,
                    "response_time_ms": sla.response_time_ms,
                    "availability": float(sla.availability_target) if sla.availability_target else None,
                    "rto_hours": sla.rto_hours,
                    "rpo_hours": sla.rpo_hours,
                }
                for sla in self._slas
            ]

        # Sort paths alphabetically (deterministic ordering)
        sorted_paths = OrderedDict(sorted(spec["paths"].items()))
        spec["paths"] = sorted_paths

        # Sort schemas alphabetically
        sorted_schemas = OrderedDict(sorted(spec["components"]["schemas"].items()))
        spec["components"]["schemas"] = sorted_schemas

        return spec

    def _add_confirmed_api_paths(self, spec, app_elem, schema_name, api_contract):
        """Add paths from confirmed API contract instead of generic CRUD."""
        error_ref = "#/components/schemas/ErrorResponse"
        tag = app_elem.element_type or "resources"

        for endpoint in api_contract.get("endpoints", []):
            method = endpoint.get("method", "GET").lower()
            path = endpoint.get("path", f"/v1/{_slugify(app_elem.name)}")
            op_id = endpoint.get("operation_id", f"{method}_{_snake(app_elem.name)}")
            summary = endpoint.get("summary", f"{method.upper()} {app_elem.name}")

            operation = OrderedDict([
                ("summary", summary),
                ("operationId", op_id),
                ("tags", [tag]),
                ("x-archimate-source", f"SolutionAppElement:{app_elem.id}"),
                ("x-contract-confirmed", True),
            ])

            # Auth
            auth = endpoint.get("auth")
            if auth and auth != "none":
                operation["security"] = [{"bearerAuth": []}]

            # Request body
            req_schema = endpoint.get("request_schema")
            if req_schema and method in ("post", "put", "patch"):
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{req_schema}Create"}}},
                }

            # Response
            resp_schema = endpoint.get("response_schema", schema_name)
            operation["responses"] = OrderedDict([
                ("200", {
                    "description": "Success",
                    "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{resp_schema}"}}},
                }),
                ("400", {"description": "Bad Request", "content": {"application/json": {"schema": {"$ref": error_ref}}}}),
                ("401", {"description": "Unauthorized", "content": {"application/json": {"schema": {"$ref": error_ref}}}}),
            ])

            # Error codes from contract
            for code in endpoint.get("error_codes", []):
                if str(code) not in operation["responses"]:
                    operation["responses"][str(code)] = {
                        "description": f"Error {code}",
                        "content": {"application/json": {"schema": {"$ref": error_ref}}},
                    }

            if path not in spec["paths"]:
                spec["paths"][path] = OrderedDict()
            spec["paths"][path][method] = operation

    def _add_app_element_paths(self, spec, app_elem, schema_name):
        """Add versioned CRUD paths for an application element."""
        # If confirmed api_contract exists in spec_data, use it instead of generic CRUD
        spec_data = getattr(app_elem, "_spec_data", None)
        if spec_data and spec_data.get("api_contract") and spec_data.get("contract_status") == "confirmed":
            self._add_confirmed_api_paths(spec, app_elem, schema_name, spec_data["api_contract"])
            return

        slug = _slugify(app_elem.name)
        base_path = f"/v1/{slug}"
        item_path = f"/v1/{slug}/{{id}}"
        schema_ref = f"#/components/schemas/{schema_name}"
        error_ref = "#/components/schemas/ErrorResponse"
        tag = app_elem.element_type or "resources"

        # SLA-derived rate limit
        rate_limit = None
        for sla in self._slas:
            if sla.throughput_tps:
                rate_limit = sla.throughput_tps
                break

        common_extensions = {"x-archimate-source": f"SolutionAppElement:{app_elem.id}"}
        if rate_limit:
            common_extensions["x-rate-limit"] = rate_limit

        # List + Create (deterministic operation order: GET, POST)
        spec["paths"][base_path] = OrderedDict([
            ("get", OrderedDict([
                ("summary", f"List {app_elem.name} resources"),
                ("operationId", f"list_{_snake(app_elem.name)}"),
                ("tags", [tag]),
                ("parameters", [
                    {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
                    {"name": "per_page", "in": "query", "schema": {"type": "integer", "default": 25, "maximum": 100}},
                    {"name": "search", "in": "query", "schema": {"type": "string"}},
                ]),
                ("responses", OrderedDict([
                    ("200", {
                        "description": f"List of {app_elem.name}",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "items": {"type": "array", "items": {"$ref": schema_ref}},
                                "total": {"type": "integer"},
                                "page": {"type": "integer"},
                            },
                        }}},
                    }),
                    ("401", {"description": "Unauthorized", "content": {"application/json": {"schema": {"$ref": error_ref}}}}),
                ])),
                *common_extensions.items(),
            ])),
            ("post", OrderedDict([
                ("summary", f"Create a new {app_elem.name}"),
                ("operationId", f"create_{_snake(app_elem.name)}"),
                ("tags", [tag]),
                ("requestBody", {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}Create"}}},
                }),
                ("responses", OrderedDict([
                    ("201", {"description": f"Created {app_elem.name}", "content": {"application/json": {"schema": {"$ref": schema_ref}}}}),
                    ("422", {"description": "Validation error", "content": {"application/json": {"schema": {"$ref": error_ref}}}}),
                ])),
                *common_extensions.items(),
            ])),
        ])

        # Get + Update + Delete (deterministic order: GET, PATCH, DELETE)
        spec["paths"][item_path] = OrderedDict([
            ("get", OrderedDict([
                ("summary", f"Get {app_elem.name} by ID"),
                ("operationId", f"get_{_snake(app_elem.name)}"),
                ("tags", [tag]),
                ("parameters", [{"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}]),
                ("responses", OrderedDict([
                    ("200", {"description": f"{app_elem.name} detail", "content": {"application/json": {"schema": {"$ref": schema_ref}}}}),
                    ("404", {"description": "Not found", "content": {"application/json": {"schema": {"$ref": error_ref}}}}),
                ])),
            ])),
            ("patch", OrderedDict([
                ("summary", f"Update {app_elem.name}"),
                ("operationId", f"update_{_snake(app_elem.name)}"),
                ("tags", [tag]),
                ("parameters", [{"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}]),
                ("requestBody", {"content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}Update"}}}}),
                ("responses", OrderedDict([
                    ("200", {"description": f"Updated {app_elem.name}", "content": {"application/json": {"schema": {"$ref": schema_ref}}}}),
                    ("422", {"description": "Validation error", "content": {"application/json": {"schema": {"$ref": error_ref}}}}),
                ])),
            ])),
            ("delete", OrderedDict([
                ("summary", f"Delete {app_elem.name}"),
                ("operationId", f"delete_{_snake(app_elem.name)}"),
                ("tags", [tag]),
                ("parameters", [{"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}]),
                ("responses", OrderedDict([("204", {"description": "Deleted"})])),
            ])),
        ])

    def _add_app_element_schema(self, spec, app_elem, schema_name):
        """Add schema + Create/Update variants. Uses code_spec if confirmed, else generic CRUD."""
        code_spec = app_elem.code_spec if hasattr(app_elem, "code_spec") else None

        if code_spec and code_spec.get("fields"):
            # Use architect-confirmed fields from code_spec
            base_props = OrderedDict()
            required_fields = []
            for field in sorted(code_spec["fields"], key=lambda f: f["name"]):
                prop = {"type": field.get("type", "string")}
                if field.get("format"):
                    prop["format"] = field["format"]
                if field.get("description"):
                    prop["description"] = field["description"]
                if field.get("enum"):
                    prop["enum"] = sorted(field["enum"])
                if field.get("readonly"):
                    prop["readOnly"] = True
                if field.get("maxLength"):
                    prop["maxLength"] = field["maxLength"]
                if field.get("minLength"):
                    prop["minLength"] = field["minLength"]
                if field.get("minimum") is not None:
                    prop["minimum"] = field["minimum"]
                if field.get("maximum") is not None:
                    prop["maximum"] = field["maximum"]
                if field.get("pattern"):
                    prop["pattern"] = field["pattern"]
                if field.get("references"):
                    prop["x-references"] = field["references"]
                base_props[field["name"]] = prop
                if field.get("required") and not field.get("readonly"):
                    required_fields.append(field["name"])
        else:
            # Fallback: generic CRUD schema
            base_props = OrderedDict([
                ("id", {"type": "string", "format": "uuid", "readOnly": True}),
                ("created_at", {"type": "string", "format": "date-time", "readOnly": True}),
                ("description", {"type": "string"}),
                ("name", {"type": "string", "maxLength": 255}),
                ("status", {"type": "string", "enum": ["active", "inactive", "deprecated"]}),
                ("updated_at", {"type": "string", "format": "date-time", "readOnly": True}),
            ])
            if app_elem.technology:
                base_props["technology_stack"] = {"type": "string", "description": f"Technology: {app_elem.technology}"}
            required_fields = ["name"]

        schema_entry = OrderedDict([
            ("type", "object"),
            ("description", app_elem.description or f"Application element: {app_elem.name}"),
            ("x-archimate-layer", "application"),
            ("x-archimate-type", app_elem.element_type),
            ("x-archimate-id", app_elem.id),
            ("properties", base_props),
        ])
        if code_spec and code_spec.get("fields"):
            schema_entry["x-code-spec-confirmed"] = True
        if required_fields:
            schema_entry["required"] = sorted(required_fields)
        spec["components"]["schemas"][schema_name] = schema_entry

        # Create variant (no readonly fields)
        create_props = OrderedDict(
            (k, v) for k, v in base_props.items()
            if not v.get("readOnly")
        )
        create_entry = OrderedDict([("type", "object"), ("properties", create_props)])
        if required_fields:
            create_entry["required"] = sorted(required_fields)
        spec["components"]["schemas"][f"{schema_name}Create"] = create_entry

        # Update variant (all optional)
        spec["components"]["schemas"][f"{schema_name}Update"] = OrderedDict([
            ("type", "object"),
            ("properties", OrderedDict((k, v) for k, v in create_props.items())),
        ])

    # ── JSON Schema ──────────────────────────────────────────────────────

    def _build_json_schemas(self):
        """Build standalone JSON Schemas with deterministic ordering."""
        schemas = OrderedDict()

        for elem in self._app_elements:
            name = _snake(elem.name)
            schemas[name] = OrderedDict([
                ("$schema", "https://json-schema.org/draft/2020-12/schema"),
                ("$id", f"https://archie.example-corp.com/schemas/{name}.json"),
                ("title", elem.name),
                ("description", elem.description or f"Schema for {elem.name}"),
                ("type", "object"),
                ("properties", OrderedDict([
                    ("description", {"type": "string"}),
                    ("id", {"type": "string", "format": "uuid"}),
                    ("name", {"type": "string", "minLength": 1, "maxLength": 255}),
                    ("status", {"type": "string", "enum": ["active", "inactive", "deprecated"]}),
                ])),
                ("required", sorted(["name"])),
                ("additionalProperties", False),
                ("x-archimate-source", {
                    "layer": "application",
                    "type": elem.element_type,
                    "solution_id": self.solution_id,
                    "element_id": elem.id,
                }),
            ])

        for comp in self._compositions:
            name = _snake(comp.component_name)
            if name not in schemas:
                schemas[name] = OrderedDict([
                    ("$schema", "https://json-schema.org/draft/2020-12/schema"),
                    ("title", comp.component_name),
                    ("description", f"Component: {comp.component_name} (role: {comp.role}, criticality: {comp.criticality})"),
                    ("type", "object"),
                    ("properties", OrderedDict([
                        ("component_type", {"type": "string", "const": comp.component_type}),
                        ("id", {"type": "string", "format": "uuid"}),
                        ("name", {"type": "string"}),
                        ("role", {"type": "string", "const": comp.role}),
                    ])),
                    ("required", sorted(["name"])),
                ])

        return schemas

    # ── AsyncAPI ─────────────────────────────────────────────────────────

    def _build_asyncapi(self):
        """Build AsyncAPI spec with protocol inference and deterministic ordering."""
        if not self._integration_flows:
            return None

        sol = self._solution
        channels = OrderedDict()

        for flow in sorted(self._integration_flows, key=lambda f: f.flow_name):
            channel_name = _slugify(flow.flow_name)
            msg_name = _pascal(flow.flow_name) + "Message"
            protocol, inferred = _infer_protocol(flow)

            message_payload = OrderedDict([
                ("type", "object"),
                ("properties", OrderedDict([
                    ("event_id", {"type": "string", "format": "uuid"}),
                    ("payload", {"type": "object"}),
                    ("source", {"type": "string"}),
                    ("timestamp", {"type": "string", "format": "date-time"}),
                ])),
            ])

            channel = OrderedDict([
                ("description", flow.notes or f"Integration flow: {flow.flow_name}"),
                ("x-archimate-source", f"SolutionIntegrationFlow:{flow.id}"),
                ("x-protocol", protocol),
            ])
            if inferred:
                channel["x-protocol-inferred"] = True
            if flow.contains_pii:
                channel["x-contains-pii"] = True
            if flow.encryption_required:
                channel["x-encryption-required"] = True

            message = OrderedDict([
                ("name", msg_name),
                ("contentType", flow.data_format or "application/json"),
                ("payload", message_payload),
            ])

            if flow.flow_direction == "outbound":
                channel["publish"] = OrderedDict([
                    ("operationId", f"publish_{_snake(flow.flow_name)}"),
                    ("summary", f"Publish {flow.flow_name} events"),
                    ("message", message),
                ])
            else:
                channel["subscribe"] = OrderedDict([
                    ("operationId", f"consume_{_snake(flow.flow_name)}"),
                    ("summary", f"Consume {flow.flow_name} events"),
                    ("message", message),
                ])

            channels[channel_name] = channel

        return OrderedDict([
            ("asyncapi", "2.6.0"),
            ("info", OrderedDict([
                ("title", f"{sol.name} Events"),
                ("version", "1.0.0"),
                ("description", f"Event-driven contracts for {sol.name}"),
            ])),
            ("channels", channels),
        ])

    # ── Contract Tests ───────────────────────────────────────────────────

    def _build_contract_tests(self, openapi_spec):
        """Generate contract test definitions from the OpenAPI spec."""
        tests = []

        for path, methods in openapi_spec.get("paths", {}).items():
            for method, operation in methods.items():
                if not isinstance(operation, dict) or "operationId" not in operation:
                    continue
                op_id = operation["operationId"]

                # Schema validation test
                for status_code, response in operation.get("responses", {}).items():
                    content = response.get("content", {})
                    schema_ref = None
                    for media_type, media in content.items():
                        if "schema" in media:
                            schema_ref = media["schema"].get("$ref") or media["schema"]
                            break

                    tests.append(OrderedDict([
                        ("test_name", f"test_{op_id}_returns_{status_code}"),
                        ("method", method.upper()),
                        ("path", path),
                        ("expected_status", int(status_code) if status_code.isdigit() else 200),
                        ("validates_schema", bool(schema_ref)),
                        ("schema_ref", schema_ref if isinstance(schema_ref, str) else None),
                    ]))

        return tests

    # ── Summary ──────────────────────────────────────────────────────────

    def _build_summary(self, maturity_score, maturity_factors):
        """Build summary with maturity scoring and architecture statistics."""
        layer_counts = {}
        for elem in self._archimate_elements:
            layer = elem.layer_type or "unknown"
            layer_counts[layer] = layer_counts.get(layer, 0) + 1

        return OrderedDict([
            ("solution", OrderedDict([
                ("id", self.solution_id),
                ("name", self._solution.name),
                ("adm_phase", self._solution.adm_phase),
                ("governance_status", self._solution.governance_status),
            ])),
            ("spec_maturity", OrderedDict([
                ("score", maturity_score),
                ("factors", maturity_factors),
                ("rating", "high" if maturity_score >= 0.8 else "medium" if maturity_score >= 0.5 else "low"),
            ])),
            ("specs_generated", OrderedDict([
                ("openapi_paths", len(self._app_elements) * 2),
                ("openapi_schemas", len(self._app_elements) * 3 + len(self._business_elements) + 1),  # +1 for ErrorResponse
                ("json_schemas", len(self._app_elements) + len(self._compositions)),
                ("asyncapi_channels", len(self._integration_flows)),
                ("contract_tests", len(self._app_elements) * 8),  # ~8 tests per element (CRUD * status codes)
            ])),
            ("source_data", OrderedDict([
                ("app_elements", len(self._app_elements)),
                ("business_elements", len(self._business_elements)),
                ("tech_elements", len(self._tech_elements)),
                ("integration_flows", len(self._integration_flows)),
                ("requirements", len(self._requirements)),
                ("quality_attributes", len(self._quality_attrs)),
                ("slas", len(self._slas)),
                ("compositions", len(self._compositions)),
                ("archimate_elements", len(self._archimate_elements)),
                ("archimate_by_layer", layer_counts),
            ])),
        ])

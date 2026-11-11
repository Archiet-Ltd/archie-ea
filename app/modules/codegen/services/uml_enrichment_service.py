"""UML Enrichment Service — transforms ArchiMate elements into UML diagrams."""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.modules.architecture_assistant.acm_properties_utils import (
    build_requirement_traceability_from_acm,
)

logger = logging.getLogger(__name__)

# ArchiMate type suffixes to strip from element names to get clean domain names.
_ARCHIMATE_SUFFIXES = re.compile(
    r"\s*(?:Data\s*Object|Business\s*Object|Application\s*Component|"
    r"Application\s*Service|Application\s*Function|Application\s*Interface|"
    r"Business\s*Process|Business\s*Service|Business\s*Function|"
    r"Technology\s*Object|Technology\s*Service|Node|Artifact|"
    r"DataObject|BusinessObject|ApplicationComponent|"
    r"ApplicationService|ApplicationFunction|ApplicationInterface|"
    r"BusinessProcess|BusinessService|BusinessFunction|"
    r"TechnologyObject|TechnologyService)\s*$",
    re.IGNORECASE,
)


def _strip_archimate_suffix(name: str) -> str:
    """Strip ArchiMate type suffixes from element names to get clean domain names."""
    stripped = _ARCHIMATE_SUFFIXES.sub("", name).strip()
    return stripped if stripped else name


def _to_table_name(name: str) -> str:
    """Normalise an entity name to a lookup key (lowercase, non-alphanumeric → underscore)."""
    return re.sub(r"[^a-z0-9]", "_", name.lower()).strip("_")


def _inject_ux_enrichment(solution_id: int, spec_data: dict) -> None:
    """Inject real enum values, display fields, and UX preferences into spec_data in-place.

    Called before the LLM genome prompt so generated code uses real DB values.

    spec_data is the ArchiMate context dict with keys like "data_objects",
    "app_components", etc. — each a list of element dicts. Elements that have
    "confirmed_fields" receive enum_values / display_field injected from the
    live DB via build_ux_enrichment().

    Also attaches enrichment["design_system"] as spec_data["ux_preferences"]
    so the LLM prompt can reference the project's design system defaults.
    """
    try:
        from app.modules.codegen.services.ux_enrichment_service import build_ux_enrichment
        enrichment = build_ux_enrichment(solution_id)

        field_controls = enrichment.get("field_controls", {})

        # field_controls is keyed by entity_name; normalise for fuzzy matching
        controls_by_key: Dict[str, dict] = {
            _to_table_name(entity_name): controls
            for entity_name, controls in field_controls.items()
        }

        # Iterate over all element groups in the context dict
        _ELEMENT_GROUPS = (
            "data_objects", "app_components", "app_services", "app_interfaces",
            "business_processes", "tech_nodes",
        )
        for group_key in _ELEMENT_GROUPS:
            for element in spec_data.get(group_key, []):
                element_name = element.get("name", "")
                element_key = _to_table_name(element_name)

                controls = controls_by_key.get(element_key, {})
                if not controls:
                    continue

                # Enrich confirmed_fields if present
                for field in element.get("confirmed_fields", []):
                    fname = field.get("name", "")
                    ctrl = controls.get(fname)
                    if not ctrl:
                        continue
                    ctrl_type = ctrl.get("type") or ctrl.get("control_type")
                    if ctrl_type == "enum":
                        detected = ctrl.get("detected_values") or ctrl.get("enum_values", [])
                        if detected:
                            field["enum_values"] = detected
                    elif ctrl_type == "relation_picker":
                        display_field = ctrl.get("display_field")
                        if display_field:
                            field["display_field"] = display_field

        # Attach design system preferences so the LLM prompt can reference them
        spec_data["ux_preferences"] = enrichment.get("design_system", {})
    except Exception as exc:
        logger.warning(
            "_inject_ux_enrichment: failed for solution %s: %s",
            solution_id, exc,
        )

_MOTIVATION_TRACE_TYPES = frozenset({
    "Requirement", "Constraint", "Principle", "Goal", "Driver",
})

UML_ENRICHMENT_PROMPT = """You are an enterprise architect converting ArchiMate 3.2 elements into UML diagrams for code generation.

## Solution Architecture Context
{solution_context}

## ArchiMate Elements

### Data Objects (become Class Diagram entities)
Note: Objects with "confirmed_fields" have architect-approved field schemas. Use these EXACT fields — do NOT invent new fields for these objects.
{data_objects_json}

### Application Interfaces (become Sequence Diagram endpoints)
{app_interfaces_json}

### Application Components (become Component Diagram services)
Note: Components with "confirmed_fields" have architect-approved field schemas. Use these EXACT fields for the class diagram — do NOT invent new fields for these components.
{app_components_json}

### Application Services
{app_services_json}

### Business Processes (become method signatures)
{business_processes_json}

### Technology Nodes (become Deployment Diagram)
{tech_nodes_json}

### Requirements (become validation rules)
Each item may include `requirement_traceability` from the architecture wizard: priority, moscow_priority, compliance_reference, verification_method, acceptance_criteria, stakeholder — reflect these in class validations, sequence-step checks, and docstrings where applicable.
{requirements_json}

### Constraints
Each item may include `requirement_traceability` (same shape as Requirements).
{constraints_json}

### Capabilities (Strategy layer — define service boundaries and domain context)
{capabilities_json}

### Business Actors / Roles (become user roles and auth scopes)
{business_actors_json}

### Work Packages (Implementation layer — inform CI/CD pipelines and migration scripts)
{work_packages_json}

## Instructions

Generate 4 UML diagram definitions as JSON. For each ArchiMate element, include its `source_element_id` for traceability.

Return ONLY valid JSON matching this schema:

{{
  "class_diagram": {{
    "classes": [
      {{
        "name": "PascalCase class name",
        "source_element_id": 123,
        "table_name": "snake_case_plural",
        "description": "string",
        "fields": [
          {{"name": "field_name", "type": "int|str|float|Decimal|datetime|bool|UUID", "nullable": false, "primary_key": false, "foreign_key": "OtherClass.id or null", "description": "string"}}
        ],
        "relationships": [
          {{"target_class": "OtherClass", "type": "one_to_many|many_to_one|many_to_many|one_to_one", "back_populates": "field_name"}}
        ],
        "validations": [
          {{"field": "field_name", "rule": "min_length|max_length|regex|range|required|unique", "value": "string", "source_requirement": "requirement name or null"}}
        ]
      }}
    ]
  }},
  "sequence_diagram": {{
    "flows": [
      {{
        "name": "flow name (e.g., Create Inventory Record)",
        "source_element_id": 123,
        "http_method": "GET|POST|PUT|DELETE",
        "path": "/api/resource/path",
        "request_body": {{"field": "type"}} or null,
        "response_body": {{"field": "type"}},
        "steps": [
          {{"from": "ComponentName", "to": "ComponentName", "action": "method_name", "description": "string", "method_body_hint": "pseudocode for implementation"}}
        ]
      }}
    ]
  }},
  "component_diagram": {{
    "components": [
      {{
        "name": "string",
        "source_element_id": 123,
        "type": "service|integration|infrastructure",
        "interfaces_provided": ["InterfaceName"],
        "interfaces_required": ["InterfaceName"],
        "dependencies": ["OtherComponentName"]
      }}
    ]
  }},
  "deployment_diagram": {{
    "nodes": [
      {{
        "name": "string",
        "source_element_id": 123,
        "node_type": "container|vm|serverless|managed_service",
        "components_deployed": ["ComponentName"],
        "properties": {{"key": "value"}}
      }}
    ]
  }}
}}

RULES:
1. Every class must have an `id` primary key field and `created_at`/`updated_at` timestamps.
2. Field types must be Python types: int, str, float, Decimal, datetime, bool, UUID.
3. Foreign keys reference other classes by name: "OtherClass.id".
4. Sequence diagram flows must have concrete HTTP paths, not abstract descriptions.
5. Every element must include its `source_element_id` from the ArchiMate input.
6. Be specific to the business domain — no generic filler.
7. CRITICAL — Business Process elements contain real workflow descriptions. Each BusinessProcess MUST become a detailed sequence flow with steps that map to service method calls. For each step include:
   - "from": the calling component/service name
   - "to": the receiving component/service name
   - "action": a concrete method name (snake_case) that will become a service method
   - "description": the actual business logic to implement (e.g. "validate policy expiry date is not in the past", "calculate premium based on risk factors")
   - "method_body_hint": a 1-2 line pseudocode hint for the method implementation
   Do NOT generate empty or generic steps like "process request" — extract the REAL workflow from the business process description.
8. CRITICAL — For each element that has a `business_rules` list, generate a private `_validate_<rule_name>(self, ...)` method stub in the service class that enforces that rule. Rule names are snake_case. The stub must raise a domain-specific exception (e.g. `ValidationError`, `BusinessRuleViolation`) rather than returning bool. Include the rule text verbatim as a docstring so the developer knows what to implement."""


class UMLEnrichmentService:

    @staticmethod
    def _normalize_element_name(name: str) -> str:
        """Lowercase + strip spaces/underscores/hyphens for fuzzy matching."""
        return re.sub(r"[\s_\-]+", "", name).lower()

    @staticmethod
    def _write_spec_data_from_uml(uml: Dict, links: list, solution_id: int = None) -> int:
        """Write inferred field schemas from UML classes back to spec_data on junctions.

        For each UML class with a source_element_id, find the matching junction and
        populate spec_data.fields if not already confirmed by an architect.

        Priority order for fields_status:
        1. confirmed / vendor_seeded / schema_imported → skip (trusted, do not overwrite)
        2. vendor seed match → write vendor_seeded
        3. fallback → write ai_inferred

        Returns the number of junctions updated.
        """
        from app.extensions import db

        classes = uml.get("class_diagram", {}).get("classes", [])
        if not classes:
            return 0

        junction_by_element = {link.element_id: link for link in links}

        # Load vendor seed templates for this solution's vendor keys (if solution_id provided)
        vendor_seeds: list = []
        if solution_id is not None:
            try:
                from app.models.solution_models import Solution
                from app.models.vendor.vendor_organization import VendorArchiMateTemplate
                from app.modules.solutions_strategic.v2.services.vendor_template_service import VendorTemplateService

                solution = Solution.query.get(solution_id)
                if solution:
                    vendor_keys = VendorTemplateService.get_solution_vendor_keys(solution)
                    if vendor_keys:
                        vendor_seeds = (
                            VendorArchiMateTemplate.query
                            .filter(
                                VendorArchiMateTemplate.vendor_key.in_(vendor_keys),
                                VendorArchiMateTemplate.spec_data_seed.isnot(None),
                            )
                            .all()
                        )
                        logger.debug(
                            "Solution %d: loaded %d vendor seed templates for keys %s",
                            solution_id, len(vendor_seeds), vendor_keys,
                        )
            except Exception:
                logger.debug("Vendor seed lookup failed for solution %s", solution_id, exc_info=True)

        # Pre-normalize vendor seed names for O(1) lookup
        seed_by_norm: Dict[str, Any] = {}
        for tmpl in vendor_seeds:
            norm = UMLEnrichmentService._normalize_element_name(tmpl.element_name)
            seed_by_norm[norm] = tmpl

        def _find_seed(element_name: str):
            """Fuzzy-match element name against vendor seed templates."""
            norm = UMLEnrichmentService._normalize_element_name(element_name or "")
            if not norm:
                return None
            # Exact normalized match
            if norm in seed_by_norm:
                return seed_by_norm[norm]
            # Substring match — either name contains the other
            for seed_norm, tmpl in seed_by_norm.items():
                if norm in seed_norm or seed_norm in norm:
                    return tmpl
            return None

        updated = 0

        for cls in classes:
            source_id = cls.get("source_element_id")
            if not source_id:
                continue

            junction = junction_by_element.get(source_id)
            if not junction:
                continue

            existing = junction.spec_data or {}
            # Don't overwrite trusted fields (human or vendor or schema wins over LLM)
            if existing.get("fields_status") in {"confirmed", "vendor_seeded", "schema_imported"}:
                continue

            fields = cls.get("fields", [])
            if not fields:
                continue

            # Try vendor seed match using element_name from junction or UML class name
            element_name = getattr(junction, "element_name", None) or cls.get("name", "")
            seed_tmpl = _find_seed(element_name)
            if seed_tmpl:
                seed_data = seed_tmpl.get_spec_data_seed()
                if seed_data and seed_data.get("fields"):
                    junction.spec_data = {
                        **existing,
                        "fields": seed_data["fields"],
                        "fields_status": "vendor_seeded",
                        "fields_version": (existing.get("fields_version", 0) or 0) + 1,
                        "inferred_from": f"vendor_seed:{seed_tmpl.vendor_key}:{seed_tmpl.element_name}",
                    }
                    db.session.add(junction)
                    updated += 1
                    continue

            junction.spec_data = {
                **existing,
                "fields": fields,
                "fields_status": "ai_inferred",  # accepted by spec_generator without human confirmation
                "fields_version": (existing.get("fields_version", 0) or 0) + 1,
                "inferred_from": "uml_enrichment",
            }
            db.session.add(junction)
            updated += 1

        if updated:
            try:
                db.session.commit()
                logger.info("Wrote spec_data to %d junctions from UML enrichment", updated)
            except Exception as e:
                db.session.rollback()
                logger.error("Failed to write spec_data from UML: %s", e)
                updated = 0

        return updated

    """Transforms ArchiMate elements into enriched UML diagrams."""

    @staticmethod
    def build_archimate_context(elements: List[Dict]) -> Dict[str, List[Dict]]:
        """Group ArchiMate elements by type for prompt injection."""
        context = {
            "data_objects": [],
            "app_interfaces": [],
            "app_components": [],
            "app_services": [],
            "business_processes": [],
            "tech_nodes": [],
            "requirements": [],
            "constraints": [],
            "capabilities": [],
            "work_packages": [],
            "business_actors": [],
        }
        type_map = {
            # Application layer → code entities
            "DataObject": "data_objects",
            "BusinessObject": "data_objects",
            "ApplicationInterface": "app_interfaces",
            "ApplicationComponent": "app_components",
            "ApplicationService": "app_services",
            "ApplicationFunction": "app_components",
            # Business layer → method signatures, roles, actors
            "BusinessProcess": "business_processes",
            "BusinessService": "business_processes",
            "BusinessFunction": "business_processes",
            "BusinessEvent": "business_processes",
            "BusinessActor": "business_actors",
            "BusinessRole": "business_actors",
            "Contract": "business_processes",
            # Technology layer → deployment config
            "Node": "tech_nodes",
            "SystemSoftware": "tech_nodes",
            "TechnologyService": "tech_nodes",
            "CommunicationNetwork": "tech_nodes",
            "Artifact": "tech_nodes",
            "Device": "tech_nodes",
            # Motivation layer → validation rules, constraints
            "Requirement": "requirements",
            "Constraint": "constraints",
            "Principle": "constraints",
            "Goal": "requirements",
            "Driver": "requirements",
            # Strategy layer → service boundaries, domain context
            "Capability": "capabilities",
            "CourseOfAction": "capabilities",
            "ValueStream": "capabilities",
            # Implementation layer → CI/CD, migration scripts
            "WorkPackage": "work_packages",
            "Deliverable": "work_packages",
            "Plateau": "work_packages",
            "Gap": "work_packages",
        }
        for el in elements:
            bucket = type_map.get(el.get("type"))
            if bucket:
                entry = {
                    "id": el.get("id"),
                    "name": el.get("name"),
                    "type": el.get("type"),
                    "description": el.get("description", ""),
                }
                # Include confirmed field specs when available
                if el.get("confirmed_fields"):
                    entry["confirmed_fields"] = el["confirmed_fields"]
                # Include structured process steps for business-layer elements
                if el.get("process_steps") and bucket == "business_processes":
                    entry["process_steps"] = el["process_steps"]
                # Forward architect-defined business rules so Rule 8 validation stubs are generated
                if el.get("business_rules") and bucket in ("business_processes", "app_components", "app_services"):
                    entry["business_rules"] = el["business_rules"]
                # Forward build_or_buy for application-layer components so the code
                # generator can emit integration stubs for "buy"/"SaaS" decisions.
                if el.get("build_or_buy") and bucket in ("app_components", "app_services"):
                    entry["build_or_buy"] = el["build_or_buy"]
                if el.get("deployment_model") and bucket in ("app_components", "tech_nodes"):
                    entry["deployment_model"] = el["deployment_model"]
                if el.get("requirement_traceability"):
                    entry["requirement_traceability"] = el["requirement_traceability"]
                context[bucket].append(entry)
        return context

    @staticmethod
    def _build_solution_context(solution_id: int) -> str:
        """Assemble architectural context from solution metadata, blueprint narratives, and drivers.

        Returns a formatted string ready for prompt injection. Empty string if solution not found.
        """
        try:
            from app.models.solution_models import Solution
            from app.models.solution_architect_models import SolutionProblemDefinition, SolutionDriver

            solution = Solution.query.get(solution_id)
            if not solution:
                return ""

            lines = []

            # --- Solution metadata ---
            lines.append("### Solution")
            lines.append(f"Name: {solution.name}")
            if solution.business_domain:
                lines.append(f"Domain: {solution.business_domain}")
            if solution.solution_type:
                lines.append(f"Type: {solution.solution_type}")
            if solution.complexity_level:
                lines.append(f"Complexity: {solution.complexity_level}")
            if solution.scope_description:
                lines.append(f"Scope: {solution.scope_description}")
            if solution.business_value:
                lines.append(f"Business value: {solution.business_value}")

            # --- Blueprint section narratives ---
            narratives = solution.section_narratives or {}
            BLUEPRINT_SECTIONS = {
                "security_viewpoint": "Security constraints",
                "nfr_satisfaction": "Non-functional requirements",
                "deployment_view": "Deployment model",
                "application_cooperation": "Application cooperation",
                "data_information": "Data / information",
                "motivation_viewpoint": "Motivation / drivers",
            }
            blueprint_lines = []
            for section_key, label in BLUEPRINT_SECTIONS.items():
                text = narratives.get(section_key, "").strip()
                if text:
                    blueprint_lines.append(f"**{label}:** {text}")
            if blueprint_lines:
                lines.append("\n### Blueprint Architectural Decisions")
                lines.extend(blueprint_lines)
                lines.append(
                    "IMPORTANT: The security constraints and NFRs above are architect-approved. "
                    "Reflect them in generated field types, validations, auth patterns, and deployment config."
                )

            # --- Business drivers ---
            problem = SolutionProblemDefinition.query.filter_by(solution_id=solution_id).first()
            if problem:
                drivers = SolutionDriver.query.filter_by(problem_id=problem.id).all()
                if drivers:
                    lines.append("\n### Business Drivers")
                    for d in drivers:
                        urgency = f" [urgency {d.urgency}/5]" if d.urgency else ""
                        lines.append(f"- {d.name}{urgency}: {d.description or ''}")

            # --- Peer solution APIs (cross-solution dependency graph) ---
            # Find other solutions that share ArchiMate elements with this one.
            # Inject their generated openapi.yaml paths so the LLM generates
            # typed client calls rather than hardcoded HTTP strings.
            try:
                from app import db as _db
                from app.models.solution_archimate_element import SolutionArchiMateElement
                from app.modules.codegen.models import CodegenGeneration as _CG

                my_elem_q = _db.session.query(
                    SolutionArchiMateElement.element_id
                ).filter_by(solution_id=solution_id).subquery()

                peer_ids = _db.session.query(
                    SolutionArchiMateElement.solution_id
                ).filter(
                    SolutionArchiMateElement.element_id.in_(my_elem_q),
                    SolutionArchiMateElement.solution_id != solution_id,
                ).distinct().limit(5).all()

                peer_ids = [r[0] for r in peer_ids]
                if peer_ids:
                    peer_lines = []
                    for pid in peer_ids:
                        psol = Solution.query.get(pid)
                        pgen = _CG.query.filter_by(solution_id=pid).first()
                        if not psol or not pgen or not pgen.generated_files:
                            continue
                        raw = pgen.generated_files.get("openapi.yaml", "")
                        if not raw:
                            continue
                        try:
                            import json as _j
                            popenapi = _j.loads(raw)
                            endpoint_sample = list(popenapi.get("paths", {}).keys())[:6]
                            peer_lines.append(
                                f"- **{psol.name}** (solution {pid}): "
                                f"{len(popenapi.get('paths', {}))} endpoints, "
                                f"e.g. {', '.join(endpoint_sample)}"
                            )
                        except Exception:
                            peer_lines.append(f"- **{psol.name}** (solution {pid}): API generated")
                    if peer_lines:
                        lines.append("\n### Peer Solution APIs (dependency graph)")
                        lines.append(
                            "These solutions share ArchiMate elements with this solution. "
                            "Generate typed httpx clients in app/integrations/ to call them — "
                            "do NOT hardcode URLs or field names."
                        )
                        lines.extend(peer_lines)
            except Exception as _peer_exc:
                logger.debug("Peer spec lookup skipped: %s", _peer_exc)

            return "\n".join(lines)
        except Exception as e:
            logger.warning("Could not build solution context for codegen: %s", e)
            return ""

    @staticmethod
    def build_prompt(context: Dict[str, List[Dict]], solution_context: str = "") -> str:
        """Build the enrichment prompt from ArchiMate context and solution architecture context."""
        try:
            from app.models.ai_service import AIPromptTemplate
            override = AIPromptTemplate.query.filter_by(
                name="solution_prompt_codegen_uml_enrichment"
            ).first()
            template = override.system_prompt if override and override.system_prompt else UML_ENRICHMENT_PROMPT
        except Exception:
            template = UML_ENRICHMENT_PROMPT

        return template.format(
            solution_context=solution_context or "(No architectural context available — infer from element names and types.)",
            data_objects_json=json.dumps(context.get("data_objects", []), indent=2),
            app_interfaces_json=json.dumps(context.get("app_interfaces", []), indent=2),
            app_components_json=json.dumps(context.get("app_components", []), indent=2),
            app_services_json=json.dumps(context.get("app_services", []), indent=2),
            business_processes_json=json.dumps(context.get("business_processes", []), indent=2),
            tech_nodes_json=json.dumps(context.get("tech_nodes", []), indent=2),
            requirements_json=json.dumps(context.get("requirements", []), indent=2),
            constraints_json=json.dumps(context.get("constraints", []), indent=2),
            capabilities_json=json.dumps(context.get("capabilities", []), indent=2),
            business_actors_json=json.dumps(context.get("business_actors", []), indent=2),
            work_packages_json=json.dumps(context.get("work_packages", []), indent=2),
        )

    @staticmethod
    def parse_uml_response(raw_text: str) -> Optional[Dict]:
        """Parse LLM response into UML JSON, stripping markdown fences.

        Falls back to json_repair when the LLM emits structurally-valid JSON
        with minor syntax errors (missing commas, trailing commas, unquoted keys).
        """
        text = raw_text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```\s*$', '', text)

        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start < 0 or json_end <= json_start:
            return None

        candidate = text[json_start:json_end]
        required_keys = {"class_diagram", "sequence_diagram", "component_diagram", "deployment_diagram"}

        # Try strict parse first — cheapest path
        try:
            parsed = json.loads(candidate)
            if required_keys.issubset(parsed.keys()):
                return parsed
            logger.warning("UML response missing keys: %s", required_keys - parsed.keys())
            return None
        except json.JSONDecodeError as e:
            logger.warning("UML JSON strict parse failed (%s) — attempting repair", e)

        # Fallback: json_repair handles missing commas, trailing commas, unquoted keys
        try:
            import json_repair
            repaired_text = json_repair.repair_json(candidate)
            parsed = json.loads(repaired_text)
            if required_keys.issubset(parsed.keys()):
                logger.info("UML JSON recovered via json_repair")
                return parsed
            logger.warning("Repaired UML response still missing keys: %s", required_keys - parsed.keys())
        except Exception as repair_err:
            logger.error("json_repair fallback failed: %s", repair_err)

        return None

    # Threshold: above this, split into batches
    BATCH_THRESHOLD = 15
    # Max data objects per batch (each gets related processes/components)
    BATCH_SIZE = 8

    @staticmethod
    def _merge_uml(base: Dict, addition: Dict) -> Dict:
        """Merge a partial UML result into the accumulated base."""
        for diagram_key in ("class_diagram", "sequence_diagram", "component_diagram", "deployment_diagram"):
            base_diagram = base.get(diagram_key, {})
            add_diagram = addition.get(diagram_key, {})
            # Each diagram has a list key: classes, flows, components, nodes
            list_key = {
                "class_diagram": "classes",
                "sequence_diagram": "flows",
                "component_diagram": "components",
                "deployment_diagram": "nodes",
            }[diagram_key]
            base_list = base_diagram.get(list_key, [])
            add_list = add_diagram.get(list_key, [])
            # Deduplicate by source_element_id or name
            existing_ids = {
                item.get("source_element_id") or item.get("name")
                for item in base_list
            }
            for item in add_list:
                item_key = item.get("source_element_id") or item.get("name")
                if item_key not in existing_ids:
                    base_list.append(item)
                    existing_ids.add(item_key)
            base_diagram[list_key] = base_list
            base[diagram_key] = base_diagram
        return base

    @staticmethod
    def _load_relationships(element_ids: List[int]) -> Dict[int, set]:
        """Load ArchiMate relationships and build adjacency map.

        Returns { element_id: set(related_element_ids) } for all elements.
        """
        adjacency = {eid: set() for eid in element_ids}
        try:
            from app.models.archimate_core import ArchiMateRelationship
            eid_set = set(element_ids)
            rels = ArchiMateRelationship.query.filter(
                (ArchiMateRelationship.source_id.in_(element_ids)) |
                (ArchiMateRelationship.target_id.in_(element_ids))
            ).all()
            for r in rels:
                if r.source_id in eid_set and r.target_id in eid_set:
                    adjacency.setdefault(r.source_id, set()).add(r.target_id)
                    adjacency.setdefault(r.target_id, set()).add(r.source_id)
            logger.info("Loaded %d ArchiMate relationships for partitioning", len(rels))
        except Exception as e:
            logger.warning("Could not load relationships for partitioning: %s", e)
        return adjacency

    @staticmethod
    def _build_batches(context: Dict[str, List[Dict]], adjacency: Optional[Dict[int, set]] = None) -> List[Dict[str, List[Dict]]]:
        """Split context into smart batches — each DataObject group gets only related processes/components.

        Uses ArchiMate relationships (if available) to match DataObjects with their
        related BusinessProcesses and AppComponents. Falls back to name similarity.
        """

        data_objects = context.get("data_objects", [])
        all_processes = context.get("business_processes", [])
        all_components = context.get("app_components", [])
        batch_size = UMLEnrichmentService.BATCH_SIZE

        if not adjacency:
            adjacency = {}

        # Build element lookup by ID
        all_by_id = {}
        for key in ("data_objects", "app_components", "business_processes", "app_services", "app_interfaces"):
            for item in context.get(key, []):
                if item.get("id"):
                    all_by_id[item["id"]] = item

        # Shared lightweight context (included in every batch, capped)
        shared = {
            "tech_nodes": context.get("tech_nodes", [])[:6],
            "requirements": context.get("requirements", [])[:6],
            "constraints": context.get("constraints", [])[:6],
            "app_interfaces": context.get("app_interfaces", [])[:5],
            "app_services": context.get("app_services", [])[:5],
            "capabilities": context.get("capabilities", [])[:5],
            "business_actors": context.get("business_actors", [])[:5],
            "work_packages": [],
        }

        batches = []
        for i in range(0, max(len(data_objects), 1), batch_size):
            batch_dos = data_objects[i:i + batch_size]
            batch_do_ids = {d["id"] for d in batch_dos if d.get("id")}
            batch_do_names = [d.get("name", "").lower() for d in batch_dos]

            # Find related processes and components via relationships
            related_ids = set()
            for do_id in batch_do_ids:
                related_ids.update(adjacency.get(do_id, set()))

            # Filter processes/components that are related
            batch_processes = []
            batch_components = []

            for proc in all_processes:
                pid = proc.get("id")
                pname = proc.get("name", "").lower()
                # Match by relationship
                if pid and pid in related_ids:
                    batch_processes.append(proc)
                    continue
                # Match by name similarity — does the process name overlap with any DO name?
                for do_name in batch_do_names:
                    # Extract meaningful words (skip type prefixes like "BusinessProcess:")
                    do_words = set(do_name.replace(":", " ").split())
                    proc_words = set(pname.replace(":", " ").split())
                    overlap = do_words & proc_words - {"the", "a", "an", "of", "for", "and", "or", "in", "to", "is"}
                    if len(overlap) >= 1:
                        batch_processes.append(proc)
                        break

            for comp in all_components:
                cid = comp.get("id")
                cname = comp.get("name", "").lower()
                if cid and cid in related_ids:
                    batch_components.append(comp)
                    continue
                for do_name in batch_do_names:
                    do_words = set(do_name.replace(":", " ").split())
                    comp_words = set(cname.replace(":", " ").split())
                    overlap = do_words & comp_words - {"the", "a", "an", "of", "for", "and", "or", "in", "to", "is"}
                    if len(overlap) >= 1:
                        batch_components.append(comp)
                        break

            # If no matches found, include a small random sample so the LLM has context
            if not batch_processes and all_processes:
                batch_processes = all_processes[:3]
            if not batch_components and all_components:
                batch_components = all_components[:3]

            batch = {
                "data_objects": batch_dos,
                "app_components": batch_components,
                "business_processes": batch_processes,
            }
            batch.update(shared)
            batches.append(batch)

        return batches

    @staticmethod
    def _enrich_single(element_dicts: List[Dict], LLMService, provider, model,
                       solution_context: str = "") -> Optional[Dict]:
        """Run a single LLM enrichment call and return parsed UML or None."""
        context = UMLEnrichmentService.build_archimate_context(element_dicts)
        prompt = UMLEnrichmentService.build_prompt(context, solution_context)
        # UML JSON responses are large — request maximum safe output for the model.
        uml_max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=16000)

        try:
            raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider, max_tokens=uml_max_tokens)
        except Exception as e:
            logger.error("LLM call failed during UML enrichment: %s", e)
            return None

        if not raw_text:
            return None

        uml = UMLEnrichmentService.parse_uml_response(raw_text)
        if not uml:
            # Retry once
            try:
                raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider, max_tokens=uml_max_tokens)
                uml = UMLEnrichmentService.parse_uml_response(raw_text)
            except Exception as retry_err:
                logger.error("Single enrichment retry failed: %s", retry_err)
        return uml

    @staticmethod
    def _run_batch(batch_context: Dict, idx: int, total: int, LLMService, provider, model,
                   solution_context: str = "") -> Optional[Dict]:
        """Execute a single batch enrichment (used by thread pool)."""
        logger.info("Enrichment batch %d/%d: %d data objects, %d processes",
                     idx + 1, total,
                     len(batch_context.get("data_objects", [])),
                     len(batch_context.get("business_processes", [])))

        prompt = UMLEnrichmentService.build_prompt(batch_context, solution_context)
        # UML JSON responses are large — request maximum safe output for the model.
        uml_max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=16000)
        try:
            raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider, max_tokens=uml_max_tokens)
        except Exception as e:
            logger.error("Batch %d LLM call failed: %s", idx + 1, e)
            return None

        if not raw_text:
            logger.warning("Batch %d returned empty response", idx + 1)
            return None

        partial = UMLEnrichmentService.parse_uml_response(raw_text)
        if not partial:
            try:
                raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider, max_tokens=uml_max_tokens)
                partial = UMLEnrichmentService.parse_uml_response(raw_text)
            except Exception as retry_err:
                logger.error("Batch %d retry failed: %s", idx + 1, retry_err)

        if partial:
            logger.info("Batch %d produced %d classes, %d flows",
                        idx + 1,
                        len(partial.get("class_diagram", {}).get("classes", [])),
                        len(partial.get("sequence_diagram", {}).get("flows", [])))
        return partial

    @staticmethod
    def _infer_relationships(uml: Dict, adjacency: Dict[int, set]) -> Dict:
        """Post-merge pass: infer foreign key relationships between classes.

        Uses ArchiMate relationships (source_element_id adjacency) and name patterns
        to add relationships to UML classes that have none.
        """
        classes = uml.get("class_diagram", {}).get("classes", [])
        if len(classes) < 2:
            return uml

        # Build lookup: source_element_id -> class
        by_source = {}
        by_name = {}
        for cls in classes:
            sid = cls.get("source_element_id")
            if sid:
                by_source[sid] = cls
            by_name[cls["name"].lower()] = cls

        added = 0
        for cls in classes:
            if cls.get("relationships"):
                continue  # Already has relationships
            sid = cls.get("source_element_id")
            if not sid:
                continue

            # Find related classes via ArchiMate relationships
            related_ids = adjacency.get(sid, set())
            for rel_id in related_ids:
                target_cls = by_source.get(rel_id)
                if target_cls and target_cls["name"] != cls["name"]:
                    if "relationships" not in cls:
                        cls["relationships"] = []
                    # Avoid duplicates
                    existing_targets = {r["target_class"] for r in cls["relationships"]}
                    if target_cls["name"] not in existing_targets:
                        cls["relationships"].append({
                            "target_class": target_cls["name"],
                            "type": "many_to_one",
                            "back_populates": target_cls["name"].lower() + "s",
                        })
                        # Add foreign key field if not exists
                        fk_name = target_cls["name"][0].lower() + target_cls["name"][1:] + "_id"
                        field_names = {f["name"] for f in cls.get("fields", [])}
                        if fk_name not in field_names:
                            cls.setdefault("fields", []).append({
                                "name": fk_name,
                                "type": "int",
                                "nullable": True,
                                "primary_key": False,
                                "foreign_key": target_cls["name"] + ".id",
                                "description": "FK to " + target_cls["name"],
                            })
                        added += 1

        if added:
            logger.info("Inferred %d cross-class relationships from ArchiMate data", added)
        return uml

    @staticmethod
    def _enrich_sparse_fields(uml: Dict, element_dicts: List[Dict], LLMService, provider, model) -> Dict:
        """Post-merge pass: enrich classes that have ≤3 fields (just id/timestamps).

        Makes a targeted LLM call per sparse class asking for domain-specific fields.
        """
        classes = uml.get("class_diagram", {}).get("classes", [])
        # Find element descriptions by ID
        desc_by_id = {e["id"]: e.get("description", "") for e in element_dicts}

        sparse = [cls for cls in classes if len(cls.get("fields", [])) <= 3]
        if not sparse:
            return uml

        logger.info("Found %d sparse classes (<=3 fields), enriching fields", len(sparse))

        # Build a single prompt for all sparse classes (cheaper than per-class calls)
        class_descriptions = []
        for cls in sparse:
            sid = cls.get("source_element_id")
            desc = desc_by_id.get(sid, "")
            class_descriptions.append(
                '- %s (source_element_id: %s): %s' % (cls["name"], sid or "?", desc or "no description")
            )

        field_prompt = """You are a data modeler. For each class below, generate 5-8 domain-specific fields.

Classes needing fields:
%s

Return ONLY valid JSON — an object where each key is the class name and the value is an array of field objects:
{
  "ClassName": [
    {"name": "field_name", "type": "int|str|float|Decimal|datetime|bool|UUID", "nullable": true, "description": "what this field stores"}
  ]
}

RULES:
1. Do NOT include id, created_at, updated_at — those already exist.
2. Field names must be snake_case Python identifiers.
3. Types must be Python types: int, str, float, Decimal, datetime, bool, UUID.
4. Be specific to the business domain described — no generic filler fields.""" % "\n".join(class_descriptions)

        try:
            raw_text, _ = LLMService._call_llm(prompt=field_prompt, model=model, provider=provider)
        except Exception as e:
            logger.error("Field enrichment LLM call failed: %s", e)
            return uml

        if not raw_text:
            return uml

        # Parse response
        text = raw_text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```\s*$', '', text)
        json_start = text.find("{")
        json_end = text.rfind("}") + 1

        if json_start < 0 or json_end <= json_start:
            logger.warning("Could not parse field enrichment response")
            return uml

        try:
            field_data = json.loads(text[json_start:json_end])
        except json.JSONDecodeError as e:
            logger.error("Failed to parse field enrichment JSON: %s", e)
            return uml

        # Merge enriched fields into sparse classes
        enriched_count = 0
        for cls in classes:
            new_fields = field_data.get(cls["name"])
            if not new_fields or not isinstance(new_fields, list):
                continue
            existing_names = {f["name"] for f in cls.get("fields", [])}
            for f in new_fields:
                if f.get("name") and f["name"] not in existing_names:
                    cls.setdefault("fields", []).append({
                        "name": f["name"],
                        "type": f.get("type", "str"),
                        "nullable": f.get("nullable", True),
                        "primary_key": False,
                        "foreign_key": None,
                        "description": f.get("description", ""),
                    })
                    existing_names.add(f["name"])
            enriched_count += 1

        logger.info("Enriched fields for %d sparse classes", enriched_count)
        return uml

    @staticmethod
    def _enrich_batched(context: Dict[str, List[Dict]], element_dicts: List[Dict],
                        adjacency: Dict[int, set], LLMService, provider, model,
                        solution_context: str = "") -> Optional[Dict]:
        """Run batched enrichment with smart partitioning and post-merge passes."""
        batches = UMLEnrichmentService._build_batches(context, adjacency)
        logger.info("Batched enrichment: %d batches from %d data objects",
                     len(batches), len(context.get("data_objects", [])))

        merged_uml = {
            "class_diagram": {"classes": []},
            "sequence_diagram": {"flows": []},
            "component_diagram": {"components": []},
            "deployment_diagram": {"nodes": []},
        }
        success_count = 0

        # Run batches sequentially (threads can't share Flask app context)
        for idx, batch_context in enumerate(batches):
            partial = UMLEnrichmentService._run_batch(
                batch_context, idx, len(batches), LLMService, provider, model, solution_context
            )
            if partial:
                merged_uml = UMLEnrichmentService._merge_uml(merged_uml, partial)
                success_count += 1
            else:
                logger.warning("Batch %d produced no results", idx + 1)

        if success_count == 0:
            return None

        logger.info("Merged %d batches: %d classes, %d flows",
                     success_count,
                     len(merged_uml.get("class_diagram", {}).get("classes", [])),
                     len(merged_uml.get("sequence_diagram", {}).get("flows", [])))

        # Post-merge pass 1: infer relationships between classes
        merged_uml = UMLEnrichmentService._infer_relationships(merged_uml, adjacency)

        # Post-merge pass 2: enrich sparse classes with domain-specific fields
        merged_uml = UMLEnrichmentService._enrich_sparse_fields(
            merged_uml, element_dicts, LLMService, provider, model
        )

        return merged_uml

    @staticmethod
    def enrich(solution_id: int) -> Dict[str, Any]:
        """Run full enrichment: query ArchiMate elements, call LLM, return UML JSON.

        For solutions with > BATCH_THRESHOLD elements, splits into batches
        centered on DataObjects to avoid LLM output truncation.
        """
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.archimate_core import ArchiMateElement
        from app.modules.ai_chat.services.llm_service import LLMService

        links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        element_ids = [link.element_id for link in links]

        if not element_ids:
            return {"success": False, "error": "No ArchiMate elements found for this solution"}

        # Build maps of element_id -> spec_data from junctions
        spec_data_map = {}
        process_steps_map = {}
        business_rules_map = {}  # element_id -> list of {name, rule} from acm_properties
        for link in links:
            if hasattr(link, "spec_data") and link.spec_data:
                if link.spec_data.get("fields"):
                    spec_data_map[link.element_id] = link.spec_data["fields"]
                if link.spec_data.get("process_steps"):
                    process_steps_map[link.element_id] = link.spec_data["process_steps"]

        _BUSINESS_TYPES = frozenset({
            "BusinessProcess", "BusinessService", "BusinessFunction", "BusinessEvent",
        })

        elements = ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
        element_dicts = []
        for e in elements:
            # Synthesize a structured description from architect-defined process steps when available.
            # This replaces the (often empty) ArchiMateElement.description for business-layer types
            # and flows directly into the UML prompt's business_processes_json → method_body_hint.
            if e.id in process_steps_map and e.type in _BUSINESS_TYPES:
                parts = []
                for i, s in enumerate(process_steps_map[e.id], 1):
                    actor = s.get("actor", "").strip()
                    action = s.get("action", "").strip()
                    target = s.get("target", "").strip()
                    validation = s.get("validation", "").strip()
                    step_str = f"{i}. "
                    if actor:
                        step_str += f"{actor} "
                    step_str += f"→ {action}"
                    if target:
                        step_str += f" → {target}"
                    if validation:
                        step_str += f" | validate: {validation}"
                    parts.append(step_str)
                description = "; ".join(parts)
            else:
                description = e.description or ""

            d = {"id": e.id, "name": _strip_archimate_suffix(e.name), "type": e.type, "layer": e.layer, "description": description}
            if e.type in _MOTIVATION_TRACE_TYPES:
                rt = build_requirement_traceability_from_acm(getattr(e, "acm_properties", None) or {})
                if rt:
                    d["requirement_traceability"] = rt
            if e.id in spec_data_map:
                d["confirmed_fields"] = spec_data_map[e.id]
            if e.id in process_steps_map:
                d["process_steps"] = process_steps_map[e.id]
            # Propagate ACM decisions so _uml_to_product_spec_bundle can route
            # "buy"/"SaaS" components to integration client stubs instead of full CRUD.
            acm = e.acm_properties or {}
            for _key in ("build_or_buy", "deployment_model"):
                _v = acm.get(_key)
                if _v:
                    d[_key] = _v.get("value", _v) if isinstance(_v, dict) else _v
            # Extract architect-defined business rules from ACM properties.
            # Rules flow into the UML prompt as Rule 8 → _validate_<name>() stubs.
            _rules = acm.get("business_rules")
            if _rules and isinstance(_rules, list):
                normalized = []
                for r in _rules:
                    if isinstance(r, dict) and r.get("rule"):
                        normalized.append({
                            "name": r.get("name", "rule").lower().replace(" ", "_"),
                            "rule": r["rule"],
                        })
                    elif isinstance(r, str) and r.strip():
                        normalized.append({"name": "rule", "rule": r.strip()})
                if normalized:
                    d["business_rules"] = normalized
                    business_rules_map[e.id] = normalized
            element_dicts.append(d)

        has_app_component = any(e["type"] == "ApplicationComponent" for e in element_dicts)
        has_data_object = any(e["type"] in ("DataObject", "BusinessObject") for e in element_dicts)
        if not has_app_component or not has_data_object:
            return {"success": False, "error": "Need at least 1 Application Component and 1 Data Object to generate UML"}

        try:
            provider, model = LLMService._get_configured_provider()
        except Exception as e:
            logger.error("No LLM provider configured for UML enrichment: %s", e)
            return {"success": False, "error": "No LLM provider configured. Go to Admin > AI settings to add one."}

        # Cap total elements to prevent OOM on large solutions.
        # Priority: DataObjects (become classes) → AppComponents (become services) →
        # BusinessProcesses (become methods) → everything else.
        MAX_ELEMENTS = 12  # Reduced from 25 — prevents OOM on 2GB droplets
        if len(element_dicts) > MAX_ELEMENTS:
            _prio = {"DataObject": 0, "BusinessObject": 0, "ApplicationComponent": 1,
                     "ApplicationFunction": 1, "BusinessProcess": 2, "BusinessService": 2}
            element_dicts = sorted(
                element_dicts,
                key=lambda e: (_prio.get(e["type"], 3), e.get("id", 0))
            )[:MAX_ELEMENTS]
            logger.info("Solution %d: capped from original count to %d elements for enrich",
                        solution_id, MAX_ELEMENTS)

        # Assemble full architectural context from solution metadata, blueprint, and drivers
        solution_context = UMLEnrichmentService._build_solution_context(solution_id)
        if solution_context:
            logger.info("Solution %d: injecting architectural context (%d chars) into UML prompt",
                        solution_id, len(solution_context))

        context = UMLEnrichmentService.build_archimate_context(element_dicts)

        # Inject real enum values, display fields, and UX preferences into context
        # before passing to the LLM so generated code uses live DB values.
        try:
            _inject_ux_enrichment(solution_id, context)
        except Exception as _ux_exc:
            logger.warning(
                "Solution %d: UX enrichment injection failed (non-fatal): %s",
                solution_id, _ux_exc,
            )

        # Decide: single-shot or batched
        total_code_elements = (
            len(context.get("data_objects", []))
            + len(context.get("app_components", []))
            + len(context.get("business_processes", []))
        )

        if total_code_elements <= UMLEnrichmentService.BATCH_THRESHOLD:
            # Small solution — single LLM call
            uml = UMLEnrichmentService._enrich_single(element_dicts, LLMService, provider, model, solution_context)
        else:
            # Large solution — batched enrichment with smart partitioning
            logger.info("Solution %d has %d code elements — using batched enrichment",
                        solution_id, total_code_elements)
            adjacency = UMLEnrichmentService._load_relationships(element_ids)
            uml = UMLEnrichmentService._enrich_batched(
                context, element_dicts, adjacency, LLMService, provider, model, solution_context
            )

        if not uml:
            return {"success": False, "error": "Failed to parse UML from LLM response"}

        # Write inferred field schemas back to spec_data on junctions
        # Pass solution_id so vendor seeds are preferred over ai_inferred when available
        UMLEnrichmentService._write_spec_data_from_uml(uml, links, solution_id=solution_id)

        return {"success": True, "uml": uml, "element_count": len(element_dicts)}

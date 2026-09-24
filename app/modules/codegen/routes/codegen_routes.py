"""Code Workbench routes — generates code from ArchiMate elements.

Supports two generation modes:
  - "llm" (default): 7 sequential LLM calls via CodeGenerationService (slower, richer)
  - "deterministic": Pure Jinja2 via DeterministicCodeGenerator (instant, reproducible)

Supported languages (deterministic mode):
  - python-fastapi (default)
  - go-chi
"""
import hashlib
import io
import json
import logging
import re
import time
import zipfile
from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    stream_with_context,
)
from flask_login import current_user, login_required
from app.utils.csrf_helper import require_csrf
from app.extensions import db
from app.modules.codegen.models import (  # noqa: F401 — import surface shared by codegen route sub-modules
    CodegenGeneration, CodegenGenerationHistory, CodegenSystemBoundary, SystemBoundarySolution,
    CodegenTemplateSet, CodegenTemplateFile, DataImport,
)
# Alias for readability within this module — all SystemBoundary refs here mean the codegen one
SystemBoundary = CodegenSystemBoundary
from app.models.solution_models import Solution
from app.modules.codegen.routes._helpers import (  # noqa: F401 — re-exported for sub-modules
    SUPPORTED_LANGUAGES,
    _PY_TYPE_MAP,
    _SECRET_PATTERNS,
    _TF_CACHE_KEYWORDS,
    _TF_DB_KEYWORDS,
    _TF_STORAGE_KEYWORDS,
    _append_intent_gate_event,
    _as_bool,
    _auto_verify_generated_tests,
    _build_peer_specs,
    _build_service_handler_js,
    _build_terraform_files,
    _check_access,
    _classify_tech_element,
    _collect_spec_confirmation_counts,
    _compute_chain_completeness,
    _compute_file_artifact_signals,
    _compute_quality_score,
    _compute_rule_coverage,
    _ensure_archimate_elements_from_journey,
    _enrich_background,
    _enrich_quality_details_for_ui,
    _enrich_solution_elements_from_brief,
    _extract_business_rules_from_brief,
    _extract_field_types,
    _extract_model_fields,
    _find_matching_endpoint,
    _find_placeholder_stubs,
    _format_python_files,
    _generate_adr,
    _generate_alembic_migration,
    _generate_alembic_support_files,
    _generate_architecture_invariant_tests,
    _generate_azure_bicep,
    _generate_azure_logic_app,
    _generate_business_rule_contract_tests,
    _generate_contract_tests,
    _generate_expo_mobile,
    _generate_implementation_guide,
    _generate_peer_client_stub,
    _generate_power_platform_solution,
    _generate_refine_frontend,
    _generate_sap_btp_integration,
    _generate_sap_cap,
    _generate_seed_expectation_tests,
    _generate_settings_config,
    _generate_shadcn_frontend,
    _generate_smoke_test,
    _generate_test_conftest,
    _generate_validator_unit_tests,
    _get_github_service,
    _get_solution_business_rules,
    _has_element_type,
    _infer_auth_from_blueprint,
    _inject_validator_stub,
    _latest_intent_verify_state,
    _map_cds_type,
    _notify_tech_lead,
    _parse_condition_to_boundary,
    _parse_condition_to_preconditions,
    _parse_github_owner_repo,
    _persist_intent_verify_state,
    _run_runtime_smoke_checks,
    _scan_for_secrets,
    _check_security_hardening,
    _lint_python_files,
    _spec_type_to_uml,
    _synthesize_uml_from_elements,
    _tf_slug,
    _topological_sort_classes,
    _uml_to_product_spec_bundle,
    _utc_now_iso,
    _validate_blueprint_constraints,
    _validate_import_graph,
    _verify_enrichment_rule_coverage,
    _generate_entity_tests,
    _rewrite_vendor_sdk_files,
)

logger = logging.getLogger(__name__)

codegen_bp = Blueprint("codegen", __name__)


# Valid ArchiMate element types accepted by the generation pipeline
_NL_VALID_TYPES = {
    # Business layer
    "BusinessActor", "BusinessRole", "BusinessProcess", "BusinessFunction",
    "BusinessService", "BusinessInterface", "BusinessObject", "BusinessEvent",
    "BusinessInteraction", "Requirement", "Constraint",
    # Application layer
    "ApplicationComponent", "ApplicationService", "ApplicationFunction",
    "ApplicationInterface", "DataObject", "ApplicationEvent",
    # Technology layer
    "Node", "Device", "SystemSoftware", "TechnologyService", "TechnologyFunction",
    "TechnologyInterface", "Artifact", "CommunicationNetwork",
    # Implementation layer
    "WorkPackage", "Deliverable",
}

_NL_TYPE_TO_LAYER = {
    "BusinessActor": "Business", "BusinessRole": "Business", "BusinessProcess": "Business",
    "BusinessFunction": "Business", "BusinessService": "Business", "BusinessInterface": "Business",
    "BusinessObject": "Business", "BusinessEvent": "Business", "BusinessInteraction": "Business",
    "Requirement": "Business", "Constraint": "Business",
    "ApplicationComponent": "Application", "ApplicationService": "Application",
    "ApplicationFunction": "Application", "ApplicationInterface": "Application",
    "DataObject": "Application", "ApplicationEvent": "Application",
    "Node": "Technology", "Device": "Technology", "SystemSoftware": "Technology",
    "TechnologyService": "Technology", "TechnologyFunction": "Technology",
    "TechnologyInterface": "Technology", "Artifact": "Technology",
    "CommunicationNetwork": "Technology",
    "WorkPackage": "Implementation", "Deliverable": "Implementation",
}

_NL_TO_ARCH_PROMPT = """You are an enterprise architect generating ArchiMate 3.2 elements from a plain-English description.

## System Description
{description}

## Instructions

Generate ArchiMate elements that represent this system. Include elements across multiple layers:
- **Business layer**: actors, roles, processes, services (how the business uses the system)
- **Application layer**: components, services, data objects (what software is built)
- **Technology layer**: nodes, infrastructure (where it runs)

Use ONLY these valid ArchiMate types:
Business: BusinessActor, BusinessRole, BusinessProcess, BusinessFunction, BusinessService, BusinessInterface, BusinessObject, BusinessEvent, Requirement
Application: ApplicationComponent, ApplicationService, ApplicationFunction, ApplicationInterface, DataObject
Technology: Node, SystemSoftware, TechnologyService, Artifact

Rules:
1. Names must be PascalCase, descriptive, and unique (e.g. "PolicyValidationService", not "Service1")
2. Descriptions must be 1-2 sentences explaining the element's purpose in context
3. Generate 8-20 elements — enough to drive useful code generation
4. Focus on DataObjects and ApplicationComponents — these produce the richest code output
5. Include at least 3 DataObjects and 3 BusinessProcesses

Return ONLY valid JSON:
{{
  "elements": [
    {{
      "name": "PascalCaseName",
      "type": "ValidArchiMateType",
      "description": "1-2 sentence description of this element's role in the system"
    }}
  ]
}}"""


@codegen_bp.route("/solutions/<int:solution_id>/codegen/generate-architecture", methods=["POST"])
@login_required
@require_csrf
def generate_architecture(solution_id):
    """Feature 4: Natural Language to Architecture.

    Converts a plain-English description into ArchiMate elements linked to this solution.
    Creates ArchiMateElement records + SolutionArchiMateElement junctions.

    Payload: { "description": "...", "append": true/false }
    - append=false (default): removes previously NL-generated elements before creating new ones
    - append=true: adds to existing elements (iterative refinement)
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    description = (payload.get("description") or "").strip()
    append_mode = payload.get("append", False)

    if not description:
        return jsonify({"error": "description is required"}), 400
    if len(description) < 20:
        return jsonify({"error": "Description too short — provide at least 20 characters"}), 400
    if len(description) > 50_000:
        return jsonify({"error": "Description too long — maximum 50,000 characters"}), 400

    # LLM call
    from app.modules.ai_chat.services.llm_service import LLMService
    try:
        provider, model = LLMService._get_configured_provider()
        prompt = _NL_TO_ARCH_PROMPT.format(description=description)
        raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
    except Exception as e:
        return jsonify({"error": f"LLM call failed: {str(e)}"}), 500

    if not raw_text:
        return jsonify({"error": "LLM returned empty response"}), 500

    # Parse JSON from LLM response
    import json as _json
    elements_data = []
    try:
        # Strip markdown code fences if present
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = _json.loads(text)
        elements_data = parsed.get("elements", [])
    except Exception:
        # Try to extract JSON block from prose
        import re as _re
        match = _re.search(r'\{.*"elements".*\}', raw_text, _re.DOTALL)
        if match:
            try:
                parsed = _json.loads(match.group())
                elements_data = parsed.get("elements", [])
            except Exception as _parse_exc:
                logger.warning("Failed to parse elements JSON from LLM response: %s", _parse_exc)

    if not elements_data:
        return jsonify({"error": "Could not parse elements from LLM response. Try rephrasing the description."}), 500

    # Import models
    from app.models.archimate_core import ArchiMateElement, ArchitectureModel
    from app.models.solution_archimate_element import SolutionArchiMateElement

    # If replace mode: remove junctions created by previous NL generation for this solution
    if not append_mode:
        old_junctions = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id,
            element_role="nl_generated",
        ).all()
        for j in old_junctions:
            db.session.delete(j)
        db.session.flush()

    # Get or create an ArchitectureModel scoped to this solution
    arch_model = ArchitectureModel.query.filter_by(solution_id=solution_id).first()
    if not arch_model:
        arch_model = ArchitectureModel(
            name=f"NL Architecture (Solution {solution_id})",
            version="1.0",
            solution_id=solution_id,
        )
        db.session.add(arch_model)
        db.session.flush()

    created = []
    skipped = []

    for el in elements_data:
        name = (el.get("name") or "").strip()
        el_type = (el.get("type") or "").strip()
        desc = (el.get("description") or "").strip()

        if not name or not el_type:
            skipped.append({"name": name, "reason": "missing name or type"})
            continue

        # Validate / normalize type
        if el_type not in _NL_VALID_TYPES:
            # Try case-insensitive match
            match = next((t for t in _NL_VALID_TYPES if t.lower() == el_type.lower()), None)
            if match:
                el_type = match
            else:
                skipped.append({"name": name, "reason": f"unknown type: {el_type}"})
                continue

        layer = _NL_TYPE_TO_LAYER.get(el_type, "Application")

        # Dedup: reuse existing element with same name+type
        existing = ArchiMateElement.query.filter_by(name=name, type=el_type).first()
        if existing:
            element = existing
        else:
            element = ArchiMateElement(
                name=name,
                type=el_type,
                layer=layer,
                description=desc,
                scope="application",
                acm_source="nl_generated",
                architecture_id=arch_model.id,
            )
            db.session.add(element)
            try:
                db.session.flush()
            except Exception:
                db.session.rollback()
                skipped.append({"name": name, "reason": "db flush error"})
                continue

        # Dedup junction
        existing_junction = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id,
            element_id=element.id,
        ).first()
        if existing_junction:
            skipped.append({"name": name, "reason": "already linked"})
            continue

        junction = SolutionArchiMateElement(
            solution_id=solution_id,
            element_id=element.id,
            layer_type=layer,
            element_table="archimate_elements",
            element_name=name,
            element_role="nl_generated",
            is_new_element=True,
            spec_data={"fields_status": "pending", "description": desc},
            created_by_id=current_user.id,
        )
        db.session.add(junction)
        created.append({"name": name, "type": el_type, "layer": layer, "description": desc})

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("NL architecture generation DB commit failed: %s", e)
        return jsonify({"error": "Database error saving elements"}), 500

    return jsonify({
        "success": True,
        "created": len(created),
        "skipped": len(skipped),
        "elements": created,
        "append_mode": append_mode,
        "message": (
            f"Created {len(created)} ArchiMate elements. "
            f"Run Phase 1 (Enrich) to generate UML from them."
        ),
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen")
@login_required
def workbench_page(solution_id):
    """Render the Code Workbench page."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        abort(403)

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        try:
            gen = CodegenGeneration(solution_id=solution_id, version=1)
            db.session.add(gen)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to create CodegenGeneration for solution %s: %s", solution_id, e)
            return render_template("errors/500.html", error="Code Workbench is temporarily unavailable."), 500

    has_uml = gen.uml_snapshot is not None
    has_files = gen.generated_files is not None
    file_list = list((gen.generated_files or {}).keys())
    github_url = gen.github_url or ""

    # Determine furthest phase
    if has_files:
        initial_phase = 4
    elif gen.config:
        initial_phase = 3
    elif has_uml:
        initial_phase = 2
    else:
        initial_phase = 1

    # ── FAST PATH: only cheap queries on page load ──────────────────────────
    # chain_completeness and genome are deferred to lazy API calls because:
    # - _compute_chain_completeness calls ArchiMateInferenceEngine.diagnose() for every
    #   linked element (50+ DB queries), blocking the response for seconds.
    # - genome_json can be 100s of KB, causing browser HTML-parse stalls.
    # _ensure_archimate_elements_from_journey is also deferred (DB writes on page load).

    # Check LLM availability for patch-mode gating (fast — reads APISettings row)
    has_llm = False
    try:
        from app.modules.ai_chat.services.llm_service import LLMService
        LLMService._get_configured_provider()
        has_llm = True
    except Exception as _llm_exc:
        logger.debug("LLM provider not configured: %s", _llm_exc)

    # Spec counts — single-pass Python loop over junction table (fast, no joins)
    pending_specs_count = 0
    confirmed_specs_count = 0
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        _READY_STATUSES = {"confirmed", "vendor_seeded", "schema_imported"}
        for j in SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all():
            if isinstance(j.spec_data, dict):
                status = j.spec_data.get("fields_status")
                if status == "pending":
                    pending_specs_count += 1
                elif status in _READY_STATUSES:
                    confirmed_specs_count += 1
    except Exception as _spec_exc:
        logger.debug("Could not load spec counts: %s", _spec_exc)

    # Staleness: blueprint updated after UML was last enriched (cheap timestamp compare)
    blueprint_stale = False
    if has_uml and gen.updated_at and solution.blueprint_updated_at:
        blueprint_stale = solution.blueprint_updated_at > gen.updated_at

    # Suggested auth inferred from blueprint security_viewpoint narrative (in-memory)
    suggested_auth = _infer_auth_from_blueprint(solution.section_narratives or {})

    # Pre-populate the NL description from the architecture journey brief (in-memory)
    journey_brief = ""
    try:
        js = solution.journey_state if isinstance(solution.journey_state, dict) else {}
        journey_brief = js.get("enriched_brief") or js.get("problem_statement") or ""
    except Exception as exc:
        logger.debug("suppressed error in workbench_page (app/modules/codegen/routes/codegen_routes.py): %s", exc)

    # Cheap COUNT to determine initial phase — no joins, no writes
    linked_elements_count = 0
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        linked_elements_count = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id
        ).count()
    except Exception as exc:
        logger.debug("suppressed error in workbench_page (app/modules/codegen/routes/codegen_routes.py): %s", exc)

    # Journey solutions with elements but no UML/files should open at Phase 3
    if initial_phase == 1 and linked_elements_count > 0:
        initial_phase = 3

    return render_template(
        "codegen/workbench.html",
        solution=solution,
        generation=gen,
        initial_phase=initial_phase,
        has_uml=has_uml,
        has_files=has_files,
        file_list=file_list,
        github_url=github_url,
        chain_completeness=None,       # deferred — fetched by JS via /codegen/meta
        pending_specs_count=pending_specs_count,
        confirmed_specs_count=confirmed_specs_count,
        has_llm=has_llm,
        blueprint_stale=blueprint_stale,
        suggested_auth=suggested_auth,
        journey_brief=journey_brief,
        linked_elements_count=linked_elements_count,
        genome_json={},                # deferred — fetched by JS via /codegen/genome
    )


@codegen_bp.route("/solutions/<int:solution_id>/codegen/uml")
@login_required
def get_uml(solution_id):
    """Get current UML snapshot."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    return jsonify({
        "success": True,
        "uml": gen.uml_snapshot if gen else None,
        "version": gen.version if gen else 0,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/meta")
@login_required
def workbench_meta(solution_id):
    """Lazy-load expensive page metadata: chain completeness + spec counts.

    Deferred from workbench_page() to prevent blocking the initial HTML response.
    Called by the JS immediately after Alpine init (non-blocking).
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    chain_completeness = None
    try:
        chain_completeness = _compute_chain_completeness(solution_id)
    except Exception as exc:
        logger.debug("suppressed error in workbench_meta (app/modules/codegen/routes/codegen_routes.py): %s", exc)

    # Fallback: compute genome-native completeness from stored genome when
    # ArchiMate chain completeness is unavailable (no SolutionArchiMateElement links).
    if chain_completeness is None:
        try:
            from app.modules.codegen.models import CodegenGeneration
            _gen_meta = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
            if _gen_meta and isinstance(_gen_meta.genome, dict):
                _gm = _gen_meta.genome.get("modules") or {}
                if _gm:
                    _mwf = sum(1 for m in _gm.values() if m.get("fields"))
                    chain_completeness = round(_mwf / len(_gm), 3)
        except Exception as exc:
            logger.debug("suppressed error in workbench_meta (app/modules/codegen/routes/codegen_routes.py): %s", exc)

    pending_specs_count = 0
    confirmed_specs_count = 0
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        _READY_STATUSES = {"confirmed", "vendor_seeded", "schema_imported"}
        junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        for j in junctions:
            if isinstance(j.spec_data, dict):
                status = j.spec_data.get("fields_status")
                if status == "pending":
                    pending_specs_count += 1
                elif status in _READY_STATUSES:
                    confirmed_specs_count += 1
    except Exception as exc:
        logger.debug("suppressed error in workbench_meta (app/modules/codegen/routes/codegen_routes.py): %s", exc)

    active_provider = None
    active_model = None
    try:
        from app.modules.ai_chat.services.llm_service_impl import LLMService
        active_provider, active_model = LLMService._get_configured_provider()
    except Exception as exc:
        logger.debug("suppressed error in workbench_meta (app/modules/codegen/routes/codegen_routes.py): %s", exc)

    return jsonify({
        "chain_completeness": chain_completeness,
        "pending_specs_count": pending_specs_count,
        "confirmed_specs_count": confirmed_specs_count,
        "active_provider": active_provider,
        "active_model": active_model,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/genome")
@login_required
def get_genome(solution_id):
    """Return the stored genome JSON for a solution.

    Deferred from workbench_page() — genome can be 100s of KB, inlining it in HTML
    caused browser parse stalls. Fetched lazily when the user opens Genome Patch.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    return jsonify({"genome": gen.genome or {} if gen else {}})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/enrich", methods=["POST"])
@login_required
@require_csrf
def enrich_uml(solution_id):
    """Phase 1: Start async UML generation from ArchiMate elements."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    import threading
    from flask import current_app
    app_ctx = current_app._get_current_object().app_context()
    t = threading.Thread(target=_enrich_background, args=(app_ctx, solution_id), daemon=True)
    t.start()
    return jsonify({"status": "running"})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/enrich/status", methods=["GET"])
@login_required
def enrich_status(solution_id):
    """Poll async UML enrichment status."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"status": "idle"})
    cfg = gen.config or {}
    enrich_st = cfg.get("_enrich_status", {})
    if not enrich_st:
        return jsonify({"status": "idle"})
    # Detect dead threads: if still "running" after 10 minutes, auto-fail.
    # Persist the transition so subsequent polls see stable state.
    if enrich_st.get("status") == "running":
        import time as _time
        now = _time.time()
        started_raw = enrich_st.get("started_at")
        started = None
        timeout_reason = ""

        if started_raw is None:
            timeout_reason = "Enrichment state is missing started_at. Please retry."
        else:
            try:
                started = float(started_raw)
            except (TypeError, ValueError):
                timeout_reason = "Enrichment state has invalid started_at. Please retry."

        if not timeout_reason and (now - started) > 600:
            timeout_reason = "Enrichment timed out — worker was recycled. Please retry."

        if timeout_reason:
            enrich_st = {
                "status": "failed",
                "error": timeout_reason,
                "timed_out_at": now,
                "started_at": started_raw,
            }
            cfg["_enrich_status"] = enrich_st
            gen.config = cfg
            try:
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                logger.warning("Failed to persist enrich timeout status: %s", exc)
    return jsonify(enrich_st)


@codegen_bp.route("/solutions/<int:solution_id>/codegen/apply-specs", methods=["POST"])
@login_required
def apply_confirmed_specs(solution_id):
    """Write architect-confirmed spec_data fields into the UML snapshot class diagram.

    This makes confirmed field specs binding rather than advisory — the LLM interpretation
    is replaced with the architect's exact field definitions before Phase 4 code generation.
    Classes are marked with `_spec_locked: true` to indicate they carry authoritative specs.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.uml_snapshot:
        return jsonify({"error": "No UML snapshot — run Phase 1 enrichment first"}), 400

    from app.models.solution_archimate_element import SolutionArchiMateElement
    junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()

    # Treat confirmed, vendor_seeded, and schema_imported as ground truth.
    # All three bypass LLM field invention — distinction is only in the UI (confidence level).
    _TRUSTED_FIELD_STATUSES = {"confirmed", "vendor_seeded", "schema_imported"}
    confirmed_map = {}  # element_id -> list of field dicts
    for j in junctions:
        if (
            isinstance(j.spec_data, dict)
            and j.spec_data.get("fields_status") in _TRUSTED_FIELD_STATUSES
            and j.spec_data.get("fields")
        ):
            confirmed_map[j.element_id] = j.spec_data["fields"]

    if not confirmed_map:
        return jsonify({"error": "No confirmed field specs found — confirm fields in the Architecture Journey wizard first"}), 400

    import copy
    uml = copy.deepcopy(gen.uml_snapshot)
    classes = uml.get("class_diagram", {}).get("classes", [])
    applied = 0

    for cls in classes:
        source_id = cls.get("source_element_id")
        if source_id not in confirmed_map:
            continue
        spec_fields = confirmed_map[source_id]
        # Convert spec field format → UML class field format
        uml_fields = []
        for f in spec_fields:
            fname = f.get("name", "").strip()
            if not fname:
                continue
            uml_fields.append({
                "name": fname,
                "type": _spec_type_to_uml(f.get("type", "string")),
                "required": bool(f.get("required", False)),
                "description": f.get("description", ""),
            })
        if uml_fields:
            # Preserve id/created_at/updated_at from existing fields; replace the rest
            _kept = [fi for fi in cls.get("fields", []) if fi.get("name") in ("id", "created_at", "updated_at")]
            _new = [fi for fi in uml_fields if fi["name"] not in ("id", "created_at", "updated_at")]
            cls["fields"] = _kept + _new
            cls["_spec_locked"] = True
            applied += 1

    if not applied:
        return jsonify({"error": "No UML classes matched confirmed specs — check that source_element_ids are set"}), 400

    try:
        gen.uml_snapshot = uml
        gen.version = (gen.version or 1) + 1
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("apply_confirmed_specs DB commit failed: %s", e)
        return jsonify({"error": "Database error saving specs"}), 500

    return jsonify({
        "success": True,
        "applied_classes": applied,
        "version": gen.version,
    })


def _normalize_salesforce_package_config(base_config, payload=None):
    """Merge Salesforce package-mode request fields into a single config dict."""
    merged = dict(base_config or {})
    payload = payload or {}

    if "package_mode" in payload:
        merged["package_mode"] = payload.get("package_mode")
    if "namespace_prefix" in payload or "namespace" in payload:
        merged["namespace_prefix"] = payload.get("namespace_prefix") or payload.get("namespace") or ""

    language = merged.get("language")
    requested_mode = str(merged.get("package_mode") or "").strip().lower()
    namespace_prefix = str(merged.get("namespace_prefix") or merged.get("namespace") or "").strip()

    if language != "salesforce-apex":
        requested_mode = "unmanaged"
        namespace_prefix = ""
    elif requested_mode not in ("managed", "unmanaged"):
        requested_mode = "managed" if namespace_prefix else "unmanaged"

    if requested_mode != "managed":
        namespace_prefix = ""

    merged["package_mode"] = requested_mode
    merged["namespace_prefix"] = namespace_prefix
    return merged


def _attach_salesforce_package_settings(bundle, config):
    """Thread Salesforce workbench config into ProductSpecBundle.product_config."""
    product_config = dict(getattr(bundle, "product_config", {}) or {})
    salesforce = dict(product_config.get("salesforce") or {})

    if config.get("language") == "salesforce-apex":
        salesforce.update({
            "package_mode": config.get("package_mode", "unmanaged"),
            "namespace_prefix": config.get("namespace_prefix", ""),
        })
        product_config["salesforce"] = salesforce
    else:
        product_config.pop("salesforce", None)

    bundle.product_config = product_config
    return bundle


def _evaluate_conformance_gate(solution_id, payload):
    """PROG-014: architectural-conformance gate on the SOURCE solution.

    Runs the AI-4 ConformanceReviewer over the blueprint before any code is
    generated from it. Generating a real app from a design that breaches a hard
    technical policy (e.g. a BLOCKED integration pattern) ships the defect — so
    CRITICAL findings soft-block generation (overridable via
    ``payload.override_conformance``); high/info findings are warn-only.

    Returns ``(block_payload | None, summary | None)``. Never raises —
    conformance must not break the generate path.
    """
    try:
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
            ConformanceReviewer,
        )
        c = ConformanceReviewer.review(solution_id)
    except Exception as exc:  # noqa: BLE001 — gate is advisory, never fatal
        logger.warning("Conformance gate unavailable for solution %s: %s", solution_id, exc)
        return None, None
    if not c.get("success"):
        return None, None

    findings = c.get("findings", []) or []
    critical = [f for f in findings if f.get("severity") == "critical"]
    summary = {
        "score": c.get("score"),
        "flagged": c.get("flagged", 0),
        "critical": len(critical),
        "findings": [
            {"severity": f.get("severity"), "title": f.get("title"),
             "category": f.get("category")}
            for f in findings[:5]
        ],
    }
    if critical and not bool(payload.get("override_conformance")):
        return {
            "success": False,
            "error": (
                f"Architecture conformance gate: {len(critical)} critical conformance "
                f"issue(s) in this blueprint (score {c.get('score')}/100). Generating an "
                "app from a non-conformant design ships the defect — resolve them, or "
                "re-run with 'generate anyway' to override."
            ),
            "action": "review_conformance",
            "conformance": summary,
            "review_packet_url": f"/solutions/{solution_id}/review-packet",
        }, summary
    return None, summary


@codegen_bp.route("/solutions/<int:solution_id>/codegen/generate", methods=["POST"])
@login_required
@require_csrf
def generate_code(solution_id):
    """Phase 4: Generate all code files from architecture.

    Supports four generation modes (set via payload.generation_mode or config.generation_mode):
      - "genome": Architectural Genome pipeline. Reads ArchiMate elements directly,
        compiles a genome IR, validates, converts to ProductSpecBundle, generates code.
        $0 LLM cost, deterministic, testable, supports state machines + security config.
      - "deterministic": Pure Jinja2 templates via UML synthesis. Instant, reproducible.
        Supports python-fastapi and go-chi.
      - "hybrid": Deterministic first, then a single LLM call enriches business logic.
        Best balance of speed and richness.
      - "llm" (legacy): 7 sequential LLM calls. Slower and prone to hallucinating
        field names. Kept for backwards compatibility.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()

    # Non-fatal warnings raised during generation. This accumulator had been removed
    # while a later `syntax_warnings.append(...)` remained, so the genome-quality
    # branch raised NameError instead of recording its warning. Note the success
    # responses in this function still emit a literal "syntax_warnings": [] — wiring
    # them to this list is a behaviour change, so it is deliberately left alone.
    syntax_warnings = []

    if gen and payload.get("version") and payload["version"] != gen.version:
        return jsonify({"error": "Version conflict. Refresh the page."}), 409

    if not gen:
        gen = CodegenGeneration(solution_id=solution_id, version=1, config={})
        db.session.add(gen)
        db.session.commit()

    # Journey→ArchiMate bridge: ensure application-layer elements exist before any
    # generation mode runs. Handles two failure paths transparently:
    #   - LLM generation never ran (no API keys) → synthesizes from SolutionCapability records
    #   - User skipped confirm_domain (Steps 4/5) → promotes pending proposals inline
    # No-op when elements already exist (normal post-journey path).
    try:
        _ensure_archimate_elements_from_journey(solution_id)
    except Exception as _bridge_err:
        logger.warning("Journey→ArchiMate bridge failed for solution %d: %s", solution_id, _bridge_err)

    # Enrich element descriptions from the solution's enriched brief BEFORE UML synthesis.
    # This ensures _infer_fields_for_capability() has real business context so generated
    # DB models reflect the actual domain rather than the generic id/name/status fallback.
    try:
        _enriched = _enrich_solution_elements_from_brief(solution_id)
        if _enriched:
            logger.info("Brief enrichment: %d elements enriched before generate_code for solution %d", _enriched, solution_id)
    except Exception as _enrich_err:
        logger.warning("Brief enrichment failed for solution %d: %s", solution_id, _enrich_err)

    # Extract business rules from the brief so _get_solution_business_rules() has data
    # even for solutions that never went through the structured constraints wizard.
    try:
        _rule_count = _extract_business_rules_from_brief(solution_id)
        if _rule_count:
            logger.info("Brief rule extraction: %d rules stored for solution %d", _rule_count, solution_id)
    except Exception as _rule_err:
        logger.warning("Brief rule extraction failed for solution %d: %s", solution_id, _rule_err)

    # If no UML snapshot, synthesize from ArchiMate elements (no LLM needed for deterministic mode)
    # Genome mode bypasses UML entirely — reads ArchiMate elements directly.
    _req_mode = payload.get("generation_mode") or (gen.config or {}).get("generation_mode", "deterministic")
    if not gen.uml_snapshot and _req_mode != "genome":
        if _req_mode in ("deterministic", "hybrid"):
            _synth_uml = _synthesize_uml_from_elements(solution_id)
            if _synth_uml:
                gen.uml_snapshot = _synth_uml
                gen.version = (gen.version or 0) + 1
                db.session.commit()
                logger.info("Synthesized UML from ArchiMate elements for solution %d", solution_id)
            else:
                return jsonify({"error": "No ArchiMate elements found. Complete Steps 2-3 first."}), 400
        else:
            return jsonify({"error": "Run Phase 1 (Enrich) first — LLM mode requires enrichment."}), 400

    config = gen.config or {"auth": "none", "python_version": "3.12"}
    # Payload overrides take precedence over stored config (e.g. test/API callers can specify language)
    # genome is the default: AABL compiler → deterministic code, no LLM variability in output.
    # LLM is used only for genome enrichment (wizard steps 1-3), not code generation.
    mode = payload.get("generation_mode") or config.get("generation_mode", "genome")
    language = payload.get("language") or config.get("language", "python-fastapi")
    generation_policy = payload.get("generation_policy") or config.get("generation_policy", "scaffold")
    effective_config = _normalize_salesforce_package_config({**config, "language": language}, payload)

    # COM-013: Journey 4 — Code generation triggered
    try:
        from app.services.analytics_service import AnalyticsService
        from flask import g
        _org_id = getattr(g, "current_org_id", None)
        AnalyticsService().capture(
            f"{_org_id}:{current_user.id}",
            "codegen_triggered",
            {
                "solution_id": solution_id,
                "target_lang": language,
                "mode": mode,
                "org_id": _org_id,
            },
        )
    except Exception as exc:
        logger.debug("suppressed error in generate_code (app/modules/codegen/routes/codegen_routes.py): %s", exc)

    strict_production = generation_policy == "production-ready"
    enforce_chain_complete = _as_bool(
        payload.get("enforce_chain_complete"),
        default=(mode == "llm" or strict_production),
    )
    require_confirmed_specs = _as_bool(
        payload.get("require_confirmed_specs"),
        default=strict_production,
    )
    block_placeholder_stubs = _as_bool(
        payload.get("block_placeholder_stubs"),
        default=strict_production,
    )

    completeness = _compute_chain_completeness(solution_id)
    if enforce_chain_complete:
        if completeness is None:
            return jsonify({
                "success": False,
                "error": (
                    "Chain completeness could not be computed — "
                    "link ArchiMate elements to this solution before generating."
                ),
                "chain_completeness": None,
                "action": "repair_chains",
                "policy": generation_policy,
            }), 422
        if completeness < 0.7:
            return jsonify({
                "success": False,
                "error": (
                    f"Architecture chains are {completeness * 100:.0f}% complete — "
                    "minimum 70% required before generation."
                ),
                "chain_completeness": completeness,
                "action": "repair_chains",
                "policy": generation_policy,
            }), 422

    if require_confirmed_specs:
        total_specs, confirmed_specs, missing_specs = _collect_spec_confirmation_counts(solution_id, gen.uml_snapshot or {})
        if total_specs > 0 and confirmed_specs < total_specs:
            return jsonify({
                "success": False,
                "error": (
                    "Production-ready generation requires confirmed field specs. "
                    f"Confirmed {confirmed_specs}/{total_specs} classes."
                ),
                "action": "confirm_specs",
                "missing_specs": missing_specs[:20],
                "policy": generation_policy,
            }), 422

    # Fetch business rules before mode branching so enforcement checks are always available.
    _biz_rules = _get_solution_business_rules(solution_id)
    _seed_ctx = {}
    try:
        from app.modules.codegen.services.domain_seed_service import DomainSeedResolver

        _seed_resolution = DomainSeedResolver.resolve(
            solution,
            (gen.uml_snapshot or {}).get("class_diagram", {}).get("classes", []),
            _biz_rules,
        )
        _seed_ctx = {
            "version": _seed_resolution.get("version"),
            "vendor_keys": _seed_resolution.get("vendor_keys", []),
            "seed_index": _seed_resolution.get("seed_index", {}),
            "coverage": _seed_resolution.get("coverage", {}),
        }
    except Exception:
        _seed_ctx = {}

    # Initialise shared variables that downstream code expects regardless of mode.
    _score_seed_ctx = None

    # ── SAP CAP generation — dedicated BTP/OData V4 path ─────────────────
    from datetime import datetime  # noqa: PLC0415 — needed before early-return paths
    if language == "sap-cap":
        try:
            files = _generate_sap_cap(solution, config, gen.uml_snapshot or {})
        except Exception as _cap_err:
            logger.exception("SAP CAP generation failed for solution %s", solution_id)
            return jsonify({"error": f"SAP CAP generation failed: {_cap_err}"}), 500

        # Quality scoring on the CAP bundle
        quality_score, quality_details = _compute_quality_score(
            gen.uml_snapshot,
            files,
            business_rules=_biz_rules,
            seed_context=_seed_ctx,
        )

        gen.generated_files = files
        gen.quality_score = quality_score
        gen.quality_details = quality_details
        gen.version = (gen.version or 0) + 1
        gen.generated_at = datetime.utcnow()
        gen.config = {**config, "language": "sap-cap", "seed_context": _seed_ctx}
        db.session.commit()

        return jsonify({
            "success": True,
            "files": files,
            "quality_score": quality_score,
            "quality_details": quality_details or {},
            "errors": [],
            "syntax_warnings": [],
            "language": "sap-cap",
            "ui_framework": "none",
            "version": gen.version,
        })

    # ── Azure Bicep generation — dedicated Azure Resource Manager path ────────
    if language == "azure-bicep":
        try:
            files = _generate_azure_bicep(solution, config, gen.uml_snapshot or {})
        except Exception as _bicep_err:
            logger.exception("Azure Bicep generation failed for solution %s", solution_id)
            return jsonify({"error": f"Azure Bicep generation failed: {_bicep_err}"}), 500

        quality_score, quality_details = _compute_quality_score(
            gen.uml_snapshot,
            files,
            business_rules=_biz_rules,
            seed_context=_seed_ctx,
        )

        gen.generated_files = files
        gen.quality_score = quality_score
        gen.quality_details = quality_details
        gen.version = (gen.version or 0) + 1
        gen.generated_at = datetime.utcnow()
        gen.config = {**config, "language": "azure-bicep", "seed_context": _seed_ctx}
        db.session.commit()

        return jsonify({
            "success": True,
            "files": files,
            "quality_score": quality_score,
            "quality_details": quality_details or {},
            "errors": [],
            "syntax_warnings": [],
            "language": "azure-bicep",
            "ui_framework": "none",
            "version": gen.version,
        })

    # ── Power Platform Solution generation — dedicated pac CLI path ───────────
    if language == "power-platform-solution":
        try:
            files = _generate_power_platform_solution(solution, config, gen.uml_snapshot or {})
        except Exception as _pp_err:
            logger.exception("Power Platform generation failed for solution %s", solution_id)
            return jsonify({"error": f"Power Platform generation failed: {_pp_err}"}), 500

        quality_score, quality_details = _compute_quality_score(
            gen.uml_snapshot,
            files,
            business_rules=_biz_rules,
            seed_context=_seed_ctx,
        )

        gen.generated_files = files
        gen.quality_score = quality_score
        gen.quality_details = quality_details
        gen.version = (gen.version or 0) + 1
        gen.generated_at = datetime.utcnow()
        gen.config = {**config, "language": "power-platform-solution", "seed_context": _seed_ctx}
        db.session.commit()

        return jsonify({
            "success": True,
            "files": files,
            "quality_score": quality_score,
            "quality_details": quality_details or {},
            "errors": [],
            "syntax_warnings": [],
            "language": "power-platform-solution",
            "ui_framework": "none",
            "version": gen.version,
        })

    # ── SAP BTP Integration Suite generation — iFlow artefacts ───────────────
    if language == "sap-btp-integration":
        try:
            files = _generate_sap_btp_integration(solution, config, gen.uml_snapshot or {})
        except Exception as exc:
            logger.error("sap-btp-integration generation error sol=%d: %s", solution_id, exc)
            return jsonify({"success": False, "error": str(exc)}), 500
        gen.generated_files = files
        gen.version = (gen.version or 0) + 1
        gen.config = {**config, "language": "sap-btp-integration", "seed_context": _seed_ctx}
        from datetime import datetime as _dt_btp
        gen.generated_at = _dt_btp.utcnow()
        db.session.commit()
        return jsonify({
            "success": True,
            "files": files,
            "language": "sap-btp-integration",
            "ui_framework": "none",
            "file_count": len(files),
            "quality_score": None,
            "quality_details": {},
            "errors": [],
            "syntax_warnings": [],
            "version": gen.version,
        })

    # ── Azure Logic App generation — ARM workflow artefacts ───────────────────
    if language == "azure-logic-app":
        try:
            files = _generate_azure_logic_app(solution, config, gen.uml_snapshot or {})
        except Exception as exc:
            logger.error("azure-logic-app generation error sol=%d: %s", solution_id, exc)
            return jsonify({"success": False, "error": str(exc)}), 500
        gen.generated_files = files
        gen.version = (gen.version or 0) + 1
        gen.config = {**config, "language": "azure-logic-app", "seed_context": _seed_ctx}
        from datetime import datetime as _dt_ala
        gen.generated_at = _dt_ala.utcnow()
        db.session.commit()
        return jsonify({
            "success": True,
            "files": files,
            "language": "azure-logic-app",
            "ui_framework": "none",
            "file_count": len(files),
            "quality_score": None,
            "quality_details": {},
            "errors": [],
            "syntax_warnings": [],
            "version": gen.version,
        })

    if mode == "genome":
        # ── Genome-based generation — Architectural Genome pipeline ──
        # Bypasses UML synthesis entirely. Reads ArchiMate elements directly,
        # compiles an Architectural Genome IR, validates it, converts to
        # ProductSpecBundle, then feeds the same DeterministicCodeGenerator.
        #
        # Benefits: deterministic, $0 LLM cost, testable, richer state machine
        # and security config support via genome sections.
        #
        # D-PASS5-4: sentinel so the Alembic call below can use `genome is not None`
        # instead of the non-idiomatic "genome" in dir() check.
        genome = None
        if language not in SUPPORTED_LANGUAGES:
            return jsonify({"error": f"Unsupported language: {language}. Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"}), 400

        try:
            from app.modules.codegen.services.aabl_compiler import compile_genome
            from app.modules.codegen.services.genome_validator import validate_genome, compute_quality_score
            from app.modules.codegen.services.genome_to_bundle import genome_to_bundle

            # Build genome config from payload + stored config
            genome_config = {
                "auth": config.get("auth", "jwt_local"),
                "observability": config.get("observability", "opentelemetry"),
                "cache": config.get("cache", "none"),
                "search": config.get("search", "none"),
                "event_bus": config.get("event_bus", "none"),
                "mfa": config.get("mfa", payload.get("mfa", "none")),
                "api_keys": config.get("api_keys", payload.get("api_keys", False)),
                "encryption_at_rest": config.get("encryption_at_rest", payload.get("encryption_at_rest", False)),
                "multi_tenancy": config.get("multi_tenancy", payload.get("multi_tenancy", True)),
                "rate_limiting": config.get("rate_limiting") or payload.get("rate_limiting") or None,
                "deployment_target": config.get("deployment_target", payload.get("deployment_target", "docker_compose")),
                "environments": config.get("environments", payload.get("environments", ["staging", "production"])),
                "ci_cd_provider": config.get("ci_cd_provider", payload.get("ci_cd_provider", "github_actions")),
                "ci_cd_registry": config.get("ci_cd_registry", payload.get("ci_cd_registry", "ghcr")),
                "roles": config.get("roles", payload.get("roles", ["admin", "user", "viewer"])),
                "identity_provider": config.get("identity_provider", payload.get("identity_provider", {})),
            }
            # Pass mobile/compliance config if provided
            if payload.get("mobile") or config.get("mobile"):
                genome_config["mobile"] = payload.get("mobile") or config.get("mobile")
            if payload.get("compliance") or config.get("compliance"):
                genome_config["compliance"] = payload.get("compliance") or config.get("compliance")

            # Step 1: Use stored genome (from template apply) if valid, else compile from ArchiMate
            _stored_genome = gen.genome if gen else None
            if _stored_genome and isinstance(_stored_genome, dict) and _stored_genome.get("genome_version"):
                from app.modules.codegen.services.aabl_compiler import migrate_genome as _migrate_genome
                genome = _migrate_genome(_stored_genome)  # G17: upgrade old schema on load
                genome["language"] = language
                logger.info("Using stored genome for solution %s (version %s)", solution_id, genome.get("genome_version"))
            else:
                genome = compile_genome(solution_id, language=language, config=genome_config)

            # G4: invoke GenomePerfector on every codegen path — it was wired only to
            # the Step 6→7 UI transition and silently skipped for API and auto-promote.
            # Falls back gracefully when LLM is unavailable.
            _perfector_succeeded = False
            try:
                from app.modules.codegen.services.genome_perfector_service import GenomePerfectorService
                _perfector = GenomePerfectorService()
                # D-PASS4-2: populate context from the compiled genome so the perfector's
                # LLM prompt is architecture-specific, not generic boilerplate.
                _solution_context = {
                    "solution_name": solution.name,
                    "capabilities_summary": [
                        c["name"] for c in genome.get("capabilities", []) if isinstance(c, dict)
                    ],
                    "elements_summary": [
                        {"name": m, "type": v.get("archimate_type")}
                        for m, v in genome.get("modules", {}).items()
                    ],
                    "build_buy_summary": [
                        {"module": m, "decision": v.get("build_or_buy")}
                        for m, v in genome.get("modules", {}).items()
                        if v.get("build_or_buy")
                    ],
                }
                _perf_result = _perfector.perfect(solution_id, genome, _solution_context)
                if _perf_result.score_after > _perf_result.score_before:
                    genome = _perf_result.perfected_genome
                    logger.info(
                        "Genome perfected for solution %d: score %d → %d",
                        solution_id, _perf_result.score_before, _perf_result.score_after,
                    )
                # Only mark succeeded when LLM actually ran — if unavailable, stay False
                # so the quality gate below degrades to warn-only (D-PASS4-3)
                _perfector_succeeded = _perf_result.llm_ran
            except Exception as _perf_err:
                logger.warning("GenomePerfector failed, continuing with original genome: %s", _perf_err)

            # Step 2: Validate genome
            genome_errors = validate_genome(genome)
            if genome_errors:
                return jsonify({
                    "success": False,
                    "error": "Genome validation failed",
                    "validation_errors": genome_errors[:20],
                    "action": "fix_architecture",
                }), 422

            # PROG-014: architectural-conformance gate (AI-4) on the SOURCE solution.
            # Critical conformance breaches soft-block (overridable); non-critical
            # findings are surfaced as warnings by the streaming path the UI uses.
            _conf_block, _conf_summary = _evaluate_conformance_gate(solution_id, payload)
            if _conf_block is not None:
                return jsonify(_conf_block), 422

            # Step 3: Compute quality score
            genome_quality = compute_quality_score(genome)

            # G11: quality gate — a genome scoring below the minimum threshold generates
            # code with empty business logic. Returning 422 with specifics lets callers
            # fix the architectural issues before generating rather than after.
            # D-PASS4-3: only hard-block when the perfector actually ran and succeeded.
            # If LLM is unavailable, the genome is still a skeleton — gate as warn-only
            # to avoid blocking the wizard's primary path entirely.
            GENOME_QUALITY_MIN_THRESHOLD = 40
            _qual_score = genome_quality.get("total", 0)
            if _qual_score < GENOME_QUALITY_MIN_THRESHOLD:
                if _perfector_succeeded:
                    _qual_breakdown = {k: v for k, v in genome_quality.items() if k != "total"}
                    return jsonify({
                        "success": False,
                        "error": (
                            f"Genome quality score {_qual_score}/100 is below the minimum of "
                            f"{GENOME_QUALITY_MIN_THRESHOLD}. The GenomePerfector ran but could "
                            "not raise the score — enrich your ArchiMate architecture."
                        ),
                        "genome_quality": genome_quality,
                        "action": "improve_genome",
                        "hint": "Review the low-scoring dimensions: " + (", ".join(
                            k for k, v in _qual_breakdown.items()
                            if isinstance(v, (int, float)) and v < 5
                        ) or "enrich modules with entities, relationships, and business rules"),
                    }), 422
                else:
                    syntax_warnings.append(
                        f"Genome quality score {_qual_score}/100 is below {GENOME_QUALITY_MIN_THRESHOLD} "
                        "and the GenomePerfector could not run (LLM unavailable). "
                        "Generated code may have sparse business logic — review and re-generate when LLM is available."
                    )

            # Step 4: Convert genome to ProductSpecBundle
            bundle = genome_to_bundle(genome)
            bundle._genome_modules = genome.get("modules", {})
            bundle = _attach_salesforce_package_settings(bundle, effective_config)

            # Step 5: Generate code via DeterministicCodeGenerator
            from app.modules.solutions_product.services.deterministic_code_generator import DeterministicCodeGenerator
            template_set_id = effective_config.get("template_set_id") or None
            if template_set_id:
                try:
                    template_set_id = int(template_set_id)
                except (TypeError, ValueError):
                    template_set_id = None
            generator = DeterministicCodeGenerator(language=language)
            code_bundle = generator.generate(bundle, template_set_id=template_set_id, solution_id=solution_id)
            files = {f.path: f.content for f in code_bundle.files}
            _genome_prod_readiness = getattr(code_bundle, "production_readiness", None)

            # D-PASS5-2: post-generation pass — emit vendor SDK client stubs for buy/vendor
            # modules and remove any SQLAlchemy model files that were generated for them.
            files = _rewrite_vendor_sdk_files(files, genome.get("modules", {}))

            # Emit openapi.yaml
            if bundle.openapi:
                import json as _json_genome
                files["openapi.yaml"] = _json_genome.dumps(bundle.openapi, indent=2)

            # Emit the genome itself for traceability and drift detection
            import json as _json_genome2
            import yaml as _yaml_genome
            try:
                files["architectural_genome.yaml"] = _yaml_genome.dump(
                    genome, default_flow_style=False, sort_keys=False, allow_unicode=True,
                )
            except Exception:
                files["architectural_genome.json"] = _json_genome2.dumps(genome, indent=2, default=str)

            errors = []
            syntax_warnings = []

            # Set shared variables that downstream code expects
            _seed_ctx = {}
            _score_seed_ctx = {}
            _biz_rules = {}

            # G8: infer mobile need from solution name/description when the genome doesn't
            # have an explicit 'mobile' section. Silently skipping mobile for apps that are
            # obviously mobile-first (field service, delivery, consumer apps) is a critical gap.
            if not genome.get("mobile"):
                _mobile_keywords = {"mobile", "ios", "android", "app", "smartphone", "tablet", "native"}
                _sol_text = f"{solution.name} {solution.description or ''}".lower()
                if any(kw in _sol_text for kw in _mobile_keywords):
                    genome["mobile"] = {"inferred": True, "platforms": ["ios", "android"]}
                    syntax_warnings.append(
                        "Mobile section auto-inferred from solution name/description. "
                        "Add an explicit 'mobile' section to your genome to customise navigation and features."
                    )

            # Generate mobile app if genome has a mobile section
            if genome.get("mobile"):
                try:
                    from app.modules.codegen.services.mobile_generator import generate_mobile_from_genome
                    _mobile_ui = (payload.get("mobile_ui_framework") or (gen.config or {}).get("mobile_ui_framework", "nativewind"))
                    mobile_files = generate_mobile_from_genome(genome, mobile_ui_framework=_mobile_ui)
                    files.update(mobile_files)
                    logger.info(
                        "Mobile generation: %d files from genome mobile section",
                        len(mobile_files),
                    )
                    # G9: add a store-submission DEPLOYMENT.md so engineers know the manual
                    # steps that cannot be automated (APNs keys, FCM credentials, code signing).
                    files["mobile/DEPLOYMENT.md"] = (
                        "# Mobile App Store Deployment Guide\n\n"
                        "The following steps must be completed manually before submitting to "
                        "the Apple App Store or Google Play Store.\n\n"
                        "## iOS (Apple App Store)\n\n"
                        "1. **APNs Auth Key** — in Apple Developer portal, create a Push Notifications key "
                        "(`.p8` file). Set `EAS_APPLE_PUSH_KEY_PATH` in your CI secrets.\n"
                        "2. **Apple Team ID & Bundle ID** — update `eas.json` "
                        "`REPLACE_WITH_YOUR_APPLE_TEAM_ID` and `REPLACE_WITH_YOUR_APP_STORE_APP_ID`.\n"
                        "3. **Code signing** — run `eas credentials` and follow the prompts to "
                        "generate or upload a distribution certificate and provisioning profile.\n"
                        "4. **Submit** — `eas submit --platform ios --latest`\n\n"
                        "## Android (Google Play)\n\n"
                        "1. **FCM Server Key** — in Firebase Console → Project Settings → Cloud Messaging, "
                        "copy the server key. Set `FCM_SERVER_KEY` in your CI secrets.\n"
                        "2. **Google Service Account** — create a service account with the "
                        "`Release Manager` role, download the JSON key, and set it as "
                        "`GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` in CI secrets.\n"
                        "3. **Keystore** — `eas credentials --platform android`, then choose "
                        "'Set up a new keystore' or upload an existing one.\n"
                        "4. **Submit** — `eas submit --platform android --latest`\n\n"
                        "## Environment Variables Required\n\n"
                        "See `.env.example` in the project root for all required variables.\n"
                    )
                except Exception as _mob_err:
                    syntax_warnings.append(f"Mobile generation failed: {_mob_err}")
                    logger.warning("Mobile generation failed for solution %d: %s", solution_id, _mob_err)

            # Store genome quality for the response (overrides standard quality_score)
            _genome_quality_score = genome_quality["total"]
            _genome_quality_breakdown = genome_quality.get("breakdown", {})

            logger.info(
                "Genome-based generation completed for solution %d: %d files, quality=%d/100",
                solution_id, len(files), _genome_quality_score,
            )
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
        except Exception as e:
            logger.exception("Genome generation failed for solution %s", solution_id)
            return jsonify({"error": f"Genome generation failed: {str(e)}"}), 500

    elif mode in ("deterministic", "hybrid"):
        # ── Step 1: Deterministic generation — always runs first ──
        # Structural correctness guaranteed: field names, types, and relationships
        # come from the real schema via ProductSpecBundle, never from LLM memory.
        if language not in SUPPORTED_LANGUAGES:
            return jsonify({"error": f"Unsupported language: {language}. Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"}), 400

        try:
            bundle = _uml_to_product_spec_bundle(gen.uml_snapshot, effective_config, solution,
                                                  business_rules=_biz_rules)
            bundle = _attach_salesforce_package_settings(bundle, effective_config)

            # ── Enrich bundle with inferred state machines + decision models ──
            # These run AFTER _uml_to_product_spec_bundle so they can merge with any
            # manually confirmed entries without overwriting them.
            try:
                from app.modules.codegen.routes._helpers import (
                    _infer_state_machines_from_brief,
                    _extract_decision_models_from_brief,
                )
                _uml_classes = (gen.uml_snapshot or {}).get("class_diagram", {}).get("classes", [])
                # State machines: only infer for classes not already confirmed in the bundle
                _inferred_sms = _infer_state_machines_from_brief(solution_id, _uml_classes, _biz_rules)
                for _sm_entity, _sm_def in _inferred_sms.items():
                    if _sm_entity not in bundle.state_machines:
                        bundle.state_machines[_sm_entity] = _sm_def
                # Decision models: always infer and append (no duplicate check needed)
                _inferred_dms = _extract_decision_models_from_brief(solution_id, _uml_classes)
                if hasattr(bundle, "decision_models"):
                    bundle.decision_models.extend(_inferred_dms)
                else:
                    bundle.decision_models = _inferred_dms
            except Exception as _enrich_sm_err:
                logger.warning(
                    "State machine / decision model inference failed for solution %d: %s",
                    solution_id, _enrich_sm_err,
                )

            # ── Enrich bundle with DB-model gap closures ──────────────────────
            # SLA → k6, risks → resilience, compliance, RBAC, ADR, integrations, KPIs
            try:
                from app.modules.codegen.routes._helpers import (
                    _build_sla_load_config,
                    _build_resilience_config,
                    _build_compliance_config,
                    _build_rbac_config,
                    _read_adr_constraints,
                    _build_integration_clients,
                    _build_kpi_metrics_config,
                )
                _sla_cfg = _build_sla_load_config(solution_id, bundle.services)
                if _sla_cfg:
                    bundle.sla_load_config = _sla_cfg
                    # Also push into infra_context.slas so TestGenerator picks them up
                    if hasattr(bundle, "infra_context") and bundle.infra_context:
                        bundle.infra_context.slas = _sla_cfg.get("_raw_slas", bundle.infra_context.slas)

                _res_cfg = _build_resilience_config(solution_id)
                if _res_cfg:
                    bundle.resilience_config = _res_cfg

                _cmp_cfg = _build_compliance_config(solution_id)
                if _cmp_cfg:
                    bundle.compliance_config = _cmp_cfg
                    # Merge framework flags into _genome_compliance for existing template access
                    if not bundle._genome_compliance:
                        bundle._genome_compliance = {}
                    bundle._genome_compliance.update({
                        "gdpr": _cmp_cfg.get("gdpr", False),
                        "sox": _cmp_cfg.get("sox", False),
                        "hipaa": _cmp_cfg.get("hipaa", False),
                        "iso27001": _cmp_cfg.get("iso27001", False),
                        "pci_dss": _cmp_cfg.get("pci_dss", False),
                    })

                _rbac_cfg = _build_rbac_config(solution_id)
                if _rbac_cfg.get("has_rbac"):
                    bundle.rbac_config = _rbac_cfg
                    # Merge into identity_provider roles list
                    if not bundle.identity_provider:
                        bundle.identity_provider = {}
                    bundle.identity_provider.setdefault("roles", [])
                    existing_roles = {r.get("name") for r in bundle.identity_provider["roles"]}
                    for role in _rbac_cfg.get("roles", []):
                        if role["name"] not in existing_roles:
                            bundle.identity_provider["roles"].append(role)

                _adrs = _read_adr_constraints(solution_id)
                if _adrs:
                    bundle.adr_constraints = _adrs
                    # Apply database/messaging overrides from ADRs
                    for adr in _adrs:
                        if adr.get("override_key") and adr.get("override_value") and bundle.deployment:
                            parts = adr["override_key"].split(".")
                            if parts[0] == "deployment" and len(parts) == 2:
                                setattr(bundle.deployment, parts[1], adr["override_value"])

                _int_clients = _build_integration_clients(solution_id)
                if _int_clients:
                    bundle.integration_clients = _int_clients

                _kpi_metrics = _build_kpi_metrics_config(solution_id)
                if _kpi_metrics:
                    bundle.kpi_metrics = _kpi_metrics

            except Exception as _gap_err:
                logger.warning(
                    "Gap-closure DB enrichment failed for solution %d: %s",
                    solution_id, _gap_err,
                )
            template_set_id = effective_config.get("template_set_id") or None
            if template_set_id:
                try:
                    template_set_id = int(template_set_id)
                except (TypeError, ValueError):
                    return jsonify({"error": "Invalid template_set_id in config"}), 400
                ts = CodegenTemplateSet.query.get(template_set_id)
                if ts and ts.created_by_id and ts.created_by_id != current_user.id:
                    template_set_id = None
            # Import here too: the genome branch's import (above) makes this name a
            # function-local, so the deterministic branch must import it in its own scope.
            from app.modules.solutions_product.services.deterministic_code_generator import DeterministicCodeGenerator
            generator = DeterministicCodeGenerator(language=language)
            code_bundle = generator.generate(bundle, template_set_id=template_set_id)
            files = {f.path: f.content for f in code_bundle.files}
            _det_prod_readiness = getattr(code_bundle, "production_readiness", None)

            # Gap 1: Emit openapi.yaml— canonical OpenAPI 3.1 spec built from UML.
            # JSON is valid YAML 1.2, so no pyyaml dependency needed.
            if bundle.openapi:
                import json as _json
                files["openapi.yaml"] = _json.dumps(bundle.openapi, indent=2)

            errors = []
            syntax_warnings = []
        except Exception as e:
            logger.exception("Deterministic generation failed for solution %s", solution_id)
            return jsonify({"error": f"Generation failed: {str(e)}"}), 500

        # ── Step 2 (hybrid only): LLM enrichment of service + route files ──
        # Fills business logic stubs, enforces business rules, adds docstrings.
        # Falls back silently to deterministic output if LLM is unavailable.
        if mode == "hybrid":
            from app.modules.codegen.services.code_generation_service import CodeGenerationService
            from app.modules.codegen.services.uml_enrichment_service import UMLEnrichmentService as _UES

            if CodeGenerationService.check_daily_limit(solution_id):
                _sol_ctx = _UES._build_solution_context(solution_id)
                enrichment = CodeGenerationService.enrich_deterministic_output(
                    files, gen.uml_snapshot, config,
                    solution_context=_sol_ctx,
                    business_rules=_biz_rules,
                )
                files.update(enrichment["files"])  # enriched files overwrite scaffold
                errors.extend(enrichment["errors"])
                CodeGenerationService.increment_daily_count(gen)
            else:
                syntax_warnings.append(
                    "LLM enrichment skipped: daily generation limit reached. "
                    "Deterministic scaffold returned as-is."
                )

    else:
        # ── LLM-only path (legacy): 7 sequential prompts, slower ──
        from app.modules.codegen.services.code_generation_service import CodeGenerationService

        if not CodeGenerationService.check_daily_limit(solution_id):
            return jsonify({"error": "Daily generation limit reached. Try again tomorrow."}), 429

        result = CodeGenerationService.generate_all(gen.uml_snapshot, config, solution)
        files = result["files"]
        errors = result["errors"]
        syntax_warnings = result.get("syntax_warnings", [])

    # ── Documentation: README + DECISIONS + Alembic migration + Contract tests ──
    from app.modules.codegen.services.code_generation_service import _generate_readme
    try:
        files["README.md"] = _generate_readme(solution, gen.uml_snapshot or {}, config, files)
    except Exception as _e:
        logger.warning("README generation failed: %s", _e)
    try:
        files["DECISIONS.md"] = _generate_adr(solution, config, gen.version)
    except Exception as _e:
        logger.warning("ADR generation failed: %s", _e)
    try:
        files["IMPLEMENTATION_GUIDE.md"] = _generate_implementation_guide(solution, gen.uml_snapshot or {}, config)
    except Exception as _e:
        logger.warning("Implementation guide generation failed: %s", _e)
    try:
        _has_migration_version = any(
            k.startswith("alembic/versions/") and k.endswith(".py")
            for k in files
        )
        if not _has_migration_version:
            # D-PASS4-6: pass genome when available so in genome mode (uml_snapshot=None)
            # the migration schema is derived from genome modules, not an empty UML dict.
            _alembic_genome = genome if mode == "genome" and genome is not None else None
            files["alembic/versions/0001_initial.py"] = _generate_alembic_migration(
                gen.uml_snapshot or {}, genome=_alembic_genome
            )
        files.update(_generate_alembic_support_files(solution.name or "app"))
    except Exception as _e:
        logger.warning("Alembic migration generation failed: %s", _e)
    try:
        _openapi_for_contracts = {}
        # G14: genome mode excluded contract tests because bundle was not bound to the variable.
        # bundle is always set in genome mode via genome_to_bundle(); remove the mode guard
        # so contract tests are generated for every generation path.
        if bundle is not None:
            _openapi_for_contracts = getattr(bundle, "openapi", {}) or {}
        _contract_tests = _generate_contract_tests(_openapi_for_contracts, solution.name or "API")
        if _contract_tests:
            files["tests/contract/test_api_contract.py"] = _contract_tests
    except Exception as _e:
        logger.warning("Contract test generation failed: %s", _e)
    try:
        _inv_tests = _generate_architecture_invariant_tests(
            getattr(solution, "section_narratives", None) or {},
            config,
            solution.name or "Solution",
            business_rules=_biz_rules,
        )
        if _inv_tests:
            files["tests/architecture/test_invariants.py"] = _inv_tests
    except Exception as _e:
        logger.warning("Architecture invariant test generation failed: %s", _e)
    try:
        _peers = _build_peer_specs(solution_id)
        for _peer in _peers:
            _safe = re.sub(r'[^a-z0-9]', '_', _peer["solution_name"].lower()).strip('_')
            files[f"app/integrations/{_safe}_client.py"] = _generate_peer_client_stub(_peer)
    except Exception as _e:
        logger.warning("Peer client stub generation failed: %s", _e)
    # Inject deterministic conftest — overrides LLM-generated one which breaks at collection time
    files["tests/conftest.py"] = _generate_test_conftest(language)

    # ── Smoke test — verifies app boots and /health returns 200 ──────────────
    # Always generated; runs in-process via TestClient without a live server.
    files["tests/test_smoke.py"] = _generate_smoke_test()
    _seed_expectation_tests = _generate_seed_expectation_tests(_score_seed_ctx or _seed_ctx)
    if _seed_expectation_tests:
        files["tests/architecture/test_seed_expectations.py"] = _seed_expectation_tests

    # ── .env.example — G16/G7: document every required env var ─────────────
    # Generated apps crash silently when os.getenv() returns None. .env.example
    # makes it explicit which vars callers must provide, including integration secrets.
    try:
        import re as _re_env
        _env_vars_found = set()
        # Scan all generated Python files for os.getenv() and os.environ[] calls
        for _fpath, _fcontent in files.items():
            if _fpath.endswith(".py"):
                _env_vars_found.update(_re_env.findall(r'os\.getenv\(["\']([A-Z][A-Z0-9_]+)', _fcontent))
                _env_vars_found.update(_re_env.findall(r'os\.environ\[["\']([A-Z][A-Z0-9_]+)', _fcontent))
        # Always include the non-negotiable base vars
        _always_required = ["DATABASE_URL", "SECRET_KEY", "ENV", "CORS_ALLOWED_ORIGINS"]
        for _v in _always_required:
            _env_vars_found.add(_v)
        _env_example_lines = [
            "# .env.example — copy to .env and fill in real values before running.",
            "# Never commit .env to source control.",
            "",
        ]
        _defaults = {
            "DATABASE_URL": "sqlite+aiosqlite:///./app.db",
            "SECRET_KEY": "REPLACE_WITH_$(openssl rand -hex 32)",
            "ENV": "development",
            "CORS_ALLOWED_ORIGINS": "http://localhost:3000",
        }
        for _var in sorted(_env_vars_found):
            _default = _defaults.get(_var, "REPLACE_WITH_REAL_VALUE")
            _env_example_lines.append(f"{_var}={_default}")
        files[".env.example"] = "\n".join(_env_example_lines) + "\n"
    except Exception as _env_err:
        logger.warning(".env.example generation failed: %s", _env_err)

    # ── pydantic-settings config — startup validation of required env vars ───
    # Fails fast with a readable error if DATABASE_URL or SECRET_KEY is missing.
    # Only generated if not already present (DeterministicCodeGenerator may emit one).
    if "app/core/config.py" not in files:
        files["app/core/config.py"] = _generate_settings_config(config)
    # Wire the settings import into main.py so startup validation actually fires.
    # Without this import, app/core/config.py is dead code — settings = Settings()
    # is never called, missing env vars are never caught at startup.
    if "app/main.py" in files and "app.core.config" not in files["app/main.py"]:
        files["app/main.py"] = (
            "from app.core.config import settings  # noqa: F401  # validates env at startup\n"
            + files["app/main.py"]
        )

    # ── Validator unit tests ─────────────────────────────────────────────────
    if _biz_rules:
        _val_tests = _generate_validator_unit_tests(_biz_rules, solution.name or "Solution")
        if _val_tests:
            files["tests/unit/test_validators.py"] = _val_tests

    # ── Business rule contract tests (behavioral HTTP enforcement) ───────────
    if _biz_rules:
        try:
            _br_contract = _generate_business_rule_contract_tests(
                _biz_rules,
                _openapi_for_contracts,
                solution.name or "Solution",
            )
            if _br_contract:
                files["tests/contract/test_business_rules.py"] = _br_contract
        except Exception as _brc_err:
            logger.warning("Business rule contract test generation failed: %s", _brc_err)

    # ── Post-enrichment rule verification ───────────────────────────────────
    # Checks that MUST rules have _validate_*() functions in service/route files.
    # Injects deterministic stubs for any rule the LLM skipped — ensures enforcement
    # exists in EVERY generation, not just when the LLM cooperates.
    _missing_validators = []
    if _biz_rules:
        _missing_validators = _verify_enrichment_rule_coverage(files, _biz_rules)
        if _missing_validators:
            logger.info(
                "Post-enrichment: injected stub validators for %d rule(s) not generated by LLM: %s",
                len(_missing_validators), _missing_validators[:5],
            )

    # -- Per-entity test suite (deterministic) --------------------------------
    _ent = _generate_entity_tests(files)
    for _ep, _ec in _ent.items():
        if _ep not in files:
            files[_ep] = _ec

    # ── Format all Python output (PEP-8 normalisation, no external deps) ─────
    _format_python_files(files)

    # ── Remove conflicting frontend approaches ────────────────────────────────
    # When Next.js App Router files are generated, Jinja2 templates, static HTML,
    # and vanilla JS stubs must not coexist — the project won't build or deploy
    # with four frontend stacks. The Next.js stack wins; the others are removed.
    _has_nextjs = any(k.startswith("frontend/app/") for k in files)
    if _has_nextjs:
        _conflict_prefixes = ("templates/", "app/static/", "ui/static/")
        _conflict_exact = {"app/routers/pages.py"}
        # app/static/admin.html is the standalone admin panel — keep it even when
        # Next.js is the primary frontend stack; it is served as a separate tool.
        _KEEP_STATIC = {"app/static/admin.html"}
        _removed_conflicts = [
            k for k in list(files)
            if (any(k.startswith(p) for p in _conflict_prefixes) or k in _conflict_exact)
            and k not in _KEEP_STATIC
        ]
        for _cf in _removed_conflicts:
            del files[_cf]
        if _removed_conflicts:
            logger.info(
                "Frontend conflict resolution: removed %d Jinja2/static files "
                "(Next.js frontend present): %s",
                len(_removed_conflicts),
                _removed_conflicts[:5],
            )

    # ── Canonicalize route directories ───────────────────────────────────────
    # LLM prompts instruct app/routers/ but the model sometimes writes app/routes/.
    # When both dirs coexist the router won't import from both; half the routes 404.
    # Merge app/routes/ into app/routers/, skipping files already defined there.
    _routes_files = {k: v for k, v in files.items() if k.startswith("app/routes/")}
    _routers_keys = {k for k in files if k.startswith("app/routers/")}
    if _routes_files and _routers_keys:
        _migrated_count = 0
        for _old_path, _content in list(_routes_files.items()):
            _new_path = "app/routers/" + _old_path[len("app/routes/"):]
            if _new_path not in files:
                files[_new_path] = _content
                _migrated_count += 1
            del files[_old_path]
        if _migrated_count:
            logger.info(
                "Route directory canonicalization: merged %d file(s) app/routes/ → app/routers/",
                _migrated_count,
            )

    # Quality scoring — includes domain fidelity from seed/adapters
    _score_seed_ctx = getattr(bundle, "seed_context", None) if mode in ("deterministic", "hybrid") else None
    quality_score, quality_details = _compute_quality_score(
        gen.uml_snapshot,
        files,
        business_rules=_biz_rules,
        seed_context=_score_seed_ctx or _seed_ctx,
    )

    # Blueprint constraint validation — section narratives + motivation-layer business rules
    _section_narratives = getattr(solution, "section_narratives", None) or {}
    blueprint_violations = _validate_blueprint_constraints(files, _section_narratives, config, business_rules=_biz_rules)
    if blueprint_violations and quality_details:
        quality_details["blueprint_violations"] = blueprint_violations
        # Deduct from quality score: errors -5pts each, warnings -2pts each
        _deduction = sum(5 if v["severity"] == "error" else 2 for v in blueprint_violations)
        quality_score = max(0, (quality_score or 0) - _deduction)

    # Genome mode: override quality score with genome-specific score
    if mode == "genome" and "_genome_quality_score" in dir():
        quality_score = _genome_quality_score
        if quality_details is None:
            quality_details = {}
        quality_details["genome_breakdown"] = _genome_quality_breakdown

    # GAP-10: Scan generated code for secrets before saving (replaces hardcoded values in-place)
    secret_warnings = _scan_for_secrets(files)

    # Security hardening check (#8) — auth presence, CORS, debug mode
    _security_result = _check_security_hardening(files)
    if quality_details is not None:
        quality_details["security"] = _security_result
    if _security_result["issues"]:
        for _si in _security_result["issues"]:
            syntax_warnings.append(f"security: {_si['issue']} [{_si['file']}]")

    # Linting check (#7) — PEP-8 E501/W29x/E711 violations in generated Python
    _lint_result = _lint_python_files(files)
    if quality_details is not None:
        quality_details["lint"] = _lint_result

    # Gap 2: Cross-file import graph validation — catches broken inter-module references
    import_graph_issues = _validate_import_graph(files)
    if import_graph_issues:
        for issue in import_graph_issues:
            syntax_warnings.append(
                f"{issue['file']}: broken import — {issue['issue']} ('{issue['import_stmt']}')"
            )
    smoke_issues = _run_runtime_smoke_checks(files, language, seed_context=_score_seed_ctx or _seed_ctx)
    for err in smoke_issues.get("errors", []):
        syntax_warnings.append(f"runtime-smoke-error: {err}")
    for warn in smoke_issues.get("warnings", []):
        syntax_warnings.append(f"runtime-smoke-warning: {warn}")
    for miss in smoke_issues.get("seed_expectation_issues", []):
        syntax_warnings.append(f"seed-expectation-missing: {miss}")
    if quality_details is not None:
        quality_details["runtime_smoke"] = smoke_issues

    # ── Quality gate (Task #10) ───────────────────────────────────────────────
    # Block save when quality dimensions fall below minimum thresholds.
    # Active by default for production-ready policy; opt-in via payload flag.
    _enforce_quality_gate = _as_bool(
        payload.get("enforce_quality_gate"),
        default=(generation_policy in ("production-ready", "quality-gated")),
    )
    if _enforce_quality_gate:
        _qg_failures = []
        try:
            _min_qs = float(payload.get("min_quality_score", 80))
            _min_cc = float(payload.get("min_chain_completeness", 0.95))
        except (ValueError, TypeError):
            return jsonify({"error": "min_quality_score and min_chain_completeness must be numbers"}), 400
        if (quality_score or 0) < _min_qs:
            _qg_failures.append({
                "dimension": "quality_score",
                "actual": quality_score,
                "required": _min_qs,
                "message": f"Quality score {quality_score:.1f} below minimum {_min_qs:.0f}",
            })
        if completeness is not None and completeness < _min_cc:
            _qg_failures.append({
                "dimension": "chain_completeness",
                "actual": round(completeness, 3),
                "required": _min_cc,
                "message": (
                    f"Chain completeness {completeness * 100:.0f}% "
                    f"below minimum {_min_cc * 100:.0f}%"
                ),
            })
        if smoke_issues.get("errors"):
            _qg_failures.append({
                "dimension": "runtime_smoke",
                "actual": len(smoke_issues["errors"]),
                "required": 0,
                "message": f"{len(smoke_issues['errors'])} runtime smoke error(s) in generated code",
                "errors": smoke_issues["errors"][:5],
            })
        # Block on hardened security issues: missing auth or private key exposure
        _sec_critical = [
            i for i in _security_result.get("issues", [])
            if "No auth" in i["issue"] or "Private key" in i["issue"] or "AWS" in i["issue"]
        ]
        if _sec_critical:
            _qg_failures.append({
                "dimension": "security",
                "actual": len(_sec_critical),
                "required": 0,
                "message": f"{len(_sec_critical)} critical security issue(s) in generated code",
                "issues": [i["issue"] for i in _sec_critical[:3]],
            })
        # Lint: block only on excessive violations (>50 E501 is a template bug, not style preference)
        try:
            _max_lint = int(payload.get("max_lint_violations", 50))
        except (ValueError, TypeError):
            return jsonify({"error": "max_lint_violations must be an integer"}), 400
        if _lint_result.get("e501_count", 0) > _max_lint:
            _qg_failures.append({
                "dimension": "lint",
                "actual": _lint_result["e501_count"],
                "required": _max_lint,
                "message": (
                    f"{_lint_result['e501_count']} lines exceed {_lint_result.get('max_line_length', 88)} "
                    f"chars (E501) — template likely producing excessively long lines"
                ),
            })
        if _qg_failures:
            return jsonify({
                "success": False,
                "error": "Generation blocked by quality gate.",
                "gate_failures": _qg_failures,
                "policy": generation_policy,
                "quality_score": quality_score,
                "quality_details": quality_details,
                "chain_completeness": completeness,
            }), 422

    # GAP-04: Compute regeneration impact preview against last history
    impact = None
    last_history = CodegenGenerationHistory.query.filter_by(
        codegen_generation_id=gen.id
    ).order_by(CodegenGenerationHistory.generated_at.desc()).first()

    if last_history and last_history.file_manifest:
        old_paths = {f["path"] for f in last_history.file_manifest}
        new_paths = set(files.keys())
        old_hashes = {f["path"]: f["hash"] for f in last_history.file_manifest}
        changed = [
            p for p in new_paths & old_paths
            if hashlib.sha256(files[p].encode()).hexdigest()[:12] != old_hashes.get(p)
        ]
        impact = {
            "added": sorted(new_paths - old_paths),
            "removed": sorted(old_paths - new_paths),
            "changed": sorted(changed),
        }

    # ── Frontend generation ───────────────────────────────────────────────────
    # Payload override takes precedence (journey wizard passes ui_framework directly)
    ui_framework = payload.get("ui_framework") or (gen.config or {}).get("ui_framework", "none")
    if ui_framework == "refine-antd" or (gen.config or {}).get("include_frontend"):
        # Legacy / Refine path — kept for backwards compatibility
        frontend_files = _generate_refine_frontend(
            solution.name or f"Solution {solution_id}",
            gen.uml_snapshot or {},
        )
        files.update(frontend_files)
    elif ui_framework == "shadcn-nextjs" and mode not in ("deterministic", "hybrid", "genome"):
        # Skip when DeterministicCodeGenerator already handled the frontend via
        # nextjs_shadcn templates (root-level frontend/ structure). The old
        # _generate_shadcn_frontend helper writes to frontend/src/ (react_shadcn
        # templates), which conflicts because Next.js prefers src/app/ over app/.
        frontend_files = _generate_shadcn_frontend(
            solution.name or f"Solution {solution_id}",
            gen.uml_snapshot or {},
            gen.config or {},
        )
        files.update(frontend_files)

    # Mobile generation (opt-in, independent of ui_framework)
    mobile_framework = payload.get("mobile_framework") or (gen.config or {}).get("mobile_framework", "none")
    if mobile_framework == "expo-react-native":
        _mob_ui = payload.get("mobile_ui_framework") or (gen.config or {}).get("mobile_ui_framework", "nativewind")
        mobile_files = _generate_expo_mobile(
            solution.name or f"Solution {solution_id}",
            gen.uml_snapshot or {},
            {**(gen.config or {}), "mobile_ui_framework": _mob_ui},
        )
        files.update(mobile_files)

    placeholder_findings = _find_placeholder_stubs(files)
    if strict_production:
        blocking_issues = []
        if placeholder_findings and block_placeholder_stubs:
            blocking_issues.append({
                "type": "placeholder_stubs",
                "count": len(placeholder_findings),
                "examples": placeholder_findings[:10],
            })
        if "tests/architecture/test_invariants.py" not in files:
            blocking_issues.append({
                "type": "missing_invariant_tests",
                "message": "Generated bundle is missing tests/architecture/test_invariants.py",
            })
        if _missing_validators:
            blocking_issues.append({
                "type": "missing_business_rule_validators",
                "count": len(_missing_validators),
                "rules": _missing_validators[:10],
            })
        if blueprint_violations:
            _error_violations = [v for v in blueprint_violations if v.get("severity") == "error"]
            if _error_violations:
                blocking_issues.append({
                    "type": "blueprint_constraint_errors",
                    "count": len(_error_violations),
                    "violations": _error_violations[:10],
                })
        if smoke_issues.get("errors"):
            blocking_issues.append({
                "type": "runtime_smoke_errors",
                "count": len(smoke_issues["errors"]),
                "errors": smoke_issues["errors"][:10],
            })
        if blocking_issues:
            return jsonify({
                "success": False,
                "error": "Production-ready generation blocked by policy gates.",
                "policy": generation_policy,
                "blocking_issues": blocking_issues,
                "quality_score": quality_score,
                "quality_details": quality_details,
            }), 422

    # Detect drift: warn if previously generated files were modified externally
    # (e.g., developer edited generated code, then someone re-generates from wizard)
    _drift_warnings = []
    if gen.generated_files and gen.config and gen.config.get("file_manifest"):
        _prev_manifest = {m["path"]: m["hash"] for m in gen.config["file_manifest"]}
        for path, content in (gen.generated_files or {}).items():
            if path in _prev_manifest:
                _cur_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
                if _cur_hash != _prev_manifest[path]:
                    _drift_warnings.append(path)
        if _drift_warnings:
            logger.warning(
                "Regeneration drift: %d files modified since last generation for solution %s: %s",
                len(_drift_warnings), solution_id, _drift_warnings[:10],
            )
            if "syntax_warnings" not in dir():
                syntax_warnings = []
            syntax_warnings.append(
                f"Warning: {len(_drift_warnings)} file(s) were modified since last generation "
                f"and will be overwritten: {', '.join(_drift_warnings[:5])}"
                + (f" (+{len(_drift_warnings) - 5} more)" if len(_drift_warnings) > 5 else "")
            )

    # Merge language-namespaced outputs so frontend/ and mobile/ coexist alongside
    # the primary API files. Without merging, generating react-shadcn wipes mobile/
    # and vice-versa — breaking Expo Snack and StackBlitz previews.
    _LANG_PREFIX = {"react-shadcn": "frontend/", "flask-nextjs": "frontend/", "flask-react": "frontend/", "react-native-expo": "mobile/"}
    _new_prefix = _LANG_PREFIX.get(language)
    if _new_prefix and gen.generated_files:
        # Keep all files from other namespaces; replace only this language's namespace
        merged = {k: v for k, v in gen.generated_files.items() if not k.startswith(_new_prefix)}
        merged.update(files)
        gen.generated_files = merged
    else:
        gen.generated_files = files
    _cfg_out = dict(gen.config or {})
    _cfg_out["seed_context"] = _score_seed_ctx or _seed_ctx
    if mode in ("deterministic", "hybrid") and bundle is not None:
        _cfg_out["provenance"] = getattr(bundle, "provenance", {})
    gen.config = _cfg_out
    gen.version += 1

    # Persist compiled genome so the DB column reflects what was used to generate code.
    # The sync path compiles the genome in-memory but previously never saved it, leaving
    # codegen_generations.genome as {} for all sync-generated solutions.
    if mode == "genome" and "genome" in dir():
        try:
            gen.genome = genome  # type: ignore[name-defined]
            if "_genome_quality_score" in dir():
                gen.genome_quality_score = _genome_quality_score  # type: ignore[name-defined]
        except Exception as _gsave_err:
            logger.warning("Could not persist genome to codegen_generations: %s", _gsave_err)

    # Persist Layer 2 traceability — stop discarding archimate_sources from GeneratedFile
    if mode in ("deterministic", "hybrid", "genome") and code_bundle is not None:
        from app.modules.codegen.models import persist_traceability
        persist_traceability(
            solution_id, code_bundle,
            spec_hash=getattr(bundle, "spec_hash", None),
        )

    if mode == "llm":
        # hybrid already incremented inside its own block above
        from app.modules.codegen.services.code_generation_service import CodeGenerationService
        CodeGenerationService.increment_daily_count(gen)

    if not errors:
        from datetime import datetime as _dt_gen
        gen.completed_at = _dt_gen.utcnow()

    # GAP-04: Record generation history
    manifest = [
        {"path": path, "hash": hashlib.sha256(content.encode()).hexdigest()[:12]}
        for path, content in files.items()
    ]
    history_count = CodegenGenerationHistory.query.filter_by(
        codegen_generation_id=gen.id
    ).count()
    version_label = f"1.{history_count}.0"

    history = CodegenGenerationHistory(
        codegen_generation_id=gen.id,
        generated_by_id=current_user.id,
        language=language,
        mode=mode,
        file_count=len(files),
        chain_completeness_score=completeness,
        version_label=version_label,
        file_manifest=manifest,
    )
    # Store quality score gracefully — columns may not exist yet on older DB instances
    try:
        history.quality_score = quality_score
        history.quality_details = quality_details
    except Exception as _qs_exc:
        logger.debug("Could not store quality score on history record: %s", _qs_exc)
    db.session.add(history)
    db.session.commit()

    # Extract service summary from generated files for the UI services panel
    _services_summary = []
    _svc_route_map = {}  # service_name -> count of route files
    for _fp in files:
        if _fp.startswith("app/services/") and _fp.endswith(".py") and _fp != "app/services/__init__.py":
            _svc_name = _fp.replace("app/services/", "").replace(".py", "").replace("_service", "").replace("_", " ").title()
            _svc_route_map.setdefault(_svc_name, {"name": _svc_name, "path_count": 0})
        elif _fp.startswith("app/api/routes/") and _fp.endswith(".py") and _fp != "app/api/routes/__init__.py":
            _route_name = _fp.replace("app/api/routes/", "").replace(".py", "").replace("_routes", "").replace("_", " ").title()
            _svc_route_map.setdefault(_route_name, {"name": _route_name, "path_count": 0})
            _svc_route_map[_route_name]["path_count"] += 1
    # Count route files that match service names
    for _fp in files:
        if _fp.startswith("app/api/routes/") and _fp.endswith(".py"):
            for _svc_key in _svc_route_map:
                if _svc_key.lower().replace(" ", "_") in _fp.lower():
                    _svc_route_map[_svc_key]["path_count"] = max(_svc_route_map[_svc_key]["path_count"], 1)
    _services_summary = sorted(_svc_route_map.values(), key=lambda s: s["name"])

    response = {
        "success": True,
        "file_count": len(files),
        "files": list(files.keys()),
        "errors": errors,
        "version": gen.version,
        "version_label": version_label,
        "mode": mode,
        "language": language,
        "chain_completeness": completeness,
        "quality_score": quality_score,
        "quality_details": quality_details,
        "generation_policy": generation_policy,
        "services": _services_summary,
    }
    # Attach production readiness score when available (genome or deterministic path)
    _prod_readiness = (
        getattr(code_bundle, "production_readiness", None)
        if code_bundle is not None else None
    )
    if _prod_readiness is not None and hasattr(_prod_readiness, "to_dict"):
        response["production_readiness"] = _prod_readiness.to_dict()
    if impact:
        response["impact"] = impact
    if secret_warnings:
        response["secret_warnings"] = secret_warnings
    if syntax_warnings:
        response["syntax_warnings"] = syntax_warnings
    if placeholder_findings:
        response["placeholder_warnings"] = placeholder_findings[:20]
    if completeness is not None and completeness < 0.7 and mode in ("deterministic", "hybrid") and not enforce_chain_complete:
        response["completeness_warning"] = (
            f"Architecture chains are {completeness * 100:.0f}% complete. "
            "Generated contracts may have structural gaps."
        )

    # GAP-09: Notify technical lead if set
    _notify_tech_lead(solution, len(files), language, completeness)

    # COM-018: Auto-push to DevOps if the org has an enabled connector config
    try:
        _org_id = getattr(solution, "organization_id", None)
        if _org_id:
            from app.services.devops_push_service import DevOpsPushService
            _devops_svc = DevOpsPushService()
            _devops_cfg = _devops_svc._get_config(_org_id)
            if _devops_cfg and _devops_cfg.enabled:
                import re as _slug_re
                _slug = _slug_re.sub(r"[^a-z0-9]", "-", solution.name.lower())[:40].strip("-")
                _push_res = _devops_svc.push(_org_id, solution_id, _slug, files)
                if _push_res.get("pr_url"):
                    response["pr_url"] = _push_res["pr_url"]
                    response["devops_branch"] = _push_res.get("branch")
    except Exception as _push_exc:
        logger.warning("DevOps auto-push failed for solution %s: %s", solution_id, _push_exc)

    return jsonify(response)


@codegen_bp.route("/solutions/<int:solution_id>/codegen/generate-stream", methods=["POST"])
@login_required
@require_csrf
def generate_stream(solution_id):
    """Streaming SSE variant of generate_code — yields phase events as JSON so the UI can
    show real progress instead of a cosmetic timer animation.

    Each event: data: {"phase": str, "status": "running"|"done"|"error", ...}
    Final event: data: {"phase": "complete", "result": {<full generate_code response>}}
    """
    from flask import Response, stream_with_context
    from app.modules.solutions_product.services.deterministic_code_generator import DeterministicCodeGenerator

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"error": "Run Phase 1 (Enrich) first"}), 400

    # Version conflict check BEFORE any mutations (auto-synthesis bumps version)
    if payload.get("version") and payload["version"] != gen.version:
        return jsonify({"error": "Version conflict. Refresh the page.", "version": gen.version}), 409

    # Auto-synthesize UML from ArchiMate elements for deterministic/genome mode (matches /generate behaviour)
    # Enrich element descriptions from the brief BEFORE synthesis so field inference has full context.
    try:
        _enrich_solution_elements_from_brief(solution_id)
    except Exception as _enrich_err:
        logger.warning("Brief enrichment failed for solution %d (stream): %s", solution_id, _enrich_err)

    # Extract business rules from the brief so _get_solution_business_rules() has data.
    try:
        _extract_business_rules_from_brief(solution_id)
    except Exception as _rule_err:
        logger.warning("Brief rule extraction failed for solution %d (stream): %s", solution_id, _rule_err)

    if not gen.uml_snapshot:
        _req_mode = payload.get("generation_mode") or (gen.config or {}).get("generation_mode", "deterministic")
        if _req_mode in ("deterministic", "hybrid", "genome"):
            _synth_uml = _synthesize_uml_from_elements(solution_id)
            if _synth_uml:
                gen.uml_snapshot = _synth_uml
                gen.version = (gen.version or 0) + 1
                db.session.commit()
                logger.info("Synthesized UML from ArchiMate elements for solution %d (stream)", solution_id)
            else:
                return jsonify({"error": "No ArchiMate elements found. Link elements or run Phase 1 first."}), 400
        else:
            return jsonify({"error": "Run Phase 1 (Enrich) first"}), 400

    config = gen.config or {"auth": "none", "python_version": "3.12"}
    # genome is the default: AABL compiler → deterministic code, no LLM variability in output.
    mode = payload.get("generation_mode") or config.get("generation_mode", "genome")
    language = payload.get("language") or config.get("language", "python-fastapi")
    _stream_ui_framework = payload.get("ui_framework") or config.get("ui_framework", "none")
    generation_policy = payload.get("generation_policy") or config.get("generation_policy", "scaffold")
    effective_config = _normalize_salesforce_package_config({**config, "language": language}, payload)
    strict_production = generation_policy == "production-ready"
    enforce_chain_complete = _as_bool(
        payload.get("enforce_chain_complete"),
        default=(mode == "llm" or strict_production),
    )
    require_confirmed_specs = _as_bool(
        payload.get("require_confirmed_specs"),
        default=strict_production,
    )
    block_placeholder_stubs = _as_bool(
        payload.get("block_placeholder_stubs"),
        default=strict_production,
    )

    completeness = _compute_chain_completeness(solution_id)
    if enforce_chain_complete:
        if completeness is None:
            return jsonify({
                "success": False,
                "error": (
                    "Chain completeness could not be computed — "
                    "link ArchiMate elements to this solution before generating."
                ),
                "chain_completeness": None,
                "action": "repair_chains",
                "policy": generation_policy,
            }), 422
        if completeness < 0.7:
            return jsonify({
                "success": False,
                "error": (
                    f"Architecture chains are {completeness * 100:.0f}% complete — "
                    "minimum 70% required before generation."
                ),
                "chain_completeness": completeness,
                "action": "repair_chains",
                "policy": generation_policy,
            }), 422

    if require_confirmed_specs:
        total_specs, confirmed_specs, missing_specs = _collect_spec_confirmation_counts(solution_id, gen.uml_snapshot or {})
        if total_specs > 0 and confirmed_specs < total_specs:
            return jsonify({
                "success": False,
                "error": (
                    "Production-ready generation requires confirmed field specs. "
                    f"Confirmed {confirmed_specs}/{total_specs} classes."
                ),
                "action": "confirm_specs",
                "missing_specs": missing_specs[:20],
                "policy": generation_policy,
            }), 422

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    @stream_with_context
    def stream():
        files = {}
        errors = []
        syntax_warnings = []
        bundle = None
        _missing = []
        # Re-query solution inside the stream — the outer ORM object becomes detached
        # once the streaming generator starts executing (session scope mismatch).
        _stream_solution = Solution.query.get(solution_id) or solution
        # Capture lazy attributes NOW, while the re-fetched solution is session-bound.
        # Reading solution.section_narratives later (mid-stream) hit DetachedInstanceError
        # once the session expired, breaking the SSE stream (frontend 'network error').
        _stream_section_narratives = getattr(_stream_solution, "section_narratives", None) or {}

        # ── Phase: deterministic scaffold ────────────────────────────────────
        yield _sse({"phase": "deterministic", "status": "running", "label": "Building scaffold…"})
        try:
            if language not in SUPPORTED_LANGUAGES:
                yield _sse({"phase": "deterministic", "status": "error",
                            "error": f"Unsupported language: {language}"})
                yield _sse({"phase": "complete", "success": False, "error": f"Unsupported language: {language}"})
                return
            _stream_biz = _get_solution_business_rules(solution_id)
            _genome_completeness = None  # fallback for chain health in genome mode

            if mode == "genome":
                # ── Genome pipeline ─────────────────────────────────────────
                # Bypasses UML entirely: compile Architectural Genome IR from
                # ArchiMate elements, validate, convert to ProductSpecBundle.
                from app.modules.codegen.services.aabl_compiler import compile_genome
                from app.modules.codegen.services.genome_validator import (
                    validate_genome, compute_quality_score,
                )
                from app.modules.codegen.services.genome_to_bundle import genome_to_bundle

                genome_config = {
                    "auth": config.get("auth", "jwt_local"),
                    "observability": config.get("observability", "opentelemetry"),
                    "cache": config.get("cache", "none"),
                    "search": config.get("search", "none"),
                    "event_bus": config.get("event_bus", "none"),
                    "mfa": config.get("mfa", payload.get("mfa", "none")),
                    "api_keys": config.get("api_keys", payload.get("api_keys", False)),
                    "encryption_at_rest": config.get("encryption_at_rest", payload.get("encryption_at_rest", False)),
                    "multi_tenancy": config.get("multi_tenancy", payload.get("multi_tenancy", True)),
                    "rate_limiting": config.get("rate_limiting") or payload.get("rate_limiting") or None,
                    "deployment_target": config.get("deployment_target", payload.get("deployment_target", "docker_compose")),
                    "environments": config.get("environments", payload.get("environments", ["staging", "production"])),
                    "ci_cd_provider": config.get("ci_cd_provider", payload.get("ci_cd_provider", "github_actions")),
                    "ci_cd_registry": config.get("ci_cd_registry", payload.get("ci_cd_registry", "ghcr")),
                    "roles": config.get("roles", payload.get("roles", ["admin", "user", "viewer"])),
                    "identity_provider": config.get("identity_provider", payload.get("identity_provider", {})),
                }
                if payload.get("mobile") or config.get("mobile"):
                    genome_config["mobile"] = payload.get("mobile") or config.get("mobile")
                if payload.get("compliance") or config.get("compliance"):
                    genome_config["compliance"] = payload.get("compliance") or config.get("compliance")

                # Use stored genome (applied from template) if valid, else compile fresh
                _stored_genome = gen.genome if gen else None
                if _stored_genome and isinstance(_stored_genome, dict) and _stored_genome.get("genome_version"):
                    from app.modules.codegen.services.aabl_compiler import migrate_genome as _migrate_genome_s
                    genome = _migrate_genome_s(_stored_genome)  # G17: upgrade old schema on load
                    genome["language"] = language
                    logger.info("Stream: using stored genome for solution %s (v%s)",
                                solution_id, genome.get("genome_version"))
                else:
                    genome = compile_genome(solution_id, language=language, config=genome_config)

                # G4: invoke GenomePerfector in the SSE path too — previously only
                # called from the UI Step 6→7 transition, never from the streaming API.
                # D-PASS4-2: populate context from the genome, not empty lists.
                _stream_perfector_succeeded = False
                try:
                    from app.modules.codegen.services.genome_perfector_service import GenomePerfectorService
                    _stream_perfector = GenomePerfectorService()
                    _stream_sol_ctx = {
                        "solution_name": _stream_solution.name,
                        "capabilities_summary": [
                            c["name"] for c in genome.get("capabilities", []) if isinstance(c, dict)
                        ],
                        "elements_summary": [
                            {"name": m, "type": v.get("archimate_type")}
                            for m, v in genome.get("modules", {}).items()
                        ],
                        "build_buy_summary": [
                            {"module": m, "decision": v.get("build_or_buy")}
                            for m, v in genome.get("modules", {}).items()
                            if v.get("build_or_buy")
                        ],
                    }
                    _stream_perf = _stream_perfector.perfect(solution_id, genome, _stream_sol_ctx)
                    if _stream_perf.score_after > _stream_perf.score_before:
                        genome = _stream_perf.perfected_genome
                    # Only mark succeeded when LLM actually ran (D-PASS4-3 / D-PASS5-3)
                    _stream_perfector_succeeded = _stream_perf.llm_ran
                except Exception as _sp_err:
                    logger.warning("Stream GenomePerfector failed: %s", _sp_err)

                genome_errors = validate_genome(genome)
                if genome_errors:
                    msg = "Genome validation failed: " + "; ".join(genome_errors[:5])
                    yield _sse({"phase": "deterministic", "status": "error", "error": msg})
                    yield _sse({"phase": "complete", "result": {"success": False, "error": msg}})
                    return

                # PROG-014: architectural-conformance gate (AI-4) on the SOURCE solution.
                # Critical breaches soft-block (overridable via override_conformance);
                # high/info findings warn but let generation proceed.
                _cf_block, _cf_summary = _evaluate_conformance_gate(solution_id, payload)
                if _cf_block is not None:
                    yield _sse({"phase": "conformance", "status": "error",
                                "error": _cf_block["error"], "conformance": _cf_block["conformance"],
                                "action": "review_conformance",
                                "review_packet_url": _cf_block["review_packet_url"]})
                    yield _sse({"phase": "complete", "result": _cf_block})
                    return
                if _cf_summary and _cf_summary.get("flagged"):
                    yield _sse({"phase": "conformance", "status": "warning",
                                "score": _cf_summary["score"], "flagged": _cf_summary["flagged"],
                                "conformance": _cf_summary,
                                "message": (
                                    f"{_cf_summary['flagged']} architecture-conformance "
                                    f"finding(s) need attention (score {_cf_summary['score']}/100)."
                                )})

                # D-PASS5-3: enforce quality gate in SSE path (mirrors sync path gate).
                # _stream_perfector_succeeded was tracked above but never compared — fix that.
                _stream_gqs = compute_quality_score(genome)
                _stream_qual_score = (
                    _stream_gqs.get("total", 0) if isinstance(_stream_gqs, dict)
                    else int(_stream_gqs * 100)
                )
                _STREAM_QUALITY_MIN = 40
                if _stream_qual_score < _STREAM_QUALITY_MIN:
                    if _stream_perfector_succeeded:
                        _qmsg = (
                            f"Genome quality {_stream_qual_score}/100 is below "
                            f"the minimum required score of {_STREAM_QUALITY_MIN}. "
                            "Add more ArchiMate elements or enrich the solution spec."
                        )
                        yield _sse({"phase": "deterministic", "status": "error", "error": _qmsg,
                                    "genome_quality": _stream_gqs})
                        yield _sse({"phase": "complete", "result": {"success": False, "error": _qmsg,
                                                                      "genome_quality": _stream_gqs}})
                        return
                    else:
                        # LLM unavailable — warn but continue so wizard users aren't hard-blocked.
                        errors.append({
                            "prompt": "genome_quality",
                            "error": f"Quality {_stream_qual_score}/100 below {_STREAM_QUALITY_MIN} (perfector unavailable — gate bypassed)",
                        })
                        yield _sse({"phase": "quality_warning", "score": _stream_qual_score,
                                    "threshold": _STREAM_QUALITY_MIN, "perfector_available": False,
                                    "message": "Quality gate bypassed: perfector LLM unavailable"})

                bundle = genome_to_bundle(genome)
                bundle._genome_modules = genome.get("modules", {})
                bundle = _attach_salesforce_package_settings(bundle, effective_config)
                template_set_id = effective_config.get("template_set_id") or None
                if template_set_id:
                    try:
                        template_set_id = int(template_set_id)
                    except (TypeError, ValueError):
                        template_set_id = None
                generator = DeterministicCodeGenerator(language=language)
                code_bundle = generator.generate(bundle, template_set_id=template_set_id, solution_id=solution_id)
                files = {f.path: f.content for f in code_bundle.files}

                # D-PASS5-2: post-generation pass — vendor SDK client stubs
                files = _rewrite_vendor_sdk_files(files, genome.get("modules", {}))

                # Emit genome YAML for traceability + drift detection
                try:
                    import yaml as _yaml_stream
                    files["architectural_genome.yaml"] = _yaml_stream.dump(
                        genome, default_flow_style=False, sort_keys=False, allow_unicode=True,
                    )
                except Exception:
                    files["architectural_genome.json"] = json.dumps(genome, indent=2, default=str)

                # Compute genome-native completeness: fraction of modules with defined fields.
                # This is used as a chain-health fallback when ArchiMate links are absent.
                _gm_data = genome.get("modules") or {}
                if _gm_data:
                    _gm_with_fields = sum(1 for m in _gm_data.values() if m.get("fields"))
                    _genome_completeness = round(_gm_with_fields / len(_gm_data), 3)

                # Persist genome on the generation record for future drift scans
                try:
                    gen.genome = genome
                    _gqs = compute_quality_score(genome)
                    gen.genome_quality_score = _gqs["total"] if isinstance(_gqs, dict) else int(_gqs * 100)
                    db.session.commit()
                except Exception as _persist_err:
                    logger.warning("Stream: could not persist genome: %s", _persist_err)

                # Inject ARCHIMATE_SOURCE trace markers into model files.
                # The genome already carries archimate_element_ids per module — use those
                # to annotate generated model files so quality gate detects traceability.
                try:
                    import re as _re_tm
                    _tm_snake = lambda s: _re_tm.sub(r'(?<!^)(?=[A-Z])', '_', str(s)).lower().replace(' ', '_').replace('-', '_')  # noqa: E731
                    for _mod_key, _mod_def in (genome.get("modules") or {}).items():
                        _elem_ids = _mod_def.get("archimate_element_ids") or []
                        if not _elem_ids:
                            continue
                        _primary_id = _elem_ids[0]
                        _agg = _mod_def.get("aggregate_root") or ""
                        _agg_snake = _tm_snake(_agg) if _agg else _mod_key
                        for _model_path in [
                            f"app/models/{_mod_key}.py",
                            f"app/models/{_agg_snake}.py",
                        ]:
                            if _model_path in files and "# ARCHIMATE_SOURCE:" not in files[_model_path]:
                                files[_model_path] = f"# ARCHIMATE_SOURCE: {_primary_id}\n" + files[_model_path]
                except Exception as _trace_err:
                    logger.warning("Genome trace marker injection failed: %s", _trace_err)
            else:
                # ── UML / deterministic / hybrid path (unchanged) ───────────
                bundle = _uml_to_product_spec_bundle(gen.uml_snapshot, effective_config, _stream_solution,
                                                      business_rules=_stream_biz)
                bundle = _attach_salesforce_package_settings(bundle, effective_config)

                # ── Enrich bundle with inferred state machines + decision models ──
                try:
                    from app.modules.codegen.routes._helpers import (
                        _infer_state_machines_from_brief,
                        _extract_decision_models_from_brief,
                    )
                    _s_classes = (gen.uml_snapshot or {}).get("class_diagram", {}).get("classes", [])
                    _s_inferred_sms = _infer_state_machines_from_brief(solution_id, _s_classes, _stream_biz)
                    for _sm_e, _sm_d in _s_inferred_sms.items():
                        if _sm_e not in bundle.state_machines:
                            bundle.state_machines[_sm_e] = _sm_d
                    _s_inferred_dms = _extract_decision_models_from_brief(solution_id, _s_classes)
                    if hasattr(bundle, "decision_models"):
                        bundle.decision_models.extend(_s_inferred_dms)
                    else:
                        bundle.decision_models = _s_inferred_dms
                except Exception as _s_enrich_err:
                    logger.warning(
                        "Stream SM/DM inference failed for solution %d: %s",
                        solution_id, _s_enrich_err,
                    )

                # ── Stream: Gap-closure DB enrichments ────────────────────────
                try:
                    from app.modules.codegen.routes._helpers import (
                        _build_sla_load_config,
                        _build_resilience_config,
                        _build_compliance_config,
                        _build_rbac_config,
                        _read_adr_constraints,
                        _build_integration_clients,
                        _build_kpi_metrics_config,
                    )
                    _s_sla = _build_sla_load_config(solution_id, bundle.services)
                    if _s_sla:
                        bundle.sla_load_config = _s_sla
                    _s_res = _build_resilience_config(solution_id)
                    if _s_res:
                        bundle.resilience_config = _s_res
                    _s_cmp = _build_compliance_config(solution_id)
                    if _s_cmp:
                        bundle.compliance_config = _s_cmp
                        if not bundle._genome_compliance:
                            bundle._genome_compliance = {}
                        bundle._genome_compliance.update({
                            "gdpr": _s_cmp.get("gdpr", False),
                            "sox": _s_cmp.get("sox", False),
                            "hipaa": _s_cmp.get("hipaa", False),
                            "iso27001": _s_cmp.get("iso27001", False),
                            "pci_dss": _s_cmp.get("pci_dss", False),
                        })
                    _s_rbac = _build_rbac_config(solution_id)
                    if _s_rbac.get("has_rbac"):
                        bundle.rbac_config = _s_rbac
                        if not bundle.identity_provider:
                            bundle.identity_provider = {}
                        bundle.identity_provider.setdefault("roles", [])
                        _ex_roles = {r.get("name") for r in bundle.identity_provider["roles"]}
                        for _r in _s_rbac.get("roles", []):
                            if _r["name"] not in _ex_roles:
                                bundle.identity_provider["roles"].append(_r)
                    _s_adrs = _read_adr_constraints(solution_id)
                    if _s_adrs:
                        bundle.adr_constraints = _s_adrs
                        for _adr in _s_adrs:
                            if _adr.get("override_key") and _adr.get("override_value") and bundle.deployment:
                                _parts = _adr["override_key"].split(".")
                                if _parts[0] == "deployment" and len(_parts) == 2:
                                    setattr(bundle.deployment, _parts[1], _adr["override_value"])
                    _s_ints = _build_integration_clients(solution_id)
                    if _s_ints:
                        bundle.integration_clients = _s_ints
                    _s_kpis = _build_kpi_metrics_config(solution_id)
                    if _s_kpis:
                        bundle.kpi_metrics = _s_kpis
                except Exception as _s_gap_err:
                    logger.warning(
                        "Stream gap-closure enrichment failed for solution %d: %s",
                        solution_id, _s_gap_err,
                    )

                generator = DeterministicCodeGenerator(language=language)
                code_bundle = generator.generate(bundle, solution_id=solution_id)
                files = {f.path: f.content for f in code_bundle.files}

            if bundle.openapi:
                files["openapi.yaml"] = json.dumps(bundle.openapi, indent=2)
            yield _sse({"phase": "deterministic", "status": "done", "file_count": len(files)})
        except Exception as exc:
            logger.exception("Streaming — deterministic failed for solution %s", solution_id)
            yield _sse({"phase": "deterministic", "status": "error", "error": str(exc)})
            yield _sse({"phase": "complete", "success": False, "error": str(exc)})
            return

        # ── Phase: LLM enrichment (hybrid only) ──────────────────────────────
        if mode == "hybrid":
            yield _sse({"phase": "enrichment", "status": "running", "label": "Enriching business logic…"})
            try:
                from app.modules.codegen.services.code_generation_service import CodeGenerationService
                from app.modules.codegen.services.uml_enrichment_service import UMLEnrichmentService as _UES
                if CodeGenerationService.check_daily_limit(solution_id):
                    _sol_ctx = _UES._build_solution_context(solution_id)
                    # _stream_biz already fetched before bundle assembly — reuse it here.
                    enrichment = CodeGenerationService.enrich_deterministic_output(
                        files, gen.uml_snapshot, config,
                        solution_context=_sol_ctx,
                        business_rules=_stream_biz,
                    )
                    files.update(enrichment["files"])
                    errors.extend(enrichment["errors"])
                    CodeGenerationService.increment_daily_count(gen)
                    yield _sse({"phase": "enrichment", "status": "done",
                                "enriched_count": len(enrichment["files"])})
                else:
                    yield _sse({"phase": "enrichment", "status": "skipped",
                                "reason": "Daily generation limit reached"})
            except Exception as exc:
                logger.warning("Streaming — hybrid enrichment failed: %s", exc)
                errors.append({"prompt": "enrichment", "error": str(exc)})
                yield _sse({"phase": "enrichment", "status": "error", "error": str(exc)})

        # ── Phase: documentation ─────────────────────────────────────────────
        yield _sse({"phase": "documentation", "status": "running", "label": "Generating documentation…"})
        try:
            from app.modules.codegen.services.code_generation_service import _generate_readme
            # In genome mode the uml_snapshot is irrelevant; pass genome dict for doc helpers
            # that accept either format. They both work with {classes: [...]} shape or fall
            # back gracefully when keys are absent.
            _doc_ctx = (gen.genome or {}) if mode == "genome" else (gen.uml_snapshot or {})
            files["README.md"] = _generate_readme(solution, _doc_ctx, config, files)
            files["DECISIONS.md"] = _generate_adr(solution, config, gen.version)
            files["IMPLEMENTATION_GUIDE.md"] = _generate_implementation_guide(solution, _doc_ctx, config)
            if not any(k.startswith("alembic/versions/") and k.endswith(".py") for k in files):
                # D-PASS5-1: pass genome as keyword arg so the genome fallback in
                # _generate_alembic_migration fires correctly. _doc_ctx in genome mode
                # is the genome dict (has "modules"), so pass empty uml + genome=_doc_ctx.
                _alembic_uml = {} if mode == "genome" else _doc_ctx
                _alembic_genome_sse = _doc_ctx if mode == "genome" else None
                files["alembic/versions/0001_initial.py"] = _generate_alembic_migration(
                    _alembic_uml, genome=_alembic_genome_sse
                )
            files.update(_generate_alembic_support_files(_stream_solution.name or "app"))
            _ct = _generate_contract_tests(getattr(bundle, "openapi", {}) or {}, _stream_solution.name or "API")
            if _ct:
                files["tests/contract/test_api_contract.py"] = _ct
            # _stream_biz was fetched before bundle assembly — do not re-fetch here.
            _inv = _generate_architecture_invariant_tests(
                _stream_section_narratives,
                config,
                _stream_solution.name or "Solution",
                business_rules=_stream_biz,
            )
            if _inv:
                files["tests/architecture/test_invariants.py"] = _inv
            _peers = _build_peer_specs(solution_id)
            for _peer in _peers:
                _safe = re.sub(r'[^a-z0-9]', '_', _peer["solution_name"].lower()).strip('_')
                files[f"app/integrations/{_safe}_client.py"] = _generate_peer_client_stub(_peer)
            # Inject deterministic conftest, smoke test, settings, validator tests
            files["tests/conftest.py"] = _generate_test_conftest(language)
            files["tests/test_smoke.py"] = _generate_smoke_test()
            _seed_expectation_tests = _generate_seed_expectation_tests(
                getattr(bundle, "seed_context", {}) if bundle is not None else {}
            )
            if _seed_expectation_tests:
                files["tests/architecture/test_seed_expectations.py"] = _seed_expectation_tests
            if "app/core/config.py" not in files:
                files["app/core/config.py"] = _generate_settings_config(config)
            # Wire settings import into main.py so startup validation fires.
            if "app/main.py" in files and "app.core.config" not in files["app/main.py"]:
                files["app/main.py"] = (
                    "from app.core.config import settings  # noqa: F401  # validates env at startup\n"
                    + files["app/main.py"]
                )
            if _stream_biz:
                _val_t = _generate_validator_unit_tests(_stream_biz, _stream_solution.name or "Solution")
                if _val_t:
                    files["tests/unit/test_validators.py"] = _val_t
                try:
                    _br_ct = _generate_business_rule_contract_tests(
                        _stream_biz,
                        files.get("openapi.yaml", {}),
                        _stream_solution.name or "Solution",
                    )
                    if _br_ct:
                        files["tests/contract/test_business_rules.py"] = _br_ct
                except Exception as exc:
                    logger.debug("suppressed error in generate_stream.stream (app/modules/codegen/routes/codegen_routes.py): %s", exc)
                _missing = _verify_enrichment_rule_coverage(files, _stream_biz)
                if _missing:
                    logger.info("Streaming: injected %d stub validator(s): %s", len(_missing), _missing[:5])
            yield _sse({"phase": "documentation", "status": "done",
                        "peer_count": len(_peers)})
        except Exception as exc:
            errors.append({"prompt": "documentation", "error": str(exc)})
            yield _sse({"phase": "documentation", "status": "error", "error": str(exc)})

        # ── Mobile generation (opt-in, independent of ui_framework) ──────────
        _stream_mobile = payload.get("mobile_framework") or config.get("mobile_framework", "none")
        if mode == "genome" and _stream_mobile == "expo-react-native":
            # G-FINAL-5: genome path — use genome-aware mobile generator (mirrors sync path lines 1222-1274)
            try:
                _stream_mob_ui = payload.get("mobile_ui_framework") or config.get("mobile_ui_framework", "nativewind")
                from app.modules.codegen.services.mobile_generator import generate_mobile_from_genome
                _stream_mob_files = generate_mobile_from_genome(
                    genome, mobile_ui_framework=_stream_mob_ui
                )
                files.update(_stream_mob_files)
                logger.info("Streaming (genome): mobile generation added %d files", len(_stream_mob_files))
            except Exception as _mob_err:
                logger.warning("Streaming (genome): mobile generation failed: %s", _mob_err)
                errors.append({"prompt": "mobile", "error": str(_mob_err)})
        elif _stream_mobile == "expo-react-native":
            try:
                _stream_mob_ui = payload.get("mobile_ui_framework") or config.get("mobile_ui_framework", "nativewind")
                mobile_files = _generate_expo_mobile(
                    _stream_solution.name or f"Solution {solution_id}",
                    _doc_ctx,
                    {**config, "mobile_ui_framework": _stream_mob_ui},
                )
                files.update(mobile_files)
                logger.info("Streaming: mobile generation added %d files", len(mobile_files))
            except Exception as _mob_err:
                logger.warning("Streaming: mobile generation failed: %s", _mob_err)
                errors.append({"prompt": "mobile", "error": str(_mob_err)})

        # ── Frontend generation (opt-in, matches non-stream /generate endpoint) ──
        # _stream_ui_framework is read from payload (wizard sends it via generate() JS).
        if _stream_ui_framework == "shadcn-nextjs":
            try:
                frontend_files = _generate_shadcn_frontend(
                    _stream_solution.name or f"Solution {solution_id}",
                    _doc_ctx,
                    config,
                )
                files.update(frontend_files)
                logger.info("Streaming: shadcn-nextjs frontend added %d files", len(frontend_files))
            except Exception as _fe_err:
                logger.warning("Streaming: shadcn-nextjs frontend generation failed: %s", _fe_err)
                errors.append({"prompt": "frontend", "error": str(_fe_err)})
        elif _stream_ui_framework == "refine-antd":
            try:
                frontend_files = _generate_refine_frontend(
                    _stream_solution.name or f"Solution {solution_id}",
                    _doc_ctx,
                )
                files.update(frontend_files)
                logger.info("Streaming: refine-antd frontend added %d files", len(frontend_files))
            except Exception as _fe_err:
                logger.warning("Streaming: refine-antd frontend generation failed: %s", _fe_err)
                errors.append({"prompt": "frontend", "error": str(_fe_err)})

        # ── Phase: formatting + validation ───────────────────────────────────
        yield _sse({"phase": "validation", "status": "running", "label": "Formatting and validating…"})
        _format_python_files(files)
        # Remove conflicting frontend stacks when Next.js is present
        # app/static/admin.html is kept — it is the standalone admin panel, not a stack.
        if any(k.startswith("frontend/app/") for k in files):
            _stream_conflicts = [
                k for k in list(files)
                if (any(k.startswith(p) for p in ("templates/", "app/static/", "ui/static/"))
                    or k == "app/routers/pages.py")
                and k != "app/static/admin.html"
            ]
            for _sc in _stream_conflicts:
                del files[_sc]
        # Canonicalize route directories: merge app/routes/ → app/routers/
        _sr = {k: v for k, v in files.items() if k.startswith("app/routes/")}
        if _sr and any(k.startswith("app/routers/") for k in files):
            for _old, _cnt in list(_sr.items()):
                _new = "app/routers/" + _old[len("app/routes/"):]
                if _new not in files:
                    files[_new] = _cnt
                del files[_old]
        secret_warnings = _scan_for_secrets(files)
        import_graph_issues = _validate_import_graph(files)
        for issue in import_graph_issues:
            syntax_warnings.append(f"{issue['file']}: {issue['issue']}")
        _stream_smoke = _run_runtime_smoke_checks(
            files,
            language,
            seed_context=getattr(bundle, "seed_context", {}) if bundle is not None else {},
        )
        for err in _stream_smoke.get("errors", []):
            syntax_warnings.append(f"runtime-smoke-error: {err}")
        for warn in _stream_smoke.get("warnings", []):
            syntax_warnings.append(f"runtime-smoke-warning: {warn}")
        for miss in _stream_smoke.get("seed_expectation_issues", []):
            syntax_warnings.append(f"seed-expectation-missing: {miss}")
        # Reuse _stream_biz set before bundle assembly — avoids a third DB round-trip.
        quality_score, quality_details = _compute_quality_score(
            gen.uml_snapshot,
            files,
            business_rules=_stream_biz,
            seed_context=getattr(bundle, "seed_context", {}) if bundle is not None else {},
        )
        _section_narratives = _stream_section_narratives
        blueprint_violations = _validate_blueprint_constraints(files, _section_narratives, config, business_rules=_stream_biz)
        if blueprint_violations and quality_details:
            quality_details["blueprint_violations"] = blueprint_violations
            _deduction = sum(5 if v["severity"] == "error" else 2 for v in blueprint_violations)
            quality_score = max(0, (quality_score or 0) - _deduction)
        if quality_details is not None:
            quality_details["runtime_smoke"] = _stream_smoke

        # ── Quality gate (Task #10, streaming path) ───────────────────────────
        _enforce_quality_gate = _as_bool(
            payload.get("enforce_quality_gate"),
            default=(generation_policy in ("production-ready", "quality-gated")),
        )
        if _enforce_quality_gate:
            _qg_failures = []
            try:
                _min_qs = float(payload.get("min_quality_score", 80))
                _min_cc = float(payload.get("min_chain_completeness", 0.95))
            except (ValueError, TypeError):
                _msg = "min_quality_score and min_chain_completeness must be numbers"
                yield _sse({"phase": "validation", "status": "error", "error": _msg})
                yield _sse({"phase": "complete", "success": False, "error": _msg})
                return
            if (quality_score or 0) < _min_qs:
                _qg_failures.append({
                    "dimension": "quality_score",
                    "actual": quality_score,
                    "required": _min_qs,
                    "message": f"Quality score {quality_score:.1f} below minimum {_min_qs:.0f}",
                })
            _cc_check = completeness if completeness is not None else _genome_completeness
            if _cc_check is not None and _cc_check < _min_cc:
                _qg_failures.append({
                    "dimension": "chain_completeness",
                    "actual": round(_cc_check, 3),
                    "required": _min_cc,
                    "message": (
                        f"Chain completeness {_cc_check * 100:.0f}% "
                        f"below minimum {_min_cc * 100:.0f}%"
                    ),
                })
            if _stream_smoke.get("errors"):
                _qg_failures.append({
                    "dimension": "runtime_smoke",
                    "actual": len(_stream_smoke["errors"]),
                    "required": 0,
                    "message": f"{len(_stream_smoke['errors'])} runtime smoke error(s) in generated code",
                    "errors": _stream_smoke["errors"][:5],
                })
            if _qg_failures:
                yield _sse({
                    "phase": "validation",
                    "status": "error",
                    "error": "Generation blocked by quality gate.",
                    "gate_failures": _qg_failures,
                    "policy": generation_policy,
                    "quality_score": quality_score,
                    "chain_completeness": _cc_check,
                })
                yield _sse({
                    "phase": "complete",
                    "success": False,
                    "error": "Generation blocked by quality gate.",
                    "gate_failures": _qg_failures,
                })
                return

        placeholder_findings = _find_placeholder_stubs(files)
        if strict_production:
            blocking_issues = []
            if placeholder_findings and block_placeholder_stubs:
                blocking_issues.append({
                    "type": "placeholder_stubs",
                    "count": len(placeholder_findings),
                    "examples": placeholder_findings[:10],
                })
            if "tests/architecture/test_invariants.py" not in files:
                blocking_issues.append({
                    "type": "missing_invariant_tests",
                    "message": "Generated bundle is missing tests/architecture/test_invariants.py",
                })
            if _missing:
                blocking_issues.append({
                    "type": "missing_business_rule_validators",
                    "count": len(_missing),
                    "rules": _missing[:10],
                })
            _error_violations = [v for v in (blueprint_violations or []) if v.get("severity") == "error"]
            if _error_violations:
                blocking_issues.append({
                    "type": "blueprint_constraint_errors",
                    "count": len(_error_violations),
                    "violations": _error_violations[:10],
                })
            if _stream_smoke.get("errors"):
                blocking_issues.append({
                    "type": "runtime_smoke_errors",
                    "count": len(_stream_smoke["errors"]),
                    "errors": _stream_smoke["errors"][:10],
                })
            if blocking_issues:
                yield _sse({
                    "phase": "validation",
                    "status": "error",
                    "error": "Production-ready generation blocked by policy gates.",
                    "blocking_issues": blocking_issues,
                    "policy": generation_policy,
                })
                yield _sse({
                    "phase": "complete",
                    "success": False,
                    "error": "Production-ready generation blocked by policy gates.",
                    "blocking_issues": blocking_issues,
                })
                return
        yield _sse({"phase": "validation", "status": "done",
                    "quality_score": quality_score,
                    "violation_count": len(blueprint_violations)})

        # ── Persist ──────────────────────────────────────────────────────────
        # Re-fetch inside the generator using a separate variable (live_gen) so that
        # the closure variable `gen` is NOT shadowed — assigning `gen = ...` anywhere
        # in this function would make Python treat `gen` as an UnboundLocal for the
        # entire function body, breaking earlier references at lines 1802/1824/1843.
        _gen_fresh = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
        live_gen = _gen_fresh if _gen_fresh else gen
        live_gen.generated_files = files
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(live_gen, "generated_files")
        # Cache blueprint_violations in config so quality endpoint can skip re-derivation
        _cfg = dict(live_gen.config or {})
        _cfg["blueprint_violations"] = blueprint_violations
        _cfg["quality_score"] = quality_score
        _cfg["seed_context"] = getattr(bundle, "seed_context", {})
        _cfg["provenance"] = getattr(bundle, "provenance", {})
        live_gen.config = _cfg
        flag_modified(live_gen, "config")
        live_gen.version += 1
        from datetime import datetime as _dt2
        live_gen.completed_at = _dt2.utcnow()
        manifest = [
            {"path": p, "hash": hashlib.sha256(c.encode()).hexdigest()[:12]}
            for p, c in files.items()
        ]
        history_count = CodegenGenerationHistory.query.filter_by(
            codegen_generation_id=live_gen.id
        ).count()
        version_label = f"1.{history_count}.0"
        _stream_completeness = _compute_chain_completeness(solution_id)
        if _stream_completeness is None and _genome_completeness is not None:
            _stream_completeness = _genome_completeness
        history = CodegenGenerationHistory(
            codegen_generation_id=live_gen.id,
            generated_by_id=current_user.id,
            language=language, mode=mode,
            file_count=len(files),
            chain_completeness_score=_stream_completeness,
            version_label=version_label,
            file_manifest=manifest,
        )
        try:
            history.quality_score = quality_score
            history.quality_details = quality_details
        except Exception as _qs_exc2:
            logger.debug("Could not store quality score on stream history record: %s", _qs_exc2)
        db.session.add(history)

        # Persist Layer 2 traceability for streaming path
        if code_bundle is not None:
            from app.modules.codegen.models import persist_traceability
            persist_traceability(
                solution_id, code_bundle,
                spec_hash=getattr(bundle, "spec_hash", None),
            )

        try:
            db.session.commit()
        except Exception as _commit_exc:
            logger.exception("generate-stream: DB commit failed for solution %s: %s", solution_id, _commit_exc)
            db.session.rollback()
            yield _sse({"phase": "complete", "success": False, "error": f"DB commit failed: {_commit_exc}"})
            return

        result = {
            "success": True,
            "file_count": len(files),
            "files": list(files.keys()),
            "errors": errors,
            "version": live_gen.version,
            "version_label": version_label,
            "mode": mode,
            "language": language,
            "ui_framework": _stream_ui_framework,
            "quality_score": quality_score,
            "quality_details": quality_details,
            "chain_completeness": _stream_completeness,
            "generation_policy": generation_policy,
        }
        if secret_warnings:
            result["secret_warnings"] = secret_warnings
        if syntax_warnings:
            result["syntax_warnings"] = syntax_warnings
        if placeholder_findings:
            result["placeholder_warnings"] = placeholder_findings[:20]
        _cc_for_warning = _stream_completeness
        if _cc_for_warning is not None and _cc_for_warning < 0.7 and not enforce_chain_complete:
            result["completeness_warning"] = (
                f"Architecture chains are {_cc_for_warning * 100:.0f}% complete. "
                "Generated contracts may have structural gaps."
            )

        _notify_tech_lead(solution, len(files), language, None)

        # ── Auto-verify: run generated tests in Docker if available ──
        # Inline in the SSE stream so users see test results as part of generation.
        # Skip if: no tests, no Dockerfile, or Docker not available.
        _has_tests = any(p.startswith("tests/") and p.endswith(".py") for p in files)
        _has_dockerfile = "Dockerfile" in files
        _auto_verify_enabled = _has_tests and _has_dockerfile and language in ("python-fastapi",)
        if _auto_verify_enabled:
            try:
                verify_result = _auto_verify_generated_tests(
                    solution_id, live_gen, files, _sse_fn=_sse
                )
                if verify_result:
                    result["auto_verify"] = verify_result
                    # Update quality score with real test results
                    if verify_result.get("pass_rate") is not None:
                        result["quality_details"] = result.get("quality_details") or {}
                        result["quality_details"]["test_pass_rate"] = verify_result["pass_rate"]
                    yield _sse({"phase": "auto_verify", "status": "done", "result": verify_result})
            except Exception as _av_exc:
                logger.warning("Auto-verify failed (non-fatal): %s", _av_exc)
                yield _sse({"phase": "auto_verify", "status": "skipped",
                            "reason": f"Auto-verify failed: {_av_exc}"})

        yield _sse({"phase": "complete", "result": result})

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@codegen_bp.route("/solutions/<int:solution_id>/codegen/patch-violation", methods=["POST"])
@login_required
@require_csrf
def patch_violation(solution_id):
    """Auto-remediate a single blueprint violation by updating config or patching generated code.

    Payload: {"constraint": "<violation constraint name>"}
    Returns: {"success": bool, "message": str, "needs_regen": bool}

    Remediations that touch config require re-generation to take effect.
    Remediations that patch generated code take effect immediately in the stored bundle.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    constraint = (payload.get("constraint") or "").strip()
    if not constraint:
        return jsonify({"error": "constraint required"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"error": "No generation found"}), 404

    config = dict(gen.config or {})
    needs_regen = False
    message = ""
    patched = True

    if constraint == "JWT authentication required":
        config["auth"] = "jwt-local"
        gen.config = config
        needs_regen = True
        message = "Auth set to JWT-local. Re-generate to apply."

    elif constraint == "JWT implementation":
        # Inject HTTPBearer security scheme into main.py if it exists and is missing it
        files = dict(gen.generated_files or {})
        main_py = next((files[k] for k in files if k.endswith("main.py")), "")
        if main_py and "bearer" not in main_py.lower() and "oauth2" not in main_py.lower():
            patch = (
                "\n# Auto-patched: JWT Bearer security scheme\n"
                "from fastapi.security import HTTPBearer as _HTTPBearer\n"
                "_bearer_scheme = _HTTPBearer(auto_error=False)\n"
            )
            patched_key = next(k for k in files if k.endswith("main.py"))
            files[patched_key] = patch + files[patched_key]
            gen.generated_files = files
            message = "JWT Bearer scheme injected into main.py. Re-generate for a complete implementation."
        else:
            message = "JWT appears present — re-generate with auth=jwt-local for full implementation."

    elif constraint in ("HTTPS/TLS enforcement",):
        config["ssl_redirect"] = True
        gen.config = config
        needs_regen = True
        message = "SSL redirect enabled in config. Re-generate to apply."

    elif constraint == "Rate limiting":
        config["rate_limiting"] = True
        gen.config = config
        needs_regen = True
        message = "Rate limiting flag set. Re-generate to inject slowapi middleware."

    elif constraint == "Audit trail":
        config["audit_trail"] = True
        gen.config = config
        needs_regen = True
        message = "Audit trail flag set. Re-generate to inject audit log hooks."

    elif constraint == "Role-based access control":
        config["rbac"] = True
        gen.config = config
        needs_regen = True
        message = "RBAC flag set. Re-generate to inject role-check dependencies."

    elif constraint == "Pagination":
        config["pagination"] = True
        gen.config = config
        needs_regen = True
        message = "Pagination flag set. Re-generate to add page/size parameters to list endpoints."

    else:
        patched = False
        message = f"No auto-patch defined for '{constraint}' — manual fix required in generated code."

    if patched:
        db.session.commit()

    return jsonify({"success": patched, "message": message, "needs_regen": needs_regen})


def _build_dimension_fix_instruction(dimension: str, files: dict, quality_details: dict) -> tuple:
    """Build a targeted LLM instruction and file context for a quality dimension fix.

    Returns (instruction, primary_file_path, primary_file_content, related_files_list)
    where related_files_list is [{path, content}, ...].
    """
    all_paths = sorted(files.keys())

    def _pick_first(patterns, fallback=""):
        for path in all_paths:
            if any(p.lower() in path.lower() for p in patterns):
                return path, files[path]
        for path in all_paths:
            if fallback and fallback.lower() in path.lower():
                return path, files[path]
        if all_paths:
            return all_paths[0], files[all_paths[0]]
        return "", ""

    def _pick_related(patterns, max_n=3):
        result = []
        for path in all_paths:
            if any(p.lower() in path.lower() for p in patterns):
                result.append({"path": path, "content": files[path][:6000]})
                if len(result) >= max_n:
                    break
        return result

    if dimension == "schema_completeness":
        per_class = quality_details.get("per_class") or {}
        weak = [n for n, d in per_class.items() if (d.get("field_count") or 0) < 7]
        class_str = ", ".join(weak[:5]) if weak else "all model classes"
        instruction = (
            f"The schema completeness score is low. These model classes have fewer than 7 fields: {class_str}. "
            "Add realistic, business-relevant fields (id, name, status, created_at, updated_at, "
            "foreign keys, etc.) to these classes in the model and schema files. "
            "Do not remove existing fields. Keep changes minimal and targeted."
        )
        primary_file, primary_content = _pick_first(["models", "schemas", "model"], "app")
        related = _pick_related(["schema", "model", "pydantic"], max_n=3)

    elif dimension == "test_coverage":
        test_files = [f for f in all_paths if "test" in f.lower()]
        source_files = [
            f for f in all_paths
            if not any(x in f.lower() for x in ["test", "__init__", "conftest"])
            and f.endswith(".py")
        ]
        untested = [
            sf for sf in source_files
            if not any(sf.split("/")[-1].replace(".py", "") in tf for tf in test_files)
        ]
        targets = (untested or source_files)[:3]
        target_str = ", ".join(targets) if targets else "source files"
        instruction = (
            f"Test coverage is below the 30%% target ({len(test_files)} test files vs "
            f"{len(source_files)} source files). Generate comprehensive pytest test files for: {target_str}. "
            "Include: unit tests for models/schemas, route integration tests with test client, "
            "and edge-case coverage. Use pytest fixtures. Name each test file with a test_ prefix."
        )
        primary_file = targets[0] if targets else (all_paths[0] if all_paths else "")
        primary_content = files.get(primary_file, "")
        related = [{"path": p, "content": files[p][:6000]} for p in targets[1:4] if p in files]

    elif dimension == "relationship_density":
        score = quality_details.get("relationship_density", 0)
        cls_count = quality_details.get("class_count", 0)
        expected = cls_count * 3
        inferred = quality_details.get("inferred_route_ops", 0)
        instruction = (
            f"Relationship density is {score:.0f}%% (needs ~{expected} HTTP operations, found {inferred}). "
            "Add the missing REST endpoints to route files. Each entity should have: "
            "GET / (list), GET /<id> (detail), POST / (create), PUT /<id> (update), DELETE /<id> (delete). "
            "Add search and filter endpoints where appropriate."
        )
        primary_file, primary_content = _pick_first(["routes", "router", "api"], "main")
        related = _pick_related(["routes", "router", "api"], max_n=3)

    elif dimension == "traceability":
        per_class = quality_details.get("per_class") or {}
        untraceable = [n for n, d in per_class.items() if not d.get("has_source")]
        class_str = ", ".join(untraceable[:5]) if untraceable else "model classes"
        instruction = (
            f"Traceability is low — {len(untraceable)} class(es) lack architecture trace markers: {class_str}. "
            "Add a comment like `# ARCHIE_TRACE: <ClassName> <- ArchiMate:DataObject/<element>` "
            "at the top of each model/schema class definition for these classes. "
            "Also add a module-level `# ARCHIE_SOURCE: solution/<name>` comment at the top of each model file."
        )
        primary_file, primary_content = _pick_first(["models", "schemas", "model"], "app")
        related = _pick_related(["model", "schema"], max_n=2)

    elif dimension == "rule_coverage":
        rule_detail = quality_details.get("rule_coverage_detail") or {}
        uncovered = rule_detail.get("uncovered", [])
        rules_str = "; ".join(uncovered[:5]) if uncovered else "business rules"
        instruction = (
            f"Business rule coverage is low. These rules are not enforced in generated code: {rules_str}. "
            "Add validation logic to enforce each rule: use Pydantic validators for model constraints, "
            "FastAPI dependency functions for access checks, and service-layer assertions for "
            "business invariants. Each rule must have a corresponding code enforcement path."
        )
        primary_file, primary_content = _pick_first(["routes", "service", "validators"], "app")
        related = _pick_related(["routes", "service", "validators", "middleware"], max_n=3)

    elif dimension == "domain_fidelity":
        df_detail = quality_details.get("domain_fidelity_detail") or {}
        missing = df_detail.get("missing_required_fields", [])
        fields_str = ", ".join(missing[:7]) if missing else "canonical required fields"
        instruction = (
            f"Domain fidelity is below target. Missing required canonical fields: {fields_str}. "
            "Add these fields to the relevant model/schema classes with correct types and constraints. "
            "Add adapter patterns for any vendor integrations that are missing. "
            "Ensure field names match the canonical domain vocabulary exactly."
        )
        primary_file, primary_content = _pick_first(["models", "schemas", "model"], "app")
        related = _pick_related(["model", "schema", "adapter"], max_n=3)

    elif dimension == "linting":
        lint_detail = quality_details.get("lint") or {}
        e501_count = lint_detail.get("e501_count", 0)
        max_len = lint_detail.get("max_line_length", 88)
        instruction = (
            f"Fix PEP-8 linting violations: {e501_count} lines exceed {max_len} characters (E501). "
            "Wrap long lines using Python line continuation inside parentheses (not backslash). "
            "Target: all lines ≤88 characters. Prioritise route handlers, service methods, and SQL "
            "queries. Do not change logic — only reformat for line length compliance."
        )
        primary_file, primary_content = _pick_first(["routes", "service", "main"], "app")
        related = _pick_related(["routes", "service", "models"], max_n=3)

    elif dimension == "security":
        sec_detail = quality_details.get("security") or {}
        issues = []
        if not sec_detail.get("has_auth"):
            issues.append("missing JWT/Bearer auth on route handlers")
        if sec_detail.get("cors_wildcard"):
            issues.append("CORS allowed_origins=['*'] must be restricted to explicit origins")
        if sec_detail.get("debug_mode"):
            issues.append("debug=True must be False in production config")
        issues_str = "; ".join(issues) if issues else "security hardening required"
        instruction = (
            f"Fix security issues in the generated code: {issues_str}. "
            "For auth: import HTTPBearer from fastapi.security and add it as a dependency to route "
            "handlers that are missing it. For CORS: replace '*' with a list of explicit allowed "
            "origins (use an env variable). For debug: set DEBUG=False (read from env). "
            "Keep all changes minimal and targeted — do not refactor unrelated code."
        )
        primary_file, primary_content = _pick_first(["main", "config", "routes"], "app")
        related = _pick_related(["routes", "config", "middleware", "auth"], max_n=3)

    else:
        instruction = f"Improve the {dimension.replace('_', ' ')} quality dimension of the generated code."
        primary_file, primary_content = _pick_first(["app", "main", "routes"], "")
        related = []

    return instruction, primary_file, primary_content, related


@codegen_bp.route("/solutions/<int:solution_id>/codegen/fix-dimension", methods=["POST"])
@login_required
@require_csrf
def fix_dimension(solution_id):
    """Stream LLM-generated code improvements for a specific quality dimension.

    Payload: {"dimension": "schema_completeness" | "test_coverage" | "relationship_density" |
                           "traceability" | "rule_coverage" | "domain_fidelity"}

    Response: text/event-stream — same SSE format as /codegen/chat-edit
              events: thinking, patch, complete, error
    Each `patch` event carries {file, diff, warnings} that the client can auto-apply
    via POST /codegen/chat-edit/apply-patch.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    _VALID_DIMENSIONS = {
        "schema_completeness", "test_coverage", "relationship_density",
        "traceability", "rule_coverage", "domain_fidelity",
    }

    payload = request.get_json(silent=True) or {}
    dimension = (payload.get("dimension") or "").strip()
    if dimension not in _VALID_DIMENSIONS:
        return jsonify({
            "error": f"Invalid dimension. Must be one of: {', '.join(sorted(_VALID_DIMENSIONS))}"
        }), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "Generate code first"}), 400

    files = gen.generated_files
    quality_details = {}
    latest_hist = CodegenGenerationHistory.query.filter_by(
        codegen_generation_id=gen.id
    ).order_by(CodegenGenerationHistory.generated_at.desc()).first()
    if latest_hist:
        quality_details = getattr(latest_hist, "quality_details", None) or {}

    instruction, primary_file, primary_content, related = _build_dimension_fix_instruction(
        dimension, files, quality_details
    )

    from flask import current_app
    from app.modules.codegen.services.nl_code_editor import stream_chat_edit

    _app = current_app._get_current_object()

    def _generate():
        with _app.app_context():
            yield from stream_chat_edit(
                instruction=instruction,
                current_file=primary_file,
                current_file_content=primary_content,
                related_files=related,
                conversation_history=[],
            )

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@codegen_bp.route("/solutions/<int:solution_id>/codegen/fix-recommendation", methods=["POST"])
@login_required
@require_csrf
def fix_recommendation(solution_id):
    """Stream LLM-generated code changes that implement a specific quality recommendation.

    Payload: {"recommendation": "<text of the recommendation>"}

    Response: text/event-stream — same SSE format as /codegen/fix-dimension
              events: thinking, patch, complete, error
    Each `patch` event carries {file, diff} that the client auto-applies via apply-patch.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    recommendation = payload.get("recommendation") or ""
    # Support both plain string and structured {text, dimension, icon} object
    if isinstance(recommendation, dict):
        recommendation = recommendation.get("text") or recommendation.get("dimension") or ""
    recommendation = str(recommendation).strip()
    if not recommendation:
        return jsonify({"error": "recommendation text is required"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "Generate code first"}), 400

    files = gen.generated_files
    all_paths = sorted(files.keys())

    # Pick the most relevant primary file based on keyword hints in the recommendation
    rec_lower = recommendation.lower()
    primary_file = ""
    primary_content = ""
    for path in all_paths:
        stem = path.split("/")[-1].lower()
        if any(kw in rec_lower for kw in [stem.replace(".py", ""), stem.replace(".ts", ""), stem.replace(".tsx", "")]):
            primary_file = path
            primary_content = files[path]
            break
    if not primary_file:
        # Fallback: prefer model/schema files as the most common target
        for path in all_paths:
            if "/models/" in path and path.endswith(".py"):
                primary_file = path
                primary_content = files[path]
                break
    if not primary_file and all_paths:
        primary_file = all_paths[0]
        primary_content = files[all_paths[0]]

    # Provide related model + schema + route files as context
    related = []
    for path in all_paths:
        if len(related) >= 4:
            break
        if path == primary_file:
            continue
        if any(x in path for x in ["/models/", "/schemas/", "/routers/", "/routes/"]):
            related.append({"path": path, "content": files[path][:5000]})

    instruction = (
        f"Implement the following improvement in the generated codebase:\n\n"
        f"{recommendation}\n\n"
        "Make the minimal change required. Do not break existing functionality. "
        "Return a unified diff for each file you modify. "
        "If multiple files need changes, return multiple diffs."
    )

    from flask import current_app
    from app.modules.codegen.services.nl_code_editor import stream_chat_edit

    _app = current_app._get_current_object()

    def _generate():
        with _app.app_context():
            yield from stream_chat_edit(
                instruction=instruction,
                current_file=primary_file,
                current_file_content=primary_content,
                related_files=related,
                conversation_history=[],
            )

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@codegen_bp.route("/solutions/<int:solution_id>/codegen/verify", methods=["POST"])
@login_required
@require_csrf
def verify_code(solution_id):
    """Execute generated tests in Docker and stream results via SSE.

    Writes all generated files to a temp directory, builds the app Docker image,
    runs pytest inside the container, streams stdout line-by-line, then tears down.
    Updates quality_details.test_execution in the latest history record with real
    pass/fail counts so the quality score reflects actual test results.

    SSE events:
      {phase: "setup"|"build"|"test"|"teardown"|"complete", status: str, ...}
    Final: {phase: "complete", success: bool, summary: {passed, failed, errors}, pass_rate: int}
    """
    import os
    import shutil
    import subprocess
    import tempfile
    from datetime import datetime as _dt
    from flask import Response, stream_with_context

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()

    # Capture pre-flight state — errors are streamed as SSE rather than returned
    # as HTTP 4xx so callers always see HTTP 200 and read the event stream for
    # failure details. This prevents the frontend (and journey tests) from
    # having to special-case HTTP errors; all outcomes arrive as SSE events.
    _preflight_error = None
    _files = None
    if not gen or not gen.generated_files:
        _preflight_error = "No generated files — run Phase 4 first to produce a code bundle"
    else:
        _files = gen.generated_files
        _verify_language = ((gen.config or {}).get("language") or "python-fastapi").lower()
        _smoke = _run_runtime_smoke_checks(
            _files,
            _verify_language,
            seed_context=(gen.config or {}).get("seed_context"),
        )
        if _smoke.get("errors"):
            _preflight_error = (
                "Runtime smoke preflight failed — fix generation issues before verification. "
                + "; ".join(_smoke.get("errors", []))
            )
        elif not any(p.startswith("tests/") and p.endswith(".py") for p in _files):
            _preflight_error = "No test files in bundle — re-generate first"
        elif "docker-compose.yml" not in _files:
            _preflight_error = "No docker-compose.yml in bundle — re-generate first"

    # Aliases: used throughout stream() below
    files = _files or {}
    # `preflight_smoke` is read inside stream() but its alias assignment had been
    # removed, so verify_code raised NameError as soon as the setup event was
    # emitted. `_smoke` is only bound in the else-branch above (it is not computed
    # when there are no generated files), hence the locals() guard rather than a
    # bare reference.
    preflight_smoke = locals().get("_smoke") or {}

    def _sse(data):
        return f"data: {json.dumps(data)}\n\n"

    @stream_with_context
    def stream():
        # ── Pre-flight: bundle / smoke errors ────────────────────────────
        # Return 200 + SSE error instead of 400/422 so callers see consistent
        # stream-based responses for all failure modes (not just Docker absence).
        if _preflight_error:
            yield _sse({
                "phase": "complete",
                "status": "error",
                "success": False,
                "message": _preflight_error,
                "summary": {"passed": 0, "failed": 0, "errors": 1},
                "pass_rate": 0,
            })
            return

        # ── Pre-flight: Docker availability check ─────────────────────────
        # "Run Tests" requires Docker daemon. On a bare server without Docker
        # the subprocess calls succeed silently then produce no output.
        # Surface this immediately so users see a clear, actionable message
        # rather than a spinner that hangs until timeout.
        try:
            _docker_check = subprocess.run(
                ["docker", "info"], capture_output=True, timeout=5
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            _docker_check = type("_R", (), {"returncode": 1})()
        if _docker_check.returncode != 0:
            yield _sse({
                "phase": "complete",
                "status": "error",
                "success": False,
                "message": (
                    "Docker is not available on this server. "
                    "To run tests locally: download the generated zip, "
                    "run `docker compose up --build`, then `docker compose run "
                    "--rm api pytest tests/ --tb=short -q`."
                ),
                "summary": {"passed": 0, "failed": 0, "errors": 1},
                "pass_rate": 0,
            })
            return

        tmpdir = None
        _test_img = None
        try:
            # ── Write files ───────────────────────────────────────────────
            tmpdir = tempfile.mkdtemp(prefix=f"archie-verify-{solution_id}-")
            yield _sse({"phase": "setup", "status": "running",
                        "label": f"Writing {len(files)} files to build context…"})
            for path, content in files.items():
                full = os.path.join(tmpdir, path.replace("/", os.sep))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                try:
                    with open(full, "w", encoding="utf-8") as fh:
                        fh.write(content)
                except Exception as _write_exc:
                    logger.warning("Could not write test file %s to tmpdir: %s", path, _write_exc)
            test_files = [p for p in files if p.startswith("tests/") and p.endswith(".py")]
            # Write a minimal .env so docker compose variable interpolation doesn't fail
            env_path = os.path.join(tmpdir, ".env")
            if not os.path.exists(env_path):
                with open(env_path, "w") as _ef:
                    _ef.write(
                        "POSTGRES_USER=app\n"
                        "POSTGRES_PASSWORD=changeme\n"
                        "POSTGRES_DB=app\n"
                        "DATABASE_URL=postgresql://app:changeme@db:5432/app\n"  # secrets-safety-ok: placeholder creds for throwaway docker test sandbox
                        "SECRET_KEY=test-secret\n"
                        "JWT_SECRET=test-jwt\n"
                        "TESTING=1\n"
                    )
            # Overwrite conftest.py with deterministic version — LLM-generated
            # conftest calls Base.metadata.create_all(bind=async_engine) synchronously
            # which raises InvalidRequestError at pytest collection time.
            _conftest_path = os.path.join(tmpdir, "tests", "conftest.py")
            os.makedirs(os.path.dirname(_conftest_path), exist_ok=True)
            with open(_conftest_path, "w", encoding="utf-8") as _cf:
                _cf.write(_generate_test_conftest(_verify_language))
            yield _sse({"phase": "setup", "status": "done",
                        "file_count": len(files), "test_file_count": len(test_files),
                        "seed_expectation_issues": preflight_smoke.get("seed_expectation_issues", [])[:20]})

            # ── Detect API service name from docker-compose.yml ───────────
            _svc_name = "api"  # default from Entelim generator
            _dc_content = files.get("docker-compose.yml", "")
            import re as _re
            for _line in _dc_content.splitlines():
                _m = _re.match(r"^  ([a-zA-Z][a-zA-Z0-9_-]+):$", _line)
                if _m and _m.group(1) not in ("db", "redis", "nginx", "cache"):
                    _svc_name = _m.group(1)
                    break

            # ── Patch Dockerfile test stage with commonly-missing test deps ─
            # The LLM-generated test stage may omit aiosqlite / email-validator.
            # Augment its pip install line so tests can import the app.
            _dockerfile_path = os.path.join(tmpdir, "Dockerfile")
            if os.path.exists(_dockerfile_path):
                _df = open(_dockerfile_path).read()
                if "AS test" in _df and "aiosqlite" not in _df:
                    _df = _df.replace(
                        "RUN pip install pytest httpx pytest-asyncio",
                        "RUN pip install pytest httpx pytest-asyncio aiosqlite email-validator",
                    )
                    # Fallback: if exact string wasn't matched, append extra install
                    if "aiosqlite" not in _df:
                        _df = _df.replace(
                            "FROM base AS test",
                            "FROM base AS test\nRUN pip install --quiet aiosqlite email-validator",
                        )
                    with open(_dockerfile_path, "w") as _dff:
                        _dff.write(_df)

            # ── Build test stage from Dockerfile ─────────────────────────
            # The Dockerfile has a dedicated `test` stage (FROM base AS test) that
            # installs pytest. Build that stage rather than the compose `run` target
            # so pytest is available in the container.
            _test_img = f"archie-test-{solution_id}-{os.path.basename(tmpdir)}"
            _has_test_stage = "AS test" in files.get("Dockerfile", "")
            _build_target = ["--target", "test"] if _has_test_stage else []
            yield _sse({"phase": "build", "status": "running",
                        "label": "Building Docker test image…"})
            build_proc = subprocess.Popen(
                ["/usr/bin/docker", "build"] + _build_target + ["-t", _test_img, "."],
                cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            _build_deadline = time.monotonic() + 300
            build_lines = []
            for line in build_proc.stdout:
                if time.monotonic() > _build_deadline:
                    build_proc.kill()
                    raise subprocess.TimeoutExpired(cmd="docker build", timeout=300)
                line = line.rstrip()
                build_lines.append(line)
                if line:
                    yield _sse({"phase": "build", "status": "running", "line": line[-180:]})
            build_proc.wait(timeout=30)

            if build_proc.returncode != 0:
                tail = "\n".join(build_lines[-15:])
                yield _sse({"phase": "build", "status": "error",
                            "error": "Docker build failed", "tail": tail})
                yield _sse({"phase": "complete", "success": False,
                            "error": "Docker build failed", "output": tail})
                return
            yield _sse({"phase": "build", "status": "done"})

            # ── Run pytest inside the test image ─────────────────────────
            # Contract tests make real HTTP calls — exclude them here.
            has_unit_tests = any(p.startswith("tests/unit/") and p.endswith(".py") for p in files)
            test_target = "tests/unit/" if has_unit_tests else "tests/"
            yield _sse({"phase": "test", "status": "running",
                        "label": f"Running pytest {test_target}…"})
            test_proc = subprocess.Popen(
                [
                    "/usr/bin/docker", "run", "--rm",
                    "--memory=512m", "--cpus=1.0",
                    "-e", "DATABASE_URL=sqlite+aiosqlite:///./test.db",
                    "-e", "TESTING=1",
                    _test_img,
                    "pytest", test_target, "--tb=short", "-q", "--no-header",
                ],
                cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            _test_deadline = time.monotonic() + 120
            test_lines = []
            for line in test_proc.stdout:
                if time.monotonic() > _test_deadline:
                    test_proc.kill()
                    raise subprocess.TimeoutExpired(cmd="docker run pytest", timeout=120)
                line = line.rstrip()
                test_lines.append(line)
                if line:
                    yield _sse({"phase": "test", "status": "running", "line": line[-180:]})
            test_proc.wait(timeout=30)

            "\n".join(test_lines)
            summary = {"passed": 0, "failed": 0, "errors": 0}
            for tl in reversed(test_lines):
                m = re.search(r'(\d+) passed(?:[^\d]+(\d+) failed)?(?:[^\d]+(\d+) error)?', tl)
                if m:
                    summary = {
                        "passed": int(m.group(1) or 0),
                        "failed": int(m.group(2) or 0),
                        "errors": int(m.group(3) or 0),
                    }
                    break

            total = summary["passed"] + summary["failed"] + summary["errors"]
            pass_rate = round(summary["passed"] / total * 100) if total > 0 else 0
            all_passed = summary["failed"] == 0 and summary["errors"] == 0

            yield _sse({
                "phase": "test",
                "status": "done" if all_passed else "partial",
                "summary": summary, "pass_rate": pass_rate,
                "exit_code": test_proc.returncode,
            })

            # ── Persist real test results into quality_details ───────────
            try:
                latest_hist = CodegenGenerationHistory.query.filter_by(
                    codegen_generation_id=gen.id
                ).order_by(CodegenGenerationHistory.generated_at.desc()).first()
                if latest_hist and latest_hist.quality_details:
                    qd = dict(latest_hist.quality_details)
                    qd["test_execution"] = {
                        "passed": summary["passed"],
                        "failed": summary["failed"],
                        "errors": summary["errors"],
                        "pass_rate": pass_rate,
                        "verified_at": _dt.utcnow().isoformat(),
                    }
                    # Override estimated test_coverage with measured pass rate
                    qd["test_coverage"] = float(pass_rate)
                    # Recalculate overall quality score with real test coverage
                    new_score = round(
                        qd.get("schema_completeness", 0) * 0.22
                        + float(pass_rate) * 0.18
                        + qd.get("relationship_density", 0) * 0.12
                        + qd.get("traceability", 0) * 0.12
                        + qd.get("rule_coverage", 100) * 0.16
                        + qd.get("domain_fidelity", 100) * 0.20,
                        1,
                    )
                    latest_hist.quality_details = qd
                    latest_hist.quality_score = new_score
                _persist_intent_verify_state(
                    gen,
                    status="pass" if all_passed else "warn",
                    summary=summary,
                    pass_rate=pass_rate,
                    error="" if all_passed else "Verification completed with test failures",
                )
                db.session.commit()
            except Exception as _upd_exc:
                db.session.rollback()
                logger.warning("Failed to persist test execution results: %s", _upd_exc)

            # ── Teardown ─────────────────────────────────────────────────
            yield _sse({"phase": "teardown", "status": "running", "label": "Removing containers…"})
            subprocess.run(
                ["/usr/bin/docker", "rmi", "-f", _test_img],
                capture_output=True, timeout=30,
            )
            yield _sse({"phase": "teardown", "status": "done"})

            yield _sse({
                "phase": "complete", "success": True,
                "summary": summary, "pass_rate": pass_rate,
            })

        except subprocess.TimeoutExpired:
            try:
                _persist_intent_verify_state(
                    gen,
                    status="warn",
                    summary={"passed": 0, "failed": 0, "errors": 1},
                    pass_rate=0,
                    error="Execution timed out (5-minute limit)",
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
            yield _sse({"phase": "complete", "success": False,
                        "error": "Execution timed out (5-minute limit)"})
        except Exception as exc:
            logger.exception("verify_code failed for solution %s", solution_id)
            try:
                _persist_intent_verify_state(
                    gen,
                    status="warn",
                    summary={"passed": 0, "failed": 0, "errors": 1},
                    pass_rate=0,
                    error=str(exc),
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
            yield _sse({"phase": "complete", "success": False, "error": str(exc)})
        finally:
            if tmpdir and os.path.exists(tmpdir):
                try:
                    if _test_img:
                        subprocess.run(
                            ["/usr/bin/docker", "rmi", "-f", _test_img],
                            capture_output=True, timeout=15,
                        )
                except Exception as _rmi_exc:
                    logger.debug("Docker rmi cleanup failed (non-critical): %s", _rmi_exc)
                shutil.rmtree(tmpdir, ignore_errors=True)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@codegen_bp.route("/solutions/<int:solution_id>/codegen/regenerate", methods=["POST"])
@login_required
@require_csrf
def regenerate_file(solution_id):
    """Regenerate files for a prompt group (e.g., 'models' regenerates all model files)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    file_key = payload.get("file_key")
    if not file_key:
        return jsonify({"error": "file_key required"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.uml_snapshot:
        return jsonify({"error": "Run Phase 1 first"}), 400

    if payload.get("version") and payload["version"] != gen.version:
        return jsonify({"error": "Version conflict. Refresh the page."}), 409

    config = gen.config or {"auth": "none", "python_version": "3.12"}

    from app.modules.codegen.services.code_generation_service import CodeGenerationService
    from app.modules.ai_chat.services.llm_service import LLMService

    prompt = CodeGenerationService.build_prompt(file_key, gen.uml_snapshot, config)
    provider, model = LLMService._get_configured_provider()

    try:
        raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
        files = CodeGenerationService.parse_code_response(raw_text)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not files:
        return jsonify({"error": "Failed to parse regenerated code"}), 500

    # GAP-10: Scan regenerated code for secrets before saving
    secret_warnings = _scan_for_secrets(files)

    existing = gen.generated_files or {}
    existing.update(files)
    gen.generated_files = existing
    gen.version += 1
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("regenerate_file DB commit failed: %s", e)
        return jsonify({"error": "Database error saving regenerated files"}), 500

    response = {
        "success": True,
        "files": list(files.keys()),
        "version": gen.version,
    }
    if secret_warnings:
        response["secret_warnings"] = secret_warnings
    return jsonify(response)


@codegen_bp.route("/solutions/<int:solution_id>/codegen/uml", methods=["PUT"])
@login_required
@require_csrf
def save_uml(solution_id):
    """Phase 2: Save UML edits."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"error": "Run Phase 1 first"}), 400

    if payload.get("version") and payload["version"] != gen.version:
        return jsonify({"error": "Version conflict. Refresh the page."}), 409

    gen.uml_snapshot = payload.get("uml", gen.uml_snapshot)
    gen.version += 1
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("save_uml DB commit failed: %s", e)
        return jsonify({"error": "Database error saving UML"}), 500
    return jsonify({"success": True, "version": gen.version})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/confirm-fields", methods=["POST"])
@login_required
@require_csrf
def confirm_fields(solution_id):
    """Phase 2: Confirm architect-edited fields back to SolutionArchiMateElement spec_data.

    Payload: { "classes": [ { "source_element_id": 123, "fields": [...] }, ... ] }
    Each field: { "name": str, "type": str, "nullable": bool, "description": str }
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    classes = payload.get("classes", [])
    if not classes:
        return jsonify({"error": "No classes provided"}), 400

    from app.models.solution_archimate_element import SolutionArchiMateElement

    links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    junction_by_element = {link.element_id: link for link in links}

    confirmed_count = 0
    skipped = []
    for cls in classes:
        source_id = cls.get("source_element_id")
        fields = cls.get("fields", [])
        if not source_id or not fields:
            continue

        junction = junction_by_element.get(source_id)
        if not junction:
            continue

        # Ownership guard: junction must belong to this solution
        if junction.solution_id != solution_id:
            skipped.append(source_id)
            continue

        # Cap field count and field value lengths to prevent DoS
        fields = fields[:50]
        fields = [
            {k: (str(v)[:500] if isinstance(v, str) else v) for k, v in f.items()}
            if isinstance(f, dict) else f
            for f in fields
        ]

        # Validate field names are valid identifiers (prevents SQL/code injection via names)
        _VALID_TYPES = {"string", "integer", "decimal", "boolean", "date", "datetime", "text", "float", "uuid", "json", "enum", "email", "url", "binary"}
        validated_fields = []
        seen_names = set()
        for f in fields:
            if not isinstance(f, dict):
                continue
            name = f.get("name", "")
            if not name or not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', str(name)):
                continue
            if name.lower() in seen_names:
                continue  # skip duplicate field names
            seen_names.add(name.lower())
            ftype = str(f.get("type", "string")).lower()
            if ftype not in _VALID_TYPES:
                f["type"] = "string"
            validated_fields.append(f)
        fields = validated_fields

        existing = junction.spec_data or {}
        junction.spec_data = {
            **existing,
            "fields": fields,
            "fields_status": "confirmed",
            "fields_version": (existing.get("fields_version", 0) or 0) + 1,
            "confirmed_by": current_user.id,
        }
        db.session.add(junction)
        confirmed_count += 1

    # Also update the UML snapshot with confirmed fields
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if gen and gen.uml_snapshot:
        uml = gen.uml_snapshot
        uml_classes = uml.get("class_diagram", {}).get("classes", [])
        confirmed_map = {c["source_element_id"]: c["fields"] for c in classes if c.get("source_element_id")}
        for uml_cls in uml_classes:
            sid = uml_cls.get("source_element_id")
            if sid in confirmed_map:
                uml_cls["fields"] = confirmed_map[sid]
        gen.uml_snapshot = uml
        gen.version += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("Failed to confirm fields for solution %s: %s", solution_id, e)
        return jsonify({"error": "Database error saving confirmed fields"}), 500

    return jsonify({
        "success": True,
        "confirmed_count": confirmed_count,
        "version": gen.version if gen else 0,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/chat-regenerate", methods=["POST"])
@login_required
@require_csrf
def chat_regenerate(solution_id):
    """Gap 3: Regenerate files using a natural language instruction.

    Payload: { "instruction": "add JWT auth", "version": N }
    Sends instruction + current generated files context to LLM, replaces affected files.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    instruction = (payload.get("instruction") or "").strip()
    if not instruction:
        return jsonify({"error": "instruction required"}), 400
    instruction = re.sub(r'```', '~~~', instruction)
    instruction = re.sub(r'^###\s+FILE:', '### NOTE:', instruction, flags=re.MULTILINE)
    instruction = instruction[:2000]

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "Generate code first (Phase 4)"}), 400

    if payload.get("version") and payload["version"] != gen.version:
        return jsonify({"error": "Version conflict. Refresh the page."}), 409

    intent_plan_active = _as_bool(payload.get("intent_plan_active"), default=False) \
        or isinstance((gen.config or {}).get("_intent_plan"), dict)
    verification_override = _as_bool(payload.get("verification_override"), default=False)
    override_reason = (payload.get("override_reason") or "").strip()
    if len(override_reason) > 400:
        override_reason = override_reason[:400]

    gate_mode = "not_required"
    verify_state = None
    if intent_plan_active:
        verified_ok, verify_state = _latest_intent_verify_state(gen, max_age_minutes=120)
        if not verified_ok:
            if not verification_override:
                _append_intent_gate_event(gen, {
                    "at": _utc_now_iso(),
                    "user_id": current_user.id,
                    "action": "chat_regenerate_blocked",
                    "mode": "blocked",
                    "reason": "verification_required",
                    "verify_state": verify_state,
                })
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                return jsonify({
                    "error": "Verification gate blocked AI changes. Run Verify Loop or provide an override reason.",
                    "gate": "verification_required",
                    "verify_state": verify_state,
                    "requires_override_reason": True,
                }), 409
            if len(override_reason) < 12:
                return jsonify({
                    "error": "override_reason is required (min 12 characters) when bypassing verification.",
                }), 400
            gate_mode = "override"
        else:
            gate_mode = "verified"

        _append_intent_gate_event(gen, {
            "at": _utc_now_iso(),
            "user_id": current_user.id,
            "action": "chat_regenerate_allowed",
            "mode": gate_mode,
            "override_reason": override_reason if gate_mode == "override" else "",
            "verify_state": verify_state,
        })
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    # Build context: file manifest + selected files content
    files = gen.generated_files
    file_manifest = "\n".join(f"- {path}" for path in sorted(files.keys()))

    # Include up to 15 files' content (truncated) for context
    file_context_parts = []
    for path in sorted(files.keys())[:15]:
        content = files[path]
        if len(content) > 2000:
            content = content[:2000] + "\n# ... truncated ..."
        file_context_parts.append(f"### {path}\n```\n{content}\n```")
    file_context = "\n\n".join(file_context_parts)

    prompt = f"""You are a code generation assistant. The user has generated a codebase and wants to modify it.

## Current Files
{file_manifest}

## File Contents
{file_context}

## User Instruction
{instruction}

## Rules
1. Return ONLY the files that need to change, in this exact format:
### FILE: path/to/file.py
```
full file content here
```

2. Include the COMPLETE file content for each changed file, not just diffs.
3. Do not change files that don't need modification.
4. Follow the existing code style and patterns.
5. Be precise — implement exactly what the user asked for."""

    from app.modules.ai_chat.services.llm_service import LLMService

    try:
        provider, model = LLMService._get_configured_provider()
        raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
    except Exception as e:
        return jsonify({"error": f"LLM call failed: {str(e)}"}), 500

    if not raw_text:
        return jsonify({"error": "LLM returned empty response"}), 500

    # Parse response: extract ### FILE: path blocks
    changed_files = {}
    current_path = None
    current_lines = []
    in_code_block = False

    for line in raw_text.split("\n"):
        if line.startswith("### FILE:"):
            # Save previous file
            if current_path and current_lines:
                changed_files[current_path] = "\n".join(current_lines)
            current_path = line.replace("### FILE:", "").strip()
            current_lines = []
            in_code_block = False
        elif current_path:
            if line.startswith("```") and not in_code_block:
                in_code_block = True
                continue
            elif line.startswith("```") and in_code_block:
                in_code_block = False
                continue
            if in_code_block:
                current_lines.append(line)

    # Save last file
    if current_path and current_lines:
        changed_files[current_path] = "\n".join(current_lines)

    if not changed_files:
        return jsonify({"error": "Could not parse any file changes from LLM response"}), 500

    _SAFE_PATH_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_\-/\.]*$')
    changed_files = {
        k: v for k, v in changed_files.items()
        if '..' not in k and not k.startswith('/') and _SAFE_PATH_RE.match(k)
    }

    # Scan for secrets
    secret_warnings = _scan_for_secrets(changed_files)

    # Compute impact
    old_hashes = {
        path: hashlib.sha256(content.encode()).hexdigest()[:12]
        for path, content in files.items()
    }
    changed_paths = []
    added_paths = []
    for path in changed_files:
        if path in files:
            new_hash = hashlib.sha256(changed_files[path].encode()).hexdigest()[:12]
            if new_hash != old_hashes.get(path):
                changed_paths.append(path)
        else:
            added_paths.append(path)

    # Capture old content for diff before merging
    old_content = {}
    for path in changed_files:
        if path in files:
            old_content[path] = files[path]

    # Merge changed files into generated_files
    existing = dict(gen.generated_files)
    existing.update(changed_files)
    gen.generated_files = existing
    gen.version += 1

    cfg = dict(gen.config or {})
    if intent_plan_active:
        intent_meta = dict(cfg.get("_intent_plan") or {})
        intent_meta["last_applied_at"] = _utc_now_iso()
        intent_meta["last_applied_by"] = current_user.id
        intent_meta["last_gate_mode"] = gate_mode
        if gate_mode == "override":
            intent_meta["last_override_reason"] = override_reason
        cfg["_intent_plan"] = intent_meta
        verify_meta = dict(cfg.get("_intent_verify") or {})
        verify_meta["used_for_apply_at"] = _utc_now_iso()
        cfg["_intent_verify"] = verify_meta
    gen.config = cfg

    # Record history
    manifest = [
        {"path": path, "hash": hashlib.sha256(content.encode()).hexdigest()[:12]}
        for path, content in existing.items()
    ]
    history_count = CodegenGenerationHistory.query.filter_by(
        codegen_generation_id=gen.id
    ).count()
    version_label = f"1.{history_count}.0"

    history = CodegenGenerationHistory(
        codegen_generation_id=gen.id,
        generated_by_id=current_user.id,
        language=(gen.config or {}).get("language", "python-fastapi"),
        mode="chat",
        file_count=len(existing),
        version_label=version_label,
        file_manifest=manifest,
    )
    db.session.add(history)
    db.session.commit()

    response = {
        "success": True,
        "changed_files": sorted(changed_paths),
        "added_files": sorted(added_paths),
        "file_count": len(changed_files),
        "version": gen.version,
        "version_label": version_label,
        "files": list(existing.keys()),
        "generated_content": changed_files,
        "old_content": old_content,
    }
    if secret_warnings:
        response["secret_warnings"] = secret_warnings
    return jsonify(response)


@codegen_bp.route("/solutions/<int:solution_id>/codegen/uml/reset", methods=["POST"])
@login_required
@require_csrf
def reset_uml(solution_id):
    """Re-enrich from ArchiMate, clearing Phases 1-4."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if gen:
        gen.uml_snapshot = None
        gen.config = None
        gen.generated_files = None
        gen.github_url = None
        gen.github_commit_sha = None
        gen.completed_at = None
        gen.version += 1
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("reset_uml DB commit failed: %s", e)
            return jsonify({"error": "Database error during reset"}), 500
    return jsonify({"success": True, "message": "Reset complete. Run Enrich again."})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/config", methods=["GET"])
@login_required
def get_config(solution_id):
    """Return stored config for a solution (lightweight — no file content)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"config": {}})
    # Strip heavyweight keys that the UI doesn't need on page load
    config = dict(gen.config or {})
    for key in ("seed_context", "provenance", "blueprint_violations", "quality_score",
                "quality_details", "runtime_smoke", "placeholder_findings"):
        config.pop(key, None)
    return jsonify({"config": config, "version": gen.version})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/config", methods=["PUT"])
@login_required
@require_csrf
def save_config(solution_id):
    """Phase 3: Save stack + repo configuration."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"error": "Run Phase 1 first"}), 400

    # Preserve daily count tracking keys
    existing_config = gen.config or {}
    language = payload.get("language", "python-fastapi")
    normalized_package_config = _normalize_salesforce_package_config({**existing_config, "language": language}, payload)

    # Validate and sanitize template_set_id
    template_set_id = payload.get("template_set_id") or None
    if template_set_id:
        try:
            template_set_id = int(template_set_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid template_set_id"}), 400
        ts = CodegenTemplateSet.query.get(template_set_id)
        if ts and ts.created_by_id and ts.created_by_id != current_user.id:
            template_set_id = None

    gen.config = {
        "language": language,
        "generation_mode": payload.get("generation_mode", "deterministic"),
        "generation_policy": payload.get("generation_policy", existing_config.get("generation_policy", "scaffold")),
        "python_version": payload.get("python_version", "3.12"),
        "auth": payload.get("auth", "jwt_local"),
        "github_org": payload.get("github_org", ""),
        "repo_name": payload.get("repo_name", ""),
        "visibility": payload.get("visibility", "private"),
        "include_readme": payload.get("include_readme", True),
        "identity_provider": payload.get("identity_provider"),
        "ci_cd": payload.get("ci_cd"),
        "ui_framework": payload.get("ui_framework", "none"),
        "mobile_framework": payload.get("mobile_framework", "none"),
        "package_mode": normalized_package_config.get("package_mode", "unmanaged"),
        "namespace_prefix": normalized_package_config.get("namespace_prefix", ""),
        "template_set_id": template_set_id,
        "_daily_reset_date": existing_config.get("_daily_reset_date", ""),
        "_daily_gen_count": existing_config.get("_daily_gen_count", 0),
    }
    gen.version += 1
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("save_config DB commit failed: %s", e)
        return jsonify({"error": "Database error saving config"}), 500
    return jsonify({"success": True, "version": gen.version})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/patch", methods=["POST"])
@login_required
@require_csrf
def patch_generated_file(solution_id):
    """Patch a single generated file using a natural language instruction.

    Payload: { path, instruction }
    Sends the current file content + instruction to the LLM with a tight
    system prompt instructing it to return only the complete updated file.
    Returns { old_content, new_content } for client-side diff preview —
    does NOT commit to DB until the client calls PATCH /codegen/files.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    path = (payload.get("path") or "").strip()
    instruction = (payload.get("instruction") or "").strip()
    if not path or not instruction:
        return jsonify({"error": "path and instruction are required"}), 400
    instruction = re.sub(r'```', '~~~', instruction)
    instruction = re.sub(r'^###\s+FILE:', '### NOTE:', instruction, flags=re.MULTILINE)
    instruction = instruction[:2000]

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404

    if path not in gen.generated_files:
        return jsonify({"error": f"File not found: {path}"}), 404

    old_content = gen.generated_files[path]

    prompt = f"""You are editing a single file in a generated application.
Return ONLY the complete updated file content — no explanation, no markdown fences, no preamble.

## File: {path}

## Current Content
{old_content}

## Instruction
{instruction}

Return the complete updated file content now:"""

    from app.modules.ai_chat.services.llm_service import LLMService
    try:
        provider, model = LLMService._get_configured_provider()
        new_content, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
    except Exception as exc:
        logger.exception("Patch LLM call failed for %s: %s", path, exc)
        return jsonify({"error": f"LLM call failed: {str(exc)}"}), 500

    if not new_content or not new_content.strip():
        return jsonify({"error": "LLM returned empty response"}), 500

    # Strip markdown fences if the LLM added them despite instructions
    new_content = new_content.strip()
    if new_content.startswith("```"):
        lines = new_content.split("\n")
        lines = lines[1:]  # drop opening fence line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        new_content = "\n".join(lines)

    return jsonify({
        "success": True,
        "path": path,
        "old_content": old_content,
        "new_content": new_content,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/intent-plan", methods=["POST"])
@login_required
@require_csrf
def plan_change_intent(solution_id):
    """Build a scoped AI implementation plan from a natural-language intent."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    instruction = (payload.get("instruction") or "").strip()
    selected_path = (payload.get("selected_path") or "").strip()
    if not instruction:
        return jsonify({"error": "instruction is required"}), 400
    instruction = re.sub(r'```', '~~~', instruction)
    instruction = instruction[:2000]

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files"}), 404
    available_files = sorted(list((gen.generated_files or {}).keys()))
    lowered = instruction.lower()

    def _infer_risk_level() -> str:
        high_terms = [
            "security", "auth", "oauth", "jwt", "permission", "token", "delete", "drop", "migration"
        ]
        medium_terms = [
            "database", "schema", "route", "api", "validation", "middleware", "cache", "deploy"
        ]
        if any(t in lowered for t in high_terms):
            return "high"
        if any(t in lowered for t in medium_terms):
            return "medium"
        return "low"

    def _heuristic_impacted_files() -> list:
        scored = []
        tokens = [t for t in re.split(r"[^a-z0-9]+", lowered) if len(t) >= 4]
        for path in available_files:
            p = path.lower()
            score = 0
            if selected_path and path == selected_path:
                score += 8

            if any(k in lowered for k in ["auth", "jwt", "oauth", "token", "permission"]):
                if any(k in p for k in ["auth", "security", "middleware", "login"]):
                    score += 5
            if any(k in lowered for k in ["api", "endpoint", "route", "controller"]):
                if "/routes/" in p or "/api/" in p:
                    score += 4
            if any(k in lowered for k in ["schema", "model", "entity", "field", "column"]):
                if "/models/" in p or "/schemas/" in p:
                    score += 4
            if any(k in lowered for k in ["test", "pytest", "coverage", "assert"]):
                if "/tests/" in p or p.startswith("tests/"):
                    score += 4
            if any(k in lowered for k in ["docker", "deploy", "ci", "iac", "terraform", "github"]):
                if any(k in p for k in ["docker", "terraform", "github", "workflow", "compose"]):
                    score += 4

            score += sum(1 for tok in tokens if tok in p)
            if score > 0:
                scored.append((score, path))

        scored.sort(key=lambda x: (-x[0], x[1]))
        impacted = [p for _, p in scored[:6]]
        if not impacted:
            if selected_path and selected_path in available_files:
                impacted = [selected_path]
            else:
                impacted = available_files[:3]
        return impacted

    impacted = _heuristic_impacted_files()
    risk = _infer_risk_level()
    verify_steps = [
        "Run Workbench quality scan and ensure no new blueprint violations.",
        "Run Workbench tests and confirm no new failing tests for changed scope.",
        "Open API Preview and validate the affected behavior end-to-end.",
    ]
    if any("/tests/" in p or p.startswith("tests/") for p in impacted):
        verify_steps.insert(1, "Confirm updated tests cover both success and failure paths.")
    if any("docker" in p.lower() or "terraform" in p.lower() for p in impacted):
        verify_steps.append("Validate deploy/preview path after change (Docker/IaC tab).")

    default_plan = {
        "goal": instruction,
        "summary": "Apply the requested change with minimal surface area and verify before promotion.",
        "risk": risk,
        "steps": [
            "Inspect impacted files and confirm current behavior.",
            "Apply a focused implementation patch only in scoped files.",
            "Regenerate or update related artifacts if impacted by the change.",
            "Run quality checks and targeted verification before finalizing.",
        ],
        "impacted_files": impacted,
        "verify": verify_steps,
    }

    plan = default_plan
    source = "heuristic"
    try:
        from app.modules.ai_chat.services.llm_service import LLMService
        provider, model = LLMService._get_configured_provider()
        files_preview = available_files[:250]
        prompt = (
            "You are a principal software architect generating a safe implementation plan.\n"
            "Return STRICT JSON only with keys: goal, summary, risk, steps, impacted_files, verify.\n"
            "Rules:\n"
            "- risk must be low|medium|high\n"
            "- steps: 3-6 concise strings\n"
            "- verify: 3-6 concise strings\n"
            "- impacted_files must only contain paths from Available Files\n"
            "- keep changes scoped and production-safe\n\n"
            f"Intent:\n{instruction}\n\n"
            f"Selected file (if any): {selected_path or 'none'}\n\n"
            f"Available Files:\n{json.dumps(files_preview, indent=2)}\n"
        )
        raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
        if raw_text:
            raw_text = raw_text.strip()
            json_match = re.search(r"\{[\s\S]*\}", raw_text)
            candidate = json_match.group(0) if json_match else raw_text
            parsed = json.loads(candidate)

            impacted_from_llm = [
                p for p in (parsed.get("impacted_files") or [])
                if isinstance(p, str) and p in available_files
            ]
            plan = {
                "goal": str(parsed.get("goal") or instruction)[:400],
                "summary": str(parsed.get("summary") or default_plan["summary"])[:600],
                "risk": (str(parsed.get("risk") or risk).lower() if str(parsed.get("risk") or risk).lower() in {"low", "medium", "high"} else risk),
                "steps": [str(s)[:220] for s in (parsed.get("steps") or default_plan["steps"])[:6]],
                "impacted_files": impacted_from_llm[:8] or impacted,
                "verify": [str(s)[:220] for s in (parsed.get("verify") or verify_steps)[:6]],
            }
            source = "llm"
    except Exception as exc:
        logger.debug("Intent planning fallback to heuristic: %s", exc)

    # Persist plan + reset verification gate state for auditability
    try:
        cfg = dict(gen.config or {})
        cfg["_intent_plan"] = {
            "goal": plan.get("goal", instruction)[:400],
            "summary": plan.get("summary", "")[:600],
            "risk": plan.get("risk", "low"),
            "impacted_files": list(plan.get("impacted_files") or [])[:10],
            "verify": list(plan.get("verify") or [])[:10],
            "source": source,
            "created_at": _utc_now_iso(),
            "created_by_id": current_user.id,
        }
        cfg["_intent_verify"] = {
            "status": "pending",
            "verified_at": "",
            "pass_rate": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "error": "Plan created. Verification required before AI regenerate.",
        }
        gen.config = cfg
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("Failed to persist intent plan metadata: %s", exc)

    return jsonify({
        "success": True,
        "source": source,
        "plan": plan,
        "available_files_considered": len(available_files),
        "verification_required": True,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/intent-state")
@login_required
def get_intent_state(solution_id):
    """Return persisted intent/verification state for UI hydration."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        empty_stats = {
            "total_events": 0,
            "blocked": 0,
            "verified": 0,
            "override": 0,
            "override_rate": 0.0,
        }
        return jsonify({
            "success": True,
            "has_generation": False,
            "plan": None,
            "verify_state": None,
            "last_gate_event": None,
            "gate_stats": empty_stats,
            "verification_required": False,
            "regenerate_allowed": True,
            "override_required_when_blocked": False,
        })

    cfg = dict(gen.config or {})
    plan = cfg.get("_intent_plan") if isinstance(cfg.get("_intent_plan"), dict) else None
    gate_events = list(cfg.get("_intent_gate_events") or [])
    last_gate_event = gate_events[-1] if gate_events else None
    blocked_count = sum(1 for e in gate_events if (e or {}).get("mode") == "blocked")
    verified_count = sum(1 for e in gate_events if (e or {}).get("mode") == "verified")
    override_count = sum(1 for e in gate_events if (e or {}).get("mode") == "override")
    total_events = len(gate_events)
    override_rate = round((override_count / total_events) * 100, 1) if total_events else 0.0

    verified_ok, verify_state = _latest_intent_verify_state(gen, max_age_minutes=120)
    verification_required = bool(plan)
    regenerate_allowed = (not verification_required) or verified_ok

    return jsonify({
        "success": True,
        "has_generation": bool(gen.generated_files),
        "plan": plan,
        "verify_state": verify_state,
        "last_gate_event": last_gate_event,
        "gate_stats": {
            "total_events": total_events,
            "blocked": blocked_count,
            "verified": verified_count,
            "override": override_count,
            "override_rate": override_rate,
        },
        "verification_required": verification_required,
        "regenerate_allowed": regenerate_allowed,
        "override_required_when_blocked": bool(verification_required and not regenerate_allowed),
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/openapi.yaml")
@login_required
def export_openapi(solution_id):
    """Export confirmed field schemas as OpenAPI 3.1 YAML."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.uml_snapshot:
        return jsonify({"error": "Run enrichment first"}), 400

    uml = gen.uml_snapshot
    classes = uml.get("class_diagram", {}).get("classes", [])
    if not classes:
        return jsonify({"error": "No classes in UML"}), 400

    # Build OpenAPI schemas from UML classes
    type_map = {
        "int": "integer", "str": "string", "float": "number",
        "Decimal": "number", "datetime": "string", "bool": "boolean",
        "UUID": "string",
    }
    format_map = {"Decimal": "decimal", "datetime": "date-time", "UUID": "uuid"}

    schemas = {}
    for cls in classes:
        props = {}
        required = []
        for f in cls.get("fields", []):
            prop = {"type": type_map.get(f.get("type", "str"), "string")}
            fmt = format_map.get(f.get("type"))
            if fmt:
                prop["format"] = fmt
            if f.get("description"):
                prop["description"] = f["description"]
            props[f["name"]] = prop
            if not f.get("nullable", True) and not f.get("primary_key"):
                required.append(f["name"])
        schema = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        schemas[cls["name"]] = schema

    # Build paths from flows
    flows = uml.get("sequence_diagram", {}).get("flows", [])
    paths = {}
    for flow in flows:
        path = flow.get("path", "")
        method = flow.get("http_method", "GET").lower()
        if path and method:
            paths.setdefault(path, {})[method] = {
                "summary": flow.get("name", ""),
                "responses": {"200": {"description": "Success"}},
            }

    # Also add CRUD paths for each class
    for cls in classes:
        slug = cls.get("table_name") or cls["name"].lower() + "s"
        list_path = f"/api/{slug}"
        detail_path = f"/api/{slug}/{{id}}"
        ref = {"$ref": f"#/components/schemas/{cls['name']}"}
        if list_path not in paths:
            paths[list_path] = {
                "get": {"summary": f"List {cls['name']}", "responses": {"200": {"description": "Success", "content": {"application/json": {"schema": {"type": "array", "items": ref}}}}}},
                "post": {"summary": f"Create {cls['name']}", "requestBody": {"content": {"application/json": {"schema": ref}}}, "responses": {"201": {"description": "Created"}}},
            }
        if detail_path not in paths:
            paths[detail_path] = {
                "get": {"summary": f"Get {cls['name']}", "responses": {"200": {"description": "Success", "content": {"application/json": {"schema": ref}}}}},
                "put": {"summary": f"Update {cls['name']}", "requestBody": {"content": {"application/json": {"schema": ref}}}, "responses": {"200": {"description": "Updated"}}},
                "delete": {"summary": f"Delete {cls['name']}", "responses": {"204": {"description": "Deleted"}}},
            }

    import json
    openapi = {
        "openapi": "3.1.0",
        "info": {
            "title": f"{solution.name} API",
            "version": "1.0.0",
            "description": "Generated from ArchiMate architecture by Entelim Code Workbench",
        },
        "paths": paths,
        "components": {"schemas": schemas},
    }

    # Convert to YAML-like format (JSON is valid YAML)
    yaml_content = json.dumps(openapi, indent=2)

    import re as _re2
    safe_name = _re2.sub(r'[^\w\-]', '_', (solution.name or 'api').strip())[:64]
    return yaml_content, 200, {
        "Content-Type": "application/x-yaml; charset=utf-8",
        "Content-Disposition": f'attachment; filename="{safe_name}-openapi.yaml"',
    }


@codegen_bp.route("/solutions/<int:solution_id>/codegen/download")
@login_required
def download_zip(solution_id):
    """Download generated code as ZIP."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "Generate code first"}), 400

    # GAP-09: Increment download counter
    gen.download_count = (gen.download_count or 0) + 1
    db.session.commit()

    config = gen.config or {}
    repo_name = config.get("repo_name", f"solution-{solution_id}")
    import re as _re
    repo_name = _re.sub(r'[^a-zA-Z0-9_\-]', '_', (repo_name or 'project').strip('/').strip())[:64] or 'project'

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filepath, content in gen.generated_files.items():
            if '..' in filepath or filepath.startswith('/'):
                continue
            zf.writestr(f"{repo_name}/{filepath}", content)

        if config.get("include_frontend"):
            frontend_files = _generate_refine_frontend(
                # was `_stream_solution`, a variable belonging to the streaming
                # handler — copy-pasted here where only `solution` exists.
                solution.name or f"Solution {solution_id}",
                gen.uml_snapshot or {},
            )
            for filepath, content in frontend_files.items():
                if '..' in filepath or filepath.startswith('/'):
                    continue
                zf.writestr(f"{repo_name}/{filepath}", content)

    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{repo_name}.zip",
    )




# ── Terraform IaC generator ────────────────────────────────────────────────

def _build_terraform_iac(solution_id: int, genome: dict, region: str, environment: str) -> dict:
    """Generate Terraform IaC files deterministically from the genome."""
    import re as _re
    app_name = _re.sub(r"[^a-z0-9]", "-", f"solution-{solution_id}")[:40].strip("-")

    genome_vals = list(genome.values()) if isinstance(genome, dict) else []
    has_db = any(
        any(kw in str(v).lower() for kw in ("database", "postgres", "sql", " db", "model", "entity"))
        for v in genome_vals
    )
    has_cache = any(
        any(kw in str(v).lower() for kw in ("cache", "redis", "session"))
        for v in genome_vals
    )

    main_tf = f"""\
terraform {{
  required_version = ">= 1.5"
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}

  backend "s3" {{
    bucket = "{app_name}-tfstate-{environment}"
    key    = "{environment}/terraform.tfstate"
    region = "{region}"
  }}
}}

provider "aws" {{
  region = var.aws_region
}}

module "vpc" {{
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
  name    = "{app_name}-{environment}"
  cidr    = "10.0.0.0/16"
  azs             = ["{region}a", "{region}b", "{region}c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
  enable_nat_gateway   = true
  single_nat_gateway   = {"true" if environment != "production" else "false"}
  enable_dns_hostnames = true
  tags = local.common_tags
}}

resource "aws_ecs_cluster" "main" {{
  name = "{app_name}-{environment}"
  tags = local.common_tags
}}

resource "aws_lb" "main" {{
  name               = substr("{app_name}-{environment}-alb", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = module.vpc.public_subnets
  tags               = local.common_tags
}}

resource "aws_lb_target_group" "api" {{
  name        = substr("{app_name}-api-tg", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = module.vpc.vpc_id
  target_type = "ip"
  health_check {{ path = "/health"; healthy_threshold = 2; interval = 30 }}
}}

resource "aws_security_group" "alb" {{
  name_prefix = "{app_name[:20]}-alb-"
  vpc_id      = module.vpc.vpc_id
  ingress {{ from_port = 80;  to_port = 80;  protocol = "tcp"; cidr_blocks = ["0.0.0.0/0"] }}
  ingress {{ from_port = 443; to_port = 443; protocol = "tcp"; cidr_blocks = ["0.0.0.0/0"] }}
  egress  {{ from_port = 0;   to_port = 0;   protocol = "-1";  cidr_blocks = ["0.0.0.0/0"] }}
  tags = local.common_tags
}}

resource "aws_security_group" "ecs_tasks" {{
  name_prefix = "{app_name[:20]}-ecs-"
  vpc_id      = module.vpc.vpc_id
  ingress {{ from_port = 8000; to_port = 8000; protocol = "tcp"; security_groups = [aws_security_group.alb.id] }}
  egress  {{ from_port = 0;    to_port = 0;    protocol = "-1";  cidr_blocks     = ["0.0.0.0/0"] }}
  tags = local.common_tags
}}
"""

    if has_db:
        main_tf += f"""
resource "aws_db_instance" "main" {{
  identifier             = "{app_name[:36]}-{environment}"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.db_instance_class
  allocated_storage      = var.db_storage_gb
  db_name                = replace("{app_name}", "-", "_")
  username               = "dbadmin"
  password               = var.db_password
  skip_final_snapshot    = {"true" if environment != "production" else "false"}
  deletion_protection    = {"true" if environment == "production" else "false"}
  backup_retention_period = {"7" if environment == "production" else "1"}
  tags = local.common_tags
}}
"""

    if has_cache:
        main_tf += f"""
resource "aws_elasticache_replication_group" "main" {{
  replication_group_id = "{app_name[:32]}-cache"
  description          = "Redis cache for {app_name}"
  node_type            = "cache.t3.micro"
  num_cache_clusters   = {"2" if environment == "production" else "1"}
  tags                 = local.common_tags
}}
"""

    main_tf += f"""
locals {{
  common_tags = {{
    Project     = "{app_name}"
    Environment = var.environment
    ManagedBy   = "terraform"
    GeneratedBy = "Entelim"
  }}
}}
"""

    variables_tf = f"""\
variable "aws_region"   {{ type = string; default = "{region}" }}
variable "environment"  {{ type = string; default = "{environment}" }}
variable "app_image"    {{ type = string; default = "public.ecr.aws/docker/library/python:3.12-slim" }}
variable "task_cpu"     {{ type = number; default = {512 if environment != "production" else 1024} }}
variable "task_memory"  {{ type = number; default = {1024 if environment != "production" else 2048} }}
"""
    if has_db:
        variables_tf += """\
variable "db_instance_class" { type = string; default = "db.t3.micro" }
variable "db_storage_gb"     { type = number; default = 20 }
variable "db_password"       { type = string; sensitive = true }
"""

    outputs_tf = """\
output "alb_dns_name"    { value = aws_lb.main.dns_name }
output "ecs_cluster_arn" { value = aws_ecs_cluster.main.arn }
output "vpc_id"          { value = module.vpc.vpc_id }
"""
    if has_db:
        outputs_tf += 'output "db_endpoint" { value = aws_db_instance.main.endpoint; sensitive = true }\n'
    if has_cache:
        outputs_tf += 'output "redis_endpoint" { value = aws_elasticache_replication_group.main.primary_endpoint_address; sensitive = true }\n'

    return {
        "terraform/main.tf": main_tf,
        "terraform/variables.tf": variables_tf,
        "terraform/outputs.tf": outputs_tf,
        "terraform/README.md": f"# Terraform IaC — solution-{solution_id} ({environment})\n\nGenerated by Entelim\n\n```bash\nterraform init && terraform plan && terraform apply\n```\n",
    }


# generate-iac and deploy-iac routes moved to deploy_routes.py — do not re-add here

# ── Docker Live Preview ────────────────────────────────────────────────────

# docker-preview routes moved to preview_routes.py — do not re-add here


@codegen_bp.route("/solutions/<int:solution_id>/codegen/push-to-git", methods=["POST"])
@login_required
@require_csrf
def push_to_git(solution_id):
    """COM-018: Push generated files to the org's configured DevOps repository.

    Creates a branch ``arch/{slug}-{timestamp}``, commits all generated files,
    and opens a pull request.  Returns the PR URL so the UI can surface it
    as a link.

    Response (success)::

        {"success": true, "pr_url": "https://...", "branch": "arch/..."}

    Response (not configured)::

        {"success": false, "error": "DevOps connector not configured for this organisation."}
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"success": False, "error": "No generated files — run code generation first"}), 400

    org_id = getattr(solution, "organization_id", None)
    if not org_id:
        return jsonify({"success": False, "error": "Solution has no organisation — cannot look up DevOps config"}), 400

    from app.services.devops_push_service import DevOpsPushService
    svc = DevOpsPushService()

    cfg = svc._get_config(org_id)
    if not cfg or not cfg.enabled:
        return jsonify({
            "success": False,
            "error": "DevOps connector not configured for this organisation. "
                     "An admin can set it up at /admin/connectors/devops.",
        }), 400

    import re as _re
    slug = _re.sub(r"[^a-z0-9]", "-", solution.name.lower())[:40].strip("-")

    result = svc.push(org_id, solution_id, slug, dict(gen.generated_files))

    if result.get("error"):
        return jsonify({"success": False, "error": result["error"]}), 502

    return jsonify({
        "success": True,
        "pr_url": result.get("pr_url"),
        "branch": result.get("branch"),
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/history")
@login_required
def get_history(solution_id):
    """Get generation history for a solution (GAP-04)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"history": []})

    entries = CodegenGenerationHistory.query.filter_by(
        codegen_generation_id=gen.id
    ).order_by(CodegenGenerationHistory.generated_at.desc()).limit(20).all()

    return jsonify({"history": [
        {
            "generated_at": e.generated_at.isoformat() + "Z" if e.generated_at else None,
            "language": e.language,
            "mode": e.mode,
            "file_count": e.file_count,
            "version": e.version_label,
            "chain_completeness": e.chain_completeness_score,
        } for e in entries
    ]})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/quality")
@login_required
def get_quality(solution_id):
    """Get latest quality score + breakdown for a solution's generated code."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"quality_score": None, "quality_details": None})

    # Try latest history entry first
    latest = CodegenGenerationHistory.query.filter_by(
        codegen_generation_id=gen.id
    ).order_by(CodegenGenerationHistory.generated_at.desc()).first()

    if latest:
        score = getattr(latest, "quality_score", None)
        details = getattr(latest, "quality_details", None)
        chain_completeness = getattr(latest, "chain_completeness_score", None)
        # Recompute on the fly if not stored (old records before column was added)
        if score is None and gen.uml_snapshot and gen.generated_files:
            score, details = _compute_quality_score(
                gen.uml_snapshot,
                gen.generated_files,
                seed_context=(gen.config or {}).get("seed_context"),
            )
    elif gen.uml_snapshot and gen.generated_files:
        score, details = _compute_quality_score(
            gen.uml_snapshot,
            gen.generated_files,
            seed_context=(gen.config or {}).get("seed_context"),
        )
        chain_completeness = None
    else:
        score, details = None, None
        chain_completeness = None

    # Merge blueprint violations — computed during generation and cached in gen.config,
    # or re-derived here so the Quality tab always shows the Blueprint Compliance panel.
    if details is not None and gen.generated_files:
        stored_violations = (gen.config or {}).get("blueprint_violations")
        if stored_violations is not None:
            details["blueprint_violations"] = stored_violations
        elif "blueprint_violations" not in details:
            _section_narratives = getattr(solution, "section_narratives", None) or {}
            details["blueprint_violations"] = _validate_blueprint_constraints(
                gen.generated_files, _section_narratives, gen.config or {}
            )

    file_keys = list(gen.generated_files.keys()) if gen.generated_files else []
    if details is not None and gen.generated_files:
        _enrich_quality_details_for_ui(details, gen.generated_files)

    cfg = gen.config or {}
    return jsonify({
        "quality_score": score,
        "quality_details": details,
        "file_keys": file_keys,
        "chain_completeness": chain_completeness,
        "generation_policy": cfg.get("generation_policy") or "scaffold",
        "generation_mode": cfg.get("generation_mode") or "hybrid",
        "language": cfg.get("language"),
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/intelligence", methods=["POST"])
@login_required
@require_csrf
def post_codegen_intelligence(solution_id):
    """Advisory AI intelligences for the Code Workbench (requirements→tests, gap coach, triage, etc.)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip()
    payload = data.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    from app.modules.codegen.services.codegen_intelligence_service import run as run_codegen_intelligence

    out = run_codegen_intelligence(solution_id, action, payload)
    if out.get("success"):
        return jsonify(out)
    status = 400 if "error" in out else 500
    return jsonify(out), status


@codegen_bp.route("/solutions/<int:solution_id>/codegen/scan-drift", methods=["POST"])
@login_required
@require_csrf
def scan_drift(solution_id):
    """Compare current GitHub repo against last generated baseline, save a CodegenDriftReport."""
    from app.modules.codegen.models import CodegenDriftReport
    from datetime import datetime

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"error": "No code generation record found"}), 404
    if not gen.github_url:
        return jsonify({"error": "Push to GitHub first to enable drift tracking"}), 400
    if not gen.github_commit_sha:
        return jsonify({"error": "No baseline commit SHA — push to GitHub again to establish a baseline"}), 400

    gh, err = _get_github_service()
    if not gh:
        return jsonify({"error": err}), 400

    owner, repo = _parse_github_owner_repo(gen.github_url)
    if not owner or not repo:
        return jsonify({"error": f"Could not parse owner/repo from GitHub URL: {gen.github_url}"}), 400

    compare = gh.compare_commits(owner, repo, base_sha=gen.github_commit_sha)

    if not compare["success"]:
        report = CodegenDriftReport(
            solution_id=solution_id,
            status="error",
            drift_items=[],
            drift_file_count=0,
            base_commit_sha=gen.github_commit_sha,
            error_message=compare["error"],
        )
        db.session.add(report)
        gen.github_last_synced_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"success": False, "error": compare["error"]}), 502

    files = compare["files"]
    status = "drifted" if files else "clean"

    report = CodegenDriftReport(
        solution_id=solution_id,
        status=status,
        drift_items=files,
        drift_file_count=len(files),
        base_commit_sha=compare["base_sha"],
        head_commit_sha=compare["head_sha"],
    )
    db.session.add(report)
    gen.github_last_synced_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "success": True,
        "status": status,
        "drift_file_count": len(files),
        "total_commits": compare["total_commits"],
        "base_sha": compare["base_sha"],
        "head_sha": compare["head_sha"],
        "drift_items": files,
        "report_id": report.id,
        "scanned_at": report.scanned_at.isoformat() + "Z",
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/drift-report")
@login_required
def get_drift_report(solution_id):
    """Return the latest CodegenDriftReport for a solution."""
    from app.modules.codegen.models import CodegenDriftReport

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    has_github = bool(gen and gen.github_url and gen.github_commit_sha)

    report = (
        CodegenDriftReport.query
        .filter_by(solution_id=solution_id)
        .order_by(CodegenDriftReport.scanned_at.desc())
        .first()
    )

    if not report:
        return jsonify({
            "has_github": has_github,
            "report": None,
        })

    return jsonify({
        "has_github": has_github,
        "report": {
            "id": report.id,
            "status": report.status,
            "scanned_at": report.scanned_at.isoformat() + "Z" if report.scanned_at else None,
            "drift_file_count": report.drift_file_count,
            "total_commits": report.drift_file_count,  # kept for compat
            "base_commit_sha": report.base_commit_sha,
            "head_commit_sha": report.head_commit_sha,
            "drift_items": report.drift_items or [],
            "error_message": report.error_message,
        },
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/import-drift", methods=["POST"])
@login_required
@require_csrf
def import_drift(solution_id):
    """Accept drift: advance the generation baseline to the current HEAD SHA.

    This tells the system 'I know about these developer changes — treat them as
    the new baseline.' The next scan will only surface drift *after* this point.
    """
    from app.modules.codegen.models import CodegenDriftReport
    from datetime import datetime

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen:
        return jsonify({"error": "No generation record found"}), 404

    # Get latest report to find the HEAD SHA
    latest = (
        CodegenDriftReport.query
        .filter_by(solution_id=solution_id)
        .order_by(CodegenDriftReport.scanned_at.desc())
        .first()
    )

    if not latest or not latest.head_commit_sha:
        return jsonify({"error": "Run a drift scan first"}), 400
    if latest.status == "clean":
        return jsonify({"success": True, "message": "Already at baseline — no drift to import"}), 200

    old_sha = gen.github_commit_sha
    gen.github_commit_sha = latest.head_commit_sha
    gen.github_last_synced_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Baseline advanced from {old_sha[:7] if old_sha else '?'} → {latest.head_commit_sha[:7]}",
        "old_sha": old_sha,
        "new_sha": latest.head_commit_sha,
        "files_accepted": latest.drift_file_count,
    })


# -- Business Rules routes (/codegen/rules*) --
# Extracted to rules_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import rules_routes  # noqa: F401


# -- Testing & Validation Routes (Phase 4) --
# Extracted to test_exec_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import test_exec_routes  # noqa: F401


# -- Data Pipeline: Upload, Map, Import routes --
# Extracted to data_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import data_routes  # noqa: F401


# -- Version & Change Management routes --
# Extracted to versioning_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import versioning_routes  # noqa: F401


# ============================================================================
# AIC-319: DEBUG MODE - Code-Level Debugging with AI
# ============================================================================

@codegen_bp.route("/solutions/<int:solution_id>/codegen/debug-session", methods=["POST"])
@login_required
@require_csrf
def start_debug_session(solution_id):
    """
    Start a code-level debugging session with AI assistance.

    Bypasses governance gates for rapid iteration. Suitable for:
    - Fixing runtime errors
    - Debugging test failures
    - Multi-file bug fixes

    Payload: {
        "error_log": "Error message or stack trace",
        "context": {"recent_changes": [...], "files": {...}}  // optional
    }

    Returns: {
        "success": bool,
        "workspace_id": int,
        "analysis": {...},
        "response": "Human-readable analysis"
    }
    """
    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    data = request.get_json(silent=True) or {}
    error_log = data.get("error_log", "").strip()

    if not error_log:
        return jsonify({"success": False, "error": "error_log is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel
        from app.modules.ai_chat.services.debug_workflow import DebugWorkflow

        kernel = WorkbenchKernel(user_id=current_user.id)
        debug_wf = DebugWorkflow(kernel, user_id=current_user.id)

        result = debug_wf.start_debug_session(
            error_log=error_log,
            solution_id=solution_id,
            context=data.get("context"),
        )

        return jsonify(result)
    except Exception as e:
        logger.error("Debug session start failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@codegen_bp.route("/solutions/<int:solution_id>/codegen/debug-analyze", methods=["POST"])
@login_required
@require_csrf
def debug_analyze_with_llm(solution_id):
    """
    Use LLM to analyze error and suggest multi-file fixes with genome/UML context.

    Payload: {
        "workspace_id": int,
        "error_log": str,
        "files": {"path": "content", ...},
        "model": "gpt-4o" // optional
    }

    Returns: {
        "success": bool,
        "analysis": {
            "root_cause": str,
            "affected_files": [str],
            "suggested_changes": [{file, change, reason}],
            "test_command": str
        }
    }
    """
    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id")
    error_log = data.get("error_log", "").strip()
    files = data.get("files", {})

    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400
    if not error_log:
        return jsonify({"success": False, "error": "error_log is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel
        from app.modules.ai_chat.services.debug_workflow import DebugWorkflow

        kernel = WorkbenchKernel(user_id=current_user.id)
        debug_wf = DebugWorkflow(kernel, user_id=current_user.id)

        result = debug_wf.analyze_with_llm(
            workspace_id=workspace_id,
            error_log=error_log,
            files_content=files,
            requested_model=data.get("model"),
            solution_id=solution_id,
        )

        return jsonify(result)
    except Exception as e:
        logger.error("Debug LLM analysis failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@codegen_bp.route("/solutions/<int:solution_id>/codegen/debug-apply", methods=["POST"])
@login_required
@require_csrf
def debug_apply_fix(solution_id):
    """
    Apply a multi-file debug fix atomically.

    Payload: {
        "workspace_id": int,
        "file_changes": {"path": "new_content", ...},
        "explanation": "What was fixed"
    }

    Returns: {
        "success": bool,
        "files_changed": [str],
        "attempt": int
    }
    """
    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id")
    file_changes = data.get("file_changes", {})
    explanation = data.get("explanation", "Debug fix applied")

    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400
    if not file_changes:
        return jsonify({"success": False, "error": "file_changes is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel
        from app.modules.ai_chat.services.debug_workflow import DebugWorkflow

        kernel = WorkbenchKernel(user_id=current_user.id)
        debug_wf = DebugWorkflow(kernel, user_id=current_user.id)

        result = debug_wf.apply_debug_fix(
            workspace_id=workspace_id,
            file_changes=file_changes,
            explanation=explanation,
        )

        if not result.get("success"):
            return jsonify(result), 400

        gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
        if gen and gen.generated_files:
            files = dict(gen.generated_files)
            for path, content in file_changes.items():
                files[path] = content
            gen.generated_files = files
            gen.version += 1
            db.session.commit()
            result["version"] = gen.version

        return jsonify(result)
    except Exception as e:
        db.session.rollback()
        logger.error("Debug fix apply failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@codegen_bp.route("/solutions/<int:solution_id>/codegen/debug-continue", methods=["POST"])
@login_required
@require_csrf
def debug_continue(solution_id):
    """
    Continue a multi-turn debugging conversation.

    Payload: {
        "workspace_id": int,
        "message": str,
        "model": str  // optional
    }

    Returns: {
        "success": bool,
        "response": str,
        "action": str  // optional
    }
    """
    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id")
    message = data.get("message", "").strip()

    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400
    if not message:
        return jsonify({"success": False, "error": "message is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel
        from app.modules.ai_chat.services.debug_workflow import DebugWorkflow

        kernel = WorkbenchKernel(user_id=current_user.id)
        debug_wf = DebugWorkflow(kernel, user_id=current_user.id)

        result = debug_wf.continue_debugging(
            workspace_id=workspace_id,
            message=message,
            requested_model=data.get("model"),
        )

        return jsonify(result)
    except Exception as e:
        logger.error("Debug continue failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# -- Workflow Designer, Migration Export, Acceptance Criteria --
# Extracted to workflow_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import connector_routes  # noqa: F401
from app.modules.codegen.routes import workflow_routes  # noqa: F401

# -- Share Link (public, no login required) --
# Extracted to share_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import share_routes  # noqa: F401
from app.modules.codegen.routes import file_routes  # noqa: F401
from app.modules.codegen.routes import template_set_routes  # noqa: F401

# -- Preview: change-preview, mock-server, live preview, StackBlitz, Expo Snack, Docker --
# Extracted to preview_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import preview_routes  # noqa: F401

# -- Deploy: GitHub PR, IaC, Coolify, recommend-language --
# Extracted to deploy_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import deploy_routes  # noqa: F401

# -- System Boundaries: multi-solution composition, docker-compose, nginx, contracts --
# Extracted to boundary_routes.py for maintainability.
# Import triggers route registration on codegen_bp.
from app.modules.codegen.routes import boundary_routes  # noqa: F401

# -- Monaco Editor AI: routes removed (monaco_routes.py was deleted, no active callers) --

# ---------------------------------------------------------------------------
# Genome Extraction — non-technical user path (AI Chat → Genome)
# POST /api/codegen/genome/extract
# ---------------------------------------------------------------------------

from app.modules.codegen.services.genome_extraction_service import (
    GenomeExtractionService as _GenomeExtractionSvc,
)


@codegen_bp.route("/api/codegen/genome/extract", methods=["POST"])
@login_required
def genome_extract():
    """Extract genome fields from plain-English conversation.

    Body: {"conversation": [{"role": "user"|"assistant", "content": "..."}], "current_genome": {...}}
    Returns: {"success": bool, "genome_partial": {...}, "completeness_pct": int,
              "next_question": {"field": "...", "text": "...", "priority": "..."} | null,
              "ready_to_generate": bool}
    """
    data = request.get_json(silent=True) or {}
    conversation = data.get("conversation")
    current_genome = data.get("current_genome") or {}

    if not conversation or not isinstance(conversation, list) or len(conversation) == 0:
        return jsonify({"success": False, "error": "conversation is required and must be non-empty"}), 400

    result = _GenomeExtractionSvc().extract(conversation=conversation, current_genome=current_genome)

    # Validate the extracted genome before returning to the client
    if result.get("success") and result.get("genome_partial"):
        try:
            from app.modules.codegen.services.genome_validation_service import GenomeValidationService
            validation = GenomeValidationService().validate(result["genome_partial"])
            result["genome_validation"] = validation.to_dict()
        except Exception as exc:
            logger.debug("suppressed error in genome_extract (app/modules/codegen/routes/codegen_routes.py): %s", exc)  # never block extraction due to validation errors

    return jsonify(result), (200 if result.get("success") else 500)


# ---------------------------------------------------------------------------
# Genome Patch — NL instruction → JSON Patch (RFC 6902)
# POST /api/codegen/genome/patch
# POST /api/codegen/genome/patch/apply
# ---------------------------------------------------------------------------


@codegen_bp.route("/api/codegen/genome/patch", methods=["POST"])
@login_required
def genome_patch():
    """Generate a JSON Patch from a natural language instruction.

    Body: {"genome": {...}, "nl_instruction": "add export to CSV"}
    Returns: {"success": bool, "patch_ops": [...], "affected_templates": [...], "confidence": float}
    """
    from app.modules.codegen.services.genome_patch_service import GenomePatchService
    data = request.get_json(silent=True) or {}
    genome = data.get("genome")
    nl_instruction = (data.get("nl_instruction") or "").strip()
    if not genome or not isinstance(genome, dict):
        return jsonify({"success": False, "error": "genome is required and must be an object"}), 400
    if not nl_instruction:
        return jsonify({"success": False, "error": "nl_instruction is required"}), 400
    result = GenomePatchService().patch(genome=genome, nl_instruction=nl_instruction)
    return jsonify(result), (200 if result.get("success") else 422)


@codegen_bp.route("/api/codegen/genome/patch/apply", methods=["POST"])
@login_required
def genome_patch_apply():
    """Apply a JSON Patch to a genome dict.

    Body: {"genome": {...}, "patch_ops": [...]}
    Returns: {"success": bool, "genome": {...}}
    """
    from app.modules.codegen.services.genome_patch_service import GenomePatchService
    data = request.get_json(silent=True) or {}
    genome = data.get("genome")
    patch_ops = data.get("patch_ops")
    if not genome or not isinstance(genome, dict):
        return jsonify({"success": False, "error": "genome is required"}), 400
    if not patch_ops or not isinstance(patch_ops, list):
        return jsonify({"success": False, "error": "patch_ops must be a non-empty array"}), 400
    try:
        updated = GenomePatchService().apply_patch(genome=genome, patch_ops=patch_ops)
        return jsonify({"success": True, "genome": updated}), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@codegen_bp.route("/solutions/<int:solution_id>/codegen/genome-patch/store", methods=["POST"])
@login_required
def genome_patch_store(solution_id):
    """Store a patched genome on the CodegenGeneration record.

    Body: {"genome": {...}}
    Returns: {"success": true}
    """
    from app.extensions import db
    from app.modules.codegen.models import CodegenGeneration
    data = request.get_json(silent=True) or {}
    genome = data.get("genome")
    if not genome or not isinstance(genome, dict):
        return jsonify({"success": False, "error": "genome is required"}), 400
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).order_by(
        CodegenGeneration.version.desc()
    ).first()
    if not gen:
        return jsonify({"success": False, "error": "No generation found"}), 404
    gen.genome = genome
    db.session.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Monaco NL Code Editing — chat-edit SSE, apply-patch, history, selection-action, complete
# POST  /solutions/<id>/codegen/chat-edit
# POST  /solutions/<id>/codegen/chat-edit/apply-patch
# GET   /solutions/<id>/codegen/chat-edit/history
# POST  /solutions/<id>/codegen/selection-action
# POST  /solutions/<id>/codegen/complete
# ---------------------------------------------------------------------------


@codegen_bp.route("/solutions/<int:solution_id>/codegen/chat-edit", methods=["POST"])
@login_required
@require_csrf
def monaco_chat_edit(solution_id):
    """Stream SSE diffs for a natural language code edit instruction.

    Body: {instruction, context: {current_file, current_file_content, related_files[]},
           conversation_history: [{role, content}]}
    Response: text/event-stream — events: thinking, patch, complete, error
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    instruction = (payload.get("instruction") or "").strip()
    if not instruction:
        return jsonify({"error": "instruction is required"}), 400

    context = payload.get("context") or {}
    current_file = (context.get("current_file") or "").strip()
    current_file_content = (context.get("current_file_content") or "").strip()
    related_file_paths = context.get("related_files") or []
    conversation_history = payload.get("conversation_history") or []

    # If client didn't send file content, load from the latest generation
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).order_by(
        CodegenGeneration.version.desc()
    ).first()
    if not current_file_content and current_file and gen and gen.generated_files:
        current_file_content = gen.generated_files.get(current_file, "")

    # Load related file contents from stored generation
    loaded_related = []
    if related_file_paths and gen and gen.generated_files:
        for rf_path in related_file_paths[:3]:
            content = gen.generated_files.get(rf_path)
            if content:
                loaded_related.append({"path": rf_path, "content": content})

    from flask import current_app
    from app.modules.codegen.services.nl_code_editor import stream_chat_edit

    # Capture app object now — the generator runs lazily after the request context ends
    _app = current_app._get_current_object()

    def _generate():
        with _app.app_context():
            yield from stream_chat_edit(
                instruction=instruction,
                current_file=current_file,
                current_file_content=current_file_content,
                related_files=loaded_related,
                conversation_history=conversation_history,
            )

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@codegen_bp.route("/solutions/<int:solution_id>/codegen/chat-edit/apply-patch", methods=["POST"])
@login_required
@require_csrf
def monaco_apply_patch(solution_id):
    """Apply a unified diff to a stored generated file.

    Body: {file: str, diff: str, force: bool}
    Returns: {success: true, new_content: str}
          or {requires_force: true, warnings: [...]}  (destructive patterns detected)
    """
    from app.extensions import db
    from app.modules.codegen.services.ast_patch_applier import (
        apply_patch as _apply_patch,
        validate_diff_safety,
        PatchApplyError,
    )

    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    file_path = (payload.get("file") or "").strip()
    diff = (payload.get("diff") or "").strip()
    force = bool(payload.get("force", False))

    if not file_path or not diff:
        return jsonify({"error": "file and diff are required"}), 400

    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).order_by(
        CodegenGeneration.version.desc()
    ).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated files found"}), 404
    if file_path not in gen.generated_files:
        return jsonify({"error": f"File not found: {file_path}"}), 404

    if not force:
        warnings = validate_diff_safety(diff)
        if warnings:
            return jsonify({"requires_force": True, "warnings": warnings}), 200

    original = gen.generated_files[file_path]
    try:
        new_content = _apply_patch(original, diff)
    except PatchApplyError as exc:
        return jsonify({"error": str(exc)}), 422

    gen.generated_files = {**gen.generated_files, file_path: new_content}
    gen.version = (gen.version or 0) + 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Database error saving patch"}), 500

    return jsonify({"success": True, "new_content": new_content})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/chat-edit/history", methods=["GET"])
@login_required
def monaco_chat_history(solution_id):
    """Return conversation history for the Monaco NL chat panel.

    Currently returns an empty list — persistence will be added in a follow-up
    when the CodegenChatMessage model is introduced.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403
    return jsonify([])


@codegen_bp.route("/solutions/<int:solution_id>/codegen/selection-action", methods=["POST"])
@login_required
@require_csrf
def nl_selection_action(solution_id):
    """SSE stream: explain/fix/refactor/add-test on a selected code region.

    Payload: {action, file, selection_start, selection_end, selection_content, full_file_content}
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    action = (payload.get("action") or "explain").strip()
    if action not in ("explain", "fix", "refactor", "add-test"):
        return jsonify({"error": f"Unknown action: {action}"}), 400

    file_path = (payload.get("file") or "").strip()[:500]
    selection_content = (payload.get("selection_content") or "")[:8000]
    full_file_content = (payload.get("full_file_content") or "")[:40000]

    from app.modules.codegen.services.nl_code_editor import stream_selection_action

    def generate():
        yield from stream_selection_action(
            action=action,
            file=file_path,
            selection_content=selection_content,
            full_file_content=full_file_content,
            conversation_history=[],
        )

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@codegen_bp.route("/solutions/<int:solution_id>/codegen/complete", methods=["POST"])
@login_required
def nl_complete(solution_id):
    """FIM code completion at cursor position (non-streaming, fast model).

    Payload: {file, prefix_lines, suffix_lines, full_file_content}
    Response: {completion: str}
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_access(solution):
        return jsonify({"error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    file_path = (payload.get("file") or "").strip()[:500]
    prefix = (payload.get("prefix_lines") or "")[-3000:]
    suffix = (payload.get("suffix_lines") or "")[:1000]

    from app.modules.codegen.services.nl_code_editor import get_completion

    completion = get_completion(file=file_path, prefix_lines=prefix, suffix_lines=suffix)
    return jsonify({"completion": completion})

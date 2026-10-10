"""
DEPRECATED: This file is migrated to app/modules/architecture/.
Registration is now centralized via app.modules.architecture.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Architecture Assistant API Routes

Provides REST API endpoints for the Autonomous Architecture Assistant service,
enabling AI-driven solution design, vendor option generation, option comparison,
and ARB submission draft generation.

Endpoints:
- POST /api/architecture-assistant/design-solution - Design solution for capability
- POST /api/architecture-assistant/generate-options - Generate vendor options
- POST /api/architecture-assistant/compare-options - Compare solution options
- POST /api/architecture-assistant/draft-arb - Generate ARB submission draft
- GET /api/architecture-assistant/recommendations/<capability_id> - Get recommendations
- POST /api/architecture-assistant/analyze-gap - Analyze gap and suggest solutions
"""

from app.services.archimate_backbone import sync_archimate_element
import json
import logging
from collections import OrderedDict
from datetime import datetime
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, jsonify, render_template, request, session
from flask_login import current_user, login_required

from app.decorators import require_roles
from app.models.archimate_motivation import (
    MotivationAssessment,
    MotivationConstraint,
    MotivationStakeholder,
)
from app.models.capability_set import CapabilitySet
from app.models.implementation_migration import Deliverable, Plateau, WorkPackage
from app.modules.architecture.services.archimate_model_generator import (
    ArchiMateModelGenerator as ArchitectureModuleModelGenerator,
)
from app.services.archimate_model_generator import ArchiMateModelGenerator
from app.services.archimate_viewpoint_service import ArchiMateViewpointService
from app.services.architecture_assistant_service import ArchitectureAssistantService
from app.services.rate_limiter import rate_limit
from app.utils.validators import validate_integer, validate_string, validation_error_response

from app import db
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ENH-010: Simple TTL cache for frequently queried catalogs
# ---------------------------------------------------------------------------
import time as _time

_catalog_cache = {}


def _populate_junction_tables_from_options(solution, solution_options_result):
    """ENH-018: Populate solution_applications and solution_vendor_products from orchestrate options.

    Extracts application IDs and vendor product IDs from the orchestration options
    and inserts them into the respective junction tables.
    """
    if not solution_options_result or not solution_options_result.get("options"):
        return

    from app.models.solution_models import solution_applications, solution_vendor_products

    for opt in solution_options_result["options"]:
        if not isinstance(opt, dict):
            continue

        # Extract vendor_product_ids from option
        vp_ids = opt.get("vendor_product_ids") or []
        if not vp_ids and opt.get("vendor_product_id"):
            vp_ids = [opt["vendor_product_id"]]
        # Also check vendor_products list (may contain IDs or dicts)
        for vp in (opt.get("vendor_products") or []):
            if isinstance(vp, dict) and vp.get("id"):
                vp_ids.append(vp["id"])
            elif isinstance(vp, int):
                vp_ids.append(vp)

        for vp_id in vp_ids:
            try:
                vp_id_int = int(vp_id)
                existing = db.session.execute(  # tenant-filtered: scoped via parent FK (solution.id)
                    solution_vendor_products.select().where(
                        (solution_vendor_products.c.solution_id == solution.id) &
                        (solution_vendor_products.c.vendor_product_id == vp_id_int)
                    )
                ).first()
                if not existing:
                    db.session.execute(  # tenant-filtered: scoped via parent FK (solution.id)
                        solution_vendor_products.insert().values(
                            solution_id=solution.id,
                            vendor_product_id=vp_id_int,
                            implementation_type="licensed",
                        )
                    )
            except (ValueError, TypeError):
                continue

        # Extract application_ids from option
        app_ids = opt.get("application_ids") or []
        if not app_ids and opt.get("application_id"):
            app_ids = [opt["application_id"]]
        # Also check existing_apps list
        for app_ref in (opt.get("existing_apps") or []):
            if isinstance(app_ref, dict) and app_ref.get("id"):
                app_ids.append(app_ref["id"])
            elif isinstance(app_ref, int):
                app_ids.append(app_ref)

        for app_id in app_ids:
            try:
                app_id_int = int(app_id)
                existing = db.session.execute(  # tenant-filtered: scoped via parent FK (solution.id)
                    solution_applications.select().where(
                        (solution_applications.c.solution_id == solution.id) &
                        (solution_applications.c.application_component_id == app_id_int)
                    )
                ).first()
                if not existing:
                    db.session.execute(  # tenant-filtered: scoped via parent FK (solution.id)
                        solution_applications.insert().values(
                            solution_id=solution.id,
                            application_component_id=app_id_int,
                            role="supporting",
                        )
                    )
            except (ValueError, TypeError):
                continue

    logger.info("ENH-018: Junction tables populated for solution_id=%s", solution.id)


def _get_cached(key, query_fn, ttl=300):
    """Return cached data if fresh, otherwise call query_fn and cache result.

    Args:
        key: Cache key string.
        query_fn: Callable that returns the data to cache.
        ttl: Time-to-live in seconds (default 5 minutes).
    """
    entry = _catalog_cache.get(key)
    now = _time.time()
    if entry and now - entry["ts"] < ttl:
        return entry["data"]
    data = query_fn()
    _catalog_cache[key] = {"data": data, "ts": now}
    return data

# Create blueprint
architecture_assistant_bp = Blueprint(
    "architecture_assistant", __name__, url_prefix="/api/architecture-assistant"
)

# Create UI blueprint
architecture_assistant_ui_bp = Blueprint(
    "architecture_assistant_ui", __name__, url_prefix="/architecture-assistant"
)


def get_service() -> ArchitectureAssistantService:
    """Get Architecture Assistant service instance."""
    return ArchitectureAssistantService()


def _log_ai_call(action, model_name, prompt_hash=None, response_hash=None,
                 input_tokens=None, output_tokens=None, cost=None,
                 solution_id=None, wizard_step=None, error=None,
                 prompt_summary=None, reasoning=None, content_type=None):
    """Log an AI/LLM invocation to the audit log. Best-effort — never raises.

    ENH-019: Added prompt_summary, reasoning, and content_type for explainability.
    """
    try:
        from app.models.ai_audit_log import AIAuditLog
        log = AIAuditLog(
            user_id=current_user.id if current_user and current_user.is_authenticated else None,
            user_email=getattr(current_user, 'email', None) if current_user and current_user.is_authenticated else None,
            action=action,
            model_name=model_name or "unknown",
            prompt_hash=prompt_hash,
            response_hash=response_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            solution_id=solution_id,
            wizard_step=wizard_step,
            error_message=error,
            success=error is None,
            prompt_summary=prompt_summary,
            reasoning=reasoning,
            content_type=content_type,
        )
        db.session.add(log)
        db.session.commit()
        return log.id
    except Exception as e:
        logger.warning("AI audit log failed: %s", e)
    return None


_generated_models_cache = OrderedDict()
_generated_models_cache_limit = 100


def _xml_attr(value):
    if value is None:
        return ""
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _xml_text(value):
    if value is None:
        return ""
    return escape(str(value))


def _store_generated_model(model):
    model_id = model.get("id")
    if not model_id:
        return

    _generated_models_cache[model_id] = model
    _generated_models_cache.move_to_end(model_id)

    while len(_generated_models_cache) > _generated_models_cache_limit:
        _generated_models_cache.popitem(last=False)


def _append_property_xml(lines, properties, indent):
    if not isinstance(properties, dict):
        return
    for key, value in properties.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        lines.append(
            f'{indent}<property key="{_xml_attr(key)}" value="{_xml_attr(value)}" />'  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
        )


def _build_archimate_model_xml(model):
    metadata = model.get("metadata") or {}
    elements = model.get("elements") or []
    relationships = model.get("relationships") or []
    viewpoints = model.get("viewpoints") or []

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<archimate_model id="{_xml_attr(model.get("id"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
            f'name="{_xml_attr(model.get("name", "ArchiMate Model"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
            f'version="{_xml_attr(model.get("version", "3.2"))}">'  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
        ),
        "  <metadata>",
    ]

    for key, value in metadata.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        lines.append(f'    <entry key="{_xml_attr(key)}">{_xml_text(value)}</entry>')  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
    lines.append("  </metadata>")

    lines.append(f'  <elements count="{len(elements)}">')  # raw-html-ok: len(...) is always an int
    for element in elements:
        lines.append(
            (
                f'    <element id="{_xml_attr(element.get("id"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
                f'type="{_xml_attr(element.get("type"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
                f'layer="{_xml_attr(element.get("layer"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
                f'name="{_xml_attr(element.get("name"))}">'  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
            )
        )
        if element.get("description"):
            lines.append(f"      <description>{_xml_text(element.get('description'))}</description>")  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
        _append_property_xml(lines, element.get("properties"), "      ")
        lines.append("    </element>")
    lines.append("  </elements>")

    lines.append(f'  <relationships count="{len(relationships)}">')  # raw-html-ok: len(...) is always an int
    for relationship in relationships:
        lines.append(
            (
                f'    <relationship id="{_xml_attr(relationship.get("id"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
                f'type="{_xml_attr(relationship.get("type"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
                f'source="{_xml_attr(relationship.get("source"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
                f'target="{_xml_attr(relationship.get("target"))}">'  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
            )
        )
        if relationship.get("description"):
            lines.append(
                f"      <description>{_xml_text(relationship.get('description'))}</description>"  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
            )
        _append_property_xml(lines, relationship.get("properties"), "      ")
        lines.append("    </relationship>")
    lines.append("  </relationships>")

    lines.append(f'  <viewpoints count="{len(viewpoints)}">')  # raw-html-ok: len(...) is always an int
    for viewpoint in viewpoints:
        viewpoint_elements = viewpoint.get("elements") or []
        viewpoint_relationships = viewpoint.get("relationships") or []
        lines.append(
            (
                f'    <viewpoint id="{_xml_attr(viewpoint.get("id"))}" '  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
                f'name="{_xml_attr(viewpoint.get("name"))}">'  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
            )
        )
        if viewpoint.get("description"):
            lines.append(f"      <description>{_xml_text(viewpoint.get('description'))}</description>")  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
        lines.append("      <elements>")
        for element_id in viewpoint_elements:
            if isinstance(element_id, dict):
                element_id = element_id.get("id")
            lines.append(f'        <element_ref id="{_xml_attr(element_id)}" />')  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
        lines.append("      </elements>")
        lines.append("      <relationships>")
        for relationship_id in viewpoint_relationships:
            if isinstance(relationship_id, dict):
                relationship_id = relationship_id.get("id")
            lines.append(f'        <relationship_ref id="{_xml_attr(relationship_id)}" />')  # raw-html-ok: _xml_attr/_xml_text (defined above in this file) are escape() wrappers -- already safe
        lines.append("      </relationships>")
        lines.append("    </viewpoint>")
    lines.append("  </viewpoints>")

    lines.append("</archimate_model>")
    return "\n".join(lines)


# Capability Sets - DB-backed CRUD
@architecture_assistant_bp.route("/capability-sets", methods=["GET", "POST"])
@login_required
@require_roles("admin", "architect")
def capability_sets():
    try:
        user_id = current_user.id
        if request.method == "GET":
            # ENH-011: Optional pagination via page & per_page query params
            page = safe_int_arg('page', None, minimum=1)
            per_page = safe_int_arg('per_page', 50, minimum=1, maximum=500)
            per_page = min(per_page, 200)  # cap

            query = CapabilitySet.query.filter(
                (CapabilitySet.user_id == user_id) | (CapabilitySet.is_public.is_(True))
            ).order_by(CapabilitySet.created_at.desc())

            if page is not None:
                total = query.count()
                sets = [s.to_dict() for s in query.offset((page - 1) * per_page).limit(per_page).all()]
                return jsonify({"success": True, "data": sets, "total": total, "page": page, "per_page": per_page}), 200

            # Unpaginated fallback (backward compatible)
            sets = [s.to_dict() for s in query.all()]
            return jsonify({"success": True, "data": sets}), 200

        # POST -> create new set
        payload = request.get_json() or {}
        name = payload.get("name")
        capability_ids = payload.get("capability_ids", [])
        description = payload.get("description")
        is_public = bool(payload.get("is_public", False))
        if not name or not capability_ids:
            return validation_error_response("name and capability_ids are required")

        owner_id = user_id

        new_set = CapabilitySet(
            user_id=owner_id,
            name=name,
            description=description,
            capability_ids=capability_ids,
            is_public=is_public,
        )
        db.session.add(new_set)
        db.session.commit()
        return jsonify({"success": True, "data": new_set.to_dict()}), 201

    except Exception as e:
        logger.error(f"Error in capability_sets: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route("/capability-sets/<int:set_id>", methods=["GET", "DELETE", "PUT"])
@login_required
@require_roles("admin", "architect")
def capability_set_detail(set_id):
    try:
        cs = CapabilitySet.query.get(set_id)
        if not cs:
            return jsonify({"success": False, "error": "Not found"}), 404

        if cs.user_id != current_user.id and not getattr(cs, 'is_public', False):
            return jsonify({"success": False, "error": "Forbidden"}), 403

        if request.method == "GET":
            return jsonify({"success": True, "data": cs.to_dict()}), 200

        if request.method == "DELETE":
            db.session.delete(cs)
            db.session.commit()
            return jsonify({"success": True, "deleted": set_id}), 200

        # PUT -> update
        payload = request.get_json() or {}
        name = payload.get("name")
        description = payload.get("description")
        capability_ids = payload.get("capability_ids")
        is_public = payload.get("is_public")
        if name is not None:
            cs.name = name
        if description is not None:
            cs.description = description
        if capability_ids is not None:
            cs.capability_ids = capability_ids
        if is_public is not None:
            cs.is_public = bool(is_public)
        db.session.commit()
        return jsonify({"success": True, "data": cs.to_dict()}), 200

    except Exception as e:
        logger.error(f"Error handling capability_set detail {set_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# Wizard Session Persistence Endpoints (AA-006)
# =============================================================================


@architecture_assistant_bp.route("/session/<step>", methods=["POST"])
@login_required
def save_wizard_step(step):
    """Save wizard step data to server-side session with 24h TTL."""
    ALLOWED_STEPS = {"capabilities", "scope", "gap_analysis", "options", "roadmap", "arb"}
    if step not in ALLOWED_STEPS:
        return jsonify({"success": False, "error": f"Unknown step: {step}"}), 400

    data = request.get_json()
    if data is None:
        return jsonify({"success": False, "error": "No JSON data provided"}), 400

    # Build or refresh the session store
    aa_session = session.get("aa_wizard", {})
    aa_session[step] = data
    aa_session["_saved_at"] = datetime.utcnow().isoformat()
    session["aa_wizard"] = aa_session
    session.modified = True

    return jsonify({"success": True, "step": step}), 200


@architecture_assistant_bp.route("/session", methods=["GET"])
@login_required
def get_wizard_session():
    """Retrieve all wizard step data, respecting 24h TTL."""
    from datetime import timedelta

    aa_session = session.get("aa_wizard", {})
    if not aa_session:
        return jsonify({"success": True, "data": {}}), 200

    saved_at_str = aa_session.get("_saved_at")
    if saved_at_str:
        try:
            saved_at = datetime.fromisoformat(saved_at_str)
            if datetime.utcnow() - saved_at > timedelta(hours=24):
                session.pop("aa_wizard", None)
                return jsonify({"success": True, "data": {}, "expired": True}), 200
        except (ValueError, TypeError):
            logger.exception("Failed to compute saved_at")
            pass

    # Return data without internal fields
    return jsonify({
        "success": True,
        "data": {k: v for k, v in aa_session.items() if not k.startswith("_")}
    }), 200


# =============================================================================
# Solution Design Endpoints
# =============================================================================


@architecture_assistant_bp.route("/design-solution", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(10, "1h")
def design_solution():
    """
    Design a solution for a capability.
    ---
    tags:
      - Architecture Assistant
    summary: Design solution for capability
    description: |
      AI-driven solution design that generates multiple options with pros/cons
      for a specific business capability. Integrates with vendor analysis and
      provides recommendations.
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - capability_id
          properties:
            capability_id:
              type: integer
              description: ID of the target capability
            requirements:
              type: object
              description: Optional requirements specification
              properties:
                min_coverage:
                  type: number
                  description: Minimum required coverage percentage
                must_have_features:
                  type: array
                  items:
                    type: string
            constraints:
              type: object
              description: Optional constraints
              properties:
                budget:
                  type: number
                  description: Maximum budget constraint
                timeline_weeks:
                  type: integer
                  description: Maximum implementation timeline
            include_vendor_analysis:
              type: boolean
              default: true
              description: Include vendor product analysis
    responses:
      200:
        description: Solution design with options and recommendations
        schema:
          type: object
          properties:
            capability_id:
              type: integer
            capability_name:
              type: string
            options:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                  name:
                    type: string
                  vendor_name:
                    type: string
                  total_score:
                    type: number
                  pros:
                    type: array
                    items:
                      type: string
                  cons:
                    type: array
                    items:
                      type: string
            recommendation:
              type: object
      400:
        description: Invalid request parameters
      404:
        description: Capability not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("Request body is required")

        # Validate capability_id
        capability_id = data.get("capability_id")
        is_valid, validated_id, error = validate_integer(
            capability_id, min_val=1, field_name="capability_id"
        )
        if not is_valid:
            return validation_error_response(error)

        # Extract optional parameters
        requirements = data.get("requirements")
        constraints = data.get("constraints")
        include_vendor_analysis = data.get("include_vendor_analysis", True)

        # Gather RAG context (best-effort — never crashes the endpoint).
        # Bound before the try: retrieval failure must leave these defined.
        _rag_context = ""
        rag_ctx = None
        try:
            from app.services.architecture_rag_service import ArchitectureRAGService
            rag_svc = ArchitectureRAGService()
            rag_ctx = rag_svc.get_context_for_solution(
                business_domain=data.get("business_domain", ""),
                capability_ids=[validated_id],
            )
            _rag_context = rag_svc.format_context(rag_ctx)
        except Exception as e:
            logger.warning("RAG context retrieval failed in design-solution: %s", e)

        # Design solution
        service = get_service()
        result = service.design_solution(
            capability_id=validated_id,
            requirements=requirements,
            constraints=constraints,
            include_vendor_analysis=include_vendor_analysis,
            # The structured dict, not the formatted string: the ranker matches
            # option text against individual principles and prior decisions, and
            # needs them as rows to name which one matched.
            rag_context=rag_ctx,
        )
        # design_solution() cannot consume rag_context: it is deterministic — it
        # ranks vendor/build/hybrid options from the capability, vendors and
        # constraints — with no LLM prompt for a text blob to enter. Passing it
        # raised TypeError, which is why it was dropped.
        #
        # But get_context_for_solution() runs four SQL queries (principles, prior
        # ARB decisions, reference architectures, solution patterns) on every
        # request, and formatting then discarding the result made all four pure
        # waste. Returning it alongside the design costs nothing further and gives
        # the caller the governance context the retrieval was written to surface.
        # Purely additive: no existing field changes.
        if isinstance(result, dict) and _rag_context:
            result.setdefault("rag_context", _rag_context)

        _log_ai_call(
            action="design_solution",
            model_name=result.get("model", "unknown") if isinstance(result, dict) else "unknown",
            input_tokens=result.get("tokens", {}).get("input") if isinstance(result, dict) else None,
            output_tokens=result.get("tokens", {}).get("output") if isinstance(result, dict) else None,
            wizard_step=1,
            error=result.get("error") if isinstance(result, dict) else None,
        )

        if "error" in result:
            if "not found" in result["error"].lower():
                return jsonify({"success": False, "error": result["error"]}), 404
            return jsonify({"success": False, "error": result["error"]}), 400

        return jsonify({"success": True, **result}), 200

    except Exception as e:
        logger.error(f"Error in design_solution endpoint: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route("/generate-problem", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(10, "1h")
def wizard_generate_problem():
    """Generate a TOGAF-style problem statement from solution scope inputs."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:200]
    domain = (data.get("domain") or "").strip()[:200]
    raw_applications = data.get("applications") or []
    raw_drivers = data.get("drivers") or []

    if not name:
        return jsonify({"error": "Solution name is required"}), 400

    # Validate and sanitize array inputs
    if not isinstance(raw_applications, list):
        return jsonify({"error": "applications must be an array"}), 400
    if not isinstance(raw_drivers, list):
        return jsonify({"error": "drivers must be an array"}), 400
    if len(raw_applications) > 20:
        return jsonify({"error": "applications array exceeds maximum of 20 items"}), 400
    if len(raw_drivers) > 20:
        return jsonify({"error": "drivers array exceeds maximum of 20 items"}), 400

    applications = [str(a)[:200] for a in raw_applications if isinstance(a, str)]
    drivers = [str(d)[:200] for d in raw_drivers if isinstance(d, str)]

    # Gather RAG context (best-effort — never crashes the endpoint)
    rag_text = ""
    try:
        from app.services.architecture_rag_service import ArchitectureRAGService
        rag_svc = ArchitectureRAGService()
        rag_ctx = rag_svc.get_context_for_solution(business_domain=domain)
        rag_text = rag_svc.format_context(rag_ctx)
    except Exception as e:
        logger.warning("RAG context retrieval failed in generate-problem: %s", e)

    # Try LLM generation
    try:
        from flask import current_app
        llm_key = current_app.config.get("LLM_API_KEY") or current_app.config.get("OPENAI_API_KEY")
        if llm_key:
            from app.services.llm_service import get_llm_service
            llm = get_llm_service()
            prompt = (
                f"Write a concise TOGAF-style architecture problem statement (3-5 sentences) for:\n"
                f"Solution: {name}\n"
                f"Business Domain: {domain}\n"
            )
            if applications:
                prompt += f"Current Applications: {', '.join(applications[:10])}\n"
            if drivers:
                prompt += f"Key Drivers: {', '.join(drivers[:10])}\n"
            if rag_text:
                prompt += f"\n--- Organizational Context ---\n{rag_text}\n---\n"
            prompt += (
                "\nThe problem statement should identify the current state issues, "
                "the business impact, and what needs to change. Be specific and actionable."
            )
            # Scrub PII before sending to LLM
            try:
                from app.modules.ai_chat.services.llm_service_impl import _scrub_prompt
                prompt = _scrub_prompt(prompt)
            except ImportError:
                logger.exception("Failed to operation")
                pass
            result = llm.generate(prompt)
            _log_ai_call(
                action="wizard_generate_problem",
                model_name=getattr(llm, 'model_name', 'unknown'),
                wizard_step=1,
                error=None if result else "empty_response",
            )
            if result:
                return jsonify({"problem_statement": result, "ai_generated": True})
    except Exception as exc:
        _log_ai_call(
            action="wizard_generate_problem",
            model_name="unknown",
            wizard_step=1,
            error=str(exc),
        )
        logger.warning("LLM problem statement generation failed: %s", exc)

    # Fallback: template-based problem statement
    parts = [f"The {domain or 'enterprise'} domain requires a solution architecture for {name}."]
    if drivers:
        parts.append(f"Key drivers include {', '.join(drivers[:5])}.")
    if applications:
        parts.append(
            f"The current landscape includes {len(applications)} application(s) "
            f"({', '.join(applications[:3])}) that must be assessed for alignment."
        )
    parts.append(
        "This initiative aims to define the target architecture, identify capability gaps, "
        "and propose implementation options aligned with enterprise strategy."
    )
    return jsonify({"problem_statement": " ".join(parts), "ai_generated": False})


def _extract_quality_constraints(qb):
    """Convert quality_baseline_data into a constraints dict for vendor option scoring."""
    constraints = {}
    dr = qb.get("deployment_release", {})
    ia = qb.get("identity_access", {})
    intg = qb.get("integration", {})
    sc = qb.get("security_controls", {})
    rel = qb.get("reliability", {})

    if dr.get("hosting"):
        constraints["preferred_hosting"] = dr["hosting"]
    if dr.get("containerized"):
        constraints["requires_containerisation"] = True
    if ia.get("auth_provider"):
        constraints["auth_provider"] = ia["auth_provider"]
    if intg.get("api_style"):
        constraints["required_api_styles"] = intg["api_style"] if isinstance(intg["api_style"], list) else [intg["api_style"]]
    if sc.get("data_classification") in ("confidential", "restricted", "secret"):
        constraints["security_tier"] = "high"
    if rel.get("availability_target"):
        try:
            constraints["min_availability"] = float(rel["availability_target"])
        except (ValueError, TypeError):
            pass
    return constraints


@architecture_assistant_bp.route("/generate-options", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(10, "1h")
def generate_options():
    """
    Generate vendor options for a capability.
    ---
    tags:
      - Architecture Assistant
    summary: Generate vendor options
    description: |
      Generates vendor product options for a specific capability with scoring
      and analysis. Can use existing analysis or generate new options.
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - capability_id
          properties:
            capability_id:
              type: integer
              description: ID of the target capability
            constraints:
              type: object
              description: Optional constraints for filtering options
            max_options:
              type: integer
              default: 5
              description: Maximum number of options to generate
    responses:
      200:
        description: Vendor options with analysis
        schema:
          type: object
          properties:
            capability_id:
              type: integer
            options:
              type: array
              items:
                type: object
            analysis_source:
              type: string
              enum: [existing, generated]
      400:
        description: Invalid request
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("Request body is required")

        # Wizard path: solution_id + gaps — generate options for each gap capability
        solution_id = data.get("solution_id")
        gaps = data.get("gaps") or []
        if solution_id and gaps:
            from app.models.solution_models import Solution as _Sol
            sol = _Sol.query.get(solution_id)
            qb = sol.quality_baseline_data if sol else {}
            # Build quality constraints from quality baseline
            quality_constraints = _extract_quality_constraints(qb or {})
            # Merge with any explicit constraints from wizard scope
            explicit = data.get("constraints") or []
            if explicit:
                quality_constraints["scope_constraints"] = explicit

            service = get_service()
            all_options = []
            for gap in gaps[:6]:  # cap at 6 gaps to avoid timeout
                if not isinstance(gap, dict):
                    continue
                cap_id = gap.get("capability_id")
                if not cap_id:
                    continue
                is_valid, validated_id, _err = validate_integer(cap_id, min_val=1, field_name="capability_id")
                if not is_valid:
                    continue
                result = service.generate_vendor_options(
                    capability_id=validated_id,
                    constraints=quality_constraints,
                    max_options=3,
                )
                for opt in result.get("options", []):
                    opt["gap_capability_id"] = validated_id
                    opt["gap_severity"] = gap.get("severity", "medium")
                all_options.extend(result.get("options", []))

            _log_ai_call(action="generate_options_wizard", model_name="template", wizard_step=4, solution_id=solution_id)
            return jsonify({"success": True, "options": all_options, "analysis_source": "quality_baseline_constrained"}), 200

        # Legacy capability-based path
        capability_id = data.get("capability_id")
        is_valid, validated_id, error = validate_integer(
            capability_id, min_val=1, field_name="capability_id"
        )
        if not is_valid:
            return validation_error_response(error)

        constraints = data.get("constraints")
        max_options = data.get("max_options", 5)

        service = get_service()
        result = service.generate_vendor_options(
            capability_id=validated_id, constraints=constraints, max_options=max_options
        )

        _log_ai_call(
            action="generate_options",
            model_name=result.get("model", "unknown") if isinstance(result, dict) else "unknown",
            input_tokens=result.get("tokens", {}).get("input") if isinstance(result, dict) else None,
            output_tokens=result.get("tokens", {}).get("output") if isinstance(result, dict) else None,
            wizard_step=3,
            error=result.get("error") if isinstance(result, dict) else None,
        )

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 400

        return jsonify({"success": True, **result}), 200

    except Exception as e:
        logger.error(f"Error in generate_options endpoint: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Architecture assistant service temporarily unavailable. Please try again.", "partial_result": None}), 503


@architecture_assistant_bp.route("/compare-options", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def compare_options():
    """
    Compare solution options.
    ---
    tags:
      - Architecture Assistant
    summary: Compare solution options
    description: |
      Compares multiple solution options with detailed analysis, scoring,
      and insights. Supports both database-stored options and custom options.
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            option_ids:
              type: array
              items:
                type: integer
              description: List of VendorOption IDs from database
            options_data:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                  name:
                    type: string
                  vendor_name:
                    type: string
                  cost_estimate:
                    type: number
                  capability_coverage:
                    type: number
                  pros:
                    type: array
                    items:
                      type: string
                  cons:
                    type: array
                    items:
                      type: string
              description: Custom option data for comparison
            weights:
              type: object
              description: Custom scoring weights
              properties:
                cost:
                  type: number
                capability_coverage:
                  type: number
                risk:
                  type: number
                strategic_fit:
                  type: number
                implementation:
                  type: number
    responses:
      200:
        description: Comparison results with matrix and insights
        schema:
          type: object
          properties:
            options:
              type: array
            comparison_matrix:
              type: object
            insights:
              type: array
            winner:
              type: object
      400:
        description: Invalid request or no options provided
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("Request body is required")

        option_ids = data.get("option_ids")
        options_data = data.get("options_data")
        weights = data.get("weights")

        if not option_ids and not options_data:
            return validation_error_response("Either option_ids or options_data must be provided")

        service = get_service()
        result = service.compare_options(
            option_ids=option_ids, options_data=options_data, weights=weights
        )

        _log_ai_call(
            action="compare_options",
            model_name=result.get("model", "unknown") if isinstance(result, dict) else "unknown",
            input_tokens=result.get("tokens", {}).get("input") if isinstance(result, dict) else None,
            output_tokens=result.get("tokens", {}).get("output") if isinstance(result, dict) else None,
            wizard_step=4,
            error=result.get("error") if isinstance(result, dict) else None,
        )

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 400

        return jsonify({"success": True, **result}), 200

    except Exception as e:
        logger.error(f"Error in compare_options endpoint: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# ARB Draft Generation Endpoints
# =============================================================================


def _fmt_list(items):
    """Render a list as a comma-separated string, or empty string."""
    if not items:
        return ""
    if isinstance(items, list):
        return ", ".join(str(i) for i in items if i)
    return str(items)


def _build_arb_draft_from_solution(solution):
    """Build a structured ARB draft directly from solution quality_baseline_data.

    Returns a dict with the same keys as arbDraft in the wizard frontend.
    All sections are pre-populated — the architect reviews and edits before submission.
    """
    qb = solution.quality_baseline_data or {}
    ia = qb.get("identity_access", {})
    dm = qb.get("data_management", {})
    sc = qb.get("security_controls", {})
    intg = qb.get("integration", {})
    obs = qb.get("observability", {})
    rel = qb.get("reliability", {})
    perf = qb.get("performance", {})
    dr = qb.get("deployment_release", {})
    ca = qb.get("compliance_audit", {})
    ops = qb.get("operational_support", {})
    tq = qb.get("testing_quality", {})
    cost = qb.get("cost_sustainability", {})

    # ── Business Justification ────────────────────────────────────────────────
    bj = "## Business Justification\n\n"
    bj += f"**Solution:** {solution.name}\n"
    if solution.business_domain:
        bj += f"**Domain:** {solution.business_domain}\n"
    bj += "\n"
    if solution.description:
        bj += f"{solution.description}\n\n"
    bj += "This solution addresses the capability gaps identified in the Architecture Vision phase "
    bj += "and delivers measurable business outcomes aligned with the enterprise architecture roadmap."
    if ca.get("regulatory_frameworks"):
        bj += f"\n\n**Regulatory drivers:** {_fmt_list(ca['regulatory_frameworks'])}"

    # ── Technical Assessment ──────────────────────────────────────────────────
    ta = "## Technical Assessment\n\n"

    if ia.get("auth_provider"):
        ta += f"**Identity & Access:** {ia['auth_provider']} ({ia.get('authz_model', 'RBAC')})"
        extras = []
        if ia.get("mfa_required"):
            extras.append("MFA enforced")
        if ia.get("sso_required"):
            extras.append("SSO required")
        if extras:
            ta += f" — {', '.join(extras)}"
        ta += "\n"

    if dm.get("primary_db"):
        ta += f"**Data Management:** {dm['primary_db']}"
        if dm.get("backup_frequency"):
            ta += f", {dm['backup_frequency']} backups"
        if dm.get("rto_hours"):
            ta += f", RTO {dm['rto_hours']}h"
        if dm.get("rpo_hours"):
            ta += f", RPO {dm['rpo_hours']}h"
        if dm.get("data_retention_years"):
            ta += f", {dm['data_retention_years']}-year retention"
        ta += "\n"

    if sc.get("data_classification"):
        ta += f"**Security Controls:** {sc['data_classification'].title()} data classification"
        ctrl = []
        if sc.get("encryption_at_rest"):
            ctrl.append("encrypted at rest")
        if sc.get("encryption_in_transit"):
            ctrl.append("TLS in transit")
        if sc.get("secret_management"):
            ctrl.append(sc["secret_management"])
        if sc.get("vulnerability_scanning"):
            ctrl.append("automated scanning")
        if ctrl:
            ta += f" — {', '.join(ctrl)}"
        ta += "\n"

    if intg.get("api_style"):
        ta += f"**Integration:** {_fmt_list(intg['api_style'])}"
        if intg.get("api_gateway"):
            ta += f" via {intg['api_gateway']}"
        if intg.get("rate_limiting"):
            ta += ", rate-limited"
        if intg.get("event_streaming"):
            ta += ", event streaming"
        ta += "\n"

    if obs.get("logging_platform"):
        ta += f"**Observability:** {obs['logging_platform']}"
        features = []
        if obs.get("metrics_enabled"):
            features.append("metrics")
        if obs.get("distributed_tracing"):
            features.append("distributed tracing")
        if obs.get("alerting_required"):
            features.append("alerting")
        if features:
            ta += f" ({', '.join(features)})"
        ta += "\n"

    if rel.get("availability_target"):
        ta += f"**Reliability:** {rel['availability_target']}% availability"
        if rel.get("redundancy_model"):
            ta += f", {rel['redundancy_model']}"
        if rel.get("auto_scaling"):
            ta += ", auto-scaling"
        if rel.get("dr_strategy"):
            ta += f", {rel['dr_strategy']} DR"
        ta += "\n"

    if dr.get("hosting"):
        ta += f"**Deployment:** {dr['hosting'].upper()}"
        if dr.get("containerized"):
            ta += " (containerised)"
        if dr.get("deployment_pattern"):
            ta += f", {dr['deployment_pattern']} deployments"
        if dr.get("ci_cd_platform"):
            ta += f" via {dr['ci_cd_platform']}"
        ta += "\n"

    if perf.get("p99_latency_ms") or perf.get("peak_rps"):
        ta += "**Performance targets:**"
        if perf.get("p99_latency_ms"):
            ta += f" P99 ≤ {perf['p99_latency_ms']}ms"
        if perf.get("peak_rps"):
            ta += f", peak {perf['peak_rps']} RPS"
        if perf.get("caching_strategy") and perf["caching_strategy"] != "":
            ta += f", {perf['caching_strategy']} caching"
        ta += "\n"

    # ── Risk Analysis ─────────────────────────────────────────────────────────
    ra = "## Risk Analysis\n\n| Risk | Probability | Impact | Mitigation |\n|------|------------|--------|------------|\n"
    ra += "| Integration complexity | Medium | High | Phased delivery with contract testing |\n"
    ra += "| Scope creep | Low | High | Change control via ARB |\n"
    ra += "| Resource availability | Medium | Medium | Early resource planning |\n"

    # Quality-baseline-derived risks
    cls = sc.get("data_classification", "")
    if cls in ("confidential", "restricted", "secret"):
        if not sc.get("vulnerability_scanning"):
            ra += f"| No vulnerability scanning ({cls} data) | High | Critical | Implement automated scanning pipeline before go-live |\n"
        if not sc.get("encryption_at_rest"):
            ra += f"| {cls.title()} data unencrypted at rest | Medium | Critical | Enable storage encryption immediately |\n"

    avail = str(rel.get("availability_target", ""))
    redund = rel.get("redundancy_model", "")
    if avail in ("99.99", "99.999") and redund == "single":
        ra += f"| Availability gap ({avail}% target with single-instance) | High | High | Upgrade to active-active or multi-region architecture |\n"

    frameworks = ca.get("regulatory_frameworks") or []
    if frameworks:
        ra += f"| Regulatory non-compliance ({_fmt_list(frameworks)}) | Low | Critical | Engage compliance team for formal gap assessment |\n"

    if ca.get("data_sovereignty_required") and dr.get("hosting") == "multi-cloud":
        ra += "| Data sovereignty conflict (multi-cloud + sovereignty requirement) | Medium | High | Pin workloads to approved regions via policy enforcement |\n"

    support_tier = ops.get("support_tier", "")
    if avail in ("99.99", "99.999") and support_tier in ("", "business-hours"):
        ra += f"| Support tier insufficient for {avail}% SLA | Medium | High | Upgrade to 24x7 NOC support |\n"

    # ── Implementation Approach ───────────────────────────────────────────────
    impl = "## Implementation Approach\n\n### Phased Delivery\n\n"
    impl += "#### Phase 1 — Foundation\n"
    if dr.get("hosting"):
        impl += f"- Provision {dr['hosting'].upper()} environment\n"
    if dr.get("containerized"):
        impl += "- Container platform setup (Docker / Kubernetes)\n"
    if ia.get("auth_provider"):
        impl += f"- Identity provider integration ({ia['auth_provider']})\n"
    sec_items = []
    if sc.get("encryption_at_rest"):
        sec_items.append("storage encryption")
    if sc.get("encryption_in_transit"):
        sec_items.append("TLS")
    if sc.get("secret_management"):
        sec_items.append(sc["secret_management"])
    if sec_items:
        impl += f"- Security baseline: {', '.join(sec_items)}\n"
    impl += "\n"

    impl += "#### Phase 2 — Core Build\n"
    if intg.get("api_style"):
        impl += f"- API layer ({_fmt_list(intg['api_style'])})"
        if intg.get("api_gateway"):
            impl += f" via {intg['api_gateway']}"
        impl += "\n"
    if dm.get("primary_db"):
        impl += f"- Data layer ({dm['primary_db']}, {dm.get('backup_frequency', 'daily')} backups)\n"
    if obs.get("logging_platform"):
        impl += f"- Observability stack ({obs['logging_platform']})\n"
    impl += "\n"

    impl += "#### Phase 3 — Testing & Go-Live\n"
    test_items = []
    if tq.get("unit_coverage_target_pct"):
        test_items.append(f"unit coverage ≥ {tq['unit_coverage_target_pct']}%")
    if tq.get("e2e_tests_required"):
        test_items.append("E2E")
    if tq.get("performance_tests_required"):
        test_items.append("load testing")
    if tq.get("security_tests_required"):
        test_items.append("DAST/SAST")
    if test_items:
        impl += f"- Testing: {', '.join(test_items)}\n"
    if dr.get("ci_cd_platform"):
        impl += f"- CI/CD pipeline validation ({dr['ci_cd_platform']})\n"
    if dr.get("deployment_pattern"):
        impl += f"- {dr['deployment_pattern'].title()} deployment to production\n"
    impl += "\n### Success Criteria\n"
    if rel.get("availability_target"):
        impl += f"- Sustained {rel['availability_target']}% availability\n"
    if perf.get("p99_latency_ms"):
        impl += f"- P99 latency ≤ {perf['p99_latency_ms']}ms\n"
    if tq.get("unit_coverage_target_pct"):
        impl += f"- Test coverage ≥ {tq['unit_coverage_target_pct']}%\n"

    # ── Cost summary ──────────────────────────────────────────────────────────
    if cost.get("monthly_budget_usd"):
        try:
            cost_summary = f"${int(float(cost['monthly_budget_usd'])):,}/month ({cost.get('budget_model', 'TBD')})"
        except (ValueError, TypeError):
            cost_summary = "To be determined"
    else:
        cost_summary = "To be determined"

    return {
        "business_justification": bj.strip(),
        "technical_assessment": ta.strip(),
        "risk_analysis": ra.strip(),
        "implementation_approach": impl.strip(),
        "cost_summary": cost_summary,
        "ai_generated": True,
        "approval_status": "pending_review",
        "quality_baseline_sourced": True,
    }


def _detect_quality_contradictions(qb):
    """Return a list of contradiction/risk warnings from quality_baseline_data."""
    warnings = []
    if not qb:
        return warnings

    rel = qb.get("reliability", {})
    sc = qb.get("security_controls", {})
    dr = qb.get("deployment_release", {})
    ca = qb.get("compliance_audit", {})
    ops = qb.get("operational_support", {})
    intg = qb.get("integration", {})

    avail = str(rel.get("availability_target", ""))
    redund = rel.get("redundancy_model", "")
    cls = sc.get("data_classification", "")
    support = ops.get("support_tier", "")
    dr_strat = rel.get("dr_strategy", "")

    # Availability vs redundancy
    if avail in ("99.99", "99.999") and redund == "single":
        warnings.append({
            "domain": "reliability",
            "severity": "error",
            "message": f"{avail}% availability requires active-active or multi-region redundancy — single instance cannot meet this SLA.",
        })

    # High availability vs weak DR
    if avail in ("99.99", "99.999") and dr_strat == "backup-restore":
        warnings.append({
            "domain": "reliability",
            "severity": "warning",
            "message": f"Backup-restore DR is incompatible with {avail}% availability — RTO will exceed the SLA window. Use warm-standby or multi-site.",
        })

    # Sensitive data without encryption
    if cls in ("confidential", "restricted", "secret") and not sc.get("encryption_at_rest"):
        warnings.append({
            "domain": "security_controls",
            "severity": "error",
            "message": f"{cls.title()} data without encryption at rest is a critical security gap.",
        })

    # Sensitive data without scanning
    if cls in ("confidential", "restricted", "secret") and not sc.get("vulnerability_scanning"):
        warnings.append({
            "domain": "security_controls",
            "severity": "warning",
            "message": f"Vulnerability scanning is required for {cls} data classifications.",
        })

    # High availability without adequate support
    if avail in ("99.99", "99.999") and support in ("", "business-hours", "extended"):
        warnings.append({
            "domain": "operational_support",
            "severity": "warning",
            "message": f"{avail}% SLA requires 24x7 NOC support — business hours or extended cover is insufficient.",
        })

    # Data sovereignty + multi-cloud
    if ca.get("data_sovereignty_required") and dr.get("hosting") == "multi-cloud":
        warnings.append({
            "domain": "compliance_audit",
            "severity": "warning",
            "message": "Data sovereignty requirement conflicts with multi-cloud hosting — data residency must be pinned to compliant regions.",
        })

    # Rate limiting without API gateway
    if intg.get("rate_limiting") and not intg.get("api_gateway"):
        warnings.append({
            "domain": "integration",
            "severity": "warning",
            "message": "Rate limiting is selected but no API gateway is specified — rate limiting requires a gateway to enforce policies.",
        })

    return warnings


@architecture_assistant_bp.route("/draft-arb", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def draft_arb():
    """
    Generate ARB submission draft.
    ---
    tags:
      - Architecture Assistant
    summary: Generate ARB submission draft
    description: |
      Generates a complete ARB (Architecture Review Board) submission draft
      with pre-filled content including business justification, technical
      assessment, risk analysis, and cost estimates.
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - capability_id
          properties:
            capability_id:
              type: integer
              description: ID of the target capability
            recommended_option:
              type: object
              description: The recommended solution option
              properties:
                id:
                  type: string
                name:
                  type: string
                vendor_name:
                  type: string
                description:
                  type: string
                cost_estimate:
                  type: number
                pros:
                  type: array
                  items:
                    type: string
                cons:
                  type: array
                  items:
                    type: string
            alternative_options:
              type: array
              items:
                type: object
              description: List of alternative options considered
            additional_context:
              type: object
              description: Additional context for the submission
    responses:
      200:
        description: ARB submission draft
        schema:
          type: object
          properties:
            draft:
              type: object
              properties:
                title:
                  type: string
                description:
                  type: string
                review_type:
                  type: string
                business_justification:
                  type: string
                technical_assessment:
                  type: string
                risk_analysis:
                  type: string
                cost_estimates:
                  type: object
            next_steps:
              type: array
              items:
                type: string
      400:
        description: Invalid request
      404:
        description: Capability not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("Request body is required")

        # Wizard path: solution_id supplied — build draft from quality baseline + solution scope
        solution_id = data.get("solution_id")
        if solution_id:
            from app.models.solution_models import Solution as _Sol
            sol = _Sol.query.get(solution_id)
            if not sol:
                return jsonify({"error": "Solution not found"}), 404
            if sol.created_by_id != current_user.id:
                return jsonify({"error": "You do not own this solution"}), 403
            draft = _build_arb_draft_from_solution(sol)
            _log_ai_call(action="draft_arb_wizard", model_name="template", wizard_step=6, solution_id=solution_id)
            return jsonify({"draft": draft, "ai_generated": True, "message": "ARB draft generated from quality baseline"}), 200

        # Legacy capability-based path
        capability_id = data.get("capability_id")
        is_valid, validated_id, error = validate_integer(
            capability_id, min_val=1, field_name="capability_id"
        )
        if not is_valid:
            return validation_error_response(error)

        recommended_option = data.get("recommended_option")
        alternative_options = data.get("alternative_options")
        additional_context = data.get("additional_context") or {}

        # Gather RAG context (best-effort — never crashes the endpoint)
        try:
            from app.services.architecture_rag_service import ArchitectureRAGService
            rag_svc = ArchitectureRAGService()
            rag_ctx = rag_svc.get_context_for_solution(
                business_domain=data.get("business_domain", ""),
                capability_ids=[validated_id],
            )
            rag_text = rag_svc.format_context(rag_ctx)
            if rag_text:
                if not isinstance(additional_context, dict):
                    additional_context = {}
                additional_context["rag_context"] = rag_text
        except Exception as e:
            logger.warning("RAG context retrieval failed in draft-arb: %s", e)

        service = get_service()
        result = service.generate_arb_draft(
            capability_id=validated_id,
            recommended_option=recommended_option,
            alternative_options=alternative_options,
            additional_context=additional_context,
        )

        _log_ai_call(
            action="draft_arb",
            model_name=result.get("model", "unknown") if isinstance(result, dict) else "unknown",
            input_tokens=result.get("tokens", {}).get("input") if isinstance(result, dict) else None,
            output_tokens=result.get("tokens", {}).get("output") if isinstance(result, dict) else None,
            wizard_step=5,
            error=result.get("error") if isinstance(result, dict) else None,
        )

        if "error" in result:
            if "not found" in result["error"].lower():
                return jsonify({"success": False, "error": result["error"]}), 404
            return jsonify({"success": False, "error": result["error"]}), 400

        return jsonify({"success": True, **result}), 200

    except Exception as e:
        logger.error(f"Error in draft_arb endpoint: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Architecture assistant service temporarily unavailable. Please try again.", "partial_result": None}), 503


# =============================================================================
# Apply Option Endpoint
# =============================================================================


@architecture_assistant_bp.route("/apply-option", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def apply_option():
    """
    Apply a solution option to a canvas.
    ---
    tags:
      - Architecture Assistant
    summary: Apply solution option to canvas
    description: |
      Applies a selected solution option to the specified canvas by executing
      the required actions (adding nodes/connections), then validates the canvas
      and returns the updated validation state.
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - option_id
            - canvas_id
            - user_id
          properties:
            option_id:
              type: string
              description: ID of the option to apply
            canvas_id:
              type: integer
              description: ID of the canvas to apply to
            user_id:
              type: integer
              description: ID of the user applying the option
            request_id:
              type: string
              description: Optional request ID for tracking
    responses:
      200:
        description: Option applied successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: true
            data:
              type: object
              properties:
                canvas_id:
                  type: integer
                validation_result:
                  type: object
                applied_changes:
                  type: array
                  items:
                    type: object
                audit_id:
                  type: string
      400:
        description: Invalid request data
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("Missing JSON payload")

        # Validate required fields
        option_id = validate_string(data.get("option_id"), "option_id", required=True)
        canvas_id = validate_integer(data.get("canvas_id"), "canvas_id", required=True)
        request_id = data.get("request_id")

        # Apply the option — user_id from server-side auth, not request body
        result = get_service().apply_option(
            option_id=option_id,
            canvas_id=canvas_id,
            user_id=current_user.id,
            request_id=request_id,
        )

        return jsonify({"success": True, "data": result}), 200

    except Exception as e:
        logger.error(f"Error in apply_option endpoint: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# Recommendations Endpoints
# =============================================================================


@architecture_assistant_bp.route("/recommendations/<int:capability_id>", methods=["GET"])
@login_required
def get_recommendations(capability_id: int):
    """
    Get AI recommendations for a capability.
    ---
    tags:
      - Architecture Assistant
    summary: Get recommendations for capability
    description: |
      Returns AI-generated recommendations for a specific capability including
      coverage improvement suggestions, rationalization opportunities, and
      vendor options.
    parameters:
      - name: capability_id
        in: path
        type: integer
        required: true
        description: ID of the capability
      - name: include_analysis
        in: query
        type: boolean
        default: true
        description: Include detailed analysis with vendor options
    responses:
      200:
        description: Recommendations and analysis
        schema:
          type: object
          properties:
            capability_id:
              type: integer
            capability_name:
              type: string
            context:
              type: object
            recommendations:
              type: array
              items:
                type: object
                properties:
                  priority:
                    type: string
                  category:
                    type: string
                  title:
                    type: string
                  description:
                    type: string
                  action:
                    type: string
            vendor_options:
              type: array
      404:
        description: Capability not found
      500:
        description: Server error
    """
    try:
        include_analysis = request.args.get("include_analysis", "true").lower() == "true"

        service = get_service()
        result = service.get_recommendations(
            capability_id=capability_id, include_analysis=include_analysis
        )

        if "error" in result:
            if "not found" in result["error"].lower():
                return jsonify({"success": False, "error": result["error"]}), 404
            return jsonify({"success": False, "error": result["error"]}), 400

        return jsonify({"success": True, **result}), 200

    except Exception as e:
        logger.error(f"Error in get_recommendations endpoint: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# Gap Analysis Endpoints
# =============================================================================


@architecture_assistant_bp.route("/analyze-gap", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(10, "1h")
def analyze_gap():
    """Accepts single capability_id or capability_id as list for multi-capability analysis"""
    """
    Analyze capability gap and suggest solutions.
    ---
    tags:
      - Architecture Assistant
    summary: Analyze gap and suggest solutions
    description: |
      Performs gap analysis for a capability, calculating current vs target
      coverage and suggesting solutions to close the gap.
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - capability_id
          properties:
            capability_id:
              type: integer
              description: ID of the capability to analyze
            target_coverage:
              type: number
              default: 100
              description: Target coverage percentage
            include_solutions:
              type: boolean
              default: true
              description: Include solution suggestions
    responses:
      200:
        description: Gap analysis results
        schema:
          type: object
          properties:
            gap_analysis:
              type: object
              properties:
                capability_id:
                  type: integer
                capability_name:
                  type: string
                current_coverage:
                  type: number
                target_coverage:
                  type: number
                gap_severity:
                  type: string
                gap_description:
                  type: string
                recommended_solutions:
                  type: array
                estimated_investment:
                  type: number
            coverage_details:
              type: object
      400:
        description: Invalid request
      404:
        description: Capability not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("Request body is required")

        # Accept wizard format: {capabilities: [{id, name, maturity_current, maturity_target}]}
        # OR legacy format: {capability_id: int|[int]}
        wizard_capabilities = data.get("capabilities")
        capability_input = data.get("capability_id")

        capability_ids = []
        maturity_map = {}

        if isinstance(wizard_capabilities, list) and len(wizard_capabilities) > 0:
            # Wizard format — extract IDs and maturity data
            for cap in wizard_capabilities[:50]:
                if not isinstance(cap, dict):
                    continue
                cid = cap.get("id")
                if cid is None:
                    continue
                is_valid, validated_id, error = validate_integer(
                    cid, min_val=1, field_name="capability_id"
                )
                if is_valid:
                    capability_ids.append(validated_id)
                    maturity_map[validated_id] = {
                        "name": str(cap.get("name", ""))[:200],
                        "maturity_current": cap.get("maturity_current", 0),
                        "maturity_target": cap.get("maturity_target", 0),
                    }
        elif isinstance(capability_input, list):
            for cid in capability_input:
                is_valid, validated_id, error = validate_integer(
                    cid, min_val=1, field_name="capability_id"
                )
                if not is_valid:
                    return validation_error_response(error)
                capability_ids.append(validated_id)
        elif capability_input is not None:
            is_valid, validated_id, error = validate_integer(
                capability_input, min_val=1, field_name="capability_id"
            )
            if not is_valid:
                return validation_error_response(error)
            capability_ids = [validated_id]

        if not capability_ids:
            return validation_error_response("At least one capability is required (capabilities array or capability_id)")

        target_coverage = data.get("target_coverage", 100.0)
        include_solutions = data.get("include_solutions", True)

        # Gather RAG context (best-effort — never crashes the endpoint).
        # Bound before the try: retrieval failure must leave these defined.
        _rag_context = ""
        rag_ctx = None
        try:
            from app.services.architecture_rag_service import ArchitectureRAGService
            rag_svc = ArchitectureRAGService()
            rag_ctx = rag_svc.get_context_for_solution(
                business_domain=data.get("business_domain", ""),
                capability_ids=capability_ids,
            )
            _rag_context = rag_svc.format_context(rag_ctx)
        except Exception as e:
            logger.warning("RAG context retrieval failed in analyze-gap: %s", e)

        service = get_service()
        result = service.analyze_gap(
            capability_ids=capability_ids,
            target_coverage=target_coverage,
            include_solutions=include_solutions,
        )
        # Same as design-solution: analyze_gap() is deterministic and cannot take a
        # text blob, but the four queries behind _rag_context have already run, so
        # the result is returned rather than discarded. Purely additive.
        if isinstance(result, dict) and _rag_context:
            result.setdefault("rag_context", _rag_context)

        _log_ai_call(
            action="analyze_gap",
            model_name=result.get("model", "unknown") if isinstance(result, dict) else "unknown",
            input_tokens=result.get("tokens", {}).get("input") if isinstance(result, dict) else None,
            output_tokens=result.get("tokens", {}).get("output") if isinstance(result, dict) else None,
            wizard_step=2,
            error=result.get("error") if isinstance(result, dict) else None,
        )

        if "error" in result:
            if "not found" in result["error"].lower():
                return jsonify({"success": False, "error": result["error"]}), 404
            return jsonify({"success": False, "error": result["error"]}), 400

        # Enrich gaps with wizard maturity data if available
        if maturity_map and "gap_analysis" in result:
            gap_data = result["gap_analysis"]
            if isinstance(gap_data, dict):
                gap_data = [gap_data]
            gaps = []
            for g in gap_data:
                cid = g.get("capability_id")
                if cid in maturity_map:
                    g["maturity_current"] = maturity_map[cid]["maturity_current"]
                    g["maturity_target"] = maturity_map[cid]["maturity_target"]
                    g["capability_name"] = maturity_map[cid]["name"] or g.get("capability_name", "")
                gaps.append(g)
            result["gaps"] = gaps

        return jsonify({"success": True, **result}), 200

    except Exception as e:
        logger.error(f"Error in analyze_gap endpoint: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Architecture assistant service temporarily unavailable. Please try again.", "partial_result": None}), 503


@architecture_assistant_bp.route("/analyze-options", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(10, "1h")
def analyze_options_route():
    """
    Analyze multiple solution options and return enriched ArchiMate artifacts, comparison matrix, and rationale.
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("Request body is required")

        option_ids = data.get("option_ids")
        options_data = data.get("options_data")
        capability_id = data.get("capability_id")
        weights = data.get("weights")

        if not option_ids and not options_data:
            return validation_error_response("Either option_ids or options_data must be provided")

        service = get_service()
        result = service.analyze_options(
            option_ids=option_ids,
            options_data=options_data,
            capability_id=capability_id,
            weights=weights,
        )

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 400

        # Persist analysis for auditability — user_id from server-side auth
        try:
            user_id = current_user.id

            if user_id and capability_id:
                import json as _json
                from datetime import datetime as _dt

                from app.models.vendor_analysis import OptionsAnalysis

                analysis = OptionsAnalysis(
                    name=f"Ad-hoc Options Analysis - {_dt.utcnow().isoformat()}",
                    description=_json.dumps(
                        {
                            "summary": result.get("winner") or {},
                            "decision_rationale": result.get("decision_rationale"),
                            "insights": result.get("insights"),
                        }
                    ),
                    capability_id=int(capability_id),
                    created_by_id=int(user_id),
                    status="completed",
                    total_vendors_analyzed=len(result.get("options", [])),
                    started_at=_dt.utcnow(),
                    completed_at=_dt.utcnow(),
                )
                db.session.add(analysis)
                db.session.commit()
                result["analysis_id"] = analysis.id
        except Exception as _persist_exc:
            logger.warning(f"Failed to persist options analysis: {_persist_exc}")

        return jsonify({"success": True, **result}), 200

    except Exception as e:
        logger.error(f"Error in analyze_options endpoint: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# Status and Health Endpoints
# =============================================================================


@architecture_assistant_bp.route("/status", methods=["GET"])
@login_required
def get_status():
    """
    Get Architecture Assistant service status.
    ---
    tags:
      - Architecture Assistant
    summary: Get service status
    description: Returns the current status and configuration of the Architecture Assistant service
    responses:
      200:
        description: Service status
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [healthy, degraded, unhealthy]
            service:
              type: string
            version:
              type: string
            capabilities:
              type: array
              items:
                type: string
    """
    return (
        jsonify(
            {
                "success": True,
                "status": "healthy",
                "service": "Architecture Assistant",
                "version": "1.0.0",
                "capabilities": [
                    "solution_design",
                    "vendor_options",
                    "option_comparison",
                    "arb_draft_generation",
                    "recommendations",
                    "gap_analysis",
                    "archimate_model_generation",
                ],
            }
        ),
        200,
    )


# =============================================================================
# ArchiMate Model Generation Endpoints
# =============================================================================


@architecture_assistant_bp.route("/generate-archimate-model", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(5, "1h")
def generate_archimate_model():
    """
    Generate complete ArchiMate 3.2 model from capability analysis.
    ---
    tags:
      - Architecture Assistant
    summary: Generate ArchiMate model
    description: |
      Generates a complete ArchiMate 3.2 model across all layers (Business,
      Application, Technology, Motivation, Strategy) based on capability
      analysis results and solution options.
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - capability_id
            - solution_options
            - gap_analysis
          properties:
            capability_id:
              type: integer
              description: ID of the analyzed capability
            solution_options:
              type: array
              items:
                type: object
              description: List of solution options with vendor/product details
            gap_analysis:
              type: object
              description: Gap analysis results from analyze-gap endpoint
            include_viewpoints:
              type: boolean
              default: true
              description: Include generated viewpoints in the model
    responses:
      200:
        description: Complete ArchiMate model
        schema:
          type: object
          properties:
            id:
              type: string
            name:
              type: string
            elements:
              type: array
              items:
                type: object
            relationships:
              type: array
              items:
                type: object
            viewpoints:
              type: array
              items:
                type: object
            metadata:
              type: object
      400:
        description: Invalid request
      404:
        description: Capability not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return validation_error_response("Request body is required")

        # Validate required fields
        capability_id = data.get("capability_id")
        solution_options = data.get("solution_options", [])
        gap_analysis = data.get("gap_analysis", {})
        include_viewpoints = data.get("include_viewpoints", True)

        if not capability_id:
            return validation_error_response("capability_id is required")
        if not isinstance(solution_options, list):
            return validation_error_response("solution_options must be an array")
        if not isinstance(gap_analysis, dict):
            return validation_error_response("gap_analysis must be an object")

        # Generate ArchiMate model
        generator = ArchiMateModelGenerator()
        model = generator.generate_model_from_capability_analysis(
            capability_id=capability_id,
            solution_options=solution_options,
            gap_analysis=gap_analysis,
            include_viewpoints=include_viewpoints,
        )

        if "error" in model:
            if "not found" in model["error"].lower():
                return jsonify({"success": False, "error": model["error"]}), 404
            return jsonify({"success": False, "error": model["error"]}), 400

        _store_generated_model(model)
        return jsonify({"success": True, "model": model}), 200

    except Exception as e:
        logger.error(f"Error in generate_archimate_model endpoint: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route("/export-archimate-model/<model_id>", methods=["GET"])
@login_required
def export_archimate_model(model_id: str):
    """
    Export ArchiMate model to Open Exchange Format XML.
    ---
    tags:
      - Architecture Assistant
    summary: Export ArchiMate model
    description: |
      Exports an ArchiMate model to Open Exchange Format XML for use
      with other ArchiMate tools and repositories.
    parameters:
      - name: model_id
        in: path
        type: string
        required: true
        description: ID of the ArchiMate model to export
    responses:
      200:
        description: ArchiMate XML export
        content:
          application/xml:
            schema:
              type: string
      404:
        description: Model not found
      500:
        description: Server error
    """
    try:
        model = _generated_models_cache.get(model_id)
        if not model:
            return jsonify({"success": False, "error": "Model not found or expired"}), 404

        xml_content = _build_archimate_model_xml(model)
        filename = (model.get("name") or "archimate-model").replace(" ", "_")

        return Response(
            xml_content,
            mimetype="application/xml",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}.xml"',
                "Cache-Control": "no-store",
            },
        )

    except Exception as e:
        logger.error(f"Error in export_archimate_model endpoint: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============ BUSINESS CONTEXT ENDPOINTS ============


@architecture_assistant_bp.route("/business-context", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def create_business_context():
    """
    Create a new business context
    ---
    tags:
      - Business Context
    summary: Create business context
    description: Create a new business context for architecture planning
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - organization
            - industry
          properties:
            name:
              type: string
              description: Business context name
            description:
              type: string
              description: Business context description
            organization:
              type: string
              description: Organization name
            industry:
              type: string
              description: Industry sector
    responses:
      201:
        description: Business context created successfully
      400:
        description: Invalid input data
      500:
        description: Server error
    """
    try:
        from app.services.business_context_service import BusinessContextService

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        # Validate required fields
        required_fields = ["name", "organization", "industry"]
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        service = BusinessContextService()
        context = service.create_business_context(
            name=data["name"],
            description=data.get("description", ""),
            organization=data["organization"],
            industry=data["industry"],
        )

        # Store Phase A extra fields in session so they are not silently dropped
        session[f"phase_a_{context.id}"] = {
            "strategic_objectives": data.get("strategic_objectives", ""),
            "stakeholders": data.get("stakeholders", ""),
            "constraints": data.get("constraints", ""),
        }

        return (
            jsonify(
                {
                    "success": True,
                    "context_id": str(context.id),
                    "data": {
                        "id": context.id,
                        "name": context.name,
                        "description": context.description,
                        "organization": context.organization,
                        "industry": context.industry,
                        "created_at": context.created_at.isoformat(),
                    },
                }
            ),
            201,
        )

    except Exception as e:
        logger.error(f"Error creating business context: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route("/business-context/<context_id>/drivers", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def add_business_driver(context_id):
    """
    Add a business driver to a context
    ---
    tags:
      - Business Context
    summary: Add business driver
    description: Add a business driver to an existing business context
    parameters:
      - in: path
        name: context_id
        required: true
        type: string
        description: Business context ID
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - category
            - impact_level
            - timeframe
          properties:
            name:
              type: string
              description: Driver name
            description:
              type: string
              description: Driver description
            category:
              type: string
              enum: [market, regulatory, technology, operational, strategic]
            impact_level:
              type: string
              enum: [high, medium, low]
            timeframe:
              type: string
              enum: [immediate, short_term, medium_term, long_term]
            stakeholders:
              type: array
              items:
                type: string
    responses:
      201:
        description: Business driver added successfully
      400:
        description: Invalid input data
      404:
        description: Business context not found
      500:
        description: Server error
    """
    try:
        from app.services.business_context_service import BusinessContextService

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        # Validate required fields
        required_fields = ["name", "category", "impact_level", "timeframe"]
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        service = BusinessContextService()
        driver = service.add_business_driver(
            context_id=context_id,
            name=data["name"],
            description=data.get("description", ""),
            category=data["category"],
            impact_level=data["impact_level"],
            timeframe=data["timeframe"],
            stakeholders=data.get("stakeholders", []),
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "id": driver.id,
                        "name": driver.name,
                        "category": driver.category,
                        "impact_level": driver.impact_level,
                        "timeframe": driver.timeframe,
                        "stakeholders": driver.stakeholders,
                    },
                }
            ),
            201,
        )

    except ValueError:
        return jsonify({"success": False, "error": "Resource not found"}), 404
    except Exception as e:
        logger.error(f"Error adding business driver: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route("/business-context/<context_id>/objectives", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def add_strategic_objective(context_id):
    """
    Add a strategic objective to a context
    ---
    tags:
      - Business Context
    summary: Add strategic objective
    description: Add a strategic objective to an existing business context
    parameters:
      - in: path
        name: context_id
        required: true
        type: string
        description: Business context ID
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - priority
            - timeframe
            - owner
          properties:
            name:
              type: string
              description: Objective name
            description:
              type: string
              description: Objective description
            kpis:
              type: array
              items:
                type: object
                properties:
                  metric:
                    type: string
                  target:
                    type: number
                  current:
                    type: number
                  unit:
                    type: string
            priority:
              type: string
              enum: [high, medium, low]
            timeframe:
              type: string
            owner:
              type: string
            dependencies:
              type: array
              items:
                type: string
    responses:
      201:
        description: Strategic objective added successfully
      400:
        description: Invalid input data
      404:
        description: Business context not found
      500:
        description: Server error
    """
    try:
        from app.services.business_context_service import BusinessContextService

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        # Validate required fields
        required_fields = ["name", "priority", "timeframe", "owner"]
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        service = BusinessContextService()
        objective = service.add_strategic_objective(
            context_id=context_id,
            name=data["name"],
            description=data.get("description", ""),
            kpis=data.get("kpis", []),
            priority=data["priority"],
            timeframe=data["timeframe"],
            owner=data["owner"],
            dependencies=data.get("dependencies", []),
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "id": objective.id,
                        "name": objective.name,
                        "priority": objective.priority,
                        "timeframe": objective.timeframe,
                        "owner": objective.owner,
                        "kpis": objective.kpis,
                    },
                }
            ),
            201,
        )

    except ValueError:
        return jsonify({"success": False, "error": "Resource not found"}), 404
    except Exception as e:
        logger.error(f"Error adding strategic objective: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route("/business-context/<context_id>/capabilities", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def add_business_capability(context_id):
    """
    Add a business capability to a context
    ---
    tags:
      - Business Context
    summary: Add business capability
    description: Add a business capability to an existing business context
    parameters:
      - in: path
        name: context_id
        required: true
        type: string
        description: Business context ID
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - domain
            - level
            - maturity
            - strategic_importance
          properties:
            name:
              type: string
              description: Capability name
            description:
              type: string
              description: Capability description
            domain:
              type: string
              description: Business domain
            level:
              type: integer
              minimum: 1
              maximum: 3
              description: Capability hierarchy level
            parent_capability:
              type: string
              description: Parent capability ID
            maturity:
              type: string
              enum: [none, initial, repeatable, defined, managed, optimizing]
            strategic_importance:
              type: string
              enum: [low, medium, high, critical]
            business_value:
              type: string
              description: Business value description
            current_state:
              type: string
              description: Current capability state
            target_state:
              type: string
              description: Target capability state
            gaps:
              type: array
              items:
                type: string
            dependencies:
              type: array
              items:
                type: string
    responses:
      201:
        description: Business capability added successfully
      400:
        description: Invalid input data
      404:
        description: Business context not found
      500:
        description: Server error
    """
    try:
        from app.services.business_context_service import BusinessContextService

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        # Validate required fields
        required_fields = ["name", "domain", "level", "maturity", "strategic_importance"]
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        service = BusinessContextService()
        capability = service.add_business_capability(
            context_id=context_id,
            name=data["name"],
            description=data.get("description", ""),
            domain=data["domain"],
            level=data["level"],
            parent_capability=data.get("parent_capability"),
            maturity=data["maturity"],
            strategic_importance=data["strategic_importance"],
            business_value=data.get("business_value", ""),
            current_state=data.get("current_state", ""),
            target_state=data.get("target_state", ""),
            gaps=data.get("gaps", []),
            dependencies=data.get("dependencies", []),
        )

        # ENH-010: Invalidate capability cache after new capability created
        from app.services.architecture_assistant_service import _invalidate_capability_cache
        _invalidate_capability_cache()

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "id": capability.id,
                        "name": capability.name,
                        "domain": capability.domain,
                        "level": capability.level,
                        "maturity": capability.maturity.value,
                        "strategic_importance": capability.strategic_importance.value,
                        "gaps": capability.gaps,
                    },
                }
            ),
            201,
        )

    except ValueError:
        return jsonify({"success": False, "error": "Resource not found"}), 404
    except Exception as e:
        logger.error(f"Error adding business capability: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route("/business-context/<context_id>/heatmap", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(10, "1h")
def generate_capability_heatmap(context_id):
    """
    Generate capability heatmap for a business context
    ---
    tags:
      - Business Context
    summary: Generate capability heatmap
    description: Generate a capability heatmap visualization from business context data
    parameters:
      - in: path
        name: context_id
        required: true
        type: string
        description: Business context ID
    responses:
      200:
        description: Capability heatmap generated successfully
      404:
        description: Business context not found
      500:
        description: Server error
    """
    try:
        from app.services.business_context_service import BusinessContextService

        service = BusinessContextService()
        heatmap = service.generate_capability_heatmap(context_id)

        return jsonify({"success": True, "data": heatmap}), 200

    except ValueError:
        return jsonify({"success": False, "error": "Resource not found"}), 404
    except Exception as e:
        logger.error(f"Error generating capability heatmap: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route(
    "/business-context/<context_id>/problem-statement", methods=["POST"]
)
@login_required
@require_roles("admin", "architect")
def generate_problem_statement(context_id):
    """
    Generate problem statement for a business context
    ---
    tags:
      - Business Context
    summary: Generate problem statement
    description: Generate a comprehensive problem statement from business context data
    parameters:
      - in: path
        name: context_id
        required: true
        type: string
        description: Business context ID
    responses:
      200:
        description: Problem statement generated successfully
      404:
        description: Business context not found
      500:
        description: Server error
    """
    try:
        from app.services.business_context_service import BusinessContextService

        service = BusinessContextService()
        problem_statement = service.generate_problem_statement(context_id)

        return jsonify({"success": True, "data": {"problem_statement": problem_statement}}), 200

    except ValueError:
        return jsonify({"success": False, "error": "Resource not found"}), 404
    except Exception as e:
        logger.error(f"Error generating problem statement: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_bp.route("/business-context/<context_id>/scope", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(10, "1h")
def generate_scope_definition(context_id):
    """
    Generate scope definition for a business context
    ---
    tags:
      - Business Context
    summary: Generate scope definition
    description: Generate a comprehensive scope definition from business context data
    parameters:
      - in: path
        name: context_id
        required: true
        type: string
        description: Business context ID
    responses:
      200:
        description: Scope definition generated successfully
      404:
        description: Business context not found
      500:
        description: Server error
    """
    try:
        from app.services.business_context_service import BusinessContextService

        service = BusinessContextService()
        scope_definition = service.generate_scope_definition(context_id)

        return jsonify({"success": True, "data": {"scope_definition": scope_definition}}), 200

    except ValueError:
        return jsonify({"success": False, "error": "Resource not found"}), 404
    except Exception as e:
        logger.error(f"Error generating scope definition: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_assistant_ui_bp.route("/business-context", methods=["GET"])
@login_required
def business_context_ui():
    """
    Business Context UI page
    ---
    tags:
      - Business Context UI
    summary: Business Context main page
    description: Main UI page for business context capture and analysis
    responses:
      200:
        description: Business Context UI page
    """
    return render_template("architecture_assistant/business_context.html")


@architecture_assistant_bp.route("/from-vision", methods=["POST"])
@login_required
def from_vision():
    """Extract scope from a vision statement and initiate orchestration.

    Accepts a plain-text vision/problem statement and optional capability_ids.
    Delegates to the same orchestration pipeline as /orchestrate but pre-fills
    the scope from the vision text, so callers don't need to structure the data.

    Request JSON:
        vision (str): Problem/vision statement (required)
        capability_ids (list[int]): Optional capability IDs to scope the analysis
        application_ids (list[int]): Optional application IDs in scope

    Returns JSON with solution_id and orchestration results.
    """
    data = request.get_json(silent=True) or {}
    vision = (data.get("vision") or "").strip()
    if not vision:
        return jsonify({"success": False, "error": "vision is required"}), 400

    # Inject vision as scope so orchestrate_wizard can proceed
    data["scope"] = {
        "definition": vision,
        "problem": vision[:500],
    }
    # Ensure capability_ids key exists (orchestrate uses it)
    if "capability_ids" not in data:
        data["capability_ids"] = []

    # Delegate to orchestrate_wizard with the enriched payload
    request._cached_json = (data, data)  # (force=False, force=True) tuple Flask uses

    return orchestrate_wizard()


@architecture_assistant_bp.route("/orchestrate", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(5, "1h")
def orchestrate_wizard():
    """
    Orchestrate the full Architecture Assistant wizard pipeline.
    ---
    tags:
      - Architecture Assistant
    summary: Run full wizard pipeline (scope → gap → options → roadmap → ARB)
    description: |
      Chains all wizard steps in a single server-side call. Accepts the
      accumulated wizard state and returns results for each phase.
      Steps that lack required input are skipped gracefully.
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - capability_ids
          properties:
            capability_ids:
              type: array
              items:
                type: integer
              description: Selected capability IDs (from Step 2)
            scope:
              type: object
              description: Business context from Step 1
              properties:
                problem:
                  type: string
                definition:
                  type: string
                stakeholders:
                  type: string
                constraints:
                  type: string
                principles:
                  type: array
                  items:
                    type: string
            constraints:
              type: object
              description: Solution constraints (budget, timeline)
            target_coverage:
              type: number
              default: 100
            include_roadmap:
              type: boolean
              default: true
    responses:
      200:
        description: Orchestrated wizard results
      400:
        description: Missing required fields
    """
    data = request.get_json(silent=True) or {}

    # Validate required input
    capability_ids = data.get("capability_ids", [])
    if not capability_ids:
        return jsonify({"success": False, "error": "capability_ids is required"}), 400

    # Normalize to list of ints
    if isinstance(capability_ids, (int, str)):
        capability_ids = [int(capability_ids)]
    else:
        capability_ids = [int(c) for c in capability_ids]

    scope = data.get("scope", {})
    constraints = data.get("constraints", {})
    target_coverage = data.get("target_coverage", 100.0)
    include_roadmap = data.get("include_roadmap", True)

    service = get_service()
    results = {
        "scope": None,
        "gap_analysis": None,
        "solution_options": None,
        "roadmap": None,
        "arb_draft": None,
        "errors": [],
    }

    # --- Phase A: Capture scope context ---
    if scope:
        results["scope"] = {
            "problem": scope.get("problem", ""),
            "definition": scope.get("definition", ""),
            "stakeholders": scope.get("stakeholders", ""),
            "constraints": scope.get("constraints", ""),
            "principles": scope.get("principles", []),
            "captured": True,
        }

    # --- Phase B-D: Gap Analysis ---
    try:
        gap_result = service.analyze_gap(
            capability_ids=capability_ids,
            target_coverage=target_coverage,
            include_solutions=True,
        )
        results["gap_analysis"] = gap_result
    except Exception as e:
        logger.error(f"Orchestrator — gap analysis failed: {e}")
        results["errors"].append({"phase": "gap_analysis", "error": str(e)})

    # --- Phase E: Solution Options ---
    primary_cap_id = capability_ids[0]
    try:
        options_result = service.analyze_options(
            options_data=None,
            option_ids=None,
            capability_id=primary_cap_id,
            weights=None,
        )
        # analyze_options returns enriched options with ArchiMate suggestions
        if options_result and options_result.get("options"):
            results["solution_options"] = {
                "options": options_result["options"],
                "decision_rationale": options_result.get("decision_rationale"),
                "analysis_id": options_result.get("analysis_id"),
            }
    except Exception as e:
        logger.error(f"Orchestrator — solution options failed: {e}")
        results["errors"].append({"phase": "solution_options", "error": str(e)})

        # Fallback: try generating vendor options directly
        try:
            fallback = service.generate_vendor_options(
                capability_id=primary_cap_id,
                constraints=constraints,
                max_options=5,
            )
            if fallback and fallback.get("options"):
                results["solution_options"] = {
                    "options": [
                        o if isinstance(o, dict) else (o.__dict__ if hasattr(o, '__dict__') else {})
                        for o in fallback["options"]
                    ],
                    "decision_rationale": None,
                    "analysis_id": None,
                    "source": "fallback_generation",
                }
        except Exception as e2:
            logger.warning(f"Orchestrator — fallback options also failed: {e2}")

    # --- Phase F: Roadmap (gap-to-roadmap conversion) ---
    if include_roadmap and results["gap_analysis"]:
        try:
            from app.services.gap_archimate_service import gap_archimate_service

            gap_severity = results["gap_analysis"].get("gap_severity", "medium")
            gap_payload = [
                {
                    "capability_id": cid,
                    "capability_type": "business",
                    "name": f"Capability {cid}",
                    "gap_types": ["coverage" if gap_severity in ("high", "critical") else "quality"],
                    "priority": gap_severity,
                    "level": 2,
                }
                for cid in capability_ids
            ]
            convert_result = gap_archimate_service.bulk_convert_gaps(gap_payload, commit=False)

            # Create work packages for new gaps
            if convert_result.get("created", 0) > 0:
                for gap_data in gap_payload:
                    gap = gap_archimate_service.find_existing_gap(
                        gap_data.get("capability_type"), gap_data.get("capability_id")
                    )
                    if gap and not gap.work_packages:
                        gap_archimate_service.create_standard_work_breakdown(gap, template="auto")

            db.session.commit()
            results["roadmap"] = {
                "gaps_converted": convert_result.get("created", 0),
                "gaps_updated": convert_result.get("updated", 0),
            }
        except Exception as e:
            logger.warning(f"Orchestrator — roadmap conversion failed: {e}")
            results["errors"].append({"phase": "roadmap", "error": str(e)})

    # --- Phase G: ARB Draft ---
    recommended_option = None
    alternative_options = []
    if results["solution_options"] and results["solution_options"].get("options"):
        opts = results["solution_options"]["options"]
        # First option is the recommended one (highest ranked)
        recommended_option = opts[0] if opts else None
        alternative_options = opts[1:4] if len(opts) > 1 else []

    if recommended_option:
        try:
            additional_context = {}
            if results["solution_options"].get("decision_rationale"):
                additional_context["decision_rationale"] = results["solution_options"]["decision_rationale"]
            if scope:
                additional_context["scope"] = scope

            arb_result = service.generate_arb_draft(
                capability_id=primary_cap_id,
                recommended_option=recommended_option,
                alternative_options=alternative_options,
                additional_context=additional_context,
            )
            results["arb_draft"] = arb_result
        except Exception as e:
            logger.error(f"Orchestrator — ARB draft failed: {e}")
            results["errors"].append({"phase": "arb_draft", "error": str(e)})

    # Summary of what completed
    completed_phases = [
        phase for phase in ["scope", "gap_analysis", "solution_options", "roadmap", "arb_draft"]
        if results.get(phase) is not None
    ]

    # ENH-017: Persist the orchestration output as a Solution record
    solution_id = data.get("solution_id")
    try:
        from app.models.solution_models import Solution, SolutionCapabilityMapping

        if solution_id:
            solution = Solution.query.get(solution_id)
            if solution and solution.created_by_id != current_user.id:
                solution = None  # ownership mismatch — do not update
        else:
            solution = None

        if not solution:
            # Create new solution from orchestration results
            solution = Solution(created_by_id=current_user.id)
            db.session.add(solution)

        # Populate from scope
        if scope:
            solution.name = scope.get("definition", "") or scope.get("problem", "Orchestrated Solution")[:200]
            solution.description = scope.get("problem", "")
        if not solution.name:
            solution.name = f"Orchestrated Solution — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
        solution.governance_status = solution.governance_status or "draft"
        solution.current_step = len(completed_phases)
        db.session.flush()

        # Link capability mappings
        for cid in capability_ids:
            existing = SolutionCapabilityMapping.query.filter_by(
                solution_id=solution.id, capability_id=cid
            ).first()
            if not existing:
                db.session.add(SolutionCapabilityMapping(
                    solution_id=solution.id,
                    capability_id=cid,
                    created_by_id=current_user.id,
                ))

        # ENH-018: Populate application and vendor product junction tables from options
        _populate_junction_tables_from_options(solution, results.get("solution_options"))

        db.session.commit()
        solution_id = solution.id
        logger.info("ENH-017: Orchestration persisted to solution_id=%s", solution_id)
    except Exception as e:
        db.session.rollback()
        logger.warning("ENH-017: Could not persist orchestration to Solution: %s", e)
        results["errors"].append({"phase": "persistence", "error": str(e)})

    # ENH-019: Build reasoning trail for explainability
    reasoning_trail = []
    for phase_name in completed_phases:
        phase_data = results.get(phase_name)
        reasoning_entry = {"phase": phase_name}
        if isinstance(phase_data, dict):
            reasoning_entry["summary"] = phase_data.get("decision_rationale") or phase_data.get("summary") or f"{phase_name} completed"
            if phase_data.get("options"):
                reasoning_entry["option_count"] = len(phase_data["options"])
        reasoning_trail.append(reasoning_entry)

    # Log the full orchestration with reasoning
    orchestration_reasoning = "; ".join(
        f"{r['phase']}: {r.get('summary', 'completed')}" for r in reasoning_trail
    )
    _log_ai_call(
        action="orchestrate_wizard",
        model_name="pipeline",
        solution_id=solution_id,
        prompt_summary=f"Orchestrate wizard for {len(capability_ids)} capabilities",
        reasoning=orchestration_reasoning,
        content_type="orchestration",
    )

    return jsonify({
        "success": True,
        "completed_phases": completed_phases,
        "total_phases": 5,
        "results": results,
        "solution_id": solution_id,
        "reasoning_trail": reasoning_trail,
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


# ---------------------------------------------------------------------------
# ENH-017: Create Solution from Design Output
# ENH-018: populate_solution_junctions service method
# ---------------------------------------------------------------------------


def populate_solution_junctions(solution_id: int, design_output: dict) -> dict:
    """ENH-018: Populate junction tables from any design path (from-vision, chat, orchestrate).

    Accepts the canonical design_output dict that all design paths produce:
        {
            "capabilities": [{"capability_id": int, ...}],
            "options": [...],
            "application_ids": [int],
            "vendor_product_ids": [int],
        }

    Returns {"capability_mappings": int, "applications": int, "vendor_products": int}
    """
    from app.models.solution_models import Solution, solution_applications, solution_vendor_products
    from app.models.solution_architect_models import SolutionCapabilityMapping

    solution = db.session.get(Solution, solution_id)
    if not solution:
        return {"error": "Solution not found"}

    counts = {"capability_mappings": 0, "applications": 0, "vendor_products": 0}

    # Capability mappings
    cap_ids = []
    for cap in (design_output.get("capabilities") or []):
        if isinstance(cap, dict) and cap.get("capability_id"):
            cap_ids.append(int(cap["capability_id"]))
        elif isinstance(cap, int):
            cap_ids.append(cap)
    for cap_id in cap_ids:
        existing = SolutionCapabilityMapping.query.filter_by(
            solution_id=solution_id, capability_id=cap_id
        ).first()
        if not existing:
            try:
                db.session.add(SolutionCapabilityMapping(
                    solution_id=solution_id,
                    capability_id=cap_id,
                    created_by_id=getattr(
                        current_user, "id", None
                    ) if current_user and current_user.is_authenticated else None,
                ))
                counts["capability_mappings"] += 1
            except Exception as exc:
                logger.warning("ENH-010: Could not map capability %s to solution: %s", cap_id, exc)

    # Application junctions
    app_ids = list(design_output.get("application_ids") or [])
    for opt in (design_output.get("options") or []):
        if isinstance(opt, dict):
            app_ids.extend(opt.get("application_ids") or [])
            if opt.get("application_id"):
                app_ids.append(opt["application_id"])
    for app_id in app_ids:
        try:
            app_id_int = int(app_id)
            existing = db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                solution_applications.select().where(
                    (solution_applications.c.solution_id == solution_id) &
                    (solution_applications.c.application_component_id == app_id_int)
                )
            ).first()
            if not existing:
                db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                    solution_applications.insert().values(
                        solution_id=solution_id,
                        application_component_id=app_id_int,
                        role="supporting",
                    )
                )
                counts["applications"] += 1
        except (ValueError, TypeError, Exception):
            continue

    # Vendor product junctions
    vp_ids = list(design_output.get("vendor_product_ids") or [])
    for opt in (design_output.get("options") or []):
        if isinstance(opt, dict):
            vp_ids.extend(opt.get("vendor_product_ids") or [])
            if opt.get("vendor_product_id"):
                vp_ids.append(opt["vendor_product_id"])
    for vp_id in vp_ids:
        try:
            vp_id_int = int(vp_id)
            existing = db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                solution_vendor_products.select().where(
                    (solution_vendor_products.c.solution_id == solution_id) &
                    (solution_vendor_products.c.vendor_product_id == vp_id_int)
                )
            ).first()
            if not existing:
                db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                    solution_vendor_products.insert().values(
                        solution_id=solution_id,
                        vendor_product_id=vp_id_int,
                        implementation_type="licensed",
                    )
                )
                counts["vendor_products"] += 1
        except (ValueError, TypeError, Exception):
            continue

    db.session.commit()
    logger.info("ENH-018: Junction tables populated for solution_id=%s: %s", solution_id, counts)
    return counts


@architecture_assistant_bp.route("/create-solution", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def create_solution_from_design():
    """ENH-017: Persist a Solution record from orchestrate/from-vision design output.

    Request JSON:
        name (str): Solution name (required)
        description (str): Solution description
        capabilities (list): [{capability_id, ...}]
        options (list): Orchestration options
        requirements (list): Requirements from design
        application_ids (list[int]): Application component IDs to link
        vendor_product_ids (list[int]): Vendor product IDs to link
        business_domain (str): Business domain
        solution_type (str): Solution type

    Returns:
        {success: true, solution_id: int, junction_counts: {...}}
    """
    from app.models.solution_models import Solution

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400

    try:
        solution = Solution(
            name=name[:255],
            description=data.get("description", ""),
            business_domain=data.get("business_domain"),
            solution_type=data.get("solution_type"),
            governance_status="draft",
            created_by_id=current_user.id,
            arb_snapshot=data.get("options"),
        )
        db.session.add(solution)
        db.session.flush()  # get solution.id

        # ENH-018: Populate junction tables
        design_output = {
            "capabilities": data.get("capabilities") or [],
            "options": data.get("options") or [],
            "application_ids": data.get("application_ids") or [],
            "vendor_product_ids": data.get("vendor_product_ids") or [],
        }
        junction_counts = populate_solution_junctions(solution.id, design_output)

        db.session.commit()
        logger.info("ENH-017: Solution created from design: id=%s, name=%s", solution.id, solution.name)

        return jsonify({
            "success": True,
            "solution_id": solution.id,
            "solution_name": solution.name,
            "junction_counts": junction_counts,
        }), 201

    except Exception as exc:
        db.session.rollback()
        logger.error("ENH-017: Error creating solution from design: %s", exc)
        return jsonify({"success": False, "error": "Failed to create solution"}), 500


# ---------------------------------------------------------------------------
# AWIZ-008: ArchiMate Motivation Layer typeahead and upsert
# ---------------------------------------------------------------------------


@architecture_assistant_bp.route("/motivation-elements", methods=["GET"])
@login_required
def motivation_elements_typeahead():
    """Return ArchiMate elements matching type and query string.

    Searches the canonical archimate_elements table (not legacy tables).
    Supports all ArchiMate 3.2 element types across all layers.
    """
    from app.models.archimate_core import ArchiMateElement

    type_ = request.args.get("type", "").strip()
    layer = request.args.get("layer", "").strip()
    q = request.args.get("q", "").strip()

    query = ArchiMateElement.query
    if q:
        query = query.filter(ArchiMateElement.name.ilike(f"%{q}%"))
    if type_:
        query = query.filter(ArchiMateElement.type == type_)
    if layer:
        query = query.filter(db.func.lower(ArchiMateElement.layer) == layer.lower())

    # ENH-011: Optional page/per_page for pagination; default still limited to 15
    page = safe_int_arg('page', None, minimum=1)
    per_page = min(safe_int_arg('per_page', 15, minimum=1, maximum=500), 100)

    # ENH-010: Cache typeahead results by query params for 5 minutes
    cache_key = f"motivation_typeahead:{q}:{type_}:{layer}:{page}:{per_page}"

    def _run_query():
        q_sorted = query.order_by(ArchiMateElement.name)
        if page is not None:
            total = q_sorted.count()
            rows = q_sorted.offset((page - 1) * per_page).limit(per_page).all()
            items = [{"id": r.id, "name": r.name, "type": r.type, "layer": r.layer} for r in rows]
            return {"elements": items, "results": items, "total": total, "page": page, "per_page": per_page}
        rows = q_sorted.limit(per_page).all()
        items = [{"id": r.id, "name": r.name, "type": r.type, "layer": r.layer} for r in rows]
        return {"elements": items, "results": items}

    results = _get_cached(cache_key, _run_query, ttl=300)
    return jsonify(results)


@architecture_assistant_bp.route("/archimate-element-types", methods=["GET"])
@login_required
def archimate_element_types():
    """Return all ArchiMate 3.2 element types grouped by layer for the create picker.

    ENH-010: Cached for 10 minutes (static reference data).
    """
    def _build_types():
        return {
            "Strategy": ["Resource", "Capability", "CourseOfAction", "ValueStream"],
            "Business": [
                "BusinessActor", "BusinessRole", "BusinessCollaboration", "BusinessInterface",
                "BusinessProcess", "BusinessFunction", "BusinessInteraction", "BusinessEvent",
                "BusinessService", "BusinessObject", "Contract", "Representation", "Product",
            ],
            "Application": [
                "ApplicationComponent", "ApplicationCollaboration", "ApplicationInterface",
                "ApplicationFunction", "ApplicationProcess", "ApplicationInteraction",
                "ApplicationEvent", "ApplicationService", "DataObject",
            ],
        "Technology": [
            "Node", "Device", "SystemSoftware", "TechnologyCollaboration",
            "TechnologyInterface", "Path", "CommunicationNetwork",
            "TechnologyFunction", "TechnologyProcess", "TechnologyInteraction",
            "TechnologyEvent", "TechnologyService", "Artifact",
        ],
        "Motivation": [
            "Stakeholder", "Driver", "Assessment", "Goal", "Outcome",
            "Principle", "Requirement", "Constraint", "Meaning", "Value",
        ],
            "Implementation_and_Migration": [
                "WorkPackage", "Deliverable", "Plateau", "Gap", "ImplementationEvent",
            ],
        }

    types_by_layer = _get_cached("archimate_element_types", _build_types, ttl=600)
    return jsonify({"types_by_layer": types_by_layer})


@architecture_assistant_bp.route("/create-element", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def create_archimate_element():
    """Create a new ArchiMate element and return it for the wizard picker."""
    from app.models.archimate_core import ArchiMateElement

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    ae_type = (data.get("type") or "").strip()
    layer = (data.get("layer") or "").strip()

    if not name:
        return jsonify({"error": "Element name is required"}), 400
    if not ae_type:
        return jsonify({"error": "Element type is required"}), 400
    if not layer:
        return jsonify({"error": "Layer is required"}), 400

    ae = ArchiMateElement.query.filter_by(name=name, type=ae_type, layer=layer).first()
    status = "existing"
    if not ae:
        ae = ArchiMateElement(name=name, type=ae_type, layer=layer)
        db.session.add(ae)
        db.session.commit()
        status = "created"

    return jsonify({
        "element": {"id": ae.id, "name": ae.name, "type": ae.type, "layer": ae.layer},
        "status": status,
    })


@architecture_assistant_bp.route("/scope-archimate", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def scope_archimate_upsert():
    """Upsert Motivation layer records from Step 1 scope inputs."""
    data = request.get_json() or {}
    problem_statement = (data.get("problem_statement") or "").strip()
    stakeholders_raw = (data.get("stakeholders") or "").strip()
    constraints_raw = (data.get("constraints") or "").strip()

    assessment = None
    stakeholder_records = []
    constraint_records = []

    try:
        if problem_statement:
            assessment = MotivationAssessment.query.filter_by(
                name=problem_statement[:80]
            ).first()
            if assessment is None:
                assessment = MotivationAssessment(
                    name=problem_statement[:80], description=problem_statement
                )
                db.session.add(assessment)
            db.session.flush()

        for entry in [s.strip() for s in stakeholders_raw.split(",") if s.strip()]:
            s = MotivationStakeholder.query.filter_by(name=entry).first()
            if s is None:
                s = MotivationStakeholder(name=entry)
                db.session.add(s)
            db.session.flush()
            stakeholder_records.append(s)

        for line in [c.strip() for c in constraints_raw.splitlines() if c.strip()]:
            c = MotivationConstraint.query.filter_by(name=line).first()
            if c is None:
                c = MotivationConstraint(name=line)
                db.session.add(c)
            db.session.flush()
            constraint_records.append(c)

        db.session.commit()
        return jsonify({
            "success": True,
            "archimate_ids": {
                "assessment": assessment.id if assessment else None,
                "stakeholders": [s.id for s in stakeholder_records],
                "constraints": [c.id for c in constraint_records],
            },
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"scope_archimate_upsert error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# AWIZ-009: ArchiMate viewpoints catalog
# ---------------------------------------------------------------------------

@architecture_assistant_bp.route("/viewpoints", methods=["GET"])
@login_required
def get_archimate_viewpoints():
    """Return all standard ArchiMate 3.2 viewpoints."""
    svc = ArchiMateViewpointService()
    viewpoints = svc.get_viewpoints()
    return jsonify({"viewpoints": list(viewpoints.values())})


# ---------------------------------------------------------------------------
# AWIZ-011: ArchiMate 5-layer model for a capability
# ---------------------------------------------------------------------------

@architecture_assistant_bp.route("/archimate-model", methods=["GET"])
@login_required
def get_archimate_model():
    """Return a full ArchiMate model for a capability, grouped by layer."""
    capability_id = request.args.get("capability_id", type=int)
    if not capability_id:
        return jsonify({"error": "capability_id is required"}), 400
    try:
        generator = ArchitectureModuleModelGenerator()
        model = generator.generate_model_from_capability_analysis(
            capability_id=capability_id, solution_options=[], gap_analysis={}
        )
        return jsonify(model)
    except Exception as e:
        logger.exception(f"get_archimate_model error for cap {capability_id}: {e}")
        return jsonify({"error": "Could not build the ArchiMate model"}), 500


# ---------------------------------------------------------------------------
# AWIZ-012: Persist ArchiMate roadmap elements (WorkPackage + Deliverable + Plateau)
# ---------------------------------------------------------------------------

@architecture_assistant_bp.route("/roadmap-elements", methods=["POST"])
@login_required
def create_roadmap_elements():
    """Create persisted WorkPackage/Deliverable ArchiMate elements from gap + option data."""
    data = request.get_json() or {}
    gap_elements = data.get("gap_elements") or []
    solution_option = data.get("solution_option") or {}
    try:
        wks = max(6, int(solution_option.get("implementation_weeks") or 24))
    except (ValueError, TypeError):
        return jsonify({"error": "solution_option.implementation_weeks must be a number"}), 400

    phases = [
        ("Discovery & Design", 1, 4, "Requirements, architecture blueprint, stakeholder alignment"),
        ("Build & Integrate", 5, wks - 2, "Implement, test, data migration, integration validation"),
        ("Go-Live", wks - 1, wks, "Deploy, hypercare, stabilisation, lessons learned"),
    ]

    try:
        result = []
        for i, (name, sw, ew, desc) in enumerate(phases):
            ew = max(sw, ew)
            wp = WorkPackage(
                name=name,
                description=desc,
                sequence_order=i + 1,
                summary=f"Weeks {sw}–{ew}",
            )
            db.session.add(wp)
            sync_archimate_element(wp)
            db.session.flush()

            if i == 2 and gap_elements:
                target_plateau_id = gap_elements[0].get("to_plateau_id")
                if target_plateau_id:
                    plateau = Plateau.query.get(target_plateau_id)
                    if plateau:
                        wp.plateaus.append(plateau)

            deliverable = Deliverable(name=f"{name} Deliverable", work_package_id=wp.id)
            db.session.add(deliverable)
            db.session.flush()

            result.append({
                "work_package_id": wp.id,
                "deliverable_id": deliverable.id,
                "name": wp.name,
                "start_week": sw,
                "end_week": ew,
            })

        db.session.commit()
        return jsonify({"work_packages": result})
    except Exception as e:
        db.session.rollback()
        logger.error(f"create_roadmap_elements error: {e}")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# AWIZ-013: Export ArchiMate model as OEF XML
# ---------------------------------------------------------------------------

@architecture_assistant_bp.route("/export-arb/<int:solution_id>", methods=["GET"])
@login_required
@require_roles("admin", "architect")
def export_arb_document(solution_id):
    """Export ARB submission as a printable HTML document."""
    from app.models.solution_models import Solution
    from app.models.solution_element import SolutionElement

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"error": "Solution not found"}), 404
    if solution.created_by_id != current_user.id:
        return jsonify({"error": "You do not own this solution"}), 403

    snapshot = solution.arb_snapshot or {}
    arb_draft = snapshot.get("arb_draft") or {}
    options = snapshot.get("options") or []
    selected = snapshot.get("selected_option_id")
    roadmap = snapshot.get("roadmap") or {}

    # Gather linked elements
    elements = []
    try:
        ses = SolutionElement.query.filter_by(solution_id=solution_id).all()
        for se in ses:
            ae = se.archimate_element
            if ae:
                elements.append({"name": ae.name, "type": ae.type, "layer": ae.layer})
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        logger.exception("Failed to database query")
        pass

    selected_option = None
    for opt in options:
        if str(opt.get("id")) == str(selected) or opt.get("name") == selected:
            selected_option = opt
            break

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ARB Submission — {escape(solution.name)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; line-height: 1.6; }}
  h1 {{ border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
  h2 {{ color: #2563eb; margin-top: 32px; }}
  .meta {{ color: #6b7280; font-size: 14px; margin-bottom: 24px; }}
  .section {{ margin-bottom: 24px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin: 2px; background: #f3f4f6; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  th, td {{ text-align: left; padding: 8px 12px; border: 1px solid #e5e7eb; }}
  th {{ background: #f9fafb; font-weight: 600; }}
  @media print {{ body {{ margin: 20px; }} }}
</style>
</head>
<body>
<h1>Architecture Review Board Submission</h1>
<div class="meta">
  <strong>Solution:</strong> {escape(solution.name)}<br>
  <strong>Domain:</strong> {escape(solution.business_domain or 'N/A')}<br>
  <strong>Status:</strong> {escape(solution.governance_status or 'draft')}<br>
  <strong>Generated:</strong> {__import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
</div>
"""
    # arb_draft is LLM-generated (AI-drafted ARB submission) and elements/
    # roadmap entries carry architect-authored freeform text -- escape() every
    # one of them before they reach this HTML response (same class of bug as
    # the 10 Sep 2026 digest-email incident, here reaching a browser directly
    # via Response(html, mimetype="text/html")).
    if arb_draft.get("business_justification"):
        html += f'<div class="section"><h2>Business Justification</h2><p>{escape(str(arb_draft["business_justification"]))}</p></div>\n'
    if arb_draft.get("technical_assessment"):
        html += f'<div class="section"><h2>Technical Assessment</h2><p>{escape(str(arb_draft["technical_assessment"]))}</p></div>\n'
    if arb_draft.get("risk_analysis"):
        html += f'<div class="section"><h2>Risk Analysis</h2><p>{escape(str(arb_draft["risk_analysis"]))}</p></div>\n'
    if arb_draft.get("implementation_approach"):
        html += f'<div class="section"><h2>Implementation Approach</h2><p>{escape(str(arb_draft["implementation_approach"]))}</p></div>\n'
    if arb_draft.get("cost_summary"):
        html += f'<div class="section"><h2>Cost Summary</h2><p>{escape(str(arb_draft["cost_summary"]))}</p></div>\n'

    if selected_option:
        html += f'<div class="section"><h2>Selected Option</h2><p><strong>{escape(str(selected_option.get("name", "N/A")))}</strong>: {escape(str(selected_option.get("description", "")))}</p></div>\n'

    if elements:
        html += '<div class="section"><h2>ArchiMate Elements</h2><table><tr><th>Name</th><th>Type</th><th>Layer</th></tr>\n'
        for el in elements:
            html += f'<tr><td>{escape(str(el["name"]))}</td><td>{escape(str(el["type"]))}</td><td>{escape(str(el["layer"]))}</td></tr>\n'
        html += '</table></div>\n'

    if roadmap.get("plateaus"):
        html += '<div class="section"><h2>Implementation Roadmap</h2><table><tr><th>Plateau</th><th>Duration</th></tr>\n'
        for p in roadmap["plateaus"]:
            html += f'<tr><td>{escape(str(p.get("name", "")))}</td><td>{escape(str(p.get("duration", "")))}</td></tr>\n'
        html += '</table></div>\n'

    html += '</body></html>'
    return Response(html, mimetype="text/html")


@architecture_assistant_bp.route("/auto-sequence", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def auto_sequence_work_packages():
    """Order work packages by plateau sequence then name."""
    data = request.get_json(silent=True) or {}
    work_packages = data.get("work_packages") or []
    plateaus = data.get("plateaus") or []

    if not work_packages:
        return jsonify({"work_packages": []})

    # Build plateau order map
    plateau_order = {}
    for i, p in enumerate(plateaus):
        name = p.get("name") or p.get("id") or str(i)
        plateau_order[name] = i

    def sort_key(wp):
        plateau_name = wp.get("plateau") or ""
        order = plateau_order.get(plateau_name, 999)
        return (order, (wp.get("name") or "").lower())

    sorted_wps = sorted(work_packages, key=sort_key)
    return jsonify({"work_packages": sorted_wps})


@architecture_assistant_bp.route("/export-archimate-oef", methods=["POST"])
@login_required
def export_archimate_oef():
    """Export a generated ArchiMate model as ArchiMate Exchange Format (OEF) XML."""
    data = request.get_json() or {}
    model = data.get("model") or {}
    try:
        generator = ArchitectureModuleModelGenerator()
        xml_content = generator.export_to_archimate_exchange(model)
        return Response(
            xml_content,
            mimetype="application/xml",
            headers={"Content-Disposition": "attachment; filename=archimate_model.xml"},
        )
    except Exception as e:
        logger.error(f"export_archimate_oef error: {e}")
        return jsonify({"error": str(e)}), 500


@architecture_assistant_bp.route("/wizard/validate-quality-baseline", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def wizard_validate_quality_baseline():
    """Return contradiction warnings for a quality_baseline payload."""
    data = request.get_json(silent=True) or {}
    qb = data.get("quality_baseline") or {}
    warnings = _detect_quality_contradictions(qb)
    return jsonify({"warnings": warnings, "count": len(warnings)})


@architecture_assistant_bp.route("/wizard/save-step", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@rate_limit(30, "1h")
def wizard_save_step():
    """Persist wizard step data to a Solution record.

    Creates the solution on first call (step 1), updates on subsequent calls.
    Returns the solution_id so the frontend can update the URL.
    """
    data = request.get_json(silent=True) or {}
    step = data.get("step", 1)
    solution_id = data.get("solution_id")

    # Ownership check: verify caller owns the solution
    if solution_id:
        from app.models.solution_models import Solution as _Sol
        _sol = _Sol.query.get(solution_id)
        if not _sol:
            return jsonify({"error": "Solution not found"}), 404
        if _sol.created_by_id != current_user.id:
            return jsonify({"error": "You do not own this solution"}), 403

    try:
        if step == 1:
            return _wizard_save_step_1(data, solution_id)
        elif step == 2:
            return _wizard_save_step_2(data, solution_id)
        elif step == 3:
            return _wizard_save_step_3(data, solution_id)
        elif step == 4:
            return _wizard_save_step_4(data, solution_id)
        elif step == 5:
            return _wizard_save_step_5(data, solution_id)
        elif step == 6:
            return _wizard_save_step_6(data, solution_id)
        elif step == 7:
            return _wizard_save_step_7(data, solution_id)
        elif step == 8:
            return _wizard_save_step_8(data, solution_id)
        elif step == 9:
            return _wizard_save_step_9(data, solution_id)
        else:
            return jsonify({"error": f"Invalid step: {step}"}), 400
    except Exception as exc:
        from sqlalchemy.orm.exc import StaleDataError
        if isinstance(exc, StaleDataError):
            db.session.rollback()
            return jsonify({
                "error": "Solution was modified by another user. Please refresh the page.",
                "conflict": True,
            }), 409
        raise


def _clear_solution_elements_by_types(solution_id, ae_types):
    """Remove SolutionElement joins for given ArchiMate types to avoid duplicates on re-save."""
    from app.models.solution_element import SolutionElement
    from app.models.archimate_core import ArchiMateElement
    try:
        joins = (
            SolutionElement.query
            .filter_by(solution_id=solution_id)
            .join(ArchiMateElement, SolutionElement.archimate_element_id == ArchiMateElement.id)
            .filter(ArchiMateElement.type.in_(ae_types))
            .all()
        )
        for se in joins:
            db.session.delete(se)
    except Exception as exc:
        logger.warning("_clear_solution_elements_by_types failed: %s", exc)


def _link_existing_element(solution_id, archimate_element_id):
    """Link an existing ArchiMateElement to a solution (no duplicate)."""
    from app.models.solution_element import SolutionElement
    from app.models.archimate_core import ArchiMateElement
    try:
        ae = ArchiMateElement.query.get(archimate_element_id)
        if not ae:
            return
        existing = SolutionElement.query.filter_by(
            solution_id=solution_id, archimate_element_id=archimate_element_id
        ).first()
        if not existing:
            db.session.add(SolutionElement(
                solution_id=solution_id,
                archimate_element_id=archimate_element_id,
                layer=ae.layer,
            ))
    except Exception as exc:
        logger.warning("_link_existing_element failed: %s", exc)


def _wizard_save_step_1(data, solution_id):
    """Step 1: Create/update solution with scope data, link apps, create motivation elements."""
    from app.models.solution_models import Solution, solution_applications
    from app.modules.solutions_strategic.v2.routes.solution_phase_routes import _sync_archimate_element

    scope = data.get("scope", {})
    if not scope.get("name") or not scope.get("business_domain"):
        return jsonify({"error": "Name and business domain are required"}), 400

    if solution_id:
        solution = Solution.query.get_or_404(solution_id)
    else:
        solution = Solution(created_by_id=current_user.id)
        db.session.add(solution)

    solution.name = scope["name"]
    solution.business_domain = scope["business_domain"]
    solution.description = scope.get("business_problem", "")
    solution.current_step = 1
    if not solution_id:
        solution.governance_status = "draft"

    # Save quality baseline if provided (Step 1B)
    qb = data.get("quality_baseline")
    if qb is not None:
        solution.quality_baseline_data = qb

    db.session.flush()

    # Link applications
    db.session.execute(  # tenant-filtered: scoped via parent FK (solution.id)
        solution_applications.delete().where(
            solution_applications.c.solution_id == solution.id
        )
    )
    for app_data in scope.get("linked_applications", []):
        db.session.execute(  # tenant-filtered: scoped via parent FK (solution.id)
            solution_applications.insert().values(
                solution_id=solution.id,
                application_component_id=app_data["id"],
                role="primary",
            )
        )

    # Clear existing motivation elements to avoid duplicates on re-save
    _clear_solution_elements_by_types(solution.id, ["Driver", "Goal", "Constraint", "Stakeholder"])

    # Create/link motivation elements via ARCH-LINK sync
    for driver in scope.get("drivers", []):
        if driver.get("id"):
            _link_existing_element(solution.id, driver["id"])
        else:
            _sync_archimate_element(
                solution.id,
                ae_type="Driver",
                ae_layer="Motivation",
                name=driver["name"],
            )

    for goal in scope.get("goals", []):
        if goal.get("id"):
            _link_existing_element(solution.id, goal["id"])
        else:
            _sync_archimate_element(
                solution.id,
                ae_type="Goal",
                ae_layer="Motivation",
                name=goal["name"],
            )

    for constraint in scope.get("constraints", []):
        if constraint.get("id"):
            _link_existing_element(solution.id, constraint["id"])
        else:
            _sync_archimate_element(
                solution.id,
                ae_type="Constraint",
                ae_layer="Motivation",
                name=constraint["name"],
            )

    for stakeholder in scope.get("stakeholders", []):
        if stakeholder.get("id"):
            _link_existing_element(solution.id, stakeholder["id"])
        else:
            _sync_archimate_element(
                solution.id,
                ae_type="Stakeholder",
                ae_layer="Motivation",
                name=stakeholder["name"],
            )

    # Link general ArchiMate elements (any layer) selected via the element search
    for ae in scope.get("archimate_elements", []):
        if ae.get("id"):
            _link_existing_element(solution.id, ae["id"])

    db.session.commit()
    return jsonify({"solution_id": solution.id, "status": "saved"})


def _wizard_save_step_2(data, solution_id):
    """Step 2: Save capability mappings with maturity scores."""
    from app.models.solution_models import Solution, SolutionCapabilityMapping

    if not solution_id:
        return jsonify({"error": "Solution must be created first (complete Step 1)"}), 400

    solution = Solution.query.get_or_404(solution_id)
    solution.current_step = max(solution.current_step or 1, 2)

    # Clear existing direct-solution capability mappings
    SolutionCapabilityMapping.query.filter_by(solution_id=solution.id).delete()

    for cap in data.get("capabilities", []):
        mapping = SolutionCapabilityMapping(
            solution_id=solution.id,
            capability_id=cap["id"],
            maturity_current=cap.get("maturity_current", 2),
            maturity_target=cap.get("maturity_target", 4),
            created_by_id=current_user.id,
        )
        db.session.add(mapping)

    db.session.commit()
    return jsonify({"solution_id": solution.id, "status": "saved"})


def _wizard_save_step_3(data, solution_id):
    """Step 3: Save gap analysis results as ArchiMate Gap elements."""
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.routes.solution_phase_routes import _sync_archimate_element

    if not solution_id:
        return jsonify({"error": "Solution must be created first"}), 400

    solution = Solution.query.get_or_404(solution_id)
    solution.current_step = max(solution.current_step or 1, 3)

    # Clear previous gap elements to avoid duplicates on re-save
    _clear_solution_elements_by_types(solution.id, ["Gap"])

    for gap in data.get("gaps", []):
        desc = gap.get("description", "")
        if gap.get("impact"):
            desc += f"\nImpact: {gap['impact']}"
        if gap.get("recommendation"):
            desc += f"\nRecommendation: {gap['recommendation']}"
        _sync_archimate_element(
            solution.id,
            ae_type="Gap",
            ae_layer="Implementation_and_Migration",
            name=f"Gap: {gap.get('capability_name', 'Unknown')}",
            description=desc,
        )

    db.session.commit()
    return jsonify({"solution_id": solution.id, "status": "saved"})


def _wizard_save_step_4(data, solution_id):
    """Step 4: Save options as SolutionOption rows (replaces arb_snapshot)."""
    from app.models.solution_models import Solution
    from app.models.solution_lifecycle_models import SolutionOption

    if not solution_id:
        return jsonify({"error": "Solution must be created first"}), 400

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"error": "Solution not found"}), 404
    if solution.created_by_id != current_user.id:
        return jsonify({"error": "You do not own this solution"}), 403

    selected = data.get("selected_option_id")

    # Delete existing options for idempotent save
    SolutionOption.query.filter_by(solution_id=solution_id).delete()

    options_data = data.get("options", [])
    for i, opt in enumerate(options_data):
        row = SolutionOption(
            solution_id=solution_id,
            name=opt.get("name", f"Option {i+1}"),
            description=opt.get("description", ""),
            strategic_fit=opt.get("strategic_fit"),
            risk_score=opt.get("risk_score"),
            coverage=opt.get("coverage"),
            pros=opt.get("pros", []),
            cons=opt.get("cons", []),
            ai_generated=opt.get("ai_generated", False),
            is_selected=(str(opt.get("id", "")) == str(selected)) if selected else False,
            rank=i + 1,
            approval_status="pending_review" if opt.get("ai_generated") else "approved",
        )
        db.session.add(row)

    solution.current_step = max(solution.current_step or 1, 4)
    db.session.commit()
    return jsonify({"solution_id": solution.id, "status": "saved"})


def _wizard_save_step_5(data, solution_id):
    """Step 5: Save roadmap plateaus and work packages as ArchiMate elements."""
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.routes.solution_phase_routes import _sync_archimate_element

    if not solution_id:
        return jsonify({"error": "Solution must be created first"}), 400

    solution = Solution.query.get_or_404(solution_id)
    solution.current_step = max(solution.current_step or 1, 5)

    # Clear previous plateau/work package elements to avoid duplicates
    _clear_solution_elements_by_types(solution.id, ["Plateau", "WorkPackage"])

    roadmap = data.get("roadmap", {})
    for plateau in roadmap.get("plateaus", []):
        _sync_archimate_element(
            solution.id,
            ae_type="Plateau",
            ae_layer="Implementation_and_Migration",
            name=plateau.get("name", "Unnamed Plateau"),
        )

    for wp in roadmap.get("workPackages", []):
        _sync_archimate_element(
            solution.id,
            ae_type="WorkPackage",
            ae_layer="Implementation_and_Migration",
            name=wp.get("name", "Unnamed Work Package"),
        )

    # Store structured roadmap in arb_snapshot for resume hydration
    existing_snapshot = solution.arb_snapshot or {}
    existing_snapshot["roadmap"] = roadmap
    solution.arb_snapshot = existing_snapshot

    db.session.commit()
    return jsonify({"solution_id": solution.id, "status": "saved"})


def _wizard_save_step_6(data, solution_id):
    """Step 6: Save ARB draft as SolutionARBDraft row (replaces arb_snapshot)."""
    from app.models.solution_models import Solution
    from app.models.solution_lifecycle_models import SolutionARBDraft

    if not solution_id:
        return jsonify({"error": "Solution must be created first"}), 400

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"error": "Solution not found"}), 404
    if solution.created_by_id != current_user.id:
        return jsonify({"error": "You do not own this solution"}), 403

    arb_data = data.get("arb_draft", {})
    draft = None
    if arb_data:
        # Upsert: find existing unsubmitted draft or create new
        draft = SolutionARBDraft.query.filter_by(
            solution_id=solution_id, submitted=False
        ).first()
        if not draft:
            max_ver = db.session.query(db.func.max(SolutionARBDraft.version)).filter_by(
                solution_id=solution_id
            ).scalar() or 0
            draft = SolutionARBDraft(solution_id=solution_id, version=max_ver + 1)
            db.session.add(draft)
        draft.business_justification = arb_data.get("business_justification", "")
        draft.technical_assessment = arb_data.get("technical_assessment", "")
        draft.risk_analysis = arb_data.get("risk_analysis", "")
        draft.cost_summary = arb_data.get("cost_summary", "")

        # Persist full draft (including implementation_approach) into arb_snapshot
        # so the export endpoint can render the latest edits
        existing_snapshot = solution.arb_snapshot or {}
        existing_snapshot["arb_draft"] = {
            "business_justification": arb_data.get("business_justification", ""),
            "technical_assessment": arb_data.get("technical_assessment", ""),
            "risk_analysis": arb_data.get("risk_analysis", ""),
            "implementation_approach": arb_data.get("implementation_approach", ""),
            "cost_summary": arb_data.get("cost_summary", ""),
        }
        solution.arb_snapshot = existing_snapshot

    if data.get("submit"):
        if draft and not draft.can_submit():
            return jsonify({"error": "ARB draft requires approval before submission"}), 403
        solution.governance_status = "arb_submitted"
        if draft:
            draft.submitted = True
            draft.submitted_at = datetime.utcnow()
            draft.submitted_by_id = current_user.id
        # Preserve existing ArchiMate snapshot behavior
        try:
            from app.models.solution_models import SolutionArchiMateElement
            elements = SolutionArchiMateElement.query.filter_by(solution_id=solution.id).all()
            existing_snapshot = solution.arb_snapshot or {}
            existing_snapshot["submit_timestamp"] = datetime.utcnow().isoformat()
            existing_snapshot["element_count"] = len(elements)
            existing_snapshot["element_ids"] = [e.archimate_element_id for e in elements]
            solution.arb_snapshot = existing_snapshot
        except Exception as e:
            logger.warning(f"ARB snapshot failed: {e}")

        # Create ARB review record if model available
        try:
            from app.models.architecture_review_board import ARBReview
            review = ARBReview(
                solution_id=solution.id,
                submitted_by_id=current_user.id,
                status="pending",
                submission_date=datetime.utcnow(),
            )
            db.session.add(review)
        except (ImportError, Exception) as e:
            logger.warning(f"ARB review creation skipped: {e}")

    solution.current_step = max(solution.current_step or 1, 6)
    db.session.commit()
    return jsonify({"solution_id": solution.id, "status": "saved"})


def _wizard_save_step_7(data, solution_id):
    """Step 7: Save execution tracking entries."""
    from app.models.solution_models import Solution
    from app.models.solution_governance import SolutionExecutionTracking

    if not solution_id:
        return jsonify({"error": "Solution must be created first"}), 400

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"error": "Solution not found"}), 404
    if solution.created_by_id != current_user.id:
        return jsonify({"error": "You do not own this solution"}), 403
    if solution.governance_status not in ("arb_submitted", "arb_approved", "approved"):
        return jsonify({"error": "Step 7 requires ARB approval"}), 403

    items = data.get("execution_items", [])

    # Delete existing for idempotent save
    SolutionExecutionTracking.query.filter_by(solution_id=solution_id).delete()

    for item in items:
        row = SolutionExecutionTracking(
            solution_id=solution_id,
            work_package_name=item.get("work_package_name", ""),
            status=item.get("status", "planned"),
            percent_complete=item.get("percent_complete", 0),
            milestone_name=item.get("milestone_name"),
            blockers=item.get("blockers"),
        )
        db.session.add(row)

    solution.current_step = max(solution.current_step or 1, 7)
    db.session.commit()
    return jsonify({"solution_id": solution.id, "status": "saved"})


def _wizard_save_step_8(data, solution_id):
    """Step 8: Save governance checkpoints to arb_snapshot JSON."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.solution_models import Solution

    if not solution_id:
        return jsonify({"error": "solution_id required for step 8"}), 400

    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id:
        return jsonify({"error": "You do not own this solution"}), 403

    checkpoints = data.get("checkpoints", [])
    if not checkpoints:
        return jsonify({"error": "At least one checkpoint required"}), 400

    valid_statuses = {"approved", "pending", "blocked", "not_started"}
    valid_phases = {"A", "B", "C", "D", "E", "F", "G", "H"}

    validated = []
    for cp in checkpoints:
        phase = (cp.get("phase") or "").upper()
        status = cp.get("status", "not_started")
        if phase not in valid_phases:
            continue
        if status not in valid_statuses:
            status = "not_started"
        validated.append({
            "phase": phase,
            "status": status,
            "notes": cp.get("notes", ""),
            "reviewer": cp.get("reviewer", ""),
            "updated_at": datetime.utcnow().isoformat(),
        })

    snapshot = solution.arb_snapshot or {}
    snapshot["governance_checkpoints"] = validated
    solution.arb_snapshot = snapshot
    flag_modified(solution, "arb_snapshot")
    solution.current_step = max(solution.current_step or 1, 8)
    db.session.commit()

    return jsonify({"status": "saved", "checkpoints": len(validated)})


def _wizard_save_step_9(data, solution_id):
    """Step 9: Save lessons learned and outcome metrics."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.solution_lifecycle_models import SolutionMetric
    from app.models.solution_models import Solution

    if not solution_id:
        return jsonify({"error": "solution_id required for step 9"}), 400

    solution = Solution.query.get_or_404(solution_id)

    # Save metrics to relational table
    metrics_data = data.get("metrics", [])
    saved_metric_count = 0
    for m in metrics_data:
        metric_name = (m.get("metric_name") or "").strip()
        if not metric_name:
            continue
        # Upsert by name
        existing = SolutionMetric.query.filter_by(
            solution_id=solution_id, name=metric_name
        ).first()
        if existing:
            existing.baseline_value = str(m.get("baseline_value", ""))
            existing.target_value = str(m.get("target_value", ""))
            existing.actual_value = str(m.get("actual_value", ""))
            existing.unit = m.get("unit", "")
            existing.notes = m.get("notes", "")
        else:
            metric = SolutionMetric(
                solution_id=solution_id,
                name=metric_name,
                baseline_value=str(m.get("baseline_value", "")),
                target_value=str(m.get("target_value", "")),
                actual_value=str(m.get("actual_value", "")),
                unit=m.get("unit", ""),
                notes=m.get("notes", ""),
            )
            db.session.add(metric)
        saved_metric_count += 1

    # Save lessons to arb_snapshot JSON
    lessons_data = data.get("lessons", [])
    snapshot = solution.arb_snapshot or {}
    validated_lessons = []
    valid_categories = {"process", "technical", "people", "governance", "other"}
    for lesson in lessons_data:
        desc = (lesson.get("description") or "").strip()
        if not desc:
            continue
        cat = lesson.get("category", "other")
        if cat not in valid_categories:
            cat = "other"
        validated_lessons.append({
            "category": cat,
            "description": desc,
            "recommendation": lesson.get("recommendation", ""),
            "created_at": datetime.utcnow().isoformat(),
        })

    snapshot["lessons_learned"] = validated_lessons
    solution.arb_snapshot = snapshot
    flag_modified(solution, "arb_snapshot")
    solution.current_step = max(solution.current_step or 1, 9)
    db.session.commit()

    return jsonify({
        "status": "saved",
        "metrics_count": saved_metric_count,
        "lessons_count": len(validated_lessons),
    })


@architecture_assistant_bp.route("/wizard/compare-options", methods=["GET"])
@login_required
@require_roles("admin", "architect")
def wizard_compare_options():
    """Compare solution options using weighted MCDA scoring."""
    from app.models.solution_lifecycle_models import SolutionOption

    solution_id = request.args.get("solution_id", type=int)
    if not solution_id:
        return jsonify({"error": "solution_id required"}), 400

    options = SolutionOption.query.filter_by(solution_id=solution_id).order_by(SolutionOption.rank).all()
    if len(options) < 2:
        return jsonify({"error": "At least 2 options required for comparison"}), 400

    # Default MCDA weights
    weights = {"strategic_fit": 0.4, "risk_score": 0.3, "coverage": 0.3}

    result = []
    for opt in options:
        sf = float(opt.strategic_fit or 0)
        rs = float(opt.risk_score or 0)
        cv = float(opt.coverage or 0)
        # Risk is inverted (lower is better)
        weighted = (sf * weights["strategic_fit"] +
                    (1 - rs) * weights["risk_score"] +
                    cv * weights["coverage"])
        entry = opt.to_dict()
        entry["weighted_score"] = round(weighted, 4)
        entry["scores"] = {"strategic_fit": sf, "risk_inverted": round(1 - rs, 4), "coverage": cv}
        result.append(entry)

    result.sort(key=lambda x: x["weighted_score"], reverse=True)
    return jsonify({"options": result, "weights": weights})


@architecture_assistant_bp.route("/wizard/approve-content", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def wizard_approve_content():
    """Approve or reject AI-generated content (4-eyes principle)."""
    data = request.get_json(silent=True) or {}
    content_type = data.get("content_type")  # "option" or "arb_draft"
    content_id = data.get("content_id")
    decision = data.get("decision")  # "approved" or "rejected"

    if decision not in ("approved", "rejected"):
        return jsonify({"error": "Decision must be 'approved' or 'rejected'"}), 400

    if content_type == "option":
        from app.models.solution_lifecycle_models import SolutionOption
        item = SolutionOption.query.get(content_id)
    elif content_type == "arb_draft":
        from app.models.solution_lifecycle_models import SolutionARBDraft
        item = SolutionARBDraft.query.get(content_id)
    else:
        return jsonify({"error": "content_type must be 'option' or 'arb_draft'"}), 400

    if not item:
        return jsonify({"error": "Content not found"}), 404

    item.approval_status = decision
    item.reviewed_by_id = current_user.id
    item.reviewed_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"status": decision, "content_id": content_id})


@architecture_assistant_bp.route("/ai-reasoning/<int:solution_id>", methods=["GET"])
@login_required
def get_ai_reasoning(solution_id):
    """ENH-019: Return AI audit log entries with reasoning for a solution.

    Provides explainability by exposing prompt_summary, reasoning, prompt_hash,
    model_name, and approval_status for each AI invocation on this solution.
    """
    from app.models.ai_audit_log import AIAuditLog
    from app.models.solution_models import Solution
    from app.utils.route_guards import require_entity

    # Zero audit entries for a solution that does not exist would read as
    # "this solution had no AI involvement".
    require_entity(Solution, solution_id, description="Solution not found")

    logs = AIAuditLog.query.filter_by(solution_id=solution_id).order_by(
        AIAuditLog.created_at.desc()
    ).limit(50).all()

    return jsonify({
        "success": True,
        "solution_id": solution_id,
        "entries": [entry.to_dict() for entry in logs],
        "total": len(logs),
    })


def _ai_audit_entry_or_404(entry_id):
    """An AI audit entry, only if the caller may see it.

    ``AIAuditLog`` has no tenant column. An entry tied to a solution is reachable through that
    solution, which is tenant-fenced (another organisation's solution is a 404). An entry with no
    solution belongs to the user who made it; a platform admin may read any.
    """
    from flask import abort

    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.ai_audit_log import AIAuditLog
    from app.models.solution_models import Solution
    from app.utils.route_guards import require_entity

    # tenant-scoping-ok: access is decided on the next lines, through the tenant-fenced solution
    # or the entry's own user.
    entry = AIAuditLog.query.get_or_404(entry_id)
    if entry.solution_id is not None:
        require_entity(Solution, entry.solution_id, description="Entry not found")
    elif not is_platform_admin(current_user) and entry.user_id != current_user.id:
        abort(404)
    return entry


@architecture_assistant_bp.route("/ai-reasoning/entry/<int:entry_id>", methods=["GET"])
@login_required
def get_ai_reasoning_detail(entry_id):
    """ENH-019: Return detailed reasoning for a single AI audit log entry."""
    entry = _ai_audit_entry_or_404(entry_id)
    return jsonify({
        "success": True,
        "entry": entry.to_dict(),
    })


@architecture_assistant_bp.route("/ai-reasoning/entry/<int:entry_id>/approve", methods=["POST"])
@login_required
@require_roles("admin", "architect")
def approve_ai_reasoning(entry_id):
    """ENH-019: Approve or reject an AI-generated output after reviewing reasoning."""
    entry = _ai_audit_entry_or_404(entry_id)
    data = request.get_json(silent=True) or {}
    decision = data.get("decision")

    if decision not in ("approved", "rejected"):
        return jsonify({"error": "Decision must be 'approved' or 'rejected'"}), 400

    entry.approval_status = decision
    entry.approved_by_id = current_user.id
    entry.approved_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "success": True,
        "entry_id": entry.id,
        "approval_status": decision,
    })


# =============================================================================
# ENH-011: Paginated capabilities endpoint for Architecture Assistant
# =============================================================================


@architecture_assistant_bp.route("/capabilities", methods=["GET"])
@login_required
def paginated_capabilities():
    """Return a paginated list of capabilities for lazy-loading in the wizard.

    Query params:
        page (int): 1-based page number (default 1).
        per_page (int): Items per page, max 200 (default 50).
        search (str): Optional name filter (case-insensitive substring match).
    """
    from app.models.unified_capability import UnifiedCapability

    page = max(1, safe_int_arg('page', 1, minimum=1))
    per_page = min(200, max(1, safe_int_arg('per_page', 50, minimum=1, maximum=500)))
    search = request.args.get("search", "").strip()

    cache_key = f"aa_capabilities:p{page}:pp{per_page}:s{search}"
    # Read-only cache check (the result is computed and stored below). Calling
    # _get_cached with query_fn=None would invoke None() on a miss.
    entry = _catalog_cache.get(cache_key)
    if entry and _time.time() - entry["ts"] < 120:
        return jsonify(entry["data"])

    query = UnifiedCapability.query.order_by(UnifiedCapability.name)
    if search:
        query = query.filter(UnifiedCapability.name.ilike(f"%{search}%"))

    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    result = {
        "success": True,
        "capabilities": [
            {
                "id": c.id,
                "name": c.name,
                "level": getattr(c, "level", None),
                "archimate_layer": getattr(c, "archimate_layer", None),
                "has_gap": getattr(c, "has_gap", False),
            }
            for c in items
        ],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": (total + per_page - 1) // per_page,
    }

    # Store in cache
    _catalog_cache[cache_key] = {"data": result, "ts": _time.time()}

    return jsonify(result)


@architecture_assistant_ui_bp.route("/", methods=["GET"])
@login_required
def architecture_assistant_index():
    """
    Architecture Assistant main page
    ---
    tags:
      - Architecture Assistant
    summary: Architecture Assistant main page
    description: Main UI page for the Architecture Assistant
    responses:
      200:
        description: Architecture Assistant UI page
    """
    llm_avail = bool(current_app.config.get('LLM_API_KEY') or current_app.config.get('OPENAI_API_KEY'))
    return render_template("architecture_assistant/index.html", llm_available=llm_avail)

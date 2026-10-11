from app.services.archimate_backbone import sync_archimate_element
import json
import logging
from flask import jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload
from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.solution_models import Solution
from app.models.solution_capability import SolutionCapability
from app.decorators import audit_log
from app.modules.ai_chat.approval_gate import tag_ai_action
from app.modules.ai_chat.services.solution_ai_service import (
    score_capability_quality,
    _token_overlap,
)
from .solution_design_routes import solution_design_bp
from app.core.api.response import api_error, api_success
from app.modules.solutions_strategic.v2.routes.traceability_helpers import (
    persist_traceability_links,
    build_traceability,
    match_capability_names_to_prefixed_ids,
)
from app.middleware.tenant_decorators import is_active_org_admin

logger = logging.getLogger(__name__)


def _compute_architecture_completeness(solution_id):
    """Compute a 0-100 architecture completeness score for a solution (CAP-010).

    Checks presence of linked entities across key architectural dimensions:
    ArchiMate elements, applications, vendor products, requirements, and capabilities.
    Each dimension contributes 20 points (present = 20, absent = 0).
    """
    score = 0
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        if SolutionArchiMateElement.query.filter_by(solution_id=solution_id).first():
            score += 20
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        logger.exception("Failed to operation")
        pass
    try:
        tbl = db.metadata.tables.get("solution_applications")
        if tbl is not None:
            row = db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                tbl.select().where(tbl.c.solution_id == solution_id).limit(1)
            ).first()
            if row:
                score += 20
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        logger.exception("Failed to compute tbl")
        pass
    try:
        tbl = db.metadata.tables.get("solution_vendor_products")
        if tbl is not None:
            row = db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                tbl.select().where(tbl.c.solution_id == solution_id).limit(1)
            ).first()
            if row:
                score += 20
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        logger.exception("Failed to compute tbl")
        pass
    try:
        from app.models.solution_architect_models import SolutionRequirement
        if SolutionRequirement.query.filter_by(solution_id=solution_id).first():
            score += 20
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        logger.exception("Failed to operation")
        pass
    try:
        from app.models.solution_models import SolutionCapabilityMapping
        if SolutionCapabilityMapping.query.filter_by(solution_id=solution_id).first():
            score += 20
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        logger.exception("Failed to operation")
        pass
    return score


def _create_notification(user_id, notification_type, message, solution_id=None):
    """Create a solution lifecycle notification (ENT-020). Caller must commit."""
    if not user_id:
        return
    try:
        from app.models.solution_governance import SolutionNotification
        n = SolutionNotification(
            solution_id=solution_id,
            user_id=user_id,
            type=notification_type,
            message=message,
        )
        db.session.add(n)
    except Exception as e:
        logger.debug("Could not create notification: %s", e)


# =============================================================================
# AI PRE-POPULATION ENDPOINT
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/ai-populate", methods=["POST"])
@login_required
@audit_log("ai_populate_solution")
def ai_populate_solution_elements(solution_id: int):
    """Use AI to suggest capabilities and ArchiMate elements for a solution."""
    solution = Solution.query.get_or_404(solution_id)

    try:
        from app.models.solution_models import SolutionCapabilityMapping
        from app.modules.solutions_strategic.v2.services.solution_ai_service import (
            SolutionAIService,
        )

        ai_service = SolutionAIService()

        # Get current capabilities - eager-load the capability relationship to avoid N+1 queries
        current_caps = SolutionCapabilityMapping.query.options(
            joinedload(SolutionCapabilityMapping.capability)
        ).filter_by(problem_id=solution_id).all()

        capabilities = [
            {
                "name": cap.capability.name if cap.capability else "Unknown",
                "category": cap.support_level or "required",
            }
            for cap in current_caps
        ]

        # Get phase from request (default Phase A)
        req_data = request.get_json(silent=True) or {}
        adm_phase = req_data.get("phase", "A")

        # Get AI suggestions
        result = ai_service.suggest_elements(
            solution_description=solution.description or solution.name,
            capabilities=capabilities,
            solution_type=solution.solution_type,
            business_domain=solution.business_domain,
        )

        # Persist reasoning state so the panel shows provenance
        reasoning_state_id = None
        try:
            from app.models.solution_reasoning import SolutionAIReasoningState
            is_ai = result.get("source") == "ai"
            state = SolutionAIReasoningState(
                solution_id=solution_id,
                adm_phase=adm_phase,
                context_snapshot={
                    "solution_name": solution.name,
                    "solution_type": solution.solution_type,
                    "business_domain": solution.business_domain,
                    "capabilities_count": len(capabilities),
                    "llm_provider": "LLM" if is_ai else "fallback",
                    "entities_created": {},
                },
                suggestions={"archimate_elements": result.get("suggestions", {})},
                confidence_score_pct=0.8 if is_ai else 0.5,
                data_sources_used={
                    "solution_description": {"source": "solution.description", "weight": 0.5},
                    "capabilities": {"count": len(capabilities), "weight": 0.3},
                    "solution_metadata": {"source": "solution_type + business_domain", "weight": 0.2},
                },
                recommendation_reasoning={
                    "methodology": "ArchiMate 3.2 layer analysis",
                    "approach": "Elements suggested per layer based on solution description and capabilities",
                    "adm_phase": adm_phase,
                },
                model_assumptions={
                    "technical_assumptions": ["Cloud or hybrid deployment", "Standard enterprise architecture patterns apply"],
                    "business_assumptions": ["Solution description is accurate", "All 6 ArchiMate layers relevant"],
                },
                alternative_options_considered={},
                uncertainty_factors={
                    "high_impact": [{"factor": "Description completeness", "likelihood": "MEDIUM", "mitigation": "Review and refine description before accepting all suggestions"}]
                } if not is_ai else {},
            )
            db.session.add(state)
            db.session.commit()
            reasoning_state_id = state.id
        except Exception as rs_err:
            db.session.rollback()
            logger.warning(f"Could not save reasoning state: {rs_err}")

        return api_success(data=
            {
                "success": result.get("success", False),
                "suggestions": result.get("suggestions", {}),
                "source": result.get("source", "unknown"),
                "solution_id": solution_id,
                "reasoning_state_id": reasoning_state_id,
            }
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in AI population: {e}")
        return api_error("An internal error occurred", 500)


def _match_against_catalog(suggestions, catalog_caps, problem_brief=""):
    """Classify AI capability suggestions against the BusinessCapability catalog.

    For each suggestion:
    - Exact name match in catalog → match_type 'exact', links to that cap
    - LLM-provided closest_match verified in catalog → 'partial'
    - Token-overlap ≥ 0.6 with any catalog entry → 'partial'
    - Otherwise → 'novel' (does not exist in catalog)

    Also runs quality scoring on each suggestion.

    Args:
        suggestions: list of dicts from SolutionAIService.suggest_capabilities()
        catalog_caps: list of BusinessCapability ORM objects (full catalog)
        problem_brief: the original problem description string for quality scoring

    Returns:
        dict with 'suggestions' (enriched list) and 'gap_summary' counts
    """
    cap_by_name = {c.name.lower(): c for c in catalog_caps}

    seen_names = set()
    enriched = []
    gap_summary = {"exact": 0, "partial": 0, "novel": 0}

    for raw in suggestions:
        name = (raw.get("name") or "").strip()
        if not name:
            continue

        # Deduplicate by lowercase name
        name_key = name.lower()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)

        match_type = "novel"
        match_score = 0.0
        closest_match_id = None

        # 1. Exact name match
        if name_key in cap_by_name:
            match_type = "exact"
            match_score = 1.0
            closest_match_id = cap_by_name[name_key].id
        else:
            # 2. LLM-suggested closest_match
            llm_closest = (raw.get("closest_match") or "").strip()
            if llm_closest and llm_closest.lower() in cap_by_name:
                match_type = "partial"
                match_score = 0.8
                closest_match_id = cap_by_name[llm_closest.lower()].id
            else:
                # 3. Token-overlap fallback across full catalog
                best_score = 0.0
                best_cap = None
                for cap in catalog_caps:
                    score = _token_overlap(name, cap.name)
                    if score > best_score:
                        best_score = score
                        best_cap = cap
                if best_score >= 0.6 and best_cap is not None:
                    match_type = "partial"
                    match_score = round(best_score, 3)
                    closest_match_id = best_cap.id

        # Coverage status from catalog (how well the existing cap is implemented)
        coverage_status = "none"
        if closest_match_id is not None:
            try:
                cap_obj = cap_by_name.get(name_key) or next(
                    (c for c in catalog_caps if c.id == closest_match_id), None
                )
                if cap_obj is not None:
                    app_count = len(cap_obj.applications)
                    if app_count >= 3:
                        coverage_status = "full"
                    elif app_count >= 1:
                        coverage_status = "partial"
            except Exception:
                coverage_status = "none"

        # Quality scoring (pure Python, no LLM)
        other_names = [s.get("name", "") for s in suggestions if s.get("name") != name]
        quality = score_capability_quality(
            name=name,
            description=raw.get("description") or raw.get("rationale") or "",
            brief=problem_brief,
            other_names=other_names,
        )

        gap_summary[match_type] = gap_summary.get(match_type, 0) + 1

        enriched.append({
            "name": name,
            "description": raw.get("description") or raw.get("rationale") or "",
            "category": raw.get("category") or "required",
            "rationale": raw.get("rationale") or "",
            "match_type": match_type,
            "match_score": match_score,
            "closest_match_id": closest_match_id,
            "coverage_status": coverage_status,
            "quality_score": quality.get("score", 0.0),
            "quality_warnings": quality.get("warnings", []),
        })

    return {"suggestions": enriched, "gap_summary": gap_summary}


@solution_design_bp.route("/ai-suggest-capabilities", methods=["POST"])
@login_required
@audit_log("ai_suggest_capabilities")
def ai_suggest_capabilities():
    """Use AI to suggest capabilities based on solution description.

    Returns enriched suggestions classified as exact/partial/novel against the
    BusinessCapability catalog, with quality scores and gap summary.
    """
    try:
        from app.models.business_capability import BusinessCapability
        from app.modules.solutions_strategic.v2.services.solution_ai_service import (
            SolutionAIService,
        )

        data = request.get_json()
        description = data.get("description", "")
        solution_type = data.get("solution_type")
        business_domain = data.get("business_domain")
        motivation_elements = data.get("motivation_elements", [])
        sol_id = data.get("solution_id")

        if not description:
            return api_error("Description is required", 400)

        # Load full ORM objects so _match_against_catalog can check relationships
        catalog_caps = BusinessCapability.query.order_by(BusinessCapability.name).all()
        existing = [{"id": c.id, "name": c.name} for c in catalog_caps]

        ai_service = SolutionAIService()
        result = ai_service.suggest_capabilities(
            solution_description=description,
            solution_type=solution_type,
            business_domain=business_domain,
            existing_capabilities=existing,
            motivation_elements=motivation_elements,
        )

        raw_suggestions = result.get("suggestions") or result.get("capabilities") or []
        enriched = _match_against_catalog(
            suggestions=raw_suggestions,
            catalog_caps=catalog_caps,
            problem_brief=description,
        )

        # Persist motivation→capability traceability links
        _PREFIX_MAP = {"Driver": "drv", "Goal": "goal", "Constraint": "con"}
        valid_source_ids = set()
        for m in motivation_elements:
            prefix = _PREFIX_MAP.get(m.get("type", ""), "drv")
            m_id = m.get("id")
            if m_id is not None:
                valid_source_ids.add(f"{prefix}_{m_id}")

        matched = enriched["suggestions"]
        if valid_source_ids and matched and sol_id:
            from app.models.traceability import TraceabilityLink
            TraceabilityLink.query.filter_by(
                solution_id=sol_id,
                traceability_layer="motivation_to_capability",
                traceability_type="derivation",
            ).delete()
            db.session.commit()

            persist_traceability_links(
                solution_id=sol_id,
                items=matched,
                ref_field="derived_from",
                item_id_field="existing_id",
                item_type_field="match_type",
                traceability_layer="motivation_to_capability",
                valid_source_ids=valid_source_ids,
            )

        return api_success(data={
            "suggestions": enriched["suggestions"],
            "gap_summary": enriched["gap_summary"],
            "source": result.get("source", "unknown"),
        })
    except Exception as e:
        logger.error(f"Error suggesting capabilities: {e}")
        return api_error("An internal error occurred", 500)


@solution_design_bp.route(
    "/<int:solution_id>/solution-capabilities", methods=["POST"]
)
@login_required
def create_ai_solution_capability(solution_id):
    """Persist an AI-derived capability scoped to this solution (not yet in global catalog).

    Body fields (all optional except name):
        name, description, category, match_type, match_score,
        closest_match_id, quality_score, quality_warnings
    """
    Solution.query.get_or_404(solution_id)
    body = request.get_json(silent=True) or {}

    name = (body.get("name") or "").strip()
    if not name:
        return api_error("name is required", 400)

    warnings_raw = body.get("quality_warnings")
    if isinstance(warnings_raw, list):
        warnings_json = json.dumps(warnings_raw)
    else:
        warnings_json = warnings_raw  # already a JSON string or None

    try:
        sol_cap = SolutionCapability(
            solution_id=solution_id,
            name=name,
            description=body.get("description") or "",
            category=body.get("category") or "required",
            match_type=body.get("match_type") or "novel",
            match_score=body.get("match_score"),
            closest_match_id=body.get("closest_match_id"),
            quality_score=body.get("quality_score"),
            quality_warnings=warnings_json,
            source="ai_derived",
        )
        db.session.add(sol_cap)
        db.session.commit()
        return api_success(data=sol_cap.to_dict())
    except Exception as e:
        db.session.rollback()
        logger.error("Error creating SolutionCapability for solution %s: %s", solution_id, e)
        return api_error("An internal error occurred", 500)


@solution_design_bp.route(
    "/<int:solution_id>/capabilities/<int:solution_cap_id>/promote",
    methods=["POST"],
)
@login_required
def promote_solution_capability(solution_id, solution_cap_id):
    """Promote a solution-scoped capability to the global BusinessCapability catalog.

    If an identical name already exists in the catalog the solution capability is
    linked to the existing entry rather than creating a duplicate.
    Sets SolutionCapability.promoted_to_id and re-runs quality scoring so the
    response includes an actionable warning when quality < 0.6.
    """
    Solution.query.get_or_404(solution_id)

    sol_cap = SolutionCapability.query.filter_by(
        id=solution_cap_id, solution_id=solution_id
    ).first_or_404()

    try:
        from app.models.business_capability import BusinessCapability

        # Check for duplicate name (case-insensitive)
        existing = BusinessCapability.query.filter(
            db.func.lower(BusinessCapability.name) == sol_cap.name.lower()
        ).first()

        if existing:
            promoted_cap = existing
        else:
            promoted_cap = BusinessCapability(
                name=sol_cap.name,
                description=sol_cap.description or "",
                level=2,
            )
            db.session.add(promoted_cap)
            db.session.flush()

        sol_cap.promoted_to_id = promoted_cap.id
        db.session.commit()

        # Re-run quality scoring after promotion
        quality = score_capability_quality(
            name=sol_cap.name,
            description=sol_cap.description or "",
            brief="",
        )
        quality_score = quality.get("score", 0.0)
        warnings = quality.get("warnings", [])
        if quality_score < 0.6:
            warnings.insert(
                0,
                f"Quality score {quality_score:.0%} is below the recommended 60% threshold — "
                "review the name before publishing to the catalog.",
            )

        return api_success(data={
            "promoted_to_id": promoted_cap.id,
            "promoted_to_name": promoted_cap.name,
            "was_existing": existing is not None,
            "solution_capability_id": sol_cap.id,
            "quality_score": quality_score,
            "quality_warnings": warnings,
        })
    except Exception as e:
        db.session.rollback()
        logger.error(
            "Error promoting SolutionCapability %s for solution %s: %s",
            solution_cap_id, solution_id, e,
        )
        return api_error("An internal error occurred", 500)


@solution_design_bp.route(
    "/solutions/<int:solution_id>/derive-system-capabilities", methods=["POST"]
)
@login_required
def derive_system_capabilities(solution_id):
    """AI-derive Technical + Application Capabilities from business capabilities.

    Takes the selected business capabilities + motivation context and asks the LLM
    to define what system capabilities (TCM + ACM) the solution needs.

    Returns::

        {
          "technical_capabilities": [
            {"name": "...", "domain": "SECURITY_IDENTITY", "description": "...",
             "rationale": "...", "existing_coverage": "none|partial|full"}
          ],
          "application_capabilities": [
            {"name": "...", "category": "core|supporting", "description": "...",
             "rationale": "...", "existing_coverage": "none|partial|full"}
          ]
        }
    """
    import json as _json
    from app.models.solution_models import Solution, SolutionCapabilityMapping
    from app.models.unified_capability import UnifiedCapability
    from app.modules.ai_chat.services.llm_service import LLMService

    solution = Solution.query.get_or_404(solution_id)

    # Gather business capabilities
    mappings = SolutionCapabilityMapping.query.filter_by(solution_id=solution_id).all()
    cap_ids = [m.capability_id for m in mappings]
    capabilities = []
    if cap_ids:
        from app.models.business_capability import BusinessCapability
        capabilities = BusinessCapability.query.filter(
            BusinessCapability.id.in_(cap_ids)
        ).all()
    if not capabilities:
        caps = UnifiedCapability.query.filter(
            UnifiedCapability.id.in_(cap_ids)
        ).all() if cap_ids else []
        capabilities = caps

    caps_json = _json.dumps([{"name": c.name, "level": getattr(c, 'level', 0)} for c in capabilities], indent=2)

    # Gather motivation context
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
    orch = SolutionAIOrchestrator()
    motiv_ctx = orch._gather_full_motivation_context(solution_id)

    # Gather existing technical capabilities for matching
    from app.models.technical_capability import TechnicalCapability
    existing_tech = TechnicalCapability.query.limit(100).all()
    existing_tech_json = _json.dumps([
        {"name": t.name, "domain": t.acm_domain, "level": t.level_number}
        for t in existing_tech
    ], indent=2) if existing_tech else "[]"

    prompt = f"""You are an enterprise architect. Given a solution's business capabilities and motivation context,
derive the TECHNICAL CAPABILITIES (infrastructure/platform needs) and APPLICATION CAPABILITIES
(what the system must be able to do) required.

## Solution: {solution.name or ''}
## Business Domain: {solution.business_domain or ''}

## Selected Business Capabilities
{caps_json}

## Drivers
{motiv_ctx['drivers_json']}

## Goals
{motiv_ctx['goals_json']}

## Constraints
{motiv_ctx['constraints_json']}

## Existing Technical Capabilities in Catalog
{existing_tech_json}

## Instructions
For each business capability, identify:
1. What TECHNICAL capabilities are needed (infrastructure, platform, security, DevOps)
2. What APPLICATION capabilities the system must provide (features, services, integrations)

Technical capability domains: USER_EXPERIENCE, APPLICATION_SERVICES, DATA_STORAGE,
SECURITY_IDENTITY, DEVOPS_PLATFORM, AI_ANALYTICS, COMMUNICATION

Return ONLY valid JSON:
{{
  "technical_capabilities": [
    {{"name": "string", "domain": "one of 7 domains above", "description": "string",
      "rationale": "which business capability/driver requires this",
      "existing_match": "name of existing tech capability if match, or null"}}
  ],
  "application_capabilities": [
    {{"name": "string", "category": "core|supporting|integration",
      "description": "what the system must do",
      "rationale": "which business capability requires this",
      "business_capability_name": "the business capability this realizes"}}
  ]
}}

Generate 4-8 technical capabilities and 4-8 application capabilities.
Prefer matching existing technical capabilities from the catalog before suggesting new ones."""

    try:
        provider, model = LLMService._get_configured_provider()
        response_text, interaction = LLMService._call_llm(
            prompt=prompt, model=model, provider=provider,
        )
        parsed = orch._parse_draft_response(response_text or '{}')

        return jsonify({
            "success": True,
            "technical_capabilities": parsed.get("technical_capabilities", []),
            "application_capabilities": parsed.get("application_capabilities", []),
        })
    except Exception as e:
        logger.error(f"System capability derivation failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "technical_capabilities": [],
            "application_capabilities": [],
        }), 500


@solution_design_bp.route("/ai-generate-requirements", methods=["POST"])
@login_required
@audit_log("ai_generate_requirements")
def ai_generate_requirements():
    """Use AI to generate requirements based on solution description and capabilities."""
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_service import (
            SolutionAIService,
        )

        data = request.get_json()
        description = data.get("description", "")
        solution_type = data.get("solution_type")
        business_domain = data.get("business_domain")
        capabilities = data.get("capabilities", [])
        solution_id = data.get("solution_id")

        if not description:
            return api_error("Description is required", 400)

        ai_service = SolutionAIService()
        result = ai_service.generate_requirements(
            solution_description=description,
            capabilities=capabilities,
            solution_type=solution_type,
            business_domain=business_domain,
        )

        # Persist reasoning state when solution_id is provided
        reasoning_state_id = None
        if solution_id:
            try:
                from app.models.solution_reasoning import SolutionAIReasoningState
                reqs = result.get("requirements") or result.get("suggestions") or []
                state = SolutionAIReasoningState(
                    solution_id=int(solution_id),
                    adm_phase="B",
                    context_snapshot={
                        "solution_type": solution_type,
                        "business_domain": business_domain,
                        "capabilities_count": len(capabilities),
                        "llm_provider": "LLM" if result.get("source") == "ai" else "fallback",
                        "entities_created": {},
                    },
                    suggestions={"requirements": reqs},
                    confidence_score_pct=0.75 if result.get("source") == "ai" else 0.5,
                    data_sources_used={
                        "solution_description": {"source": "request.description", "weight": 0.6},
                        "solution_metadata": {"source": "solution_type + business_domain", "weight": 0.4},
                    },
                    recommendation_reasoning={
                        "methodology": "Requirements generation from solution description",
                        "adm_phase": "B",
                        "total_generated": len(reqs),
                    },
                    model_assumptions={
                        "business_assumptions": ["Description captures all key needs", "Standard requirement types applicable"],
                    },
                )
                db.session.add(state)
                db.session.commit()
                reasoning_state_id = state.id
            except Exception as rs_err:
                db.session.rollback()
                logger.warning(f"Could not save requirements reasoning state: {rs_err}")

        response = dict(result)
        response["reasoning_state_id"] = reasoning_state_id
        return api_success(data=response)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error generating requirements: {e}")
        return api_error("An internal error occurred", 500)


@solution_design_bp.route("/<int:solution_id>/ai-suggestion-feedback", methods=["POST"])
@login_required
def ai_suggestion_feedback(solution_id: int):
    """Record user accept/reject feedback on an AI suggestion batch.

    Updates the SolutionAIReasoningState record with which suggestions were
    accepted, how many entities were created, and the overall feedback action.
    """
    from app.models.solution_reasoning import SolutionAIReasoningState

    data = request.get_json(silent=True) or {}
    reasoning_state_id = data.get("reasoning_state_id")
    action = data.get("action")  # 'accept' | 'reject'
    suggestion_id = data.get("suggestion_id")
    entity_type = data.get("entity_type", "unknown")

    if not reasoning_state_id or action not in ("accept", "reject"):
        return api_error("reasoning_state_id and action are required", 400)

    try:
        state = SolutionAIReasoningState.query.filter_by(
            id=int(reasoning_state_id), solution_id=solution_id
        ).first()
        if not state:
            return api_error("Reasoning state not found", 404)

        state.user_feedback = action
        if suggestion_id:
            state.selected_suggestion_id = str(suggestion_id)

        if action == "accept":
            ctx = dict(state.context_snapshot or {})
            counts = dict(ctx.get("entities_created") or {})
            counts[entity_type] = counts.get(entity_type, 0) + 1
            ctx["entities_created"] = counts
            state.context_snapshot = ctx

        db.session.commit()
        return api_success(data={"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error recording AI suggestion feedback: {e}")
        return api_error("Internal error", 500)


@solution_design_bp.route("/ai-generate-roadmap", methods=["POST"])
@login_required
@audit_log("ai_generate_roadmap")
def ai_generate_roadmap():
    """Use AI to generate implementation roadmap based on solution."""
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_service import (
            SolutionAIService,
        )

        data = request.get_json()
        description = data.get("description", "")
        solution_type = data.get("solution_type")
        capabilities = data.get("capabilities", [])
        archimate_elements = data.get("archimate_elements", {})

        if not description:
            return api_error("Description is required", 400)

        ai_service = SolutionAIService()
        result = ai_service.generate_roadmap_items(
            solution_description=description,
            capabilities=capabilities,
            solution_type=solution_type,
            archimate_elements=archimate_elements,
        )

        return api_success(data=result)
    except Exception as e:
        logger.error(f"Error generating roadmap: {e}")
        return api_error("An internal error occurred", 500)


@solution_design_bp.route("/ai-suggest-archimate", methods=["POST"])
@login_required
@audit_log("ai_suggest_archimate")
def ai_suggest_archimate_elements():
    """Use AI to suggest ArchiMate elements based on solution description.

    This endpoint is called during solution creation BEFORE a solution_id exists.
    """
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_service import (
            SolutionAIService,
        )

        data = request.get_json()
        description = data.get("description", "")
        solution_type = data.get("solution_type")
        business_domain = data.get("business_domain")
        capabilities = data.get("capabilities", [])

        if not description:
            return api_error("Description is required", 400)

        ai_service = SolutionAIService()
        result = ai_service.suggest_elements(
            solution_description=description,
            capabilities=capabilities,
            solution_type=solution_type,
            business_domain=business_domain,
        )

        # Transform suggestions into the format expected by the frontend
        suggestions = result.get("suggestions", {})
        archimate_elements = {}

        for layer, elements in suggestions.items():
            layer_key = layer.lower()
            archimate_elements[layer_key] = []
            for i, elem in enumerate(elements or []):
                archimate_elements[layer_key].append(
                    {
                        "element_id": f"ai-{layer_key}-{i + 1}",
                        "element_table": elem.get(
                            "element_table", elem.get("element_type", "Element")
                        ),
                        "element_type": elem.get("element_type", "Element"),
                        "name": elem.get("name", ""),
                        "description": elem.get("description", ""),
                        "color": {
                            "motivation": "#B3A2C7",
                            "strategy": "#F5D742",
                            "business": "#FFFFB5",
                            "application": "#B5E3FF",
                            "technology": "#C9E6B5",
                            "implementation": "#FFB5B5",
                        }.get(layer_key, "#CCCCCC"),
                        "source": result.get("source", "ai_generated"),
                    }
                )

        return api_success(data=
            {
                "archimate_elements": archimate_elements,
                "source": result.get("source", "ai_generated"),
                "total_elements": sum(len(v) for v in archimate_elements.values()),
            }
        )
    except Exception as e:
        logger.error(f"Error suggesting ArchiMate elements: {e}")
        return api_error("An internal error occurred", 500)


@solution_design_bp.route("/<int:solution_id>/generate-draft", methods=["POST"])
@login_required
def generate_draft_architecture(solution_id):
    """Generate a full TOGAF draft architecture from an architect's brief using LLM."""
    solution = Solution.query.get_or_404(solution_id)
    brief = request.get_json() or {}
    if not brief.get("problem_statement", "").strip():
        return api_error("problem_statement is required", 400)
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
        result = SolutionAIOrchestrator().generate_draft_architecture(
            solution_id=solution_id,
            brief=brief,
            user_id=current_user.id,
        )
        if result.get("success"):
            tag_ai_action("generate_draft", "solution", solution_id)
            if solution.created_by_id:
                _create_notification(
                    solution.created_by_id,
                    "ai_generation",
                    f"AI draft generated for solution '{solution.name}'.",
                    solution_id=solution.id,
                )
            db.session.commit()
        status_code = 200 if result.get("success") else 500
        return jsonify(result), status_code
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in generate_draft_architecture route: {e}", exc_info=True)
        return api_error("Operation failed", 500)


@solution_design_bp.route("/<int:solution_id>/api/blueprint/<section_id>/generate-elements", methods=["POST"])
@login_required
def generate_blueprint_section(solution_id, section_id):
    """Generate ArchiMate elements for a specific blueprint viewpoint section.

    Maps section_id → orchestrator method:
      - vision_motivation, value_stream_map           → generate_strategy_layer
      - application_cooperation, data_information      → generate_architecture_layers
      - deployment_view, network_communication         → generate_architecture_layers (tech layer)
      - transition_roadmap, work_packages, gap_analysis → generate_implementation_layer
      - all others                                     → generate_draft_architecture (full)
    Returns {success, elements, narrative, created}.
    """
    _STRATEGY_SECTIONS = {"vision_motivation", "value_stream_map"}
    _ARCH_SECTIONS = {"application_cooperation", "data_information",
                      "deployment_view", "network_communication"}
    _IMPL_SECTIONS = {"transition_roadmap", "work_packages", "gap_analysis"}

    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator

        orch = SolutionAIOrchestrator()
        if section_id in _STRATEGY_SECTIONS:
            result = orch.generate_strategy_layer(solution_id)
        elif section_id in _ARCH_SECTIONS:
            result = orch.generate_architecture_layers(solution_id)
        elif section_id in _IMPL_SECTIONS:
            result = orch.generate_implementation_layer(solution_id)
        else:
            # Fallback: generate everything
            solution = Solution.query.get_or_404(solution_id)
            brief = {"problem_statement": solution.description or solution.name or ""}
            result = orch.generate_draft_architecture(
                solution_id=solution_id, brief=brief, user_id=current_user.id
            )

        # Fetch updated elements for this section so the UI can refresh without reload
        from app.modules.solutions_strategic.v2.routes.solution_design_routes import _viewpoint_elements_for_section
        elements = []
        try:
            elements = _viewpoint_elements_for_section(solution_id, section_id)
        except Exception as exc:
            logger.debug("suppressed error in generate_blueprint_section (app/modules/solutions_strategic/v2/routes/solution_ai_routes.py): %s", exc)

        tag_ai_action("generate_section", "solution", solution_id)
        db.session.commit()
        return jsonify({
            "success": True,
            "section_id": section_id,
            "elements": elements,
            "created": result.get("created", {}),
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in generate_blueprint_section {section_id}: {e}", exc_info=True)
        return api_error("Section generation failed", 500)


@solution_design_bp.route("/<int:solution_id>/generate-strategy", methods=["POST"])
@login_required
@audit_log("generate_strategy_layer")
def generate_strategy_layer_route(solution_id):
    """Generate Strategy Layer entities (CoA, ValueStream, gap analysis) from capabilities."""
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
        data = request.get_json() or {}
        capability_ids = data.get('capability_ids')

        orch = SolutionAIOrchestrator()
        result = orch.generate_strategy_layer(solution_id, capability_ids)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in generate_strategy_layer: {e}", exc_info=True)
        return api_error("Strategy generation failed", 500)


@solution_design_bp.route("/<int:solution_id>/generate-architecture-layers", methods=["POST"])
@login_required
@audit_log("generate_architecture_layers")
def generate_architecture_layers_route(solution_id):
    """Generate Business + Application + Technology layers from capabilities."""
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
        data = request.get_json() or {}
        capability_ids = data.get('capability_ids')

        orch = SolutionAIOrchestrator()
        result = orch.generate_architecture_layers(solution_id, capability_ids)

        # Persist capability→element traceability links
        capabilities = data.get('capabilities', [])
        if capabilities and result.get("success") is not False:
            from app.models.traceability import TraceabilityLink
            TraceabilityLink.query.filter_by(
                solution_id=solution_id,
                traceability_layer="capability_to_element",
                traceability_type="derivation",
            ).delete()
            db.session.commit()

            valid_cap_ids = {c.get("prefixed_id", "") for c in capabilities if c.get("prefixed_id")}

            # Extract elements from orchestrator result
            elements = []
            if isinstance(result, dict):
                for layer_name in ["business", "application", "technology", "strategy"]:
                    layer_elems = result.get(layer_name, [])
                    if not isinstance(layer_elems, list):
                        layer_elems = result.get("elements_by_layer", {}).get(layer_name, [])
                    if isinstance(layer_elems, list):
                        elements.extend(layer_elems)

            if valid_cap_ids and elements:
                # The orchestrator uses capability_name (string) for traceability.
                # Convert to prefixed IDs so persist_traceability_links can use them.
                match_capability_names_to_prefixed_ids(elements, "capability_name", capabilities)
                persist_traceability_links(
                    solution_id=solution_id,
                    items=elements,
                    ref_field="driven_by",
                    item_id_field="id",
                    item_type_field="type",
                    traceability_layer="capability_to_element",
                    valid_source_ids=valid_cap_ids,
                )

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in generate_architecture_layers: {e}", exc_info=True)
        return api_error("Architecture generation failed", 500)


@solution_design_bp.route("/<int:solution_id>/generate-implementation", methods=["POST"])
@login_required
@audit_log("generate_implementation_layer")
def generate_implementation_layer_route(solution_id):
    """Generate Implementation Layer: work packages, gaps, plateaus, Kanban cards, Gantt items."""
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
        data = request.get_json() or {}
        capability_ids = data.get('capability_ids')

        architecture_elements = data.get('architecture_elements', [])

        orch = SolutionAIOrchestrator()
        result = orch.generate_implementation_layer(solution_id, capability_ids, architecture_elements=architecture_elements)

        # Persist element→implementation traceability links
        if architecture_elements and result.get("success") is not False:
            from app.models.traceability import TraceabilityLink
            TraceabilityLink.query.filter_by(
                solution_id=solution_id,
                traceability_layer="element_to_implementation",
                traceability_type="derivation",
            ).delete()
            db.session.commit()

            valid_elem_ids = {e.get("prefixed_id", "") for e in architecture_elements if e.get("prefixed_id")}

            impl_elements = []
            if isinstance(result, dict):
                for layer_name in ["implementation", "strategy"]:
                    layer_elems = result.get(layer_name, [])
                    if not isinstance(layer_elems, list):
                        layer_elems = result.get("elements_by_layer", {}).get(layer_name, [])
                    if isinstance(layer_elems, list):
                        impl_elements.extend(layer_elems)

            if valid_elem_ids and impl_elements:
                # The LLM now returns 'addresses' with prefixed element IDs.
                # Fallback: if LLM didn't return addresses, try name matching.
                elem_name_to_pid = {}
                for ae in architecture_elements:
                    if ae.get("name") and ae.get("prefixed_id"):
                        elem_name_to_pid[ae["name"].lower().strip()] = ae["prefixed_id"]

                for item in impl_elements:
                    if not item.get("addresses"):
                        # Fallback: match element names in capability_name/gap_name fields
                        matched_pids = []
                        for field in ("gap_name", "capability_name", "name"):
                            val = item.get(field, "")
                            if isinstance(val, str):
                                for ename, epid in elem_name_to_pid.items():
                                    if ename in val.lower():
                                        matched_pids.append(epid)
                        if matched_pids:
                            item["addresses"] = list(set(matched_pids))

                persist_traceability_links(
                    solution_id=solution_id,
                    items=impl_elements,
                    ref_field="addresses",
                    item_id_field="id",
                    item_type_field="type",
                    traceability_layer="element_to_implementation",
                    valid_source_ids=valid_elem_ids,
                )

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in generate_implementation_layer: {e}", exc_info=True)
        return api_error("Implementation generation failed", 500)


@solution_design_bp.route("/<int:solution_id>/generate-variants", methods=["POST"])
@login_required
def generate_architecture_variants_route(solution_id):
    """Generate 3 architecture variants (cost/timeline/risk) and store as SolutionRecommendations."""
    Solution.query.get_or_404(solution_id)
    brief = request.get_json() or {}
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
        result = SolutionAIOrchestrator().generate_architecture_variants(
            solution_id=solution_id,
            brief=brief,
            user_id=current_user.id,
        )
        status_code = 200 if result.get("success") else 500
        return jsonify(result), status_code
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in generate_architecture_variants route: {e}", exc_info=True)
        return api_error("Operation failed", 500)


@solution_design_bp.route("/<int:solution_id>/apply-variant/<int:recommendation_id>", methods=["POST"])
@login_required
def apply_architecture_variant_route(solution_id, recommendation_id):
    """Apply a stored architecture variant by creating entities from its payload."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
        result = SolutionAIOrchestrator().apply_architecture_variant(
            solution_id=solution_id,
            recommendation_id=recommendation_id,
            user_id=current_user.id,
        )
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in apply_architecture_variant route: {e}", exc_info=True)
        return api_error("Operation failed", 500)


# ENT-018: Live impact recalculation (no LLM — pure DB + arithmetic)
@solution_design_bp.route("/<int:solution_id>/recalculate-impact", methods=["POST"])
@login_required
def recalculate_impact(solution_id: int):
    """Return updated maturity %, risk summary, entity counts, and next milestone."""
    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not is_active_org_admin():
        return api_error("Forbidden", 403)
    try:
        from app.models.solution_lifecycle_models import SolutionRisk, SolutionMetric, SolutionPlateau
        from app.models.solution_architect_models import SolutionDriver, SolutionGoal, SolutionConstraint, SolutionRequirement
        from app.models.solution_architect_models import SolutionRecommendation
        from app.models.solution_architect_models import SolutionAnalysisSession
        sid = solution_id
        drivers_n = SolutionDriver.query.filter_by(problem_id=None).count()
        # Get counts via problem_def chain
        prob_defs = []
        sessions = SolutionAnalysisSession.query.filter_by(solution_id=sid).all()
        for s in sessions:
            from app.models.solution_architect_models import SolutionProblemDefinition
            pds = SolutionProblemDefinition.query.filter_by(session_id=s.id).all()
            prob_defs.extend(pds)
        pd_ids = [p.id for p in prob_defs]
        drivers_n = SolutionDriver.query.filter(SolutionDriver.problem_id.in_(pd_ids)).count() if pd_ids else 0
        goals_n = SolutionGoal.query.filter(SolutionGoal.problem_id.in_(pd_ids)).count() if pd_ids else 0
        constraints_n = SolutionConstraint.query.filter(SolutionConstraint.problem_id.in_(pd_ids)).count() if pd_ids else 0
        requirements_n = SolutionRequirement.query.filter(SolutionRequirement.problem_id.in_(pd_ids)).count() if pd_ids else 0
        session_ids = [s.id for s in sessions]
        options_n = SolutionRecommendation.query.filter(SolutionRecommendation.session_id.in_(session_ids)).count() if session_ids else 0
        risks = SolutionRisk.query.filter_by(solution_id=sid).all()
        metrics_n = SolutionMetric.query.filter_by(solution_id=sid).count()
        plateaus_n = SolutionPlateau.query.filter_by(solution_id=sid).count()
        # Maturity score (same weights as view_solution)
        weights = {"drivers": 8, "goals": 7, "constraints": 5, "requirements": 10, "risks": 7,
                   "options": 10, "plateaus": 5, "metrics": 5}
        total_w = sum(weights.values())
        earned = sum(w for k, w in weights.items() if locals().get(f"{k}_n", 0) > 0)
        maturity_pct = round(earned / total_w * 100) if total_w else 0
        # Risk summary
        risk_summary = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for r in risks:
            lvl = (r.impact or "medium").lower()
            if lvl in risk_summary:
                risk_summary[lvl] += 1
        # Next milestone text
        gaps = [k for k, n in [("drivers", drivers_n), ("goals", goals_n), ("constraints", constraints_n),
                                 ("requirements", requirements_n), ("risks", len(risks)),
                                 ("options", options_n), ("plateaus", plateaus_n), ("metrics", metrics_n)] if n == 0]
        next_milestone = f"Add {gaps[0]} to progress" if gaps else "All phases populated"
        return api_success(data={
            "maturity_percentage": maturity_pct,
            "risk_summary": risk_summary,
            "entity_counts": {
                "drivers": drivers_n, "goals": goals_n, "constraints": constraints_n,
                "requirements": requirements_n, "risks": len(risks), "options": options_n,
                "plateaus": plateaus_n, "metrics": metrics_n,
            },
            "next_milestone": next_milestone,
        })
    except Exception as e:
        logger.error(f"recalculate_impact error: {e}", exc_info=True)
        return api_error("Operation failed", 500)


# =============================================================================
# ENT-074: AI-generated ArchiMate suggestions — accept/reject into solution
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/ai/suggest-archimate", methods=["POST"])
@login_required
@audit_log("ai_suggest_archimate_for_solution")
def suggest_archimate_for_solution(solution_id: int):
    """Trigger AI to generate ArchiMate element suggestions for a solution.

    Uses the AI orchestrator to analyse the solution description, drivers,
    goals, and business domain to produce per-layer ArchiMate element
    suggestions with confidence scores and rationale.
    """
    solution = Solution.query.get_or_404(solution_id)

    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
            SolutionAIOrchestrator,
        )

        orchestrator = SolutionAIOrchestrator()
        archimate_suggestions = orchestrator._get_archimate_suggestions(solution)

        # Build structured suggestion list with unique IDs for the UI
        suggestions = []
        for idx, elem in enumerate(archimate_suggestions or []):
            suggestions.append({
                "suggestion_id": f"ai-arch-{solution_id}-{idx}",
                "name": elem.get("name", ""),
                "type": elem.get("type") or elem.get("element_type", "Element"),
                "layer": elem.get("layer", "application"),
                "description": elem.get("description", ""),
                "rationale": elem.get("rationale", "Suggested based on solution analysis"),
                "confidence": elem.get("confidence", 0.7),
            })

        # Persist reasoning state
        reasoning_state_id = None
        try:
            from app.models.solution_reasoning import SolutionAIReasoningState

            state = SolutionAIReasoningState(
                solution_id=solution_id,
                adm_phase=solution.adm_phase or "A",
                context_snapshot={
                    "solution_name": solution.name,
                    "solution_type": solution.solution_type,
                    "business_domain": solution.business_domain,
                    "trigger": "suggest_archimate_for_solution",
                },
                suggestions={"archimate_elements": suggestions},
                confidence_score_pct=0.75,
                data_sources_used={
                    "solution_description": {"source": "solution.description", "weight": 0.5},
                    "business_domain": {"source": "solution.business_domain", "weight": 0.3},
                    "solution_type": {"source": "solution.solution_type", "weight": 0.2},
                },
                recommendation_reasoning={
                    "methodology": "ArchiMate 3.2 layer analysis via AI orchestrator",
                    "total_suggestions": len(suggestions),
                },
            )
            db.session.add(state)
            db.session.commit()
            reasoning_state_id = state.id
        except Exception as rs_err:
            db.session.rollback()
            logger.warning("Could not save reasoning state for suggest-archimate: %s", rs_err)

        return api_success(data={
            "suggestions": suggestions,
            "total": len(suggestions),
            "reasoning_state_id": reasoning_state_id,
        })
    except Exception as e:
        db.session.rollback()
        logger.error("Error generating ArchiMate suggestions for solution %s: %s", solution_id, e)
        return api_error("An internal error occurred", 500)


@solution_design_bp.route("/<int:solution_id>/ai/accept-suggestion", methods=["POST"])
@login_required
@audit_log("ai_accept_archimate_suggestion")
def accept_archimate_suggestion(solution_id: int):
    """Accept a single AI-suggested ArchiMate element.

    Creates the ArchiMate element in the DB (or finds existing match)
    and links it to the solution via SolutionArchiMateElement with
    element_role='ai_derived'.
    """
    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    element_type = (data.get("type") or data.get("element_type") or "Element").strip()
    layer = (data.get("layer") or "application").strip()
    description = (data.get("description") or "").strip()
    suggestion_id = data.get("suggestion_id")

    if not name:
        return api_error("Element name is required", 400)

    try:
        from app.models.models import ArchiMateElement
        from app.models.solution_archimate_element import SolutionArchiMateElement

        # Find or create the ArchiMate element
        existing = ArchiMateElement.query.filter_by(name=name, type=element_type, layer=layer).first()
        if existing:
            element = existing
        else:
            element = ArchiMateElement(
                name=name,
                type=element_type,
                layer=layer,
                description=description,
                scope="application",
            )
            db.session.add(element)
            db.session.flush()

        # Link to solution (skip if already linked)
        existing_link = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id, element_id=element.id
        ).first()
        if existing_link:
            return api_success(data={
                "element_id": element.id,
                "link_id": existing_link.id,
                "already_linked": True,
                "message": "Element already linked to solution",
                "completeness_score": _compute_architecture_completeness(solution_id),
            })

        link = SolutionArchiMateElement(
            solution_id=solution_id,
            element_id=element.id,
            element_role="ai_derived",
        )
        db.session.add(link)
        db.session.flush()

        # CAP-010: Set AI-generated notes marker via the polymorphic model
        try:
            from app.models.solution_models import SolutionArchiMateElement as SAMEPoly
            db.session.execute(  # tenant-filtered: scoped via parent FK (link.id)
                SAMEPoly.__table__.update()
                .where(SAMEPoly.__table__.c.id == link.id)
                .values(notes="AI-generated from capability-driven design")
            )
        except Exception as notes_err:
            logger.debug("Could not set notes on link %s: %s", link.id, notes_err)

        # CAP-010: Auto-link to other junction tables based on element type
        auto_linked = {}
        if element_type in ("ApplicationComponent", "ApplicationService"):
            try:
                # Check if a matching ApplicationComponent record exists by name
                match = ApplicationComponent.query.filter(
                    ApplicationComponent.name.ilike(name)
                ).first()
                if match:
                    tbl = db.metadata.tables.get("solution_applications")
                    if tbl is not None:
                        existing_app_link = db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                            tbl.select().where(
                                tbl.c.solution_id == solution_id
                            ).where(
                                tbl.c.application_component_id == match.id
                            )
                        ).first()
                        if not existing_app_link:
                            db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
                                tbl.insert().values(
                                    solution_id=solution_id,
                                    application_component_id=match.id,
                                    role="supporting",
                                )
                            )
                            auto_linked["application"] = {
                                "id": match.id,
                                "name": match.name,
                            }
            except Exception as app_err:
                logger.debug("CAP-010 auto-link application failed: %s", app_err)

        elif element_type == "Requirement":
            try:
                from app.models.solution_architect_models import SolutionRequirement
                # Create a SolutionRequirement linked to this solution
                req = SolutionRequirement(
                    solution_id=solution_id,
                    name=name,
                    description=description or f"Auto-generated requirement: {name}",
                    source="AI-generated from capability-driven design",
                    status="open",
                )
                db.session.add(req)
                sync_archimate_element(req)
                db.session.flush()
                auto_linked["requirement"] = {"id": req.id, "name": req.name}
            except Exception as req_err:
                logger.debug("CAP-010 auto-link requirement failed: %s", req_err)

        db.session.commit()

        # CAP-010: Compute architecture completeness score
        completeness_score = _compute_architecture_completeness(solution_id)

        # Record feedback on reasoning state if suggestion_id provided
        if suggestion_id and data.get("reasoning_state_id"):
            try:
                from app.models.solution_reasoning import SolutionAIReasoningState

                state = SolutionAIReasoningState.query.filter_by(
                    id=int(data["reasoning_state_id"]), solution_id=solution_id
                ).first()
                if state:
                    ctx = dict(state.context_snapshot or {})
                    accepted = ctx.get("accepted_suggestions", [])
                    accepted.append(suggestion_id)
                    ctx["accepted_suggestions"] = accepted
                    counts = dict(ctx.get("entities_created") or {})
                    counts["archimate_elements"] = counts.get("archimate_elements", 0) + 1
                    ctx["entities_created"] = counts
                    state.context_snapshot = ctx
                    db.session.commit()
            except Exception as fb_err:
                db.session.rollback()
                logger.debug("Could not record accept feedback: %s", fb_err)

        # --- Chain completion via inference engine (bidirectional) ---
        chain_result = None
        try:
            from app.modules.architecture_assistant.journey_graph import JourneyGraph
            jg = JourneyGraph.resume_for_solution(solution_id)

            # Scope element to the architecture graph
            element.architecture_id = jg.architecture_id
            db.session.flush()

            # Run bidirectional 3-pass inference (upstream + downstream)
            result = jg.engine.generate_chain(element.id, direction="both")

            # Link ALL engine-created elements (upstream AND downstream) to solution
            linked_ids = set()
            chain_elements_linked = 0
            for created_el_id in result.elements_created:
                el_id = created_el_id if isinstance(created_el_id, int) else getattr(created_el_id, "id", None)
                if el_id and el_id not in linked_ids:
                    linked_ids.add(el_id)
                    existing_link = SolutionArchiMateElement.query.filter_by(
                        solution_id=solution_id, element_id=el_id
                    ).first()
                    if not existing_link:
                        db.session.add(SolutionArchiMateElement(
                            solution_id=solution_id,
                            element_id=el_id,
                            element_role="inferred",
                        ))
                        chain_elements_linked += 1
            db.session.commit()

            chain_result = {
                "nodes_created": len(result.elements_created),
                "relationships_created": len(result.relationships_created),
                "elements_linked": chain_elements_linked,
                "completeness": result.completeness_score,
            }
        except Exception as e:
            logger.warning("Chain completion failed for element %d: %s", element.id, e)
            # Persist architecture_id even if chain failed (self-healing)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

        return api_success(data={
            "element_id": element.id,
            "link_id": link.id,
            "already_linked": False,
            "message": f"Element '{name}' accepted and linked to solution",
            "auto_linked": auto_linked,
            "completeness_score": completeness_score,
            "chain_result": chain_result,
        })
    except Exception as e:
        db.session.rollback()
        logger.error("Error accepting ArchiMate suggestion for solution %s: %s", solution_id, e)
        return api_error("An internal error occurred", 500)


@solution_design_bp.route("/<int:solution_id>/ai/reject-suggestion", methods=["POST"])
@login_required
@audit_log("ai_reject_archimate_suggestion")
def reject_archimate_suggestion(solution_id: int):
    """Reject/dismiss an AI-suggested ArchiMate element.

    Logs the rejection for learning but does not persist the element.
    """
    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}

    suggestion_id = data.get("suggestion_id")
    reason = (data.get("reason") or "").strip()

    if not suggestion_id:
        return api_error("suggestion_id is required", 400)

    try:
        reasoning_state_id = data.get("reasoning_state_id")
        if reasoning_state_id:
            from app.models.solution_reasoning import SolutionAIReasoningState

            state = SolutionAIReasoningState.query.filter_by(
                id=int(reasoning_state_id), solution_id=solution_id
            ).first()
            if state:
                ctx = dict(state.context_snapshot or {})
                rejected = ctx.get("rejected_suggestions", [])
                rejected.append({"suggestion_id": suggestion_id, "reason": reason})
                ctx["rejected_suggestions"] = rejected
                state.context_snapshot = ctx
                db.session.commit()

        logger.info(
            "AI ArchiMate suggestion rejected: solution=%s, suggestion=%s, reason=%s",
            solution_id,
            suggestion_id,
            reason,
        )

        return api_success(data={"success": True, "message": "Suggestion rejected"})
    except Exception as e:
        db.session.rollback()
        logger.error("Error rejecting ArchiMate suggestion for solution %s: %s", solution_id, e)
        return api_error("An internal error occurred", 500)


@solution_design_bp.route("/<int:solution_id>/ai/bulk-accept", methods=["POST"])
@login_required
@audit_log("ai_bulk_accept_archimate")
def bulk_accept_archimate_suggestions(solution_id: int):
    """Accept multiple AI-suggested ArchiMate elements at once.

    Body: {"suggestions": [{"name":"...", "type":"...", "layer":"...", ...}, ...]}
    """
    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}
    suggestions = data.get("suggestions", [])

    if not suggestions:
        return api_error("No suggestions provided", 400)

    try:
        from app.models.models import ArchiMateElement
        from app.models.solution_archimate_element import SolutionArchiMateElement

        accepted = []
        skipped = []

        for item in suggestions:
            name = (item.get("name") or "").strip()
            element_type = (item.get("type") or item.get("element_type") or "Element").strip()
            layer = (item.get("layer") or "application").strip()
            description = (item.get("description") or "").strip()

            if not name:
                skipped.append({"suggestion_id": item.get("suggestion_id"), "reason": "Missing name"})
                continue

            # Find or create
            existing = ArchiMateElement.query.filter_by(name=name, type=element_type, layer=layer).first()
            if existing:
                element = existing
            else:
                element = ArchiMateElement(
                    name=name,
                    type=element_type,
                    layer=layer,
                    description=description,
                    scope="application",
                )
                db.session.add(element)
                db.session.flush()

            # Link if not already linked
            existing_link = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id, element_id=element.id
            ).first()
            if existing_link:
                skipped.append({
                    "suggestion_id": item.get("suggestion_id"),
                    "element_id": element.id,
                    "reason": "Already linked",
                })
                continue

            link = SolutionArchiMateElement(
                solution_id=solution_id,
                element_id=element.id,
                element_role="ai_derived",
            )
            db.session.add(link)
            accepted.append({
                "suggestion_id": item.get("suggestion_id"),
                "element_id": element.id,
                "name": name,
                "element": element,
            })

        db.session.commit()

        # Update reasoning state
        reasoning_state_id = data.get("reasoning_state_id")
        if reasoning_state_id:
            try:
                from app.models.solution_reasoning import SolutionAIReasoningState

                state = SolutionAIReasoningState.query.filter_by(
                    id=int(reasoning_state_id), solution_id=solution_id
                ).first()
                if state:
                    ctx = dict(state.context_snapshot or {})
                    prev_accepted = ctx.get("accepted_suggestions", [])
                    prev_accepted.extend([a["suggestion_id"] for a in accepted if a.get("suggestion_id")])
                    ctx["accepted_suggestions"] = prev_accepted
                    counts = dict(ctx.get("entities_created") or {})
                    counts["archimate_elements"] = counts.get("archimate_elements", 0) + len(accepted)
                    ctx["entities_created"] = counts
                    state.context_snapshot = ctx
                    state.user_feedback = "accept"
                    db.session.commit()
            except Exception as fb_err:
                db.session.rollback()
                logger.debug("Could not record bulk-accept feedback: %s", fb_err)

        # --- Bulk chain completion via inference engine ---
        chain_result = None
        try:
            from app.modules.architecture_assistant.journey_graph import JourneyGraph
            jg = JourneyGraph.resume_for_solution(solution_id)

            # Scope all accepted elements to the architecture graph
            for item in accepted:
                el = item.get("element")
                if el:
                    el.architecture_id = jg.architecture_id
            db.session.flush()

            # Skip Pass 3 (semantic refinement) for bulk — structure is enough
            jg.engine.context.skip_semantic_pass = True

            total_nodes = 0
            total_rels = 0
            total_linked = 0
            linked_ids = set()  # Dedup: same element inferred from multiple roots

            for item in accepted:
                el = item.get("element")
                if not el:
                    continue
                try:
                    # Track accumulator position to get per-element delta
                    prev_el_count = len(jg.engine.context.elements_created)
                    prev_rel_count = len(jg.engine.context.relationships_created)

                    jg.engine.generate_chain(el.id, direction="both")

                    # Count only this element's new creations
                    new_el_ids = jg.engine.context.elements_created[prev_el_count:]
                    new_rels = jg.engine.context.relationships_created[prev_rel_count:]
                    total_nodes += len(new_el_ids)
                    total_rels += len(new_rels)

                    for created_el_id in new_el_ids:
                        el_id = created_el_id if isinstance(created_el_id, int) else getattr(created_el_id, "id", None)
                        if el_id and el_id not in linked_ids:
                            linked_ids.add(el_id)
                            existing_link = SolutionArchiMateElement.query.filter_by(
                                solution_id=solution_id, element_id=el_id
                            ).first()
                            if not existing_link:
                                db.session.add(SolutionArchiMateElement(
                                    solution_id=solution_id,
                                    element_id=el_id,
                                    element_role="inferred",
                                ))
                                total_linked += 1
                except Exception as e:
                    logger.warning("Chain failed for element %d: %s", el.id, e)

            db.session.commit()
            chain_result = {
                "nodes_created": total_nodes,
                "relationships_created": total_rels,
                "elements_linked": total_linked,
            }
        except Exception as e:
            logger.warning("Bulk chain completion failed: %s", e)

        # Strip element objects before returning (not JSON-serializable)
        for item in accepted:
            item.pop("element", None)

        return api_success(data={
            "accepted": accepted,
            "skipped": skipped,
            "accepted_count": len(accepted),
            "skipped_count": len(skipped),
            "chain_result": chain_result,
        })
    except Exception as e:
        db.session.rollback()
        logger.error("Error in bulk-accept for solution %s: %s", solution_id, e)
        return api_error("An internal error occurred", 500)


@solution_design_bp.route("/<int:solution_id>/ai/inference-preview", methods=["GET"])
@login_required
def inference_preview(solution_id):
    """Show what the engine would infer from accepting an element of this type.

    Returns both upstream and downstream canonical chain expectations.
    Non-mutating — reads rules only, touches no DB.
    """
    element_type = request.args.get("element_type", "")
    if not element_type:
        return api_error("element_type is required", 400)

    from app.modules.architecture.services.inference_rules_registry import InferenceRulesRegistry
    registry = InferenceRulesRegistry()

    # Walk the full downstream chain recursively
    downstream = []
    visited = {element_type}
    queue = [element_type]
    while queue:
        current_type = queue.pop(0)
        for target_type, rel_type, meta in registry.expected_downstream(current_type):
            if target_type not in visited:
                visited.add(target_type)
                downstream.append({
                    "type": target_type,
                    "rel_type": rel_type,
                    "required": meta.get("required", False),
                    "direction": "downstream",
                })
                queue.append(target_type)

    # Walk the upstream chain recursively
    upstream = []
    visited_up = {element_type}
    queue_up = [element_type]
    while queue_up:
        current_type = queue_up.pop(0)
        for parent_type in registry.allowed_upstream_types(current_type):
            if parent_type not in visited_up:
                visited_up.add(parent_type)
                rel_type = registry.canonical_rel_type(parent_type, current_type)
                upstream.append({
                    "type": parent_type,
                    "rel_type": rel_type,
                    "required": True,
                    "direction": "upstream",
                })
                queue_up.append(parent_type)

    all_chain = upstream + downstream
    return api_success(data={
        "element_type": element_type,
        "upstream_chain": upstream,
        "downstream_chain": downstream,
        "total_required": sum(1 for c in all_chain if c["required"]),
        "total_optional": sum(1 for c in all_chain if not c["required"]),
    })


@solution_design_bp.route("/<int:solution_id>/traceability", methods=["GET"])
@login_required
def get_solution_traceability(solution_id):
    """Get the full traceability chain for a solution."""
    try:
        result = build_traceability(solution_id)
        return api_success(data=result)
    except Exception as e:
        logger.error(f"Error building traceability: {e}", exc_info=True)
        return api_error("Failed to build traceability chain", 500)

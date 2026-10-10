"""
Solution AI Prompt Admin — allows admins to customize LLM prompts
used by the Architecture Journey.

Registered as a separate blueprint to avoid being overwritten
by deploys to admin_routes.py.
"""

import difflib
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from app.decorators import admin_required, audit_log
from app.extensions import db
from app.middleware.tenant_decorators import platform_admin_required
from app.models.ai_service import AIPromptTemplate, AIPromptTemplateVersion
from app.services import solution_prompt_override_service

logger = logging.getLogger(__name__)

solution_prompt_admin_bp = Blueprint(
    "solution_prompt_admin", __name__, url_prefix="/admin"
)

_SOLUTION_PROMPT_DEFAULTS = None


def _get_cap_suggestion_default():
    try:
        from app.modules.ai_chat.services.solution_ai_service import SolutionAIService
        return SolutionAIService.CAPABILITY_SUGGESTION_PROMPT
    except Exception:
        return "(Could not load default prompt)"


def _get_prompt_defaults():
    """Lazy-load default prompts from orchestrator class attributes."""
    global _SOLUTION_PROMPT_DEFAULTS
    if _SOLUTION_PROMPT_DEFAULTS is not None:
        return _SOLUTION_PROMPT_DEFAULTS

    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
        SolutionAIOrchestrator,
    )

    _SOLUTION_PROMPT_DEFAULTS = {
        "draft_architecture": {
            "name": "Draft Architecture (Phase A)",
            "description": "Generates the complete TOGAF ADM Motivation Layer with all ArchiMate 3.2 element types.",
            "prompt_text": SolutionAIOrchestrator.DRAFT_ARCHITECTURE_PROMPT,
            "category": "Phase A",
            "variables": "solution_name, solution_type, business_domain, complexity, adm_phase, problem_statement, current_state, budget_range, timeline_months, compliance_needs, key_stakeholders, industry_context, technology_preferences, org_context",
        },
        "architecture_variants": {
            "name": "Architecture Variants",
            "description": "Produces 3 alternative architecture options: cost-optimized, timeline-optimized, and risk-balanced.",
            "prompt_text": SolutionAIOrchestrator.ARCHITECTURE_VARIANTS_PROMPT,
            "category": "Phase A",
            "variables": "solution_name, business_domain, problem_statement",
        },
        "strategy_specialist": {
            "name": "Strategy Layer (Phase B)",
            "description": "ArchiMate Strategy specialist — courses of action, value streams, capability gap analysis, resources.",
            "prompt_text": SolutionAIOrchestrator.STRATEGY_SPECIALIST_PROMPT,
            "category": "Phase B",
            "variables": "solution_name, business_domain, problem_statement, stakeholders_json, drivers_json, assessments_json, goals_json, outcomes_json, principles_json, requirements_json, constraints_json, values_json, capability_count, capabilities_json, nfr_checklist",
        },
        "business_specialist": {
            "name": "Business Layer (Phase C)",
            "description": "ArchiMate Business specialist — business actors, processes, services, roles, objects, events.",
            "prompt_text": SolutionAIOrchestrator.BUSINESS_SPECIALIST_PROMPT,
            "category": "Phase C",
            "variables": "solution_name, business_domain, stakeholders_json, drivers_json, goals_json, principles_json, constraints_json, capabilities_json, courses_of_action_json, existing_business_json, nfr_checklist, advanced_business_schema",
        },
        "application_specialist": {
            "name": "Application Layer (Phase C)",
            "description": "ArchiMate Application specialist — application components, services, data objects, interfaces.",
            "prompt_text": SolutionAIOrchestrator.APPLICATION_SPECIALIST_PROMPT,
            "category": "Phase C",
            "variables": "solution_name, business_domain, requirements_json, principles_json, constraints_json, capabilities_json, business_services_json, app_count, existing_apps_json, nfr_checklist",
        },
        "technology_specialist": {
            "name": "Technology Layer (Phase D)",
            "description": "ArchiMate Technology specialist — nodes, system software, technology services, networks, artifacts.",
            "prompt_text": SolutionAIOrchestrator.TECHNOLOGY_SPECIALIST_PROMPT,
            "category": "Phase D",
            "variables": "solution_name, business_domain, tech_preferences, constraints_json, principles_json, app_components_json, existing_infra_json, nfr_checklist",
        },
        "implementation_specialist": {
            "name": "Implementation & Migration (Phase F)",
            "description": "TOGAF Phase F Migration Planning — plateaus, gaps, work packages, deliverables, milestones.",
            "prompt_text": SolutionAIOrchestrator.IMPLEMENTATION_SPECIALIST_PROMPT,
            "category": "Phase F",
            "variables": "solution_name, business_domain, timeline_constraint, budget_constraint, goals_json, stakeholders_json, principles_json, gaps_json, capabilities_json, plateaus_json, nfr_checklist",
        },
        "capability_suggestion": {
            "name": "Capability Suggestions (Step 2)",
            "description": "Maps business problems to APQC capabilities — suggests which capabilities a solution should address.",
            "prompt_text": _get_cap_suggestion_default(),
            "category": "Step 2",
            "variables": "solution_description, solution_type, business_domain, capability_catalog",
        },
        "codegen_uml_enrichment": {
            "name": "UML Enrichment (Code Workbench)",
            "description": "Transforms ArchiMate elements into UML class, sequence, component, and deployment diagrams for code generation.",
            "prompt_text": _get_codegen_prompt("uml_enrichment_service", "UML_ENRICHMENT_PROMPT"),
            "category": "Code Workbench",
            "variables": "data_objects_json, app_interfaces_json, app_components_json, app_services_json, business_processes_json, tech_nodes_json, requirements_json, constraints_json",
        },
        "codegen_models": {
            "name": "Code Generation: Models",
            "description": "Generates SQLAlchemy 2.0 model files from UML class diagram.",
            "prompt_text": _get_codegen_prompt("code_generation_service", "PROMPTS", "models"),
            "category": "Code Workbench",
            "variables": "class_diagram_json, python_version, auth_type",
        },
        "codegen_schemas": {
            "name": "Code Generation: Schemas",
            "description": "Generates Pydantic v2 schemas (Create/Update/Response) from UML class diagram.",
            "prompt_text": _get_codegen_prompt("code_generation_service", "PROMPTS", "schemas"),
            "category": "Code Workbench",
            "variables": "class_diagram_json",
        },
        "codegen_routes": {
            "name": "Code Generation: Routes",
            "description": "Generates FastAPI route handlers with full CRUD operations.",
            "prompt_text": _get_codegen_prompt("code_generation_service", "PROMPTS", "routes"),
            "category": "Code Workbench",
            "variables": "sequence_diagram_json, class_diagram_json, auth_type",
        },
        "codegen_services": {
            "name": "Code Generation: Services",
            "description": "Generates service layer classes with business logic from component diagram.",
            "prompt_text": _get_codegen_prompt("code_generation_service", "PROMPTS", "services"),
            "category": "Code Workbench",
            "variables": "component_diagram_json, sequence_diagram_json",
        },
        "codegen_integrations": {
            "name": "Code Generation: Integrations",
            "description": "Generates typed Protocol interfaces for external system integrations.",
            "prompt_text": _get_codegen_prompt("code_generation_service", "PROMPTS", "integrations"),
            "category": "Code Workbench",
            "variables": "external_interfaces_json",
        },
        "codegen_tests": {
            "name": "Code Generation: Tests",
            "description": "Generates pytest test files with CRUD test cases for all routes.",
            "prompt_text": _get_codegen_prompt("code_generation_service", "PROMPTS", "tests"),
            "category": "Code Workbench",
            "variables": "routes_summary_json, class_diagram_json",
        },
        "codegen_infrastructure": {
            "name": "Code Generation: Infrastructure",
            "description": "Generates Dockerfile, docker-compose.yml, requirements.txt, and config files.",
            "prompt_text": _get_codegen_prompt("code_generation_service", "PROMPTS", "infrastructure"),
            "category": "Code Workbench",
            "variables": "deployment_diagram_json, python_version",
        },
    }
    return _SOLUTION_PROMPT_DEFAULTS


def _get_codegen_prompt(module_name, attr_name, key=None):
    """Load a codegen prompt default from the service modules."""
    try:
        if module_name == "uml_enrichment_service":
            from app.modules.codegen.services.uml_enrichment_service import UML_ENRICHMENT_PROMPT
            return UML_ENRICHMENT_PROMPT
        elif module_name == "code_generation_service":
            from app.modules.codegen.services.code_generation_service import PROMPTS
            return PROMPTS.get(key, "(prompt not found)")
    except Exception as exc:
        logger.debug("Could not load codegen prompt default: %s", exc)
    return "(Could not load default prompt)"


@solution_prompt_admin_bp.route("/solution-prompts")
@login_required
# AIPromptTemplate carries no organization_id -- these are the platform's own
# LLM system prompts, shared by every tenant. admin_required alone let any
# tenant's own admin read every prompt, its override history and diffs; the
# write routes on this same resource (update/reset/rollback, below) already
# require platform_admin_required -- the reads were the gap
# (R1 admin-rbac systemic fix; see also
# test_solution_prompt_overrides_platform_admin.py, which covers the writes).
@platform_admin_required
@admin_required
def solution_prompts_page():
    """Render the solution AI prompt management page."""
    return render_template("admin/solution_prompts.html")


@solution_prompt_admin_bp.route("/solution-prompts/data")
@login_required
@platform_admin_required
@admin_required
def solution_prompts_data():
    """JSON API: return all solution prompt configs merged with DB overrides."""
    defaults = _get_prompt_defaults()
    prompts = []

    for key, config in defaults.items():
        override_name = solution_prompt_override_service.override_key(key)
        override = AIPromptTemplate.query.filter_by(name=override_name).first()

        prompts.append({
            "key": key,
            "name": config["name"],
            "description": config["description"],
            "category": config["category"],
            "variables": config["variables"],
            "default_prompt": config["prompt_text"],
            "current_prompt": override.system_prompt if override else config["prompt_text"],
            "has_override": override is not None,
            "updated_by_id": override.updated_by_id if override else None,
            "updated_at": override.updated_at.isoformat() if override and override.updated_at else None,
            "version": override.version if override else None,
        })

    return jsonify({"prompts": prompts})


@solution_prompt_admin_bp.route("/solution-prompts/<prompt_key>/update", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("update_solution_prompt")
def solution_prompt_update(prompt_key):
    """Save a custom override for a solution prompt."""
    defaults = _get_prompt_defaults()
    if prompt_key not in defaults:
        return jsonify({"error": f"Unknown prompt: {prompt_key}"}), 404

    payload = request.get_json(silent=True) or {}
    prompt_text = (payload.get("prompt_text") or "").strip()

    if not prompt_text:
        return jsonify({"error": "Prompt text cannot be empty"}), 400

    try:
        override = solution_prompt_override_service.update_override(
            prompt_key, defaults[prompt_key]["description"], prompt_text
        )
        logger.info("Solution prompt override saved for %s by user %s", prompt_key, current_user.id)
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        logger.exception("Failed to save solution prompt override for %s", prompt_key)
        return jsonify({"error": "Database error saving override"}), 500

    config = defaults[prompt_key]
    return jsonify({
        "success": True,
        "prompt": {
            "key": prompt_key,
            "name": config["name"],
            "description": config["description"],
            "category": config["category"],
            "variables": config["variables"],
            "default_prompt": config["prompt_text"],
            "current_prompt": override.system_prompt,
            "has_override": True,
            "updated_by_id": override.updated_by_id,
            "updated_at": override.updated_at.isoformat() if override.updated_at else None,
            "version": override.version,
        },
    })


@solution_prompt_admin_bp.route("/solution-prompts/<prompt_key>/reset", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("reset_solution_prompt")
def solution_prompt_reset(prompt_key):
    """Remove custom override, reverting to hardcoded default."""
    defaults = _get_prompt_defaults()
    if prompt_key not in defaults:
        return jsonify({"error": f"Unknown prompt: {prompt_key}"}), 404

    try:
        solution_prompt_override_service.reset_override(prompt_key)
        logger.info("Solution prompt override reset for %s by user %s", prompt_key, current_user.id)
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        logger.exception("Failed to reset solution prompt for %s", prompt_key)
        return jsonify({"error": "Database error resetting prompt"}), 500

    config = defaults[prompt_key]
    return jsonify({
        "success": True,
        "prompt": {
            "key": prompt_key,
            "name": config["name"],
            "description": config["description"],
            "category": config["category"],
            "variables": config["variables"],
            "default_prompt": config["prompt_text"],
            "current_prompt": config["prompt_text"],
            "has_override": False,
        },
    })


@solution_prompt_admin_bp.route("/solution-prompts/<prompt_key>/history")
@login_required
@platform_admin_required
@admin_required
def solution_prompt_history(prompt_key):
    """A-05: version history for a prompt override, newest first.

    Each entry is the content that was *replaced* by the next change (see the
    snapshot-before-mutate comments in update/reset above), plus the current
    live content as the implicit newest entry so the UI can diff any two
    versions including "current".
    """
    defaults = _get_prompt_defaults()
    if prompt_key not in defaults:
        return jsonify({"error": f"Unknown prompt: {prompt_key}"}), 404

    override_name = solution_prompt_override_service.override_key(prompt_key)
    override = AIPromptTemplate.query.filter_by(name=override_name).first()
    history = (
        AIPromptTemplateVersion.query.filter_by(template_name=override_name)
        .order_by(AIPromptTemplateVersion.version.desc(), AIPromptTemplateVersion.id.desc())
        .all()
    )

    versions = []
    if override:
        versions.append({
            "version": override.version,
            "system_prompt": override.system_prompt,
            "change_type": "current",
            "updated_by_id": override.updated_by_id,
            "created_at": override.updated_at.isoformat() if override.updated_at else None,
            "is_current": True,
        })
    for h in history:
        versions.append({
            "version": h.version,
            "system_prompt": h.system_prompt,
            "change_type": h.change_type,
            "updated_by_id": h.updated_by_id,
            "created_at": h.created_at.isoformat() if h.created_at else None,
            "is_current": False,
        })

    return jsonify({"key": prompt_key, "has_override": override is not None, "versions": versions})


def _version_content(prompt_key, version, override_name):
    """Look up one version's text: "current" is the live row, an int is history."""
    if version == "current":
        override = AIPromptTemplate.query.filter_by(name=override_name).first()
        return override.system_prompt if override else None
    row = (
        AIPromptTemplateVersion.query.filter_by(template_name=override_name, version=int(version))
        .order_by(AIPromptTemplateVersion.id.desc())
        .first()
    )
    return row.system_prompt if row else None


@solution_prompt_admin_bp.route("/solution-prompts/<prompt_key>/diff")
@login_required
@platform_admin_required
@admin_required
def solution_prompt_diff(prompt_key):
    """A-05: unified diff between two versions (or a version and "current").

    ?from=<version|current>&to=<version|current>, e.g. ?from=2&to=current.
    """
    defaults = _get_prompt_defaults()
    if prompt_key not in defaults:
        return jsonify({"error": f"Unknown prompt: {prompt_key}"}), 404

    from_v = request.args.get("from", "current")
    to_v = request.args.get("to", "current")
    override_name = solution_prompt_override_service.override_key(prompt_key)

    try:
        from_text = _version_content(prompt_key, from_v, override_name)
        to_text = _version_content(prompt_key, to_v, override_name)
    except (TypeError, ValueError):
        return jsonify({"error": "from/to must be an integer version or 'current'"}), 400

    if from_text is None or to_text is None:
        return jsonify({"error": "One or both versions were not found"}), 404

    diff_lines = list(
        difflib.unified_diff(
            from_text.splitlines(),
            to_text.splitlines(),
            fromfile=f"{prompt_key} v{from_v}",
            tofile=f"{prompt_key} v{to_v}",
            lineterm="",
        )
    )
    return jsonify({
        "key": prompt_key,
        "from": from_v,
        "to": to_v,
        "identical": from_text == to_text,
        "diff": diff_lines,
    })


@solution_prompt_admin_bp.route("/solution-prompts/<prompt_key>/rollback/<int:version>", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("rollback_solution_prompt")
def solution_prompt_rollback(prompt_key, version):
    """A-05: restore a prior version's content as the live override.

    Never overwrites history — the current live content is itself snapshotted
    first (change_type="update", same as a normal edit), so a rollback can
    always be undone by rolling back again. The restored content is applied
    with a fresh version number (current max + 1), consistent with an update:
    rollback is "an edit whose new text happens to come from history," not a
    time-travelling mutation of past rows.
    """
    defaults = _get_prompt_defaults()
    if prompt_key not in defaults:
        return jsonify({"error": f"Unknown prompt: {prompt_key}"}), 404

    try:
        override = solution_prompt_override_service.rollback_override(
            prompt_key, version, defaults[prompt_key]["description"]
        )
        if override is None:
            return jsonify({"error": f"No version {version} found for {prompt_key}"}), 404
        logger.info(
            "Solution prompt %s rolled back to version %s by user %s",
            prompt_key, version, current_user.id,
        )
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        logger.exception("Failed to roll back solution prompt %s to version %s", prompt_key, version)
        return jsonify({"error": "Database error rolling back prompt"}), 500

    return jsonify({
        "success": True,
        "prompt": {
            "key": prompt_key,
            "current_prompt": override.system_prompt,
            "has_override": True,
            "updated_by_id": override.updated_by_id,
            "updated_at": override.updated_at.isoformat() if override.updated_at else None,
            "version": override.version,
            "rolled_back_from_version": version,
        },
    })

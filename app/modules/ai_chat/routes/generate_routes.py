"""
-> app.modules.ai_chat.routes

Generate Routes (AIC-011)

REST endpoints for AI chat structured deliverable generation.
All routes delegate to StructuredDeliverableService and return JSON.

Routes (all on unified_ai_chat_bp, prefix /ai-chat):
  POST /generate/solution-analysis
  POST /generate/sad/<int:solution_id>
  POST /generate/visual
  POST /generate/roadmap
  POST /generate/risk-register
  POST /generate/org-impact
  POST /generate/benefit-baseline
  POST /generate/feasibility-review
  POST /generate/full-package
  POST /architect/export-brief
"""
import logging
from html import escape

from flask import jsonify, make_response, request
from flask_login import current_user, login_required

from app.services.cost_visibility import costs_visible

from app.modules.ai_chat.services.structured_deliverable_service import (
    StructuredDeliverableService,
)

from . import unified_ai_chat_bp

logger = logging.getLogger(__name__)


def _svc() -> StructuredDeliverableService:
    uid = current_user.id if current_user.is_authenticated else None
    return StructuredDeliverableService(user_id=uid)


# ---------------------------------------------------------------------------
# 1. Solution Analysis — Buy / Build / Reuse
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/solution-analysis", methods=["POST"])
@login_required
def generate_solution_analysis():
    """Run Buy/Build/Reuse analysis and return ranked recommendations."""
    data = request.get_json(silent=True) or {}
    problem = data.get("problem_description", "").strip()
    if not problem:
        return jsonify({"success": False, "error": "problem_description is required"}), 400

    result = _svc().generate_solution_analysis(
        problem_description=problem,
        capability_id=data.get("capability_id"),
        budget_min=data.get("budget_min"),
        budget_max=data.get("budget_max"),
        timeline_months=data.get("timeline_months"),
        user_count=data.get("user_count"),
        is_critical=data.get("is_critical", False),
        organization_size=data.get("organization_size", "enterprise"),
        industry_vertical=data.get("industry_vertical", ""),
        existing_constraints=data.get("existing_constraints", []),
        compliance_requirements=data.get("compliance_requirements", []),
        show_costs=costs_visible(current_user),
    )
    status = 200 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 2. SAD Sections — auto-populate sections 03, 04, 06
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/sad/<int:solution_id>", methods=["POST"])
@login_required
def generate_sad_sections(solution_id):
    """Auto-populate SAD phase-C sections for an existing solution."""
    result = _svc().generate_sad_sections(solution_id=solution_id)
    status = 200 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 3. Visual Diagram — Mermaid / PlantUML
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/visual", methods=["POST"])
@login_required
def generate_visual():
    """Generate an architecture diagram or visual artifact."""
    data = request.get_json(silent=True) or {}
    result = _svc().generate_visual(
        viz_type=data.get("viz_type", "archimate_diagram"),
        output_format=data.get("output_format", "mermaid"),
        context=data.get("context"),
        options=data.get("options"),
    )
    status = 200 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 4. Roadmap — Implementation & Migration
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/roadmap", methods=["POST"])
@login_required
def generate_roadmap():
    """Generate an ArchiMate Implementation & Migration roadmap from capability gaps."""
    data = request.get_json(silent=True) or {}
    result = _svc().generate_roadmap(
        gap_ids=data.get("gap_ids"),
        architecture_id=data.get("architecture_id"),
        timeline_months=data.get("timeline_months", 18),
        priority_filter=data.get("priority_filter"),
    )
    status = 200 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 5. Risk Register
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/risk-register", methods=["POST"])
@login_required
def generate_risk_register():
    """Create RiskSnapshot records for a solution."""
    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400

    result = _svc().generate_risk_register(
        solution_id=solution_id,
        analysis_result=data.get("analysis_result"),
        risks=data.get("risks"),
    )
    status = 201 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 6. Org Impact Assessment
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/org-impact", methods=["POST"])
@login_required
def generate_org_impact():
    """Create SolutionOrgImpact records for a solution."""
    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400

    result = _svc().generate_org_impact(
        solution_id=solution_id,
        impact_areas=data.get("impact_areas"),
        analysis_result=data.get("analysis_result"),
    )
    status = 201 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 7. Benefit Baseline
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/benefit-baseline", methods=["POST"])
@login_required
def generate_benefit_baseline():
    """Create SolutionBenefitRealization baseline records for a solution."""
    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400

    result = _svc().generate_benefit_baseline(
        solution_id=solution_id,
        benefits=data.get("benefits"),
        analysis_result=data.get("analysis_result"),
    )
    status = 201 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 8. Feasibility Review
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/feasibility-review", methods=["POST"])
@login_required
def generate_feasibility_review():
    """Create a SolutionFeasibilityReview record for a solution."""
    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400

    result = _svc().generate_feasibility_review(
        solution_id=solution_id,
        analysis_result=data.get("analysis_result"),
        review_data=data.get("review_data"),
    )
    status = 201 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 9. Full Package — all deliverables in one call
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/full-package", methods=["POST"])
@login_required
def generate_full_package():
    """
    Generate the complete set of structured deliverables for a solution:
    solution analysis, SAD sections, visual diagram, roadmap, risk register,
    org impact, benefit baseline, and feasibility review.
    """
    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    problem = data.get("problem_description", "").strip()
    if not solution_id or not problem:
        return jsonify({"success": False, "error": "solution_id and problem_description are required"}), 400

    result = _svc().generate_full_package(
        solution_id=solution_id,
        problem_description=problem,
        capability_id=data.get("capability_id"),
        budget_min=data.get("budget_min"),
        budget_max=data.get("budget_max"),
        timeline_months=data.get("timeline_months", 12),
        organization_size=data.get("organization_size", "enterprise"),
        industry_vertical=data.get("industry_vertical", ""),
        existing_constraints=data.get("existing_constraints", []),
        compliance_requirements=data.get("compliance_requirements", []),
        show_costs=costs_visible(current_user),
    )
    status = 200 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 10. Requirements Backlog — AI-generated SolutionRequirement records
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/requirements", methods=["POST"])
@login_required
def generate_requirements():
    """Generate requirements backlog for a solution."""
    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400
    try:
        solution_id = int(solution_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "solution_id must be an integer"}), 400

    result = _svc().generate_requirements(
        solution_id=solution_id,
        capability_id=data.get("capability_id"),
        count=data.get("count", 5),
    )
    status = 201 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 11. Test Cases — BDD scenarios from acceptance criteria
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/generate/test-cases", methods=["POST"])
@login_required
def generate_test_cases():
    """Generate BDD test cases from acceptance criteria for a solution."""
    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400
    try:
        solution_id = int(solution_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "solution_id must be an integer"}), 400

    result = _svc().generate_test_cases(
        solution_id=solution_id,
        options=data.get("options"),
    )
    status = 200 if result.get("success") else 500
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 12. Architecture Brief / ARB Submission Export (A95-025)
# ---------------------------------------------------------------------------

@unified_ai_chat_bp.route("/architect/export-brief", methods=["POST"])
@login_required
def architect_export_brief():
    """Export architect phase card contents as a formatted HTML document.

    Accepts JSON body:
      - solution_id (int, required)
      - format: 'pdf' (full architecture brief) or 'arb' (ARB submission only)

    Returns HTML content with Content-Disposition attachment header.
    """
    from app.models.solution_models import Solution

    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400
    try:
        solution_id = int(solution_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "solution_id must be an integer"}), 400

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    export_format = data.get("format", "pdf")

    # Build HTML content from solution data. Every field below is
    # architect-authored freeform text with no character restriction, so
    # every one is escape()'d before it reaches this hand-built HTML
    # document -- found 11 Sep 2026 while triaging the raw-html-escaping
    # gate: only the <title> line was originally flagged, but every field
    # in both branches below had the identical bug.
    title = escape(solution.name or f"Solution {solution_id}")
    desc = escape(solution.description or "N/A")
    domain = escape(solution.business_domain or "N/A")
    sol_type = escape(solution.solution_type or "N/A")
    biz_value = escape(solution.business_value or "N/A")
    sections = []

    if export_format == "arb":
        # ARB submission: focused on review-ready content
        sections.append(f"<h1>ARB Submission: {title}</h1>")
        sections.append(f"<p><strong>Solution ID:</strong> {solution_id}</p>")
        sections.append(f"<p><strong>Description:</strong> {desc}</p>")
        sections.append(f"<p><strong>Domain:</strong> {domain}</p>")
        sections.append(f"<p><strong>Solution Type:</strong> {sol_type}</p>")
        sections.append(f"<p><strong>Business Value:</strong> {biz_value}</p>")
        filename = f"arb_submission_{solution_id}.html"
    else:
        # Full architecture brief: all phases
        scope = escape(solution.scope_description or "N/A")
        status = escape(solution.status or "N/A")
        sections.append(f"<h1>Architecture Brief: {title}</h1>")
        sections.append(f"<p><strong>Solution ID:</strong> {solution_id}</p>")
        sections.append(f"<p><strong>Description:</strong> {desc}</p>")
        sections.append(f"<p><strong>Scope:</strong> {scope}</p>")
        sections.append(f"<p><strong>Domain:</strong> {domain}</p>")
        sections.append(f"<p><strong>Solution Type:</strong> {sol_type}</p>")
        sections.append(f"<p><strong>Business Value:</strong> {biz_value}</p>")
        sections.append(f"<p><strong>Status:</strong> {status}</p>")
        filename = f"architecture_brief_{solution_id}.html"

    html_content = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"  # raw-html-ok: title is escape()'d above (solution.name or a solution_id-derived default)
        "<style>body{font-family:system-ui,sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;}"
        "h1{color:#1a1a2e;border-bottom:2px solid #e2e8f0;padding-bottom:0.5rem;}"
        "p{margin:0.5rem 0;line-height:1.6;}"
        "strong{color:#374151;}</style>"
        "</head><body>"
        + "\n".join(sections)
        + "</body></html>"
    )

    response = make_response(html_content)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'  # raw-html-ok: filename is built from solution_id, an already-int()-validated route param, never free text
    return response
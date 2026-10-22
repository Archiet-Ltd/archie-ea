"""
DEPRECATED: This file is migrated to app/modules/solutions_strategic/.
Registration is now centralized via app.modules.solutions_strategic.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Solution Architect Workspace Routes

Provides view routes for the Solution Architect Workspace feature:
- /solution-architect/ - Main workspace dashboard
- /solution-architect/workspace - Solution options analysis workspace
- /solution-architect/sessions - List of analysis sessions
- /solution-architect/sessions/<id> - Session detail view
"""

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from app.decorators import WORK_PACKAGE_WRITE_ROLES, require_roles
from werkzeug.exceptions import HTTPException

from app import db
from app.decorators import audit_log
from app.models.solution_architect_models import SolutionAnalysisSession, SolutionSessionStatus

solution_architect_bp = Blueprint("solution_architect", __name__, url_prefix="/solutions/architect")


@solution_architect_bp.route("/")
@login_required
def index():
    """Solution Architect — redirect to solutions list."""
    return redirect(url_for("solution_design.list_solutions"))


@solution_architect_bp.route("/workspace")
@solution_architect_bp.route("/workspace/<int:session_id>")
@login_required
def workspace(session_id=None):
    """
    Solution options analysis workspace.

    If session_id is provided, load that session.
    Otherwise, create a new session or show workspace landing.
    """
    if not session_id:
        return redirect(url_for("solution_design.list_solutions"))

    try:
        session = SolutionAnalysisSession.query.get_or_404(session_id)
        # Verify ownership
        if session.created_by_id != current_user.id:
            flash("You don't have access to this session.", "error")
            return redirect(url_for("solution_design.list_solutions"))
    except Exception as e:
        current_app.logger.error(f"Error loading workspace session: {e}")
        return redirect(url_for("solution_design.list_solutions"))

    return render_template(
        "solutions/session_detail.html",
        session=session,
        problem=session.problem_definition,
    )


# sessions() listing route removed — dev artifact, access sessions from /solutions/


@solution_architect_bp.route("/sessions/<int:session_id>")
@login_required
def session_detail(session_id):
    """View a specific solution analysis session."""
    session = SolutionAnalysisSession.query.get_or_404(session_id)

    # Verify ownership
    if session.created_by_id != current_user.id:
        flash("You don't have access to this session.", "error")
        return redirect(url_for("solution_design.list_solutions"))

    return render_template(
        "solutions/session_detail.html",
        session=session,
        problem=session.problem_definition,
    )


@solution_architect_bp.route("/sessions/create", methods=["GET", "POST"])
@login_required
@audit_log("create_architect_session")
def create_session():
    """Create a new solution analysis session."""
    if request.method == "POST":
        data = request.form.to_dict()

        session = SolutionAnalysisSession(
            name=data.get("name", "New Analysis Session"),
            description=data.get("description", ""),
            status=SolutionSessionStatus.DRAFT,
            created_by_id=current_user.id,
        )

        db.session.add(session)
        db.session.commit()

        flash("Session created successfully.", "success")
        return redirect(url_for("solution_architect.workspace", session_id=session.id))

    return redirect(url_for("solution_architect.workspace"))


@solution_architect_bp.route("/sessions/<int:session_id>/delete", methods=["POST"])
@login_required
@audit_log("delete_architect_session")
def delete_session(session_id):
    """Delete a solution analysis session."""
    session = SolutionAnalysisSession.query.get_or_404(session_id)

    # Verify ownership
    if session.created_by_id != current_user.id:
        if request.is_json or request.headers.get("Content-Type") == "application/json":
            return jsonify({"success": False, "error": "Access denied"}), 403
        flash("You don't have access to this session.", "error")
        return redirect(url_for("solution_design.list_solutions"))

    db.session.delete(session)
    db.session.commit()

    if request.is_json or request.headers.get("Content-Type") == "application/json":
        return jsonify({"success": True, "message": "Session deleted"})

    flash("Session deleted successfully.", "success")
    return redirect(url_for("solution_design.list_solutions"))


@solution_architect_bp.route("/api/sessions/<int:session_id>/clone", methods=["POST"])
@login_required
@audit_log("clone_architect_session")
def clone_session(session_id):
    """Clone a solution analysis session."""
    try:
        session = SolutionAnalysisSession.query.get_or_404(session_id)

        # SolutionAnalysisSession has no 'problem_description' column (that lives on
        # SolutionProblemDefinition) — passing it TypeError'd on every clone.
        new_session = SolutionAnalysisSession(
            name=f"{session.name} (Copy)",
            description=session.description,
            status=SolutionSessionStatus.DRAFT
            if hasattr(SolutionSessionStatus, "DRAFT")
            else "draft",
            created_by_id=current_user.id,
        )
        db.session.add(new_session)
        db.session.commit()

        return jsonify({"success": True, "session_id": new_session.id})
    except HTTPException:
        # A 404 or 403 from the lookup is the refusal itself, not an internal error.
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error cloning session {session_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =========================================================================
# ANALYSIS API ENDPOINTS (recovered from advanced implementation)
# =========================================================================


@solution_architect_bp.route("/api/analyze", methods=["POST"])
@login_required
@audit_log("architect_analyze")
def analyze():
    """Run comprehensive solution options analysis."""
    try:
        from decimal import Decimal

        from app.modules.solutions_strategic.v2.services.solution_architect_orchestrator import (
            SolutionArchitectOrchestrator,
        )

        data = request.get_json()

        if not data or not data.get("problem_description"):
            return jsonify({"success": False, "error": "Problem description is required"}), 400

        orchestrator = SolutionArchitectOrchestrator()

        result = orchestrator.analyze_problem(
            problem_description=data.get("problem_description"),
            capability_id=data.get("capability_id"),
            budget_min=Decimal(str(data["budget_min"])) if data.get("budget_min") else None,
            budget_max=Decimal(str(data["budget_max"])) if data.get("budget_max") else None,
            timeline_months=data.get("timeline_months"),
            user_count=data.get("user_count"),
            is_critical=data.get("is_critical", False),
            organization_size=data.get("organization_size"),
            industry_vertical=data.get("industry_vertical"),
            existing_constraints=data.get("constraints", []),
            compliance_requirements=data.get("compliance_requirements", []),
        )

        return jsonify({"success": True, "analysis": result})

    except Exception as e:
        current_app.logger.error(f"Error in solution analysis: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_architect_bp.route("/api/analysis/<int:analysis_id>/details/<path_type>")
@login_required
def analysis_details(analysis_id, path_type):
    """Get detailed breakdown for a specific analysis path."""
    try:
        from app.modules.solutions_strategic.v2.services.solution_architect_orchestrator import (
            SolutionArchitectOrchestrator,
        )

        valid_types = ["reuse", "buy", "build", "hybrid"]
        if path_type not in valid_types:
            return jsonify({"success": False, "error": f"Invalid path type: {path_type}"}), 400

        orchestrator = SolutionArchitectOrchestrator()
        details = orchestrator.get_path_details(analysis_id, path_type)

        return jsonify({"success": True, "details": details})

    except Exception as e:
        current_app.logger.error(f"Error getting analysis details: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_architect_bp.route("/api/analysis/<int:analysis_id>/export", methods=["POST"])
@login_required
@audit_log("export_analysis")
def export_analysis(analysis_id):
    """Export analysis results to PDF, PPTX, or XLSX."""
    try:
        from app.modules.solutions_strategic.v2.services.solution_architect_orchestrator import (
            SolutionArchitectOrchestrator,
        )

        data = request.get_json() or {}
        export_format = data.get("format", "pdf")

        if export_format not in ["pdf", "pptx", "xlsx"]:
            return jsonify({"success": False, "error": "Unsupported format"}), 400

        orchestrator = SolutionArchitectOrchestrator()
        result = orchestrator.export_analysis(analysis_id, export_format)

        return jsonify({"success": True, "export": result})

    except Exception as e:
        current_app.logger.error(f"Error exporting analysis: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_architect_bp.route("/api/quick-check", methods=["POST"])
@login_required
@audit_log("architect_quick_check")
def quick_check():
    """Lightweight heuristic-based quick recommendation."""
    try:
        data = request.get_json()

        if not data or not data.get("problem_description"):
            return jsonify({"success": False, "error": "Problem description is required"}), 400

        description = data["problem_description"].lower()

        # Score keywords
        buy_keywords = [
            "purchase",
            "vendor",
            "commercial",
            "license",
            "saas",
            "off-the-shelf",
            "cots",
            "subscribe",
            "marketplace",
        ]
        build_keywords = [
            "custom",
            "develop",
            "create",
            "build",
            "code",
            "engineer",
            "bespoke",
            "unique",
            "proprietary",
        ]
        reuse_keywords = [
            "existing",
            "reuse",
            "extend",
            "enhance",
            "repurpose",
            "leverage",
            "modify",
            "adapt",
            "internal",
        ]

        scores = {
            "buy": sum(1 for kw in buy_keywords if kw in description),
            "build": sum(1 for kw in build_keywords if kw in description),
            "reuse": sum(1 for kw in reuse_keywords if kw in description),
        }

        total = sum(scores.values()) or 1
        recommendation = max(scores, key=scores.get)
        confidence = scores[recommendation] / total

        return jsonify(
            {
                "success": True,
                "recommendation": recommendation,
                "confidence": round(confidence, 2),
                "scores": {k: round(v / total, 2) for k, v in scores.items()},
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error in quick check: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =========================================================================
# OPTIONS ANALYSIS SESSION ENDPOINTS
# =========================================================================


@solution_architect_bp.route("/api/sessions/<int:session_id>/run-analysis", methods=["POST"])
@login_required
@audit_log("run_session_analysis")
def run_session_analysis(session_id):
    """Run options analysis for a session and persist recommendations.

    Uses the SolutionArchitectOrchestrator to perform 4 - phase analysis
    (Buy/Build/Reuse, existing apps, vendor research, gap analysis) and
    saves results as SolutionRecommendation records on the session.
    """
    try:
        from decimal import Decimal

        from app.models.solution_architect_models import (
            RecommendationOptionType,
            SolutionRecommendation,
        )
        from app.modules.solutions_strategic.v2.services.solution_architect_orchestrator import (
            SolutionArchitectOrchestrator,
        )

        session = SolutionAnalysisSession.query.get_or_404(session_id)

        if session.created_by_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        problem = session.problem_definition
        if not problem:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No problem definition found. Please define the problem first.",
                    }
                ),
                400,
            )

        orchestrator = SolutionArchitectOrchestrator()

        result = orchestrator.analyze_problem(
            problem_description=problem.problem_description,
            capability_id=None,
            budget_min=Decimal(str(problem.budget_min)) if problem.budget_min else None,
            budget_max=Decimal(str(problem.budget_max)) if problem.budget_max else None,
            timeline_months=problem.timeline_months,
            user_count=problem.user_count,
            is_critical=problem.is_critical or False,
            organization_size=problem.organization_size,
            industry_vertical=problem.industry_vertical,
            existing_constraints=[c.name for c in problem.constraints]
            if problem.constraints
            else [],
            compliance_requirements=problem.compliance_requirements or [],
        )

        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error", "Analysis failed")}), 500

        # Clear existing recommendations for this session
        SolutionRecommendation.query.filter_by(session_id=session_id).delete()

        # Persist new recommendations
        option_type_map = {
            "REUSE": RecommendationOptionType.REUSE,
            "BUY": RecommendationOptionType.BUY,
            "BUILD": RecommendationOptionType.BUILD,
            "HYBRID": RecommendationOptionType.HYBRID,
            "PARTNER": RecommendationOptionType.PARTNER,
        }

        recommendations_data = result.get("recommendations", [])
        for rank, rec in enumerate(recommendations_data, start=1):
            option_type_str = rec.get("option_type", "BUILD").upper()
            option_type = option_type_map.get(option_type_str, RecommendationOptionType.BUILD)

            estimated_cost = rec.get("estimated_cost")
            cost_min = None
            cost_max = None
            if isinstance(estimated_cost, (int, float)):
                cost_min = Decimal(str(estimated_cost * 0.8))
                cost_max = Decimal(str(estimated_cost * 1.2))

            recommendation = SolutionRecommendation(
                session_id=session_id,
                option_type=option_type,
                rank=rank,
                score=rec.get("score", 0),
                confidence=min(1.0, rec.get("score", 0) / 100.0),
                estimated_cost_min=cost_min,
                estimated_cost_max=cost_max,
                timeline_months=rec.get("timeline_months"),
                pros=rec.get("pros", []),
                cons=rec.get("cons", []),
                risks=[{"description": rec.get("risk_level", "MEDIUM"), "severity": 3}],
                justification=rec.get("description", ""),
                data_sources=["orchestrator_analysis"],
            )
            db.session.add(recommendation)

        # Update session status
        session.status = SolutionSessionStatus.IN_PROGRESS
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "recommendations": [
                    {
                        "option_type": rec.get("option_type", ""),
                        "score": rec.get("score", 0),
                        "estimated_cost": rec.get("estimated_cost"),
                        "timeline_months": rec.get("timeline_months"),
                        "risk_level": rec.get("risk_level", "MEDIUM"),
                        "description": rec.get("description", ""),
                        "pros": rec.get("pros", []),
                        "cons": rec.get("cons", []),
                    }
                    for rec in recommendations_data
                ],
                "buy_build_analysis": result.get("buy_build_analysis"),
                "existing_applications": result.get("existing_applications"),
                "vendor_options": result.get("vendor_options"),
                "gap_analysis": result.get("gap_analysis"),
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error running session analysis: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_architect_bp.route("/api/sessions/<int:session_id>/analysis-results")
@login_required
def get_session_analysis_results(session_id):
    """Get persisted analysis results for a session.

    Returns all SolutionRecommendation records for the session
    serialized as JSON for the options analysis UI.
    """
    try:
        from app.models.solution_architect_models import SolutionRecommendation

        session = SolutionAnalysisSession.query.get_or_404(session_id)

        if session.created_by_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        recommendations = (
            SolutionRecommendation.query.filter_by(session_id=session_id)
            .order_by(SolutionRecommendation.rank.asc())
            .all()
        )

        return jsonify(
            {
                "success": True,
                "has_results": len(recommendations) > 0,
                "recommendations": [
                    {
                        "id": rec.id,
                        "option_type": rec.option_type.value.upper(),
                        "rank": rec.rank,
                        "score": rec.score,
                        "confidence": rec.confidence,
                        "estimated_cost_min": float(rec.estimated_cost_min)
                        if rec.estimated_cost_min
                        else None,
                        "estimated_cost_max": float(rec.estimated_cost_max)
                        if rec.estimated_cost_max
                        else None,
                        "timeline_months": rec.timeline_months,
                        "pros": rec.pros or [],
                        "cons": rec.cons or [],
                        "risks": rec.risks or [],
                        "justification": rec.justification,
                        "generated_at": rec.generated_at.isoformat() if rec.generated_at else None,
                    }
                    for rec in recommendations
                ],
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error getting analysis results: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =========================================================================
# AI-POWERED SOLUTION DESIGN ENDPOINTS
# =========================================================================


@solution_architect_bp.route("/api/sessions/<int:session_id>/ai-elements", methods=["POST"])
@login_required
@audit_log("generate_ai_elements")
def generate_ai_elements(session_id):
    """Generate AI-powered ArchiMate element suggestions for all 6 layers.

    Uses SolutionAIService to analyze the session's problem description
    and suggest appropriate ArchiMate elements (Motivation, Strategy,
    Business, Application, Technology, Implementation layers).
    """
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_service import (
            SolutionAIService,
        )

        session = SolutionAnalysisSession.query.get_or_404(session_id)
        if session.created_by_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        problem = session.problem_definition
        if not problem:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No problem definition found. Please define the problem first.",
                    }
                ),
                400,
            )

        # Extract capabilities from problem definition
        capabilities = []
        if problem.capabilities:
            for cap_mapping in problem.capabilities:
                cap = cap_mapping.capability
                if cap:
                    capabilities.append(
                        {
                            "name": cap.name,
                            "category": cap_mapping.support_level.value
                            if cap_mapping.support_level
                            else "required",
                        }
                    )

        service = SolutionAIService()
        result = service.suggest_elements(
            solution_description=problem.problem_description,
            capabilities=capabilities if capabilities else None,
            solution_type=None,
            business_domain=problem.industry_vertical,
        )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(f"Error generating AI elements: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_architect_bp.route("/api/sessions/<int:session_id>/ai-requirements", methods=["POST"])
@login_required
@audit_log("generate_ai_requirements")
def generate_ai_requirements(session_id):
    """Generate AI-powered requirements from the session's problem description.

    Uses SolutionAIService to produce Functional, Non-Functional, and
    Constraint requirements with priority and acceptance criteria.
    """
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_service import (
            SolutionAIService,
        )

        session = SolutionAnalysisSession.query.get_or_404(session_id)
        if session.created_by_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        problem = session.problem_definition
        if not problem:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No problem definition found. Please define the problem first.",
                    }
                ),
                400,
            )

        capabilities = []
        if problem.capabilities:
            for cap_mapping in problem.capabilities:
                cap = cap_mapping.capability
                if cap:
                    capabilities.append(
                        {
                            "name": cap.name,
                            "category": cap_mapping.support_level.value
                            if cap_mapping.support_level
                            else "required",
                        }
                    )

        service = SolutionAIService()
        result = service.generate_requirements(
            solution_description=problem.problem_description,
            capabilities=capabilities if capabilities else None,
            solution_type=None,
            business_domain=problem.industry_vertical,
        )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(f"Error generating AI requirements: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_architect_bp.route("/api/sessions/<int:session_id>/ai-roadmap", methods=["POST"])
@login_required
@audit_log("generate_ai_roadmap")
def generate_ai_roadmap(session_id):
    """Generate AI-powered implementation roadmap with work packages by phase.

    Uses SolutionAIService to create Phase 1 - 4 work packages covering
    Foundation, Core Features, Enhancement, and Go-Live stages.
    """
    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_service import (
            SolutionAIService,
        )

        session = SolutionAnalysisSession.query.get_or_404(session_id)
        if session.created_by_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        problem = session.problem_definition
        if not problem:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No problem definition found. Please define the problem first.",
                    }
                ),
                400,
            )

        capabilities = []
        if problem.capabilities:
            for cap_mapping in problem.capabilities:
                cap = cap_mapping.capability
                if cap:
                    capabilities.append(
                        {
                            "name": cap.name,
                            "category": cap_mapping.support_level.value
                            if cap_mapping.support_level
                            else "required",
                        }
                    )

        service = SolutionAIService()
        result = service.generate_roadmap_items(
            solution_description=problem.problem_description,
            capabilities=capabilities if capabilities else None,
            solution_type=None,
        )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(f"Error generating AI roadmap: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_architect_bp.route("/api/sessions/<int:session_id>/ai-patterns", methods=["POST"])
@login_required
@audit_log("detect_ai_patterns")
def detect_ai_patterns(session_id):
    """Detect matching architecture patterns from the problem description.

    Uses ArchiMatePatternLibrary trigger keywords to match the session's
    problem description against 10 built-in architecture patterns
    (3 - tier web, microservices, SaaS, data warehouse, etc.).
    """
    try:
        from app.modules.solutions_strategic.v2.services.archimate_pattern_library import (
            ArchiMatePatternLibrary,
        )

        session = SolutionAnalysisSession.query.get_or_404(session_id)
        if session.created_by_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        problem = session.problem_definition
        if not problem:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No problem definition found. Please define the problem first.",
                    }
                ),
                400,
            )

        description_text = (problem.problem_description or "").lower()
        library = ArchiMatePatternLibrary()
        matched_patterns = []

        for pattern_id, pattern in library.PATTERNS.items():
            triggers = pattern.get("triggers", [])
            matched_triggers = [t for t in triggers if t in description_text]
            confidence = len(matched_triggers) / len(triggers) if triggers else 0.0

            matched_patterns.append(
                {
                    "pattern_id": pattern_id,
                    "name": pattern["name"],
                    "description": pattern["description"],
                    "confidence": round(confidence, 3),
                    "matched_triggers": matched_triggers,
                    "total_triggers": len(triggers),
                    "is_match": confidence >= pattern.get("confidence_threshold", 0.7),
                    "elements": [
                        {
                            "name": e["name"].replace("{app}", "Solution"),
                            "type": e["type"],
                            "layer": e["layer"],
                            "description": e.get("description", ""),
                        }
                        for e in pattern.get("elements", [])
                    ],
                }
            )

        # Sort: matches first (by confidence desc), then non-matches (by confidence desc)
        matched_patterns.sort(key=lambda p: (-int(p["is_match"]), -p["confidence"]))

        return jsonify(
            {
                "success": True,
                "patterns": matched_patterns,
                "match_count": sum(1 for p in matched_patterns if p["is_match"]),
                "source": "pattern_library",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error detecting patterns: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =========================================================================
# ORCHESTRATION ENDPOINTS (EA/SA wiring)
# =========================================================================


@solution_architect_bp.route(
    "/api/sessions/<int:session_id>/accept-recommendation", methods=["POST"]
)
@login_required
@audit_log("accept_recommendation")
def accept_recommendation(session_id: int):
    """Accept a recommendation and auto-create a Solution."""
    from app.services.solution_orchestration_service import (
        SolutionOrchestrationService,
    )

    data = request.get_json(silent=True) or {}
    recommendation_id = data.get("recommendation_id")
    if not recommendation_id:
        return jsonify({"success": False, "error": "recommendation_id is required"}), 400

    service = SolutionOrchestrationService()
    result = service.accept_recommendation(
        session_id=session_id,
        recommendation_id=int(recommendation_id),
        user_id=current_user.id,
    )

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify(result)


@solution_architect_bp.route(
    "/api/sessions/<int:session_id>/analyze-problem", methods=["POST"]
)
@login_required
@audit_log("analyze_problem")
def analyze_problem(session_id: int):
    """Run AI analysis on problem definition to populate motivation layer."""
    from app.services.solution_orchestration_service import (
        SolutionOrchestrationService,
    )

    service = SolutionOrchestrationService()
    result = service.analyze_problem(session_id=session_id)

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)


@solution_architect_bp.route(
    "/api/sessions/<int:session_id>/apply-elements", methods=["POST"]
)
@login_required
@audit_log("apply_elements")
def apply_elements(session_id: int):
    """Apply AI-suggested ArchiMate elements to a solution."""
    from app.services.solution_orchestration_service import (
        SolutionOrchestrationService,
    )

    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    elements = data.get("elements", [])

    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400
    if not elements:
        return jsonify({"success": False, "error": "elements list is required"}), 400

    service = SolutionOrchestrationService()
    result = service.apply_element_suggestions(
        solution_id=int(solution_id),
        elements=elements,
    )

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)


@solution_architect_bp.route(
    "/api/sessions/<int:session_id>/apply-requirements", methods=["POST"]
)
@login_required
@audit_log("apply_requirements")
def apply_requirements(session_id: int):
    """Apply AI-suggested requirements to the session's problem definition."""
    from app.services.solution_orchestration_service import (
        SolutionOrchestrationService,
    )

    data = request.get_json(silent=True) or {}
    requirements = data.get("requirements", [])

    if not requirements:
        return jsonify({"success": False, "error": "requirements list is required"}), 400

    service = SolutionOrchestrationService()
    result = service.apply_requirement_suggestions(
        session_id=session_id,
        requirements=requirements,
    )

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)


@solution_architect_bp.route(
    "/api/sessions/<int:session_id>/apply-roadmap", methods=["POST"]
)
@login_required
@require_roles(*WORK_PACKAGE_WRITE_ROLES)
@audit_log("apply_roadmap")
def apply_roadmap(session_id: int):
    """Apply AI-suggested roadmap items to a solution."""
    from app.services.solution_orchestration_service import (
        SolutionOrchestrationService,
    )

    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    roadmap_items = data.get("roadmap_items", [])

    if not solution_id:
        return jsonify({"success": False, "error": "solution_id is required"}), 400
    if not roadmap_items:
        return jsonify({"success": False, "error": "roadmap_items list is required"}), 400

    service = SolutionOrchestrationService()
    result = service.apply_roadmap_suggestions(
        solution_id=int(solution_id),
        roadmap_items=roadmap_items,
    )

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)

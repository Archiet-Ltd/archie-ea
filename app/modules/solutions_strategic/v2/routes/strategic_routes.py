"""
NOT deprecated, despite what this header said until 2026-08-09. It claimed the
file was a fallback kept until "Phase 6 cleanup" and instructed readers not to
modify it. It is registered and serving traffic: app/_bootstrap/blueprints.py
line 742 imports strategic_bp from this module and registers it.

The header was actively harmful - a defect audit found the compliance dashboard
here rendering a fully compliant portfolio from a database error, and the
instruction not to modify is exactly the kind of thing that leaves such a bug
in place. If this file is ever genuinely retired, delete it rather than leaving
a note that contradicts the blueprint registration.

Strategic Planning Routes

Provides routes for investment prioritization, risk assessment, and strategic decision support.
"""

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_portfolio import ApplicationComponent
from app.models.apqc_process import APQCProcess, ProcessApplicationMapping
from app.models.archimate_core import ArchiMateElement
from app.models.business_capability import BusinessCapability
from app.models.solution_models import SolutionArchiMateElement, solution_applications
from app.modules.solutions_strategic.v2.services.architecture_governance_service import (
    ArchitectureGovernanceService,
)
from app.modules.solutions_strategic.v2.services.capability_health_service import (
    CapabilityHealthService,
)
from app.modules.solutions_strategic.v2.services.compliance_tracking_service import (
    ComplianceTrackingService,
)
from app.modules.solutions_strategic.v2.services.dependency_visualization_service import (
    DependencyVisualizationService,
)
from app.modules.solutions_strategic.v2.services.impact_analysis_service import (
    ImpactAnalysisService,
)
from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
    InvestmentPrioritizationService,
)
from app.modules.solutions_strategic.v2.services.process_optimization_service import (
    ProcessOptimizationService,
)
from app.modules.solutions_strategic.v2.services.risk_assessment_service import (
    RiskAssessmentService,
)
from app.modules.solutions_strategic.v2.services.risk_mitigation_service import (
    RiskMitigationService,
)
from app.modules.solutions_strategic.v2.services.strategic_service import StrategicService
from app.modules.solutions_strategic.v2.services.technology_roadmap_service import (
    TechnologyRoadmapService,
)
from app.modules.solutions_strategic.v2.services.strategic_recommendation_engine import (
    StrategicRecommendationEngine,
)
from app.modules.solutions_strategic.v2.services.arb_integration_service import (
    ARBIntegrationService,
)
from app.models.strategic import CapabilityHealthOverride
from app import db
from app.decorators import audit_log
from datetime import datetime
from app.utils.pagination import safe_int_arg

strategic_bp = Blueprint("strategic", __name__, url_prefix="/strategic")


def _build_solution_impact_fallback(element_id: int, change_type: str = "MODIFY"):
    """Build a useful impact payload from application/solution relationships."""
    from app.models.solution_models import Solution
    from app.models.solution_sad_models import SolutionAPQCProcess
    from app.models.vendor.vendor_organization import VendorProduct
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
        _get_solution_capabilities_payload,
    )

    element = db.session.get(ArchiMateElement, element_id)
    if not element:
        return None

    app = None
    if getattr(element, "application_component_id", None):
        app = db.session.get(ApplicationComponent, element.application_component_id)
    if app is None and (element.type or "") == "ApplicationComponent":
        app = ApplicationComponent.query.filter_by(archimate_element_id=element.id).first()

    direct_dependencies = []
    indirect_dependencies = []
    seen_direct = set()
    seen_indirect = set()
    solution_ids = set()

    def _push(target, seen, item):
        key = (item.get("id"), item.get("type"), item.get("name"))
        if key not in seen:
            target.append(item)
            seen.add(key)

    if app is not None:
        cap_rows = (
            # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
            db.session.query(BusinessCapability, ApplicationCapabilityMapping)
            .join(
                ApplicationCapabilityMapping,
                ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
            )
            .filter(ApplicationCapabilityMapping.application_component_id == app.id)
            .all()
        )
        for capability, mapping in cap_rows:
            _push(
                direct_dependencies,
                seen_direct,
                {
                    "id": capability.id,
                    "name": capability.name,
                    "type": "BusinessCapability",
                    "level": 2,
                    "dependency_level": getattr(mapping, "relationship_strength", None)
                    or getattr(mapping, "gap_severity", None)
                    or "medium",
                    "criticality": getattr(mapping, "business_criticality", None) or "",
                    "app_name": app.name,
                    "tco": float(getattr(app, "total_cost_of_ownership", 0) or 0),
                },
            )

        proc_rows = (
            db.session.query(APQCProcess)
            .join(ProcessApplicationMapping)
            .filter(ProcessApplicationMapping.application_id == app.id)
            .distinct()
            .all()
        )
        for process in proc_rows:
            _push(
                direct_dependencies,
                seen_direct,
                {
                    "id": process.id,
                    "name": process.process_name,
                    "type": "BusinessProcess",
                    "level": 2,
                    "dependency_level": "medium",
                    "criticality": "",
                    "app_name": app.name,
                    "tco": 0.0,
                },
            )

        app_solution_rows = db.session.execute(  # tenant-filtered: scoped via parent FK
            db.select(solution_applications.c.solution_id).where(
                solution_applications.c.application_component_id == app.id
            )
        ).fetchall()
        solution_ids.update(row[0] for row in app_solution_rows if row[0])

    linked_solution_rows = SolutionArchiMateElement.query.filter_by(element_id=element_id).all()
    solution_ids.update(row.solution_id for row in linked_solution_rows if row.solution_id)

    svp_table = db.metadata.tables.get("solution_vendor_products")
    for solution_id in solution_ids:
        solution = db.session.get(Solution, solution_id)
        if not solution:
            continue

        for capability in _get_solution_capabilities_payload(solution):
            _push(
                direct_dependencies,
                seen_direct,
                {
                    "id": capability.get("capability_id") or capability.get("id"),
                    "name": capability.get("name") or capability.get("capability_name"),
                    "type": "BusinessCapability",
                    "level": 2,
                    "dependency_level": capability.get("gap_severity") or "medium",
                    "criticality": "",
                    "app_name": app.name if app else "",
                    "tco": 0.0,
                },
            )

        process_links = SolutionAPQCProcess.query.filter_by(solution_id=solution_id).all()
        process_ids = [link.apqc_process_id for link in process_links if link.apqc_process_id]
        if process_ids:
            for process in APQCProcess.query.filter(APQCProcess.id.in_(process_ids)).all():
                _push(
                    direct_dependencies,
                    seen_direct,
                    {
                        "id": process.id,
                        "name": process.process_name,
                        "type": "BusinessProcess",
                        "level": 2,
                        "dependency_level": "medium",
                        "criticality": "",
                        "app_name": app.name if app else "",
                        "tco": 0.0,
                    },
                )

        if svp_table is not None:
            vp_rows = db.session.execute(  # tenant-filtered: scoped via solution_id FK
                svp_table.select().where(svp_table.c.solution_id == solution_id)
            ).fetchall()
            vp_ids = [row.vendor_product_id for row in vp_rows if row.vendor_product_id]
            if vp_ids:
                for product in VendorProduct.query.filter(VendorProduct.id.in_(vp_ids)).all():
                    _push(
                        indirect_dependencies,
                        seen_indirect,
                        {
                            "id": product.id,
                            "name": product.name,
                            "type": "VendorProduct",
                            "level": 3,
                            "dependency_level": "low",
                            "criticality": "",
                            "app_name": app.name if app else "",
                            "tco": 0.0,
                        },
                    )

        tech_links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        tech_ids = [
            row.element_id
            for row in tech_links
            if row.element_id and (row.layer_type or "").lower() == "technology"
        ]
        if tech_ids:
            for tech in ArchiMateElement.query.filter(ArchiMateElement.id.in_(tech_ids)).all():
                _push(
                    indirect_dependencies,
                    seen_indirect,
                    {
                        "id": tech.id,
                        "name": tech.name,
                        "type": tech.type or "Technology",
                        "level": 3,
                        "dependency_level": getattr(tech, "dependency_level", None) or "medium",
                        "criticality": "",
                        "app_name": app.name if app else "",
                        "tco": 0.0,
                    },
                )

    total_affected = len(direct_dependencies) + len(indirect_dependencies)
    if total_affected == 0:
        return None

    weighted_score = len(direct_dependencies) * 3 + len(indirect_dependencies)
    if weighted_score >= 12:
        risk_level = "CRITICAL"
    elif weighted_score >= 8:
        risk_level = "HIGH"
    elif weighted_score >= 3:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    # F-12, Capgemini dry-run: `total_affected * 25000` invented a financial-risk
    # figure out of a literal per-dependency dollar amount with no source —
    # exactly the "0 that means not computed" problem CLAUDE.md's
    # never-invent-data rule calls out. Report the real TCO or nothing.
    app_tco = float(getattr(app, "total_cost_of_ownership", 0) or 0) if app else 0.0
    estimated_financial_risk = app_tco if app_tco > 0 else None

    return {
        "element_id": element_id,
        "change_type": change_type,
        "direct_dependencies": direct_dependencies,
        "indirect_dependencies": indirect_dependencies,
        "total_affected": total_affected,
        "weighted_score": weighted_score,
        "risk_level": risk_level,
        "estimated_financial_risk": estimated_financial_risk,
        "analysis_id": None,
        "fallback_used": True,
    }


@strategic_bp.route("/capability-health")
@login_required
def capability_health():
    """Capability health dashboard."""
    try:
        service = CapabilityHealthService()
        metrics = service.get_capability_health_metrics()
        return render_template("strategic/capability_health.html", metrics=metrics)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/capability-health")
@login_required
def api_capability_health():
    """API endpoint for capability health metrics."""
    try:
        service = CapabilityHealthService()
        metrics = service.get_capability_health_metrics()
        return jsonify(metrics)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/investment-matrix")
@login_required
def investment_matrix():
    """Investment prioritization matrix dashboard."""
    try:
        service = InvestmentPrioritizationService()
        analysis = service.analyze_investment_priorities(include_risk_analysis=True)

        return render_template(
            "strategic/investment_matrix.html",
            capability_scores=analysis["capability_scores"],
            critical_investments=analysis["critical_investments"],
            high_investments=analysis["high_investments"],
            medium_investments=analysis["medium_investments"],
            low_investments=analysis["low_investments"],
            portfolio_metrics=analysis["portfolio_metrics"],
            recommendations=analysis["recommendations"],
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/investment-analysis")
@login_required
def api_investment_analysis():
    """API endpoint for investment analysis."""
    try:
        service = InvestmentPrioritizationService()
        analysis = service.analyze_investment_priorities(include_risk_analysis=True)
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# REMOVED: /api/risks/<capability_id>/details — now served by strategic_risks_hardened.py
# (hardened version adds: @login_required, role-based auth, rate limiting, audit logging)

# REMOVED: /api/risks/<capability_id>/mitigation — now served by strategic_risks_hardened.py
# (hardened version adds: @login_required, role-based auth, rate limiting, audit logging)


@strategic_bp.route("/api/risks/<int:capability_id>/assign-owner", methods=["POST"])
@login_required
@audit_log("assign_risk_owner")
def assign_risk_owner(capability_id):
    """Quick action to assign risk owner."""
    try:
        data = request.get_json()
        owner = data.get("owner")
        if not owner:
            return jsonify({"error": "Owner name required"}), 400
        result = RiskMitigationService.assign_risk_owner(capability_id, owner)
        return jsonify(result)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/risks/<int:capability_id>/status", methods=["PATCH"])
@login_required
@audit_log("update_risk_status")
def update_risk_status(capability_id):
    """Update mitigation status."""
    try:
        data = request.get_json()
        status = data.get("status")
        result = RiskMitigationService.update_mitigation_status(capability_id, status)
        return jsonify(result)
    except ValueError:
        return jsonify({"error": "Invalid request parameters"}), 400
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# REMOVED: /api/risks/statuses — now served by strategic_risks_hardened.py
# (hardened version adds: @login_required, role-based auth, rate limiting, audit logging)


@strategic_bp.route("/risk-assessment")
@login_required
def risk_assessment():
    """Risk assessment dashboard."""
    try:
        service = RiskAssessmentService()
        analysis = service.analyze_portfolio_risks(include_technology_debt=True)

        return render_template(
            "strategic/risk_assessment.html",
            capability_risks=analysis["capability_risks"],
            critical_risks=analysis["critical_risks"],
            high_risks=analysis["high_risks"],
            medium_risks=analysis["medium_risks"],
            low_risks=analysis["low_risks"],
            portfolio_metrics=analysis["portfolio_metrics"],
            recommendations=analysis["recommendations"],
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/risk-analysis")
@login_required
def api_risk_analysis():
    """API endpoint for risk analysis."""
    try:
        service = RiskAssessmentService()
        analysis = service.analyze_portfolio_risks(include_technology_debt=True)
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/impact-analysis")
@login_required
def impact_analysis():
    """Impact analysis dashboard — architecture change ripple-effect visualization."""
    return render_template("strategic/impact_analysis.html")


@strategic_bp.route("/api/impact-analysis", methods=["POST"])
@login_required
@audit_log("impact_analysis")
def api_impact_analysis():
    """API endpoint for impact analysis."""
    try:
        data = request.get_json()
        element_id = data.get("element_id")
        change_type = data.get("change_type", "MODIFY")

        if not element_id:
            return jsonify({"error": "element_id is required"}), 400

        service = ImpactAnalysisService()
        analysis = service.analyze_change_impact(element_id, change_type)
        fallback_analysis = _build_solution_impact_fallback(element_id, change_type)
        if (
            fallback_analysis
            and fallback_analysis.get("total_affected", 0) > analysis.get("total_affected", 0)
        ):
            analysis = {
                **analysis,
                **fallback_analysis,
                "analysis_id": analysis.get("analysis_id"),
            }
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/impact-analysis/history", methods=["GET"])
@login_required
def api_impact_analysis_history():
    """Return the last 10 impact analyses run by the caller's organisation."""
    try:
        from app.models.traceability import ImpactAnalysisResult
        records = (
            ImpactAnalysisResult.for_organization(current_user.organization_id)
            .order_by(ImpactAnalysisResult.created_at.desc())
            .limit(10)
            .all()
        )
        return jsonify([r.to_dict() for r in records])
    except Exception:
        current_app.logger.exception("Impact analysis history query failed")
        return jsonify(
            {"error": "Could not load the impact analysis history"}
        ), 500


@strategic_bp.route("/api/portfolio-impact", methods=["POST"])
@login_required
@audit_log("portfolio_impact")
def api_portfolio_impact():
    """API endpoint for portfolio-wide impact analysis."""
    try:
        data = request.get_json()
        change_scenarios = data.get("change_scenarios", [])

        if not change_scenarios:
            return jsonify({"error": "change_scenarios is required"}), 400

        service = ImpactAnalysisService()
        analysis = service.analyze_portfolio_impact(change_scenarios)
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/process-optimization")
@login_required
def process_optimization():
    """Process optimization dashboard."""
    try:
        service = ProcessOptimizationService()
        analysis = service.analyze_process_portfolio(include_benchmarking=True)

        return render_template(
            "strategic/process_optimization.html",
            process_analyses=analysis["process_analyses"],
            critical_processes=analysis["critical_processes"],
            high_processes=analysis["high_processes"],
            medium_processes=analysis["medium_processes"],
            low_processes=analysis["low_processes"],
            portfolio_metrics=analysis["portfolio_metrics"],
            recommendations=analysis["recommendations"],
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/process-analysis")
@login_required
def api_process_analysis():
    """API endpoint for process optimization analysis."""
    try:
        service = ProcessOptimizationService()
        analysis = service.analyze_process_portfolio(include_benchmarking=True)
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/compliance-tracking")
@login_required
def compliance_tracking():
    """Compliance tracking dashboard."""
    try:
        service = ComplianceTrackingService()
        analysis = service.analyze_compliance_portfolio(include_risk_assessment=True)

        return render_template(
            "strategic/compliance_tracking.html",
            capability_compliance=analysis["capability_compliance"],
            critical_compliance=analysis["critical_compliance"],
            high_compliance=analysis["high_compliance"],
            medium_compliance=analysis["medium_compliance"],
            low_compliance=analysis["low_compliance"],
            portfolio_metrics=analysis["portfolio_metrics"],
            recommendations=analysis["recommendations"],
        )
    except Exception:
        # Do NOT render the template with empty lists here. Doing so showed a
        # compliance dashboard reporting zero capabilities and zero findings,
        # which the user cannot distinguish from a fully compliant portfolio.
        # Matches the sibling dashboards (dependency_visualization,
        # technology_roadmap) which surface the failure instead.
        current_app.logger.exception("Compliance tracking dashboard failed")
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/compliance-analysis")
@login_required
def api_compliance_analysis():
    """API endpoint for compliance tracking analysis."""
    try:
        service = ComplianceTrackingService()
        analysis = service.analyze_compliance_portfolio(include_risk_assessment=True)
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/dependency-visualization")
@login_required
def dependency_visualization():
    """Dependency visualization dashboard."""
    try:
        service = DependencyVisualizationService()
        analysis = service.analyze_dependency_portfolio(include_visualization=True)

        return render_template(
            "strategic/dependency_visualization.html",
            dependency_graph=analysis["dependency_graph"],
            dependency_metrics=analysis["dependency_metrics"],
            critical_paths=analysis["critical_paths"],
            health_analysis=analysis["health_analysis"],
            visualization_data=analysis["visualization_data"],
            recommendations=analysis["recommendations"],
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/dependency-analysis")
@login_required
def api_dependency_analysis():
    """API endpoint for dependency visualization analysis."""
    try:
        service = DependencyVisualizationService()
        analysis = service.analyze_dependency_portfolio(include_visualization=True)
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/technology-roadmap")
@login_required
def technology_roadmap():
    """Technology roadmap dashboard."""
    try:
        service = TechnologyRoadmapService()
        analysis = service.analyze_technology_portfolio(include_innovation=True)

        return render_template(
            "strategic/technology_roadmap.html",
            technology_analyses=analysis["technology_analyses"],
            critical_technology=analysis["critical_technology"],
            high_technology=analysis["high_technology"],
            medium_technology=analysis["medium_technology"],
            low_technology=analysis["low_technology"],
            portfolio_metrics=analysis["portfolio_metrics"],
            roadmap_phases=analysis["roadmap_phases"],
            recommendations=analysis["recommendations"],
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/technology-analysis")
@login_required
def api_technology_analysis():
    """API endpoint for technology roadmap analysis."""
    try:
        service = TechnologyRoadmapService()
        analysis = service.analyze_technology_portfolio(include_innovation=True)
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/architecture-governance")
@login_required
def architecture_governance():
    """Architecture governance dashboard."""
    try:
        service = ArchitectureGovernanceService()
        analysis = service.analyze_governance_portfolio(include_compliance=True)

        return render_template(
            "strategic/architecture_governance.html",
            governance_analyses=analysis["governance_analyses"],
            critical_governance=analysis["critical_governance"],
            high_governance=analysis["high_governance"],
            medium_governance=analysis["medium_governance"],
            low_governance=analysis["low_governance"],
            portfolio_metrics=analysis["portfolio_metrics"],
            recommendations=analysis["recommendations"],
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/governance-analysis")
@login_required
def api_governance_analysis():
    """API endpoint for architecture governance analysis."""
    try:
        service = ArchitectureGovernanceService()
        analysis = service.analyze_governance_portfolio(include_compliance=True)
        return jsonify(analysis)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/submit-review", methods=["POST"])
@login_required
@audit_log("submit_review")
def api_submit_review():
    """API endpoint for submitting architecture review."""
    try:
        data = request.get_json()
        element_id = data.get("element_id")
        reviewer_id = data.get("reviewer_id")
        review_type = data.get("review_type", "STANDARD")

        if not element_id or not reviewer_id:
            return jsonify({"error": "element_id and reviewer_id are required"}), 400

        service = ArchitectureGovernanceService()
        result = service.submit_for_review(element_id, reviewer_id, review_type)
        return jsonify(result)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@strategic_bp.route("/api/check-compliance", methods=["POST"])
@login_required
@audit_log("check_compliance")
def api_check_compliance():
    """API endpoint for checking compliance."""
    try:
        data = request.get_json()
        element_id = data.get("element_id")

        if not element_id:
            return jsonify({"error": "element_id is required"}), 400

        service = ArchitectureGovernanceService()
        result = service.check_compliance(element_id)
        return jsonify(result)
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# TAKE ACTION ENDPOINTS - Strategic Initiative Creation
# ============================================================================


@strategic_bp.route("/api/initiatives/from-health", methods=["POST"])
@login_required
@audit_log("create_initiative_from_health")
def api_create_initiative_from_health():
    """Create strategic initiative from capability health dashboard."""
    try:
        data = request.get_json()
        
        # Delegate to StrategicService
        result = StrategicService.create_initiative(data)
        
        if result.get("success"):
            return jsonify({
                "success": True,
                "message": "Remediation plan created successfully",
                "initiative": result.get("initiative")
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "Failed to create initiative")
            }), 400
            
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/initiatives/from-investment", methods=["POST"])
@login_required
@audit_log("create_initiative_from_investment")
def api_create_initiative_from_investment():
    """Create strategic initiative from investment matrix dashboard."""
    try:
        data = request.get_json()
        
        # Delegate to StrategicService
        result = StrategicService.create_initiative(data)
        
        if result.get("success"):
            return jsonify({
                "success": True,
                "message": "Investment proposal created successfully",
                "initiative": result.get("initiative")
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "Failed to create initiative")
            }), 400
            
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/initiatives/from-risk", methods=["POST"])
@login_required
@audit_log("create_initiative_from_risk")
def api_create_initiative_from_risk():
    """Create strategic initiative from risk assessment dashboard."""
    try:
        data = request.get_json()
        
        # Delegate to StrategicService
        result = StrategicService.create_initiative(data)
        
        if result.get("success"):
            return jsonify({
                "success": True,
                "message": "Risk mitigation plan created successfully",
                "initiative": result.get("initiative")
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "Failed to create initiative")
            }), 400
            
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/initiatives/from-impact", methods=["POST"])
@login_required
@audit_log("create_initiative_from_impact")
def api_create_initiative_from_impact():
    """Create strategic initiative from impact analysis dashboard."""
    try:
        data = request.get_json()
        
        # Delegate to StrategicService
        result = StrategicService.create_initiative(data)
        
        if result.get("success"):
            return jsonify({
                "success": True,
                "message": "Change request created successfully",
                "initiative": result.get("initiative")
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "Failed to create initiative")
            }), 400
            
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# CAPABILITY HEALTH OVERRIDES - Manual Score Overrides with Audit Trail
# ============================================================================


def _health_overrides():
    """Capability health overrides visible to the caller's organisation.

    An override carries no organisation column; the capability it overrides does.
    The inner join lets the automatic tenant filter on ``BusinessCapability``
    apply to every override read or changed here.
    """
    return CapabilityHealthOverride.query.join(
        BusinessCapability, CapabilityHealthOverride.capability_id == BusinessCapability.id
    )


def _health_override_in_org(override_id):
    return _health_overrides().filter(CapabilityHealthOverride.id == override_id).first()


@strategic_bp.route("/api/capability-health/overrides", methods=["POST"])
@login_required
@audit_log("create_health_override")
def api_create_health_override():
    """Create a manual override for a capability health score."""
    try:
        from flask_login import current_user
        
        data = request.get_json()
        
        # Validate required fields
        required = ["capability_id", "override_score", "justification", "override_reason"]
        if not all(field in data for field in required):
            return jsonify({"success": False, "error": "Missing required fields"}), 400
        
        capability_id = data["capability_id"]
        override_score = float(data["override_score"])
        
        # Validate capability exists
        capability = BusinessCapability.query.get(capability_id)
        if not capability:
            return jsonify({"success": False, "error": "Capability not found"}), 404
        
        # Validate score range
        if not 0 <= override_score <= 100:
            return jsonify({"success": False, "error": "Score must be between 0 and 100"}), 400
        
        # Calculate original score
        service = CapabilityHealthService()
        metrics = service.get_capability_health_metrics()
        cap_metrics = next((c for c in metrics["health_by_capability"] if c["id"] == capability_id), None)
        original_score = cap_metrics["score"] if cap_metrics else 0.0
        
        # Deactivate any existing active overrides for this capability
        existing = CapabilityHealthOverride.query.filter_by(
            capability_id=capability_id, active=True
        ).all()
        for override in existing:
            override.active = False
        
        # Create new override
        new_override = CapabilityHealthOverride(
            capability_id=capability_id,
            original_score=original_score,
            override_score=override_score,
            justification=data["justification"],
            override_reason=data["override_reason"],
            created_by_id=current_user.id if hasattr(current_user, "id") else None,
            expires_at=datetime.strptime(data["expires_at"], "%Y-%m-%d").date()
            if data.get("expires_at")
            else None,
        )
        
        db.session.add(new_override)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Health score override created successfully",
            "override": new_override.to_dict(),
        })
        
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/capability-health/overrides", methods=["GET"])
@login_required
def api_list_health_overrides():
    """List all capability health overrides with optional filtering."""
    try:
        # Optional filters
        active_only = request.args.get("active", "false").lower() == "true"
        capability_id = request.args.get("capability_id", type=int)
        
        query = _health_overrides()
        
        if active_only:
            query = query.filter(CapabilityHealthOverride.active.is_(True))
        
        if capability_id:
            query = query.filter(CapabilityHealthOverride.capability_id == capability_id)
        
        overrides = query.order_by(CapabilityHealthOverride.created_at.desc()).all()
        
        return jsonify({
            "success": True,
            "overrides": [o.to_dict() for o in overrides],
            "count": len(overrides),
        })
        
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/capability-health/overrides/<int:override_id>", methods=["GET"])
@login_required
def api_get_health_override(override_id):
    """Get a specific capability health override."""
    try:
        override = _health_override_in_org(override_id)
        
        if not override:
            return jsonify({"success": False, "error": "Override not found"}), 404
        
        return jsonify({"success": True, "override": override.to_dict()})
        
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/capability-health/overrides/<int:override_id>", methods=["PUT"])
@login_required
@audit_log("update_health_override")
def api_update_health_override(override_id):
    """Update an existing capability health override."""
    try:
        
        override = _health_override_in_org(override_id)
        
        if not override:
            return jsonify({"success": False, "error": "Override not found"}), 404
        
        data = request.get_json()
        
        # Update allowed fields
        if "override_score" in data:
            score = float(data["override_score"])
            if not 0 <= score <= 100:
                return jsonify({"success": False, "error": "Score must be between 0 and 100"}), 400
            override.override_score = score
        
        if "justification" in data:
            override.justification = data["justification"]
        
        if "override_reason" in data:
            override.override_reason = data["override_reason"]
        
        if "expires_at" in data:
            override.expires_at = (
                datetime.strptime(data["expires_at"], "%Y-%m-%d").date()
                if data["expires_at"]
                else None
            )
        
        override.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Override updated successfully",
            "override": override.to_dict(),
        })
        
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/capability-health/overrides/<int:override_id>", methods=["DELETE"])
@login_required
@audit_log("delete_health_override")
def api_delete_health_override(override_id):
    """Deactivate a capability health override (soft delete)."""
    try:
        override = _health_override_in_org(override_id)
        
        if not override:
            return jsonify({"success": False, "error": "Override not found"}), 404
        
        override.active = False
        override.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Override deactivated successfully",
        })
        
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# LLM-POWERED STRATEGIC RECOMMENDATIONS
# ============================================================================

@strategic_bp.route("/api/recommendations/<dashboard>", methods=["POST"])
# CSRF: Protected via X-CSRFToken header sent by Platform.fetch
@login_required
@audit_log("generate_recommendations")
def api_generate_recommendations(dashboard):
    """
    Generate LLM-powered recommendations for a strategic dashboard.
    
    Args:
        dashboard: Dashboard name (capability_health, investment_matrix, risk_assessment, impact_analysis)
    
    Request Body:
        {
            "context": {...},  # Rich context with org info, metrics, initiatives
            "max_recommendations": 5  # Optional, default 5
        }
    
    Returns:
        JSON: {
            "success": true,
            "recommendations": [...],
            "metadata": {
                "dashboard": str,
                "generated_at": str,
                "model_used": str,
                "provider_used": str
            }
        }
    """
    try:
        # Parse request body
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400
        
        context = data.get("context", {})
        max_recs = data.get("max_recommendations", 5)
        
        # Get current user ID if authenticated
        user_id = current_user.id if current_user.is_authenticated else None
        
        # Generate recommendations
        engine = StrategicRecommendationEngine()
        recommendations = engine.generate_recommendations(
            dashboard=dashboard,
            context=context,
            max_recommendations=max_recs,
            created_by_id=user_id
        )
        
        # Build metadata
        metadata = {
            "dashboard": dashboard,
            "generated_at": datetime.utcnow().isoformat(),
            "count": len(recommendations)
        }
        
        if recommendations:
            metadata["model_used"] = recommendations[0].get("model_used")
            metadata["provider_used"] = recommendations[0].get("provider_used")
        
        return jsonify({
            "success": True,
            "recommendations": recommendations,
            "metadata": metadata
        })
        
    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/recommendations/<int:rec_id>/rate", methods=["POST"])
# CSRF: Protected via X-CSRFToken header sent by Platform.fetch
@login_required
@audit_log("rate_recommendation")
def api_rate_recommendation(rec_id):
    """
    Rate a strategic recommendation with user feedback.
    
    Args:
        rec_id: Recommendation ID
    
    Request Body:
        {
            "rating": 1-5,  # Required
            "feedback_notes": str,  # Optional
            "was_implemented": bool  # Optional, default false
        }
    
    Returns:
        JSON: {"success": true, "message": "Recommendation rated successfully"}
    """
    try:
        # Parse request body
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400
        
        rating = data.get("rating")
        if rating is None:
            return jsonify({"success": False, "error": "rating field required"}), 400
        
        if not isinstance(rating, int) or not 1 <= rating <= 5:
            return jsonify({"success": False, "error": "rating must be 1-5"}), 400
        
        feedback_notes = data.get("feedback_notes")
        was_implemented = data.get("was_implemented", False)
        
        # Get current user ID if authenticated
        user_id = current_user.id if current_user.is_authenticated else None
        
        # Rate recommendation
        engine = StrategicRecommendationEngine()
        success = engine.rate_recommendation(
            recommendation_id=rec_id,
            rating=rating,
            feedback_notes=feedback_notes,
            was_implemented=was_implemented,
            user_id=user_id
        )
        
        if success:
            return jsonify({
                "success": True,
                "message": "Recommendation rated successfully"
            })
        else:
            return jsonify({
                "success": False,
                "error": "Recommendation not found"
            }), 404
        
    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/recommendations/<dashboard>", methods=["GET"])
@login_required
def api_get_recommendations(dashboard):
    """
    Fetch stored recommendations for a dashboard.
    
    Args:
        dashboard: Dashboard name
    
    Query Params:
        capability_id: Filter by capability ID (optional)
        limit: Max recommendations to return (default 10)
        include_rated: Include already-rated recommendations (default true)
    
    Returns:
        JSON: {
            "success": true,
            "recommendations": [...],
            "metadata": {...}
        }
    """
    try:
        # Parse query params
        capability_id = request.args.get("capability_id", type=int)
        limit = safe_int_arg('limit', 10, minimum=1, maximum=500)
        include_rated = request.args.get("include_rated", default="true").lower() == "true"
        
        # Fetch recommendations
        engine = StrategicRecommendationEngine()
        recommendations = engine.get_recommendations(
            dashboard=dashboard,
            capability_id=capability_id,
            limit=limit,
            include_rated=include_rated
        )
        
        return jsonify({
            "success": True,
            "recommendations": recommendations,
            "metadata": {
                "dashboard": dashboard,
                "count": len(recommendations),
                "capability_id": capability_id,
                "limit": limit
            }
        })
        
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =========================================================================
# ARB INTEGRATION ENDPOINTS
# =========================================================================

@strategic_bp.route("/api/arb/submit-capability/<int:capability_id>", methods=["POST"])
@login_required
@audit_log("submit_capability_to_arb")
def api_submit_capability_to_arb(capability_id):
    """
    Submit a capability for ARB review with pre-populated business case.
    
    Args:
        capability_id: Capability ID to submit
    
    Request Body:
        {
            "justification": str,  # Additional justification (optional)
            "priority_override": str,  # Override priority (optional)
            "estimated_timeline": str,  # Override timeline (optional)
            "additional_context": {}  # Extra metadata (optional)
        }
    
    Returns:
        JSON: {
            "success": true,
            "review_id": int,
            "review_number": str,
            "arb_status": str,
            "message": str
        }
    """
    try:
        # Parse request body
        data = request.get_json() or {}
        
        justification = data.get("justification")
        priority_override = data.get("priority_override")
        estimated_timeline = data.get("estimated_timeline")
        additional_context = data.get("additional_context")
        
        # Submit to ARB
        arb_service = ARBIntegrationService()
        review_item = arb_service.submit_capability_for_review(
            capability_id=capability_id,
            submitted_by_id=current_user.id,
            justification=justification,
            priority_override=priority_override,
            estimated_timeline=estimated_timeline,
            additional_context=additional_context
        )
        
        return jsonify({
            "success": True,
            "review_id": review_item.id,
            "review_number": review_item.review_number,
            "arb_status": "pending_review",
            "message": f"Capability submitted for ARB review (#{review_item.review_number})",
            "review_url": f"/arb/review/{review_item.id}"
        })
        
    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/arb/capability-status/<int:capability_id>", methods=["GET"])
@login_required
def api_get_capability_arb_status(capability_id):
    """
    Get ARB status and review details for a capability.
    
    Args:
        capability_id: Capability ID
    
    Returns:
        JSON: {
            "success": true,
            "arb_status": str,
            "submission_date": str,
            "decision_date": str,
            "review_details": {...}
        }
    """
    try:
        arb_service = ARBIntegrationService()
        status = arb_service.get_capability_arb_status(capability_id)
        
        return jsonify({
            "success": True,
            **status
        })
        
    except ValueError:
        return jsonify({"success": False, "error": "Resource not found"}), 404
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/arb/sync-decision/<int:review_id>", methods=["POST"])
@login_required
@audit_log("sync_arb_decision")
def api_sync_arb_decision(review_id):
    """
    Webhook endpoint to sync ARB decision back to capability.
    
    Called automatically when ARB makes a decision on a capability review.
    
    Args:
        review_id: ARB review item ID
    
    Returns:
        JSON: {"success": true, "capability_updated": bool}
    """
    try:
        arb_service = ARBIntegrationService()
        capability = arb_service.sync_arb_decision_to_capability(review_id)
        
        if capability:
            return jsonify({
                "success": True,
                "capability_updated": True,
                "capability_id": capability.id,
                "capability_name": capability.name,
                "arb_status": capability.arb_status
            })
        else:
            return jsonify({
                "success": True,
                "capability_updated": False,
                "message": "Review is not a capability review or no linked capability found"
            })
        
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@strategic_bp.route("/api/arb/portfolio-summary", methods=["GET"])
@login_required
def api_get_arb_portfolio_summary():
    """
    Get portfolio-wide ARB submission statistics.
    
    Returns:
        JSON: {
            "total_capabilities": int,
            "with_arb_tracking": int,
            "not_submitted": int,
            "pending_review": int,
            "approved": int,
            "rejected": int,
            "approval_rate": float
        }
    """
    try:
        arb_service = ARBIntegrationService()
        summary = arb_service.get_arb_portfolio_summary()
        
        return jsonify({
            "success": True,
            **summary
        })
        
    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

"""
Vendor Options Analysis API Routes

Provides API endpoints for vendor comparison and analysis with export capabilities.
"""
from werkzeug.exceptions import HTTPException
import csv
import io
import json
from datetime import datetime

from flask import abort, current_app, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from .. import db
from . import application_mgmt
from app.utils.pagination import safe_int_arg


VALID_WEIGHT_KEYS = frozenset(
    ["cost", "capability_coverage", "risk", "strategic_fit", "implementation"]
)


def _is_active_org_admin(user) -> bool:
    """D-4 (admin-rbac-active-org continuation): the "or admin" branch of
    every owner-or-admin check in this file used to be ``hasattr(user,
    "is_admin") and user.is_admin()`` -- a global ``Permission.ADMINISTER``
    flag, independent of which organisation is active in the session. Since
    every self-registered user is Administrator of their own organisation
    (OptionsAnalysis/StakeholderInput are both TenantMixin-scoped, so this
    never crossed organisations, but it did let a Viewer of the ACTIVE
    organisation -- home-org Administrator of their OWN organisation --
    read or act on another member's analysis/stakeholder-input row within
    that organisation), re-derived against ``g.current_org_id`` instead, the
    same predicate ``admin_required``/``org_admin_required`` already use.
    """
    if not hasattr(user, "is_authenticated") or not user.is_authenticated:
        return False
    from flask import g

    from app.middleware.tenant_decorators import is_platform_admin
    from app.services.rbac_service import rbac_service

    active_org_id = getattr(g, "current_org_id", None)
    return is_platform_admin(user) or rbac_service.is_org_admin(user, active_org_id)


def _resolve_vendor_name(vendor_option):
    """Extract display name from a VendorOption, checking org and product relationships."""
    if vendor_option.vendor_organization:
        return vendor_option.vendor_organization.name
    if vendor_option.vendor_product and vendor_option.vendor_product.vendor_organization:
        return (
            f"{vendor_option.vendor_product.vendor_organization.name}"
            f" - {vendor_option.vendor_product.name}"
        )
    return vendor_option.vendor_name or "Unknown"


def _serialize_vendor_option(vo, include_extended=False):
    """Serialize a VendorOption to a JSON-safe dict."""
    data = {
        "id": vo.id,
        "name": _resolve_vendor_name(vo),
        "ranking": vo.ranking,
        "total_score": float(vo.total_score) if vo.total_score else None,
        "cost_score": float(vo.cost_score) if vo.cost_score else None,
        "capability_score": float(vo.capability_coverage_score)
        if vo.capability_coverage_score
        else None,
        "risk_score": float(vo.risk_score) if vo.risk_score else None,
        "strategic_fit_score": float(vo.strategic_fit_score)
        if vo.strategic_fit_score
        else None,
        "implementation_score": float(vo.implementation_score)
        if vo.implementation_score
        else None,
        "tco_total": float(vo.tco_total) if vo.tco_total else None,
    }
    if include_extended:
        data["license_cost_annual"] = (
            float(vo.license_cost_annual) if vo.license_cost_annual else None
        )
        data["support_cost_annual"] = (
            float(vo.support_cost_annual) if vo.support_cost_annual else None
        )
        data["confidence_score"] = (
            min(float(vo.total_score) / 100.0, 1.0)
            if vo.total_score
            else None
        )
        data["manual_scores"] = {
            field: getattr(vo, field, None) for field in MANUAL_SCORE_FIELDS
        }
    return data


def _validate_criteria_weights(weights):
    """Validate criteria_weights dict. Returns (cleaned_dict, error_string)."""
    if not isinstance(weights, dict):
        return None, "criteria_weights must be a JSON object"
    if set(weights.keys()) != VALID_WEIGHT_KEYS:
        return None, (
            f"criteria_weights must contain exactly these keys: "
            f"{', '.join(sorted(VALID_WEIGHT_KEYS))}"
        )
    try:
        values = {k: float(v) for k, v in weights.items()}
    except (TypeError, ValueError):
        return None, "All weight values must be numbers"
    total = sum(values.values())
    if abs(total - 1.0) > 0.01:
        return None, f"Weights must sum to 1.0, got {total:.4f}"
    if any(v < 0 for v in values.values()):
        return None, "Weight values cannot be negative"
    return values, None


def _check_analysis_access(analysis):
    """Check if current user can access this analysis. Returns error response or None."""
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404
    if analysis.created_by_id != current_user.id:
        if not _is_active_org_admin(current_user):
            return jsonify({"error": "Access denied"}), 403
    return None


# ---------------------------------------------------------------------------
# PAGE ROUTES — render HTML templates for vendor analysis workflows
# ---------------------------------------------------------------------------
@application_mgmt.route("/vendor-analysis", methods=["GET"])
@login_required
def vendor_analysis_list():
    """Vendor Options Analysis list page."""
    return render_template("application_mgmt/vendor_analysis_list.html")


@application_mgmt.route("/vendor-analysis/new", methods=["GET"])
@login_required
def vendor_analysis_new():
    """Create a new vendor analysis — redirects to detail page with id=0 (wizard mode)."""
    return render_template("application_mgmt/vendor_analysis_detail.html", analysis_id=0)


@application_mgmt.route("/vendor-analysis/<int:analysis_id>", methods=["GET"])
@login_required
def vendor_analysis_detail(analysis_id):
    """Vendor analysis detail / comparison workbench page."""
    from app.models.vendor_analysis import OptionsAnalysis
    from app.utils.route_guards import load_entity

    # The page used to render for any id, so a nonexistent analysis produced a
    # workbench titled with an id that is not a record. Match the JSON detail
    # endpoint's contract: 404 when absent, 403 when it is someone else's.
    analysis = load_entity(OptionsAnalysis, analysis_id)
    if analysis is None:
        abort(404, description="Analysis not found")
    if analysis.created_by_id != current_user.id and not _is_active_org_admin(current_user):
        abort(403)
    return render_template(
        "application_mgmt/vendor_analysis_detail.html", analysis_id=analysis_id
    )


# ---------------------------------------------------------------------------
# LIST analyses
# ---------------------------------------------------------------------------
@application_mgmt.route("/api/vendor-analyses", methods=["GET"])
@login_required
def api_list_vendor_analyses():
    """List all vendor analyses for the current user."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(max(safe_int_arg('per_page', 20, minimum=1, maximum=500), 1), 100)
        status_filter = request.args.get("status")
        search = request.args.get("search", "").strip()
        sort_by = request.args.get("sort_by", "created_at")
        sort_dir = request.args.get("sort_dir", "desc")

        query = OptionsAnalysis.query.filter_by(created_by_id=current_user.id)

        if status_filter:
            if status_filter in ("approved", "rejected", "pending_approval"):
                query = query.filter_by(approval_status=status_filter)
            else:
                query = query.filter_by(status=status_filter)

        if search:
            query = query.filter(OptionsAnalysis.name.ilike(f"%{search}%"))


        total_count = query.count()
        approved_count = OptionsAnalysis.query.filter_by(
            created_by_id=current_user.id, approval_status="approved"
        ).count()
        in_progress_count = OptionsAnalysis.query.filter_by(
            created_by_id=current_user.id
        ).filter(OptionsAnalysis.status.in_(["draft", "running"])).count()
        with_rec_count = OptionsAnalysis.query.filter_by(
            created_by_id=current_user.id
        ).filter(OptionsAnalysis.recommendation_confidence.isnot(None)).count()

        SORTABLE = {"created_at", "name", "status", "updated_at"}
        if sort_by not in SORTABLE:
            sort_by = "created_at"
        sort_col = getattr(OptionsAnalysis, sort_by)
        query = query.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        results = []
        for a in pagination.items:
            winner = a.get_winner()
            results.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "status": a.status,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                    "vendor_count": len(a.vendor_options),
                    "recommended_vendor": _resolve_vendor_name(winner) if winner else None,
                    "recommendation_confidence": (
                        float(a.recommendation_confidence)
                        if a.recommendation_confidence
                        else None
                    ),
                    "approval_status": a.approval_status,
                }
            )
        return jsonify({
            "analyses": results,
            "summary": {
                "total": total_count,
                "approved": approved_count,
                "in_progress": in_progress_count,
                "with_recommendation": with_rec_count,
            },
            "pagination": {
                "page": pagination.page,
                "per_page": pagination.per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "has_next": pagination.has_next,
                "has_prev": pagination.has_prev,
            },
        })
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error listing analyses: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# GET single analysis detail
# ---------------------------------------------------------------------------
@application_mgmt.route("/api/vendor-analysis/<int:analysis_id>", methods=["GET"])
@login_required
def api_get_vendor_analysis_detail(analysis_id):
    """Get full detail for a single analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        vendors = [
            _serialize_vendor_option(vo, include_extended=True)
            for vo in sorted(analysis.vendor_options, key=lambda v: v.ranking or 999)
        ]

        winner = analysis.get_winner()
        return jsonify(
            {
                "id": analysis.id,
                "name": analysis.name,
                "description": analysis.description,
                "status": analysis.status,
                "analysis_type": analysis.analysis_type,
                "criteria_weights": analysis.get_criteria_weights(),
                "tco_years": analysis.tco_years,
                "organization_size": analysis.organization_size,
                "industry_vertical": analysis.industry_vertical,
                "deployment_scale": analysis.deployment_scale,
                "approval_status": analysis.approval_status,
                "approval_notes": analysis.approval_notes,
                "created_at": analysis.created_at.isoformat()
                if analysis.created_at
                else None,
                "updated_at": analysis.updated_at.isoformat()
                if analysis.updated_at
                else None,
                "vendors": vendors,
                "total_vendors": len(vendors),
                "recommended_vendor": _resolve_vendor_name(winner)
                if winner
                else None,
                "average_score": (
                    sum(v["total_score"] or 0 for v in vendors) / len(vendors)
                    if vendors
                    else 0
                ),
                "confidence": (
                    float(analysis.recommendation_confidence)
                    if analysis.recommendation_confidence
                    else None
                ),
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error getting analysis detail: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# PATCH update analysis
# ---------------------------------------------------------------------------
@application_mgmt.route("/api/vendor-analysis/<int:analysis_id>", methods=["PATCH"])
@login_required
def api_update_vendor_analysis(analysis_id):
    """Update analysis metadata, weights, or scope."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        if "name" in data:
            name = str(data["name"]).strip()
            if not name or len(name) > 256:
                return jsonify({"error": "Name must be 1-256 characters"}), 400
            analysis.name = name

        if "description" in data:
            analysis.description = data["description"]

        if "criteria_weights" in data:
            weights, err = _validate_criteria_weights(data["criteria_weights"])
            if err:
                return jsonify({"error": err}), 400
            analysis.set_criteria_weights(weights)

        VALID_ORG_SIZES = {"smb", "midmarket", "enterprise"}
        VALID_INDUSTRIES = {
            "", "financial", "healthcare", "retail", "manufacturing",
            "technology", "energy", "government", "telecom", "other",
        }

        if "organization_size" in data:
            if data["organization_size"] not in VALID_ORG_SIZES:
                return jsonify({"error": f"organization_size must be one of: {', '.join(sorted(VALID_ORG_SIZES))}"}), 400
            analysis.organization_size = data["organization_size"]
        if "industry_vertical" in data:
            if data["industry_vertical"] not in VALID_INDUSTRIES:
                return jsonify({"error": "Invalid industry_vertical"}), 400
            analysis.industry_vertical = data["industry_vertical"]
        if "tco_years" in data:
            try:
                tco = int(data["tco_years"])
                if tco < 1 or tco > 30:
                    return jsonify({"error": "tco_years must be between 1 and 30"}), 400
                analysis.tco_years = tco
            except (TypeError, ValueError):
                return jsonify({"error": "tco_years must be an integer"}), 400

        db.session.commit()
        return jsonify({"success": True, "analysis_id": analysis.id})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating analysis: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# DELETE analysis
# ---------------------------------------------------------------------------
@application_mgmt.route("/api/vendor-analysis/<int:analysis_id>", methods=["DELETE"])
@login_required
def api_delete_vendor_analysis(analysis_id):
    """Delete an analysis and all its child records."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        db.session.delete(analysis)
        db.session.commit()
        return jsonify({"success": True})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting analysis: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# POST add vendor option
# ---------------------------------------------------------------------------
@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/options", methods=["POST"]
)
@login_required
def api_add_vendor_option(analysis_id):
    """Add a vendor option to an existing analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis, VendorOption

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        data = request.get_json()
        vendor_org_id = data.get("vendor_org_id")
        vendor_product_id = data.get("vendor_product_id")
        vendor_name = data.get("vendor_name")

        if not any([vendor_org_id, vendor_product_id, vendor_name]):
            return jsonify(
                {"error": "Provide vendor_org_id, vendor_product_id, or vendor_name"}
            ), 400

        option = VendorOption(
            analysis_id=analysis.id,
            vendor_organization_id=vendor_org_id,
            vendor_product_id=vendor_product_id,
            vendor_name=vendor_name or "Unknown",
        )
        db.session.add(option)
        db.session.commit()
        return jsonify({"success": True, "option_id": option.id}), 201
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error adding vendor option: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# DELETE vendor option
# ---------------------------------------------------------------------------
@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/options/<int:option_id>",
    methods=["DELETE"],
)
@login_required
def api_delete_vendor_option(analysis_id, option_id):
    """Remove a vendor option from an analysis."""
    try:
        from app.models.vendor_analysis import VendorOption

        option = VendorOption.query.filter_by(
            id=option_id, analysis_id=analysis_id
        ).first()
        if not option:
            return jsonify({"error": "Option not found"}), 404
        denied = _check_analysis_access(option.analysis)
        if denied:
            return denied

        db.session.delete(option)
        db.session.commit()
        return jsonify({"success": True})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting vendor option: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# Manual sub-scores for a vendor option
# ---------------------------------------------------------------------------

# All VendorOption integer fields (1-10) that users can set manually.
MANUAL_SCORE_FIELDS = frozenset([
    # Risk (lower = better)
    "vendor_lock_in_risk",
    "market_position_risk",
    "support_continuity_risk",
    "technology_maturity_risk",
    "compliance_risk",
    # Strategic fit (higher = better)
    "technology_alignment",
    "roadmap_alignment",
    "ecosystem_fit",
    "future_proofing",
    "vendor_relationship",
    # Implementation (lower = better)
    "implementation_complexity",
    "integration_difficulty",
    "data_migration_risk",
    "change_management_impact",
    "training_requirements",
    "skill_availability",
    # Technical ratings (higher = better)
    "scalability_rating",
    "security_rating",
    "performance_rating",
])


@application_mgmt.route(
    "/api/vendor-analyses/<int:analysis_id>/options/<int:option_id>/scores",
    methods=["GET"],
)
@login_required
def api_get_option_scores(analysis_id, option_id):
    """Return current manual sub-score values for a vendor option."""
    try:
        from app.models.vendor_analysis import VendorOption

        option = VendorOption.query.filter_by(
            id=option_id, analysis_id=analysis_id
        ).first()
        if not option:
            return jsonify({"error": "Option not found"}), 404
        denied = _check_analysis_access(option.analysis)
        if denied:
            return denied

        scores = {field: getattr(option, field, None) for field in MANUAL_SCORE_FIELDS}
        return jsonify({"success": True, "scores": scores})
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error getting option scores: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analyses/<int:analysis_id>/options/<int:option_id>/scores",
    methods=["PATCH"],
)
@login_required
def api_update_option_scores(analysis_id, option_id):
    """Set manual sub-score values on a vendor option and recalculate aggregates."""
    try:
        from app.models.vendor_analysis import VendorOption

        option = VendorOption.query.filter_by(
            id=option_id, analysis_id=analysis_id
        ).first()
        if not option:
            return jsonify({"error": "Option not found"}), 404
        denied = _check_analysis_access(option.analysis)
        if denied:
            return denied

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Validate: reject unknown fields
        unknown = set(data.keys()) - MANUAL_SCORE_FIELDS
        if unknown:
            return jsonify({
                "error": f"Unknown fields: {', '.join(sorted(unknown))}. "
                         f"Allowed: {', '.join(sorted(MANUAL_SCORE_FIELDS))}"
            }), 400

        # Validate: each value must be an integer 1-10 or null to clear
        errors = []
        updated = {}
        for field, value in data.items():
            if value is None:
                setattr(option, field, None)
                updated[field] = None
                continue
            if not isinstance(value, int) or value < 1 or value > 10:
                errors.append(f"{field} must be an integer 1-10 (got {value!r})")
                continue
            setattr(option, field, value)
            updated[field] = value

        if errors and not updated:
            return jsonify({"error": "; ".join(errors)}), 400

        db.session.flush()

        # Recalculate aggregate scores
        from app.services.vendor_analysis.options_analysis_service import (
            OptionsAnalysisService,
        )
        weights = option.analysis.get_criteria_weights()
        required_cap_ids = OptionsAnalysisService._collect_required_capability_ids(option.analysis)
        OptionsAnalysisService._score_vendor_option(option, weights, required_cap_ids)
        OptionsAnalysisService._normalize_cost_scores(option.analysis.vendor_options)
        db.session.commit()

        result = {
            "success": True,
            "scores": updated,
            "recalculated": {
                "risk_score": float(option.risk_score) if option.risk_score else None,
                "strategic_fit_score": float(option.strategic_fit_score) if option.strategic_fit_score else None,
                "implementation_score": float(option.implementation_score) if option.implementation_score else None,
                "capability_coverage_score": float(option.capability_coverage_score) if option.capability_coverage_score else None,
                "cost_score": float(option.cost_score) if option.cost_score else None,
                "total_score": float(option.total_score) if option.total_score else None,
            },
        }
        if errors:
            result["warnings"] = errors
        return jsonify(result)
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating option scores: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# POST run/re-run scoring
# ---------------------------------------------------------------------------
@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/run-scoring", methods=["POST"]
)
@login_required
def api_run_scoring(analysis_id):
    """Run or re-run scoring on an existing analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis
        from app.services.vendor_analysis.options_analysis_service import (
            OptionsAnalysisService,
        )

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        data = request.get_json() or {}
        if "criteria_weights" in data:
            weights, err = _validate_criteria_weights(data["criteria_weights"])
            if err:
                return jsonify({"error": err}), 400
            analysis.set_criteria_weights(weights)

        service = OptionsAnalysisService()
        service.run_analysis(analysis.id)
        db.session.commit()

        return jsonify({"success": True, "status": analysis.status})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error running scoring: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# POST record decision
# ---------------------------------------------------------------------------
@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/decision", methods=["POST"]
)
@login_required
def api_record_decision(analysis_id):
    """Record a final decision with rationale and approval status."""
    try:
        from app.models.vendor_analysis import AnalysisAuditLog, OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        data = request.get_json()
        decision = data.get("decision")
        rationale = data.get("rationale", "").strip()

        if decision not in ("approved", "rejected", "deferred"):
            return jsonify(
                {"error": "decision must be 'approved', 'rejected', or 'deferred'"}
            ), 400
        if not rationale:
            return jsonify({"error": "rationale is required"}), 400

        if analysis.created_by_id == current_user.id:
            return jsonify({"error": "Cannot approve/reject your own analysis. A different user must review."}), 403

        analysis.approval_status = decision
        analysis.approval_notes = rationale
        analysis.approved_by_id = current_user.id
        analysis.approved_at = datetime.utcnow()

        if decision == "approved":
            selected_vendor_id = data.get("selected_vendor_option_id")
            if selected_vendor_id:
                from app.models.vendor_analysis import VendorOption

                vo = VendorOption.query.filter_by(
                    id=selected_vendor_id, analysis_id=analysis.id
                ).first()
                if vo and vo.technology_stack_id:
                    analysis.recommended_vendor_id = vo.technology_stack_id

                # Link to rationalization: update ReplacementPlans for apps
                # under this capability with Replace/Migrate disposition
                if vo and analysis.capability_id:
                    try:
                        from app.models.application_rationalization import (
                            ApplicationRationalizationScore,
                            ReplacementPlan,
                        )
                        replace_scores = (
                            ApplicationRationalizationScore.query
                            .join(
                                ApplicationRationalizationScore.application,
                            )
                            .filter(
                                ApplicationRationalizationScore.recommended_disposition.in_(
                                    ["replace", "migrate"]
                                ),
                            )
                            .all()
                        )
                        vendor_name = vo.vendor_name or "Approved vendor"
                        for score in replace_scores:
                            plan = ReplacementPlan.query.filter_by(
                                source_app_id=score.application_component_id
                            ).first()
                            if plan and not plan.target_app_name:
                                plan.target_app_name = (
                                    f"{vendor_name} (from analysis: {analysis.name})"
                                )
                    except HTTPException:
                        raise
                    except Exception as link_err:
                        current_app.logger.warning(
                            f"Rationalization linkage skipped: {link_err}"
                        )

        audit = AnalysisAuditLog(
            analysis_id=analysis.id,
            event_type=f"decision_{decision}",
            event_data=json.dumps(
                {"rationale": rationale, "decision": decision}
            ),
            performed_by_id=current_user.id,
            performed_at=datetime.utcnow(),
        )
        db.session.add(audit)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "approval_status": analysis.approval_status,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error recording decision: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendor-analysis/create", methods=["POST"])
@login_required
def api_create_vendor_analysis():
    """Create and run a vendor options analysis."""
    try:
        from app.models.business_capabilities import BusinessCapability
        from app.services.vendor_analysis.options_analysis_service import OptionsAnalysisService

        data = request.get_json()

        # Validate required fields - accept both singular and plural forms
        capability_id = data.get("capability_id")
        capability_ids = data.get("capability_ids")

        if not data.get("name"):
            return jsonify({"error": "Name is required"}), 400

        # Support both singular capability_id and plural capability_ids from frontend
        if not capability_id and capability_ids:
            if isinstance(capability_ids, list) and len(capability_ids) > 0:
                capability_id = capability_ids[0]  # Use first capability for analysis
            else:
                return jsonify({"error": "capability_id or capability_ids is required"}), 400
        elif not capability_id:
            return jsonify({"error": "capability_id or capability_ids is required"}), 400

        capability = db.session.get(BusinessCapability, capability_id)
        if not capability:
            # Capability-first mode sends UnifiedCapability IDs — resolve to BusinessCapability
            try:
                from app.models.unified_capability import UnifiedCapability
                unified_cap = db.session.get(UnifiedCapability, capability_id)
                if unified_cap:
                    # Find matching BusinessCapability by name
                    capability = BusinessCapability.query.filter(
                        BusinessCapability.name == unified_cap.name
                    ).first()
                    if capability:
                        capability_id = capability.id
                    else:
                        # Auto-create a BusinessCapability from the UnifiedCapability
                        capability = BusinessCapability(
                            name=unified_cap.name,
                            description=unified_cap.description or "",
                            category=unified_cap.category or "general",
                        )
                        db.session.add(capability)
                        db.session.flush()
                        capability_id = capability.id
            except HTTPException:
                raise
            except Exception as cap_err:
                current_app.logger.warning(f"Capability resolution failed: {cap_err}")

        if not capability:
            return jsonify({"error": "Capability not found"}), 404

        raw_weights = data.get("criteria_weights")
        if raw_weights:
            validated_weights, err = _validate_criteria_weights(raw_weights)
            if err:
                return jsonify({"error": err}), 400
            raw_weights = validated_weights

        service = OptionsAnalysisService()

        # Create analysis
        analysis = service.create_analysis(
            name=data["name"],
            capability_id=capability_id,
            vendor_org_ids=data.get("vendor_org_ids", []),
            vendor_product_ids=data.get("vendor_product_ids", []),
            created_by=current_user,
            criteria_weights=raw_weights,
            analysis_type=data.get("analysis_type", "standard"),
            tco_years=data.get("tco_years", 5),
            organization_size=data.get("organization_size"),
            industry_vertical=data.get("industry_vertical"),
            deployment_scale=data.get("deployment_scale"),
            user_count_estimate=data.get("user_count_estimate"),
            integration_complexity=data.get("integration_complexity"),
        )

        # Run analysis
        service.run_analysis(analysis.id)

        # Auto-seed requirements from capability children (L2/L3)
        try:
            from app.models.vendor_analysis import RequiredCapability

            children = BusinessCapability.query.filter_by(
                parent_capability_id=capability_id
            ).all()
            importance_map = {
                "critical": "critical",
                "high": "high",
                "medium": "medium",
                "low": "low",
            }
            for child in children:
                imp = importance_map.get(
                    (child.strategic_importance or "").lower(), "medium"
                )
                req = RequiredCapability(
                    analysis_id=analysis.id,
                    capability_name=child.name,
                    capability_description=child.description or "",
                    category="functional",
                    importance=imp,
                    must_have=(imp == "critical"),
                    weight_multiplier=2.0 if imp == "critical" else 1.0,
                )
                db.session.add(req)
        except HTTPException:
            raise
        except Exception as seed_err:
            current_app.logger.warning(
                f"Auto-seed requirements skipped: {seed_err}"
            )

        db.session.commit()

        return jsonify({"success": True, "analysis_id": analysis.id, "status": analysis.status})

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating vendor analysis: {str(e)}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendor-analysis/<int:analysis_id>/comparison", methods=["GET"])
@login_required
def api_get_vendor_comparison(analysis_id):
    """Get vendor comparison matrix data."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        vendors = [
            _serialize_vendor_option(vo, include_extended=True)
            for vo in analysis.vendor_options
        ]

        return jsonify(
            {"vendors": vendors, "analysis_id": analysis.id, "analysis_name": analysis.name}
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendor comparison: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# Requirements CRUD + Fulfillment Matrix
# ---------------------------------------------------------------------------

VALID_IMPORTANCE = frozenset(["critical", "high", "medium", "low"])
VALID_CATEGORIES = frozenset(
    ["functional", "technical", "security", "compliance", "integration", ""]
)
VALID_FULFILLMENT = frozenset(["met", "partial", "not_met"])


def _ensure_fulfillment_column():
    """Add fulfillment_data column to required_capabilities if missing."""
    try:
        from sqlalchemy import inspect as sa_inspect, text

        insp = sa_inspect(db.engine)
        cols = [c["name"] for c in insp.get_columns("required_capabilities")]
        if "fulfillment_data" not in cols:
            db.session.execute(
                text(
                    "ALTER TABLE required_capabilities "
                    "ADD COLUMN fulfillment_data TEXT"
                )
            )
            db.session.commit()
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()


def _serialize_requirement(req, vendor_option_ids=None):
    """Serialize a RequiredCapability to a JSON-safe dict."""
    fulfillment = {}
    if hasattr(req, "fulfillment_data") and req.fulfillment_data:
        try:
            fulfillment = json.loads(req.fulfillment_data)
        except (json.JSONDecodeError, TypeError):
            fulfillment = {}

    # Pad missing vendor IDs with null so the UI can render empty cells
    if vendor_option_ids:
        for vid in vendor_option_ids:
            str_vid = str(vid)
            if str_vid not in fulfillment:
                fulfillment[str_vid] = None

    return {
        "id": req.id,
        "capability_name": req.capability_name,
        "description": req.capability_description,
        "importance": req.importance,
        "must_have": req.must_have or False,
        "category": req.category or "",
        "acceptance_criteria": req.acceptance_criteria or "",
        "weight_multiplier": req.weight_multiplier or 1.0,
        "fulfillment": fulfillment,
    }


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/requirements", methods=["GET"]
)
@login_required
def api_list_requirements(analysis_id):
    """List all requirements for an analysis with fulfillment data."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis, RequiredCapability

        _ensure_fulfillment_column()

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        vendor_ids = [vo.id for vo in analysis.vendor_options]
        requirements = RequiredCapability.query.filter_by(
            analysis_id=analysis_id
        ).order_by(RequiredCapability.id).all()

        return jsonify({
            "success": True,
            "requirements": [
                _serialize_requirement(r, vendor_ids) for r in requirements
            ],
        })
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error listing requirements: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/requirements", methods=["POST"]
)
@login_required
def api_create_requirement(analysis_id):
    """Create a new requirement for an analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis, RequiredCapability

        _ensure_fulfillment_column()

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        cap_name = (data.get("capability_name") or "").strip()
        if not cap_name or len(cap_name) > 256:
            return jsonify({"error": "capability_name is required (max 256 chars)"}), 400

        importance = (data.get("importance") or "medium").lower()
        if importance not in VALID_IMPORTANCE:
            return jsonify({
                "error": f"importance must be one of: {', '.join(sorted(VALID_IMPORTANCE))}"
            }), 400

        category = (data.get("category") or "").lower()
        if category and category not in VALID_CATEGORIES:
            return jsonify({
                "error": f"category must be one of: {', '.join(sorted(c for c in VALID_CATEGORIES if c))}"
            }), 400

        weight_map = {"critical": 2.0, "high": 1.5, "medium": 1.0, "low": 0.5}

        req = RequiredCapability(
            analysis_id=analysis_id,
            capability_name=cap_name,
            capability_description=data.get("description", ""),
            importance=importance,
            must_have=bool(data.get("must_have", False)),
            category=category or None,
            acceptance_criteria=data.get("acceptance_criteria", ""),
            weight_multiplier=float(
                data.get("weight_multiplier", weight_map.get(importance, 1.0))
            ),
        )
        db.session.add(req)
        db.session.commit()

        vendor_ids = [vo.id for vo in analysis.vendor_options]
        return jsonify({
            "success": True,
            "requirement": _serialize_requirement(req, vendor_ids),
        }), 201
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating requirement: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/requirements/<int:req_id>",
    methods=["PATCH"],
)
@login_required
def api_update_requirement(analysis_id, req_id):
    """Update a requirement."""
    try:
        from app.models.vendor_analysis import RequiredCapability

        _ensure_fulfillment_column()

        req = RequiredCapability.query.filter_by(
            id=req_id, analysis_id=analysis_id
        ).first()
        if not req:
            return jsonify({"error": "Requirement not found"}), 404

        denied = _check_analysis_access(req.analysis)
        if denied:
            return denied

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        if "capability_name" in data:
            name = (data["capability_name"] or "").strip()
            if not name or len(name) > 256:
                return jsonify({"error": "capability_name must be 1-256 chars"}), 400
            req.capability_name = name

        if "description" in data:
            req.capability_description = data["description"]

        if "importance" in data:
            imp = (data["importance"] or "").lower()
            if imp not in VALID_IMPORTANCE:
                return jsonify({
                    "error": f"importance must be one of: {', '.join(sorted(VALID_IMPORTANCE))}"
                }), 400
            req.importance = imp

        if "must_have" in data:
            req.must_have = bool(data["must_have"])

        if "category" in data:
            cat = (data["category"] or "").lower()
            if cat and cat not in VALID_CATEGORIES:
                return jsonify({"error": "Invalid category"}), 400
            req.category = cat or None

        if "acceptance_criteria" in data:
            req.acceptance_criteria = data["acceptance_criteria"]

        if "weight_multiplier" in data:
            try:
                wm = float(data["weight_multiplier"])
                if wm < 0.1 or wm > 5.0:
                    return jsonify({"error": "weight_multiplier must be 0.1-5.0"}), 400
                req.weight_multiplier = wm
            except (TypeError, ValueError):
                return jsonify({"error": "weight_multiplier must be a number"}), 400

        db.session.commit()

        vendor_ids = [vo.id for vo in req.analysis.vendor_options]
        return jsonify({
            "success": True,
            "requirement": _serialize_requirement(req, vendor_ids),
        })
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating requirement: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/requirements/<int:req_id>",
    methods=["DELETE"],
)
@login_required
def api_delete_requirement(analysis_id, req_id):
    """Delete a requirement."""
    try:
        from app.models.vendor_analysis import RequiredCapability

        req = RequiredCapability.query.filter_by(
            id=req_id, analysis_id=analysis_id
        ).first()
        if not req:
            return jsonify({"error": "Requirement not found"}), 404

        denied = _check_analysis_access(req.analysis)
        if denied:
            return denied

        db.session.delete(req)
        db.session.commit()
        return jsonify({"success": True})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting requirement: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/requirements/<int:req_id>/fulfillment",
    methods=["POST"],
)
@login_required
def api_set_fulfillment(analysis_id, req_id):
    """Record whether a vendor option meets a requirement."""
    try:
        from app.models.vendor_analysis import RequiredCapability, VendorOption

        _ensure_fulfillment_column()

        req = RequiredCapability.query.filter_by(
            id=req_id, analysis_id=analysis_id
        ).first()
        if not req:
            return jsonify({"error": "Requirement not found"}), 404

        denied = _check_analysis_access(req.analysis)
        if denied:
            return denied

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        vendor_option_id = data.get("vendor_option_id")
        status = data.get("status")

        if not vendor_option_id:
            return jsonify({"error": "vendor_option_id is required"}), 400

        vo = VendorOption.query.filter_by(
            id=vendor_option_id, analysis_id=analysis_id
        ).first()
        if not vo:
            return jsonify({"error": "Vendor option not found in this analysis"}), 404

        if status and status not in VALID_FULFILLMENT:
            return jsonify({
                "error": f"status must be one of: {', '.join(sorted(VALID_FULFILLMENT))}"
            }), 400

        fulfillment = {}
        if hasattr(req, "fulfillment_data") and req.fulfillment_data:
            try:
                fulfillment = json.loads(req.fulfillment_data)
            except (json.JSONDecodeError, TypeError):
                fulfillment = {}

        str_vid = str(vendor_option_id)
        if status:
            fulfillment[str_vid] = status
        else:
            fulfillment.pop(str_vid, None)

        req.fulfillment_data = json.dumps(fulfillment)
        db.session.commit()

        return jsonify({"success": True, "fulfillment": fulfillment})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error setting fulfillment: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendor-analysis/<int:analysis_id>/results", methods=["GET"])
@login_required
def api_get_vendor_results(analysis_id):
    """Get vendor analysis results."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        vendors = [
            _serialize_vendor_option(vo, include_extended=True)
            for vo in sorted(analysis.vendor_options, key=lambda v: v.ranking or 999)
        ]

        winner = analysis.get_winner()
        avg_score = (
            sum(v["total_score"] or 0 for v in vendors) / len(vendors)
            if vendors
            else 0
        )

        return jsonify(
            {
                "vendors": vendors,
                "total_vendors": len(vendors),
                "recommended_vendor": _resolve_vendor_name(winner) if winner else None,
                "average_score": avg_score,
                "confidence": float(analysis.recommendation_confidence)
                if analysis.recommendation_confidence
                else None,
                "analysis_id": analysis.id,
                "analysis_name": analysis.name,
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendor results: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendor-analysis/<int:analysis_id>/export", methods=["GET"])
@login_required
def api_export_vendor_analysis(analysis_id):
    """Export vendor analysis in CSV, PDF, or PPT format."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis
        from app.services.vendor_analysis.export_service import ExportService

        format_type = request.args.get("format", "csv").lower()

        if format_type not in ["csv", "pdf", "ppt", "pptx"]:
            return jsonify({"error": "Invalid format. Use csv, pdf, or ppt"}), 400

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied
        ExportService()

        if format_type == "csv":
            # CSV Export
            output = io.StringIO()
            writer = csv.writer(output)

            # Write header
            writer.writerow(
                [
                    "Rank",
                    "Vendor",
                    "Total Score",
                    "Cost Score",
                    "Capability Score",
                    "Risk Score",
                    "Strategic Fit",
                    "Implementation",
                    "TCO (5yr)",
                    "License Cost (Annual)",
                    "Support Cost (Annual)",
                ]
            )

            # Write vendor data
            for vendor_option in sorted(analysis.vendor_options, key=lambda v: v.ranking or 999):
                vendor_name = _resolve_vendor_name(vendor_option)
                writer.writerow(
                    [
                        vendor_option.ranking or "",
                        vendor_name,
                        vendor_option.total_score or "",
                        vendor_option.cost_score or "",
                        vendor_option.capability_coverage_score or "",
                        vendor_option.risk_score or "",
                        vendor_option.strategic_fit_score or "",
                        vendor_option.implementation_score or "",
                        vendor_option.tco_total or "",
                        vendor_option.license_cost_annual or "",
                        vendor_option.support_cost_annual or "",
                    ]
                )

            output.seek(0)
            csv_bytes = io.BytesIO(output.getvalue().encode("utf-8"))
            csv_bytes.seek(0)

            filename = (
                f'vendor_analysis_{analysis_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            )
            return send_file(
                csv_bytes, mimetype="text/csv", as_attachment=True, download_name=filename
            )

        elif format_type in ["pdf", "ppt", "pptx"]:
            # PDF and PPT export not yet implemented - return 501 with clear message
            return jsonify({
                "error": f"{format_type.upper()} export is not yet available. Please use CSV or Excel format.",
                "available_formats": ["csv", "xlsx"]
            }), 501

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error exporting vendor analysis: {str(e)}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendor-analysis/<int:analysis_id>/export-history", methods=["GET"])
@login_required
def api_get_export_history(analysis_id):
    """Get export history for an analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis
        from app.utils.route_guards import load_entity

        denied = _check_analysis_access(load_entity(OptionsAnalysis, analysis_id))
        if denied:
            return denied
        # For now, return empty array (can be enhanced with export tracking table)
        return jsonify([])
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error getting export history: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/capabilities", methods=["GET"])
@login_required
def api_get_capabilities():
    """Get list of capabilities for dropdown."""
    try:
        from app.models.unified_capability import UnifiedCapability

        capabilities = UnifiedCapability.query.order_by(UnifiedCapability.name).all()

        return jsonify(
            [
                {"id": cap.id, "name": cap.name, "description": cap.description or ""}
                for cap in capabilities
            ]
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting capabilities: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendors/by-capabilities", methods=["POST"])
@login_required
def api_get_vendors_by_capabilities():
    """Get vendors that support specific capabilities.

    The UI sends UnifiedCapability IDs, but VendorProductCapability references
    legacy BusinessCapability IDs.  We bridge the gap using two strategies:
      1. deprecated_in_favor_of_id  (BusinessCapability → UnifiedCapability FK)
      2. Exact name match fallback  (for unmigrated records)
    If neither yields matches the response is an honest empty list.
    """
    try:
        capability_ids = request.json.get("capability_ids", [])

        if not capability_ids:
            return jsonify({"error": "No capability IDs provided"}), 400

        from sqlalchemy import distinct, func

        from app.models.business_capabilities import BusinessCapability
        from app.models.unified_capability import UnifiedCapability
        from app.models.vendor import VendorOrganization, VendorProduct, VendorProductCapability

        # ── Step 1: Resolve UnifiedCapability IDs → legacy BusinessCapability IDs ──
        # Strategy A: Use the deprecated_in_favor_of_id reverse FK
        legacy_ids_via_fk = [
            row[0]
            for row in db.session.query(BusinessCapability.id)
            .filter(BusinessCapability.deprecated_in_favor_of_id.in_(capability_ids))
            .all()
        ]

        # Strategy B: Name-based matching for records without the FK set
        if len(legacy_ids_via_fk) < len(capability_ids):
            # Get unified capability names for the requested IDs
            unified_names = [
                row[0]
                for row in db.session.query(UnifiedCapability.name)
                .filter(UnifiedCapability.id.in_(capability_ids))
                .all()
            ]
            if unified_names:
                legacy_ids_via_name = [
                    row[0]
                    for row in db.session.query(BusinessCapability.id)
                    .filter(
                        BusinessCapability.name.in_(unified_names),
                        ~BusinessCapability.id.in_(legacy_ids_via_fk) if legacy_ids_via_fk else True,
                    )
                    .all()
                ]
            else:
                legacy_ids_via_name = []
        else:
            legacy_ids_via_name = []

        resolved_biz_cap_ids = list(set(legacy_ids_via_fk + legacy_ids_via_name))

        if not resolved_biz_cap_ids:
            # No mappable capabilities — return empty honestly
            current_app.logger.info(
                "No BusinessCapability records map to UnifiedCapability IDs %s. "
                "Vendor discovery returned 0 results.",
                capability_ids,
            )
            return jsonify([])

        # ── Step 2: Query vendors by resolved BusinessCapability IDs ──
        vendor_coverage = (
            db.session.query(
                VendorOrganization.id,
                VendorOrganization.name,
                func.count(distinct(VendorProductCapability.business_capability_id)).label(
                    "supported_count"
                ),
            )
            .join(VendorProduct, VendorOrganization.id == VendorProduct.vendor_organization_id)
            .join(VendorProductCapability, VendorProduct.id == VendorProductCapability.vendor_product_id)
            .filter(VendorProductCapability.business_capability_id.in_(resolved_biz_cap_ids))
            .group_by(VendorOrganization.id, VendorOrganization.name)
            .order_by(
                func.count(distinct(VendorProductCapability.business_capability_id)).desc()
            )
            .limit(30)
            .all()
        )

        if not vendor_coverage:
            return jsonify([])

        # ── Step 3: Batch-prefetch product counts (avoid N+1) ──
        coverage_vendor_ids = [vid for vid, _, _ in vendor_coverage]
        product_counts = dict(
            db.session.query(
                VendorProduct.vendor_organization_id,
                func.count(VendorProduct.id),
            )
            .filter(VendorProduct.vendor_organization_id.in_(coverage_vendor_ids))
            .group_by(VendorProduct.vendor_organization_id)
            .all()
        )

        vendor_list = []
        for vendor_id, vendor_name, supported_count in vendor_coverage:
            vendor_list.append(
                {
                    "id": vendor_id,
                    "name": vendor_name,
                    "supported_capabilities": supported_count,
                    "total_capabilities": len(capability_ids),
                    "total_products": product_counts.get(vendor_id, 0),
                }
            )

        return jsonify(vendor_list)

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendors by capabilities: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/business-domains", methods=["GET"])
@login_required
def api_get_business_domains():
    """
    Get list of business domains for EA analysis.

    ---
    tags:
      - Dashboard
      - Business Domains
    responses:
      200:
        description: List of business domains
        schema:
          type: array
          items:
            type: object
            properties:
              id:
                type: integer
                description: Domain ID
              name:
                type: string
                description: Domain name
              description:
                type: string
                description: Domain description
      500:
        description: Internal server error
    """
    try:
        from app.models.unified_capability import BusinessDomain

        domains = BusinessDomain.query.order_by(BusinessDomain.name).all()

        # Auto-seed business domains on first access if table is empty
        if not domains:
            current_app.logger.info("BusinessDomain table empty — auto-seeding defaults")
            try:
                from app.services.seed_management_service import SeedManagementService
                svc = SeedManagementService()
                svc._seed_business_domains()
                domains = BusinessDomain.query.order_by(BusinessDomain.name).all()
                current_app.logger.info(f"Auto-seeded {len(domains)} business domains")
            except HTTPException:
                raise
            except Exception as seed_err:
                current_app.logger.warning(f"Auto-seed failed: {seed_err}")

        result = []
        for domain in domains:
            domain_data = {
                "id": domain.id,
                "name": domain.name,
                "code": domain.code,
                "description": domain.description or "",
            }
            result.append(domain_data)

        return jsonify(result)

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting business domains: {str(e)}")
        import traceback

        current_app.logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/value-streams", methods=["GET"])
@login_required
def api_get_value_streams():
    """Get value streams for a specific business domain."""
    try:
        domain_id = request.args.get("domain_id", type=int)
        domain_code = request.args.get("domain_code", type=str)
        current_app.logger.info(
            f"Value streams requested - domain_id: {domain_id}, domain_code: {domain_code}"
        )

        if not domain_id and not domain_code:
            # No domain filter chosen yet (e.g. the page's initial load,
            # before the caller narrows by domain) — this is the honest
            # empty state, not a lookup failure. It used to fall through to
            # the "domain not found" branch below and answer 404 for a
            # request that named no domain at all, which is what every one
            # of this page's own initial loads did.
            current_app.logger.info("Value streams requested with no domain filter — returning []")
            return jsonify([])

        from app.models.unified_capability import BusinessDomain

        # Try to find domain by ID first, then by code
        domain = None
        if domain_id:
            domain = db.session.get(BusinessDomain, domain_id)

        if not domain and domain_code:
            domain = BusinessDomain.query.filter_by(code=domain_code).first()

        if not domain:
            current_app.logger.error(
                f"Domain not found - tried ID: {domain_id}, Code: {domain_code}"
            )
            return (
                jsonify(
                    {
                        "error": f"Business domain not found. Tried ID: {domain_id}, Code: {domain_code}"
                    }
                ),
                404,
            )

        current_app.logger.info(f"Found domain: {domain.code} - {domain.name}")

        # For now, return standard value streams based on domain
        # In a full implementation, this would query actual value stream data
        value_streams = get_standard_value_streams(domain.code)
        current_app.logger.info(f"Returning {len(value_streams)} value streams for {domain.code}")

        return jsonify(value_streams)

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting value streams: {str(e)}")
        import traceback

        current_app.logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/related-capabilities", methods=["POST"])
@login_required
def api_get_related_capabilities():
    """Get related capabilities at a specific level."""
    try:
        data = request.get_json()
        current_app.logger.info(f"Related capabilities request: {data}")

        capability_ids = data.get("capability_ids", [])
        target_level = data.get("target_level")

        # Convert target_level to int if present
        if target_level is not None:
            try:
                target_level = int(target_level)
            except (ValueError, TypeError):
                return jsonify({"error": "Target level must be a valid integer"}), 400

        current_app.logger.info(f"Capability IDs: {capability_ids}, Target Level: {target_level}")

        if not capability_ids or not target_level:
            return jsonify({"error": "Capability IDs and target level required"}), 400

        from sqlalchemy.orm import joinedload

        from app.models.unified_capability import UnifiedCapability

        # Coerce incoming IDs to int (frontend may send strings)
        parent_ids = [int(i) for i in capability_ids if str(i).lstrip("-").isdigit()]

        # Filter by both level AND parent to respect the capability hierarchy
        base_query = UnifiedCapability.query.filter(
            UnifiedCapability.level == target_level,
            UnifiedCapability.parent_capability_id.in_(parent_ids),
        )
        current_app.logger.info(f"Base query created for level {target_level}, parents {parent_ids}")

        # Execute query with domain relationship
        capabilities = (
            base_query.options(joinedload(UnifiedCapability.domain))
            .order_by(UnifiedCapability.name)
            .all()
        )

        current_app.logger.info(f"Found {len(capabilities)} capabilities at level {target_level}")

        result = []
        for cap in capabilities:
            try:
                domain_name = "Unknown"
                if cap.domain:
                    domain_name = cap.domain.name if cap.domain.name else "Unknown"

                result.append(
                    {
                        "id": cap.id,
                        "name": cap.name,
                        "description": cap.description or "",
                        "level": cap.level,
                        "domain": domain_name,
                        "code": cap.code or "",
                        "specialization_type": getattr(cap, "specialization_type", "") or "",
                    }
                )
            except HTTPException:
                raise
            except Exception as cap_error:
                current_app.logger.error(f"Error processing capability {cap.id}: {str(cap_error)}")
                continue

        current_app.logger.info(f"Returning {len(result)} capabilities")
        return jsonify(result)

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting related capabilities: {str(e)}")
        import traceback

        current_app.logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": "An internal error occurred"}), 500


def get_standard_value_streams(domain_code):
    """Get standard value streams for a business domain."""
    # Standard value streams mapped to business domains
    value_stream_map = {
        "CUST": [
            {"id": "vs1", "name": "Customer Acquisition"},
            {"id": "vs2", "name": "Customer Onboarding"},
            {"id": "vs3", "name": "Customer Service"},
            {"id": "vs4", "name": "Customer Retention"},
        ],
        "PROD": [
            {"id": "vs5", "name": "Product Development"},
            {"id": "vs6", "name": "Product Manufacturing"},
            {"id": "vs7", "name": "Quality Management"},
            {"id": "vs8", "name": "Product Delivery"},
        ],
        "OPER": [
            {"id": "vs9", "name": "Supply Chain Management"},
            {"id": "vs10", "name": "Operations Planning"},
            {"id": "vs11", "name": "Process Optimization"},
            {"id": "vs12", "name": "Resource Management"},
        ],
        "TECH": [
            {"id": "vs13", "name": "Technology Strategy"},
            {"id": "vs14", "name": "Digital Transformation"},
            {"id": "vs15", "name": "IT Operations"},
            {"id": "vs16", "name": "Innovation Management"},
        ],
        "DATA": [
            {"id": "vs17", "name": "Data Strategy"},
            {"id": "vs18", "name": "Analytics & Insights"},
            {"id": "vs19", "name": "Data Governance"},
            {"id": "vs20", "name": "Information Management"},
        ],
        "FIN": [
            {"id": "vs21", "name": "Financial Planning"},
            {"id": "vs22", "name": "Risk Management"},
            {"id": "vs23", "name": "Compliance Management"},
            {"id": "vs24", "name": "Investment Management"},
        ],
        "RISK": [
            {"id": "vs25", "name": "Risk Assessment"},
            {"id": "vs26", "name": "Compliance Monitoring"},
            {"id": "vs27", "name": "Internal Controls"},
            {"id": "vs28", "name": "Audit Management"},
        ],
    }

    return value_stream_map.get(
        domain_code,
        [
            {"id": "vs_default1", "name": "Strategic Planning"},
            {"id": "vs_default2", "name": "Operational Excellence"},
            {"id": "vs_default3", "name": "Performance Management"},
            {"id": "vs_default4", "name": "Continuous Improvement"},
        ],
    )


@application_mgmt.route("/api/unified-capabilities", methods=["GET"])
@login_required
def api_get_unified_capabilities():
    """Get list of unified capabilities for dropdown."""
    try:
        from sqlalchemy.orm import joinedload

        from app.models.unified_capability import UnifiedCapability

        # Get level filter from query parameter
        level_filter = request.args.get("level", type=int)
        spec_type = request.args.get("specialization_type")

        # Start with base query
        query = UnifiedCapability.query

        # Apply level filter if specified
        if level_filter is not None:
            query = query.filter(UnifiedCapability.level == level_filter)

        # Apply specialization type filter if specified
        if spec_type:
            query = query.filter(UnifiedCapability.specialization_type == spec_type)

        # Execute query with domain relationship
        capabilities = (
            query.options(joinedload(UnifiedCapability.domain))
            .order_by(UnifiedCapability.level, UnifiedCapability.name)
            .all()
        )

        # Fallback to business_capability table when unified_capabilities is empty
        if not capabilities:
            from app.models.business_capabilities import BusinessCapability
            bc_query = BusinessCapability.query
            if level_filter is not None:
                bc_query = bc_query.filter(BusinessCapability.level == level_filter)
            capabilities_bc = (
                bc_query.order_by(BusinessCapability.level, BusinessCapability.name).all()
            )
            return jsonify(
                [
                    {
                        "id": cap.id,
                        "name": cap.name,
                        "description": cap.description or "",
                        "level": cap.level,
                        "domain": cap.business_domain or "Unknown",
                        "code": cap.code or "",
                        "category": cap.category or "",
                        "specialization_type": "BUSINESS",
                        "archimate_layer": "strategy",
                        "archimate_element_type": "BusinessCapability",
                    }
                    for cap in capabilities_bc
                ]
            )

        return jsonify(
            [
                {
                    "id": cap.id,
                    "name": cap.name,
                    "description": cap.description or "",
                    "level": cap.level,
                    "domain": cap.domain.name if cap.domain else "Unknown",
                    "code": cap.code or "",
                    "category": cap.category or "",
                    "specialization_type": cap.specialization_type or "",
                    "archimate_layer": getattr(cap, "archimate_layer", None) or "",
                    "archimate_element_type": (
                        "BusinessCapability"
                        if (getattr(cap, "specialization_type", None) or "").upper() == "BUSINESS"
                        else "ApplicationCapability"
                        if (getattr(cap, "specialization_type", None) or "").upper() == "APPLICATION"
                        else "TechnologyCapability"
                        if (getattr(cap, "specialization_type", None) or "").upper() == "TECHNICAL"
                        else "Capability"
                    ),
                }
                for cap in capabilities
            ]
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting capabilities: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/capability-levels", methods=["GET"])
@login_required
def api_get_capability_levels():
    """Get available capability levels."""
    try:
        from app.models.unified_capability import UnifiedCapability

        # Get distinct levels from capabilities
        levels = (
            db.session.query(UnifiedCapability.level)
            .distinct()
            .order_by(UnifiedCapability.level)
            .all()
        )

        level_info = []
        for level_tuple in levels:
            level = level_tuple[0]
            if level == 0:
                name = "Business Domains (L0)"
            elif level == 1:
                name = "Strategic Capabilities (L1)"
            elif level == 2:
                name = "Tactical Capabilities (L2)"
            elif level == 3:
                name = "Operational Capabilities (L3)"
            else:
                name = f"Level {level}"

            level_info.append({"level": level, "name": name})

        return jsonify(level_info)

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting capability levels: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendors/organizations", methods=["GET"])
@login_required
def api_get_vendor_organizations():
    """Get list of vendor organizations for dropdown."""
    try:
        from app.models.vendor.vendor_organization import VendorOrganization

        vendors = VendorOrganization.query.order_by(VendorOrganization.name).all()

        return jsonify(
            [
                {
                    "id": vendor.id,
                    "name": vendor.name,
                    "display_name": vendor.display_name or vendor.name,
                }
                for vendor in vendors
            ]
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendor organizations: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendors/products", methods=["GET"])
@login_required
def api_get_vendor_products():
    """Get list of vendor products for dropdown."""
    try:
        from sqlalchemy.orm import joinedload

        from app.models.vendor.vendor_organization import VendorProduct

        products = (
            VendorProduct.query.options(joinedload(VendorProduct.vendor_organization))
            .order_by(VendorProduct.name)
            .all()
        )

        return jsonify(
            [
                {
                    "id": product.id,
                    "name": product.name,
                    "vendor_organization": {
                        "id": product.vendor_organization.id
                        if product.vendor_organization
                        else None,
                        "name": product.vendor_organization.name
                        if product.vendor_organization
                        else "Unknown",
                    },
                    "vendor_name": product.vendor_organization.name
                    if product.vendor_organization
                    else "Unknown",
                }
                for product in products
            ]
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendor products: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendor-analysis/<int:analysis_id>/provenance", methods=["GET"])
@login_required
def api_get_vendor_provenance(analysis_id):
    """Get provenance data for vendor analysis results."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        results = []
        for vo in analysis.vendor_options:
            criteria_scores = {}
            if vo.cost_score is not None:
                criteria_scores["cost"] = float(vo.cost_score)
            if vo.capability_coverage_score is not None:
                criteria_scores["capability_coverage"] = float(vo.capability_coverage_score)
            if vo.risk_score is not None:
                criteria_scores["risk"] = float(vo.risk_score)
            if vo.strategic_fit_score is not None:
                criteria_scores["strategic_fit"] = float(vo.strategic_fit_score)
            if vo.implementation_score is not None:
                criteria_scores["implementation"] = float(vo.implementation_score)

            results.append({
                "option_id": vo.id,
                "option_name": _resolve_vendor_name(vo),
                "confidence_score": (
                    min(float(vo.total_score) / 100.0, 1.0)
                    if vo.total_score
                    else None
                ),
                "criteria_scores": criteria_scores,
                "ranking": vo.ranking,
                "tco_total": float(vo.tco_total) if vo.tco_total else None,
            })

        return jsonify({
            "success": True,
            "data": {
                "analysis_id": analysis.id,
                "analysis_name": analysis.name,
                "results": results,
            },
        })

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendor provenance: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# Scenario Comparison API
# ---------------------------------------------------------------------------

def _compute_scenario_rankings(vendor_options, weights):
    """Compute vendor rankings under a given set of criteria weights.

    Returns (rankings_list, winner_option_id, winner_score) where rankings_list
    is a JSON-serialisable list sorted by score descending.
    """
    scored = []
    for vo in vendor_options:
        total = 0.0
        total += (vo.cost_score or 0.0) * weights.get("cost", 0.25)
        total += (vo.capability_coverage_score or 0.0) * weights.get("capability_coverage", 0.25)
        total += (vo.risk_score or 0.0) * weights.get("risk", 0.20)
        total += (vo.strategic_fit_score or 0.0) * weights.get("strategic_fit", 0.15)
        total += (vo.implementation_score or 0.0) * weights.get("implementation", 0.15)
        total = round(total, 2)
        scored.append({
            "vendor_option_id": vo.id,
            "vendor_name": _resolve_vendor_name(vo),
            "total_score": total,
        })
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    for idx, entry in enumerate(scored, 1):
        entry["ranking"] = idx

    winner = scored[0] if scored else None
    winner_id = winner["vendor_option_id"] if winner else None
    winner_score = winner["total_score"] if winner else None
    return scored, winner_id, winner_score


def _serialize_scenario(scenario):
    """Serialize an AnalysisScenario to a JSON-safe dict."""
    return {
        "id": scenario.id,
        "scenario_name": scenario.scenario_name,
        "description": scenario.description,
        "is_baseline": scenario.is_baseline,
        "criteria_weights": scenario.get_criteria_weights(),
        "vendor_rankings": scenario.get_vendor_rankings(),
        "recommended_vendor_id": scenario.recommended_vendor_id,
        "winner_score": float(scenario.scenario_winner_score)
        if scenario.scenario_winner_score
        else None,
        "cost_delta": float(scenario.cost_delta) if scenario.cost_delta else None,
        "created_at": scenario.created_at.isoformat() if scenario.created_at else None,
    }


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/scenarios", methods=["GET"]
)
@login_required
def api_list_scenarios(analysis_id):
    """List all scenarios for an analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        scenarios = [_serialize_scenario(s) for s in analysis.scenarios]
        return jsonify({"success": True, "scenarios": scenarios})
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error listing scenarios: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/scenarios", methods=["POST"]
)
@login_required
def api_create_scenario(analysis_id):
    """Create a new scenario with custom criteria weights and compute rankings."""
    try:
        from app.models.vendor_analysis import AnalysisScenario, OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        data = request.get_json()
        if not data or not data.get("scenario_name"):
            return jsonify({"success": False, "error": "scenario_name is required"}), 400

        raw_weights = data.get("criteria_weights")
        if not raw_weights:
            return jsonify({"success": False, "error": "criteria_weights is required"}), 400

        weights, err = _validate_criteria_weights(raw_weights)
        if err:
            return jsonify({"success": False, "error": err}), 400

        rankings, winner_id, winner_score = _compute_scenario_rankings(
            analysis.vendor_options, weights
        )

        scenario = AnalysisScenario(
            analysis_id=analysis.id,
            scenario_name=data["scenario_name"].strip()[:128],
            description=data.get("description", ""),
            criteria_weights=json.dumps(weights),
            vendor_rankings=json.dumps(rankings),
            recommended_vendor_id=winner_id,
            scenario_winner_score=winner_score,
            created_by_id=current_user.id,
        )
        db.session.add(scenario)
        db.session.commit()

        return jsonify({"success": True, "scenario": _serialize_scenario(scenario)}), 201
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating scenario: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/scenarios/<int:scenario_id>",
    methods=["DELETE"],
)
@login_required
def api_delete_scenario(analysis_id, scenario_id):
    """Delete a scenario."""
    try:
        from app.models.vendor_analysis import AnalysisScenario

        scenario = AnalysisScenario.query.filter_by(
            id=scenario_id, analysis_id=analysis_id
        ).first()
        if not scenario:
            return jsonify({"success": False, "error": "Scenario not found"}), 404
        denied = _check_analysis_access(scenario.analysis)
        if denied:
            return denied

        db.session.delete(scenario)
        db.session.commit()
        return jsonify({"success": True})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting scenario: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/scenarios/save-current",
    methods=["POST"],
)
@login_required
def api_save_current_as_scenario(analysis_id):
    """Save the analysis's current weights + scores as a named scenario snapshot."""
    try:
        from app.models.vendor_analysis import AnalysisScenario, OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        denied = _check_analysis_access(analysis)
        if denied:
            return denied

        data = request.get_json()
        if not data or not data.get("scenario_name"):
            return jsonify({"success": False, "error": "scenario_name is required"}), 400

        weights = analysis.get_criteria_weights()
        rankings, winner_id, winner_score = _compute_scenario_rankings(
            analysis.vendor_options, weights
        )

        # Check if this is the first scenario — mark it as baseline
        existing_count = AnalysisScenario.query.filter_by(analysis_id=analysis.id).count()
        is_baseline = existing_count == 0

        scenario = AnalysisScenario(
            analysis_id=analysis.id,
            scenario_name=data["scenario_name"].strip()[:128],
            description=data.get("description", "Snapshot of current analysis weights"),
            is_baseline=is_baseline,
            criteria_weights=json.dumps(weights),
            vendor_rankings=json.dumps(rankings),
            recommended_vendor_id=winner_id,
            scenario_winner_score=winner_score,
            created_by_id=current_user.id,
        )
        db.session.add(scenario)
        db.session.commit()

        return jsonify({"success": True, "scenario": _serialize_scenario(scenario)}), 201
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving current scenario: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# Stakeholder multi-rater
# ---------------------------------------------------------------------------

def _serialize_stakeholder(si):
    """Serialize a StakeholderInput to a JSON-safe dict."""
    return {
        "id": si.id,
        "stakeholder_id": si.stakeholder_id,
        "stakeholder_name": si.stakeholder.email if si.stakeholder else None,
        "stakeholder_role": si.stakeholder_role,
        "department": si.department,
        "custom_weights": si.get_custom_weights(),
        "vendor_scores": si.get_vendor_scores(),
        "preferred_vendor_id": si.preferred_vendor_id,
        "comments": si.comments,
        "is_complete": si.is_complete,
        "submitted_at": si.submitted_at.isoformat() if si.submitted_at else None,
    }


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/stakeholders", methods=["GET"]
)
@login_required
def api_list_stakeholders(analysis_id):
    """List stakeholder inputs for an analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        if not analysis:
            return jsonify({"error": "Analysis not found"}), 404

        stakeholders = [_serialize_stakeholder(si) for si in analysis.stakeholder_inputs]
        return jsonify({"success": True, "stakeholders": stakeholders})
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error listing stakeholders: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/stakeholders", methods=["POST"]
)
@login_required
def api_invite_stakeholder(analysis_id):
    """Invite a stakeholder to provide input on an analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis, StakeholderInput

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        if not analysis:
            return jsonify({"error": "Analysis not found"}), 404

        data = request.get_json()
        stakeholder_id = data.get("stakeholder_id")
        role = data.get("stakeholder_role", "").strip()

        if not stakeholder_id or not role:
            return jsonify({"error": "stakeholder_id and stakeholder_role are required"}), 400

        valid_roles = {"IT", "Finance", "Business", "Legal", "Security", "Executive"}
        if role not in valid_roles:
            return jsonify({"error": f"role must be one of: {', '.join(sorted(valid_roles))}"}), 400

        # Check not already invited
        existing = StakeholderInput.query.filter_by(
            analysis_id=analysis_id, stakeholder_id=stakeholder_id
        ).first()
        if existing:
            return jsonify({"error": "Stakeholder already invited"}), 409

        si = StakeholderInput(
            analysis_id=analysis_id,
            stakeholder_id=stakeholder_id,
            stakeholder_role=role,
            department=data.get("department"),
            invitation_sent_at=datetime.utcnow(),
        )
        db.session.add(si)
        db.session.commit()

        return jsonify({"success": True, "stakeholder": _serialize_stakeholder(si)}), 201
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error inviting stakeholder: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/stakeholders/<int:input_id>/scores",
    methods=["PATCH"],
)
@login_required
def api_submit_stakeholder_scores(analysis_id, input_id):
    """Submit or update stakeholder scores for vendors."""
    try:
        from app.models.vendor_analysis import StakeholderInput

        si = StakeholderInput.query.filter_by(
            id=input_id, analysis_id=analysis_id
        ).first()
        if not si:
            return jsonify({"error": "Stakeholder input not found"}), 404

        # Only the stakeholder themselves (or an admin of the active
        # organisation -- see _is_active_org_admin) can submit.
        if si.stakeholder_id != current_user.id and not _is_active_org_admin(current_user):
            return jsonify({"error": "Only the invited stakeholder can submit scores"}), 403

        data = request.get_json()

        # Vendor scores: {vendor_option_id: {cost: 1-10, risk: 1-10, ...}}
        if "vendor_scores" in data:
            si.vendor_scores = json.dumps(data["vendor_scores"])

        if "custom_weights" in data:
            si.custom_weights = json.dumps(data["custom_weights"])

        if "preferred_vendor_id" in data:
            si.preferred_vendor_id = data["preferred_vendor_id"]

        if "comments" in data:
            si.comments = data["comments"]

        if "concerns" in data:
            si.concerns = json.dumps(data["concerns"]) if isinstance(data["concerns"], list) else data["concerns"]

        si.submitted_at = datetime.utcnow()
        si.is_complete = True
        si.invitation_accepted_at = si.invitation_accepted_at or datetime.utcnow()

        db.session.commit()

        return jsonify({"success": True, "stakeholder": _serialize_stakeholder(si)})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error submitting stakeholder scores: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/stakeholders/consensus", methods=["GET"]
)
@login_required
def api_get_stakeholder_consensus(analysis_id):
    """Get aggregated stakeholder consensus for an analysis."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = db.session.get(OptionsAnalysis, analysis_id)
        if not analysis:
            return jsonify({"error": "Analysis not found"}), 404

        completed = [si for si in analysis.stakeholder_inputs if si.is_complete]
        total_invited = len(analysis.stakeholder_inputs)

        # Aggregate vendor scores across stakeholders
        vendor_aggregates = {}  # vendor_id -> {dimension -> [scores]}
        vendor_preferences = {}  # vendor_id -> count

        for si in completed:
            scores = si.get_vendor_scores() or {}
            for vid_str, dimensions in scores.items():
                vid = str(vid_str)
                if vid not in vendor_aggregates:
                    vendor_aggregates[vid] = {}
                for dim, score in dimensions.items():
                    if dim == "notes":
                        continue
                    if dim not in vendor_aggregates[vid]:
                        vendor_aggregates[vid][dim] = []
                    try:
                        vendor_aggregates[vid][dim].append(float(score))
                    except (ValueError, TypeError):
                        pass

            if si.preferred_vendor_id:
                vid = str(si.preferred_vendor_id)
                vendor_preferences[vid] = vendor_preferences.get(vid, 0) + 1

        # Compute averages
        consensus = {}
        for vid, dims in vendor_aggregates.items():
            consensus[vid] = {
                dim: round(sum(scores) / len(scores), 1)
                for dim, scores in dims.items()
                if scores
            }

        return jsonify({
            "success": True,
            "total_invited": total_invited,
            "total_completed": len(completed),
            "completion_rate": round(len(completed) / total_invited * 100, 1) if total_invited else 0,
            "vendor_consensus": consensus,
            "vendor_preferences": vendor_preferences,
        })
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Error computing consensus: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/vendor-analysis/<int:analysis_id>/stakeholders/<int:input_id>",
    methods=["DELETE"],
)
@login_required
def api_remove_stakeholder(analysis_id, input_id):
    """Remove a stakeholder from an analysis (analysis owner or admin only)."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis, StakeholderInput

        # The organisation fence arrives with OptionsAnalysis's tenant column.
        # tenant-scoping-ok: access is decided by _check_analysis_access (owner or admin) on the next line
        denied = _check_analysis_access(db.session.get(OptionsAnalysis, analysis_id))
        if denied:
            return denied

        si = StakeholderInput.query.filter_by(
            id=input_id, analysis_id=analysis_id
        ).first()
        if not si:
            return jsonify({"error": "Stakeholder input not found"}), 404

        db.session.delete(si)
        db.session.commit()

        return jsonify({"success": True})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error removing stakeholder: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500

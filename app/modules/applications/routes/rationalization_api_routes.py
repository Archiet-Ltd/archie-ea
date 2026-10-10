"""Rationalization, duplicate detection, element CRUD, and template API routes."""

import json
import logging
from datetime import datetime
from decimal import Decimal

from flask import current_app, jsonify, render_template, render_template_string, request
from flask_login import current_user, login_required

from app import db
from app.decorators import audit_log, require_roles
from app.middleware.tenant_decorators import platform_admin_required
from app.models.application_portfolio import ApplicationComponent
from app.services.application_cost_accessor import set_annual_cost
from app.services.rate_limiter import rate_limit

from . import unified_applications_bp
from app.utils.pagination import safe_int_arg
from app.utils.route_guards import require_entity
from app.utils.tenant_users import escape_like_literal

logger = logging.getLogger(__name__)


# ── RAT-114: Audit trail helper ──────────────────────────────────────────


def _log_rationalization_audit(
    app_id,
    score_id,
    action,
    actor,
    before_state=None,
    after_state=None,
    details=None,
    actor_type="user",
):
    """RAT-114: Create an audit trail entry for a rationalization event.

    Adds the entry to the current session but does NOT commit — the caller's
    transaction is responsible for the commit so that the audit entry and the
    state change are atomic.
    """
    from app.models.application_rationalization import RationalizationAuditEntry

    try:
        entry = RationalizationAuditEntry(
            application_id=app_id,
            score_id=score_id,
            action=action,
            actor=actor,
            actor_type=actor_type,
            before_state=before_state,
            after_state=after_state,
            # details is a TEXT column. A caller passing a dict raised
            # "can't adapt type 'dict'" at COMMIT -- outside the caller's own
            # try/except, so the whole request 500ed rather than losing just
            # the audit entry. Serialise here so no caller can reintroduce it.
            details=(
                details
                if details is None or isinstance(details, str)
                else json.dumps(details, default=str, sort_keys=True)
            ),
        )
        db.session.add(entry)
    except Exception as exc:
        logger.error(
            "Failed to create audit entry for app %s action %s: %s",
            app_id,
            action,
            exc,
        )


# ── RATA-013: Auto-create consolidation entry on group/app approval ─────


def _auto_create_consolidation_for_app(app_id, score, actor_name):
    """RATA-013: When an app is approved, auto-create a ConsolidationListEntry
    if one doesn't already exist for this app."""
    try:
        from app.models.consolidation_list import ConsolidationListEntry

        existing = ConsolidationListEntry.query.filter(
            ConsolidationListEntry.application_id == app_id
        ).first()
        if existing:
            return  # Already has a consolidation entry

        disposition = getattr(score, "disposition_action", None)
        # Only auto-create for actionable dispositions
        if disposition not in ("retire", "replace", "consolidate", "migrate", "rehost", "replatform", "refactor"):
            return

        savings = float(getattr(score, "estimated_annual_savings", 0) or 0)
        entry = ConsolidationListEntry(
            application_id=app_id,
            recommended_action=disposition,
            estimated_savings=savings,
            status="proposed",
            priority="high" if disposition in ("retire", "replace", "consolidate") else "medium",
            notes=f"Auto-created on approval by {actor_name}",
        )
        db.session.add(entry)

        _log_rationalization_audit(
            app_id=app_id,
            score_id=getattr(score, "id", None),
            action="auto_consolidation_created",
            actor=actor_name,
            after_state={"disposition": disposition, "estimated_savings": savings},
            details=(
                "Auto-created consolidation entry: %s (estimated annual savings %.2f)"
                % (disposition, savings)
            ),
        )
    except Exception as exc:
        logger.error("RATA-013 auto-consolidation failed for app %s: %s", app_id, exc)


# ── Rationalization dashboard & duplicate detection ──────────────────────


@unified_applications_bp.route("/rationalization")
@login_required
def rationalization_dashboard():
    """
    Unified Application Rationalization Dashboard.

    Provides:
    - Duplicate detection with multiple strategies
    - Consolidation workflow
    - Analysis history
    """
    try:
        from config import CurrencyConfig

        currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "£")
    except Exception:
        currency_symbol = "£"

    try:
        from app.models.unified_duplicate_detection import UnifiedDuplicateGroup  # noqa: F401
        from app.services.unified_duplicate_detection_service import (
            UnifiedDuplicateDetectionService,
        )

        service = UnifiedDuplicateDetectionService()

        # Get statistics
        total_apps = ApplicationComponent.query.count()
        groups = service.get_duplicate_groups("simple", include_applications=True)
        runs = service.get_detection_runs("simple")

        # Calculate stats
        total_groups = len(groups)
        pending_groups = len([g for g in groups if g.get("status") == "pending"])
        resolved_groups = len(
            [g for g in groups if g.get("status") in ["resolved", "approved"]]
        )
        estimated_savings = sum(g.get("estimated_savings", 0) for g in groups)

        # RATA-005: Get latest detection run for metadata display
        from app.models.unified_duplicate_detection import UnifiedDetectionRun
        latest_run = UnifiedDetectionRun.query.order_by(
            UnifiedDetectionRun.created_at.desc()
        ).first()

        # Pipeline counts for workflow stepper
        from sqlalchemy import func, or_

        from app.models.consolidation_list import ConsolidationListEntry
        from app.models.application_rationalization import ApplicationRationalizationScore

        consolidation_count = ConsolidationListEntry.query.count()
        time_scored_count = ApplicationRationalizationScore.query.count()
        roadmap_count = ConsolidationListEntry.query.filter(
            or_(
                ConsolidationListEntry.recommended_action == "add_to_roadmap",
                ConsolidationListEntry.roadmap_item_id.isnot(None),
            )
        ).count()

        # Combine duplicate group savings + consolidation list savings
        consolidation_savings = db.session.query(
            func.coalesce(func.sum(ConsolidationListEntry.estimated_savings), 0)
        ).scalar() or 0
        total_savings = estimated_savings + float(consolidation_savings)

        stats = {
            "total_applications": total_apps,
            "duplicate_groups": total_groups,
            "total_groups": total_groups,
            "pending_groups": pending_groups,
            "resolved_groups": resolved_groups,
            "estimated_savings": total_savings,
            "consolidation_count": consolidation_count,
            "time_scored_count": time_scored_count,
            "roadmap_count": roadmap_count,
        }

        # H7: the pipeline stepper renders every stage as count/total, and every
        # 'total' here is meant to be >= the count next to it (a subset of the
        # portfolio, or of the previous stage). "Plan: 2/0" happened because
        # consolidation_count (ConsolidationListEntry) and time_scored_count
        # (ApplicationRationalizationScore) are two independent tables with no
        # subset relationship, so nothing enforced that invariant. The template
        # was fixed to share total_apps as every stage's denominator, but log
        # loudly if a genuine data inconsistency would still violate it, rather
        # than silently rendering (or silently clamping) a fraction that lies.
        for _label, _count in (
            ("consolidation_count", consolidation_count),
            ("time_scored_count", time_scored_count),
            ("roadmap_count", roadmap_count),
        ):
            if _count > total_apps:
                current_app.logger.warning(
                    "RATIONALIZATION_STAGE_INVARIANT_VIOLATED: %s=%d exceeds "
                    "total_applications=%d -- pipeline stepper percentages for "
                    "this org are not trustworthy until this is investigated",
                    _label, _count, total_apps,
                )

        # RAT-001: Build data quality report for "What to do next" panel
        data_quality = _build_data_quality_report(total_apps)

        # Count insufficient_evidence dispositions
        insufficient_count = ApplicationRationalizationScore.query.filter(
            ApplicationRationalizationScore.disposition_action == "insufficient_evidence"
        ).count() if time_scored_count > 0 else 0

        return render_template(
            "applications/rationalization/dashboard.html",
            stats=stats,
            groups=groups,
            runs=runs[:10] if runs else [],
            latest_run=latest_run,
            currency_symbol=currency_symbol,
            active_tab="dashboard",
            data_quality=data_quality,
            insufficient_count=insufficient_count,
        )
    except Exception as e:
        current_app.logger.exception("Error loading rationalization dashboard: %s", e)
        try:
            db.session.rollback()
        except Exception as exc:
            logger.debug("suppressed error in rationalization_dashboard (app/modules/applications/routes/rationalization_api_routes.py): %s", exc)
        try:
            # stats=None, insufficient_count=None: the zeroed dict this used to
            # pass rendered a complete, plausible pipeline - "0 duplicate
            # groups, 0 estimated savings" - built entirely from a failed query.
            return render_template(
                "applications/rationalization/dashboard.html",
                stats=None,
                groups=[],
                runs=[],
                latest_run=None,
                error="An error occurred loading rationalization data.",
                currency_symbol=currency_symbol,
                active_tab="dashboard",
                data_quality={},
                insufficient_count=None,
                load_error="Rationalization statistics could not be read.",
            )
        except Exception as inner_err:
            current_app.logger.error(f"Dashboard error handler also failed: {inner_err}")
            try:
                db.session.rollback()
            except Exception as exc:
                logger.debug("suppressed error in rationalization_dashboard (app/modules/applications/routes/rationalization_api_routes.py): %s", exc)
            try:
                return render_template_string(
                    "{% extends 'layouts/admin_base.html' %}"
                    "{% block content %}"
                    "<div class='p-8'>"
                    "<h1 class='text-2xl font-semibold'>Rationalization</h1>"
                    "<p class='mt-4 text-muted-foreground'>Service temporarily unavailable. Please try again later.</p>"
                    "<a href=\"{{ url_for('admin.index') }}\" class='mt-4 inline-block text-primary'>Return to Dashboard</a>"
                    "</div>"
                    "{% endblock %}"
                ), 200
            except Exception:
                return render_template_string(
                    "<html><head><title>Rationalization</title></head>"
                    "<body><nav id='sidebar-nav'></nav><h1>Rationalization</h1>"
                    "<p>Service temporarily unavailable. <a href='/dashboard'>Return to Dashboard</a></p>"
                    "</body></html>"
                ), 200


@unified_applications_bp.route("/rationalization/workbench")
@login_required
def rationalization_workbench():
    """Decision Workbench — filter, triage, and bulk-action scored applications."""
    try:
        from config import CurrencyConfig
        currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "£")
    except Exception:
        currency_symbol = "£"

    return render_template(
        "applications/rationalization/workbench.html",
        currency_symbol=currency_symbol,
        active_tab="workbench",
    )


@unified_applications_bp.route("/rationalization/planning/<int:app_id>")
@login_required
def rationalization_planning(app_id):
    """Per-application planning page — decision dossier, replacement, retirement, overrides."""
    app = ApplicationComponent.query.get_or_404(app_id)

    from app.models.application_rationalization import ApplicationRationalizationScore
    score = ApplicationRationalizationScore.query.filter_by(
        application_component_id=app_id
    ).first()

    try:
        from config import CurrencyConfig
        currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "£")
    except Exception:
        currency_symbol = "£"

    return render_template(
        "applications/rationalization/planning.html",
        app=app,
        score=score,
        currency_symbol=currency_symbol,
        active_tab="planning",
        planning_app=app,
    )


@unified_applications_bp.route("/rationalization/tracking")
@login_required
def rationalization_tracking():
    """Portfolio-level outcomes — benefits tracking, dependency risk, retirement sequence."""
    try:
        from config import CurrencyConfig
        currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "£")
    except Exception:
        currency_symbol = "£"

    try:
        from app.models.application_rationalization import RationalizationBenefitsTracker
        from sqlalchemy import func

        projected = db.session.query(
            func.coalesce(func.sum(RationalizationBenefitsTracker.projected_annual_savings), 0)
        ).scalar() or 0
        actual = db.session.query(
            func.coalesce(func.sum(RationalizationBenefitsTracker.actual_annual_savings), 0)
        ).scalar() or 0
        financial = {"projected": float(projected), "actual": float(actual)}
    except Exception as e:
        current_app.logger.error(f"Error loading tracking financials: {e}")
        financial = {"projected": 0, "actual": 0}

    return render_template(
        "applications/rationalization/tracking.html",
        currency_symbol=currency_symbol,
        active_tab="tracking",
        financial_summary=financial,
    )


@unified_applications_bp.route("/rationalization/api/run-detection", methods=["POST"])
@login_required
@platform_admin_required
@rate_limit(3, "1h")
@audit_log("rationalization_run_detection")
def rationalization_run_detection():
    """Run duplicate detection analysis."""
    from app.services.unified_duplicate_detection_service import (
        UnifiedDuplicateDetectionService,
    )

    try:
        data = request.get_json() or {}
        strategy = data.get("strategy", "hybrid")
        threshold = data.get("similarity_threshold", 0.55)

        if strategy not in ["fast", "hybrid", "enhanced"]:
            return jsonify({"success": False, "error": "Invalid strategy"}), 400

        if not (0.0 <= threshold <= 1.0):
            return jsonify(
                {"success": False, "error": "Threshold must be between 0 and 1"}
            ), 400

        service = UnifiedDuplicateDetectionService()
        result = service.run_detection(
            similarity_threshold=threshold, strategy=strategy
        )

        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Detection error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/groups")
@login_required
def rationalization_get_groups():
    """Get all duplicate groups with application details."""
    from app.services.unified_duplicate_detection_service import (
        UnifiedDuplicateDetectionService,
    )

    try:
        service = UnifiedDuplicateDetectionService()
        groups = service.get_duplicate_groups("simple", include_applications=True)
        return jsonify({"success": True, "groups": groups})
    except Exception as e:
        current_app.logger.error(f"Error getting groups: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/runs")
@login_required
@platform_admin_required
def rationalization_get_runs():
    """Get detection run history."""
    from app.services.unified_duplicate_detection_service import (
        UnifiedDuplicateDetectionService,
    )

    try:
        service = UnifiedDuplicateDetectionService()
        runs = service.get_detection_runs("simple")
        return jsonify({"success": True, "runs": runs})
    except Exception as e:
        current_app.logger.error(f"Error getting runs: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/auto-resolve-exact", methods=["POST"]
)
@login_required
@platform_admin_required
@audit_log("rationalization_auto_resolve")
def rationalization_auto_resolve():
    """Auto-resolve exact match duplicate groups."""
    from app.models.architecture_review_board import ARBAuditLog
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    try:
        exact_groups = UnifiedDuplicateGroup.query.filter_by(
            duplicate_type="exact", status="pending"
        ).all()
        resolved_count = 0

        for group in exact_groups:
            group.status = "resolved"
            group.resolution_action = "keep_primary"
            resolved_count += 1

            # Create audit trail entry
            audit_entry = ARBAuditLog(
                entity_type="duplicate_group",
                entity_id=group.id,
                entity_reference=f"group_{group.id}",
                action="auto_resolve",
                action_description=f"Auto-resolved exact match duplicate group #{group.id} with keep_primary strategy",
                user_id=current_user.id
                if current_user and current_user.is_authenticated
                else None,
                user_email=getattr(current_user, "email", None)
                if current_user and current_user.is_authenticated
                else None,
                timestamp=datetime.utcnow(),
            )
            db.session.add(audit_entry)

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "resolved_count": resolved_count,
                "message": f"Auto-resolved {resolved_count} exact match groups",
            }
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Auto-resolve error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/ignore-merge-candidate", methods=["POST"]
)
@login_required
@audit_log("rationalization_ignore_merge")
def rationalization_ignore_merge_candidate():
    """Persist an ignore decision for a merge candidate pair."""
    from app.models.architecture_review_board import ARBAuditLog

    try:
        data = request.get_json()
        primary_id = data.get("primary_id")
        duplicate_id = data.get("duplicate_id")

        if not primary_id or not duplicate_id:
            return jsonify(
                {"success": False, "error": "primary_id and duplicate_id are required"}
            ), 400

        audit_entry = ARBAuditLog(
            entity_type="merge_candidate",
            entity_id=int(primary_id),
            entity_reference=f"{primary_id}:{duplicate_id}",
            action="ignore",
            action_description=f"Ignored merge candidate: app {primary_id} and app {duplicate_id}",
            user_id=current_user.id
            if current_user and current_user.is_authenticated
            else None,
            user_email=getattr(current_user, "email", None)
            if current_user and current_user.is_authenticated
            else None,
            timestamp=datetime.utcnow(),
        )
        db.session.add(audit_entry)
        db.session.commit()

        return jsonify({"success": True, "message": "Candidate ignored"})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Ignore merge candidate error: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500



# ── Rationalization Policy API ───────────────────────────────────────────


@unified_applications_bp.route("/rationalization/api/policies", methods=["GET"])
@login_required
def rationalization_list_policies():
    """
    List all RationalizationPolicy records.

    Returns a JSON array of policy dicts including thresholds, weights,
    mandatory checks, and scope filter so the UI can display which policy
    applies to which portfolio or business unit.

    Response shape::

        {
            "policies": [
                {
                    "id": 1,
                    "name": "Default",
                    "description": "...",
                    "is_default": true,
                    "thresholds": {...},
                    "dimension_weights": {...},
                    "mandatory_checks": [...],
                    "scope_filter": null,
                    "created_at": "2026-03-13T...",
                    "updated_at": "2026-03-13T..."
                },
                ...
            ]
        }
    """
    from app.models.application_rationalization import RationalizationPolicy

    try:
        policies = (
            RationalizationPolicy.query
            .order_by(
                RationalizationPolicy.is_default.desc(),
                RationalizationPolicy.name.asc(),
            )
            .all()
        )
        return jsonify({"policies": [p.to_dict() for p in policies]})
    except Exception as exc:
        current_app.logger.error(f"Error listing rationalization policies: {exc}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ── Element CRUD API ─────────────────────────────────────────────────────


@unified_applications_bp.route("/api/elements/<int:element_id>", methods=["GET"])
@login_required
def api_get_element(element_id):
    """Get a single ArchiMate element by ID."""
    try:
        from app.models.models import ArchiMateElement

        element = ArchiMateElement.query.get(element_id)
        if not element:
            return jsonify({"success": False, "error": "Element not found"}), 404

        return jsonify(
            {
                "success": True,
                "element": {
                    "id": element.id,
                    "name": element.name,
                    "type": element.type,
                    "layer": element.layer,
                    "description": element.description,
                    "scope": element.scope or "",
                    "status": element.status or "",
                    "priority": element.priority or "",
                    "properties": element.properties,
                    "documentation": element.documentation or "",
                    "application_component_id": element.application_component_id,
                },
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error getting element {element_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def _element_usage(element_id, exclude_diagram_id=None):
    """How many relationships and OTHER saved diagrams reference this element.

    The Composer's own "Remove from canvas" already unlinks an element from
    one diagram while leaving it in the repository (composer_graph.js
    deleteElement: "Element stays in catalog"). This is the check for the
    other action -- deleting the repository row itself, which other diagrams
    and every relationship touching it would silently lose data for.

    exclude_diagram_id must be the diagram the delete is being requested
    from: an element is trivially "on a diagram" -- the one the architect is
    looking at right now -- and counting that against it would make every
    single delete look "in use" and require force/admin, which defeats the
    whole point of the safe, no-force path. What actually matters is whether
    it is referenced *elsewhere*.
    """
    from app.models.archimate_core import SavedDiagramElement
    from app.models.models import ArchiMateRelationship

    rel_count = ArchiMateRelationship.query.filter(
        db.or_(
            ArchiMateRelationship.source_id == element_id,
            ArchiMateRelationship.target_id == element_id,
        )
    ).count()
    diagram_query = db.session.query(SavedDiagramElement.diagram_id).filter(
        SavedDiagramElement.element_id == element_id
    )
    if exclude_diagram_id:
        diagram_query = diagram_query.filter(SavedDiagramElement.diagram_id != exclude_diagram_id)
    diagram_count = diagram_query.distinct().count()
    return {"relationships": rel_count, "diagrams": diagram_count}


@unified_applications_bp.route("/api/elements/<int:element_id>/usage", methods=["GET"])
@login_required
def api_element_usage(element_id):
    """Report how many relationships/other diagrams reference an element.

    Called before offering a repository-level delete so the confirmation the
    architect sees states the real blast radius rather than a generic warning.

    Query Parameters:
        diagram_id (int): the diagram the delete is being offered from --
            excluded from the "other diagrams" count (see _element_usage).
    """
    from app.models.models import ArchiMateElement

    element = ArchiMateElement.query.get(element_id)
    if not element:
        return jsonify({"success": False, "error": "Element not found"}), 404
    exclude_diagram_id = request.args.get("diagram_id", type=int)
    return jsonify({"success": True, "usage": _element_usage(element_id, exclude_diagram_id)})


@unified_applications_bp.route("/api/elements/<int:element_id>", methods=["DELETE"])
@login_required
@require_roles("admin", "architect")
@audit_log("element_delete")
def api_delete_element(element_id):
    """Delete an ArchiMate element from the repository (not just a diagram).

    RAT-114/GAP-DEL-001: this used to delete unconditionally, with no check
    for other diagrams or relationships depending on the row -- either a
    foreign-key violation on the next reference, or (if the FK has no
    protection) a silent orphan elsewhere. Blocked by default when the
    element is in use; ?force=true overrides it, but only for admins --
    "I created a duplicate by mistake" is an architect's call, "delete this
    out from under other diagrams anyway" is not.
    """
    try:
        from app.models.archimate_core import SavedDiagramElement
        from app.models.models import ArchiMateElement, ArchiMateRelationship

        element = ArchiMateElement.query.get(element_id)
        if not element:
            return jsonify({"success": False, "error": "Element not found"}), 404

        exclude_diagram_id = request.args.get("diagram_id", type=int)
        usage = _element_usage(element_id, exclude_diagram_id)
        force = request.args.get("force", "").lower() == "true"

        if (usage["relationships"] or usage["diagrams"]) and not force:
            return jsonify({
                "success": False,
                "error": "Element is in use elsewhere",
                "usage": usage,
            }), 409

        if force and not (current_user.is_platform_admin or getattr(current_user, "is_org_admin", False)):
            return jsonify({
                "success": False,
                "error": "Only an admin can delete an element that is still in use elsewhere",
                "usage": usage,
            }), 403

        if force:
            ArchiMateRelationship.query.filter(
                db.or_(
                    ArchiMateRelationship.source_id == element_id,
                    ArchiMateRelationship.target_id == element_id,
                )
            ).delete(synchronize_session=False)

        # Unconditional, not just under force: exclude_diagram_id only kept
        # the *current* diagram's placement out of the usage count above (so
        # "in use" means in use elsewhere, not just here) -- but that row
        # still exists and still foreign-keys to this element, so it must go
        # regardless of force or the delete 500s on the FK constraint.
        # force additionally clears every OTHER diagram's placement row.
        placement_query = SavedDiagramElement.query.filter_by(element_id=element_id)
        if not force and exclude_diagram_id:
            placement_query = placement_query.filter_by(diagram_id=exclude_diagram_id)
        placement_query.delete(synchronize_session=False)

        db.session.delete(element)
        db.session.commit()

        return jsonify({"success": True, "message": "Element deleted", "usage": usage})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting element {element_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ── Requirements & Template API ──────────────────────────────────────────


@unified_applications_bp.route("/api/requirements", methods=["GET"])
@login_required
def api_list_requirements():
    """List all requirements for requirement picker."""
    try:
        from app.models.models import Requirement

        requirements = Requirement.query.order_by(Requirement.title).limit(500).all()
        return jsonify(
            {
                "requirements": [
                    {
                        "id": r.id,
                        "title": r.title,
                        "description": (r.description or "")[:200],
                        "type": r.type,
                        "priority": r.priority,
                        "category": getattr(r, "category", ""),
                        "compliance_status": getattr(r, "compliance_status", ""),
                    }
                    for r in requirements
                ]
            }
        )
    except Exception as e:
        current_app.logger.exception("Error listing requirements: %s", e)
        return jsonify({"success": False, "error": "Could not load requirements"}), 500


@unified_applications_bp.route("/api/templates/frameworks", methods=["GET"])
@login_required
def api_template_frameworks():
    """Get distinct framework values from ElementTemplate."""
    try:
        from app.models.element_templates import ElementTemplate

        frameworks = (
            db.session.query(ElementTemplate.framework)
            .filter(ElementTemplate.is_active.is_(True))
            .distinct()
            .order_by(ElementTemplate.framework)
            .all()
        )
        return jsonify([f[0] for f in frameworks if f[0]])
    except Exception as e:
        current_app.logger.exception("Error loading frameworks: %s", e)
        return jsonify({"success": False, "error": "Could not load frameworks"}), 500


@unified_applications_bp.route("/api/templates/categories", methods=["GET"])
@login_required
def api_template_categories():
    """Get distinct categories filtered by framework."""
    try:
        from app.models.element_templates import ElementTemplate

        framework = request.args.get("framework", "")
        query = db.session.query(ElementTemplate.category).filter(
            ElementTemplate.is_active.is_(True)
        )
        if framework:
            query = query.filter(ElementTemplate.framework == framework)

        categories = query.distinct().order_by(ElementTemplate.category).all()
        return jsonify([c[0] for c in categories if c[0]])
    except Exception as e:
        current_app.logger.exception("Error loading categories: %s", e)
        return jsonify({"success": False, "error": "Could not load categories"}), 500


@unified_applications_bp.route("/api/templates", methods=["GET"])
@login_required
def api_list_templates():
    """Get filtered list of ElementTemplates."""
    try:
        from app.models.element_templates import ElementTemplate

        query = ElementTemplate.query.filter(ElementTemplate.is_active.is_(True))

        framework = request.args.get("framework")
        category = request.args.get("category")
        layer = request.args.get("layer")
        element_type = request.args.get("element_type")
        search = request.args.get("search")
        limit = safe_int_arg('limit', 200, minimum=1, maximum=500)

        if framework:
            query = query.filter(ElementTemplate.framework == framework)
        if category:
            query = query.filter(ElementTemplate.category == category)
        if layer:
            query = query.filter(ElementTemplate.layer == layer)
        if element_type:
            query = query.filter(ElementTemplate.element_type == element_type)
        if search:
            _escaped = escape_like_literal(search)
            query = query.filter(
                db.or_(
                    ElementTemplate.name.ilike(f"%{_escaped}%", escape="\\"),
                    ElementTemplate.description.ilike(f"%{_escaped}%", escape="\\"),
                    ElementTemplate.keywords.ilike(f"%{_escaped}%", escape="\\"),
                )
            )

        templates = (
            query.order_by(ElementTemplate.framework, ElementTemplate.name)
            .limit(limit)
            .all()
        )

        return jsonify(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "framework": t.framework,
                    "category": t.category,
                    "subcategory": t.subcategory,
                    "element_type": t.element_type,
                    "layer": t.layer,
                    "description": (t.description or "")[:200],
                    "code": t.code,
                    "usage_count": t.usage_count or 0,
                }
                for t in templates
            ]
        )
    except Exception as e:
        current_app.logger.exception("Error listing templates: %s", e)
        return jsonify({"success": False, "error": "Could not load templates"}), 500


@unified_applications_bp.route("/api/templates/element-types", methods=["GET"])
@login_required
def api_template_element_types():
    """Get distinct element types for a given layer."""
    try:
        from app.models.element_templates import ElementTemplate

        layer = request.args.get("layer", "")
        query = db.session.query(ElementTemplate.element_type).filter(
            ElementTemplate.is_active.is_(True)
        )
        if layer:
            query = query.filter(ElementTemplate.layer == layer)

        types = query.distinct().order_by(ElementTemplate.element_type).all()
        return jsonify([t[0] for t in types if t[0]])
    except Exception as e:
        current_app.logger.exception("Error loading element types: %s", e)
        return jsonify({"success": False, "error": "Could not load element types"}), 500


@unified_applications_bp.route(
    "/api/<int:app_id>/templates/recommendations", methods=["GET"]
)
@login_required
def api_template_recommendations(app_id):
    """Get recommended templates for an application."""
    app = require_entity(ApplicationComponent, app_id, description="Application not found")
    try:
        from app.models.element_templates import (
            ElementTemplate,
            ElementTemplateRecommendation,
        )

        # Find recommendations based on application type and other triggers
        triggers = []
        if app.component_type:
            triggers.append(("application_type", app.component_type))
        if app.technology_stack:
            triggers.append(("tech_stack", app.technology_stack))
        if app.business_domain:
            triggers.append(("industry", app.business_domain))

        if not triggers:
            # Return popular templates as fallback
            templates = (
                ElementTemplate.query.filter(ElementTemplate.is_active.is_(True))
                .order_by(ElementTemplate.usage_count.desc())
                .limit(20)
                .all()
            )
        else:
            # Find matching recommendations
            conditions = [
                db.and_(
                    ElementTemplateRecommendation.trigger_type == t_type,
                    ElementTemplateRecommendation.trigger_value == t_value,
                )
                for t_type, t_value in triggers
            ]
            rec_ids = (
                db.session.query(ElementTemplateRecommendation.template_id)
                .filter(
                    ElementTemplateRecommendation.is_active.is_(True),
                    db.or_(*conditions),
                )
                .distinct()
                .all()
            )
            template_ids = [r[0] for r in rec_ids]

            if template_ids:
                templates = ElementTemplate.query.filter(
                    ElementTemplate.id.in_(template_ids),
                    ElementTemplate.is_active.is_(True),
                ).all()
            else:
                templates = (
                    ElementTemplate.query.filter(ElementTemplate.is_active.is_(True))
                    .order_by(ElementTemplate.usage_count.desc())
                    .limit(20)
                    .all()
                )

        return jsonify(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "framework": t.framework,
                    "category": t.category,
                    "element_type": t.element_type,
                    "layer": t.layer,
                    "description": (t.description or "")[:200],
                    "code": t.code,
                    "recommended": True,
                }
                for t in templates
            ]
        )
    except Exception as e:
        current_app.logger.exception("Error loading recommendations for app %s: %s", app_id, e)
        return jsonify(
            {"success": False, "error": "Could not load template recommendations"}
        ), 500


# ── Retirement Blocker Assessment API (RAT-109) ───────────────────────────


@unified_applications_bp.route(
    "/rationalization/api/retirement-blockers/<int:app_id>", methods=["GET"]
)
@login_required
def rationalization_retirement_blockers(app_id):
    """
    Authoritative retirement blocker assessment across five categories.

    Checks integrations, users, contracts, compliance, and data migration
    constraints before an application can be retired or replaced.

    Response shape::

        {
            "success": true,
            "app_id": 42,
            "app_name": "SAP ERP",
            "data": {
                "categories": [
                    {
                        "name": "Integrations",
                        "status": "blocked" | "warning" | "clear",
                        "count": 3,
                        "details": "..."
                    },
                    ...
                ],
                "total_blockers": 2,
                "is_retirement_safe": false,
                "blocker_summary": "2 retirement blocker(s) across: Integrations, Contracts."
            }
        }
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.services.rationalization_scoring_service import RationalizationScoringService

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        assessment = RationalizationScoringService.assess_retirement_blockers(app_obj)

        return jsonify({
            "success": True,
            "app_id": app_id,
            "app_name": app_obj.name,
            "data": assessment,
        })
    except Exception as e:
        current_app.logger.error(
            f"Error assessing retirement blockers for app {app_id}: {e}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ── Dependency Impact Analysis API ───────────────────────────────────────


@unified_applications_bp.route(
    "/rationalization/api/dependency-impact/<int:app_id>", methods=["GET"]
)
@login_required
def rationalization_dependency_impact(app_id):
    """
    Combined dependency impact endpoint for a single application.

    Returns retirement blockers + blast radius in one call, plus a
    retirement_safe flag so the UI can gate ELIMINATE dispositioning.

    Query params:
        depth (int, 1-5): Traversal depth for blast radius (default 3).
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.services.rationalization_scoring_service import RationalizationScoringService

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        depth = request.args.get("depth", 3, type=int)
        depth = min(max(depth, 1), 5)

        blockers = RationalizationScoringService.get_retirement_blockers(app_id)
        blast_radius = RationalizationScoringService.get_blast_radius(app_id, depth)

        if "error" in blast_radius and not blast_radius.get("success", True):
            return jsonify({"success": False, "error": blast_radius["error"]}), 500

        retirement_safe = (
            not blockers.get("has_critical_blockers", False)
            and blast_radius.get("risk_level", "low") not in ("critical", "high")
        )

        return jsonify(
            {
                "success": True,
                "app_id": app_id,
                "app_name": app_obj.name,
                "blockers": blockers,
                "blast_radius": blast_radius,
                "retirement_safe": retirement_safe,
            }
        )
    except Exception as e:
        current_app.logger.error(
            f"Error calculating dependency impact for app {app_id}: {e}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/retirement-sequence", methods=["GET"]
)
@login_required
def rationalization_retirement_sequence():
    """
    Return a dependency-aware retirement sequence for ELIMINATE/MIGRATE apps.

    Computes topological waves so that apps with no downstream consumers retire
    first, unlocking their dependents in subsequent waves.

    Query params:
        app_ids (str): Optional comma-separated list of ApplicationComponent IDs.
                       If omitted, all ELIMINATE/MIGRATE scored apps are used.

    Response shape::

        {
            "success": true,
            "total_apps": 12,
            "total_waves": 3,
            "waves": [
                {
                    "wave_number": 1,
                    "app_count": 4,
                    "apps": [
                        {
                            "app_id": 42,
                            "app_name": "Legacy CRM",
                            "disposition": "ELIMINATE",
                            "lifecycle_status": "retire",
                            "business_criticality": "low",
                            "blocked_by": [],
                            "unblocks": [55, 61]
                        },
                        ...
                    ]
                },
                ...
            ],
            "unsequenced": []
        }
    """
    from app.services.rationalization_scoring_service import RationalizationScoringService

    try:
        raw_ids = request.args.get("app_ids", "").strip()
        app_ids = None
        if raw_ids:
            parsed = []
            for part in raw_ids.split(","):
                part = part.strip()
                if part.isdigit():
                    parsed.append(int(part))
            if parsed:
                app_ids = parsed

        result = RationalizationScoringService.compute_retirement_sequence(app_ids=app_ids)

        if not result.get("success", True):
            return jsonify({"success": False, "error": result.get("error", "Unknown error")}), 500

        return jsonify(result)

    except Exception as exc:
        current_app.logger.error(
            f"Error computing retirement sequence: {exc}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/portfolio-dependencies", methods=["GET"]
)
@login_required
def rationalization_portfolio_dependencies():
    """
    Portfolio-wide dependency risk summary.

    Returns a paginated list of applications enriched with their
    upstream blocker count and blast-radius risk level, so the
    dependency-risk section of the rationalization dashboard can show
    which apps carry the most retirement risk across the portfolio.

    Query params:
        page (int): Page number, 1-based (default 1).
        per_page (int): Results per page, max 100 (default 25).
        risk_level (str): Filter by blast_radius risk level
                          (low|medium|high|critical).  Optional.
    """
    from sqlalchemy import func

    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import ApplicationDependency

    try:
        page = max(safe_int_arg('page', 1, minimum=1), 1)
        per_page = min(safe_int_arg('per_page', 25, minimum=1, maximum=500), 100)
        risk_filter = request.args.get("risk_level", "").strip().lower()

        # Subquery: count of upstream blockers per target app (apps that depend ON this app)
        blocker_counts = (
            db.session.query(
                ApplicationDependency.target_app_id.label("app_id"),
                func.count(ApplicationDependency.id).label("blocker_count"),
                func.sum(
                    db.case(
                        (
                            db.or_(
                                ApplicationDependency.blocks_retirement.is_(True),
                                ApplicationDependency.dependency_strength.in_(
                                    ["critical", "high"]
                                ),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("critical_blocker_count"),
            )
            .filter(ApplicationDependency.status == "active")
            .group_by(ApplicationDependency.target_app_id)
            .subquery()
        )

        # Subquery: count of downstream dependents per source app
        downstream_counts = (
            db.session.query(
                ApplicationDependency.source_app_id.label("app_id"),
                func.count(ApplicationDependency.id).label("downstream_count"),
            )
            .filter(ApplicationDependency.status == "active")
            .group_by(ApplicationDependency.source_app_id)
            .subquery()
        )

        # Join applications with their dependency counts
        query = (
            db.session.query(
                ApplicationComponent,
                func.coalesce(blocker_counts.c.blocker_count, 0).label("blocker_count"),
                func.coalesce(
                    blocker_counts.c.critical_blocker_count, 0
                ).label("critical_blocker_count"),
                func.coalesce(downstream_counts.c.downstream_count, 0).label(
                    "downstream_count"
                ),
            )
            .outerjoin(
                blocker_counts,
                ApplicationComponent.id == blocker_counts.c.app_id,
            )
            .outerjoin(
                downstream_counts,
                ApplicationComponent.id == downstream_counts.c.app_id,
            )
            .filter(
                db.or_(
                    blocker_counts.c.blocker_count > 0,
                    downstream_counts.c.downstream_count > 0,
                )
            )
            .order_by(
                func.coalesce(blocker_counts.c.critical_blocker_count, 0).desc(),
                func.coalesce(blocker_counts.c.blocker_count, 0).desc(),
                ApplicationComponent.name,
            )
        )

        # Materialise before applying risk_level filter (risk_level is derived)
        all_rows = query.all()

        def _risk_level(blocker_ct, critical_ct, downstream_ct):
            """Derive risk level from dependency counts — mirrors service logic."""
            total = blocker_ct + downstream_ct
            if critical_ct >= 3 or total >= 10:
                return "critical"
            if critical_ct >= 1 or total >= 5:
                return "high"
            if total >= 2:
                return "medium"
            return "low"

        results = []
        for row in all_rows:
            app_obj, blocker_ct, critical_ct, downstream_ct = row
            rl = _risk_level(int(blocker_ct), int(critical_ct), int(downstream_ct))
            if risk_filter and rl != risk_filter:
                continue
            results.append(
                {
                    "app_id": app_obj.id,
                    "app_name": app_obj.name,
                    "lifecycle_status": app_obj.lifecycle_status,
                    "business_criticality": app_obj.business_criticality,
                    "blocker_count": int(blocker_ct),
                    "critical_blocker_count": int(critical_ct),
                    "downstream_count": int(downstream_ct),
                    "risk_level": rl,
                }
            )

        total = len(results)
        start = (page - 1) * per_page
        end = start + per_page
        page_results = results[start:end]

        return jsonify(
            {
                "success": True,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": max(1, (total + per_page - 1) // per_page),
                "apps": page_results,
            }
        )
    except Exception as e:
        current_app.logger.error(
            f"Error loading portfolio dependencies: {e}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/evidence-trail/<int:app_id>", methods=["GET"]
)
@login_required
def rationalization_evidence_trail(app_id):
    """
    Return the full structured evidence trail for a single application.

    Exposes every score driver, its weighting, the raw evidence value observed,
    the points it contributed, the source model field, and a plain-English
    rationale — so architects can challenge or approve the recommendation.

    The trail is computed fresh on every call (read-only; no DB writes).

    Query params:
        None

    Response shape::

        {
          "success": true,
          "app_id": 42,
          "app_name": "SAP ERP",
          "scoring_config_name": "CIO.gov Federal Baseline",
          "scores": {
            "technical_health": 68.0,
            "business_value": 81.5,
            "cost_efficiency": 44.0,
            "vendor_risk": 72.0,
            "overall": 66.93
          },
          "weights": {
            "technical_health": 30.0,
            "business_value": 35.0,
            "cost_efficiency": 25.0,
            "vendor_risk": 10.0
          },
          "overall_score": 66.93,
          "time_action": "TOLERATE",
          "time_action_tentative": false,
          "disposition_action": "retain",
          "disposition_confidence": "medium",
          "confidence_reasons": ["Score near threshold boundary"],
          "insufficient_evidence": false,
          "missing_dimensions": [],
          "readiness": { ... },
          "evidence_trail": [
            {
              "factor": "Technical Health",
              "dimension": "technical_health",
              "weight": 30.0,
              "raw_value": 68.0,
              "contribution": 20.4,
              "source": "ApplicationComponent (...)",
              "rationale": "...",
              "sub_factors": [
                {
                  "factor": "Lifecycle Status",
                  "raw_value": "operational",
                  "contribution": 20,
                  "source": "ApplicationComponent.lifecycle_status",
                  "rationale": "..."
                },
                ...
              ]
            },
            ...
          ]
        }
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.services.rationalization_scoring_service import RationalizationScoringService

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        result = RationalizationScoringService.get_evidence_trail(app_id, app=app_obj)

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 500

        return jsonify({"success": True, **result})

    except Exception as e:
        current_app.logger.error(
            f"Error returning evidence trail for app {app_id}: {e}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/portfolio-readiness", methods=["GET"]
)
@login_required
def rationalization_portfolio_readiness():
    """
    Portfolio-wide decision-readiness summary.

    Returns counts of decision-ready vs incomplete applications (scored only),
    plus a paginated list of applications enriched with their readiness fields
    so the dashboard can surface missing-data states.

    Query params:
        page (int): Page number, 1-based (default 1).
        per_page (int): Results per page, max 100 (default 25).
        ready (str): Filter — 'yes' for decision-ready, 'no' for incomplete.
    """
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.models.application_portfolio import ApplicationComponent

    try:
        page = max(safe_int_arg('page', 1, minimum=1), 1)
        per_page = min(safe_int_arg('per_page', 25, minimum=1, maximum=500), 100)
        ready_filter = request.args.get("ready", "").strip().lower()

        query = (
            db.session.query(ApplicationRationalizationScore, ApplicationComponent)
            .join(
                ApplicationComponent,
                ApplicationRationalizationScore.application_component_id == ApplicationComponent.id,
            )
            .filter(ApplicationRationalizationScore.readiness_score.isnot(None))
        )

        if ready_filter == "yes":
            query = query.filter(
                ApplicationRationalizationScore.is_decision_ready.is_(True)
            )
        elif ready_filter == "no":
            query = query.filter(
                db.or_(
                    ApplicationRationalizationScore.is_decision_ready.is_(False),
                    ApplicationRationalizationScore.is_decision_ready.is_(None),
                )
            )

        all_rows = query.all()

        # Build summary counts across all scored rows (not just filtered page)
        all_scored = (
            db.session.query(ApplicationRationalizationScore)
            .filter(ApplicationRationalizationScore.readiness_score.isnot(None))
            .all()
        )
        total_scored = len(all_scored)
        decision_ready_count = sum(
            1 for r in all_scored if r.is_decision_ready
        )
        incomplete_count = total_scored - decision_ready_count

        results = []
        for score, app_obj in all_rows:
            missing_critical = []
            if score.readiness_dimensions:
                from app.models.application_rationalization import READINESS_DIMENSIONS
                missing_critical = [
                    dim
                    for dim, meta in READINESS_DIMENSIONS.items()
                    if meta["severity"] == "high"
                    and not score.readiness_dimensions.get(dim, False)
                ]
            is_decision_ready = bool(score.is_decision_ready)
            results.append(
                {
                    "app_id": app_obj.id,
                    "app_name": app_obj.name,
                    "lifecycle_status": app_obj.lifecycle_status,  # model-safety-ok
                    "business_criticality": app_obj.business_criticality,  # model-safety-ok
                    "readiness_score": score.readiness_score,
                    "is_decision_ready": is_decision_ready,
                    "readiness_dimensions": score.readiness_dimensions or {},
                    "missing_critical": missing_critical,
                    "time_action": score.rationalization_action,
                    "time_action_tentative": not is_decision_ready,
                    "disposition_action": score.disposition_action,
                    "disposition_confidence": score.disposition_confidence,
                    "confidence_reasons": score.confidence_reasons or [],
                    "insufficient_evidence": not is_decision_ready,
                    "missing_dimensions": missing_critical,
                }
            )

        total = len(results)
        start = (page - 1) * per_page
        end = start + per_page
        page_results = results[start:end]

        return jsonify(
            {
                "success": True,
                "summary": {
                    "total_scored": total_scored,
                    "decision_ready_count": decision_ready_count,
                    "incomplete_count": incomplete_count,
                },
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": max(1, (total + per_page - 1) // per_page),
                "apps": page_results,
            }
        )
    except Exception as e:
        current_app.logger.error(
            f"Error loading portfolio readiness: {e}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/replacement-plan/<int:app_id>", methods=["GET"]
)
@login_required
def rationalization_get_replacement_plan(app_id):
    """
    Return the replacement plan for a single application.

    Returns 404 when no plan has been created yet so the UI can decide
    whether to render an empty form or an edit form.

    Response shape (success)::

        {
            "success": true,
            "plan": {
                "id": 1,
                "source_app_id": 42,
                "source_app_name": "Legacy CRM",
                "target_app_id": 99,
                "target_app_name": "Salesforce",
                "migration_phase": "planning",
                "estimated_cost": 150000.0,
                "estimated_duration_months": 12,
                "risk_level": "medium",
                "rollout_strategy": "phased",
                "notes": "...",
                "created_by": "a.smith@example.com",
                "created_at": "2026-03-13T...",
                "updated_at": "2026-03-13T..."
            }
        }
    """
    from app.models.application_rationalization import ReplacementPlan

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        plan = ReplacementPlan.query.filter_by(source_app_id=app_id).first()
        if not plan:
            return jsonify({"success": False, "error": "No replacement plan found"}), 404

        return jsonify({"success": True, "plan": plan.to_dict()})
    except Exception as exc:
        current_app.logger.error(
            f"Error loading replacement plan for app {app_id}: {exc}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/replacement-plan/<int:app_id>", methods=["POST"]
)
@login_required
@audit_log("replacement_plan_save")
def rationalization_save_replacement_plan(app_id):
    """
    Create or update the replacement plan for an application.

    Accepts JSON body with the plan fields.  All fields are optional on update;
    only supplied keys are applied to the record.

    Request body::

        {
            "target_app_id":             99,           // nullable — use null for TBD
            "target_app_name":           "Salesforce", // cached display name
            "migration_phase":           "planning",   // planning|pilot|migration|cutover|decommission
            "estimated_cost":            150000.0,     // nullable
            "estimated_duration_months": 12,           // nullable
            "risk_level":                "medium",     // low|medium|high|critical
            "rollout_strategy":          "phased",     // big_bang|phased|parallel_run|pilot_first
            "notes":                     "..."         // nullable
        }

    Response shape::

        {"success": true, "plan": {...}, "created": true|false}
    """
    from app.models.application_rationalization import ReplacementPlan

    VALID_PHASES = {"planning", "pilot", "migration", "cutover", "decommission"}
    VALID_RISK = {"low", "medium", "high", "critical"}
    VALID_STRATEGIES = {"big_bang", "phased", "parallel_run", "pilot_first"}

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        data = request.get_json(silent=True) or {}

        # Validate controlled vocabularies when supplied
        if "migration_phase" in data and data["migration_phase"] not in VALID_PHASES:
            return jsonify(
                {"success": False, "error": f"migration_phase must be one of {sorted(VALID_PHASES)}"}
            ), 400
        if "risk_level" in data and data["risk_level"] not in VALID_RISK:
            return jsonify(
                {"success": False, "error": f"risk_level must be one of {sorted(VALID_RISK)}"}
            ), 400
        if "rollout_strategy" in data and data["rollout_strategy"] not in VALID_STRATEGIES:
            return jsonify(
                {"success": False, "error": f"rollout_strategy must be one of {sorted(VALID_STRATEGIES)}"}
            ), 400

        # Validate target_app_id FK when supplied and not null
        target_app_id = data.get("target_app_id")
        if target_app_id is not None:
            target_app = ApplicationComponent.query.get(int(target_app_id))
            if not target_app:
                return jsonify(
                    {"success": False, "error": f"Target application {target_app_id} not found"}
                ), 400
            # Auto-populate target_app_name from the FK target when not explicitly provided
            if "target_app_name" not in data or not data.get("target_app_name"):
                data["target_app_name"] = target_app.name

        plan = ReplacementPlan.query.filter_by(source_app_id=app_id).first()
        created = plan is None

        if created:
            plan = ReplacementPlan(source_app_id=app_id)
            db.session.add(plan)

        # Apply supplied fields
        settable = {
            "target_app_id",
            "target_app_name",
            "migration_phase",
            "estimated_cost",
            "estimated_duration_months",
            "risk_level",
            "rollout_strategy",
            "notes",
        }
        for key in settable:
            if key in data:
                setattr(plan, key, data[key])

        # Record who saved this
        if current_user and current_user.is_authenticated:
            plan.created_by = getattr(current_user, "email", None) or getattr(current_user, "username", None)  # model-safety-ok

        db.session.commit()

        return jsonify({"success": True, "plan": plan.to_dict(), "created": created})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Error saving replacement plan for app {app_id}: {exc}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/review/<int:app_id>", methods=["POST"]
)
@login_required
@audit_log("rationalization_review_transition")
def rationalization_review_transition(app_id):
    """
    Advance or retract the review workflow state for a rationalization score.

    Request body::

        {
            "action":   "reviewed" | "approved" | "rejected" | "exception_approved",
            "notes":    "optional review note",
            "reviewer": "display name or email of the person performing the action"
        }

    The ``action`` value becomes the **new** ``review_status``.  The transition must be
    permitted by ``ApplicationRationalizationScore.REVIEW_TRANSITIONS`` from the current
    state, otherwise the request is rejected with HTTP 422.

    Response shape::

        {
            "success": true,
            "review_status": "approved",
            "reviewed_by": "alice@example.com",
            "reviewed_at": "2026-03-13T14:00:00",
            "approved_by": "alice@example.com",
            "approved_at": "2026-03-13T14:00:00"
        }
    """
    from app.models.application_rationalization import ApplicationRationalizationScore

    VALID_ACTIONS = {"reviewed", "approved", "rejected", "exception_approved", "draft"}

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()
        if not score:
            return jsonify(
                {"success": False, "error": f"No rationalization score found for application {app_id}"}
            ), 404

        data = request.get_json(silent=True) or {}
        action = (data.get("action") or "").strip().lower()

        if action not in VALID_ACTIONS:
            return jsonify(
                {
                    "success": False,
                    "error": f"action must be one of: {sorted(VALID_ACTIONS - {'draft'})}",
                }
            ), 400

        current_status = score.review_status or "draft"
        allowed_next = ApplicationRationalizationScore.REVIEW_TRANSITIONS.get(current_status, [])

        if action not in allowed_next:
            return jsonify(
                {
                    "success": False,
                    "error": (
                        f"Transition from '{current_status}' to '{action}' is not permitted. "
                        f"Allowed transitions from '{current_status}': {allowed_next or ['(none — terminal state)']}"
                    ),
                }
            ), 422

        notes = (data.get("notes") or "").strip() or None
        reviewer = (data.get("reviewer") or "").strip() or None
        if not reviewer and current_user and current_user.is_authenticated:
            reviewer = (  # model-safety-ok
                getattr(current_user, "email", None)
                or getattr(current_user, "username", None)
            )

        now = datetime.utcnow()
        before_review_state = {
            "review_status": score.review_status,
            "reviewed_by": score.reviewed_by,
            "approved_by": score.approved_by,
        }
        score.review_status = action

        if action == "reviewed":
            score.reviewed_by = reviewer
            score.reviewed_at = now
            if notes:
                score.review_notes = notes
        elif action in ("approved", "exception_approved"):
            score.approved_by = reviewer
            score.approved_at = now
            if notes:
                score.review_notes = notes
        elif action == "rejected":
            score.reviewed_by = reviewer
            score.reviewed_at = now
            if notes:
                score.review_notes = notes
        elif action == "draft":
            # Reset review fields on re-submission
            score.reviewed_by = None
            score.reviewed_at = None
            score.approved_by = None
            score.approved_at = None
            if notes:
                score.review_notes = notes

        # RAT-114: Record audit trail entry for this review transition
        _log_rationalization_audit(
            app_id=app_id,
            score_id=score.id,
            action="review_transition",
            actor=reviewer or "unknown",
            before_state=before_review_state,
            after_state={"review_status": action},
            details=notes,
        )

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "review_status": score.review_status,
                "reviewed_by": score.reviewed_by,
                "reviewed_at": score.reviewed_at.isoformat() if score.reviewed_at else None,
                "approved_by": score.approved_by,
                "approved_at": score.approved_at.isoformat() if score.approved_at else None,
                "review_notes": score.review_notes,
            }
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Error transitioning review state for app {app_id}: {exc}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/review-queue", methods=["GET"])
@login_required
def rationalization_review_queue():
    """
    List all scored applications grouped by review status.

    Query parameters:

    - ``status`` — filter to a specific review state
      (``draft`` | ``reviewed`` | ``approved`` | ``rejected`` | ``exception_approved``)
    - ``page``  — page number, 1-indexed (default: 1)
    - ``per_page`` — results per page, max 200 (default: 50)

    Response shape::

        {
            "success": true,
            "total": 42,
            "page": 1,
            "per_page": 50,
            "results": [
                {
                    "app_id": 7,
                    "app_name": "SAP ERP",
                    "rationalization_action": "ELIMINATE",
                    "disposition_action": "retire",
                    "overall_health_score": 28,
                    "review_status": "draft",
                    "reviewed_by": null,
                    "reviewed_at": null,
                    "approved_by": null,
                    "approved_at": null,
                    "review_notes": null,
                    "allowed_transitions": ["reviewed"]
                },
                ...
            ]
        }
    """
    from app.models.application_rationalization import ApplicationRationalizationScore

    VALID_STATUSES = {"draft", "reviewed", "approved", "rejected", "exception_approved"}

    try:
        status_filter = request.args.get("status", "").strip().lower() or None
        if status_filter and status_filter not in VALID_STATUSES:
            return jsonify(
                {"success": False, "error": f"status must be one of: {sorted(VALID_STATUSES)}"}
            ), 400

        try:
            page = max(1, safe_int_arg('page', 1, minimum=1))
            per_page = min(200, max(1, safe_int_arg('per_page', 50, minimum=1, maximum=500)))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "page and per_page must be integers"}), 400

        query = (
            db.session.query(ApplicationRationalizationScore, ApplicationComponent)
            .join(
                ApplicationComponent,
                ApplicationRationalizationScore.application_component_id == ApplicationComponent.id,
            )
        )

        if status_filter:
            query = query.filter(
                ApplicationRationalizationScore.review_status == status_filter
            )
        else:
            # Default: return draft + reviewed first so the queue highlights pending actions
            query = query.order_by(
                db.case(
                    {
                        "draft": 0,
                        "reviewed": 1,
                        "rejected": 2,
                        "approved": 3,
                        "exception_approved": 4,
                    },
                    value=ApplicationRationalizationScore.review_status,
                    else_=5,
                ),
                ApplicationRationalizationScore.overall_health_score.asc(),
            )

        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()

        results = []
        for score, app_obj in rows:
            current_status = score.review_status or "draft"
            results.append(
                {
                    "app_id": app_obj.id,
                    "app_name": app_obj.name,
                    "rationalization_action": score.rationalization_action,
                    "disposition_action": score.disposition_action,
                    "overall_health_score": score.overall_health_score,
                    "review_status": current_status,
                    "reviewed_by": score.reviewed_by,
                    "reviewed_at": score.reviewed_at.isoformat() if score.reviewed_at else None,
                    "approved_by": score.approved_by,
                    "approved_at": score.approved_at.isoformat() if score.approved_at else None,
                    "review_notes": score.review_notes,
                    "allowed_transitions": ApplicationRationalizationScore.REVIEW_TRANSITIONS.get(
                        current_status, []
                    ),
                }
            )

        return jsonify(
            {
                "success": True,
                "total": total,
                "page": page,
                "per_page": per_page,
                "results": results,
            }
        )
    except Exception as exc:
        current_app.logger.error(f"Error fetching review queue: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ── RAT-119: Portfolio triage workbench ──────────────────────────────────


@unified_applications_bp.route("/rationalization/api/portfolio-workbench")
@login_required
def rationalization_portfolio_workbench():
    """Filtered portfolio triage view for decision workbench.

    RAT-119: Supports multi-dimensional filtering by readiness, confidence,
    disposition, TIME label, review status, business unit, and free-text search.
    Returns paginated results with global facet counts for filter UI.
    """
    from app.models.application_rationalization import ApplicationRationalizationScore
    from sqlalchemy import func

    try:
        # Parse filters
        readiness = request.args.get("readiness", "").strip().lower() or None
        confidence = request.args.get("confidence", "").strip().lower() or None
        dispositions = [d.strip() for d in request.args.get("disposition", "").split(",") if d.strip()] or None
        time_actions = [t.strip().upper() for t in request.args.get("time_action", "").split(",") if t.strip()] or None
        review_statuses = [s.strip() for s in request.args.get("review_status", "").split(",") if s.strip()] or None
        business_unit = request.args.get("business_unit", "").strip() or None
        lifecycle = request.args.get("lifecycle", "").strip() or None
        search = request.args.get("search", "").strip() or None
        sort_by = request.args.get("sort_by", "score").strip().lower()
        sort_dir = request.args.get("sort_dir", "asc").strip().lower()

        include_unscored = request.args.get("include_unscored", "false").strip().lower() == "true"

        try:
            page = max(1, safe_int_arg('page', 1, minimum=1))
            per_page = min(1000, max(1, safe_int_arg('per_page', 200, minimum=1, maximum=500)))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "page/per_page must be integers"}), 400

        if include_unscored:
            # Left outer join: show ALL apps, scored or not
            query = (
                db.session.query(ApplicationComponent, ApplicationRationalizationScore)
                .outerjoin(
                    ApplicationRationalizationScore,
                    ApplicationRationalizationScore.application_component_id == ApplicationComponent.id,
                )
            )
        else:
            # Default: only scored apps (inner join)
            query = (
                db.session.query(ApplicationRationalizationScore, ApplicationComponent)
                .join(ApplicationComponent, ApplicationRationalizationScore.application_component_id == ApplicationComponent.id)
            )

        # Apply filters (score-dependent filters only apply when not include_unscored or score exists)
        if not include_unscored:
            if readiness == "ready":
                query = query.filter(ApplicationRationalizationScore.is_decision_ready == True)  # noqa: E712
            elif readiness == "not_ready":
                query = query.filter(ApplicationRationalizationScore.is_decision_ready == False)  # noqa: E712

            if confidence:
                query = query.filter(ApplicationRationalizationScore.disposition_confidence == confidence)

            if dispositions:
                query = query.filter(ApplicationRationalizationScore.disposition_action.in_(dispositions))

            if time_actions:
                query = query.filter(ApplicationRationalizationScore.rationalization_action.in_(time_actions))

            if review_statuses:
                query = query.filter(ApplicationRationalizationScore.review_status.in_(review_statuses))

        if business_unit:
            query = query.filter(ApplicationComponent.business_unit.ilike(f"%{business_unit}%"))  # model-safety-ok

        if lifecycle:
            query = query.filter(ApplicationComponent.lifecycle_status.ilike(f"%{lifecycle}%"))  # model-safety-ok

        if search:
            query = query.filter(ApplicationComponent.name.ilike(f"%{search}%"))

        # Sorting
        if include_unscored:
            sort_col = ApplicationComponent.name
            query = query.order_by(sort_col.asc())
        else:
            sort_map = {
                "score": ApplicationRationalizationScore.overall_health_score,
                "name": ApplicationComponent.name,
                "disposition": ApplicationRationalizationScore.disposition_action,
                "readiness": ApplicationRationalizationScore.readiness_score,
            }
            sort_col = sort_map.get(sort_by, ApplicationRationalizationScore.overall_health_score)
            query = query.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()

        # Build facets from the unfiltered base — always reflect global counts
        base_q = db.session.query(ApplicationRationalizationScore)

        disp_counts = dict(
            base_q.with_entities(
                ApplicationRationalizationScore.disposition_action,
                func.count(ApplicationRationalizationScore.id),
            ).group_by(ApplicationRationalizationScore.disposition_action).all()
        )

        conf_counts = dict(
            base_q.with_entities(
                ApplicationRationalizationScore.disposition_confidence,
                func.count(ApplicationRationalizationScore.id),
            ).group_by(ApplicationRationalizationScore.disposition_confidence).all()
        )

        ready_count = base_q.filter(ApplicationRationalizationScore.is_decision_ready == True).count()  # noqa: E712
        not_ready_count = base_q.filter(ApplicationRationalizationScore.is_decision_ready == False).count()  # noqa: E712

        status_counts = dict(
            base_q.with_entities(
                ApplicationRationalizationScore.review_status,
                func.count(ApplicationRationalizationScore.id),
            ).group_by(ApplicationRationalizationScore.review_status).all()
        )

        results = []
        for row in rows:
            if include_unscored:
                app_obj, score = row
            else:
                score, app_obj = row
            results.append({
                "app_id": app_obj.id,
                "app_name": app_obj.name,
                "business_unit": getattr(app_obj, "business_unit", None),  # model-safety-ok
                "rationalization_action": score.rationalization_action if score else None,
                "disposition_action": score.disposition_action if score else None,
                "disposition_confidence": score.disposition_confidence if score else None,
                "overall_health_score": score.overall_health_score if score else None,
                "is_decision_ready": score.is_decision_ready if score else False,
                "readiness_score": score.readiness_score if score else None,
                "review_status": (score.review_status or "draft") if score else "unscored",
            })

        filters_applied = {}
        if readiness:
            filters_applied["readiness"] = readiness
        if confidence:
            filters_applied["confidence"] = confidence
        if dispositions:
            filters_applied["disposition"] = dispositions
        if time_actions:
            filters_applied["time_action"] = time_actions
        if review_statuses:
            filters_applied["review_status"] = review_statuses
        if business_unit:
            filters_applied["business_unit"] = business_unit
        if lifecycle:
            filters_applied["lifecycle"] = lifecycle
        if search:
            filters_applied["search"] = search

        # Lifecycle facet from ApplicationComponent
        lifecycle_counts = dict(
            db.session.query(
                ApplicationComponent.lifecycle_status,
                func.count(ApplicationComponent.id),
            ).filter(ApplicationComponent.lifecycle_status.isnot(None))
            .group_by(ApplicationComponent.lifecycle_status).all()
        )

        return jsonify({
            "success": True,
            "total": total,
            "page": page,
            "per_page": per_page,
            "filters_applied": filters_applied,
            "results": results,
            "facets": {
                "dispositions": {k: v for k, v in disp_counts.items() if k},
                "confidence_levels": {k: v for k, v in conf_counts.items() if k},
                "readiness": {"ready": ready_count, "not_ready": not_ready_count},
                "review_statuses": {k: v for k, v in status_counts.items() if k},
                "lifecycle_statuses": {k: v for k, v in lifecycle_counts.items() if k},
            },
        })
    except Exception as exc:
        current_app.logger.error(f"Error in portfolio workbench: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ── RAT-112: ARB governance endpoints ────────────────────────────────────


@unified_applications_bp.route(
    "/rationalization/api/arb-submit/<int:app_id>", methods=["POST"]
)
@login_required
def api_arb_submit(app_id):
    """Submit a rationalization recommendation to ARB for governance review.

    RAT-112: Only applicable to governed dispositions (retire, replace, consolidate).
    Sets arb_required=True and arb_submission_status='submitted'.
    """
    from app.models.application_rationalization import ApplicationRationalizationScore

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()
        if not score:
            return jsonify(
                {"success": False, "error": f"No rationalization score found for application {app_id}"}
            ), 404

        disposition = (getattr(score, "disposition_action", None) or "").lower()  # model-safety-ok
        if disposition not in ApplicationRationalizationScore.GOVERNED_DISPOSITIONS:
            return jsonify(
                {
                    "success": False,
                    "error": (
                        f"Disposition '{disposition}' does not require ARB governance. "
                        f"Only the following dispositions are governed: "
                        f"{sorted(ApplicationRationalizationScore.GOVERNED_DISPOSITIONS)}"
                    ),
                }
            ), 422

        submitter = None
        if current_user and current_user.is_authenticated:
            submitter = (  # model-safety-ok
                getattr(current_user, "email", None)
                or getattr(current_user, "username", None)
            )

        arb_before = {
            "arb_required": getattr(score, "arb_required", False),  # model-safety-ok
            "arb_submission_status": getattr(score, "arb_submission_status", None),  # model-safety-ok
        }
        score.arb_required = True
        score.arb_submission_status = "submitted"
        score.arb_submitted_at = datetime.utcnow()
        score.arb_submitted_by = submitter

        # RAT-114: Record audit trail entry for ARB submission
        _log_rationalization_audit(
            app_id=app_id,
            score_id=score.id,
            action="arb_submitted",
            actor=submitter or "unknown",
            before_state=arb_before,
            after_state={"arb_required": True, "arb_submission_status": "submitted"},
            details=f"Disposition: {disposition}",
        )

        db.session.commit()

        current_app.logger.info(
            "RAT-112: ARB submission recorded for app %s by %s (disposition=%s)",
            app_id, submitter, disposition,
        )
        return jsonify(
            {
                "success": True,
                "arb_required": score.arb_required,
                "arb_submission_status": score.arb_submission_status,
                "arb_submitted_at": score.arb_submitted_at.isoformat() if score.arb_submitted_at else None,
                "arb_submitted_by": score.arb_submitted_by,
            }
        )
    except Exception as exc:
        current_app.logger.error(f"RAT-112: Error submitting to ARB for app {app_id}: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/arb-status/<int:app_id>", methods=["GET"]
)
@login_required
def api_arb_status(app_id):
    """Return the ARB governance status for the given application's rationalization score.

    RAT-112: Returns arb_required, submission status, and decision fields.
    If no score exists, returns arb_required=False with success=True.
    """
    from app.models.application_rationalization import ApplicationRationalizationScore

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()
        if not score:
            return jsonify(
                {
                    "success": True,
                    "arb_required": False,
                    "arb_submission_status": None,
                    "arb_decision": None,
                    "arb_decision_at": None,
                    "arb_decision_notes": None,
                    "arb_submitted_by": None,
                    "arb_submitted_at": None,
                    "disposition_action": None,
                }
            )

        return jsonify(
            {
                "success": True,
                "arb_required": bool(getattr(score, "arb_required", False)),  # model-safety-ok
                "arb_submission_status": getattr(score, "arb_submission_status", None),  # model-safety-ok
                "arb_decision": getattr(score, "arb_decision", None),  # model-safety-ok
                "arb_decision_at": (
                    score.arb_decision_at.isoformat()  # model-safety-ok
                    if getattr(score, "arb_decision_at", None) else None
                ),
                "arb_decision_notes": getattr(score, "arb_decision_notes", None),  # model-safety-ok
                "arb_submitted_by": getattr(score, "arb_submitted_by", None),  # model-safety-ok
                "arb_submitted_at": (
                    score.arb_submitted_at.isoformat()  # model-safety-ok
                    if getattr(score, "arb_submitted_at", None) else None
                ),
                "disposition_action": score.disposition_action,
            }
        )
    except Exception as exc:
        current_app.logger.error(f"RAT-112: Error fetching ARB status for app {app_id}: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/arb-decide/<int:app_id>", methods=["POST"]
)
@login_required
def api_arb_decide(app_id):
    """Record an ARB decision for a submitted rationalization recommendation.

    RAT-112: Accepts decision (approved/approved_with_conditions/rejected/deferred) and
    optional notes.  If decision=approved, auto-transitions review_status from
    'reviewed' to 'approved'.

    Request body::

        {
            "decision": "approved" | "approved_with_conditions" | "rejected" | "deferred",
            "notes": "optional decision rationale"
        }
    """
    from app.models.application_rationalization import ApplicationRationalizationScore

    VALID_DECISIONS = {"approved", "approved_with_conditions", "rejected", "deferred"}

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": f"Application {app_id} not found"}), 404

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()
        if not score:
            return jsonify(
                {"success": False, "error": f"No rationalization score found for application {app_id}"}
            ), 404

        if not getattr(score, "arb_required", False):  # model-safety-ok
            return jsonify(
                {"success": False, "error": "This recommendation has not been submitted to ARB"}
            ), 422

        data = request.get_json(silent=True) or {}
        decision = (data.get("decision") or "").strip().lower()
        if decision not in VALID_DECISIONS:
            return jsonify(
                {
                    "success": False,
                    "error": f"decision must be one of: {sorted(VALID_DECISIONS)}",
                }
            ), 400

        notes = (data.get("notes") or "").strip() or None
        now = datetime.utcnow()

        arb_decide_before = {
            "arb_decision": getattr(score, "arb_decision", None),  # model-safety-ok
            "arb_submission_status": getattr(score, "arb_submission_status", None),  # model-safety-ok
            "review_status": score.review_status,
        }

        score.arb_decision = decision
        score.arb_decision_at = now
        score.arb_decision_notes = notes
        score.arb_submission_status = decision  # mirrors decision in status field

        # Auto-transition: if ARB approves and the score is in 'reviewed', advance to 'approved'
        if decision == "approved":
            current_review = score.review_status or "draft"
            allowed_next = ApplicationRationalizationScore.REVIEW_TRANSITIONS.get(current_review, [])
            if "approved" in allowed_next:
                decider = None
                if current_user and current_user.is_authenticated:
                    decider = (  # model-safety-ok
                        getattr(current_user, "email", None)
                        or getattr(current_user, "username", None)
                    )
                score.review_status = "approved"
                score.approved_by = decider
                score.approved_at = now
                if notes:
                    score.review_notes = notes

        # RAT-114: Record audit trail entry for ARB decision
        arb_actor = None
        if current_user and current_user.is_authenticated:
            arb_actor = (  # model-safety-ok
                getattr(current_user, "email", None)
                or getattr(current_user, "username", None)
            )
        _log_rationalization_audit(
            app_id=app_id,
            score_id=score.id,
            action="arb_decided",
            actor=arb_actor or "unknown",
            before_state=arb_decide_before,
            after_state={
                "arb_decision": decision,
                "arb_submission_status": decision,
                "review_status": score.review_status,
            },
            details=notes,
        )

        db.session.commit()

        current_app.logger.info(
            "RAT-112: ARB decision '%s' recorded for app %s", decision, app_id
        )
        return jsonify(
            {
                "success": True,
                "arb_decision": score.arb_decision,
                "arb_decision_at": score.arb_decision_at.isoformat() if score.arb_decision_at else None,
                "arb_submission_status": score.arb_submission_status,
                "review_status": score.review_status,
            }
        )
    except Exception as exc:
        current_app.logger.error(f"RAT-112: Error recording ARB decision for app {app_id}: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ── RAT-113: Override mechanism ──────────────────────────────────────────


@unified_applications_bp.route(
    "/rationalization/api/override/<int:app_id>", methods=["POST"]
)
@login_required
def api_create_override(app_id):
    """Create or update a time-bounded override on the system recommendation."""
    try:
        from datetime import timedelta

        from app.models.application_rationalization import (
            ApplicationRationalizationScore,
            DispositionAction,
        )

        data = request.get_json(silent=True) or {}

        disposition = (data.get("disposition") or "").strip()
        rationale = (data.get("rationale") or "").strip()
        expiry_days = data.get("expiry_days")

        # Validate disposition
        valid_dispositions = {d.value for d in DispositionAction}
        if disposition not in valid_dispositions:
            return jsonify({"success": False, "error": f"Invalid disposition. Must be one of: {', '.join(sorted(valid_dispositions))}"}), 400

        # Validate rationale length
        if len(rationale) < 20:
            return jsonify({"success": False, "error": "Rationale must be at least 20 characters."}), 400

        # Validate expiry_days
        try:
            expiry_days = int(expiry_days)
        except (TypeError, ValueError):
            expiry_days = None
        if not expiry_days or expiry_days < 1 or expiry_days > 365:
            return jsonify({"success": False, "error": "expiry_days must be an integer between 1 and 365."}), 400

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()
        if not score:
            return jsonify({"success": False, "error": "No rationalization score found for this application."}), 404

        actor_name = ""
        try:
            actor_name = current_user.display_name or current_user.email or str(current_user.id)  # model-safety-ok
        except Exception:
            actor_name = "unknown"

        now = datetime.utcnow()
        original = getattr(score, "override_original_disposition", None) or score.disposition_action  # model-safety-ok
        override_before = {
            "override_active": bool(getattr(score, "override_active", False)),  # model-safety-ok
            "override_disposition": getattr(score, "override_disposition", None),  # model-safety-ok
            "system_disposition": score.disposition_action,
        }

        score.override_active = True
        score.override_disposition = disposition
        score.override_rationale = rationale
        score.override_actor = actor_name
        score.override_created_at = now
        score.override_expiry = now + timedelta(days=expiry_days)
        score.override_original_disposition = original

        # RAT-114: Record audit trail entry for override creation
        _log_rationalization_audit(
            app_id=app_id,
            score_id=score.id,
            action="override_created",
            actor=actor_name,
            before_state=override_before,
            after_state={
                "override_active": True,
                "override_disposition": disposition,
                "expiry_days": expiry_days,
            },
            details=rationale,
        )

        db.session.commit()
        current_app.logger.info(
            "RAT-113 override created: app_id=%d disposition=%s actor=%s expiry_days=%d",
            app_id, disposition, actor_name, expiry_days,
        )

        return jsonify({
            "success": True,
            "override_active": True,
            "override_disposition": score.override_disposition,
            "override_rationale": score.override_rationale,
            "override_actor": score.override_actor,
            "override_created_at": score.override_created_at.isoformat() if score.override_created_at else None,
            "override_expiry": score.override_expiry.isoformat() if score.override_expiry else None,
            "override_original_disposition": score.override_original_disposition,
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("RAT-113 create override error: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred."}), 500


@unified_applications_bp.route(
    "/rationalization/api/override/<int:app_id>", methods=["GET"]
)
@login_required
def api_get_override(app_id):
    """Return the current override status for an application."""
    try:
        from app.models.application_rationalization import ApplicationRationalizationScore

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()
        if not score:
            return jsonify({"success": False, "error": "No rationalization score found."}), 404

        now = datetime.utcnow()
        override_active = bool(getattr(score, "override_active", False))  # model-safety-ok
        override_expiry = getattr(score, "override_expiry", None)  # model-safety-ok
        override_created_at = getattr(score, "override_created_at", None)  # model-safety-ok
        is_expired = False
        if override_active and override_expiry:
            is_expired = now >= override_expiry

        return jsonify({
            "success": True,
            "override_active": override_active,
            "override_disposition": getattr(score, "override_disposition", None),  # model-safety-ok
            "override_rationale": getattr(score, "override_rationale", None),  # model-safety-ok
            "override_actor": getattr(score, "override_actor", None),  # model-safety-ok
            "override_created_at": override_created_at.isoformat() if override_created_at else None,
            "override_expiry": override_expiry.isoformat() if override_expiry else None,
            "override_original_disposition": getattr(score, "override_original_disposition", None),  # model-safety-ok
            "system_disposition": score.disposition_action,
            "is_expired": is_expired,
        })
    except Exception as exc:
        current_app.logger.error("RAT-113 get override error: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred."}), 500


@unified_applications_bp.route(
    "/rationalization/api/override/<int:app_id>", methods=["DELETE"]
)
@login_required
def api_delete_override(app_id):
    """Remove an active override, restoring the system recommendation."""
    try:
        from app.models.application_rationalization import ApplicationRationalizationScore

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()
        if not score:
            return jsonify({"success": False, "error": "No rationalization score found."}), 404

        remove_before = {
            "override_active": bool(getattr(score, "override_active", False)),  # model-safety-ok
            "override_disposition": getattr(score, "override_disposition", None),  # model-safety-ok
        }
        score.override_active = False

        remove_actor = ""
        try:
            remove_actor = (  # model-safety-ok
                getattr(current_user, "email", None)
                or getattr(current_user, "username", None)
                or "unknown"
            )
        except Exception:
            remove_actor = "unknown"

        # RAT-114: Record audit trail entry for override removal
        _log_rationalization_audit(
            app_id=app_id,
            score_id=score.id,
            action="override_removed",
            actor=remove_actor,
            before_state=remove_before,
            after_state={"override_active": False},
        )

        db.session.commit()
        current_app.logger.info("RAT-113 override removed: app_id=%d", app_id)
        return jsonify({"success": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("RAT-113 delete override error: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred."}), 500


# ── RAT-114: Audit trail endpoint ────────────────────────────────────────


@unified_applications_bp.route("/rationalization/api/audit-trail/<int:app_id>")
@login_required
def rationalization_audit_trail(app_id):
    """RAT-114: Retrieve audit trail for an application's rationalization decisions.

    Query parameters:
      - page (int, default 1)
      - per_page (int, default 25, max 100)
      - action (str, optional) — filter by specific action type
    """
    from app.models.application_rationalization import RationalizationAuditEntry

    require_entity(ApplicationComponent, app_id, description="Application not found")

    try:
        try:
            page = max(1, safe_int_arg('page', 1, minimum=1))
            per_page = min(100, max(1, safe_int_arg('per_page', 25, minimum=1, maximum=500)))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "page/per_page must be integers"}), 400

        action_filter = request.args.get("action", "").strip() or None

        query = RationalizationAuditEntry.query.filter_by(application_id=app_id)
        if action_filter:
            query = query.filter_by(action=action_filter)
        query = query.order_by(RationalizationAuditEntry.created_at.desc())

        total = query.count()
        entries = query.offset((page - 1) * per_page).limit(per_page).all()

        results = []
        for entry in entries:
            results.append(
                {
                    "id": entry.id,
                    "action": entry.action,
                    "actor": entry.actor,
                    "actor_type": entry.actor_type,
                    "before_state": entry.before_state,
                    "after_state": entry.after_state,
                    "details": entry.details,
                    "created_at": entry.created_at.isoformat() if entry.created_at else None,
                }
            )

        return jsonify(
            {
                "success": True,
                "total": total,
                "page": page,
                "per_page": per_page,
                "entries": results,
            }
        )
    except Exception as exc:
        current_app.logger.error(
            f"Error fetching audit trail for app {app_id}: {exc}", exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/decision-dossier/<int:app_id>")
@login_required
def rationalization_decision_dossier(app_id):
    """RAT-120: Comprehensive decision dossier for a single application."""
    from app.models.application_rationalization import (
        ApplicationDependency,
        ApplicationRationalizationScore,
    )

    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": "Application not found"}), 404

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()

        # Build dossier sections
        dossier = {
            "application": {
                "id": app_obj.id,
                "name": app_obj.name,
                "business_unit": getattr(app_obj, "business_unit", None),  # model-safety-ok
                "lifecycle_status": getattr(app_obj, "lifecycle_status", None),  # model-safety-ok
                "owner": getattr(app_obj, "owner", None),  # model-safety-ok
            },
        }

        if not score:
            dossier["has_score"] = False
            return jsonify({"success": True, **dossier})

        dossier["has_score"] = True

        # Recommendation section
        dossier["recommendation"] = {
            "rationalization_action": score.rationalization_action,
            "disposition_action": score.disposition_action,
            "disposition_confidence": getattr(score, "disposition_confidence", None),  # model-safety-ok
            "confidence_reasons": getattr(score, "confidence_reasons", None),  # model-safety-ok
            "action_rationale": score.action_rationale,
            "overall_health_score": score.overall_health_score,
            "priority": score.priority,
        }

        # Score dimensions
        dossier["scores"] = {
            "technical_health": score.technical_health_score,
            "business_value": score.business_value_score,
            "cost_efficiency": score.cost_efficiency_score,
            "vendor_risk": score.vendor_risk_score,
        }

        # Readiness section
        dossier["readiness"] = {
            "is_decision_ready": getattr(score, "is_decision_ready", None),  # model-safety-ok
            "readiness_score": getattr(score, "readiness_score", None),  # model-safety-ok
            "readiness_dimensions": getattr(score, "readiness_dimensions", None),  # model-safety-ok
        }

        # Review status
        dossier["review"] = {
            "review_status": getattr(score, "review_status", "draft"),  # model-safety-ok
            "reviewed_by": getattr(score, "reviewed_by", None),  # model-safety-ok
            "reviewed_at": getattr(score, "reviewed_at", None),  # model-safety-ok
            "approved_by": getattr(score, "approved_by", None),  # model-safety-ok
            "approved_at": getattr(score, "approved_at", None),  # model-safety-ok
            "review_notes": getattr(score, "review_notes", None),  # model-safety-ok
        }
        # Serialize datetimes
        for key in ("reviewed_at", "approved_at"):
            val = dossier["review"][key]
            if val and hasattr(val, "isoformat"):
                dossier["review"][key] = val.isoformat()

        # ARB governance
        dossier["arb"] = {
            "arb_required": getattr(score, "arb_required", False),  # model-safety-ok
            "arb_submission_status": getattr(score, "arb_submission_status", None),  # model-safety-ok
            "arb_decision": getattr(score, "arb_decision", None),  # model-safety-ok
            "arb_decision_notes": getattr(score, "arb_decision_notes", None),  # model-safety-ok
        }

        # Override status
        dossier["override"] = {
            "override_active": getattr(score, "override_active", False),  # model-safety-ok
            "override_disposition": getattr(score, "override_disposition", None),  # model-safety-ok
            "override_rationale": getattr(score, "override_rationale", None),  # model-safety-ok
            "override_actor": getattr(score, "override_actor", None),  # model-safety-ok
        }

        # Dependencies
        deps_out = ApplicationDependency.query.filter_by(source_app_id=app_id).limit(20).all()
        deps_in = ApplicationDependency.query.filter_by(target_app_id=app_id).limit(20).all()
        dossier["dependencies"] = {
            "outbound_count": len(deps_out),
            "inbound_count": len(deps_in),
            "critical_blockers": [
                {
                    "target_app_id": d.target_app_id,
                    "dependency_type": d.dependency_type,
                    "dependency_strength": d.dependency_strength,
                    "blocks_retirement": d.blocks_retirement,
                }
                for d in deps_out
                if d.blocks_retirement
            ],
        }

        # Financial impact
        dossier["financial"] = {
            "estimated_annual_savings": (
                float(score.estimated_annual_savings) if score.estimated_annual_savings else None
            ),
            "estimated_investment_needed": (
                float(score.estimated_investment_needed) if score.estimated_investment_needed else None
            ),
            "total_cost_of_ownership": (
                float(score.total_cost_of_ownership) if score.total_cost_of_ownership else None
            ),
        }

        return jsonify({"success": True, **dossier})
    except Exception as exc:
        current_app.logger.error(
            "Error building decision dossier for app %d: %s", app_id, exc, exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def _arb_gate_reason(score):
    """Why this score may not advance to "approved" yet, or None if it may.

    RAT-112 states the rule on the model -- governed dispositions (retire,
    replace, consolidate) require an ARB approval before the review workflow
    advances to "approved" -- and nothing enforced it. The single-application
    ARB decision endpoint is the legitimate path to "approved" for these; the
    bulk review endpoint reached the same state without consulting ARB at all.
    """
    if not getattr(score, "requires_arb", False):  # model-safety-ok
        return None
    if (getattr(score, "arb_decision", None) or "").lower() in {  # model-safety-ok
        "approved",
        "approved_with_conditions",
    }:
        return None
    return (
        "Disposition '%s' is ARB-governed and has no ARB approval; "
        "submit it to ARB and record the decision first."
        % (score.disposition_action or "")
    )


@unified_applications_bp.route("/rationalization/api/bulk-review", methods=["POST"])
@login_required
def rationalization_bulk_review():
    """RAT-121: Bulk review action for multiple applications."""
    from app.models.application_rationalization import (
        ApplicationRationalizationScore,
        DispositionAction,
    )

    try:
        data = request.get_json(silent=True) or {}
        app_ids = data.get("app_ids", [])
        action = (data.get("action") or "").strip().lower()
        notes = (data.get("notes") or "").strip()
        # Read alongside action/notes from the same payload. Its extraction had been
        # removed while the "set_disposition" branch below still used it, so that
        # branch raised NameError after already having mutated earlier records in
        # the batch.
        disposition_value = (data.get("disposition") or "").strip()

        if not app_ids or not isinstance(app_ids, list):
            return jsonify({"success": False, "error": "app_ids must be a non-empty list"}), 400

        if len(app_ids) > 50:
            return jsonify({"success": False, "error": "Maximum 50 applications per bulk action"}), 400

        # "set_disposition" was implemented below but omitted from this set, so
        # every request for it was rejected with 400 before reaching the branch.
        # That is the portfolio_manager persona's core action -- recording a 7R
        # disposition against a scored application -- and it was unreachable.
        valid_actions = {"approve", "defer", "request_data", "set_disposition"}
        if action not in valid_actions:
            return jsonify({"success": False, "error": f"action must be one of: {sorted(valid_actions)}"}), 400

        if action == "set_disposition":
            allowed_dispositions = {d.value for d in DispositionAction}
            if disposition_value not in allowed_dispositions:
                return jsonify({
                    "success": False,
                    "error": "disposition must be one of: %s" % sorted(allowed_dispositions),
                }), 400

        actor_name = getattr(current_user, "display_name", None) or getattr(current_user, "username", "system")  # model-safety-ok

        scores = ApplicationRationalizationScore.query.filter(
            ApplicationRationalizationScore.application_component_id.in_(app_ids)
        ).all()

        score_map = {s.application_component_id: s for s in scores}

        results = {"processed": [], "skipped": [], "errors": []}

        for aid in app_ids:
            score = score_map.get(aid)
            if not score:
                results["skipped"].append({"app_id": aid, "reason": "No rationalization score"})
                continue

            current_status = getattr(score, "review_status", "draft") or "draft"  # model-safety-ok
            transitions = ApplicationRationalizationScore.REVIEW_TRANSITIONS.get(current_status, [])

            if action == "approve":
                if "approved" not in transitions:
                    results["skipped"].append({"app_id": aid, "reason": f"Cannot approve from status: {current_status}"})
                    continue
                blocked = _arb_gate_reason(score)
                if blocked:
                    results["skipped"].append({"app_id": aid, "reason": blocked})
                    continue
                score.review_status = "approved"
                score.approved_by = actor_name
                score.approved_at = datetime.utcnow()
                if notes:
                    score.review_notes = (getattr(score, "review_notes", "") or "") + f"\n[Bulk approve] {notes}"  # model-safety-ok
                results["processed"].append({"app_id": aid, "new_status": "approved"})

                # RATA-013: Auto-create consolidation entry on approval
                _auto_create_consolidation_for_app(aid, score, actor_name)

            elif action == "defer":
                score.review_status = "draft"
                if notes:
                    score.review_notes = (getattr(score, "review_notes", "") or "") + f"\n[Deferred] {notes}"  # model-safety-ok
                results["processed"].append({"app_id": aid, "new_status": "draft"})

            elif action == "request_data":
                score.review_notes = (getattr(score, "review_notes", "") or "") + f"\n[Data requested by {actor_name}] {notes}"  # model-safety-ok
                results["processed"].append({"app_id": aid, "action": "data_requested"})

            elif action == "set_disposition":
                score.disposition_action = disposition_value
                score.disposition_confidence = "manual"
                score.review_notes = (getattr(score, "review_notes", "") or "") + f"\n[Disposition set to {disposition_value} by {actor_name}] {notes}".rstrip()  # model-safety-ok

                # RAT-112: retire/replace/consolidate are governed. Setting one
                # does NOT approve it -- it flags the record as needing ARB and
                # leaves the review status where it was. This branch previously
                # set review_status="approved" unconditionally, which would have
                # let a portfolio manager retire an application with no ARB
                # decision at all, through the model's own governance rule.
                if _arb_gate_reason(score):
                    score.arb_required = True
                    results["processed"].append({
                        "app_id": aid,
                        "new_status": score.review_status or "draft",
                        "disposition": disposition_value,
                        "arb_required": True,
                    })
                    continue

                if "approved" not in transitions:
                    results["processed"].append({
                        "app_id": aid,
                        "new_status": current_status,
                        "disposition": disposition_value,
                    })
                    continue

                score.review_status = "approved"
                score.approved_by = actor_name
                score.approved_at = datetime.utcnow()
                results["processed"].append({"app_id": aid, "new_status": "approved", "disposition": disposition_value})
                _auto_create_consolidation_for_app(aid, score, actor_name)

        db.session.commit()

        return jsonify({
            "success": True,
            "summary": {
                "processed": len(results["processed"]),
                "skipped": len(results["skipped"]),
            },
            "results": results,
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error in bulk review: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/create-roadmap-item/<int:app_id>", methods=["POST"])
@login_required
def rationalization_create_roadmap_item(app_id):
    """RAT-115: Generate a roadmap item from an approved rationalization recommendation."""
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.models.consolidation_list import ConsolidationListEntry

    try:
        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()

        if not score:
            return jsonify({"success": False, "error": "No rationalization score found"}), 404

        review_status = getattr(score, "review_status", "draft")  # model-safety-ok
        if review_status != "approved":
            return jsonify({
                "success": False,
                "error": f"Only approved recommendations can generate roadmap items (current status: {review_status})",
            }), 400

        data = request.get_json(silent=True) or {}
        owner = (data.get("owner") or "").strip()
        target_date = (data.get("target_date") or "").strip()
        notes = (data.get("notes") or "").strip()

        if not owner:
            return jsonify({"success": False, "error": "owner is required"}), 400

        app_obj = ApplicationComponent.query.get(app_id)
        app_name = app_obj.name if app_obj else f"App #{app_id}"

        disposition = getattr(score, "disposition_action", None) or score.rationalization_action  # model-safety-ok

        note_parts = [f"Generated from rationalization recommendation ({disposition})."]
        if notes:
            note_parts.append(notes)
        combined_notes = " ".join(note_parts)

        entry = ConsolidationListEntry(
            application_id=app_id,
            recommended_action="add_to_roadmap",
            source_type="rationalization",
            source_group_name=f"Rationalization: {disposition}",
            notes=combined_notes,
            status="planned",
            priority=getattr(score, "priority", "medium") or "medium",  # model-safety-ok
            assigned_to=owner,
        )

        if target_date:
            try:
                from datetime import datetime as dt
                entry.target_date = dt.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"success": False, "error": "target_date must be YYYY-MM-DD"}), 400

        db.session.add(entry)
        db.session.commit()

        current_app.logger.info("RAT-115 roadmap item created: app_id=%d entry_id=%d", app_id, entry.id)
        return jsonify({
            "success": True,
            "roadmap_item_id": entry.id,
            "message": f"Roadmap item created for {app_name}",
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("RAT-115 create roadmap item error app_id=%d: %s", app_id, exc, exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/roadmap-status/<int:app_id>")
@login_required
def rationalization_roadmap_status(app_id):
    """RAT-115: Check if a roadmap item exists for this application."""
    from app.models.consolidation_list import ConsolidationListEntry
    from app.models.application_rationalization import ApplicationRationalizationScore

    require_entity(ApplicationComponent, app_id, description="Application not found")

    try:
        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()

        existing = ConsolidationListEntry.query.filter_by(
            application_id=app_id,
            recommended_action="add_to_roadmap",
        ).first()

        return jsonify({
            "success": True,
            "has_roadmap_item": existing is not None,
            "roadmap_item_id": existing.id if existing else None,
            "can_create": score is not None and getattr(score, "review_status", "draft") == "approved",  # model-safety-ok
            "review_status": getattr(score, "review_status", "draft") if score else None,  # model-safety-ok
        })
    except Exception as exc:
        current_app.logger.error("RAT-115 roadmap status error app_id=%d: %s", app_id, exc, exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/decommission-plan/<int:app_id>")
@login_required
def rationalization_get_decommission_plan(app_id):
    """RAT-116: Get decommission plan for an application."""
    from app.models.application_rationalization import DecommissionPlan, ApplicationRationalizationScore

    require_entity(ApplicationComponent, app_id, description="Application not found")

    try:
        plan = DecommissionPlan.query.filter_by(application_id=app_id).first()
        score = ApplicationRationalizationScore.query.filter_by(application_component_id=app_id).first()

        disposition = getattr(score, "disposition_action", None) if score else None  # model-safety-ok
        requires_plan = disposition in ("retire", "replace")

        if not plan:
            return jsonify({
                "success": True,
                "has_plan": False,
                "requires_plan": requires_plan,
                "disposition": disposition,
            })

        return jsonify({
            "success": True,
            "has_plan": True,
            "requires_plan": requires_plan,
            "disposition": disposition,
            "plan": {
                "id": plan.id,
                "plan_status": plan.plan_status,
                "plan_owner": plan.plan_owner,
                "migration_approach": plan.migration_approach,
                "migration_steps": plan.migration_steps,
                "data_migration_plan": plan.data_migration_plan,
                "cutover_date": plan.cutover_date.isoformat() if plan.cutover_date else None,
                "cutover_steps": plan.cutover_steps,
                "downtime_window": plan.downtime_window,
                "validation_criteria": plan.validation_criteria,
                "smoke_test_plan": plan.smoke_test_plan,
                "rollback_steps": plan.rollback_steps,
                "rollback_trigger": plan.rollback_trigger,
                "rollback_window": plan.rollback_window,
                "closure_criteria": plan.closure_criteria,
                "data_retention_period": plan.data_retention_period,
                "communication_plan": plan.communication_plan,
                "affected_teams": plan.affected_teams,
                "created_by": plan.created_by,
                "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
            },
        })
    except Exception as exc:
        current_app.logger.error(
            "Error fetching decommission plan for app %d: %s", app_id, exc, exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/decommission-plan/<int:app_id>", methods=["POST"]
)
@login_required
def rationalization_save_decommission_plan(app_id):
    """RAT-116: Create or update decommission plan."""
    from app.models.application_rationalization import DecommissionPlan, ApplicationRationalizationScore

    try:
        score = ApplicationRationalizationScore.query.filter_by(application_component_id=app_id).first()
        disposition = getattr(score, "disposition_action", None) if score else None  # model-safety-ok

        if disposition not in ("retire", "replace"):
            return jsonify({
                "success": False,
                "error": (
                    f"Decommission plans are only for retire/replace dispositions "
                    f"(current: {disposition})"
                ),
            }), 400

        data = request.get_json(silent=True) or {}

        plan = DecommissionPlan.query.filter_by(application_id=app_id).first()
        if not plan:
            plan = DecommissionPlan(
                application_id=app_id,
                score_id=score.id if score else None,
                created_by=(
                    getattr(current_user, "display_name", None)  # model-safety-ok
                    or getattr(current_user, "username", "system")  # model-safety-ok
                ),
            )
            db.session.add(plan)

        # Update scalar text fields from request
        for field in [
            "plan_owner",
            "migration_approach",
            "data_migration_plan",
            "downtime_window",
            "smoke_test_plan",
            "rollback_trigger",
            "rollback_window",
            "data_retention_period",
            "communication_plan",
        ]:
            if field in data:
                setattr(plan, field, data[field])

        # Update JSON array fields
        for json_field in [
            "migration_steps",
            "cutover_steps",
            "validation_criteria",
            "rollback_steps",
            "closure_criteria",
            "affected_teams",
        ]:
            if json_field in data:
                setattr(plan, json_field, data[json_field])

        if "cutover_date" in data and data["cutover_date"]:
            try:
                from datetime import datetime as dt
                plan.cutover_date = dt.strptime(data["cutover_date"], "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"success": False, "error": "cutover_date must be YYYY-MM-DD"}), 400

        if "plan_status" in data:
            valid_statuses = {"draft", "reviewed", "approved", "in_progress", "completed"}
            if data["plan_status"] not in valid_statuses:
                return jsonify({
                    "success": False,
                    "error": f"plan_status must be one of {sorted(valid_statuses)}",
                }), 400
            plan.plan_status = data["plan_status"]

        db.session.commit()

        return jsonify({"success": True, "plan_id": plan.id, "plan_status": plan.plan_status})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            "Error saving decommission plan for app %d: %s", app_id, exc, exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/workflow-status/<int:app_id>")
@login_required
def rationalization_workflow_status(app_id):
    """RAT-126: Get end-to-end workflow status for an application."""
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.models.consolidation_list import ConsolidationListEntry

    require_entity(ApplicationComponent, app_id, description="Application not found")

    try:
        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_id
        ).first()

        roadmap_item = ConsolidationListEntry.query.filter_by(
            application_id=app_id,
            recommended_action="add_to_roadmap",
        ).first()

        steps = {
            "assessed": score is not None,
            "scored": score is not None and score.overall_health_score is not None,
            "readiness_checked": score is not None and getattr(score, "readiness_dimensions", None) is not None,  # model-safety-ok
            "evidence_available": score is not None and score.action_rationale is not None,
            "reviewed": score is not None and getattr(score, "review_status", "draft") in ("reviewed", "approved", "exception_approved"),  # model-safety-ok
            "approved": score is not None and getattr(score, "review_status", "draft") in ("approved", "exception_approved"),  # model-safety-ok
            "arb_governed": score is not None and getattr(score, "arb_required", False),  # model-safety-ok
            "arb_decided": score is not None and getattr(score, "arb_decision", None) is not None,  # model-safety-ok
            "roadmap_created": roadmap_item is not None,
        }

        total_steps = len(steps)
        completed_steps = sum(1 for v in steps.values() if v)
        completion_pct = int((completed_steps / total_steps) * 100) if total_steps > 0 else 0

        return jsonify({
            "success": True,
            "app_id": app_id,
            "steps": steps,
            "completion_pct": completion_pct,
            "current_phase": _determine_current_phase(steps),
        })
    except Exception as exc:
        current_app.logger.error(
            "Error fetching workflow status for app %s: %s", app_id, exc, exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def _determine_current_phase(steps):
    """Determine the current workflow phase from step completion."""
    if steps.get("roadmap_created"):
        return "execution"
    if steps.get("approved"):
        return "approved"
    if steps.get("reviewed"):
        return "review"
    if steps.get("scored"):
        return "assessment"
    return "initial"


@unified_applications_bp.route("/rationalization/api/benefits/<int:app_id>")
@login_required
def rationalization_get_benefits(app_id):
    """RAT-117: Get benefits tracking for an application."""
    from app.models.application_rationalization import RationalizationBenefitsTracker

    require_entity(ApplicationComponent, app_id, description="Application not found")

    try:
        tracker = RationalizationBenefitsTracker.query.filter_by(
            application_id=app_id
        ).first()
        if not tracker:
            return jsonify({"success": True, "has_tracking": False})
        return jsonify(
            {
                "success": True,
                "has_tracking": True,
                "benefits": {
                    "tracking_status": getattr(tracker, "tracking_status", None),  # model-safety-ok
                    "projected_annual_savings": (
                        float(tracker.projected_annual_savings)
                        if tracker.projected_annual_savings is not None
                        else None
                    ),
                    "projected_risk_reduction": getattr(tracker, "projected_risk_reduction", None),  # model-safety-ok
                    "projected_simplification_score": getattr(tracker, "projected_simplification_score", None),  # model-safety-ok
                    "projected_timeline_months": getattr(tracker, "projected_timeline_months", None),  # model-safety-ok
                    "actual_annual_savings": (
                        float(tracker.actual_annual_savings)
                        if tracker.actual_annual_savings is not None
                        else None
                    ),
                    "actual_risk_reduction": getattr(tracker, "actual_risk_reduction", None),  # model-safety-ok
                    "actual_simplification_score": getattr(tracker, "actual_simplification_score", None),  # model-safety-ok
                    "actual_timeline_months": getattr(tracker, "actual_timeline_months", None),  # model-safety-ok
                    "savings_variance_pct": getattr(tracker, "savings_variance_pct", None),  # model-safety-ok
                    "measurement_date": (
                        tracker.measurement_date.isoformat()
                        if tracker.measurement_date
                        else None
                    ),
                    "measured_by": getattr(tracker, "measured_by", None),  # model-safety-ok
                    "measurement_notes": getattr(tracker, "measurement_notes", None),  # model-safety-ok
                },
            }
        )
    except Exception as exc:
        current_app.logger.error(
            "Error fetching benefits for app %d: %s", app_id, exc, exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/rationalization/api/benefits/<int:app_id>", methods=["POST"]
)
@login_required
def rationalization_save_benefits(app_id):
    """RAT-117: Create or update benefits tracking."""
    from datetime import datetime as dt

    from app.models.application_rationalization import (
        ApplicationRationalizationScore,
        RationalizationBenefitsTracker,
    )

    try:
        data = request.get_json(silent=True) or {}
        tracker = RationalizationBenefitsTracker.query.filter_by(
            application_id=app_id
        ).first()
        if not tracker:
            score = ApplicationRationalizationScore.query.filter_by(
                application_component_id=app_id
            ).first()
            tracker = RationalizationBenefitsTracker(
                application_id=app_id,
                score_id=score.id if score else None,
            )
            db.session.add(tracker)

        for field in ["projected_annual_savings", "actual_annual_savings"]:
            if field in data and data[field] is not None:
                setattr(tracker, field, data[field])

        for field in [
            "projected_risk_reduction",
            "actual_risk_reduction",
            "measurement_notes",
            "measured_by",
        ]:
            if field in data:
                setattr(tracker, field, data[field])

        for field in [
            "projected_simplification_score",
            "actual_simplification_score",
            "projected_timeline_months",
            "actual_timeline_months",
        ]:
            if field in data and data[field] is not None:
                setattr(tracker, field, int(data[field]))

        if "tracking_status" in data:
            valid = {"projected", "in_progress", "measured", "validated"}
            if data["tracking_status"] not in valid:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"tracking_status must be one of {sorted(valid)}",
                        }
                    ),
                    400,
                )
            tracker.tracking_status = data["tracking_status"]

        if "measurement_date" in data and data["measurement_date"]:
            try:
                tracker.measurement_date = dt.strptime(
                    data["measurement_date"], "%Y-%m-%d"
                ).date()
            except ValueError:
                return (
                    jsonify(
                        {"success": False, "error": "measurement_date must be YYYY-MM-DD"}
                    ),
                    400,
                )

        tracker.calculate_variance()
        db.session.commit()
        return jsonify(
            {
                "success": True,
                "tracking_status": tracker.tracking_status,
                "savings_variance_pct": tracker.savings_variance_pct,
            }
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            "Error saving benefits for app %d: %s", app_id, exc, exc_info=True
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/model-metadata")
@login_required
def rationalization_model_metadata():
    """RAT-118: Get current scoring model metadata and version."""
    from app.services.rationalization_scoring_service import RationalizationScoringService

    try:
        metadata = RationalizationScoringService.get_model_metadata()
        return jsonify({"success": True, **metadata})
    except Exception as exc:
        current_app.logger.error(f"Error fetching model metadata: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/calibration-report")
@login_required
def rationalization_calibration_report():
    """RAT-118: Generate calibration report from realized outcomes."""
    from app.services.rationalization_scoring_service import RationalizationScoringService

    try:
        report = RationalizationScoringService.compute_calibration_adjustments()
        return jsonify({"success": True, **report})
    except Exception as exc:
        current_app.logger.error(f"Error generating calibration report: {exc}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/rationalization/api/executive-summary")
@login_required
def rationalization_executive_summary():
    """RAT-122: Portfolio-level executive summary for rationalization posture."""
    from app.models.application_rationalization import ApplicationRationalizationScore
    from sqlalchemy import func

    try:
        # Portfolio disposition distribution
        disposition_dist = dict(
            db.session.query(
                ApplicationRationalizationScore.disposition_action,
                func.count(ApplicationRationalizationScore.id),
            ).filter(
                ApplicationRationalizationScore.disposition_action.isnot(None)
            ).group_by(ApplicationRationalizationScore.disposition_action).all()
        )

        # TIME distribution
        time_dist = dict(
            db.session.query(
                ApplicationRationalizationScore.rationalization_action,
                func.count(ApplicationRationalizationScore.id),
            ).group_by(ApplicationRationalizationScore.rationalization_action).all()
        )

        # Score distribution (health score buckets: 0-25, 26-50, 51-75, 76-100)
        total_scored = ApplicationRationalizationScore.query.count()
        score_buckets = {
            "critical_0_25": ApplicationRationalizationScore.query.filter(
                ApplicationRationalizationScore.overall_health_score <= 25
            ).count(),
            "poor_26_50": ApplicationRationalizationScore.query.filter(
                ApplicationRationalizationScore.overall_health_score > 25,
                ApplicationRationalizationScore.overall_health_score <= 50,
            ).count(),
            "fair_51_75": ApplicationRationalizationScore.query.filter(
                ApplicationRationalizationScore.overall_health_score > 50,
                ApplicationRationalizationScore.overall_health_score <= 75,
            ).count(),
            "good_76_100": ApplicationRationalizationScore.query.filter(
                ApplicationRationalizationScore.overall_health_score > 75,
            ).count(),
        }

        # Financial summary
        savings_agg = db.session.query(
            func.sum(ApplicationRationalizationScore.estimated_annual_savings),
            func.sum(ApplicationRationalizationScore.estimated_investment_needed),
        ).first()

        # Readiness overview
        ready_count = ApplicationRationalizationScore.query.filter(
            ApplicationRationalizationScore.is_decision_ready == True  # noqa: E712
        ).count()
        not_ready_count = ApplicationRationalizationScore.query.filter(
            ApplicationRationalizationScore.is_decision_ready == False  # noqa: E712
        ).count()

        # Review status distribution
        review_dist = dict(
            db.session.query(
                ApplicationRationalizationScore.review_status,
                func.count(ApplicationRationalizationScore.id),
            ).group_by(ApplicationRationalizationScore.review_status).all()
        )

        # Confidence distribution
        confidence_dist = dict(
            db.session.query(
                ApplicationRationalizationScore.disposition_confidence,
                func.count(ApplicationRationalizationScore.id),
            ).filter(
                ApplicationRationalizationScore.disposition_confidence.isnot(None)
            ).group_by(ApplicationRationalizationScore.disposition_confidence).all()
        )

        return jsonify({
            "success": True,
            "total_scored": total_scored,
            "disposition_distribution": {k: v for k, v in disposition_dist.items() if k},
            "time_distribution": {k: v for k, v in time_dist.items() if k},
            "score_buckets": score_buckets,
            "financial": {
                "total_projected_savings": float(savings_agg[0]) if savings_agg[0] else 0,
                "total_investment_needed": float(savings_agg[1]) if savings_agg[1] else 0,
            },
            "readiness": {
                "ready": ready_count,
                "not_ready": not_ready_count,
            },
            "review_status_distribution": {k: v for k, v in review_dist.items() if k},
            "confidence_distribution": {k: v for k, v in confidence_dist.items() if k},
        })
    except Exception as exc:
        current_app.logger.exception(
            "Error generating executive summary: %s", exc
        )
        # No zeroed distributions or financials here: a 0 saving is
        # indistinguishable from a measured one, and the dashboard would
        # render a fully-scored-and-worthless portfolio out of a DB error.
        return jsonify({
            "success": False,
            "error": "Could not generate the executive summary",
        }), 500


# ── RATA-002: Scoring HTTP endpoints ─────────────────────────────────────


@unified_applications_bp.route(
    "/rationalization/api/score/<int:app_id>", methods=["POST"]
)
@login_required
def rationalization_score_app(app_id):
    """RATA-002/REQ-RAT-102: Score a single application."""
    from app.models.application_rationalization import (
        RationalizationBenefitsTracker,
    )
    from app.services.rationalization_scoring_service import (
        RationalizationScoringService,
    )

    app_obj = ApplicationComponent.query.get(app_id)
    if not app_obj:
        return jsonify({"success": False, "error": "Application not found"}), 404

    try:
        score = RationalizationScoringService.calculate_app_score(app_id, app=app_obj)
        if not score:
            return jsonify({"success": False, "error": "Scoring failed — check logs"}), 500

        # Auto-create benefits tracker record (REQ-RAT-102)
        existing_tracker = RationalizationBenefitsTracker.query.filter_by(
            application_id=app_id, score_id=score.id
        ).first()
        if not existing_tracker:
            tracker = RationalizationBenefitsTracker(
                application_id=app_id,
                score_id=score.id,
                projected_annual_savings=score.estimated_annual_savings or 0,
            )
            db.session.add(tracker)
            db.session.commit()

        # Build data quality metadata
        readiness = RationalizationScoringService.evaluate_readiness(app_obj)

        _log_rationalization_audit(
            app_id=app_id,
            score_id=score.id,
            action="score_calculated",
            actor=current_user.email if current_user else "system",
            details={"overall_score": score.overall_health_score, "action": score.rationalization_action},
        )

        return jsonify({
            "success": True,
            "score": {
                "id": score.id,
                "overall_health_score": score.overall_health_score,
                "technical_health_score": score.technical_health_score,
                "business_value_score": score.business_value_score,
                "cost_efficiency_score": score.cost_efficiency_score,
                "vendor_risk_score": score.vendor_risk_score,
                "rationalization_action": score.rationalization_action,
                "disposition_action": score.disposition_action,
                "disposition_confidence": score.disposition_confidence,
                "estimated_annual_savings": float(score.estimated_annual_savings or 0),
            },
            "data_quality": {
                "readiness_score": readiness["readiness_score"],
                "is_decision_ready": readiness["is_decision_ready"],
                "missing_critical": readiness["missing_critical"],
                "dimensions": readiness["dimensions"],
            },
        })
    except Exception as exc:
        logger.error("RATA-002 score app %s failed: %s", app_id, exc, exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 500


@unified_applications_bp.route("/rationalization/api/bulk-score", methods=["POST"])
@login_required
def rationalization_bulk_score():
    """RATA-002/REQ-RAT-103: Bulk score portfolio with scope control."""
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.services.rationalization_scoring_service import (
        RationalizationScoringService,
    )

    payload = request.get_json(silent=True) or {}
    scope = payload.get("scope", "all")
    explicit_ids = payload.get("app_ids", [])

    try:
        if explicit_ids:
            apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(explicit_ids)
            ).all()
        elif scope == "detected":
            # Apps in duplicate groups
            from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

            group_rows = (
                db.session.query(UnifiedDuplicateGroup.application_ids)
                .filter(UnifiedDuplicateGroup.status != "dismissed")
                .all()
            )
            all_ids = set()
            for row in group_rows:
                if row.application_ids:
                    for aid in row.application_ids:
                        all_ids.add(int(aid))
            apps = (
                ApplicationComponent.query.filter(
                    ApplicationComponent.id.in_(list(all_ids))
                ).all()
                if all_ids
                else []
            )
        elif scope == "unscored":
            scored_ids = [
                r[0]
                for r in db.session.query(
                    ApplicationRationalizationScore.application_component_id
                )
                .distinct()
                .all()
            ]
            from app.services.rationalization_scoring_service import (
                RationalizationScoringService as _RSS,
            )
            base_filter = ApplicationComponent.lifecycle_status.in_(
                _RSS.SCOREABLE_LIFECYCLE_VALUES
            )
            query = ApplicationComponent.query.filter(base_filter)
            if scored_ids:
                query = query.filter(~ApplicationComponent.id.in_(scored_ids))
            apps = query.all()
        else:
            # scope == "all" — delegate to portfolio scorer
            results = RationalizationScoringService.calculate_portfolio_scores(
                force_recalculate=payload.get("force", False)
            )
            total = results.get("total_apps", 0)
            dq_report = _build_data_quality_report(total)
            return jsonify({
                "success": results.get("success", False),
                "scored": results.get("processed", 0),
                "skipped": total - results.get("processed", 0) - results.get("errors", 0),
                "errors": results.get("errors", 0),
                "time_distribution": results.get("time_distribution", {}),
                "average_scores": results.get("average_scores", {}),
                "data_quality_report": dq_report,
            })

        # For non-"all" scopes, score individually
        scored = 0
        skipped = 0
        errors = 0
        for app_obj in apps:
            try:
                score = RationalizationScoringService.calculate_app_score(
                    app_obj.id, app=app_obj
                )
                if score:
                    scored += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.error("Bulk score error for app %s: %s", app_obj.id, exc)
                errors += 1

        db.session.commit()
        dq_report = _build_data_quality_report(len(apps))

        return jsonify({
            "success": True,
            "scored": scored,
            "skipped": skipped,
            "errors": errors,
            "data_quality_report": dq_report,
        })

    except Exception as exc:
        logger.error("RATA-002 bulk score failed: %s", exc, exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 500


@unified_applications_bp.route("/rationalization/api/rescore-all", methods=["POST"])
@login_required
def rationalization_rescore_all():
    """Convenience alias: rescore all apps (force=False by default, skips recent scores).

    POST body (optional JSON):
      {"force": true}   — recalculate even when a recent score exists (within 30 days)

    This delegates to calculate_portfolio_scores() which already has 30-day caching:
    apps scored within the past 30 days are reused; only un-scored (or stale) apps
    are re-computed.  Pass force=true to re-run all 850+ apps unconditionally.
    """
    from app.services.rationalization_scoring_service import RationalizationScoringService

    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force", False))

    try:
        results = RationalizationScoringService.calculate_portfolio_scores(
            force_recalculate=force
        )
        total = results.get("total_apps", 0)
        dq_report = _build_data_quality_report(total)
        return jsonify({
            "success": results.get("success", False),
            "scored": results.get("processed", 0),
            "skipped": total - results.get("processed", 0) - results.get("errors", 0),
            "errors": results.get("errors", 0),
            "total_apps": total,
            "time_distribution": results.get("time_distribution", {}),
            "average_scores": results.get("average_scores", {}),
            "data_quality_report": dq_report,
        })
    except Exception as exc:
        logger.error("rescore-all failed: %s", exc, exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 500


def _build_data_quality_report(total_apps: int) -> dict:
    """REQ-RAT-117: Build field coverage report after scoring."""
    try:
        from app.models.application_capability import ApplicationCapabilityMapping

        apps = ApplicationComponent.query.limit(total_apps or 1000).all()
        if not apps:
            return {"total_processed": 0, "field_coverage": {}}

        count = len(apps)
        app_ids = [a.id for a in apps]

        owner_count = sum(
            1 for a in apps if a.application_owner and a.application_owner.strip()
        )
        lifecycle_count = sum(
            1 for a in apps if a.lifecycle_status or a.deployment_status
        )
        cost_count = sum(
            1
            for a in apps
            if any(
                getattr(a, f, None) and float(getattr(a, f, 0) or 0) > 0
                for f in (
                    "total_cost_of_ownership",
                    "license_cost",
                    "maintenance_cost",
                    "infrastructure_cost",
                )
            )
        )
        risk_count = sum(
            1
            for a in apps
            if (a.technical_risk and a.technical_risk.strip())
            or (a.business_risk and a.business_risk.strip())
        )
        # Vendor coverage was never computed here, and the template rendered
        # `field_coverage.get('vendor', 0)` -- a hardcoded zero for a value
        # nothing had measured. A portfolio with a vendor recorded against
        # every application was shown a red "Vendor Coverage 0%" bar on the
        # rationalization dashboard while the applications list, two clicks
        # away, said "5 of 5 applications have a vendor recorded". Same
        # definition as that list (app/modules/applications/routes/
        # list_views.py) so the two can never disagree again.
        vendor_count = sum(
            1
            for a in apps
            if (a.vendor_name and a.vendor_name.strip()) or a.vendor_product_id
        )
        # Capability coverage: apps with at least one mapped capability
        mapped_ids = set(
            r[0]
            for r in db.session.query(
                ApplicationCapabilityMapping.application_component_id
            )
            .filter(
                ApplicationCapabilityMapping.application_component_id.in_(app_ids)
            )
            .distinct()
            .all()
        )
        capability_count = len(mapped_ids)

        return {
            "total_processed": count,
            "field_coverage": {
                "owner": round(owner_count / count * 100, 1) if count else 0,
                "lifecycle": round(lifecycle_count / count * 100, 1) if count else 0,
                "cost": round(cost_count / count * 100, 1) if count else 0,
                "risk": round(risk_count / count * 100, 1) if count else 0,
                "vendor": round(vendor_count / count * 100, 1) if count else 0,
                "capability": round(capability_count / count * 100, 1) if count else 0,
            },
        }
    except Exception as exc:
        logger.error("Data quality report failed: %s", exc)
        return {"total_processed": 0, "field_coverage": {}, "error": str(exc)}


# ── RATA-009: Portfolio scores lightweight API ───────────────────────────


_PORTFOLIO_SCORES_CACHE = {}   # {cache_key: (timestamp, payload)}
_PORTFOLIO_SCORES_TTL = 60     # seconds — short enough to reflect rescoring


@unified_applications_bp.route("/rationalization/api/portfolio-scores")
@login_required
def rationalization_portfolio_scores():
    """RATA-009/REQ-RAT-204: Lightweight bulk read of all scored apps.

    Response is cached for 60 s per disposition filter value to avoid
    hammering the DB on every page load (850 apps × JOIN was causing
    ERR_EMPTY_RESPONSE under load on the 2-worker production server).
    """
    import time
    from app.models.application_rationalization import ApplicationRationalizationScore

    disposition_filter = request.args.get("disposition", "")
    cache_key = f"portfolio_scores:{disposition_filter}"

    # Return cached payload if still fresh
    cached = _PORTFOLIO_SCORES_CACHE.get(cache_key)
    if cached:
        cached_at, payload = cached
        if time.time() - cached_at < _PORTFOLIO_SCORES_TTL:
            resp = current_app.response_class(
                payload, status=200, mimetype="application/json"
            )
            resp.headers["X-Cache"] = "HIT"
            return resp

    try:
        # Select only the columns needed for the TIME quadrant scatter plot —
        # avoids loading full ORM objects for 850 rows.
        query = db.session.query(
            ApplicationComponent.id.label("app_id"),
            ApplicationComponent.name.label("app_name"),
            ApplicationRationalizationScore.technical_health_score,
            ApplicationRationalizationScore.business_value_score,
            ApplicationRationalizationScore.cost_efficiency_score,
            ApplicationRationalizationScore.vendor_risk_score,
            ApplicationRationalizationScore.overall_health_score,
            ApplicationRationalizationScore.rationalization_action,
            ApplicationRationalizationScore.disposition_action,
            ApplicationRationalizationScore.estimated_annual_savings,
        ).join(
            ApplicationComponent,
            ApplicationRationalizationScore.application_component_id
            == ApplicationComponent.id,
        )

        if disposition_filter:
            dispositions = [d.strip() for d in disposition_filter.split(",")]
            query = query.filter(
                ApplicationRationalizationScore.disposition_action.in_(dispositions)
            )

        rows = query.limit(500).all()

        results = [
            {
                "app_id": r.app_id,
                "app_name": r.app_name,
                "technical_health_score": r.technical_health_score,
                "business_value_score": r.business_value_score,
                "cost_efficiency_score": r.cost_efficiency_score,
                "vendor_risk_score": r.vendor_risk_score,
                "overall_health_score": r.overall_health_score,
                "rationalization_action": r.rationalization_action,
                "disposition_action": r.disposition_action,
                "estimated_annual_savings": float(r.estimated_annual_savings or 0),
            }
            for r in rows
        ]

        import json
        payload = json.dumps(results)
        _PORTFOLIO_SCORES_CACHE[cache_key] = (time.time(), payload)

        resp = current_app.response_class(
            payload, status=200, mimetype="application/json"
        )
        resp.headers["X-Cache"] = "MISS"
        return resp

    except Exception as exc:
        logger.exception("RATA-009 portfolio-scores failed: %s", exc)
        return jsonify(
            {"success": False, "error": "Could not load portfolio scores"}
        ), 500


# ── RATA-018: Business case export ───────────────────────────────────────


@unified_applications_bp.route("/rationalization/api/export", methods=["POST"])
@login_required
def rationalization_export():
    """RATA-018/REQ-RAT-206–209: Export rationalization business case."""
    from flask import send_file
    import io
    from app.services.rationalization_export_service import RationalizationExportService

    payload = request.get_json(silent=True) or {}
    fmt = payload.get("format", "csv")
    scope = payload.get("scope", {})

    try:
        if fmt == "csv":
            data = RationalizationExportService.generate_csv(scope)
            return send_file(
                io.BytesIO(data),
                mimetype="text/csv",
                as_attachment=True,
                download_name="rationalization_export.csv",
            )
        elif fmt == "excel":
            data = RationalizationExportService.generate_excel(scope)
            return send_file(
                io.BytesIO(data),
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name="rationalization_business_case.xlsx",
            )
        elif fmt == "pdf":
            data = RationalizationExportService.generate_pdf(scope)
            return send_file(
                io.BytesIO(data),
                mimetype="text/html",
                as_attachment=True,
                download_name="rationalization_business_case.html",
            )
        else:
            return jsonify({"success": False, "error": f"Unknown format: {fmt}"}), 400
    except Exception as exc:
        logger.error("RATA-018 export failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── RATA-011: Dependency batch import ────────────────────────────────────

VALID_DEPENDENCY_TYPES = {
    "api_call", "data_feed", "batch_job", "event_subscription",
    "shared_database", "authentication", "reporting", "orchestration",
}
VALID_DEPENDENCY_STRENGTHS = {"critical", "high", "medium", "low", "optional"}


@unified_applications_bp.route(
    "/rationalization/api/dependencies/import", methods=["POST"]
)
@login_required
def rationalization_import_dependencies():
    """RATA-011/REQ-RAT-110: Batch import application dependency records."""
    import csv
    import io
    from app.models.application_rationalization import ApplicationDependency

    imported = 0
    skipped = 0
    errors_list = []

    try:
        rows = []
        content_type = request.content_type or ""

        if "application/json" in content_type:
            payload = request.get_json(silent=True) or {}
            rows = payload.get("dependencies", [])
        elif "multipart/form-data" in content_type:
            file = request.files.get("file")
            if not file:
                return jsonify({"success": False, "error": "No file uploaded"}), 400
            stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
            reader = csv.DictReader(stream)
            for r in reader:
                rows.append({
                    "source_app_id": int(r.get("source_app_id", 0)),
                    "target_app_id": int(r.get("target_app_id", 0)),
                    "dependency_type": r.get("dependency_type", ""),
                    "dependency_strength": r.get("dependency_strength", "medium"),
                    "blocks_retirement": str(r.get("blocks_retirement", "false")).lower() in ("true", "1", "yes"),
                })
        else:
            return jsonify({"success": False, "error": "Unsupported content type"}), 400

        if not rows:
            return jsonify({"success": True, "imported": 0, "skipped": 0, "errors": []})

        # Validate app IDs in bulk
        all_ids = set()
        for r in rows:
            all_ids.add(r.get("source_app_id"))
            all_ids.add(r.get("target_app_id"))
        existing_ids = {
            row[0]
            for row in db.session.query(ApplicationComponent.id)
            .filter(ApplicationComponent.id.in_(list(all_ids)))
            .all()
        }

        # Check existing deps for deduplication
        existing_deps = set()
        if rows:
            existing_rows = db.session.query(
                ApplicationDependency.source_app_id,
                ApplicationDependency.target_app_id,
            ).all()
            existing_deps = {(r[0], r[1]) for r in existing_rows}

        for idx, row in enumerate(rows):
            src = row.get("source_app_id")
            tgt = row.get("target_app_id")
            dep_type = row.get("dependency_type", "")

            if not src or not tgt:
                errors_list.append({"row": idx + 1, "reason": "Missing source_app_id or target_app_id"})
                continue

            if src not in existing_ids:
                errors_list.append({"row": idx + 1, "reason": f"source_app_id {src} not found"})
                continue
            if tgt not in existing_ids:
                errors_list.append({"row": idx + 1, "reason": f"target_app_id {tgt} not found"})
                continue

            if dep_type and dep_type not in VALID_DEPENDENCY_TYPES:
                errors_list.append({"row": idx + 1, "reason": f"Invalid dependency_type: {dep_type}"})
                continue

            if (src, tgt) in existing_deps:
                skipped += 1
                continue

            strength = row.get("dependency_strength", "medium")
            if strength not in VALID_DEPENDENCY_STRENGTHS:
                strength = "medium"

            dep = ApplicationDependency(
                source_app_id=src,
                target_app_id=tgt,
                dependency_type=dep_type or "api_call",
                dependency_strength=strength,
                blocks_retirement=bool(row.get("blocks_retirement", False)),
            )
            db.session.add(dep)
            existing_deps.add((src, tgt))
            imported += 1

        db.session.commit()

        return jsonify({
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "errors": errors_list,
        })

    except Exception as exc:
        logger.error("RATA-011 dependency import failed: %s", exc, exc_info=True)
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 500


@unified_applications_bp.route(
    "/api/<int:app_id>/elements/check-duplicate", methods=["GET"]
)
@login_required
def api_check_duplicate_element(app_id):
    """Check if an element with the given name exists for this application."""
    require_entity(ApplicationComponent, app_id, description="Application not found")

    try:
        from app.models.models import ArchiMateElement

        name = request.args.get("name", "")
        if not name:
            return jsonify({"exists": False})

        exists = (
            ArchiMateElement.query.filter(
                ArchiMateElement.application_component_id == app_id,
                db.func.lower(ArchiMateElement.name) == name.lower(),
            ).first()
            is not None
        )

        return jsonify({"exists": exists})
    except Exception as e:
        current_app.logger.exception("Error checking duplicate element: %s", e)
        return jsonify(
            {"success": False, "error": "Could not check for a duplicate element"}
        ), 500


# ── RAT-001: Bulk data enrichment page and API ───────────────────────────


@unified_applications_bp.route("/rationalization/enrich")
@login_required
def rationalization_enrich():
    """RAT-001: Bulk data enrichment page — shows top 50 apps by lowest health score."""
    return render_template(
        "applications/rationalization/enrich.html",
        active_tab="enrich",
    )


@unified_applications_bp.route("/rationalization/api/enrich-candidates")
@login_required
def rationalization_enrich_candidates():
    """RAT-001: Return top 50 apps sorted by health score (lowest first) for enrichment."""
    from app.models.application_rationalization import ApplicationRationalizationScore

    try:
        limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 200)

        # Left-join scores so we include apps with no score at all
        query = (
            db.session.query(ApplicationComponent, ApplicationRationalizationScore)
            .outerjoin(
                ApplicationRationalizationScore,
                ApplicationRationalizationScore.application_component_id == ApplicationComponent.id,
            )
            .order_by(
                db.case(
                    (ApplicationRationalizationScore.overall_health_score.is_(None), 0),
                    else_=ApplicationRationalizationScore.overall_health_score,
                ).asc()
            )
            .limit(limit)
        )

        rows = query.all()
        results = []
        for app_obj, score in rows:
            results.append({
                "id": app_obj.id,
                "name": app_obj.name or "Unknown",
                "health_score": score.overall_health_score if score else None,
                "disposition": score.disposition_action if score else None,
                "total_cost_of_ownership": float(app_obj.total_cost_of_ownership or 0),
                "application_owner": app_obj.application_owner or "",
                "business_criticality": app_obj.business_criticality or "",
            })

        return jsonify({"success": True, "applications": results})
    except Exception as exc:
        current_app.logger.error("Enrich candidates failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 500


@unified_applications_bp.route(
    "/rationalization/api/enrich/<int:app_id>", methods=["POST"]
)
@login_required
def rationalization_enrich_app(app_id):
    """RAT-001: Update enrichment fields for a single application."""
    try:
        app_obj = ApplicationComponent.query.get(app_id)
        if not app_obj:
            return jsonify({"success": False, "error": "Application not found"}), 404

        payload = request.get_json(silent=True) or {}
        changed = []

        if "total_cost_of_ownership" in payload:
            val = payload["total_cost_of_ownership"]
            if val is not None:
                try:
                    set_annual_cost(app_obj, Decimal(str(val)))
                    changed.append("total_cost_of_ownership")
                except (ValueError, TypeError):
                    logger.exception("Failed to operation")
                    pass

        if "application_owner" in payload:
            val = (payload["application_owner"] or "").strip()
            if val:
                app_obj.application_owner = val[:100]
                changed.append("application_owner")

        if "business_criticality" in payload:
            val = (payload["business_criticality"] or "").strip()
            allowed = {"Critical", "High", "Medium", "Low"}
            if val in allowed:
                app_obj.business_criticality = val
                changed.append("business_criticality")

        if not changed:
            return jsonify({"success": False, "error": "No valid fields provided"}), 400

        db.session.commit()

        logger.info(
            "RAT-001: Enriched app %s (%s) fields: %s by %s",
            app_id,
            app_obj.name,
            changed,
            current_user.email if hasattr(current_user, "email") else "unknown",
        )

        return jsonify({
            "success": True,
            "updated_fields": changed,
            "app_id": app_id,
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Enrich app %s failed: %s", app_id, exc, exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

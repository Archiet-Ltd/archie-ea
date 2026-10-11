"""CSRF-exempt AJAX update routes for the Applications module.

Extracted from app/routes/unified_applications_routes.py:
- Lines 3463-3509: update_overview
- Lines 3511-3558: update_health_quality
- Lines 6384-6467: update_governance
- Lines 6470-6563: update_resources
- Lines 6566-6738: update_strategy_layer

All 5 routes use @csrf.exempt before @login_required and handle both AJAX
(X-Requested-With) and standard form submissions.
"""

import logging

from flask import current_app, flash, g, jsonify, redirect, request, session, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError, validate_csrf

from app import db
from app.decorators import audit_log
from . import unified_applications_bp
from ._helpers import _verify_app_in_org

logger = logging.getLogger(__name__)


@unified_applications_bp.route("/<int:id>/overview-update", methods=["POST"])
@login_required
@audit_log("update_application_overview")
def update_overview(id):
    """Unified save for Overview: updates app metadata and vendor classifications atomically."""
    app = _verify_app_in_org(id, g.current_org_id, raise_not_found=True)

    try:
        # Validate and sanitize input fields
        name = request.form.get("name", app.name)
        if name and len(name) > 255:
            name = name[:255]

        description = request.form.get("description", app.description)
        if description and len(description) > 5000:
            description = description[:5000]

        component_type = request.form.get("application_type", app.component_type)
        valid_types = {
            "web_application", "mobile_application", "desktop_application",
            "api_service", "microservice", "batch_process", "data_pipeline",
            "integration_platform", "database", "infrastructure", "other", None, ""
        }
        if component_type and component_type not in valid_types:
            component_type = app.component_type

        business_criticality = request.form.get("criticality", app.business_criticality)
        valid_criticalities = {
            "mission_critical", "business_critical", "business_operational",
            "administrative", "low", "medium", "high", "critical", None, ""
        }
        if business_criticality and business_criticality not in valid_criticalities:
            business_criticality = app.business_criticality

        technology_stack = request.form.get("technology_stack", app.technology_stack)
        if technology_stack and len(technology_stack) > 500:
            technology_stack = technology_stack[:500]

        # Update application fields with validated values
        app.name = name
        app.description = description
        app.component_type = component_type
        app.business_criticality = business_criticality
        app.technology_stack = technology_stack
        app.updated_by = current_user.id

        db.session.commit()

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"status": "success", "message": "Application updated successfully"}
        else:
            flash("Application updated successfully!", "success")
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )

    except Exception:
        db.session.rollback()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {
                "status": "error",
                "message": "An error occurred processing the request",
            }, 400
        else:
            flash("Error updating application. Please try again.", "error")
            return redirect(url_for("unified_applications.application_detail", id=id))


@unified_applications_bp.route("/<int:id>/health-quality-update", methods=["POST"])
@login_required
@audit_log("update_health_quality")
def update_health_quality(id):
    """Update health and quality metrics for an application."""
    app = _verify_app_in_org(id, g.current_org_id, raise_not_found=True)

    try:
        # Validate and coerce numeric health/quality metrics
        def _safe_float(value, default, min_val=None, max_val=None):
            """Coerce to float with range validation."""
            if value is None or value == "":
                return default
            try:
                result = float(value)
                if min_val is not None and result < min_val:
                    return min_val
                if max_val is not None and result > max_val:
                    return max_val
                return result
            except (ValueError, TypeError):
                return default

        app.technical_debt_hours = _safe_float(
            request.form.get("technical_debt_hours"),
            app.technical_debt_hours, min_val=0, max_val=100000
        )
        app.maintainability_score = _safe_float(
            request.form.get("maintainability_score"),
            app.maintainability_score, min_val=0, max_val=100
        )
        app.security_score = _safe_float(
            request.form.get("security_score"),
            app.security_score, min_val=0, max_val=100
        )
        app.performance_score = _safe_float(
            request.form.get("performance_score"),
            app.performance_score, min_val=0, max_val=100
        )
        app.updated_by = current_user.id

        db.session.commit()

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {
                "status": "success",
                "message": "Health and quality metrics updated successfully",
            }
        else:
            flash("Health and quality metrics updated successfully!", "success")
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )

    except Exception:
        db.session.rollback()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {
                "status": "error",
                "message": "An error occurred processing the request",
            }, 400
        else:
            flash("Error updating metrics. Please try again.", "error")
            return redirect(url_for("unified_applications.application_detail", id=id))


@unified_applications_bp.route("/<int:id>/governance-update", methods=["POST"])
@login_required
@audit_log("update_governance")
def update_governance(id):
    """Update governance and compliance fields for an application"""
    app = _verify_app_in_org(id, g.current_org_id, raise_not_found=True)

    # Validate CSRF token manually (route exempted from global CSRF)
    try:
        token = request.form.get("csrf_token")
        if not token:
            flash(
                "Security validation failed: CSRF token missing. Please refresh the page and try again.",
                "error",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )
        validate_csrf(token)
    except CSRFError as e:
        current_app.logger.warning(f"CSRF validation failed for app {app.id}: {e}")
        flash(
            "Security validation failed. Please refresh the page and try again.",
            "error",
        )
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )

    try:
        changed_fields = []

        # Data Privacy fields
        contains_pii = request.form.get("contains_pii", "false").lower() == "true"
        if app.pii_data_processed != contains_pii:
            app.pii_data_processed = contains_pii
            changed_fields.append("pii_data_processed")

        is_gdpr = request.form.get("is_gdpr_sensitive", "false").lower() == "true"
        if app.gdpr_compliant != is_gdpr:
            app.gdpr_compliant = is_gdpr
            changed_fields.append("gdpr_compliant")

        # Security Classification fields
        data_classification = (
            request.form.get("data_classification", "").strip() or None
        )
        if app.data_classification != data_classification:
            app.data_classification = data_classification
            changed_fields.append("data_classification")

        business_criticality = (
            request.form.get("business_criticality", "").strip() or None
        )
        if app.business_criticality != business_criticality:
            app.business_criticality = business_criticality
            changed_fields.append("business_criticality")

        if changed_fields:
            db.session.commit()
            change_count = len(changed_fields)
            flash(
                f"Governance settings updated successfully ({change_count} changes).",
                "success",
            )
        else:
            flash("No changes detected.", "info")

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error updating governance for app {id}: {exc}")
        flash("Error updating governance. Please try again.", "danger")

    return redirect(
        url_for(
            "unified_applications.application_detail", id=app.id, tab="architecture"
        )
    )


@unified_applications_bp.route("/<int:id>/resources-update", methods=["POST"])
@login_required
@audit_log("update_resources")
def update_resources(id):
    """Update resource and capacity planning fields for an application"""
    app = _verify_app_in_org(id, g.current_org_id, raise_not_found=True)

    # Validate CSRF token manually (route exempted from global CSRF)
    try:
        token = request.form.get("csrf_token")
        if not token:
            flash(
                "Security validation failed: CSRF token missing. Please refresh the page and try again.",
                "error",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )
        validate_csrf(token)
    except CSRFError as e:
        current_app.logger.warning(f"CSRF validation failed for app {app.id}: {e}")
        flash(
            "Security validation failed. Please refresh the page and try again.",
            "error",
        )
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )

    try:
        # Update personnel/key resource fields
        technical_lead = request.form.get("technical_lead")
        development_team = request.form.get("development_team")
        support_team = request.form.get("support_team")
        product_manager = request.form.get("product_manager")
        business_domain = request.form.get("business_domain")

        # Track changes
        changes = []

        if technical_lead is not None and technical_lead != app.technical_lead:
            app.technical_lead = technical_lead.strip() or None
            changes.append("Technical Lead")

        if development_team is not None and development_team != app.development_team:
            app.development_team = development_team.strip() or None
            changes.append("Development Team")

        if support_team is not None and support_team != app.support_team:
            app.support_team = support_team.strip() or None
            changes.append("Support Team")

        if product_manager is not None and product_manager != app.product_manager:
            app.product_manager = product_manager.strip() or None
            changes.append("Product Manager")

        if business_domain is not None and business_domain != app.business_domain:
            app.business_domain = business_domain.strip() or None
            changes.append("Business Domain")

        if changes:
            db.session.commit()
            flash(f"Resource information updated: {', '.join(changes)}", "success")
        else:
            flash("No changes detected in resource information.", "info")

        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Error updating resources for application {id}: {str(e)}"
        )
        flash("Error updating resource information. Please try again.", "error")
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )


@unified_applications_bp.route("/<int:id>/layers/strategy-update", methods=["POST"])
@login_required
@audit_log("update_strategy_layer")
def update_strategy_layer(id):
    """Update Strategy Layer elements (Capabilities, Resources, Value Streams, Courses of Action)"""
    _verify_app_in_org(id, g.current_org_id, raise_not_found=True)

    try:
        token = request.form.get("csrf_token")
        if not token:
            return jsonify({"success": False, "error": "CSRF token missing"}), 400
        validate_csrf(token)
    except CSRFError as e:
        current_app.logger.warning(
            f"CSRF validation failed for strategy layer app {id}: {e}"
        )
        return jsonify({"success": False, "error": "CSRF validation failed"}), 403

    try:
        # Import required forms and models
        from app.application_mgmt.forms import StrategyLayerForm
        from app.models.business_capabilities import Capability
        from app.models.business_layer import Resource

        form = StrategyLayerForm(request.form)

        if not form.validate():
            # Handle nested form errors (FieldList with FormField)
            def format_errors(errors_dict):
                formatted = []
                for field, msgs in errors_dict.items():
                    if isinstance(msgs, dict):
                        # Nested form errors
                        for sub_field, sub_msgs in msgs.items():
                            if isinstance(sub_msgs, list):
                                formatted.append(
                                    f"{field}.{sub_field}: {', '.join(str(m) for m in sub_msgs)}"
                                )
                            else:
                                formatted.append(f"{field}.{sub_field}: {sub_msgs}")
                    elif isinstance(msgs, list):
                        # Regular field errors
                        formatted.append(f"{field}: {', '.join(str(m) for m in msgs)}")
                    else:
                        formatted.append(f"{field}: {msgs}")
                return formatted

            errors = format_errors(form.errors)
            return jsonify({"success": False, "errors": errors}), 400

        changed_fields = []

        # Process capability updates and deletions
        if form.capabilities.data:
            # OPTIMIZATION: Batch-prefetch all capability objects to avoid N+1 queries
            _form_cap_ids = [
                int(cd.get("id")) for cd in form.capabilities.data if cd.get("id")
            ]
            _form_caps = (
                Capability.query.filter(Capability.id.in_(_form_cap_ids)).all()
                if _form_cap_ids
                else []
            )
            _form_caps_by_id = {c.id: c for c in _form_caps}

            for cap_data in form.capabilities.data:
                cap_id = cap_data.get("id")
                cap_delete = cap_data.get("_delete")

                # Handle deletion
                if cap_id and cap_delete:
                    cap_obj = _form_caps_by_id.get(int(cap_id))
                    if cap_obj:
                        db.session.delete(cap_obj)
                        changed_fields.append(f"capability_{cap_id}_deleted")
                    continue

                # Handle update
                cap_name = cap_data.get("name")
                cap_level = cap_data.get("level")
                cap_maturity = cap_data.get("maturity")
                cap_coverage = cap_data.get("coverage")

                if cap_id and cap_name:
                    cap_obj = _form_caps_by_id.get(int(cap_id))
                    if cap_obj:
                        old_name = cap_obj.name
                        cap_obj.name = cap_name
                        cap_obj.level = cap_level or 1
                        cap_obj.maturity_level = cap_maturity or "initial"
                        cap_obj.coverage_percentage = cap_coverage or 0

                        if old_name != cap_name:
                            changed_fields.append(f"capability_{cap_id}")
                        db.session.add(cap_obj)

        # Process resource updates and deletions
        if form.resources.data:
            # OPTIMIZATION: Batch-prefetch all resource objects to avoid N+1 queries
            _form_res_ids = [
                int(rd.get("id")) for rd in form.resources.data if rd.get("id")
            ]
            _form_resources = (
                Resource.query.filter(Resource.id.in_(_form_res_ids)).all()
                if _form_res_ids
                else []
            )
            _form_resources_by_id = {r.id: r for r in _form_resources}

            for res_data in form.resources.data:
                res_id = res_data.get("id")
                res_delete = res_data.get("_delete")

                # Handle deletion
                if res_id and res_delete:
                    res_obj = _form_resources_by_id.get(int(res_id))
                    if res_obj:
                        db.session.delete(res_obj)
                        changed_fields.append(f"resource_{res_id}_deleted")
                    continue

                # Handle update
                res_name = res_data.get("name")
                res_type = res_data.get("resource_type")
                res_avail = res_data.get("availability")

                if res_id and res_name:
                    res_obj = _form_resources_by_id.get(int(res_id))
                    if res_obj:
                        old_name = res_obj.name
                        res_obj.name = res_name
                        res_obj.resource_type = res_type or "technical"
                        if hasattr(
                            res_obj, "availability"
                        ):  # model-safety-ok: Resource class may not have availability field
                            res_obj.availability = res_avail or "shared"

                        if old_name != res_name:
                            changed_fields.append(f"resource_{res_id}")
                        db.session.add(res_obj)

        db.session.commit()

        if changed_fields:
            try:
                session["strategy_changes"] = changed_fields
            except Exception:
                logger.debug(
                    "Failed to store strategy changes in session", exc_info=True
                )

        return jsonify(
            {
                "success": True,
                "message": f"Strategy layer updated ({len(changed_fields)} changes)",
                "changed": changed_fields,
            }
        )

    except Exception as exc:
        db.session.rollback()
        import traceback

        error_trace = traceback.format_exc()
        current_app.logger.error(
            f"Error updating strategy layer: {str(exc)}\n{error_trace}"
        )
        return jsonify(
            {
                "success": False,
                "error": "An internal error occurred updating the strategy layer.",
            }
        ), 500

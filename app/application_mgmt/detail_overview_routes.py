"""
Detail overview/governance/roadmap routes for Application Management.
"""
# mass-deletion-ok — BE-179 removes 16 manual CSRF blocks replaced by global CSRFProtect

import logging
from datetime import datetime

from flask import current_app, flash, jsonify, render_template, request
from flask_login import login_required

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.implementation_migration import WorkPackage
from . import application_mgmt
from .routes import _redirect_to_detail

logger = logging.getLogger(__name__)


@application_mgmt.route("/applications/<int:id>/roadmap")
@login_required
def application_roadmap(id):
    """Application-specific transformation roadmap"""
    app = ApplicationComponent.query.get_or_404(id)

    # Generate timeline (3 years for application transformation)
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2026, 12, 31)

    # Get application-specific work packages
    work_packages = []

    # Query real work packages for this application
    app_work_packages = WorkPackage.query.filter_by(application_component_id=id).all()

    for wp in app_work_packages:
        # Get implementation events for this work package
        impl_events = []
        if wp.implementation_events:
            for event in wp.implementation_events:
                impl_events.append(
                    {
                        "id": event.id,
                        "name": event.name,
                        "event_date": event.event_date,
                        "event_type": event.event_type,
                        "status": event.status,
                        "description": event.description,
                    }
                )

        # Get triggering business event
        triggering_event = None
        if wp.triggering_event:
            triggering_event = {
                "id": wp.triggering_event.id,
                "name": wp.triggering_event.name,
                "event_type": wp.triggering_event.event_type,
                "time_sensitivity": wp.triggering_event.time_sensitivity,
            }

        work_packages.append(
            {
                "id": wp.id,
                "name": wp.name,
                "description": wp.description,
                "work_package_type": "development",  # Default, could be enhanced
                "status": wp.status or "planned",
                "priority": wp.priority or "medium",
                "assigned_to": wp.owner.username if wp.owner else "Unassigned",
                "start_date": wp.start_date,
                "end_date": wp.end_date,
                "progress_percentage": 0,  # Could be calculated
                "estimated_effort_hours": wp.estimated_effort_hours,
                "technical_details": "",  # Could be added to model
                "implementation_events": impl_events,
                "triggering_event": triggering_event,
            }
        )

    # If no real work packages, add sample data for demonstration
    if not work_packages:
        work_packages = [
            {
                "id": 1,
                "name": "User Authentication Enhancement",
                "description": "Implement modern OAuth 2.0 authentication with MFA support",
                "work_package_type": "enhancement",
                "status": "in_progress",
                "priority": "high",
                "assigned_to": "Security Team",
                "start_date": datetime(2024, 1, 15),
                "end_date": datetime(2024, 3, 30),
                "progress_percentage": 65,
                "estimated_effort_hours": 120,
                "technical_details": "OAuth 2.0, MFA, JWT tokens",
                "implementation_events": [
                    {
                        "id": 1,
                        "name": "Security Review Complete",
                        "event_date": datetime(2024, 2, 1),
                        "event_type": "review",
                        "status": "completed",
                    },
                    {
                        "id": 2,
                        "name": "UAT Start",
                        "event_date": datetime(2024, 3, 15),
                        "event_type": "testing",
                        "status": "planned",
                    },
                ],
                "triggering_event": {
                    "id": 1,
                    "name": "Security Policy Update",
                    "event_type": "internal",
                    "time_sensitivity": "high",
                },
            },
            {
                "id": 2,
                "name": "Database Migration",
                "description": "Migrate from legacy database to PostgreSQL with zero downtime",
                "work_package_type": "migration",
                "status": "planned",
                "priority": "critical",
                "assigned_to": "Database Team",
                "start_date": datetime(2024, 4, 1),
                "end_date": datetime(2024, 6, 30),
                "progress_percentage": 0,
                "estimated_effort_hours": 200,
                "technical_details": "PostgreSQL, migration scripts, data validation",
                "implementation_events": [
                    {
                        "id": 3,
                        "name": "Migration Testing",
                        "event_date": datetime(2024, 5, 15),
                        "event_type": "testing",
                        "status": "planned",
                    },
                    {
                        "id": 4,
                        "name": "Go-Live",
                        "event_date": datetime(2024, 6, 30),
                        "event_type": "deployment",
                        "status": "planned",
                    },
                ],
                "triggering_event": None,
            },
            {
                "id": 3,
                "name": "API Integration",
                "description": "Develop REST API for third-party integrations",
                "work_package_type": "development",
                "status": "in_progress",
                "priority": "medium",
                "assigned_to": "Development Team",
                "start_date": datetime(2024, 2, 1),
                "end_date": datetime(2024, 5, 15),
                "progress_percentage": 40,
                "estimated_effort_hours": 160,
                "technical_details": "REST API, OpenAPI, rate limiting",
                "implementation_events": [
                    {
                        "id": 5,
                        "name": "API Design Review",
                        "event_date": datetime(2024, 2, 28),
                        "event_type": "review",
                        "status": "completed",
                    }
                ],
                "triggering_event": {
                    "id": 2,
                    "name": "Partner Integration Request",
                    "event_type": "external",
                    "time_sensitivity": "medium",
                },
            },
        ]

    return render_template(
        "applications/dashboard.html",
        application=app,
        work_packages=work_packages,
        sample_work_packages=work_packages,  # For JavaScript fallback
        start_date=start_date,
        end_date=end_date,
    )


@application_mgmt.route(
    "/applications/<int:id>/health-quality-update", methods=["POST"]
)
@login_required
def update_health_quality(id):
    """Update health and quality metrics for an application."""
    app = ApplicationComponent.query.get_or_404(id)

    # csrf-ok: global CSRFProtect active

    try:
        # Update health metrics
        if request.form.get("sla_availability_percentage"):
            app.sla_availability_percentage = float(
                request.form.get("sla_availability_percentage")
            )
        if request.form.get("current_uptime_percentage"):
            app.current_uptime_percentage = float(
                request.form.get("current_uptime_percentage")
            )
        if request.form.get("mttr_hours"):
            app.mean_time_to_recovery_hours = float(request.form.get("mttr_hours"))
        if request.form.get("change_failure_rate"):
            app.change_failure_rate_percent = float(
                request.form.get("change_failure_rate")
            )

        app.monitoring_enabled = "monitoring_enabled" in request.form
        if request.form.get("monitoring_tool"):
            app.monitoring_tool = request.form.get("monitoring_tool")
        if request.form.get("health_check_url"):
            app.health_check_url = request.form.get("health_check_url")
        if request.form.get("deployment_frequency"):
            app.deployment_frequency = request.form.get("deployment_frequency")

        # Update quality metrics
        if request.form.get("code_quality_score"):
            app.code_quality_score = float(request.form.get("code_quality_score"))
        if request.form.get("technical_debt_hours"):
            app.technical_debt_hours = int(request.form.get("technical_debt_hours"))
        if request.form.get("bugs_count"):
            app.bugs_count = int(request.form.get("bugs_count"))
        if request.form.get("vulnerabilities_count"):
            app.vulnerabilities_count = int(request.form.get("vulnerabilities_count"))
        if request.form.get("code_coverage_percent"):
            app.code_coverage_percent = float(request.form.get("code_coverage_percent"))
        if request.form.get("automated_testing_coverage"):
            app.automated_testing_coverage = float(
                request.form.get("automated_testing_coverage")
            )

        if request.form.get("last_scan_date"):
            try:
                app.last_code_quality_scan = datetime.strptime(
                    request.form.get("last_scan_date"), "%Y-%m-%d"
                )
            except ValueError:
                pass  # Keep existing value if invalid date

        db.session.commit()
        flash("Health & Quality metrics updated successfully!", "success")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating health quality metrics: {str(e)}")
        flash("Error updating metrics. Please try again.", "error")

    return _redirect_to_detail(app.id, tab="health")


@application_mgmt.route("/applications/<int:id>/governance-update", methods=["POST"])
@login_required
def update_governance(id):
    """Update governance and compliance fields for an application"""
    app = ApplicationComponent.query.get_or_404(id)

    # csrf-ok: global CSRFProtect active

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
        flash(f"Error updating governance: {str(exc)}", "danger")

    return _redirect_to_detail(app.id, tab="governance")


@application_mgmt.route("/applications/<int:id>/resources-update", methods=["POST"])
@login_required
def update_resources(id):
    """Update resource and capacity planning fields for an application"""
    app = ApplicationComponent.query.get_or_404(id)

    # csrf-ok: global CSRFProtect active

    try:
        # Update personnel/key resource fields
        business_owner = request.form.get("business_owner")
        technical_lead = request.form.get("technical_lead")
        development_team = request.form.get("development_team")
        support_team = request.form.get("support_team")
        product_manager = request.form.get("product_manager")
        business_domain = request.form.get("business_domain")

        # Track changes
        changes = []

        if business_owner is not None and business_owner != app.business_owner:
            app.business_owner = business_owner.strip() or None
            changes.append("Business Owner")

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

        db.session.commit()

        if changes:
            flash(f"Resources updated successfully: {', '.join(changes)}", "success")
        else:
            flash("No changes made.", "info")

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error updating resources for app {id}: {exc}")
        flash(f"Error updating resources: {str(exc)}", "danger")

    return _redirect_to_detail(app.id, tab="resources")


@application_mgmt.route("/applications/<int:id>/quick-update", methods=["POST"])
@login_required
def application_quick_update(id):
    """Quick update application from modal - handles all fields via JSON or form data"""
    current_app.logger.info(f"===== quick-update route called for app {id} =====")

    app = ApplicationComponent.query.get_or_404(id)
    current_app.logger.info(f"Found app: {app.name}")

    try:
        # Accept both JSON and form data
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()

        current_app.logger.info(f"Received data: {data}")

        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        # Update all fields from the modal
        if "name" in data:
            app.name = data["name"]
        if "component_type" in data:
            app.component_type = data["component_type"]
        if "deployment_status" in data:
            app.deployment_status = data["deployment_status"]
        if "business_criticality" in data:
            # Convert numeric criticality back to text
            crit_map = {
                "5": "Critical",
                "4": "High",
                "3": "Medium",
                "2": "Low",
                "1": "None",
            }
            app.business_criticality = crit_map.get(
                data["business_criticality"], data["business_criticality"]
            )

        # Business Context & Ownership
        if "business_domain" in data:
            app.business_domain = data["business_domain"]
        if "business_owner" in data:
            app.business_owner = data["business_owner"]
        if "product_manager" in data:
            app.product_manager = data["product_manager"]
        if "technical_lead" in data:
            app.technical_lead = data["technical_lead"]
        if "development_team" in data:
            app.development_team = data["development_team"]
        if "support_team" in data:
            app.support_team = data["support_team"]

        # Users & Usage
        if "user_count" in data:
            app.user_count = int(data["user_count"]) if data["user_count"] else None
        if "user_type" in data:
            app.user_type = data["user_type"]
        if "average_daily_users" in data:
            app.average_daily_users = (
                int(data["average_daily_users"])
                if data["average_daily_users"]
                else None
            )
        if "concurrent_users_max" in data:
            app.concurrent_users_max = (
                int(data["concurrent_users_max"])
                if data["concurrent_users_max"]
                else None
            )

        # Technology Stack
        if "technology_stack" in data:
            app.technology_stack = data["technology_stack"]
        if "programming_languages" in data:
            app.programming_languages = data["programming_languages"]
        if "frameworks" in data:
            app.frameworks = data["frameworks"]
        if "primary_database" in data:
            app.primary_database = data["primary_database"]
        if "cache_technology" in data:
            app.cache_technology = data["cache_technology"]
        if "message_queue" in data:
            app.message_queue = data["message_queue"]

        # Deployment & Infrastructure
        if "deployment_model" in data:
            app.deployment_model = data["deployment_model"]
        if "cloud_provider" in data:
            app.cloud_provider = data["cloud_provider"]
        if "deployment_region" in data:
            app.deployment_region = data["deployment_region"]
        if "container_image" in data:
            app.container_image = data["container_image"]
        if "kubernetes_namespace" in data:
            app.kubernetes_namespace = data["kubernetes_namespace"]

        # Performance & Scalability
        if "response_time_target_ms" in data:
            app.response_time_target_ms = (
                int(data["response_time_target_ms"])
                if data["response_time_target_ms"]
                else None
            )
        if "current_response_time_ms" in data:
            app.current_response_time_ms = (
                int(data["current_response_time_ms"])
                if data["current_response_time_ms"]
                else None
            )
        if "throughput_target_tps" in data:
            app.throughput_target_tps = (
                int(data["throughput_target_tps"])
                if data["throughput_target_tps"]
                else None
            )
        if "current_throughput_tps" in data:
            app.current_throughput_tps = (
                int(data["current_throughput_tps"])
                if data["current_throughput_tps"]
                else None
            )

        # Security & Compliance
        if "authentication_method" in data:
            app.authentication_method = data["authentication_method"]
        if "compliance_tags" in data:
            app.compliance_tags = data["compliance_tags"]
        if "encryption_at_rest" in data:
            app.encryption_at_rest = data["encryption_at_rest"]
        if "encryption_in_transit" in data:
            app.encryption_in_transit = data["encryption_in_transit"]
        if "pii_data_processed" in data:
            app.pii_data_processed = data["pii_data_processed"]
        if "gdpr_compliant" in data:
            app.gdpr_compliant = data["gdpr_compliant"]

        # Operations & Monitoring
        if "monitoring_tool" in data:
            app.monitoring_tool = data["monitoring_tool"]
        if "logging_tool" in data:
            app.logging_tool = data["logging_tool"]
        if "ci_cd_pipeline_url" in data:
            app.ci_cd_pipeline_url = data["ci_cd_pipeline_url"]
        if "health_check_url" in data:
            app.health_check_url = data["health_check_url"]

        # Documentation
        if "documentation_url" in data:
            app.documentation_url = data["documentation_url"]
        if "api_documentation_url" in data:
            app.api_documentation_url = data["api_documentation_url"]
        if "architecture_diagram_url" in data:
            app.architecture_diagram_url = data["architecture_diagram_url"]
        if "runbook_url" in data:
            app.runbook_url = data["runbook_url"]

        # Notes
        if "notes" in data:
            app.notes = data["notes"]

        app.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({"success": True, "message": "Application updated successfully"})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating application {id}: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

"""
Dashboard routes for Application Management.
"""
# mass-deletion-ok — BE-179 removes 16 manual CSRF blocks replaced by global CSRFProtect

from flask import current_app, render_template
from flask_login import login_required
from sqlalchemy import func

from .. import db
from ..models.application_layer import ApplicationInterface
from ..models.application_portfolio import ApplicationComponent
from ..services.application_kind import applications_query
from . import application_mgmt


@application_mgmt.route("/dashboard")
@login_required
def dashboard():
    """
    Application Management Dashboard

    Displays:
    - 4 metric cards (Total Apps, Active Interfaces, Capability Coverage, Tech Debt)
    - Chart: Applications by Type
    - Table: Recent Applications
    """
    # Metric 1: Total Applications
    total_apps = applications_query().count()

    # Metric 2: Active Interfaces
    try:
        active_interfaces = ApplicationInterface.query.filter_by(
            operational_status="active"
        ).count()
    except Exception:
        current_app.logger.debug("Failed to count active interfaces", exc_info=True)
        active_interfaces = 0

    # Metric 3: Capability Coverage (apps with capability mappings)
    # Use raw SQL for counting since mapping table might not have a model
    try:
        # application_capability_mapping.organization_id is NULL on every row
        # in production (added nullable by reconcile-schema, never
        # backfilled) — a predicate directly on it matches zero rows and
        # this metric silently reports 0 apps with capabilities for every
        # org. Scope via the FK parent business_capability instead (it *is*
        # tenant-owned and backfilled). See e622d36 /
        # rationalization_scoring_service.py.
        from app.middleware.tenant_context import current_org_id

        _org = current_org_id()
        # Fully static SQL (no string building) so bandit B608 stays clean; the
        # NULL-checked bound param scopes to the current org in a request context
        # and passes all rows for the no-context (CLI) fallback — identical to the
        # prior conditional-clause form. See rationalization_scoring_service.py.
        result = db.session.execute(
            db.text(
                "SELECT COUNT(DISTINCT acm.application_component_id) "
                "FROM application_capability_mapping acm "
                "JOIN business_capability bc ON bc.id = acm.business_capability_id "
                "WHERE (:org IS NULL OR bc.organization_id = :org)"
            ),
            {"org": _org},
        )
        apps_with_capabilities = result.scalar() or 0
    except Exception:
        current_app.logger.debug("Failed to count apps with capabilities", exc_info=True)
        apps_with_capabilities = 0

    capability_coverage = (
        round((apps_with_capabilities / total_apps * 100), 1) if total_apps > 0 else 0
    )

    # Metric 4: Technical Debt Score (average technical debt hours across all apps)
    # Note: technical_debt_hours field removed from ApplicationComponent model
    avg_tech_debt = 0

    # Chart Data: Applications by Type
    apps_by_type = (
        db.session.query(
            ApplicationComponent.component_type, func.count(ApplicationComponent.id)
        )
        .group_by(ApplicationComponent.component_type)
        .all()
    )

    chart_labels = [row[0] or "Unknown" for row in apps_by_type]
    chart_data = [row[1] for row in apps_by_type]

    # Table Data: Recent Applications (last 10)
    recent_apps = (
        ApplicationComponent.query.order_by(ApplicationComponent.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "applications/dashboard.html",
        total_apps=total_apps,
        active_interfaces=active_interfaces,
        capability_coverage=capability_coverage,
        avg_tech_debt=avg_tech_debt,
        chart_labels=chart_labels,
        chart_data=chart_data,
        recent_apps=recent_apps,
    )

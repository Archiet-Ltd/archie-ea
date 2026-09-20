"""
My Applications Routes (NS-012, NS-013)

Application manager persona dashboards for owned application management.
All queries scoped by current user's ownership records.

ADR Reference: docs/adr/0011-application-manager-persona.md
"""

from datetime import date

from flask import render_template, request
from flask_login import current_user, login_required

from app.decorators import requires_application_owner
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent
from app.utils.pagination import safe_int_arg

from . import my_applications_bp

# routes.py never imported services.py at all - get_owned_apps is defined inline
# below, so the whole services module was dead code despite containing exactly
# the summary functions these templates require. Every page that reads a summary
# raised jinja2.UndefinedError, which is almost certainly the real cause of the
# "every page route returned 500" that got this module disabled on 2026-06-11.
from .services import (
    get_application_health_summary,
    get_owned_applications,
    get_ownership_summary,
    get_user_applications,
)

# Rows the dashboard's My Applications panel lists before it points at the full
# list; the list page shows the same applications, 20 to a page.
DASHBOARD_PANEL_ROWS = 10
LIST_PAGE_SIZE = 20
# Upper bound on a requested page number, so an absurd value stays a small integer.
LIST_MAX_PAGE = 1_000_000


def get_owned_apps():
    """Get applications owned by current user.

    Resolved against ApplicationComponent, not Solution. ApplicationOwner.
    application_id is a foreign key to application_components, so looking those
    ids up in the solutions table matched on nothing more than two independent id
    sequences happening to collide - this persona was shown whichever unrelated
    solution shared an integer with an application it owned, which is both wrong
    and a cross-record disclosure. The templates agree: they read
    application_type, business_criticality, lifecycle_status and hosting_type,
    which are ApplicationComponent fields.

    The definition of "owned by this user" lives in services.py and is shared by
    every count and list on these pages.
    """
    return get_owned_applications(current_user.id)


@my_applications_bp.route("/")
@login_required
@requires_application_owner
def dashboard():
    """Application manager dashboard - overview of owned applications."""
    # Total Apps, the health tiles and the panel rows are all read from the one
    # ownership definition in services.py, so they cannot contradict each other.
    recent_apps, _ = get_user_applications(current_user.id, per_page=DASHBOARD_PANEL_ROWS)

    return render_template(
        "my_applications/dashboard.html",
        recent_apps=recent_apps,
        ownership_summary=get_ownership_summary(current_user.id),
        health_summary=get_application_health_summary(current_user.id),
    )


@my_applications_bp.route("/list")
@login_required
@requires_application_owner
def app_list():
    """List applications owned by current user."""
    # app_list.html iterates item.application.name / item.ownership_type /
    # item.is_primary - it wants ownership records joined to their application,
    # not bare applications. get_user_applications() returns exactly that shape
    # and was written for this template; the inline get_owned_apps() above returns
    # ApplicationComponent rows, so every card raised UndefinedError on
    # item.application and the page 500'd as soon as the user owned anything.
    #
    # The ownership-type tabs, the search box and the pager on that template all
    # send query parameters; the route reads them so each tab lists exactly the
    # rows its count describes.
    ownership_type = request.args.get("type")
    if ownership_type not in ApplicationOwner.OWNERSHIP_TYPES:
        ownership_type = None
    search = (request.args.get("search") or "").strip()
    page = safe_int_arg("page", 1, minimum=1, maximum=LIST_MAX_PAGE)

    # A page past the end (a stale bookmark, or an assignment removed while the
    # list was open) is served as the last page rather than an empty one; the
    # service clamps it before it queries, so no oversized offset reaches the
    # database.
    apps, total = get_user_applications(
        current_user.id,
        ownership_type=ownership_type,
        search=search or None,
        page=page,
        per_page=LIST_PAGE_SIZE,
    )
    last_page = max(1, -(-total // LIST_PAGE_SIZE))
    page = min(page, last_page)

    owned = get_ownership_summary(current_user.id)
    return render_template(
        "my_applications/app_list.html",
        apps=apps,
        # Rows matching the selected tab and search; the pager reads it.
        total=total,
        page=page,
        last_page=last_page,
        ownership_type=ownership_type,
        search=search,
        # Tab counts follow an active search; owned_total is every application
        # the user owns, whatever the search.
        ownership_summary=get_ownership_summary(current_user.id, search=search) if search else owned,
        owned_total=owned["total"],
    )


@my_applications_bp.route("/app/<int:app_id>")
@login_required
@requires_application_owner
def app_detail(app_id):
    """View details of an owned application."""
    # Verify ownership
    # tenant-scoping-ok: self-lookup, filtered by the authenticated user's own id.
    ownership = ApplicationOwner.query.filter_by(
        user_id=current_user.id,
        application_id=app_id
    ).first()

    if not ownership:
        from flask import abort
        abort(403, description="You do not own this application")

    app = ApplicationComponent.query.get_or_404(app_id)

    return render_template(
        "my_applications/app_detail.html",
        app=app,
        ownership=ownership,
    )


@my_applications_bp.route("/health")
@login_required
@requires_application_owner
def health_overview():
    """Health overview of owned applications."""
    apps = get_owned_apps()

    # Group by health status
    by_health = {
        'healthy': [],
        'at_risk': [],
        'critical': [],
        'unknown': [],
    }

    for app in apps:
        health = getattr(app, 'health_status', None) or 'unknown'
        if health in by_health:
            by_health[health].append(app)
        else:
            by_health['unknown'].append(app)

    return render_template(
        "my_applications/health_overview.html",
        apps=apps,
        by_health=by_health,
        health_summary=get_application_health_summary(current_user.id),
    )


@my_applications_bp.route("/roadmap")
@login_required
@requires_application_owner
def roadmap_impact():
    """View roadmap items affecting owned applications."""
    apps = get_owned_apps()

    # Get apps with sunset dates or lifecycle changes
    upcoming_changes = []
    for app in apps:
        sunset = getattr(app, 'sunset_date', None)
        if sunset and sunset > date.today():
            upcoming_changes.append({
                'app': app,
                'date': sunset,
                'type': 'Sunset',
            })

    # Sort by date
    upcoming_changes.sort(key=lambda x: x['date'])

    return render_template(
        "my_applications/roadmap_impact.html",
        apps=apps,
        upcoming_changes=upcoming_changes,
        today=date.today(),
        ownership_summary=get_ownership_summary(current_user.id),
    )

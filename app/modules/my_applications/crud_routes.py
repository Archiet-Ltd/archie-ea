"""Write path for the Application Manager persona.

Until 2026-07-31 this module exposed five routes, all GET. An application manager
could look at the applications they were accountable for and change nothing about
them - not the lifecycle status, not the health assessment, not the named
technical lead. The persona is defined by ownership of a record it could only
read.

Scoping here is by ownership, not just tenancy. Being in the right organisation
is not sufficient: the whole point of the persona is that it manages the subset
of applications it owns, so every handler confirms an ApplicationOwner row links
the current user to the application before writing.

That check has to be explicit. ApplicationOwner carries organization_id but not
TenantMixin, so nothing filters it, and Solution.query.get() would happily return
an application this user has no relationship to at all.
"""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import requires_application_owner_or_org_admin
from app.extensions import db
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent

from . import my_applications_bp

# Vocabularies are pinned here rather than accepted from the form. Both fields
# drive grouped counts on the dashboard and health overview; a value outside the
# set is not merely untidy, it silently vanishes from every total.
LIFECYCLE_STATUSES = [
    "planning", "development", "testing", "operational", "deprecated", "retired",
]
DEPLOYMENT_STATUSES = ["design", "development", "testing", "production"]
HEALTH_STATUSES = ["healthy", "at_risk", "critical"]


def _owned_application_or_404(app_id):
    """The application, only if the current user is a registered owner of it."""
    # tenant-scoping-ok: self-lookup, filtered by the authenticated user's own id.
    ownership = ApplicationOwner.query.filter_by(
        user_id=current_user.id, application_id=app_id
    ).first()
    if not ownership:
        from flask import abort

        # 404, not 403: the user has no relationship to this record, so confirming
        # it exists tells them something they have no standing to learn.
        abort(404)
    return ApplicationComponent.query.get_or_404(app_id)


@my_applications_bp.route("/app/<int:app_id>/edit", methods=["GET", "POST"])
@login_required
@requires_application_owner_or_org_admin
def app_edit(app_id):
    """Maintain the application record you are accountable for."""
    app = _owned_application_or_404(app_id)

    if request.method == "POST":
        form = request.form

        description = (form.get("description") or "").strip()
        app.description = description or None

        # Reject rather than coerce. lifecycle_status carries default="operational",
        # so quietly mapping an unrecognised value to None lets the column default
        # fire and files the application as operational - inventing a meaningful
        # status is worse than storing the junk. Empty still means "not set".
        for field, allowed in (
            ("lifecycle_status", LIFECYCLE_STATUSES),
            ("deployment_status", DEPLOYMENT_STATUSES),
            ("health_status", HEALTH_STATUSES),
        ):
            value = (form.get(field) or "").strip()
            if value and value not in allowed:
                flash("%s is not a recognised value." % field.replace("_", " ").title(), "danger")
                return render_template(
                    "my_applications/app_form.html",
                    app=app, statuses=LIFECYCLE_STATUSES,
                    deployment_statuses=DEPLOYMENT_STATUSES,
                    health_statuses=HEALTH_STATUSES,
                ), 400
            setattr(app, field, value or None)

        for field in ("business_owner", "technical_owner"):
            value = (form.get(field) or "").strip()
            setattr(app, field, value[:255] or None)

        db.session.commit()
        flash("Application updated.", "success")
        return redirect(url_for("my_applications.app_detail", app_id=app.id))

    # AI health assessment "Apply suggestion" links in here as
    # ?suggest_health=...&suggest_lifecycle=..., one or both. This only
    # changes what the <select> shows pre-selected - it never writes to
    # the database on its own, and an out-of-vocabulary value (a stale
    # link, a hand-edited URL) is silently ignored rather than applied,
    # so the owner still has to review and submit the form to save it.
    #
    # Deliberately NOT written onto `app` here: this is a GET handler, but
    # `app` is a session-attached ORM object, so setting an attribute on it
    # leaves it dirty for the rest of the request. Any later commit in the
    # same request - a middleware flush, another view sharing the session -
    # would persist an AI suggestion the owner never reviewed or submitted.
    # Passed to the template as separate variables instead; the form select
    # decides its own preselection.
    suggested_health = request.args.get("suggest_health")
    if suggested_health not in HEALTH_STATUSES:
        suggested_health = None
    suggested_lifecycle = request.args.get("suggest_lifecycle")
    if suggested_lifecycle not in LIFECYCLE_STATUSES:
        suggested_lifecycle = None

    return render_template(
        "my_applications/app_form.html",
        app=app,
        statuses=LIFECYCLE_STATUSES,
        deployment_statuses=DEPLOYMENT_STATUSES,
        health_statuses=HEALTH_STATUSES,
        suggested_health=suggested_health,
        suggested_lifecycle=suggested_lifecycle,
    )

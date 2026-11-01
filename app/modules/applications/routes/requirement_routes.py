"""Link a requirement of an adopted framework to an application.

The requirement list is the one the organisation holds through its adopted frameworks (DORA, NIS2,
ISO 27001 and the rest); the link is the organisation's own application-to-control record, shown on the
application's page.
"""

from __future__ import annotations

from flask import flash, g, redirect, request, url_for
from flask_login import current_user, login_required

from app.core.auth.permissions import can
from app.decorators import audit_log
from app.modules.compliance.services.applicability_service import ApplicabilityService

from . import unified_applications_bp
from ._helpers import _verify_app_in_org


@unified_applications_bp.route("/<int:app_id>/requirements", methods=["POST"])
@login_required
@audit_log("application_requirement_link")
def link_requirement(app_id: int):
    """Link the chosen requirement to this application and return to its page."""
    org_id = g.current_org_id
    app = _verify_app_in_org(app_id, org_id, raise_not_found=True)
    back = url_for("unified_applications.application_detail", id=app.id)
    if not can(current_user, "edit", "application"):
        flash("Your role cannot link requirements to applications.", "error")
        return redirect(back)
    control_id = request.form.get("control_id", type=int)
    if control_id is None:
        flash("Choose a requirement to link.", "error")
        return redirect(back)
    try:
        ApplicabilityService.link_requirement(org_id, app.id, control_id)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(back)
    flash("Requirement linked to %s." % app.name, "success")
    return redirect(back)

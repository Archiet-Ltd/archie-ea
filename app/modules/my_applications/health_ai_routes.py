"""AI health assessment endpoint for the Application Manager persona.

Registered onto my_applications_bp via a side-effect import in
app/modules/my_applications/__init__.py, matching the routes.py /
crud_routes.py pattern already used by this module.

Purely advisory: it returns a suggested health/lifecycle assessment and
writes nothing to the application. Applying a suggestion still goes through
the existing GET/POST /my-applications/app/<id>/edit flow, which the owner
must review and submit themselves.
"""

from __future__ import annotations

import logging

from flask import abort, jsonify
from flask_login import current_user, login_required

from app.decorators import requires_application_owner_or_org_admin
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent
from app.modules.my_applications.health_ai_service import (
    ApplicationHealthAIError,
    generate_health_assessment,
)
from app.services.feature_flag_service import FeatureFlagService

from . import my_applications_bp

logger = logging.getLogger(__name__)


def _owned_application_or_404(app_id):
    """The application, only if the current user is a registered owner of it.

    Mirrors crud_routes.py's helper of the same name: a 404, not a 403,
    because confirming the application exists at all would tell a
    non-owner something they have no standing to learn.
    """
    # tenant-scoping-ok: self-lookup, filtered by the authenticated user's own id.
    ownership = ApplicationOwner.query.filter_by(
        user_id=current_user.id, application_id=app_id
    ).first()
    if not ownership:
        abort(404)
    return ApplicationComponent.query.get_or_404(app_id)


@my_applications_bp.route("/api/app/<int:app_id>/ai-health-assessment", methods=["POST"])
@login_required
@requires_application_owner_or_org_admin
def ai_health_assessment(app_id):
    """POST /my-applications/api/app/<id>/ai-health-assessment

    Generates an AI health assessment for an application the current user
    owns: a summary, suggested health/lifecycle status, the signals behind
    them, recommended actions, and a rationale. Nothing here is persisted -
    it is advisory input for the owner, who still edits the record via the
    existing edit form.
    """
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS,
        endpoint_name="my_applications.ai_health_assessment",
    )
    if feature_guard:
        return feature_guard

    app = _owned_application_or_404(app_id)

    try:
        assessment = generate_health_assessment(app)
    except ApplicationHealthAIError as e:
        logger.warning("AI health assessment unparseable for app %s: %s", app_id, e)
        return jsonify({"error": f"AI health assessment failed: {e}"}), 502
    except Exception as e:
        logger.exception("AI health assessment generation failed for app %s", app_id)
        return jsonify({"error": f"AI health assessment failed: {e}"}), 502

    return jsonify({"assessment": assessment})

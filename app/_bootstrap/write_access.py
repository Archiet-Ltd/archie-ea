"""Refuse an API write from a person who holds no write permission.

A member whose organisation role is viewer used to create, rename and delete applications through
the API: the browser hid the buttons, and the route's only guard was a sign-in check. One hook
asks the one write answer (``may_write``) before any state-changing ``/api/`` route runs, so a new
route is covered the day it is registered.
"""

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# Endpoints a person without write permission may still call, each with the reason. None writes to
# the model: each changes only the signed-in person's own record, or computes an answer and stores nothing.
READ_ONLY_ALLOWED = {
    "api_logout": "Ends the person's own session.",
    "api_notification_mark_read": "Marks one of the person's own notifications as read.",
    "api_notifications_mark_all_read": "Marks the person's own notifications as read.",
    "main.api_mark_notification_read": "Marks one of the person's own notifications as read.",
    "api_v1.llm.set_user_preference": "Saves the person's own choice of assistant model.",
    "dashboard.api_welcome_dismiss": "Records that the person closed their own welcome panel.",
    "dashboard.api_onboarding_complete": "Records the person's own onboarding state and view preference.",
    "intelligence_api.ask_nl_question": "Answers a plain-language question from the read-only query catalogue; stores nothing.",
    "api_v1.impact_v1.analyze_impact": "Computes an impact answer from the model and stores nothing.",
}


def init_write_access(app):
    from flask import jsonify, request
    from flask_login import current_user

    @app.before_request
    def _refuse_api_write_without_permission():
        if request.method not in WRITE_METHODS:
            return None
        if not (request.path.startswith("/api/") or "/api/" in request.path):
            return None
        if request.endpoint in READ_ONLY_ALLOWED:
            return None
        if not current_user.is_authenticated:
            return None
        from app.core.auth.permissions import READ_ONLY_CODE, may_write

        if may_write(current_user):
            return None
        return jsonify({
            "success": False,
            "error": "Your role is read-only, so it cannot make this change.",
            "code": READ_ONLY_CODE,
        }), 403

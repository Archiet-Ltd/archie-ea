"""Send an administrator whose first week has ended to set up two-factor
sign-in, on every signed-in request.

The sign-in form only decides at sign-in. A session that began without a
second factor (the sign-up session, a remember-me cookie) is checked here, so
the first-week window is the same condition on every request and not a
permanent exemption. The condition itself lives in ``mfa_service``.
"""

from __future__ import annotations

_EXEMPT_ENDPOINTS = {
    "static",
    "account.mfa_enrol",
    "account.mfa_challenge",
    "account.logout",
}


def init_mfa_enrolment(app):
    @app.before_request
    def _require_enrolment():
        from flask import g, jsonify, request
        from flask_login import current_user

        endpoint = request.endpoint or ""
        if endpoint in _EXEMPT_ENDPOINTS or endpoint.endswith(".static") or request.path.startswith("/static/"):
            return None
        if getattr(g, "auth_mode", None) == "bearer":
            return None
        try:
            if not (current_user and current_user.is_authenticated):
                return None
        except Exception:
            return None

        from app.services import mfa_service

        user = current_user._get_current_object()
        if not mfa_service.must_enrol_now(user):
            return None

        reason = mfa_service.window_closed_reason(user)
        if request.is_json or request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
            return jsonify({"error": "mfa_enrolment_required", "message": reason}), 403

        from flask import redirect, session, url_for

        from app.modules.account import mfa_flow

        session.pop(mfa_flow.TRIGGER_KEY, None)
        if request.method == "GET":
            session[mfa_flow.RETURN_KEY] = request.full_path.rstrip("?")
        return redirect(url_for("account.mfa_enrol"))

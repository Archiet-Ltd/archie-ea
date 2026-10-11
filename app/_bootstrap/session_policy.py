"""Server-authoritative session idle timeout (finding F-07).

The platform had exactly one session control: ``PERMANENT_SESSION_LIFETIME``
of 8 hours (config.py), an *absolute* cap. There was no idle timeout, and the
only thing resembling one — ``static/js/core/06-session-timeout.js`` — is a
client-side timer that starts at page load, never resets on activity and polls
``/health`` (a liveness endpoint that says nothing about the session). Anything
that does not execute page JavaScript, which is every direct API call, was
unaffected by it entirely.

This module adds the missing half: a ``before_request`` check that compares a
last-activity timestamp *stored in the signed session cookie* against now, and
tears the session down when the gap exceeds the configured idle window. It is
authoritative because the client cannot move the timestamp forward without
making a request, and cannot forge it without the secret key.

It also enforces server-side session revocation (the logout-doesn't-revoke
finding): before the idle check runs, it looks up the session's ``_sid``
against ``app.services.session_registry``. A missing/unknown/revoked ``_sid``
is rejected -- fail closed -- which is what makes logout (and password
change, see task 02) actually invalidate a captured cookie instead of just
asking the browser to forget it. Per ADR 0008 this lives here, reusing the
existing exemption list and response-shaping branches, rather than as a
second authenticating ``before_request`` hook.

The absolute cap is deliberately left alone and remains separate:
``PERMANENT_SESSION_LIFETIME`` bounds how long a session may live at all, this
bounds how long it may sit unused. Both apply.

Configuration
-------------
``SESSION_IDLE_TIMEOUT`` — ``timedelta`` or seconds. Defaults to 30 minutes,
the enterprise-typical value the finding asks for. Set to 0 to disable.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: Session key holding the epoch seconds of the last request this session made.
LAST_ACTIVITY_KEY = "_last_activity_at"

_DEFAULT_IDLE = timedelta(minutes=30)

# R1-B12 PR 2 (TB-0144/PB-0100): administrators get tighter limits than the
# platform default -- a shorter idle window, and an absolute cap on session
# age regardless of activity (the platform default only has PERMANENT_
# SESSION_LIFETIME, an 8-hour cookie-level cap that is the same for every
# role; this adds a second, stricter, role-aware cap read from the session
# registry's created_at rather than the cookie, so it cannot be extended by
# a refreshed cookie the way the cookie-level cap can).
_DEFAULT_IDLE_ADMIN = timedelta(minutes=15)
_DEFAULT_ABSOLUTE_ADMIN = timedelta(hours=4)

#: Requests that must NOT count as activity. A background /health poll from an
#: abandoned tab would otherwise refresh the stamp forever - which is exactly
#: the defect the old client-side "extend" button had, since it pinged /health.
_EXEMPT_ENDPOINTS = {
    "static",
    "global_health_check",   # /health
    "health.health",         # /health (blueprint duplicate)
    "healthz",
    "version_endpoint",      # /version
    "csp_report",            # /api/csp-report
}


def _idle_seconds(app):
    raw = app.config.get("SESSION_IDLE_TIMEOUT", _DEFAULT_IDLE)
    if isinstance(raw, timedelta):
        return int(raw.total_seconds())
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(_DEFAULT_IDLE.total_seconds())


def _timedelta_seconds(raw, default):
    if isinstance(raw, timedelta):
        return int(raw.total_seconds())
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default.total_seconds())


def _idle_seconds_admin(app):
    return _timedelta_seconds(
        app.config.get("SESSION_IDLE_TIMEOUT_ADMIN", _DEFAULT_IDLE_ADMIN), _DEFAULT_IDLE_ADMIN
    )


def _absolute_seconds_admin(app):
    return _timedelta_seconds(
        app.config.get("SESSION_ABSOLUTE_TIMEOUT_ADMIN", _DEFAULT_ABSOLUTE_ADMIN),
        _DEFAULT_ABSOLUTE_ADMIN,
    )


def _is_admin(user):
    return bool(getattr(user, "is_org_admin", False) or getattr(user, "is_platform_admin", False))


def _now():
    return int(datetime.now(timezone.utc).timestamp())


def _wants_json(request):
    return (
        "/api/" in request.path
        or "/ai-chat/" in request.path
        or request.content_type == "application/json"
        or request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def _force_clear_remember_cookie(response):
    """Belt-and-suspenders for D1 (round 2): explicitly delete the remember
    cookie on the rejection response itself, rather than relying solely on
    Flask-Login's session["_remember"] = "clear" marker surviving to
    response-build time. Cheap and idempotent if the marker also worked.
    """
    from flask import current_app

    try:
        cookie_name = current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
        domain = current_app.config.get("REMEMBER_COOKIE_DOMAIN")
        path = current_app.config.get("REMEMBER_COOKIE_PATH", "/")
        response.delete_cookie(cookie_name, domain=domain, path=path)
    except Exception as exc:
        logger.warning("Could not force-clear remember cookie: %s", exc)
    return response


def _reject_response(request, code, message):
    """Build the shared 401-JSON / redirect-to-login response for a rejected
    session, used by both the revocation check and the idle timeout."""
    from flask import jsonify, redirect, url_for

    if _wants_json(request):
        resp = jsonify({"success": False, "error": message, "code": code})
        resp.status_code = 401
        return resp
    try:
        login_url = url_for("account.login", timeout=code)
    except Exception:
        login_url = "/account/login?timeout={}".format(code)
    return redirect(login_url)


def init_session_policy(app):
    """Install the session-enforcement ``before_request`` hook (revocation +
    idle timeout)."""

    idle_seconds = _idle_seconds(app)
    app.config["SESSION_IDLE_TIMEOUT_SECONDS"] = idle_seconds

    # Exposed to templates so the courtesy client-side warning uses the same
    # number as the server rather than its own hard-coded 8 hours.
    @app.context_processor
    def _inject_idle_timeout():
        return {"session_idle_timeout_seconds": idle_seconds}

    @app.before_request
    def _enforce_session_policy():
        from flask import g, request, session
        from flask_login import current_user, logout_user

        # Static assets and the liveness probe must not keep a session alive:
        # a background /health poll from an abandoned tab would otherwise
        # refresh the timestamp forever, which is exactly the defect the
        # existing client-side "extend" button has.
        # Endpoint names verified against app.url_map, not guessed: /health is
        # registered twice (an inline route and a blueprint), and /version is a
        # build probe. Guessing "health" would have silently exempted nothing.
        endpoint = request.endpoint or ""
        if endpoint in _EXEMPT_ENDPOINTS or request.path.startswith("/static/"):
            return None

        # SCIM (R1-B26) is bearer-token only and never reads the session
        # cookie, so a stale cookie sent alongside must not turn a
        # provisioning call into a sign-in redirect.
        if request.blueprint == "scim":
            return None

        try:
            authenticated = bool(current_user and current_user.is_authenticated)
            # R1-B26: Flask-Login's ``is_authenticated`` is ``is_active`` on a
            # User, so a deactivated user reads as anonymous here. Look at the
            # loaded user itself so the session, the remember-me cookie and the
            # audit trail are all dealt with, not just left to ``login_required``.
            _deactivated = (
                current_user._get_current_object()
                if getattr(current_user, "deactivated_at", None) is not None
                else None
            )
        except Exception as exc:
            logger.debug("session policy: current_user unavailable: %s", exc)
            return None

        if _deactivated is not None:
            session.clear()
            logout_user()
            logger.info("Request refused for deactivated user %s", _deactivated.get_id())
            from app.services import auth_audit

            auth_audit.record_session_rejected(_deactivated, "user_deactivated")
            resp = _reject_response(
                request, "deactivated", "This account is not active. Contact your administrator."
            )
            _force_clear_remember_cookie(resp)
            return resp

        if not authenticated:
            return None

        # A genuine MCP client authenticates with a bearer token ONLY -- no
        # session cookie, no "_sid" at all, ever (see
        # app/modules/oauth_provider/identity.py's flask-login
        # request_loader, which never calls login_user() and never touches
        # the session). This whole hook is a *session*-idle-timeout and
        # *session*-revocation policy: it has nothing to check for a request
        # that was never authenticated via a session in the first place, and
        # the fail-closed revocation branch below would otherwise reject
        # every bearer-only call outright (no "_sid" in the session ->
        # session_registry.is_active(None) -> False -> 401 "revoked"), which
        # is exactly the production bug this fixes. The bearer token's own
        # lifecycle (OAuthToken.is_active -- revocation/expiry -- plus the
        # resource/organization checks in identity.py) is the authorization
        # control for this path instead; this is a narrow, request-scoped
        # exemption that only ever applies when the identity loader itself
        # already set the marker below, and never changes behaviour for any
        # session-cookie-authenticated request.
        if getattr(g, "auth_mode", None) == "bearer":
            return None

        # --- Server-side revocation check (logout / password-change) -----
        # Fail CLOSED: a session with no registry record (never minted, or
        # revoked by logout/password-change/idle-timeout/admin) is rejected.
        # This is deliberate -- deploying this check logs out every
        # currently-live session once, including any cookie captured before
        # this fix shipped, which is the whole point of the fix.
        from app.services import session_registry

        sid = session.get("_sid")
        if not session_registry.is_active(sid):
            user_label = getattr(current_user, "email", None) or current_user.get_id()
            # Captured before logout_user()/session.clear() make current_user
            # anonymous, so the audit row below still records who was rejected.
            _rejected_user = current_user._get_current_object() if current_user.is_authenticated else None
            # Order matters (D1, round 2): clear the session FIRST, then call
            # logout_user(). Flask-Login's logout_user() signals remember-cookie
            # deletion by writing session["_remember"] = "clear", which
            # _update_remember_cookie reads at response-build time. Calling
            # session.clear() *after* logout_user() (the round-1 order) wipes
            # that marker before the response is built, so the remember_token
            # cookie was never actually deleted -- with
            # REMEMBER_COOKIE_REFRESH_EACH_REQUEST=True and SSO logins passing
            # remember=True unconditionally, that produced an unbreakable
            # redirect loop: flask-login silently re-authenticates the user
            # from the still-live remember cookie on every subsequent request.
            session.clear()
            logout_user()
            logger.info("Session rejected (revoked/unknown sid) for %s", user_label)
            if _rejected_user is not None:
                from app.services import auth_audit

                auth_audit.record_session_rejected(_rejected_user, "revoked_or_unknown_sid")
            resp = _reject_response(request, "revoked", "Your session is no longer valid. Please log in again.")
            _force_clear_remember_cookie(resp)
            return resp
        session_registry.touch(sid)

        # R1-B12 PR 2: an administrator's session is bound by two extra,
        # stricter checks before the platform-default idle check below even
        # runs -- an absolute cap from the registry's created_at (which a
        # refreshed cookie cannot push out, unlike PERMANENT_SESSION_LIFETIME),
        # and its own shorter idle window.
        is_admin = _is_admin(current_user)
        if is_admin:
            age = session_registry.age_seconds(sid)
            if age is not None:
                absolute_seconds = _absolute_seconds_admin(app)
                if absolute_seconds > 0 and age > absolute_seconds:
                    user_label = getattr(current_user, "email", None) or current_user.get_id()
                    _rejected_user = current_user._get_current_object()
                    session_registry.revoke(sid, "admin_absolute_timeout")
                    session.clear()
                    logout_user()
                    logger.info(
                        "Admin session absolute timeout: signed out %s after %ss (limit %ss)",
                        user_label, int(age), absolute_seconds,
                    )
                    from app.services import auth_audit

                    auth_audit.record_session_rejected(_rejected_user, "admin_absolute_timeout")
                    resp = _reject_response(
                        request, "admin_absolute",
                        "Administrator sessions expire after a fixed time. Please log in again.",
                    )
                    _force_clear_remember_cookie(resp)
                    return resp

        effective_idle_seconds = _idle_seconds_admin(app) if is_admin else idle_seconds

        if effective_idle_seconds <= 0:
            return None

        now = _now()
        last = session.get(LAST_ACTIVITY_KEY)

        if isinstance(last, int) and now - last > effective_idle_seconds:
            user_label = getattr(current_user, "email", None) or current_user.get_id()
            _rejected_user = current_user._get_current_object() if current_user.is_authenticated else None
            session_registry.revoke(sid, "idle_timeout")
            # Same ordering fix as the revocation branch above (D1, round 2).
            session.clear()
            logout_user()
            logger.info(
                "Session idle timeout: signed out %s after %ss idle (limit %ss)",
                user_label,
                now - last,
                effective_idle_seconds,
            )
            if _rejected_user is not None:
                from app.services import auth_audit

                auth_audit.record_session_rejected(_rejected_user, "idle_timeout")
            resp = _reject_response(
                request, "idle", "Session expired due to inactivity. Please log in again."
            )
            _force_clear_remember_cookie(resp)
            return resp

        # Genuine activity — advance the stamp. ``session.permanent`` is set at
        # login; writing here also refreshes the absolute-lifetime cookie,
        # which is the pre-existing SESSION_REFRESH_EACH_REQUEST behaviour.
        session[LAST_ACTIVITY_KEY] = now
        return None

    if idle_seconds <= 0:
        app.logger.info("Session idle timeout disabled (SESSION_IDLE_TIMEOUT=0); revocation check still active")
    else:
        app.logger.info("Session idle timeout enabled: %ss", idle_seconds)

"""Application-wide request throttling (finding F-06).

Before this module the only throttle in the codebase was
``app.services.rate_limiter.rate_limit``, applied by hand to a handful of
authentication routes. Everything else — every read API, every create/update
endpoint — was unbounded, which the QA sweep demonstrated with 40 unthrottled
GETs and 25 unthrottled POSTs. That matters here beyond the usual abuse case:
ARCH-001 records four complete outages under *single-user* load, so an
unthrottled client is also an availability control that does not exist.

Design notes
------------
* Limits are declared **globally** here rather than per route, so a new
  blueprint is covered the moment it is registered. Per-route overrides remain
  available via ``limiter.limit`` / ``limiter.exempt``.
* Counters live in the database (``app.services.rate_limiter.DatabaseStorage``)
  so a counter survives a gunicorn worker recycle and is shared across workers —
  an in-memory counter divided by N workers is N times the intended limit.
  ``RATELIMIT_STORAGE_URI`` still overrides it (for instance a Redis address).
* An administrator's allow and deny lists are applied before any counting: a
  denied address or user is refused outright, an allowed one is not counted, and
  deny wins over allow. See ``app.services.rate_limiter`` and /admin/rate-limits.
* Keys are per authenticated user where possible, per IP otherwise. Keying an
  authenticated app purely on IP would make a single office NAT one bucket.
"""

import logging
import os

logger = logging.getLogger(__name__)

limiter = None

# Endpoints that must never be throttled, with the exact endpoint names this
# app registers (verified against app.url_map, not guessed):
#  - liveness/version probes. A throttled /health reads as an outage to the
#    orchestrator and would make the healthcheck flap — which is the restart
#    loop ARCH-001 is actually about, so throttling it would make the finding
#    worse rather than better.
#  - the CSP violation report sink. Rate-limiting it would silently discard
#    exactly the reports that matter (a real attack generates many).
#  - static assets: one page load pulls dozens, exhausting any sane limit.
_EXEMPT_ENDPOINTS = {
    "static",
    "global_health_check",   # /health
    "health.health",         # /health (blueprint duplicate)
    "healthz",
    "version_endpoint",      # /version
    "csp_report",            # /api/csp-report
}

_DEFAULT_LIMITS = ["1000 per hour", "120 per minute"]
# Writes are the expensive, state-changing half; hold them well below reads.
_WRITE_LIMITS = ["300 per hour", "30 per minute"]

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _write_limits():
    """Per-request extra limits, applied only to state-changing verbs.

    Expressed as a callable in ``default_limits`` rather than by decorating the
    app: Flask-Limiter's ``limit()`` decorator expects a view function, and an
    empty string means "no additional limit for this request".
    """
    from flask import request

    if request.method.upper() not in _WRITE_METHODS:
        return ""
    return "; ".join(_WRITE_LIMITS)


def _storage_uri():
    """The counter store: an explicit ``RATELIMIT_STORAGE_URI``, else the database.

    The database is what makes the limit one limit across workers with nothing extra to run. Only
    when its storage class is unavailable do we fall back to in-memory, per-worker counting.
    """
    url = (os.environ.get("RATELIMIT_STORAGE_URI") or "").strip()
    if url:
        return url
    from app.services.rate_limiter import STORAGE_URI, DatabaseStorage

    if DatabaseStorage is None:  # pragma: no cover - dependency is pinned
        logger.warning("Rate limiting: shared storage unavailable — using in-memory storage")
        return "memory://"
    return STORAGE_URI


def _identity():
    """Per-user key when authenticated, per-IP otherwise."""
    try:
        from flask_login import current_user

        if current_user and current_user.is_authenticated:
            return f"user:{current_user.get_id()}"
    except Exception as exc:
        logger.debug("rate-limit identity: current_user unavailable: %s", exc)
    from flask_limiter.util import get_remote_address

    return f"ip:{get_remote_address()}"


def _wants_json() -> bool:
    from flask import request

    return bool(
        "/api/" in request.path
        or request.content_type == "application/json"
        or request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.path.startswith("/mcp")
    )


def _json_refusal(sentence, error_type):
    from flask import jsonify

    return jsonify({"success": False, "error": sentence, "error_type": error_type})


def _session_user_id():
    """The signed-in user's id from the session alone: no user is loaded this early in the request."""
    from flask import session

    raw = session.get("_user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _log_refusal(rule):
    from flask import request

    from app.services.rate_limiter import record_violation

    record_violation(
        address=request.remote_addr,
        user_id=_session_user_id(),
        route=f"{request.method} {request.path}",
        rule=str(rule),
    )


def _install_access_lists(app):
    """Refuse a denied address or user, and mark an allowed one so it is not counted.

    Registered before Flask-Limiter's own hook so a denied caller is refused before anything is
    counted. The address is the connection's (``request.remote_addr``, which the application's proxy
    configuration already corrects); a forwarded-for header supplied by the caller is never read here.
    """

    @app.before_request
    def _access_lists():
        from flask import current_app, g, request

        if not current_app.config.get("RATE_LIMITING_ENABLED", True):
            return None
        if (request.endpoint or "") in _EXEMPT_ENDPOINTS or (request.path or "").startswith("/static/"):
            return None
        from app.services.rate_limiter import DENIED_MESSAGE, access_verdict

        try:
            verdict, label = access_verdict(request.remote_addr, _session_user_id())
        except Exception as exc:  # a list that cannot be read must not take the site down
            current_app.logger.warning("allow/deny lists not applied: %s", exc)
            return None
        if verdict == "allow":
            g._rate_limit_allowed = True
            return None
        if verdict != "deny":
            return None
        _log_refusal("denied: " + str(label))
        if _wants_json():
            resp = _json_refusal(DENIED_MESSAGE, "address_blocked")
        else:
            from flask import render_template

            try:
                resp = current_app.make_response(
                    render_template("errors/403.html", error_title="This address is blocked",
                                    error_message=DENIED_MESSAGE, blocked=True)
                )
            except Exception:
                resp = current_app.make_response(DENIED_MESSAGE)
        resp.status_code = 403
        return resp


def refuse(retry_after, rule, verdict=None):
    """The one refusal for a limit that has been reached: JSON for an API caller, else the 429 page.

    Says how long to wait, logs the refusal, and carries the RateLimit and Retry-After headers.
    """
    from flask import current_app, render_template

    from app.services.rate_limiter import apply_headers, wait_phrase

    _log_refusal(rule)
    sentence = "Rate limit exceeded. Slow down and retry shortly."
    if retry_after:
        sentence = "Rate limit exceeded. Try again in %s." % wait_phrase(retry_after)
    if _wants_json():
        resp = _json_refusal(sentence, "rate_limited")
    else:
        try:
            resp = current_app.make_response(
                render_template(
                    "errors/429.html", retry_after=retry_after,
                    wait_text=wait_phrase(retry_after) if retry_after else None,
                )
            )
        except Exception:
            resp = current_app.make_response(sentence)
    resp.status_code = 429
    if verdict is not None:
        apply_headers(resp, verdict)
    if retry_after:
        resp.headers["Retry-After"] = str(retry_after)
    return resp


def init_rate_limiting(app):
    """Install Flask-Limiter with global default limits.

    Never fatal: a limiter that fails to install must degrade to "no throttle",
    not to "no application".
    """
    global limiter
    try:
        from flask_limiter import Limiter
    except ImportError:  # pragma: no cover - dependency is pinned
        app.logger.warning(
            "Rate limiting disabled: flask_limiter is not installed"
        )
        return None

    # Flask-Limiter's own RATELIMIT_ENABLED is a *wiring* switch, not a runtime
    # one: init_app returns early when it is false, so the before_request hook
    # is never registered and the limiter can never be turned back on. Keep it
    # true and gate at request time on the app's own RATE_LIMITING_ENABLED flag
    # (already honoured by app/services/rate_limiter.py) so the switch is real.
    app.config["RATELIMIT_ENABLED"] = True
    app.config.setdefault("RATELIMIT_HEADERS_ENABLED", True)  # emits Retry-After
    # The remaining allowance, reported on every limited response under the names the IETF
    # draft uses (RateLimit-Limit / -Remaining / -Reset), with Retry-After on a refusal.
    app.config["RATELIMIT_HEADER_LIMIT"] = "RateLimit-Limit"
    app.config["RATELIMIT_HEADER_REMAINING"] = "RateLimit-Remaining"
    app.config["RATELIMIT_HEADER_RESET"] = "RateLimit-Reset"
    # Off by default under TESTING: the whole suite shares one session-scoped
    # app and one loopback address, so a live limiter would 429 unrelated tests.
    enabled = app.config.setdefault(
        "RATE_LIMITING_ENABLED", not app.config.get("TESTING", False)
    )

    _install_access_lists(app)

    # Registered before Flask-Limiter's own hook, so it runs after it (response hooks run last-in
    # first-out) and can see what the request limiter has already reported.
    @app.after_request
    def _report_allowance(response):
        """State the tightest allowance counted for this request, once, in the RateLimit headers."""
        from flask import g

        from app.services.rate_limiter import apply_headers

        verdict = getattr(g, "_counted_verdict", None)
        if verdict is None:
            return response
        headers = response.headers
        reported = []
        for value in headers.getlist("RateLimit-Remaining"):
            try:
                reported.append(int(value))
            except ValueError:
                reported.append(None)
        known = [n for n in reported if n is not None]
        if not known or verdict.remaining <= min(known):
            apply_headers(response, verdict)
        elif len(reported) > 1:  # the request limiter's own is tighter: keep that one, once
            tightest = reported.index(min(known))
            for name in ("RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"):
                values = headers.getlist(name)
                if len(values) > tightest:
                    headers.setlist(name, [values[tightest]])
        return response

    try:
        limiter = Limiter(
            key_func=_identity,
            app=app,
            # Writes carry their own, tighter bucket on top of the default.
            default_limits=[*_DEFAULT_LIMITS, _write_limits],
            storage_uri=_storage_uri(),
            strategy="fixed-window",
            enabled=True,
            headers_enabled=True,
            swallow_errors=True,
        )
    except Exception as exc:
        app.logger.warning("Rate limiting install failed (non-fatal): %s", exc)
        return None

    @limiter.request_filter
    def _exempt():
        from flask import current_app, g, request

        if not current_app.config.get("RATE_LIMITING_ENABLED", True):
            return True
        if getattr(g, "_rate_limit_allowed", False):
            return True  # on the administrator's allow list: not counted
        endpoint = request.endpoint or ""
        return endpoint in _EXEMPT_ENDPOINTS or (request.path or "").startswith(
            "/static/"
        )

    @app.errorhandler(429)
    def _too_many_requests(err):
        retry_after = getattr(err, "retry_after", None) or getattr(
            getattr(err, "response", None), "headers", {}
        ).get("Retry-After")
        try:
            retry_after = int(retry_after) if retry_after else None
        except (TypeError, ValueError):
            retry_after = None
        return refuse(retry_after, getattr(err, "description", None) or "rate limit")

    app.logger.info(
        "Rate limiting enabled=%s defaults=%s writes=%s",
        enabled,
        _DEFAULT_LIMITS,
        _WRITE_LIMITS,
    )
    return limiter

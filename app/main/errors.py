import traceback

from flask import current_app, jsonify, render_template, request

from app.main.views import main
from app.services.rate_limiter import RateLimitExceeded
from app.utils.plain_errors import PLAIN, plain_json, render_plain_error


def _log_exception(e):
    try:
        tb = traceback.format_exc()
        with open("error_debug.log", "a", encoding="utf-8") as fh:
            fh.write("\n-----\n")
            fh.write(tb)
            fh.write("\n-----\n")
    except Exception:
        # Best effort only; avoid raising further exceptions
        current_app.logger.exception("Failed to write to error_debug.log")


@main.app_errorhandler(RateLimitExceeded)
def handle_rate_limit_exceeded(e):
    """Global handler for rate limit exceeded errors (import-fix-rate-limiting)."""
    current_app.logger.warning(
        f"Rate limit exceeded: {e.limit}/{e.window} - "
        f"IP: {request.remote_addr}, Path: {request.path}"
    )
    # Return JSON for API/AJAX requests, otherwise a simple text response
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        response = jsonify(
            {
                "error": "Rate limit exceeded",
                "message": f"Too many requests. Limit: {e.limit} per {e.window}.",
                "limit": e.limit,
                "window": e.window,
                "retry_after": e.retry_after,
            }
        )
        response.status_code = 429
        if e.retry_after:
            response.headers["Retry-After"] = str(e.retry_after)
        return response
    else:
        # For non-API requests (form submissions, page loads), return a 429 page
        response = jsonify(
            {
                "error": "Rate limit exceeded",
                "message": f"Too many requests. Please wait and try again. Limit: {e.limit} per {e.window}.",
                "retry_after": e.retry_after,
            }
        )
        response.status_code = 429
        if e.retry_after:
            response.headers["Retry-After"] = str(e.retry_after)
        return response


def _api_error(status, message):
    """JSON for an /api/ caller, or None when an HTML page is the right answer.

    These three handlers are registered per status code, and Flask prefers the
    most specific handler it can find - so they beat the generic HTTPException
    handler in _bootstrap/extensions.py and an /api/ path was still being served
    an HTML error page. A front end that asks for JSON and gets HTML fails at
    JSON.parse, so the user sees a script error instead of "not found" or "you
    do not have access": the refusal is right and the explanation is lost.

    Keyed on the path rather than the Accept header because fetch() sends
    Accept: */* by default, so header sniffing misses most real callers.
    """
    if "/api/" not in request.path:
        return None
    if status in PLAIN:
        return plain_json(status, message.lower().replace(" ", "_"))
    return jsonify({
        "success": False,
        "error": message,
        "error_type": message.lower().replace(" ", "_"),
    }), status


@main.app_errorhandler(403)
def forbidden(e):
    api = _api_error(403, "Forbidden")
    if api is not None:
        return api
    try:
        return render_plain_error(403)
    except Exception:
        _log_exception(e)
        return ("Forbidden", 403)


@main.app_errorhandler(404)
def page_not_found(e):
    api = _api_error(404, "Not Found")
    if api is not None:
        return api
    try:
        return render_plain_error(404)
    except Exception:
        _log_exception(e)
        return ("Page Not Found", 404)


@main.app_errorhandler(500)
def internal_server_error(e):
    api = _api_error(500, "Internal Server Error")
    if api is not None:
        return api
    try:
        return render_plain_error(500)
    except Exception:
        _log_exception(e)
        return ("Internal Server Error", 500)


@main.app_errorhandler(503)
def service_unavailable(e):
    """ARCH-002: preserve the requested path through a 503 so a deep link or
    bookmark survives an outage instead of silently dropping the user at '/'.

    This only covers the case where the Flask process itself is up and chooses
    to answer 503 (e.g. a readiness gate). When the whole worker/container is
    dead, Caddy answers instead — see deploy/Caddyfile.proxy's handle_errors
    block and app/static/errors/503.html, which carry the same request path
    forward using a server-rendered placeholder rather than client JS.
    """
    api = _api_error(503, "Service Unavailable")
    if api is not None:
        return api
    requested_path = request.full_path if request.query_string else request.path
    try:
        return render_template("errors/503.html", requested_path=requested_path), 503
    except Exception:
        _log_exception(e)
        return ("Service Unavailable", 503)

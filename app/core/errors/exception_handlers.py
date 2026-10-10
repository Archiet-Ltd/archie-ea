"""
Unified exception-to-response mappers for new modular architecture.

Registers Flask error handlers that convert ``FlaskShadcnException`` (and its
sub-hierarchy) into standardised JSON responses using ``app.core.api.response``.

This module is opt-in for new modules only.  Legacy modules retain their own
error handling via ``app/utils/error_handlers.py``.

Usage::

    from app.core.errors.exception_handlers import register_guardrail_error_handlers

    def create_app():
        app = Flask(__name__)
        register_guardrail_error_handlers(app)
"""

import logging
import time
from typing import Tuple

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)

_GUARDRAIL_HANDLERS_REGISTERED = False


def _request_id() -> str:
    """The request's trace id (``app/utils/tracing.py``), the one the logs carry."""
    from app.utils.tracing import current_trace_id

    return current_trace_id() or getattr(request, "request_id", None) or request.headers.get(
        "X-Request-ID", f"req-{int(time.time() * 1000)}"
    )


def _is_new_module_request() -> bool:
    """Return True if the current request targets a new-architecture module.

    New modules register blueprints under ``/m/`` or carry the
    ``X-Module-Version: 2`` header.  Legacy routes are unaffected.
    """
    if request.headers.get("X-Module-Version") == "2":
        return True
    if request.path.startswith("/m/"):
        return True
    bp = request.blueprints[0] if request.blueprints else None
    if bp and getattr(request, "_guardrail_enabled", False):
        return True
    return False


def _build_error_body(
    message: str,
    error_code: str,
    status_code: int,
    details: dict | None = None,
    recovery_action: str | None = None,
) -> dict:
    body = {
        "success": False,
        "error": message,
        "error_code": error_code,
        "request_id": _request_id(),
    }
    if recovery_action:
        body["recovery_action"] = recovery_action
    if details:
        body["details"] = details
    return body


def _handle_app_exception(exc) -> Tuple:
    """Handle FlaskShadcnException hierarchy."""
    from app.exceptions import FlaskShadcnException

    if not isinstance(exc, FlaskShadcnException):
        raise exc

    logger.warning(
        "AppException %s: %s",
        exc.error_code,
        exc.message,
        # request_id and organization_id are on every record already.
        extra={"error_code": exc.error_code, **exc.log_context},
    )

    body = _build_error_body(
        message=exc.user_message,
        error_code=exc.error_code,
        status_code=exc.status_code,
        details=exc.details if exc.details else None,
        recovery_action=exc.recovery_action,
    )
    return jsonify(body), exc.status_code


def _handle_http_exception(exc: HTTPException) -> Tuple:
    """Handle Werkzeug HTTP exceptions with consistent format."""
    if not _is_new_module_request():
        raise exc

    body = _build_error_body(
        message=exc.description or "An error occurred",
        error_code=f"HTTP_{exc.code}",
        status_code=exc.code,
    )
    return jsonify(body), exc.code


def _handle_unhandled_exception(exc: Exception) -> Tuple:
    """Catch-all for unexpected errors in new modules."""
    if not _is_new_module_request():
        raise exc

    logger.exception(
        "Unhandled exception in new module: %s",
        str(exc),
    )

    body = _build_error_body(
        message="An unexpected error occurred. Please try again.",
        error_code="INTERNAL_ERROR",
        status_code=500,
        recovery_action="If this persists, contact support.",
    )
    return jsonify(body), 500


def register_guardrail_error_handlers(app: Flask) -> None:
    """Register error handlers for the new guardrail framework.

    Safe to call multiple times — only registers once.
    Handlers check ``_is_new_module_request()`` and re-raise for legacy routes,
    so legacy error handling is never affected.
    """
    global _GUARDRAIL_HANDLERS_REGISTERED
    if _GUARDRAIL_HANDLERS_REGISTERED:
        return

    from app.exceptions import FlaskShadcnException

    app.register_error_handler(FlaskShadcnException, _handle_app_exception)
    app.register_error_handler(HTTPException, _handle_http_exception)
    app.register_error_handler(Exception, _handle_unhandled_exception)

    _GUARDRAIL_HANDLERS_REGISTERED = True
    logger.info("Guardrail error handlers registered")

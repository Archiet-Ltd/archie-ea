"""
Decorator for routes that require LLM service availability.

Provides graceful degradation when LLM providers are not configured.
Returns 503 Service Unavailable with user-friendly message instead of 500 errors.

Usage:
    @app.route('/api/generate')
    @login_required
    @require_llm
    def generate():
        # LLM service is guaranteed to be available here
        result = llm_service.generate_text(...)
        return jsonify(result)
"""

import logging
from functools import wraps

from flask import current_app, flash, jsonify, redirect, request, url_for

logger = logging.getLogger(__name__)


def require_llm(f):
    """
    Decorator to check if LLM service is available before executing route.

    For API routes (JSON requests): Returns 503 with error details
    For HTML routes: Flashes message and redirects to referring page or dashboard

    This prevents 500 errors and provides graceful degradation when LLM providers
    are not configured.

    Returns:
        503 Service Unavailable with helpful error message if LLM not available
        Original route response if LLM is available
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        try:
            if not LLMService.is_available():
                error_message = (
                    "AI features are currently unavailable. "
                    "Please configure an LLM provider in Admin > AI settings "
                    "or contact your system administrator."
                )

                # Check if this is an API request (expects JSON)
                if (
                    request.path.startswith("/api/")
                    or request.is_json
                    or "application/json" in request.accept_mimetypes
                ):
                    return jsonify(
                        {
                            "success": False,
                            "error": error_message,
                            "error_code": "LLM_UNAVAILABLE",
                            "status_code": 503,
                        }
                    ), 503

                # HTML request - flash message and redirect
                flash(error_message, "warning")

                # Try to redirect to referring page
                referrer = request.referrer
                if referrer and referrer.startswith(request.host_url):
                    return redirect(referrer)

                # Fallback to dashboard
                return redirect(url_for("main.dashboard"))

        except Exception as e:
            logger.error(f"Error checking LLM availability in decorator: {e}")
            # If check fails, let the route execute (fail open for backward compatibility)
            current_app.logger.warning(
                f"LLM availability check failed for {request.path}: {e}. "
                "Allowing request to proceed."
            )

        return f(*args, **kwargs)

    return decorated_function

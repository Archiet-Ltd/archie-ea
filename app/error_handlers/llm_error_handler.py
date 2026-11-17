"""
Global error handler for LLM unavailability.

Catches ValueError from LLM Service and converts to 503 Service Unavailable
with user-friendly messages instead of 500 Internal Server Error.

This provides graceful degradation across all AI features without modifying
individual routes.

Registered in app/__init__.py via app.register_error_handler()
"""

import logging

from flask import jsonify, request

logger = logging.getLogger(__name__)


def handle_llm_unavailable_error(error):
    """
    Handle ValueError exceptions from LLM service when provider not configured.

    Converts LLM configuration errors to 503 Service Unavailable responses.

    Args:
        error: The ValueError exception raised by LLMService

    Returns:
        JSON response with 503 status code and helpful message
    """
    error_message = str(error)

    # Check if this is an LLM configuration error
    llm_error_indicators = [
        "No enabled LLM provider",
        "No LLM provider",
        "LLM provider found",
        "API key",
        "api-settings",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ]

    is_llm_error = any(indicator in error_message for indicator in llm_error_indicators)

    if is_llm_error:
        logger.warning(f"LLM service unavailable for {request.path}: {error_message}")

        user_friendly_message = (
            "The assistant could not answer just now. "
            "Your question is saved; try again in a moment."
        )

        # Include technical details for debugging (visible to admins)
        response_data = {
            "success": False,
            "error": user_friendly_message,
            "error_code": "LLM_SERVICE_UNAVAILABLE",
            "status_code": 503,
            "technical_details": error_message if request.args.get("debug") else None,
        }

        return jsonify(response_data), 503

    # Not an LLM error - let Flask's default error handler deal with it
    # This will result in a 500 error as expected
    raise error


def register_llm_error_handler(app):
    """
    Register the LLM error handler with the Flask application.

    Should be called during application initialization in app/__init__.py

    Args:
        app: Flask application instance
    """

    # Register for ValueError exceptions
    # Only handles LLM-related ValueErrors, re-raises others
    app.register_error_handler(ValueError, handle_llm_unavailable_error)

    logger.info("Registered LLM error handler for graceful degradation")

"""Production-Ready Error Handling System"""

import logging
from functools import wraps
from typing import Any, Dict, Tuple

from flask import Flask, jsonify, request

from app.exceptions import FlaskShadcnException, MissingRequiredFieldError, ValidationError

logger = logging.getLogger(__name__)


def format_error_response(error: Exception, status_code: int = 500) -> Tuple[Dict[str, Any], int]:
    """Format error as JSON response."""
    if isinstance(error, FlaskShadcnException):
        response = error.to_dict()
        return response, error.status_code

    response = {
        "success": False,
        "error": "An unexpected error occurred.",
        "error_code": "INTERNAL_ERROR",
    }
    return response, status_code


def register_error_handlers(app: Flask):
    """Register error handlers."""

    @app.errorhandler(404)
    def handle_404(error):
        from app.utils.plain_errors import plain_json, render_plain_error
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return plain_json(404, "not_found", error_code="NOT_FOUND")
        try:
            return render_plain_error(404)
        except Exception:
            return "<h1>We could not find that page</h1><p><a href='/'>Go to the dashboard</a></p>", 404

    @app.errorhandler(500)
    def handle_500(error):
        logger.error("Internal server error: %s", error, exc_info=True)
        from app.utils.plain_errors import plain_json, render_plain_error
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return plain_json(500, "internal_error")
        try:
            return render_plain_error(500)
        except Exception:
            return "<h1>Something went wrong on our side</h1><p><a href='/'>Go to the dashboard</a></p>", 500

    @app.errorhandler(FlaskShadcnException)
    def handle_app_error(error):
        response, status_code = format_error_response(error)
        return jsonify(response), status_code


def safe_route(func):
    """Decorator for automatic error handling."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except FlaskShadcnException:
            raise
        except ValueError as e:
            raise ValidationError(message=str(e))
        except KeyError as e:
            raise MissingRequiredFieldError(field=str(e).strip("'\""))

    return wrapper


class safe_db_operation:
    """Context manager for database operations."""

    def __init__(self, operation_name: str):
        self.operation_name = operation_name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return exc_type is None


def handle_errors(app: Flask):
    """Register all error handlers."""
    register_error_handlers(app)
    logger.info("Error handlers registered")

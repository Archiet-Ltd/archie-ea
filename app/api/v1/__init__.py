"""
API v1 Blueprint - Versioned API Endpoints

Implements PRD - 003: API Response Standardization
Provides versioned namespace for all API endpoints with standardized responses.
"""

import logging

from flask import Blueprint, jsonify, redirect, request, url_for
from flask_login import login_required

from app import db
from app.utils.api_response import deprecated_response, error_response, success_response

# Create the v1 API blueprint
api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
logger = logging.getLogger(__name__)


@api_v1_bp.before_request
def add_api_version_header():
    """Add X-API-Version header to all responses"""
    # This will be applied to all routes in this blueprint
    pass


@api_v1_bp.after_request
def after_request(response):
    """Add X-API-Version header to all responses"""
    response.headers["X-API-Version"] = "1.0"
    return response


# Import and register all versioned endpoints
from .applications import applications_bp
from .capabilities import capabilities_bp
from .dashboard import dashboard_bp
from .elements import elements_v1_bp
from .enterprise import enterprise_bp
from .impact import impact_bp
from .llm import llm_bp
from .mappings import mappings_bp
from .vendors import vendors_bp

# Register sub-blueprints
api_v1_bp.register_blueprint(applications_bp, url_prefix="/applications")
api_v1_bp.register_blueprint(vendors_bp, url_prefix="/vendors")
api_v1_bp.register_blueprint(capabilities_bp, url_prefix="/capabilities")
api_v1_bp.register_blueprint(enterprise_bp, url_prefix="/enterprise")
api_v1_bp.register_blueprint(dashboard_bp, url_prefix="/dashboard")
api_v1_bp.register_blueprint(mappings_bp, url_prefix="/mappings")
api_v1_bp.register_blueprint(llm_bp, url_prefix="/llm")
api_v1_bp.register_blueprint(impact_bp, url_prefix="/impact")
api_v1_bp.register_blueprint(elements_v1_bp, url_prefix="/elements")


# Legacy endpoint redirects with deprecation warnings
@api_v1_bp.route("/legacy-redirect")
def legacy_redirect_example():
    """
    Example of how to handle legacy endpoint redirects
    """
    new_endpoint = request.args.get("new", "/api/v1/")
    return deprecated_response(new_endpoint)


@api_v1_bp.route("/")
def api_v1_info():
    """
    API v1 Information Endpoint
    ---
    tags:
      - API v1
    summary: Get API v1 information
    description: Returns information about the v1 API including available endpoints
    responses:
      200:
        description: API v1 information
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: true
            data:
              type: object
              properties:
                version:
                  type: string
                  example: "1.0"
                description:
                  type: string
                  example: "Flask Enterprise Architecture API v1"
                endpoints:
                  type: array
                  items:
                    type: string
                    example: "/applications"
    """
    endpoints = ["/applications", "/vendors", "/capabilities", "/enterprise", "/dashboard"]

    return success_response(
        {
            "version": "1.0",
            "description": "Flask Enterprise Architecture API v1",
            "endpoints": endpoints,
            "base_url": "/api/v1",
        }
    )


@api_v1_bp.errorhandler(404)
def api_v1_not_found(error):
    """Handle API v1 not found errors."""
    logger.warning("API v1 404 route=%s method=%s: %s", request.path, request.method, error)
    return error_response("Endpoint not found", status_code=404)


@api_v1_bp.errorhandler(500)
def api_v1_internal_error(error):
    """Handle API v1 internal server errors."""
    logger.error(
        "API v1 500 route=%s method=%s: %s",
        request.path,
        request.method,
        error,
        exc_info=True,
    )
    db.session.rollback()
    return error_response("An internal error occurred", status_code=500)

"""
API Module Entry Point

This module serves as the central entry point for all API versions and provides
common functionality including:
- API versioning support
- Version header handling (X-API-Version)
- Deprecation warnings for legacy endpoints
- Backward compatibility middleware

API Structure:
    /api/v1/  - Current stable API (v1.0)
        /applications - Application portfolio management
        /vendors - Vendor management
        /capabilities - Capability management
        /enterprise - Enterprise architecture
        /dashboard - Dashboard data

Usage:
    from app.api import register_api_blueprints
    register_api_blueprints(app)
"""

from functools import wraps

from flask import g, make_response, request

# API Version Configuration
API_VERSIONS = {
    "1.0": {"status": "current", "prefix": "/api/v1", "deprecated": False, "sunset_date": None}
}

DEFAULT_API_VERSION = "1.0"
SUPPORTED_VERSIONS = ["1.0"]
LATEST_VERSION = "1.0"


def get_requested_version():
    """
    Get the API version from the request.

    Checks in order:
    1. X-API-Version header
    2. Accept header (application/vnd.api+json;version=X)
    3. Query parameter (?api_version=X)
    4. Default to latest version

    Returns:
        str: The requested API version
    """
    # Check X-API-Version header
    version = request.headers.get("X-API-Version")
    if version and version in SUPPORTED_VERSIONS:
        return version

    # Check Accept header for version
    accept_header = request.headers.get("Accept", "")
    if "version=" in accept_header:
        try:
            version = accept_header.split("version=")[1].split(";")[0].split(",")[0].strip()
            if version in SUPPORTED_VERSIONS:
                return version
        except (IndexError, ValueError):
            pass

    # Check query parameter
    version = request.args.get("api_version")
    if version and version in SUPPORTED_VERSIONS:
        return version

    # Default to latest version
    return DEFAULT_API_VERSION


def api_version_required(min_version=None, max_version=None):
    """
    Decorator to enforce API version requirements on endpoints.

    Args:
        min_version: Minimum required API version
        max_version: Maximum allowed API version

    Returns:
        Decorated function
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            version = get_requested_version()
            g.api_version = version

            if min_version and version < min_version:
                from app.utils.api_response import error_response

                return error_response(
                    message=f"This endpoint requires API version {min_version} or higher",
                    code="VERSION_TOO_LOW",
                    status_code=400,
                )

            if max_version and version > max_version:
                from app.utils.api_response import error_response

                return error_response(
                    message=f"This endpoint is not available in API version {version}. Max version: {max_version}",
                    code="VERSION_TOO_HIGH",
                    status_code=400,
                )

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def add_version_headers(response, version=None):
    """
    Add API version headers to a response.

    Args:
        response: Flask response object
        version: API version string (defaults to requested version)

    Returns:
        Modified response with version headers
    """
    version = version or getattr(g, "api_version", DEFAULT_API_VERSION)

    response.headers["X-API-Version"] = version
    response.headers["X-API-Supported-Versions"] = ", ".join(SUPPORTED_VERSIONS)
    response.headers["X-API-Latest-Version"] = LATEST_VERSION

    return response


def add_deprecation_headers(response, deprecated_date=None, sunset_date=None, replacement_url=None):
    """
    Add deprecation headers to a response for legacy endpoints.

    Args:
        response: Flask response object
        deprecated_date: Date when the endpoint was deprecated
        sunset_date: Date when the endpoint will be removed
        replacement_url: URL of the replacement endpoint

    Returns:
        Modified response with deprecation headers
    """
    response.headers["Deprecation"] = deprecated_date or "true"

    if sunset_date:
        response.headers["Sunset"] = sunset_date

    if replacement_url:
        response.headers["Link"] = f'<{replacement_url}>; rel="successor-version"'

    response.headers[
        "Warning"
    ] = '299 - "This endpoint is deprecated. Please migrate to the new versioned API."'

    return response


def register_api_blueprints(app):
    """
    Register all API blueprints with the Flask application.

    This function registers:
    - API v1 blueprint at /api/v1
    - Roadmap API blueprint
    - Any other API-related blueprints

    It also sets up:
    - Version detection middleware
    - Response header middleware for version info

    Args:
        app: Flask application instance
    """
    # Import v1 API blueprint
    from .v1 import api_v1_bp

    # Register v1 API
    app.register_blueprint(api_v1_bp)
    app.logger.info("[API] API v1 blueprint registered at /api/v1")

    # Import and register roadmap API
    try:
        from .roadmap_api import roadmap_bp

        app.register_blueprint(roadmap_bp)
        app.logger.info("[API] Roadmap API blueprint registered")
    except ImportError as e:
        app.logger.warning(f"[API] Could not register roadmap API: {e}")

    # Import and register ACM (Application Capability Model) API
    try:
        from .acm_routes import acm_bp

        app.register_blueprint(acm_bp)
        app.logger.info("[API] ACM Technical Capability API registered at /api/acm")
    except ImportError as e:
        app.logger.warning(f"[API] Could not register ACM API: {e}")

    # Import and register ArchiMate Generation API
    try:
        from .archimate_generation_routes import archimate_generation_bp

        app.register_blueprint(archimate_generation_bp)
        app.logger.info("[API] ArchiMate Generation API registered at /api/archimate")
    except ImportError as e:
        app.logger.warning(f"[API] Could not register ArchiMate Generation API: {e}")

    # Set up request processing for version detection
    @app.before_request
    def detect_api_version():
        """Store the requested API version in g for later use."""
        if request.path.startswith("/api/"):
            g.api_version = get_requested_version()

    # Set up response processing for version headers
    @app.after_request
    def add_api_version_to_response(response):
        """Add API version headers to all API responses."""
        if request.path.startswith("/api/"):
            add_version_headers(response)
        return response

    app.logger.info(
        f"[API] API versioning enabled. Supported versions: {', '.join(SUPPORTED_VERSIONS)}"
    )

    return app


def create_deprecated_redirect(old_endpoint, new_endpoint, methods=None):
    """
    Create a deprecated endpoint that redirects to a new versioned endpoint.

    This is useful for maintaining backward compatibility while encouraging
    migration to the new versioned API.

    Args:
        old_endpoint: The old endpoint path
        new_endpoint: The new versioned endpoint path
        methods: HTTP methods to support (default: ['GET'])

    Returns:
        Route decorator function
    """
    methods = methods or ["GET"]

    def decorator(blueprint):
        @blueprint.route(old_endpoint, methods=methods)
        def deprecated_handler():
            from flask import redirect

            from app.utils.api_response import deprecated_response

            # For API clients, return deprecation response
            if (
                request.accept_mimetypes.best_match(["application/json", "text/html"])
                == "application/json"
            ):
                return deprecated_response(new_endpoint)

            # For browsers, redirect
            response = make_response(redirect(new_endpoint, code=301))
            add_deprecation_headers(
                response,
                deprecated_date="2025 - 01 - 01",
                sunset_date="2026 - 01 - 01",
                replacement_url=new_endpoint,
            )
            return response

        return deprecated_handler

    return decorator


# Export public API
__all__ = [
    "register_api_blueprints",
    "get_requested_version",
    "api_version_required",
    "add_version_headers",
    "add_deprecation_headers",
    "create_deprecated_redirect",
    "API_VERSIONS",
    "SUPPORTED_VERSIONS",
    "LATEST_VERSION",
    "DEFAULT_API_VERSION",
]

"""
Swagger / OpenAPI documentation setup.

Feature-flagged behind FeatureFlag(key='openapi_docs').
Swagger UI requires authentication — not publicly accessible.
"""


def init_swagger(app):
    """Initialize Flasgger Swagger UI if available and feature-flagged on."""
    try:
        from flasgger import Swagger
    except ImportError:
        app.logger.info("[SWAGGER] flasgger not installed — skipping OpenAPI docs")
        return

    # Feature-flag check: only initialize when openapi_docs is active
    @app.before_request
    def _check_swagger_auth():
        from flask import request, abort
        from flask_login import current_user

        if request.path.startswith(("/apidocs", "/apispec", "/flasgger_static")):
            # Check feature flag
            try:
                from app.models.feature_flags import FeatureFlag

                flag = FeatureFlag.query.filter_by(key="openapi_docs").first()
                if flag and not flag.is_active:
                    abort(404)
            except Exception:  # fabricated-ok: only rolls back the poisoned txn, returns no value
                from app import db
                db.session.rollback()  # Prevent transaction poisoning

            # Require authentication
            if not current_user.is_authenticated:
                abort(401)

    # Dynamic host from SERVER_NAME or request
    server_name = app.config.get("SERVER_NAME") or "localhost:5000"

    swagger_config = {
        "headers": [],
        "specs": [
            {
                "endpoint": "apispec",
                "route": "/apispec.json",
                "rule_filter": lambda rule: True,
                "model_filter": lambda tag: True,
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/apidocs/",
        "validation": False,
        "title": "Entelim Enterprise Architecture API",
        "description": "RESTful API for TOGAF 9.2 / ArchiMate 3.2 Enterprise Architecture Management",
        "version": "1.0.0",
        "contact": {"name": "API Support", "email": "support@example.com"},
    }

    swagger_template = {
        "swagger": "2.0",
        "info": {
            "title": "Entelim Enterprise Architecture API",
            "description": "RESTful API for TOGAF 9.2 / ArchiMate 3.2 Enterprise Architecture Management",
            "version": "1.0.0",
            "contact": {"name": "API Support", "email": "support@example.com"},
        },
        "host": server_name,
        "basePath": "/",
        "schemes": ["https", "http"],
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "JWT Authorization header using the Bearer scheme. Example: 'Bearer {token}'",
            },
            "Session": {
                "type": "apiKey",
                "name": "session",
                "in": "cookie",
                "description": "Flask-Login session cookie authentication",
            },
        },
        "tags": [
            {"name": "Auth", "description": "Authentication endpoints"},
            {"name": "Health", "description": "API Health Check endpoints"},
            {"name": "Applications", "description": "Application Portfolio Management API"},
            {"name": "Vendors", "description": "Vendor Management and Analysis API"},
            {"name": "Capabilities", "description": "Business Capability Management API"},
            {"name": "Dashboard", "description": "Dashboard data and metrics API"},
            {"name": "Enterprise", "description": "Enterprise Architecture API"},
            {"name": "Workflows", "description": "EA Workflow Engine API"},
            {"name": "ARB", "description": "Architecture Review Board API"},
            {"name": "ArchiMate", "description": "ArchiMate model and viewpoint API"},
            {"name": "AI Chat", "description": "AI-powered architecture assistant"},
        ],
        "paths": {
            "/api/auth/login": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "User login",
                    "description": "Authenticate user and return JWT token",
                    "consumes": ["application/json"],
                    "produces": ["application/json"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "credentials",
                            "description": "Login credentials",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                                "required": ["username", "password"],
                            },
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Login successful",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "token": {"type": "string"},
                                    "user": {"type": "object"},
                                },
                            },
                        },
                        "401": {"description": "Invalid credentials"},
                    },
                }
            },
            "/api/enterprise/applications": {
                "get": {
                    "tags": ["Enterprise"],
                    "summary": "Search applications",
                    "description": "Search enterprise application portfolio with optional filters",
                    "produces": ["application/json"],
                    "parameters": [
                        {
                            "in": "query",
                            "name": "search",
                            "type": "string",
                            "description": "Search term for application name or description",
                        },
                        {
                            "in": "query",
                            "name": "limit",
                            "type": "integer",
                            "default": 10,
                            "description": "Maximum results to return",
                        },
                    ],
                    "responses": {
                        "200": {"description": "List of matching applications"},
                        "401": {"description": "Authentication required"},
                    },
                }
            },
            "/api/vendors/": {
                "get": {
                    "tags": ["Vendors"],
                    "summary": "List vendors",
                    "description": "Retrieve vendor list with optional filtering",
                    "produces": ["application/json"],
                    "responses": {
                        "200": {"description": "Vendor list"},
                        "401": {"description": "Authentication required"},
                    },
                }
            },
            "/api/capabilities/": {
                "get": {
                    "tags": ["Capabilities"],
                    "summary": "List business capabilities",
                    "description": "Retrieve business capability hierarchy",
                    "produces": ["application/json"],
                    "responses": {
                        "200": {"description": "Capability tree"},
                        "401": {"description": "Authentication required"},
                    },
                }
            },
            "/api/arb/submissions": {
                "get": {
                    "tags": ["ARB"],
                    "summary": "List ARB submissions",
                    "description": "Retrieve Architecture Review Board submissions",
                    "produces": ["application/json"],
                    "responses": {
                        "200": {"description": "List of ARB submissions"},
                        "401": {"description": "Authentication required"},
                    },
                },
                "post": {
                    "tags": ["ARB"],
                    "summary": "Create ARB submission",
                    "description": "Submit a new architecture review request",
                    "consumes": ["application/json"],
                    "produces": ["application/json"],
                    "responses": {
                        "201": {"description": "Submission created"},
                        "400": {"description": "Validation error"},
                        "401": {"description": "Authentication required"},
                    },
                },
            },
            "/api/workflows/": {
                "get": {
                    "tags": ["Workflows"],
                    "summary": "List workflow definitions",
                    "description": "Retrieve available EA workflow definitions",
                    "produces": ["application/json"],
                    "responses": {
                        "200": {"description": "List of workflow definitions"},
                        "401": {"description": "Authentication required"},
                    },
                }
            },
            "/monitoring/health": {
                "get": {
                    "tags": ["Health"],
                    "summary": "Health check",
                    "description": "Returns system health status including database, memory, redis, and LLM connectivity",
                    "produces": ["application/json"],
                    "responses": {
                        "200": {
                            "description": "System healthy",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "status": {"type": "string", "enum": ["healthy", "degraded", "unhealthy"]},
                                    "checks": {"type": "object"},
                                },
                            },
                        }
                    },
                }
            },
        },
    }

    try:
        app.logger.info("[SWAGGER] Initializing OpenAPI documentation...")
        Swagger(app, config=swagger_config, template=swagger_template)
        app.logger.info("[SWAGGER] API documentation available at /apidocs/ (requires auth)")
    except Exception as e:
        app.logger.error("[SWAGGER] Failed to initialize: %s", e)

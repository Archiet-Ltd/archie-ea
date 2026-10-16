"""
Inline routes registered directly on the app (health check, API auth, apidocs).
"""

import logging

from flask import Flask

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application Management Page
# ---------------------------------------------------------------------------


def _register_application_management(app):
    """Register /application-management/ route for modal hosting."""
    from flask import redirect, url_for
    from flask_login import login_required

    @app.route("/application-management/")
    @login_required
    def application_management():
        """Application management page - redirects to dashboard."""
        return redirect(url_for("application_mgmt.dashboard"))


def init_inline_routes(app: Flask, config_name: str):
    """Register routes that live directly on the app object."""
    from app.extensions import csrf, db

    _register_health_check(app, config_name, csrf, db)
    _register_api_auth(app, csrf)
    _register_csp_report(app, csrf)
    # _register_metrics removed — ops tooling, not architect-facing
    _register_notifications(app, csrf)
    _register_application_management(app)

    # Convenience redirects for common URL mistakes
    from flask import redirect as _redirect

    @app.route("/capabilities")
    @app.route("/capabilities/")
    def capabilities_redirect():
        return _redirect("/capability-map", code=301)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


# Cache for health check results — avoids repeated slow probes within 30s.
# Module-level rather than a closure local so a test can assert a FRESH probe
# instead of silently asserting against a 30-second-old answer, which is the
# one thing a health-check test must not do.
_health_cache = {"result": None, "timestamp": 0.0}
_CACHE_TTL_SECONDS = 30


def _register_health_check(app, config_name, csrf, db):

    def _probe_with_timeout(fn, timeout_seconds=1.0):
        """Run a probe function with a thread-based timeout.

        Returns the probe result dict, or a timeout status dict if the probe
        exceeds *timeout_seconds*.
        """
        import threading

        result_holder = [None]
        exception_holder = [None]

        def _target():
            try:
                result_holder[0] = fn()
            except Exception as exc:
                exception_holder[0] = exc

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=timeout_seconds)

        if t.is_alive():
            # Probe exceeded its time budget
            return {"status": "timeout", "message": f"Probe exceeded {timeout_seconds}s timeout"}
        if exception_holder[0] is not None:
            raise exception_holder[0]
        return result_holder[0]

    @app.route("/health", methods=["GET"])
    def global_health_check():
        """
        Global API Health Check
        ---
        tags:
          - Health
        summary: Check overall application health
        description: Returns health status of the application including database connectivity, cache, and system metrics
        responses:
          200:
            description: Application is healthy
            schema:
              type: object
              properties:
                success:
                  type: boolean
                  example: true
                service:
                  type: string
                  example: "Flask Enterprise Architecture API"
                status:
                  type: string
                  enum: [healthy, unhealthy, degraded]
                  example: "healthy"
                timestamp:
                  type: string
                  format: date-time
                version:
                  type: string
                  example: "1.0.0"
                checks:
                  type: object
                  properties:
                    database:
                      type: object
                    redis:
                      type: object
                    memory:
                      type: object
          503:
            description: Application is unhealthy
        """
        import time as _time
        from datetime import datetime

        from flask import jsonify
        from sqlalchemy import text

        # Return cached result if still fresh
        now = _time.monotonic()
        if _health_cache["result"] is not None and (now - _health_cache["timestamp"]) < _CACHE_TTL_SECONDS:
            cached = _health_cache["result"].copy()
            cached["timestamp"] = datetime.utcnow().isoformat()
            cached["cached"] = True
            status_code = 200 if cached.get("success", True) else 503
            return jsonify(cached), status_code

        health_status = {
            "success": True,
            "service": "Flask Enterprise Architecture API",
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0.0",
            "environment": config_name,
            "checks": {},
        }

        overall_healthy = True

        # Database connectivity check (2s timeout)
        try:
            def _db_probe():
                with app.app_context():
                    start_time = datetime.now()
                    db.session.execute(text("SELECT 1"))  # tenant-exempt: bootstrap health probe
                    db_time = (datetime.now() - start_time).total_seconds() * 1000
                    return {
                        "status": "healthy",
                        "response_time_ms": round(db_time, 2),
                        "type": app.config.get("SQLALCHEMY_DATABASE_URI", "").split(":")[0]
                        if app.config.get("SQLALCHEMY_DATABASE_URI")
                        else "unknown",
                    }

            health_status["checks"]["database"] = _probe_with_timeout(_db_probe, timeout_seconds=2.0)
            if health_status["checks"]["database"].get("status") == "timeout":
                overall_healthy = False
        except Exception as e:
            overall_healthy = False
            health_status["checks"]["database"] = {
                "status": "unhealthy",
                "error": str(e),
            }

        # Redis connectivity check (1s timeout, with socket timeouts)
        try:
            import redis as redis_lib

            redis_url = app.config.get("REDIS_URL") or app.config.get("RQ_DEFAULT_URL")
            if redis_url:
                def _redis_probe():
                    start_time = datetime.now()
                    r = redis_lib.from_url(
                        redis_url,
                        socket_timeout=1,
                        socket_connect_timeout=1,
                    )
                    r.ping()
                    redis_time = (datetime.now() - start_time).total_seconds() * 1000
                    return {
                        "status": "healthy",
                        "response_time_ms": round(redis_time, 2),
                    }

                health_status["checks"]["redis"] = _probe_with_timeout(_redis_probe, timeout_seconds=1.0)
            else:
                health_status["checks"]["redis"] = {
                    "status": "not_configured",
                    "message": "Redis URL not configured",
                }
        except ImportError:
            health_status["checks"]["redis"] = {
                "status": "not_available",
                "message": "Redis library not installed",
            }
        except Exception as e:
            health_status["checks"]["redis"] = {"status": "unhealthy", "error": str(e)}

        # Memory check (local — no timeout needed, but cap at 1s as safety net)
        try:
            import psutil

            def _memory_probe():
                process = psutil.Process()
                memory_info = process.memory_info()
                return {
                    "status": "healthy",
                    "rss_mb": round(memory_info.rss / 1024 / 1024, 2),
                    "vms_mb": round(memory_info.vms / 1024 / 1024, 2),
                    "percent": round(process.memory_percent(), 2),
                }

            health_status["checks"]["memory"] = _probe_with_timeout(_memory_probe, timeout_seconds=1.0)
        except ImportError:
            health_status["checks"]["memory"] = {
                "status": "unknown",
                "message": "psutil not installed - install for detailed memory monitoring",
            }
        except Exception as e:
            health_status["checks"]["memory"] = {"status": "unknown", "error": str(e)}

        # LLM providers check (1s timeout)
        try:
            def _llm_probe():
                with app.app_context():
                    # Same source of truth as /ai-chat/token-usage and every
                    # AI call: the resolver that actually picks the provider.
                    # This used to count enabled api_settings rows, which
                    # reported "No LLM providers enabled" on a process that was
                    # serving AI requests from an environment-configured
                    # provider — see LLMService.configuration_status.
                    from app.modules.ai_chat.services.llm_service_impl import (
                        LLMService,
                    )

                    status = LLMService.configuration_status()
                    configured = status.get("configured")
                    if configured is None:
                        message = status.get(
                            "reason", "Could not determine LLM configuration."
                        )
                        health = "unknown"
                    elif configured:
                        message = (
                            f"LLM provider configured: {status['provider']} "
                            f"(source: {status['source']})"
                        )
                        health = "healthy"
                    else:
                        message = "No LLM provider is configured"
                        health = "warning"
                    return {
                        "status": health,
                        "configured": configured,
                        "provider": status.get("provider"),
                        # The count of enabled api_settings rows. NOT the
                        # verdict: a provider configured by environment
                        # variable has no row, so this can legitimately be 0
                        # while the AI works.
                        "db_enabled_providers": status.get("db_enabled_providers"),
                        "message": message,
                    }

            health_status["checks"]["llm_providers"] = _probe_with_timeout(_llm_probe, timeout_seconds=1.0)
        except Exception as e:
            health_status["checks"]["llm_providers"] = {
                "status": "unknown",
                "error": str(e),
            }

        # Embedding service configuration check (1s timeout)
        try:
            def _embedding_probe():
                with app.app_context():
                    from app.services.vector_embedding_service import VectorEmbeddingService

                    embedding_status = VectorEmbeddingService.configuration_status()
                    return {
                        "status": "healthy" if embedding_status["ready"] else "warning",
                        "ready": embedding_status["ready"],
                        "enabled_providers": embedding_status["enabled_providers"],
                        "local_embeddings_enabled": embedding_status[
                            "local_embeddings_enabled"
                        ],
                        "default_model": embedding_status["default_model"],
                        "message": "Embedding service configured"
                        if embedding_status["ready"]
                        else "No cloud embedding providers configured",
                    }

            health_status["checks"]["embedding_service"] = _probe_with_timeout(_embedding_probe, timeout_seconds=1.0)
        except Exception as e:
            health_status["checks"]["embedding_service"] = {
                "status": "unknown",
                "error": str(e),
            }

        # Overall status
        health_status["status"] = "healthy" if overall_healthy else "unhealthy"
        health_status["success"] = overall_healthy

        # Cache the result
        _health_cache["result"] = health_status
        _health_cache["timestamp"] = _time.monotonic()

        status_code = 200 if overall_healthy else 503
        return jsonify(health_status), status_code

    # csrf.exempt: health check endpoint — monitoring systems poll this without browser sessions
    csrf.exempt(global_health_check)

    @app.route("/version", methods=["GET"])
    def version_endpoint():
        """
        Build identity
        ---
        tags:
          - Health
        summary: Report the single build identifier this process is running (ARCH-062)
        description: >
          Every cache-busting `?v=` on a static asset, and this endpoint, are stamped
          from the same value (`app._bootstrap.build_info.get_build_id()`) so a bug
          report or a rollback can be tied to exactly one build.
        responses:
          200:
            description: Build identifier
            schema:
              type: object
              properties:
                build_id:
                  type: string
                  example: "a1b2c3d4"
        """
        from flask import jsonify

        from app._bootstrap.build_info import get_build_id

        return jsonify({"build_id": get_build_id()}), 200

    csrf.exempt(version_endpoint)


# ---------------------------------------------------------------------------
# API auth (login / logout / session status)
# ---------------------------------------------------------------------------


def _register_api_auth(app, csrf):
    @app.route("/api/auth/login", methods=["POST"])
    def api_login():
        """
        API Login
        ---
        tags:
          - Auth
        summary: Login to get session authentication
        description: |
          Authenticate with email and password to establish a session.
          After successful login, all subsequent API requests will be authenticated via session cookie.

          An administrator account enrolled in (or required to enrol in)
          multi-factor authentication cannot complete sign-in through this
          endpoint: it always refuses with 401 and error "mfa_required"
          rather than establishing a session, since this API path has no
          multi-factor challenge step of its own.
        consumes:
          - application/json
        parameters:
          - in: body
            name: credentials
            required: true
            schema:
              type: object
              required:
                - email
                - password
              properties:
                email:
                  type: string
                  format: email
                  example: "flask-base-admin@example.com"
                password:
                  type: string
                  format: password
                  example: "password"
                remember_me:
                  type: boolean
                  default: false
                  description: Keep session alive longer
        responses:
          200:
            description: Login successful
            schema:
              type: object
              properties:
                success:
                  type: boolean
                  example: true
                message:
                  type: string
                  example: "Login successful"
                user:
                  type: object
                  properties:
                    id:
                      type: integer
                    email:
                      type: string
                    first_name:
                      type: string
                    last_name:
                      type: string
                    is_admin:
                      type: boolean
          401:
            description: |
              Invalid credentials, or the account requires multi-factor
              authentication this endpoint cannot complete (two distinct
              causes sharing this status code; see error for which one).
            schema:
              type: object
              properties:
                success:
                  type: boolean
                  example: false
                error:
                  type: string
                  enum:
                    - "Invalid email or password"
                    - "mfa_required"
                  example: "Invalid email or password"
          400:
            description: Bad request - missing email or password
        """
        from flask import jsonify, request

        from app.models import User
        from app.services import mfa_service, session_registry

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "JSON body required"}), 400

        email = data.get("email")
        password = data.get("password")
        remember_me = data.get("remember_me", False)

        if not email or not password:
            return jsonify(
                {"success": False, "error": "Email and password required"}
            ), 400

        user = User.find_by_email(email)

        if (
            user is None
            or user.password_hash is None
            or not user.verify_password(password)
        ):
            return jsonify(
                {"success": False, "error": "Invalid email or password"}
            ), 401

        # Decide before anything touches the session: a deactivated account is
        # answered exactly like a wrong password, with no cookie difference.
        if not user.is_active:
            app.logger.info("api login refused for deactivated user %s", user.id)
            return jsonify(
                {"success": False, "error": "Invalid email or password"}
            ), 401

        # CRITICAL fix: an administrator with MFA required must never get a
        # real session from a password alone. This endpoint is a pure JSON
        # API with no API-based TOTP-code submission step (out of scope to
        # add here), so the only safe behaviour is to refuse the login
        # outright -- never call login_and_register -- and tell the caller
        # why. Mirrors the gate app/modules/account/v2/routes/account_routes.py's
        # login() already applies to the form-based login.
        if mfa_service.required_for(user):
            return jsonify(
                {
                    "success": False,
                    "error": "mfa_required",
                    "message": "This account requires multi-factor authentication, which this API endpoint does not support. Sign in through the web application instead.",
                }
            ), 401

        # Fix Session Fixation: Regenerate session ID after successful authentication
        if not session_registry.login_and_register(user, remember=remember_me):
            # Deactivated: answered exactly like a wrong password, so the
            # right password cannot be confirmed for a leaver's account.
            app.logger.info("api login refused for deactivated user %s", user.id)
            return jsonify(
                {"success": False, "error": "Invalid email or password"}
            ), 401

        return (
            jsonify(
                {
                    "success": True,
                    "message": "Login successful",
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "is_admin": user.is_admin(),
                    },
                }
            ),
            200,
        )

    # csrf.exempt: API login — token-based authentication endpoint, no browser session or CSRF token expected
    csrf.exempt(api_login)

    @app.route("/api/auth/logout", methods=["POST"])
    def api_logout():
        """
        API Logout
        ---
        tags:
          - Auth
        summary: Logout and clear session
        description: Logs out the current user and clears the session cookie
        responses:
          200:
            description: Logout successful
            schema:
              type: object
              properties:
                success:
                  type: boolean
                  example: true
                message:
                  type: string
                  example: "Logged out successfully"
        """
        from flask import jsonify

        from app.modules.account.services.account_service import AccountService

        AccountService.logout()
        return jsonify({"success": True, "message": "Logged out successfully"}), 200

    # csrf.exempt: API logout — token-based authentication endpoint, no browser session or CSRF token expected
    csrf.exempt(api_logout)

    @app.route("/api/auth/session", methods=["GET"])
    def api_session_status():
        """
        Session Status
        ---
        tags:
          - Auth
        summary: Check current authentication status
        description: Returns information about the current session and authenticated user
        responses:
          200:
            description: Session status
            schema:
              type: object
              properties:
                authenticated:
                  type: boolean
                user:
                  type: object
                  properties:
                    id:
                      type: integer
                    email:
                      type: string
                    first_name:
                      type: string
                    last_name:
                      type: string
                    is_admin:
                      type: boolean
        """
        from flask import jsonify as json_response
        from flask_login import current_user

        if current_user.is_authenticated:
            return (
                json_response(
                    {
                        "authenticated": True,
                        "user": {
                            "id": current_user.id,
                            "email": current_user.email,
                            "first_name": current_user.first_name,
                            "last_name": current_user.last_name,
                            "is_admin": current_user.is_admin(),
                        },
                    }
                ),
                200,
            )
        else:
            return json_response({"authenticated": False, "user": None}), 200


# ---------------------------------------------------------------------------
# CSP violation reporting
# ---------------------------------------------------------------------------


def _register_csp_report(app, csrf):
    csp_logger = logging.getLogger("csp_violations")

    @app.route("/api/csp-report", methods=["POST"])
    def csp_report():
        """
        CSP Violation Report Receiver
        ---
        tags:
          - Security
        summary: Receive Content-Security-Policy violation reports from browsers
        description: |
          Browsers automatically POST CSP violation reports to this endpoint
          when a Content-Security-Policy directive is violated. No authentication
          or CSRF token is required because browsers send these reports
          automatically without user interaction.
        consumes:
          - application/json
          - application/csp-report
        parameters:
          - in: body
            name: report
            required: true
            schema:
              type: object
              properties:
                csp-report:
                  type: object
                  properties:
                    document-uri:
                      type: string
                    violated-directive:
                      type: string
                    blocked-uri:
                      type: string
                    original-policy:
                      type: string
        responses:
          204:
            description: Report received successfully (no content returned)
          400:
            description: Invalid or missing report body
        """
        from flask import request

        try:
            data = request.get_json(force=True, silent=True)
        except Exception:
            data = None

        if not data or not isinstance(data, dict):
            return "", 400

        report = data.get("csp-report", {})

        csp_logger.warning(
            "CSP violation: directive=%s blocked_uri=%s document_uri=%s referrer=%s source_file=%s line=%s",
            report.get("violated-directive", "unknown"),
            report.get("blocked-uri", "unknown"),
            report.get("document-uri", "unknown"),
            report.get("referrer", ""),
            report.get("source-file", ""),
            report.get("line-number", ""),
        )

        return "", 204

    # csrf.exempt: CSP report endpoint — browsers send violation reports automatically without CSRF tokens
    csrf.exempt(csp_report)


# ---------------------------------------------------------------------------
# Prometheus-compatible /metrics endpoint (T-050)
# ---------------------------------------------------------------------------


def _register_metrics(app, csrf):
    @app.route("/metrics", methods=["GET"])
    def prometheus_metrics():
        """
        Prometheus Metrics
        ---
        tags:
          - Observability
        summary: Prometheus-compatible metrics endpoint
        description: |
          Returns application metrics in Prometheus text exposition format.
          Protected by admin authentication — unauthenticated requests get 401.
        responses:
          200:
            description: Metrics in Prometheus text format
            content:
              text/plain:
                schema:
                  type: string
          401:
            description: Authentication required
        """
        from flask import Response, request
        from flask_login import current_user

        from app.core.observability.metrics import metrics_collector

        # Protect metrics — require PLATFORM admin auth or localhost.
        #
        # D-4 (admin-rbac-active-org continuation): this used to accept any
        # ``current_user.is_admin()`` -- a global Permission.ADMINISTER
        # flag that every self-registered user holds for their own
        # organisation, not a platform-wide authority. Operational metrics
        # span every tenant on the instance, so this was arguably the more
        # severe end of the admin-anywhere bug class this PR fixes
        # elsewhere: an ordinary organisation's own admin (no org-switching
        # needed at all) could read cross-tenant operational metrics.
        # Tightened to the canonical is_platform_admin predicate rather
        # than the org-aware admin_required/org_admin_required check used
        # elsewhere in this PR, since metrics are a genuinely platform-wide
        # resource, not a per-organisation one.
        from app.middleware.tenant_decorators import is_platform_admin

        is_local = request.remote_addr in ("127.0.0.1", "::1", "localhost")
        is_admin = (
            current_user
            and hasattr(current_user, "is_authenticated")
            and current_user.is_authenticated
            and is_platform_admin(current_user)
        )

        if not is_local and not is_admin:
            return Response("Unauthorized\n", status=401, content_type="text/plain")

        summary = metrics_collector.get_summary()

        # Build Prometheus text exposition format
        lines = []
        lines.append("# HELP app_uptime_seconds Application uptime in seconds")
        lines.append("# TYPE app_uptime_seconds gauge")
        lines.append(f"app_uptime_seconds {summary['uptime_seconds']}")
        lines.append("")

        lines.append("# HELP app_requests_total Total requests processed")
        lines.append("# TYPE app_requests_total counter")
        lines.append(f"app_requests_total {summary['total_requests']}")
        lines.append("")

        lines.append("# HELP app_errors_total Total error responses (4xx/5xx)")
        lines.append("# TYPE app_errors_total counter")
        lines.append(f"app_errors_total {summary['total_errors']}")
        lines.append("")

        lines.append("# HELP app_error_rate Overall error rate (0-1)")
        lines.append("# TYPE app_error_rate gauge")
        lines.append(f"app_error_rate {summary['overall_error_rate']}")
        lines.append("")

        lines.append("# HELP app_endpoint_requests_total Requests per endpoint")
        lines.append("# TYPE app_endpoint_requests_total counter")
        for endpoint, data in summary.get("endpoints", {}).items():
            safe_name = endpoint.replace('"', '\\"') if endpoint else "unknown"
            lines.append(
                f'app_endpoint_requests_total{{endpoint="{safe_name}"}} {data["requests"]}'  # raw-html-ok: Prometheus text exposition format (text/plain), not HTML; safe_name already has its own quote-escaping for this format
            )
        lines.append("")

        lines.append("# HELP app_endpoint_errors_total Errors per endpoint")
        lines.append("# TYPE app_endpoint_errors_total counter")
        for endpoint, data in summary.get("endpoints", {}).items():
            safe_name = endpoint.replace('"', '\\"') if endpoint else "unknown"
            lines.append(
                f'app_endpoint_errors_total{{endpoint="{safe_name}"}} {data["errors"]}'  # raw-html-ok: Prometheus text exposition format (text/plain), not HTML; safe_name already has its own quote-escaping for this format
            )
        lines.append("")

        lines.append("# HELP app_endpoint_latency_p50_ms 50th percentile latency (ms)")
        lines.append("# TYPE app_endpoint_latency_p50_ms gauge")
        for endpoint, data in summary.get("endpoints", {}).items():
            safe_name = endpoint.replace('"', '\\"') if endpoint else "unknown"
            lines.append(
                f'app_endpoint_latency_p50_ms{{endpoint="{safe_name}"}} {data["p50_ms"]}'  # raw-html-ok: Prometheus text exposition format (text/plain), not HTML; safe_name already has its own quote-escaping for this format
            )
        lines.append("")

        lines.append("# HELP app_endpoint_latency_p95_ms 95th percentile latency (ms)")
        lines.append("# TYPE app_endpoint_latency_p95_ms gauge")
        for endpoint, data in summary.get("endpoints", {}).items():
            safe_name = endpoint.replace('"', '\\"') if endpoint else "unknown"
            lines.append(
                f'app_endpoint_latency_p95_ms{{endpoint="{safe_name}"}} {data["p95_ms"]}'  # raw-html-ok: Prometheus text exposition format (text/plain), not HTML; safe_name already has its own quote-escaping for this format
            )
        lines.append("")

        body = "\n".join(lines) + "\n"
        return Response(
            body, status=200, content_type="text/plain; version=0.0.4; charset=utf-8"
        )

    # csrf.exempt: Prometheus metrics — internal monitoring scraper cannot include CSRF tokens
    csrf.exempt(prometheus_metrics)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def _register_notifications(app, csrf):
    from flask_login import login_required

    @app.route("/api/notifications", methods=["GET"], strict_slashes=False)
    def api_notifications():
        """PLT-013: Return merged notification feed (Notification + SolutionNotification)."""
        from flask import jsonify
        from flask_login import current_user

        if not current_user.is_authenticated:
            return jsonify({"unread_count": 0, "items": []}), 200

        try:
            from app.models.models import Notification

            gen_items = (
                Notification.query.filter_by(user_id=current_user.id)
                .order_by(Notification.created_at.desc())
                .limit(20)
                .all()
            )
        except Exception:
            gen_items = []

        try:
            # `db` was imported here only for the or_()/and_() that wrapped the
            # removed fixture-name filter. Dropped with it rather than left as an
            # unused import (ruff F401, and lint-core is gated at zero).
            from app.models.solution_governance import SolutionNotification
            from app.models.solution_models import Solution

            sol_items = (
                SolutionNotification.query
                .filter_by(user_id=current_user.id)
                .outerjoin(Solution, SolutionNotification.solution_id == Solution.id)
                # The three ~ilike exclusions removed here filtered solutions
                # named 'J1-AutoTest-%', 'J7-E2E-Test%' and '%-AutoTest-%'.
                # Those are this repository's own fixture names, hidden from
                # what a real user sees. A customer who names a solution
                # "Migration-AutoTest-Rig" lost it from their own screen with
                # no explanation, and the filter made leaked test rows
                # invisible, so the leak never got fixed. Purge the rows;
                # do not hide them.
                # The or_() wrapper existed only to let notifications with no
                # solution survive that filter. With the filter gone it admits
                # every row, so it is removed rather than left as a no-op that
                # reads like a deliberate condition.
                .order_by(SolutionNotification.created_at.desc())
                .limit(20)
                .all()
            )
        except Exception:
            sol_items = []

        merged = []
        for n in gen_items:
            merged.append({
                "id": n.id,
                "source": "notification",
                "message": n.message,
                "read": n.read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "url": getattr(n, "url", None),
            })
        for n in sol_items:
            merged.append({
                "id": n.id,
                "source": "solution",
                "solution_id": n.solution_id,
                "type": n.type,
                "message": n.message,
                "read": n.read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "url": f"/solutions/{n.solution_id}" if n.solution_id else None,
            })

        merged.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        merged = merged[:20]
        unread = sum(1 for n in merged if not n.get("read"))
        return jsonify({"unread_count": unread, "items": merged}), 200

    @app.route("/api/notifications/mark-all-read", methods=["POST"])
    @login_required
    def api_notifications_mark_all_read():
        """PLT-013: Mark every notification for the current user as read."""
        from flask import jsonify
        from flask_login import current_user
        from app import db as _db

        try:
            from app.models.models import Notification

            _db.session.query(Notification).filter_by(
                user_id=current_user.id, read=False
            ).update({"read": True})
        except Exception:
            import logging
            logging.getLogger(__name__).debug("Notification table not available for mark-all-read")

        try:
            from app.models.solution_governance import SolutionNotification

            _db.session.query(SolutionNotification).filter_by(
                user_id=current_user.id, read=False
            ).update({"read": True})
        except Exception:
            import logging
            logging.getLogger(__name__).debug("SolutionNotification table not available for mark-all-read")

        _db.session.commit()
        return jsonify({"success": True}), 200

    @app.route("/api/notifications/<int:notif_id>/mark-read", methods=["POST"])
    @login_required
    def api_notification_mark_read(notif_id):
        """PLT-013: Mark a single notification as read."""
        from flask import jsonify, request
        from flask_login import current_user
        from app import db as _db

        source = request.args.get("source", "notification")
        try:
            if source == "solution":
                from app.models.solution_governance import SolutionNotification

                notif = SolutionNotification.query.filter_by(
                    id=notif_id, user_id=current_user.id
                ).first()
            else:
                from app.models.models import Notification

                notif = Notification.query.filter_by(
                    id=notif_id, user_id=current_user.id
                ).first()

            if notif:
                notif.read = True
                _db.session.commit()
                return jsonify({"success": True}), 200
            return jsonify({"success": False, "error": "Not found"}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    # PLT-018: Business unit scope management
    @app.route("/api/bu-scope", methods=["GET"])
    @login_required
    def api_get_bu_scope():
        """PLT-018: Return available business units and current scope."""
        from flask import jsonify, session
        from flask_login import current_user

        try:
            from app.models.business_layer import BusinessActor

            bus = BusinessActor.query.filter(
                BusinessActor.actor_type.in_(["Department", "Business Unit", "Organization"])
            ).order_by(BusinessActor.name).all()
            items = [{"id": b.id, "name": b.name, "actor_type": b.actor_type} for b in bus]
        except Exception:
            import logging
            logging.getLogger(__name__).debug("BusinessActor table not available for BU scope")
            items = []

        current_bu = session.get("bu_scope_id")
        return jsonify({
            "business_units": items,
            "current_bu_id": current_bu,
            "user_bu_id": getattr(current_user, "business_unit_id", None),
        }), 200

    @app.route("/api/bu-scope", methods=["POST"])
    @login_required
    def api_set_bu_scope():
        """PLT-018: Set the active BU scope in session."""
        from flask import jsonify, request, session

        data = request.get_json(silent=True) or {}
        bu_id = data.get("bu_id")
        if bu_id is not None:
            session["bu_scope_id"] = int(bu_id)
        else:
            session.pop("bu_scope_id", None)
        return jsonify({"success": True, "bu_scope_id": session.get("bu_scope_id")}), 200


# ---------------------------------------------------------------------------
# Application Management Page
# ---------------------------------------------------------------------------


def _register_application_management(app):
    """Register /application-management/ route for modal hosting."""
    from flask import redirect, url_for
    from flask_login import login_required

    @app.route("/application-management/")
    @login_required
    def application_management():
        """Application management page - redirects to dashboard."""
        return redirect(url_for("application_mgmt.dashboard"))

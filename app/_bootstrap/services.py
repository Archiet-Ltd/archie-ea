"""
Service initialization — job queue, audit, prometheus, LLM validation.
"""

import os


def init_services(app, config_name):
    """Initialize background services and integrations."""

    # FAR-012: Session stability check
    _check_session_configuration(app, config_name)

    # Template utils and filters
    from app.utils import register_template_utils

    register_template_utils(app)


def _check_session_configuration(app, config_name):
    """FAR-012: Check session configuration for potential stability issues."""
    if config_name in ("production", "heroku", "unix"):
        if not os.environ.get("SECRET_KEY"):
            app.logger.critical(
                "[SESSION] SECRET_KEY not set in environment! "
                "Sessions will be invalidated on every server restart. "
                "Set SECRET_KEY in your production .env file."
            )
    else:
        # Development mode - warn about unstable sessions
        if not os.environ.get("SECRET_KEY"):
            app.logger.warning(
                "[SESSION] Running without SECRET_KEY. "
                "Sessions will not persist across restarts."
            )

    try:
        from app.template_helpers import register_currency_filters

        register_currency_filters(app)
    except Exception as e:
        app.logger.warning(f"Failed to register currency filters: {e}")

    # SSL
    if not app.debug and not app.testing and not app.config.get("SSL_DISABLE", True):
        try:
            from flask_sslify import SSLify

            SSLify(app)
        except ImportError:
            pass

    # LLM startup validation
    llm_required = app.config.get("LLM_REQUIRED", False)
    if llm_required:
        try:
            from app.services.llm_service import LLMService

            LLMService._get_configured_provider()
            app.logger.info("LLM configuration validated successfully")
        except ValueError as e:
            app.logger.error(f"CRITICAL: LLM is required but not configured: {e}")
            if config_name == "production":
                raise RuntimeError(
                    "LLM_REQUIRED is set but no LLM provider is configured."
                ) from e

    # Error handlers
    try:
        from app.utils.error_handlers import register_error_handlers

        register_error_handlers(app)
        app.logger.info("\u2705 Production error handlers registered")
    except Exception as e:
        app.logger.warning(f"Failed to register error handlers: {e}")

    # Template filters
    try:
        from app.template_helpers import register_template_filters

        register_template_filters(app)
        app.logger.info("\u2705 Template filters registered")
    except Exception as e:
        app.logger.warning(f"Failed to register template filters: {e}")

    # LLM validation on first request
    def validate_llm_configuration():
        with app.app_context():
            try:
                from sqlalchemy.exc import OperationalError, ProgrammingError
                from app.models.models import APISettings

                from app.jobs.tenant_safe_job import platform_scope

                # A boot-time report across every organisation, so it needs the
                # platform scope under row-level security; it only logs.
                with platform_scope("LLM configuration report: any organisation's enabled providers"):
                    enabled = APISettings.query.filter_by(enabled=True).all()
                if not enabled:
                    app.logger.warning("[LLM CONFIG] No LLM providers enabled.")
                    return
                valid = [
                    p.provider for p in enabled if p.api_key and len(p.api_key) > 10
                ]
                if valid:
                    app.logger.info(f"[LLM CONFIG] Active: {', '.join(valid)}")
                else:
                    app.logger.warning("[LLM CONFIG] No valid API keys configured.")
            except (OperationalError, ProgrammingError):
                from app import db as _db
                _db.session.rollback()
            except Exception as e:
                from app import db as _db
                _db.session.rollback()
                app.logger.error(f"[LLM CONFIG] Error: {e}")

    @app.before_request
    def _validate_llm_once():
        if not getattr(app, "_llm_validated", False):
            app._llm_validated = True
            validate_llm_configuration()

    # Vendor catalog bootstrap (dev only — guarded by DEV_VEND_CATALOG env var)
    try:
        if os.environ.get("DEV_VEND_CATALOG", "0") == "1":
            from app.extensions.vendor_catalog_bootstrap import bootstrap_vendor_catalog

            bootstrap_vendor_catalog(app)
            app.logger.info(
                "[DEV] Vendor catalog bootstrap executed (DEV_VEND_CATALOG)."
            )
        else:
            app.logger.debug(
                "[DEV] Vendor Catalog bootstrap skipped (DEV_VEND_CATALOG not set)."
            )
    except Exception as e:
        app.logger.warning(f"[DEV] Vendor Catalog bootstrap failed: {e}")

    # Seed default AI prompt templates (non-critical data seeding — moved from security.py)
    try:
        from app.services.ai_prompt_seeder import seed_default_ai_prompt_templates

        with app.app_context():
            seed_default_ai_prompt_templates()
        app.logger.info("AI prompt templates seeded (if missing).")
    except Exception as e:
        app.logger.warning(f"AI prompt seeding failed (non-critical): {e}")

    try:
        from app.extensions.vendor_catalog_bootstrap import bootstrap_vendor_catalog

        bootstrap_vendor_catalog(app)
        app.logger.info(
            "[DEV] Vendor catalog bootstrap initialized (DEV_VEND_CATALOG)."
        )
    except Exception as e:
        app.logger.warning(f"[DEV] Vendor Catalog bootstrap failed: {e}")

    # Job queue worker
    if config_name != "testing" and not os.environ.get("DISABLE_JOB_QUEUE_WORKER"):
        try:
            from app.services.job_queue_service import get_job_queue_service
            import atexit

            job_queue = get_job_queue_service()
            job_queue.app = app
            job_queue.start_worker()
            atexit.register(lambda: job_queue.stop_worker())
            app.logger.info("\u2705 Job queue worker initialized successfully")
        except Exception as e:
            app.logger.warning(f"Failed to initialize job queue worker: {e}")

    # Audit logging
    try:
        from app.services.audit_integration import audit_integration

        audit_integration.init_app(app)
        audit_integration.enable_for_blueprint("unified_applications")
        audit_integration.enable_for_blueprint("vendor_management")
        audit_integration.enable_for_blueprint("capability_map")
        app.logger.info("\u2705 Audit logging integration initialized")
    except Exception as e:
        app.logger.warning(f"Failed to initialize audit logging: {e}")

    # LLM health check — must run inside an app context so DB queries work
    try:
        from app.services.llm_health_check import validate_llm_config

        from app.jobs.tenant_safe_job import platform_scope

        with app.app_context(), platform_scope("boot-time LLM health check across every organisation"):
            llm_status = validate_llm_config()
        app.config["AI_FEATURES_ENABLED"] = llm_status["configured"]
        if llm_status["configured"]:
            app.logger.info(f"\u2705 AI Features: ENABLED ({llm_status['message']})")
        else:
            app.logger.warning(f"AI Features: DISABLED ({llm_status['message']})")
    except Exception as e:
        app.logger.error(f"LLM validation failed: {e}. AI features disabled.")
        app.config["AI_FEATURES_ENABLED"] = False

    # OpenTelemetry distributed tracing (T-032)
    # Enabled when OTEL_ENABLED=true or OTEL_EXPORTER_OTLP_ENDPOINT is set.
    # Gracefully skipped if opentelemetry packages are not installed.
    _init_opentelemetry(app, config_name)


def _init_opentelemetry(app, config_name):
    """Initialize OpenTelemetry SDK with Flask auto-instrumentation.

    Environment variables:
        OTEL_ENABLED              - Set to 'true' to enable (default: auto-detect)
        OTEL_EXPORTER_OTLP_ENDPOINT - OTLP collector endpoint (e.g. http://localhost:4317)
        OTEL_SERVICE_NAME         - Service name (default: archie)
        OTEL_TRACES_EXPORTER      - 'otlp' or 'console' (default: otlp if endpoint set)
    """
    if config_name == "testing":
        return

    otel_enabled = os.environ.get("OTEL_ENABLED", "").lower() == "true"
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")

    # Auto-enable if endpoint is configured
    if not otel_enabled and not otlp_endpoint:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.instrumentation.flask import FlaskInstrumentor

        service_name = os.environ.get("OTEL_SERVICE_NAME", "archie")
        resource = Resource(attributes={SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)

        exporter_type = os.environ.get(
            "OTEL_TRACES_EXPORTER", "otlp" if otlp_endpoint else "console"
        )

        if exporter_type == "otlp" and otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            except ImportError:
                try:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                        OTLPSpanExporter,
                    )

                    exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                except ImportError:
                    app.logger.warning(
                        "[OTEL] OTLP exporter not installed, falling back to console"
                    )
                    exporter = ConsoleSpanExporter()
        else:
            exporter = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        FlaskInstrumentor().instrument_app(app)

        app.logger.info(
            f"\u2705 OpenTelemetry tracing initialized (service={service_name}, exporter={exporter_type})"
        )

    except ImportError:
        app.logger.warning(
            "[OTEL] opentelemetry-sdk not installed. "
            "Install: pip install opentelemetry-sdk opentelemetry-instrumentation-flask"
        )
    except Exception as e:
        app.logger.warning(f"[OTEL] Failed to initialize OpenTelemetry: {e}")

import os
from datetime import timedelta

# Optional raygun import
try:
    from raygun4py.middleware import flask as flask_raygun

    HAS_RAYGUN = True
except ImportError:
    HAS_RAYGUN = False
    flask_raygun = None

# Python 3 only (Python 2 is no longer supported)
import urllib.parse

basedir = os.path.abspath(os.path.dirname(__file__))

# Canonical module configuration. The v2 ("guardrail") modules are the active code
# path (see CLAUDE.md), and the navigation/templates reference their endpoints, so
# they MUST be registered by default. Without these flags a fresh install registers
# only legacy/partial blueprints and every authenticated page 500s on a sidebar
# url_for to an unregistered endpoint (e.g. solution_design.list_solutions). Using
# setdefault means an explicit env value (true/false) is still respected.
for _canonical_flag in (
    "USE_SOLUTIONS_STRATEGIC_GUARDRAILS",
    "USE_NEW_SOLUTIONS_STRATEGIC",
    "USE_CAPABILITIES_GUARDRAILS",
    "USE_NEW_CAPABILITIES",
    "USE_DASHBOARD_GUARDRAILS",
    "USE_NEW_DASHBOARD",
    "USE_APPLICATIONS_GUARDRAILS",
    "USE_NEW_APPLICATIONS",
    "USE_ARCHITECTURE_GUARDRAILS",
    "USE_NEW_ARCHITECTURE",
    "USE_AI_CHAT_GUARDRAILS",
    "USE_NEW_AI_CHAT",
    # NOTE: vendors are intentionally NOT defaulted to the v2/guardrail path.
    # The always-on unified vendor blueprints serve the vendor pages AND the
    # /api/vendors/* JSON the vendor dashboard widgets call (list, ranking).
    # Forcing USE_VENDORS_GUARDRAILS switches to v2-only and drops the unified
    # API, 404-ing those widgets ("not found" in the UI).
):
    os.environ.setdefault(_canonical_flag, "true")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_positive_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except (AttributeError, TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

# Load environment variables from .env file
from dotenv import load_dotenv

# Load from .env file using explicit path (ensures it works regardless of working directory)
env_path = os.path.join(basedir, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)
    print(f"Loaded environment from: {env_path}")
else:
    # Fallback to default behavior
    load_dotenv()
    print("Loaded environment from default location")

# Verify critical API keys are loaded
#
# This list must track LLMService's own provider_priority
# (app/modules/ai_chat/services/llm_service_impl.py) - it previously omitted
# OPENROUTER_API_KEY and AZURE_OPENAI_API_KEY, so this printed "no LLM API
# keys found" even when _get_configured_provider() had already found and was
# using a real OpenRouter key from the environment. Two independent checks
# disagreeing, with the wrong one printed first and loudest, is what made
# every session reading this log line report "no provider configured" as
# fact without verifying against the function that actually gates AI
# features.
_api_keys_loaded = []
for key in [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "HUGGINGFACE_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_API_KEY",
]:
    if os.getenv(key):
        _api_keys_loaded.append(key)
if _api_keys_loaded:
    print(f"API keys loaded: {', '.join(_api_keys_loaded)}")
else:
    print("WARNING: No LLM API keys found in environment!")


class Config:
    APP_NAME = os.environ.get("APP_NAME", "A.R.C.H.I.E.")
    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        import secrets

        SECRET_KEY = secrets.token_hex(32)
        print(
            "WARNING: SECRET_KEY env var not set. A random key has been generated "
            "for this session. Sessions will NOT persist across restarts. "
            "Set SECRET_KEY in your .env file for production use."
        )
    SQLALCHEMY_COMMIT_ON_TEARDOWN = True
    # Separate from SECRET_KEY so database command capabilities can be rotated
    # without invalidating sessions. This value is shared only by the schema
    # owner (which installs the verifier key) and application processes (which
    # sign exact command documents); it is never exposed through database SQL.
    TRANSFORMATION_COMMAND_CAPABILITY_SECRET = os.environ.get(
        "TRANSFORMATION_COMMAND_CAPABILITY_SECRET", ""
    )
    TRANSFORMATION_COMMAND_CAPABILITY_PREVIOUS_SECRETS = os.environ.get(
        "TRANSFORMATION_COMMAND_CAPABILITY_PREVIOUS_SECRETS", ""
    )
    # Typed ARB waiver expiry is disabled until tenants, one service principal
    # per tenant, and a scheduler capability are explicitly configured.
    ARB_CONDITION_EXPIRY_CAPABILITY = os.environ.get(
        "ARB_CONDITION_EXPIRY_CAPABILITY", ""
    )
    ARB_CONDITION_EXPIRY_PRINCIPALS = os.environ.get(
        "ARB_CONDITION_EXPIRY_PRINCIPALS", "{}"
    )
    ARB_CONDITION_EXPIRY_ORGANIZATION_IDS = os.environ.get(
        "ARB_CONDITION_EXPIRY_ORGANIZATION_IDS", ""
    )
    ARB_CONDITION_EXPIRY_BATCH_SIZE = os.environ.get(
        "ARB_CONDITION_EXPIRY_BATCH_SIZE", "100"
    )
    ARB_CONDITION_EXPIRY_INTERVAL_MINUTES = os.environ.get(
        "ARB_CONDITION_EXPIRY_INTERVAL_MINUTES", "5"
    )

    # T-002: capability maturity projection — recurring interval, configurable
    # downward. Default 15 minutes per the task brief.
    CAPABILITY_PROJECTION_INTERVAL_MINUTES = os.environ.get(
        "CAPABILITY_PROJECTION_INTERVAL_MINUTES", "15"
    )

    # T-003: derived-fact recompute (DE-4) — recurring interval, configurable
    # downward. Default 10 minutes per the task brief (ADR-003).
    DERIVED_RECOMPUTE_INTERVAL_MINUTES = os.environ.get(
        "DERIVED_RECOMPUTE_INTERVAL_MINUTES", "10"
    )

    # Session security — 8-hour session lifetime, 30-day remember-me cookie
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    # F-07: the 8 hours above is an ABSOLUTE cap; it is not an idle timeout and
    # never was. SESSION_IDLE_TIMEOUT is the separate, server-enforced limit on
    # how long a session may sit unused before it is torn down
    # (app/_bootstrap/session_policy.py). 30 minutes is the enterprise-typical
    # value; set to 0 to disable.
    SESSION_IDLE_TIMEOUT = timedelta(
        minutes=int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "30") or 30)
    )
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    SESSION_REFRESH_EACH_REQUEST = True  # FAR-012: Ensure session is refreshed on every request
    REMEMBER_COOKIE_REFRESH_EACH_REQUEST = True  # FAR-012: Refresh remember-me cookie on activity

    # Cookie attributes — set explicitly at the base class rather than relying on
    # Flask/Flask-Login's own defaults (finding A-04/ARCH-051/C-10). Flask's
    # built-in defaults happen to match most of this (HttpOnly=True,
    # SameSite=Lax) but that is an implicit fact about the framework version in
    # use, not a control this app owns — it silently stops matching the moment
    # a dependency bump changes the default, or SameSite=None is ever needed
    # for a specific integration. ProductionConfig overrides SECURE to True.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = _env_bool("REMEMBER_COOKIE_SECURE", False)

    # JWT Configuration
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY") or SECRET_KEY
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=1)

    # Email
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.sendgrid.net")
    MAIL_PORT = os.environ.get("MAIL_PORT", 587)
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", True)
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", False)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER")

    # Analytics
    GOOGLE_ANALYTICS_ID = os.environ.get("GOOGLE_ANALYTICS_ID", "")
    SEGMENT_API_KEY = os.environ.get("SEGMENT_API_KEY", "")

    # Admin account
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
    if not ADMIN_PASSWORD:
        import secrets

        ADMIN_PASSWORD = secrets.token_urlsafe(16)
        print(
            "WARNING: ADMIN_PASSWORD env var not set. Generated random password. "
            "Set ADMIN_PASSWORD in your .env file for production use."
        )
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "flask-base-admin@example.com")
    # Optional same-tenant, confirmed user who owns generated application-owner
    # evidence requests. Invalid/unavailable IDs fall back to the workstream lead.
    TRANSFORMATION_PORTFOLIO_STEWARD_ID = _env_optional_positive_int(
        "TRANSFORMATION_PORTFOLIO_STEWARD_ID"
    )
    EMAIL_SUBJECT_PREFIX = "[{}]".format(APP_NAME)
    EMAIL_SENDER = "{app_name} Admin <{email}>".format(
        app_name=APP_NAME, email=MAIL_USERNAME
    )

    # ARCHIE Deploy: Credential encryption + Coolify PaaS + n8n connector sync
    CREDENTIAL_ENCRYPTION_KEY = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
    COOLIFY_API_URL = os.environ.get("COOLIFY_API_URL", "http://localhost:8000")
    COOLIFY_API_TOKEN = os.environ.get("COOLIFY_API_TOKEN", "")
    COOLIFY_DOMAIN_SUFFIX = os.environ.get("COOLIFY_DOMAIN_SUFFIX", "archie.example.com")
    N8N_API_URL = os.environ.get("N8N_API_URL", "http://localhost:5678")
    N8N_API_TOKEN = os.environ.get("N8N_API_TOKEN", "")

    REDIS_URL = os.getenv("REDIS_URL") or os.getenv(
        "REDISTOGO_URL", "redis://localhost:6379/0"
    )

    RAYGUN_APIKEY = os.environ.get("RAYGUN_APIKEY")

    # Stripe Billing (COM-001) — platform works without these; set in production .env
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_PRO = os.environ.get("STRIPE_PRICE_PRO", "")
    STRIPE_PRICE_ENTERPRISE = os.environ.get("STRIPE_PRICE_ENTERPRISE", "")

    # Jira inbound webhook (TPM-008). POST /webhooks/jira is unauthenticated and
    # csrf-exempt by necessity, so this HMAC secret is its ONLY access control.
    # The handler read this key before it was ever declared here, so it always
    # resolved to "" - and the signature check treated an empty secret as
    # "skip verification". Left unset the endpoint now rejects every request
    # (fail closed); set it to the secret configured on the Jira webhook to
    # enable the integration.
    JIRA_WEBHOOK_SECRET = os.environ.get("JIRA_WEBHOOK_SECRET", "")

    # Parse the REDIS_URL to set RQ config variables
    urllib.parse.uses_netloc.append("redis")
    url = urllib.parse.urlparse(REDIS_URL)

    RQ_DEFAULT_HOST = url.hostname
    RQ_DEFAULT_PORT = url.port
    RQ_DEFAULT_PASSWORD = url.password
    RQ_DEFAULT_DB = 0

    # Celery configuration (async task queue)
    # Feature-flagged: set CELERY_ENABLED=true to enable async batch import
    CELERY_ENABLED = os.environ.get("CELERY_ENABLED", "false").lower() == "true"
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL)
    CELERY_TASK_SERIALIZER = "json"
    CELERY_ACCEPT_CONTENT = ["json"]
    CELERY_RESULT_SERIALIZER = "json"
    CELERY_TIMEZONE = "UTC"
    CELERY_ENABLE_UTC = True

    # SSO / Enterprise Identity (S0-01)
    # Supports Azure AD and Okta (OIDC).
    # Feature-flagged: SSO routes only active when FeatureFlag(key='sso_authentication') is enabled.
    SSO_PROVIDERS = {
        "azure": {
            "client_id": os.environ.get("AZURE_AD_CLIENT_ID", ""),
            "client_secret": os.environ.get("AZURE_AD_CLIENT_SECRET", ""),
            "server_metadata_url": os.environ.get(
                "AZURE_AD_METADATA_URL",
                "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration".format(
                    tenant=os.environ.get("AZURE_AD_TENANT_ID", "common")
                ),
            ),
            "client_kwargs": {"scope": "openid email profile"},
        },
        "okta": {
            "client_id": os.environ.get("OKTA_CLIENT_ID", ""),
            "client_secret": os.environ.get("OKTA_CLIENT_SECRET", ""),
            "server_metadata_url": os.environ.get("OKTA_METADATA_URL", ""),
            "client_kwargs": {"scope": "openid email profile"},
        },
    }

    # Internationalization (S2-01) — date/number/currency formatting
    # Full string translation (gettext) is Phase 2.
    BABEL_DEFAULT_LOCALE = os.environ.get("BABEL_DEFAULT_LOCALE", "en")
    BABEL_DEFAULT_TIMEZONE = os.environ.get("BABEL_DEFAULT_TIMEZONE", "UTC")

    # Read Replica Configuration (ENH-012)
    # Set DATABASE_READ_REPLICA_URL to a replica DB connection string to offload
    # dashboard and portfolio GET queries from the primary. When not configured,
    # all queries use the default SQLALCHEMY_DATABASE_URI (no-op).
    DATABASE_READ_REPLICA_URL = os.environ.get("DATABASE_READ_REPLICA_URL", "")
    USE_READ_REPLICA_FOR_DASHBOARD = (
        os.environ.get("USE_READ_REPLICA_FOR_DASHBOARD", "false").lower() == "true"
    )

    # AI-originated CRUD approval gate (A95-008)
    # When true, AI CRUD endpoints return 202 pending_approval instead of writing directly.
    # Defaults ON (governance wave, Aug 2026): the @require_ai_approval-decorated
    # /ai-chat/data/* endpoints (app/modules/ai_chat/routes/workflow_routes.py) and the
    # LLM-agent mutating-tool queue are AI-*initiated* writes — an LLM decided to make
    # them, so they go through a human approval queue by default. This does NOT gate
    # the ai_chat slash commands (/link-capability, /generate-from-capabilities in
    # command_parser_service.py): those are parsed verbatim from the user's own typed
    # message, not proposed by the LLM, so they execute directly regardless of this
    # flag — see the comments at their write sites. Set REQUIRE_AI_APPROVAL=false to
    # restore the pre-Aug-2026 direct-write behaviour for the LLM-agent paths.
    #
    # This comment described the intent, not the behaviour, until 31 Aug 2026:
    # the agent tool loop never read this key. Its queue decision came from the
    # per-session `agent_auto_execute` preference alone, which any authenticated
    # user could flip via POST /session/toggle-auto-execute — so an operator who
    # set this true still had every tier:"auto" mutating tool executing without
    # an approval row. Both agent call sites now resolve through
    # chat_core._agent_auto_execute_allowed(), which fails closed, and the
    # ai-approval-honoured gate keeps them there.
    REQUIRE_AI_APPROVAL = os.environ.get("REQUIRE_AI_APPROVAL", "true").lower() == "true"

    # Outbound calls to third-party APIs (GitHub, Crunchbase, G2) from
    # app/services/api_clients/. Default FALSE, which is the air-gapped posture
    # ADR-0005 commits this product to.
    #
    # Nothing enforced this until 31 Aug 2026: the air-gap gate checks that UI
    # assets are not fetched from a CDN and says nothing about the server making
    # its own calls. An adversarial sweep found /api/pipeline/market-analysis/
    # <category> reaching https://api.github.com inside a logged-in request and
    # echoing third-party repository text back as this product's "market
    # analysis" -- including, for a nonsense category, the description of a
    # credential-phishing script, returned with success:true.
    #
    # Set ALLOW_EXTERNAL_API_CALLS=true only where egress is understood and
    # permitted. With it false the clients return an honest failure rather than
    # fabricated analysis.
    ALLOW_EXTERNAL_API_CALLS = (
        os.environ.get("ALLOW_EXTERNAL_API_CALLS", "false").lower() == "true"
    )

    # North Star Navigation (NORTH-STAR-001)
    # Enterprise-grade navigation for Fortune 500 TOGAF/ArchiMate practitioners.
    # When enabled: renamed navigation items, persona-based filtering, hidden admin items.
    # Rollback: set to false and restart service (zero risk, instant revert).
    # ENABLED 2026-04-02: Quick wins from NORTH_STAR_ASSESSMENT.md Part 10
    ENABLE_NORTH_STAR_NAV = _env_bool("ENABLE_NORTH_STAR_NAV", True)

    # North Star Navigation Phase 2 (NORTH-STAR-002)
    # Complete ArchiMate 3.2 layer-based navigation with all 55 element types.
    # When enabled: 6-section IA, layer-first architecture, Portfolio/Architecture separation.
    # Depends on: ENABLE_NORTH_STAR_NAV=True (Phase 1 must be active)
    # Rollback: set to false and restart service.
    ENABLE_NORTH_STAR_PHASE2 = _env_bool("ENABLE_NORTH_STAR_PHASE2", False)

    # Page-aware AI guide is fail-closed by default and only activates when
    # this flag is explicitly enabled and an LLM provider is configured.
    AI_PAGE_GUIDE_ENABLED = _env_bool("AI_PAGE_GUIDE_ENABLED", False)

    # File Upload Settings
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    ALLOWED_EXTENSIONS = {
        "pdf",
        "docx",
        "doc",
        "xlsx",
        "xls",
        "png",
        "jpg",
        "jpeg",
        "vsdx",
        "txt",
        "csv",
        "md",
    }
    UPLOAD_FOLDER = os.path.join(basedir, "uploads", "documents")

    @staticmethod
    def init_app(app):
        """Base class hook — subclasses override to customize app initialization."""
        # Behind a TLS-terminating reverse proxy (Caddy/nginx/Traefik), trust the
        # X-Forwarded-* headers so request.scheme/host reflect the real https URL.
        # Without this the app builds http:// URLs behind https, causing
        # mixed-content blocking. Opt-in via TRUST_PROXY so forged headers are not
        # trusted when the app is exposed directly.
        if os.environ.get("TRUST_PROXY", "false").lower() in ("1", "true", "yes") \
                and not getattr(app, "_proxyfix_applied", False):
            from werkzeug.middleware.proxy_fix import ProxyFix
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
            app._proxyfix_applied = True
        return


class DevelopmentConfig(Config):
    DEBUG = True
    ASSETS_DEBUG = True

    # N+1 query detection — set SQLALCHEMY_ECHO=true in .env to enable query logging
    SQLALCHEMY_ECHO = os.environ.get("SQLALCHEMY_ECHO", "false").lower() == "true"
    JIRA_AUTO_PUSH = os.environ.get("JIRA_AUTO_PUSH", "false").lower() == "true"

    # PostgreSQL configuration (REQUIRED - validated at runtime)
    # Default to a local Postgres instance for development when DATABASE_URL is not set.
    # Standard port 5432; set DATABASE_URL in .env to override (e.g. Windows installs
    # that run Postgres on 5439).
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5432/archie",  # secrets-safety-ok
    )

    # SERVER_NAME must NOT be hardcoded — Flask-WTF CSRF validates the Referer
    # header against SERVER_NAME, so a hardcoded value causes "CSRF token is missing"
    # when the browser omits the Referer header (privacy settings, extensions, etc.).
    # Only set when explicitly provided via environment variable.
    SERVER_NAME = os.environ.get("SERVER_NAME") or None
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "http")
    APPLICATION_ROOT = os.environ.get("APPLICATION_ROOT", "/")

    # PostgreSQL connection options (optimized for local development)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "connect_args": {
            "client_encoding": "utf8",
            "options": "-c client_encoding=utf8",
        },
        "pool_reset_on_return": "commit",
    }

    @classmethod
    def init_app(cls, app):
        # Ensure a sensible default exists for development (Postgres is the standard)
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            app.logger.warning(
                "No DATABASE_URL provided; falling back to local Postgres default."
            )


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    TRANSFORMATION_COMMAND_CAPABILITY_SECRET = "74" * 32
    TRANSFORMATION_COMMAND_CAPABILITY_PREVIOUS_SECRETS = ""

    # Brute-force protection is a production control; under test it throttles the
    # suite instead of an attacker. /account/login is capped at 10 POSTs per
    # minute keyed on IP, and every smoke test signs in from 127.0.0.1 — so as
    # the suite grows it is structurally guaranteed to refuse its own logins.
    #
    # It did. A full single-process run failed 31 tests and errored 9 more, every
    # one of them a sign-in refused with "Rate limit exceeded: 10 per 1m", across
    # the authorisation matrix, the archetype journeys and the AI chat journey.
    # The archetype that lost varied by timing, which made it read as a race.
    #
    # tests/test_rate_limiting.py covers the limiter directly, below this switch,
    # and asserts the login route still carries the decorator — so disabling it
    # here costs no coverage of the control. It only stops the control from
    # deciding the outcome of tests that are not about it.
    RATE_LIMITING_ENABLED = False

    # PostgreSQL REQUIRED for tests (matches production behavior)
    # SQLite is NOT supported - tests must use PostgreSQL for consistency
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5432/archie_test",  # secrets-safety-ok
    )

    # PostgreSQL connection options for testing.
    #
    # NullPool, not a sized pool: many older test modules hand-roll a module-
    # scoped `app` fixture (each builds its own engine) and never dispose it, so
    # a pooled engine keeps up to pool_size+max_overflow connections open per
    # module. Across the ~2350-test suite that accumulated past Postgres's
    # default max_connections=100 and the run died mid-way with "FATAL: sorry,
    # too many clients already" (15 errors on CI). NullPool opens a connection
    # per checkout and closes it on return, so nothing idle is held between
    # tests and the count stays flat regardless of how many engines exist.
    from sqlalchemy.pool import NullPool as _NullPool  # noqa: PLC0415

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "poolclass": _NullPool,
    }

    @classmethod
    def init_app(cls, app):
        # Validate PostgreSQL is being used (not SQLite)
        db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if "sqlite" in db_uri.lower():
            raise ValueError(
                "TEST_DATABASE_URL must be PostgreSQL, not SQLite. "
                "Set TEST_DATABASE_URL to a PostgreSQL connection string. "
                "Example: postgresql://postgres:postgres@localhost:5432/flask_test"  # secrets-safety-ok
            )

        # The guard used to be `if not db_uri`, which can never be true: the
        # default above always fills it in. So the one warning that would tell
        # you which database you are actually on was dead code, and the silent
        # fallback is expensive - it sends pytest at a long-lived `archie_test`
        # on the default port, which drifts from the models over time. That cost
        # a real misdiagnosis: a run against the stale fallback failed with
        # UniqueViolation on ix_value_streams_code, an index that does not exist
        # in a database built from the current models, and the failures were
        # initially read as a code defect.
        if not os.environ.get("TEST_DATABASE_URL"):
            app.logger.warning(
                "TEST_DATABASE_URL is not set; falling back to %s. "
                "A long-lived fallback database drifts from the models - set "
                "TEST_DATABASE_URL explicitly before trusting a failure.",
                db_uri,
            )

        print("THIS APP IS IN TESTING MODE. YOU SHOULD NOT SEE THIS IN PRODUCTION.")


class ProductionConfig(Config):
    DEBUG = False
    USE_RELOADER = False

    # Session cookie security.
    #
    # Secure defaults to TRUE here. It used to be derived from
    # PREFERRED_URL_SCHEME, which ProductionConfig never sets (only
    # DevelopmentConfig does), so the default resolved to "http" -> False and a
    # production deployment shipped session and remember-me cookies over plain
    # HTTP unless an operator happened to set the env var. A security default
    # that depends on a variable this class does not define is not a default.
    #
    # Set SESSION_COOKIE_SECURE=false explicitly for the rare TLS-terminated-
    # nowhere deployment; that is now a visible decision rather than the
    # accident.
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", True)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = _env_bool("REMEMBER_COOKIE_SECURE", True)
    REMEMBER_COOKIE_HTTPONLY = True
    # Was unset, so the remember-me cookie had no SameSite protection at all
    # while the session cookie did.
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # PostgreSQL for production (REQUIRED - validated at runtime)
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SSL_DISABLE = os.environ.get("SSL_DISABLE", "False") == "True"
    JIRA_AUTO_PUSH = os.environ.get("JIRA_AUTO_PUSH", "false").lower() == "true"

    # PostgreSQL connection options (optimized for production)
    # Sized per-process, not per-deployment: preload_app=True plus the
    # post_fork db.engine.dispose() hook means every gunicorn worker and the
    # RQ worker gets its own full pool. The hardcoded 20+30 let 2 workers +
    # RQ worker demand 150 connections against max_connections=100. Env-
    # overridable so the pool can track GUNICORN_THREADS without a code change.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": int(os.environ.get("SQLALCHEMY_POOL_SIZE", 20)),
        "max_overflow": int(os.environ.get("SQLALCHEMY_MAX_OVERFLOW", 30)),
        "pool_timeout": 30,
        "connect_args": {
            "client_encoding": "utf8",
            "options": "-c client_encoding=utf8",
        },
        "pool_reset_on_return": "commit",
    }

    @classmethod
    def init_app(cls, app):
        # Validate DATABASE_URL is set
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise ValueError(
                "DATABASE_URL environment variable is required. PostgreSQL must be configured for production."
            )

        Config.init_app(app)
        assert os.environ.get("SECRET_KEY"), "SECRET_KEY IS NOT SET!"
        if not app.config.get("TRANSFORMATION_COMMAND_CAPABILITY_SECRET"):
            raise ValueError(
                "TRANSFORMATION_COMMAND_CAPABILITY_SECRET is required in production"
            )

        # Trust the nginx reverse proxy's forwarded headers (X-Forwarded-Proto /
        # -For) so request.scheme/host reflect the real client-facing protocol.
        # Without this, behind TLS the app still builds http:// external URLs
        # (breaking OAuth redirect URIs, email links) and treats requests as
        # insecure. nginx is the single trusted hop. Idempotent so a subclass
        # (Heroku/Unix) re-running init_app cannot double-wrap.
        if not getattr(app, "_proxyfix_applied", False):
            from werkzeug.middleware.proxy_fix import ProxyFix

            app.wsgi_app = ProxyFix(
                app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
            )
            app._proxyfix_applied = True

        if HAS_RAYGUN and flask_raygun and app.config.get("RAYGUN_APIKEY"):
            flask_raygun.Provider(app, app.config["RAYGUN_APIKEY"]).attach()


class HerokuConfig(ProductionConfig):
    @classmethod
    def init_app(cls, app):
        # ProductionConfig.init_app applies ProxyFix (idempotently); no need to
        # re-wrap here.
        ProductionConfig.init_app(app)


class UnixConfig(ProductionConfig):
    @classmethod
    def init_app(cls, app):
        ProductionConfig.init_app(app)

        # Log to syslog
        import logging
        from logging.handlers import SysLogHandler

        syslog_handler = SysLogHandler()
        syslog_handler.setLevel(logging.WARNING)
        app.logger.addHandler(syslog_handler)


# Currency Configuration System
class CurrencyConfig:
    """Centralized currency configuration for the application"""

    DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "GBP")

    SUPPORTED_CURRENCIES = {
        "GBP": {
            "symbol": "£",
            "code": "GBP",
            "position": "prefix",  # prefix or suffix
            "decimal_places": 2,
            "thousands_separator": ",",
            "name": "British Pound Sterling",
        },
        "USD": {
            "symbol": "$",
            "code": "USD",
            "position": "prefix",
            "decimal_places": 2,
            "thousands_separator": ",",
            "name": "United States Dollar",
        },
        "EUR": {
            "symbol": "€",
            "code": "EUR",
            "position": "suffix",
            "decimal_places": 2,
            "thousands_separator": ".",
            "name": "Euro",
        },
        "JPY": {
            "symbol": "¥",
            "code": "JPY",
            "position": "prefix",
            "decimal_places": 0,
            "thousands_separator": ",",
            "name": "Japanese Yen",
        },
        "CAD": {
            "symbol": "C$",
            "code": "CAD",
            "position": "prefix",
            "decimal_places": 2,
            "thousands_separator": ",",
            "name": "Canadian Dollar",
        },
        "AUD": {
            "symbol": "A$",
            "code": "AUD",
            "position": "prefix",
            "decimal_places": 2,
            "thousands_separator": ",",
            "name": "Australian Dollar",
        },
    }

    @classmethod
    def get_currency_config(cls, currency_code=None):
        """Get currency configuration for specified currency code"""
        if not currency_code:
            currency_code = cls.DEFAULT_CURRENCY

        config = cls.SUPPORTED_CURRENCIES.get(currency_code)
        if not config:
            # Fallback to default currency instead of crashing
            config = cls.SUPPORTED_CURRENCIES.get(cls.DEFAULT_CURRENCY)

        return config

    @classmethod
    def get_org_currency_code(cls, organization=None):
        """H-04: single source of truth for "which currency does this org use".

        Reads `organization.settings['currency_code']` (a plain JSON field
        already on Organization — no schema change) and falls back to
        DEFAULT_CURRENCY. Every server-rendered money figure AND the client
        currencyManager default should both come from this, not from a
        hardcoded '$' or '£' baked into a template.
        """
        code = None
        try:
            if organization is not None:
                settings = getattr(organization, "settings", None) or {}
                code = settings.get("currency_code")
        except Exception:
            code = None
        if not code or not cls.is_supported(code):
            code = cls.DEFAULT_CURRENCY
        return code

    @classmethod
    def get_org_currency_config(cls, organization=None):
        """Currency config dict for the given organization (see get_org_currency_code)."""
        return cls.get_currency_config(cls.get_org_currency_code(organization))

    @classmethod
    def is_supported(cls, currency_code):
        """Check if currency is supported"""
        return currency_code in cls.SUPPORTED_CURRENCIES

    @classmethod
    def get_all_supported_codes(cls):
        """Get list of all supported currency codes"""
        return list(cls.SUPPORTED_CURRENCIES.keys())


config = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
    "heroku": HerokuConfig,
    "unix": UnixConfig,
}

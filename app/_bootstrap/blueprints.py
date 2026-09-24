"""
Blueprint registration — all legacy and feature-flagged blueprints.

Each ``_register_*`` helper handles one domain. Feature-flagged modules use
the pattern: try new module → fall back to legacy blueprint(s).
"""

import os
import logging

logger = logging.getLogger(__name__)


def _csrf_exempt_blueprint(app, blueprint):
    """Exempt all routes in a blueprint from CSRF protection."""
    # Iterate through all routes registered in the app
    # Find routes that belong to this blueprint and mark their view functions as exempt
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith(blueprint.name + "."):
            view_func = app.view_functions.get(rule.endpoint)
            if view_func is not None:
                # Set the csrf_exempt attribute directly on the view function
                view_func.csrf_exempt = True
                logger.debug(f"[CSRF] Marked view as exempt: {rule.endpoint}")
    
    count = len([r for r in app.url_map.iter_rules() if r.endpoint.startswith(blueprint.name + ".")])
    logger.info(f"[CSRF] Exempted {count} routes in blueprint '{blueprint.name}'")



class _RegistrationFailureCapture(logging.Handler):
    """Collect WARNING+ records emitted while blueprints register.

    ``init_blueprints`` deliberately swallows import errors so one broken module
    degrades a single feature instead of taking down the whole app — 99 of its
    ~100 ``except`` handlers report via ``app.logger.warning``/``.error``. That
    resilience is intentional, but it also makes breakage invisible: a module can
    silently stop registering and the only symptom is a ``BuildError`` 500 the
    next time a template calls ``url_for()`` on one of its endpoints.

    Capturing the records here turns every one of those handlers into an
    assertable signal without editing a single call site. Read the result from
    ``app.extensions["blueprint_registration_failures"]``; see
    ``tests/test_boot_health.py``.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        try:
            self.records.append(
                {
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        except Exception:  # noqa: BLE001 — a broken record must never break boot
            pass


def init_blueprints(app):
    """Register every blueprint / module on *app*, recording any failures.

    Thin wrapper around :func:`_init_blueprints` that captures the warnings the
    registration helpers emit when a module fails to import, and stashes them on
    ``app.extensions["blueprint_registration_failures"]``.
    """
    capture = _RegistrationFailureCapture()
    original_level = app.logger.level

    # A logger whose effective level is above WARNING would drop the records
    # before any handler sees them. Lower it for the duration, then restore.
    if original_level > logging.WARNING or original_level == logging.NOTSET:
        app.logger.setLevel(logging.WARNING)
    app.logger.addHandler(capture)
    try:
        _init_blueprints(app)
    finally:
        app.logger.removeHandler(capture)
        app.logger.setLevel(original_level)
        app.extensions["blueprint_registration_failures"] = capture.records


def _init_blueprints(app):
    """Register every blueprint / module on *app*."""
    from app.extensions import csrf

    # v2 is the canonical architecture: the shipped templates link to v2 endpoints
    # (e.g. solution_design.*, capability_map.*, admin.* org/SSO/webhook pages).
    # Each module is tiered behind a USE_*_GUARDRAILS flag that historically defaulted
    # OFF, so a fresh install fell back to legacy blueprints and ~66 sidebar/template
    # url_for() calls raised BuildError (500s). Production runs these ON; default them
    # ON here too so a clean clone works. Opt back to legacy with the matching =false.
    for _gf in (
        "USE_ACCOUNT_GUARDRAILS", "USE_ADMIN_GUARDRAILS", "USE_VENDORS_GUARDRAILS",
        "USE_IMPORT_BATCH_GUARDRAILS", "USE_SOLUTIONS_STRATEGIC_GUARDRAILS",
        "USE_ARCHITECTURE_GUARDRAILS", "USE_APPLICATIONS_GUARDRAILS",
        "USE_CAPABILITIES_GUARDRAILS", "USE_AI_CHAT_GUARDRAILS",
        "USE_DUPLICATE_DETECTION_GUARDRAILS", "USE_GOVERNANCE_GUARDRAILS",
        "USE_DASHBOARD_GUARDRAILS",
    ):
        os.environ.setdefault(_gf, "true")

    # --- Core blueprints (always registered) ---
    _register_main(app)
    _register_account(app)
    _register_application_mgmt(app)
    _register_admin(app, csrf)
    _register_vendors(app, csrf)

    # --- Always-on API blueprints ---
    _register_always_on_apis(app, csrf)
    
    # --- North Star Phase 4: Enterprise Export Formats (NORTH-STAR-004) ---
    _register_enterprise_exports(app, csrf)
    
    # --- North Star Phase 2: ArchiMate Layer Navigation (NORTH-STAR-002) ---
    _register_archimate_layer_navigation(app)

    # --- Enterprise Genome (ADR 0010) — deterministic, provenance-carrying
    # emitter slices; each registers non-fatally so one broken slice degrades
    # to a missing page, not a dead app. ---
    try:
        from app.modules.genome.routes.coverage_routes import register as register_genome_coverage
        register_genome_coverage(app)
        app.logger.info("[BLUEPRINT] Genome coverage matrix registered at /genome/coverage")
    except Exception as _gc_exc:
        app.logger.warning("[BLUEPRINT] Genome coverage not available: %s", _gc_exc)
    try:
        from app.modules.codegen.routes.genome_data_routes import genome_data_bp
        app.register_blueprint(genome_data_bp)
        app.logger.info("[BLUEPRINT] Genome data RoPA registered at /genome/data/ropa")
    except Exception as _gd_exc:
        app.logger.warning("[BLUEPRINT] Genome data RoPA not available: %s", _gd_exc)
    try:
        from app.modules.genome.routes.roadmap_routes import register as register_genome_roadmap
        register_genome_roadmap(app)
        app.logger.info("[BLUEPRINT] Genome transformation roadmap registered at /genome/roadmap")
    except Exception as _gr_exc:
        app.logger.warning("[BLUEPRINT] Genome roadmap not available: %s", _gr_exc)
    try:
        from app.modules.genome.routes.drift_routes import register as register_genome_drift
        register_genome_drift(app)
        app.logger.info("[BLUEPRINT] Genome model-health / drift registered at /genome/model-health")
    except Exception as _gh_exc:
        app.logger.warning("[BLUEPRINT] Genome model-health not available: %s", _gh_exc)
    try:
        from app.modules.enterprise_genome.routes.security_genome_routes import enterprise_genome_bp
        app.register_blueprint(enterprise_genome_bp)
        app.logger.info("[BLUEPRINT] Genome security matrix registered at /enterprise-genome/security/matrix")
    except Exception as _gs_exc:
        app.logger.warning("[BLUEPRINT] Genome security matrix not available: %s", _gs_exc)
    try:
        from app.modules.enterprise_genome.routes.ai_systems_routes import ai_systems_genome_bp
        app.register_blueprint(ai_systems_genome_bp)
        app.logger.info("[BLUEPRINT] Genome AI-systems register at /genome/ai-systems")
    except Exception as _gai_exc:
        app.logger.warning("[BLUEPRINT] Genome AI-systems not available: %s", _gai_exc)

    # --- Feature-flagged domain modules ---
    _ff_solutions_strategic = _register_solutions_strategic(app, csrf)
    _ff_architecture = _register_architecture(app, csrf)
    _ff_applications = _register_applications(app)
    _ff_capabilities = _register_capabilities(app, csrf)
    _ff_dashboard = _register_dashboard(app)
    _ff_ai_chat = _register_ai_chat(app, csrf)
    _ff_dedupe = _register_dedupe(app)
    _ff_governance = _register_governance(app, csrf)
    _ff_industry_apqc = _register_industry_apqc(app)
    _register_solution_product(app)
    _register_intelligence(app)

    # --- North Star Persona MVP modules (NS-008, NS-009, NS-010, NS-011, NS-012, NS-013) ---
    _register_persona_modules(app)

    # --- Unconditional misc blueprints ---
    _register_misc_blueprints(app, csrf)

    # --- Health / Metrics removed (ops tooling, not architect-facing) ---

    # --- Late-registered APIs (function-based registration) ---
    _register_late_apis(
        app,
        csrf,
        _ff_solutions_strategic=_ff_solutions_strategic,
        _ff_architecture=_ff_architecture,
        _ff_applications=_ff_applications,
        _ff_capabilities=_ff_capabilities,
        _ff_dashboard=_ff_dashboard,
        _ff_ai_chat=_ff_ai_chat,
        _ff_dedupe=_ff_dedupe,
        _ff_vendors=_is_flag("USE_NEW_VENDORS") or _is_flag("USE_VENDORS_GUARDRAILS"),
        _ff_import_batch=_is_flag("USE_NEW_IMPORT_BATCH")
        or _is_flag("USE_IMPORT_BATCH_GUARDRAILS"),
        _ff_industry_apqc=_ff_industry_apqc,
    )

    # --- Tail blueprints (webhooks, ADR, testing, debug, etc.) ---
    _register_tail_blueprints(
        app,
        csrf,
        _ff_architecture=_ff_architecture,
        _ff_solutions_strategic=_ff_solutions_strategic,
        _ff_import_batch=_is_flag("USE_NEW_IMPORT_BATCH")
        or _is_flag("USE_IMPORT_BATCH_GUARDRAILS"),
    )

    # --- SSO federation (COM-005) ---
    _register_sso(app)

    # --- Standalone feature blueprints the v2 module paths omit ---
    _register_optional_standalone(app)

    # --- Blueprint registry enforcement ---
    _warn_non_canonical_blueprints(app)


def _register_optional_standalone(app):
    """Register standalone feature blueprints the active v2 module paths don't,
    but shipped templates link to (governance dashboard, billing, team). Idempotent:
    skips any blueprint name already registered, so it never double-registers."""
    import importlib

    specs = [
        # (module_path, attr, url_prefix or None to use the blueprint's own)
        ("app.modules.governance.routes.governance_dashboard_routes", "governance_bp", None),
        ("app.modules.portfolio.routes.portfolio_routes", "portfolio_bp", None),
        # Declared since forever, imported by nothing: /admin/security 404'd in
        # every tier. Registered here (not in admin v1/v2) because it is
        # tier-independent, like billing and team below.
        ("app.modules.admin.security_routes", "admin_security_bp", None),
        ("app.modules.admin.billing_routes", "billing_bp", "/admin/billing"),
        ("app.modules.admin.team_routes", "team_bp", "/admin"),
        ("app.modules.business_model_canvas.routes", "business_model_bp", "/business-model"),
        ("app.modules.organization.routes", "organization_bp", "/organization"),
        ("app.modules.business_case.routes", "business_case_bp", "/business-case"),
        # The Business Architecture practice landing page — one front door to
        # the twelve BA outputs, all of which already ship scattered across
        # five generic sidebar zones.
        (
            "app.modules.business_architecture.routes",
            "business_architecture_bp",
            "/business-architecture",
        ),
        # ARCH-124: Tech Radar — adopt/trial/assess/hold over the existing
        # Technology-layer ArchiMateElement catalogue.
        ("app.modules.tech_radar.routes", "tech_radar_bp", "/technology/radar"),
        # SAP S/4HANA Interface Register (Task 02): gives
        # ApplicationInterfaceMetadata its first producer, scoped to a
        # TechnologyRoadmapInitiative.
        (
            "app.modules.interface_register.routes",
            "interface_register_bp",
            "/interface-register",
        ),
        # BA-B1: revocable, read-only share links for capability artefacts. Its
        # public route (/shared/<token>) is the only unauthenticated page here —
        # scope comes from the share row, never from the URL. See the module
        # docstring in app/modules/sharing/routes.py.
        ("app.modules.sharing.routes", "artefact_share_bp", None),
        # ARCH-123 (Data Lineage) is NOT a new blueprint: it extends the
        # existing app.modules.architecture.routes.data_architecture_routes
        # (blueprint "data_architecture", already registered elsewhere) with
        # a real create path and a lineage view — see that file.
    ]
    for module_path, attr, prefix in specs:
        try:
            bp = getattr(importlib.import_module(module_path), attr)
            if bp.name in app.blueprints:
                continue
            if prefix:
                app.register_blueprint(bp, url_prefix=prefix)
            else:
                app.register_blueprint(bp)
            app.logger.info("[BLUEPRINT] standalone %s registered", bp.name)
        except Exception as exc:
            app.logger.warning("[BLUEPRINT] standalone %s skipped (non-fatal): %s", attr, exc)


# ---------------------------------------------------------------------------
# Canonical blueprint registry (see BLUEPRINT_REGISTRY.md)
# ---------------------------------------------------------------------------

CANONICAL_BLUEPRINTS = {
    'unified_applications', 'application_api',
    'unified_vendors', 'unified_vendors_api',
    'capability_map',
    'archimate_crud',
    'solution_design',
    'arb', 'arb_workflow',
    'consolidation_list',
    'unified_ai_chat',
    # 'implementation_planning',  # Not canonical, but NOT dead: it is still
    # registered elsewhere and serves 26 live rules under /implementation/*
    # (verified against app.url_map). The previous note here said "all routes
    # 404", which is false and has already misled one change. Deregistering it
    # is still the intent — until then, treat its routes as live.
    'strategic',
    'admin', 'account',
    'dashboard_pages',
    # North Star Persona MVP (ADR-0009, ADR-0010, ADR-0011)
    'procurement', 'my_applications',
}


def _register_sso(app):
    """Register SSO federation blueprint (COM-005)."""
    try:
        from app.modules.auth.sso_routes import sso_bp

        app.register_blueprint(sso_bp)
        app.logger.info("[SSO] SSO federation blueprint registered (COM-005)")
    except Exception as exc:
        app.logger.warning("[SSO] Could not register SSO blueprint (non-fatal): %s", exc)


def _warn_non_canonical_blueprints(app):
    """Log warnings for non-canonical blueprints at startup."""
    non_canonical = []
    for bp_name, bp_obj in app.blueprints.items():
        if bp_name not in CANONICAL_BLUEPRINTS:
            route_count = len(list(bp_obj.deferred_functions))
            non_canonical.append((bp_name, route_count))

    if non_canonical:
        logger.info(
            "Blueprint registry: %d canonical, %d frozen (non-canonical)",
            len(CANONICAL_BLUEPRINTS),
            len(non_canonical),
        )
        for bp_name, count in sorted(non_canonical, key=lambda x: -x[1]):
            logger.debug(
                "Frozen blueprint: %s (%d deferred registrations) "
                "— do not add new routes",
                bp_name, count,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_flag(name):
    return os.environ.get(name, "false").lower() == "true"


def _safe_register(app, import_fn, label, **register_kwargs):
    """Import and register a blueprint, logging warnings on failure."""
    try:
        bp = import_fn()
        app.register_blueprint(bp, **register_kwargs)
        app.logger.info(f"[BLUEPRINT] {label} registered")
        return True
    except ImportError as e:
        app.logger.warning(f"[BLUEPRINT] {label} not available: {e}")
        return False
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] {label} failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _register_main(app):
    try:
        from app.main import main as main_blueprint

        app.register_blueprint(main_blueprint)
    except Exception as e:
        app.logger.warning("Failed to register main blueprint: %s", e)


def _register_account(app):
    # --- Tier 1: v2 (guardrail-enabled, new architecture) ---
    if _is_flag("USE_ACCOUNT_GUARDRAILS"):
        try:
            from app.modules.account.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info("[MODULE-V2] Account v2 registered (guardrail-enabled)")
            return
        except Exception as e:
            app.logger.error(f"[MODULE-V2] Account v2 failed, falling back to v1: {e}")

    # --- Tier 2: v1 (modular, no guardrails) ---
    _use = _is_flag("USE_NEW_ACCOUNT")
    if _use:
        try:
            from app.modules.account import register as _reg

            _reg(app)
            app.logger.info("[MODULE] Account registered via app.modules.account")
            return
        except Exception as e:
            app.logger.error(
                f"[MODULE] Account module failed, falling back to legacy: {e}"
            )

    # --- Tier 3: legacy routes (with compat wrappers) ---
    from app.account import account as account_blueprint

    try:
        from app.compat.account import wrap_legacy_account_bp

        wrap_legacy_account_bp(account_blueprint)
    except Exception as e:
        app.logger.warning(f"[COMPAT] Account compat wrapper failed (non-fatal): {e}")
    app.register_blueprint(account_blueprint, url_prefix="/account")


def _register_application_mgmt(app):
    from app.application_mgmt import application_mgmt

    app.register_blueprint(application_mgmt)

    _safe_register(
        app,
        lambda: __import__(
            "app.api.application_routes", fromlist=["application_api_bp"]
        ).application_api_bp,
        "Application API at /api/applications",
    )


def _register_admin(app, csrf):
    # --- Tier 1: v2 guardrail-enabled module ---
    if _is_flag("USE_ADMIN_GUARDRAILS"):
        try:
            from app.modules.admin.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info(
                "[MODULE-V2] Admin v2 registered (guardrail-enabled, 3 blueprints)"
            )
            return
        except Exception as _e:
            import traceback
            app.logger.warning(
                f"[MODULE-V2] admin v2 import failed ({_e}), falling back to v1\n"
                + traceback.format_exc()
            )

    # --- Tier 2: v1 modular module ---
    if _is_flag("USE_NEW_ADMIN"):
        try:
            from app.modules.admin import register as _reg

            _reg(app)
            app.logger.info("[MODULE] Admin registered via app.modules.admin")
            return
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] admin import failed ({_e}), falling back to legacy"
            )

    # --- Tier 3: legacy blueprints + compat wrappers ---
    from app.admin import admin as admin_blueprint
    from app.admin import sidebar_mgmt_bp

    if os.environ.get("USE_ADMIN_COMPAT", "true").lower() != "false":
        try:
            from app.compat.admin import (
                wrap_legacy_admin_bp,
                wrap_legacy_sidebar_mgmt_bp,
            )

            wrap_legacy_admin_bp(admin_blueprint)
            wrap_legacy_sidebar_mgmt_bp(sidebar_mgmt_bp)
        except Exception as _e:
            app.logger.warning(
                f"[COMPAT] admin compat wrappers failed ({_e}), registering without wrappers"
            )

    app.register_blueprint(admin_blueprint, url_prefix="/admin")
    app.register_blueprint(sidebar_mgmt_bp)
    app.logger.info(
        "[BLUEPRINT] Admin legacy registered (2 blueprints, /admin + /api/admin/sidebar)"
    )


def _register_vendors(app, csrf):
    # --- Tier 1: v2 (guardrail-enabled) ---
    if _is_flag("USE_VENDORS_GUARDRAILS"):
        try:
            from app.modules.vendors.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info("[BLUEPRINT] Vendors v2 registered (guardrail-enabled)")
            return
        except Exception as e:
            app.logger.warning("Failed to register Vendors v2 blueprint: %s", e)
    # --- Tier 2: New modular architecture ---
    if _is_flag("USE_NEW_VENDORS"):
        try:
            from app.modules.vendors import register as _reg

            _reg(app)
            app.logger.info("[MODULE] Vendors registered via app.modules.vendors")
            return
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] vendors import failed ({_e}), falling back to legacy"
            )
    # BPM-002: Tier 3 legacy fallback removed. USE_VENDORS_GUARDRAILS=True (Tier 1) is the
    # only active path. All 12 vendor blueprint families are registered via vendors v2 module.
    # vendor_analysis_bp and vendor_mdm_bp are covered by v2/unified_vendor_api.
    # options_analysis_bp is NOT: its routes do not exist under v2 and the blueprint is
    # registered nowhere, so those endpoints are unreachable. Wire it up or delete it
    # (deferred as a judgement call; see the W1-1 findings in the diligence register).
    # Neither feature flag is enabled: the v2 (guardrail) vendor module is not mounted.
    # Vendor routes are still served by the always-on unified vendor blueprints, so this
    # is the expected default — not a failure. Set USE_VENDORS_GUARDRAILS=true to opt into v2.
    app.logger.info(
        "[BLUEPRINT] Vendors v2 (guardrail) module not enabled; vendor routes served by "
        "the unified vendor blueprints. Set USE_VENDORS_GUARDRAILS=true to use v2."
    )


# ---------------------------------------------------------------------------
# Always-on API blueprints
# ---------------------------------------------------------------------------


def _register_always_on_apis(app, csrf):
    # Health checks — no auth, must register first so probes always work
    from app.routes.health_routes import health_bp

    app.register_blueprint(health_bp)
    # csrf.exempt: health check blueprint — monitoring probes cannot include CSRF tokens
    csrf.exempt(health_bp)
    app.logger.info("[BLUEPRINT] Health checks registered at /health, /health/db")

    # Error aggregation: client-error sink + platform-admin /admin/errors page
    # (app/_bootstrap/error_tracking.py covers the server-side half).
    from app.modules.monitoring.routes.error_events_routes import error_events_bp

    app.register_blueprint(error_events_bp)
    app.logger.info("[BLUEPRINT] Error aggregation registered at /api/client-error, /admin/errors")

    # Security API
    from app.routes.security_api import security_bp

    app.register_blueprint(security_bp)
    app.logger.info("[BLUEPRINT] Security API registered at /api/security")

    # Enterprise API v2
    from app.routes.enterprise_api import enterprise_api_bp

    app.register_blueprint(enterprise_api_bp)
    app.logger.info("[BLUEPRINT] Enterprise API v2 registered at /api/v2/enterprise")

    # Framework Configuration API
    from app.api.framework_config import framework_config_bp

    app.register_blueprint(framework_config_bp)

    # Framework Configuration UI
    from app.framework_config.routes import framework_config_ui_bp

    app.register_blueprint(framework_config_ui_bp)
    app.logger.info("[BLUEPRINT] Framework Config UI registered at /framework-config")

    # API v1
    from app.api.v1 import api_v1_bp

    app.register_blueprint(api_v1_bp)
    app.logger.info("[BLUEPRINT] API v1 registered at /api/v1")

    # OAuth 2.1 authorization server — the provider side of authlib
    # (the client side is already in app/modules/account/ for SSO).
    # CSRF-exempt: the /oauth/token endpoint is called by OAuth clients
    # with a Bearer token or no session cookie at all.
    from app.modules.oauth_provider import oauth_provider_bp, oauth_metadata_bp

    app.register_blueprint(oauth_provider_bp)
    app.logger.info("[BLUEPRINT] OAuth provider registered at /oauth")
    app.register_blueprint(oauth_metadata_bp)
    app.logger.info("[BLUEPRINT] OAuth metadata registered at /.well-known")
    _csrf_exempt_blueprint(app, oauth_provider_bp)
    _csrf_exempt_blueprint(app, oauth_metadata_bp)

    # MCP Streamable HTTP endpoint — the read-only lens tools
    from app.modules.mcp import mcp_bp

    app.register_blueprint(mcp_bp)
    app.logger.info("[BLUEPRINT] MCP endpoint registered at /mcp")
    _csrf_exempt_blueprint(app, mcp_bp)
    
    # api_v1 blueprint is NOT CSRF-exempt. Audited 2026-08-18 (finding A-04/ARCH-051/C-10):
    # every route under app/api/v1/ authenticates with @login_required (the browser
    # session cookie), not a Bearer token — there is no token-based auth path in this
    # blueprint at all. The previous blanket `csrf.exempt(api_v1_bp)` here let a
    # logged-in browser session be driven by a cross-site form/fetch with no token,
    # e.g. POST /api/v1/applications/ (create) and bulk-delete endpoints. The
    # front-end CSRF safety net (app/static/js/core/03-fetch.js) already injects
    # X-CSRFToken on every mutating fetch, including calls to /api/v1/*, so browser
    # callers are unaffected by requiring the token here.


    # GDPR data-subject API (export / erasure / status).
    # Routes carry their own full "/api/gdpr/..." paths, so no url_prefix here.
    # Deliberately NOT csrf-exempt: the erasure route is a POST driven from the
    # browser session, and the front-end fetch wrapper already sends X-CSRFToken.
    from app.modules.compliance.gdpr_routes import gdpr_bp

    app.register_blueprint(gdpr_bp)
    app.logger.info("[BLUEPRINT] GDPR data-subject API registered at /api/gdpr")

    # Unified Enterprise Architecture
    from app.routes.unified_enterprise_routes import enterprise_bp

    app.register_blueprint(enterprise_bp)

    # Dynamic Dashboards
    try:
        from app.main.dynamic_dashboards import dynamic_dashboards

        app.register_blueprint(dynamic_dashboards)
    except ImportError:
        pass  # module deleted; dashboard routes disabled

    # Unified Low Priority
    from app.routes.unified_low_priority_routes import unified_low_priority_bp

    app.register_blueprint(unified_low_priority_bp)

    # COM-015: intelligent_agents removed (BPM-001 wave-3, zero callers, merged into unified_ai_chat)

    # Code Generation API
    from app.api.code_generation_routes import code_generation_bp

    app.register_blueprint(code_generation_bp)
    app.logger.info("[BLUEPRINT] Code Generation API registered at /code-generation")

    # Code Workbench (ArchiMate → Code generation wizard)
    from app.modules.codegen.routes.codegen_routes import codegen_bp

    # SAP reverse-engineering import route (POST /api/sap/import)
    # MUST be registered on codegen_bp BEFORE app.register_blueprint(codegen_bp).
    # Flask freezes blueprint routes at registration time — adding after is a no-op.
    try:
        from app.modules.codegen.services.sap_importer import register_sap_import_routes
        register_sap_import_routes()
        app.logger.info("[SAP] SAP import route attached to codegen_bp at /api/sap/import")
    except Exception as _sap_exc:
        app.logger.warning("[SAP] Could not register SAP import routes: %s", _sap_exc)

    app.register_blueprint(codegen_bp)
    app.logger.info("[BLUEPRINT] Code Workbench registered at /solutions/<id>/codegen")

    # Issue Tracking API (ent-08)
    from app.routes.issue_tracking import issue_bp

    app.register_blueprint(issue_bp)
    app.logger.info("[BLUEPRINT] Issue Tracking API registered at /api/solutions/<id>/issues")

    # COM-015: predictive_analytics removed (BPM-001 wave-3, zero callers, merged into strategic)

    # Workflow Optimization
    try:
        from app.routes.workflow_optimization_routes import workflow_optimization_bp

        app.register_blueprint(workflow_optimization_bp)
        app.logger.info(
            "[BLUEPRINT] Workflow Optimization API registered at /api/workflow-optimization"
        )
    except ImportError as e:
        app.logger.warning(f"[BLUEPRINT] Workflow Optimization API not available: {e}")

    # BA Phase B API (BA-014)
    try:
        from app.api.ba_phase_b_routes import ba_phase_b_bp

        app.register_blueprint(ba_phase_b_bp)
        app.logger.info("[BLUEPRINT] BA Phase B API registered at /api/ea-workflows/ba")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register BA Phase B routes: {e}")

    # Phase A (Architecture Vision) API (VA-004)
    try:
        from app.modules.ea_workflows.routes.phase_a_routes import phase_a_bp

        app.register_blueprint(phase_a_bp)
        app.logger.info("[BLUEPRINT] Phase A API registered at /api/ea/phase-a")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Phase A routes: {e}")

    # Phase F Migration Planning API (MP-005)
    try:
        from app.modules.ea_workflows.routes.phase_f_routes import phase_f_bp

        app.register_blueprint(phase_f_bp)
        app.logger.info("[BLUEPRINT] Phase F API registered at /api/ea/phase-f")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Phase F routes: {e}")

    # Phase G (Implementation Governance) API (AG-005)
    try:
        from app.modules.ea_workflows.routes.phase_g_routes import phase_g_bp

        app.register_blueprint(phase_g_bp)
        app.logger.info("[BLUEPRINT] Phase G API registered at /api/ea/phase-g")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Phase G routes: {e}")

    # ArchiMate Phase Summary API (AV-007)
    try:
        from app.api.ea_phase_summary_routes import ea_phase_summary_bp

        app.register_blueprint(ea_phase_summary_bp)
        app.logger.info("[BLUEPRINT] ArchiMate Phase Summary API registered at /api/ea/phases")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register EA Phase Summary routes: {e}")

    # Epic Hierarchy API + UI (TPM-004)
    try:
        from app.modules.architecture.routes.epic_routes import epic_bp, epic_ui_bp

        app.register_blueprint(epic_bp)
        app.register_blueprint(epic_ui_bp)
        app.logger.info("[BLUEPRINT] Epic Hierarchy API registered at /api/epics")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Epic Hierarchy routes: {e}")

    # Product Roadmap outcome view (TPM-011)
    try:
        from app.modules.architecture.routes.roadmap_outcome_routes import roadmap_outcome_bp

        app.register_blueprint(roadmap_outcome_bp)
        app.logger.info("[BLUEPRINT] Product Roadmap registered at /product-roadmap")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Product Roadmap routes: {e}")

    # Definition of Done API + UI (TPM-010)
    try:
        from app.modules.architecture.routes.dod_routes import dod_api_bp, dod_ui_bp

        app.register_blueprint(dod_api_bp)
        app.register_blueprint(dod_ui_bp)
        app.logger.info("[BLUEPRINT] DoD API registered at /api/dod")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register DoD routes: {e}")

    # Stakeholder Map — Power/Interest grid canvas
    try:
        from app.modules.architecture.routes.stakeholder_map_routes import (
            stakeholder_map_ui_bp,
            stakeholder_map_api_bp,
        )

        app.register_blueprint(stakeholder_map_ui_bp)
        app.register_blueprint(stakeholder_map_api_bp)
        app.logger.info("[BLUEPRINT] Stakeholder Map registered at /stakeholders/map")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Stakeholder Map routes: {e}")

    # Technical Debt Dashboard — REMOVED (depended on non-existent artifacts/*.json)

    # Confidence Review Dashboard (UI + API for low-confidence mapping review)
    try:
        from app.routes.confidence_review_routes import confidence_review_bp

        app.register_blueprint(confidence_review_bp)
        app.logger.info(
            "[BLUEPRINT] Confidence Review Dashboard registered at /reviews"
        )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register Confidence Review routes: {e}"
        )

    # Confidence Review API (threshold controls, bulk approve/reject)
    try:
        from app.api.confidence_review_routes import confidence_review_bp as confidence_review_api_bp

        app.register_blueprint(confidence_review_api_bp)
        # NOT CSRF-exempt. Audited 2026-08-18 (P-04): every route in
        # app/api/confidence_review_routes.py uses @login_required against the
        # session cookie; there is no Bearer/API-key authentication anywhere in
        # this blueprint despite the "programmatic callers" justification that
        # used to sit here. That is the same falsely-justified pattern removed
        # from api_v1 in 9cda379 — a session-cookie-authenticated endpoint is
        # never a legitimate CSRF opt-out. See app/_bootstrap/csrf_coverage.py.
        app.logger.info(
            "[BLUEPRINT] Confidence Review API registered at /api/confidence"
        )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register Confidence Review API routes: {e}"
        )


# ---------------------------------------------------------------------------
# Feature-flagged domain modules
# ---------------------------------------------------------------------------


def _register_solutions_strategic(app, csrf):
    # --- Tier 1: v2 (guardrail-enabled) ---
    if _is_flag("USE_SOLUTIONS_STRATEGIC_GUARDRAILS"):
        try:
            from app.modules.solutions_strategic.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info(
                "[BLUEPRINT] Solutions/Strategic v2 registered (guardrail-enabled)"
            )
            return True
        except Exception as e:
            # FAIL HARD — when the v2 flag is explicitly set, a silent fallback to
            # v1 is worse than a startup crash. The operator has opted in to v2;
            # any import/registration failure must be surfaced immediately so it is
            # fixed at the source rather than masked by an incomplete v1 fallback.
            import traceback
            app.logger.critical(
                "[BLUEPRINT] FATAL — USE_SOLUTIONS_STRATEGIC_GUARDRAILS=true but v2 failed "
                "to register. App will NOT start. Fix the v2 import error below.\n%s",
                traceback.format_exc(),
            )
            raise RuntimeError(
                "USE_SOLUTIONS_STRATEGIC_GUARDRAILS=true but v2 registration failed: "
                f"{e}\n\nSee startup log for full traceback. "
                "Do NOT fix v1 files as a workaround — fix the v2 import error."
            ) from e
    # --- Tier 2: New modular architecture ---
    if _is_flag("USE_NEW_SOLUTIONS_STRATEGIC"):
        try:
            from app.modules.solutions_strategic import register as _reg

            _reg(app)
            app.logger.info(
                "[MODULE] Solutions/Strategic registered via app.modules.solutions_strategic"
            )
            return True
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] solutions_strategic import failed ({_e}), falling back to legacy"
            )
    # --- Tier 3: Legacy fallback with compat wrappers ---
    if os.environ.get("USE_SOLUTIONS_STRATEGIC_COMPAT", "true").lower() != "false":
        try:
            from app.compat.solutions_strategic import (
                wrap_legacy_solutions_strategic_bp,
            )
        except Exception as _ce:
            app.logger.warning(
                f"[COMPAT] solutions_strategic compat wrapper import failed ({_ce})"
            )
            wrap_legacy_solutions_strategic_bp = None
    else:
        wrap_legacy_solutions_strategic_bp = None

    from app.modules.solutions_strategic.v2.routes.roadmap_api import roadmap_bp

    if wrap_legacy_solutions_strategic_bp:
        wrap_legacy_solutions_strategic_bp(roadmap_bp)
    app.register_blueprint(roadmap_bp)

    from app.modules.solutions_strategic.v2.routes.strategic_routes import strategic_bp

    if wrap_legacy_solutions_strategic_bp:
        wrap_legacy_solutions_strategic_bp(strategic_bp)
    app.register_blueprint(strategic_bp)

    try:
        from app.modules.solutions_strategic.v2.routes.strategic_risks_hardened import (
            strategic_risks_bp,
        )

        if wrap_legacy_solutions_strategic_bp:
            wrap_legacy_solutions_strategic_bp(strategic_risks_bp)
        app.register_blueprint(strategic_risks_bp)
        app.logger.info(
            "[BLUEPRINT] Strategic Risks (hardened) registered at /strategic/api"
        )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register Strategic Risks hardened: {e}"
        )

    app.logger.info(
        "[BLUEPRINT] Solutions/Strategic legacy registered (with compat wrappers)"
    )
    return False


def _register_architecture(app, csrf):
    # SA-008 completeness routes are tier-independent: they have no v2 equivalent, so
    # registering them only in the Tier-3 fallback below left both the page
    # (/solutions/<id>/completeness) and the API it fetches
    # (/api/solutions/<id>/completeness) unrouted whenever USE_ARCHITECTURE_GUARDRAILS
    # is on — which is the default. Register before the tier branches, all of which
    # can return early.
    try:
        from app.modules.architecture.routes.completeness_routes import completeness_bp

        app.register_blueprint(completeness_bp)
        app.logger.info("[BLUEPRINT] SA-008 completeness routes registered")
    except ImportError as e:
        app.logger.warning(f"Completeness blueprint not available: {e}")

    # --- Tier 1: v2 (guardrail-enabled) ---
    if _is_flag("USE_ARCHITECTURE_GUARDRAILS"):
        try:
            from app.modules.architecture.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info(
                "[BLUEPRINT] Architecture v2 registered (guardrail-enabled)"
            )
            # Data Architecture blueprint — register alongside v2 (all tiers)
            try:
                from app.modules.architecture.routes.data_architecture_routes import data_architecture_bp
                app.register_blueprint(data_architecture_bp)
                app.logger.info("[BLUEPRINT] Data Architecture registered at /architecture/data-*")
            except ImportError as _da_e:
                app.logger.warning(f"Data Architecture blueprint not available: {_da_e}")
            return True
        except Exception as e:
            app.logger.warning("Failed to register Architecture v2 blueprint: %s", e)
    # --- Tier 2: New modular architecture ---
    if _is_flag("USE_NEW_ARCHITECTURE"):
        try:
            from app.modules.architecture import register as _reg

            _reg(app)
            app.logger.info(
                "[MODULE] Architecture registered via app.modules.architecture"
            )
            return True
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] architecture import failed ({_e}), falling back to legacy"
            )
    # --- Tier 3: Legacy fallback with compat wrappers ---
    if os.environ.get("USE_ARCHITECTURE_COMPAT", "true").lower() != "false":
        try:
            from app.compat.architecture import wrap_legacy_architecture_bp
        except Exception as _ce:
            app.logger.warning(
                f"[COMPAT] architecture compat wrapper import failed ({_ce})"
            )
            wrap_legacy_architecture_bp = None
    else:
        wrap_legacy_architecture_bp = None

    try:
        from app.modules.architecture.api.archimate_routes import archimate_api

        if wrap_legacy_architecture_bp:
            wrap_legacy_architecture_bp(archimate_api)
        app.register_blueprint(archimate_api)
        app.logger.info("[BLUEPRINT] ArchiMate API registered at /api/archimate")
    except ImportError as e:
        app.logger.warning(f"ArchiMate API blueprint not available: {e}")

    try:
        from app.modules.architecture.routes.archimate_routes import archimate_bp

        app.register_blueprint(archimate_bp)
        app.logger.info("[BLUEPRINT] ArchiMate routes registered at /archimate")
    except ImportError as e:
        app.logger.warning(f"ArchiMate routes blueprint not available: {e}")

    try:
        from app.archimate_crud import archimate_crud

        if wrap_legacy_architecture_bp:
            wrap_legacy_architecture_bp(archimate_crud)
        app.register_blueprint(archimate_crud)
        app.logger.info(
            "[BLUEPRINT] ArchiMate CRUD Dashboard registered at /architecture"
        )
    except ImportError as e:
        app.logger.warning(f"ArchiMate CRUD dashboard blueprint not available: {e}")

    try:
        from app.modules.architecture.api.viewpoint_routes import viewpoint_bp

        if wrap_legacy_architecture_bp:
            wrap_legacy_architecture_bp(viewpoint_bp)
        app.register_blueprint(viewpoint_bp)
        app.logger.info("[BLUEPRINT] Viewpoint API registered at /api/viewpoints")
    except ImportError as e:
        app.logger.warning(f"Viewpoint API blueprint not available: {e}")

    from app.modules.architecture.routes.architecture_routes import architecture_bp

    if wrap_legacy_architecture_bp:
        wrap_legacy_architecture_bp(architecture_bp)
    app.register_blueprint(architecture_bp)

    from app.modules.architecture.routes.architecture_crud_routes import (
        architecture_crud_bp,
    )

    if wrap_legacy_architecture_bp:
        wrap_legacy_architecture_bp(architecture_crud_bp)
    app.register_blueprint(architecture_crud_bp)
    app.logger.info("[BLUEPRINT] Architecture legacy registered (with compat wrappers)")

    try:
        from app.modules.architecture.routes.data_architecture_routes import data_architecture_bp

        if wrap_legacy_architecture_bp:
            wrap_legacy_architecture_bp(data_architecture_bp)
        app.register_blueprint(data_architecture_bp)
        app.logger.info("[BLUEPRINT] Data Architecture registered at /architecture/data-architecture")
    except ImportError as e:
        app.logger.warning(f"Data Architecture blueprint not available: {e}")

    return False


def _register_applications(app):
    # --- Tier 1: v2 (guardrail-enabled) ---
    v2_registered = False
    if _is_flag("USE_APPLICATIONS_GUARDRAILS"):
        try:
            from app.modules.applications.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info(
                "[BLUEPRINT] Applications v2 registered (guardrail-enabled)"
            )
            v2_registered = True
        except Exception as e:
            app.logger.warning("Failed to register Applications v2 blueprint: %s", e)

    # --- Tier 2: New modular architecture ---
    if not v2_registered and _is_flag("USE_NEW_APPLICATIONS"):
        try:
            from app.modules.applications import register as _reg

            _reg(app)
            app.logger.info(
                "[MODULE] Applications registered via app.modules.applications"
            )
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] applications import failed ({_e}), falling back to legacy"
            )
            v2_registered = False

    # --- Tier 3: Legacy fallback with compat wrappers (only if v2 not registered) ---
    if not v2_registered:
        from app.modules.applications.routes import unified_applications_bp

        if os.environ.get("USE_APPLICATIONS_COMPAT", "true").lower() != "false":
            try:
                from app.compat.applications import wrap_legacy_applications_bp

                wrap_legacy_applications_bp(unified_applications_bp)
            except Exception as _ce:
                app.logger.warning(
                    f"[COMPAT] applications compat wrapper failed ({_ce}), registering raw"
                )
        app.register_blueprint(unified_applications_bp)
        app.logger.info(
            "[BLUEPRINT] Applications legacy registered (with compat wrappers)"
        )

    # Application Capability Tagging (always registered, regardless of tier)
    try:
        from app.modules.applications.routes.capability_tagging_routes import (
            application_tags_bp,
        )

        app.register_blueprint(application_tags_bp)
        app.logger.info(
            "[BLUEPRINT] Application Capability Tagging API registered at /dashboard/api/applications"
        )
    except Exception as _e:
        app.logger.warning(
            f"[BLUEPRINT] Application Capability Tagging failed to register: {_e}"
        )

    return v2_registered


def _register_capabilities(app, csrf):
    # --- Tier 1: v2 (guardrail-enabled) ---
    if _is_flag("USE_CAPABILITIES_GUARDRAILS"):
        try:
            from app.modules.capabilities.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info(
                "[BLUEPRINT] Capabilities v2 registered (guardrail-enabled)"
            )
            return True
        except Exception as e:
            app.logger.warning("Failed to register Capabilities v2 blueprint: %s", e)
    # --- Tier 2: New modular architecture ---
    if _is_flag("USE_NEW_CAPABILITIES"):
        try:
            from app.modules.capabilities import register as _reg

            _reg(app)
            app.logger.info(
                "[MODULE] Capabilities registered via app.modules.capabilities"
            )
            return True
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] capabilities import failed ({_e}), falling back to legacy"
            )
    # --- Tier 3: Legacy fallback with compat wrappers ---
    from app.main.capability_framework_routes import capability_framework_bp

    if os.environ.get("USE_CAPABILITIES_COMPAT", "true").lower() != "false":
        try:
            from app.compat.capabilities import wrap_legacy_capabilities_bp

            wrap_legacy_capabilities_bp(capability_framework_bp)
        except Exception as _ce:
            app.logger.warning(
                f"[COMPAT] capabilities compat wrapper failed ({_ce}), registering raw"
            )
    app.register_blueprint(capability_framework_bp)
    app.logger.info(
        "[BLUEPRINT] Capability Framework registered (with compat wrappers)"
    )

    from app.main.capability_maturity_routes import maturity_management

    app.register_blueprint(maturity_management)

    try:
        from app.modules.capabilities.routes.abacus_consolidation import (
            bp as abacus_consolidation_bp,
        )

        app.register_blueprint(abacus_consolidation_bp)
        app.logger.info(
            "[BLUEPRINT] Abacus Consolidation registered at /admin/abacus/consolidation"
        )
    except ImportError as e:
        app.logger.warning(f"Abacus Consolidation blueprint not available: {e}")

    app.logger.info("[BLUEPRINT] Capabilities legacy registered")
    return False


def _register_dashboard(app):
    # --- Tier 1: v2 (guardrail-enabled, new architecture) ---
    # USE_DASHBOARD_GUARDRAILS defaults ON via init_blueprints (v2 is canonical:
    # only it registers the health_scorecard route admin_sidebar.html links to).
    if _is_flag("USE_DASHBOARD_GUARDRAILS"):
        try:
            from app.modules.dashboard.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info("[BLUEPRINT] Dashboard v2 registered (guardrail-enabled)")
            return True
        except Exception as e:
            app.logger.warning("Failed to register Dashboard v2 blueprint: %s", e)
            # Fallback to next tier
    # --- Tier 2: New modular architecture ---
    if _is_flag("USE_NEW_DASHBOARD"):
        try:
            from app.modules.dashboard import register as _reg

            _reg(app)
            app.logger.info("[MODULE] Dashboard registered via app.modules.dashboard")
            return True
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] dashboard import failed ({_e}), falling back to legacy"
            )

    # --- Tier 3: Legacy fallback with compatibility wrappers ---
    from app.dashboard.views import dashboard as dashboard_blueprint

    if os.environ.get("USE_DASHBOARD_COMPAT", "true").lower() != "false":
        try:
            from app.compat.dashboard import wrap_legacy_dashboard_bp

            wrap_legacy_dashboard_bp(dashboard_blueprint)
        except Exception as _ce:
            app.logger.warning(
                f"[COMPAT] dashboard compat wrapper failed ({_ce}), registering raw"
            )
    app.register_blueprint(dashboard_blueprint, url_prefix="/dashboard")
    return False


def _register_ai_chat(app, csrf):
    # --- Tier 1: v2 (guardrail-enabled) ---
    if _is_flag("USE_AI_CHAT_GUARDRAILS"):
        try:
            from app.modules.ai_chat.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info("[BLUEPRINT] AI Chat v2 registered (guardrail-enabled)")
            return True
        except Exception as e:
            app.logger.warning("Failed to register AI Chat v2 blueprint: %s", e)
    # --- Tier 2: New modular architecture ---
    if _is_flag("USE_NEW_AI_CHAT"):
        try:
            from app.modules.ai_chat import register as _reg

            _reg(app)
            app.logger.info("[MODULE] AI Chat registered via app.modules.ai_chat")
            return True
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] ai_chat import failed ({_e}), falling back to legacy"
            )

    # --- Tier 3: Legacy fallback with compat wrappers ---
    from app.modules.ai_chat.routes import unified_ai_chat_bp
    # COM-015: ai_data_interaction removed (BPM-001 wave-3, zero callers, merged into unified_ai_chat)
    from app.modules.ai_chat.routes.ai_assistance_routes import ai_assistance_bp

    if os.environ.get("USE_AI_CHAT_COMPAT", "true").lower() != "false":
        try:
            from app.compat.ai_chat import wrap_legacy_ai_chat_bp

            for _bp in (unified_ai_chat_bp, ai_assistance_bp):
                wrap_legacy_ai_chat_bp(_bp)
        except Exception as _ce:
            app.logger.warning(
                f"[COMPAT] ai_chat compat wrapper failed ({_ce}), registering raw"
            )

    app.register_blueprint(unified_ai_chat_bp)
    app.register_blueprint(ai_assistance_bp)
    return False


def _register_dedupe(app):
    # --- Tier 1: v2 (guardrail-enabled) ---
    if _is_flag("USE_DUPLICATE_DETECTION_GUARDRAILS"):
        try:
            from app.modules.duplicate_detection.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info(
                "[BLUEPRINT] Duplicate Detection v2 registered (guardrail-enabled)"
            )
            return True
        except Exception as e:
            app.logger.warning(
                "Failed to register Duplicate Detection v2 blueprint: %s", e
            )
    # --- Tier 2: New modular architecture ---
    if _is_flag("USE_NEW_DEDUPE"):
        try:
            from app.modules.duplicate_detection import register as _reg

            _reg(app)
            app.logger.info(
                "[MODULE] Duplicate detection registered via app.modules.duplicate_detection"
            )
            return True
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] duplicate_detection import failed ({_e}), falling back to legacy"
            )

    # --- Tier 3: Legacy fallback with compat wrappers ---
    from app.modules.duplicate_detection.routes import unified_duplicate_bp

    if os.environ.get("USE_DEDUPE_COMPAT", "true").lower() != "false":
        try:
            from app.compat.duplicate_detection import wrap_legacy_dedupe_bp

            wrap_legacy_dedupe_bp(unified_duplicate_bp)
        except Exception as _ce:
            app.logger.warning(
                f"[COMPAT] dedupe compat wrapper failed ({_ce}), registering raw"
            )
    app.register_blueprint(unified_duplicate_bp)
    return False


def _register_governance(app, csrf):
    # --- Tier 1: v2 (guardrail-enabled) ---
    if _is_flag("USE_GOVERNANCE_GUARDRAILS"):
        try:
            from app.modules.governance.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info("[BLUEPRINT] Governance v2 registered (guardrail-enabled)")
            return True
        except Exception as e:
            app.logger.warning("Failed to register Governance v2 blueprint: %s", e)
    # --- Tier 2: New modular architecture ---
    if _is_flag("USE_NEW_GOVERNANCE"):
        try:
            from app.modules.governance import register as _reg

            _reg(app)
            app.logger.info("[MODULE] Governance registered via app.modules.governance")
            return True
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] governance import failed ({_e}), falling back to legacy"
            )

    # --- Tier 3: Legacy fallback with compat wrappers ---
    from app.modules.governance.routes.consolidation_list_routes import (
        consolidation_list_bp,
    )
    # capability_management removed — Naming Dashboard was empty shell
    from app.modules.governance.routes.capability_governance_routes import (
        capability_governance,
    )

    if os.environ.get("USE_GOVERNANCE_COMPAT", "true").lower() != "false":
        try:
            from app.compat.governance import wrap_legacy_governance_bp

            for _bp in (
                consolidation_list_bp,
                capability_governance,
            ):
                wrap_legacy_governance_bp(_bp)
        except Exception as _ce:
            app.logger.warning(
                f"[COMPAT] governance compat wrapper failed ({_ce}), registering raw"
            )

    app.register_blueprint(consolidation_list_bp)
    app.register_blueprint(capability_governance, url_prefix="/capability-governance")
    return False


def _register_industry_apqc(app):
    """Register Industry APQC module."""
    # Always use the new modular architecture
    try:
        from app.modules.industry_apqc import register as _reg

        _reg(app)
        app.logger.info(
            "[MODULE] Industry APQC registered via app.modules.industry_apqc"
        )
        return True
    except Exception as _e:
        app.logger.error(f"[MODULE] Industry APQC import failed: {_e}")
        return False


def _register_intelligence(app):
    """Register the intelligence module (T-001 skeleton — mounts nothing yet).

    ``app.modules.intelligence.register`` currently registers no blueprint and
    no event hook (T-003/T-004 add those). This call exists so later tasks
    have exactly one non-fatal registration point to extend, matching every
    other module here.
    """
    try:
        from app.modules.intelligence import register as _reg

        _reg(app)
        app.logger.info(
            "[MODULE] Intelligence module registered (app.modules.intelligence)"
        )
    except Exception as e:
        app.logger.warning("Failed to register intelligence module: %s", e)


def _register_solution_product(app):
    """Register Solution Product generation module (GA — flag removed COM-023)."""
    try:
        from app.modules.solutions_product.routes.product_routes import (
            solution_product_bp,
        )

        app.register_blueprint(solution_product_bp)
        app.logger.info(
            "[BLUEPRINT] Solution Product API registered at /api/solutions/*/product"
        )
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Solution Product registration failed: {e}")


# ---------------------------------------------------------------------------
# North Star Persona MVP modules (NS-008 through NS-013)
# ---------------------------------------------------------------------------


def _register_persona_modules(app):
    """North Star persona modules — RE-ENABLED 2026-07-31.

    Disabled on 2026-06-11 on the grounds that both modules had "ZERO templates
    in app/templates/" and returned 500 for any user holding the matching role.
    Disabling was the right call at the time; a 500 is worse than an absent page.

    The premise was incomplete, though. Both blueprints declare
    template_folder="templates" and ship 12 substantive templates (1,919 lines)
    at app/modules/{procurement,my_applications}/templates/, all extending
    layouts/admin_base.html. Flask resolves those through the blueprint's own
    loader, not app/templates/ — so looking only in app/templates/ found nothing
    while the templates were present all along.

    Every dependency was verified before re-enabling: the ApplicationOwner,
    VendorContract and LicenseEntitlement models exist, their tables
    (application_owners, vendor_contracts, license_entitlements) exist in the
    production database, and requires_procurement / requires_application_owner
    are exported from app.decorators.

    The cost of leaving them off was not neutral. ENTERPRISE_ROLE_SECTION_MAP
    grants the procurement archetype 3 sections and application_manager 5 — and
    the missing module is the DEFINING section of each. Two of nine archetypes
    were promised a product that returned 404.

    Registration is guarded: a failure here must not take down the rest of the
    application, and is loud rather than silent.

    tests/journeys/ asserts these pages actually render, so this cannot regress
    to a 500 unnoticed.
    """
    try:
        from app.modules.my_applications import my_applications_bp
        from app.modules.my_applications import routes as _my_app_routes  # noqa: F401

        app.register_blueprint(my_applications_bp)
        logger.info("[Blueprint] my_applications registered (/my-applications)")
    except Exception as e:  # noqa: BLE001
        logger.error("[Blueprint] my_applications FAILED to register: %s", e)

    try:
        from app.modules.procurement import procurement_bp
        from app.modules.procurement import routes as _proc_routes  # noqa: F401

        app.register_blueprint(procurement_bp)
        logger.info("[Blueprint] procurement registered (/procurement)")
    except Exception as e:  # noqa: BLE001
        logger.error("[Blueprint] procurement FAILED to register: %s", e)

    # All-modules directory (shell-overhaul Wave 1, Task 3 fix round). Not
    # role-gated — every authenticated user can see the full surface list,
    # same as the sidebar's own view_functions guard already lets a curious
    # user reach anything by URL; this just makes that discoverable.
    try:
        from app.modules.modules_directory import modules_directory_bp
        from app.modules.modules_directory import routes as _modules_dir_routes  # noqa: F401

        app.register_blueprint(modules_directory_bp)
        logger.info("[Blueprint] modules_directory registered (/modules)")
    except Exception as e:  # noqa: BLE001
        logger.error("[Blueprint] modules_directory FAILED to register: %s", e)


# ---------------------------------------------------------------------------
# Misc unconditional
# ---------------------------------------------------------------------------


def _register_misc_blueprints(app, csrf):
    _use_vendors = _is_flag("USE_NEW_VENDORS") or _is_flag("USE_VENDORS_GUARDRAILS")
    _use_applications = _is_flag("USE_NEW_APPLICATIONS") or _is_flag(
        "USE_APPLICATIONS_GUARDRAILS"
    )
    _use_solutions = _is_flag("USE_NEW_SOLUTIONS_STRATEGIC") or _is_flag(
        "USE_SOLUTIONS_STRATEGIC_GUARDRAILS"
    )

    if not _use_vendors:
        # Vendor Management
        from app.modules.vendors.routes.vendor_management_routes import (
            vendor_management_bp,
        )

        app.register_blueprint(vendor_management_bp)
        app.logger.info(
            "[BLUEPRINT] Vendor Management registered at /vendor-management"
        )

        # Unified Vendors
        try:
            from app.unified_vendors import unified_vendors_bp, unified_vendors_api_bp

            app.register_blueprint(unified_vendors_bp)
            app.register_blueprint(unified_vendors_api_bp)
            app.logger.info(
                "[BLUEPRINT] Unified Vendors registered at /vendors and /api/vendors"
            )
            app.logger.info(
                "[VENDOR CONSOLIDATION] 7 vendor modules consolidated into 1 unified platform"
            )
        except ImportError as e:
            app.logger.warning(f"[BLUEPRINT] Unified Vendors not available: {e}")

        # COM-015: vendor_comparison removed (BPM-001 wave-2, zero callers, merged into unified_vendors_api)

    # The vendors page (vendors/list.html) and its JS depend on the vendor JSON API
    # (/api/vendors/list, /api/vendors/ranking) and on vendor_management.create_vendor.
    # When USE_VENDORS_GUARDRAILS is on, the v2 vendor blueprints can fail to register
    # with no fallback, so the vendor table 404s on load ("NOT FOUND" toasts) and the
    # Add Vendor button is dead. Register the canonical vendor API + management routes
    # unconditionally, guarded against double-registration so v1 and v2 both work.
    if "unified_vendors_api" not in app.blueprints:
        try:
            from app.modules.vendors.routes.unified_vendor_api import (
                unified_vendors_api_bp,
            )

            app.register_blueprint(unified_vendors_api_bp)
            app.logger.info(
                "[BLUEPRINT] Vendor JSON API registered at /api/vendors (always-on)"
            )
        except Exception as e:  # pragma: no cover - defensive
            app.logger.warning(f"[BLUEPRINT] Vendor JSON API registration failed: {e}")

    if "vendor_management" not in app.blueprints:
        try:
            from app.modules.vendors.routes.vendor_management_routes import (
                vendor_management_bp,
            )

            app.register_blueprint(vendor_management_bp)
            app.logger.info(
                "[BLUEPRINT] Vendor Management registered (always-on)"
            )
        except Exception as e:  # pragma: no cover - defensive
            app.logger.warning(
                f"[BLUEPRINT] Vendor Management registration failed: {e}"
            )

    if not _use_applications:
        # Application Merging
        from app.api.application_merging_routes import merging_bp

        app.register_blueprint(merging_bp)

        # Implementation Planning — REMOVED (PLT-099 audit)
        # Module deprecated: functionality migrated to UnifiedWorkPackage + ADM Kanban.
        # All 31 routes were behind a feature flag that defaults to OFF (abort(404)).

    # NOTE: solution_design_bp registration moved to _register_tail_blueprints()
    # to use _ff_solutions_strategic (actual success flag) instead of _use_solutions
    # (flag-is-set check that ignores v2 import failures).


# ---------------------------------------------------------------------------
# Monitoring — health_bp is registered unconditionally by _register_always_on_apis().
# This section is intentionally empty; monitoring module blueprints are ops tooling
# not needed in the architect-facing app. _register_monitoring() has been removed
# as it was dead code (never called from init_blueprints).


# ---------------------------------------------------------------------------
# Late-registered APIs (function-based)
# ---------------------------------------------------------------------------


def _register_late_apis(app, csrf, **flags):
    _ff_vendors = flags.get("_ff_vendors", False)
    _ff_architecture = flags.get("_ff_architecture", False)
    _ff_ai_chat = flags.get("_ff_ai_chat", False)
    _ff_dedupe = flags.get("_ff_dedupe", False)
    _ff_capabilities = flags.get("_ff_capabilities", False)
    _ff_dashboard = flags.get("_ff_dashboard", False)
    _ff_applications = flags.get("_ff_applications", False)
    _ff_solutions_strategic = flags.get("_ff_solutions_strategic", False)
    _ff_import_batch = flags.get("_ff_import_batch", False)
    _ff_industry_apqc = flags.get("_ff_industry_apqc", False)

    if not _ff_ai_chat:
        try:
            from app.modules.ai_chat.routes.ai_gap_detection_routes import (
                ai_gap_detection_bp,
            )

            app.register_blueprint(ai_gap_detection_bp)
            app.logger.info(
                "[BLUEPRINT] AI Gap Detection API registered at /api/ai-gap-detection"
            )
        except ImportError as e:
            app.logger.warning(f"[BLUEPRINT] AI Gap Detection API not available: {e}")

    # COM-015: legacy_redirects removed (BPM-001 wave-1, zero callers, REMOVE)
    # COM-015: legacy_vendor_redirects removed (BPM-001 wave-1, zero callers, REMOVE)

    # Industry APQC API (skip if new module is active)
    # The 'app.api.industry_api' module was migrated to 'app.modules.industry_apqc.routes.apqc_api_routes'
    # and registered via _register_industry_apqc() above. This legacy fallback is removed.

    # Usage Analytics Dashboard
    try:
        from app.routes.usage_analytics_routes import usage_analytics_bp

        app.register_blueprint(usage_analytics_bp)
        app.logger.info(
            "[BLUEPRINT] Usage Analytics Dashboard registered at /usage-analytics"
        )
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Usage Analytics Dashboard not available: {e}")

    # AI Suggestions API
    try:
        from app.api.suggestions_routes import suggestions_bp

        app.register_blueprint(suggestions_bp)
        app.logger.info("[BLUEPRINT] AI Suggestions API registered at /api/suggestions")
    except ImportError as e:
        app.logger.warning(f"[BLUEPRINT] AI Suggestions API not available: {e}")

    # Consolidation Planning API
    try:
        from app.api.consolidation_routes import consolidation_bp

        app.register_blueprint(consolidation_bp)
        app.logger.info(
            "[BLUEPRINT] Consolidation Planning API registered at /api/consolidation"
        )
    except ImportError as e:
        app.logger.warning(f"[BLUEPRINT] Consolidation Planning API not available: {e}")

    # APQC Hierarchy API
    try:
        from app.api.apqc_hierarchy_routes import register_apqc_hierarchy_routes

        register_apqc_hierarchy_routes(app)
        app.logger.info("[API] APQC hierarchy routes registered")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register APQC hierarchy routes: {e}")

    # Vendor Product API
    if not _ff_vendors:
        try:
            from app.modules.vendors.api.vendor_product_routes import (
                register_vendor_product_routes,
            )

            register_vendor_product_routes(app)
            app.logger.info("[API] Vendor product routes registered")
        except Exception as e:
            app.logger.warning(f"[API] Failed to register vendor product routes: {e}")

    # Capability Taxonomy API
    if not _ff_capabilities:
        try:
            from app.modules.capabilities.api.capability_taxonomy_routes import (
                register_capability_taxonomy_routes,
            )

            register_capability_taxonomy_routes(app)
            app.logger.info("[API] Capability taxonomy routes registered")
        except Exception as e:
            app.logger.warning(
                f"[API] Failed to register capability taxonomy routes: {e}"
            )

    # CA-001: Capability ArchiMate coverage API
    try:
        from app.api.capability_archimate_routes import capability_archimate_bp
        app.register_blueprint(capability_archimate_bp)
        app.logger.info("[API] Capability ArchiMate coverage routes registered")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register capability archimate routes: {e}")

    # CG-006: ArchiMate Generation API — GET /api/archimate/elements (used by blueprint picker)
    try:
        from app.api.archimate_generation_routes import archimate_generation_bp
        app.register_blueprint(archimate_generation_bp)
        app.logger.info("[API] ArchiMate Generation API registered at /api/archimate")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register ArchiMate Generation API: {e}")

    # Import batch (feature-flagged, 3-tier)
    _ff_import_batch_v2 = _is_flag("USE_IMPORT_BATCH_GUARDRAILS")
    if _ff_import_batch_v2:
        try:
            from app.modules.import_batch.v2 import register as _reg_v2

            _reg_v2(app)
            app.logger.info(
                "[BLUEPRINT] Import batch v2 registered (guardrail-enabled)"
            )
            _ff_import_batch = True  # skip legacy below
        except Exception as e:
            app.logger.warning("Failed to register Import Batch v2: %s", e)
            _ff_import_batch_v2 = False

    if not _ff_import_batch_v2 and _ff_import_batch:
        try:
            from app.modules.import_batch import register as _reg

            _reg(app)
            app.logger.info(
                "[MODULE] Import batch registered via app.modules.import_batch"
            )
        except Exception as _e:
            app.logger.warning(
                f"[MODULE] import_batch import failed ({_e}), falling back to legacy"
            )
            _ff_import_batch = False

    if not _ff_import_batch and not _ff_import_batch_v2:
        try:
            from app.modules.import_batch.routes.batch_processing_routes import (
                register_batch_processing_routes,
            )

            if os.environ.get("USE_IMPORT_BATCH_COMPAT", "true").lower() != "false":
                try:

                    # Note: register_batch_processing_routes is a function, not a blueprint
                    # Compat wrapper would need to be applied differently if needed
                    app.logger.info(
                        "[COMPAT] Import batch compat available (function-based registration)"
                    )
                except Exception as _ce:
                    app.logger.warning(
                        f"[COMPAT] import_batch compat wrapper import failed ({_ce})"
                    )
            register_batch_processing_routes(app)
            app.logger.info("[API] Batch processing routes registered")
        except Exception as e:
            app.logger.warning(f"[API] Failed to register batch processing routes: {e}")

    # NOTE: review_queue_api_routes.py was an incomplete stub (6 routes, no
    # error handling). Canonical implementation: app/api/review_queue_routes.py
    # (8 routes, pagination, validation, audit logging). Stub deleted.

    # Dashboard routes
    if not _ff_dashboard:
        try:
            from app.api.dashboard_routes import register_dashboard_routes

            register_dashboard_routes(app)
            app.logger.info("[API] Dashboard routes registered")
        except Exception as e:
            app.logger.warning(f"[API] Failed to register dashboard routes: {e}")

    # Review Queue API
    try:
        from app.api.review_queue_routes import register_review_queue_routes

        register_review_queue_routes(app)
        app.logger.info("[API] Review queue routes registered")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register review queue routes: {e}")

    # Import History API
    try:
        from app.api.import_history_routes import register_import_history_routes

        register_import_history_routes(app)
        app.logger.info("[API] Import history routes registered")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register import history routes: {e}")

    # NOTE: vendor_catalog_routes registration removed — shadowed by vendors_api.root
    # which is registered earlier in _register_vendors(). The vendor_catalog_routes
    # endpoint at /api/vendors was unreachable dead code.

    # Vendor Discovery API
    if not _ff_vendors:
        try:
            from app.modules.vendors.api.vendor_discovery_routes import (
                register_vendor_discovery_routes,
            )

            register_vendor_discovery_routes(app)
            app.logger.info("[API] Vendor discovery routes registered")
        except Exception as e:
            app.logger.warning(f"[API] Failed to register vendor discovery routes: {e}")

    # COM-015: semantic_discovery removed (BPM-001 wave-2, zero callers, merged into unified_vendors_api)

    # Vendor consolidation notice
    app.logger.info(
        "[VENDOR CONSOLIDATION] vendor_population module removed - consolidating into unified vendor module"
    )

    # Advanced TCO API
    try:
        from app.api.advanced_tco_routes import advanced_tco_bp

        app.register_blueprint(advanced_tco_bp)
        app.logger.info("Advanced TCO API routes registered")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register advanced TCO routes: {e}")

        # COM-015: ai_vendor_discovery removed (BPM-001 wave-2, zero callers, merged into unified_vendors_api)

    # Coverage Matrix
    try:
        from app.api.coverage_matrix_routes import coverage_matrix_bp

        app.register_blueprint(coverage_matrix_bp)
        app.logger.info("Coverage matrix API routes registered")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register coverage matrix routes: {e}")

    # API Pipeline
    try:
        from app.api.api_pipeline_routes import api_pipeline_bp

        app.register_blueprint(api_pipeline_bp)
        app.logger.info("[API] API Pipeline routes registered at /api/pipeline")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register API pipeline routes: {e}")

        # COM-015: advanced_vendor removed (BPM-001 wave-2, zero callers, merged into unified_vendors_api)

    # ACM routes
    if not _ff_capabilities:
        try:
            from app.modules.capabilities.api.acm_routes import acm_bp

            app.register_blueprint(acm_bp)
            app.logger.info("[API] ACM Technical Capability API registered at /api/acm")
        except Exception as e:
            app.logger.warning(f"[API] Failed to register ACM routes: {e}")

    # SA Phase C API (SA-014)
    try:
        from app.api.sa_phase_c_routes import sa_phase_c_bp

        app.register_blueprint(sa_phase_c_bp)
        app.logger.info("[API] SA Phase C API registered at /api/ea-workflows/sa")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register SA Phase C routes: {e}")

    # Phase D Technology Architecture API (TD-005)
    try:
        from app.modules.ea_workflows.routes.phase_d_routes import phase_d_bp

        app.register_blueprint(phase_d_bp)
        app.logger.info("[API] Phase D Technology API registered at /api/ea/phase-d")
    except Exception as e:
        app.logger.warning(f"[API] Failed to register Phase D routes: {e}")


# ---------------------------------------------------------------------------
# Tail blueprints (architect UI, debug, webhooks, etc.)
# ---------------------------------------------------------------------------


def _register_tail_blueprints(app, csrf, **flags):
    _ff_architecture = flags.get("_ff_architecture", False)
    _ff_solutions_strategic = flags.get("_ff_solutions_strategic", False)
    _ff_import_batch = flags.get("_ff_import_batch", False)

    # Architect UI
    if not _ff_architecture:
        try:
            from app.modules.architecture.routes.architect_ui_routes import (
                architect_ui_bp,
            )

            app.register_blueprint(architect_ui_bp)
            app.logger.info("[BLUEPRINT] Architect UI routes registered")
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register Architect UI routes: {e}"
            )

    # Solution Design
    if not _ff_solutions_strategic:
        try:
            from app.modules.solutions_strategic.routes.solution_design_routes import (
                solution_design_bp,
            )

            app.register_blueprint(solution_design_bp)
            app.logger.info("[BLUEPRINT] Solution Design registered at /solutions")
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register Solution Design routes: {e}"
            )

    if not _ff_solutions_strategic:
        # Roadmap Builder
        try:
            from app.modules.solutions_strategic.routes.roadmap_builder_routes import (
                roadmap_builder_bp,
            )

            app.register_blueprint(roadmap_builder_bp)
            app.logger.info(
                "[BLUEPRINT] Roadmap Builder API registered at /api/roadmap-builder"
            )
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register Roadmap Builder API routes: {e}"
            )

    # Architecture Monitoring API: mounted by app.modules.architecture (v2 and module
    # tiers) only when ARCHITECTURE_MONITORING_API_ENABLED is on; off by default.

    # Typed ARB condition evidence/verify/waive API. Canonical-only surface with
    # no legacy counterpart, so it is not gated on the legacy architecture flag.
    try:
        from app.modules.architecture.routes.arb_condition_routes import (
            arb_conditions_api_bp,
        )

        app.register_blueprint(arb_conditions_api_bp)
        app.logger.info(
            "[BLUEPRINT] Typed ARB condition API registered at /arb/api/conditions"
        )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register typed ARB condition routes: {e}"
        )

    # Typed ARB condition HTML child routes (blueprint §11). The non-JS form
    # transport for the same commands; separate blueprint so it does not depend
    # on arb_bp being imported first.
    try:
        from app.modules.architecture.routes.arb_condition_html_routes import (
            arb_conditions_html_bp,
        )

        app.register_blueprint(arb_conditions_html_bp)
        app.logger.info(
            "[BLUEPRINT] Typed ARB condition HTML child routes registered at /arb/reviews"
        )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register typed ARB condition HTML routes: {e}"
        )

    # ARB routes
    if not _ff_architecture:
        try:
            from app.modules.architecture.routes.arb_routes import arb_bp

            app.register_blueprint(arb_bp)
            app.logger.info("[BLUEPRINT] ARB routes registered at /arb")
        except Exception as e:
            app.logger.warning(f"[BLUEPRINT] Failed to register ARB routes: {e}")

        try:
            from app.modules.architecture.routes.arb_workflow_routes import (
                arb_workflow_bp,
            )

            app.register_blueprint(arb_workflow_bp)
            app.logger.info(
                "[BLUEPRINT] ARB Workflow API registered at /api/arb-workflow"
            )
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register ARB Workflow routes: {e}"
            )

        # ADM Kanban views
        try:
            from app.modules.architecture.routes.adm_kanban_view_routes import (
                adm_kanban_view_bp,
            )

            app.register_blueprint(adm_kanban_view_bp)
            app.logger.info("[BLUEPRINT] ADM Kanban views registered at /adm-kanban")
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register ADM Kanban view routes: {e}"
            )

        # ADM Kanban API
        try:
            from app.modules.architecture.routes.adm_kanban_routes import adm_kanban_bp

            app.register_blueprint(adm_kanban_bp)
            app.logger.info("[BLUEPRINT] ADM Kanban API registered at /api/adm-kanban")
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register ADM Kanban API routes: {e}"
            )

        # ADM Kanban V2 API
        try:
            from app.modules.architecture.routes.adm_kanban_v2_routes import (
                adm_kanban_v2_bp,
            )

            app.register_blueprint(adm_kanban_v2_bp)
            app.logger.info(
                "[BLUEPRINT] ADM Kanban V2 API registered at /api/adm-kanban/v2"
            )
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register ADM Kanban V2 API routes: {e}"
            )

    # Ensure the ADM Kanban v2 page never ships without its backing API.
    # Some startup paths can leave the page blueprint active while the v2 API
    # blueprint is still absent; register it here if it is available but missing.
    if "adm_kanban_view" in app.blueprints and "adm_kanban_v2" not in app.blueprints:
        try:
            from app.modules.architecture.routes.adm_kanban_v2_routes import (
                adm_kanban_v2_bp,
            )

            app.register_blueprint(adm_kanban_v2_bp)
            app.logger.info(
                "[BLUEPRINT] ADM Kanban V2 API registered via fallback at /api/adm-kanban/v2"
            )
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed fallback registration for ADM Kanban V2 API routes: {e}"
            )

    # Sprint API (TPM-001)
    try:
        from app.modules.architecture.routes.sprint_routes import sprint_bp, sprint_view_bp

        app.register_blueprint(sprint_bp)
        app.register_blueprint(sprint_view_bp)
        app.logger.info("[BLUEPRINT] Sprint API registered at /api/sprints")
        app.logger.info("[BLUEPRINT] Sprint views registered at /sprints")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Sprint API routes: {e}")

    # Flow Analytics — cycle time, throughput, WIP (TPM-009)
    try:
        from app.modules.architecture.routes.analytics_routes import flow_analytics_bp

        app.register_blueprint(flow_analytics_bp)
        app.logger.info("[BLUEPRINT] Flow Analytics registered at /analytics/flow")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Flow Analytics routes: {e}")

    # Communication Log API (TPM-012)
    try:
        from app.modules.architecture.routes.communication_routes import communication_bp

        app.register_blueprint(communication_bp)
        app.logger.info("[BLUEPRINT] Communication Log API registered at /api/communications")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Communication Log routes: {e}")

    # Risk Heat Map API + UI (TPM-013)
    try:
        from app.modules.architecture.routes.risk_routes import risk_bp

        app.register_blueprint(risk_bp)
        app.logger.info("[BLUEPRINT] Risk Heat Map registered at /api/risks and /solutions/<id>/risks")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Risk Heat Map routes: {e}")

    # Architecture Decision Records (EA ADRs)
    try:
        from app.main.routes_architecture_decisions import arch_decisions_bp

        app.register_blueprint(arch_decisions_bp)
        app.logger.info("[BLUEPRINT] Architecture Decisions registered at /architecture/decisions")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Architecture Decisions routes: {e}")

    # Solution Architect Workspace
    if not _ff_solutions_strategic:
        try:
            from app.modules.solutions_strategic.routes.solution_architect_routes import (
                solution_architect_bp,
            )

            app.register_blueprint(solution_architect_bp)
            app.logger.info(
                "[BLUEPRINT] Solution Architect Workspace registered at /solution-architect"
            )
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register Solution Architect routes: {e}"
            )

    # Solution ArchiMate API — registered by solutions_strategic module __init__.py
    # (removed duplicate registration that caused "name already registered" warning)

    # Batch Import (feature-flagged — skip if v2 or v1 already registered)
    if not _ff_import_batch and not _is_flag("USE_IMPORT_BATCH_GUARDRAILS"):
        try:
            from app.modules.import_batch.routes.batch_import_routes import (
                batch_import_bp,
            )

            app.register_blueprint(batch_import_bp)
            app.logger.info(
                "[BLUEPRINT] Batch Import API registered at /api/batch-import"
            )
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register Batch Import routes: {e}"
            )

        try:
            from app.modules.import_batch.routes.batch_import_view_routes import (
                batch_import_view_bp,
            )

            app.register_blueprint(batch_import_view_bp)
            app.logger.info(
                "[BLUEPRINT] Batch Import views registered at /batch-import"
            )
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register Batch Import view routes: {e}"
            )

        try:
            from app.modules.import_batch.routes.unified_import_routes import (
                bp as unified_import_bp,
            )

            app.register_blueprint(unified_import_bp)
            app.logger.info("[BLUEPRINT] Unified Import API registered at /api/import")
        except Exception as e:
            app.logger.warning(
                f"[BLUEPRINT] Failed to register Unified Import routes: {e}"
            )

    # Sidebar API (hardened)
    try:
        from app.routes.sidebar_api_hardened import sidebar_api_bp

        app.register_blueprint(sidebar_api_bp)
        app.logger.info("[BLUEPRINT] Sidebar API (hardened) registered at /api/sidebar")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Sidebar API routes: {e}")

    # Webhooks and connectors
    try:
        from app.routes.connector_routes import connector_bp
        from app.routes.webhook import webhook_bp

        app.register_blueprint(webhook_bp)
        app.register_blueprint(connector_bp)
        app.logger.info("\u2705 Webhook API routes registered at /api/webhooks")
    except Exception as e:
        app.logger.warning(f"Failed to register webhook routes: {e}")

    # COM-008: ServiceNow CMDB admin connector routes.
    # Fallback only — admin v2 (app/modules/admin/v2/__init__.py) registers this
    # blueprint when USE_ADMIN_V2 is on; re-registering raised a warning every boot.
    try:
        if "m365_connector" not in app.blueprints:
            from app.modules.admin.connector_routes import m365_connector_bp

            app.register_blueprint(m365_connector_bp)
            app.logger.info("[BLUEPRINT] ServiceNow/M365 connector admin routes registered at /admin/connectors")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register connector admin routes: {e}")

    # Jira webhook receiver (TPM-008)
    try:
        from app.modules.architecture.routes.webhook_routes import webhook_routes_bp

        app.register_blueprint(webhook_routes_bp)
        app.logger.info("[BLUEPRINT] Jira webhook receiver registered at /webhooks/jira")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Jira webhook routes: {e}")

    # Backlog Prioritisation — MoSCoW / WSJF / RICE (TPM-006)
    try:
        from app.modules.architecture.routes.prioritisation_routes import prioritisation_bp

        app.register_blueprint(prioritisation_bp)
        app.logger.info("[BLUEPRINT] Backlog Prioritisation registered at /backlog/prioritisation")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Prioritisation routes: {e}")

    # COM-015: compat_stats removed (BPM-001 wave-1, zero callers, REMOVE)

    # Architecture Assistant
    if not _ff_architecture:
        try:
            from app.modules.architecture.routes.architecture_assistant_routes import (
                architecture_assistant_bp,
            )

            app.register_blueprint(architecture_assistant_bp)
        except Exception as e:
            app.logger.warning(f"Failed to register Architecture Assistant routes: {e}")

    # Journey v1 DELETED — superseded by architecture_journey (was /journey/, now /architecture-journey/)
    # Journey is registered via solutions_strategic v2 module __init__.py


def _register_enterprise_exports(app, csrf):
    """Register North Star Phase 4 enterprise export endpoints.
    
    Provides Fortune 500 export formats:
    - PDF: Solution Architecture Documents (ARB-ready)
    - PowerPoint: Executive presentation decks
    - Excel: Portfolio data + TCO models
    - ArchiMate XML: Open Exchange Format (Sparx EA/BiZZdesign compatible)
    
    Endpoints:
    - GET  /api/export/solutions/{id}/pdf
    - GET  /api/export/solutions/{id}/pptx
    - GET  /api/export/portfolio/excel
    - POST /api/export/archimate/xml
    """
    try:
        from app.api.enterprise_export_routes import export_bp
        
        app.register_blueprint(export_bp)
        app.logger.info("[BLUEPRINT] Enterprise Export API registered at /api/export (NORTH-STAR-004)")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Enterprise Export registration failed: {e}")
        # Non-fatal - exports are optional enhancement


def _register_archimate_layer_navigation(app):
    """Register North Star Phase 2 ArchiMate layer navigation routes.
    
    Provides layer-specific entry points that redirect to the ArchiMate composer
    with appropriate filters. Enables navigation to all 55 ArchiMate 3.2 element types
    organized by layer.
    
    Routes:
    - /architecture/motivation/* - Motivation Layer (9 element types)
    - /architecture/strategy/* - Strategy Layer (4 element types)
    - /architecture/business/* - Business Layer (13 element types)
    - /architecture/application/* - Application Layer (8 element types)
    - /architecture/technology/* - Technology Layer (11 element types)
    - /architecture/physical/* - Physical Layer (4 element types)
    - /architecture/implementation/* - Implementation Layer (4 element types)
    """
    try:
        from app.modules.architecture.routes.archimate_layer_nav_routes import archimate_layer_nav_bp
        
        app.register_blueprint(archimate_layer_nav_bp)
        app.logger.info("[BLUEPRINT] ArchiMate Layer Navigation registered at /architecture/* (NORTH-STAR-002)")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] ArchiMate Layer Navigation registration failed: {e}")
        # Non-fatal - navigation will fall back to main composer

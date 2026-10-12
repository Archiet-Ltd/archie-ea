"""
Context processors — global template variables.
"""

import flask
from sqlalchemy import event

_EMPTY_NAV_COUNTS = {"applications": 0, "vendors": 0, "elements": 0, "capabilities": 0}

# Non-request callers (CLI/tests) still use a short-lived per-process cache so
# repeated reads do not re-count on every call. Request paths read fresh and are
# memoised only for the life of the request below.
_nav_counts_cache: dict = {}
_NAV_COUNTS_TTL = 300

# Not cached across requests. These counts were held for five minutes in a
# per-process dict, so after a create or an import the pages that decide "is
# anything modelled yet" (Ask, Twin map, the first-run card) kept answering
# "nothing" until the entry expired -- and under gunicorn each worker held its
# own copy, so clearing it in the worker that handled the write left every
# other worker stale. The four counts are indexed single-column COUNTs, cheap
# enough to read fresh; they are memoised for the length of one request only,
# because a single page asks for them more than once.
_NAV_COUNTS_MEMO_KEY = "entelim.nav_counts_by_org"  # in the WSGI environ: one request

_NAV_COUNT_MODELS = (
    "ApplicationComponent",
    "ArchiMateElement",
    "BusinessCapability",
    "VendorOrganization",
)

EM_DASH = "—"


def _invalidate_nav_counts(session, flush_context):
    """Evict nav-count cache entries for every organisation whose counted
    records changed in this flush. Registered once at module level so that
    multiple ``create_app()`` calls do not stack listeners."""
    touched = set(session.new) | set(session.deleted)
    if not touched:
        return
    org_ids = set()
    clear_all = False
    for obj in touched:
        cls_name = type(obj).__name__
        if cls_name not in _NAV_COUNT_MODELS:
            continue
        if cls_name == "VendorOrganization":
            clear_all = True
        else:
            org_id = getattr(obj, "organization_id", None)
            if org_id is not None:
                org_ids.add(org_id)
    if clear_all:
        _nav_counts_cache.clear()
    for org_id in org_ids:
        _nav_counts_cache.pop(org_id, None)


from app.extensions import db  # noqa: E402 — module-level db import safe here
if not event.contains(db.session, "after_flush", _invalidate_nav_counts):
    event.listen(db.session, "after_flush", _invalidate_nav_counts)



def compute_nav_counts(org_id, ttl=_NAV_COUNTS_TTL):
    """Sidebar entity counts for one organisation.

    Scoping is explicit. ``db.session.query(db.func.count(Model.id))`` is a
    COLUMN query, and the tenant isolation in this codebase is
    ``with_loader_criteria``, which only applies to ENTITY queries — so these
    counts were never filtered for anyone. A browser walk showed a tenant with
    14 applications being told it had 38, the total across every organisation.

    ``VendorOrganization`` has no organization_id column at all, so its count is
    global by construction; that matches what the vendor list itself shows and
    is called out here rather than silently scoped to something it isn't.

    In request context these counts are read fresh and memoised only on the
    WSGI environ for the life of one page render, so a create is visible on the
    very next load. Non-request callers fall back to the short-lived process
    cache above.
    """
    import time
    from flask import has_request_context, request

    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.models.vendor.vendor_organization import VendorOrganization

    memo = None
    if has_request_context():
        memo = request.environ.setdefault(_NAV_COUNTS_MEMO_KEY, {})
        if org_id in memo:
            return dict(memo[org_id])
    else:
        now = time.time()
        hit = _nav_counts_cache.get(org_id)
        if hit is not None and now - hit["timestamp"] < hit.get("ttl", ttl):
            return dict(hit["data"])

    def _scoped(model):
        if org_id is None:
            return 0
        q = db.session.query(db.func.count(model.id))
        q = q.filter(model.organization_id == org_id)
        return q.scalar() or 0

    counts = {
        "applications": _scoped(ApplicationComponent),
        "elements": _scoped(ArchiMateElement),
        "capabilities": _scoped(BusinessCapability),
        # Not tenant-scoped anywhere in the product — see docstring.
        "vendors": db.session.query(db.func.count(VendorOrganization.id)).scalar() or 0,
    }
    if memo is not None:
        memo[org_id] = dict(counts)
    else:
        entry = {"data": dict(counts), "timestamp": time.time()}
        # Emptiness is judged on the tenant's own counts: vendors are global, so
        # one vendor row anywhere would otherwise keep a brand-new, empty
        # organisation on the long TTL and hide its first writes from other workers.
        if all(v == 0 for k, v in counts.items() if k != "vendors"):
            entry["ttl"] = 5
        _nav_counts_cache[org_id] = entry
    return counts


def init_context_processors(app):
    """Register all context processors for Jinja templates."""

    # Keyed by organisation id: the per-model counts below are tenant-filtered
    # entity queries, so one shared entry served one tenant's counts to all.
    _dashboard_categories_cache: dict = {}
    _cache_ttl = 300  # 5 minutes

    @app.context_processor
    def inject_dashboard_categories():
        """Make dashboard categories available to all templates - OPTIMIZED WITH CACHING"""
        import time
        from collections import defaultdict

        from flask import request, url_for
        from sqlalchemy.exc import OperationalError, ProgrammingError

        from app.extensions import db
        try:
            from app.main.dynamic_dashboards import MODEL_REGISTRY
        except ImportError:
            MODEL_REGISTRY = {}  # module deleted, dashboard categories disabled

        default_result = {"dashboard_categories": {}, "dashboard_registry_url": "#"}

        try:
            if request.endpoint and not any(
                path in request.path for path in ["/auto-dashboard", "/dashboard"]
            ):
                return default_result
        except Exception:
            return default_result

        from flask import g, has_request_context

        _org_key = getattr(g, "current_org_id", None) if has_request_context() else None
        current_time = time.time()
        _hit = _dashboard_categories_cache.get(_org_key)
        if _hit is not None and current_time - _hit["timestamp"] < _cache_ttl:
            result = _hit["data"].copy()
            try:
                result["dashboard_registry_url"] = url_for(
                    "dynamic_dashboards.model_registry_index"
                )
            except Exception:
                result["dashboard_registry_url"] = "#"
            return result

        try:
            category_icons = {
                "Vendor Management": "store",
                "Application Architecture": "layers",
                "Business Architecture": "building - 2",
                "Implementation": "rocket",
                "Governance": "scale",
                "Strategy": "target",
                "Technology": "cpu",
                "Data Architecture": "database",
                "Cost Intelligence": "dollar-sign",
                "Compliance": "shield-check",
                "Requirements": "file-text",
                "Reference Models": "book-open",
                "Enterprise Intelligence": "brain",
                "Integration": "link",
            }

            categories = defaultdict(list)
            for model_name, info in MODEL_REGISTRY.items():
                category = info.get("category", "Other")
                try:
                    model_class = info.get("model")
                    if model_class is not None:
                        count = model_class.query.count()
                    else:
                        count = 0
                except (OperationalError, ProgrammingError):
                    db.session.rollback()
                    count = 0
                except Exception:
                    count = 0
                categories[category].append(
                    {
                        "name": model_name,
                        "label": info.get("label", model_name),
                        "count": count,
                        "icon": info.get("icon", "database"),
                        "description": info.get("description", ""),
                    }
                )

            for category in categories:
                categories[category].sort(
                    key=lambda x: (-x["count"], x["label"])
                )
            sorted_categories = dict(
                sorted(
                    categories.items(),
                    key=lambda item: (-sum(m["count"] for m in item[1]), item[0]),
                )
            )
            for cat_name in sorted_categories:
                sorted_categories[cat_name] = [
                    {**m, "icon": category_icons.get(cat_name, "folder")}
                    if "icon" not in m or m["icon"] == "database"
                    else m
                    for m in sorted_categories[cat_name]
                ]

            _dashboard_categories_cache[_org_key] = {
                "data": {"dashboard_categories": sorted_categories},
                "timestamp": current_time,
            }

            try:
                registry_url = url_for("dynamic_dashboards.model_registry_index")
            except Exception:
                registry_url = "#"  # blueprint not registered

            return {
                "dashboard_categories": sorted_categories,
                "dashboard_registry_url": registry_url,
            }
        except (OperationalError, ProgrammingError):
            db.session.rollback()
            return default_result
        except Exception as e:  # fabricated-ok: empty nav categories on error, rendered as no items not a measured value
            app.logger.debug(f"Error loading dashboard categories: {e}")
            return default_result

    @app.context_processor
    def inject_guardrail_status():
        """Make guardrail status available to admin templates"""
        return {"guardrail_status": "active"}

    @app.context_processor
    def inject_applications_and_vendors():
        """Make applications and vendors available to admin templates that need them"""
        from flask import request
        from sqlalchemy.exc import OperationalError, ProgrammingError

        from app.extensions import db

        try:
            if request.endpoint and not any(
                path in request.path
                for path in ["/admin", "/capability", "/architecture", "/enterprise"]
            ):
                return {"applications": [], "vendors": []}
        except Exception:  # fabricated-ok: empty nav lists, not measured data; page needs no app/vendor list here
            return {"applications": [], "vendors": []}

        # Not cached across requests. The query below is tenant-filtered (an
        # entity query, so with_loader_criteria applies). Its result used to be
        # held for five minutes per worker process, which both hid a newly
        # created application from these pages and kept ORM rows alive past the
        # session that loaded them.
        try:
            from app.models.application_portfolio import ApplicationComponent

            applications = (
                ApplicationComponent.query.order_by(ApplicationComponent.name).all()
            )

            try:
                from app.models.vendor.vendor_organization import VendorOrganization
                vendors = VendorOrganization.query.order_by(VendorOrganization.name).all()
            except Exception:
                vendors = []

            return {"applications": applications, "vendors": vendors}
        except (OperationalError, ProgrammingError):
            db.session.rollback()
            return {"applications": [], "vendors": []}
        except Exception as e:  # fabricated-ok: empty nav lists on error, rendered as no items not a measured value
            db.session.rollback()
            app.logger.debug(f"Error loading applications/vendors: {e}")
            return {"applications": [], "vendors": []}

    @app.context_processor
    def inject_flask():
        """Make flask object available to all templates to fix flash messaging issues"""
        return {"flask": flask}

    @app.context_processor
    def inject_currency_config():
        """H-04: one currency source of truth for every template.

        `currency_config` (symbol/code/decimal_places) is derived from the
        current org's `settings['currency_code']` (config.CurrencyConfig).
        Server-rendered money should read `currency_config.symbol` /
        `.decimal_places` here rather than hardcoding '$' or '£'; the same
        org currency code is also handed to the client currencyManager (see
        partials/_head.html) so JS-rendered figures agree with server ones.
        """
        from flask import has_request_context
        from app.middleware.tenant_context import current_org
        from config import CurrencyConfig

        organization = None
        try:
            if has_request_context():
                organization = current_org()
        except Exception:  # noqa: BLE001 — currency display can't 500 a page
            organization = None

        try:
            cfg = CurrencyConfig.get_org_currency_config(organization)
        except Exception as e:  # noqa: BLE001
            app.logger.warning(f"currency config unavailable: {e}")
            cfg = CurrencyConfig.get_currency_config()

        return {"currency_config": cfg, "currency_symbol": cfg["symbol"]}

    @app.context_processor
    def inject_nav_counts():
        """Live entity counts for sidebar navigation labels.

        Replaces hardcoded counts that go stale (sidebar said 358 vendors
        while the dashboard card said 17). Read fresh on each request, so a
        record created a moment ago is counted on the next page load.
        """
        from flask import g, has_request_context

        org_id = getattr(g, "current_org_id", None) if has_request_context() else None
        try:
            return {"nav_counts": compute_nav_counts(org_id)}
        except Exception as e:  # noqa: BLE001 — a sidebar label can't 500 a page
            app.logger.warning(f"nav counts unavailable: {e}")
            return {"nav_counts": dict(_EMPTY_NAV_COUNTS)}

    @app.context_processor
    def inject_active_organization():
        """Expose the active organisation and memberships to templates."""
        from flask_login import current_user

        from app.middleware.tenant_context import accessible_organizations, current_org

        try:
            org = current_org()
            memberships = accessible_organizations(current_user)
        except Exception as e:  # noqa: BLE001 — header identity must not 500 pages
            app.logger.warning(f"active organization context unavailable: {e}")
            org = None
            memberships = []

        return {
            "active_organization": org,
            "active_organization_name": getattr(org, "name", None),
            "organization_memberships": memberships,
        }

    @app.context_processor
    def inject_legal_links():
        """The legal pages live right now, for the public footer and checkout."""
        from app.services.legal_pages import legal_links

        return {"legal_links": legal_links()}

    @app.context_processor
    def inject_feature_flags():
        """Make feature flag helpers available to all templates"""
        from app.models import FeatureFlag

        return {
            "is_feature_enabled": FeatureFlag.is_feature_enabled,
            "get_feature": lambda key: FeatureFlag.query.filter_by(key=key).first(),
            "sidebar_features": FeatureFlag.get_sidebar_features,
        }

    @app.context_processor
    def inject_north_star_config():
        """Make North Star navigation flags available to templates.
        
        Phase 1: Renamed terms, hidden admin items (NORTH-STAR-001)
        Phase 2: Complete ArchiMate 3.2 layer navigation (NORTH-STAR-002)
        Phase 3: Professional visual polish (NORTH-STAR-003)
        """
        from app.models import FeatureFlag
        
        # Initialize defaults — Phase 3 (professional blue theme) is ON by default:
        # the North Star visual standard is the platform's committed direction.
        phase1_enabled = True
        phase2_enabled = True
        phase3_enabled = True

        try:
            phase1_flag = FeatureFlag.query.filter_by(key='north_star_navigation').first()
            if phase1_flag:
                phase1_enabled = phase1_flag.enabled

            phase2_flag = FeatureFlag.query.filter_by(key='north_star_phase2').first()
            if phase2_flag:
                phase2_enabled = phase2_flag.enabled

            phase3_flag = FeatureFlag.query.filter_by(key='north_star_phase3').first()
            if phase3_flag:
                phase3_enabled = phase3_flag.enabled

        except Exception as e:
            # A failing query earlier in the request can poison the transaction so
            # these FeatureFlag reads raise InFailedSqlTransaction. Roll back so the
            # rest of the request (and the pooled connection) recovers cleanly.
            try:
                from app.extensions import db
                db.session.rollback()
            except Exception:
                pass
            app.logger.error(f"North Star context processor error: {e}", exc_info=True)

        return {
            "north_star_enabled": phase1_enabled,
            "north_star_phase2_enabled": phase2_enabled,
            "north_star_phase3_enabled": phase3_enabled,
            # Short alias used in base templates
            "phase3_enabled": phase3_enabled,
        }

    @app.context_processor
    def inject_user_feature_flags():
        """Provide feature flags as a JSON-serializable dict for Alpine.store('user')."""
        from flask_login import current_user

        from app.models import FeatureFlag

        if not current_user.is_authenticated:
            return {"user_feature_flags": {}}

        try:
            flags = FeatureFlag.query.filter_by(enabled=True).all()
            flag_dict = {f.key: True for f in flags}
            return {"user_feature_flags": flag_dict}
        except Exception:
            from app.extensions import db
            db.session.rollback()
            return {"user_feature_flags": {}}

    @app.context_processor
    def inject_page_guide_context():
        """Provide resolved page-guide context to authenticated templates."""
        from flask import request
        from flask_login import current_user

        if not current_user.is_authenticated:
            return {"page_guide_context": {"enabled": False, "supported": False}}

        try:
            from app.modules.ai_chat.services.page_guide_registry import (
                build_generic_page_guide,
                resolve_page_guide,
            )
            from app.modules.ai_chat.services.page_guide_service import PageGuideService

            entry = resolve_page_guide(request.endpoint, request.view_args or {})
            enabled = PageGuideService.is_enabled()
            if not entry:
                if not enabled:
                    return {"page_guide_context": {"enabled": False, "supported": False}}
                entry = build_generic_page_guide(request.endpoint, request.view_args or {})

            return {
                "page_guide_context": {
                    "enabled": enabled,
                    "supported": entry.get("guide_mode") == "specialized",
                    "guide_mode": entry.get("guide_mode", "specialized"),
                    "page_key": entry["page_key"],
                    "scope_key": entry["scope_key"],
                    "title": entry["title"],
                    "summary": entry["summary"],
                    "starter_questions": entry.get("starter_questions", []),
                    "glossary": entry.get("glossary", []),
                    "suggested_actions": entry.get("suggested_actions", []),
                }
            }
        except Exception:
            return {"page_guide_context": {"enabled": False, "supported": False}}

    # S1-01: Role-based sidebar section visibility
    ROLE_SECTION_MAP = {
        "enterprise_architect": {
            "home", "application", "architecture", "solutions",
            "roadmaps", "governance", "capabilities", "tools",
            "data", "utilities", "admin",
        },
        "solutions_architect": {
            "home", "application", "architecture", "solutions",
            "governance", "capabilities",
        },
        "business_architect": {
            "home", "capabilities", "governance", "architecture",
        },
        "data_architect": {
            "home", "architecture", "application", "capabilities", "data",
        },
        "technology_architect": {
            "home", "architecture", "application", "tools", "utilities",
        },
        "application_architect": {
            "home", "application", "capabilities", "architecture",
        },
        "integration_architect": {
            "home", "architecture", "application", "tools", "utilities",
        },
        "compliance": {
            "home", "governance", "architecture", "roadmaps",
        },
        "manager": {
            "home", "governance", "roadmaps", "architecture",
        },
    }

    # PLT-040: enterprise_role-based sidebar visibility (takes precedence over archetype)
    # NS-006: Updated for North Star Persona MVP with 8 enterprise roles
    # (+ business_architect added for the Business Architect persona)
    #
    # ADR-0008 correction (Task 05, sap-s4-interface-register): this used to be
    # a second, hand-maintained literal dict that disagreed with
    # app.utils.role_access.ROLE_SECTION_ACCESS — security_architect and
    # data_architect had entries there and none here, so those two personas
    # fell through to the archetype map (or all_sections) for sidebar
    # visibility while ROLE_SECTION_ACCESS.can_access_section() — the
    # predicate every route guard in this codebase actually calls, including
    # interface_register's _guard() — already granted them data_integration.
    # That gap meant this map's own idea of section access disagreed with
    # ROLE_SECTION_ACCESS. One system of record: derive this map from
    # ROLE_SECTION_ACCESS and layer the legacy section aliases
    # (application/tools/data/utilities/admin) that predate the NS-006
    # section names on top, per role, exactly where they were inlined before.
    #
    # CORRECTED (Task 05 round 2 review): this dedup does NOT, by itself, put
    # an Interface Register link in front of security_architect or
    # data_architect. ENTERPRISE_ROLE_SECTION_MAP only feeds
    # `user_visible_sections` below, and no template/macro/JS reads that
    # variable — the real sidebar renders from
    # app.utils.role_access.get_sidebar_zones() /
    # `_MY_WORK_LINKS`, a completely separate structure. The actual "was
    # authorised but had no link" defect was fixed by adding real
    # `_link("Interface Register", ...)` entries to
    # `_MY_WORK_LINKS[ROLE_SECURITY_ARCHITECT]` and
    # `_MY_WORK_LINKS[ROLE_DATA_ARCHITECT]` in role_access.py. This map
    # derivation remains a legitimate, worthwhile ADR-0008 cleanup (one fewer
    # hand-maintained literal disagreeing with the canonical
    # ROLE_SECTION_ACCESS), it just is not sufficient on its own and never
    # was — do not cite it as evidence that a sidebar link exists.
    from app.utils.role_access import ROLE_SECTION_ACCESS as _ROLE_SECTION_ACCESS

    _LEGACY_SECTION_ALIASES = {
        "enterprise_architect": {"application", "tools", "data", "utilities"},
        "portfolio_manager": {"application", "tools"},
        "procurement": {"application"},
        "application_manager": {"application"},
        "platform_admin": {"application", "tools", "data", "utilities", "admin"},
    }
    ENTERPRISE_ROLE_SECTION_MAP = {
        role: sections | _LEGACY_SECTION_ALIASES.get(role, set())
        for role, sections in _ROLE_SECTION_ACCESS.items()
    }

    @app.context_processor
    def inject_user_visible_sections():
        """Inject role-based sidebar sections for persona-specific navigation (S1-01, NS-006)."""
        from flask_login import current_user

        # Default: show everything (admins and unknown roles see all)
        # NS-006: Added portfolio, procurement, my_applications, data_integration, administration
        all_sections = {
            "home", "application", "architecture", "solutions",
            "roadmaps", "governance", "capabilities", "tools",
            "data", "utilities", "admin",
            # North Star Persona MVP sections
            "portfolio", "procurement", "my_applications",
            "data_integration", "administration",
            # Business Architect persona section
            "business_architecture",
        }

        if not current_user.is_authenticated:
            return {"user_visible_sections": all_sections, "show_all_sections": True}

        # Check localStorage-backed "Show All" override (passed via cookie)
        from flask import request
        show_all = request.cookies.get("show_all_sections") == "true"

        if show_all or current_user.is_admin():
            return {"user_visible_sections": all_sections, "show_all_sections": True}

        # PLT-040: enterprise_role takes precedence over role_archetype
        enterprise_role = getattr(current_user, "enterprise_role", None)
        if enterprise_role:
            ent_key = enterprise_role.strip().lower().replace(" ", "_")
            if ent_key in ENTERPRISE_ROLE_SECTION_MAP:
                return {"user_visible_sections": ENTERPRISE_ROLE_SECTION_MAP[ent_key], "show_all_sections": False}

        archetype = getattr(current_user, "role_archetype", None)
        if archetype:
            key = archetype.strip().lower().replace(" ", "_")
            visible = ROLE_SECTION_MAP.get(key, all_sections)
        else:
            visible = all_sections

        return {"user_visible_sections": visible, "show_all_sections": False}

    app.jinja_env.globals["flask"] = flask

    # NS-006: Register role-based access functions for persona navigation
    from app.utils.role_access import get_sidebar_zones, role_access_context_processor
    role_funcs = role_access_context_processor()
    for name, func in role_funcs.items():
        app.jinja_env.globals[name] = func

    # Shell-overhaul Wave 1 (Task 3): persona sidebar zones. The sidebar
    # template (components/admin_sidebar.html) renders from this only — see
    # app/utils/role_access.py for the single source of truth.
    app.jinja_env.globals["get_sidebar_zones"] = get_sidebar_zones

    # E2E-7: the header's role badge (components/admin_header.html) read
    # current_user.role.name -- the legacy Flask-Base Role/Permission table,
    # which has only a couple of rows -- instead of enterprise_role, the
    # vocabulary that actually distinguishes the 11 personas (see "Three
    # authz vocabularies" — CLAUDE.md). Registered globally rather than
    # per-route so the shared header gets it on every page, not just the
    # one route that happened to pass it into its own render_template call.
    from app.utils.role_access import get_role_display_name
    app.jinja_env.globals["get_role_display_name"] = get_role_display_name

    # Register HTML sanitizer filter for safe rendering of user-generated rich text
    from app.utils.html_sanitizer import sanitize_html
    app.jinja_env.filters["sanitize_html"] = sanitize_html

    # Register error humanization filter for workflow error messages
    from app.utils.template_utils import humanize_error
    app.jinja_env.filters["humanize_error"] = humanize_error

    # Register date/datetime formatting filters
    def _filter_format_date(value, fmt='%d %b %Y'):
        if value is None:
            return ''
        if hasattr(value, 'strftime'):
            return value.strftime(fmt)
        return str(value)

    def _filter_format_datetime(value, fmt='%d %b %Y %H:%M'):
        if value is None:
            return ''
        if hasattr(value, 'strftime'):
            return value.strftime(fmt)
        return str(value)

    app.jinja_env.filters["format_date"] = _filter_format_date
    app.jinja_env.filters["format_datetime"] = _filter_format_datetime

    # H-04: one currency formatting path for server-rendered money, matching
    # whatever inject_currency_config() resolved for this org (organization
    # settings['currency_code'] -> config.CurrencyConfig). None stays None ->
    # template renders an em dash, never a fabricated 0.
    def _filter_format_currency(value, show_code=False, currency=None):
        # A missing amount renders as an em dash, never a blank cell and
        # never a fabricated 0 (CLAUDE.md).
        if value is None:
            return EM_DASH
        from flask import has_request_context
        from app.middleware.tenant_context import current_org
        from config import CurrencyConfig

        organization = None
        try:
            if has_request_context():
                organization = current_org()
        except Exception:
            organization = None
        cfg = CurrencyConfig.get_org_currency_config(organization)
        if currency:
            cfg = dict(cfg, code=currency)
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return EM_DASH
        formatted = "{:,.{dp}f}".format(amount, dp=cfg["decimal_places"])
        symbolled = f"{cfg['symbol']}{formatted}" if cfg["position"] == "prefix" else f"{formatted}{cfg['symbol']}"
        return f"{cfg['code']} {symbolled}" if show_code else symbolled

    app.jinja_env.filters["format_currency"] = _filter_format_currency

    # Shell-overhaul Wave 2 (Task 5): templates need a safe way to compare a
    # stored date against "today" (e.g. contract-expiry urgency banners)
    # without doing date arithmetic in Jinja directly, which has no
    # `datetime` module in scope and previously produced a `None`-minus-date
    # TypeError (applications/dashboard.html 500'd on any app with
    # contract_expiry_date set). Returns None — never a fabricated number —
    # when there is nothing to compare.
    def _days_until(value):
        import datetime as _dt
        if value is None:
            return None
        try:
            if isinstance(value, _dt.datetime):
                value = value.date()
            return (value - _dt.date.today()).days
        except TypeError:
            return None

    app.jinja_env.globals["days_until"] = _days_until

    # Cache-busting: single build identifier shared with app/_bootstrap/assets.py
    # and the /version endpoint (ARCH-062) — see build_info.get_build_id().
    from app._bootstrap.build_info import get_build_id
    _static_version = get_build_id()

    @app.context_processor
    def inject_static_version():
        """Provide static_version for cache-busting JS/CSS includes, and build_id
        for user-facing display (About/footer, ARCH-062) — same value, same source
        (build_info.get_build_id()), so what a user sees matches what /version and
        every asset URL report."""
        return {"static_version": _static_version, "build_id": _static_version}

    def _filter_versioned_static(filename):
        """Jinja2 filter: {{ 'js/foo.js' | versioned_static }}
        Returns URL with ?v=<git_hash> for cache-busting."""
        url = flask.url_for("static", filename=filename)
        return f"{url}?v={_static_version}"

    app.jinja_env.filters["versioned_static"] = _filter_versioned_static

    # Override url_for globally so ALL static file references auto-version.
    # This covers all 132 uses across 66 templates without touching each file.
    from werkzeug.routing import BuildError

    _original_url_for = flask.url_for

    def _versioned_url_for(endpoint, **values):
        try:
            url = _original_url_for(endpoint, **values)
        except BuildError:
            # A template references an endpoint that isn't registered — e.g. a nav
            # link to a module disabled by a feature flag, or a stale/renamed route.
            # Degrade to a dead link instead of 500-ing the entire page (a missing
            # sidebar link must never take down every authenticated page); log it
            # once so the bad reference stays discoverable.
            app.logger.warning(
                "url_for: unknown endpoint %r -> rendered as '#'", endpoint
            )
            return "#"
        if endpoint == "static" and _static_version:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}v={_static_version}"
        return url

    app.jinja_env.globals["url_for"] = _versioned_url_for

    # COM-013: Expose POSTHOG_API_KEY to templates for the browser JS snippet.
    @app.context_processor
    def inject_posthog_key():
        import os
        return {"posthog_key": os.environ.get("POSTHOG_API_KEY", "")}

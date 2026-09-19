"""
Application list views, dashboard, and table-data API.

Extracted from app/routes/unified_applications_routes.py
(lines 211-216, 371-587, 590-642, 3561-3688, 3045-3113, 8381-8398).

Routes:
    - test_simple()              GET "/test-simple"
    - application_list()         GET "/"
    - application_roadmap()      GET "/roadmap"
    - api_list()                 GET "/api/list"
    - api_table_data()           GET "/api/table-data"
    - dashboard()                GET "/dashboard"
    - model_dashboard(model_name) GET "/model-dashboard/<string:model_name>"
"""

import logging
from urllib.parse import urlencode

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_capability import UnifiedCapability
from app.modules.my_applications.services import has_assigned_owner
from app.security.import_decorators import with_import_security
from app.utils.pagination import safe_int_arg

# Import performance utilities (conditionally available)  # dead-code-ok
try:
    from app.services.core.data_cache import (  # dead-code-ok
        get_all_applications,  # noqa: F401 — availability probe: the import IS the test
        get_application_filter_options,  # noqa: F401 — availability probe: the import IS the test
        invalidate_application_cache,  # noqa: F401 — availability probe: the import IS the test
    )
    from app.services.core.eager_loading import get_application_options  # dead-code-ok  # noqa: F401 — availability probe: the import IS the test

    PERF_UTILS_AVAILABLE = True
except ImportError:
    PERF_UTILS_AVAILABLE = False

from . import unified_applications_bp

logger = logging.getLogger(__name__)

# APP-032: canonical Abacus lifecycle codes for portfolio filter (matches production; filter is case-insensitive)
# ARCH-100: allow-listed sortable columns for application_list() (?sort=&dir=)
# — maps the query-param name to the actual ORM column, so a bad/unknown
# value can never reach ORDER BY.
_SORTABLE_COLUMNS = {
    "id": ApplicationComponent.id,
    "name": ApplicationComponent.name,
    "type": ApplicationComponent.application_category,
    "lifecycle_status": ApplicationComponent.lifecycle_status,
}

_LIFECYCLE_ABACUS_CODES = (
    "1. UNDETERMINED",
    "2.1 STRATEGIC",
    "2.2 TACTICAL",
    "3. SUNSET",
    "4.1 DECOM DECIDED",
    "4.2 DECOM PLANNED",
    "4.3 READ-ONLY",
    "4.4 STOPPED",
    "5. DECOMMISSIONED",
)


def _escape_like(value):
    """Escape SQL LIKE wildcards (%, _) to prevent data enumeration."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@unified_applications_bp.route("/roadmap")
@login_required
def application_roadmap():
    """
    Portfolio-level application roadmap.

    Redirects to the Enterprise Capability Roadmap which provides the
    portfolio-level implementation & migration view across all applications.
    """
    return redirect(url_for("main.capability_roadmap"))


@unified_applications_bp.route("/")
@login_required
@with_import_security
def application_list():
    """
    Applications list page.

    Data contract (template context):
        applications       list[ApplicationComponent]  — paginated rows (eager-loaded)
        total_count        int   — filtered row count
        portfolio_total    int   — unfiltered portfolio size (for stats)
        current_page       int
        total_pages        int
        page_size          int
        has_prev           bool
        has_next           bool
        filters            dict  — {search, type, status, process_level, capability_level, domain}
        filter_options     dict  — {component_types, lifecycle_statuses, process_levels, capability_levels, domains}
        stats              dict  — {total, active_portfolio, strategic, tactical, sunset_decom_pipeline, decommissioned}
        include_decom      bool  — True when ?include_decom is truthy (show decommissioned rows)
        qs_include_decom_true   str — query string to enable toggle (page reset to 1)
        qs_include_decom_false  str — query string to disable toggle (page reset to 1)
        currency_symbol    str
    """
    # Counts are None, not 0: this context is only ever rendered after the
    # query failed, and "0 applications" is a claim about the estate.
    _EMPTY_CONTEXT = dict(
        applications=[],
        total_count=None,
        portfolio_total=None,
        current_page=1,
        total_pages=1,
        page_size=25,
        has_prev=False,
        has_next=False,
        filters=dict(
            search="", type="", status="", process_level="", capability_level="", domain=""
        ),
        filter_options=dict(
            component_types=[],
            lifecycle_statuses=[],
            process_levels=[],
            capability_levels=[],
            domains=[],
        ),
        stats=dict(
            total=None,
            active_portfolio=None,
            strategic=None,
            tactical=None,
            sunset_decom_pipeline=None,
            decommissioned=None,
            owner_assigned=None,
            vendor_assigned=None,
        ),
        sort="",
        dir="asc",
        load_error="The application portfolio could not be read.",
        currency_symbol="\u00a3",
        bu_filter_active=False,
        bu_name=None,
        show_all_override=False,
        include_decom=False,
        qs_include_decom_true="",
        qs_include_decom_false="",
    )

    try:
        # Clear any prior failed transaction state for this request thread.
        # This prevents InFailedSqlTransaction cascades from unrelated earlier errors.
        try:
            db.session.rollback()
        except Exception as rollback_error:
            logger.error(
                "Failed to reset SQLAlchemy session before application_list: %s",
                rollback_error,
                exc_info=True,
            )

        # ── 1. Parse request parameters ──────────────────────────────────────
        search = request.args.get("search", "").strip()
        type_filter = request.args.get("type", "").strip()
        status_filter = request.args.get("status", "").strip()
        process_level_filter = request.args.get("process_level", "").strip()
        capability_level_filter = request.args.get("capability_level", "").strip()
        domain_filter = request.args.get("domain", "").strip()
        page = max(1, safe_int_arg('page', 1, minimum=1))
        page_size = min(max(1, safe_int_arg('page_size', 25, minimum=1, maximum=500)), 100)

        # APP-100: sortable headers — allow-listed column + direction only,
        # so an arbitrary ?sort= value can never reach ORDER BY.
        sort_param = request.args.get("sort", "").strip()
        dir_param = request.args.get("dir", "asc").strip().lower()
        sort_column = _SORTABLE_COLUMNS.get(sort_param)
        sort_dir = "desc" if dir_param == "desc" else "asc"

        # APP-031: default hide decommissioned; ?include_decom=true shows full portfolio
        include_decom = request.args.get("include_decom", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        def _toggle_include_decom_querystrings():
            """Same filters, page reset to 1, with or without include_decom (for toggle links)."""
            d = request.args.to_dict(flat=True)
            d["page"] = "1"
            d_on = dict(d)
            d_on["include_decom"] = "true"
            d_off = dict(d)
            d_off.pop("include_decom", None)
            return urlencode(d_on, doseq=True), urlencode(d_off, doseq=True)

        qs_include_decom_true, qs_include_decom_false = _toggle_include_decom_querystrings()

        # ── 1b. PLT-019: BU scope resolution ─────────────────────────────────
        # Determine if the current user has a business unit scope set (PLT-018).
        # Admin users can override with ?bu=all to see all applications.
        bu_filter_active = False
        bu_name = None
        show_all_override = False
        _user_bu_id = getattr(current_user, "business_unit_id", None)  # model-safety-ok
        _bu_all_requested = request.args.get("bu", "").strip().lower() == "all"
        if _bu_all_requested and hasattr(current_user, "is_admin") and current_user.is_admin():
            show_all_override = True
        elif _user_bu_id:
            # Resolve the BU actor name for the indicator label
            try:
                from app.models.business_layer import BusinessActor as _BusinessActor

                _bu_actor = db.session.get(_BusinessActor, _user_bu_id)
                if _bu_actor:
                    bu_name = _bu_actor.name
                    bu_filter_active = True
            except Exception as _bu_exc:
                current_app.logger.warning(
                    "PLT-019: could not resolve business_unit_id=%s: %s", _user_bu_id, _bu_exc
                )

        # ── 2. Portfolio total (excluding duplicates) for stats ────────────────
        portfolio_total = ApplicationComponent.query.filter(
            ~ApplicationComponent.name.ilike("(Duplicate)%", escape="\\")
        ).count()

        # ── 3. Build filtered query ───────────────────────────────────────────
        query = ApplicationComponent.query

        # Exclude duplicate-flagged applications from default view unless
        # the user is explicitly searching for them.
        if not search or "(duplicate)" not in (search or "").lower():
            query = query.filter(~ApplicationComponent.name.ilike("(Duplicate)%", escape="\\"))

        # APP-031: active portfolio by default (exclude Abacus decommissioned code)
        if not include_decom:
            query = query.filter(
                db.or_(
                    ApplicationComponent.lifecycle_status.is_(None),
                    func.lower(ApplicationComponent.lifecycle_status) != "5. decommissioned",
                )
            )

        # PLT-019: Apply BU scope filter via application_business_actor_mapping junction
        if bu_filter_active and not show_all_override:
            from app.models.relationship_tables import ApplicationBusinessActorMapping

            query = query.join(
                ApplicationBusinessActorMapping,
                ApplicationBusinessActorMapping.application_component_id == ApplicationComponent.id,
            ).filter(ApplicationBusinessActorMapping.business_actor_id == _user_bu_id)

        if search:
            safe_search = f"%{_escape_like(search)}%"
            query = query.filter(
                ApplicationComponent.name.ilike(safe_search, escape="\\")
                | ApplicationComponent.description.ilike(safe_search, escape="\\")
                | ApplicationComponent.technology_stack.ilike(safe_search, escape="\\")
            )
        if type_filter:
            # "Type" is component_type everywhere it is shown (fact sheet, edit form,
            # list column). application_category is a separate, mostly-empty field;
            # filtering on it left the Type dropdown dead. Filter on component_type.
            query = query.filter(ApplicationComponent.component_type == type_filter)
        # APP-032: lifecycle_status only (no deployment_status); ignore legacy/invalid query values
        if status_filter:
            sf = status_filter.strip().lower()
            canonical_lowers = {c.lower() for c in _LIFECYCLE_ABACUS_CODES}
            if sf in ("4.3 read-only", "4.3 read only"):
                query = query.filter(
                    func.lower(ApplicationComponent.lifecycle_status).in_(
                        ("4.3 read-only", "4.3 read only")
                    )
                )
            elif sf in canonical_lowers:
                query = query.filter(func.lower(ApplicationComponent.lifecycle_status) == sf)

        # Capability-side filters — only join when needed to avoid cross-product
        has_relationship_joins = False

        if capability_level_filter:
            from app.models.unified_application_capability_mapping import (
                UnifiedApplicationCapabilityMapping,
            )

            query = query.join(
                UnifiedApplicationCapabilityMapping,
                UnifiedApplicationCapabilityMapping.application_component_id
                == ApplicationComponent.id,
            ).join(
                UnifiedCapability,
                UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
            )
            has_relationship_joins = True
            try:
                query = query.filter(UnifiedCapability.level == int(capability_level_filter))
            except ValueError:
                capability_level_filter = ""

        # Process level filter (0=Value Chain … 4=Activity)
        if process_level_filter:
            try:
                from app.models.process_data import BusinessProcess

                query = query.join(ApplicationComponent.supported_processes).filter(
                    BusinessProcess.level == int(process_level_filter)
                )
                has_relationship_joins = True
            except (ValueError, ImportError):
                logger.exception("Failed to operation")
                pass

        # Domain filter: direct field match on application_components.business_domain
        if domain_filter:
            query = query.filter(ApplicationComponent.business_domain == domain_filter)

        # ── 4. Count filtered results ─────────────────────────────────────────
        if has_relationship_joins:
            total_count = query.with_entities(ApplicationComponent.id).distinct().count()
            query = query.distinct()
        else:
            total_count = query.count()
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, total_pages)

        # ── 5. Eager-load and paginate ────────────────────────────────────────
        eager_opts = [
            joinedload(ApplicationComponent.capability_mappings).joinedload(
                ApplicationCapabilityMapping.business_capability
            ),
            joinedload(ApplicationComponent.supported_processes),
        ]
        try:
            if hasattr(ApplicationComponent, "primary_vendor_product"):
                eager_opts.append(joinedload(ApplicationComponent.primary_vendor_product))
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            current_app.logger.debug("primary_vendor_product eager-load unavailable", exc_info=True)

        order_by_clause = (
            sort_column.desc() if sort_dir == "desc" else sort_column.asc()
        ) if sort_column is not None else ApplicationComponent.name

        try:
            pagination = (
                query.options(*eager_opts)
                .order_by(order_by_clause)
                .paginate(page=page, per_page=page_size, error_out=False)
            )
        except Exception as eager_exc:
            current_app.logger.warning(
                "applications.list eager-load fallback triggered: %s", eager_exc
            )
            pagination = query.order_by(order_by_clause).paginate(
                page=page, per_page=page_size, error_out=False
            )

        # ── 6. Build filter option lists (deduplicated, normalised) ───────────
        def _distinct_normalised(col):
            rows = db.session.query(col).distinct().all()
            seen, result = set(), []
            for (val,) in rows:
                if val:
                    n = str(val).strip()
                    k = n.lower()
                    if k not in seen:
                        seen.add(k)
                        result.append(n)
            return sorted(result)

        # "Type" is component_type (the field the fact sheet, edit form and list
        # column all treat as Type), not application_category.
        component_types = _distinct_normalised(ApplicationComponent.component_type)
        # APP-032: fixed Abacus lifecycle list (not DISTINCT deployment_status or ad hoc DB strings)
        lifecycle_statuses = [{"value": v, "label": v} for v in _LIFECYCLE_ABACUS_CODES]
        try:
            capability_levels = _distinct_normalised(UnifiedCapability.level)
        except Exception as cap_exc:
            current_app.logger.warning(
                "applications.list capability level options unavailable: %s", cap_exc
            )
            capability_levels = []
        process_levels = [str(i) for i in range(5)]
        # Use actual business_domain values from imported apps (not BusinessDomain model)
        domains = _distinct_normalised(ApplicationComponent.business_domain)

        # ── 7. Stats (always against full portfolio) ──────────────────────────
        # APP-030: lifecycle-only card counts from Abacus lifecycle_status codes.
        # lifecycle_status holds TWO vocabularies in practice: the Abacus codes
        # ("3. sunset", "5. decommissioned") that the filter dropdown offers, and
        # the plain words ("sunset", "deprecated", "retired") written by imports,
        # seeds and the ArchiMate side. Counting only the coded form made the
        # tiles contradict the table right underneath them -- the list showed a
        # row whose Lifecycle Status column read "Sunset" while the
        # "Sunset/Decom Pipeline" tile above it read 0. Recognise both spellings
        # so the tile and the column can never disagree; a plausible-but-wrong
        # count is worse than none because the reader cannot tell.
        _decommissioned_vals = {
            "5. decommissioned",
            "decommissioned",
            "retired",
        }
        _sunset_pipeline_vals = {
            "3. sunset",
            "4.1 decom decided",
            "4.2 decom planned",
            "4.3 read-only",
            "4.3 read only",
            "4.4 stopped",
            "sunset",
            "deprecated",
            "sunsetting",
            "end_of_life",
            "end-of-life",
        }
        _stats_base = ApplicationComponent.query.filter(
            ~ApplicationComponent.name.ilike("(Duplicate)%", escape="\\")
        )

        def _lifecycle_count(values):
            return _stats_base.filter(
                func.lower(ApplicationComponent.lifecycle_status).in_(list(values))
            ).count()

        decommissioned_count = _lifecycle_count(_decommissioned_vals)

        # ARCH-106: Data Quality banner used to hardcode "Owner: 0/N" and
        # "Vendor: 0/N" — literal zeros, not computed from any data. Count real
        # owner-assigned and vendor-assigned applications instead.
        # An application counts as having an assigned owner when a business owner is
        # recorded on it or an application manager is assigned to it; the second half
        # is the same ownership the My Applications screens count for a user.
        owner_assigned_count = _stats_base.filter(
            has_assigned_owner(current_user.organization_id)
        ).count()
        vendor_assigned_count = _stats_base.filter(
            db.or_(
                db.and_(
                    ApplicationComponent.vendor_name.isnot(None),
                    ApplicationComponent.vendor_name != "",
                ),
                ApplicationComponent.vendor_product_id.isnot(None),
            )
        ).count()

        stats = {
            "total": portfolio_total,
            "active_portfolio": max(0, portfolio_total - decommissioned_count),
            "strategic": _lifecycle_count({"2.1 strategic"}),
            "tactical": _lifecycle_count({"2.2 tactical"}),
            "sunset_decom_pipeline": _lifecycle_count(_sunset_pipeline_vals),
            "decommissioned": decommissioned_count,
            "owner_assigned": owner_assigned_count,
            "vendor_assigned": vendor_assigned_count,
        }

        # ── 8. Currency symbol ────────────────────────────────────────────────
        try:
            from config import CurrencyConfig

            currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "\u00a3")
        except Exception:
            current_app.logger.debug(
                "CurrencyConfig unavailable, using default symbol", exc_info=True
            )
            currency_symbol = "\u00a3"

        return render_template(
            "applications/list_simple.html",
            applications=pagination.items,
            total_count=total_count,
            portfolio_total=portfolio_total,
            current_page=page,
            total_pages=total_pages,
            page_size=page_size,
            has_prev=pagination.has_prev,
            has_next=pagination.has_next,
            filters=dict(
                search=search,
                type=type_filter,
                status=status_filter,
                process_level=process_level_filter,
                capability_level=capability_level_filter,
                domain=domain_filter,
            ),
            filter_options=dict(
                component_types=component_types,
                lifecycle_statuses=lifecycle_statuses,
                process_levels=process_levels,
                capability_levels=capability_levels,
                domains=domains,
            ),
            stats=stats,
            currency_symbol=currency_symbol,
            bu_filter_active=bu_filter_active,
            bu_name=bu_name,
            show_all_override=show_all_override,
            include_decom=include_decom,
            qs_include_decom_true=qs_include_decom_true,
            qs_include_decom_false=qs_include_decom_false,
            sort=sort_param if sort_column is not None else "",
            dir=sort_dir,
        )

    except Exception as exc:
        try:
            db.session.rollback()
        except Exception as rollback_error:
            current_app.logger.warning(
                "Rollback failed while handling applications list error: %s",
                rollback_error,
                exc_info=True,
            )
        current_app.logger.error("Error loading applications list: %s", exc, exc_info=True)
        flash("Error loading applications. Please try again.", "error")
        return render_template("applications/list_simple.html", **_EMPTY_CONTEXT)


@unified_applications_bp.route("/api/list")
@login_required
def api_list():
    """
    API endpoint for applications list - Returns JSON

    ---
    tags:
      - Applications
    responses:
      200:
        description: List of all applications
        schema:
          type: array
          items:
            type: object
            properties:
              id:
                type: integer
                description: Application ID
              name:
                type: string
                description: Application name
              description:
                type: string
                description: Application description
      401:
        description: Unauthorized
      500:
        description: Internal server error
    """
    try:
        # BUG-B3 FIX: Apply a hard limit so this endpoint never returns the full
        # portfolio in one response. Callers (e.g. auto-map app selector in list.js)
        # can paginate using ?page=N&per_page=N. Default 200, max 500.
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 200, minimum=1, maximum=500), 500)
        search = request.args.get("search", "").strip()
        sort_by = request.args.get("sort", "name").strip()
        status_filter = request.args.get("status", "").strip()

        # Base query — exclude decommissioned when searching for picker use
        query = ApplicationComponent.query
        if search:
            safe_search = f"%{_escape_like(search)}%"
            query = query.filter(ApplicationComponent.name.ilike(safe_search, escape="\\"))

        if status_filter:
            query = query.filter(ApplicationComponent.lifecycle_status == status_filter)

        # Sort: "relevance" puts STRATEGIC first, then TACTICAL, then rest
        if sort_by == "relevance":
            from sqlalchemy import case

            # Normalized lifecycle values (see abacus_field_mapping.normalize_lifecycle_status);
            # raw Abacus codes kept as fallback for rows imported before normalization.
            lifecycle_order = case(
                (ApplicationComponent.lifecycle_status.in_(["operational", "2.1 STRATEGIC", "2.2 TACTICAL"]), 0),
                (ApplicationComponent.lifecycle_status.in_(["development", "testing", "planning", "1. UNDETERMINED"]), 1),
                (ApplicationComponent.lifecycle_status.in_(["deprecated", "3. SUNSET"]), 2),
                (ApplicationComponent.lifecycle_status.in_(["retired", "5. DECOMMISSIONED"]), 3),
                else_=4,
            )
            query = query.order_by(lifecycle_order, ApplicationComponent.name)
        else:
            query = query.order_by(ApplicationComponent.name)

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        apps = pagination.items

        # Enrich with capability counts for picker context
        app_ids = [app.id for app in apps]
        cap_counts = {}
        if app_ids:
            try:
                from sqlalchemy import func as sqla_func

                from app.models.application_capability import ApplicationCapabilityMapping

                cap_count_rows = (
                    db.session.query(
                        ApplicationCapabilityMapping.application_component_id,
                        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                        sqla_func.count(ApplicationCapabilityMapping.id),
                    )
                    .filter(ApplicationCapabilityMapping.application_component_id.in_(app_ids))
                    .group_by(ApplicationCapabilityMapping.application_component_id)
                    .all()
                )
                cap_counts = dict(cap_count_rows)
            except Exception as e:
                current_app.logger.debug("Capability count enrichment failed: %s", e)

        return jsonify(
            {
                "applications": [
                    {
                        "id": app.id,
                        "name": app.name,
                        "component_type": app.component_type,
                        "business_criticality": app.business_criticality,
                        "technology_stack": app.technology_stack,
                        "vendor_name": app.vendor_name,
                        "lifecycle_status": app.lifecycle_status or "",
                        "capability_count": cap_counts.get(app.id, 0),
                        "created_at": app.created_at.isoformat() if app.created_at else None,
                    }
                    for app in apps
                ],
                "total": pagination.total,
                "page": pagination.page,
                "pages": pagination.pages,
                "per_page": pagination.per_page,
            }
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception as rollback_error:
            current_app.logger.warning(
                "Rollback failed while handling applications API list error: %s",
                rollback_error,
                exc_info=True,
            )
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route("/api/table-data")
@login_required
def api_table_data():
    """API endpoint for comprehensive applications table data"""
    try:
        # Defensive rollback: ensure session is usable before running read queries.
        try:
            db.session.rollback()
        except Exception as rollback_error:
            logger.error(
                "Failed to reset SQLAlchemy session before api_table_data: %s",
                rollback_error,
                exc_info=True,
            )

        # Get pagination parameters
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 200)
        search = request.args.get("search", "").strip()
        type_filter = request.args.get("type", "").strip()
        status_filter = request.args.get("status", "").strip()

        # Build query with eager loading to prevent N + 1 issues
        # Note: capability_mappings uses ApplicationCapabilityMapping which has `business_capability` relationship
        query = ApplicationComponent.query.options(
            selectinload(ApplicationComponent.capability_mappings).selectinload(
                ApplicationCapabilityMapping.business_capability
            ),
            selectinload(ApplicationComponent.supported_processes),
        )

        # Apply filters
        if search:
            safe_search = f"%{_escape_like(search)}%"
            query = query.filter(
                ApplicationComponent.name.ilike(safe_search, escape="\\")
                | ApplicationComponent.description.ilike(safe_search, escape="\\")
                | ApplicationComponent.technology_stack.ilike(safe_search, escape="\\")
            )

        if type_filter:
            query = query.filter(ApplicationComponent.component_type == type_filter)

        if status_filter:
            query = query.filter(ApplicationComponent.lifecycle_status == status_filter)

        # Order by name
        query = query.order_by(ApplicationComponent.name)

        # Paginate
        try:
            pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        except Exception as eager_exc:
            current_app.logger.warning(
                "applications.api_table_data eager-load fallback triggered: %s",
                eager_exc,
            )
            fallback_query = ApplicationComponent.query

            if search:
                safe_search = f"%{_escape_like(search)}%"
                fallback_query = fallback_query.filter(
                    ApplicationComponent.name.ilike(safe_search, escape="\\")
                    | ApplicationComponent.description.ilike(safe_search, escape="\\")
                    | ApplicationComponent.technology_stack.ilike(safe_search, escape="\\")
                )

            if type_filter:
                fallback_query = fallback_query.filter(
                    ApplicationComponent.component_type == type_filter
                )

            if status_filter:
                fallback_query = fallback_query.filter(
                    ApplicationComponent.lifecycle_status == status_filter
                )

            pagination = fallback_query.order_by(ApplicationComponent.name).paginate(
                page=page, per_page=per_page, error_out=False
            )

        # Convert to dictionary format for table
        data = []
        for app in pagination.items:
            # Get capabilities for this application (uses eager-loaded business_capability)
            capabilities = []
            try:
                if app.capability_mappings:
                    capabilities = [
                        mapping.business_capability.name
                        if mapping.business_capability
                        else "Unknown"
                        for mapping in app.capability_mappings[:5]  # Limit to first 5
                    ]
            except Exception:
                current_app.logger.debug(
                    "Failed to load capabilities for app %s", getattr(app, "id", "?"), exc_info=True
                )
                capabilities = []

            # Get business processes for this application
            business_processes = []
            try:
                if app.supported_processes:
                    business_processes = [
                        process.name if process else "Unknown"
                        for process in app.supported_processes[:5]  # Limit to first 5
                    ]
            except Exception:
                current_app.logger.debug(
                    "Failed to load processes for app %s", getattr(app, "id", "?"), exc_info=True
                )
                business_processes = []

            app_dict = {
                "id": getattr(app, "id", None),
                "name": getattr(app, "name", "") or "",
                "description": getattr(app, "description", "") or "",
                "capabilities": capabilities,
                "business_processes": business_processes,
                "component_type": getattr(app, "component_type", "") or "",
                "application_category": getattr(app, "application_category", "") or "",
                "technology_stack": getattr(app, "technology_stack", "") or "",
                "version": getattr(app, "version", "") or "",
                "deployment_status": getattr(app, "deployment_status", None) or "",
                "business_domain": getattr(app, "business_domain", "") or "",
                "business_owner": getattr(app, "business_owner", "") or "",
                "business_criticality": getattr(app, "business_criticality", "") or "",
                "development_team": getattr(app, "development_team", "") or "",
                "user_count": getattr(app, "user_count", 0) or 0,
                "created_at": (
                    getattr(app, "created_at", None).isoformat()
                    if getattr(app, "created_at", None)
                    else None
                ),
                "updated_at": (
                    getattr(app, "updated_at", None).isoformat()
                    if getattr(app, "updated_at", None)
                    else None
                ),
            }
            data.append(app_dict)

        return jsonify(
            {
                "success": True,
                "data": data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": pagination.total,
                    "pages": pagination.pages,
                    "has_prev": pagination.has_prev,
                    "has_next": pagination.has_next,
                },
            }
        )

    except Exception as e:
        try:
            db.session.rollback()
        except Exception as rollback_error:
            current_app.logger.warning(
                "Rollback failed in api_table_data error handler: %s",
                rollback_error,
                exc_info=True,
            )
        current_app.logger.error(f"Error in api_table_data: {e}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "An internal error occurred",
                    "data": [],
                    "pagination": {
                        "page": 1,
                        "per_page": 50,
                        "total": 0,
                        "pages": 0,
                        "has_prev": False,
                        "has_next": False,
                    },
                }
            ),
            500,
        )


@unified_applications_bp.route("/dashboard")
@login_required
def dashboard():
    """Application Dashboard — redirects to the application list.

    The dashboard.html template is an application detail page that expects an
    ``app`` context variable. This route computed aggregate portfolio stats but
    the template never consumed them, causing UndefinedError on every request.
    Redirect to the list page which is the real portfolio overview.
    """
    return redirect(url_for("unified_applications.application_list"))


@unified_applications_bp.route("/model-dashboard/<string:model_name>")
@login_required
def model_dashboard(model_name):
    """Model Dashboard - Display dynamic dashboard for a specific model"""
    try:
        from app.main.dynamic_dashboards import generate_model_dashboard

        return generate_model_dashboard(model_name)
    except ImportError:
        # Fallback if dynamic_dashboards is not available
        flash(f'Model dashboard for "{model_name}" is not available', "warning")
        return redirect(url_for("unified_applications.dashboard"))
    except Exception as e:
        current_app.logger.error(f"Error generating model dashboard for {model_name}: {e}")
        flash("Error loading model dashboard. Please try again.", "error")
        return redirect(url_for("unified_applications.dashboard"))


@unified_applications_bp.route("/api/bulk-lifecycle", methods=["POST"])
@login_required
def api_bulk_lifecycle():
    """PLT-020: Bulk update lifecycle stage for selected applications.

    17 Aug 2026 QA finding: this validated against LifecycleStatus.ALL
    (planning/development/testing/pilot/production/maintenance/sunset/
    retired) — a vocabulary the "Lifecycle" bulk menu never sent. The menu
    sends this app's actual lifecycle_status values, the TOGAF-decommission
    phase scheme also used by list_simple.html's STATUS_MAP badge lookup
    (2.1 strategic, 2.2 tactical, ...). Every real click failed validation
    100% of the time — "bulk lifecycle management completely non-functional"
    as reported. Validate against the vocabulary this UI actually uses.
    """
    from app.models.application_portfolio import APPLICATION_LIFECYCLE_STAGES

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    ids = data.get("ids", [])
    lifecycle_stage = data.get("lifecycle_stage", "").strip().lower()

    if not ids:
        return jsonify({"success": False, "error": "No application IDs provided"}), 400
    if lifecycle_stage not in APPLICATION_LIFECYCLE_STAGES:
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Invalid lifecycle stage. Must be one of: {', '.join(APPLICATION_LIFECYCLE_STAGES)}",
                }
            ),
            400,
        )

    apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(ids)).all()
    apps_by_id = {a.id: a for a in apps}

    updated_count = 0
    errors = []
    for app_id in ids:
        app_obj = apps_by_id.get(app_id)
        if not app_obj:
            errors.append(f"Application {app_id} not found")
            continue
        old_stage = app_obj.lifecycle_status
        app_obj.lifecycle_status = lifecycle_stage
        updated_count += 1
        logger.info(
            "PLT-020 bulk lifecycle: app=%s (%s) changed %s -> %s by user=%s",
            app_obj.id,
            app_obj.name,
            old_stage,
            lifecycle_stage,
            getattr(current_app, "current_user_id", "unknown"),
        )

    if updated_count > 0:
        db.session.commit()

    return jsonify(
        {
            "success": updated_count > 0,
            "updated_count": updated_count,
            "errors": errors,
        }
    )


@unified_applications_bp.route("/api/bulk-assign-owner", methods=["POST"])
@login_required
def api_bulk_assign_owner():
    """PLT-021: Bulk assign owner to selected applications."""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    ids = data.get("ids", [])
    owner_name = data.get("owner_name", "").strip()

    if not ids:
        return jsonify({"success": False, "error": "No application IDs provided"}), 400
    if not owner_name:
        return jsonify({"success": False, "error": "Owner name is required"}), 400

    apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(ids)).all()
    apps_by_id = {a.id: a for a in apps}

    updated_count = 0
    errors = []
    for app_id in ids:
        app_obj = apps_by_id.get(app_id)
        if not app_obj:
            errors.append(f"Application {app_id} not found")
            continue
        old_owner = app_obj.application_owner
        app_obj.application_owner = owner_name
        updated_count += 1
        logger.info(
            "PLT-021 bulk assign owner: app=%s (%s) owner %s -> %s",
            app_obj.id,
            app_obj.name,
            old_owner,
            owner_name,
        )

    if updated_count > 0:
        db.session.commit()
        # PLT-021: audit log for bulk owner assignment
        try:
            from app.models.architecture_review_board import ARBAuditLog

            ARBAuditLog.log(
                action="bulk_assign_owner",
                entity_type="application",
                details=f"Assigned owner '{owner_name}' to {updated_count} application(s)",
                user_id=current_user.id,
            )
        except Exception:
            logger.warning("PLT-021: audit log write failed (non-blocking)")

    return jsonify(
        {
            "success": updated_count > 0,
            "updated_count": updated_count,
            "errors": errors,
        }
    )


@unified_applications_bp.route("/api/tags", methods=["GET"])
@login_required
def api_tags():
    """PLT-022: Return distinct tags across all applications for autocomplete."""
    all_tags = set()
    apps_with_tags = ApplicationComponent.query.filter(ApplicationComponent.tags.isnot(None)).all()
    for app_obj in apps_with_tags:
        if isinstance(app_obj.tags, list):
            all_tags.update(app_obj.tags)
    search = request.args.get("search", "").strip().lower()
    if search:
        all_tags = {t for t in all_tags if search in t.lower()}
    return jsonify({"tags": sorted(all_tags)})


@unified_applications_bp.route("/api/bulk-tag", methods=["POST"])
@login_required
def api_bulk_tag():
    """PLT-022: Add a tag to all selected applications."""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    ids = data.get("ids", [])
    tag = data.get("tag", "").strip()

    if not ids:
        return jsonify({"success": False, "error": "No application IDs provided"}), 400
    if not tag:
        return jsonify({"success": False, "error": "Tag is required"}), 400

    apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(ids)).all()
    apps_by_id = {a.id: a for a in apps}

    updated_count = 0
    for app_id in ids:
        app_obj = apps_by_id.get(app_id)
        if not app_obj:
            continue
        current_tags = app_obj.tags or []
        if tag not in current_tags:
            current_tags.append(tag)
            app_obj.tags = current_tags
            db.session.flag_modified(app_obj, "tags")
            updated_count += 1
            logger.info(
                "PLT-022 bulk tag: app=%s (%s) added tag '%s'", app_obj.id, app_obj.name, tag
            )

    if updated_count > 0:
        db.session.commit()

    return jsonify(
        {
            "success": True,
            "updated_count": updated_count,
            "tag": tag,
        }
    )

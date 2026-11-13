"""
Capability Map — template-rendering views and shared helpers.

Extracted from app/routes/capability_map_routes.py (lines 49-82, 1300-1338, 3016-3082).

Routes:
    - index()           GET ""  "/"
    - hierarchy()       GET "/hierarchy"
    - network()         GET "/network"
    - simple_view()     GET "/simple"
    - dashboard()       GET "/dashboard"

Helpers:
    - build_nodes_edges(catalog)   — used by mapping_routes.api_nodes_edges()
"""

from flask import abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for  # dead-code-ok
from flask_login import current_user, login_required

from app.extensions.cache import cached
from app import db

from . import capability_map


def _compute_capability_mapping_counts():
    """Real per-capability app-mapping counts, org-scoped, one bounded query.

    Returns ``None`` (not ``{}``) when the count could not be computed at
    all — e.g. no tenant context, or the query itself failed — so callers
    can tell "we asked and every capability truly has 0 mapped apps" (a
    real, dict-shaped answer, possibly all-zero) apart from "we never
    asked" (None). ``cap_to_dict`` below turns that into
    ``mapping_count: None`` on every node, which the hierarchy page's
    ``getCoverageDotClass`` renders as no dot at all rather than a
    fabricated red one, and the legend only appears when this returned a
    real dict.

    Uses ``UnifiedApplicationCapabilityMapping`` joined with
    ``UnifiedCapability`` (the canonical store PR 1 built). Only the
    tenant's own capabilities (``organization_id == org_id``) are counted;
    shared reference capabilities (``organization_id IS NULL``) are
    excluded from per-organisation mapping counts.
    """
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return None

    from sqlalchemy import func
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    try:
        rows = (
            db.session.query(
                UnifiedApplicationCapabilityMapping.unified_capability_id,
                func.count(UnifiedApplicationCapabilityMapping.id),
            )
            .join(
                UnifiedCapability,
                UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
            )
            .filter(UnifiedCapability.organization_id == org_id)
            .group_by(UnifiedApplicationCapabilityMapping.unified_capability_id)
            .all()
        )
    except Exception:
        current_app.logger.exception("Could not compute capability mapping counts")
        return None

    return dict(rows)


@capability_map.route("")
@capability_map.route("/")
@login_required
def index():
    """Main capability mapping page - new consolidated view with Map/Health/Maturity lenses"""
    from app.modules.capabilities.services.capability_count_service import (
        count_capabilities,
    )

    try:
        total_capabilities = count_capabilities()
    except Exception:
        current_app.logger.exception("Could not count business capabilities")
        total_capabilities = None

    try:
        from app.modules.account.services import onboarding_service

        # Only when nothing is on the map at all: with capabilities present, an empty result is a filter.
        no_industry_chosen = not total_capabilities and not onboarding_service.choices(current_user.organization)["industry"]
    except Exception:
        current_app.logger.exception("Could not read the industry chosen at sign-up")
        no_industry_chosen = False

    from app.services.cost_visibility import COSTS_WITHHELD, costs_visible

    return render_template(
        "capability_map/consolidated.html",
        costs_withheld=not costs_visible(current_user),
        costs_withheld_text=COSTS_WITHHELD,
        total_capabilities=total_capabilities,
        no_industry_chosen=no_industry_chosen,
    )


# Redirects from old capability pages to the consolidated page with lens/view params.
# Each old address redirects to /capability-map/ with its lens or view preset and the
# same filter; nothing returns 404 and no content is lost.


@capability_map.route("/health")
@login_required
def redirect_health():
    """Redirect old health page to consolidated page with health lens."""
    return redirect(url_for("capability_map.index", lens="health"))


@capability_map.route("/maturity")
@login_required
def redirect_maturity():
    """Redirect old maturity page to consolidated page with maturity lens."""
    return redirect(url_for("capability_map.index", lens="maturity"))


@capability_map.route("/heatmap")
@login_required
def redirect_heatmap():
    """Redirect old heat-map page to consolidated page with health lens."""
    return redirect(url_for("capability_map.index", lens="health"))


@capability_map.route("/list")
@login_required
def redirect_list():
    """Redirect old capability list page to consolidated page with list view."""
    return redirect(url_for("capability_map.index", view="list"))


@capability_map.route("/tree")
@login_required
def redirect_tree():
    """Redirect old tree page to consolidated page with list view."""
    return redirect(url_for("capability_map.index", view="list"))


@capability_map.route("/search")
@login_required
def redirect_search():
    """Redirect old search page to consolidated page with list view and search query."""
    q = request.args.get("q", "")
    return redirect(url_for("capability_map.index", view="list", search=q))


# ARCH-064: dialogs that are closed on arrival are no longer serialised into
# /capability-map/'s initial HTML. They are fetched from here the first time
# something opens one. The allow-list is explicit so the variant name — which
# arrives from the client — can never select an arbitrary template.
# What a top-level starter capability is, in its framework's words, shown instead of repeating its own name.
_FRAMEWORK_TOP_LEVEL = {"BIAN": "BIAN business area", "APQC": "APQC category"}

LAZY_MODAL_VARIANTS = frozenset(
    {
        "mapping-modal",
        "acm-mapping-modal",
        "process-mapping-modal",
        "apqc-mapping-modal",
        "archimate-mapping-modal",
    }
)


@capability_map.route("/partials/mapping-modal/<variant>")
@login_required
def mapping_modal_partial(variant):
    """Render one lazily-loaded mapping dialog as an HTML fragment."""
    if variant not in LAZY_MODAL_VARIANTS:
        abort(404)
    return render_template("capability_map/_lazy_mapping_modals.html", variant=variant)


@cached(
    ttl=300,
    key_prefix="capability_map:hierarchy",
    key_func=lambda: getattr(g, "current_org_id", None),
)
def _hierarchy_context():
    """Build the hierarchy page's template context (the query results only).

    This is what gets cached — never the rendered HTML. ``render_template``
    bakes the current request's CSP nonce into ``<script nonce="...">`` /
    ``<style nonce="...">`` attributes (see CspNonceExtension in
    app/_bootstrap/security.py). Caching that rendered string, as this used
    to, meant a cache hit on request 2 served request 1's nonce inside a
    response whose Content-Security-Policy header carried request 2's own
    (freshly generated, per-request) nonce — the two never matched, so the
    browser refused every nonce'd tag on the page. Caching only the data and
    calling ``render_template`` fresh on every request keeps the nonce and
    the header in the same response, cache hit or not.
    """
    from app.modules.capabilities.services.capability_count_service import (
        count_capabilities,
    )

    try:
        total_capabilities = count_capabilities()
    except Exception:
        current_app.logger.exception("Could not count capabilities")
        total_capabilities = None

    try:
        from app.models.unified_capability import UnifiedCapability

        capabilities = UnifiedCapability.query.order_by(
            UnifiedCapability.level, UnifiedCapability.name
        ).all()

        # Build parent lookup
        children_by_parent = {}
        for c in capabilities:
            if c.parent_capability_id:
                children_by_parent.setdefault(c.parent_capability_id, []).append(c)

        # Real per-capability app-mapping counts (or None — see the
        # function's docstring for why None is not the same as {}).
        mapping_counts = _compute_capability_mapping_counts()

        def cap_to_dict(cap):
            kids = children_by_parent.get(cap.id, [])
            mapping_count = (
                mapping_counts.get(cap.id, 0) if mapping_counts is not None else None
            )
            return {
                "name": cap.name,
                "description": cap.description or "",
                "level": cap.level,
                # Falsy values hide the badge in the template; "Unknown" and a
                # hardcoded "core" pill were fabricated labels on every row.
                "domain": cap.domain.name if cap.domain else "",
                "category": cap.category or "",
                "capability_type": getattr(cap, "capability_type", None) or "",
                "functions": [],
                "mapping_count": mapping_count,
                "children": [cap_to_dict(k) for k in kids],
            }

        # Root = L1 capabilities (no parent)
        roots = [c for c in capabilities if c.level == 1]
        catalog = {"children": [cap_to_dict(r) for r in roots]}

        return {
            "catalog": catalog,
            "total_capabilities": total_capabilities,
            "has_coverage_data": mapping_counts is not None,
            "load_error": None,
        }
    except Exception as e:
        from app import db

        db.session.rollback()
        current_app.logger.exception("Unexpected error loading hierarchy: %s", e)
        # The catalog shape is required by the Alpine tree, so it stays a dict
        # with an empty children list - no invented nodes. load_error is what
        # tells the user the tree is empty because nothing could be read.
        return {
            "catalog": {"children": []},
            "total_capabilities": total_capabilities,
            "has_coverage_data": False,
            "load_error": "The capability hierarchy could not be read.",
        }


@capability_map.route("/hierarchy")
@login_required
def hierarchy():
    """Capability hierarchy visualization — uses real UnifiedCapability data."""
    context = _hierarchy_context()
    if context["load_error"]:
        flash("Error loading the capability hierarchy. Please try again.", "error")
    return render_template(
        "capability_map/hierarchy.html",
        catalog=context["catalog"],
        total_capabilities=context["total_capabilities"],
        has_coverage_data=context["has_coverage_data"],
        load_error=context["load_error"],
    )


@capability_map.route("/network")
@login_required
def network():
    """Capability network visualization"""
    return render_template("capability_map/network.html")


@cached(
    ttl=300,
    key_prefix="capability_map:simple",
    key_func=lambda: getattr(g, "current_org_id", None),
)
def _simple_view_context():
    """Build the simple view's template context (the query results only).

    Cached separately from the render — see ``_hierarchy_context`` above for
    why: caching ``render_template``'s output bakes in that request's CSP
    nonce, which a later cache hit then serves under a different response's
    (freshly generated) nonce, and the browser blocks the mismatch.
    """
    try:
        from app.models.unified_capability import UnifiedCapability

        capabilities = UnifiedCapability.query.order_by(
            UnifiedCapability.level, UnifiedCapability.name
        ).all()

        from app.services.capability_type import (
            CAPABILITY_TYPE_HELP,
            CAPABILITY_TYPE_LABELS,
            CAPABILITY_TYPES,
            normalise_capability_type,
        )

        def capability_row(cap):
            fact_sheet_id = None
            if cap.source_table == "business_capability" and str(cap.source_id).isdigit():
                fact_sheet_id = int(cap.source_id)
            return {
                "name": cap.name,
                "description": cap.description or "",
                "level": cap.level,
                "domain": cap.domain.name if cap.domain else "",
                "fact_sheet_id": fact_sheet_id,
            }

        by_type = {key: [] for key in CAPABILITY_TYPES}
        unclassified = []
        for cap in capabilities:
            key = normalise_capability_type(cap.capability_type)
            (by_type[key] if key else unclassified).append(capability_row(cap))

        capability_groups = [
            {
                "key": key,
                "name": CAPABILITY_TYPE_LABELS[key],
                "description": CAPABILITY_TYPE_HELP[key],
                "capabilities": by_type[key],
            }
            for key in CAPABILITY_TYPES
        ]
        if unclassified:
            capability_groups.append(
                {
                    "key": "unclassified",
                    "name": "Not yet classified",
                    "description": "No one has said whether these are strategic, operational or supporting.",
                    "capabilities": unclassified,
                }
            )

        roots = [c for c in capabilities if c.level == 1]
        levels = [c.level for c in capabilities if c.level is not None]
        domains = {c.domain.name for c in capabilities if c.domain}

        stats = {
            "total": len(capabilities),
            "l1_count": len(roots),
            "max_depth": max(levels) if levels else None,
            "domain_count": len(domains),
        }

        return {
            "capability_groups": capability_groups,
            "stats": stats,
            "load_error": None,
        }
    except Exception:
        from app import db

        db.session.rollback()
        current_app.logger.exception("Could not load the simple capability view")
        return {
            "capability_groups": [],
            "stats": {"total": None, "l1_count": None, "max_depth": None, "domain_count": None},
            "load_error": "The capability list could not be read.",
        }


@capability_map.route("/simple")
@login_required
def simple_view():
    """Simple flat view of capabilities — real UnifiedCapability data.

    Was previously a 612-line static template with no context at all: a
    hardcoded "38 capabilities / 124 functions / 11 domains" and a fictional
    "Digital Application Platform" taxonomy, shown identically to every
    tenant. Rebuilt on the same query pattern as ``hierarchy()`` above —
    level-1 roots with their direct children — but flattened for a page
    that is meant to be simple, not a recursive tree.
    """
    context = _simple_view_context()
    if context["load_error"]:
        flash("Error loading the capability view. Please try again.", "error")
    return render_template(
        "capability_map/simple.html",
        capability_groups=context["capability_groups"],
        stats=context["stats"],
        load_error=context["load_error"],
    )


@cached(
    ttl=300,
    key_prefix="capability_map:dashboard",
    key_func=lambda: getattr(g, "current_org_id", None),
)
def _dashboard_context():
    """Build the dashboard's template context (the query results only).

    Cached separately from the render — see ``_hierarchy_context`` above for
    why: caching ``render_template``'s output bakes in that request's CSP
    nonce, which a later cache hit then serves under a different response's
    (freshly generated) nonce, and the browser blocks the mismatch. Returns
    ``None`` on failure so the route can fall back to the (uncached)
    error template exactly as before.
    """
    try:
        # Get statistics
        from app.services.application_capability_catalog import (
            ApplicationCapabilityCatalogService,
        )

        validation = ApplicationCapabilityCatalogService.validate_capability_structure()
        catalog = ApplicationCapabilityCatalogService.get_catalog_hierarchy()

        # Get application statistics
        from app.models.application_layer import ApplicationComponent

        applications = ApplicationComponent.query.count()
        from app.models.business_capabilities import ApplicationCapabilityCoverage

        mappings = ApplicationCapabilityCoverage.query.count()

        return {
            "catalog": catalog,
            "validation": validation,
            "app_count": applications,
            "mapping_count": mappings,
        }
    except Exception as e:
        current_app.logger.error(f"Error loading capability map: {e}")
        return None


@capability_map.route("/dashboard")
@login_required
def dashboard():
    """Comprehensive dashboard with multiple visualization types"""
    context = _dashboard_context()
    if context is None:
        return render_template(
            "capability_map/error.html",
            error="An unexpected error occurred. Please try again.",
        )
    return render_template(
        "capability_map/index.html",
        catalog=context["catalog"],
        validation=context["validation"],
        app_count=context["app_count"],
        mapping_count=context["mapping_count"],
    )


# ---------------------------------------------------------------------------
# Helper: build_nodes_edges — used by mapping_routes.api_nodes_edges()
# ---------------------------------------------------------------------------


def build_nodes_edges(catalog):
    """Build nodes and edges for network visualization"""
    nodes = []
    edges = []
    node_id = 0

    # catalog is a tree root dict with "children"; flatten to list of capabilities
    capabilities = catalog.get("children", []) if isinstance(catalog, dict) else catalog

    # Add capability nodes
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        nodes.append(
            {
                "id": node_id,
                "name": capability.get("name", "Unknown"),
                "category": capability.get("category", "capability"),
                "level": capability.get("level", 1),
                "domain": capability.get("domain", "Unknown"),
                "type": capability.get("type", "capability"),
            }
        )
        cap_node_id = node_id
        node_id += 1

        # Add function nodes for this capability
        for function in capability.get("functions", []):
            if not isinstance(function, dict):
                continue
            nodes.append(
                {
                    "id": node_id,
                    "name": function.get("name", "Unknown"),
                    "category": "function",
                    "level": function.get("level", 2),
                    "domain": capability.get("domain", "Unknown"),
                    "type": "function",
                }
            )
            # Edge from capability to its function
            edges.append({"source": cap_node_id, "target": node_id})
            node_id += 1

        # Recurse into children
        for child in capability.get("children", []):
            if not isinstance(child, dict):
                continue
            nodes.append(
                {
                    "id": node_id,
                    "name": child.get("name", "Unknown"),
                    "category": child.get("category", "capability"),
                    "level": child.get("level", 2),
                    "domain": child.get("domain", capability.get("domain", "Unknown")),
                    "type": child.get("type", "capability"),
                }
            )
            edges.append({"source": cap_node_id, "target": node_id})
            node_id += 1

    return nodes, edges


@cached(
    ttl=300,
    key_prefix="capability_map:api_hierarchy",
    key_func=lambda: getattr(g, "current_org_id", None),
    skip_store=lambda result: bool(result.get("load_error")),
)
def _api_hierarchy_context():
    """Build the capability hierarchy for the new capability map page.

    Returns domains with their capabilities, including health scores,
    maturity levels, application counts, and ownership.
    """
    from app.modules.capabilities.services.capability_count_service import (
        count_capabilities,
    )
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.application_layer import ApplicationComponent
    from sqlalchemy import func

    try:
        total_capabilities = count_capabilities()
    except Exception:
        current_app.logger.exception("Could not count capabilities")
        total_capabilities = None

    try:
        org_id = getattr(g, "current_org_id", None)

        from app import db

        # Get all capabilities visible to this organisation
        capabilities = UnifiedCapability.query.order_by(
            UnifiedCapability.level, UnifiedCapability.name
        ).all()

        # Build parent lookup
        children_by_parent = {}
        for c in capabilities:
            if c.parent_capability_id:
                children_by_parent.setdefault(c.parent_capability_id, []).append(c)

        # Capabilities that came from the starter model chosen at sign-up, by the id of the row they are projected from.
        from app.models.business_capabilities import BusinessCapability

        starter_sources = {
            str(row_id): source
            for row_id, source in db.session.query(
                BusinessCapability.id, BusinessCapability.discovery_source
            ).filter(
                BusinessCapability.organization_id == org_id,
                BusinessCapability.discovery_source.like("starter:%"),
            ).all()
        } if org_id is not None else {}

        # Get application mapping counts
        mapping_counts = {}
        if org_id is not None:
            try:
                rows = (
                    db.session.query(
                        UnifiedApplicationCapabilityMapping.unified_capability_id,
                        func.count(UnifiedApplicationCapabilityMapping.id),
                    )
                    .join(
                        UnifiedCapability,
                        UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
                    )
                    .filter(UnifiedCapability.organization_id == org_id)
                    .group_by(UnifiedApplicationCapabilityMapping.unified_capability_id)
                    .all()
                )
                mapping_counts = dict(rows)
            except Exception:
                current_app.logger.exception("Could not compute capability mapping counts")

        # Get application details for each capability
        app_details = {}
        if org_id is not None and mapping_counts:
            cap_ids = list(mapping_counts.keys())
            # Scoped through the capability's organisation, as the count above is: the mapping
            # table carries no organisation of its own.
            mappings = (
                UnifiedApplicationCapabilityMapping.query
                .join(
                    UnifiedCapability,
                    UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
                )
                .filter(
                    UnifiedApplicationCapabilityMapping.unified_capability_id.in_(cap_ids),
                    UnifiedCapability.organization_id == org_id,
                )
                .all()
            )

            app_ids = list(set(m.application_component_id for m in mappings))
            apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(app_ids)
            ).all() if app_ids else []
            app_map = {a.id: a for a in apps}

            # The application page link is left out, not fatal, when that module did not register.
            can_link = "unified_applications.application_detail" in current_app.view_functions

            for m in mappings:
                app = app_map.get(m.application_component_id)
                if app:
                    app_details.setdefault(m.unified_capability_id, []).append({
                        "id": app.id,
                        "name": app.name,
                        "lifecycleStatus": app.lifecycle_status,
                        "supportLevel": m.support_level,
                        "coveragePercentage": m.coverage_percentage,
                        "url": url_for('unified_applications.application_detail', id=app.id) if can_link else None,
                    })

            # One count, one list: coverage is the number of applications listed, which went
            # through the application's own tenant filter, so a mapping row pointing at another
            # organisation's application is neither counted nor revealed.
            mapping_counts = {
                cap_id: len(app_details.get(cap_id, [])) for cap_id in mapping_counts
            }

        # Group capabilities by domain. A capability starts a branch when it is a top-level one
        # or when its parent is not among those this organisation can see: a sub-level capability
        # with no parent is listed, not dropped.
        visible_ids = {c.id for c in capabilities}
        domains_map = {}
        for cap in capabilities:
            if cap.level != 1 and cap.parent_capability_id in visible_ids:
                continue
            domain_name = cap.domain.name if cap.domain else "Uncategorised"
            domain_code = cap.domain.code if cap.domain else "UNCAT"
            if domain_name not in domains_map:
                domains_map[domain_name] = {
                    "id": cap.domain.id if cap.domain else 0,
                    "name": domain_name,
                    "code": domain_code,
                    "capabilities": []
                }
            domains_map[domain_name]["capabilities"].append(cap)

        def not_assessed(cap, mapping_count):
            """A starter capability nobody has rated or mapped an application to: it has no health or
            coverage to show yet, and a figure for it would be invented."""
            if cap.source_table != "business_capability" or not starter_sources.get(str(cap.source_id)):
                return False
            recorded = (
                cap.current_maturity_level, cap.target_maturity_level, cap.reliability_score,
                cap.performance_score, cap.security_score, cap.scalability_score,
            )
            return all(value is None for value in recorded) and not mapping_count

        # Health is the capability health computation, the one the capability health page shows.
        # A capability projected from a business capability takes that capability's figure; one
        # with nothing recorded has none (None), and a capability not projected from one has none.
        from app.modules.capabilities.services.capability_health_service import CapabilityHealthService

        health_by_business_capability = (
            {
                row["id"]: row["score"]
                for row in CapabilityHealthService().get_capability_health_metrics()["health_by_capability"]
            }
            if org_id is not None else {}
        )

        # Owners are read from the owner record, the same answer the risk assessment gives.
        from app.services.risk_service import capability_owner_names

        owner_names = (
            capability_owner_names([c.id for c in capabilities], org_id) if org_id is not None else {}
        )

        def compute_health_score(cap):
            if cap.source_table != "business_capability" or not str(cap.source_id or "").isdigit():
                return None
            return health_by_business_capability.get(int(cap.source_id))

        def cap_to_detail(cap):
            kids = children_by_parent.get(cap.id, [])
            mapping_count = mapping_counts.get(cap.id, 0)
            apps = app_details.get(cap.id, [])

            health_score = compute_health_score(cap)

            # Six lens measures
            # Maturity: current and target levels with gap
            maturity_current = cap.current_maturity_level
            maturity_target = cap.target_maturity_level
            maturity_gap_val = None
            if maturity_current is not None and maturity_target is not None:
                maturity_gap_val = maturity_target - maturity_current

            # Importance: strategic importance rating
            importance = cap.strategic_importance

            # Investment: annual cost and investment required
            investment_now = cap.annual_cost
            investment_planned = cap.investment_required

            # Control coverage: security score
            control_coverage = cap.security_score

            # Application coverage: count of mapped applications
            application_coverage = mapping_count

            domain_name = cap.domain.name if cap.domain else "Uncategorised"
            starter_model = (
                starter_sources.get(str(cap.source_id), "")[len("starter:"):] or None
                if cap.source_table == "business_capability" else None
            )
            return {
                "id": cap.id,
                "name": cap.name,
                "description": cap.description or "",
                "level": cap.level,
                "domainName": domain_name,
                # What a card or panel shows beside the name: never the capability's own name.
                "domainLabel": (
                    _FRAMEWORK_TOP_LEVEL.get(starter_model, "Top-level capability")
                    if domain_name == cap.name else domain_name
                ),
                "domainCode": cap.domain.code if cap.domain else "UNCAT",
                "type": cap.capability_type or cap.category or "business",
                "code": cap.code,
                "starterModel": starter_model,
                "owner": owner_names.get(cap.id),
                "businessOwner": cap.business_owner,
                "itOwner": cap.it_owner,
                "capabilityOwner": cap.capability_owner,
                "productManager": cap.product_manager,
                "strategicImportance": cap.strategic_importance,
                "businessCriticality": cap.business_criticality,
                "isCoreDifferentiator": cap.is_core_differentiator,
                # Maturity lens
                "currentMaturity": maturity_current,
                "targetMaturity": maturity_target,
                "maturityGap": maturity_gap_val,
                "maturityAssessmentDate": cap.maturity_assessment_date.isoformat() if cap.maturity_assessment_date else None,
                "maturityAssessmentNotes": cap.maturity_assessment_notes,
                # Importance lens
                "importance": importance,
                # Investment lens
                "investmentNow": investment_now,
                "investmentPlanned": investment_planned,
                # Health lens
                "healthScore": health_score,
                "notAssessed": not_assessed(cap, mapping_count),
                "reliabilityScore": cap.reliability_score,
                "performanceScore": cap.performance_score,
                "securityScore": cap.security_score,
                "scalabilityScore": cap.scalability_score,
                # Control coverage lens
                "controlCoverage": control_coverage,
                # Application coverage lens
                "applicationCoverage": application_coverage,
                "applicationCount": mapping_count,
                "applications": apps,
                "children": [cap_to_detail(k) for k in kids]
            }

        # Build domain list with L1 capabilities and their children
        domains = []
        for domain_name, domain_data in sorted(domains_map.items()):
            l1_caps = domain_data["capabilities"]
            domain_caps = []
            for cap in l1_caps:
                domain_caps.append(cap_to_detail(cap))

            domains.append({
                "id": domain_data["id"],
                "name": domain_data["name"],
                "code": domain_data["code"],
                "capabilities": domain_caps
            })

        return {
            "domains": domains,
            "total_capabilities": total_capabilities,
            "load_error": None,
        }
    except Exception as e:
        from app import db
        db.session.rollback()
        current_app.logger.exception("Unexpected error loading API hierarchy: %s", e)
        return {
            "domains": [],
            "total_capabilities": total_capabilities,
            "load_error": "The capability hierarchy could not be read.",
        }


@capability_map.route("/api/capabilities/hierarchy")
@login_required
def api_capabilities_hierarchy():
    """API endpoint for the new capability map page."""
    from app.services.cost_visibility import COSTS_WITHHELD, costs_visible, withhold_costs

    context = _api_hierarchy_context()
    if context["load_error"]:
        return jsonify({"error": context["load_error"], "domains": []}), 500
    if not costs_visible(current_user):
        # The context is stored for the organisation, whoever asked first: the money is left out on the way
        # out, never in the stored copy, so the person it is withheld from and the person it is shown to
        # each get their own answer from the same store.
        context = withhold_costs(context, keys=("investmentNow", "investmentPlanned"))
        context["costs_withheld"] = COSTS_WITHHELD
    return jsonify(context)

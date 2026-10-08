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

from flask import abort, current_app, flash, g, render_template  # dead-code-ok
from flask_login import login_required

from app.extensions.cache import cached

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

    from app import db
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
    """Main capability mapping page"""
    from app.modules.capabilities.services.capability_count_service import (
        count_capabilities,
    )

    try:
        total_capabilities = count_capabilities()
    except Exception:
        current_app.logger.exception("Could not count business capabilities")
        total_capabilities = None

    return render_template("capability_map/index.html", total_capabilities=total_capabilities)


# ARCH-064: dialogs that are closed on arrival are no longer serialised into
# /capability-map/'s initial HTML. They are fetched from here the first time
# something opens one. The allow-list is explicit so the variant name — which
# arrives from the client — can never select an arbitrary template.
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

        children_by_parent = {}
        for c in capabilities:
            if c.parent_capability_id:
                children_by_parent.setdefault(c.parent_capability_id, []).append(c)

        def child_to_dict(cap):
            return {
                "name": cap.name,
                "description": cap.description or "",
                "level": cap.level,
                "domain": cap.domain.name if cap.domain else "",
            }

        roots = [c for c in capabilities if c.level == 1]
        capability_groups = [
            {
                "name": root.name,
                "description": root.description or "",
                "level": root.level,
                "domain": root.domain.name if root.domain else "",
                "children": [
                    child_to_dict(child) for child in children_by_parent.get(root.id, [])
                ],
            }
            for root in roots
        ]

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

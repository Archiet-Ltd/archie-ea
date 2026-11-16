"""
Unified / manufacturing / process domain management routes.

Extracted from capability_map_routes.py (lines 3081-3417).
Routes registered on the shared ``capability_map`` blueprint.
"""

from flask import current_app, g, jsonify  # dead-code-ok
from flask_login import login_required

from app import db

from . import capability_map


# =============================================================================
# Business Domain Endpoints (Unified Tab)
# =============================================================================


@capability_map.route("/api/unified/domains")
@login_required
def api_unified_domains():
    """
    Get business domain statistics for the Unified Capability tab.

    Queries BusinessCapability directly, using level-1 capabilities as domains.
    Falls back to UnifiedCapability/BusinessDomain if no BusinessCapability data exists.

    ---
    tags:
      - Business Capabilities
    responses:
      200:
        description: List of business domains with coverage statistics
    """
    try:
        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.business_capabilities import BusinessCapability
        from app.modules.capabilities.services.capability_count_service import (
            capability_scope,
            count_business_capabilities,
        )

        # Use BusinessCapability (real imported data) as the primary source.
        # count_business_capabilities() is the single counting function shared
        # with /capability-map/hierarchy so the two pages can't disagree.
        total_capabilities = count_business_capabilities()

        # ApplicationCapabilityMapping is not TenantMixin (see the model), and
        # its own organization_id column is NULL on every row in production
        # (added nullable by reconcile-schema, never backfilled) — a
        # predicate on that column would silently report zero mappings for
        # every org. Scope via the TenantMixin FK parent BusinessCapability
        # instead (joins below). See e622d36 / rationalization_scoring_service.py.
        # Without scoping at all, mapped_count / all_mapped_cap_ids would
        # aggregate across every tenant, inflating this org's coverage past
        # 100% and leaking other orgs' mapping volume into the Capability
        # Map's default tab.
        org_id = getattr(g, "current_org_id", None)
        if org_id is None:
            current_app.logger.warning(
                "api_unified_domains called with no org context; failing closed to zero mappings"
            )

        if total_capabilities > 0:
            # Level-1 capabilities serve as domains
            domains = (
                BusinessCapability.query.filter_by(level=1)
                .order_by(BusinessCapability.name)
                .all()
            )

            # Count capabilities with at least one app mapping (this org only)
            if org_id is not None:
                mapped_count = (
                    db.session.query(ApplicationCapabilityMapping.business_capability_id)
                    .join(
                        BusinessCapability,
                        ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
                    )
                    .filter(BusinessCapability.organization_id == org_id)
                    .distinct()
                    .count()
                )
            else:
                mapped_count = 0

            coverage = round(
                (mapped_count / total_capabilities * 100) if total_capabilities > 0 else 0, 1
            )

            # Batch-prefetch all capabilities and mappings to avoid N+1 queries
            all_biz_caps = BusinessCapability.query.all()
            children_by_parent = {}
            for bc in all_biz_caps:
                if bc.parent_capability_id is not None:
                    children_by_parent.setdefault(bc.parent_capability_id, []).append(bc)

            if org_id is not None:
                all_mapped_cap_ids = set(
                    row[0] for row in
                    db.session.query(ApplicationCapabilityMapping.business_capability_id)
                    .join(
                        BusinessCapability,
                        ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
                    )
                    .filter(BusinessCapability.organization_id == org_id)
                    .distinct()
                    .all()
                )
            else:
                all_mapped_cap_ids = set()

            domain_list = []
            for domain in domains:
                # Get all children (capabilities under this domain) from prefetched data
                children = children_by_parent.get(domain.id, [])

                # Include the domain itself in count if it has no children
                all_cap_ids = [domain.id] + [c.id for c in children]
                # Also include grandchildren from prefetched data
                for child in children:
                    grandchildren = children_by_parent.get(child.id, [])
                    all_cap_ids.extend([gc.id for gc in grandchildren])

                cap_count = len(all_cap_ids) - 1  # Exclude domain itself from count

                # Count mapped capabilities in this domain using prefetched set
                domain_mapped = len(set(all_cap_ids) & all_mapped_cap_ids) if all_cap_ids else 0

                domain_coverage = round(
                    (domain_mapped / cap_count * 100) if cap_count > 0 else 0, 1
                )

                # Count by level
                l2_count = len([c for c in children if c.level == 2])
                l3_count = sum(
                    len(children_by_parent.get(c.id, []))
                    for c in children
                )

                domain_list.append(
                    {
                        "id": domain.id,
                        "code": domain.code or "",
                        "name": domain.name,
                        "description": domain.description or "",
                        "capability_count": cap_count,
                        "mapped_count": domain_mapped,
                        "coverage": domain_coverage,
                        "l1_count": 1,
                        "l2_count": l2_count,
                        "l3_count": l3_count,
                    }
                )

            return jsonify(
                {
                    "success": True,
                    "domain_count": len(domains),
                    "total_capabilities": total_capabilities,
                    "own_capabilities": capability_scope()["own"],
                    "mapped_count": mapped_count,
                    "coverage": coverage,
                    "domains": domain_list,
                }
            )

        # No BusinessCapability data — return empty
        return jsonify(
            {
                "success": True,
                "domain_count": 0,
                "total_capabilities": 0,
                "mapped_count": 0,
                "coverage": 0,
                "domains": [],
            }
        )

    except Exception as e:
        current_app.logger.exception(f"Error loading business domains: {e}")
        return jsonify({"success": False, "error": "Could not load business domains"}), 500


# =============================================================================
# Manufacturing Domain Endpoints
# =============================================================================


@capability_map.route("/api/manufacturing/domains")
@login_required
def api_manufacturing_domains():
    """
    Get manufacturing domain statistics for the Manufacturing Capability tab.

    ---
    tags:
      - Manufacturing
    responses:
      200:
        description: Manufacturing domain statistics
    """
    try:
        from app.models.manufacturing_capability import ManufacturingCapability

        # Get all manufacturing capabilities
        capabilities = ManufacturingCapability.query.all()
        total_count = len(capabilities)

        # Group by domain
        domain_stats = {
            "production": {"count": 0, "mapped": 0, "oee_sum": 0, "oee_count": 0},
            "supply_chain": {"count": 0, "mapped": 0, "oee_sum": 0, "oee_count": 0},
            "quality": {"count": 0, "mapped": 0, "oee_sum": 0, "oee_count": 0},
            "maintenance": {"count": 0, "mapped": 0, "oee_sum": 0, "oee_count": 0},
            "engineering": {"count": 0, "mapped": 0, "oee_sum": 0, "oee_count": 0},
        }

        total_oee_sum = 0
        total_oee_count = 0
        mapped_count = 0

        for cap in capabilities:
            domain = cap.manufacturing_domain or "production"
            if domain in domain_stats:
                domain_stats[domain]["count"] += 1

                # Check if mapped (has unified_capability_id)
                if cap.unified_capability_id:
                    domain_stats[domain]["mapped"] += 1
                    mapped_count += 1

                # Track OEE if available
                if cap.oee_current:
                    domain_stats[domain]["oee_sum"] += cap.oee_current
                    domain_stats[domain]["oee_count"] += 1
                    total_oee_sum += cap.oee_current
                    total_oee_count += 1

        # Calculate coverages and OEE
        domains = {}
        for domain, stats in domain_stats.items():
            coverage = round(
                (stats["mapped"] / stats["count"] * 100) if stats["count"] > 0 else 0, 1
            )
            avg_oee = round(stats["oee_sum"] / stats["oee_count"]) if stats["oee_count"] > 0 else 0
            domains[domain] = {
                "count": stats["count"],
                "mapped": stats["mapped"],
                "coverage": coverage,
                "avg_oee": avg_oee,
            }

        overall_coverage = round((mapped_count / total_count * 100) if total_count > 0 else 0, 1)
        overall_oee = round(total_oee_sum / total_oee_count) if total_oee_count > 0 else 0

        return jsonify(
            {
                "success": True,
                "total_capabilities": total_count,
                "mapped_count": mapped_count,
                "coverage": overall_coverage,
                "avg_oee": overall_oee,
                "domains": domains,
            }
        )

    except Exception as e:
        current_app.logger.exception(f"Error loading manufacturing domains: {e}")
        return jsonify(
            {"success": False, "error": "Could not load manufacturing domains"}
        ), 500


# =============================================================================
# Process Category Endpoints
# =============================================================================


@capability_map.route("/api/process/categories")
@login_required
def api_process_categories():
    """
    Get APQC Process Category statistics for the Process Gaps tab.

    ---
    tags:
      - Process
      - APQC
    responses:
      200:
        description: APQC process category statistics
    """
    try:
        from app.models.apqc_process import APQCProcess

        # Get all processes
        processes = APQCProcess.query.all()

        # Initialize categories (1.0 through 13.0)
        categories = {}
        for i in range(1, 14):
            categories[f"{i}.0"] = {"count": 0, "mapped": 0, "coverage": 0}

        for proc in processes:
            # Determine category from process_code (e.g., "1.2.3" -> "1.0")
            if proc.process_code:
                parts = proc.process_code.split(".")
                if parts and parts[0].isdigit():
                    cat_num = int(parts[0])
                    if 1 <= cat_num <= 13:
                        cat_key = f"{cat_num}.0"
                        categories[cat_key]["count"] += 1

                        # Check if process has application coverage
                        # Using has_application_mapping or checking related apps
                        has_mapping = getattr(proc, "has_application_mapping", False)
                        if has_mapping or (hasattr(proc, "applications") and proc.applications):
                            categories[cat_key]["mapped"] += 1

        # Calculate coverage percentages
        for cat_key, data in categories.items():
            if data["count"] > 0:
                data["coverage"] = round((data["mapped"] / data["count"]) * 100, 1)

        return jsonify({"success": True, "categories": categories})

    except Exception as e:
        current_app.logger.exception(f"Error loading process categories: {e}")
        return jsonify({"success": False, "error": "Could not load process categories"}), 500

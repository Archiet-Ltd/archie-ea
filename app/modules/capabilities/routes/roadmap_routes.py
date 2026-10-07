"""
Capability Map — Roadmap gap analysis & work packages.

Extracted from app/routes/capability_map_routes.py (lines 2625-2737, 4272-5317).

Routes (18):
    - api_roadmap_capabilities()                         GET "/api/roadmap/capabilities"
    - api_roadmap_gaps()                                 GET "/api/roadmap/gaps"
    - api_roadmap_create_gap()                           POST "/api/roadmap/gaps"
    - api_roadmap_archimate_gaps()                       GET "/api/roadmap/archimate-gaps"
    - api_roadmap_convert_gaps()                         POST "/api/roadmap/gaps/convert"
    - api_roadmap_add_from_capability()                  POST "/api/roadmap/gaps/add-from-capability"
    - api_roadmap_get_gap(gap_id)                        GET "/api/roadmap/gaps/<int:gap_id>"
    - api_roadmap_update_gap(gap_id)                     PUT "/api/roadmap/gaps/<int:gap_id>"
    - api_roadmap_delete_gap(gap_id)                     DELETE "/api/roadmap/gaps/<int:gap_id>"
    - api_roadmap_create_work_package(gap_id)            POST "/api/roadmap/gaps/<int:gap_id>/work-packages"
    - api_roadmap_work_packages()                        GET "/api/roadmap/work-packages"
    - api_roadmap_create_standalone_work_package()       POST "/api/roadmap/work-packages"
    - api_roadmap_get_work_package(wp_id)                GET "/api/roadmap/work-packages/<int:wp_id>"
    - api_roadmap_update_work_package(wp_id)             PUT "/api/roadmap/work-packages/<int:wp_id>"
    - api_roadmap_delete_work_package(wp_id)             DELETE "/api/roadmap/work-packages/<int:wp_id>"
    - api_roadmap_create_child_work_package(wp_id)       POST "/api/roadmap/work-packages/<int:wp_id>/children"

Helpers (3):
    - _calculate_gap_priority(gap_types, apps, cap, today)
    - _get_gap_start_date(gap_types, apps, priority)
    - _get_gap_end_date(gap_types, apps, priority)
"""

from app.services.archimate_backbone import sync_archimate_element
from datetime import datetime

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.services.rate_limiter import rate_limit

from app import db

from . import capability_map
import logging
logger = logging.getLogger(__name__)


@capability_map.route("/api/roadmap/capabilities")
@login_required
@rate_limit(60, "1m")
def api_roadmap_capabilities():
    """
    API endpoint to get capabilities grouped by roadmap priority.

    Returns capabilities organized for roadmap visualization with timeline data.
    """
    try:
        from app.models.business_capabilities import (
            ApplicationCapabilityCoverage,
            BusinessCapability,
        )
        from app.models.unified_capability import UnifiedCapability

        capabilities = BusinessCapability.query.all()
        mappings = ApplicationCapabilityCoverage.query.all()
        mapped_cap_ids = {m.capability_id for m in mappings}

        # D-R7-2: this is the roadmap screen's own data feed, sitting in the
        # same file/blueprint as api_roadmap_detect_gaps() (the "Detect gaps"
        # button), which was already fixed under D-R5-1 to read maturity
        # through the single authority accessor rather than the SOURCE
        # columns. Reading `cap.current_maturity_level` / `.target_maturity_level`
        # / `.maturity_gap` here (the source, per ADR 0008 rule 3) instead of
        # the authority let the two co-located endpoints disagree for up to
        # the 15-minute raw-SQL-write-to-projection window: the list shows
        # "has a gap" while the button reports zero gaps found. Fetch the
        # authority for every candidate ONCE, batched, same as the sibling
        # endpoint.
        org_id = capabilities[0].organization_id if capabilities else None
        maturity_map = UnifiedCapability.maturity_for_sources(
            "business_capability", [c.id for c in capabilities], organization_id=org_id
        )

        # Group by roadmap priority
        roadmap_groups = {
            "immediate": [],  # Priority 1 - Now
            "short_term": [],  # Priority 2 - 3 - 6 months
            "medium_term": [],  # Priority 3 - 6 - 12 months
            "long_term": [],  # Priority 4 - 12+ months
            "unplanned": [],  # No priority set
        }

        for cap in capabilities:
            domain = None  # BusinessCapability uses string business_domain

            # Calculate gap status — D-R7-2: read through the same authority
            # accessor as api_roadmap_detect_gaps(), not the source columns.
            maturity = maturity_map.get(str(cap.id))
            if maturity is None or maturity.get("reason_code") == "no_maturity_recorded":
                cap_current_maturity = None
                cap_target_maturity = None
                cap_maturity_gap = None
            else:
                cap_current_maturity = maturity["current_maturity_level"]
                cap_target_maturity = maturity["target_maturity_level"]
                cap_maturity_gap = (
                    (cap_target_maturity - cap_current_maturity)
                    if cap_current_maturity is not None and cap_target_maturity is not None
                    else None
                )

            is_mapped = cap.id in mapped_cap_ids
            has_maturity_gap = (cap_maturity_gap or 0) > 0

            # Get investment priority from domain if available
            domain_investment_priority = (
                domain.investment_priority
                if domain and hasattr(domain, "investment_priority")
                else None
            )

            cap_data = {
                "id": str(cap.id),
                "name": cap.name,
                "code": cap.code,
                "level": cap.level,
                "domain": domain.name if domain else "Unknown",
                "domain_code": domain.code if domain else "UNK",
                "strategic_importance": cap.strategic_importance,
                "business_criticality": getattr(cap, "business_criticality", None)
                or getattr(cap, "strategic_importance", None),
                "is_core_differentiator": getattr(cap, "is_core_differentiator", None),
                "current_maturity": cap_current_maturity,
                "target_maturity": cap_target_maturity,
                "maturity_gap": cap_maturity_gap,
                "is_mapped": is_mapped,
                "has_maturity_gap": has_maturity_gap,
                "investment_priority": domain_investment_priority,
                "annual_cost": getattr(cap, "annual_cost", None),
                "status": getattr(cap, "status", None),
            }

            # Assign to roadmap group based on priority (capability roadmap_priority or domain investment_priority)
            priority = getattr(cap, "roadmap_priority", None) or domain_investment_priority

            if priority in ["critical", "immediate", "1"]:
                roadmap_groups["immediate"].append(cap_data)
            elif priority in ["high", "short_term", "2"]:
                roadmap_groups["short_term"].append(cap_data)
            elif priority in ["medium", "medium_term", "3"]:
                roadmap_groups["medium_term"].append(cap_data)
            elif priority in ["low", "long_term", "4"]:
                roadmap_groups["long_term"].append(cap_data)
            else:
                roadmap_groups["unplanned"].append(cap_data)

        # Sort each group by strategic importance
        importance_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        for group in roadmap_groups.values():
            group.sort(key=lambda x: importance_order.get(x["strategic_importance"], 4))

        # Calculate statistics
        total_capabilities = len(capabilities)
        planned_capabilities = total_capabilities - len(roadmap_groups["unplanned"])

        return jsonify(
            {
                "roadmap": roadmap_groups,
                "statistics": {
                    "total_capabilities": total_capabilities,
                    "planned_capabilities": planned_capabilities,
                    "unplanned_capabilities": len(roadmap_groups["unplanned"]),
                    "planning_coverage": round(
                        (planned_capabilities / total_capabilities * 100)
                        if total_capabilities > 0
                        else 0,
                        1,
                    ),
                    "by_phase": {
                        "immediate": len(roadmap_groups["immediate"]),
                        "short_term": len(roadmap_groups["short_term"]),
                        "medium_term": len(roadmap_groups["medium_term"]),
                        "long_term": len(roadmap_groups["long_term"]),
                    },
                },
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting roadmap capabilities: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# =============================================================================
# Roadmap Gap Analysis Endpoints
# =============================================================================


@capability_map.route("/api/roadmap/gaps")
@login_required
@rate_limit(60, "1m")
def api_roadmap_gaps():
    """
    Get comprehensive gap analysis for the roadmap view.
    Identifies four types of gaps:
    - Coverage gaps: Capabilities with 0 applications
    - Quality gaps: Capabilities with only tactical/low-value apps (need strategic)
    - Retirement gaps: Capabilities where apps are marked for retirement
    - Modernization gaps: Capabilities with apps needing renewal/upgrade

    ---
    tags:
      - Roadmap
      - Gap Analysis
    parameters:
      - name: capability_type
        in: query
        type: string
        required: false
        description: Filter by capability type (business, technical, process)
      - name: gap_type
        in: query
        type: string
        required: false
        description: Filter by gap type (coverage, quality, retirement, modernization)
    responses:
      200:
        description: Comprehensive gap analysis data
    """
    try:
        from datetime import datetime, timedelta

        from app.models.application_portfolio import ApplicationComponent
        from app.models.apqc_process import APQCProcess
        from app.models.technical_capability import TechnicalCapability
        from app.models.business_capabilities import (
            ApplicationCapabilityCoverage,
            BusinessCapability,
        )

        # C1 fix: default to "business" when no capability_type is requested.
        # Technical capabilities and APQC processes are SHARED reference taxonomies
        # (no organization_id / TenantMixin) with no per-tenant coverage mapping in
        # most tenants, so leaving this unfiltered counted nearly the entire shared
        # taxonomy as a "gap" for every tenant regardless of whether that tenant owns
        # any capabilities at all -- producing a large, tenant-independent number
        # (e.g. 352) that "Detect Gaps" (which only scans BusinessCapability, see
        # api_roadmap_detect_gaps below) can never create/update records for. That
        # mismatch -- a big tile next to "0 gap(s) created/updated" -- is the C1
        # defect. Business capabilities are the system of record Detect Gaps acts
        # on, so they are now also the default population this tile counts.
        # Explicit ?capability_type=technical|process is still honoured for callers
        # that want to inspect the shared-taxonomy coverage gaps separately.
        capability_type_filter = request.args.get("capability_type") or "business"
        gap_type_filter = request.args.get("gap_type")

        gaps = []
        today = datetime.now().date()

        # =====================================================================
        # 1. Business Capabilities (from real APQC data)
        # =====================================================================
        if not capability_type_filter or capability_type_filter == "business":
            from app.models.application_layer import ApplicationComponent

            biz_caps = BusinessCapability.query.all()
            gap_unified_caps_by_id = {c.id: c for c in biz_caps}

            # Batch-prefetch coverage mappings
            all_coverage = ApplicationCapabilityCoverage.query.all()
            coverage_by_cap = {}
            for cov in all_coverage:
                coverage_by_cap.setdefault(cov.capability_id, []).append(cov)

            # Batch-prefetch applications
            all_app_ids = {cov.application_component_id for cov in all_coverage}
            all_apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(all_app_ids)).all() if all_app_ids else []
            apps_by_id = {a.id: a for a in all_apps}

            for cap in biz_caps:
                cap_mappings = coverage_by_cap.get(cap.id, [])
                apps = [apps_by_id.get(m.application_component_id) for m in cap_mappings if apps_by_id.get(m.application_component_id)]
                app_count = len(apps)

                gap_types = []
                gap_details = []

                # Coverage Gap: No applications
                if app_count == 0:
                    gap_types.append("coverage")
                    gap_details.append("No applications mapped")

                if apps:
                    # Quality Gap: Only tactical/low strategic apps
                    strategic_apps = [
                        a for a in apps if a.strategic_importance in ("critical", "high")
                    ]
                    tactical_apps = [
                        a
                        for a in apps
                        if a.strategic_importance in ("low", "medium") or not a.strategic_importance
                    ]

                    if len(strategic_apps) == 0 and len(tactical_apps) > 0:
                        gap_types.append("quality")
                        gap_details.append(f"{len(tactical_apps)} tactical apps, no strategic")

                    # Retirement Gap: Apps marked for retirement
                    retiring_apps = [
                        a
                        for a in apps
                        if a.lifecycle_status in ("deprecated", "retired", "sunset")
                        or (
                            a.planned_retirement_date
                            and a.planned_retirement_date <= today + timedelta(days=365)
                        )
                    ]
                    if retiring_apps:
                        gap_types.append("retirement")
                        gap_details.append(f"{len(retiring_apps)} apps retiring/deprecated")

                    # Modernization Gap: Apps with high risk or obsolescence
                    modernization_apps = [
                        a
                        for a in apps
                        if a.technical_risk in ("high", "critical")
                        or a.obsolescence_risk in ("high", "critical")
                        or (a.technology_age_years and a.technology_age_years > 10)
                    ]
                    if modernization_apps:
                        gap_types.append("modernization")
                        gap_details.append(f"{len(modernization_apps)} apps need modernization")

                if gap_types:
                    # Determine priority based on gap severity
                    priority = "low"
                    if "coverage" in gap_types:
                        priority = (
                            "high" if cap.strategic_importance in ("critical", "high") else "medium"
                        )
                    if "retirement" in gap_types:
                        priority = (
                            "critical"
                            if any(
                                a.planned_retirement_date
                                and a.planned_retirement_date <= today + timedelta(days=180)
                                for a in apps
                            )
                            else "high"
                        )
                    if "modernization" in gap_types and any(
                        a.technical_risk == "critical" for a in apps
                    ):
                        priority = "critical"

                    # Get parent info for hierarchy filtering (use prefetched lookup)
                    parent_id = getattr(cap, "parent_id", None)
                    parent_cap = None
                    if parent_id:
                        parent_cap = gap_unified_caps_by_id.get(parent_id)

                    # Build hierarchy path for filtering descendants
                    hierarchy_path = f"business-{cap.id}"
                    if parent_id:
                        hierarchy_path = f"business-{parent_id}," + hierarchy_path

                    gaps.append(
                        {
                            "id": f"business-{cap.id}",
                            "capability_id": cap.id,
                            "capability_type": "business",
                            "name": cap.name,
                            "domain": getattr(cap, "business_domain", None) or "Unknown",
                            "level": cap.level or 1,
                            "parent_id": parent_id,
                            "parent_name": parent_cap.name if parent_cap else None,
                            "hierarchy_path": hierarchy_path,
                            "gap_types": gap_types,
                            "gap_details": gap_details,
                            "primary_gap": gap_types[0] if gap_types else None,
                            "priority": priority,
                            "app_count": app_count,
                            "strategic_importance": cap.strategic_importance or "medium",
                            "business_owner": getattr(cap, "business_owner", None) or "Unassigned",
                            # Timeline dates - use app retirement dates if available, else generate
                            "start_date": _get_gap_start_date(gap_types, apps, priority),
                            "end_date": _get_gap_end_date(gap_types, apps, priority),
                            "applications": [
                                {
                                    "id": a.id,
                                    "name": a.name,
                                    "strategic_importance": a.strategic_importance,
                                    "lifecycle_status": a.lifecycle_status,
                                    "planned_retirement_date": a.planned_retirement_date.isoformat()
                                    if a.planned_retirement_date
                                    else None,
                                    "technical_risk": a.technical_risk,
                                    "obsolescence_risk": a.obsolescence_risk,
                                }
                                for a in apps[:5]
                            ],  # Limit to 5 for performance
                        }
                    )

        # =====================================================================
        # 2. Technical Capabilities (ACM)
        # =====================================================================
        if not capability_type_filter or capability_type_filter == "technical":
            tech_caps = TechnicalCapability.query.all()

            # OPTIMIZATION: Batch-prefetch tech capability -> application mappings to avoid N+1 queries
            from app.models.technical_capability import application_technical_capability_mapping as _atcm
            tech_cap_ids = [c.id for c in tech_caps]
            _tech_app_rows = db.session.query(
                _atcm.c.technical_capability_id, _atcm.c.application_id
            ).filter(_atcm.c.technical_capability_id.in_(tech_cap_ids)).all() if tech_cap_ids else []
            _tech_app_ids_by_cap = {}
            for row in _tech_app_rows:
                _tech_app_ids_by_cap.setdefault(row[0], []).append(row[1])
            _all_tech_app_ids = {row[1] for row in _tech_app_rows}
            _tech_apps_all = ApplicationComponent.query.filter(ApplicationComponent.id.in_(_all_tech_app_ids)).all() if _all_tech_app_ids else []
            _tech_apps_by_id = {a.id: a for a in _tech_apps_all}
            # Prefetch tech caps by ID for parent lookups
            gap_tech_caps_by_id = {c.id: c for c in tech_caps}

            for cap in tech_caps:
                # Use prefetched application data instead of lazy-loading cap.applications
                _cap_app_ids = _tech_app_ids_by_cap.get(cap.id, [])
                apps = [_tech_apps_by_id[aid] for aid in _cap_app_ids if aid in _tech_apps_by_id]
                app_count = len(apps)

                gap_types = []
                gap_details = []

                # Coverage Gap
                if app_count == 0:
                    gap_types.append("coverage")
                    gap_details.append("No applications mapped")

                if apps:
                    # Quality Gap
                    strategic_apps = [
                        a for a in apps if a.strategic_importance in ("critical", "high")
                    ]
                    tactical_apps = [
                        a
                        for a in apps
                        if a.strategic_importance in ("low", "medium") or not a.strategic_importance
                    ]

                    if len(strategic_apps) == 0 and len(tactical_apps) > 0:
                        gap_types.append("quality")
                        gap_details.append(f"{len(tactical_apps)} tactical apps, no strategic")

                    # Retirement Gap
                    retiring_apps = [
                        a
                        for a in apps
                        if a.lifecycle_status in ("deprecated", "retired", "sunset")
                        or (
                            a.planned_retirement_date
                            and a.planned_retirement_date <= today + timedelta(days=365)
                        )
                    ]
                    if retiring_apps:
                        gap_types.append("retirement")
                        gap_details.append(f"{len(retiring_apps)} apps retiring/deprecated")

                    # Modernization Gap
                    modernization_apps = [
                        a
                        for a in apps
                        if a.technical_risk in ("high", "critical")
                        or a.obsolescence_risk in ("high", "critical")
                        or (a.technology_age_years and a.technology_age_years > 10)
                    ]
                    if modernization_apps:
                        gap_types.append("modernization")
                        gap_details.append(f"{len(modernization_apps)} apps need modernization")

                if gap_types:
                    priority = _calculate_gap_priority(gap_types, apps, cap, today)

                    # Get parent info for technical capabilities
                    parent_id = getattr(cap, "parent_id", None)
                    parent_cap = None
                    if parent_id:
                        parent_cap = gap_tech_caps_by_id.get(parent_id)

                    # Build hierarchy path
                    hierarchy_path = f"technical-{cap.id}"
                    if parent_id:
                        hierarchy_path = f"technical-{parent_id}," + hierarchy_path

                    gaps.append(
                        {
                            "id": f"technical-{cap.id}",
                            "capability_id": cap.id,
                            "capability_type": "technical",
                            "name": cap.name,
                            "domain": cap.acm_domain or "Unknown",
                            "level": cap.level_number or 1,
                            "parent_id": parent_id,
                            "parent_name": parent_cap.name if parent_cap else None,
                            "hierarchy_path": hierarchy_path,
                            "gap_types": gap_types,
                            "gap_details": gap_details,
                            "primary_gap": gap_types[0] if gap_types else None,
                            "priority": priority,
                            "app_count": app_count,
                            "strategic_importance": getattr(cap, "is_differentiating", False)
                            and "high"
                            or "medium",
                            "business_owner": "Unassigned",
                            "start_date": _get_gap_start_date(gap_types, apps, priority),
                            "end_date": _get_gap_end_date(gap_types, apps, priority),
                            "applications": [
                                {
                                    "id": a.id,
                                    "name": a.name,
                                    "strategic_importance": a.strategic_importance,
                                    "lifecycle_status": a.lifecycle_status,
                                    "planned_retirement_date": a.planned_retirement_date.isoformat()
                                    if a.planned_retirement_date
                                    else None,
                                    "technical_risk": a.technical_risk,
                                    "obsolescence_risk": a.obsolescence_risk,
                                }
                                for a in apps[:5]
                            ],
                        }
                    )

        # =====================================================================
        # 3. Process Capabilities (APQC)
        # =====================================================================
        if not capability_type_filter or capability_type_filter == "process":
            try:
                # Note: APQCProcess may not have direct applications relationship
                # Get all processes and filter by apqc_level property
                all_processes = APQCProcess.query.limit(500).all()
                # Filter to top 3 levels using the apqc_level property
                processes = [p for p in all_processes if p.apqc_level and p.apqc_level <= 3]

                # OPTIMIZATION: Batch-prefetch all processes by ID for parent lookups
                gap_processes_by_id = {p.id: p for p in all_processes}

                for proc in processes:
                    # APQCProcess doesn't have direct applications relationship
                    # We'll mark all processes as coverage gaps for now
                    apps = []
                    if hasattr(proc, "applications"):
                        try:
                            apps = list(proc.applications)
                        except Exception:
                            apps = []
                    app_count = len(apps)

                    gap_types = []
                    gap_details = []

                    # Coverage Gap
                    if app_count == 0:
                        gap_types.append("coverage")
                        gap_details.append("No applications mapped")

                    if apps:
                        # Quality Gap
                        strategic_apps = [
                            a for a in apps if a.strategic_importance in ("critical", "high")
                        ]
                        tactical_apps = [
                            a
                            for a in apps
                            if a.strategic_importance in ("low", "medium")
                            or not a.strategic_importance
                        ]

                        if len(strategic_apps) == 0 and len(tactical_apps) > 0:
                            gap_types.append("quality")
                            gap_details.append(f"{len(tactical_apps)} tactical apps, no strategic")

                        # Retirement Gap
                        retiring_apps = [
                            a
                            for a in apps
                            if a.lifecycle_status in ("deprecated", "retired", "sunset")
                            or (
                                a.planned_retirement_date
                                and a.planned_retirement_date <= today + timedelta(days=365)
                            )
                        ]
                        if retiring_apps:
                            gap_types.append("retirement")
                            gap_details.append(f"{len(retiring_apps)} apps retiring/deprecated")

                        # Modernization Gap
                        modernization_apps = [
                            a
                            for a in apps
                            if a.technical_risk in ("high", "critical")
                            or a.obsolescence_risk in ("high", "critical")
                        ]
                        if modernization_apps:
                            gap_types.append("modernization")
                            gap_details.append(f"{len(modernization_apps)} apps need modernization")

                    if gap_types:
                        priority = "medium"
                        if "retirement" in gap_types:
                            priority = "high"
                        proc_level = proc.apqc_level or 1
                        if "coverage" in gap_types and proc_level <= 2:
                            priority = "high"

                        # Get parent info for APQC processes
                        parent_id = getattr(proc, "parent_process_id", None)
                        parent_proc = None
                        if parent_id:
                            parent_proc = gap_processes_by_id.get(parent_id)

                        # Build hierarchy path
                        hierarchy_path = f"process-{proc.id}"
                        if parent_id:
                            hierarchy_path = f"process-{parent_id}," + hierarchy_path

                        gaps.append(
                            {
                                "id": f"process-{proc.id}",
                                "capability_id": proc.id,
                                "capability_type": "process",
                                "name": proc.process_name,
                                "domain": proc.process_code.split(".")[0] + ".0"
                                if proc.process_code
                                else "Unknown",
                                "level": proc_level,
                                "parent_id": parent_id,
                                "parent_name": parent_proc.process_name if parent_proc else None,
                                "hierarchy_path": hierarchy_path,
                                "gap_types": gap_types,
                                "gap_details": gap_details,
                                "primary_gap": gap_types[0] if gap_types else None,
                                "priority": priority,
                                "app_count": app_count,
                                "strategic_importance": "medium",
                                "business_owner": "Unassigned",
                                "start_date": _get_gap_start_date(gap_types, apps, priority),
                                "end_date": _get_gap_end_date(gap_types, apps, priority),
                                "applications": [
                                    {
                                        "id": a.id,
                                        "name": a.name,
                                        "strategic_importance": a.strategic_importance,
                                        "lifecycle_status": a.lifecycle_status,
                                        "planned_retirement_date": a.planned_retirement_date.isoformat()
                                        if a.planned_retirement_date
                                        else None,
                                        "technical_risk": a.technical_risk,
                                    }
                                    for a in apps[:5]
                                ],
                            }
                        )
            except Exception as proc_error:
                current_app.logger.warning(f"Error loading process gaps: {proc_error}")

        # Filter by gap type if specified
        if gap_type_filter:
            gaps = [g for g in gaps if gap_type_filter in g["gap_types"]]

        # Calculate statistics
        stats = {
            "total_gaps": len(gaps),
            "coverage_gaps": len([g for g in gaps if "coverage" in g["gap_types"]]),
            "quality_gaps": len([g for g in gaps if "quality" in g["gap_types"]]),
            "retirement_gaps": len([g for g in gaps if "retirement" in g["gap_types"]]),
            "modernization_gaps": len([g for g in gaps if "modernization" in g["gap_types"]]),
            "critical_priority": len([g for g in gaps if g["priority"] == "critical"]),
            "high_priority": len([g for g in gaps if g["priority"] == "high"]),
            "by_capability_type": {
                "business": len([g for g in gaps if g["capability_type"] == "business"]),
                "technical": len([g for g in gaps if g["capability_type"] == "technical"]),
                "process": len([g for g in gaps if g["capability_type"] == "process"]),
            },
        }

        return jsonify(
            {
                "success": True,
                "gaps": gaps,
                "statistics": stats,
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting roadmap gaps: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred", "gaps": [], "statistics": {}}), 500


@capability_map.route("/api/roadmap/gaps", methods=["POST"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_gap_create_direct")
def api_roadmap_create_gap():
    """
    Create a Gap record directly (without requiring a source capability).

    Request body:
    {
        "name": "CRM Capability Gap",
        "description": "Missing CRM capabilities",
        "gap_type": "coverage",
        "priority": "high",
        "plateau_id": 1
    }
    Returns 201 with gap dict on success.
    """
    try:
        from app.models.implementation_migration import Gap, Plateau

        data = request.get_json() or {}
        if not data.get("name"):
            return jsonify({"success": False, "error": "name is required"}), 400

        valid_gap_types = ["coverage", "quality", "retirement", "modernization", "custom"]
        gap_type = data.get("gap_type", "coverage")
        if gap_type not in valid_gap_types:
            gap_type = "custom"

        valid_priorities = ["critical", "high", "medium", "low"]
        priority = data.get("priority", "medium")
        if priority not in valid_priorities:
            priority = "medium"

        plateau_id = data.get("plateau_id")
        if plateau_id:
            plateau = Plateau.query.get(plateau_id)
            if not plateau:
                return jsonify({"success": False, "error": "Plateau not found"}), 404

        gap = Gap(
            name=data["name"],
            description=data.get("description", ""),
            gap_type=gap_type,
            priority=priority,
            resolution_status="identified",
        )
        if plateau_id:
            gap.target_plateau_id = plateau_id

        db.session.add(gap)
        db.session.commit()

        return jsonify({
            "success": True,
            "gap": {
                "id": gap.id,
                "name": gap.name,
                "description": gap.description,
                "gap_type": gap.gap_type,
                "priority": gap.priority,
                "resolution_status": gap.resolution_status,
                "target_plateau_id": gap.target_plateau_id,
            },
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating gap: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def _calculate_gap_priority(gap_types, apps, cap, today):
    """Calculate gap priority based on gap type and application attributes."""
    from datetime import timedelta

    priority = "low"

    if "coverage" in gap_types:
        priority = "medium"
        if getattr(cap, "is_foundational", False) or getattr(cap, "is_differentiating", False):
            priority = "high"

    if "retirement" in gap_types:
        # Check if any app is retiring soon (within 6 months)
        soon_retiring = [
            a
            for a in apps
            if a.planned_retirement_date
            and a.planned_retirement_date <= today + timedelta(days=180)
        ]
        priority = "critical" if soon_retiring else "high"

    if "modernization" in gap_types:
        critical_risk = [
            a for a in apps if a.technical_risk == "critical" or a.obsolescence_risk == "critical"
        ]
        if critical_risk:
            priority = "critical"
        elif priority not in ("critical", "high"):
            priority = "high"

    return priority


def _get_gap_start_date(gap_types, apps, priority):
    """Get start date for gap resolution based on type and priority."""
    from datetime import timedelta

    today = datetime.now().date()

    # If retirement gap, start before the earliest retirement date
    if "retirement" in gap_types and apps:
        retirement_dates = [a.planned_retirement_date for a in apps if a.planned_retirement_date]
        if retirement_dates:
            earliest = min(retirement_dates)
            # Start 6 months before retirement
            start = earliest - timedelta(days=180)
            return max(start, today).isoformat()

    # Default based on priority
    days_map = {"critical": 0, "high": 30, "medium": 90, "low": 180}
    days = days_map.get(priority, 90)
    return (today + timedelta(days=days)).isoformat()


def _get_gap_end_date(gap_types, apps, priority):
    """Get end date for gap resolution based on type and priority."""
    from datetime import timedelta

    today = datetime.now().date()

    # If retirement gap, end at the retirement date
    if "retirement" in gap_types and apps:
        retirement_dates = [a.planned_retirement_date for a in apps if a.planned_retirement_date]
        if retirement_dates:
            return max(retirement_dates).isoformat()

    # Default based on priority
    days_map = {"critical": 90, "high": 180, "medium": 365, "low": 545}
    days = days_map.get(priority, 365)
    return (today + timedelta(days=days)).isoformat()


# =============================================================================
# ArchiMate Gap & WorkPackage Management API
# =============================================================================


@capability_map.route("/api/roadmap/archimate-gaps")
@login_required
@rate_limit(60, "1m")
def api_roadmap_archimate_gaps():
    """
    Get ArchiMate Gap records for roadmap display.

    These are persisted gaps (converted from auto-detected or manually created).

    Query params:
        gap_type: Filter by gap type (coverage, quality, retirement, modernization)
        priority: Filter by priority (critical, high, medium, low)
        resolution_status: Filter by status (identified, in_progress, resolved)
        capability_type: Filter by source capability type (business, technical, process)
    """
    try:
        from app.services.gap_archimate_service import gap_archimate_service

        filters = {
            "gap_type": request.args.get("gap_type"),
            "priority": request.args.get("priority"),
            "resolution_status": request.args.get("resolution_status"),
            "source_capability_type": request.args.get("capability_type"),
        }
        # Remove None values
        filters = {k: v for k, v in filters.items() if v}

        gaps = gap_archimate_service.get_gaps_for_roadmap(filters)

        # Build gap data with hierarchical work packages
        gaps_data = []
        for gap in gaps:
            gap_dict = gap.to_roadmap_dict()
            # Get only top-level work packages and include their children recursively
            top_level_wps = [wp for wp in gap.work_packages if wp.parent_id is None]
            gap_dict["work_packages"] = [
                wp.to_roadmap_dict(include_children=True) for wp in top_level_wps
            ]
            gaps_data.append(gap_dict)

        return jsonify({"success": True, "gaps": gaps_data, "total_count": len(gaps)})

    except Exception as e:
        current_app.logger.error(f"Error getting ArchiMate gaps: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/gaps/convert", methods=["POST"])
@rate_limit(30, "1m")
@login_required
@audit_log("roadmap_gaps_convert")
def api_roadmap_convert_gaps():
    """
    Convert auto-detected capability gaps to ArchiMate Gap records.

    Request body:
    {
        "gaps": [
            {
                "capability_id": 123,
                "capability_type": "business",
                "name": "Customer Management",
                "gap_types": ["coverage"],
                "priority": "high",
                ...
            }
        ],
        "create_work_packages": false,
        "work_package_template": "auto"
    }
    """
    try:
        from app.services.gap_archimate_service import gap_archimate_service

        data = request.get_json()
        gaps_data = data.get("gaps", [])
        create_wps = data.get("create_work_packages", False)
        wp_template = data.get("work_package_template", "auto")

        result = gap_archimate_service.bulk_convert_gaps(gaps_data, commit=False)

        # Optionally create work packages
        if create_wps and result["created"] > 0:

            for gap_data in gaps_data:
                gap = gap_archimate_service.find_existing_gap(
                    gap_data.get("capability_type"), gap_data.get("capability_id")
                )
                if gap and not gap.work_packages:
                    gap_archimate_service.create_standard_work_breakdown(gap, template=wp_template)

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "created": result["created"],
                "updated": result["updated"],
                "errors": result["errors"],
                "message": f"Converted {result['created']} new gaps, updated {result['updated']} existing",
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error converting gaps: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/gaps/add-from-capability", methods=["POST"])
@rate_limit(30, "1m")
@login_required
@audit_log("roadmap_gap_create")
def api_roadmap_add_from_capability():
    """
    Add a capability to the roadmap by creating an ArchiMate Gap record.

    Request body:
    {
        "capability_id": 123,
        "capability_type": "business",
        "capability_name": "Customer Management",
        "level": 2,
        "gap_type": "coverage",
        "priority": "high",
        "start_date": "2026 - 01 - 01",
        "end_date": "2026 - 06 - 30",
        "color": "#6B7280",
        "create_work_packages": true
    }
    """
    try:
        from app.services.gap_archimate_service import gap_archimate_service

        # Parse JSON with error handling
        try:
            data = request.get_json()
        except Exception as e:
            current_app.logger.error(f"Invalid JSON in request body: {e}")
            return jsonify({"success": False, "error": "Invalid JSON in request body"}), 400

        # Validate required fields
        required_fields = ["capability_id", "capability_type", "capability_name"]
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Missing required fields: {', '.join(missing_fields)}",
                    }
                ),
                400,
            )

        # Validate capability_type
        valid_capability_types = ["business", "technical", "process"]
        if data.get("capability_type") not in valid_capability_types:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Invalid capability_type. Must be one of: {', '.join(valid_capability_types)}",
                    }
                ),
                400,
            )

        # Check if gap already exists for this capability
        existing_gap = gap_archimate_service.find_existing_gap(
            data.get("capability_type"), data.get("capability_id")
        )

        if existing_gap:
            return (
                jsonify({"success": False, "error": "This capability is already on the roadmap"}),
                400,
            )

        # Create gap data structure
        gap_data = {
            "capability_id": data.get("capability_id"),
            "capability_type": data.get("capability_type"),
            "name": data.get("capability_name"),
            "gap_types": [data.get("gap_type", "coverage")],
            "priority": data.get("priority", "medium"),
            "level": data.get("level", 1),
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "color": data.get("color", "#6B7280"),
        }

        # Convert to ArchiMate Gap
        # Returns (Gap, was_created). Assigning the tuple straight to `gap`
        # made the `gap.id` below raise AttributeError on every call.
        gap, _was_created = gap_archimate_service.convert_capability_gap_to_archimate(
            gap_data
        )

        # Create work packages if requested
        if data.get("create_work_packages", False):
            gap_archimate_service.create_standard_work_breakdown(gap, template="auto")

        db.session.commit()

        return jsonify(
            {"success": True, "gap_id": gap.id, "message": f"Added '{gap.name}' to roadmap"}
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error adding capability to roadmap: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/gaps/<int:gap_id>", methods=["GET"])
@login_required
@rate_limit(60, "1m")
def api_roadmap_get_gap(gap_id):
    """Get a single ArchiMate Gap with its work packages."""
    try:
        from app.models.implementation_migration import Gap

        gap = Gap.query.get(gap_id)
        if not gap:
            return jsonify({"success": False, "error": "Gap not found"}), 404

        gap_data = gap.to_roadmap_dict()

        # Get only top-level work packages (parent_id is None) and include their children recursively
        top_level_wps = [wp for wp in gap.work_packages if wp.parent_id is None]
        gap_data["work_packages"] = [
            wp.to_roadmap_dict(include_children=True) for wp in top_level_wps
        ]

        return jsonify({"success": True, "gap": gap_data})

    except Exception as e:
        current_app.logger.error(f"Error getting gap: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/gaps/<int:gap_id>", methods=["PUT"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_gap_update")
def api_roadmap_update_gap(gap_id):
    """
    Update an ArchiMate Gap.

    Request body: Fields to update (name, description, color, priority, dates, etc.)
    """
    try:
        from app.services.gap_archimate_service import gap_archimate_service

        data = request.get_json()
        gap = gap_archimate_service.update_gap(gap_id, data)

        if not gap:
            return jsonify({"success": False, "error": "Gap not found"}), 404

        db.session.commit()

        return jsonify(
            {"success": True, "gap": gap.to_roadmap_dict(), "message": "Gap updated successfully"}
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating gap: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/gaps/<int:gap_id>", methods=["DELETE"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_gap_delete")
def api_roadmap_delete_gap(gap_id):
    """Delete an ArchiMate Gap."""
    try:
        from app.services.gap_archimate_service import gap_archimate_service

        if gap_archimate_service.delete_gap(gap_id):
            db.session.commit()
            return jsonify({"success": True, "message": "Gap deleted successfully"})
        else:
            return jsonify({"success": False, "error": "Gap not found"}), 404

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting gap: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/gaps/<int:gap_id>/work-packages", methods=["POST"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_work_package_create")
def api_roadmap_create_work_package(gap_id):
    """
    Create a WorkPackage to resolve a Gap.

    Request body:
    {
        "name": "Implement CRM System",
        "start_date": "2026 - 02 - 01",
        "target_date": "2026 - 06 - 30",
        "color": "#3B82F6",
        "template": "auto"  // Or: default, vendor_selection, modernization, retirement
    }
    """
    try:
        from app.models.implementation_migration import Gap
        from app.services.gap_archimate_service import gap_archimate_service

        gap = Gap.query.get(gap_id)
        if not gap:
            return jsonify({"success": False, "error": "Gap not found"}), 404

        data = request.get_json() or {}

        # Validate date fields before passing to service
        from datetime import date as date_type

        for date_field in ("start_date", "target_date"):
            if data.get(date_field):
                try:
                    date_type.fromisoformat(data[date_field])
                except (ValueError, TypeError):
                    return jsonify({
                        "success": False,
                        "error": f"Invalid {date_field} format. Use ISO 8601 (YYYY-MM-DD).",
                    }), 400

        if data.get("start_date") and data.get("target_date"):
            parsed_start = date_type.fromisoformat(data["start_date"])
            parsed_target = date_type.fromisoformat(data["target_date"])
            if parsed_target < parsed_start:
                return jsonify({
                    "success": False,
                    "error": "target_date must be on or after start_date.",
                }), 400

        template = data.pop("template", None)

        if template:
            # Create with standard work breakdown
            wp = gap_archimate_service.create_standard_work_breakdown(gap, template=template)
        else:
            # Create single work package
            wp = gap_archimate_service.create_work_package_for_gap(gap, data)

        from app.services import work_package_service
        from app.utils.tenant import current_organization_id

        org_id = current_organization_id()
        copy = None
        try:
            db.session.commit()
        except Exception as commit_err:
            # Production DB may be missing new columns (migration freeze).
            # Rollback and make the work package through the one writer (its core
            # columns only), linked to the gap as the writer links it.
            db.session.rollback()
            current_app.logger.warning(
                f"WorkPackage commit failed ({commit_err}), retrying through the writer"
            )
            from datetime import date as date_type

            def _day(value):
                return date_type.fromisoformat(value) if value else None

            copy = work_package_service.create_work_package(
                organization_id=org_id,
                user_id=current_user.id if current_user.is_authenticated else None,
                name=data.get("name") or f"Resolve: {gap.name}",
                description=data.get("description", gap.description),
                start_date=_day(data.get("start_date")),
                end_date=_day(data.get("target_date")),
                priority=data.get("priority", "medium"),
                status="planned",
                gap_id=gap.id,
            )
            db.session.commit()

        # The screen's ids are the one store's ids: answer with the row of the one
        # store (the copy the bridge made of the older row, or the row the writer
        # just made), never the older list's id.
        if copy is None:
            copy = work_package_service.get_by_source("work_packages", wp.id, org_id)
        if copy is None:
            current_app.logger.error("create-from-gap: no row in the one store for work package %s", wp.id)
            return jsonify({"success": False, "error": "An internal error occurred"}), 500
        wp_dict = work_package_service.to_roadmap_dict(copy, org_id, include_children=True)

        return jsonify(
            {
                "success": True,
                "work_package": wp_dict,
                "message": "Work package created successfully",
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating work package: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/work-packages")
@login_required
@rate_limit(60, "1m")
def api_roadmap_work_packages():
    """
    Get work packages for roadmap display, from the one work package store
    (this organisation's rows only).

    Query params:
        gap_id: Filter by associated gap
        root_only: If true, only return root-level packages
        include_children: If true, include nested children
    """
    try:
        from sqlalchemy import or_, select

        from app.models.relationship_tables import gap_work_packages
        from app.models.unified_work_package import UnifiedWorkPackage as UWP
        from app.services import work_package_service
        from app.utils.tenant import current_organization_id

        org_id = current_organization_id()
        gap_id = request.args.get("gap_id", type=int)
        root_only = request.args.get("root_only", "true").lower() == "true"
        include_children = request.args.get("include_children", "true").lower() == "true"

        try:
            query = work_package_service.query_for(org_id)
            if root_only:
                query = query.filter(UWP.parent_id.is_(None))
            if gap_id:
                # Rows made here are linked to the gap by an ArchiMate relationship;
                # rows carried over from the older work package list are linked
                # through gap_work_packages by that list's id.
                linked = select(gap_work_packages.c.work_package_id).where(
                    gap_work_packages.c.gap_id == gap_id
                )
                query = query.filter(or_(
                    UWP.id.in_(work_package_service.work_package_ids_for_gap(gap_id, org_id)),
                    (UWP.source_table == "work_packages") & UWP.source_id.in_(linked),
                ))
            work_packages = query.order_by(UWP.start_date, UWP.id).all()
        except Exception as qe:
            current_app.logger.warning(f"Work packages query failed: {qe}", exc_info=True)
            work_packages = []

        serialized = []
        for wp in work_packages:
            try:
                serialized.append(
                    work_package_service.to_roadmap_dict(
                        wp, org_id, include_children=include_children
                    )
                )
            except Exception as se:
                current_app.logger.warning(f"WorkPackage {wp.id} serialization failed: {se}")
                serialized.append({"id": wp.id, "name": wp.name, "status": wp.status})

        return jsonify(
            {
                "success": True,
                "work_packages": serialized,
                "total_count": len(serialized),
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting work packages: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def _resolve_roadmap_gap_id(raw_gap_id):
    """Turn whatever the roadmap sent into a real Gap row, or explain why not.

    GET /api/roadmap/gaps returns TWO kinds of gap. Stored Gap rows carry their
    integer primary key. Derived gaps -- one per capability with no application
    mapped -- are synthesised on read and carry a composite id of the form
    "{capability_type}-{capability_id}", e.g. "business-279". They are not rows;
    nothing with that id exists to point a foreign key at.

    The roadmap's own "Add Work Package" dropdown is populated from that combined
    list, so selecting a derived gap and submitting sent "business-279" to an
    endpoint doing Gap.query.get(...), which raised and returned 500. The QA
    audit of 30 Aug 2026 (High 1) called it correctly: "The single most important
    link in an EA tool -- turning an identified gap into planned work -- cannot
    be completed through the UI", with a modal reading only "Notice / Not Found".

    Converting a derived gap into planned work is exactly the user's intent, and
    the machinery for it already existed: add-from-capability materialises a real
    Gap from a capability via gap_archimate_service. This routes the composite id
    through the same service -- reusing an existing gap for that capability if
    one is already on the roadmap, creating it if not -- so the work package
    lands against a real row and keeps the traceability that justified it.

    Returns (gap, error_response). Exactly one is not None.
    """
    from app.models.implementation_migration import Gap

    if raw_gap_id in (None, ""):
        return None, None

    # A plain integer is a stored Gap: the original path, unchanged.
    try:
        return_gap = Gap.query.get(int(raw_gap_id))
    except (TypeError, ValueError):
        return_gap = None
    else:
        if return_gap is None:
            return None, (jsonify({"success": False, "error": "Gap not found"}), 404)
        return return_gap, None

    # Otherwise it should be a derived "{type}-{id}" composite.
    text = str(raw_gap_id)
    capability_type, _, capability_ref = text.partition("-")
    valid_types = ("business", "technical", "process")
    if capability_type not in valid_types or not capability_ref.isdigit():
        return None, (
            jsonify({
                "success": False,
                "error": (
                    "gap_id %r is neither a stored gap nor a derived "
                    "{type}-{id} reference" % text
                ),
            }),
            400,
        )

    from app.services.gap_archimate_service import gap_archimate_service

    capability_id = int(capability_ref)
    existing = gap_archimate_service.find_existing_gap(capability_type, capability_id)
    if existing is not None:
        return existing, None

    name = _derived_capability_name(capability_type, capability_id)
    if name is None:
        return None, (
            jsonify({
                "success": False,
                "error": "No %s capability %d exists to raise a gap against"
                         % (capability_type, capability_id),
            }),
            404,
        )

    gap, _was_created = gap_archimate_service.convert_capability_gap_to_archimate({
        "capability_id": capability_id,
        "capability_type": capability_type,
        "name": name,
        "gap_types": ["coverage"],
        "priority": "medium",
        "level": 1,
        "color": "#6B7280",
    })
    db.session.flush()
    return gap, None


def _derived_capability_name(capability_type, capability_id):
    """The capability a derived gap was synthesised from, or None if it is gone."""
    if capability_type == "business":
        from app.models.business_capabilities import BusinessCapability

        row = BusinessCapability.query.get(capability_id)
    elif capability_type == "technical":
        from app.models.technical_capability import TechnicalCapability

        row = TechnicalCapability.query.get(capability_id)
    else:
        from app.models.apqc_process import APQCProcess

        row = APQCProcess.query.get(capability_id)
    return getattr(row, "name", None) if row is not None else None


@capability_map.route("/api/roadmap/work-packages", methods=["POST"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_work_package_create_standalone")
def api_roadmap_create_standalone_work_package():
    """
    Create a standalone WorkPackage (not linked to a specific gap via URL).

    Request body:
    {
        "name": "CRM Implementation",
        "description": "Implement CRM system",
        "status": "planned",
        "priority": "medium",
        "gap_id": 1,
        "plateau_id": 1
    }
    Returns 201 with work_package dict on success.
    """
    try:
        from app.models.implementation_migration import Plateau
        from app.services import work_package_service
        from app.utils.tenant import current_organization_id

        data = request.get_json() or {}
        if not data.get("name"):
            return jsonify({"success": False, "error": "name is required"}), 400

        valid_statuses = ["planned", "in_progress", "completed", "on_hold", "cancelled"]
        status = data.get("status", "planned")
        if status not in valid_statuses:
            status = "planned"

        valid_priorities = ["critical", "high", "medium", "low"]
        priority = data.get("priority", "medium")
        if priority not in valid_priorities:
            priority = "medium"

        gap_id = data.get("gap_id")
        plateau_id = data.get("plateau_id")

        # Accepts a stored Gap id OR a derived "{type}-{id}" reference, which is
        # what this page's own dropdown supplies for the 500-odd gaps it computes.
        gap, gap_error = _resolve_roadmap_gap_id(gap_id)
        if gap_error is not None:
            return gap_error

        plateau = None
        if plateau_id:
            plateau = Plateau.query.get(plateau_id)
            if not plateau:
                return jsonify({"success": False, "error": "Plateau not found"}), 404

        from datetime import date as date_type

        start_date = None
        if data.get("start_date"):
            try:
                start_date = date_type.fromisoformat(data["start_date"])
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": "Invalid start_date format. Use ISO 8601 (YYYY-MM-DD).",
                }), 400

        target_date = None
        if data.get("target_date"):
            try:
                target_date = date_type.fromisoformat(data["target_date"])
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": "Invalid target_date format. Use ISO 8601 (YYYY-MM-DD).",
                }), 400

        if start_date and target_date and target_date < start_date:
            return jsonify({
                "success": False,
                "error": "target_date must be on or after start_date.",
            }), 400

        try:
            wp = work_package_service.create_work_package(
                organization_id=current_organization_id(),
                user_id=current_user.id if current_user.is_authenticated else None,
                name=data["name"],
                description=data.get("description", ""),
                status=status,
                priority=priority,
                start_date=start_date,
                end_date=target_date,
                gap_id=gap.id if gap else None,
                plateau_id=plateau.id if plateau else None,
            )
        except work_package_service.WorkPackageError as exc:
            db.session.rollback()
            return jsonify({"success": False, "error": str(exc)}), (
                404 if isinstance(exc, work_package_service.WorkPackageNotFound) else 400
            )

        db.session.commit()

        return jsonify({
            "success": True,
            "work_package": {
                "id": wp.id,
                "name": wp.name,
                "description": wp.description,
                "status": wp.status,
                "priority": wp.priority,
                "start_date": wp.start_date.date().isoformat() if wp.start_date else None,
                "target_date": wp.end_date.date().isoformat() if wp.end_date else None,
            },
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating standalone work package: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/work-packages/<int:wp_id>", methods=["GET"])
@login_required
@rate_limit(60, "1m")
def api_roadmap_get_work_package(wp_id):
    """Get a single work package with children."""
    try:
        from app.services import work_package_service
        from app.utils.tenant import current_organization_id

        org_id = current_organization_id()
        wp = work_package_service.get_work_package(wp_id, org_id)
        if not wp:
            return jsonify({"success": False, "error": "Work package not found"}), 404

        return jsonify({
            "success": True,
            "work_package": work_package_service.to_roadmap_dict(wp, org_id, include_children=True),
        })

    except Exception as e:
        current_app.logger.error(f"Error getting work package: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/work-packages/<int:wp_id>", methods=["PUT"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_work_package_update")
def api_roadmap_update_work_package(wp_id):
    """
    Update a WorkPackage.

    Request body: Fields to update (name, dates, status, color, percent_complete, etc.)
    """
    try:
        from datetime import date as date_type

        from app.services import work_package_service
        from app.utils.tenant import current_organization_id

        data = request.get_json() or {}

        # Validate date fields before passing to service
        for date_field in ("start_date", "target_date"):
            if date_field in data and data[date_field] is not None:
                if isinstance(data[date_field], str):
                    try:
                        date_type.fromisoformat(data[date_field])
                    except (ValueError, TypeError):
                        return jsonify({
                            "success": False,
                            "error": f"Invalid {date_field} format. Use ISO 8601 (YYYY-MM-DD).",
                        }), 400

        # Validate target_date >= start_date when both are provided
        parsed_start = None
        parsed_target = None
        if data.get("start_date") and isinstance(data["start_date"], str):
            parsed_start = date_type.fromisoformat(data["start_date"])
        if data.get("target_date") and isinstance(data["target_date"], str):
            parsed_target = date_type.fromisoformat(data["target_date"])

        if parsed_start and parsed_target and parsed_target < parsed_start:
            return jsonify({
                "success": False,
                "error": "target_date must be on or after start_date.",
            }), 400

        org_id = current_organization_id()
        try:
            wp = work_package_service.update_work_package(
                wp_id,
                organization_id=org_id,
                user_id=current_user.id if current_user.is_authenticated else None,
                **work_package_service.from_form(data),
            )
        except work_package_service.WorkPackageNotFound:
            db.session.rollback()
            return jsonify({"success": False, "error": "Work package not found"}), 404
        except work_package_service.WorkPackageError as exc:
            db.session.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "work_package": work_package_service.to_roadmap_dict(wp, org_id),
                "message": "Work package updated successfully",
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating work package: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/work-packages/<int:wp_id>", methods=["DELETE"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_work_package_delete")
def api_roadmap_delete_work_package(wp_id):
    """Delete a WorkPackage (and optionally its children)."""
    try:
        from app.services import work_package_service
        from app.utils.tenant import current_organization_id

        cascade = request.args.get("cascade", "true").lower() == "true"

        try:
            work_package_service.delete_work_package(
                wp_id, organization_id=current_organization_id(), cascade=cascade
            )
        except work_package_service.WorkPackageNotFound:
            db.session.rollback()
            return jsonify({"success": False, "error": "Work package not found"}), 404
        db.session.commit()
        return jsonify({"success": True, "message": "Work package deleted successfully"})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting work package: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/work-packages/<int:wp_id>/children", methods=["POST"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_child_work_package_create")
def api_roadmap_create_child_work_package(wp_id):
    """
    Create a child work package under a parent.

    Request body:
    {
        "name": "Requirements Gathering",
        "start_date": "2026 - 02 - 01",
        "target_date": "2026 - 02 - 28",
        "estimated_effort_hours": 80
    }
    """
    try:
        from app.services import work_package_service
        from app.utils.tenant import current_organization_id

        org_id = current_organization_id()
        parent = work_package_service.get_work_package(wp_id, org_id)
        if not parent:
            return jsonify({"success": False, "error": "Parent work package not found"}), 404

        data = request.get_json() or {}

        # Validate date fields before passing to service
        from datetime import date as date_type

        for date_field in ("start_date", "target_date"):
            if data.get(date_field):
                try:
                    date_type.fromisoformat(data[date_field])
                except (ValueError, TypeError):
                    return jsonify({
                        "success": False,
                        "error": f"Invalid {date_field} format. Use ISO 8601 (YYYY-MM-DD).",
                    }), 400

        if data.get("start_date") and data.get("target_date"):
            parsed_start = date_type.fromisoformat(data["start_date"])
            parsed_target = date_type.fromisoformat(data["target_date"])
            if parsed_target < parsed_start:
                return jsonify({
                    "success": False,
                    "error": "target_date must be on or after start_date.",
                }), 400

        child_fields = work_package_service.from_form(data)
        child_fields["parent_id"] = parent.id
        child_fields.setdefault("gap_id", parent.gap_id)
        child = work_package_service.create_work_package(
            organization_id=org_id,
            user_id=current_user.id if current_user.is_authenticated else None,
            **child_fields,
        )

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "work_package": work_package_service.to_roadmap_dict(child, org_id),
                "message": "Child work package created successfully",
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating child work package: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# RDM-010: Plateau CRUD + Gap Auto-Detection + AI Generation
# =============================================================================


@capability_map.route("/api/roadmap/plateaus", methods=["GET"])
@login_required
@rate_limit(60, "1m")
def api_roadmap_get_plateaus():
    """
    Get all Plateaus ordered by sequence_order.

    Returns:
        JSON: { plateaus: [ { id, name, description, sequence_order, target_date } ] }
    """
    try:
        from app.models.implementation_migration import Plateau

        plateaus = Plateau.query.order_by(Plateau.sequence_order.asc()).all()
        return jsonify({
            "success": True,
            "plateaus": [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "sequence_order": p.sequence_order,
                    "target_date": p.target_date.isoformat() if p.target_date else None,
                }
                for p in plateaus
            ],
        })
    except Exception as e:
        current_app.logger.error(f"Error fetching plateaus: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/plateaus", methods=["POST"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_plateau_create")
def api_roadmap_create_plateau():
    """
    Create a new Plateau.

    Request body: { name (required), description, sequence_order (required), target_date }
    Returns 201 with plateau dict on success.
    """
    try:
        from app.models.implementation_migration import Plateau

        data = request.get_json() or {}
        if not data.get("name"):
            return jsonify({"success": False, "error": "name is required"}), 400
        if data.get("sequence_order") is None:
            return jsonify({"success": False, "error": "sequence_order is required"}), 400

        from datetime import date as date_type
        target_date = None
        if data.get("target_date"):
            try:
                target_date = date_type.fromisoformat(data["target_date"])
            except ValueError:
                return jsonify({"success": False, "error": "target_date must be YYYY-MM-DD"}), 400

        plateau = Plateau(
            name=data["name"],
            description=data.get("description", ""),
            sequence_order=int(data["sequence_order"]),
            target_date=target_date,
        )
        db.session.add(plateau)
        sync_archimate_element(plateau)
        db.session.commit()

        return jsonify({
            "success": True,
            "plateau": {
                "id": plateau.id,
                "name": plateau.name,
                "description": plateau.description,
                "sequence_order": plateau.sequence_order,
                "target_date": plateau.target_date.isoformat() if plateau.target_date else None,
            },
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating plateau: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/gaps/detect", methods=["POST"])
@login_required
@rate_limit(30, "1m")
@audit_log("roadmap_gap_detect")
def api_roadmap_detect_gaps():
    """
    Auto-detect capability gaps and create/update ArchiMate Gap records.

    Scans all UnifiedCapabilities with maturity_gap > 0 and converts them
    to Gap records using GapArchiMateService. Returns counts and gap list.

    Returns:
        JSON: { created: N, updated: M, gaps: [ gap dicts ] }
    """
    try:
        from app.models.business_capabilities import BusinessCapability
        from app.models.unified_capability import UnifiedCapability
        from app.modules.architecture.services.gap_archimate_service import GapArchiMateService

        service = GapArchiMateService()

        # D-R5-1: selection and magnitude/prose must read the SAME store.
        # Previously the selection predicate below read the SOURCE
        # (`BusinessCapability.current_maturity_level`) while the magnitude
        # and persisted description read the AUTHORITY
        # (`UnifiedCapability.maturity_for_source`). Those two stores can
        # disagree for up to the 15-minute projection interval — the raw-SQL
        # UPDATE in maturity_routes.py bypasses the ORM sync listener and
        # only the next scheduled projection run catches the authority up —
        # so a capability could be SELECTED off a fresh source write while
        # its magnitude/prose were computed off a stale (still-NULL)
        # authority row, fabricating a persisted "gap of 0" via `or 0` for a
        # capability whose real gap was nonzero. Fetch the authority data for
        # every candidate ONCE, up front, and use that SAME fetched data for
        # both selection and magnitude/prose — never a mix of the two
        # stores in one request.
        all_caps = BusinessCapability.query.all()
        org_id = all_caps[0].organization_id if all_caps else None
        maturity_map = UnifiedCapability.maturity_for_sources(
            "business_capability", [c.id for c in all_caps], organization_id=org_id
        )

        capabilities = []
        for cap in all_caps:
            maturity = maturity_map.get(str(cap.id))
            if maturity is None or maturity["reason_code"] == "no_maturity_recorded":
                # Authority has no recorded maturity (or the projection is
                # stale and hasn't caught up yet) — skip rather than
                # fabricate a gap from whichever store happens to be
                # readable. See D-R5-1.
                continue
            current = maturity["current_maturity_level"]
            target = maturity["target_maturity_level"]
            if current is None or target is None:
                continue
            if (target - current) > 0:
                capabilities.append((cap, maturity))

        created_count = 0
        updated_count = 0
        gap_results = []

        for cap, maturity in capabilities:
            # T-002: read current maturity through the single authority
            # accessor rather than `cap.current_maturity` / `.target_maturity`
            # — those names never existed on BusinessCapability (its columns
            # are `current_maturity_level` / `target_maturity_level`, the
            # projection's *source*, per ADR 0008 rule 3), so this always
            # rendered "unknown" regardless of whether an assessment existed.
            # R3-8: this loop is already filtered (see the selection above)
            # to rows where the authority accessor returned real,
            # non-None current/target values, so no reason-code fallback is
            # needed here — both labels are always numeric.
            current_label = maturity["current_maturity_level"]
            target_label = maturity["target_maturity_level"]
            # R3-7 / D-R5-1: compute the gap from the same accessor values
            # used for selection and rendered above, not from the stale,
            # unsynced `BusinessCapability.maturity_gap` derived column —
            # reading that column here would be a THIRD, potentially-
            # contradicting answer alongside the accessor values just
            # rendered into `current_label`/`target_label`. No `or 0`
            # fabrication needed: both values are guaranteed non-None here.
            computed_gap = target_label - current_label
            gap_data = {
                "source_capability_type": "business",
                "source_capability_id": cap.id,
                "name": f"Gap: {cap.name}",
                "description": (
                    f"Capability '{cap.name}' has a maturity gap of {computed_gap}. "
                    f"Current maturity: {current_label}. "
                    f"Target maturity: {target_label}."
                ),
                # D-R5-4: `maturity["current_maturity_level"]` is now always
                # a real int here (filtered above), so `is 0` (falsy but
                # assessed) must be distinguished from "not assessed" via an
                # explicit `is None` check rather than `not maturity[...]`,
                # which would misclassify a genuine 0 maturity as unassessed.
                "gap_type": "coverage" if current_label is None else "quality",
                "priority": (
                    "critical" if computed_gap >= 3
                    else "high" if computed_gap >= 2
                    else "medium"
                ),
                "severity": (
                    "critical" if computed_gap >= 3
                    else "high" if computed_gap >= 2
                    else "medium"
                ),
                "auto_generated": True,
                "generation_source": "maturity_delta",
            }

            gap, was_created = service.convert_capability_gap_to_archimate(
                gap_data, update_existing=True
            )

            # Stamp auto-generation fields (new columns from RDM-002)
            gap.auto_generated = True
            gap.generation_source = "maturity_delta"

            if was_created:
                created_count += 1
            else:
                updated_count += 1

            gap_results.append({
                "id": gap.id,
                "name": gap.name,
                "gap_type": gap.gap_type,
                "priority": gap.priority,
                "resolution_status": gap.resolution_status,
                "auto_generated": gap.auto_generated,
                "generation_source": gap.generation_source,
                "source_capability_id": gap.source_capability_id,
            })

        db.session.commit()

        return jsonify({
            "success": True,
            "created": created_count,
            "updated": updated_count,
            "gaps": gap_results,
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error detecting gaps: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/roadmap/ai-generate", methods=["POST"])
@login_required
@rate_limit(10, "1m")
@audit_log("roadmap_ai_generate")
def api_roadmap_ai_generate():
    """
    AI-assisted roadmap generation preview.

    Accepts a list of gap IDs and returns a structured preview of suggested
    WorkPackages and Plateau assignments. Does NOT write to the database —
    the preview is returned for human confirmation.

    Request body: { gap_ids: [1, 2, 3] }
    Returns: { preview: [ { gap_id, gap_name, suggested_work_package, suggested_plateau } ] }
    """
    try:
        from app.models.implementation_migration import Gap
        from app.modules.architecture.services.gap_archimate_service import PRIORITY_TIMEFRAMES

        data = request.get_json() or {}
        gap_ids = data.get("gap_ids", [])

        if not gap_ids:
            return jsonify({"success": False, "error": "gap_ids is required and must be non-empty"}), 400

        gaps = Gap.query.filter(Gap.id.in_(gap_ids)).all()
        if not gaps:
            return jsonify({"success": False, "error": "No gaps found for the provided IDs"}), 404

        from datetime import date, timedelta
        today = date.today()

        preview = []
        for gap in gaps:
            timeframe_days = PRIORITY_TIMEFRAMES.get(gap.priority or "medium", 365)
            start_offset = (
                0 if gap.priority == "critical"
                else 30 if gap.priority == "high"
                else 60
            )
            estimated_start = today + timedelta(days=start_offset)
            target_resolution = today + timedelta(days=start_offset + timeframe_days)

            # Plateau assignment: critical/high → Plateau 1, medium → Plateau 2, low → Plateau 3
            if gap.priority in ("critical", "high"):
                suggested_plateau = "Plateau 1 — Immediate"
            elif gap.priority == "medium":
                suggested_plateau = "Plateau 2 — Near Term"
            else:
                suggested_plateau = "Plateau 3 — Long Term"

            preview.append({
                "gap_id": gap.id,
                "gap_name": gap.name,
                "gap_type": gap.gap_type,
                "gap_priority": gap.priority,
                "suggested_work_package": {
                    "name": f"Resolve: {gap.name}",
                    "description": (
                        f"Work package to close gap '{gap.name}'. "
                        f"Gap type: {gap.gap_type or 'coverage'}. "
                        f"Priority: {gap.priority or 'medium'}."
                    ),
                    "priority": gap.priority or "medium",
                    "estimated_start_date": estimated_start.isoformat(),
                    "target_resolution_date": target_resolution.isoformat(),
                    "element_type": "WorkPackage",
                    "layer": "implementation",
                },
                "suggested_plateau": suggested_plateau,
            })

        return jsonify({
            "success": True,
            "preview": preview,
            "total_gaps": len(gaps),
        })

    except Exception as e:
        current_app.logger.error(f"Error generating AI roadmap preview: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/capabilities/<int:capability_id>/traceability")
@login_required
@rate_limit(60, "1m")
def api_capability_traceability(capability_id):
    """
    Full traceability chain: capability → applications → technologies → vendors.

    Returns a unified view linking a capability down through the
    application, technology, and vendor layers in a single API call.
    """
    try:
        import json as _json

        from app.models.application_portfolio import ApplicationComponent
        from app.models.business_capabilities import (
            ApplicationCapabilityCoverage,
            BusinessCapability,
        )

        capability = BusinessCapability.query.get(capability_id)
        if not capability:
            return jsonify({"success": False, "error": "Capability not found"}), 404

        mappings = (
            ApplicationCapabilityCoverage.query
            .filter_by(capability_id=capability_id)
            .all()
        )

        app_ids = [m.application_component_id for m in mappings]
        apps = (
            ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all()
            if app_ids
            else []
        )

        vendor_set = set()
        tech_set = set()
        app_list = []

        for app_comp in apps:
            # Parse technology_stack JSON field
            technologies = []
            if app_comp.technology_stack:
                try:
                    raw = _json.loads(app_comp.technology_stack)
                    if isinstance(raw, list):
                        for item in raw:
                            if isinstance(item, str):
                                technologies.append({"name": item, "type": "general"})
                                tech_set.add(item)
                            elif isinstance(item, dict):
                                technologies.append({
                                    "name": item.get("name", "Unknown"),
                                    "type": item.get("type", "general"),
                                })
                                tech_set.add(item.get("name", "Unknown"))
                except (ValueError, TypeError):
                    logger.exception("Failed to JSON parsing")
                    pass

            # Vendor via primary_vendor_product relationship
            vendors = []
            if app_comp.primary_vendor_product:
                vp = app_comp.primary_vendor_product
                vendor_name = (
                    vp.vendor_organization.name if vp.vendor_organization else None
                )
                vendors.append({
                    "id": vp.id,
                    "product_name": vp.name,
                    "vendor_name": vendor_name,
                })
                if vendor_name:
                    vendor_set.add(vendor_name)

            app_list.append({
                "id": app_comp.id,
                "name": app_comp.name,
                "status": getattr(app_comp, "lifecycle_status", None) or getattr(app_comp, "status", None),
                "technologies": technologies,
                "vendors": vendors,
            })

        return jsonify({
            "success": True,
            "capability": {
                "id": capability.id,
                "name": capability.name,
                "maturity": getattr(capability, "maturity_level", None),
            },
            "applications": app_list,
            "summary": {
                "app_count": len(app_list),
                "tech_count": len(tech_set),
                "vendor_count": len(vendor_set),
            },
        })

    except Exception as e:
        current_app.logger.error(f"Error fetching traceability for capability {capability_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

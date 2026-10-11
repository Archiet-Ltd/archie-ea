"""
Process-capability integration & traceability routes.

Extracted from capability_map_routes.py (lines 1975-2625, 2740-3015, 4074-4275).
Routes registered on the shared ``capability_map`` blueprint.
"""


from flask import current_app, jsonify, request
from flask_login import login_required

from app.decorators import audit_log

from . import capability_map


@capability_map.route("/api/process-gaps")
@login_required
def api_process_gaps():
    """
    API endpoint for Process Gap Analysis - identifies processes without application support.

    Uses the Process Classification Framework (APQC-style) to show:
    - Processes without any application support
    - Processes with partial automation
    - Process-to-Application mapping gaps
    """
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.apqc_process import APQCProcess
        from app.models.process_data import BusinessProcess
        from app.models.relationship_tables import ApplicationProcessSupport

        # Get filter parameters
        level_filter = request.args.get("level", "")
        type_filter = request.args.get("type", "")  # core, support, management
        category_filter = request.args.get("category", "")  # operational, strategic, enabling
        search_filter = request.args.get("search", "").lower()

        # Get all business processes (including those linked to APQC)
        # Temporarily disabled due to missing migration - just use APQC processes
        try:
            all_processes = BusinessProcess.query.all()
            mapped_apqc_ids = {bp.apqc_process_id for bp in all_processes if bp.apqc_process_id}
            unmapped_apqc_processes = APQCProcess.query.filter(
                ~APQCProcess.id.in_(mapped_apqc_ids)
            ).all()
            # Combine: BusinessProcess + unmapped APQC processes
            combined_processes = all_processes + unmapped_apqc_processes
        except Exception as e:
            # Fallback: just use APQC processes
            current_app.logger.warning(f"BusinessProcess query failed, using APQC only: {e}")
            combined_processes = APQCProcess.query.all()

        # Get all process-application mappings
        all_mappings = ApplicationProcessSupport.query.filter_by(is_active=True).all()
        mapped_process_ids = {m.business_process_id for m in all_mappings}

        # OPTIMIZATION: Pre-fetch all referenced applications in batch to avoid N + 1 queries
        app_ids_in_mappings = {m.application_component_id for m in all_mappings}
        all_apps_for_mappings = (
            ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(app_ids_in_mappings)
            ).all()
            if app_ids_in_mappings
            else []
        )
        apps_by_id = {a.id: a for a in all_apps_for_mappings}

        # Build mapping details for partially mapped processes
        mapping_details = {}
        for m in all_mappings:
            if m.business_process_id not in mapping_details:
                mapping_details[m.business_process_id] = []
            # Use pre-fetched lookup instead of query
            app = apps_by_id.get(m.application_component_id)
            if app:
                mapping_details[m.business_process_id].append(
                    {
                        "app_id": str(app.id),
                        "app_name": app.name,
                        "support_type": m.support_type,
                        "automation_level": m.automation_level or 0,
                        "criticality": m.criticality,
                    }
                )

        # Build process data with gap analysis
        process_gap_data = []
        for process in combined_processes:
            # Handle different field names for BusinessProcess vs APQCProcess
            if hasattr(process, "name"):  # model-safety-ok: polymorphic - BusinessProcess has name, APQCProcess has process_name
                process_name = process.name
                process_description = process.description
                process_level = process.level
                process_code = process.process_code
                process_owner = process.process_owner
                business_unit = process.business_unit
                is_automated = process.is_automated or False
                automation_percentage = process.automation_percentage or 0
                maturity_level = process.maturity_level or 1
                standardization_level = process.standardization_level or "ad_hoc"
                digitalization_level = process.digitalization_level or "manual"
                sox_relevant = process.sox_relevant or False
                gdpr_relevant = process.gdpr_relevant or False
                cycle_time_hours = process.cycle_time_hours
                frequency = process.frequency
                value_chain_stage = process.value_chain_stage
                status = process.status or "active"

                # Get APQC data through relationship if available
                if process.apqc_process:
                    # Use APQC data if available, fallback to BusinessProcess
                    process_name = process.apqc_process.process_name
                    process_description = process.apqc_process.process_description
                    process_code = process.apqc_process.process_code
                    process_owner = process.apqc_process.process_owner or process.process_owner
                    maturity_level = process.apqc_process.process_maturity or process.maturity_level
                else:
                    pass

            else:  # APQCProcess (unmapped APQC processes)
                process_name = process.process_name
                process_description = process.process_description
                # Derive level from process_code (e.g., "1.0" -> level 0, "1.1" -> level 1, "1.1.1" -> level 2)
                if process.process_code:
                    level_parts = process.process_code.split(".")
                    process_level = len(level_parts) - 1
                else:
                    process_level = 2  # Default to Process level
                process_code = process.process_code
                process_owner = process.process_owner
                business_unit = None
                is_automated = False  # APQC processes don't have this field by default
                automation_percentage = 0
                maturity_level = process.process_maturity or 1
                standardization_level = "ad_hoc"  # Default for APQC
                digitalization_level = "manual"  # Default for APQC
                sox_relevant = False  # APQC processes don't have this by default
                gdpr_relevant = False  # APQC processes don't have this by default
                cycle_time_hours = None
                frequency = None
                value_chain_stage = None
                status = "active"

            # Apply filters
            if level_filter and str(process_level) != level_filter:
                continue
            if type_filter and process.process_type != type_filter:
                continue
            if category_filter and process.process_category != category_filter:
                continue
            if search_filter:
                if (
                    search_filter not in (process_name or "").lower()
                    and search_filter not in (process_code or "").lower()
                    and search_filter not in (process_owner or "").lower()
                ):
                    continue

            is_mapped = process.id in mapped_process_ids
            apps = mapping_details.get(process.id, [])

            # Calculate automation coverage
            total_automation = sum(app.get("automation_level", 0) for app in apps)
            avg_automation = total_automation / len(apps) if apps else 0

            # Determine gap status
            if not is_mapped:
                gap_status = "no_coverage"
                gap_severity = "critical" if process.process_type == "core" else "high"
            elif avg_automation < 30:
                gap_status = "minimal_automation"
                gap_severity = "high"
            elif avg_automation < 70:
                gap_status = "partial_automation"
                gap_severity = "medium"
            else:
                gap_status = "well_automated"
                gap_severity = "low"

            # Calculate business impact based on process attributes
            business_impact = 0
            if process.process_type == "core":
                business_impact += 40
            elif process.process_type == "support":
                business_impact += 20
            elif process.process_type == "management":
                business_impact += 30

            if sox_relevant or gdpr_relevant:
                business_impact += 30

            if maturity_level:
                # Lower maturity = higher impact of gaps
                business_impact += (5 - maturity_level) * 6

            business_impact = min(business_impact, 100)

            process_gap_data.append(
                {
                    "id": str(process.id),
                    "name": process_name,
                    "process_code": process_code,
                    "description": process_description,
                    "level": process_level,  # Use derived level for APQC
                    "level_name": {
                        0: "Value Chain",
                        1: "Process Group",
                        2: "Process",
                        3: "Subprocess",
                        4: "Activity",
                    }.get(process_level, "Process"),
                    "process_type": process.process_type or "unknown",
                    "process_category": process.process_category or "operational",
                    "value_chain_stage": value_chain_stage,
                    "process_owner": process_owner or "Unassigned",
                    "business_unit": business_unit,
                    "is_automated": is_automated,
                    "automation_percentage": automation_percentage,
                    "maturity_level": maturity_level,
                    "standardization_level": standardization_level,
                    "digitalization_level": digitalization_level,
                    "sox_relevant": sox_relevant,
                    "gdpr_relevant": gdpr_relevant,
                    "cycle_time_hours": cycle_time_hours,
                    "frequency": frequency,
                    "is_mapped": is_mapped,
                    "mapping_count": len(apps),
                    "applications": apps,
                    "avg_automation_coverage": round(avg_automation, 1),
                    "gap_status": gap_status,
                    "gap_severity": gap_severity,
                    "business_impact": business_impact,
                    "status": status,
                }
            )

        # Sort by business impact (highest first), then by gap severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        process_gap_data.sort(
            key=lambda x: (-x["business_impact"], severity_order.get(x["gap_severity"], 4))
        )

        # Calculate statistics
        total_processes = len(process_gap_data)
        unmapped_processes = len([p for p in process_gap_data if not p["is_mapped"]])
        partially_automated = len(
            [p for p in process_gap_data if p["gap_status"] == "partial_automation"]
        )
        minimal_automation = len(
            [p for p in process_gap_data if p["gap_status"] == "minimal_automation"]
        )
        well_automated = len([p for p in process_gap_data if p["gap_status"] == "well_automated"])

        critical_gaps = len([p for p in process_gap_data if p["gap_severity"] == "critical"])
        high_gaps = len([p for p in process_gap_data if p["gap_severity"] == "high"])

        # Get unique filter options
        process_types = list(set(p["process_type"] for p in process_gap_data if p["process_type"]))
        process_categories = list(
            set(p["process_category"] for p in process_gap_data if p["process_category"])
        )
        levels = list(set(str(p["level"]) for p in process_gap_data))

        return jsonify(
            {
                "process_gaps": process_gap_data,
                "statistics": {
                    "total_processes": total_processes,
                    "unmapped_processes": unmapped_processes,
                    "partially_automated": partially_automated,
                    "minimal_automation": minimal_automation,
                    "well_automated": well_automated,
                    "coverage_percentage": round(
                        ((total_processes - unmapped_processes) / total_processes) * 100, 2
                    )
                    if total_processes > 0
                    else 0,
                    "automation_coverage": round(
                        sum(p["avg_automation_coverage"] for p in process_gap_data)
                        / total_processes,
                        1,
                    )
                    if total_processes > 0
                    else 0,
                    "critical_gaps": critical_gaps,
                    "high_gaps": high_gaps,
                },
                "filters": {
                    "process_types": process_types,
                    "process_categories": process_categories,
                    "levels": sorted(levels),
                },
            }
        )
    except ImportError as e:
        # The zeroed statistics here were indistinguishable from a measured "no
        # gaps, 0% coverage" reading. The caller throws on `error` and renders a
        # retry panel carrying this message.
        current_app.logger.exception(f"Process models not available: {e}")
        return jsonify(
            {
                "error": "Process models not configured. Please set up BusinessProcess "
                         "and ApplicationProcessSupport tables.",
            }
        ), 500
    except Exception as e:
        current_app.logger.error(f"Error in process gap analysis: {e}")
        return jsonify({"error": "An internal error occurred", "process_gaps": [], "statistics": {}}), 500


# =============================================================================
# NEW ENDPOINTS: Capability-Process Integration & Traceability
# =============================================================================


@capability_map.route("/api/capabilities/<capability_id>/processes")
@login_required
def api_capability_processes(capability_id):
    """
    API endpoint to get all processes linked to a capability.

    Returns processes via UnifiedCapabilityProcessMapping -> APQCProcess -> BusinessProcess chain.
    Includes application support information for each process.

    Optimized: Pre-fetches related data in batch to avoid N + 1 queries.
    """
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.apqc_process import APQCProcess, CapabilityProcessMapping
        from app.models.business_capabilities import BusinessCapability
        from app.models.process_data import BusinessProcess
        from app.models.relationship_tables import ApplicationProcessSupport

        # Convert capability_id
        try:
            capability_id_int = int(str(capability_id).strip())
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid capability ID: {capability_id}"}), 400

        # Find capability in BusinessCapability (real APQC data)
        capability = BusinessCapability.query.get(capability_id_int)
        if not capability:
            return jsonify({"error": f"Capability not found: {capability_id}"}), 404

        # Get process mappings
        process_mappings = CapabilityProcessMapping.query.filter_by(
            capability_id=capability_id_int
        ).all()

        # OPTIMIZATION: Pre-fetch all APQC processes in batch
        apqc_ids = {m.apqc_process_id for m in process_mappings}
        all_apqc = APQCProcess.query.filter(APQCProcess.id.in_(apqc_ids)).all() if apqc_ids else []
        apqc_by_id = {p.id: p for p in all_apqc}

        # Pre-fetch all business processes linked to these APQC processes
        all_business_processes = (
            BusinessProcess.query.filter(BusinessProcess.apqc_process_id.in_(apqc_ids)).all()
            if apqc_ids
            else []
        )
        bp_by_apqc_id = {}
        for bp in all_business_processes:
            if bp.apqc_process_id not in bp_by_apqc_id:
                bp_by_apqc_id[bp.apqc_process_id] = []
            bp_by_apqc_id[bp.apqc_process_id].append(bp)

        # Pre-fetch all application process supports
        bp_ids = {bp.id for bp in all_business_processes}
        all_supports = (
            ApplicationProcessSupport.query.filter(
                ApplicationProcessSupport.business_process_id.in_(bp_ids),
                ApplicationProcessSupport.is_active == True,
            ).all()
            if bp_ids
            else []
        )
        supports_by_bp_id = {}
        for s in all_supports:
            if s.business_process_id not in supports_by_bp_id:
                supports_by_bp_id[s.business_process_id] = []
            supports_by_bp_id[s.business_process_id].append(s)

        # Pre-fetch all applications referenced by supports
        app_ids = {s.application_component_id for s in all_supports}
        all_apps = (
            ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all()
            if app_ids
            else []
        )
        apps_by_id = {a.id: a for a in all_apps}

        processes_data = []

        for mapping in process_mappings:
            # Get APQC process from pre-fetched data
            apqc_process = apqc_by_id.get(mapping.apqc_process_id)
            if not apqc_process:
                continue

            # Get business processes linked to this APQC process from pre-fetched data
            business_processes = bp_by_apqc_id.get(apqc_process.id, [])

            # For each business process, get supporting applications from pre-fetched data
            for bp in business_processes:
                app_supports = supports_by_bp_id.get(bp.id, [])

                applications = []
                for support in app_supports:
                    app = apps_by_id.get(support.application_component_id)
                    if app:
                        applications.append(
                            {
                                "id": str(app.id),
                                "name": app.name,
                                "support_type": support.support_type,
                                "automation_level": support.automation_level,
                                "criticality": support.criticality,
                                "is_system_of_record": support.is_system_of_record,
                            }
                        )

                processes_data.append(
                    {
                        "apqc_process": {
                            "id": apqc_process.id,
                            "code": apqc_process.process_code,
                            "name": apqc_process.process_name,
                            "category": apqc_process.process_category,
                            "maturity": apqc_process.process_maturity,
                        },
                        "business_process": {
                            "id": bp.id,
                            "name": bp.name,
                            "process_code": bp.process_code,
                            "level": bp.level,
                            "automation_percentage": bp.automation_percentage,
                            "process_type": bp.process_type,
                        },
                        "mapping": {
                            "relationship_type": mapping.relationship_type,
                            "relationship_strength": mapping.relationship_strength,
                            "impact_level": mapping.impact_level,
                            "process_contribution": mapping.process_contribution,
                        },
                        "applications": applications,
                        "application_count": len(applications),
                        "has_gap": len(applications) == 0,
                    }
                )

        # Calculate statistics
        total_processes = len(processes_data)
        processes_with_apps = len([p for p in processes_data if p["application_count"] > 0])
        processes_with_gaps = len([p for p in processes_data if p["has_gap"]])

        return jsonify(
            {
                "capability": {
                    "id": str(capability.id),
                    "name": capability.name,
                    "code": capability.code,
                },
                "processes": processes_data,
                "statistics": {
                    "total_processes": total_processes,
                    "processes_with_applications": processes_with_apps,
                    "processes_with_gaps": processes_with_gaps,
                    "coverage_percentage": round(
                        (processes_with_apps / total_processes * 100) if total_processes > 0 else 0,
                        1,
                    ),
                },
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting capability processes: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/processes/<process_id>/capabilities")
@login_required
def api_process_capabilities(process_id):
    """
    API endpoint to get all capabilities that a process enables/supports.

    Supports both BusinessProcess IDs and APQCProcess IDs.
    Reverse lookup: Process -> APQCProcess -> UnifiedCapabilityProcessMapping -> UnifiedCapability
    """
    try:
        from app.models.apqc_process import APQCProcess, CapabilityProcessMapping
        from app.models.business_capabilities import BusinessCapability
        from app.models.process_data import BusinessProcess
        from app.models.apqc_process import CapabilityProcessMapping as CPM

        capabilities_data = []
        process_info = None
        apqc_process = None

        # Try to find as BusinessProcess first
        business_process = BusinessProcess.query.get(process_id)

        if business_process:
            process_info = {
                "id": str(business_process.id),
                "name": business_process.name,
                "process_code": business_process.process_code,
                "level": business_process.level,
                "process_type": business_process.process_type,
                "automation_percentage": business_process.automation_percentage,
                "source": "business_process",
            }
            # If process has APQC mapping, get the APQC process
            if business_process.apqc_process_id:
                apqc_process = APQCProcess.query.get(business_process.apqc_process_id)
        else:
            # Try to find as APQCProcess directly
            apqc_process = APQCProcess.query.get(process_id)
            if apqc_process:
                # Derive level from process_code (e.g., "1.0"=1, "1.1"=2, "1.1.1"=3)
                apqc_level = (
                    len(apqc_process.process_code.split(".")) if apqc_process.process_code else 1
                )
                process_info = {
                    "id": str(apqc_process.id),
                    "name": apqc_process.process_name,
                    "process_code": apqc_process.process_code,
                    "level": apqc_level,
                    "process_type": apqc_process.process_type or "apqc",
                    "automation_percentage": None,
                    "source": "apqc_process",
                }

        if not process_info:
            return jsonify({"error": f"Process not found: {process_id}"}), 404

        # Find capabilities via APQC process mapping
        if apqc_process:
            mappings = CPM.query.filter_by(
                apqc_process_id=apqc_process.id
            ).all()

            for mapping in mappings:
                capability = BusinessCapability.query.get(mapping.capability_id)
                if capability:
                    capabilities_data.append(
                        {
                            "id": str(capability.id),
                            "name": capability.name,
                            "code": capability.code,
                            "level": capability.level,
                            "domain": {
                                "id": None,
                                "name": capability.business_domain or "Unknown",
                                "code": "UNK",
                            },
                            "strategic_importance": capability.strategic_importance,
                            "business_criticality": getattr(capability, 'business_criticality', None),
                            "current_maturity": capability.current_maturity_level,
                            "mapping": {
                                "relationship_type": mapping.relationship_type,
                                "relationship_strength": mapping.relationship_strength,
                                "impact_level": mapping.impact_level,
                                "process_contribution": mapping.process_contribution,
                            },
                        }
                    )

            # Fall back to CapabilityProcessMapping -> BusinessCapability if no unified results
            if not capabilities_data:
                from app.services.apqc_mapping_tenant_fence import (
                    fenced_capability_mappings_query,
                )

                # apqc_process_id is a shared reference id, unlike the
                # capability_id this route's siblings already fence via a
                # TenantMixin .get() before reaching here -- unfenced, this
                # loaded every organisation's mappings for the process into
                # memory (found in this consolidation sweep).
                biz_mappings = fenced_capability_mappings_query().filter(
                    CapabilityProcessMapping.apqc_process_id == apqc_process.id
                ).all()

                for mapping in biz_mappings:
                    capability = BusinessCapability.query.get(mapping.capability_id)
                    if capability:
                        capabilities_data.append(
                            {
                                "id": str(capability.id),
                                "name": capability.name,
                                "code": capability.code,
                                "level": capability.level,
                                "domain": {
                                    "id": None,
                                    "name": capability.business_domain or "Unknown",
                                    "code": "UNK",
                                },
                                "strategic_importance": capability.strategic_importance,
                                "business_criticality": capability.business_criticality,
                                "current_maturity": capability.current_maturity_level,
                                "mapping": {
                                    "relationship_type": mapping.relationship_type,
                                    "relationship_strength": mapping.relationship_strength,
                                    "impact_level": mapping.impact_level,
                                    "process_contribution": mapping.process_contribution,
                                },
                            }
                        )

        # Build APQC process info if available
        apqc_info = None
        if apqc_process:
            apqc_level = (
                len(apqc_process.process_code.split(".")) if apqc_process.process_code else 1
            )
            apqc_info = {
                "id": str(apqc_process.id),
                "code": apqc_process.process_code,
                "name": apqc_process.process_name,
                "level": apqc_level,
                "process_type": apqc_process.process_type,
                "category": apqc_process.process_category,
            }

        return jsonify(
            {
                "process": process_info,
                "apqc_process": apqc_info,
                "capabilities": capabilities_data,
                "capability_count": len(capabilities_data),
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting process capabilities: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/capabilities/<capability_id>/full-traceability")
@login_required
def api_capability_full_traceability(capability_id):
    """
    API endpoint for complete capability traceability chain.

    Returns: Capability -> Processes -> Applications with full gap analysis.
    This is the comprehensive view showing the complete value chain.

    Optimized: Pre-fetches related data in batch to avoid N + 1 queries.
    """
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.apqc_process import APQCProcess, CapabilityProcessMapping
        from app.models.business_capabilities import (
            ApplicationCapabilityCoverage,
            BusinessCapability,
        )
        from app.models.process_data import BusinessProcess
        from app.models.relationship_tables import ApplicationProcessSupport

        # Convert capability_id
        try:
            capability_id_int = int(str(capability_id).strip())
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid capability ID: {capability_id}"}), 400

        # Find capability in BusinessCapability (real APQC data)
        capability = BusinessCapability.query.get(capability_id_int)
        if not capability:
            return jsonify({"error": f"Capability not found: {capability_id}"}), 404

        domain_name = capability.business_domain or "Unknown"
        domain_code = "UNK"

        # Get direct application mappings via ApplicationCapabilityCoverage
        direct_app_mappings = ApplicationCapabilityCoverage.query.filter_by(
            capability_id=capability_id_int
        ).all()

        # Pre-fetch applications for direct mappings
        direct_app_ids = {m.application_component_id for m in direct_app_mappings}
        direct_apps_list = (
            ApplicationComponent.query.filter(ApplicationComponent.id.in_(direct_app_ids)).all()
            if direct_app_ids
            else []
        )
        direct_apps_by_id = {a.id: a for a in direct_apps_list}

        direct_applications = []
        for mapping in direct_app_mappings:
            app = direct_apps_by_id.get(mapping.application_component_id)
            if app:
                direct_applications.append(
                    {
                        "id": str(app.id),
                        "name": app.name,
                        "component_type": app.component_type,
                        "lifecycle_status": app.lifecycle_status,
                        "business_criticality": app.business_criticality,
                        "mapping": {
                            "coverage_percentage": mapping.coverage_percentage,
                            "support_level": mapping.support_level,
                            "is_strategic": mapping.is_strategic,
                            "investment_priority": mapping.investment_priority,
                        },
                    }
                )

        # Get process chain: Capability -> APQC Process -> Business Process -> Applications
        process_mappings = CapabilityProcessMapping.query.filter_by(
            capability_id=capability_id_int
        ).all()

        # OPTIMIZATION: Pre-fetch all APQC processes
        apqc_ids = {pm.apqc_process_id for pm in process_mappings}
        all_apqc = APQCProcess.query.filter(APQCProcess.id.in_(apqc_ids)).all() if apqc_ids else []
        apqc_by_id = {p.id: p for p in all_apqc}

        # Pre-fetch all business processes linked to these APQC processes
        all_business_processes = (
            BusinessProcess.query.filter(BusinessProcess.apqc_process_id.in_(apqc_ids)).all()
            if apqc_ids
            else []
        )
        bp_by_apqc_id = {}
        for bp in all_business_processes:
            if bp.apqc_process_id not in bp_by_apqc_id:
                bp_by_apqc_id[bp.apqc_process_id] = []
            bp_by_apqc_id[bp.apqc_process_id].append(bp)

        # Pre-fetch all application process supports
        bp_ids = {bp.id for bp in all_business_processes}
        all_supports = (
            ApplicationProcessSupport.query.filter(
                ApplicationProcessSupport.business_process_id.in_(bp_ids),
                ApplicationProcessSupport.is_active == True,
            ).all()
            if bp_ids
            else []
        )
        supports_by_bp_id = {}
        for s in all_supports:
            if s.business_process_id not in supports_by_bp_id:
                supports_by_bp_id[s.business_process_id] = []
            supports_by_bp_id[s.business_process_id].append(s)

        # Pre-fetch all applications referenced by supports
        process_app_ids = {s.application_component_id for s in all_supports}
        all_process_apps_list = (
            ApplicationComponent.query.filter(ApplicationComponent.id.in_(process_app_ids)).all()
            if process_app_ids
            else []
        )
        apps_by_id = {a.id: a for a in all_process_apps_list}

        process_chain = []
        all_process_apps = set()

        for pm in process_mappings:
            apqc_process = apqc_by_id.get(pm.apqc_process_id)
            if not apqc_process:
                continue

            # Get business processes for this APQC process from pre-fetched data
            business_processes = bp_by_apqc_id.get(apqc_process.id, [])

            bp_data = []
            for bp in business_processes:
                # Get applications supporting this process from pre-fetched data
                app_supports = supports_by_bp_id.get(bp.id, [])

                apps = []
                for support in app_supports:
                    app = apps_by_id.get(support.application_component_id)
                    if app:
                        all_process_apps.add(app.id)
                        apps.append(
                            {
                                "id": str(app.id),
                                "name": app.name,
                                "support_type": support.support_type,
                                "automation_level": support.automation_level,
                                "is_system_of_record": support.is_system_of_record,
                            }
                        )

                bp_data.append(
                    {
                        "id": bp.id,
                        "name": bp.name,
                        "process_code": bp.process_code,
                        "automation_percentage": bp.automation_percentage,
                        "applications": apps,
                        "has_gap": len(apps) == 0,
                    }
                )

            process_chain.append(
                {
                    "apqc_process": {
                        "id": apqc_process.id,
                        "code": apqc_process.process_code,
                        "name": apqc_process.process_name,
                    },
                    "mapping": {
                        "relationship_type": pm.relationship_type,
                        "impact_level": pm.impact_level,
                    },
                    "business_processes": bp_data,
                }
            )

        # Gap Analysis
        total_processes = sum(len(pc["business_processes"]) for pc in process_chain)
        processes_with_apps = sum(
            1 for pc in process_chain for bp in pc["business_processes"] if not bp["has_gap"]
        )
        processes_with_gaps = total_processes - processes_with_apps

        # Identify gaps
        gaps = {
            "process_gaps": [
                {
                    "process_name": bp["name"],
                    "process_code": bp["process_code"],
                    "apqc_process": pc["apqc_process"]["name"],
                    "severity": "high" if pc["mapping"]["impact_level"] == "critical" else "medium",
                }
                for pc in process_chain
                for bp in pc["business_processes"]
                if bp["has_gap"]
            ],
            "low_automation": [
                {
                    "process_name": bp["name"],
                    "automation_percentage": bp["automation_percentage"],
                    "recommendation": "Increase automation coverage",
                }
                for pc in process_chain
                for bp in pc["business_processes"]
                if (bp["automation_percentage"] or 0) < 50 and not bp["has_gap"]
            ],
            "non_strategic_apps": [
                app
                for app in direct_applications
                if app.get("lifecycle_status") in ["phase_out", "retire", "end_of_life"]
            ],
        }

        return jsonify(
            {
                "capability": {
                    "id": str(capability.id),
                    "name": capability.name,
                    "code": capability.code,
                    "level": capability.level,
                    "domain": {
                        "name": domain_name,
                        "code": domain_code,
                    },
                    "strategic_importance": capability.strategic_importance,
                    "current_maturity": capability.current_maturity_level,
                    "target_maturity": capability.target_maturity_level,
                },
                "direct_applications": direct_applications,
                "process_chain": process_chain,
                "gaps": gaps,
                "statistics": {
                    "direct_application_count": len(direct_applications),
                    "total_processes": total_processes,
                    "processes_with_applications": processes_with_apps,
                    "processes_with_gaps": processes_with_gaps,
                    "process_coverage_percentage": round(
                        (processes_with_apps / total_processes * 100) if total_processes > 0 else 0,
                        1,
                    ),
                    "unique_applications_via_processes": len(all_process_apps),
                    "total_gaps_identified": len(gaps["process_gaps"])
                    + len(gaps["non_strategic_apps"]),
                },
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting capability traceability: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# =============================================================================
# Process Mapping Endpoints (99.99% copy from ACM)
# =============================================================================


@capability_map.route("/api/process-gaps/process/<int:process_id>/applications")
@login_required
def api_process_applications(process_id):
    """
    Get all applications with their mapping status to a specific APQC process.

    ---
    tags:
      - Process Gaps
      - Process Mapping
    parameters:
      - name: process_id
        in: path
        required: true
        type: integer
        description: APQC Process ID
    responses:
      200:
        description: Applications list with mapping status
    """
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.apqc_process import APQCProcess, ProcessApplicationMapping
        from app.services.apqc_mapping_tenant_fence import (
            fenced_application_mappings_query,
        )

        # Verify process exists
        process = APQCProcess.query.get(process_id)
        if not process:
            return jsonify({"error": f"Process {process_id} not found"}), 404

        # Get all applications
        applications = ApplicationComponent.query.all()

        # Get existing mappings for this process. ProcessApplicationMapping
        # has no organization_id of its own; unfenced, this loaded every
        # organisation's mappings for the process into memory (pr303-v2
        # review, DEFECT D4).
        existing_mappings = fenced_application_mappings_query().filter(
            ProcessApplicationMapping.apqc_process_id == process_id
        ).all()

        # Create mapping lookup
        mapping_lookup = {m.application_id: m for m in existing_mappings}

        # Build applications data
        applications_data = []
        for app in applications:
            mapping = mapping_lookup.get(app.id)
            applications_data.append(
                {
                    "id": app.id,
                    "name": app.name,
                    "type": app.application_type or "Unknown",
                    "domain": app.business_domain or "Unknown",
                    "status": app.deployment_status or "active",
                    "description": app.description,
                    "is_mapped": mapping is not None,
                    "mapping_id": mapping.id if mapping else None,
                    "support_level": mapping.support_level if mapping else None,
                    "automation_level": mapping.automation_level if mapping else None,
                    "notes": mapping.assessment_notes if mapping else None,
                }
            )

        return jsonify(
            {
                "success": True,
                "process": {
                    "id": process.id,
                    "name": process.process_name,
                    "code": process.process_code,
                    "type": process.process_type,
                    "owner": process.process_owner,
                },
                "applications": applications_data,
                "total_applications": len(applications_data),
                "mapped_applications": len(existing_mappings),
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error loading process applications: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/process-gaps/test")
@login_required
def api_process_test():
    """Simple test endpoint to verify routing works."""
    return jsonify({"success": True, "message": "Process API routing works"})


@capability_map.route("/api/check-auth")
@login_required
def check_auth():
    """Check if user is authenticated for Process Mapping Modal."""
    try:
        from flask_login import current_user

        if current_user.is_authenticated:
            return jsonify(
                {
                    "authenticated": True,
                    "user_id": current_user.id,
                    "username": getattr(current_user, "username", "User"),
                }
            )
        else:
            return jsonify({"authenticated": False, "message": "User not authenticated"})
    except Exception as e:
        current_app.logger.error(f"Error checking authentication: {e}")
        return jsonify({"authenticated": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/process-gaps/mappings/bulk", methods=["POST"])
@login_required
@audit_log("save_process_bulk_mappings")
def api_process_bulk_mappings():
    """Process bulk mappings with error handling."""
    try:
        from datetime import datetime

        from app import db
        from app.models.apqc_process import ProcessApplicationMapping
        from app.services.apqc_mapping_tenant_fence import (
            application_mapping_in_caller_org,
            application_owned_by_caller,
        )

        # Debug: Log incoming data
        data = request.get_json()
        current_app.logger.debug(f"Process bulk mappings request data: {data}")

        mappings = data.get("mappings", [])
        current_app.logger.debug(f"Number of mappings to process: {len(mappings)}")

        if not mappings:
            return jsonify({"success": False, "error": "No mappings provided"}), 400

        created_count = 0
        updated_count = 0

        for mapping_data in mappings:
            app_id = mapping_data.get("application_id")
            process_id = mapping_data.get("apqc_process_id")
            mapping_id = mapping_data.get("mapping_id")

            current_app.logger.debug(
                f"Processing mapping: app_id={app_id}, process_id={process_id}, mapping_id={mapping_id}"
            )

            if not app_id or not process_id:
                current_app.logger.warning(
                    f"Skipping mapping due to missing IDs: app_id={app_id}, process_id={process_id}"
                )
                continue

            if mapping_id:
                # Update existing mapping. ProcessApplicationMapping has no
                # organization_id of its own; unfenced, this updated any
                # organisation's mapping by caller-supplied mapping_id
                # (pr303-v1 review, the original finding this whole
                # consolidation started from).
                mapping = application_mapping_in_caller_org(mapping_id)
                if mapping:
                    mapping.support_level = mapping_data.get("support_level", "partial")
                    mapping.automation_level = mapping_data.get("automation_level", 1)
                    mapping.process_coverage = mapping_data.get("process_coverage", 50)
                    mapping.assessment_notes = mapping_data.get("notes", "")
                    mapping.updated_at = datetime.utcnow()
                    updated_count += 1
                    current_app.logger.debug(f"Updated mapping {mapping_id}")
            else:
                # Create new mapping. Verify app_id belongs to the caller's
                # organisation first -- otherwise a caller could point a new
                # mapping at another organisation's application.
                if not application_owned_by_caller(app_id):
                    current_app.logger.warning(
                        f"Skipping mapping: application {app_id} not found in caller's organisation"
                    )
                    continue

                current_app.logger.debug(
                    f"Creating new mapping for app_id={app_id}, process_id={process_id}"
                )

                # Use SQLAlchemy ORM instead of raw SQL
                new_mapping = ProcessApplicationMapping(
                    application_id=app_id,
                    apqc_process_id=process_id,
                    support_level=mapping_data.get("support_level", "partial"),
                    automation_level=mapping_data.get("automation_level", 1),
                    process_coverage=mapping_data.get("process_coverage", 50),
                    assessment_notes=mapping_data.get("notes", ""),
                    created_at=datetime.utcnow(),
                )
                db.session.add(new_mapping)
                created_count += 1

        db.session.commit()

        result = {
            "success": True,
            "created": created_count,
            "updated": updated_count,
            "message": f"Successfully saved {created_count + updated_count} mappings",
        }

        current_app.logger.debug(f"Process bulk mappings result: {result}")
        return jsonify(result)

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving process bulk mappings: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

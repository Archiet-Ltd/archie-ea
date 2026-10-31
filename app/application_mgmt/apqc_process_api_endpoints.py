"""
APQC Process API Endpoints for Vendor Analysis

Provides endpoints for loading APQC PCF process hierarchy and vendor-process matching.
"""

from flask import current_app, jsonify, request
from flask_login import login_required

from .. import db
from . import application_mgmt


@application_mgmt.route("/api/apqc-processes", methods=["GET"])
@login_required
def api_get_apqc_processes():
    """Get APQC processes filtered by level."""
    try:
        from app.models.apqc_process import APQCProcess

        level = request.args.get("level", type=int)
        category = request.args.get("category")

        query = APQCProcess.query

        if category:
            query = query.filter(APQCProcess.process_category == category)

        if level:
            # Prefer database-level filtering on category_level_N columns
            # when available, with in-memory fallback on process_code parsing
            if level == 1:
                query = query.filter(
                    APQCProcess.category_level_1.isnot(None),
                    db.or_(
                        APQCProcess.category_level_2.is_(None),
                        APQCProcess.category_level_2 == "",
                    ),
                )
                processes = query.order_by(APQCProcess.process_code).all()
                if not processes:
                    all_procs = APQCProcess.query.all()
                    processes = [p for p in all_procs if p.apqc_level == 1]
            else:
                all_procs = query.all()
                processes = [p for p in all_procs if p.apqc_level == level]
        else:
            processes = query.order_by(APQCProcess.process_code).all()

        result = []
        for proc in processes:
            archimate_type = "BusinessFunction" if proc.apqc_level == 1 else "BusinessProcess"
            result.append(
                {
                    "id": proc.id,
                    "process_code": proc.process_code,
                    "process_name": proc.process_name,
                    "name": proc.process_name,
                    "process_description": proc.process_description,
                    "level": proc.apqc_level,
                    "category": proc.process_category,
                    "parent_process_id": proc.parent_process_id,
                    "archimate_element_type": archimate_type,
                }
            )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(
            f"Error getting APQC processes: {str(e)}", exc_info=True
        )
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/apqc-processes/children", methods=["POST"])
@login_required
def api_get_apqc_process_children():
    """Get child APQC processes for given parent IDs at target level."""
    try:
        from app.models.apqc_process import APQCProcess

        data = request.get_json()
        parent_ids = data.get("parent_ids", [])
        target_level = data.get("target_level")

        if not parent_ids or not target_level:
            return jsonify({"error": "parent_ids and target_level required"}), 400

        # Get all processes at target level
        all_processes = APQCProcess.query.all()
        target_processes = [p for p in all_processes if p.apqc_level == target_level]

        # Filter to only children of specified parents
        # Match by process_code hierarchy (e.g., "1.1" is child of "1.0")
        parent_processes = APQCProcess.query.filter(
            APQCProcess.id.in_(parent_ids)
        ).all()
        parent_codes = [p.process_code for p in parent_processes]

        children = []
        for proc in target_processes:
            # Check if this process is a child of any parent
            for parent_code in parent_codes:
                # Remove trailing .0 suffix from parent code for matching
                # Use removesuffix to avoid rstrip stripping individual chars
                parent_prefix = (
                    parent_code.removesuffix(".0")
                    if parent_code.endswith(".0")
                    else parent_code
                )
                if proc.process_code.startswith(parent_prefix + "."):
                    children.append(proc)
                    break

        result = []
        for proc in children:
            archimate_type = "BusinessFunction" if proc.apqc_level == 1 else "BusinessProcess"
            result.append(
                {
                    "id": proc.id,
                    "process_code": proc.process_code,
                    "process_name": proc.process_name,
                    "name": proc.process_name,
                    "process_description": proc.process_description,
                    "level": proc.apqc_level,
                    "category": proc.process_category,
                    "parent_process_id": proc.parent_process_id,
                    "archimate_element_type": archimate_type,
                }
            )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(
            f"Error getting APQC process children: {str(e)}", exc_info=True
        )
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/vendors/by-processes", methods=["POST"])
@login_required
def api_get_vendors_by_processes():
    """Get vendors that support specified APQC processes.

    Uses a 2-tier honest strategy:
      Tier 1 — Direct VendorProductAPQCMapping (preferred, real data)
      Tier 2 — Capability bridge fallback via UnifiedCapabilityProcessMapping
    If both tiers return empty, returns [] — no faking.

    Response shape matches /api/vendors/by-capabilities for shared UI rendering:
      { id, name, supported_capabilities, total_capabilities, total_products,
        process_coverage, supported_process_count, total_process_count }
    """
    try:
        data = request.get_json()
        process_ids = data.get("process_ids", [])

        if not process_ids:
            return jsonify({"error": "process_ids required"}), 400

        # ── Tier 1: Direct VendorProductAPQCMapping ──
        vendors = _tier1_direct_apqc_mapping(process_ids)

        # ── Tier 2: Capability bridge fallback (if Tier 1 is empty) ──
        if not vendors:
            vendors = _tier2_capability_bridge(process_ids)

        return jsonify(vendors)

    except Exception as e:
        current_app.logger.error(
            f"Error getting vendors by processes: {str(e)}", exc_info=True
        )
        return jsonify({"error": "An internal error occurred"}), 500


def _tier1_direct_apqc_mapping(process_ids):
    """Tier 1: Query VendorProductAPQCMapping → VendorProduct → VendorOrganization."""
    from sqlalchemy import distinct, func

    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
    from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping

    vendor_coverage = (
        db.session.query(
            VendorOrganization.id,
            VendorOrganization.name,
            func.count(distinct(VendorProductAPQCMapping.apqc_process_id)).label(
                "process_count"
            ),
            func.avg(VendorProductAPQCMapping.coverage_percentage).label("avg_coverage"),
            func.count(distinct(VendorProduct.id)).label("product_count"),
        )
        .join(VendorProduct, VendorOrganization.id == VendorProduct.vendor_organization_id)
        .join(
            VendorProductAPQCMapping,
            VendorProduct.id == VendorProductAPQCMapping.vendor_product_id,
        )
        .filter(VendorProductAPQCMapping.apqc_process_id.in_(process_ids))
        .group_by(VendorOrganization.id, VendorOrganization.name)
        .order_by(
            func.count(distinct(VendorProductAPQCMapping.apqc_process_id)).desc()
        )
        .limit(30)
        .all()
    )

    if not vendor_coverage:
        return []

    total_processes = len(process_ids)
    vendors = []
    for vendor_id, vendor_name, proc_count, avg_cov, product_count in vendor_coverage:
        coverage_pct = round(proc_count / total_processes * 100, 1) if total_processes > 0 else 0
        vendors.append(
            {
                "id": vendor_id,
                "name": vendor_name,
                "supported_capabilities": proc_count,
                "total_capabilities": total_processes,
                "total_products": product_count,
                "process_coverage": coverage_pct,
                "supported_process_count": proc_count,
                "total_process_count": total_processes,
                "avg_coverage_percentage": round(float(avg_cov or 0), 1),
            }
        )

    return vendors


def _tier2_capability_bridge(process_ids):
    """Tier 2: Use UnifiedCapabilityProcessMapping to find linked capabilities,
    resolve to BusinessCapability IDs, then query VendorProductCapability."""
    from sqlalchemy import distinct, func

    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import (
        UnifiedCapability,
        UnifiedCapabilityProcessMapping,
    )
    from app.models.vendor import VendorProductCapability
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    # Find linked UnifiedCapability IDs
    unified_cap_ids = [
        row[0]
        for row in db.session.query(
            distinct(UnifiedCapabilityProcessMapping.capability_id)
        )
        .filter(UnifiedCapabilityProcessMapping.apqc_process_id.in_(process_ids))
        .all()
    ]

    if not unified_cap_ids:
        return []

    # Resolve UnifiedCapability → BusinessCapability (FK + name strategy)
    legacy_ids_via_fk = [
        row[0]
        for row in db.session.query(BusinessCapability.id)
        .filter(BusinessCapability.deprecated_in_favor_of_id.in_(unified_cap_ids))
        .all()
    ]

    if len(legacy_ids_via_fk) < len(unified_cap_ids):
        unified_names = [
            row[0]
            for row in db.session.query(UnifiedCapability.name)
            .filter(UnifiedCapability.id.in_(unified_cap_ids))
            .all()
        ]
        if unified_names:
            legacy_ids_via_name = [
                row[0]
                for row in db.session.query(BusinessCapability.id)
                .filter(
                    BusinessCapability.name.in_(unified_names),
                    ~BusinessCapability.id.in_(legacy_ids_via_fk)
                    if legacy_ids_via_fk
                    else True,
                )
                .all()
            ]
        else:
            legacy_ids_via_name = []
    else:
        legacy_ids_via_name = []

    resolved_biz_cap_ids = list(set(legacy_ids_via_fk + legacy_ids_via_name))

    if not resolved_biz_cap_ids:
        return []

    # Query vendors via VendorProductCapability
    vendor_coverage = (
        db.session.query(
            VendorOrganization.id,
            VendorOrganization.name,
            func.count(distinct(VendorProductCapability.business_capability_id)).label(
                "cap_count"
            ),
            func.count(distinct(VendorProduct.id)).label("product_count"),
        )
        .join(VendorProduct, VendorOrganization.id == VendorProduct.vendor_organization_id)
        .join(
            VendorProductCapability,
            VendorProduct.id == VendorProductCapability.vendor_product_id,
        )
        .filter(VendorProductCapability.business_capability_id.in_(resolved_biz_cap_ids))
        .group_by(VendorOrganization.id, VendorOrganization.name)
        .order_by(
            func.count(distinct(VendorProductCapability.business_capability_id)).desc()
        )
        .limit(30)
        .all()
    )

    if not vendor_coverage:
        return []

    total_processes = len(process_ids)
    total_caps = len(resolved_biz_cap_ids)
    vendors = []
    for vendor_id, vendor_name, cap_count, product_count in vendor_coverage:
        coverage_pct = round(cap_count / total_caps * 100, 1) if total_caps > 0 else 0
        vendors.append(
            {
                "id": vendor_id,
                "name": vendor_name,
                "supported_capabilities": cap_count,
                "total_capabilities": total_processes,
                "total_products": product_count,
                "process_coverage": coverage_pct,
                "supported_process_count": cap_count,
                "total_process_count": total_processes,
            }
        )

    return vendors


def _resolve_business_capability_ids_for_value_streams(value_stream_ids):
    """Value streams -> the business capabilities they depend on.

    Two existing links, both already populated for a tenant whose value
    streams have been mapped: directly (value_stream_capabilities) and
    through each value stream's stages (value_stream_stage_capabilities).
    Both are plain association tables, so the automatic tenant filter on the
    ValueStream / ValueStreamStage / BusinessCapability join partners is what
    keeps this scoped to the signed-in organisation.
    """
    from app.models.business_capabilities import BusinessCapability
    from app.models.relationship_tables import (
        value_stream_capabilities,
        value_stream_stage_capabilities,
    )
    from app.models.unified_capability import ValueStream, ValueStreamStage

    direct_cap_ids = [
        row[0]
        for row in db.session.query(BusinessCapability.id)
        .join(
            value_stream_capabilities,
            value_stream_capabilities.c.business_capability_id == BusinessCapability.id,
        )
        .join(
            ValueStream,
            ValueStream.id == value_stream_capabilities.c.value_stream_id,
        )
        .filter(ValueStream.id.in_(value_stream_ids))
        .all()
    ]

    stage_cap_ids = [
        row[0]
        for row in db.session.query(BusinessCapability.id)
        .join(
            value_stream_stage_capabilities,
            value_stream_stage_capabilities.c.business_capability_id == BusinessCapability.id,
        )
        .join(
            ValueStreamStage,
            ValueStreamStage.id == value_stream_stage_capabilities.c.value_stream_stage_id,
        )
        .filter(ValueStreamStage.value_stream_id.in_(value_stream_ids))
        .all()
    ]

    return list(set(direct_cap_ids + stage_cap_ids))


def _vendor_coverage_for_business_capabilities(biz_cap_ids):
    """Vendor coverage for an already-resolved set of BusinessCapability ids.

    Same VendorProductCapability -> VendorProduct -> VendorOrganization join
    the capability bridge above runs; shared here so value-stream discovery
    does not grow a third copy of it.
    """
    from sqlalchemy import distinct, func

    from app.models.vendor import VendorProductCapability
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    vendor_coverage = (
        db.session.query(
            VendorOrganization.id,
            VendorOrganization.name,
            func.count(distinct(VendorProductCapability.business_capability_id)).label(
                "cap_count"
            ),
            func.count(distinct(VendorProduct.id)).label("product_count"),
        )
        .join(VendorProduct, VendorOrganization.id == VendorProduct.vendor_organization_id)
        .join(
            VendorProductCapability,
            VendorProduct.id == VendorProductCapability.vendor_product_id,
        )
        .filter(VendorProductCapability.business_capability_id.in_(biz_cap_ids))
        .group_by(VendorOrganization.id, VendorOrganization.name)
        .order_by(
            func.count(distinct(VendorProductCapability.business_capability_id)).desc()
        )
        .limit(30)
        .all()
    )

    if not vendor_coverage:
        return []

    total_caps = len(biz_cap_ids)
    vendors = []
    for vendor_id, vendor_name, cap_count, product_count in vendor_coverage:
        vendors.append(
            {
                "id": vendor_id,
                "name": vendor_name,
                "supported_capabilities": cap_count,
                "total_capabilities": total_caps,
                "total_products": product_count,
            }
        )

    return vendors


@application_mgmt.route("/api/vendors/by-value-streams", methods=["POST"])
@login_required
def api_get_vendors_by_value_streams():
    """Get vendors that enable the selected value streams, through the
    capabilities those value streams resolve to.

    Resolution happens here, server-side, not in the browser: a value stream
    carries no vendor coverage of its own, so this maps each selected value
    stream to its mapped capabilities (directly and through its stages) and
    runs the same vendor-coverage lookup the capabilities tab uses. An honest
    empty list when nothing is mapped, matching the other two discovery
    routes.
    """
    try:
        data = request.get_json()
        value_stream_ids = data.get("value_stream_ids", [])

        if not value_stream_ids:
            return jsonify({"error": "value_stream_ids required"}), 400

        resolved_biz_cap_ids = _resolve_business_capability_ids_for_value_streams(
            value_stream_ids
        )

        if not resolved_biz_cap_ids:
            current_app.logger.info(
                "No capability maps to value streams %s. Vendor discovery returned 0 results.",
                value_stream_ids,
            )
            return jsonify([])

        vendors = _vendor_coverage_for_business_capabilities(resolved_biz_cap_ids)
        return jsonify(vendors)

    except Exception as e:
        current_app.logger.error(
            f"Error getting vendors by value streams: {str(e)}", exc_info=True
        )
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/value-stream-capabilities", methods=["POST"])
@login_required
def api_get_value_stream_capabilities():
    """Get capabilities linked to the selected value streams.

    Mirrors /api/process-capabilities: used by the frontend in value-stream
    mode to resolve a valid capability_id for the OptionsAnalysis model
    (which requires a capability_id FK), the same way process mode resolves
    one through APQC processes.
    """
    try:
        from app.models.business_capabilities import BusinessCapability

        data = request.get_json()
        value_stream_ids = data.get("value_stream_ids", [])

        if not value_stream_ids:
            return jsonify({"error": "value_stream_ids required"}), 400

        biz_cap_ids = _resolve_business_capability_ids_for_value_streams(value_stream_ids)

        if not biz_cap_ids:
            return jsonify([])

        rows = (
            db.session.query(BusinessCapability.id, BusinessCapability.name)
            .filter(BusinessCapability.id.in_(biz_cap_ids))
            .all()
        )
        return jsonify([{"id": row[0], "name": row[1]} for row in rows])

    except Exception as e:
        current_app.logger.error(
            f"Error getting value stream capabilities: {str(e)}", exc_info=True
        )
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/process-capabilities", methods=["POST"])
@login_required
def api_get_process_capabilities():
    """Get capabilities linked to APQC processes via UnifiedCapabilityProcessMapping.

    Used by the frontend in process-driven mode to resolve a valid capability_id
    for the OptionsAnalysis model (which requires capability_id FK).
    """
    try:
        from sqlalchemy import distinct

        from app.models.business_capabilities import BusinessCapability
        from app.models.unified_capability import (
            UnifiedCapability,
            UnifiedCapabilityProcessMapping,
        )

        data = request.get_json()
        process_ids = data.get("process_ids", [])

        if not process_ids:
            return jsonify({"error": "process_ids required"}), 400

        # Find linked UnifiedCapability IDs
        unified_cap_ids = [
            row[0]
            for row in db.session.query(
                distinct(UnifiedCapabilityProcessMapping.capability_id)
            )
            .filter(UnifiedCapabilityProcessMapping.apqc_process_id.in_(process_ids))
            .all()
        ]

        if not unified_cap_ids:
            return jsonify([])

        # Resolve to BusinessCapability (same FK + name strategy)
        legacy_caps_via_fk = (
            db.session.query(BusinessCapability.id, BusinessCapability.name)
            .filter(BusinessCapability.deprecated_in_favor_of_id.in_(unified_cap_ids))
            .all()
        )

        result = [{"id": row[0], "name": row[1]} for row in legacy_caps_via_fk]
        found_ids = {row[0] for row in legacy_caps_via_fk}

        # Name fallback for unmapped ones
        if len(found_ids) < len(unified_cap_ids):
            unified_names = [
                row[0]
                for row in db.session.query(UnifiedCapability.name)
                .filter(UnifiedCapability.id.in_(unified_cap_ids))
                .all()
            ]
            if unified_names:
                name_matches = (
                    db.session.query(BusinessCapability.id, BusinessCapability.name)
                    .filter(
                        BusinessCapability.name.in_(unified_names),
                        ~BusinessCapability.id.in_(list(found_ids))
                        if found_ids
                        else True,
                    )
                    .all()
                )
                result.extend(
                    [{"id": row[0], "name": row[1]} for row in name_matches]
                )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(
            f"Error getting process capabilities: {str(e)}", exc_info=True
        )
        return jsonify({"error": "An internal error occurred"}), 500

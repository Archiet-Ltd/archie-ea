"""
Capability Framework Dashboard Routes
"""

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from app import db
from app.models import ApplicationComponent
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import BusinessDomain, UnifiedCapability
from app.utils.route_guards import require_entity_json
from app.middleware.tenant_decorators import platform_admin_required

capability_framework_bp = Blueprint(
    "capability_framework", __name__, url_prefix="/capability-framework"
)


@capability_framework_bp.route("/")
@platform_admin_required
def dashboard():
    """Redirect to new Framework Management Dashboard"""
    from flask import redirect, url_for

    return redirect(url_for("main.framework_management.dashboard"))


@capability_framework_bp.route("/api/domains")
@platform_admin_required
def get_domains():
    """Get all business domains with capability counts"""
    domains = (
        db.session.query(BusinessDomain, func.count(UnifiedCapability.id).label("capability_count"))
        .outerjoin(UnifiedCapability, BusinessDomain.id == UnifiedCapability.domain_id)
        .group_by(BusinessDomain.id)
        .all()
    )

    result = []
    for domain, count in domains:
        result.append(
            {
                "id": domain.id,
                "name": domain.name,
                "code": domain.code,
                "description": domain.description,
                "domain_type": domain.domain_type,
                "capability_count": count or 0,
                "strategic_weight": domain.strategic_weight,
                "investment_priority": domain.investment_priority,
            }
        )

    return jsonify(result)


@capability_framework_bp.route("/api/capabilities")
@platform_admin_required
def get_capabilities():
    """Get capabilities with optional filtering"""
    domain_id = request.args.get("domain_id", type=int)
    level = request.args.get("level", type=int)

    query = db.session.query(UnifiedCapability, BusinessDomain.name.label("domain_name")).join(
        BusinessDomain, UnifiedCapability.domain_id == BusinessDomain.id
    )

    if domain_id:
        query = query.filter(UnifiedCapability.domain_id == domain_id)
    if level:
        query = query.filter(UnifiedCapability.level == level)

    capabilities = query.all()

    result = []
    for cap, domain_name in capabilities:
        result.append(
            {
                "id": cap.id,
                "name": cap.name,
                "code": cap.code,
                "level": cap.level,
                "category": cap.category,
                "capability_type": cap.capability_type,
                "description": cap.description,
                "domain_name": domain_name,
                "strategic_importance": cap.strategic_importance,
                "business_criticality": cap.business_criticality,
                "manufacturing_critical": cap.manufacturing_critical,
                "industry_domain": cap.industry_domain,
                "current_maturity_level": cap.current_maturity_level,
                "target_maturity_level": cap.target_maturity_level,
                "parent_capability_id": cap.parent_capability_id,
            }
        )

    return jsonify(result)


@capability_framework_bp.route("/api/capability/<int:capability_id>/applications")
@platform_admin_required
def get_capability_applications(capability_id):
    """Get applications mapped to a specific capability"""
    # Unknown capability -> 404, not an empty application list. The `main`
    # blueprint renders an HTML 404 page, so return the JSON tuple explicitly.
    _capability, missing = require_entity_json(
        UnifiedCapability, capability_id, label="Capability"
    )
    if missing:
        return missing

    mappings = (
        db.session.query(
            UnifiedApplicationCapabilityMapping,
            ApplicationComponent.name.label("application_name"),
            ApplicationComponent.description.label("application_description"),
        )
        .join(
            ApplicationComponent,
            UnifiedApplicationCapabilityMapping.application_component_id == ApplicationComponent.id,
        )
        .filter(UnifiedApplicationCapabilityMapping.unified_capability_id == capability_id)
        .all()
    )

    result = []
    for mapping, app_name, app_desc in mappings:
        result.append(
            {
                "mapping_id": mapping.id,
                "application_name": app_name,
                "application_description": app_desc,
                "support_level": mapping.support_level,
                "coverage_percentage": mapping.coverage_percentage,
                "support_quality": mapping.support_quality,
                "gap_status": mapping.gap_status,
                "relationship_strength": mapping.relationship_strength,
                "integration_complexity": mapping.integration_complexity,
                "priority": mapping.priority,
            }
        )

    return jsonify(result)


@capability_framework_bp.route("/api/statistics")
@platform_admin_required
def get_statistics():
    """Get framework statistics"""
    stats = {
        "total_domains": BusinessDomain.query.count(),
        "total_capabilities": UnifiedCapability.query.count(),
        "manufacturing_capabilities": UnifiedCapability.query.filter_by(
            manufacturing_critical=True
        ).count(),
        "total_mappings": UnifiedApplicationCapabilityMapping.query.count(),
        "capabilities_by_level": dict(
            db.session.query(UnifiedCapability.level, func.count(UnifiedCapability.id))
            .group_by(UnifiedCapability.level)
            .all()
        ),
        "gap_analysis": {
            "fully_covered": UnifiedApplicationCapabilityMapping.query.filter_by(
                gap_status="fully_covered"
            ).count(),
            "partially_covered": UnifiedApplicationCapabilityMapping.query.filter_by(
                gap_status="partially_covered"
            ).count(),
            "gap": UnifiedApplicationCapabilityMapping.query.filter_by(gap_status="gap").count(),
        },
    }

    return jsonify(stats)


@capability_framework_bp.route("/api/maturity-heatmap")
@platform_admin_required
def get_maturity_heatmap():
    """Get maturity heatmap data: domains x maturity levels with health scores"""
    from app.services.capability_heatmap_service import CapabilityHeatmapService

    service = CapabilityHeatmapService()
    data = service.get_maturity_heatmap()
    return jsonify(data)


@capability_framework_bp.route("/api/gap-alerts")
@platform_admin_required
def get_gap_alerts():
    """Get gap alerts: unmapped capabilities, low coverage, and maturity gaps"""
    from app.services.capability_heatmap_service import CapabilityHeatmapService

    service = CapabilityHeatmapService()
    data = service.get_gap_alerts()
    return jsonify(data)


@capability_framework_bp.route("/api/domain-health")
@platform_admin_required
def get_domain_health():
    """Get domain health scores with status classification"""
    from app.services.capability_heatmap_service import CapabilityHeatmapService

    service = CapabilityHeatmapService()
    data = service.get_domain_health()
    return jsonify(data)

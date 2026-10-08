"""
Framework Management Dashboard

Shows available frameworks, extensions, and templates.
Allows adoption and deployment of framework configurations.
"""

import json
import re
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from app import db
from app.decorators import audit_log
from app.models.framework_configuration import (
    CapabilityFrameworkConfiguration,
    FrameworkConfigurationTemplate,
    FrameworkExtension,
    FrameworkInstance,
)
from app.middleware.tenant_decorators import platform_admin_required

framework_management_bp = Blueprint(
    "framework_management", __name__, url_prefix="/framework-management"
)


@framework_management_bp.route("/")
@platform_admin_required
def dashboard():
    """Framework Management Dashboard"""
    has_manufacturing = db.session.query(FrameworkInstance).count() > 0
    return render_template("framework_management/dashboard.html", has_manufacturing=has_manufacturing)


MATURITY_LABELS = {1: "Initial", 2: "Developing", 3: "Defined", 4: "Managed", 5: "Optimizing"}


@framework_management_bp.route("/manufacturing/dashboard")
@platform_admin_required
def framework_dashboard():
    """Manufacturing Excellence Framework Dashboard — real data only, no fabricated values."""
    instance_count = db.session.query(FrameworkInstance).count()
    active_extensions = db.session.query(FrameworkExtension).filter(
        FrameworkExtension.status == "active"
    ).count()

    avg_coverage = None
    maturity_level = None
    if instance_count > 0:
        result = db.session.query(
            db.func.avg(FrameworkInstance.implementation_percentage)
        ).scalar()
        avg_coverage = round(result, 1) if result is not None else 0.0
        maturity_result = db.session.query(
            db.func.avg(FrameworkInstance.current_maturity_level)
        ).scalar()
        if maturity_result is not None:
            level = int(round(maturity_result))
            maturity_level = f"Level {level}" if 1 <= level <= 5 else None
            if maturity_level and level in MATURITY_LABELS:
                maturity_level = f"{maturity_level} ({MATURITY_LABELS[level]})"

    stats = {
        "capability_coverage": f"{avg_coverage}%" if avg_coverage is not None else "\u2014",
        "maturity_level": maturity_level or "\u2014",
        "active_processes": str(instance_count) if instance_count > 0 else "\u2014",
        "performance_score": str(active_extensions) if active_extensions > 0 else "\u2014",
    }
    return render_template("framework_management/manufacturing_dashboard.html", stats=stats)


@framework_management_bp.route("/manufacturing/table")
@platform_admin_required
def framework_table():
    """Manufacturing Excellence Framework Data Table"""
    return render_template("framework_management/manufacturing_table.html")


@framework_management_bp.route("/api/manufacturing/instances")
@platform_admin_required
def get_manufacturing_instances():
    """Return framework instances for manufacturing table — real data only."""
    instances = FrameworkInstance.query.order_by(FrameworkInstance.instance_name).all()
    data = []
    for inst in instances:
        level = inst.current_maturity_level or 1
        perf = inst.implementation_percentage if inst.implementation_percentage is not None else None
        data.append({
            "id": inst.id,
            "name": inst.instance_name or "—",
            "category": inst.organization_unit or "—",
            "maturity": level,
            "performance": round(perf, 1) if perf is not None else None,
            "status": inst.status or "—",
            "owner": inst.instance_owner or "—",
            "lastUpdated": inst.last_health_check.strftime("%Y-%m-%d") if inst.last_health_check else "—",
        })
    return jsonify({"data": data, "total": len(data)})


def _extension_slug(value):
    """Normalise a name/code to the dash-separated form used in extension dashboard URLs."""
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _extension_json_list(raw):
    """Parse a FrameworkExtension JSON-array text column into a list of strings."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


@framework_management_bp.route("/extensions/<extension_name>")
@platform_admin_required
def extension_dashboard(extension_name):
    """Framework Extension Dashboard — renders the registered FrameworkExtension record, if any."""
    slug = _extension_slug(extension_name)
    extension = next(
        (
            ext
            for ext in FrameworkExtension.query.all()
            if _extension_slug(ext.extension_code) == slug
            or _extension_slug(ext.extension_name) == slug
        ),
        None,
    )
    features = _extension_json_list(extension.additional_capabilities) if extension else []
    compatible_versions = ", ".join(_extension_json_list(extension.compatible_versions)) if extension else ""
    dependencies_display = ", ".join(_extension_json_list(extension.dependencies)) if extension else ""
    return render_template(
        "framework_management/extension_dashboard.html",
        extension_name=extension_name,
        extension=extension,
        features=features,
        compatible_versions=compatible_versions,
        dependencies_display=dependencies_display,
    )


@framework_management_bp.route("/templates/<template_name>")
@platform_admin_required
def template_dashboard(template_name):
    """Framework Template Dashboard"""
    return render_template(
        "framework_management/template_dashboard.html", template_name=template_name
    )


@framework_management_bp.route("/api/available-frameworks")
@platform_admin_required
def get_available_frameworks():
    """Get available frameworks, extensions, and templates"""

    # Get all configurations (not just active ones)
    configurations = CapabilityFrameworkConfiguration.query.limit(200).all()

    # Get available extensions
    extensions = FrameworkExtension.query.filter_by(status="active").all()

    # Get available templates
    templates = FrameworkConfigurationTemplate.query.filter_by(status="active").all()

    # Get active instances
    instances = FrameworkInstance.query.filter_by(status="operational").all()

    return jsonify(
        {
            "configurations": [config.to_dict() for config in configurations],
            "extensions": [
                {
                    "id": ext.id,
                    "name": ext.extension_name,
                    "description": ext.extension_description,
                    "code": ext.extension_code,
                    "type": ext.extension_type,
                    "category": ext.extension_category,
                    "version": ext.extension_version,
                    "provider": ext.provider,
                    "status": ext.status,
                    "download_count": ext.download_count,
                    "user_rating": ext.user_rating,
                    "target_framework": ext.target_framework,
                    "additional_domains": ext.additional_domains,
                    "additional_capabilities": ext.additional_capabilities,
                    "created_at": ext.created_at.isoformat(),
                }
                for ext in extensions
            ],
            "templates": [
                {
                    "id": template.id,
                    "name": template.template_name,
                    "description": template.template_description,
                    "code": template.template_code,
                    "type": template.template_type,
                    "category": template.template_category,
                    "organization_size": template.organization_size,
                    "provider": template.provider,
                    "usage_count": template.usage_count,
                    "success_rate": template.success_rate,
                    "quality_score": template.quality_score,
                    "target_industries": template.target_industries,
                    "complexity_level": template.complexity_level,
                    "created_at": template.created_at.isoformat(),
                }
                for template in templates
            ],
            "instances": [
                {
                    "id": instance.id,
                    "name": instance.instance_name,
                    "description": instance.instance_description,
                    "organization_unit": instance.organization_unit,
                    "implementation_scope": instance.implementation_scope,
                    "status": instance.status,
                    "implementation_percentage": instance.implementation_percentage,
                    "current_maturity_level": instance.current_maturity_level,
                    "active_users": instance.active_users,
                    "created_at": instance.created_at.isoformat(),
                }
                for instance in instances
            ],
        }
    )


@framework_management_bp.route("/api/deploy-configuration", methods=["POST"])
@platform_admin_required
@audit_log("deploy_configuration")
def deploy_configuration():
    """Deploy a framework configuration"""
    data = request.get_json()

    try:
        # Create new framework instance
        instance = FrameworkInstance(
            instance_name=data["instance_name"],
            instance_description=data.get("description", ""),
            configuration_id=data["configuration_id"],
            organization_unit=data.get("organization_unit", "Enterprise"),
            implementation_scope=data.get("implementation_scope", "enterprise"),
            implementation_methodology=data.get("implementation_methodology", "big_bang"),
            status="implementing",
            instance_owner=data.get("instance_owner", "System"),
        )

        db.session.add(instance)
        db.session.commit()

        # Update configuration status
        configuration = CapabilityFrameworkConfiguration.query.get(data["configuration_id"])
        if configuration:
            configuration.status = "active"

        return jsonify(
            {
                "success": True,
                "message": f'Framework configuration "{data["instance_name"]}" deployed successfully',
                "instance_id": instance.id,
            }
        )

    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "message": "An internal error occurred"}), 500


@framework_management_bp.route("/api/activate-extension", methods=["POST"])
@framework_management_bp.route("/api/activate-extension/<int:extension_id>", methods=["POST"])
@platform_admin_required
@audit_log("activate_extension")
def activate_extension(extension_id=None):
    """Activate a framework extension by ID (URL param) or name (JSON body)."""
    try:
        if extension_id is not None:
            extension = FrameworkExtension.query.get_or_404(extension_id)
        else:
            data = request.get_json() or {}
            name = data.get("extension_name", "")
            extension = FrameworkExtension.query.filter_by(extension_name=name).first()
            if not extension:
                return jsonify({"success": False, "message": f"Extension '{name}' not found"}), 404

        # Update download count
        extension.download_count = extension.download_count + 1

        # In a real implementation, this would:
        # 1. Download extension files
        # 2. Install extension components
        # 3. Update framework configuration
        # 4. Validate installation

        return jsonify(
            {
                "success": True,
                "message": f'Extension "{extension.extension_name}" activated successfully',
                "extension": {
                    "id": extension.id,
                    "name": extension.extension_name,
                    "code": extension.extension_code,
                },
            }
        )

    except Exception:
        return jsonify({"success": False, "message": "An internal error occurred"}), 500


@framework_management_bp.route("/api/apply-template", methods=["POST"])
@platform_admin_required
@audit_log("apply_template")
def apply_template():
    """Apply a framework template"""
    data = request.get_json()

    try:
        template = FrameworkConfigurationTemplate.query.get_or_404(data["template_id"])

        # Create new configuration from template
        configuration = CapabilityFrameworkConfiguration(
            configuration_name=data["configuration_name"],
            configuration_description=data.get("description", template.template_description),
            configuration_code=f"AUTO_{template.template_code}_{datetime.now().strftime('%Y%m%d')}",
            base_framework=template.template_configuration.get(
                "base_framework", "Unified_Manufacturing_Excellence"
            ),
            organization_name=data.get("organization_name"),
            organization_type=data.get("organization_type"),
            industry_focus=template.template_category,
            enabled_domains=template.default_domains,
            enabled_extensions=template.default_extensions,
            status="draft",
            configuration_owner=data.get("configuration_owner", "System"),
        )

        # Apply template configuration
        if template.template_configuration:
            # Parse and apply template settings
            template_config = json.loads(template.template_configuration)
            for key, value in template_config.items():
                if hasattr(configuration, key):
                    setattr(configuration, key, value)

        db.session.add(configuration)

        # Update template usage count
        template.usage_count = template.usage_count + 1

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f'Template "{template.template_name}" applied successfully',
                "configuration_id": configuration.id,
                "configuration": configuration.to_dict(),
            }
        )

    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "message": "An internal error occurred"}), 500


@framework_management_bp.route("/api/statistics")
@platform_admin_required
def get_statistics():
    """Get framework management statistics"""

    total_configurations = CapabilityFrameworkConfiguration.query.count()
    active_configurations = CapabilityFrameworkConfiguration.query.filter_by(
        status="active"
    ).count()
    total_extensions = FrameworkExtension.query.count()
    active_extensions = FrameworkExtension.query.filter_by(status="active").count()
    total_templates = FrameworkConfigurationTemplate.query.count()
    active_instances = FrameworkInstance.query.filter_by(status="operational").count()

    # Get most popular extensions
    popular_extensions = (
        FrameworkExtension.query.order_by(FrameworkExtension.download_count.desc()).limit(5).all()
    )

    # Get most used templates
    popular_templates = (
        FrameworkConfigurationTemplate.query.order_by(
            FrameworkConfigurationTemplate.usage_count.desc()
        )
        .limit(5)
        .all()
    )

    return jsonify(
        {
            "total_configurations": total_configurations,
            "active_configurations": active_configurations,
            "total_extensions": total_extensions,
            "active_extensions": active_extensions,
            "total_templates": total_templates,
            "active_instances": active_instances,
            "popular_extensions": [
                {
                    "name": ext.extension_name,
                    "downloads": ext.download_count,
                    "rating": ext.user_rating,
                }
                for ext in popular_extensions
            ],
            "popular_templates": [
                {
                    "name": template.template_name,
                    "usage": template.usage_count,
                    "success_rate": template.success_rate,
                }
                for template in popular_templates
            ],
        }
    )


@framework_management_bp.route("/api/active-framework")
@platform_admin_required
def get_active_framework():
    """Get the currently active framework configuration"""
    active_config = CapabilityFrameworkConfiguration.query.filter_by(status="active").first()

    if active_config:
        # Get active instance if exists
        active_instance = FrameworkInstance.query.filter_by(
            configuration_id=active_config.id, status="operational"
        ).first()

        return jsonify(
            {
                "configuration": active_config.to_dict(),
                "instance": {
                    "id": active_instance.id,
                    "name": active_instance.instance_name,
                    "implementation_percentage": active_instance.implementation_percentage,
                    "current_maturity_level": active_instance.current_maturity_level,
                    "active_users": active_instance.active_users,
                }
                if active_instance
                else None,
            }
        )

    return jsonify({"configuration": None, "instance": None})

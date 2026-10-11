"""
Framework Configuration API Routes

RESTful API endpoints for framework configuration management.
Provides configuration-driven capability management with industry extensions.

Endpoints:
- Framework configuration CRUD operations
- Extension management and installation
- Configuration validation and compliance
- Migration support from legacy frameworks
- Template management and deployment
"""

import json
import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from app.middleware.tenant_decorators import platform_admin_required

from app import db
from app.jobs.tenant_safe_job import platform_scope
from app.decorators import audit_log
from app.models.framework_configuration import (
    CapabilityFrameworkConfiguration,
    FrameworkConfigurationTemplate,
    FrameworkInstance,
)
from app.services.framework_configuration_service import (
    FrameworkConfigurationService,
    FrameworkExtensionService,
    FrameworkMigrationService,
    FrameworkValidationService,
)

framework_config_bp = Blueprint("framework_config", __name__, url_prefix="/api/framework-config")
logger = logging.getLogger(__name__)


@framework_config_bp.route("/configurations", methods=["GET"])
@platform_admin_required
def get_configurations():
    """
    Get all framework configurations
    ---
    tags:
      - Framework Config
    summary: List all framework configurations
    description: Retrieve all framework configurations with optional filtering by organization or status
    security:
      - cookieAuth: []
    parameters:
      - name: organization_name
        in: query
        type: string
        required: false
        description: Filter by organization name
      - name: status
        in: query
        type: string
        required: false
        description: Filter by status (draft, active, archived)
    responses:
      200:
        description: List of configurations
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: array
              items:
                type: object
            count:
              type: integer
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        organization_name = request.args.get("organization_name")
        status = request.args.get("status")

        query = CapabilityFrameworkConfiguration.query

        if organization_name:
            query = query.filter_by(organization_name=organization_name)

        if status:
            query = query.filter_by(status=status)

        configurations = query.order_by(CapabilityFrameworkConfiguration.created_at.desc()).all()

        return jsonify(
            {
                "success": True,
                "data": [config.to_dict() for config in configurations],
                "count": len(configurations),
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/configurations", methods=["POST"])
@platform_admin_required
@audit_log("framework_config_create")
def create_configuration():
    """
    Create a new framework configuration
    ---
    tags:
      - Framework Config
    summary: Create new framework configuration
    description: Create a new framework configuration with the provided settings
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - configuration_name
            - configuration_code
          properties:
            configuration_name:
              type: string
              description: Name of the configuration
            configuration_code:
              type: string
              description: Unique code identifier
            organization_name:
              type: string
            status:
              type: string
              enum: [draft, active, archived]
    responses:
      201:
        description: Configuration created successfully
      400:
        description: Missing required fields or duplicate code
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ["configuration_name", "configuration_code"]
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        # Check if configuration code already exists
        existing = CapabilityFrameworkConfiguration.query.filter_by(
            configuration_code=data["configuration_code"]
        ).first()

        if existing:
            return jsonify({"success": False, "error": "Configuration code already exists"}), 400

        # Create configuration
        with platform_scope("platform administrator creates a shared framework configuration"):
            configuration = FrameworkConfigurationService.create_configuration(data)

        return (
            jsonify(
                {
                    "success": True,
                    "data": configuration.to_dict(),
                    "message": "Framework configuration created successfully",
                }
            ),
            201,
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/configurations/<int:config_id>", methods=["GET"])
@platform_admin_required
def get_configuration(config_id):
    """
    Get framework configuration by ID
    ---
    tags:
      - Framework Config
    summary: Get configuration by ID
    description: Retrieve a specific framework configuration by its ID
    security:
      - cookieAuth: []
    parameters:
      - name: config_id
        in: path
        type: integer
        required: true
        description: Configuration ID
    responses:
      200:
        description: Configuration details
      401:
        description: Unauthorized
      404:
        description: Configuration not found
      500:
        description: Server error
    """
    try:
        configuration = FrameworkConfigurationService.get_configuration_by_id(config_id)

        if not configuration:
            return jsonify({"success": False, "error": "Configuration not found"}), 404

        return jsonify({"success": True, "data": configuration.to_dict()})

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/configurations/<int:config_id>", methods=["PUT"])
@platform_admin_required
@audit_log("framework_config_update")
def update_configuration(config_id):
    """
    Update framework configuration
    ---
    tags:
      - Framework Config
    summary: Update configuration
    description: Update an existing framework configuration
    security:
      - cookieAuth: []
    parameters:
      - name: config_id
        in: path
        type: integer
        required: true
        description: Configuration ID
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            configuration_name:
              type: string
            status:
              type: string
    responses:
      200:
        description: Configuration updated successfully
      401:
        description: Unauthorized
      404:
        description: Configuration not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()

        with platform_scope("platform administrator edits a shared framework configuration"):
            configuration = FrameworkConfigurationService.update_configuration(config_id, data)

        if not configuration:
            return jsonify({"success": False, "error": "Configuration not found"}), 404

        return jsonify(
            {
                "success": True,
                "data": configuration.to_dict(),
                "message": "Framework configuration updated successfully",
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/configurations/<int:config_id>", methods=["DELETE"])
@platform_admin_required
@audit_log("framework_config_delete")
def delete_configuration(config_id):
    """
    Delete framework configuration
    ---
    tags:
      - Framework Config
    summary: Delete configuration
    description: Delete a framework configuration by ID
    security:
      - cookieAuth: []
    parameters:
      - name: config_id
        in: path
        type: integer
        required: true
        description: Configuration ID
    responses:
      200:
        description: Configuration deleted successfully
      401:
        description: Unauthorized
      404:
        description: Configuration not found
      500:
        description: Server error
    """
    try:
        with platform_scope("platform administrator deletes a shared framework configuration"):
            success = FrameworkConfigurationService.delete_configuration(config_id)

        if not success:
            return (
                jsonify(
                    {"success": False, "error": "Configuration not found or cannot be deleted"}
                ),
                404,
            )

        return jsonify({"success": True, "message": "Framework configuration deleted successfully"})

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/configurations/<int:config_id>/validate", methods=["POST"])
@platform_admin_required
def validate_configuration(config_id):
    """
    Validate framework configuration
    ---
    tags:
      - Framework Config
    summary: Validate configuration
    description: Validate a framework configuration for completeness and compliance
    security:
      - cookieAuth: []
    parameters:
      - name: config_id
        in: path
        type: integer
        required: true
        description: Configuration ID
    responses:
      200:
        description: Validation results
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        validation_result = FrameworkValidationService.validate_configuration(config_id)

        return jsonify({"success": True, "data": validation_result})

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/configurations/active", methods=["GET"])
@platform_admin_required
def get_active_configuration():
    """
    Get active configuration for organization
    ---
    tags:
      - Framework Config
    summary: Get active configuration
    description: Get the currently active configuration for an organization
    security:
      - cookieAuth: []
    parameters:
      - name: organization_name
        in: query
        type: string
        required: false
        description: Organization name
    responses:
      200:
        description: Active configuration
      401:
        description: Unauthorized
      404:
        description: No active configuration found
      500:
        description: Server error
    """
    try:
        organization_name = request.args.get("organization_name")
        configuration = FrameworkConfigurationService.get_active_configuration(organization_name)

        if not configuration:
            return jsonify({"success": False, "error": "No active configuration found"}), 404

        return jsonify({"success": True, "data": configuration.to_dict()})

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/extensions", methods=["GET"])
@platform_admin_required
def get_available_extensions():
    """
    Get available framework extensions
    ---
    tags:
      - Framework Config
    summary: List available extensions
    description: Retrieve all available framework extensions optionally filtered by industry
    security:
      - cookieAuth: []
    parameters:
      - name: industry
        in: query
        type: string
        required: false
        description: Filter by industry
    responses:
      200:
        description: List of extensions
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        industry = request.args.get("industry")
        extensions = FrameworkExtensionService.get_available_extensions(industry)

        return jsonify(
            {
                "success": True,
                "data": [
                    {
                        "id": ext.id,
                        "extension_name": ext.extension_name,
                        "extension_description": ext.extension_description,
                        "extension_code": ext.extension_code,
                        "extension_type": ext.extension_type,
                        "extension_category": ext.extension_category,
                        "extension_version": ext.extension_version,
                        "provider": ext.provider,
                        "license_type": ext.license_type,
                        "status": ext.status,
                    }
                    for ext in extensions
                ],
                "count": len(extensions),
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/extensions/<extension_code>", methods=["GET"])
@platform_admin_required
def get_extension_details(extension_code):
    """
    Get extension details by code
    ---
    tags:
      - Framework Config
    summary: Get extension details
    description: Get detailed information about a specific extension
    security:
      - cookieAuth: []
    parameters:
      - name: extension_code
        in: path
        type: string
        required: true
        description: Extension code
    responses:
      200:
        description: Extension details
      401:
        description: Unauthorized
      404:
        description: Extension not found
      500:
        description: Server error
    """
    try:
        extension = FrameworkExtensionService.get_extension_by_code(extension_code)

        if not extension:
            return jsonify({"success": False, "error": "Extension not found"}), 404

        return jsonify(
            {
                "success": True,
                "data": {
                    "id": extension.id,
                    "extension_name": extension.extension_name,
                    "extension_description": extension.extension_description,
                    "extension_code": extension.extension_code,
                    "extension_type": extension.extension_type,
                    "extension_category": extension.extension_category,
                    "extension_version": extension.extension_version,
                    "target_framework": extension.target_framework,
                    "compatible_versions": extension.compatible_versions,
                    "additional_domains": extension.additional_domains,
                    "additional_capabilities": extension.additional_capabilities,
                    "additional_kpis": extension.additional_kpis,
                    "additional_value_streams": extension.additional_value_streams,
                    "prerequisites": extension.prerequisites,
                    "dependencies": extension.dependencies,
                    "resource_requirements": extension.resource_requirements,
                    "provider": extension.provider,
                    "license_type": extension.license_type,
                    "support_level": extension.support_level,
                    "status": extension.status,
                    "quality_score": extension.quality_score,
                    "download_count": extension.download_count,
                    "user_rating": extension.user_rating,
                    "documentation_url": extension.documentation_url,
                },
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route(
    "/configurations/<int:config_id>/extensions/<extension_code>/install", methods=["POST"]
)
@platform_admin_required
@audit_log("framework_extension_install")
def install_extension(config_id, extension_code):
    """
    Install extension on configuration
    ---
    tags:
      - Framework Config
    summary: Install extension
    description: Install an extension on a specific configuration
    security:
      - cookieAuth: []
    parameters:
      - name: config_id
        in: path
        type: integer
        required: true
        description: Configuration ID
      - name: extension_code
        in: path
        type: string
        required: true
        description: Extension code
    responses:
      200:
        description: Extension installed successfully
      400:
        description: Installation failed
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        with platform_scope("platform administrator installs an extension on a shared framework configuration"):
            success = FrameworkExtensionService.install_extension(config_id, extension_code)

        if not success:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Failed to install extension (incompatible or not found)",
                    }
                ),
                400,
            )

        return jsonify(
            {"success": True, "message": f"Extension {extension_code} installed successfully"}
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/templates", methods=["GET"])
@platform_admin_required
def get_configuration_templates():
    """
    Get available configuration templates
    ---
    tags:
      - Framework Config
    summary: List configuration templates
    description: Get all available configuration templates with optional filtering
    security:
      - cookieAuth: []
    parameters:
      - name: template_type
        in: query
        type: string
        required: false
        description: Filter by template type
      - name: industry
        in: query
        type: string
        required: false
        description: Filter by industry
      - name: organization_size
        in: query
        type: string
        required: false
        description: Filter by organization size
    responses:
      200:
        description: List of templates
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        template_type = request.args.get("template_type")
        industry = request.args.get("industry")
        organization_size = request.args.get("organization_size")

        query = FrameworkConfigurationTemplate.query.filter_by(status="active")

        if template_type:
            query = query.filter_by(template_type=template_type)

        if industry:
            # Filter by target industries (JSON field)
            templates = query.all()
            filtered_templates = []
            for template in templates:
                if template.target_industries:
                    target_industries = json.loads(template.target_industries)
                    if industry in target_industries:
                        filtered_templates.append(template)
                else:
                    filtered_templates.append(template)
            templates = filtered_templates
        else:
            templates = query.all()

        if organization_size:
            templates = [t for t in templates if t.organization_size == organization_size]

        return jsonify(
            {
                "success": True,
                "data": [
                    {
                        "id": template.id,
                        "template_name": template.template_name,
                        "template_description": template.template_description,
                        "template_code": template.template_code,
                        "template_type": template.template_type,
                        "template_category": template.template_category,
                        "organization_size": template.organization_size,
                        "complexity_level": template.complexity_level,
                        "provider": template.provider,
                        "version": template.version,
                        "usage_count": template.usage_count,
                        "success_rate": template.success_rate,
                        "quality_score": template.quality_score,
                        "certification_status": template.certification_status,
                    }
                    for template in templates
                ],
                "count": len(templates),
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/templates/<int:template_id>/deploy", methods=["POST"])
@platform_admin_required
@audit_log("framework_template_deploy")
def deploy_template(template_id):
    """
    Deploy configuration template
    ---
    tags:
      - Framework Config
    summary: Deploy template
    description: Deploy a configuration template to create a new configuration
    security:
      - cookieAuth: []
    parameters:
      - name: template_id
        in: path
        type: integer
        required: true
        description: Template ID
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            configuration_name:
              type: string
            configuration_code:
              type: string
            organization_name:
              type: string
    responses:
      200:
        description: Template deployed successfully
      401:
        description: Unauthorized
      404:
        description: Template not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        template = FrameworkConfigurationTemplate.query.get(template_id)

        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404

        # Parse template configuration
        template_config = json.loads(template.template_configuration)

        # Override with user-provided values
        if "configuration_name" in data:
            template_config["configuration_name"] = data["configuration_name"]

        if "configuration_code" in data:
            template_config["configuration_code"] = data["configuration_code"]

        if "organization_name" in data:
            template_config["organization_name"] = data["organization_name"]

        # Create configuration from template, and count the use on the shared
        # template: both rows are platform-wide catalogue rows.
        with platform_scope("platform administrator deploys a shared framework template"):
            configuration = FrameworkConfigurationService.create_configuration(template_config)

            # Update template usage count
            template.usage_count += 1
            db.session.commit()

        return jsonify(
            {
                "success": True,
                "data": configuration.to_dict(),
                "message": "Template deployed successfully",
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/migrations", methods=["POST"])
@platform_admin_required
@audit_log("framework_migration_create")
def create_migration_mapping():
    """
    Create migration mapping from legacy framework
    ---
    tags:
      - Framework Config
    summary: Create migration mapping
    description: Create a migration mapping from a legacy framework to the new configuration
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - migration_name
            - migration_code
            - source_framework_name
            - target_configuration_id
          properties:
            migration_name:
              type: string
            migration_code:
              type: string
            source_framework_name:
              type: string
            target_configuration_id:
              type: integer
    responses:
      201:
        description: Migration mapping created
      400:
        description: Missing required fields
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = [
            "migration_name",
            "migration_code",
            "source_framework_name",
            "target_configuration_id",
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        with platform_scope("platform administrator creates a shared framework migration mapping"):
            migration = FrameworkMigrationService.create_migration_mapping(data)

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "id": migration.id,
                        "migration_name": migration.migration_name,
                        "migration_description": migration.migration_description,
                        "migration_code": migration.migration_code,
                        "source_framework_name": migration.source_framework_name,
                        "source_framework_version": migration.source_framework_version,
                        "source_framework_type": migration.source_framework_type,
                        "target_configuration_id": migration.target_configuration_id,
                        "status": migration.status,
                        "created_at": migration.created_at.isoformat(),
                    },
                    "message": "Migration mapping created successfully",
                }
            ),
            201,
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/migrations/<int:migration_id>/execute", methods=["POST"])
@platform_admin_required
@audit_log("framework_migration_execute")
def execute_migration(migration_id):
    """
    Execute framework migration
    ---
    tags:
      - Framework Config
    summary: Execute migration
    description: Execute a previously created migration mapping
    security:
      - cookieAuth: []
    parameters:
      - name: migration_id
        in: path
        type: integer
        required: true
        description: Migration ID
    responses:
      200:
        description: Migration executed
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        with platform_scope("platform administrator runs a shared framework migration mapping"):
            result = FrameworkMigrationService.execute_migration(migration_id)

        return jsonify(
            {
                "success": result["success"],
                "data": result.get("results"),
                "message": "Migration executed successfully"
                if result["success"]
                else "Migration failed",
                "error": result.get("error"),
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/instances", methods=["GET"])
@platform_admin_required
def get_framework_instances():
    """
    Get framework instances
    ---
    tags:
      - Framework Config
    summary: List framework instances
    description: Get all framework instances with optional filtering
    security:
      - cookieAuth: []
    parameters:
      - name: configuration_id
        in: query
        type: integer
        required: false
        description: Filter by configuration ID
      - name: status
        in: query
        type: string
        required: false
        description: Filter by status
    responses:
      200:
        description: List of instances
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        configuration_id = request.args.get("configuration_id")
        status = request.args.get("status")

        query = FrameworkInstance.query

        if configuration_id:
            query = query.filter_by(configuration_id=configuration_id)

        if status:
            query = query.filter_by(status=status)

        instances = query.order_by(FrameworkInstance.created_at.desc()).all()

        return jsonify(
            {
                "success": True,
                "data": [
                    {
                        "id": instance.id,
                        "instance_name": instance.instance_name,
                        "instance_description": instance.instance_description,
                        "configuration_id": instance.configuration_id,
                        "configuration_name": instance.configuration.configuration_name
                        if instance.configuration
                        else None,
                        "organization_unit": instance.organization_unit,
                        "geographic_location": instance.geographic_location,
                        "implementation_scope": instance.implementation_scope,
                        "implementation_date": instance.implementation_date.isoformat()
                        if instance.implementation_date
                        else None,
                        "current_maturity_level": instance.current_maturity_level,
                        "capabilities_implemented": instance.capabilities_implemented,
                        "capabilities_total": instance.capabilities_total,
                        "implementation_percentage": instance.implementation_percentage,
                        "user_adoption_rate": instance.user_adoption_rate,
                        "data_quality_score": instance.data_quality_score,
                        "compliance_score": instance.compliance_score,
                        "active_users": instance.active_users,
                        "status": instance.status,
                        "instance_owner": instance.instance_owner,
                        "created_at": instance.created_at.isoformat(),
                        "updated_at": instance.updated_at.isoformat(),
                    }
                    for instance in instances
                ],
                "count": len(instances),
            }
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/instances", methods=["POST"])
@platform_admin_required
@audit_log("framework_instance_create")
def create_framework_instance():
    """
    Create framework instance
    ---
    tags:
      - Framework Config
    summary: Create framework instance
    description: Create a new framework instance for a configuration
    security:
      - cookieAuth: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - instance_name
            - configuration_id
          properties:
            instance_name:
              type: string
            instance_description:
              type: string
            configuration_id:
              type: integer
            organization_unit:
              type: string
            geographic_location:
              type: string
            implementation_scope:
              type: string
            implementation_date:
              type: string
              format: date
    responses:
      201:
        description: Instance created successfully
      400:
        description: Missing required fields or invalid configuration
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ["instance_name", "configuration_id"]
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        # Check if configuration exists
        configuration = CapabilityFrameworkConfiguration.query.get(data["configuration_id"])
        if not configuration:
            return jsonify({"success": False, "error": "Configuration not found"}), 400

        # Create instance
        instance = FrameworkInstance(
            instance_name=data["instance_name"],
            instance_description=data.get("instance_description"),
            configuration_id=data["configuration_id"],
            organization_unit=data.get("organization_unit"),
            geographic_location=data.get("geographic_location"),
            implementation_scope=data.get("implementation_scope", "department"),
            implementation_date=datetime.strptime(data["implementation_date"], "%Y-%m-%d").date()
            if "implementation_date" in data
            else None,
            implementation_team=data.get("implementation_team"),
            instance_owner=data.get("instance_owner"),
            technical_contact=data.get("technical_contact"),
            business_sponsor=data.get("business_sponsor"),
        )

        db.session.add(instance)
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "id": instance.id,
                        "instance_name": instance.instance_name,
                        "configuration_id": instance.configuration_id,
                        "status": instance.status,
                        "created_at": instance.created_at.isoformat(),
                    },
                    "message": "Framework instance created successfully",
                }
            ),
            201,
        )

    except Exception:
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@framework_config_bp.route("/health", methods=["GET"])
@platform_admin_required
def health_check():
    """
    Framework Config API Health Check
    ---
    tags:
      - Framework Config
      - Health
    summary: Check Framework Configuration API health
    description: Returns health status specific to the Framework Configuration service
    responses:
      200:
        description: Service is healthy
        schema:
          type: object
          properties:
            success:
              type: boolean
            service:
              type: string
            status:
              type: string
              enum: [healthy, unhealthy]
            timestamp:
              type: string
              format: date-time
            checks:
              type: object
      503:
        description: Service is unhealthy
    """
    import os
    from datetime import datetime

    from sqlalchemy import text

    health_status = {
        "success": True,
        "service": "Framework Configuration API",
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {},
    }

    overall_healthy = True

    # Database connectivity check
    try:
        start_time = datetime.now()
        from app import db

        db.session.execute(text("SELECT 1"))  # tenant-exempt: system config
        db_time = (datetime.now() - start_time).total_seconds() * 1000

        # Get pool size (handle both callable and property versions)
        pool_size = getattr(db.engine.pool, "size", "unknown")
        if callable(pool_size):
            pool_size = pool_size()

        health_status["checks"]["database"] = {
            "status": "healthy",
            "response_time_ms": round(db_time, 2),
            "connection_pool": pool_size,
        }
    except Exception:
        overall_healthy = False
        health_status["checks"]["database"] = {"status": "unhealthy", "error": "See server logs for details"}

    # API-specific checks
    try:
        # Check if we can access configuration
        from app.models.models import APISettings

        api_count = APISettings.query.count()

        health_status["checks"]["api_config"] = {
            "status": "healthy",
            "configured_providers": api_count,
        }
    except Exception:
        overall_healthy = False
        health_status["checks"]["api_config"] = {"status": "unhealthy", "error": "See server logs for details"}

    # Basic memory check (without psutil)
    try:
        import resource

        memory_usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Convert to MB
        if os.name == "posix":
            if os.uname().sysname == "Darwin":
                memory_mb = memory_usage / 1024 / 1024  # bytes to MB
            else:
                memory_mb = memory_usage / 1024  # KB to MB
        else:
            memory_mb = memory_usage / 1024 / 1024  # Windows fallback

        health_status["checks"]["memory"] = {
            "status": "healthy",
            "process_usage_mb": round(memory_mb, 2),
            "note": "Basic memory check - install psutil for detailed monitoring",
        }
    except Exception:
        health_status["checks"]["memory"] = {
            "status": "unknown",
            "error": "See server logs for details",
            "note": "Memory monitoring unavailable - install psutil for detailed monitoring",
        }

    # Overall status
    health_status["status"] = "healthy" if overall_healthy else "unhealthy"
    health_status["success"] = overall_healthy

    # Return appropriate HTTP status code
    status_code = 200 if overall_healthy else 503
    return jsonify(health_status), status_code


@framework_config_bp.errorhandler(404)
def framework_config_not_found(error):
    """Handle blueprint-scoped 404 errors."""
    logger.warning(
        "Framework config API 404 route=%s method=%s: %s",
        request.path,
        request.method,
        error,
    )
    return jsonify({"success": False, "error": "Endpoint not found"}), 404


@framework_config_bp.errorhandler(500)
def framework_config_internal_error(error):
    """Handle blueprint-scoped 500 errors."""
    logger.error(
        "Framework config API 500 route=%s method=%s: %s",
        request.path,
        request.method,
        error,
        exc_info=True,
    )
    db.session.rollback()
    return jsonify({"success": False, "error": "An internal error occurred"}), 500

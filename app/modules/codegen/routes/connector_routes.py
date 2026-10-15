"""Connector CRUD routes for the Code Workbench.

Handles integration connectors: list, test, create, active, delete,
objects, mappings, schedule, pause, resume, and sync history.

Routes are registered on codegen_bp (imported from codegen_routes).
This module is loaded via:
    from app.modules.codegen.routes import connector_routes  # noqa: F401
at the bottom of codegen_routes.py.
"""
import logging

from flask import abort, jsonify, request
from flask_login import login_required

from app.models.solution_models import Solution
from app.modules.codegen.models import CodegenGeneration
from app.modules.codegen.routes.codegen_routes import codegen_bp
from app.modules.codegen.routes._helpers import _check_access
from app.utils.route_guards import require_entity_in_active_org

logger = logging.getLogger(__name__)


# ── Connector integration routes ──────────────────────────────────────────


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors", methods=["GET"])
@login_required
def list_connectors(solution_id):
    """List available connectors and suggest matches based on architecture tech stacks."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.services.connector_catalog_service import ConnectorCatalogService

    catalog = ConnectorCatalogService()
    connectors = catalog.list_connectors()

    # Suggest connectors based on architecture elements' tech stacks
    tech_stacks = []
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if gen and gen.uml_snapshot:
        elements = gen.uml_snapshot if isinstance(gen.uml_snapshot, list) else gen.uml_snapshot.get("elements", [])
        for el in elements:
            props = el.get("properties", {}) if isinstance(el, dict) else {}
            stack = props.get("technology_stack")
            if stack:
                tech_stacks.append(stack)

    suggestions = catalog.suggest(tech_stacks) if tech_stacks else []
    suggested_types = [s["type"] for s in suggestions]

    return jsonify({
        "connectors": connectors,
        "suggestions": suggested_types,
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/test", methods=["POST"])
@login_required
def test_connector_connection(solution_id):
    """Test connectivity to an external system before creating a connector."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    body = request.get_json(silent=True) or {}
    connector_type = body.get("connector_type")
    credentials = body.get("credentials", {})

    if not connector_type:
        return jsonify({"success": False, "error": "connector_type is required"}), 400

    orch = ConnectorOrchestrator()
    result = orch.test_connection(connector_type, credentials)
    return jsonify(result)


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors", methods=["POST"])
@login_required
def create_connector(solution_id):
    """Create a connector and start an n8n sync workflow."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    body = request.get_json(silent=True) or {}
    connector_type = body.get("connector_type")
    credentials = body.get("credentials", {})
    object_mappings = body.get("object_mappings", {})
    frequency = body.get("frequency", "hourly")
    target_api_url = body.get("target_api_url", request.host_url.rstrip("/"))

    if not connector_type:
        return jsonify({"success": False, "error": "connector_type is required"}), 400

    from app.modules.intelligence.services.connector_allowlist import (
        ConnectorNotPermitted,
        assert_connector_permitted,
    )

    try:
        assert_connector_permitted(connector_type)
    except ConnectorNotPermitted as exc:
        return jsonify({"success": False, "error": str(exc)}), 403

    from app.utils.ssrf_guard import BlockedOutboundURL, validate_user_supplied_url

    try:
        validate_user_supplied_url(target_api_url)
        base_url = credentials.get("base_url") if isinstance(credentials, dict) else None
        if base_url:
            validate_user_supplied_url(base_url)
    except BlockedOutboundURL:
        return jsonify({"success": False, "error": "That address is not allowed."}), 400

    try:
        orch = ConnectorOrchestrator()
        connector = orch.create_sync_workflow(
            solution_id=solution_id,
            connector_type=connector_type,
            credentials=credentials,
            object_mappings=object_mappings,
            target_api_url=target_api_url,
            frequency=frequency,
        )
        return jsonify({
            "success": True,
            "connector_id": connector.id,
            "n8n_workflow_id": connector.n8n_workflow_id,
            "sync_frequency": connector.sync_frequency,
        }), 201
    except BlockedOutboundURL:
        return jsonify({"success": False, "error": "That address is not allowed."}), 400
    except RuntimeError as exc:
        logger.error("Connector creation failed: %s", exc)
        return jsonify({"success": False, "error": "The connector could not be created."}), 502


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/active", methods=["GET"])
@login_required
def list_active_connectors(solution_id):
    """List active connectors for a solution."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.models import SolutionConnector

    connectors = SolutionConnector.query.filter_by(solution_id=solution_id).all()
    return jsonify({
        "connectors": [
            {
                "id": c.id,
                "connector_type": c.connector_type,
                "n8n_workflow_id": c.n8n_workflow_id,
                "sync_frequency": c.sync_frequency,
                "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
                "last_sync_status": c.last_sync_status,
                "records_synced": c.records_synced,
                "object_mappings": c.object_mappings,
            }
            for c in connectors
        ],
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/<int:connector_id>", methods=["DELETE"])
@login_required
def delete_connector(solution_id, connector_id):
    """Delete a connector and its n8n workflow."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.models import SolutionConnector
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    connector = SolutionConnector.query.get_or_404(connector_id)
    if connector.solution_id != solution_id:
        abort(404)

    orch = ConnectorOrchestrator()
    result = orch.delete_workflow(connector)

    if result["success"]:
        from app.modules.codegen.services.credential_vault import CredentialVault
        CredentialVault().delete(solution_id, connector.connector_type)

    return jsonify(result), 200 if result["success"] else 502


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/<int:connector_id>/objects", methods=["GET"])
@login_required
def discover_connector_objects(solution_id, connector_id):
    """Discover available source objects for a connector."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.models import SolutionConnector
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    connector = SolutionConnector.query.get_or_404(connector_id)
    if connector.solution_id != solution_id:
        abort(404)

    orch = ConnectorOrchestrator()
    objects = orch.discover_objects(connector.connector_type)
    return jsonify({"objects": objects, "connector_type": connector.connector_type})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/<int:connector_id>/mappings", methods=["PATCH"])
@login_required
def update_connector_mappings(solution_id, connector_id):
    """Update object mappings for a connector."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.models import SolutionConnector
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    connector = SolutionConnector.query.get_or_404(connector_id)
    if connector.solution_id != solution_id:
        abort(404)

    body = request.get_json(silent=True) or {}
    object_mappings = body.get("object_mappings", {})
    if not object_mappings:
        return jsonify({"success": False, "error": "object_mappings is required"}), 400

    orch = ConnectorOrchestrator()
    result = orch.map_objects(connector, object_mappings)
    return jsonify(result)


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/<int:connector_id>/schedule", methods=["PATCH"])
@login_required
def update_connector_schedule(solution_id, connector_id):
    """Update the sync frequency for a connector."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.models import SolutionConnector
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    connector = SolutionConnector.query.get_or_404(connector_id)
    if connector.solution_id != solution_id:
        abort(404)

    body = request.get_json(silent=True) or {}
    frequency = body.get("frequency")
    if not frequency:
        return jsonify({"success": False, "error": "frequency is required"}), 400

    orch = ConnectorOrchestrator()
    result = orch.schedule_sync(connector, frequency)
    return jsonify(result), 200 if result["success"] else 502


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/<int:connector_id>/pause", methods=["POST"])
@login_required
def pause_connector(solution_id, connector_id):
    """Pause sync for a connector by deactivating its n8n workflow."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.models import SolutionConnector
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    connector = SolutionConnector.query.get_or_404(connector_id)
    if connector.solution_id != solution_id:
        abort(404)

    if not connector.n8n_workflow_id:
        return jsonify({"success": False, "error": "No n8n workflow associated"}), 400

    orch = ConnectorOrchestrator()
    result = orch.pause_workflow(connector.n8n_workflow_id)
    return jsonify(result), 200 if result["success"] else 502


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/<int:connector_id>/resume", methods=["POST"])
@login_required
def resume_connector(solution_id, connector_id):
    """Resume sync for a connector by reactivating its n8n workflow."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.models import SolutionConnector
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    connector = SolutionConnector.query.get_or_404(connector_id)
    if connector.solution_id != solution_id:
        abort(404)

    if not connector.n8n_workflow_id:
        return jsonify({"success": False, "error": "No n8n workflow associated"}), 400

    orch = ConnectorOrchestrator()
    result = orch.resume_workflow(connector.n8n_workflow_id)
    return jsonify(result), 200 if result["success"] else 502


@codegen_bp.route("/solutions/<int:solution_id>/codegen/connectors/<int:connector_id>/history", methods=["GET"])
@login_required
def connector_sync_history(solution_id, connector_id):
    """Get sync execution history for a connector from n8n."""
    solution = require_entity_in_active_org(Solution, solution_id)
    if not _check_access(solution):
        abort(403)

    from app.modules.codegen.models import SolutionConnector
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator

    connector = SolutionConnector.query.get_or_404(connector_id)
    if connector.solution_id != solution_id:
        abort(404)

    if not connector.n8n_workflow_id:
        return jsonify({"success": True, "executions": []})

    orch = ConnectorOrchestrator()
    result = orch.get_sync_history(connector.n8n_workflow_id)
    return jsonify(result), 200 if result["success"] else 502

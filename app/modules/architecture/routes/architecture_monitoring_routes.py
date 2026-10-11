"""
This blueprint is mounted only when ARCHITECTURE_MONITORING_API_ENABLED is on.
Off by default; mounted when configured.

Architecture Monitoring API Routes

Provides REST API endpoints for continuous architecture monitoring,
baseline management, drift detection, and alert management.

Endpoints:
- Monitoring Status:
  - GET /api/architecture-monitoring/status - Get monitoring status
  - PUT /api/architecture-monitoring/status - Update monitoring status
  - PUT /api/architecture-monitoring/configure - Configure monitoring

- Baseline Management:
  - GET /api/architecture-monitoring/baseline - List baselines
  - POST /api/architecture-monitoring/baseline - Capture new baseline
  - GET /api/architecture-monitoring/baseline/<id> - Get baseline
  - DELETE /api/architecture-monitoring/baseline/<id> - Delete baseline
  - POST /api/architecture-monitoring/baseline/<id>/activate - Set as active

- Scanning:
  - POST /api/architecture-monitoring/scan - Trigger manual scan

- Drift Analysis:
  - GET /api/architecture-monitoring/drift - Get drift analysis
  - GET /api/architecture-monitoring/drift/<baseline_id> - Get drift vs specific baseline

- Alerts:
  - GET /api/architecture-monitoring/alerts - Get alerts
  - GET /api/architecture-monitoring/alerts/<id> - Get specific alert
  - POST /api/architecture-monitoring/alerts/<id>/acknowledge - Acknowledge alert
  - POST /api/architecture-monitoring/alerts/bulk-acknowledge - Bulk acknowledge
  - DELETE /api/architecture-monitoring/alerts/acknowledged - Clear acknowledged alerts
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log, require_roles
from app.modules.architecture.services.architecture_monitoring_service import ArchitectureMonitoringService
from app.utils.pagination import safe_int_arg
from config import _env_bool

architecture_monitoring_bp = Blueprint(
    "architecture_monitoring", __name__, url_prefix="/api/architecture-monitoring"
)


def monitoring_api_enabled(app) -> bool:
    """True when the monitoring API should be mounted: app config or the environment says so."""
    if app.config.get("ARCHITECTURE_MONITORING_API_ENABLED"):
        return True
    return _env_bool("ARCHITECTURE_MONITORING_API_ENABLED", False)


def _get_service() -> ArchitectureMonitoringService:
    """Get service instance."""
    return ArchitectureMonitoringService()


def _get_current_user() -> str:
    """Get current user identifier."""
    if hasattr(current_user, "username"):
        return current_user.username
    elif hasattr(current_user, "id"):
        return str(current_user.id)
    return "system"


# =============================================================================
# Monitoring Status Endpoints
# =============================================================================


@architecture_monitoring_bp.route("/status", methods=["GET"])
@login_required
def get_monitoring_status():
    """
    Get current monitoring status and configuration.

    Returns:
        JSON with monitoring status, configuration, and alert counts
    """
    service = _get_service()
    result = service.get_monitoring_status()

    return jsonify(result)


@architecture_monitoring_bp.route("/status", methods=["PUT"])
@login_required
@require_roles("admin")
@audit_log("monitoring_status_update")
def set_monitoring_status():
    """
    Update monitoring status (active, paused).

    Request Body:
        {
            "status": "active" | "paused"
        }

    Returns:
        JSON with updated status
    """
    data = request.get_json()
    if not data or "status" not in data:
        return jsonify({"success": False, "error": "status is required"}), 400

    service = _get_service()
    result = service.set_monitoring_status(data["status"])

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)


@architecture_monitoring_bp.route("/configure", methods=["PUT"])
@login_required
@require_roles("admin")
@audit_log("monitoring_configure")
def configure_monitoring():
    """
    Configure monitoring parameters.

    Request Body:
        {
            "scan_interval_minutes": 60,
            "coverage_warning_threshold": 5,
            "coverage_critical_threshold": 15,
            "health_warning_threshold": 10,
            "health_critical_threshold": 20
        }

    Returns:
        JSON with configuration result
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body is required"}), 400

    service = _get_service()
    result = service.configure_monitoring(
        scan_interval_minutes=data.get("scan_interval_minutes"),
        coverage_warning_threshold=data.get("coverage_warning_threshold"),
        coverage_critical_threshold=data.get("coverage_critical_threshold"),
        health_warning_threshold=data.get("health_warning_threshold"),
        health_critical_threshold=data.get("health_critical_threshold"),
    )

    return jsonify(result)


# =============================================================================
# Baseline Management Endpoints
# =============================================================================


@architecture_monitoring_bp.route("/baseline", methods=["GET"])
@login_required
def list_baselines():
    """
    List all captured baselines.

    Returns:
        JSON with list of baselines
    """
    service = _get_service()
    result = service.list_baselines()

    return jsonify({"success": True, "data": result})


@architecture_monitoring_bp.route("/baseline", methods=["POST"])
@login_required
@require_roles("admin", "enterprise_architect")
@audit_log("monitoring_baseline_capture")
def capture_baseline():
    """
    Capture current architecture state as a baseline.

    Request Body:
        {
            "name": "Baseline Name",
            "description": "Optional description",
            "set_as_active": true
        }

    Returns:
        JSON with baseline details
    """
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"success": False, "error": "name is required"}), 400

    service = _get_service()
    result = service.capture_baseline(
        name=data["name"],
        description=data.get("description"),
        created_by=_get_current_user(),
        set_as_active=data.get("set_as_active", True),
    )

    if not result.get("success"):
        return jsonify(result), 500

    return jsonify(result), 201


@architecture_monitoring_bp.route("/baseline/<baseline_id>", methods=["GET"])
@login_required
def get_baseline(baseline_id: str):
    """
    Get a specific baseline by ID.

    Path Parameters:
        baseline_id: ID of the baseline

    Returns:
        JSON with baseline details including snapshots
    """
    service = _get_service()
    result = service.get_baseline(baseline_id)

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify(result)


@architecture_monitoring_bp.route("/baseline/<baseline_id>", methods=["DELETE"])
@login_required
@require_roles("admin")
@audit_log("monitoring_baseline_delete")
def delete_baseline(baseline_id: str):
    """
    Delete a baseline.

    Path Parameters:
        baseline_id: ID of the baseline to delete

    Returns:
        JSON with deletion result
    """
    service = _get_service()
    result = service.delete_baseline(baseline_id)

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify(result)


@architecture_monitoring_bp.route("/baseline/<baseline_id>/activate", methods=["POST"])
@login_required
@require_roles("admin", "enterprise_architect")
@audit_log("monitoring_baseline_activate")
def activate_baseline(baseline_id: str):
    """
    Set a baseline as the active baseline for drift comparison.

    Path Parameters:
        baseline_id: ID of the baseline to activate

    Returns:
        JSON with result
    """
    service = _get_service()
    result = service.set_active_baseline(baseline_id)

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify(result)


# =============================================================================
# Scanning Endpoints
# =============================================================================


@architecture_monitoring_bp.route("/scan", methods=["POST"])
@login_required
@audit_log("monitoring_scan_trigger")
def trigger_scan():
    """
    Trigger a manual architecture scan and drift analysis.

    Returns:
        JSON with scan results and any new alerts
    """
    service = _get_service()
    result = service.trigger_scan(created_by=_get_current_user())

    if not result.get("success"):
        return jsonify(result), 400 if "paused" in result.get("error", "") else 500

    return jsonify(result)


# =============================================================================
# Drift Analysis Endpoints
# =============================================================================


@architecture_monitoring_bp.route("/drift", methods=["GET"])
@login_required
def get_drift_analysis():
    """
    Get drift analysis against the active baseline.

    Query Parameters:
        baseline_id (str): Optional specific baseline to compare against

    Returns:
        JSON with drift analysis results
    """
    baseline_id = request.args.get("baseline_id")

    service = _get_service()
    result = service.analyze_drift(baseline_id)

    if not result.get("success"):
        return jsonify(result), 400

    return jsonify(result)


@architecture_monitoring_bp.route("/drift/<baseline_id>", methods=["GET"])
@login_required
def get_drift_against_baseline(baseline_id: str):
    """
    Get drift analysis against a specific baseline.

    Path Parameters:
        baseline_id: ID of the baseline to compare against

    Returns:
        JSON with drift analysis results
    """
    service = _get_service()
    result = service.analyze_drift(baseline_id)

    if not result.get("success"):
        return jsonify(result), 404 if "not found" in result.get("error", "").lower() else 400

    return jsonify(result)


# =============================================================================
# Alert Management Endpoints
# =============================================================================


@architecture_monitoring_bp.route("/alerts", methods=["GET"])
@login_required
def get_alerts():
    """
    Get alerts with optional filters.

    Query Parameters:
        severity (str): Filter by severity (info, warning, critical)
        alert_type (str): Filter by alert type
        acknowledged (bool): Filter by acknowledgment status
        limit (int): Maximum results (default: 100)
        offset (int): Pagination offset (default: 0)

    Returns:
        JSON with alerts
    """
    severity = request.args.get("severity")
    alert_type = request.args.get("alert_type")
    acknowledged = request.args.get("acknowledged")
    limit = safe_int_arg('limit', 100, minimum=1, maximum=500)
    offset = safe_int_arg('offset', 0, minimum=0)

    # Convert acknowledged string to boolean
    acknowledged_bool = None
    if acknowledged is not None:
        acknowledged_bool = acknowledged.lower() in ("true", "1", "yes")

    service = _get_service()
    result = service.get_alerts(
        severity=severity,
        alert_type=alert_type,
        acknowledged=acknowledged_bool,
        limit=limit,
        offset=offset,
    )

    return jsonify({"success": True, "data": result})


@architecture_monitoring_bp.route("/alerts/<alert_id>", methods=["GET"])
@login_required
def get_alert(alert_id: str):
    """
    Get a specific alert by ID.

    Path Parameters:
        alert_id: ID of the alert

    Returns:
        JSON with alert details
    """
    service = _get_service()
    result = service.get_alert(alert_id)

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify(result)


@architecture_monitoring_bp.route("/alerts/<alert_id>/acknowledge", methods=["POST"])
@login_required
@audit_log("monitoring_alert_ack")
def acknowledge_alert(alert_id: str):
    """
    Acknowledge an alert.

    Path Parameters:
        alert_id: ID of the alert to acknowledge

    Returns:
        JSON with result
    """
    service = _get_service()
    result = service.acknowledge_alert(alert_id=alert_id, acknowledged_by=_get_current_user())

    if not result.get("success"):
        return jsonify(result), 404

    return jsonify(result)


@architecture_monitoring_bp.route("/alerts/bulk-acknowledge", methods=["POST"])
@login_required
@audit_log("monitoring_alerts_bulk_ack")
def bulk_acknowledge_alerts():
    """
    Acknowledge multiple alerts.

    Request Body:
        {
            "alert_ids": ["id1", "id2", "id3"]
        }

    Returns:
        JSON with result
    """
    data = request.get_json()
    if not data or "alert_ids" not in data:
        return jsonify({"success": False, "error": "alert_ids is required"}), 400

    service = _get_service()
    result = service.bulk_acknowledge_alerts(
        alert_ids=data["alert_ids"], acknowledged_by=_get_current_user()
    )

    return jsonify(result)


@architecture_monitoring_bp.route("/alerts/acknowledged", methods=["DELETE"])
@login_required
@require_roles("admin")
@audit_log("monitoring_alerts_clear")
def clear_acknowledged_alerts():
    """
    Clear all acknowledged alerts.

    Returns:
        JSON with result
    """
    service = _get_service()
    result = service.clear_acknowledged_alerts()

    return jsonify(result)


# =============================================================================
# Health Check
# =============================================================================


@architecture_monitoring_bp.route("/health", methods=["GET"])
@login_required
def health_check():
    """
    Health check endpoint for the Architecture Monitoring service.

    Returns:
        JSON with service status
    """
    return jsonify(
        {
            "success": True,
            "service": "architecture-monitoring",
            "status": "healthy",
            "endpoints": [
                "GET /api/architecture-monitoring/status",
                "PUT /api/architecture-monitoring/status",
                "PUT /api/architecture-monitoring/configure",
                "GET /api/architecture-monitoring/baseline",
                "POST /api/architecture-monitoring/baseline",
                "GET /api/architecture-monitoring/baseline/<id>",
                "DELETE /api/architecture-monitoring/baseline/<id>",
                "POST /api/architecture-monitoring/baseline/<id>/activate",
                "POST /api/architecture-monitoring/scan",
                "GET /api/architecture-monitoring/drift",
                "GET /api/architecture-monitoring/drift/<baseline_id>",
                "GET /api/architecture-monitoring/alerts",
                "GET /api/architecture-monitoring/alerts/<id>",
                "POST /api/architecture-monitoring/alerts/<id>/acknowledge",
                "POST /api/architecture-monitoring/alerts/bulk-acknowledge",
                "DELETE /api/architecture-monitoring/alerts/acknowledged",
            ],
        }
    )

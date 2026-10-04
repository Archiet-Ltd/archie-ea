"""
Connector health monitoring routes.
Provides UI and API for monitoring external system integrations.
"""

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from app.decorators import admin_required, audit_log

from app.services.connector_framework import (
    ConnectorConfig,
    ConnectorManager,
    SyncLog,
)
from flask_login import login_required
from app.utils.api_response import error_response, not_found_response
from app.utils.pagination import safe_int_arg

connector_bp = Blueprint("connectors", __name__, url_prefix="/integrations")


@connector_bp.route("/connectors")
@login_required
@admin_required
# H5: this page's own empty state told a user to "Configure connectors in
# connector_framework.py" -- a source-file reference with no in-product
# config path, because there is none: connectors are wired in code, not
# through this UI. Nobody but an operator with repo access could act on
# that instruction, so the page is internal/admin-only, not a
# customer-facing surface -- @login_required alone let any signed-in user
# reach it.
def dashboard():
    """Connector health monitoring dashboard (admin-only, no self-serve config)."""
    return render_template("integrations/connector_dashboard.html")


@connector_bp.route("/api/connectors", methods=["GET"])
@login_required
@admin_required
def api_list_connectors():
    """List all configured connectors with current status."""
    try:
        connectors = ConnectorConfig.query.limit(200).all()

        result = []
        for conn in connectors:
            # Get latest sync log
            latest_sync = (
                SyncLog.query.filter_by(connector_id=conn.id)
                .order_by(SyncLog.started_at.desc())
                .first()
            )

            result.append(
                {
                    "id": conn.id,
                    "name": conn.name,
                    "connector_type": conn.connector_type.value,
                    "status": conn.status.value,
                    "sync_mode": conn.sync_mode.value,
                    "last_sync": latest_sync.completed_at.isoformat()
                    if latest_sync and latest_sync.completed_at
                    else None,
                    "last_sync_status": latest_sync.status if latest_sync else None,
                    "created_at": conn.created_at.isoformat(),
                    "updated_at": conn.updated_at.isoformat(),
                }
            )

        return jsonify({"connectors": result}), 200

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@connector_bp.route("/api/connectors/<string:connector_id>", methods=["GET"])
@login_required
@admin_required
def api_get_connector(connector_id):
    """Get detailed connector information."""
    try:
        conn = ConnectorConfig.query.get(connector_id)
        if not conn:
            return jsonify({"error": "Connector not found"}), 404

        # Get recent sync logs (last 10)
        recent_syncs = (
            SyncLog.query.filter_by(connector_id=connector_id)
            .order_by(SyncLog.started_at.desc())
            .limit(10)
            .all()
        )

        return (
            jsonify(
                {
                    "connector": {
                        "id": conn.id,
                        "name": conn.name,
                        "connector_type": conn.connector_type.value,
                        "status": conn.status.value,
                        "sync_mode": conn.sync_mode.value,
                        "config": conn.config_data,  # Field mappings, etc.
                        "created_at": conn.created_at.isoformat(),
                        "updated_at": conn.updated_at.isoformat(),
                    },
                    "recent_syncs": [
                        {
                            "id": log.id,
                            "sync_type": log.sync_type,
                            "status": log.status,
                            "records_processed": log.records_processed,
                            "records_created": log.records_created,
                            "records_updated": log.records_updated,
                            "records_deleted": log.records_deleted,
                            "error_message": log.error_message,
                            "started_at": log.started_at.isoformat(),
                            "completed_at": log.completed_at.isoformat()
                            if log.completed_at
                            else None,
                        }
                        for log in recent_syncs
                    ],
                }
            ),
            200,
        )

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@connector_bp.route("/api/connectors/<string:connector_id>/sync-logs", methods=["GET"])
@login_required
@admin_required
def api_get_sync_logs(connector_id):
    """Get sync history for a connector."""
    try:
        # Pagination
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 20, minimum=1, maximum=500)

        # Date range filter
        days = request.args.get("days", 30, type=int)
        since = datetime.utcnow() - timedelta(days=days)

        query = (
            SyncLog.query.filter_by(connector_id=connector_id)
            .filter(SyncLog.started_at >= since)
            .order_by(SyncLog.started_at.desc())
        )

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        return (
            jsonify(
                {
                    "logs": [
                        {
                            "id": log.id,
                            "sync_type": log.sync_type,
                            "status": log.status,
                            "records_processed": log.records_processed,
                            "records_created": log.records_created,
                            "records_updated": log.records_updated,
                            "records_deleted": log.records_deleted,
                            "error_message": log.error_message,
                            "started_at": log.started_at.isoformat(),
                            "completed_at": log.completed_at.isoformat()
                            if log.completed_at
                            else None,
                            "duration_seconds": (
                                (log.completed_at - log.started_at).total_seconds()
                                if log.completed_at
                                else None
                            ),
                        }
                        for log in pagination.items
                    ],
                    "pagination": {
                        "page": page,
                        "per_page": per_page,
                        "total": pagination.total,
                        "pages": pagination.pages,
                    },
                }
            ),
            200,
        )

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@connector_bp.route("/api/connectors/<string:connector_id>/test", methods=["POST"])
@login_required
@admin_required
@audit_log("connector_test")
def api_test_connection(connector_id):
    """Test connection to external system."""
    try:
        conn = ConnectorConfig.query.get(connector_id)
        if not conn:
            return jsonify({"error": "Connector not found"}), 404

        # Get connector instance from manager
        manager = ConnectorManager()
        connector = manager.get_connector(connector_id)

        if not connector:
            return jsonify({"error": "Connector not initialized"}), 500

        # Test connection (async in production, sync for simplicity here)
        import asyncio

        success = asyncio.run(connector.test_connection())

        return (
            jsonify(
                {
                    "success": success,
                    "message": "Connection successful" if success else "Connection failed",
                    "tested_at": datetime.utcnow().isoformat(),
                }
            ),
            200,
        )

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@connector_bp.route("/api/connectors/<string:connector_id>/sync", methods=["POST"])
@login_required
@admin_required
@audit_log("connector_sync")
def api_trigger_sync(connector_id):
    """Manually trigger a sync for a connector.

    Sync is not available yet for any connector type: no connector in
    app/connectors/ is ever registered with ConnectorManager, and none of
    their batch_sync implementations persist a fetched record anywhere --
    they log a "mapped but not persisted" line instead. This always
    returned a generic 500 (ConnectorManager has no get_connector method),
    which read as a bug rather than as the missing feature it is; this
    reports that honestly instead.
    """
    try:
        conn = ConnectorConfig.query.get(connector_id)
    except Exception:
        return error_response(
            message="An internal error occurred",
            code="INTERNAL_ERROR",
            status_code=500,
        )

    if not conn:
        return not_found_response("Connector")

    return error_response(
        message=(
            "Sync is not available for this connector yet. No connector "
            "type in this deployment is wired to persist synced records, "
            "so there is nothing to run."
        ),
        code="SYNC_NOT_IMPLEMENTED",
        status_code=501,
    )

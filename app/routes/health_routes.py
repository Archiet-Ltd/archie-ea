"""
Health check endpoints for load balancer and Kubernetes liveness/readiness probes.
No authentication required — these must be reachable without a session.
"""
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from app.extensions import db
from app.services.platform_slo_service import get_platform_slo_status

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health():
    """Liveness probe — confirms the Flask process is alive."""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "archie",
    }), 200


@health_bp.route("/health/db", methods=["GET"])
def health_db():
    """Readiness probe — confirms DB connectivity is healthy."""
    try:
        db.session.execute(db.text("SELECT 1"))  # tenant-exempt: health check
        return jsonify({
            "status": "ok",
            "database": "connected",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "database": "unreachable",
            "detail": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 503


@health_bp.route("/health/slo", methods=["GET"])
def health_slo():
    """Internal platform SLO attainment.

    Unauthenticated like /health and /health/db — production watch alerts on
    this. Returns only aggregate objective numbers: no organisation names,
    user data or request paths (see app/services/platform_slo_service.py).
    """
    return jsonify(get_platform_slo_status()), 200

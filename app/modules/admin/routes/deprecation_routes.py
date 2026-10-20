"""
Deprecation Status Dashboard Routes (migrated).

Migrated from: app/routes/admin/deprecation_routes.py
Blueprint name preserved: "deprecation"
URL prefix preserved: /admin/deprecation
"""
import logging
import os
from datetime import datetime

import requests
from flask import Blueprint, g, jsonify, render_template, request
from flask_login import current_user, login_required

from app.decorators import audit_log, admin_required
from app.extensions import db
from app.middleware.tenant_decorators import (
    is_platform_admin,
    org_admin_required,
    platform_admin_required,
)

from app.utils.deprecation import (
    create_retiring_notices,
    get_deprecation_metrics,
    mark_operation_retiring,
    validate_retirement,
)

logger = logging.getLogger(__name__)

deprecation_bp = Blueprint("deprecation", __name__, url_prefix="/admin/deprecation")

WEBHOOK_TIMEOUT = 10


def get_webhook_urls():
    """Get webhook URLs from environment or config."""
    return {
        "pagerduty": os.environ.get("DEPRECATION_PAGERDUTY_WEBHOOK"),
        "slack": os.environ.get("DEPRECATION_SLACK_WEBHOOK"),
        "opsgenie": os.environ.get("DEPRECATION_OPSGENIE_WEBHOOK"),
    }


def send_pagerduty_alert(alert_data):
    """Send alert to PagerDuty."""
    webhook_url = get_webhook_urls()["pagerduty"]
    if not webhook_url:
        return {"success": False, "error": "PagerDuty webhook not configured"}

    payload = {
        "routing_key": os.environ.get("PAGERDUTY_ROUTING_KEY", ""),
        "event_action": "trigger",
        "dedup_key": f"deprecation-{alert_data.get('endpoint', 'unknown')}",
        "payload": {
            "summary": alert_data.get("summary", "Deprecation alert"),
            "severity": alert_data.get("severity", "warning"),
            "source": "flask-deprecation-monitor",
            "timestamp": datetime.utcnow().isoformat(),
            "custom_details": alert_data,
        },
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=WEBHOOK_TIMEOUT)
        return {"success": response.status_code in (200, 201, 202)}
    except Exception as e:
        logger.error(f"Failed to send PagerDuty alert: {e}")
        return {"success": False, "error": str(e)}


def send_slack_alert(alert_data):
    """Send alert to Slack."""
    webhook_url = get_webhook_urls()["slack"]
    if not webhook_url:
        return {"success": False, "error": "Slack webhook not configured"}

    severity_emoji = {
        "critical": ":rotating_light:",
        "warning": ":warning:",
        "info": ":information_source:",
    }
    emoji = severity_emoji.get(alert_data.get("severity"), ":warning:")

    payload = {
        "attachments": [
            {
                "color": {
                    "critical": "danger",
                    "warning": "warning",
                    "info": "good",
                }.get(alert_data.get("severity"), "warning"),
                "title": f"{emoji} Deprecation Alert",
                "fields": [
                    {
                        "title": "Endpoint",
                        "value": alert_data.get("endpoint", "unknown"),
                        "short": True,
                    },
                    {
                        "title": "Severity",
                        "value": alert_data.get("severity", "unknown"),
                        "short": True,
                    },
                    {
                        "title": "Value",
                        "value": str(alert_data.get("value", "N/A")),
                        "short": True,
                    },
                    {
                        "title": "Description",
                        "value": alert_data.get("description", "N/A"),
                        "short": False,
                    },
                ],
                "footer": "Deprecation Monitor",
                "ts": int(datetime.utcnow().timestamp()),
            }
        ]
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=WEBHOOK_TIMEOUT)
        return {"success": response.status_code in (200, 201, 202)}
    except Exception as e:
        logger.error(f"Failed to send Slack alert: {e}")
        return {"success": False, "error": str(e)}


def send_opsgenie_alert(alert_data):
    """Send alert to OpsGenie."""
    webhook_url = get_webhook_urls()["opsgenie"]
    if not webhook_url:
        return {"success": False, "error": "OpsGenie webhook not configured"}

    payload = {
        "message": f"Deprecation Alert: {alert_data.get('endpoint', 'unknown')}",
        "description": alert_data.get("description", "Deprecation alert"),
        "priority": {"critical": "P1", "warning": "P3", "info": "P5"}.get(
            alert_data.get("severity"), "P3"
        ),
        "tags": ["deprecation", alert_data.get("severity", "warning")],
        "source": "flask-deprecation-monitor",
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={
                "Authorization": f"GenieKey {os.environ.get('OPSGENIE_API_KEY', '')}"
            },
            timeout=WEBHOOK_TIMEOUT,
        )
        return {"success": response.status_code in (200, 201, 202)}
    except Exception as e:
        logger.error(f"Failed to send OpsGenie alert: {e}")
        return {"success": False, "error": str(e)}


def clients_response():
    """The interface consumers of the active organisation, as a JSON response.

    An organisation administrator sees their own organisation's consumers with
    names, operation, last call and call count. A platform administrator sees
    only a count of consumers and calls per organisation, never a name. Shared
    by the live admin module and the guardrailed one so both answer alike.
    """
    from app.jobs.tenant_safe_job import platform_scope
    from app.modules.oauth_provider.models import ApiOperationCall, OAuthClient

    if is_platform_admin(current_user):
        with platform_scope("deprecation consumer counts per organisation"):
            rows = db.session.execute(
                db.text(
                    "SELECT organization_id, COUNT(DISTINCT consumer_key) AS client_count, "
                    "SUM(call_count) AS total_calls FROM api_operation_calls "
                    "GROUP BY organization_id ORDER BY organization_id"
                )
            ).all()
        return jsonify(
            {
                "organisations": [
                    {
                        "organization_id": row.organization_id,
                        "client_count": int(row.client_count),
                        "total_calls": int(row.total_calls),
                    }
                    for row in rows
                ]
            }
        )

    rows = (
        db.session.query(ApiOperationCall, OAuthClient.client_name)
        .outerjoin(OAuthClient, ApiOperationCall.client_id == OAuthClient.client_id)
        .filter(ApiOperationCall.organization_id == g.current_org_id)
        .order_by(ApiOperationCall.operation_id, ApiOperationCall.consumer_key)
        .all()
    )
    return jsonify(
        {
            "clients": [
                {
                    "client_id": call.consumer_key,
                    "client_name": name or "Personal token",
                    "token_kind": call.token_kind,
                    "operation_id": call.operation_id,
                    "last_called_at": (
                        call.last_called_at.isoformat() if call.last_called_at else None
                    ),
                    "call_count": call.call_count,
                }
                for call, name in rows
            ]
        }
    )


def mark_retiring_response():
    """Mark an operation as retiring for the active organisation, as a JSON response.

    Stores the retirement, notifies each affected consumer owner once, and
    commits both. Marking the same operation again notifies nobody twice.
    """
    data = request.get_json(silent=True) or {}
    try:
        retires_on, replacement = validate_retirement(
            data.get("operation_id"), data.get("retires_on"), data.get("replacement_operation_id")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    retirement = mark_operation_retiring(
        g.current_org_id,
        data["operation_id"],
        retires_on,
        replacement,
        user_id=current_user.id,
    )
    created = create_retiring_notices(retirement)
    db.session.commit()
    return jsonify(
        {
            "status": "marked",
            "operation_id": retirement.operation_id,
            "retires_on": retirement.retires_on.isoformat(),
            "replacement_operation_id": retirement.replacement_operation_id,
            "notices_created": created,
        }
    )


@deprecation_bp.route("/")
@login_required
# The page itself shows only the active organisation's interface consumers
# (the platform administrator's view is counts), so an organisation
# administrator may open it. The metrics, alert and webhook routes below stay
# platform-only: get_deprecation_metrics() is process-wide, deployment-level
# API usage telemetry (not tenant data), and the webhooks send alerts to
# PagerDuty/Slack/OpsGenie using shared, platform-level credentials. A tenant
# admin reaching all of that was the R1 admin-rbac systemic fix.
@org_admin_required
def dashboard():
    """Render the deprecation status dashboard."""
    return render_template("admin/deprecation_status.html")


@deprecation_bp.route("/api/clients")
@login_required
@org_admin_required
def api_clients():
    """Interface consumers of the active organisation (counts only for a platform admin)."""
    return clients_response()


@deprecation_bp.route("/api/mark-retiring", methods=["POST"])
@login_required
@org_admin_required
@audit_log("mark_operation_retiring")
def api_mark_retiring():
    """Mark an operation as retiring and notify its consumers' owners once."""
    return mark_retiring_response()


@deprecation_bp.route("/api/stats")
@login_required
@platform_admin_required
@admin_required
def api_stats():
    """Get deprecation metrics as JSON."""
    metrics = get_deprecation_metrics()
    stats = metrics.get_usage_stats()
    return jsonify(stats)


@deprecation_bp.route("/api/alerts")
@login_required
@platform_admin_required
@admin_required
def api_alerts():
    """Get deprecation spike alerts as JSON."""
    metrics = get_deprecation_metrics()
    threshold = request.args.get("threshold", 100, type=int)
    window_minutes = request.args.get("window", 5, type=int)
    alerts = metrics.get_spike_alerts(
        threshold=threshold, window_minutes=window_minutes
    )
    return jsonify(alerts)


@deprecation_bp.route("/api/velocity")
@login_required
@platform_admin_required
@admin_required
def api_velocity():
    """Get endpoint velocity as JSON."""
    metrics = get_deprecation_metrics()
    endpoint = request.args.get("endpoint")
    window_minutes = request.args.get("window", 10, type=int)

    if endpoint:
        velocity = metrics.get_endpoint_velocity(endpoint, window_minutes)
        return jsonify({"endpoint": endpoint, "velocity_rpm": velocity})

    velocities = {}
    for ep in metrics.get_usage_counts().keys():
        velocities[ep] = metrics.get_endpoint_velocity(ep, window_minutes)

    return jsonify({"velocities": velocities})


@deprecation_bp.route("/api/export")
@login_required
@platform_admin_required
@admin_required
def api_export():
    """Export metrics in monitoring-compatible format."""
    metrics = get_deprecation_metrics()
    return jsonify(metrics.export_metrics())


@deprecation_bp.route("/api/webhook", methods=["POST"])
@login_required
@platform_admin_required
@admin_required
@audit_log("deprecation_webhook_send")
def api_webhook():
    """Webhook endpoint to send deprecation alerts to external systems."""
    try:
        data = request.get_json() or {}
        alerts = data.get("alerts", [])
        targets = data.get("targets", ["pagerduty", "slack", "opsgenie"])

        if not alerts:
            metrics = get_deprecation_metrics()
            alerts = metrics.get_spike_alerts(threshold=100, window_minutes=5)

        results = {}
        delivered_count = 0

        for alert in alerts:
            if "pagerduty" in targets:
                pd_result = send_pagerduty_alert(alert)
                results["pagerduty"] = pd_result
                if pd_result.get("success"):
                    delivered_count += 1

            if "slack" in targets:
                slack_result = send_slack_alert(alert)
                results["slack"] = slack_result
                if slack_result.get("success"):
                    delivered_count += 1

            if "opsgenie" in targets:
                og_result = send_opsgenie_alert(alert)
                results["opsgenie"] = og_result
                if og_result.get("success"):
                    delivered_count += 1

        logger.info(f"Webhook delivered {delivered_count} alerts")

        return jsonify(
            {
                "delivered": delivered_count,
                "total_alerts": len(alerts),
                "results": results,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@deprecation_bp.route("/api/webhook/test", methods=["POST"])
@platform_admin_required
@admin_required
@audit_log("deprecation_webhook_test")
def api_webhook_test():
    """Test webhook configuration by sending a test alert."""
    test_alert = {
        "endpoint": "test-endpoint",
        "severity": "warning",
        "summary": "Test deprecation alert",
        "description": "This is a test alert from the deprecation monitor",
        "value": 42,
    }

    return jsonify(
        {"test_alert": test_alert, "webhook_urls_configured": bool(get_webhook_urls())}
    )

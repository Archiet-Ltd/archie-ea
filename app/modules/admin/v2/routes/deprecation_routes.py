"""
Deprecation Status Dashboard Routes v2 — guardrail-enabled.

Uses the new architecture:
- @timed_route for automatic metrics collection on all endpoints
- Observability (request_id in response headers)

URL prefix preserved: /admin/deprecation (set on blueprint directly)
Blueprint name: deprecation (same as v1 — no cross-module url_for refs found)

All 7 routes preserved exactly from v1 deprecation_routes.py.
"""
import logging
import os
from datetime import datetime

import requests
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from app.decorators import admin_required, audit_log
from app.middleware.tenant_decorators import platform_admin_required

from app.core.compat import mark_blueprint_guardrailed
from app.core.decorators import timed_route
from app.utils.deprecation import get_deprecation_metrics

logger = logging.getLogger(__name__)

deprecation_bp_v2 = Blueprint("deprecation", __name__, url_prefix="/admin/deprecation")
mark_blueprint_guardrailed(deprecation_bp_v2)

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


@deprecation_bp_v2.route("/")
@timed_route
@login_required
# get_deprecation_metrics() is process-wide, deployment-level API usage
# telemetry (not tenant data), and the webhook routes below send alerts to
# PagerDuty/Slack/OpsGenie using shared, platform-level credentials.
# admin_required alone let any tenant's own admin reach all of this
# (R1 admin-rbac systemic fix).
@platform_admin_required
@admin_required
def dashboard():
    """Render the deprecation status dashboard."""
    return render_template("admin/deprecation_status.html")


@deprecation_bp_v2.route("/api/stats")
@timed_route
@login_required
@platform_admin_required
@admin_required
def api_stats():
    """Get deprecation metrics as JSON."""
    metrics = get_deprecation_metrics()
    stats = metrics.get_usage_stats()
    return jsonify(stats)


@deprecation_bp_v2.route("/api/alerts")
@timed_route
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


@deprecation_bp_v2.route("/api/velocity")
@timed_route
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


@deprecation_bp_v2.route("/api/export")
@timed_route
@login_required
@platform_admin_required
@admin_required
def api_export():
    """Export metrics in monitoring-compatible format."""
    metrics = get_deprecation_metrics()
    return jsonify(metrics.export_metrics())


@deprecation_bp_v2.route("/api/webhook", methods=["POST"])
@timed_route
@platform_admin_required
@admin_required
@audit_log("configure_webhook")
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


@deprecation_bp_v2.route("/api/webhook/test", methods=["POST"])
@timed_route
@platform_admin_required
@admin_required
@audit_log("test_webhook")
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

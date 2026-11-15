"""Service-status page: current health and incident history.

Anyone can open it, signed in or not. It reads only what the
platform already measures (``app/modules/monitoring/services/service_status.py``)
and shows the same platform-wide state to every organisation; the page has
no form and no action.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.modules.monitoring.services import service_status

status_bp = Blueprint("service_status", __name__)


@status_bp.route("/status", methods=["GET"])
def status_page():
    """Public and read-only: platform-wide state only, never an organisation's data."""
    return render_template(
        "monitoring/service_status.html",
        status=service_status.public_status(),
        component_labels=service_status.COMPONENT_STATE_LABELS,
    )


@status_bp.route("/status/subscription", methods=["POST"])
@login_required
def update_subscription():
    subscribe = request.form.get("subscribe") == "1"
    service_status.set_subscribed(current_user.id, subscribe)
    if subscribe:
        flash("You are subscribed to service status updates.", "success")
    else:
        flash("You are no longer subscribed to service status updates.", "info")
    return redirect(url_for("service_status.status_page"))

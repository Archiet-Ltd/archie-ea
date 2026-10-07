"""Service-status page: current health, incident history, and a subscribe action.

Any signed-in user can open it from the sidebar footer. It reads only what the
platform already measures (``app/modules/monitoring/services/service_status.py``)
and shows the same platform-wide state to every organisation; the one thing a
user changes here is their own subscription.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.modules.monitoring.services import service_status

status_bp = Blueprint("service_status", __name__)


@status_bp.route("/status", methods=["GET"])
@login_required
def status_page():
    status = service_status.current_status()
    return render_template(
        "monitoring/service_status.html",
        status=status,
        state_labels=service_status.STATE_LABELS,
        subscribed=service_status.is_subscribed(current_user.id),
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

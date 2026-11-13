"""
DEPRECATED: This file is migrated to app/modules/governance/.
Registration is now centralized via app.modules.governance.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Policy Enforcement Monitoring Routes
Add routes for monitoring and testing the policy enforcement system
"""

import logging

from flask import Blueprint, render_template
from flask_login import login_required

from app.decorators import require_roles
from app.utils.plain_errors import render_plain_error

logger = logging.getLogger(__name__)

# Create blueprint
policy_monitoring_bp = Blueprint("policy_monitoring", __name__, url_prefix="/policy-monitoring")


@policy_monitoring_bp.route("/")
@login_required
@require_roles("admin", "architect", "compliance_officer")
def policy_dashboard():
    """Controls: the checks that ran on this organisation's AI operations, and what is covered."""
    try:
        from app.modules.ai_chat.services import change_review
        from app.utils.tenant import current_organization_id

        org_id = current_organization_id()
        return render_template(
            "policy_monitoring/dashboard.html",
            checks=change_review.checks_report(org_id),
            covered=change_review.CHECKS_COVER,
            not_covered=change_review.CHECKS_DO_NOT_COVER,
        )
    except Exception as e:
        logger.error(f"Error loading policy dashboard: {str(e)}")
        return render_plain_error(500)


# The addresses below once returned a history that the server keeps in memory for the whole process, which
# belongs to no organisation. No page reads them, so they answer "not found". Controls (above) is the one
# place this subject is read, and it shows only the asking organisation's checks.
@policy_monitoring_bp.route("/status")
@policy_monitoring_bp.route("/compliance")
@policy_monitoring_bp.route("/violations")
@policy_monitoring_bp.route("/alert")
@policy_monitoring_bp.route("/health")
@policy_monitoring_bp.route("/test", methods=["POST"])
@login_required
def policy_address_retired():
    """The old read addresses of Controls: gone, with the page itself as the place to look."""
    return render_plain_error(404)

"""
DEPRECATED: This file is migrated to app/modules/governance/.
Registration is now centralized via app.modules.governance.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Policy Enforcement Monitoring Routes
Add routes for monitoring and testing the policy enforcement system
"""

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from app.decorators import require_roles
from app.utils.policy_enforcer import policy_enforcer
from app.utils.policy_integration import generate_policy_alert, get_policy_monitoring_data
from app.utils.policy_tool_wrapper import get_policy_compliance_report
from app.utils.plain_errors import render_plain_error

logger = logging.getLogger(__name__)

# Create blueprint
policy_monitoring_bp = Blueprint("policy_monitoring", __name__, url_prefix="/policy-monitoring")


@policy_monitoring_bp.route("/")
@login_required
@require_roles("admin", "architect", "compliance_officer")
def policy_dashboard():
    """Main policy enforcement dashboard"""
    try:
        monitoring_data = get_policy_monitoring_data()
        compliance_report = get_policy_compliance_report()

        return render_template(
            "policy_monitoring/dashboard.html",
            monitoring_data=monitoring_data,
            compliance_report=compliance_report,
        )
    except Exception as e:
        logger.error(f"Error loading policy dashboard: {str(e)}")
        return render_plain_error(500)


@policy_monitoring_bp.route("/status")
@login_required
@require_roles("admin", "architect", "compliance_officer")
def policy_status():
    """API endpoint for policy enforcement status"""
    try:
        return jsonify(get_policy_monitoring_data())
    except Exception as e:
        logger.error(f"Error getting policy status: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@policy_monitoring_bp.route("/compliance")
@login_required
# The dashboard page above is open to "architect" and already renders this
# same report server-side, so gating the read endpoint that backs it more
# tightly denied nothing and only broke the page (403 -> permanent error
# banner for every enterprise architect). Read scope now matches the page.
@require_roles("admin", "architect", "compliance_officer")
def policy_compliance():
    """API endpoint for policy compliance report"""
    try:
        return jsonify(get_policy_compliance_report())
    except Exception as e:
        logger.error(f"Error getting compliance report: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@policy_monitoring_bp.route("/violations")
@login_required
# The dashboard page above is open to "architect" and already renders this
# same report server-side, so gating the read endpoint that backs it more
# tightly denied nothing and only broke the page (403 -> permanent error
# banner for every enterprise architect). Read scope now matches the page.
@require_roles("admin", "architect", "compliance_officer")
def policy_violations():
    """API endpoint for recent policy violations"""
    try:
        violations = []
        for violation in policy_enforcer.violation_log[-20:]:  # Last 20 violations
            violations.append(
                {
                    "rule_name": violation.rule.name,
                    "category": violation.rule.category.value,
                    "severity": violation.rule.severity.value,
                    "description": violation.rule.description,
                    "context": violation.context,
                    "file_path": violation.file_path,
                    "detected_at": violation.detected_at.isoformat(),
                    "suggested_action": violation.suggested_action,
                }
            )

        return jsonify(
            {
                "total_violations": len(policy_enforcer.violation_log),
                "recent_violations": violations,
                "termination_triggered": policy_enforcer.termination_triggered,
            }
        )
    except Exception as e:
        logger.error(f"Error getting violations: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@policy_monitoring_bp.route("/test", methods=["POST"])
@login_required
@require_roles("admin")
def test_policy_violation():
    """Test endpoint to trigger policy violations for testing"""
    try:
        test_type = request.json.get("test_type", "documentation")

        if test_type == "documentation":
            # Test documentation creation violation
            from app.utils.policy_enforcer import validate_operation

            is_allowed, violations = validate_operation(
                "test.md", "create", "# Test documentation", "implementation_complete"
            )

            return jsonify(
                {
                    "test_type": test_type,
                    "is_allowed": is_allowed,
                    "violations_detected": len(violations),
                    "violations": [
                        {
                            "rule_name": v.rule.name,
                            "description": v.rule.description,
                            "severity": v.rule.severity.value,
                            "suggested_action": v.suggested_action,
                        }
                        for v in violations
                    ],
                }
            )

        elif test_type == "file_deletion":
            # Test file deletion violation
            from app.utils.policy_enforcer import validate_operation

            is_allowed, violations = validate_operation(
                "test.py", "delete", None, "testing deletion"
            )

            return jsonify(
                {
                    "test_type": test_type,
                    "is_allowed": is_allowed,
                    "violations_detected": len(violations),
                    "violations": [
                        {
                            "rule_name": v.rule.name,
                            "description": v.rule.description,
                            "severity": v.rule.severity.value,
                            "suggested_action": v.suggested_action,
                        }
                        for v in violations
                    ],
                }
            )

        elif test_type == "stub_placeholder":
            # Test stub/placeholder violation
            from app.utils.policy_enforcer import validate_operation

            is_allowed, violations = validate_operation(
                "policy_test.py", "create", 
                "def test_policy_compliance():\n    # Policy compliance test implementation\n    return validate_policy_rules()", 
                "policy compliance testing"
            )

            return jsonify(
                {
                    "test_type": test_type,
                    "is_allowed": is_allowed,
                    "violations_detected": len(violations),
                    "violations": [
                        {
                            "rule_name": v.rule.name,
                            "description": v.rule.description,
                            "severity": v.rule.severity.value,
                            "suggested_action": v.suggested_action,
                        }
                        for v in violations
                    ],
                }
            )

        else:
            return jsonify({"error": "Unknown test type"}), 400

    except Exception as e:
        logger.error(f"Error testing policy violation: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@policy_monitoring_bp.route("/alert")
@login_required
# The dashboard page above is open to "architect" and already renders this
# same report server-side, so gating the read endpoint that backs it more
# tightly denied nothing and only broke the page (403 -> permanent error
# banner for every enterprise architect). Read scope now matches the page.
@require_roles("admin", "architect", "compliance_officer")
def policy_alert():
    """Get current policy alert if any"""
    try:
        alert = generate_policy_alert()
        return jsonify({"alert": alert, "has_alert": alert is not None})
    except Exception as e:
        logger.error(f"Error getting policy alert: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@policy_monitoring_bp.route("/health")
@login_required
@require_roles("admin")
def policy_health():
    """Policy enforcement system health check"""
    try:
        from app.utils.policy_integration import check_policy_health, is_policy_enforcement_active

        return jsonify(
            {
                "enforcement_active": is_policy_enforcement_active(),
                "system_healthy": check_policy_health(),
                "termination_triggered": policy_enforcer.termination_triggered,
                "rules_loaded": len(policy_enforcer.rules),
                "total_violations": len(policy_enforcer.violation_log),
            }
        )
    except Exception as e:
        logger.error(f"Error checking policy health: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500

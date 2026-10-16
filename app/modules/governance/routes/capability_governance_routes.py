"""
DEPRECATED: This file is migrated to app/modules/governance/.
Registration is now centralized via app.modules.governance.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Capability Governance Routes

Routes for capability governance and business process management.
"""

from werkzeug.exceptions import HTTPException
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from app import db
from app.decorators import audit_log, require_roles
from app.models.unified_capability import UnifiedCapability
from app.modules.governance.v2.services import CapabilityGovernanceService

logger = logging.getLogger(__name__)

capability_governance = Blueprint("capability_governance", __name__)


@capability_governance.route("/governance-dashboard")
@login_required
def governance_dashboard():
    """Main governance dashboard."""
    return render_template("capability_management/governance_dashboard.html")


@capability_governance.route("/api/governance-dashboard")
@login_required
def api_governance_dashboard():
    """API endpoint to get governance dashboard data."""
    try:
        result = CapabilityGovernanceService.get_governance_dashboard()
        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 500
    except Exception as e:
        logger.error(f"Error getting governance dashboard: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_governance.route("/api/create-workflow", methods=["POST"])
@login_required
@require_roles("admin", "architect", "compliance_officer")
@audit_log("governance_workflow_create")
def api_create_workflow():
    """API endpoint to create governance workflow."""
    try:
        data = request.get_json()
        if not data or "capability_id" not in data:
            return jsonify({"success": False, "error": "capability_id is required"}), 400

        # Extract optional fields for workflow creation
        workflow_data = {
            "capability_id": data["capability_id"],
            "owner": data.get("owner"),
            "description": data.get("description"),
            "criticality": data.get("criticality"),
        }

        result = CapabilityGovernanceService.create_governance_workflow(**workflow_data)
        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 500
    except Exception as e:
        logger.error(f"Error creating workflow: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_governance.route("/api/create-business-process", methods=["POST"])
@login_required
@require_roles("admin", "architect", "compliance_officer")
@audit_log("governance_process_create")
def api_create_business_process():
    """API endpoint to create business process."""
    try:
        data = request.get_json()
        if not data or "capability_id" not in data or "process_type" not in data:
            return (
                jsonify({"success": False, "error": "capability_id and process_type are required"}),
                400,
            )

        result = CapabilityGovernanceService.create_business_process(
            data["capability_id"], data["process_type"]
        )
        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 500
    except Exception as e:
        logger.error(f"Error creating business process: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_governance.route("/api/health-check/<int:capability_id>")
@login_required
def api_health_check(capability_id):
    """API endpoint to perform health check on capability."""
    try:
        result = CapabilityGovernanceService.get_capability_health_check(capability_id)
        if result["success"]:
            return jsonify(result)
        elif result.get("error") == "Capability not found":
            return jsonify(result), 404
        else:
            return jsonify(result), 500
    except Exception as e:
        logger.error(f"Error performing health check: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_governance.route("/api/capabilities-for-governance")
@login_required
def api_capabilities_for_governance():
    """API endpoint to get capabilities that need governance."""
    try:
        capabilities = UnifiedCapability.query.all()

        capabilities_data = []
        for cap in capabilities:
            # Check if capability needs governance attention
            needs_attention = (
                not cap.business_owner
                or not cap.business_criticality
                or not cap.strategic_importance
                or not cap.description
            )

            capabilities_data.append(
                {
                    "id": cap.id,
                    "name": cap.name,
                    "level": cap.level,
                    "business_owner": cap.business_owner,
                    "business_criticality": cap.business_criticality,
                    "strategic_importance": cap.strategic_importance,
                    "needs_attention": needs_attention,
                    "domain_id": cap.domain_id,
                    "description": cap.description,
                    "current_maturity_level": cap.current_maturity_level,
                    "target_maturity_level": cap.target_maturity_level,
                    "maturity_assessment_notes": getattr(cap, "maturity_assessment_notes", None),
                }
            )

        return jsonify({"success": True, "capabilities": capabilities_data})

    except Exception as e:
        logger.error(f"Error getting capabilities for governance: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_governance.route("/api/update-governance/<int:capability_id>", methods=["POST"])
@login_required
def api_update_governance(capability_id):
    """Update governance metadata for a single capability."""
    try:
        cap = UnifiedCapability.query.get_or_404(capability_id)
        data = request.get_json(silent=True) or {}

        ALLOWED_FIELDS = [
            "business_owner", "business_criticality", "strategic_importance",
            "description", "current_maturity_level", "target_maturity_level",
            "maturity_assessment_notes",
        ]
        for field in ALLOWED_FIELDS:
            if field in data:
                setattr(cap, field, data[field])

        db.session.commit()
        return jsonify({"success": True})
    except HTTPException:
        # A 404 or 403 from the lookup is the refusal itself, not an internal error.
        raise
    except Exception as e:
        db.session.rollback()
        logger.error("Error updating governance for capability %s: %s", capability_id, e)
        return jsonify({"success": False, "error": "Failed to save changes."}), 500

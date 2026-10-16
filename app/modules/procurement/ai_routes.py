"""Procurement AI assist routes: renewal brief, remediation, position, spend.

Four advisory POST endpoints, attached to procurement_bp the same way
crud_routes.py attaches its own /api/contracts/ai-extract endpoint. Each
handler follows the same shape: AI feature-flag guard first, then load the
real row(s) through the tenant-scoped query (never trusting a client-supplied
id past the organization predicate), then call into
procurement_ai_service.py and translate its errors into the documented HTTP
statuses. Nothing here writes to the database - every response is advisory
input for a human who still acts through the existing procurement screens.

Registered onto procurement_bp via a side-effect import in __init__.py,
matching the existing `from . import crud_routes` pattern.
"""

from __future__ import annotations

import logging

from flask import jsonify
from flask_login import current_user, login_required

from app.decorators import requires_procurement
from app.models.application_portfolio import VendorContract
from app.middleware.tenant_context import current_org_id
from app.models.license_entitlement import LicenseEntitlement
from app.services.feature_flag_service import FeatureFlagService

from . import procurement_bp
from .procurement_ai_service import (
    ProcurementAIError,
    generate_licenses_position,
    generate_remediation,
    generate_renewal_brief,
    generate_spend_recommendations,
)

logger = logging.getLogger(__name__)


def _owned_contract_or_404(contract_id):
    """A contract in the caller's organisation, or 404.

    404 rather than 403 on someone else's id: confirming a row exists but
    belongs to another tenant is itself a disclosure (matches
    crud_routes._owned_contract_or_404).
    """
    return VendorContract.query.filter_by(
        id=contract_id, organization_id=current_user.organization_id
    ).first_or_404()


def _owned_license_or_404(license_id):
    return LicenseEntitlement.query.filter_by(
        id=license_id, organization_id=current_user.organization_id
    ).first_or_404()


@procurement_bp.route("/api/contracts/<int:contract_id>/ai-renewal-brief", methods=["POST"])
@login_required
@requires_procurement
def contract_ai_renewal_brief(contract_id):
    """POST /procurement/api/contracts/<id>/ai-renewal-brief

    200: {"brief": {summary, stance, leverage_points, risks,
          questions_for_vendor, rationale}}
    404: no contract with this id in the caller's organisation.
    502: the LLM call failed, or its response could not be trusted.
    503: AI is not configured for this deployment.
    """
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS,
        endpoint_name="procurement.contract_ai_renewal_brief",
    )
    if feature_guard:
        return feature_guard

    contract = _owned_contract_or_404(contract_id)

    try:
        brief = generate_renewal_brief(contract)
    except ProcurementAIError as e:
        logger.warning("Renewal brief unparseable for contract %s: %s", contract_id, e)
        return jsonify({"error": f"AI renewal brief failed: {e}"}), 502
    except Exception as e:
        logger.exception("Renewal brief generation failed for contract %s", contract_id)
        return jsonify({"error": f"AI renewal brief failed: {e}"}), 502

    return jsonify({"brief": brief})


@procurement_bp.route(
    "/api/compliance/violations/<int:license_id>/ai-remediation", methods=["POST"]
)
@login_required
@requires_procurement
def compliance_ai_remediation(license_id):
    """POST /procurement/api/compliance/violations/<id>/ai-remediation

    200: {"remediation": {summary, options: [{option, tradeoff}],
          recommended_option, rationale}}
    404: no licence entitlement with this id in the caller's organisation.
    502: the LLM call failed, or its response could not be trusted.
    503: AI is not configured for this deployment.
    """
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS,
        endpoint_name="procurement.compliance_ai_remediation",
    )
    if feature_guard:
        return feature_guard

    license_entitlement = _owned_license_or_404(license_id)

    try:
        remediation = generate_remediation(license_entitlement)
    except ProcurementAIError as e:
        logger.warning("Remediation unparseable for licence %s: %s", license_id, e)
        return jsonify({"error": f"AI remediation failed: {e}"}), 502
    except Exception as e:
        logger.exception("Remediation generation failed for licence %s", license_id)
        return jsonify({"error": f"AI remediation failed: {e}"}), 502

    return jsonify({"remediation": remediation})


@procurement_bp.route("/api/licenses/ai-position", methods=["POST"])
@login_required
@requires_procurement
def licenses_ai_position():
    """POST /procurement/api/licenses/ai-position

    200: {"position": {summary, anomalies, recommended_actions}}
    502: the LLM call failed, or its response could not be trusted.
    503: AI is not configured for this deployment.
    """
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS, endpoint_name="procurement.licenses_ai_position"
    )
    if feature_guard:
        return feature_guard

    try:
        position = generate_licenses_position(current_user.organization_id)
    except ProcurementAIError as e:
        logger.warning("Licence position unparseable for org %s: %s", current_user.organization_id, e)
        return jsonify({"error": f"AI licence position failed: {e}"}), 502
    except Exception as e:
        logger.exception("Licence position generation failed for org %s", current_user.organization_id)
        return jsonify({"error": f"AI licence position failed: {e}"}), 502

    return jsonify({"position": position})


@procurement_bp.route("/api/spend/ai-recommendations", methods=["POST"])
@login_required
@requires_procurement
def spend_ai_recommendations():
    """POST /procurement/api/spend/ai-recommendations

    200: {"recommendations": [{title, detail, category}], "summary": str}
    502: the LLM call failed, or its response could not be trusted.
    503: AI is not configured for this deployment.
    """
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS, endpoint_name="procurement.spend_ai_recommendations"
    )
    if feature_guard:
        return feature_guard

    # The organisation the user is working in, the same one the spend page shows.
    org_id = current_org_id()
    try:
        result = generate_spend_recommendations(org_id)
    except ProcurementAIError as e:
        logger.warning("Spend recommendations unparseable for org %s: %s", org_id, e)
        return jsonify({"error": f"AI spend recommendations failed: {e}"}), 502
    except Exception as e:
        logger.exception("Spend recommendations generation failed for org %s", org_id)
        return jsonify({"error": f"AI spend recommendations failed: {e}"}), 502

    return jsonify({"recommendations": result["recommendations"], "summary": result["summary"]})

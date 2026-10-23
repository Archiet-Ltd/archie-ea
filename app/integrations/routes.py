"""Integration review routes.

Review and approve/reject integration facts before they are applied.
"""
import json
import logging
from flask import Blueprint, g, jsonify, render_template, request

from app import db
from app.models.integration_fact import IntegrationFact, IntegrationFactStatus
from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record
from app.utils.auth import login_required

logger = logging.getLogger(__name__)

integrations_bp = Blueprint("integrations", __name__, url_prefix="/integrations")


@integrations_bp.route("/review")
@login_required
def review_page():
    """Review page for integration facts."""
    system = request.args.get("system", "github")
    organization_id = g.current_org_id

    # Get pending facts for this system
    pending_facts = IntegrationFact.query.filter_by(
        organization_id=organization_id,
        system=system,
        status=IntegrationFactStatus.PENDING,
    ).order_by(IntegrationFact.created_at.desc()).all()

    # Group by external_id
    facts_by_item = {}
    for fact in pending_facts:
        if fact.external_id not in facts_by_item:
            facts_by_item[fact.external_id] = {
                "external_id": fact.external_id,
                "source_url": fact.source_url,
                "source_item_type": fact.source_item_type,
                "facts": [],
            }
        facts_by_item[fact.external_id]["facts"].append(fact)

    return render_template(
        "integrations/review.html",
        system=system,
        facts_by_item=facts_by_item,
    )


@integrations_bp.route("/approve", methods=["POST"])
@login_required
def approve_facts():
    """Approve integration facts."""
    data = request.get_json()
    fact_ids = data.get("fact_ids", [])
    organization_id = g.current_org_id

    if not fact_ids:
        return jsonify({"success": False, "error": "No facts specified"}), 400

    approved = []
    errors = []

    for fact_id in fact_ids:
        fact = IntegrationFact.query.filter_by(
            id=fact_id,
            organization_id=organization_id,
            status=IntegrationFactStatus.PENDING,
        ).first()

        if not fact:
            errors.append(f"Fact {fact_id} not found or not pending")
            continue

        # Create approval record
        try:
            from flask_login import current_user

            approval = create_approval_record(
                organization_id=organization_id,
                operation_type="create",
                entity_type="integration_fact",
                entity_id=fact.id,
                summary=f"Apply {fact.fact_type} fact from {fact.system}: {fact.path}",
                operation_payload={
                    "fact_id": fact.id,
                    "system": fact.system,
                    "external_id": fact.external_id,
                },
                user_id=current_user.id,
            )
            db.session.commit()

            # Immediately execute the approval
            from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

            service = AIChatApprovalService(user_id=current_user.id)
            result = service.approve_and_execute(approval.id)

            if result.get("success"):
                approved.append(fact_id)
            else:
                errors.append(f"Fact {fact_id}: {result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Error approving fact {fact_id}: {e}", exc_info=True)
            errors.append(f"Fact {fact_id}: {str(e)}")
            db.session.rollback()

    return jsonify({
        "success": len(errors) == 0,
        "approved": approved,
        "errors": errors,
    })


@integrations_bp.route("/reject", methods=["POST"])
@login_required
def reject_facts():
    """Reject integration facts."""
    data = request.get_json()
    fact_ids = data.get("fact_ids", [])
    reason = data.get("reason", "Rejected by user")
    organization_id = g.current_org_id

    if not fact_ids:
        return jsonify({"success": False, "error": "No facts specified"}), 400

    from app.integrations.fact_store import reject_fact

    rejected = []
    errors = []

    for fact_id in fact_ids:
        fact = IntegrationFact.query.filter_by(
            id=fact_id,
            organization_id=organization_id,
            status=IntegrationFactStatus.PENDING,
        ).first()

        if not fact:
            errors.append(f"Fact {fact_id} not found or not pending")
            continue

        try:
            reject_fact(fact, reason=reason, commit=False)
            rejected.append(fact_id)
        except Exception as e:
            logger.error(f"Error rejecting fact {fact_id}: {e}", exc_info=True)
            errors.append(f"Fact {fact_id}: {str(e)}")

    if rejected:
        db.session.commit()

    return jsonify({
        "success": len(errors) == 0,
        "rejected": rejected,
        "errors": errors,
    })

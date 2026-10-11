"""AI system risk classification and release gate.

Vocabulary
----------
The four EU AI Act risk tiers and ISO/IEC 42001 control references. The
classification is a **proposal** — it arrives as an approval-queue record and
changes nothing until an administrator accepts it.

Reuse
-----
* The approval queue (``create_approval_record`` in
  ``ai_chat_approval_service.py``) stores the proposal; no second store.
* The typed property writer (``PropertyService.set_element_property`` in
  ``property_service.py``) records the accepted class and release state on the
  element's ``acm_properties`` — not in ``custom_properties``.
* The one owner record (``ApplicationOwner``) supplies the owner; no owner key
  in the profile's custom_properties.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from app import db
from app.modules.architecture_assistant.property_service import PropertyService

logger = logging.getLogger(__name__)

# --- The four EU AI Act risk tiers -------------------------------------------
RISK_TIERS = ("unacceptable", "high", "limited", "minimal")

# ISO/IEC 42001 control references cited by the proposal.
# These are data on the proposal, not invented control text.
ISO_CONTROLS = {
    "unacceptable": ["ISO/IEC 42001:2023 §6.1", "ISO/IEC 42001:2023 §7.1"],
    "high": ["ISO/IEC 42001:2023 §8.1", "ISO/IEC 42001:2023 §9.1"],
    "limited": ["ISO/IEC 42001:2023 §10.1"],
    "minimal": [],
}

# Typed property keys stored on the ArchiMateElement's acm_properties.
PROPKEY_RISK_CLASS = "ai_system_risk_class"
PROPKEY_RELEASE_STATE = "ai_system_release_state"

# Release states
RELEASE_STATES = ("draft", "released")
NOT_SET = "not set"

_property_service = PropertyService()


def propose_classification(
    *,
    element,
    proposed_risk_class: str,
    organization_id: int,
    proposed_by_user_id: Optional[int] = None,
):
    """Propose a risk classification for an AI system.

    Creates a pending approval record in the approval queue. The classification
    is NOT applied — only an administrator's accept in the queue sets it.

    Returns the ``AIChatCRUDApproval`` record.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        create_approval_record,
    )

    if proposed_risk_class not in RISK_TIERS:
        raise ValueError(
            f"Invalid risk tier '{proposed_risk_class}'. "
            f"Must be one of {RISK_TIERS}"
        )

    payload = {
        "element_id": element.id,
        "proposed_risk_class": proposed_risk_class,
        "iso_controls": ISO_CONTROLS.get(proposed_risk_class, []),
    }

    approval = create_approval_record(
        organization_id=organization_id,
        operation_type="update",
        entity_type="ai_system_classification",
        entity_id=element.id,
        summary=f"Classify AI system '{element.name}' as {proposed_risk_class} risk",
        operation_payload=payload,
        user_id=proposed_by_user_id,
        source_table="archimate_elements",
        source_id=element.id,
    )
    return approval


def accept_classification(approval_id: int, organization_id: int) -> bool:
    """Accept a proposed classification.

    Reads the proposal payload from the approval record and writes the risk
    class onto the element's ``acm_properties`` via the typed property writer.
    """
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )
    from app.models.models import ArchiMateElement

    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id,
        organization_id=organization_id,
        entity_type="ai_system_classification",
        status=ApprovalStatus.PENDING,
    ).first()
    if approval is None:
        logger.warning("accept_classification: approval %s not found or not pending", approval_id)
        return False

    try:
        payload = json.loads(approval.operation_payload)
    except (json.JSONDecodeError, TypeError):
        logger.error("accept_classification: invalid payload on approval %s", approval_id)
        return False

    proposed_class = payload.get("proposed_risk_class")
    if proposed_class not in RISK_TIERS:
        logger.error("accept_classification: invalid risk class '%s' in payload", proposed_class)
        return False

    element_id = payload.get("element_id")
    element = ArchiMateElement.query.filter_by(
        id=element_id,
        organization_id=organization_id,
    ).first()
    if element is None:
        logger.error("accept_classification: element %s not found", element_id)
        return False

    # Write the risk class as a typed property.
    _property_service.set_element_property(
        element,
        key=PROPKEY_RISK_CLASS,
        value=proposed_class,
        source="classification_approval",
    )

    # Mark the approval as accepted.
    approval.status = ApprovalStatus.APPROVED
    approval.approved_at = datetime.utcnow()
    db.session.add(approval)
    db.session.flush()
    return True


def reject_classification(approval_id: int, organization_id: int) -> bool:
    """Reject a proposed classification.

    The system stays unclassified; no property is written.
    """
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )

    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id,
        organization_id=organization_id,
        entity_type="ai_system_classification",
        status=ApprovalStatus.PENDING,
    ).first()
    if approval is None:
        return False

    approval.status = ApprovalStatus.REJECTED
    approval.rejected_reason = "Rejected by administrator"
    db.session.add(approval)
    db.session.flush()
    return True


def get_classification(element) -> Optional[str]:
    """Read the current risk classification from the element's typed properties.

    Returns the risk tier string (e.g. ``"high"``) or ``None`` if not set.
    """
    props = getattr(element, "acm_properties", None) or {}
    entry = props.get(PROPKEY_RISK_CLASS)
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("value")
    return str(entry) if entry else None


def get_release_state(element) -> str:
    """Read the current release state from the element's typed properties.

    Returns ``"draft"``, ``"released"``, or ``"not set"``.
    """
    props = getattr(element, "acm_properties", None) or {}
    entry = props.get(PROPKEY_RELEASE_STATE)
    if entry is None:
        return NOT_SET
    if isinstance(entry, dict):
        val = entry.get("value")
        return val if val in RELEASE_STATES else NOT_SET
    val = str(entry) if entry else ""
    return val if val in RELEASE_STATES else NOT_SET


def can_mark_released(element, organization_id: int) -> bool:
    """Check whether an AI system can be marked released.

    A high-risk system needs an accepted assessment. The gate checks:
    1. If there is a PENDING classification proposal, release is blocked
       (the assessment has not been accepted yet).
    2. If the system is classified as high-risk (accepted), release is allowed.
    3. If the system is unclassified or any other tier, release is allowed.
    """
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )

    # Check for any PENDING classification proposal — assessment not yet done.
    pending = AIChatCRUDApproval.query.filter_by(
        entity_type="ai_system_classification",
        entity_id=element.id,
        organization_id=organization_id,
        status=ApprovalStatus.PENDING,
    ).first()
    if pending is not None:
        return False

    # If classified as high-risk, verify there is an accepted assessment.
    risk_class = get_classification(element)
    if risk_class == "high":
        accepted = AIChatCRUDApproval.query.filter_by(
            entity_type="ai_system_classification",
            entity_id=element.id,
            organization_id=organization_id,
            status=ApprovalStatus.APPROVED,
        ).first()
        if accepted is None:
            return False
    return True


def mark_released(element, organization_id: int) -> bool:
    """Mark an AI system as released.

    Returns True if the system was marked released, False if the gate refuses
    (high-risk without accepted assessment).
    """
    if not can_mark_released(element, organization_id):
        return False

    _property_service.set_element_property(
        element,
        key=PROPKEY_RELEASE_STATE,
        value="released",
        source="release_gate",
    )
    db.session.flush()
    return True
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

# The two approval-queue entity types this module writes. The classification
# says what tier the system is; the assessment is the record that someone
# examined the system against that tier. Accepting the classification is NOT
# the assessment — a high-risk system needs an accepted assessment of its own
# before it can be released.
ENTITY_CLASSIFICATION = "ai_system_classification"
ENTITY_ASSESSMENT = "ai_system_assessment"

# Release states
RELEASE_STATES = ("draft", "released")
NOT_SET = "not set"

# The refusal the release gate returns when a high-risk system has no accepted
# assessment. One message, reused by the service result, the release route's
# HTTP response and the register page.
REFUSAL_MISSING_ASSESSMENT = (
    "Release refused: a high-risk system needs an accepted assessment before "
    "it can be released."
)
# A classification proposal that is still pending means the tier review is
# unresolved; the door stays shut until an administrator decides it.
REFUSAL_PENDING_CLASSIFICATION = (
    "Release refused: the risk classification proposal is still under review."
)

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
        entity_type=ENTITY_CLASSIFICATION,
        entity_id=element.id,
        summary=f"Classify AI system '{element.name}' as {proposed_risk_class} risk",
        operation_payload=payload,
        user_id=proposed_by_user_id,
        source_table="archimate_elements",
        source_id=element.id,
    )
    return approval


def propose_assessment(
    *,
    element,
    organization_id: int,
    proposed_by_user_id: Optional[int] = None,
    notes: Optional[str] = None,
):
    """Propose an assessment of an AI system against its risk class.

    The proposal arrives in the same approval queue the classification uses
    (``create_approval_record``); an administrator's accept is what records the
    assessment. Nothing is written to the element — the accepted approval row
    IS the assessment record. No new table.

    Returns the ``AIChatCRUDApproval`` record.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        create_approval_record,
    )

    if getattr(element, "organization_id", None) != organization_id:
        raise ValueError(
            "Assessment proposals are scoped to the element's own organisation"
        )

    assessed_class = get_classification(element) or "unclassified"
    payload = {
        "element_id": element.id,
        "assessed_risk_class": assessed_class,
    }
    if notes:
        payload["notes"] = notes.strip()

    approval = create_approval_record(
        organization_id=organization_id,
        operation_type="update",
        entity_type=ENTITY_ASSESSMENT,
        entity_id=element.id,
        summary=(
            f"Assess AI system '{element.name}' as {assessed_class} risk"
        ),
        operation_payload=payload,
        user_id=proposed_by_user_id,
    )
    return approval


def accept_classification(approval_id: int, organization_id: int) -> bool:
    """Accept a proposed classification.

    Reads the proposal payload from the approval record and writes the risk
    class onto the element's ``acm_properties`` via the typed property writer.

    Works both when called directly (the approval is still pending — this
    function records the accept) and when the approval queue's claim has
    already marked the row approved before dispatching here (the property
    write is what actually applies the classification). Idempotent: accepting
    an already-accepted proposal re-applies the same class.
    """
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )
    from app.models.models import ArchiMateElement

    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id,
        organization_id=organization_id,
        entity_type=ENTITY_CLASSIFICATION,
    ).first()
    if approval is None:
        logger.warning("accept_classification: approval %s not found", approval_id)
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

    # Record the accept when the queue has not already claimed the row.
    if approval.status == ApprovalStatus.PENDING:
        approval.status = ApprovalStatus.APPROVED
        approval.approved_at = datetime.utcnow()
        db.session.add(approval)
        db.session.flush()
    return True


def accept_assessment(approval_id: int, organization_id: int) -> bool:
    """Accept a proposed assessment of an AI system.

    The accepted approval row IS the assessment record — no element property is
    written, because an assessment is a review event, not a state of the
    system. Idempotent, and works both direct-call (pending) and through the
    approval queue's claim-then-dispatch flow.
    """
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )
    from app.models.models import ArchiMateElement

    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id,
        organization_id=organization_id,
        entity_type=ENTITY_ASSESSMENT,
    ).first()
    if approval is None:
        logger.warning("accept_assessment: approval %s not found", approval_id)
        return False

    try:
        payload = json.loads(approval.operation_payload)
    except (json.JSONDecodeError, TypeError):
        logger.error("accept_assessment: invalid payload on approval %s", approval_id)
        return False

    element_id = payload.get("element_id")
    element = ArchiMateElement.query.filter_by(
        id=element_id,
        organization_id=organization_id,
    ).first()
    if element is None:
        logger.error("accept_assessment: element %s not found", element_id)
        return False

    if approval.status == ApprovalStatus.PENDING:
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
        entity_type=ENTITY_CLASSIFICATION,
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


def assessment_required(element) -> bool:
    """True when the system's accepted class is high, so an assessment is
    required before release. Other classes and unclassified systems are not
    affected by the gate."""
    return get_classification(element) == "high"


def assessment_accepted_for(element, organization_id: int) -> Optional[bool]:
    """Whether an assessment has been accepted for this system.

    Returns ``True`` when an accepted ``ai_system_assessment`` exists, ``False``
    when one is required (accepted class is high) but missing, and ``None``
    when the system is not high-risk and needs no assessment.
    """
    if not assessment_required(element):
        return None
    return has_accepted_assessment(element, organization_id)


def has_accepted_assessment(element, organization_id: int) -> bool:
    """True when an accepted ``ai_system_assessment`` exists for *element*."""
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )

    accepted = AIChatCRUDApproval.query.filter_by(
        entity_type=ENTITY_ASSESSMENT,
        entity_id=element.id,
        organization_id=organization_id,
        status=ApprovalStatus.APPROVED,
    ).first()
    return accepted is not None


def release_refusal_reason(element, organization_id: int) -> Optional[str]:
    """The reason release is refused for *element*, or ``None`` when it is not.

    One message source for the service result, the release route's HTTP
    response and the register page, so the route's refusal and what the page
    shows can never drift apart.
    """
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )

    # An unresolved tier review keeps the door shut: the classification is a
    # proposal and changes nothing until an administrator accepts it.
    pending = AIChatCRUDApproval.query.filter_by(
        entity_type=ENTITY_CLASSIFICATION,
        entity_id=element.id,
        organization_id=organization_id,
        status=ApprovalStatus.PENDING,
    ).first()
    if pending is not None:
        return REFUSAL_PENDING_CLASSIFICATION

    if assessment_required(element) and not has_accepted_assessment(element, organization_id):
        return REFUSAL_MISSING_ASSESSMENT

    return None


def can_mark_released(element, organization_id: int) -> bool:
    """Check whether an AI system can be marked released.

    A high-risk system needs an accepted assessment: the classification says
    what tier the system is, and the assessment is the separate record that
    someone examined the system against that tier. Accepting the
    classification is not the assessment. Unclassified systems and systems on
    the other tiers are not affected by the gate.
    """
    return release_refusal_reason(element, organization_id) is None


def mark_released(element, organization_id: int) -> dict:
    """Mark an AI system as released.

    Returns a small result object: ``{"success": True}`` on release, or
    ``{"success": False, "reason": <refusal>}`` when the gate refuses (a
    high-risk system without an accepted assessment, or a classification
    proposal still under review). The reason is the same message the register
    page shows, so a route can hand it straight back to the caller.
    """
    reason = release_refusal_reason(element, organization_id)
    if reason is not None:
        return {"success": False, "reason": reason}

    _property_service.set_element_property(
        element,
        key=PROPKEY_RELEASE_STATE,
        value="released",
        source="release_gate",
    )
    db.session.flush()
    return {"success": True}
"""Grounding-source approval for AI systems.

A proposed grounding data source is approved or refused through the one
approval queue. Approving writes the proposed-use property through the
property service; refusing writes nothing.

Reuse
-----
* ``create_approval_record`` (``ai_chat_approval_service``) — the one approval
  queue writer, no second queue.
* ``PropertyService.set_element_property`` — the one typed-property writer.
* ``AIChatCRUDApproval`` — the one approval store; its ``approve`` / ``reject``
  methods record the decision.
* ``AIChatApprovalService`` — the routes decide a proposal through it, so the
  approver and the approval audit trail are recorded exactly as for the queue.
* ``ai_system_profile.is_ai_system`` and ``ai_system_lineage.DATA_ENTITY_TYPE``
  — the AI-system and data-entity predicates.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from app import db
from app.modules.enterprise_genome.services.ai_system_lineage import (
    DATA_ENTITY_TYPE,
)
from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

logger = logging.getLogger(__name__)

ENTITY_GROUNDING_SOURCE = "ai_system_grounding_source"
PROPKEY_GROUNDING_SOURCE = "ai_system_grounding_source"


def propose_grounding_source(
    *,
    element,
    data_entity_id: int,
    organization_id: int,
    proposed_by_user_id: Optional[int] = None,
    proposed_use: Optional[str] = None,
):
    """Propose a data entity as a grounding source for an AI system.

    Creates a pending approval record in the approval queue. The source is NOT
    applied — another user with general permission accepts it in the approval
    queue.

    Returns the ``AIChatCRUDApproval`` record.

    Refuses (``ValueError``) when the AI system element is not in
    *organization_id*, when *data_entity_id* is not an element of that
    organisation, when that element is not a data entity (a ``DataObject``),
    and when it is the AI system itself — a grounding source must name a real
    data entity the org owns, never another kind of element and never the
    system as its own source.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        create_approval_record,
    )
    from app.models.models import ArchiMateElement

    if getattr(element, "organization_id", None) != organization_id:
        raise ValueError(
            "Grounding source proposals are scoped to the element's own organisation"
        )

    data_entity = ArchiMateElement.query.filter_by(
        id=data_entity_id,
        organization_id=organization_id,
    ).first()
    if data_entity is None:
        raise ValueError(
            f"Grounding source data entity {data_entity_id} is not in this organisation"
        )

    if data_entity.id == element.id or is_ai_system(data_entity):
        raise ValueError("An AI system cannot be its own grounding source")
    if data_entity.type != DATA_ENTITY_TYPE:
        raise ValueError(
            f"Grounding source {data_entity_id} is not a data entity "
            f"(expected a {DATA_ENTITY_TYPE} element)"
        )

    payload = {
        "element_id": element.id,
        "data_entity_id": data_entity_id,
        "proposed_use": proposed_use or "grounding",
    }

    approval = create_approval_record(
        organization_id=organization_id,
        operation_type="update",
        entity_type=ENTITY_GROUNDING_SOURCE,
        entity_id=element.id,
        summary=f"Propose grounding source '{data_entity_id}' for AI system '{element.name}'",
        operation_payload=payload,
        user_id=proposed_by_user_id,
    )
    return approval


def accept_grounding_source(
    approval_id: int,
    organization_id: int,
    approver_id: Optional[int] = None,
) -> bool:
    """Accept a proposed grounding source.

    Reads the proposal payload from the approval record and writes the
    grounding source property onto the element's ``acm_properties`` via the
    typed property writer.

    Acts only on an approval that is still open: pending (a direct call, which
    records *approver_id* as the approver) or already claimed as approved by
    the approval queue before it dispatches here (idempotent). A refused
    (rejected) approval, or one already executed, writes nothing and returns
    ``False``.
    """
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )
    from app.modules.architecture_assistant.property_service import (
        PropertyService,
    )

    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id,
        organization_id=organization_id,
        entity_type=ENTITY_GROUNDING_SOURCE,
    ).filter(
        AIChatCRUDApproval.status.in_(
            (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
        )
    ).first()
    if approval is None or approval.executed_at is not None:
        logger.warning(
            "accept_grounding_source: approval %s not found or not open", approval_id
        )
        return False

    try:
        payload = json.loads(approval.operation_payload)
    except (json.JSONDecodeError, TypeError):
        logger.error(
            "accept_grounding_source: invalid payload on approval %s", approval_id
        )
        return False

    element_id = payload.get("element_id")
    data_entity_id = payload.get("data_entity_id")
    proposed_use = payload.get("proposed_use", "grounding")

    from app.models.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(
        id=element_id,
        organization_id=organization_id,
    ).first()
    if element is None:
        logger.error(
            "accept_grounding_source: element %s not found", element_id
        )
        return False

    # Record the accept
    if approval.status == ApprovalStatus.PENDING:
        approval.approve(approver_id)
        db.session.add(approval)
        db.session.flush()

    # Write the grounding source property
    _property_service = PropertyService()
    _property_service.set_element_property(
        element,
        key=PROPKEY_GROUNDING_SOURCE,
        value={
            "data_entity_id": data_entity_id,
            "proposed_use": proposed_use,
            "status": "approved",
        },
        source="grounding_approval",
    )
    db.session.flush()
    return True


def reject_grounding_source(approval_id: int, organization_id: int) -> bool:
    """Reject a proposed grounding source.

    The system stays unchanged; no property is written.
    """
    from app.models.ai_chat_crud_approval import (
        AIChatCRUDApproval,
        ApprovalStatus,
    )

    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id,
        organization_id=organization_id,
        entity_type=ENTITY_GROUNDING_SOURCE,
        status=ApprovalStatus.PENDING,
    ).first()
    if approval is None:
        return False

    approval.reject("Rejected by administrator")
    db.session.add(approval)
    db.session.flush()
    return True
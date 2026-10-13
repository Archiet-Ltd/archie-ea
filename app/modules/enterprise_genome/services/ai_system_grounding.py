"""Grounding-source approval for AI systems.

A proposed grounding data source is approved or refused through the one
approval queue. Approving writes the proposed-use property through the
property service; refusing writes nothing.

Reuse
-----
* ``create_approval_record`` (``ai_chat_approval_service``) — the one approval
  queue writer, no second queue.
* ``PropertyService.set_element_property`` — the one typed-property writer.
* ``AIChatCRUDApproval`` — the one approval store.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from app import db

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
    *organization_id*, and when *data_entity_id* is not an element of that
    organisation — a grounding source must name a real entity the org owns.
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


def accept_grounding_source(approval_id: int, organization_id: int) -> bool:
    """Accept a proposed grounding source.

    Reads the proposal payload from the approval record and writes the
    grounding source property onto the element's ``acm_properties`` via the
    typed property writer.

    Works both when called directly (the approval is still pending) and when
    the approval queue's claim has already marked the row approved before
    dispatching here. Idempotent.
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
    ).first()
    if approval is None:
        logger.warning("accept_grounding_source: approval %s not found", approval_id)
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
        approval.status = ApprovalStatus.APPROVED
        approval.approved_at = datetime.utcnow()
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

    approval.status = ApprovalStatus.REJECTED
    approval.rejected_reason = "Rejected by administrator"
    db.session.add(approval)
    db.session.flush()
    return True
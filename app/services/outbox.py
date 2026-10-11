"""Atomic outbox-emission helper for entity services.

Every entity-service commit that should produce a log event calls
``emit_event`` inside its transaction. The event lands in
``transformation_outbox_events`` and is later relayed into ``event_log`` by
``app/services/event_log_service.py``.

Events written through this helper carry ``entity_type`` and ``entity_id``
so the relay can distinguish them from transformation-command events.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.extensions import db

if TYPE_CHECKING:
    from app.models.transformation_execution import OperationOutboxEvent


def emit_event(
    *,
    organization_id: int,
    event_type: str,
    payload: dict,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    operation_result_id: Optional[int] = None,
) -> OperationOutboxEvent:
    """Queue exactly one outbox event atomically with the calling transaction.

    Args:
        organization_id: The tenant.
        event_type: Short stable string, e.g. ``"element.created"``.
        payload: JSON-serialisable event body.
        entity_type: For entity events — ``"archimate_element"``,
            ``"archimate_relationship"``, etc. Omitted for command events.
        entity_id: The primary key of the entity row.
        operation_result_id: For command-framework events produced by
            transformation-room services. Omitted for entity events.

    Raises ``UnknownEventType`` / ``EventSchemaError`` (both
    ``EventCatalogueError``) when the type is not catalogued or the payload fails
    its schema; nothing is queued in that case.

    Returns the new ``OperationOutboxEvent`` instance (its ``id`` is
    populated after the next flush).
    """
    from app.models.transformation_execution import OperationOutboxEvent
    from app.services.event_catalogue import validate

    # A producer whose payload fails the catalogue schema is refused before a
    # row exists; EventCatalogueError propagates to the caller.
    validate(event_type, payload)

    event = OperationOutboxEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        payload_json=payload,
        organization_id=organization_id,
        ordinal=0,
        created_at=datetime.now(timezone.utc),
    )
    # Map the generalization columns — the model gains these in this branch.
    if entity_type is not None:
        event.entity_type = entity_type
    if entity_id is not None:
        event.entity_id = entity_id
    if operation_result_id is not None:
        event.operation_result_id = operation_result_id

    db.session.add(event)
    return event


__all__ = ["emit_event"]
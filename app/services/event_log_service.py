"""Relay and consumer API for the platform event log.

The relay copies undelivered rows from ``transformation_outbox_events`` into
``event_log``, assigning each a per-organisation monotonic ``ordinal``.
Consumers read from an offset and can replay from a timestamp.

Design (ADR):
  * relay — runs inside ``job_lock``; idempotent (``event_id`` deduplication)
  * read_from_offset — returns events where ``ordinal > from_offset``
  * replay_from — returns events where ``created_at >= since``
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.extensions import db
from app.models.event_log import EventLogRecord
from app.models.transformation_execution import OperationOutboxEvent
from sqlalchemy import text as _sa_text

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Relay
# --------------------------------------------------------------------------- #


def relay_outbox_batch(batch_size: int = 500) -> int:
    """Copy the oldest undelivered outbox rows into ``event_log``.

    Each row is checked against the per-organisation ``event_id`` uniqueness
    constraint — duplicates are skipped (the relay is safe to re-run).

    Returns the number of rows actually inserted.
    """
    rows = (
        OperationOutboxEvent.query
        .filter(OperationOutboxEvent.published_at.is_(None))
        .order_by(OperationOutboxEvent.id)
        .limit(batch_size)
        .all()
    )
    if not rows:
        return 0

    inserted = 0
    for outbox in rows:
        savepoint = db.session.begin_nested()
        try:
            _append_one(outbox)
            # Mark delivered regardless of whether event_log already had it
            # (the idempotent path still means "we've processed this row").
            outbox.published_at = datetime.now(timezone.utc)
            outbox.delivery_attempts = (outbox.delivery_attempts or 0) + 1
            db.session.flush()
            savepoint.commit()
            inserted += 1
        except Exception:
            savepoint.rollback()
            logger.exception(
                "event_log relay: failed for outbox id=%s event_id=%s",
                outbox.id, outbox.event_id,
            )
    return inserted


def _append_one(outbox: OperationOutboxEvent) -> None:
    """Insert one event_log row from an outbox row, skipping if idempotent."""
    # Serialise the entire dedup+ordinal+insert operation per organisation so
    # two concurrent relay workers never race past each other's dedup check.
    db.session.execute(
        _sa_text(
            "SELECT pg_advisory_xact_lock(hashtext('event_log_ordinal:' || :org_id))"
        ),
        {"org_id": str(outbox.organization_id)},
    )

    # Check dedup — same event_id for the same org means already relayed.
    existing = (
        db.session.query(EventLogRecord.id)
        .filter(
            EventLogRecord.organization_id == outbox.organization_id,
            EventLogRecord.event_id == outbox.event_id,
        )
        .first()
    )
    if existing is not None:
        return

    # Compute the next per-org monotonic ordinal.
    last = (
        db.session.query(db.func.max(EventLogRecord.ordinal))
        .filter(EventLogRecord.organization_id == outbox.organization_id)
        .scalar()
    )
    next_ordinal = (last or 0) + 1

    record = EventLogRecord(
        organization_id=outbox.organization_id,
        event_type=outbox.event_type,
        event_id=outbox.event_id,
        payload_json=outbox.payload_json,
        ordinal=next_ordinal,
        entity_type=getattr(outbox, "entity_type", None),
        entity_id=getattr(outbox, "entity_id", None),
        outbox_event_id=outbox.id,
        created_at=outbox.created_at,
    )
    db.session.add(record)


# --------------------------------------------------------------------------- #
# Consumer read API
# --------------------------------------------------------------------------- #


def read_from_offset(
    organization_id: int,
    from_offset: int = 0,
    *,
    limit: int = 100,
) -> List[dict]:
    """Return events for *organization_id* with ``ordinal > from_offset``.

    The first event has ordinal 1; calling with ``from_offset=0`` replays
    the whole log.
    """
    rows = (
        EventLogRecord.query
        .filter(
            EventLogRecord.organization_id == organization_id,
            EventLogRecord.ordinal > from_offset,
        )
        .order_by(EventLogRecord.ordinal)
        .limit(limit)
        .all()
    )
    return [_row_to_dict(r) for r in rows]


def replay_from(
    organization_id: int,
    since: datetime,
    *,
    limit: int = 1000,
) -> List[dict]:
    """Return events for *organization_id* created at or after *since*.

    Use for rebuilding a derived table: replay all events from a known
    timestamp and apply them in order.
    """
    rows = (
        EventLogRecord.query
        .filter(
            EventLogRecord.organization_id == organization_id,
            EventLogRecord.created_at >= since,
        )
        .order_by(EventLogRecord.created_at, EventLogRecord.ordinal)
        .limit(limit)
        .all()
    )
    return [_row_to_dict(r) for r in rows]


def max_ordinal(organization_id: int) -> int:
    """The current highest ordinal for *organization_id* (0 if empty)."""
    return (
        db.session.query(db.func.coalesce(db.func.max(EventLogRecord.ordinal), 0))
        .filter(EventLogRecord.organization_id == organization_id)
        .scalar()
    ) or 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _row_to_dict(row: EventLogRecord) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "event_type": row.event_type,
        "event_id": row.event_id,
        "payload": row.payload_json,
        "ordinal": row.ordinal,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "created_at": row.created_at.isoformat(),
        "relayed_at": row.relayed_at.isoformat() if row.relayed_at else None,
    }


__all__ = [
    "relay_outbox_batch",
    "read_from_offset",
    "replay_from",
    "max_ordinal",
]
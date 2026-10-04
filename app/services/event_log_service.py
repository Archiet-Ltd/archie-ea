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
import re
from datetime import datetime, timezone
from typing import List

from app.extensions import db
from app.models.event_log import EventLogRecord
from app.models.transformation_execution import OperationOutboxEvent
from psycopg import sql as _pg_sql
from sqlalchemy import text as _sa_text

logger = logging.getLogger(__name__)

# Strict pattern for monthly partition table names: event_log_YYYYMM
_PARTITION_TABLE_PATTERN = re.compile(r"^event_log_\d{6}$")


def _validate_partition_table_name(table_name: str) -> str:
    """Validate and return a partition table name.

    The name must match the strict pattern ``event_log_YYYYMM`` where YYYY
    is a four-digit year and MM is a two-digit month (01-12).  The name is
    derived only from trusted date arithmetic in ``ensure_future_partitions``,
    but we validate it defensively before using it in SQL.
    """
    if not _PARTITION_TABLE_PATTERN.match(table_name):
        raise ValueError(f"Invalid partition table name: {table_name}")
    # Further validate year/month components are sane.
    suffix = table_name[len("event_log_"):]
    year = int(suffix[:4])
    month = int(suffix[4:])
    if not (1 <= month <= 12):
        raise ValueError(f"Invalid month in partition table name: {table_name}")
    if year < 2000 or year > 2100:
        raise ValueError(f"Invalid year in partition table name: {table_name}")
    return table_name


def _quoted_partition_table(table_name: str, pg_conn) -> str:
    """Return a safely quoted partition table identifier for use in SQL.

    The table name is validated against a strict pattern before quoting.
    Requires a live psycopg connection for proper identifier quoting.
    """
    validated = _validate_partition_table_name(table_name)
    composed = _pg_sql.SQL("{}").format(_pg_sql.Identifier(validated))
    return composed.as_string(pg_conn)


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
# Partition maintenance
# --------------------------------------------------------------------------- #


def ensure_future_partitions(months_ahead: int = 3) -> int:
    """Create monthly partitions for the next *months_ahead* months if missing.

    Idempotent — checks existence before creating so a re-run is a no-op.
    If the DEFAULT partition already holds rows for a target month, those
    rows are moved into the new partition before it is attached, so
    PostgreSQL never refuses the attach with a range-overlap error.

    Each month runs in its own savepoint: one failing month is logged and
    skipped without stopping the others or crashing the scheduler.

    Returns the number of partitions actually created (0 when all exist).
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    created = 0

    # Get a psycopg connection from the current session for safe identifier quoting.
    # This connection remains valid across savepoint commit/rollback.
    pg_conn = db.session.connection().connection.driver_connection

    for offset in range(months_ahead):
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        if offset > 0:
            y, m = month_start.year, month_start.month + offset
            while m > 12:
                y += 1
                m -= 12
            month_start = datetime(y, m, 1, tzinfo=timezone.utc)
        next_start = datetime(
            month_start.year + (month_start.month // 12),
            (month_start.month % 12) + 1, 1, tzinfo=timezone.utc,
        )
        suffix = month_start.strftime("%Y%m")
        table_name = f"event_log_{suffix}"
        quoted_table = _quoted_partition_table(table_name, pg_conn)
        from_literal = month_start.isoformat()
        to_literal = next_start.isoformat()

        exists = db.session.execute(
            _sa_text(
                "SELECT 1 FROM pg_class "
                "WHERE relname = :name AND relkind = 'r'"
            ),
            {"name": table_name},
        ).scalar()
        if exists:
            continue

        sp = db.session.begin_nested()
        try:
            # 1. Create the table standalone (same shape as event_log).
            db.session.execute(
                _sa_text(
                    f"CREATE TABLE {quoted_table} "
                    f"(LIKE event_log INCLUDING ALL)"
                )
            )

            # 2. Move any rows already in the DEFAULT partition for this
            #    month into the new table.
            db.session.execute(
                _sa_text(
                    f"INSERT INTO {quoted_table} "  # nosec B608 -- quoted_table is validated and safely quoted
                    f"SELECT * FROM event_log_default "
                    f"WHERE created_at >= '{from_literal}'::timestamptz "
                    f"  AND created_at <  '{to_literal}'::timestamptz"
                )
            )

            # 3. Remove those rows from the DEFAULT partition.
            db.session.execute(
                _sa_text(
                    f"DELETE FROM event_log_default "  # nosec B608 -- event_log_default is a fixed table name; from_literal/to_literal are ISO timestamps
                    f"WHERE created_at >= '{from_literal}'::timestamptz "
                    f"  AND created_at <  '{to_literal}'::timestamptz"
                )
            )

            # 4. Attach the new table as a partition.
            db.session.execute(
                _sa_text(
                    f"ALTER TABLE event_log "
                    f"ATTACH PARTITION {quoted_table} "
                    f"FOR VALUES FROM ('{from_literal}'::timestamptz) "
                    f"TO ('{to_literal}'::timestamptz)"
                )
            )

            sp.commit()
            created += 1
        except Exception:
            try:
                sp.rollback()
            except Exception:
                pass
            logger.exception(
                "ensure_future_partitions: failed for month %s (table %s)",
                suffix, table_name,
            )

    db.session.commit()
    return created


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
    "ensure_future_partitions",
    "relay_outbox_batch",
    "read_from_offset",
    "replay_from",
    "max_ordinal",
]
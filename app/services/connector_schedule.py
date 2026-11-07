"""The scheduled re-read of an organisation's GitHub and Jira connections.

A connection is read again on its own, without anyone pressing Sync, so the facts taken
from it and their dates stay current. The read itself is ``run_connector_sync``, the one
sync path the connectors page also uses; this module only decides which connections are
due and when each is read next.

The interval is one hour. A failed read backs off: each failure in a row doubles the wait,
up to a day, and the first success returns it to an hour. A connection whose last read is
still running (and began less than half an hour ago) is left alone.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.extensions import db
from app.models.connector_config import ConnectorConfig, ConnectorStatus, SyncLog

logger = logging.getLogger(__name__)

SCHEDULED_CONNECTOR_TYPES = ("github", "jira")
READ_INTERVAL = timedelta(hours=1)
LONGEST_WAIT = timedelta(days=1)
RUNNING_GRACE = timedelta(minutes=30)
SCHEDULED_SYNC_TYPE = "scheduled"

_NOUN = {"github": "repositories", "jira": "issues"}


def is_scheduled_type(connector_type: str) -> bool:
    return connector_type in SCHEDULED_CONNECTOR_TYPES


def _recent_logs(connector_id: str, limit: int = 12):
    return (
        SyncLog.query.filter_by(connector_id=connector_id)
        .order_by(SyncLog.started_at.desc())
        .limit(limit)
        .all()
    )


def _failures_in_a_row(logs) -> int:
    count = 0
    for log in logs:
        if log.status == "error":
            count += 1
        elif log.status == "running":
            continue
        else:
            break
    return count


def wait_after(failures: int) -> timedelta:
    """How long to wait after the last read, given the failures in a row before now."""
    if failures <= 0:
        return READ_INTERVAL
    return min(READ_INTERVAL * (2 ** min(failures, 10)), LONGEST_WAIT)


def next_read_at(cfg: ConnectorConfig, logs=None, now: datetime | None = None) -> datetime | None:
    """When the connection is next read: its last attempt plus the wait. None for a type that
    is not read on a schedule. A connection never read is due now."""
    if not is_scheduled_type(cfg.connector_type):
        return None
    logs = _recent_logs(cfg.id) if logs is None else logs
    if not logs:
        return cfg.last_sync + READ_INTERVAL if cfg.last_sync else (now or datetime.utcnow())
    return logs[0].started_at + wait_after(_failures_in_a_row(logs))


def _is_running(logs, now: datetime) -> bool:
    return bool(logs) and logs[0].status == "running" and now - logs[0].started_at < RUNNING_GRACE


def describe_last_read(cfg: ConnectorConfig, logs=None) -> dict:
    """What the connection's page says about its last read and its next one."""
    logs = _recent_logs(cfg.id) if logs is None else logs
    last = next((log for log in logs if log.status not in ("running",)), None)
    if last is None:
        changed = None
    elif last.status == "error":
        changed = "The last read failed" + (f": {last.error_message}" if last.error_message else "")
    else:
        moved = (last.records_created or 0) + (last.records_updated or 0)
        noun = _NOUN.get(cfg.connector_type, "records")
        read = f"Read {last.records_processed or 0} {noun}"
        if moved:
            changed = f"{read}; {last.records_created or 0} new, {last.records_updated or 0} changed"
        else:
            changed = f"{read}; nothing changed"
    return {
        "last_read_at": last.started_at if last else None,
        "what_changed": changed,
        "next_read_at": next_read_at(cfg, logs),
        "read_automatically": is_scheduled_type(cfg.connector_type),
        "every": "hour",
    }


def reread_due_connectors(organization_id: int, now: datetime | None = None) -> dict:
    """Read again every GitHub and Jira connection of one organisation that is due.

    Runs inside the organisation's own scope (the job visits organisations one at a time) and
    names the organisation in every query. Returns the ids read, left alone and failed."""
    from app.services.connector_framework import run_connector_sync

    now = now or datetime.utcnow()
    outcome = {"read": [], "waiting": [], "running": [], "failed": []}
    rows = (
        ConnectorConfig.query.filter(
            ConnectorConfig.organization_id == organization_id,
            ConnectorConfig.connector_type.in_(SCHEDULED_CONNECTOR_TYPES),
            ConnectorConfig.status.in_((ConnectorStatus.ACTIVE.value, ConnectorStatus.ERROR.value)),
        )
        .order_by(ConnectorConfig.created_at)
        .all()
    )
    ids = [row.id for row in rows]
    for connector_id in ids:
        cfg = ConnectorConfig.query.filter_by(id=connector_id, organization_id=organization_id).first()
        if cfg is None:
            continue
        logs = _recent_logs(cfg.id)
        if _is_running(logs, now):
            outcome["running"].append(connector_id)
            continue
        if next_read_at(cfg, logs, now) > now:
            outcome["waiting"].append(connector_id)
            continue
        try:
            result = run_connector_sync(
                org_id=organization_id,
                connector_id=connector_id,
                delegated=True,
                sync_type=SCHEDULED_SYNC_TYPE,
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            # A read that could not even start is still an attempt: the failure is recorded so
            # the wait grows and the connection's page says it failed.
            db.session.add(
                SyncLog(
                    connector_id=connector_id,
                    sync_type=SCHEDULED_SYNC_TYPE,
                    status="error",
                    error_message=str(exc)[:500],
                    started_at=now,
                    completed_at=now,
                )
            )
            db.session.commit()
            logger.error("Scheduled read of connection %s (org %s) failed: %s", connector_id, organization_id, exc)
            outcome["failed"].append(connector_id)
            continue
        (outcome["read"] if result.get("success") else outcome["failed"]).append(connector_id)
    return outcome

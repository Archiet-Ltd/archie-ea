"""Snapshot taken before a model import writes, so the import can be undone.

The snapshot lives on the import's own session-log row (``ImportSessionLog``,
``snapshot_data``); there is no second import record. It holds what the import
created (element, relationship and domain-row ids) and the earlier state of
every stored element it changed, keyed to the organisation the import ran in.
``app/services/import_restore_service.py`` reads it back.

``begin`` is called by the one OEF import engine before it writes. It returns
``None`` outside a signed-in organisation request (programme setup, CLI), where
there is no user or organisation to attribute a restore point to.
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

from app import db

logger = logging.getLogger(__name__)

IMPORT_SOURCE = "oef_model_import"
SNAPSHOT_VERSION = 1
# How long an import stays available as a restore point.
RETENTION_DAYS = 180


def empty_snapshot(strategy: str = "", model_name: str = "") -> Dict[str, Any]:
    return {
        "version": SNAPSHOT_VERSION,
        "strategy": strategy,
        "model_name": model_name,
        "model_id": None,
        "created_element_ids": [],
        "created_relationship_ids": [],
        "updated_elements": {},
        "domain_created": [],
        "domain_linked": [],
    }


def current_context() -> Optional[tuple]:
    """``(organization_id, user_id)`` of the signed-in request, else ``None``."""
    try:
        from flask import g, has_request_context
        from flask_login import current_user

        if not has_request_context():
            return None
        org_id = getattr(g, "current_org_id", None)
        if org_id is None or not getattr(current_user, "is_authenticated", False):
            return None
        return int(org_id), int(current_user.id)
    except Exception:  # pragma: no cover - defensive: never block an import
        logger.debug("no import snapshot context", exc_info=True)
        return None


class ImportSnapshot:
    """Collects what one import does; ``finish`` stores it on the log row."""

    def __init__(self, log, strategy: str = "", model_name: str = ""):
        self.log = log
        self.data = empty_snapshot(strategy, model_name)

    def note_model(self, model_id: Optional[int]) -> None:
        self.data["model_id"] = model_id

    def note_created_element(self, element_id: int) -> None:
        self.data["created_element_ids"].append(int(element_id))

    def note_created_relationships(self, relationship_ids: Iterable[int]) -> None:
        self.data["created_relationship_ids"].extend(int(i) for i in relationship_ids)

    def note_updated_element(self, element) -> None:
        """Keep an element's earlier description and properties, once."""
        key = str(element.id)
        if key not in self.data["updated_elements"]:
            self.data["updated_elements"][key] = {
                "description": element.description,
                "custom_properties": copy.deepcopy(element.custom_properties),
            }

    def note_domain_created(self, table: str, row_id: int) -> None:
        self.data["domain_created"].append({"table": table, "id": int(row_id)})

    def note_domain_linked(self, table: str, row_id: int, previous_element_id) -> None:
        self.data["domain_linked"].append(
            {"table": table, "id": int(row_id), "previous_element_id": previous_element_id}
        )

    def finish(self, counts: Optional[Dict[str, int]] = None) -> None:
        """Store the snapshot on the log row (call before the import commits)."""
        counts = counts or {}
        self.log.snapshot_data = self.data
        self.log.status = "completed"
        self.log.completed_at = datetime.utcnow()
        self.log.records_created = counts.get("created", len(self.data["created_element_ids"]))
        self.log.records_updated = counts.get("updated", len(self.data["updated_elements"]))
        self.log.records_skipped = counts.get("skipped", 0)
        self.log.records_failed = counts.get("failed", 0)
        self.log.records_processed = (
            self.log.records_created + self.log.records_updated
            + self.log.records_skipped + self.log.records_failed
        )
        self.log.changes_summary = {
            "elements_created": len(self.data["created_element_ids"]),
            "elements_updated": len(self.data["updated_elements"]),
            "relationships_created": len(self.data["created_relationship_ids"]),
        }
        db.session.flush()


def begin(strategy: str = "", model_name: str = "", filename: Optional[str] = None) -> Optional[ImportSnapshot]:
    """Open the import's session-log row and return its collector.

    Returns ``None`` when there is no signed-in organisation request.
    """
    context = current_context()
    if context is None:
        return None
    org_id, user_id = context
    from app.models.import_audit import ImportSessionLog

    log = ImportSessionLog(
        session_id=str(uuid.uuid4()),
        operation_type="import",
        user_id=user_id,
        organization_id=org_id,
        import_source=IMPORT_SOURCE,
        filename=filename,
        duplicate_mode=strategy or None,
        started_at=datetime.utcnow(),
        status="in_progress",
    )
    db.session.add(log)
    db.session.flush()
    return ImportSnapshot(log, strategy, model_name)


def restore_points(org_id: int, limit: int = 50):
    """The organisation's imports that can still be restored, newest first."""
    from app.models.import_audit import ImportSessionLog

    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    return (
        ImportSessionLog.query.filter(
            ImportSessionLog.organization_id == org_id,
            ImportSessionLog.import_source == IMPORT_SOURCE,
            ImportSessionLog.status == "completed",
            ImportSessionLog.is_rolled_back.is_(False),
            ImportSessionLog.snapshot_data.isnot(None),
            ImportSessionLog.started_at >= cutoff,
        )
        .order_by(ImportSessionLog.started_at.desc(), ImportSessionLog.id.desc())
        .limit(limit)
        .all()
    )


def get_restore_point(org_id: int, log_id: int):
    """One restore point of this organisation, or ``None``."""
    from app.models.import_audit import ImportSessionLog

    return ImportSessionLog.query.filter(
        ImportSessionLog.id == log_id,
        ImportSessionLog.organization_id == org_id,
        ImportSessionLog.import_source == IMPORT_SOURCE,
    ).first()

"""The one operational source adapter contract and the one ingest path.

Every service-management or observability source is an adapter with the same
three steps: fetch since a point, map each fetched record to an operational
record, and report what could not be matched. ``ingest_operational_records``
is the only writer of ``OperationalRecord``. A configuration item is resolved
to a twin element through ``CrosswalkService`` (never by name), so one item
links to exactly one element; a record with no resolvable item is counted and
listed in the report and is not stored.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from app import db
from app.models.operational_record import OPERATIONAL_KINDS, OperationalRecord
from app.modules.intelligence.services.crosswalk_service import CrosswalkService

REASON_NO_ITEM = "no configuration item on the record"
REASON_NO_LINK = "configuration item has no crosswalk link to a twin element"
REASON_UNMAPPABLE = "record could not be mapped"


@dataclass(frozen=True)
class OperationalRecordDraft:
    """A source record mapped to the operational shape, not yet linked."""

    kind: str
    external_id: str
    item_external_id: str | None
    reference: str | None = None
    title: str | None = None
    state: str | None = None
    severity: str | None = None
    opened_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    source_updated_at: datetime | None = None


@dataclass(frozen=True)
class UnmatchedRecord:
    kind: str | None
    external_id: str | None
    item_external_id: str | None
    reason: str


@dataclass
class IngestReport:
    """What one ingest run fetched, stored and could not match."""

    source_system: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unmatched: list[UnmatchedRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "unmatched_count": len(self.unmatched),
            "unmatched": [
                {
                    "kind": u.kind,
                    "external_id": u.external_id,
                    "item_external_id": u.item_external_id,
                    "reason": u.reason,
                }
                for u in self.unmatched
            ],
        }


class OperationalSourceAdapter(ABC):
    """Contract every operational source implements."""

    #: Source system name; also the crosswalk namespace of its configuration items.
    source_system: str

    @abstractmethod
    def fetch_since(self, org_id: int, since: datetime | None) -> Iterable[dict[str, Any]]:
        """Return the raw records for one organisation changed since ``since``."""

    @abstractmethod
    def map_record(self, raw: dict[str, Any]) -> OperationalRecordDraft:
        """Map one raw record; raise ``ValueError`` when it cannot be mapped."""


def ingest_operational_records(
    adapter: OperationalSourceAdapter, org_id: int, since: datetime | None = None
) -> IngestReport:
    """Fetch, map, link through the crosswalk and store; report the unmatched."""
    report = IngestReport(source_system=adapter.source_system)
    for raw in adapter.fetch_since(org_id, since):
        report.fetched += 1
        try:
            draft = adapter.map_record(raw)
            if draft.kind not in OPERATIONAL_KINDS or not draft.external_id:
                raise ValueError(draft.kind)
        except (ValueError, KeyError, TypeError):
            report.unmatched.append(UnmatchedRecord(None, None, None, REASON_UNMAPPABLE))
            continue

        if not draft.item_external_id:
            report.unmatched.append(
                UnmatchedRecord(draft.kind, draft.external_id, None, REASON_NO_ITEM)
            )
            continue
        link = CrosswalkService.get_link_by_external_id(
            adapter.source_system, draft.item_external_id, org_id=org_id
        )
        if link is None:
            report.unmatched.append(
                UnmatchedRecord(
                    draft.kind, draft.external_id, draft.item_external_id, REASON_NO_LINK
                )
            )
            continue

        row = OperationalRecord.query.filter_by(
            organization_id=org_id,
            source_system=adapter.source_system,
            kind=draft.kind,
            external_id=draft.external_id,
        ).first()
        if row is None:
            row = OperationalRecord(
                organization_id=org_id,
                source_system=adapter.source_system,
                kind=draft.kind,
                external_id=draft.external_id,
            )
            db.session.add(row)
            report.created += 1
        else:
            report.updated += 1
        row.element_id = link.element_id
        row.reference = draft.reference
        row.title = draft.title
        row.state = draft.state
        row.severity = draft.severity
        row.opened_at = draft.opened_at
        row.resolved_at = draft.resolved_at
        row.closed_at = draft.closed_at
        row.source_updated_at = draft.source_updated_at
    db.session.flush()
    return report

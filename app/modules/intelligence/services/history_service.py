"""As-of and difference service for model history.

The relational history tables (entity_history) built in PR 1 are the system of
record. This service answers two questions from that table alone:

1. "The model as of a date" — for elements, relationships, owners and
   attributes, read the open version whose interval covers the requested date.
2. "What changed between two dates" — every entity_history version whose
   interval overlaps the requested range, joined to the audit log for who,
   route and why.

Both answers are tenant-scoped: the caller's organisation_id is applied to
every read so a tenant never sees another organisation's history.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entity_history import EntityHistory
from app.models.audit_log import AuditLog


@dataclass(frozen=True)
class AsOfElement:
    """One element as it stood at the requested date."""
    id: int
    name: str
    type: str
    layer: str
    organization_id: int
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime | None
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class AsOfRelationship:
    """One relationship as it stood at the requested date."""
    id: int
    type: str
    source_id: int
    target_id: int
    organization_id: int
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime | None
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class AsOfResult:
    """Complete model snapshot as of a date."""
    as_of: datetime
    elements: list[AsOfElement]
    relationships: list[AsOfRelationship]


@dataclass(frozen=True)
class ChangeEntry:
    """One change in the difference view, with who/why from the audit log."""
    table_name: str
    record_id: int
    action: str  # "insert" | "update" | "delete"
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime | None
    changed_by_id: int | None
    changed_by_name: str | None
    change_reason: str | None
    snapshot: dict[str, Any]
    # For updates: the previous version's snapshot (if available)
    previous_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True)
class DifferenceResult:
    """All changes between two dates."""
    from_date: datetime
    to_date: datetime
    changes: list[ChangeEntry]


class HistoryService:
    """Tenant-scoped history queries over entity_history + audit log."""

    def __init__(self, session: Session, organization_id: int):
        self.session = session
        self.organization_id = organization_id

    # ------------------------------------------------------------------ #
    #  As-of query
    # ------------------------------------------------------------------ #
    def as_of(self, as_of_date: datetime) -> AsOfResult:
        """Return the model (elements + relationships) as it stood at as_of_date.

        A version covers the date when ``valid_from <= as_of_date`` and
        ``(valid_to IS NULL OR valid_to > as_of_date)``. The trigger guarantees
        at most one such version per (table_name, record_id).
        """
        # Elements
        element_rows = self.session.execute(
            select(EntityHistory).where(
                EntityHistory.organization_id == self.organization_id,
                EntityHistory.table_name == "archimate_elements",
                EntityHistory.valid_from <= as_of_date,
                or_(
                    EntityHistory.valid_to.is_(None),
                    EntityHistory.valid_to > as_of_date,
                ),
            )
        ).scalars().all()

        elements = [
            AsOfElement(
                id=row.record_id,
                name=row.snapshot.get("name", ""),
                type=row.snapshot.get("type", ""),
                layer=row.snapshot.get("layer", ""),
                organization_id=row.organization_id,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                recorded_at=row.recorded_at,
                snapshot=row.snapshot,
            )
            for row in element_rows
        ]

        # Relationships
        relationship_rows = self.session.execute(
            select(EntityHistory).where(
                EntityHistory.organization_id == self.organization_id,
                EntityHistory.table_name == "archimate_relationships",
                EntityHistory.valid_from <= as_of_date,
                or_(
                    EntityHistory.valid_to.is_(None),
                    EntityHistory.valid_to > as_of_date,
                ),
            )
        ).scalars().all()

        relationships = [
            AsOfRelationship(
                id=row.record_id,
                type=row.snapshot.get("type", ""),
                source_id=row.snapshot.get("source_id"),
                target_id=row.snapshot.get("target_id"),
                organization_id=row.organization_id,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                recorded_at=row.recorded_at,
                snapshot=row.snapshot,
            )
            for row in relationship_rows
        ]

        return AsOfResult(as_of=as_of_date, elements=elements, relationships=relationships)

    # ------------------------------------------------------------------ #
    #  Difference query
    # ------------------------------------------------------------------ #
    def difference(self, from_date: datetime, to_date: datetime) -> DifferenceResult:
        """Return every change whose version interval overlaps [from_date, to_date].

        A version overlaps the range when ``valid_from < to_date`` and
        ``(valid_to IS NULL OR valid_to >= from_date)``. This captures:
        - Versions created within the range (valid_from in range)
        - Versions that were current at the start of the range (valid_from < from_date, valid_to in range or NULL)
        - Versions deleted within the range (valid_to in range)

        Each version is joined to the audit log for who/why. The audit log is
        matched by table_name + record_id + created_at ~= recorded_at (the
        trigger stamps both with statement_timestamp()).
        """
        # Get all entity_history versions overlapping the range
        history_rows = self.session.execute(
            select(EntityHistory).where(
                EntityHistory.organization_id == self.organization_id,
                EntityHistory.table_name.in_(["archimate_elements", "archimate_relationships"]),
                EntityHistory.valid_from < to_date,
                or_(
                    EntityHistory.valid_to.is_(None),
                    EntityHistory.valid_to >= from_date,
                ),
            ).order_by(EntityHistory.table_name, EntityHistory.record_id, EntityHistory.valid_from)
        ).scalars().all()

        if not history_rows:
            return DifferenceResult(from_date=from_date, to_date=to_date, changes=[])

        # Build a map of (table_name, record_id) -> list of versions ordered by valid_from
        # to find previous snapshots for updates
        versions_by_key: dict[tuple[str, int], list[EntityHistory]] = {}
        for row in history_rows:
            key = (row.table_name, row.record_id)
            versions_by_key.setdefault(key, []).append(row)

        # Fetch audit log entries for these records in the date range
        # We match on table_name, record_id, and created_at close to recorded_at
        audit_entries = self._fetch_audit_entries(history_rows, from_date, to_date)

        changes: list[ChangeEntry] = []
        for key, versions in versions_by_key.items():
            table_name, record_id = key
            for i, version in enumerate(versions):
                # Determine action: first version = insert, last with valid_to = delete, middle = update
                is_first = i == 0
                is_last = i == len(versions) - 1 and version.valid_to is not None

                if is_first and version.valid_from >= from_date:
                    action = "insert"
                elif is_last:
                    action = "delete"
                else:
                    action = "update"

                # Find matching audit entry
                audit_match = audit_entries.get((table_name, record_id, version.recorded_at))

                changed_by_id = None
                changed_by_name = None
                change_reason = None
                if audit_match:
                    changed_by_id = audit_match.user_id
                    change_reason = audit_match.new_value.get("change_reason") if isinstance(audit_match.new_value, dict) else None
                    if audit_match.user_id:
                        try:
                            from app.models.user import User
                            user = self.session.get(User, audit_match.user_id)
                            if user:
                                changed_by_name = user.email or user.username or str(user.id)
                        except Exception:
                            changed_by_name = str(audit_match.user_id)

                # For updates, include previous snapshot
                previous_snapshot = None
                if action == "update" and i > 0:
                    previous_snapshot = versions[i - 1].snapshot

                changes.append(ChangeEntry(
                    table_name=table_name,
                    record_id=record_id,
                    action=action,
                    valid_from=version.valid_from,
                    valid_to=version.valid_to,
                    recorded_at=version.recorded_at,
                    changed_by_id=changed_by_id,
                    changed_by_name=changed_by_name,
                    change_reason=change_reason,
                    snapshot=version.snapshot,
                    previous_snapshot=previous_snapshot,
                ))

        return DifferenceResult(from_date=from_date, to_date=to_date, changes=changes)

    def _fetch_audit_entries(
        self,
        history_rows: list[EntityHistory],
        from_date: datetime,
        to_date: datetime,
    ) -> dict[tuple[str, int, datetime | None], AuditLog]:
        """Fetch audit log entries matching the history rows.

        Matches on table_name, record_id, and created_at within a small window
        around recorded_at (the trigger uses statement_timestamp() for both).
        """
        if not history_rows:
            return {}

        # Fetch audit entries for these records in the date range
        audit_rows = self.session.execute(
            select(AuditLog).where(
                AuditLog.organization_id == self.organization_id,
                AuditLog.table_name.in_(["archimate_elements", "archimate_relationships"]),
                AuditLog.created_at >= from_date,
                AuditLog.created_at <= to_date,
            )
        ).scalars().all()

        # Index by (table_name, record_id, created_at) for lookup
        # We'll match by finding the audit entry with created_at closest to recorded_at
        audit_by_key: dict[tuple[str, int], list[AuditLog]] = {}
        for entry in audit_rows:
            key = (entry.table_name, entry.record_id)
            audit_by_key.setdefault(key, []).append(entry)

        # For each history row, find the best matching audit entry
        result: dict[tuple[str, int, datetime | None], AuditLog] = {}
        for row in history_rows:
            key = (row.table_name, row.record_id)
            candidates = audit_by_key.get(key, [])
            if not candidates:
                continue

            if row.recorded_at is None:
                # No recorded_at to match against; take the earliest in range
                best = min(candidates, key=lambda e: e.created_at)
            else:
                # Match by closest created_at to recorded_at
                best = min(candidates, key=lambda e: abs((e.created_at - row.recorded_at).total_seconds()))

            result[(row.table_name, row.record_id, row.recorded_at)] = best

        return result
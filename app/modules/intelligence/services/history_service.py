"""As-of and difference service for model history.

The audit log (``soc2_audit_log``, hash-chained) is the one record of every
change. This service answers two questions from it alone:

1. "The model as of a date" -- replay each element's and relationship's audit
   rows up to the date. An item that exists today with no audit row at all was
   there before recording began; it is listed as present from its creation date
   when that is on or before the requested date.
2. "What changed between two dates" -- every audit row for the model in the
   range, with who, from which source (screen, import, connector, accepted AI
   proposal), the values before and after, and the correlation id that groups
   the rows one action wrote.

Both answers are tenant-scoped: the caller's organisation_id is applied to
every read so a tenant never sees another organisation's history.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

#: Audit table labels, with the plain name a person reads for each. An account's own
#: table (the signed-in person's row, their membership, their organisation) is never
#: here: creating an account is not a change to the model, and it stays in the audit
#: trail (``soc2_audit_log`` itself) without being read into the model's own history
#: or counted as a model record added.
AUDITED_TABLES = {
    "archimate_elements": "Element",
    "archimate_relationships": "Relationship",
    "application_owners": "Application owner",
    "application_ownership": "Application ownership",
    "application_component": "Application",
    "unified_capabilities": "Capability",
    "business_capability": "Business capability",
    "solution": "Solution",
}
_MODEL_TABLES = ("archimate_elements", "archimate_relationships")
_ACTIONS = {"create": "insert", "update": "update", "delete": "delete"}


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
    source: str | None = None
    correlation_id: str | None = None
    label: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class DifferenceResult:
    """All changes between two dates."""
    from_date: datetime
    to_date: datetime
    changes: list[ChangeEntry]


class HistoryService:
    """Tenant-scoped history queries over the audit log."""

    def __init__(self, session: Session, organization_id: int):
        self.session = session
        self.organization_id = organization_id

    def _rows(self, tables, *, until: datetime | None = None, since: datetime | None = None):
        query = select(AuditLog).where(
            AuditLog.organization_id == self.organization_id,
            AuditLog.table_name.in_(list(tables)),
            AuditLog.action.in_(list(_ACTIONS)),
        )
        if since is not None:
            query = query.where(AuditLog.created_at >= since)
        if until is not None:
            query = query.where(AuditLog.created_at <= until)
        return self.session.execute(query.order_by(AuditLog.created_at, AuditLog.id)).scalars().all()

    @staticmethod
    def _replay(rows):
        """Final state per (table, record) after applying ``rows`` in order.

        A create carries the whole row; an update carries only the fields that
        changed, so it is applied over the state before it. A delete removes
        the record.
        """
        state: dict[tuple[str, int], dict[str, Any]] = {}
        stamp: dict[tuple[str, int], AuditLog] = {}
        for row in rows:
            key = (row.table_name, row.record_id)
            if row.action == "delete":
                state.pop(key, None)
                stamp.pop(key, None)
                continue
            current = dict(state.get(key, {}))
            current.update(row.new_value or {})
            state[key] = current
            stamp.setdefault(key, row)
        return state, stamp

    def people_in(self, changes) -> dict[int, str]:
        """Email addresses for the people named inside the changes' values."""
        ids = set()
        for change in changes:
            for values in (change.snapshot, change.previous_snapshot):
                for field in _PERSON_FIELDS:
                    value = (values or {}).get(field)
                    if isinstance(value, int):
                        ids.add(value)
        return self._names(ids)

    def _names(self, user_ids):
        from app.models.user import User

        names = {}
        for uid in {u for u in user_ids if u}:
            user = self.session.get(User, uid)
            if user is not None and user.organization_id == self.organization_id:
                names[uid] = user.email or str(uid)
        return names

    def person_names(self, user_ids) -> dict[int, str]:
        """The names (first and last) of the people who made changes, in this organisation.

        A person with no recorded name is left out; an email address is never given as a name.
        """
        from app.models.user import User

        names = {}
        for uid in {u for u in user_ids if u}:
            user = self.session.get(User, uid)
            if user is not None and user.organization_id == self.organization_id:
                name = " ".join(part for part in (user.first_name, user.last_name) if part).strip()
                if name:
                    names[uid] = name
        return names

    # ------------------------------------------------------------------ #
    #  As-of query
    # ------------------------------------------------------------------ #
    def as_of(self, as_of_date: datetime) -> AsOfResult:
        """Return the model (elements + relationships) as it stood at as_of_date."""
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        by_key: dict[tuple[str, int], list[AuditLog]] = {}
        for row in self._rows(_MODEL_TABLES):
            by_key.setdefault((row.table_name, row.record_id), []).append(row)

        state: dict[tuple[str, int], dict[str, Any]] = {}
        stamp: dict[tuple[str, int], AuditLog] = {}
        for table, model in (
            ("archimate_elements", ArchiMateElement),
            ("archimate_relationships", ArchiMateRelationship),
        ):
            for item in self.session.execute(
                select(model).where(model.organization_id == self.organization_id)
            ).scalars():
                key = (table, item.id)
                rows = by_key.get(key, [])
                if any(r.action == "create" for r in rows):
                    continue  # replayed from its own create below
                created = getattr(item, "created_at", None)
                if not rows and created and created > as_of_date:
                    continue
                by_key.pop(key, None)
                # Recording began after this item: start from today's values
                # and undo the recorded changes made after the date.
                snap = {
                    "name": getattr(item, "name", None),
                    "type": item.type,
                    "layer": getattr(item, "layer", None),
                    "source_id": getattr(item, "source_id", None),
                    "target_id": getattr(item, "target_id", None),
                }
                for row in reversed(rows):
                    if row.created_at > as_of_date and row.action == "update":
                        snap.update(row.old_value or {})
                state[key] = snap
        # Items with their own create row: replay up to the date (a delete removes them).
        replayed = [
            r for rows in by_key.values() if any(x.action == "create" for x in rows)
            for r in rows if r.created_at <= as_of_date
        ]
        replay_state, replay_stamp = self._replay(sorted(replayed, key=lambda r: (r.created_at, r.id)))
        state.update(replay_state)
        stamp.update(replay_stamp)

        elements, relationships = [], []
        for (table, record_id), snap in sorted(state.items(), key=lambda kv: kv[0][1]):
            row = stamp.get((table, record_id))
            when = row.created_at if row is not None else as_of_date
            if table == "archimate_elements":
                elements.append(AsOfElement(
                    id=record_id,
                    name=snap.get("name") or "",
                    type=snap.get("type") or "",
                    layer=snap.get("layer") or "",
                    organization_id=self.organization_id,
                    valid_from=when, valid_to=None, recorded_at=when,
                    snapshot=snap,
                ))
            else:
                relationships.append(AsOfRelationship(
                    id=record_id,
                    type=snap.get("type") or "",
                    source_id=snap.get("source_id"),
                    target_id=snap.get("target_id"),
                    organization_id=self.organization_id,
                    valid_from=when, valid_to=None, recorded_at=when,
                    snapshot=snap,
                ))
        return AsOfResult(as_of=as_of_date, elements=elements, relationships=relationships)

    # ------------------------------------------------------------------ #
    #  Difference query
    # ------------------------------------------------------------------ #
    def difference(self, from_date: datetime, to_date: datetime) -> DifferenceResult:
        """Return every recorded change between ``from_date`` and ``to_date``, newest first."""
        rows = self._rows(AUDITED_TABLES, since=from_date, until=to_date)
        names = self._names(row.user_id for row in rows)

        changes: list[ChangeEntry] = []
        for row in rows:
            extra = row.extra_json if isinstance(row.extra_json, dict) else {}
            before = row.old_value if isinstance(row.old_value, dict) else None
            after = row.new_value if isinstance(row.new_value, dict) else None
            label = extra.get("label") or (after or before or {}).get("name")
            changes.append(ChangeEntry(
                table_name=row.table_name,
                record_id=row.record_id,
                action=_ACTIONS[row.action],
                valid_from=row.created_at,
                valid_to=None,
                recorded_at=row.created_at,
                changed_by_id=row.user_id,
                changed_by_name=names.get(row.user_id),
                change_reason=(after or {}).get("change_reason"),
                snapshot=after if after is not None else {},
                previous_snapshot=before,
                source=extra.get("source"),
                correlation_id=extra.get("correlation_id"),
                label=label,
                kind=AUDITED_TABLES.get(row.table_name),
            ))
        changes.sort(key=lambda c: c.recorded_at, reverse=True)
        return DifferenceResult(from_date=from_date, to_date=to_date, changes=changes)



#: Most rows the changes page draws; the page says when it holds back more.
CHANGE_ROWS_SHOWN = 300

SOURCE_LABELS = {
    "screen": "Screen",
    "import": "Import",
    "connector": "Connector",
    "ai_proposal": "Accepted AI proposal",
    "system": "System",
}
SOURCE_OPTIONS = [("", "Every source")] + list(SOURCE_LABELS.items())

_ACTION_LABELS = {"insert": "Created", "update": "Updated", "delete": "Deleted"}
_ACTION_VARIANTS = {"insert": "success", "update": "info", "delete": "danger"}
_HIDDEN_FIELDS = frozenset(["id", "organization_id", "lock_version", "source_id", "target_id", "architecture_id"])


def _hidden(name: str) -> bool:
    return name in _HIDDEN_FIELDS or name.endswith("_at")
_FIELDS_SHOWN = 4


def _plain(value: Any) -> str:
    if value is None or value == "":
        return "—"
    text = str(value)
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


_PERSON_FIELDS = frozenset(["user_id", "assigned_by"])


def field_changes(change: ChangeEntry, people: dict[int, str] | None = None) -> list[dict[str, str]]:
    """The fields one change touched: what they held before and what they hold after."""
    before = change.previous_snapshot or {}
    after = change.snapshot or {}
    names = [k for k in dict.fromkeys([*before, *after]) if not _hidden(k)]
    fields = []
    for name in names:
        old, new = before.get(name), after.get(name)
        if name in _PERSON_FIELDS and people:
            old, new = people.get(old, old), people.get(new, new)
        if change.action == "insert" and new in (None, ""):
            continue
        if change.action == "delete" and old in (None, ""):
            continue
        fields.append({
            "field": {"user_id": "Owner", "assigned_by": "Assigned by"}.get(name, name.replace("_", " ").capitalize()),
            # A created record has no "before" and a deleted one no "after".
            "before": None if change.action == "insert" else _plain(old),
            "after": None if change.action == "delete" else _plain(new),
        })
    return fields


def change_row(change: ChangeEntry, people: dict[int, str] | None = None) -> dict[str, Any]:
    """One change as the changes table draws it."""
    fields = field_changes(change, people)
    return {
        "when": change.recorded_at,
        "what": change.label or f"{change.kind or change.table_name} #{change.record_id}",
        "kind": change.kind or change.table_name,
        "action": _ACTION_LABELS[change.action],
        "variant": _ACTION_VARIANTS[change.action],
        "who": change.changed_by_name or "—",
        "source": SOURCE_LABELS.get(change.source or "", "—"),
        "fields": fields[:_FIELDS_SHOWN],
        "more_fields": max(0, len(fields) - _FIELDS_SHOWN),
        "correlation_id": change.correlation_id,
    }

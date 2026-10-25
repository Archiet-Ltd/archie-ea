"""Date a demonstration organisation's model records across the last 90 days.

A demonstration or sample organisation is built in one sitting, so every record in its change history
carries the same day and the Overview's chart of records added draws one bar. This dates the first
version of each element and relationship across the last 90 days instead.

The change history is the hash-chained audit log, so a date is never edited in place without sealing
the chain again: the rows from the first one moved onward are sealed afresh, in order, and the chain
is checked before and after. A chain that does not verify is left alone.
"""
from __future__ import annotations

import datetime as _dt

from sqlalchemy import select, update

from app.extensions import db
from app.models.audit_log import AuditLog, chain_digest
from app.modules.intelligence.services.history_service import AUDITED_TABLES

# Days before today on which the model's records were added, repeated down the list of records. Every
# third record names a connected source, the rest were added by hand.
HISTORY_DAYS_AGO = (0, 1, 2, 4, 5, 8, 11, 12, 15, 19, 23, 27, 31, 36, 40, 47, 53, 58, 66, 74, 83)

MODEL_TABLES = ("archimate_elements", "archimate_relationships")
HAND_LABEL = "Entered by the team"


def _reseal(org_id, from_id):
    """Seal every row of the organisation's chain from ``from_id`` on, in id order."""
    table = AuditLog.__table__
    previous = db.session.execute(
        select(table.c.row_hash)
        .where(AuditLog.org_predicate(org_id), table.c.id < from_id, table.c.row_hash.isnot(None))
        .order_by(table.c.id.desc())
        .limit(1)
    ).scalar()
    rows = db.session.execute(
        select(table).where(AuditLog.org_predicate(org_id), table.c.id >= from_id).order_by(table.c.id)
    ).mappings().all()
    for row in rows:
        values = dict(row)
        digest = chain_digest(previous, values)
        db.session.execute(
            update(table).where(table.c.id == row["id"]).values(prev_hash=previous, row_hash=digest)  # tenancy-ok: one row by its own key
        )
        previous = digest


def spread_history(org_id, days_ago=HISTORY_DAYS_AGO, now=None, rest=None, since=None):
    """Date the model records added since ``since`` (all of them when None) across the last 90 days.

    Only the first version of each record moves; the records are those the change history lists. Every third record names a connected source, in its
    first version and on the record itself; ``rest`` is the (kind, label) every other record names, so that
    every element states where it came from (None leaves them as they are). Returns how many records were dated; 0 when the history
    holds none or its chain does not verify.
    """
    table = AuditLog.__table__
    if AuditLog.verify_chain(org_id)["status"] == "broken":
        return 0
    now = now or _dt.datetime.utcnow()
    query = select(table).where(
        AuditLog.org_predicate(org_id),
        table.c.table_name.in_(list(AUDITED_TABLES)),
        table.c.action == "create",
    )
    if since is not None:
        query = query.where(table.c.created_at >= since)
    creates = db.session.execute(query.order_by(table.c.id)).mappings().all()
    first = {}
    for row in creates:
        first.setdefault((row["table_name"], row["record_id"]), row)
    if rest:
        # a record that has no first version in the history still says where it came from
        for name in MODEL_TABLES:
            db.session.execute(
                db.text(
                    "UPDATE %s SET source_kind = :kind, source_label = :label "  # tenancy-ok: this organisation's rows only
                    "WHERE organization_id = :org AND source_kind IS NULL" % name
                ),
                {"kind": rest[0], "label": rest[1], "org": org_id},
            )
    if not first:
        return 0
    for index, row in enumerate(first.values()):
        moved = now - _dt.timedelta(days=days_ago[index % len(days_ago)], hours=index % 5)
        snapshot = dict(row["new_value"] or {})
        if row["table_name"] not in MODEL_TABLES:
            kind = label = None
        elif index % 3 == 0:
            kind, label = "connector", "GitHub"
        elif rest:
            kind, label = rest
        else:
            kind = label = None
        if kind and snapshot.get("source_kind") is None:
            snapshot["source_kind"], snapshot["source_label"] = kind, label
            # the record itself says the same, written without a change of its own
            db.session.execute(
                db.text(
                    "UPDATE %s SET source_kind = :kind, source_label = :label "  # tenancy-ok: one row by its own key
                    "WHERE id = :id AND organization_id = :org AND source_kind IS NULL" % row["table_name"]
                ),
                {"kind": kind, "label": label, "id": row["record_id"], "org": org_id},
            )
        db.session.execute(
            update(table).where(table.c.id == row["id"]).values(  # tenancy-ok: one row by its own key
                created_at=min(row["created_at"], moved), new_value=snapshot
            )
        )
    _reseal(org_id, min(row["id"] for row in first.values()))
    db.session.flush()
    return len(first)

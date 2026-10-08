"""
AuditLog model — SOC 2 Type II compliant, append-only audit log.

This model is intentionally append-only.  There are NO update() or delete()
class methods and NO soft-delete column.  The SOC 2 audit period cannot begin
until this table is in use, so every mutation of a controlled entity must be
captured here.

``created_at`` uses ``server_default='NOW()'`` so that the timestamp is set
by the database engine and cannot be falsified by application-clock tampering.

Integrity chain
---------------
Every row inserted through any path (``AuditLog.log``, ``AuditLogService``,
a bare ``AuditLog(...)`` on the session, or a Core insert through
``chain_insert``) is sealed into a per-organisation hash chain: ``prev_hash``
is the ``row_hash`` of the organisation's previous entry and ``row_hash`` is
the SHA-256 of ``prev_hash`` plus the canonical form of the row's columns.
Altering, deleting or inserting a row in SQL breaks the chain at that row,
which ``verify_chain`` reports. Rows written before the chain existed keep
NULL hashes and are reported as not covered, never as verified.

Other audit stores (ArchiMate composer, ARB, application rationalisation)
are copied into this table as they are written, carrying ``source_table`` /
``source_id``; the source row's ``retired_into_id`` points at its copy.
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import Index, event, select
from sqlalchemy.engine import Engine

from app.extensions import db
import logging

logger = logging.getLogger(__name__)


class AuditLog(db.Model):
    """Append-only SOC 2 audit log entry.

    Design invariants:
    - No row is ever modified or deleted after insertion.
    - ``created_at`` is set by the DB server to prevent clock tampering.
    """

    __tablename__ = "soc2_audit_log"
    __table_args__ = (
        Index("ix_soc2_audit_org_created", "organization_id", "created_at"),
        Index("ix_soc2_audit_user_created", "user_id", "created_at"),
        Index("ix_soc2_audit_record", "table_name", "record_id"),
        # Chain tail lookup and ordered export/verify per organisation.
        Index("ix_soc2_audit_org_id", "organization_id", "id"),
        {"extend_existing": True},
    )

    # BigInteger PK to support high-volume audit trails without 32-bit overflow.
    id = db.Column(db.BigInteger, primary_key=True)

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id"),
        nullable=True,
        index=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True, index=True
    )

    action = db.Column(db.String(20), nullable=False, index=True)
    table_name = db.Column(db.String(100), nullable=False, index=True)
    record_id = db.Column(db.Integer, nullable=True)
    old_value = db.Column(db.JSON, nullable=True)
    new_value = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)   # IPv6-safe
    user_agent = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        index=True,
        default=datetime.utcnow,
        server_default=db.text("NOW()"),
    )
    extra_json = db.Column(db.JSON, nullable=True)

    # Provenance for rows copied from another audit store (ADR 0008). NULL for
    # rows written here first.
    source_table = db.Column(db.String(100), nullable=True)
    source_id = db.Column(db.Integer, nullable=True)

    # Integrity chain, per organisation. NULL on rows recorded before the
    # chain existed; set on every row inserted since.
    prev_hash = db.Column(db.String(64), nullable=True)
    row_hash = db.Column(db.String(64), nullable=True)

    def __repr__(self):
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"table={self.table_name!r} record={self.record_id}>"
        )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "action": self.action,
            "table_name": self.table_name,
            "record_id": self.record_id,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "row_hash": self.row_hash,
        }

    # ------------------------------------------------------------------ #
    #  Backward-compat display aliases. The admin audit-log view + CSV    #
    #  export were written against an older schema (timestamp/user_email/ #
    #  entity_*/description/status/old_values/new_values). Expose those   #
    #  names as read-only properties over the real columns so the view    #
    #  renders instead of blowing up on missing attributes. SQL-level     #
    #  filters must still use the real column names.                      #
    # ------------------------------------------------------------------ #
    @property
    def timestamp(self):
        return self.created_at

    @property
    def entity_type(self):
        return self.table_name

    @property
    def entity_id(self):
        return self.record_id

    @property
    def old_values(self):
        return self.old_value

    @property
    def new_values(self):
        return self.new_value

    @property
    def user_email(self):
        # No email column on AuditLog; resolve for display, best-effort.
        if not self.user_id:
            return ""
        try:
            from app.models.user import User

            u = User.query.filter_by(id=self.user_id).first()
            return u.email if u and getattr(u, "email", None) else str(self.user_id)
        except Exception:
            return str(self.user_id)

    @property
    def entity_name(self):
        return ""  # not tracked in this schema

    @property
    def description(self):
        if not self.action:
            return ""
        if self.action == "tool_refused" and isinstance(self.new_value, dict):
            # A refused AI tool call: which tool, and the rule that refused it.
            return "AI tool '%s' refused. %s" % (
                self.new_value.get("tool") or "unknown",
                self.new_value.get("rule_description") or "",
            )
        _rec = f"#{self.record_id}" if self.record_id else ""
        return f"{self.action} {self.table_name or ''}{_rec}".strip()

    @property
    def status(self):
        if self.action == "tool_refused":
            return "refused"
        return ""  # not tracked

    @property
    def request_id(self):
        return None

    @property
    def error_message(self):
        return None

    # ------------------------------------------------------------------ #
    #  Class-level helpers — INSERT ONLY, no update/delete methods.       #
    # ------------------------------------------------------------------ #

    @classmethod
    def log(cls, **kwargs):
        """Insert a new audit log entry.  Non-blocking — never raises.

        Accepts both SOC 2 kwargs (``table_name``, ``record_id``,
        ``old_value``, ``new_value``) and the legacy kwargs used by
        pre-existing callers (``entity_type`` → ``table_name``,
        ``entity_id`` → ``record_id``, ``old_values`` → ``old_value``,
        ``new_values`` → ``new_value``) for backward compatibility.
        """
        try:
            # Map legacy kwargs to the SOC 2 schema.
            if "entity_type" in kwargs and "table_name" not in kwargs:
                kwargs["table_name"] = kwargs.pop("entity_type")
            if "entity_id" in kwargs and "record_id" not in kwargs:
                kwargs["record_id"] = kwargs.pop("entity_id")
            if "old_values" in kwargs and "old_value" not in kwargs:
                kwargs["old_value"] = kwargs.pop("old_values")
            if "new_values" in kwargs and "new_value" not in kwargs:
                kwargs["new_value"] = kwargs.pop("new_values")

            # Drop legacy columns that have no SOC 2 equivalent.
            for _drop in (
                "entity_name", "description", "status", "error_message",
                "request_id", "session_id", "user_email",
            ):
                kwargs.pop(_drop, None)

            # Ensure required fields are present and within length limits.
            kwargs.setdefault("table_name", "unknown")
            kwargs.setdefault("action", "admin")
            kwargs["action"] = str(kwargs["action"])[:20]

            entry = cls(**kwargs)
            db.session.add(entry)
            db.session.commit()
            return entry
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "AuditLog.log() failed (non-blocking)", exc_info=True
            )
            try:
                db.session.rollback()
            except Exception as exc:
                logger.debug("suppressed error in AuditLog.log (app/models/audit_log.py): %s", exc)
            return None

    @classmethod
    def get_recent(
        cls,
        org_id=None,
        limit=100,
        entity_type=None,
        action=None,
        user_id=None,
    ):
        """Return recent audit entries with optional filtering."""
        query = cls.query.order_by(cls.created_at.desc())
        if org_id is not None:
            query = query.filter_by(organization_id=org_id)
        if entity_type is not None:
            query = query.filter_by(table_name=entity_type)
        if action is not None:
            query = query.filter_by(action=str(action)[:20])
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        return query.limit(limit).all()

    @classmethod
    def get_entity_history(cls, entity_type, entity_id, limit=50):
        """Return audit history for a specific entity (backward compat)."""
        return (
            cls.query
            .filter_by(table_name=entity_type, record_id=entity_id)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def log_file_upload(
        cls,
        user_id,
        filename,
        sanitized_filename=None,
        file_size_bytes=None,
        mime_type=None,
        ip_address=None,
        route=None,
        status="success",
        error_message=None,
    ):
        """Log a file-upload event (backward compat helper)."""
        return cls.log(
            action="admin",
            table_name="file",
            user_id=user_id,
            ip_address=ip_address,
            new_value={
                "original_filename": filename,
                "sanitized_filename": sanitized_filename,
                "file_size_bytes": file_size_bytes,
                "mime_type": mime_type,
                "route": route,
                "status": status,
                "error_message": error_message,
            },
        )

    @classmethod
    def record_ai_action(cls, action_type: str, entity_type: str, entity_id=None):
        """Record an AI-originated action (backward compat)."""
        return cls.log(
            action=str(action_type)[:20],
            table_name=entity_type,
            record_id=entity_id,
            new_value={"ai_originated": True},
        )

    # ------------------------------------------------------------------ #
    #  Integrity chain: verification and the recorded verification result #
    # ------------------------------------------------------------------ #

    VERIFY_ACTION = "verify"

    @classmethod
    def org_predicate(cls, org_id):
        """The one tenant predicate every read of this table goes through."""
        if org_id is None:
            return cls.organization_id.is_(None)
        return cls.organization_id == org_id

    @classmethod
    def verify_chain(cls, org_id, batch_size=2000):
        """Walk ``org_id``'s chain in id order and report the first break.

        Returns a dict with ``status`` one of ``intact`` / ``broken`` /
        ``empty`` (no sealed entry to check), ``checked`` (sealed entries
        verified), ``unsealed`` (entries recorded before the chain existed,
        which this cannot vouch for), and on a break ``first_broken_id`` and
        ``reason``, plus ``last_id`` / ``last_row_hash`` of the newest entry
        verified. A chain cannot show that its newest entries were removed;
        the recorded ``last_row_hash``, kept by the auditor with the result,
        is what shows that.
        """
        table = cls.__table__

        result = {
            "status": "empty",
            "checked": 0,
            "unsealed": 0,
            "first_broken_id": None,
            "reason": None,
            "last_id": None,
            "last_row_hash": None,
        }
        prev = None
        sealed_seen = False
        last_seen_id = 0
        while True:
            rows = db.session.execute(
                select(table)
                .where(cls.org_predicate(org_id), table.c.id > last_seen_id)
                .order_by(table.c.id)
                .limit(batch_size)
            ).mappings().all()
            if not rows:
                break
            for row in rows:
                last_seen_id = row["id"]
                if row["row_hash"] is None:
                    if not sealed_seen:
                        result["unsealed"] += 1
                        continue
                    return cls._broken(result, row["id"], "This entry carries no seal.")
                sealed_seen = True
                if row["prev_hash"] != prev:
                    return cls._broken(
                        result, row["id"],
                        "This entry does not follow the one before it: an entry "
                        "was removed, inserted or re-sealed.",
                    )
                if row["row_hash"] not in (
                    chain_digest(row["prev_hash"], row),
                    chain_digest(row["prev_hash"], row, LEGACY_CHAINED_COLUMNS),
                ):
                    return cls._broken(result, row["id"], "This entry was altered after it was recorded.")
                prev = row["row_hash"]
                result["checked"] += 1
                result["last_id"] = row["id"]
                result["last_row_hash"] = prev
        result["status"] = "intact" if sealed_seen else "empty"
        return result

    @staticmethod
    def _broken(result, row_id, reason):
        result["status"] = "broken"
        result["first_broken_id"] = row_id
        result["reason"] = reason
        return result

    @classmethod
    def verify_and_record(cls, org_id, user_id=None):
        """Verify ``org_id``'s chain and record the result as an entry of its own."""
        result = cls.verify_chain(org_id)
        cls.log(
            action=cls.VERIFY_ACTION,
            table_name=cls.__tablename__,
            organization_id=org_id,
            user_id=user_id,
            new_value=result,
        )
        return result

    @classmethod
    def latest_verification(cls, org_id):
        return (
            cls.query.filter(
                cls.org_predicate(org_id),
                cls.action == cls.VERIFY_ACTION,
                cls.table_name == cls.__tablename__,
            )
            .order_by(cls.id.desc())
            .first()
        )

    # ------------------------------------------------------------------ #
    #  Export: every matching row, in id order, in bounded batches.       #
    # ------------------------------------------------------------------ #

    @classmethod
    def iter_rows(cls, criteria, upto_id, batch_size=1000):
        """Yield row mappings matching ``criteria`` with ``id <= upto_id``.

        Keyset-paginated so an export of any size holds one batch in memory
        and never stops at an arbitrary cap. ``criteria`` must include the
        organisation predicate.
        """
        table = cls.__table__
        last_id = 0
        while True:
            rows = db.session.execute(
                select(table)
                .where(*criteria, table.c.id > last_id, table.c.id <= upto_id)
                .order_by(table.c.id)
                .limit(batch_size)
            ).mappings().all()
            if not rows:
                return
            yield from rows
            last_id = rows[-1]["id"]


# ---------------------------------------------------------------------- #
#  Sealing. One implementation for ORM inserts and Core inserts.         #
# ---------------------------------------------------------------------- #

#: Columns covered by ``row_hash``. ``prev_hash`` is covered by prefixing it.
#: ``id`` is assigned by the database and is not hashed: the chain's order is
#: id order (appends per organisation are serialised by the advisory lock),
#: and ``prev_hash`` links make any removal, insertion or reordering visible.
CHAINED_COLUMNS = (
    "organization_id", "user_id", "action", "table_name", "record_id",
    "old_value", "new_value", "ip_address", "user_agent", "created_at",
    "extra_json", "source_table", "source_id",
)
#: What entries sealed before the id left the seal covered: the same columns
#: plus ``id``. Those seals stay valid, so verification accepts either form.
LEGACY_CHAINED_COLUMNS = ("id", *CHAINED_COLUMNS)
_JSON_COLUMNS = ("old_value", "new_value", "extra_json")
_INT_COLUMNS = ("organization_id", "user_id", "record_id", "source_id")
_STR_COLUMNS = ("action", "table_name", "ip_address", "user_agent", "source_table")

# Namespace for pg_advisory_xact_lock(namespace, organisation): serialises
# chain appends per organisation so two transactions cannot fork the chain.
_CHAIN_LOCK_NAMESPACE = 50_210_001
_CHAIN_INFO_KEY = "_audit_chain"


def _canonical_json(value):
    if value is None:
        return None
    # Round-trip first so tuples, int keys and the like take the shape the
    # database hands back, then serialise deterministically.
    return json.dumps(
        json.loads(json.dumps(value, default=str)),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _normalise_timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _normalise(values):
    """Coerce values to the types the database stores, in place."""
    for key in _INT_COLUMNS:
        if values.get(key) is not None:
            values[key] = int(values[key])
    for key in _STR_COLUMNS:
        if values.get(key) is not None:
            values[key] = str(values[key])
    values["created_at"] = _normalise_timestamp(values.get("created_at")) or datetime.utcnow()
    return values


def chain_digest(prev_hash, values, columns=CHAINED_COLUMNS):
    """SHA-256 over ``prev_hash`` and the canonical form of the chained columns."""
    body = {}
    for key in columns:
        value = values.get(key)
        if key in _JSON_COLUMNS:
            value = _canonical_json(value)
        elif key == "created_at" and value is not None:
            value = _normalise_timestamp(value).isoformat(timespec="microseconds")
        body[key] = value
    payload = (prev_hash or "") + "\n" + json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chain_state(connection):
    """Per-transaction chain tails, keyed by the server transaction id.

    Rows sealed earlier in the same flush are not in the table yet, so the
    tail of each organisation's chain is carried here for the life of the
    transaction. Keying on the server's transaction id means a state left
    behind by a transaction that ended without a visible commit or rollback
    is never reused.
    """
    txid = connection.exec_driver_sql("SELECT txid_current()").scalar()
    state = connection.info.get(_CHAIN_INFO_KEY)
    if not state or state["txid"] != txid:
        state = {"txid": txid, "tails": {}}
        connection.info[_CHAIN_INFO_KEY] = state
    return state


def seal(connection, values):
    """Assign ``prev_hash`` and ``row_hash`` to an audit row about to be inserted."""
    _normalise(values)
    state = _chain_state(connection)
    org_id = values.get("organization_id")
    key = org_id if org_id is not None else 0
    if key not in state["tails"]:
        connection.execute(
            db.text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": _CHAIN_LOCK_NAMESPACE, "key": key},
        )
        table = AuditLog.__table__
        tail = connection.execute(
            select(table.c.row_hash)
            .where(AuditLog.org_predicate(org_id), table.c.row_hash.isnot(None))
            .order_by(table.c.id.desc())
            .limit(1)
        ).scalar()
        state["tails"][key] = tail
    values["prev_hash"] = state["tails"][key]
    values["row_hash"] = chain_digest(values["prev_hash"], values)
    state["tails"][key] = values["row_hash"]
    return values


def chain_insert(connection, **values):
    """Insert one sealed audit row through ``connection``; returns its id.

    For writers that run mid-flush (mapper events) and so cannot use the
    session. ORM inserts are sealed by the ``before_insert`` hook below.
    """
    seal(connection, values)
    table = AuditLog.__table__
    return connection.execute(table.insert().values(**values).returning(table.c.id)).scalar()


@event.listens_for(AuditLog, "before_insert")
def _seal_orm_insert(mapper, connection, target):
    values = {key: getattr(target, key, None) for key in CHAINED_COLUMNS}
    seal(connection, values)
    for key in ("prev_hash", "row_hash", "created_at", *_INT_COLUMNS, *_STR_COLUMNS):
        setattr(target, key, values.get(key))


def _forget_chain_state(conn, *args):
    conn.info.pop(_CHAIN_INFO_KEY, None)


# A rolled-back savepoint removes rows the cached tail may point at.
for _evt in ("commit", "rollback", "rollback_savepoint"):
    event.listen(Engine, _evt, _forget_chain_state)


# ---------------------------------------------------------------------- #
#  Copies from the other audit stores (ADR 0008: one audit store).       #
# ---------------------------------------------------------------------- #

# One literal statement per table: table names are never interpolated into SQL.
_ORG_OF_SQL = {
    "saved_diagrams": "SELECT organization_id FROM saved_diagrams WHERE id = :id",  # tenancy-ok: attributing one row by its own key
    "users": "SELECT organization_id FROM users WHERE id = :id",  # tenancy-ok: attributing one row by its own key
    "application_components": "SELECT organization_id FROM application_components WHERE id = :id",  # tenancy-ok: attributing one row by its own key
}

#: Points one source row at its copy (``:audit_id``, ``:id``), per source table.
RETIRE_SQL = {
    "archimate_audit_logs": "UPDATE archimate_audit_logs SET retired_into_id = :audit_id WHERE id = :id",  # tenancy-ok: one row by its own key
    "arb_audit_logs": "UPDATE arb_audit_logs SET retired_into_id = :audit_id WHERE id = :id",  # tenancy-ok: one row by its own key
    "rationalization_audit_entries": "UPDATE rationalization_audit_entries SET retired_into_id = :audit_id WHERE id = :id",  # tenancy-ok: one row by its own key
}


def _org_of(connection, table, row_id):
    if row_id is None:
        return None
    return connection.execute(db.text(_ORG_OF_SQL[table]), {"id": row_id}).scalar()


def _existing_user(connection, user_id):
    """``user_id`` if that user exists, else None (source stores hold loose ids)."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    found = connection.execute(
        db.text("SELECT id FROM users WHERE id = :id"), {"id": user_id}  # tenancy-ok: existence check of one id
    ).scalar()
    return found


def _clip(value, length):
    return str(value)[:length] if value is not None else None


def _from_arb(connection, row):
    return {
        "organization_id": row["organization_id"],
        "user_id": _existing_user(connection, row["user_id"]),
        "action": _clip(row["action"], 20),
        "table_name": _clip(f"arb:{row['entity_type']}", 100),
        "record_id": row["entity_id"],
        "old_value": row["old_value"],
        "new_value": {
            "entity_reference": row["entity_reference"],
            "description": row["action_description"],
            "new_value": row["new_value"],
            "changed_fields": row["changed_fields"],
        },
        "ip_address": _clip(row["ip_address"], 45),
        "user_agent": _clip(row["user_agent"], 500),
        "created_at": row["timestamp"],
        "extra_json": {"source_action": row["action"], "request_id": row["request_id"]},
    }


def _from_archimate(connection, row):
    # No organisation column: the diagram's organisation, else its author's.
    org_id = _org_of(connection, "saved_diagrams", row["viewpoint_id"])
    if org_id is None:
        org_id = _org_of(connection, "users", row["user_id"])
    return {
        "organization_id": org_id,
        "user_id": _existing_user(connection, row["user_id"]),
        "action": _clip(row["action"], 20),
        "table_name": _clip(f"archimate:{row['entity_type'] or 'diagram'}", 100),
        "record_id": row["entity_id"],
        "old_value": row["old_value"],
        "new_value": {
            "entity_name": row["entity_name"],
            "viewpoint_id": row["viewpoint_id"],
            "value": row["new_value"],
        },
        "created_at": row["created_at"],
        "extra_json": {"source_action": row["action"]},
    }


def _from_rationalization(connection, row):
    org_id = _org_of(connection, "application_components", row["application_id"])
    user_id = _existing_user(connection, row["actor"])
    return {
        "organization_id": org_id,
        "user_id": user_id,
        "action": _clip(row["action"], 20),
        "table_name": "rationalization:application",
        "record_id": row["application_id"],
        "old_value": row["before_state"],
        "new_value": {
            "after_state": row["after_state"],
            "details": row["details"],
            "score_id": row["score_id"],
        },
        "created_at": row["created_at"],
        "extra_json": {
            "source_action": row["action"],
            "actor": row["actor"],
            "actor_type": row["actor_type"],
        },
    }


#: source table -> builder of the AuditLog copy's values.
MIRRORED_SOURCES = {
    "archimate_audit_logs": _from_archimate,
    "arb_audit_logs": _from_arb,
    "rationalization_audit_entries": _from_rationalization,
}


def mirror_values(connection, source_table, row):
    """AuditLog values for one source row, or None when it has no organisation.

    A row whose organisation cannot be determined is never copied into
    shared scope; the backfill lists it for the platform administrator.
    """
    values = MIRRORED_SOURCES[source_table](connection, row)
    if values.get("organization_id") is None:
        return None
    values["source_table"] = source_table
    values["source_id"] = row["id"]
    return values


def mirror_source_row(connection, source_table, row):
    """Copy one source row into this table and point it at its copy.

    Runs inside a savepoint: a failure here is logged and never fails the
    action the source row records. Returns the copy's id, or None.
    """
    try:
        with connection.begin_nested():
            values = mirror_values(connection, source_table, row)
            if values is None:
                return None
            audit_id = chain_insert(connection, **values)
            connection.execute(
                db.text(RETIRE_SQL[source_table]),
                {"audit_id": audit_id, "id": row["id"]},
            )
        return audit_id
    except Exception:
        logger.warning(
            "Copy of %s #%s into the audit log failed (non-blocking)",
            source_table, row.get("id"), exc_info=True,
        )
        return None


def _mirror_after_insert(mapper, connection, target):
    source_table = getattr(target, "__tablename__", None)
    if source_table not in MIRRORED_SOURCES:
        return
    from sqlalchemy.orm.attributes import set_committed_value

    row = {attr.key: getattr(target, attr.key, None) for attr in mapper.column_attrs}
    audit_id = mirror_source_row(connection, source_table, row)
    if audit_id is not None and hasattr(target, "retired_into_id"):
        set_committed_value(target, "retired_into_id", audit_id)


event.listen(db.Model, "after_insert", _mirror_after_insert, propagate=True)

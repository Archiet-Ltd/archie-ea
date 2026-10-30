"""
audit_middleware — SQLAlchemy event listeners for SOC 2 audit logging.

Registers ``after_insert``, ``after_update``, and ``after_delete`` mapper
events on the key controlled models so that every DB mutation is captured
in ``soc2_audit_log`` without requiring callers to remember to call the
audit service explicitly.

Controlled models: Solution, ApplicationComponent, ArchitectureReviewBoard,
User.

Registration is attempted lazily so that import failures on any individual
model class never prevent the application from starting.

Call ``install_audit_logging(app)`` once in ``app/__init__.py``.
"""

import importlib
import logging

from sqlalchemy import event, inspect

logger = logging.getLogger(__name__)

# (module_path, class_name, logical_table_name)
_CONTROLLED_MODELS = [
    ("app.models.solution_models", "Solution", "solution"),
    ("app.models.application", "ApplicationComponent", "application_component"),
    (
        "app.models.architecture_review_board",
        "ArchitectureReviewBoard",
        "architecture_review_board",
    ),
    ("app.models.user", "User", "user"),
    # The model itself: every create, update and delete of an element or a
    # relationship, who owns an application, and the capability store.
    ("app.models.archimate_core", "ArchiMateElement", "archimate_elements"),
    ("app.models.archimate_core", "ArchiMateRelationship", "archimate_relationships"),
    ("app.models.application_owner", "ApplicationOwner", "application_owners"),
    ("app.models.enterprise_intelligence", "ApplicationOwnership", "application_ownership"),
    ("app.models.unified_capability", "UnifiedCapability", "unified_capabilities"),
    ("app.models.business_capabilities", "BusinessCapability", "business_capability"),
]

#: The table labels this listener fires for -- the true boundary for anything that
#: reacts to a create here (such as history_spread.py's date-of-first-version), which
#: is a different question from which of them the model's own change history reads
#: (app.modules.intelligence.services.history_service.AUDITED_TABLES; an account's own
#: table is a controlled model so its writes are audited, but is deliberately absent
#: from that other list).
CONTROLLED_TABLE_LABELS = frozenset(label for _, _, label in _CONTROLLED_MODELS)

# Columns the database or the ORM stamps on every write; a change to only
# these is not a change a person made.
_BOOKKEEPING_COLUMNS = frozenset(
    ["updated_at", "modified_at", "recorded_at", "valid_from", "valid_to", "superseded_at"]
)

_EXCLUDED_COLUMNS = frozenset(
    ["password_hash", "token", "secret", "api_key", "fernet_key"]
)


def _get_request_context():
    """Return (user_id, org_id, ip, ua) from the current Flask request.

    Returns four ``None`` values when called outside a request context.
    """
    try:
        from flask import g, has_request_context, request
        from flask_login import current_user

        if not has_request_context():
            return None, None, None, None

        user_id = current_user.id if current_user.is_authenticated else None
        org_id = getattr(g, "current_org_id", None)

        forwarded = request.headers.get("X-Forwarded-For")
        ip = (forwarded.split(",")[0].strip() if forwarded else request.remote_addr or "")[:45]

        ua_raw = request.headers.get("User-Agent", "")
        ua = ua_raw[:500] if ua_raw else None

        return user_id, org_id, ip, ua
    except Exception:
        return None, None, None, None


def _json_safe(value):
    """Coerce a value into something the JSON audit columns can store."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    # Structured values stay structured, so a recorded change can be applied again.
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _label_of(connection, target):
    """What a person calls the changed record: its name, or for a relationship its two ends."""
    if target is None:
        return None
    for attr in ("name", "title"):
        label = getattr(target, attr, None)
        if label:
            return label
    source_id = getattr(target, "source_id", None)
    target_id = getattr(target, "target_id", None)
    if source_id is not None and target_id is not None and hasattr(target, "type"):
        from sqlalchemy import text

        names = dict(connection.execute(
            text("SELECT id, name FROM archimate_elements WHERE id IN (:a, :b)"),  # tenancy-ok: names of the two ends of the row being audited, read by their own keys
            {"a": source_id, "b": target_id},
        ).all())
        return "%s: %s to %s" % (
            target.type, names.get(source_id, "element %s" % source_id),
            names.get(target_id, "element %s" % target_id),
        )
    return None


def _write_audit(connection, action, table_label, record_id, old_value=None, new_value=None, target=None,
                 recorded_at=None):
    """Insert one audit row using the flush's own connection.

    Mapper events (after_insert/update/delete) fire mid-flush, so we must NOT use
    db.session here — committing during a flush raises. Writing through the event
    ``connection`` records the row in the same transaction as the mutation that
    triggered it.
    """
    from app.models.audit_log import chain_insert

    from app.services.change_source import current_change_context

    user_id, org_id, ip, ua = _get_request_context()
    context = current_change_context()
    if user_id is None:
        user_id = context["user_id"]
    # The row's own organisation wins over the request's: an importer or a
    # connector writes for the organisation it was run for.
    owner_org = getattr(target, "organization_id", None) if target is not None else None
    if owner_org is not None:
        org_id = owner_org
    label = _label_of(connection, target)
    # Sealed into the organisation's integrity chain like every other entry.
    # A date is given only by a demonstration or sample load, as the record's first version is written.
    dated = {"created_at": recorded_at} if recorded_at is not None else {}
    chain_insert(
        connection,
        **dated,
        action=str(action)[:20],
        table_name=table_label,
        record_id=record_id,
        old_value=old_value,
        new_value=new_value,
        user_id=user_id,
        organization_id=org_id,
        ip_address=ip,
        user_agent=ua,
        extra_json={
            "source": context["source"],
            "correlation_id": context["correlation_id"],
            "label": _json_safe(label),
        },
    )


def _column_snapshot(target):
    """JSON-safe dict of a model's column values (columns only, secrets excluded)."""
    snap = {}
    for attr in inspect(target).mapper.column_attrs:
        if attr.key in _EXCLUDED_COLUMNS:
            continue
        snap[attr.key] = _json_safe(getattr(target, attr.key, None))
    return snap


_LISTENERS: dict = {}


def _listeners_for(table_label: str):
    """The three listeners for a table, made once so installing twice adds no second copy."""
    if table_label not in _LISTENERS:
        _LISTENERS[table_label] = (
            _make_after_insert(table_label),
            _make_after_update(table_label),
            _make_after_delete(table_label),
        )
    return _LISTENERS[table_label]


def record_create(connection, table_label: str, target) -> None:
    """Write the first version of ``target`` to the change history, dated when a demonstration load asks.

    The mapper's after_insert listener calls this. A row that a mapper event writes with a Core insert
    (a strategy-layer mirror element) has no flush of its own, so its writer calls this with a transient
    instance, and the row gets its create entry like every other.
    """
    from app.services.change_source import current_change_context

    try:
        record_id = getattr(target, "id", None)
        recorded_at = None
        dating = current_change_context()["dating"]
        if dating is not None:
            try:
                recorded_at = dating.date_new_record(connection, table_label, target)
            except Exception:
                # The record is still audited, with today's date, rather than not at all.
                logger.warning("audit dating failed for %s", table_label, exc_info=True)
        _write_audit(
            connection, "create", table_label, record_id,
            new_value=_column_snapshot(target), target=target, recorded_at=recorded_at,
        )
    except Exception:
        logger.debug("audit after_insert failed for %s", table_label, exc_info=True)


def _make_after_insert(table_label: str):
    def _after_insert(mapper, connection, target):
        record_create(connection, table_label, target)

    return _after_insert


def _make_after_update(table_label: str):
    def _after_update(mapper, connection, target):
        try:
            record_id = getattr(target, "id", None)

            old_val = {}
            new_val = {}
            for attr in inspect(target).attrs:
                hist = attr.history
                if hist.has_changes():
                    key = attr.key
                    if key in _EXCLUDED_COLUMNS:
                        continue
                    if key in _BOOKKEEPING_COLUMNS:
                        continue
                    before = _json_safe(hist.deleted[0] if hist.deleted else None)
                    after = _json_safe(hist.added[0] if hist.added else None)
                    if before in (None, "") and after in (None, ""):
                        continue  # empty to empty is not a change
                    old_val[key] = before
                    new_val[key] = after

            if not old_val and not new_val:
                return  # nothing auditable changed

            _write_audit(
                connection, "update", table_label, record_id,
                old_value=old_val, new_value=new_val, target=target,
            )
        except Exception:
            logger.debug("audit after_update failed for %s", table_label, exc_info=True)

    return _after_update


def _make_after_delete(table_label: str):
    def _after_delete(mapper, connection, target):
        try:
            record_id = getattr(target, "id", None)
            _write_audit(
                connection, "delete", table_label, record_id,
                old_value=_column_snapshot(target), target=target,
            )
        except Exception:
            logger.debug("audit after_delete failed for %s", table_label, exc_info=True)

    return _after_delete


def install_audit_logging(app):
    """Register SQLAlchemy mapper events for SOC 2 audit logging.

    Call once from ``app/__init__.py`` inside ``create_app()``.
    """
    registered = 0
    for module_path, class_name, table_label in _CONTROLLED_MODELS:
        try:
            module = importlib.import_module(module_path)
            model_cls = getattr(module, class_name)

            inserted, updated, deleted = _listeners_for(table_label)
            for name, fn in (("after_insert", inserted), ("after_update", updated), ("after_delete", deleted)):
                if not event.contains(model_cls, name, fn):
                    event.listen(model_cls, name, fn)

            registered += 1
            logger.debug("audit_middleware: registered events for %s.%s", module_path, class_name)
        except (ImportError, AttributeError) as exc:
            logger.debug(
                "audit_middleware: skipped %s.%s — %s", module_path, class_name, exc
            )

    logger.info("audit_middleware: registered events on %d controlled models", registered)

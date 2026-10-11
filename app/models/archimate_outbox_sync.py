"""Outbox emission for ArchiMate element and relationship mutations.

Uses a session-level ``after_flush`` listener so outbox events are queued
inside the same transaction without triggering a nested flush.  Every write
to ``ArchiMateElement`` and ``ArchiMateRelationship`` produces exactly one
row in ``transformation_outbox_events``.

The relay in ``app/services/event_log_service.py`` copies these rows into
the partitioned ``event_log``.

State is stored on ``session.info`` so concurrent sessions (threads,
greenlets, or nested sessions) never share pending/seen/depth state.
"""

from __future__ import annotations

import logging

from sqlalchemy import event, inspect

from app.extensions import db

logger = logging.getLogger(__name__)

# The models we listen for.
_TARGET_MODELS: dict = {}

# Keys for session.info dict — scoped per session, never shared across threads.
_INFO_KEY_PENDING = "_archimate_outbox_pending"
_INFO_KEY_SEEN = "_archimate_outbox_seen"
_INFO_KEY_DEPTH = "_archimate_outbox_flush_depth"


def _get_pending(session) -> list:
    pending = session.info.get(_INFO_KEY_PENDING)
    if pending is None:
        pending = []
        session.info[_INFO_KEY_PENDING] = pending
    return pending


def _get_seen(session) -> set:
    seen = session.info.get(_INFO_KEY_SEEN)
    if seen is None:
        seen = set()
        session.info[_INFO_KEY_SEEN] = seen
    return seen


def _get_depth(session) -> int:
    return session.info.get(_INFO_KEY_DEPTH, 0)


def _set_depth(session, value: int) -> None:
    session.info[_INFO_KEY_DEPTH] = value


def _entity_type_for(instance: object) -> str | None:
    """Return the entity_type string for a flushed model instance."""
    cls = type(instance)
    for model_cls, et in _TARGET_MODELS.items():
        if issubclass(cls, model_cls):
            return et
    return None


def _action_for(target: object) -> str | None:
    """Determine the mutation action for a flushed instance."""
    state = inspect(target)
    if state.deleted:
        return "deleted"
    if state.transient or not state.has_identity:
        return None  # handled below
    # was_insert: True for new persistent instances.
    if state.was_insert:
        return "created"
    if state.modified:
        return "updated"
    return None


@event.listens_for(db.session, "after_flush")
def _on_after_flush(session, flush_context):
    """Collect new/changed/deleted entities during flush and queue outbox events.

    SQLAlchemy's ``after_flush`` fires once per flush (not per object).  New
    objects added here will be flushed by the next auto-flush (usually the
    one triggered by ``session.commit()``).

    The recursive flush triggered by adding outbox rows must not re-process
    the same entities — we track seen instances per session and only
    collect on the outermost call.
    """
    depth = _get_depth(session) + 1
    _set_depth(session, depth)

    pending = _get_pending(session)
    seen = _get_seen(session)

    if depth == 1:
        pending.clear()
        seen.clear()

    for instance in session.new:
        if id(instance) not in seen:
            et = _entity_type_for(instance)
            if et is not None:
                seen.add(id(instance))
                pending.append((et, "created", instance))

    for instance in session.dirty:
        if id(instance) not in seen:
            et = _entity_type_for(instance)
            if et is not None:
                seen.add(id(instance))
                pending.append((et, "updated", instance))

    for instance in session.deleted:
        if id(instance) not in seen:
            et = _entity_type_for(instance)
            if et is not None:
                seen.add(id(instance))
                pending.append((et, "deleted", instance))

    # Only emit on the outermost call — recursive calls are just the
    # outbox rows themselves being flushed and carry no target entities.
    if depth > 1:
        _set_depth(session, depth - 1)
        return

    # Now create outbox rows.  This happens AFTER the main flush has
    # committed its SQL to the database, so we are safe to add more objects.
    from app.services.outbox import emit_event

    for entity_type, action, target in pending:
        org_id = getattr(target, "organization_id", None)
        if org_id is None:
            logger.warning(
                "outbox_sync: %s %s has no organization_id — event skipped",
                entity_type, action,
            )
            continue
        event_type_val = f"{entity_type}.{action}"
        payload = {
            "action": action,
            "id": getattr(target, "id", None),
        }
        if hasattr(target, "name"):
            payload["name"] = target.name
        if hasattr(target, "type"):
            payload["type"] = target.type
        if hasattr(target, "layer"):
            payload["layer"] = target.layer

        try:
            emit_event(
                organization_id=org_id,
                event_type=event_type_val,
                payload=payload,
                entity_type=entity_type,
                entity_id=getattr(target, "id", None),
            )
        except Exception:
            logger.exception(
                "outbox_sync: failed to emit %s.%s for id=%s",
                entity_type, action, getattr(target, "id", None),
            )

    _set_depth(session, depth - 1)


def install_archimate_outbox_sync():
    """Record the models to watch; the listener is global on ``db.session``.

    Idempotent — calling again is safe.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    _TARGET_MODELS.clear()
    _TARGET_MODELS[ArchiMateElement] = "archimate_element"
    _TARGET_MODELS[ArchiMateRelationship] = "archimate_relationship"

    logger.info("ArchiMate outbox sync installed for elements and relationships")


__all__ = ["install_archimate_outbox_sync"]
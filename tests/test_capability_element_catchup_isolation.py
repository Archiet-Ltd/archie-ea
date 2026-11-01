"""The element-id catch-up queue belongs to its own session and transaction.

A capability queued for the after-flush catch-up must not outlive a failed
flush, must never be handed to a different session, and the update it issues
must name the capability's organisation.
"""

from __future__ import annotations

import gc

import pytest
from sqlalchemy import event, text

from app.models import business_capabilities as bc
from app.models.business_capabilities import BusinessCapability


def _queued(session) -> set:
    """Every capability id still waiting for a catch-up, wherever it is kept."""
    queued = set(session.info.get("pending_element_reprojection") or ())
    for ids in getattr(bc, "_pending_element_reprojection", {}).values():
        queued |= set(ids)
    return queued


def _statements(connection):
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(connection, "before_cursor_execute", _record)
    return seen, lambda: event.remove(connection, "before_cursor_execute", _record)


def test_a_failed_flush_leaves_nothing_queued_for_the_next_session(db_session, make_org):
    org = make_org("catchup-rollback")
    stale = _queued(db_session)

    def _fail_after_insert(session, flush_context):
        raise RuntimeError("flush failed after the insert step")

    event.listen(db_session, "after_flush", _fail_after_insert, insert=True)
    try:
        with pytest.raises(RuntimeError):
            with db_session.begin_nested():
                db_session.add(
                    BusinessCapability(name="Rolled Back", organization_id=org.id, level=1)
                )
                db_session.flush()
    finally:
        event.remove(db_session, "after_flush", _fail_after_insert)

    assert _queued(db_session) == stale, "a rolled-back capability is still queued"

    # A different session, even one reusing the dead session's object id,
    # finds nothing to catch up and issues no element update.
    other = db_session.session_factory()
    try:
        seen, stop = _statements(other.connection())
        try:
            bc._on_after_flush_reproject_capability_elements(other, None)
        finally:
            stop()
        assert _queued(other) == stale
        assert not [s for s in seen if "UPDATE unified_capabilities" in s]
    finally:
        other.close()
        gc.collect()


def test_the_catchup_update_names_the_capabilitys_organisation(db_session, make_org):
    org = make_org("catchup-scope")
    seen, stop = _statements(db_session.connection())
    try:
        cap = BusinessCapability(name="Scoped Catch-up", organization_id=org.id, level=1)
        db_session.add(cap)
        db_session.commit()
    finally:
        stop()

    updates = [s for s in seen if s.lstrip().startswith("UPDATE unified_capabilities SET archimate_element_id")]
    assert updates, "the catch-up did not run for a brand-new capability"
    assert all("organization_id" in s for s in updates), updates
    assert _queued(db_session) == set(), "the queue was not emptied by the flush that used it"
    projected = db_session.execute(
        text(
            "SELECT archimate_element_id FROM unified_capabilities "
            "WHERE source_table = 'business_capability' AND source_id = :sid"
        ),
        {"sid": str(cap.id)},
    ).scalar_one()
    assert projected == cap.archimate_element_id

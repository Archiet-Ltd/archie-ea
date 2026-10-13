"""The outbox relay must take its row locks up front, in one order.

``relay_outbox_batch`` used to read a batch of unpublished outbox rows with no
lock and then update them one at a time inside one transaction, so it took row
locks as it went. Anything else locking the same rows in a different order --
a fixture tearing rows down, a second relay worker -- could close a cycle, and
PostgreSQL killed one side with "deadlock detected" on
``transformation_outbox_events``. Selecting the batch ``FOR UPDATE SKIP LOCKED``
takes every lock before the first update and leaves rows another transaction
holds to that transaction.
"""
import threading
import uuid
from unittest.mock import patch

from sqlalchemy import text

from app import db


def _make_org_with_outbox_rows(app, count):
    from app.models.organization import Organization
    from app.services.outbox import emit_event

    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"Relay Lock Org {suffix}", slug=f"relay-lock-{suffix}")
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        events = [
            emit_event(
                organization_id=org_id,
                event_type="test.relay.locking",
                payload={"seq": i},
                entity_type="test_entity",
                entity_id=i,
            )
            for i in range(count)
        ]
        db.session.commit()
        ids = [event.id for event in events]
        db.session.remove()
    return org_id, sorted(ids)


def test_relay_does_not_deadlock_with_a_transaction_locking_rows_in_the_other_order(
    app, _schema
):
    from app.services import event_log_service

    org_id, (first, middle, last) = _make_org_with_outbox_rows(app, 3)
    real_append_one = event_log_service._append_one
    relay_has_first = threading.Event()
    other_holds_last = threading.Event()
    errors = []

    def append_one(outbox):
        if outbox.id == middle:
            # The relay has already updated (and so locked) the first row. Let the other transaction lock
            # the last row now; once the relay selects its batch up front that
            # lock simply waits, so the wait is short and its absence is fine.
            relay_has_first.set()
            other_holds_last.wait(timeout=3)
        real_append_one(outbox)

    def relay():
        try:
            with app.app_context():
                event_log_service.relay_outbox_batch()
                db.session.commit()
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(f"relay: {exc}")
        finally:
            db.session.remove()

    def other():
        # Locks the last row, then the first: the opposite order to the relay.
        try:
            with app.app_context(), db.engine.begin() as connection:
                relay_has_first.wait(timeout=10)
                connection.execute(
                    text("SELECT 1 FROM transformation_outbox_events WHERE id = :i FOR UPDATE"),
                    {"i": last},
                )
                other_holds_last.set()
                connection.execute(
                    text("SELECT 1 FROM transformation_outbox_events WHERE id = :i FOR UPDATE"),
                    {"i": first},
                )
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(f"other: {exc}")
        finally:
            other_holds_last.set()

    try:
        with patch.object(event_log_service, "_append_one", append_one):
            threads = [threading.Thread(target=relay, daemon=True), threading.Thread(target=other, daemon=True)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
        assert not any(thread.is_alive() for thread in threads), "a transaction is still blocked"
        assert errors == [], errors
    finally:
        with app.app_context():
            db.session.execute(
                text(
                    "UPDATE transformation_outbox_events SET published_at = NOW() "
                    "WHERE organization_id = :org_id AND published_at IS NULL"
                ),
                {"org_id": org_id},
            )
            db.session.commit()
            db.session.remove()

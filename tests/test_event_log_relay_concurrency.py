"""The event-log relay runs for every organisation without raising.

Production logged ``RuntimeError: deque mutated during iteration`` from the
relay's first query after a server start. ``Session.execute`` walks the
session's ``do_orm_execute`` listener deque, and the scheduler thread that runs
the relay was started while ``create_app`` was still registering listeners on
that same session (the tenant filter, then every module's models and hooks), so
a relay firing during boot iterated a deque the main thread was appending to.
The scheduler must start only once the session's listeners are all in place.
"""
import threading
import uuid

from sqlalchemy import func

from app import db


_BOOT_PROBE = """
import json
from app import create_app, db
from app._bootstrap import extensions

def count():
    session = db.session.session_factory()
    try:
        return sum(
            len(getattr(session.dispatch, name))
            for name in ("do_orm_execute", "before_flush", "after_flush")
        )
    finally:
        session.close()

seen = {}
real = extensions.init_scheduler

def recording(app):
    seen["at_start"] = count()
    return real(app)

extensions.init_scheduler = recording
create_app("testing")
print(json.dumps({"at_start": seen["at_start"], "at_end": count()}))
"""


def test_scheduler_starts_after_the_session_listeners_are_registered(_schema):
    """A fresh process, because a warm one has every listener registered already."""
    import json
    import os
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, "-c", _BOOT_PROBE],
        cwd=root,
        env={**os.environ, "PYTHONPATH": root},
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    )
    counts = json.loads(result.stdout.strip().splitlines()[-1])

    assert counts["at_start"] == counts["at_end"], (
        "session listeners were registered after the scheduler thread started, "
        "so a relay run can iterate a listener deque that is still growing: "
        f"{counts}"
    )


def _org_with_outbox_rows(app, count):
    from app.models.organization import Organization
    from app.services.outbox import emit_event

    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"Relay Once Org {suffix}", slug=f"relay-once-{suffix}")
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        events = [
            emit_event(
                organization_id=org_id,
                event_type="test.relay.once",
                payload={"seq": i},
                entity_type="test_entity",
                entity_id=i,
            )
            for i in range(count)
        ]
        db.session.commit()
        ids = [event.id for event in events]
        db.session.remove()
    return org_id, ids


def test_two_concurrent_relay_runs_relay_each_outbox_row_once(app, _schema):
    from app.jobs.tenant_safe_job import run_for_each_tenant
    from app.models.event_log import EventLogRecord
    from app.services.event_log_service import relay_outbox_batch

    org_a, rows_a = _org_with_outbox_rows(app, 6)
    org_b, rows_b = _org_with_outbox_rows(app, 6)
    runs = []
    errors = []
    barrier = threading.Barrier(2)

    def run():
        try:
            barrier.wait(timeout=30)
            runs.append(
                run_for_each_tenant(
                    app,
                    "event-log-relay",
                    lambda _org_id: relay_outbox_batch(),
                    organization_ids=[org_a, org_b],
                    use_lock=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors
    assert [run.failed for run in runs] == [[], []] or all(not run.failed for run in runs)

    with app.app_context():
        for org_id, outbox_ids in ((org_a, rows_a), (org_b, rows_b)):
            relayed = db.session.execute(
                db.text(
                    "SELECT outbox_event_id, count(*) FROM event_log "
                    "WHERE organization_id = :org AND outbox_event_id = ANY(:ids) "
                    "GROUP BY outbox_event_id"
                ),
                {"org": org_id, "ids": outbox_ids},
            ).all()
            assert sorted(row[0] for row in relayed) == sorted(outbox_ids)
            assert all(row[1] == 1 for row in relayed)
            ordinals = [
                r[0]
                for r in db.session.execute(
                    db.text("SELECT ordinal FROM event_log WHERE organization_id = :org"),
                    {"org": org_id},
                ).all()
            ]
            assert len(ordinals) == len(set(ordinals))

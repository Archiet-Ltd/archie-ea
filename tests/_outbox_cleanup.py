"""Test-teardown helper: remove an organisation's transformation outbox rows.

``transformation_outbox_events`` is append-only (a BEFORE UPDATE OR DELETE
trigger rejects every delete), and its ``organization_id`` foreign key cascades.
Since every ArchiMate element write queues an outbox row, any test that commits
a mirrored domain row (an application, a capability, ...) and then deletes the
organisation hits the trigger through the cascade and fails at teardown.

The trigger's own ``archie.signed_envelope_repair`` escape hatch does not help
here: it lets the trigger return NEW, which is NULL for a DELETE, so the row is
silently skipped. ``session_replication_role = replica`` is the switch that
stops user triggers firing, and it is transaction-local, so it is turned on for
the one DELETE below and off again straight after.

Call it after the test's own deletes and before deleting the organisation.
"""

from sqlalchemy import text


def purge_outbox_events(session, org_ids):
    """Delete the outbox rows of ``org_ids`` and commit. Needs a superuser role."""
    org_ids = [int(org_id) for org_id in org_ids if org_id is not None]
    if not org_ids:
        return
    session.execute(text("SET LOCAL session_replication_role = replica"))
    try:
        session.execute(
            text("DELETE FROM transformation_outbox_events WHERE organization_id = ANY(:ids)"),
            {"ids": org_ids},
        )
    finally:
        session.execute(text("SET LOCAL session_replication_role = DEFAULT"))
    session.commit()

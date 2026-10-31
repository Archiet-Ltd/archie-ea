"""The guard that admits deleting an organisation's own append-only rows.

Each of the delete-refusing trigger functions admits exactly one thing it
did not before — a DELETE, never an UPDATE, while the transaction-local marker
``archie.deleting_organisation`` names the row's own organisation. Every other mutation is
refused exactly as before. These tests exercise the shared helper
(``archie_org_deletion_admits``) directly against one guarded table
(``transformation_outbox_events``), at the database level, with no application code in the way.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


def _insert_outbox_event(db_session, org_id):
    """Insert one minimal, valid transformation_outbox_events row for ``org_id``."""
    event_id = str(uuid.uuid4())
    db_session.execute(
        text(
            "INSERT INTO transformation_outbox_events "
            "(organization_id, event_id, ordinal, event_type, entity_type, entity_id, payload_json) "
            "VALUES (:org_id, :event_id, 0, 'test.guard.probe', 'TestProbe', 0, '{}'::json)"
        ),
        {"org_id": org_id, "event_id": event_id},
    )
    row_id = db_session.execute(
        text("SELECT id FROM transformation_outbox_events WHERE event_id = :event_id"),
        {"event_id": event_id},
    ).scalar_one()
    return row_id


def _clear_marker(db_session):
    db_session.execute(text("SELECT set_config('archie.deleting_organisation', '', true)"))


def _set_marker(db_session, org_id):
    db_session.execute(
        text("SELECT set_config('archie.deleting_organisation', :marker, true)"),
        {"marker": str(org_id)},
    )


def test_delete_with_no_marker_is_refused(db_session, make_org):
    org = make_org("guard-no-marker")
    row_id = _insert_outbox_event(db_session, org.id)
    _clear_marker(db_session)

    with pytest.raises(Exception, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text("DELETE FROM transformation_outbox_events WHERE id = :id"),
                {"id": row_id},
            )

    assert db_session.execute(
        text("SELECT 1 FROM transformation_outbox_events WHERE id = :id"), {"id": row_id}
    ).first() is not None, "the row was removed although no marker admitted it"


def test_delete_with_marker_set_to_another_organisation_is_refused(db_session, make_org):
    org = make_org("guard-wrong-org")
    other = make_org("guard-wrong-org-other")
    row_id = _insert_outbox_event(db_session, org.id)
    _set_marker(db_session, other.id)

    with pytest.raises(Exception, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text("DELETE FROM transformation_outbox_events WHERE id = :id"),
                {"id": row_id},
            )

    _clear_marker(db_session)
    assert db_session.execute(
        text("SELECT 1 FROM transformation_outbox_events WHERE id = :id"), {"id": row_id}
    ).first() is not None, "the row was removed although the marker named a different organisation"


def test_update_with_the_marker_set_is_still_refused(db_session, make_org):
    """The marker admits a DELETE; it never admits an UPDATE."""
    org = make_org("guard-update")
    row_id = _insert_outbox_event(db_session, org.id)
    _set_marker(db_session, org.id)

    with pytest.raises(Exception, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "UPDATE transformation_outbox_events SET event_type = 'changed' "
                    "WHERE id = :id"
                ),
                {"id": row_id},
            )

    _clear_marker(db_session)
    row = db_session.execute(
        text("SELECT event_type FROM transformation_outbox_events WHERE id = :id"), {"id": row_id}
    ).one()
    assert row[0] == "test.guard.probe", "the marker admitted an UPDATE; it must admit a DELETE only"


def test_delete_of_an_arb_evidence_snapshot_is_admitted_by_the_same_marker(db_session, make_org):
    """A second guarded table (a different file, a different function) honours the same marker:
    archie_reject_transformation_mutation and reject_archie_evidence_mutation are not the same
    function, so this is not just re-testing the first guard."""
    from app.models.arb_submission_evidence import ARBSubmissionEvidenceSnapshot

    org = make_org("guard-evidence")
    snapshot = ARBSubmissionEvidenceSnapshot(organization_id=org.id)
    db_session.add(snapshot)
    db_session.flush()
    snapshot_id = snapshot.id

    _clear_marker(db_session)
    with pytest.raises(Exception, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text("DELETE FROM arb_submission_evidence_snapshots WHERE id = :id"),
                {"id": snapshot_id},
            )

    _set_marker(db_session, org.id)
    db_session.execute(
        text("DELETE FROM arb_submission_evidence_snapshots WHERE id = :id"), {"id": snapshot_id}
    )
    _clear_marker(db_session)
    assert db_session.execute(
        text("SELECT 1 FROM arb_submission_evidence_snapshots WHERE id = :id"), {"id": snapshot_id}
    ).first() is None


def test_delete_of_a_null_organisation_row_is_refused_even_with_a_marker_set(db_session, make_org):
    """A platform row with no organisation names no organisation's deletion.

    archie_org_deletion_admits(NULL) must refuse, not admit: a guarded table's row with a NULL
    organization_id belongs to no organisation's delete, however the marker is set.
    """
    from app.models.arb_submission_evidence import ARBSubmissionEvidenceSnapshot

    org = make_org("guard-null-org")
    snapshot = ARBSubmissionEvidenceSnapshot(organization_id=None)
    db_session.add(snapshot)
    db_session.flush()
    snapshot_id = snapshot.id

    _set_marker(db_session, org.id)
    with pytest.raises(Exception, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text("DELETE FROM arb_submission_evidence_snapshots WHERE id = :id"),
                {"id": snapshot_id},
            )

    _clear_marker(db_session)
    assert db_session.execute(
        text("SELECT 1 FROM arb_submission_evidence_snapshots WHERE id = :id"), {"id": snapshot_id}
    ).first() is not None, "a NULL-organisation row was deleted by a marker naming a real organisation"


def test_delete_with_the_marker_set_to_its_own_organisation_is_admitted(db_session, make_org):
    org = make_org("guard-admitted")
    row_id = _insert_outbox_event(db_session, org.id)
    _set_marker(db_session, org.id)

    db_session.execute(
        text("DELETE FROM transformation_outbox_events WHERE id = :id"), {"id": row_id}
    )
    _clear_marker(db_session)

    assert db_session.execute(
        text("SELECT 1 FROM transformation_outbox_events WHERE id = :id"), {"id": row_id}
    ).first() is None, "the marker did not admit the delete of its own organisation's row"

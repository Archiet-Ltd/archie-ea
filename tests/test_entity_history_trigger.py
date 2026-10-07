"""The generic entity_history trigger and its backfill.

The trigger/table are schema objects, not something a per-test rolled-back
transaction should create and discard every time (and a savepoint rollback
would undo the CREATE TRIGGER along with everything else) -- installed once
per test session directly on the engine, like the schema itself.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


@pytest.fixture(scope="session", autouse=True)
def _entity_history_trigger(app, _schema):
    """Install the entity_history trigger once, directly on the engine."""
    from app import db
    from app.commands.apply_entity_history_trigger import FUNCTION_SQL, TABLES

    with app.app_context():
        conn = db.engine.connect()
        try:
            conn.execute(text(FUNCTION_SQL))
            for table in TABLES:
                conn.execute(text(f'DROP TRIGGER IF EXISTS entity_history_trg ON "{table}"'))
                conn.execute(text(f"""
                    CREATE TRIGGER entity_history_trg
                    BEFORE INSERT OR UPDATE OR DELETE ON "{table}"
                    FOR EACH ROW
                    EXECUTE FUNCTION entity_history_record_version()
                """))
            conn.commit()
        finally:
            conn.close()
    yield


def _element(db_session, org, name="El"):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                           organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org, source, target):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(type="serving", source_id=source.id, target_id=target.id,
                                 organization_id=org.id)
    db_session.add(rel)
    db_session.flush()
    return rel


def test_insert_leaves_one_open_version(app, db_session, make_org):
    org = make_org("eh-insert")
    el = _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")

    rows = db_session.execute(text(
        "SELECT valid_to FROM entity_history WHERE table_name='archimate_elements' AND record_id=:id"
    ), {"id": el.id}).fetchall()

    assert len(rows) == 1
    assert rows[0][0] is None


def test_two_updates_leave_three_non_overlapping_versions(app, db_session, make_org):
    org = make_org("eh-double-update")
    el = _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")

    db_session.execute(text("UPDATE archimate_elements SET name = name || '-a' WHERE id=:id"), {"id": el.id})
    db_session.commit()
    db_session.execute(text("UPDATE archimate_elements SET name = name || '-b' WHERE id=:id"), {"id": el.id})
    db_session.commit()

    rows = db_session.execute(text(
        "SELECT valid_from, valid_to FROM entity_history "
        "WHERE table_name='archimate_elements' AND record_id=:id ORDER BY valid_from"
    ), {"id": el.id}).fetchall()

    assert len(rows) == 3
    # Non-overlapping: each row's valid_to equals the next row's valid_from.
    for i in range(len(rows) - 1):
        assert rows[i][1] == rows[i + 1][0]
    # Exactly one open (current) version.
    open_count = sum(1 for r in rows if r[1] is None)
    assert open_count == 1


def test_two_updates_in_one_transaction_do_not_produce_a_zero_length_version(
    app, db_session, make_org
):
    """statement_timestamp(), not NOW()/transaction_timestamp(): two updates
    with no intervening commit (the same database transaction) must still
    get two distinct timestamps, or the version the first update opened
    closes at the exact instant it opened."""
    org = make_org("eh-same-transaction")
    el = _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")

    db_session.execute(text("UPDATE archimate_elements SET name = name || '-a' WHERE id=:id"), {"id": el.id})
    db_session.execute(text("UPDATE archimate_elements SET name = name || '-b' WHERE id=:id"), {"id": el.id})
    db_session.commit()

    rows = db_session.execute(text(
        "SELECT valid_from, valid_to FROM entity_history "
        "WHERE table_name='archimate_elements' AND record_id=:id ORDER BY valid_from"
    ), {"id": el.id}).fetchall()

    assert len(rows) == 3
    for row in rows:
        if row[1] is not None:
            assert row[0] != row[1], "zero-length version: valid_from == valid_to"


def test_as_of_snapshot_matches_the_state_recorded_at_that_time(app, db_session, make_org):
    """A test that the as-of answer for a date equals a snapshot taken on
    that date -- proven directly against entity_history rather than a PR 2
    service that does not exist yet in this PR."""
    org = make_org("eh-as-of")
    el = _element(db_session, org, name="Original")
    original_name = el.name

    db_session.execute(text("UPDATE archimate_elements SET name = 'Changed' WHERE id=:id"), {"id": el.id})
    db_session.commit()

    version_at_creation = db_session.execute(text(
        "SELECT snapshot->>'name' FROM entity_history "
        "WHERE table_name='archimate_elements' AND record_id=:id AND valid_to IS NOT NULL"
    ), {"id": el.id}).scalar()

    assert version_at_creation == original_name


def test_entity_history_excludes_a_foreign_organisations_rows(app, db_session, make_org):
    """Two-organisation test: entity_history is TenantMixin, so the ambient
    ORM filter (not a raw query) must exclude another organisation's
    versions -- the same fence every other tenant-scoped table gets."""
    from flask import g

    from app.models.entity_history import EntityHistory

    org_a, org_b = make_org("eh-iso-a"), make_org("eh-iso-b")
    el_a = _element(db_session, org_a, name=f"El-A-{uuid.uuid4().hex[:6]}")
    el_b = _element(db_session, org_b, name=f"SECRET-El-B-{uuid.uuid4().hex[:6]}")

    g.current_org_id = org_a.id
    visible_ids = {h.record_id for h in EntityHistory.query.filter_by(table_name="archimate_elements").all()}
    assert el_a.id in visible_ids
    assert el_b.id not in visible_ids


def test_backfill_seeds_one_open_version_per_pre_existing_row_with_no_history(app, db_session, make_org):
    """backfill-entity-history seeds a row that predates the trigger (no
    entity_history row at all yet) with one open version; recorded_at is
    NULL ("unknown") with no matching audit-log entry."""
    from click.testing import CliRunner

    from app.commands.backfill_entity_history import backfill_entity_history

    org = make_org("eh-backfill")
    el = _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")
    db_session.commit()
    el_id = el.id

    # This row already has a trigger-written open version (the INSERT
    # above). Simulate "predates the trigger" by deleting it, the same
    # state a row created before apply-entity-history-trigger first ran
    # would be in.
    db_session.execute(text(
        "DELETE FROM entity_history WHERE table_name='archimate_elements' AND record_id=:id"
    ), {"id": el_id})
    db_session.commit()

    runner = CliRunner()
    result = runner.invoke(backfill_entity_history, [])
    assert result.exit_code == 0, result.output

    row = db_session.execute(text(
        "SELECT valid_to, recorded_at, source FROM entity_history "
        "WHERE table_name='archimate_elements' AND record_id=:id"
    ), {"id": el_id}).fetchone()

    assert row is not None
    assert row[0] is None  # open
    assert row[1] is None  # no audit-log entry for this test row -> unknown
    assert row[2] == "backfill"


def test_backfill_is_idempotent(app, db_session, make_org):
    from click.testing import CliRunner

    from app.commands.backfill_entity_history import backfill_entity_history

    org = make_org("eh-backfill-idempotent")
    _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")
    db_session.commit()

    runner = CliRunner()
    first = runner.invoke(backfill_entity_history, [])
    second = runner.invoke(backfill_entity_history, ["--dry-run"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "would seed 0 version" in second.output


# ---------------------------------------------------------------------------
# pr311-v1 final-check fix round
# ---------------------------------------------------------------------------


def test_relationship_insert_leaves_one_open_version(app, db_session, make_org):
    """The brief's scope includes relationships, not just elements."""
    org = make_org("eh-rel-insert")
    source = _element(db_session, org)
    target = _element(db_session, org)
    rel = _relationship(db_session, org, source, target)

    rows = db_session.execute(text(
        "SELECT valid_to FROM entity_history WHERE table_name='archimate_relationships' AND record_id=:id"
    ), {"id": rel.id}).fetchall()

    assert len(rows) == 1
    assert rows[0][0] is None


def test_relationship_two_updates_leave_three_non_overlapping_versions_with_the_right_org(
    app, db_session, make_org
):
    org = make_org("eh-rel-double-update")
    source = _element(db_session, org)
    target = _element(db_session, org)
    rel = _relationship(db_session, org, source, target)

    db_session.execute(text("UPDATE archimate_relationships SET type = 'flow' WHERE id=:id"), {"id": rel.id})
    db_session.commit()
    db_session.execute(text("UPDATE archimate_relationships SET type = 'access' WHERE id=:id"), {"id": rel.id})
    db_session.commit()

    rows = db_session.execute(text(
        "SELECT valid_from, valid_to, organization_id FROM entity_history "
        "WHERE table_name='archimate_relationships' AND record_id=:id ORDER BY valid_from"
    ), {"id": rel.id}).fetchall()

    assert len(rows) == 3
    for i in range(len(rows) - 1):
        assert rows[i][1] == rows[i + 1][0]
    assert sum(1 for r in rows if r[1] is None) == 1
    assert all(r[2] == org.id for r in rows)


def test_delete_closes_the_current_open_version(app, db_session, make_org):
    """A delete is a change -- the open version must close, not
    stay open and silently misrepresent a deleted row as still current."""
    org = make_org("eh-delete")
    el = _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")
    el_id = el.id
    db_session.commit()

    before = db_session.execute(text(
        "SELECT COUNT(*) FROM entity_history WHERE table_name='archimate_elements' "
        "AND record_id=:id AND valid_to IS NULL"
    ), {"id": el_id}).scalar()
    assert before == 1

    db_session.execute(text("DELETE FROM archimate_elements WHERE id=:id"), {"id": el_id})
    db_session.commit()

    after_open = db_session.execute(text(
        "SELECT COUNT(*) FROM entity_history WHERE table_name='archimate_elements' "
        "AND record_id=:id AND valid_to IS NULL"
    ), {"id": el_id}).scalar()
    after_total = db_session.execute(text(
        "SELECT COUNT(*) FROM entity_history WHERE table_name='archimate_elements' AND record_id=:id"
    ), {"id": el_id}).scalar()

    assert after_open == 0, "the delete must close the open version, not leave it current"
    assert after_total == 1, "no new version is opened for a row that no longer exists"


def test_insert_stamps_the_base_rows_own_time_columns(app, db_session, make_org):
    """valid_from/recorded_at on archimate_elements itself (not
    just the entity_history copy) must be populated, or the brief's owned
    columns are dead."""
    org = make_org("eh-base-stamp")
    el = _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")

    row = db_session.execute(text(
        "SELECT valid_from, valid_to, recorded_at, superseded_at FROM archimate_elements WHERE id=:id"
    ), {"id": el.id}).fetchone()

    assert row.valid_from is not None
    assert row.valid_to is None
    assert row.recorded_at is not None
    assert row.superseded_at is None


def test_update_restamps_the_base_rows_own_time_columns(app, db_session, make_org):
    org = make_org("eh-base-restamp")
    el = _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")
    el_id = el.id
    db_session.commit()

    first = db_session.execute(text(
        "SELECT valid_from FROM archimate_elements WHERE id=:id"
    ), {"id": el_id}).scalar()

    db_session.execute(text("UPDATE archimate_elements SET name = name || '-x' WHERE id=:id"), {"id": el_id})
    db_session.commit()

    second_valid_from, second_valid_to = db_session.execute(text(
        "SELECT valid_from, valid_to FROM archimate_elements WHERE id=:id"
    ), {"id": el_id}).fetchone()

    assert second_valid_from != first
    assert second_valid_to is None


def test_backfill_does_not_hide_a_pre_existing_row_from_an_earlier_as_of_date(
    app, db_session, make_org
):
    """Backfilling a row with no audit-log entry must not make it
    look like it started existing at backfill time -- it must stay visible
    to an as-of date from before the backfill ran."""
    from click.testing import CliRunner

    from app.commands.backfill_entity_history import backfill_entity_history

    org = make_org("eh-backfill-asof")
    el = _element(db_session, org, name=f"El-{uuid.uuid4().hex[:6]}")
    db_session.commit()
    el_id = el.id

    db_session.execute(text(
        "DELETE FROM entity_history WHERE table_name='archimate_elements' AND record_id=:id"
    ), {"id": el_id})
    # Disable the trigger for this one simulation step: an ordinary UPDATE
    # to archimate_elements would otherwise be treated as a real change,
    # overwriting our NULLs right back to statement_timestamp() and
    # re-seeding entity_history with a trigger-sourced row -- which would
    # make backfill see the row as already covered and skip it entirely,
    # defeating the "predates the trigger" state this test means to set up.
    db_session.execute(text("ALTER TABLE archimate_elements DISABLE TRIGGER entity_history_trg"))
    db_session.execute(text(
        "UPDATE archimate_elements SET valid_from = NULL, recorded_at = NULL WHERE id=:id"
    ), {"id": el_id})
    db_session.execute(text("ALTER TABLE archimate_elements ENABLE TRIGGER entity_history_trg"))
    db_session.commit()

    runner = CliRunner()
    result = runner.invoke(backfill_entity_history, [])
    assert result.exit_code == 0, result.output

    yesterday = "2000-01-01"  # long before this test ever ran
    visible_long_ago = db_session.execute(text(
        "SELECT COUNT(*) FROM entity_history WHERE table_name='archimate_elements' "
        "AND record_id=:id AND valid_from <= :asof AND (valid_to IS NULL OR valid_to > :asof)"
    ), {"id": el_id, "asof": yesterday}).scalar()

    assert visible_long_ago == 1, "an unknown-start row must be visible to a date long before it was backfilled"

    base_valid_from, base_recorded_at = db_session.execute(text(
        "SELECT valid_from, recorded_at FROM archimate_elements WHERE id=:id"
    ), {"id": el_id}).fetchone()
    assert base_recorded_at is None
    assert base_valid_from is not None  # -infinity, not NULL and not "now"


def test_entity_history_accepts_a_row_with_an_invalid_organization_id(
    app, db_session, make_org
):
    """entity_history.organization_id deliberately omits a ForeignKey
    constraint (app/models/entity_history.py:40-51) because pre-existing
    archimate_elements rows may carry an organization_id that does not
    exist in the organizations table.  This test proves the trigger can
    still write to entity_history without FK violation when the source
    row has an invalid org_id."""
    from sqlalchemy import text as _text

    org = make_org("eh-invalid-org")
    el = _element(db_session, org, name=f"El-invalid-org-{uuid.uuid4().hex[:6]}")
    el_id = el.id
    db_session.commit()

    # Drop the FK constraint temporarily (transactional DDL -- the test
    # transaction rolls back, restoring the constraint).  This simulates
    # the drift that existed before the FK was ever enforced: pre-existing
    # rows with an organization_id that does not match any organization.
    db_session.execute(_text(
        "ALTER TABLE archimate_elements DROP CONSTRAINT archimate_elements_organization_id_fkey"
    ))
    db_session.execute(_text(
        "UPDATE archimate_elements SET organization_id = :bad_org WHERE id = :id"
    ), {"bad_org": 999999, "id": el_id})
    db_session.commit()

    # Now trigger an UPDATE on the row -- the trigger must write to
    # entity_history without raising ForeignKeyViolation.
    db_session.execute(_text(
        "UPDATE archimate_elements SET name = name || '-updated' WHERE id = :id"
    ), {"id": el_id})
    db_session.commit()

    rows = db_session.execute(_text(
        "SELECT organization_id, valid_to FROM entity_history "
        "WHERE table_name='archimate_elements' AND record_id=:id ORDER BY valid_from"
    ), {"id": el_id}).fetchall()

    assert len(rows) >= 2  # insert version + update version
    # The first version (from the INSERT) has the original valid org_id;
    # the second version (from the UPDATE after DROP CONSTRAINT) must
    # carry the invalid org_id without FK error.
    assert rows[0][0] == org.id
    assert rows[1][0] == 999999
    # Exactly one open version.
    assert sum(1 for r in rows if r[1] is None) == 1

"""Deleting an organisation finds its rows by the ``organization_id`` column, not by a foreign key.

A database whose column was added after the table (``reconcile-schema`` adds columns, never
constraints) holds no foreign key from that table to ``organizations``. Its rows still belong to
the organisation and must go with it, and the other organisation's rows in the same table stay.
"""

import uuid

import pytest
from sqlalchemy import text


@pytest.fixture
def keyless_table(app):
    """A tenant table whose ``organization_id`` has no foreign key, as on a reconciled database."""
    from app import db

    name = "keyless_probe_%s" % uuid.uuid4().hex[:8]
    with app.app_context():
        db.session.execute(text(
            'CREATE TABLE "%s" (id serial PRIMARY KEY, organization_id integer NOT NULL, label text)' % name
        ))
        db.session.commit()
    yield name
    with app.app_context():
        db.session.rollback()
        db.session.execute(text('DROP TABLE IF EXISTS "%s"' % name))
        db.session.commit()
        db.session.remove()


def _tables_with_organization_id(sess):
    return [
        row[0] for row in sess.execute(text(
            "SELECT c.relname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE a.attname = 'organization_id' AND a.attnum > 0 AND NOT a.attisdropped "
            "AND n.nspname = current_schema() AND c.relkind IN ('r', 'p') AND c.relname <> 'organizations'"
        ))
    ]


def test_deleting_an_organisation_removes_rows_of_a_table_with_no_foreign_key(app, keyless_table):
    from app import db
    from app.models.organization import Organization
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        doomed = Organization(name="Doomed %s" % marker, slug="doomed-%s" % marker)
        kept = Organization(name="Kept %s" % marker, slug="kept-%s" % marker)
        db.session.add_all([doomed, kept])
        db.session.flush()
        ids = {"doomed": doomed.id, "kept": kept.id}
        for key, count in (("doomed", 3), ("kept", 2)):
            for n in range(count):
                db.session.execute(
                    text('INSERT INTO "%s" (organization_id, label) VALUES (:o, :l)' % keyless_table),
                    {"o": ids[key], "l": "%s %d" % (key, n)},
                )
        db.session.commit()
        db.session.remove()

    try:
        with app.app_context():
            assert not db.session.execute(text(
                "SELECT 1 FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'"
            ), {"t": keyless_table}).first(), "the probe table must carry no foreign key"
            OrganizationDeletionService.delete_organization(ids["doomed"])
            db.session.remove()

        with app.app_context():
            assert db.session.get(Organization, ids["doomed"]) is None
            for table in _tables_with_organization_id(db.session):
                left = db.session.execute(
                    text('SELECT count(*) FROM "%s" WHERE organization_id = :o' % table), {"o": ids["doomed"]}
                ).scalar()
                assert left == 0, "%s still holds %d rows of the deleted organisation" % (table, left)
            kept_rows = db.session.execute(
                text('SELECT label FROM "%s" WHERE organization_id = :o ORDER BY label' % keyless_table),
                {"o": ids["kept"]},
            ).scalars().all()
            assert kept_rows == ["kept 0", "kept 1"]
            assert db.session.get(Organization, ids["kept"]) is not None
            db.session.remove()
    finally:
        with app.app_context():
            for key in ("kept", "doomed"):
                if db.session.get(Organization, ids[key]) is not None:
                    OrganizationDeletionService.delete_organization(ids[key])
            db.session.remove()

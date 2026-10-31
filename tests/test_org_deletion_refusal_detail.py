"""A refused deletion says which organisation holds the blocking records, what kind, and gives examples.

Also pinned here: the refusals that happen before any write for a link of several columns and for
a missing account role, and the text the migration restores when it is rolled back. Every test uses
two real organisations and compares the whole database before and after.
"""

import re
import uuid

import pytest
from sqlalchemy import text

from tests.test_org_deletion_every_reference import _element_in, _refused
from tests.test_org_deletion_writes_only_inside import _cleanup, _snapshot, _two_organisations


def test_a_person_of_another_organisation_pointing_into_the_deleted_one_names_that_organisation(app):
    from app import db
    from app.models.business_layer import BusinessActor
    from app.models.user import User

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        actor = BusinessActor(name="A actor %s" % marker, organization_id=a.id)
        db.session.add(actor)
        db.session.flush()
        person = User(first_name="Dana", last_name="Bee", email="dana-%s@example.com" % marker,
                      organization_id=b.id)
        db.session.add(person)
        db.session.flush()
        db.session.execute(text("UPDATE users SET business_unit_id = :e WHERE id = :u"),
                           {"e": actor.id, "u": person.id})
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "marker": marker, "person": person.id}
        ids["before"] = _snapshot(db)
        db.session.remove()
    try:
        message = _refused(app, ids, "Org B %s: 1 person" % marker)
        assert "Shared platform records" not in message, message
        assert "dana-%s@example.com" % marker in message, message
    finally:
        with app.app_context():
            db.session.execute(text("UPDATE users SET business_unit_id = NULL WHERE id = :u"), {"u": ids["person"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def test_the_refusal_names_each_kind_with_its_count_and_at_most_three_examples(app):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        element = _element_in(db, a.id, "A element %s" % marker)
        apps = [ApplicationComponent(name="B app %d %s" % (n, marker), organization_id=b.id,
                                     archimate_element_id=element.id) for n in range(5)]
        db.session.add_all(apps)
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "marker": marker, "apps": [x.id for x in apps]}
        ids["before"] = _snapshot(db)
        db.session.remove()
    try:
        message = _refused(app, ids, "Org B %s: 5 application components (for example " % marker)
        assert len(re.findall(r'application component "B app \d', message)) == 3, message
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM application_components WHERE id = ANY(:i)"), {"i": ids["apps"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def test_the_rolled_back_migration_restores_the_function_text_exactly():
    import importlib.util
    import pathlib

    from app.models.transformation_db_guards import _ORG_DELETION_ADMITS_SQL

    path = pathlib.Path(__file__).resolve().parents[1] / "migrations/versions/20261031_org_deleting_row_own.py"
    spec = importlib.util.spec_from_file_location("org_deleting_row_own", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    added = (
        "    -- And this transaction must have written that row: a row another transaction wrote (committed\n"
        "    -- or not) does not count. The row records the id of the transaction that wrote it, which\n"
        "    -- stays the same inside a savepoint.\n"
    )
    assert added in _ORG_DELETION_ADMITS_SQL
    previous = _ORG_DELETION_ADMITS_SQL.replace(added, "").replace("          AND written_by_txid = txid_current()\n", "")
    assert module._PREVIOUS_SQL == previous


@pytest.fixture
def composite_tables(app):
    """A table of the organisation's, and one that hangs from it only through a two-column key."""
    from app import db

    suffix = uuid.uuid4().hex[:8]
    parent, child = "zz_parent_%s" % suffix, "zz_child_%s" % suffix
    with app.app_context():
        db.session.execute(text(
            'CREATE TABLE "%s" (id integer NOT NULL, organization_id integer NOT NULL REFERENCES organizations(id), '
            "PRIMARY KEY (id, organization_id))" % parent))
        db.session.execute(text(
            'CREATE TABLE "%s" (id serial PRIMARY KEY, parent_id integer NOT NULL, parent_org integer NOT NULL, '
            'FOREIGN KEY (parent_id, parent_org) REFERENCES "%s" (id, organization_id))' % (child, parent)))
        db.session.commit()
        db.session.remove()
    yield parent, child
    with app.app_context():
        db.session.rollback()
        db.session.execute(text('DROP TABLE IF EXISTS "%s"' % child))
        db.session.execute(text('DROP TABLE IF EXISTS "%s"' % parent))
        db.session.commit()
        db.session.remove()


def test_a_row_hanging_from_the_organisation_through_a_composite_key_is_refused_before_any_write(
        app, composite_tables):
    from app import db

    parent, child = composite_tables
    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        db.session.execute(text('INSERT INTO "%s" (id, organization_id) VALUES (1, :o)' % parent), {"o": a.id})
        db.session.execute(text('INSERT INTO "%s" (parent_id, parent_org) VALUES (1, :o)' % child), {"o": a.id})
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "marker": marker}
        ids["before"] = _snapshot(db)
        db.session.remove()
    try:
        _refused(app, ids, "depend on it through a link of several columns")
    finally:
        with app.app_context():
            db.session.execute(text('DELETE FROM "%s"' % child))
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def test_a_missing_account_role_for_a_detached_person_refuses_before_any_write(app, monkeypatch):
    from app import db
    from app.models.org_role import OrgRole
    from app.services import org_deletion_service
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, sole, _ = _two_organisations(db, marker)
        db.session.add(OrgRole(organization_id=b.id, user_id=sole.id, role="viewer"))
        db.session.commit()
        ids = {"a": a.id, "b": b.id}
        before = _snapshot(db)
        db.session.remove()
    monkeypatch.setitem(org_deletion_service._ACCOUNT_ROLE, "viewer", "Missing role %s" % marker)
    try:
        with app.app_context():
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["a"])
            message = str(refused.value)
            db.session.remove()
        assert 'the account role "Missing role %s"' % marker in message, message
        assert "Nothing was deleted." in message, message
        with app.app_context():
            assert _snapshot(db) == before, "a refused deletion changed the database"
            db.session.remove()
    finally:
        monkeypatch.undo()
        _cleanup(app, ids["a"], ids["b"])

"""Deleting an organisation writes nothing in any other organisation and moves nobody into one.

  - a person who would be removed and is named in a NOT NULL column of another organisation's row
    refuses the deletion;
  - a person who also belongs to another organisation is detached and that organisation's rows
    stay exactly as they were;
  - an organisation whose people nobody else names is deleted in full.
"""

import json
import uuid

import pytest
from sqlalchemy import text


def _rows_of(db, org_id, leave_out=()):
    """Every row of every table with an organization_id column, for one organisation, as text."""
    tables = [name for (name,) in db.session.execute(text(
        "SELECT c.relname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE a.attname = 'organization_id' "
        "AND a.attnum > 0 AND NOT a.attisdropped AND n.nspname = current_schema() AND c.relkind = 'r' "
        "AND c.relname <> 'organizations' ORDER BY 1")) if name not in leave_out]
    snapshot = {}
    for table in tables:
        rows = db.session.execute(
            text('SELECT t::text FROM "%s" t WHERE organization_id = :o ORDER BY 1' % table), {"o": org_id}
        ).scalars().all()
        if rows:
            snapshot[table] = rows
    return json.dumps(snapshot, sort_keys=True)


# The detached person's own home row (and its audit entry) legitimately changes; nothing else may.
MOVED = ("users", "soc2_audit_log")


def _cleanup(app, *org_ids):
    from app import db
    from app.models.organization import Organization
    from app.services.org_deletion_service import OrganizationDeletionService

    with app.app_context():
        db.session.rollback()
        for org_id in org_ids:
            if db.session.get(Organization, org_id) is not None:
                OrganizationDeletionService.delete_organization(org_id)
        db.session.remove()


def test_a_person_named_in_a_required_column_of_another_organisation_refuses_the_deletion(app):
    from app import db
    from app.models.models import Notification
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a = Organization(name="Org A %s" % marker, slug="org-a-%s" % marker)
        b = Organization(name="Org B %s" % marker, slug="org-b-%s" % marker)
        db.session.add_all([a, b])
        db.session.flush()
        sole = User(email="sole-%s@example.com" % marker, organization_id=a.id)
        db.session.add(sole)
        db.session.flush()
        db.session.add(Notification(user_id=sole.id, message="B's note %s" % marker, organization_id=b.id))
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "sole": sole.id}
        before = _rows_of(db, b.id)
        db.session.remove()

    try:
        with app.app_context():
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["a"])
            assert "Org B %s" % marker in str(refused.value)
            db.session.remove()
        with app.app_context():
            assert _rows_of(db, ids["b"]) == before
            sole = db.session.get(User, ids["sole"])
            assert sole is not None and sole.organization_id == ids["a"]
            assert db.session.get(Organization, ids["a"]) is not None
            db.session.remove()
    finally:
        _cleanup(app, ids["b"], ids["a"])


def test_a_person_who_also_belongs_to_another_organisation_is_detached_and_its_rows_are_untouched(app):
    from app import db
    from app.models.models import Notification
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a = Organization(name="Org A %s" % marker, slug="org-a-%s" % marker)
        b = Organization(name="Org B %s" % marker, slug="org-b-%s" % marker)
        db.session.add_all([a, b])
        db.session.flush()
        shared = User(email="shared-%s@example.com" % marker, organization_id=a.id)
        db.session.add(shared)
        db.session.flush()
        db.session.add_all([
            OrgRole(organization_id=b.id, user_id=shared.id, role="viewer"),
            Notification(user_id=shared.id, message="B's note %s" % marker, organization_id=b.id),
        ])
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "shared": shared.id}
        before = _rows_of(db, b.id, MOVED)
        db.session.remove()

    try:
        with app.app_context():
            message = OrganizationDeletionService.delete_organization(ids["a"])
            db.session.remove()
        assert "1 person who also belongs elsewhere was detached" in message, message
        with app.app_context():
            assert db.session.get(Organization, ids["a"]) is None
            person = db.session.get(User, ids["shared"])
            assert person is not None and person.organization_id == ids["b"]
            assert _rows_of(db, ids["b"], MOVED) == before
            db.session.remove()
    finally:
        _cleanup(app, ids["a"], ids["b"])


def test_an_organisation_whose_people_nobody_else_names_is_deleted_in_full(app):
    from app import db
    from app.models.models import Notification
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a = Organization(name="Org A %s" % marker, slug="org-a-%s" % marker)
        b = Organization(name="Org B %s" % marker, slug="org-b-%s" % marker)
        db.session.add_all([a, b])
        db.session.flush()
        sole = User(email="sole-%s@example.com" % marker, organization_id=a.id)
        bystander = User(email="bystander-%s@example.com" % marker, organization_id=b.id)
        db.session.add_all([sole, bystander])
        db.session.flush()
        db.session.add_all([
            Notification(user_id=sole.id, message="A's note %s" % marker, organization_id=a.id),
            Notification(user_id=bystander.id, message="B's note %s" % marker, organization_id=b.id),
        ])
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "sole": sole.id}
        before = _rows_of(db, b.id)
        db.session.remove()

    try:
        with app.app_context():
            message = OrganizationDeletionService.delete_organization(ids["a"])
            db.session.remove()
        assert "1 person who belonged only to it was removed" in message, message
        with app.app_context():
            assert db.session.get(Organization, ids["a"]) is None
            assert db.session.get(User, ids["sole"]) is None
            assert _rows_of(db, ids["a"]) == "{}"
            assert _rows_of(db, ids["b"]) == before
            db.session.remove()
    finally:
        _cleanup(app, ids["a"], ids["b"])

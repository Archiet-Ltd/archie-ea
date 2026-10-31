"""Deleting an organisation writes only rows that belong to it, whatever table names its people.

A row belongs to the deleted organisation when its plan reaches it: an organisation column equal to
it, or a cascade from one of its rows. Any other row (another organisation's, a platform row, a
row in a table with no organisation column that hangs from either) is never written; if it names a
person who would be removed, the deletion is refused before any write.
"""

import json
import uuid

import pytest
from sqlalchemy import text


def _snapshot(db):
    """Every row of every table, as parsed JSON, keyed by table then by the row's own text."""
    names = db.session.execute(text(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relkind = 'r' ORDER BY 1")).scalars().all()
    snapshot = {}
    for name in names:
        rows = db.session.execute(text('SELECT to_jsonb(t)::text FROM "%s" t' % name)).scalars().all()
        snapshot[name] = {row: json.loads(row) for row in rows}
    return snapshot


def _outside_changes(before, after, org_id, moved_people=()):
    """Rows that changed, appeared or vanished and are not the deleted organisation's own.

    A vanished row of a table with an organisation column must carry that organisation; a vanished
    row of a table without one is reported too, so a test names the rows it expects to go. The one
    ruled change is a detached person's home column.
    """
    changes = []
    for table, old_rows in before.items():
        new_rows = after.get(table, {})
        for text_, row in old_rows.items():
            if text_ in new_rows:
                continue
            if table == "users" and row["id"] in moved_people:
                continue
            if "organization_id" in row and row["organization_id"] == org_id:
                continue
            if table == "organizations" and row["id"] == org_id:
                continue
            changes.append(("vanished", table, row))
        for text_, row in new_rows.items():
            if text_ in old_rows:
                continue
            if table == "users" and row["id"] in moved_people:
                continue
            if table in ("soc2_audit_log", "audit_logs") and row.get("organization_id") is None:
                continue  # the platform record of the deletion
            changes.append(("appeared", table, row))
    return changes


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


def _two_organisations(db, marker):
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization
    from app.models.user import User

    a = Organization(name="Org A %s" % marker, slug="org-a-%s" % marker)
    b = Organization(name="Org B %s" % marker, slug="org-b-%s" % marker)
    db.session.add_all([a, b])
    db.session.flush()
    sole = User(email="sole-%s@example.com" % marker, organization_id=a.id)
    db.session.add(sole)
    element = ArchiMateElement(name="B element %s" % marker, type="BusinessProcess", layer="Business",
                               organization_id=b.id)
    db.session.add(element)
    db.session.flush()
    return a, b, sole, element


def test_a_comment_on_another_organisations_element_naming_a_removed_person_refuses_the_deletion(app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, sole, element = _two_organisations(db, marker)
        db.session.execute(text(
            "INSERT INTO archimate_element_comments (element_id, user_id, comment_text, created_at) "
            "VALUES (:e, :u, :t, now())"), {"e": element.id, "u": sole.id, "t": "B's comment %s" % marker})
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "sole": sole.id}
        before = _snapshot(db)
        db.session.remove()

    try:
        with app.app_context():
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["a"])
            message = str(refused.value)
            assert "Org B %s" % marker in message and "1 person" in message, message
            assert "Nothing was deleted" in message
            db.session.remove()
        with app.app_context():
            assert _snapshot(db) == before
            assert db.session.get(Organization, ids["a"]) is not None
            assert db.session.get(User, ids["sole"]) is not None
            db.session.remove()
    finally:
        _cleanup(app, ids["b"], ids["a"])


def test_a_platform_row_naming_a_removed_person_refuses_the_deletion(app):
    from app import db
    from app.models.models import Notification
    from app.models.organization import Organization
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, sole, _ = _two_organisations(db, marker)
        db.session.add(Notification(user_id=sole.id, message="Platform note %s" % marker, organization_id=None))
        db.session.commit()
        ids = {"a": a.id, "b": b.id}
        before = _snapshot(db)
        db.session.remove()

    try:
        with app.app_context():
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["a"])
            message = str(refused.value)
            assert "shared platform records" in message and "1 person" in message, message
            db.session.remove()
        with app.app_context():
            assert _snapshot(db) == before
            assert db.session.get(Organization, ids["a"]) is not None
            db.session.remove()
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.execute(text("DELETE FROM notifications WHERE message = :m"),
                               {"m": "Platform note %s" % marker})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["b"], ids["a"])


def test_a_detached_person_moves_home_to_the_organisation_where_they_hold_a_role_and_nothing_else_is_written(app):
    from app import db
    from app.models.models import Notification
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, sole, element = _two_organisations(db, marker)
        db.session.add_all([
            OrgRole(organization_id=b.id, user_id=sole.id, role="viewer"),
            Notification(user_id=sole.id, message="B's note %s" % marker, organization_id=b.id),
        ])
        db.session.execute(text(
            "INSERT INTO archimate_element_comments (element_id, user_id, comment_text, created_at) "
            "VALUES (:e, :u, :t, now())"), {"e": element.id, "u": sole.id, "t": "B's comment %s" % marker})
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "sole": sole.id}
        before = _snapshot(db)
        db.session.remove()

    try:
        with app.app_context():
            message = OrganizationDeletionService.delete_organization(ids["a"])
            db.session.remove()
        assert "1 person who also belongs elsewhere was detached" in message, message
        assert "Org B %s" % marker in message, message
        with app.app_context():
            after = _snapshot(db)
            assert db.session.get(Organization, ids["a"]) is None
            assert db.session.get(User, ids["sole"]).organization_id == ids["b"]
            assert _outside_changes(before, after, ids["a"], moved_people=(ids["sole"],)) == []
            for table, row in (("users", ids["sole"]),):
                old = next(r for r in before[table].values() if r["id"] == row)
                new = next(r for r in after[table].values() if r["id"] == row)
                assert {k: v for k, v in old.items() if k not in ("organization_id",)} == \
                    {k: v for k, v in new.items() if k not in ("organization_id",)}
            record = db.session.execute(text(
                "SELECT new_value FROM soc2_audit_log WHERE organization_id IS NULL AND table_name = 'organizations' "
                "AND record_id = :a"), {"a": ids["a"]}).scalar()
            assert record["people_detached_to"] == {str(ids["sole"]): ids["b"]}, record
            db.session.remove()
    finally:
        _cleanup(app, ids["a"], ids["b"])

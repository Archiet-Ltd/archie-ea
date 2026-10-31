"""Deleting an organisation never writes another organisation's rows or removes a platform administrator.

  - a person whose home is the deleted organisation, but whose name a surviving organisation's row
    must carry (a NOT NULL column), is kept and moved to that organisation;
  - an organisation that is the home of a platform administrator is refused before anything changes;
  - a nullable pointer to a deleted row, in a table with no organisation column, is cleared and
    the row is kept.
"""

import uuid

import pytest
from sqlalchemy import text


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


def test_a_person_a_surviving_organisation_must_name_is_kept_and_its_row_untouched(app):
    from app import db
    from app.models.models import Notification
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        doomed = Organization(name="Doomed %s" % marker, slug="doomed-%s" % marker)
        other = Organization(name="Other %s" % marker, slug="other-%s" % marker)
        db.session.add_all([doomed, other])
        db.session.flush()
        author = User(email="author-%s@example.com" % marker, organization_id=doomed.id)
        alone = User(email="alone-%s@example.com" % marker, organization_id=doomed.id)
        db.session.add_all([author, alone])
        db.session.flush()
        note = Notification(user_id=author.id, message="Written in the other organisation", organization_id=other.id)
        db.session.add(note)
        db.session.commit()
        ids = {"doomed": doomed.id, "other": other.id, "author": author.id, "alone": alone.id, "note": note.id}
        db.session.remove()

    try:
        with app.app_context():
            message = OrganizationDeletionService.delete_organization(ids["doomed"])
            db.session.remove()
        assert "1 person who belonged only to it was removed" in message, message
        assert "1 person who also belongs elsewhere was detached" in message, message

        with app.app_context():
            assert db.session.get(Organization, ids["doomed"]) is None
            kept = db.session.get(User, ids["author"])
            assert kept is not None, "a person the other organisation's row names was removed"
            assert kept.organization_id == ids["other"]
            assert db.session.get(User, ids["alone"]) is None
            row = db.session.execute(
                text("SELECT user_id, organization_id FROM notifications WHERE id = :i"), {"i": ids["note"]}
            ).one()
            assert tuple(row) == (ids["author"], ids["other"]), "the other organisation's row was changed or removed"
            db.session.remove()
    finally:
        _cleanup(app, ids["doomed"], ids["other"])


def test_an_organisation_that_is_a_platform_administrators_home_is_not_deleted(app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import Role
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        home = Organization(name="Admin home %s" % marker, slug="admin-home-%s" % marker)
        db.session.add(home)
        db.session.flush()
        admin = User(email="admin-%s@example.com" % marker, organization_id=home.id, confirmed=True)
        admin.role = Role.query.filter_by(name="Administrator").one()
        admin.is_platform_admin = True
        member = User(email="member-%s@example.com" % marker, organization_id=home.id)
        db.session.add_all([admin, member])
        db.session.commit()
        ids = {"home": home.id, "admin": admin.id, "member": member.id, "name": home.name}
        db.session.remove()

    try:
        with app.app_context():
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["home"], requester_id=ids["admin"])
            assert "1 platform administrator must be moved to another organisation" in str(refused.value)
            assert ids["name"] in str(refused.value)
            db.session.remove()

        with app.app_context():
            assert db.session.get(Organization, ids["home"]) is not None
            assert db.session.get(User, ids["admin"]) is not None
            assert db.session.get(User, ids["member"]) is not None
            assert db.session.execute(text(
                "SELECT count(*) FROM soc2_audit_log WHERE table_name = 'organizations' AND record_id = :i "
                "AND action = 'DELETE'"), {"i": ids["home"]}).scalar() == 0
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("UPDATE users SET is_platform_admin = false WHERE id = :i"), {"i": ids["admin"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["home"])


def test_a_nullable_pointer_to_a_deleted_element_is_cleared_and_its_row_kept(app):
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorArchiMateTemplate
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        doomed = Organization(name="Doomed tpl %s" % marker, slug="doomed-tpl-%s" % marker)
        db.session.add(doomed)
        db.session.flush()
        element = ArchiMateElement(
            name="Doomed element %s" % marker, type="ApplicationComponent", layer="Application",
            organization_id=doomed.id,
        )
        db.session.add(element)
        db.session.flush()
        template = VendorArchiMateTemplate(
            vendor_key="SAP", element_id=element.id, element_name="Kept template %s" % marker,
        )
        db.session.add(template)
        db.session.commit()
        ids = {"doomed": doomed.id, "template": template.id}
        db.session.remove()

    try:
        with app.app_context():
            OrganizationDeletionService.delete_organization(ids["doomed"])
            db.session.remove()
        with app.app_context():
            row = db.session.execute(
                text("SELECT element_id, element_name FROM vendor_archimate_templates WHERE id = :i"),
                {"i": ids["template"]},
            ).first()
            assert row is not None, "the template row was deleted with the element it pointed at"
            assert row[0] is None and row[1] == "Kept template %s" % marker
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM vendor_archimate_templates WHERE id = :i"), {"i": ids["template"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["doomed"])

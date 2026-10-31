"""Deleting an organisation never writes another organisation's rows or removes a platform administrator.

  - a person who would be removed, but whom a surviving organisation's row names, refuses the whole
    deletion before anything is written;
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


def test_a_person_a_surviving_organisation_must_name_refuses_the_deletion(app):
    from app import db
    from app.models.models import Notification
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        doomed = Organization(name="Doomed %s" % marker, slug="doomed-%s" % marker)
        other = Organization(name="Other %s" % marker, slug="other-%s" % marker)
        db.session.add_all([doomed, other])
        db.session.flush()
        author = User(email="author-%s@example.com" % marker, organization_id=doomed.id)
        db.session.add(author)
        db.session.flush()
        note = Notification(user_id=author.id, message="Written in the other organisation", organization_id=other.id)
        db.session.add(note)
        db.session.commit()
        ids = {"doomed": doomed.id, "other": other.id, "author": author.id, "note": note.id, "name": other.name}
        db.session.remove()

    try:
        with app.app_context():
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["doomed"])
            assert "Doomed %s cannot be deleted: records outside it still refer to it. %s: 1 " % (marker, ids["name"]) in str(refused.value)
            assert "Remove or reassign those references first. Nothing was deleted." in str(refused.value)
            db.session.remove()

        with app.app_context():
            assert db.session.get(Organization, ids["doomed"]) is not None
            person = db.session.get(User, ids["author"])
            assert person is not None and person.organization_id == ids["doomed"]
            row = db.session.execute(
                text("SELECT user_id, organization_id FROM notifications WHERE id = :i"), {"i": ids["note"]}
            ).one()
            assert tuple(row) == (ids["author"], ids["other"]), "the other organisation's row was changed or removed"
            db.session.remove()
    finally:
        _cleanup(app, ids["other"], ids["doomed"])


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


def test_a_platform_template_pointing_at_an_element_of_the_deleted_organisation_refuses_the_deletion(app):
    """A row outside the organisation is never rewritten to let the deletion through.

    This used to assert that the template row survived with its pointer cleared. That was a write to
    a shared platform row the deletion has no right to make, so the deletion is now refused and the
    template is left exactly as it was.
    """
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorArchiMateTemplate
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

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
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["doomed"])
            assert "Shared platform records: 1 " in str(refused.value)
            db.session.remove()
        with app.app_context():
            assert db.session.get(Organization, ids["doomed"]) is not None
            row = db.session.execute(
                text("SELECT element_id, element_name FROM vendor_archimate_templates WHERE id = :i"),
                {"i": ids["template"]},
            ).first()
            assert row is not None, "the template row was deleted"
            assert row[0] is not None and row[1] == "Kept template %s" % marker, "the template was rewritten"
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM vendor_archimate_templates WHERE id = :i"), {"i": ids["template"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["doomed"])

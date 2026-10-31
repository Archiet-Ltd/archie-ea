"""Deleting an organisation never depends on another organisation existing.

On a database with no Default organisation, deleting an organisation:
  - removes a person who belonged only to it, and their sessions;
  - detaches a person whose home it was but who also holds a role elsewhere; their home moves there;
  - removes another organisation's person's role in it, and keeps that person;
  - is refused, with nothing changed, when another organisation's row names a person who would be removed.
"""

import uuid

import pytest
from sqlalchemy import text


@pytest.fixture
def no_default_organisation(app):
    """Hide the Default organisation for the length of the test, and give it back afterwards."""
    from app import db

    hidden = "default-hidden-%s" % uuid.uuid4().hex[:8]
    with app.app_context():
        db.session.execute(text("UPDATE organizations SET slug = :hidden WHERE slug = 'default'"), {"hidden": hidden})
        db.session.commit()
        assert db.session.execute(text("SELECT count(*) FROM organizations WHERE slug = 'default'")).scalar() == 0
    yield
    with app.app_context():
        db.session.rollback()
        db.session.execute(text("UPDATE organizations SET slug = 'default' WHERE slug = :hidden"), {"hidden": hidden})
        db.session.commit()
        db.session.remove()


def test_deleting_an_organisation_needs_no_default_organisation(app, no_default_organisation):
    from app import db
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        doomed = Organization(name="Doomed %s" % marker, slug="doomed-%s" % marker)
        kept = Organization(name="Kept %s" % marker, slug="kept-%s" % marker)
        db.session.add_all([doomed, kept])
        db.session.flush()
        only_here = User(email="only-here-%s@example.com" % marker, organization_id=doomed.id)
        also_elsewhere = User(email="also-elsewhere-%s@example.com" % marker, organization_id=doomed.id)
        visitor = User(email="visitor-%s@example.com" % marker, organization_id=kept.id)
        db.session.add_all([only_here, also_elsewhere, visitor])
        db.session.flush()
        db.session.add_all([
            OrgRole(organization_id=kept.id, user_id=also_elsewhere.id, role="viewer"),
            OrgRole(organization_id=doomed.id, user_id=visitor.id, role="viewer"),
        ])
        db.session.commit()
        ids = {
            "doomed": doomed.id, "kept": kept.id, "only_here": only_here.id,
            "also_elsewhere": also_elsewhere.id, "visitor": visitor.id,
        }
        db.session.remove()

    try:
        with app.app_context():
            message = OrganizationDeletionService.delete_organization(ids["doomed"])
            db.session.remove()
        assert "1 person who belonged only to it was removed" in message
        assert "1 person who also belongs elsewhere was detached" in message

        with app.app_context():
            assert db.session.get(Organization, ids["doomed"]) is None
            assert db.session.get(User, ids["only_here"]) is None
            moved = db.session.get(User, ids["also_elsewhere"])
            assert moved is not None and moved.organization_id == ids["kept"]
            visitor = db.session.get(User, ids["visitor"])
            assert visitor is not None and visitor.organization_id == ids["kept"]
            assert OrgRole.query.filter_by(organization_id=ids["doomed"]).count() == 0
            assert OrgRole.query.filter_by(user_id=ids["also_elsewhere"], organization_id=ids["kept"]).count() == 1
            db.session.remove()
    finally:
        with app.app_context():
            if db.session.get(Organization, ids["kept"]) is not None:
                OrganizationDeletionService.delete_organization(ids["kept"])
            leftover = db.session.get(Organization, ids["doomed"])
            if leftover is not None:
                OrganizationDeletionService.delete_organization(ids["doomed"])
            db.session.remove()


def test_a_person_named_in_a_nullable_column_of_another_organisation_refuses_the_deletion(app):
    from app import db
    from app.models.organization import Organization
    from app.models.strategic import StrategicInitiative
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        doomed = Organization(name="Doomed %s" % marker, slug="doomed-%s" % marker)
        kept = Organization(name="Kept %s" % marker, slug="kept-%s" % marker)
        db.session.add_all([doomed, kept])
        db.session.flush()
        only_here = User(email="only-here-%s@example.com" % marker, organization_id=doomed.id)
        db.session.add(only_here)
        db.session.flush()
        plan = StrategicInitiative(name="Kept plan %s" % marker, organization_id=kept.id, owner_id=only_here.id)
        db.session.add(plan)
        db.session.commit()
        ids = {"doomed": doomed.id, "kept": kept.id, "person": only_here.id, "plan": plan.id}
        db.session.remove()

    try:
        with app.app_context():
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["doomed"])
            assert "Kept %s" % marker in str(refused.value)
            assert "Nothing was deleted" in str(refused.value)
            db.session.remove()
        with app.app_context():
            assert db.session.get(Organization, ids["doomed"]) is not None
            person = db.session.get(User, ids["person"])
            assert person is not None and person.organization_id == ids["doomed"]
            owner = db.session.execute(
                text("SELECT owner_id FROM strategic_initiatives WHERE id = :i"), {"i": ids["plan"]}
            ).scalar()
            assert owner == ids["person"], "the other organisation's row was written"
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM strategic_initiatives WHERE id = :i"), {"i": ids["plan"]})
            db.session.commit()
            db.session.remove()
            for org_id in (ids["kept"], ids["doomed"]):
                if db.session.get(Organization, org_id) is not None:
                    OrganizationDeletionService.delete_organization(org_id)
            db.session.remove()


def test_a_person_named_by_another_organisations_append_only_row_refuses_the_deletion(app):
    """The refusal is plain and comes before any write, so no foreign-key error ever shows.

    arb_submission_evidence_snapshots is append-only and its ``actor_id`` is a nullable foreign
    key to ``users``: a surviving organisation's snapshot can name a person who would otherwise
    be removed with their own organisation.
    """
    from app import db
    from app.models.arb_submission_evidence import ARBSubmissionEvidenceSnapshot
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        doomed = Organization(name="Doomed append %s" % marker, slug="doomed-append-%s" % marker)
        other = Organization(name="Other append %s" % marker, slug="other-append-%s" % marker)
        db.session.add_all([doomed, other])
        db.session.flush()
        departing = User(email="departing-%s@example.com" % marker, organization_id=doomed.id)
        db.session.add(departing)
        db.session.flush()
        snapshot = ARBSubmissionEvidenceSnapshot(organization_id=other.id, actor_id=departing.id)
        db.session.add(snapshot)
        db.session.commit()
        ids = {"doomed": doomed.id, "other": other.id, "departing": departing.id, "snapshot": snapshot.id}
        db.session.remove()

    try:
        with app.app_context():
            with pytest.raises(OrganizationDeletionRefused) as refused:
                OrganizationDeletionService.delete_organization(ids["doomed"])
            message = str(refused.value)
            assert "Other append %s" % marker in message
            assert "Reassign those records or move these people first" in message
            assert "foreign key" not in message and "append-only" not in message
            db.session.rollback()
            db.session.remove()

        # Nothing was written: the organisation, the person and the citing row all still stand.
        with app.app_context():
            assert db.session.get(Organization, ids["doomed"]) is not None
            row = db.session.execute(
                text("SELECT actor_id FROM arb_submission_evidence_snapshots WHERE id = :id"),
                {"id": ids["snapshot"]},
            ).one()
            assert row[0] == ids["departing"]
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(
                text("INSERT INTO organization_deletions_in_progress (target_organization_id) "
                     "VALUES (:org_id) ON CONFLICT DO NOTHING"),
                {"org_id": ids["other"]},
            )
            db.session.execute(
                text("SELECT set_config('archie.deleting_organisation', :org_id, true)"),
                {"org_id": str(ids["other"])},
            )
            db.session.execute(
                text("DELETE FROM arb_submission_evidence_snapshots WHERE id = :id"),
                {"id": ids["snapshot"]},
            )
            db.session.execute(
                text("DELETE FROM organization_deletions_in_progress WHERE target_organization_id = :org_id"),
                {"org_id": ids["other"]},
            )
            db.session.commit()
            db.session.remove()
            for org_id in (ids["other"], ids["doomed"]):
                if db.session.get(Organization, org_id) is not None:
                    OrganizationDeletionService.delete_organization(org_id)
            db.session.remove()

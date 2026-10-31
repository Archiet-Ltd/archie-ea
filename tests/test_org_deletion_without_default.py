"""Deleting an organisation never depends on another organisation existing.

On a database with no Default organisation, deleting an organisation:
  - removes a person who belonged only to it, and their sessions;
  - detaches a person whose home it was but who also holds a role elsewhere; their home moves there;
  - removes another organisation's person's role in it, and keeps that person;
  - keeps another organisation's row that named the removed person, without the name.
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
    from app.models.strategic import StrategicInitiative
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
            StrategicInitiative(name="Kept plan %s" % marker, organization_id=kept.id, owner_id=only_here.id),
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
            plan = StrategicInitiative.query.filter_by(name="Kept plan %s" % marker).one()
            assert plan.organization_id == ids["kept"] and plan.owner_id is None
            db.session.remove()
    finally:
        with app.app_context():
            if db.session.get(Organization, ids["kept"]) is not None:
                OrganizationDeletionService.delete_organization(ids["kept"])
            leftover = db.session.get(Organization, ids["doomed"])
            if leftover is not None:
                OrganizationDeletionService.delete_organization(ids["doomed"])
            db.session.remove()


def test_a_departing_person_named_by_another_organisations_append_only_row_is_never_updated(app):
    """``_remove_people`` never issues an UPDATE against an append-only table.

    arb_submission_evidence_snapshots is append-only (``reject_archie_evidence_mutation``
    admits a DELETE under the deletion marker, never an UPDATE) and its ``actor_id`` is a
    nullable foreign key to ``users`` with no tenant check of its own, so a person leaving
    one organisation can still be named by another, surviving organisation's snapshot row.
    Before this fix, deleting the person's own organisation tried to NULL that column and the
    append-only guard refused the UPDATE outright -- a defect, since nulling an append-only
    row's column is never admitted no matter which organisation's delete is in progress.

    The column is still a real foreign key with no cascade of its own, so a person a surviving
    organisation's append-only row insists on still naming cannot be deleted either (deleting
    them would leave that row's own reference dangling) -- that half is a genuine, pre-existing
    referential-integrity conflict between the two organisations' data, not something a trigger
    or a retry can paper over, and it now surfaces as the database's own plain foreign-key
    refusal naming the row that is holding on, never as the append-only guard's misleading
    "is append-only" exception.
    """
    from app import db
    from app.models.arb_submission_evidence import ARBSubmissionEvidenceSnapshot
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.org_deletion_service import OrganizationDeletionService

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
            with pytest.raises(Exception) as excinfo:
                OrganizationDeletionService.delete_organization(ids["doomed"])
            message = str(excinfo.value)
            assert "append-only" not in message, (
                "the guard rejected an UPDATE this code must no longer attempt: " + message
            )
            assert "arb_submission_evidence_snapshots" in message, (
                "expected the database's own foreign-key refusal naming the citing row: " + message
            )
            db.session.rollback()
            db.session.remove()

        # Rolled back whole: the organisation, the person and the citing row all still stand.
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
                text("SELECT set_config('archie.deleting_organisation', :org_id, true)"),
                {"org_id": str(ids["other"])},
            )
            db.session.execute(
                text("DELETE FROM arb_submission_evidence_snapshots WHERE id = :id"),
                {"id": ids["snapshot"]},
            )
            db.session.commit()
            db.session.remove()
            for org_id in (ids["other"], ids["doomed"]):
                if db.session.get(Organization, org_id) is not None:
                    OrganizationDeletionService.delete_organization(org_id)
            db.session.remove()

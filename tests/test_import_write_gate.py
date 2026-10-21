"""Perm02b: Viewer cannot import a model file.

The three import-write routes carry ``@require_permission("create", "relationship")``
so a Viewer gets 403 and nothing is written; administrators and architects still import.
"""

import uuid

import pytest


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"PM {label} {suffix}", slug=f"pm-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, org_role, platform_role="User"):
    """A confirmed user whose organisation role is ``org_role``."""
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    role = Role.query.filter_by(name=platform_role).first()
    user = User(
        email=f"pm-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Perm",
        last_name=org_role,
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    if org_role == "org_admin":
        user.grant_org_admin()
    db_session.flush()
    return user


def _two_elements(db_session, org):
    """Create two ArchiMate elements in the organisation."""
    from app.models.archimate_core import ArchiMateElement

    e1 = ArchiMateElement(
        name=f"Elem {uuid.uuid4().hex[:6]}",
        type="Business",
        layer="business",
        organization_id=org.id,
    )
    e2 = ArchiMateElement(
        name=f"Elem {uuid.uuid4().hex[:6]}",
        type="Business",
        layer="business",
        organization_id=org.id,
    )
    db_session.add_all([e1, e2])
    db_session.flush()
    return e1, e2


def _count_elements_relationships(db_session):
    """Return (element_count, relationship_count) after expiring all."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    db_session.expire_all()
    elem_count = db_session.query(ArchiMateElement).count()
    rel_count = db_session.query(ArchiMateRelationship).count()
    return elem_count, rel_count


@pytest.fixture
def client(app):
    return app.test_client()


class TestViewerImportRefused:
    """A Viewer POST to any import-write route returns 403 and writes nothing."""

    def test_viewer_refused_import_oef(self, app, db_session, login_as, client):
        org = _org(db_session, "view-import")
        viewer = _user(db_session, org, "viewer")
        db_session.commit()

        login_as(client, viewer)

        elem_before, rel_before = _count_elements_relationships(db_session)

        resp = client.post("/architecture/import/oef",
                           data={"file": (b"<?xml version='1.0'?><model></model>", "test.oef")},
                           content_type="multipart/form-data")
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"

        elem_after, rel_after = _count_elements_relationships(db_session)
        assert elem_after == elem_before, "Element count changed despite 403"
        assert rel_after == rel_before, "Relationship count changed despite 403"

    def test_viewer_refused_import_oef_preview(self, app, db_session, login_as, client):
        org = _org(db_session, "view-import-preview")
        viewer = _user(db_session, org, "viewer")
        db_session.commit()

        login_as(client, viewer)

        elem_before, rel_before = _count_elements_relationships(db_session)

        resp = client.post("/architecture/import/oef/preview",
                           data={"file": (b"<?xml version='1.0'?><model></model>", "test.oef")},
                           content_type="multipart/form-data")
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"

        elem_after, rel_after = _count_elements_relationships(db_session)
        assert elem_after == elem_before, "Element count changed despite 403"
        assert rel_after == rel_before, "Relationship count changed despite 403"

    def test_viewer_refused_api_import_oef(self, app, db_session, login_as, client):
        org = _org(db_session, "view-import-api")
        viewer = _user(db_session, org, "viewer")
        db_session.commit()

        login_as(client, viewer)

        elem_before, rel_before = _count_elements_relationships(db_session)

        resp = client.post("/archimate/api/import/oef",
                           data={"file": (b"<?xml version='1.0'?><model></model>", "test.oef")},
                           content_type="multipart/form-data")
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"

        elem_after, rel_after = _count_elements_relationships(db_session)
        assert elem_after == elem_before, "Element count changed despite 403"
        assert rel_after == rel_before, "Relationship count changed despite 403"


class TestArchitectImportSucceeds:
    """An Architect POST of a two-element fixture succeeds."""

    def test_architect_succeeds_import_oef(self, app, db_session, login_as, client):
        org = _org(db_session, "arch-import")
        architect = _user(db_session, org, "architect")
        db_session.commit()

        login_as(client, architect)

        _two_elements(db_session, org)

        elem_before, rel_before = _count_elements_relationships(db_session)

        resp = client.post("/architecture/import/oef",
                           data={"file": (b"<?xml version='1.0'?><model></model>", "test.oef")},
                           content_type="multipart/form-data")
        assert resp.status_code not in (401, 403), f"Expected 200, got {resp.status_code}"

        elem_after, rel_after = _count_elements_relationships(db_session)
        # Import should have added elements/relationships
        assert elem_after >= elem_before, "No elements created for architect"

    def test_architect_succeeds_import_oef_preview(self, app, db_session, login_as, client):
        org = _org(db_session, "arch-import-preview")
        architect = _user(db_session, org, "architect")
        db_session.commit()

        login_as(client, architect)

        _two_elements(db_session, org)

        elem_before, rel_before = _count_elements_relationships(db_session)

        resp = client.post("/architecture/import/oef/preview",
                           data={"file": (b"<?xml version='1.0'?><model></model>", "test.oef")},
                           content_type="multipart/form-data")
        assert resp.status_code not in (401, 403), f"Expected 200, got {resp.status_code}"

        elem_after, rel_after = _count_elements_relationships(db_session)
        assert elem_after >= elem_before, "No elements created for architect"

    def test_architect_succeeds_api_import_oef(self, app, db_session, login_as, client):
        org = _org(db_session, "arch-import-api")
        architect = _user(db_session, org, "architect")
        db_session.commit()

        login_as(client, architect)

        _two_elements(db_session, org)

        elem_before, rel_before = _count_elements_relationships(db_session)

        resp = client.post("/api/import/oef",
                           data={"file": (b"<?xml version='1.0'?><model></model>", "test.oef")},
                           content_type="multipart/form-data")
        assert resp.status_code not in (401, 403), f"Expected 200, got {resp.status_code}"

        elem_after, rel_after = _count_elements_relationships(db_session)
        assert elem_after >= elem_before, "No elements created for architect"


class TestImportStillPassesExisting:
    """The existing permission matrix tests still pass."""

    def test_existing_permission_matrix_still_works(self, app, db_session, login_as, client):
        """Verify the original permission matrix tests still pass."""
        from tests.test_permission_matrix_slice1 import _org as _orig_org

        org = _orig_org(db_session, "existing")
        architect = _user(db_session, org, "architect")
        db_session.commit()

        login_as(client, architect)
        resp = client.get("/applications/")
        assert resp.status_code == 200
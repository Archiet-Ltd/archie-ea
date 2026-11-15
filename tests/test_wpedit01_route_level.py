"""wpedit01 route-level: every work-package write route refuses a Viewer and allows an architect.

Sends every write route as a Viewer and asserts refusal status and unchanged data;
the same test as an architect asserts success. Fails at line tip before the
can() guards are added; passes after.
"""

import pytest

from tests.test_ba_tenant_and_authz import (
    _cleanup_ids,
    _login,
    _make_org_id,
    _make_user_id,
)


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_wp(app, org_id, name):
    from app import db
    from app.services import work_package_service

    with app.app_context():
        wp = work_package_service.create_work_package(organization_id=org_id, name=name)
        db.session.commit()
        return wp.id


def _wp_exists(app, wp_id):
    from app import db
    from app.models.unified_work_package import UnifiedWorkPackage

    with app.app_context():
        return db.session.get(UnifiedWorkPackage, wp_id) is not None


# ---------------------------------------------------------------------------
# Viewer is refused on every write route
# ---------------------------------------------------------------------------


def test_viewer_refused_create_work_package(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WPV")
        uid = _make_user_id(db, org, "WpViewer", enterprise_role="procurement", role_name="Viewer")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.post("/enterprise/api/work-packages", json={"name": "Viewer create"})
            assert r.status_code == 403, r.get_data(as_text=True)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])


def test_viewer_refused_update_work_package(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WPV")
        uid = _make_user_id(db, org, "WpViewer", enterprise_role="procurement", role_name="Viewer")
        wp_id = _make_wp(app, org, "Viewer target")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.patch(f"/enterprise/api/work-packages/{wp_id}", json={"name": "Changed"})
            assert r.status_code == 403, r.get_data(as_text=True)
        assert _wp_exists(app, wp_id)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])


def test_viewer_refused_delete_work_package(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WPV")
        uid = _make_user_id(db, org, "WpViewer", enterprise_role="procurement", role_name="Viewer")
        wp_id = _make_wp(app, org, "Viewer delete target")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.delete(f"/enterprise/api/work-packages/{wp_id}")
            assert r.status_code == 403, r.get_data(as_text=True)
        assert _wp_exists(app, wp_id)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])


def test_viewer_refused_bulk_delete_work_packages(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WPV")
        uid = _make_user_id(db, org, "WpViewer", enterprise_role="procurement", role_name="Viewer")
        wp_id = _make_wp(app, org, "Viewer bulk target")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.delete("/enterprise/api/work-packages/bulk", json={"ids": [wp_id]})
            assert r.status_code == 403, r.get_data(as_text=True)
        assert _wp_exists(app, wp_id)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])


def test_viewer_refused_ui_delete_work_package(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WPV")
        uid = _make_user_id(db, org, "WpViewer", enterprise_role="procurement", role_name="Viewer")
        wp_id = _make_wp(app, org, "Viewer UI delete target")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.post(f"/enterprise/implementation/work-packages?delete={wp_id}")
            assert r.status_code in (302, 403), r.get_data(as_text=True)
        assert _wp_exists(app, wp_id)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])


# ---------------------------------------------------------------------------
# Architect is allowed on every write route
# ---------------------------------------------------------------------------


def test_architect_can_create_work_package(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WPA")
        uid = _make_user_id(db, org, "WpArchitect", enterprise_role="enterprise_architect")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.post("/enterprise/api/work-packages", json={"name": "Architect create"})
            assert r.status_code == 201, r.get_data(as_text=True)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])


def test_architect_can_update_work_package(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WPA")
        uid = _make_user_id(db, org, "WpArchitect", enterprise_role="enterprise_architect")
        wp_id = _make_wp(app, org, "Architect target")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.patch(f"/enterprise/api/work-packages/{wp_id}", json={"name": "Changed by architect"})
            assert r.status_code == 200, r.get_data(as_text=True)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])


def test_architect_can_delete_work_package(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WPA")
        uid = _make_user_id(db, org, "WpArchitect", enterprise_role="enterprise_architect")
        wp_id = _make_wp(app, org, "Architect delete target")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.delete(f"/enterprise/api/work-packages/{wp_id}")
            assert r.status_code == 200, r.get_data(as_text=True)
        assert not _wp_exists(app, wp_id)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])

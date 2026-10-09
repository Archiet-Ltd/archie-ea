"""F-06 regression: a work package can be deleted, one at a time, in-tenant only.

The 2 Sep 2026 browser audit found work packages could not be deleted: the bulk
"Delete selected" was inert (the JS opened a modal id that did not exist) and rows
had no delete at all. The UI fix reuses the typed-DELETE confirm flow per row; this
pins the new per-id endpoint that backs it and its tenant scoping.
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
    """A work package in the one store: the screen's ids are that store's ids."""
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


def test_owner_can_delete_own_work_package(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "WP")
        uid = _make_user_id(db, org, "WpOwner", enterprise_role="enterprise_architect")
    wp_id = _make_wp(app, org, "Decommission legacy CRM")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.delete(f"/enterprise/api/work-packages/{wp_id}")
            assert r.status_code == 200, r.get_data(as_text=True)
            assert r.get_json()["deleted"] == 1
        assert not _wp_exists(app, wp_id)
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])


def test_cannot_delete_another_tenants_work_package(client, app):
    """TenantMixin scopes the lookup: a foreign id must 404, and the row must survive."""
    from app import db
    from app.models.organization import Organization
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.user import User

    with app.app_context():
        org_a = _make_org_id(db, "WPA")
        org_b = _make_org_id(db, "WPB")
        attacker = _make_user_id(db, org_a, "WpAttacker", enterprise_role="enterprise_architect")
    victim_wp = _make_wp(app, org_b, "Victim work package")
    try:
        with app.app_context():
            _login(client, attacker)
            r = client.delete(f"/enterprise/api/work-packages/{victim_wp}")
            assert r.status_code == 404
        assert _wp_exists(app, victim_wp)
    finally:
        with app.app_context():
            _cleanup_ids(db, UnifiedWorkPackage, [victim_wp])
            _cleanup_ids(db, User, [attacker])
            _cleanup_ids(db, Organization, [org_a, org_b])

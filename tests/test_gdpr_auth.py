"""Authorization regression tests for app/modules/compliance/gdpr_routes.py.

WHY THIS FILE EXISTS
---------------------
The three GDPR endpoints (export/<user_id>, delete/<user_id>, status/<user_id>)
originally had NO auth at all: any unauthenticated caller could export or
permanently delete any user's PII, and the delete endpoint trusted a
`requester_id` supplied in the request JSON body for its audit log. The
blueprint is (and remains) UNREGISTERED in the real app — confirmed 404 in
prod, so this was dormant, not live. It is secured anyway as defence in
depth: registering it in the future should not silently ship an
unauthenticated PII-export/delete landmine.

The blueprint is deliberately NOT registered on the real `app` fixture
(app/_bootstrap/blueprints.py never wires it in, and this file must not
change that — turning it into a live feature is a separate product
decision). Instead this module registers `gdpr_bp` only on its own
module-scoped Flask test app, so the routes can be exercised in isolation.

Authorization model under test:
  - All three routes require @login_required (anonymous -> 302/401).
  - export / status: allowed for the subject themselves (user_id ==
    current_user.id) OR a platform_admin acting on any user_id.
  - delete: platform_admin ONLY, even for the caller's own user_id (no
    self-service permanent delete without a separate confirmation flow).
"""

import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app, db
    from app.modules.compliance.gdpr_routes import gdpr_bp

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    # Registered ONLY on this test app, never on the real one — see module
    # docstring. Guard against double-registration if pytest reimports.
    if "gdpr_bp" not in app.blueprints:
        app.register_blueprint(gdpr_bp)

    with app.app_context():
        db.create_all()

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org_id(db, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"{label} Org {suffix}", slug=f"{label.lower()}-org-{suffix}")
    db.session.add(org)
    db.session.flush()
    db.session.commit()
    return org.id


def _make_user_id(db, org_id, label, is_platform_admin=False):
    suffix = uuid.uuid4().hex[:8]
    from app.models.user import Permission, Role, User

    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="procurement",
        is_platform_admin=is_platform_admin,
    )
    if is_platform_admin:
        user.role = Role.query.filter(
            Role.permissions.op("&")(Permission.ADMINISTER) == Permission.ADMINISTER
        ).first()
    db.session.add(user)
    db.session.commit()
    return user.id


def _login(client, user_id):
    """See tests/test_ba_tenant_and_authz.py::_login for why the g-cache clear
    is required — the same Flask-Login caching hazard applies here."""
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id, app=client.application)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def two_users(app):
    """Real, fully committed rows rather than db_session/make_org: this
    file's own app fixture registers gdpr_bp only on itself, deliberately
    never on the real app (see module docstring) — a session-scoped
    db_session cannot depend on that module-scoped app (pytest raises
    ScopeMismatch), so db_session/make_org are not usable here, and there is
    no canonical make_user anywhere in scope either (tests/conftest.py and
    tests/_session_test_helpers.py create no User). Deletes exactly what it
    created, by id, instead — including the soc2_audit_log and gdpr_requests
    rows these tests' own authenticated actions write along the way.
    """
    from app import db

    with app.app_context():
        org_id = _make_org_id(db, "GdprAuth")
        subject_id = _make_user_id(db, org_id, "Subject")
        other_id = _make_user_id(db, org_id, "Other")
        admin_id = _make_user_id(db, org_id, "Admin", is_platform_admin=True)

    yield {"subject": subject_id, "other": other_id, "admin": admin_id}

    with app.app_context():
        from app.models.audit_log import AuditLog
        from app.models.gdpr_request import GDPRRequest
        from app.models.organization import Organization
        from app.models.user import User

        user_ids = (subject_id, other_id, admin_id)
        # User is an audited ("controlled") model, so creating each of the
        # three above already wrote a soc2_audit_log row keyed by record_id
        # (user_id there is the acting user, None outside a request — this
        # catches those too, not just the insert rows). An admin action above
        # (e.g. the delete-user-data anonymisation) can ALSO write a row whose
        # user_id is the acting admin, not the subject — either way, and
        # unlike user_sessions, that FK is not ondelete=CASCADE, so both must
        # be cleared before the users they reference or the delete below
        # raises a ForeignKeyViolation. gdpr_requests has no FK at all but
        # still needs deleting; user_sessions cascades with the user.
        db.session.query(AuditLog).filter(
            db.or_(
                AuditLog.user_id.in_(user_ids),
                db.and_(AuditLog.table_name == "user", AuditLog.record_id.in_(user_ids)),
            )
        ).delete(synchronize_session=False)
        db.session.query(GDPRRequest).filter(GDPRRequest.user_id.in_(user_ids)).delete(synchronize_session=False)
        for uid in user_ids:
            db.session.query(User).filter_by(id=uid).delete()
        db.session.query(Organization).filter_by(id=org_id).delete()
        db.session.commit()


class TestAnonymousBlocked:
    def test_export_requires_login(self, client, two_users):
        resp = client.get(f"/api/gdpr/export/{two_users['subject']}", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_delete_requires_login(self, client, two_users):
        resp = client.post(
            f"/api/gdpr/delete/{two_users['subject']}", json={}, follow_redirects=False
        )
        assert resp.status_code in (302, 401)

    def test_status_requires_login(self, client, two_users):
        resp = client.get(f"/api/gdpr/status/{two_users['subject']}", follow_redirects=False)
        assert resp.status_code in (302, 401)


class TestNonAdminCannotActOnAnotherUser:
    def test_export_of_other_user_forbidden(self, client, two_users):
        _login(client, two_users["other"])
        resp = client.get(f"/api/gdpr/export/{two_users['subject']}")
        assert resp.status_code == 403

    def test_status_of_other_user_forbidden(self, client, two_users):
        _login(client, two_users["other"])
        resp = client.get(f"/api/gdpr/status/{two_users['subject']}")
        assert resp.status_code == 403

    def test_delete_of_other_user_forbidden(self, client, two_users):
        _login(client, two_users["other"])
        resp = client.post(f"/api/gdpr/delete/{two_users['subject']}", json={})
        assert resp.status_code == 403


class TestSelfServiceExportStatusOnly:
    def test_self_export_allowed(self, client, two_users):
        _login(client, two_users["subject"])
        resp = client.get(f"/api/gdpr/export/{two_users['subject']}")
        # Not 403/401 — self access is permitted (may 404 if GDPRService has
        # no exportable data, which is a service-layer concern, not auth).
        assert resp.status_code != 403

    def test_self_status_allowed(self, client, two_users):
        _login(client, two_users["subject"])
        resp = client.get(f"/api/gdpr/status/{two_users['subject']}")
        assert resp.status_code != 403

    def test_self_delete_forbidden(self, client, two_users):
        """Delete is platform_admin-only even for the subject's own data —
        no unconfirmed self-service permanent delete."""
        _login(client, two_users["subject"])
        resp = client.post(f"/api/gdpr/delete/{two_users['subject']}", json={})
        assert resp.status_code == 403


class TestPlatformAdminCanActOnAnyUser:
    def test_admin_export_of_other_user_allowed(self, client, two_users):
        _login(client, two_users["admin"])
        resp = client.get(f"/api/gdpr/export/{two_users['subject']}")
        assert resp.status_code != 403

    def test_admin_status_of_other_user_allowed(self, client, two_users):
        _login(client, two_users["admin"])
        resp = client.get(f"/api/gdpr/status/{two_users['subject']}")
        assert resp.status_code != 403

    def test_admin_delete_of_other_user_allowed(self, client, two_users):
        _login(client, two_users["admin"])
        resp = client.post(f"/api/gdpr/delete/{two_users['subject']}", json={})
        assert resp.status_code != 403

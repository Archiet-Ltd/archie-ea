"""Tests for the policy monitoring scan API endpoint."""
import pytest
from flask import url_for

from app.models.user import User, Role
from app.models.policy_monitoring import ComplianceStatus
from app.models.application_portfolio import ApplicationComponent


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
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


def _make_user(db_session, org, *, enterprise_role, role_name=None, platform_admin=False):
    user = User(
        email=f"{enterprise_role}-{role_name or ''}-{org.id}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
        is_platform_admin=platform_admin,
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    if role_name:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
        if role:
            user.role = role
            db_session.flush()
    return user


def _make_logged_in_client(app, db_session, make_org, enterprise_role, role_name=None):
    org = make_org("test-org")
    user = _make_user(db_session, org, enterprise_role=enterprise_role, role_name=role_name)
    client = app.test_client()
    _login(client, user.id)
    return client, user, org


@pytest.fixture
def org_a(make_org):
    return make_org("org-a")


@pytest.fixture
def org_b(make_org):
    return make_org("org-b")


def test_scan_requires_write_role(app, db_session, org_a, org_b):
    """Org A read-only user gets 403 when trying to scan."""
    client, user, org = _make_logged_in_client(
        app, db_session, lambda name: org_a, enterprise_role="enterprise_architect", role_name="Viewer"
    )
    # Create an application in org A
    app_component = ApplicationComponent(
        name="Test App",
        application_code="TAPP",
        organization_id=org.id,
    )
    db_session.add(app_component)
    db_session.flush()

    with app.test_request_context():
        scan_url = url_for("unified_low_priority.api_scan_compliance")
    resp = client.post(
        scan_url,
        json={"application_id": app_component.id},
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"


def test_scan_foreign_application_returns_404_and_no_row_written(
    app, db_session, org_a, org_b
):
    """Org A editor scanning an org B application gets 404 and no row written."""
    client, user, org = _make_logged_in_client(
        app, db_session, lambda name: org_a, enterprise_role="enterprise_architect", role_name="Editor"
    )
    # Create an application in org B
    foreign_app = ApplicationComponent(
        name="Foreign App",
        application_code="FAPP",
        organization_id=org_b.id,
    )
    db_session.add(foreign_app)
    db_session.flush()

    with app.test_request_context():
        scan_url = url_for("unified_low_priority.api_scan_compliance")
    resp = client.post(
        scan_url,
        json={"application_id": foreign_app.id},
    )
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
    # Ensure no compliance status row was written for the foreign application
    compliance = ComplianceStatus.query.filter_by(
        entity_type="application", entity_id=foreign_app.id
    ).first()
    assert compliance is None, "Compliance status should not be created for foreign application"


def test_scan_own_application_succeeds_and_writes_row(app, db_session, org_a):
    """Org A editor scanning its own application works and writes a compliance status row."""
    client, user, org = _make_logged_in_client(
        app, db_session, lambda name: org_a, enterprise_role="enterprise_architect", role_name="Editor"
    )
    # Create an application in org A
    app_component = ApplicationComponent(
        name="Own App",
        application_code="OAPP",
        organization_id=org.id,
    )
    db_session.add(app_component)
    db_session.flush()

    with app.test_request_context():
        scan_url = url_for("unified_low_priority.api_scan_compliance")
    resp = client.post(
        scan_url,
        json={"application_id": app_component.id},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.get_json()
    assert data.get("success") is True
    # Ensure a compliance status row was written
    compliance = ComplianceStatus.query.filter_by(
        entity_type="application", entity_id=app_component.id
    ).first()
    assert compliance is not None, "Compliance status should be created"
    assert compliance.entity_id == app_component.id
    assert compliance.entity_type == "application"